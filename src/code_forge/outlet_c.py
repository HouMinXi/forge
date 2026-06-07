# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Outlet C (subagent) orchestrator.

Routes subagent review through the same StateMachine that Outlet A uses,
ensuring identical receipt writing and cycle-counting behavior.
Reviewer-provided code_excerpts flow: reviewer JSON -> l1_provider
4-tuple -> machine.py -> receipt.py -> verify.py.
"""
from __future__ import annotations

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
    _json_to_state_findings,
)
from .state import Mode, StateFinding, Verdict

_PASS_NAMES = ["qodo", "expert", "adversarial"]

ReviewerSpawnFn = Callable[[str, str], str]



def run_outlet_c(
    resolved_review: ResolvedReview,
    source_hash: str,
    cwd: Path,
    spawn_fn: ReviewerSpawnFn,
    falsifier: "Falsifier | None" = None,
    max_total_rounds: int = 20,
) -> Verdict:
    """Run Outlet C through StateMachine with reviewer excerpt passthrough."""
    if falsifier is None:
        from .factories import build_falsifier
        # No backend passed -- thread one through run_outlet_c before Outlet C goes live.
        falsifier = build_falsifier("auto")

    def _l1_provider() -> tuple[list[StateFinding], list[dict], Usage, float]:
        findings: list[StateFinding] = []
        all_excerpts: list[dict] = []
        for pass_name in _PASS_NAMES:
            raw = ""
            try:
                raw = spawn_fn(pass_name, resolved_review.git_diff or "")
            except Exception as e:
                findings.append(StateFinding(
                    id="l1-%s-spawn-fail" % pass_name,
                    fingerprint="spawn-fail-%s" % pass_name,
                    source="L1",
                    disposition=Disposition.CONFIRMED,
                    file="<spawn>",
                    line_range=[0, 0],
                    description="spawn failed: %s" % e,
                ))
                continue
            try:
                validated = validate_reviewer_json(raw)
                findings.extend(
                    _json_to_state_findings(validated, pass_name),
                )
                all_excerpts.extend(_collect_excerpts(validated))
            except ValueError as e:
                findings.append(StateFinding(
                    id="l1-%s-schema-fail" % pass_name,
                    fingerprint="schema-fail-%s" % pass_name,
                    source="L1",
                    disposition=Disposition.CONFIRMED,
                    file="<schema-validation>",
                    line_range=[0, 0],
                    description="schema validation failed: %s" % e,
                ))
        return (findings, all_excerpts, Usage(), 0.0)

    sm = StateMachine(
        mode=Mode.LOCAL,
        falsifier=falsifier,
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=resolved_review,
        source_hash=source_hash,
        baseline_spec_repr="outlet-c",
        cwd=cwd,
        registry={},
        l0_runner=lambda reg, files: ([], []),
        l1_provider=_l1_provider,
        l2_runner=lambda df, bc: ([], []),
        e2e_runner=lambda dt, rr: ([], []),
        max_total_rounds=max_total_rounds,
    )
    return sm.run()
