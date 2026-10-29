# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Identical-round stall: L0 findings the autofix loop skips cannot move the map (#262).

LOCAL's autofix loop skips findings whose source is in _AUTOFIX_SKIPPED_SOURCES:
no fix attempt, no budget spent, no promotion. Production L0 runners emit source
"L0" only, so these cases use an injected l0_runner, or build rounds through the
real merge and snapshot code. Each round snapshot records, in l0_autofix_skipped,
the L0 fingerprints whose every raw finding has a skipped source. A frozen
CONFIRMED map pinned only by those fingerprints stalls instead of running to
max_total_rounds. Any fingerprint that one of the three window rows does not
list holds the stall off, including every fingerprint of a row written before
the field existed.
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


def _finding(fp, source, disposition=Disposition.CONFIRMED):
    return StateFinding(
        id=f"{fp}-{source}",
        fingerprint=fp,
        source=source,
        disposition=disposition,
        file="test.py",
        line_range=[1, 1],
        description="test finding",
    )


def _no_change_autofixer(tmp_path):
    """Every fix attempt returns NO_CHANGE, so each one spends fix budget."""
    fixture = tmp_path / "autofix.json"
    fixture.write_text(json.dumps({"default": "NO_CHANGE"}), encoding="utf-8")
    return StubAutoFixer(fixture)


def _machine(tmp_path, l0_findings=(), max_rounds=8, max_fix_attempts=3, l0_runner=None):
    if l0_runner is None:

        def l0_runner(registry, files):
            return list(l0_findings), []

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
        l0_runner=l0_runner,
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


def test_snapshot_field_is_sorted(tmp_path):
    """With 14 skipped fingerprints a set iterates in sorted order with odds near 1/14!, so list() fails."""
    machine = _machine(tmp_path)
    fingerprints = [f"fp-{name}" for name in "qwertyuiopzxcv"]
    machine._append_round_snapshot(0, [_finding(fp, "MUTANT") for fp in fingerprints], [])
    row = machine._state.round_history[-1]["l0_autofix_skipped"]
    assert row == sorted(fingerprints)


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


@pytest.mark.parametrize(
    "value",
    ["x", ("x",), {"x"}, {"x": True}, None, 0],
    ids=["str", "tuple", "set", "dict", "none", "int"],
)
def test_malformed_field_holds_off_the_stall(tmp_path, value):
    """Only a list proves a skip. list(value) would read "x" from every iterable case."""
    machine = _machine(tmp_path)
    _freeze(machine, l0=[_finding("x", "MUTANT")])
    machine._state.round_history[-1]["l0_autofix_skipped"] = value
    assert not machine._stalled_on_identical_round()


def _host_row(round_index, action):
    """A host round row that product_round_history drops: pending, or without dispositions."""
    return {
        "round": round_index,
        "clean_credit_action": action,
        "phase_status": dict.fromkeys(("l0", "rulepack", "l1", "l2", "e2e", "coverage"), "not_run"),
        "acquisition_failures": [],
        "reset_observed": False,
        "source_hash": "abc",
        "reviewed_repositories": None,
        "clean_rounds_after": 0,
    }


@pytest.mark.parametrize("action", ["pending", "unavailable"])
@pytest.mark.parametrize(
    ("projected", "trailing", "stalls"),
    [(["x"], [], True), ([], ["x"], False)],
    ids=["projected-skips", "trailing-skips"],
)
def test_skip_proof_comes_from_the_projected_row(tmp_path, action, projected, trailing, stalls):
    """The skip proof is read from the same projected row as the frozen map.

    A trailing pending or dispositionless host row is not a product snapshot, so
    its l0_autofix_skipped neither proves nor disproves a skip.
    """
    machine = _machine(tmp_path)
    _freeze(machine, l0=[_finding("x", "MUTANT")])
    machine._state.round_history[-1]["l0_autofix_skipped"] = projected
    machine._state.round_history.append({**_host_row(3, action), "l0_autofix_skipped": trailing})
    assert machine._stalled_on_identical_round() is stalls


@pytest.mark.parametrize("row", [0, 1, 2])
def test_every_window_row_must_prove_the_skip(tmp_path, row):
    """A round in the window that handled the fingerprint spent budget there."""
    machine = _machine(tmp_path)
    _freeze(machine, l0=[_finding("fp-l0", "MUTANT")])
    machine._state.round_history[row]["l0_autofix_skipped"] = []
    assert not machine._stalled_on_identical_round()


