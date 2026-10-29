# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Identical-round stall: L0 findings the autofix loop skips cannot move the map (#262).

LOCAL's autofix loop skips findings whose source is in _AUTOFIX_SKIPPED_SOURCES:
no fix attempt, no budget spent, no promotion. Production L0 runners emit source
"L0" only, so these cases use an injected l0_runner, or build rounds through the
real merge and snapshot code. Each round snapshot records, in l0_autofix_skipped,
the L0 fingerprints whose every raw finding has a skipped source. A frozen
CONFIRMED map pinned only by those fingerprints stalls instead of running to
max_total_rounds. Any fingerprint the latest snapshot does not list holds the
stall off, including every fingerprint of a snapshot written before the field
existed.
"""

import json
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
        id=f"{fp}-{source}",
        fingerprint=fp,
        source=source,
        disposition=Disposition.CONFIRMED,
        file="test.py",
        line_range=[1, 1],
        description="test finding",
    )


def _no_change_autofixer(tmp_path):
    """Every fix attempt returns NO_CHANGE, so each one spends fix budget."""
    fixture = tmp_path / "autofix.json"
    fixture.write_text(json.dumps({"default": "NO_CHANGE"}), encoding="utf-8")
    return StubAutoFixer(fixture)


def _machine(tmp_path, l0_findings=(), max_rounds=8, max_fix_attempts=3):
    return StateMachine(
        mode=Mode.LOCAL,
        falsifier=StubFalsifier(),  # default disposition: CONFIRMED
        autofixer=_no_change_autofixer(tmp_path),
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
        max_fix_attempts=max_fix_attempts,
    )


def _freeze(machine, l0, l1=()):
    """Three identical rounds built by the real merge and snapshot code."""
    machine._state.findings = machine._merge_findings(list(l0), list(l1))
    for round_index in range(3):
        machine._append_round_snapshot(round_index, list(l0), list(l1))


def _stalls(machine):
    return [e for e in machine._state.infra_errors if e.startswith(STALL_DIAGNOSTIC_PREFIX)]


def test_snapshot_records_fingerprints_whose_every_l0_finding_is_skipped(tmp_path):
    machine = _machine(tmp_path)
    l0 = [
        _finding("fp-b", "MUTANT"),
        _finding("fp-a", "INFRA"),
        _finding("fp-a", "FIXVAL"),
        _finding("fp-mixed", "L0"),
        _finding("fp-mixed", "MUTANT"),
        _finding("fp-handled", "L0"),
    ]
    machine._append_round_snapshot(0, l0, [_finding("fp-l1", "MUTANT")])
    assert machine._state.round_history[-1]["l0_autofix_skipped"] == ["fp-a", "fp-b"]


@pytest.mark.parametrize("source", SKIPPED_SOURCES)
def test_skipped_source_l0_finding_does_not_hold_off_the_stall(tmp_path, source):
    machine = _machine(tmp_path)
    _freeze(machine, l0=[_finding("fp-l0", source)], l1=[_finding("fp-l1", "L1")])
    assert machine._stalled_on_identical_round()


def test_skipped_source_alone_pins_the_map(tmp_path):
    machine = _machine(tmp_path)
    _freeze(machine, l0=[_finding("fp-l0", "MUTANT")])
    assert machine._stalled_on_identical_round()


@pytest.mark.parametrize("source", ["L0", "L1", "COVERAGE", "EXEC", "RULEPACK", "UNTRUSTED"])
def test_l0_finding_the_autofix_loop_handles_still_holds_off_the_stall(tmp_path, source):
    machine = _machine(tmp_path)
    _freeze(machine, l0=[_finding("fp-l0", source)], l1=[_finding("fp-l1", "L1")])
    assert not machine._stalled_on_identical_round()


def test_unknown_source_still_holds_off_the_stall(tmp_path):
    """Skipping is an allow-list: a source outside it counts as budget left."""
    machine = _machine(tmp_path)
    _freeze(machine, l0=[_finding("fp-l0", "NEWSRC")], l1=[_finding("fp-l1", "L1")])
    assert not machine._stalled_on_identical_round()


@pytest.mark.parametrize("sources", [["MUTANT", "INFRA"], SKIPPED_SOURCES])
def test_fingerprint_with_only_skipped_sources_stalls(tmp_path, sources):
    machine = _machine(tmp_path)
    _freeze(machine, l0=[_finding("fp-l0", source) for source in sources])
    assert machine._stalled_on_identical_round()


@pytest.mark.parametrize("order", ["skipped-last", "handled-last"])
def test_fingerprint_with_mixed_sources_still_holds_off_the_stall(tmp_path, order):
    """The merge keeps one finding per fingerprint; the snapshot still sees both."""
    mixed = [_finding("fp-l0", "L0"), _finding("fp-l0", "MUTANT")]
    if order == "handled-last":
        mixed.reverse()
    machine = _machine(tmp_path)
    _freeze(machine, l0=mixed, l1=[_finding("fp-l1", "L1")])
    assert len(machine._state.findings) == 2  # one per fingerprint
    assert not machine._stalled_on_identical_round()


def test_snapshot_without_the_field_holds_off_the_stall(tmp_path):
    """A snapshot written before l0_autofix_skipped existed proves no skip (fail closed)."""
    machine = _machine(tmp_path)
    _freeze(machine, l0=[_finding("fp-l0", "MUTANT")])
    for row in machine._state.round_history:
        del row["l0_autofix_skipped"]
    assert not machine._stalled_on_identical_round()


def test_malformed_field_holds_off_the_stall(tmp_path):
    machine = _machine(tmp_path)
    _freeze(machine, l0=[_finding("fp-l0", "MUTANT")])
    machine._state.round_history[-1]["l0_autofix_skipped"] = "fp-l0"
    assert not machine._stalled_on_identical_round()


def test_only_the_latest_snapshot_proves_a_skip(tmp_path):
    machine = _machine(tmp_path)
    _freeze(machine, l0=[_finding("fp-l0", "MUTANT")])
    machine._state.round_history[-1]["l0_autofix_skipped"] = []
    assert not machine._stalled_on_identical_round()


def test_another_l0_fingerprint_with_budget_left_holds_off_the_stall(tmp_path):
    machine = _machine(tmp_path)
    _freeze(machine, l0=[_finding("fp-mut", "MUTANT"), _finding("fp-l0", "L0")])
    assert not machine._stalled_on_identical_round()


def test_pending_promotion_still_runs_first(tmp_path):
    """A skipped-source finding does not cut short a pending promotion of another L0 finding."""
    machine = _machine(tmp_path)
    _freeze(machine, l0=[_finding("fp-mut", "MUTANT"), _finding("fp-l0", "L0")])
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
    stalls = _stalls(machine)
    assert len(stalls) == 1
    assert "fp-frozen" in stalls[0]
    # The NO_CHANGE autofixer would spend budget on any finding it saw:
    # the stall path spends none, promotes nothing and grants no clean credit.
    assert machine._state.fix_attempts == {}
    assert not machine._state.promoted_fingerprints
    assert machine._state.consecutive_clean_rounds == 0


def test_handled_l0_finding_spends_its_budget_before_the_stall(tmp_path):
    """Control for the case above: the same run with source L0 spends budget and promotes."""
    machine = _machine(tmp_path, l0_findings=[_finding("fp-frozen", "L0")], max_rounds=12)
    machine.run()
    assert machine._state.fix_attempts == {"fp-frozen": 3}
    assert machine._state.promoted_fingerprints == {"fp-frozen"}


@pytest.mark.parametrize("order", ["skipped-last", "handled-last"])
def test_duplicate_pair_spends_its_budget_before_the_stall(tmp_path, order):
    """End to end: [fp-x/L0, fp-x/MUTANT] keeps the stall off until fp-x is promoted."""
    pair = [_finding("fp-x", "L0"), _finding("fp-x", "MUTANT")]
    if order == "handled-last":
        pair.reverse()
    machine = _machine(tmp_path, l0_findings=pair, max_rounds=12, max_fix_attempts=5)
    machine.run()
    # Five NO_CHANGE attempts on rounds 0-4, promotion on round 5.
    assert machine._state.fix_attempts == {"fp-x": 5}
    assert machine._state.promoted_fingerprints == {"fp-x"}
    assert machine._state.round >= 5
    assert all(row["l0_autofix_skipped"] == [] for row in machine._state.round_history)
