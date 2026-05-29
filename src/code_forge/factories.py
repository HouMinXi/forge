# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""STATE-10 falsifier + autofixer + revert_fn factories.

Centralizes "which impl do we instantiate" decisions so cli.py stays
declarative and Phase 4 can swap impls without touching the CLI.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable

from .autofix import AutoFixer, FixOutcome, StubAutoFixer
from .baseline import ResolvedReview
from .disposition import Disposition
from .falsify import Falsifier, StubFalsifier
from .e2e_check import run_e2e_check
from .mutation import run_mutation
from .state import StateFinding


def build_falsifier(engine: str) -> Falsifier:
    """STATE-10 engine factory.

    engine = "auto": try Phase 4 import; fall back to stub if absent.
    engine = "stub": always StubFalsifier.
    engine = "real": Phase 4 falsifier (NOT shipped v2.0).
    """
    if engine == "stub":
        return StubFalsifier()
    if engine == "auto":
        try:
            from .falsify_real import RealFalsifier  # noqa: F401
            return RealFalsifier()
        except ImportError:
            return StubFalsifier()
    if engine == "real":
        try:
            from .falsify_real import RealFalsifier
            return RealFalsifier()
        except ImportError:
            raise NotImplementedError(
                "--falsification-engine=real requires Phase 4 "
                "(not shipped in v2.0). Use "
                "--falsification-engine=auto or =stub."
            )
    raise ValueError(
        "unknown engine: %r (expected auto|stub|real)" % engine
    )


def build_autofixer(resolved: ResolvedReview) -> AutoFixer:
    """v2.0 returns StubAutoFixer; non-git wraps to prevent PARSE_FAIL.

    R2-M1: non-git mode wraps StubAutoFixer in _NonGitSafeAutoFixer
    to convert PARSE_FAIL -> NO_CHANGE, preventing revert_fn from
    being invoked (which would raise NotImplementedError per B1).
    """
    base = StubAutoFixer()
    if resolved.mode_hint == "non-git":
        return _NonGitSafeAutoFixer(base)
    return base


class _NonGitSafeAutoFixer(AutoFixer):
    """R2-M1 wrapper: convert PARSE_FAIL -> NO_CHANGE in non-git mode.

    Prevents StateMachine from calling revert_fn (which raises
    NotImplementedError per B1 in non-git mode). Behavior:
      - PARSE_FAIL -> NO_CHANGE (consumes fix budget; no revert call)
      - SUCCESS / NO_CHANGE / EXCEPTION pass through unchanged

    R3-7: fix signature byte-equal to AutoFixer ABC.
    """

    def __init__(self, inner: AutoFixer):
        super().__init__()
        self._inner = inner

    def fix(self, finding: StateFinding, mode_hint: str) -> FixOutcome:
        """Wrap inner fix; convert PARSE_FAIL to NO_CHANGE."""
        outcome = self._inner.fix(finding, mode_hint)
        if outcome == FixOutcome.PARSE_FAIL:
            return FixOutcome.NO_CHANGE
        return outcome


def build_revert_fn(
    resolved: ResolvedReview, cwd: Path
) -> Callable[[StateFinding], None]:
    """Build revert_fn for StateMachine constructor.

    Dispatches on resolved.mode_hint:
      "git"     -> git restore <file>
      "non-git" -> NotImplementedError (B1: v2.0 limitation)
    """
    if resolved.mode_hint == "git":
        return _make_git_restore(cwd)
    if resolved.mode_hint == "non-git":
        return _make_snapshot_restore(cwd, resolved)
    raise ValueError("unknown mode_hint: %r" % resolved.mode_hint)


def _make_git_restore(
    cwd: Path,
) -> Callable[[StateFinding], None]:
    """Git mode revert: restore file to index version."""
    def _revert(finding: StateFinding) -> None:
        subprocess.run(
            ["git", "restore", "--", finding.file],
            cwd=str(cwd), check=True,
        )
    return _revert


def _make_snapshot_restore(
    cwd: Path, resolved: ResolvedReview
) -> Callable[[StateFinding], None]:
    """B1: non-git revert NOT supported in v2.0.

    02-03 Snapshot stores content_hash only, not raw content.
    revert_fn raises NotImplementedError unconditionally.
    """
    def _revert(finding: StateFinding) -> None:
        raise NotImplementedError(
            "non-git autofix revert is not supported in v2.0 "
            "(02-03 Snapshot stores content_hash, not raw content). "
            "Use git mode for autofix, or configure autofixer to "
            "avoid PARSE_FAIL outcomes in non-git mode. "
            "Tracked as v2.x candidate."
        )
    return _revert