@pytest.mark.parametrize("value", [None, "missing"], ids=["malformed", "missing"])
def test_an_earlier_window_row_without_proof_holds_off_the_stall(tmp_path, value):
    machine = _machine(tmp_path)
    _freeze(machine, l0=[_finding("fp-l0", "MUTANT")])
    if value == "missing":
        del machine._state.round_history[0]["l0_autofix_skipped"]
    else:
        machine._state.round_history[0]["l0_autofix_skipped"] = value
    assert not machine._stalled_on_identical_round()


def test_rows_before_the_window_do_not_matter(tmp_path):
    """Only the three window rows are read; an older row that handled fp-l0 does not count."""
    machine = _machine(tmp_path)
    machine._append_round_snapshot(0, [_finding("fp-l0", "L0")], [])
    machine._state.findings = machine._merge_findings([_finding("fp-l0", "MUTANT")], [])
    for round_index in range(1, 4):
        machine._append_round_snapshot(round_index, [_finding("fp-l0", "MUTANT")], [])
    assert machine._state.round_history[0]["l0_autofix_skipped"] == []
    assert machine._stalled_on_identical_round()


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


def test_spent_skipped_fingerprint_is_not_pending_promotion(tmp_path):
    """A fingerprint the autofix loop skips is never promoted, so spent budget cannot hold the stall."""
    machine = _machine(tmp_path)
    _freeze(machine, l0=[_finding("fp-l0", "MUTANT")])
    machine._state.fix_attempts = {"fp-l0": 3}
    assert machine._stalled_on_identical_round()


def test_spent_handled_fingerprint_is_pending_promotion(tmp_path):
    """Control: CONFIRMED plus UNCERTAIN stalls, unless an L0 budget is exactly spent."""
    machine = _machine(tmp_path)
    _freeze(machine, l0=[_finding("fp-l0", "L0")], l1=[_finding("fp-l1", "L1", Disposition.UNCERTAIN)])
    assert machine._stalled_on_identical_round()
    machine._state.fix_attempts = {"fp-l0": 3}  # == max_fix_attempts
    assert not machine._stalled_on_identical_round()


def test_attempts_above_the_limit_are_pending_promotion(tmp_path):
    """max_fix_attempts is not persisted; a resume with a lower limit leaves attempts above it."""
    machine = _machine(tmp_path, max_fix_attempts=3)
    _freeze(machine, l0=[_finding("fp-l0", "L0")], l1=[_finding("fp-l1", "L1", Disposition.UNCERTAIN)])
    machine._state.fix_attempts = {"fp-l0": 5}
    assert not machine._stalled_on_identical_round()


def test_resume_with_a_lower_fix_limit_promotes(tmp_path):
    """End to end: 4 attempts under max_fix_attempts=6, then a resume under 3 promotes."""
    first = _machine(tmp_path, l0_findings=[_finding("fp-l0", "L0")], max_rounds=4, max_fix_attempts=6)
    first.run()
    assert first._state.fix_attempts == {"fp-l0": 4}
    assert not first._state.promoted_fingerprints
    resumed = _machine(
        tmp_path, l0_findings=[_finding("fp-l0", "L0")], max_rounds=10, max_fix_attempts=3
    )
    resumed.run()
    assert resumed._state.promoted_fingerprints == {"fp-l0"}
    assert resumed._state.fix_attempts == {"fp-l0": 4}
    assert _stalls(resumed) == []


def test_resume_from_state_written_before_the_field(tmp_path):
    """End to end: rows without l0_autofix_skipped prove no skip, so they hold the stall off.

    Three old rows freeze the map. A skip needs proof in all three window rows,
    so the resumed run stalls only once three new rows carry the field (round
    index 5). Reading a missing field as a skip would stall at round index 3.
    """
    findings = [_finding("fp-x", "MUTANT")]
    _machine(tmp_path, l0_findings=findings, max_rounds=3).run()
    state_path = tmp_path / ".code-forge" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(state["round_history"]) == 3
    for row in state["round_history"]:
        row.pop("l0_autofix_skipped")
    state["infra_errors"] = []
    state_path.write_text(json.dumps(state), encoding="utf-8")

    resumed = _machine(tmp_path, l0_findings=findings, max_rounds=10)
    assert resumed.run() == Verdict.ESCALATED
    history = resumed._state.round_history
    assert ["l0_autofix_skipped" in row for row in history] == [False] * 3 + [True] * 3
    assert all(row["l0_autofix_skipped"] == ["fp-x"] for row in history[3:])
    assert resumed._state.round == 5
    assert len(_stalls(resumed)) == 1


