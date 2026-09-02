# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Outlet C (subagent) orchestrator.

Routes subagent review through the same StateMachine that Outlet A uses,
ensuring identical receipt writing and cycle-counting behavior.
Reviewer-provided code_excerpts flow: reviewer JSON -> l1_provider
4-tuple -> machine.py -> receipt.py -> verify.py.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Callable

from .autofix import StubAutoFixer
from .baseline import ResolvedReview
from .disposition import Disposition
from .falsify import Falsifier
from .llm_invoke import Usage
from .machine import StateMachine
from .reviewer_json import (
    validate_reviewer_json,
    _collect_excerpts,
    _dedup_by_fingerprint,
    _json_to_state_findings,
)
from .state import Mode, StateFinding, Verdict, _PASS_NAMES

log = logging.getLogger(__name__)

_DEFAULT_CHUNK_THRESHOLD_KB = 100


def _read_chunk_threshold_kb() -> int:
    """Read FORGE_DIFF_CHUNK_THRESHOLD_KB from env.

    Returns default (100) if unset. Returns -1 (always-chunk) if
    non-positive. Logs warning and returns default if non-numeric.
    """
    raw = os.environ.get("FORGE_DIFF_CHUNK_THRESHOLD_KB")
    if raw is None:
        return _DEFAULT_CHUNK_THRESHOLD_KB
    try:
        val = int(raw)
        return val
    except ValueError:
        log.warning(
            "non-numeric FORGE_DIFF_CHUNK_THRESHOLD_KB=%r, "
            "falling back to default %d",
            raw, _DEFAULT_CHUNK_THRESHOLD_KB,
        )
        return _DEFAULT_CHUNK_THRESHOLD_KB


def _split_diff_by_file(diff: str) -> list[str]:
    """Split a unified diff into per-file chunks.

    Uses findall (not split) to preserve the 'diff --git' header in
    each chunk. Falls back to '--- a/' / '+++ b/' pairs for non-git
    diffs. Skips chunks with no hunk headers (binary files, pure
    renames). Returns empty list on parse failure (caller falls
    through to un-chunked path).
    """
    if not diff or not diff.strip():
        return []
    # Primary: git-style diff headers.
    chunks = re.findall(
        r'diff --git .+?(?=diff --git |\Z)', diff, re.DOTALL,
    )
    if chunks:
        return [c for c in chunks if "@@" in c]
    # Fallback: unified diff without git headers.
    chunks = re.findall(
        r'(?:^|\n)--- .+?\n\+\+\+ .+?(?=(?:\n)--- |\Z)',
        diff, re.DOTALL,
    )
    if chunks:
        return [c for c in chunks if "@@" in c]
    return []


def _run_chunk(
    chunk_diff: str,
    spawn_fn: Callable[[str, str], str],
    pass_names: tuple[str, ...],
) -> tuple[list[StateFinding], list[dict], Usage, float]:
    """Run all passes on one diff chunk.

    Returns (findings, excerpts, usage, duration). On spawn/schema
    failure for a pass, appends an INFRA finding and continues to
    the next pass (same behavior as the original loop).
    """
    findings: list[StateFinding] = []
    all_excerpts: list[dict] = []
    for pass_name in pass_names:
        raw = ""
        try:
            raw = spawn_fn(pass_name, chunk_diff)
        except Exception as e:
            findings.append(StateFinding(
                id="l1-%s-spawn-fail" % pass_name,
                fingerprint="spawn-fail-%s" % pass_name,
                source="INFRA",
                disposition=Disposition.CONFIRMED,
                file="<spawn>",
                line_range=[0, 0],
                description="spawn failed: %s" % e,
            ))
            continue
        try:
            validated = validate_reviewer_json(raw)
            findings.extend(
                # Outlet C spawns a subprocess per pass and never holds a
                # BackendConfig, so it names the outlet rather than
                # inventing a model.
                _json_to_state_findings(
                    validated, pass_name, backend="subagent",
                ),
            )
            all_excerpts.extend(_collect_excerpts(validated))
        except ValueError as e:
            findings.append(StateFinding(
                id="l1-%s-schema-fail" % pass_name,
                fingerprint="schema-fail-%s" % pass_name,
                source="INFRA",
                disposition=Disposition.CONFIRMED,
                file="<schema-validation>",
                line_range=[0, 0],
                description="schema validation failed: %s" % e,
            ))
    return (findings, all_excerpts, Usage(), 0.0)

ReviewerSpawnFn = Callable[[str, str], str]



def run_outlet_c(
    resolved_review: ResolvedReview,
    source_hash: str,
    cwd: Path,
    spawn_fn: ReviewerSpawnFn,
    falsifier: "Falsifier | None" = None,
    max_total_rounds: int = 20,
    clean_round_threshold: int = 3,
    registry: "dict | None" = None,
    backend: "object | None" = None,
    advisory_runners: "list | None" = None,
    engine: str = "auto",
) -> Verdict:
    """Run Outlet C through StateMachine with reviewer excerpt passthrough."""
    if registry is None:
        registry = {}
    if advisory_runners is None:
        advisory_runners = []
    if falsifier is None:
        from .factories import build_falsifier
        falsifier = build_falsifier(engine, backend=backend)

    def _l1_provider() -> tuple[list[StateFinding], list[dict], Usage, float]:
        diff = resolved_review.git_diff or ""
        threshold_kb = _read_chunk_threshold_kb()
        diff_kb = len(diff.encode("utf-8")) / 1024

        if threshold_kb > 0 and diff_kb <= threshold_kb:
            # Under threshold: single chunk (original behavior).
            return _run_chunk(diff, spawn_fn, _PASS_NAMES)

        # Over threshold: chunk by file.
        chunks = _split_diff_by_file(diff)
        if not chunks:
            # Parse failure or binary-only diff: fall through to
            # un-chunked path to avoid silent zero-findings.
            log.warning(
                "chunking parse produced 0 chunks from %.1fKB diff, "
                "falling through to un-chunked path", diff_kb,
            )
            return _run_chunk(diff, spawn_fn, _PASS_NAMES)

        all_findings: list[StateFinding] = []
        all_excerpts: list[dict] = []
        total_usage = Usage()
        total_duration = 0.0
        for chunk in chunks:
            c_findings, c_excerpts, c_usage, c_dur = _run_chunk(
                chunk, spawn_fn, _PASS_NAMES,
            )
            all_findings.extend(c_findings)
            all_excerpts.extend(c_excerpts)
            total_usage = Usage(
                input_tokens=(
                    total_usage.input_tokens + c_usage.input_tokens
                ),
                output_tokens=(
                    total_usage.output_tokens + c_usage.output_tokens
                ),
            )
            total_duration += c_dur

        # Dedup by fingerprint (shared first-in-wins helper, same fold
        # semantics as every factories.py L1 site).
        deduped = _dedup_by_fingerprint(all_findings)
        return (deduped, all_excerpts, total_usage, total_duration)

    sm = StateMachine(
        mode=Mode.LOCAL,
        falsifier=falsifier,
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=resolved_review,
        source_hash=source_hash,
        baseline_spec_repr="outlet-c",
        cwd=cwd,
        registry=registry,
        l1_provider=_l1_provider,
        advisory_runners=advisory_runners,
        max_total_rounds=max_total_rounds,
        clean_round_threshold=clean_round_threshold,
    )
    return sm.run()