def build_l2_runner() -> Callable:
    """Build l2_runner (mutation testing) callable.

    Returns a callable with signature:
        (diff_files: list[str], baseline_cmd: list[str])
        -> tuple[list[StateFinding], list[str]]

    If mutmut is not on PATH, returns a no-op callable that produces
    a single MUTATION_SKIPPED finding (soft dependency).

    The returned callable delegates to run_mutation from the mutation
    module when mutmut is available.
    """
    if shutil.which("mutmut") is None:
        # mutmut not available, return no-op with MUTATION_SKIPPED
        def _no_mutation(
            diff_files: list[str],
            baseline_cmd: list[str],
        ) -> tuple[list[StateFinding], list[str]]:
            findings = [
                StateFinding(
                    id="MUTATION_SKIPPED",
                    fingerprint="mutation-unavailable",
                    source="MUTANT",
                    disposition=Disposition.DISMISSED,
                    file="",
                    line_range=[],
                    description="mutmut not installed (soft dependency)",
                )
            ]
            infra_errors = ["mutmut not found on PATH"]
            return (findings, infra_errors)
        return _no_mutation

    # mutmut is available, delegate to run_mutation
    return run_mutation


def build_e2e_checker() -> Callable:
    """Build e2e_checker callable for R3 coverage heuristic.

    Returns a callable with signature:
        (diff_text: str, repo_root: Path)
        -> tuple[list[StateFinding], list[str]]

    Unlike build_l2_runner, there is no external-binary availability check:
    e2e_check has no soft dependency (unidiff is a hard dep already used by
    diff.py). The factory returns run_e2e_check directly.

    The factory exists for symmetry with build_l2_runner so plan 03-03 can
    inject it into the state machine the same way as l2_runner, and a future
    variant (e.g. with config loading) can be swapped without touching
    machine.py.
    """
    return run_e2e_check


def build_l1_provider(
    engine: str,
    resolved: "ResolvedReview",
) -> "Callable":
    """Build l1_provider. engine="stub" returns empty lambda (defect D fix)."""
    if engine == "stub":
        return lambda: []

    import hashlib as _hl
    from .llm_invoke import LLMInvokeError, llm_invoke

    def _provider() -> list:
        diff_text = resolved.git_diff or ""
        if not diff_text:
            return []

        pass_configs = [
            ("qodo", "structural code reviewer: correctness and logic errors"),
            ("expert", "senior engineer: SOLID, architecture, security"),
            ("adversarial", "adversarial QE: assume bugs exist"),
        ]

        all_candidates = []
        seen = set()

        for pass_name, role in pass_configs:
            prompt = (
                "You are a " + role + ". Review this diff. "
                'Return JSON: {"findings": [{"file": "...", "line": N, '
                '"severity": "P0"|"P1"|"P2"|"P3", '
                '"description": "..."}]}\n\nDiff:\n' + diff_text
            )
            try:
                response = llm_invoke(prompt)
            except LLMInvokeError as exc:
                import sys
                print(
                    "code-forge: L1 pass '%s' failed: %s" % (pass_name, exc),
                    file=sys.stderr,
                )
                continue

            if not isinstance(response, dict):
                continue
            findings_raw = response.get("findings")
            if not isinstance(findings_raw, list):
                continue

            for f_raw in findings_raw:
                if not isinstance(f_raw, dict):
                    continue
                file_path = f_raw.get("file") or "unknown"
                try:
                    line = int(f_raw.get("line") or 0)
                except (ValueError, TypeError):
                    line = 0
                desc = f_raw.get("description") or ""
                fp_src = file_path + ":" + str(line) + ":" + desc
                fp = _hl.sha256(fp_src.encode()).hexdigest()[:16]
                if fp in seen:
                    continue
                seen.add(fp)
                from .disposition import Disposition
                from .state import StateFinding
                all_candidates.append(StateFinding(
                    id="l1-" + pass_name + "-" + fp,
                    fingerprint=fp, source="L1",
                    disposition=Disposition.UNCERTAIN,
                    file=file_path,
                    line_range=[line, line],
                    description="[" + pass_name + "] " + desc,
                ))
        return all_candidates

    return _provider
