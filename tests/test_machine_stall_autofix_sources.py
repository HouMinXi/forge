# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Identical-round stall: L0 findings the autofix loop skips cannot move the map (#262).

LOCAL's autofix loop skips findings whose source is in _AUTOFIX_SKIPPED_SOURCES:
no fix attempt, no budget spent, no promotion. Production L0 runners emit source
"L0" only, so these cases use an injected l0_runner or set findings directly. A
frozen CONFIRMED map pinned only by skipped-source L0 findings stalls instead of
running to max_total_rounds. Any fingerprint not proven skipped still holds the
stall off.
"""

from pathlib import Path

import pytest

from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.machine import StateMachine
from code_forge.state import Mode, StateFinding, Verdict

STALL_DIAGNOSTIC_PREFIX = "review stalled: identical disposition maps for 3 consecutive rounds"
SKIPPED_SOURCES = ["MUTANT", "E2E_CHECK", "FIXVAL", "INFRA"]


def _finding(fp, source):
    return StateFinding(
        id=fp,
        fingerprint=fp,
        source=source,
        disposition=Disposition.CONFIRMED,
        file="test.py",
        line_range=[1, 1],
        description="test finding",
    )


def _machine(tmp_path, l0_findings=(), max_rounds=8):
    return StateMachine(
        mode=Mode.LOCAL,
        falsifier=StubFalsifier(),  # default disposition: CONFIRMED
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=ResolvedReview(
            source_files=[Path("test.py")],
            baseline_content=None,
            git_diff=None,
            mode_hint="git",
        ),
        source_hash="abc",
        baseline_spec_repr="empty",
        cwd=tmp_path,
        registry={},
        l0_runner=lambda registry, files: (list(l0_findings), []),
        max_total_rounds=max_rounds,
        max_fix_attempts=3,
    )


def _freeze(machine, dispositions, l0, findings):
    row = {"dispositions": dispositions, "l0_fingerprints": list(l0)}
    machine._state.round_history = [dict(row, round=i) for i in range(3)]
    machine._state.findings = list(findings)


@pytest.mark.parametrize("source", SKIPPED_SOURCES)
def test_skipped_source_l0_finding_does_not_hold_off_the_stall(tmp_path, source):
    machine = _machine(tmp_path)
    _freeze(
        machine,
        {"fp-l0": "CONFIRMED", "fp-l1": "CONFIRMED"},
        l0=["fp-l0"],
        findings=[_finding("fp-l0", source), _finding("fp-l1", "L1")],
    )
    assert machine._stalled_on_identical_round()


def test_skipped_source_alone_pins_the_map(tmp_path):
    machine = _machine(tmp_path)
    _freeze(machine, {"fp-l0": "CONFIRMED"}, l0=["fp-l0"], findings=[_finding("fp-l0", "MUTANT")])
    assert machine._stalled_on_identical_round()


@pytest.mark.parametrize("source", ["L0", "L1", "COVERAGE", "EXEC", "RULEPACK", "UNTRUSTED"])
def test_l0_finding_the_autofix_loop_handles_still_holds_off_the_stall(tmp_path, source):
    machine = _machine(tmp_path)
    _freeze(
        machine,
        {"fp-l0": "CONFIRMED", "fp-l1": "CONFIRMED"},
        l0=["fp-l0"],
        findings=[_finding("fp-l0", source), _finding("fp-l1", "L1")],
    )
    assert not machine._stalled_on_identical_round()


def test_unknown_source_still_holds_off_the_stall(tmp_path):
    """Skipping is an allow-list: a source outside it counts as budget left."""
    machine = _machine(tmp_path)
    _freeze(
        machine,
        {"fp-l0": "CONFIRMED", "fp-l1": "CONFIRMED"},
        l0=["fp-l0"],
        findings=[_finding("fp-l0", "NEWSRC"), _finding("fp-l1", "L1")],
    )
    assert not machine._stalled_on_identical_round()


def test_fingerprint_with_two_skipped_sources_stalls(tmp_path):
    machine = _machine(tmp_path)
    _freeze(
        machine,
        {"fp-l0": "CONFIRMED"},
        l0=["fp-l0"],
        findings=[_finding("fp-l0", "MUTANT"), _finding("fp-l0", "INFRA")],
    )
    assert machine._stalled_on_identical_round()


def test_fingerprint_absent_from_current_findings_still_holds_off_the_stall(tmp_path):
    """Without proof that autofix skips the fingerprint, it counts as budget left."""
    machine = _machine(tmp_path)
    _freeze(
        machine,
        {"fp-l0": "CONFIRMED", "fp-l1": "CONFIRMED"},
        l0=["fp-l0"],
        findings=[_finding("fp-other", "MUTANT")],
    )
    assert not machine._stalled_on_identical_round()


@pytest.mark.parametrize("order", ["skipped-first", "handled-first"])
def test_fingerprint_with_mixed_sources_still_holds_off_the_stall(tmp_path, order):
    mixed = [_finding("fp-l0", "MUTANT"), _finding("fp-l0", "L0")]
    if order == "handled-first":
        mixed.reverse()
    machine = _machine(tmp_path)
    _freeze(machine, {"fp-l0": "CONFIRMED", "fp-l1": "CONFIRMED"}, l0=["fp-l0"], findings=mixed)
    assert not machine._stalled_on_identical_round()


def test_another_l0_fingerprint_with_budget_left_holds_off_the_stall(tmp_path):
    machine = _machine(tmp_path)
    _freeze(
        machine,
        {"fp-mut": "CONFIRMED", "fp-l0": "CONFIRMED"},
        l0=["fp-mut", "fp-l0"],
        findings=[_finding("fp-mut", "MUTANT"), _finding("fp-l0", "L0")],
    )
    assert not machine._stalled_on_identical_round()


def test_pending_promotion_still_runs_first(tmp_path):
    """A skipped-source finding does not cut short a pending promotion of another L0 finding."""
    machine = _machine(tmp_path)
    _freeze(
        machine,
        {"fp-mut": "CONFIRMED", "fp-l0": "CONFIRMED"},
        l0=["fp-mut", "fp-l0"],
        findings=[_finding("fp-mut", "MUTANT"), _finding("fp-l0", "L0")],
    )
    machine._state.fix_attempts = {"fp-l0": 3}
    assert not machine._stalled_on_identical_round()
    machine._state.promoted_fingerprints = {"fp-l0"}
    assert machine._stalled_on_identical_round()


@pytest.mark.parametrize("source", SKIPPED_SOURCES)
def test_frozen_skipped_source_rounds_stall_instead_of_running_to_max(tmp_path, source):
    """End to end: a CONFIRMED skipped-source L0 finding on unchanged source stops at round 3."""
    machine = _machine(tmp_path, l0_findings=[_finding("fp-frozen", source)])
    verdict = machine.run()
    assert verdict == Verdict.ESCALATED
    assert machine._state.converged is False
    assert machine._state.round == 2
    stalls = [e for e in machine._state.infra_errors if e.startswith(STALL_DIAGNOSTIC_PREFIX)]
    assert len(stalls) == 1
    assert "fp-frozen" in stalls[0]
    # The stall path spends no fix budget, promotes nothing and grants no clean credit.
    assert machine._state.fix_attempts == {}
    assert not machine._state.promoted_fingerprints
    assert machine._state.consecutive_clean_rounds == 0