def _round_aware_runner(machine_ref, findings_for_round):
    def runner(registry, files):
        return findings_for_round(machine_ref[0]._state.round), []

    return runner


def test_spent_budget_promotes_before_an_uncertain_map_stalls(tmp_path):
    """End to end: budget spent on round index 2 waits for its promotion on round 3.

    The frozen map holds CONFIRMED and UNCERTAIN, which stalls at once unless the
    pending-promotion guard counts fix_attempts == max_fix_attempts as pending.
    """
    findings = [_finding("fp-l0", "L0"), _finding("fp-unc", "MUTANT", Disposition.UNCERTAIN)]
    machine = _machine(tmp_path, l0_findings=findings, max_rounds=8)
    machine.run()
    assert machine._state.fix_attempts == {"fp-l0": 3}
    assert machine._state.promoted_fingerprints == {"fp-l0"}
    assert machine._state.round == 3
    assert _stalls(machine) == []


def test_budget_spent_as_l0_then_skipped_source_only_stalls(tmp_path):
    """End to end: fp-x spends its budget as L0, then arrives only as MUTANT.

    The autofix loop skips the MUTANT finding, so fp-x is never promoted. Its
    spent budget must not hold the stall off for the rest of the run.
    """
    ref = []

    def findings_for_round(round_index):
        return [_finding("fp-x", "L0" if round_index < 3 else "MUTANT")]

    machine = _machine(tmp_path, max_rounds=12, l0_runner=_round_aware_runner(ref, findings_for_round))
    ref.append(machine)
    assert machine.run() == Verdict.ESCALATED
    assert machine._state.fix_attempts == {"fp-x": 3}
    assert not machine._state.promoted_fingerprints
    stalls = _stalls(machine)
    assert len(stalls) == 1
    assert "fp-x" in stalls[0]
    assert machine._state.round == 5  # rows 3-5: the first window skipped in every row


@pytest.mark.parametrize("first", ["L0", "MUTANT"])
def test_alternating_sources_spend_the_budget_and_promote(tmp_path, first):
    """End to end: fp-x alternates between L0 and MUTANT, so every window holds a handled round.

    Each L0 round spends one attempt; the map stays frozen but the fingerprint
    is promoted after its third attempt, as on main, instead of stalling early.
    """
    other = "MUTANT" if first == "L0" else "L0"
    ref = []
    machine = _machine(
        tmp_path,
        max_rounds=12,
        l0_runner=_round_aware_runner(
            ref, lambda round_index: [_finding("fp-x", first if round_index % 2 == 0 else other)]
        ),
    )
    ref.append(machine)
    machine.run()
    assert machine._state.fix_attempts == {"fp-x": 3}
    assert machine._state.promoted_fingerprints == {"fp-x"}
    assert _stalls(machine) == []
    assert machine._state.round == (6 if first == "L0" else 7)


def test_skipped_window_before_a_handled_round_stalls(tmp_path):
    """Known limit, pinned: three MUTANT-only rounds stall at round index 2.

    The stall cannot see that the producer would switch fp-x to L0 on round 3;
    by then the review is already ESCALATED with no fix attempt spent.
    """
    ref = []
    machine = _machine(
        tmp_path,
        max_rounds=12,
        l0_runner=_round_aware_runner(
            ref, lambda round_index: [_finding("fp-x", "MUTANT" if round_index < 3 else "L0")]
        ),
    )
    ref.append(machine)
    assert machine.run() == Verdict.ESCALATED
    assert machine._state.round == 2
    assert machine._state.fix_attempts == {}
    assert len(_stalls(machine)) == 1


def test_budget_spent_as_l0_stays_pending_while_still_l0(tmp_path):
    """Control for the case above: a spent fingerprint that stays L0 is promoted, not stalled."""
    ref = []
    machine = _machine(
        tmp_path,
        max_rounds=12,
        l0_runner=_round_aware_runner(ref, lambda round_index: [_finding("fp-x", "L0")]),
    )
    ref.append(machine)
    machine.run()
    assert machine._state.fix_attempts == {"fp-x": 3}
    assert machine._state.promoted_fingerprints == {"fp-x"}
    assert _stalls(machine) == []


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
