# SPDX-License-Identifier: Apache-2.0
"""Unavailable host attempts cannot claim an unobserved review fixpoint."""

from copy import deepcopy

import pytest

from code_forge.autofix import NoChangeAutoFixer
from code_forge.errors import CorruptedStateError
from code_forge.machine import TimeoutBreaker, _FixpointResult
from code_forge.state import (
    ROUND_PHASES,
    Disposition,
    State,
    StateFinding,
    Verdict,
    earned_history_cycles,
    load_state,
    product_round_history,
    save_state,
    validate_round_history,
)
from tests import test_resume_receipt_provenance as pipeline

review_workspace = pipeline.review_workspace
SOURCE = pipeline.SOURCE_HASH
MISSING_OBSERVATION = "unavailable host round has fixpoint without L1 observation"


def _unavailable_row(l0, l1):
    phases = dict.fromkeys(ROUND_PHASES, "not_run")
    phases.update(l0=l0, l1=l1)
    row = {
        "round": 0,
        "source_hash": SOURCE,
        "reviewed_repositories": None,
        "clean_credit_action": "unavailable",
        "phase_status": phases,
        "acquisition_failures": [],
        "reset_observed": False,
        "clean_rounds_after": 0,
    }
    if "returned" in phases.values():
        row["dispositions"] = {}
    return row


@pytest.mark.parametrize("l0", ["not_run", "returned"])
@pytest.mark.parametrize("l1", ["not_run", "failed"])
@pytest.mark.parametrize("fixpoint", ["CLEAN", "RESET", "CYCLE_RESTART", "INCOMPLETE", None, "unknown"])
def test_unavailable_nonreturned_l1_rejects_every_present_fixpoint(tmp_path, l0, l1, fixpoint):
    row = _unavailable_row(l0, l1)
    row["fixpoint"] = fixpoint
    state = State(source_hash=SOURCE, round_history=[row])
    with pytest.raises(CorruptedStateError, match=MISSING_OBSERVATION):
        validate_round_history([row])
    with pytest.raises(CorruptedStateError, match=MISSING_OBSERVATION):
        product_round_history([row])
    with pytest.raises(CorruptedStateError, match=MISSING_OBSERVATION):
        earned_history_cycles(state, SOURCE, None)
    path = tmp_path / "state.json"
    save_state(state, path)
    original = path.read_bytes()
    with pytest.raises(CorruptedStateError, match=MISSING_OBSERVATION):
        load_state(path)
    assert path.read_bytes() == original  # Loading must not silently repair old histories.


@pytest.mark.parametrize("l0", ["not_run", "returned"])
@pytest.mark.parametrize("l1", ["not_run", "failed"])
def test_unavailable_without_fixpoint_round_trips_without_earning(tmp_path, l0, l1):
    row = _unavailable_row(l0, l1)
    state = State(source_hash=SOURCE, round_history=[row])
    validate_round_history([row], 0)
    path = tmp_path / "state.json"
    save_state(state, path)
    restored = load_state(path)
    assert restored.round_history == [row]
    assert product_round_history(restored.round_history) == ([row] if l0 == "returned" else [])
    assert earned_history_cycles(restored, SOURCE, None) == []
    assert restored.consecutive_clean_rounds == 0


def test_legacy_clean_history_retains_its_existing_interpretation(tmp_path):
    row = {"round": 0, "fixpoint": "CLEAN", "dispositions": {}, "clean_rounds_after": 1}
    state = State(source_hash=SOURCE, round_history=[row], consecutive_clean_rounds=1)
    validate_round_history([row], 0)
    path = tmp_path / "state.json"
    save_state(state, path)
    restored = load_state(path)
    assert restored.round_history == product_round_history([row]) == [row]
    assert earned_history_cycles(restored, SOURCE, None) == [1]


@pytest.mark.parametrize("prefix_rounds", [0, 2])
@pytest.mark.parametrize("boundary", ["all_not_run", "l0_failed", "l0_returned", "l1_failed"])
def test_actual_unavailable_failure_omits_fixpoint_and_keeps_primary(
    review_workspace, monkeypatch, boundary, prefix_rounds
):
    root = review_workspace
    if prefix_rounds:
        pipeline._machine(root, monkeypatch, rounds=prefix_rounds).run()
    machine = pipeline._machine(root, monkeypatch, rounds=1)
    primary = TimeoutBreaker("original phase failure")

    def fail(*args):
        raise primary

    method = {
        "all_not_run": "_execute_round",
        "l0_failed": "_run_l0_phase",
        "l0_returned": "_run_rulepack_blocking_phase",
        "l1_failed": "l1_provider",
    }[boundary]
    monkeypatch.setattr(machine, method, fail)
    with pytest.raises(TimeoutBreaker) as caught:
        machine.run()
    assert caught.value is primary
    state = load_state(root / ".code-forge" / "state.json")
    row = state.round_history[-1]
    assert row["clean_credit_action"] == "unavailable"
    assert "fixpoint" not in row
    assert row["phase_status"]["l0"] == (
        "not_run" if boundary == "all_not_run" else "failed" if boundary == "l0_failed" else "returned"
    )
    assert row["phase_status"]["l1"] == ("failed" if boundary == "l1_failed" else "not_run")
    assert row["reset_observed"] is False
    assert state.verdict == Verdict.FAIL
    assert state.converged is False
    assert state.consecutive_clean_rounds == row["clean_rounds_after"] == prefix_rounds
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == list(
        range(1, prefix_rounds + 1)
    )
    projected = product_round_history(state.round_history)
    assert len(projected) == prefix_rounds + (boundary in ("l0_returned", "l1_failed"))
    if prefix_rounds:
        with pytest.raises(CorruptedStateError, match="cannot prove"):
            earned_history_cycles(state, SOURCE, None)
    else:
        assert earned_history_cycles(state, SOURCE, None) == []
    assert not any("host attempt save failed" in error for error in state.infra_errors)


@pytest.mark.parametrize("phase", ["l0", "rulepack"])
def test_actual_pre_l1_product_reset_is_preserved(review_workspace, monkeypatch, phase):
    root = review_workspace
    pipeline._machine(root, monkeypatch, rounds=2).run()
    machine = pipeline._machine(root, monkeypatch, rounds=1)
    machine.autofixer = NoChangeAutoFixer()
    finding = StateFinding(
        "observed-product",
        "observed-product",
        phase.upper(),
        Disposition.CONFIRMED,
        "control.ts",
        [2, 2],
        "P1: actual product",
    )
    method = "_run_l0_phase" if phase == "l0" else "_run_rulepack_blocking_phase"
    monkeypatch.setattr(machine, method, lambda: [finding])
    primary = TimeoutBreaker("original L1 failure")

    def fail():
        raise primary

    machine.l1_provider = fail
    with pytest.raises(TimeoutBreaker) as caught:
        machine.run()
    assert caught.value is primary
    state = load_state(root / ".code-forge" / "state.json")
    row = state.round_history[-1]
    assert row["phase_status"][phase] == "returned"
    assert row["phase_status"]["l1"] == "failed"
    assert row["clean_credit_action"] == "reset"
    assert row["fixpoint"] == "RESET"
    assert row["reset_observed"] is True
    assert state.verdict == Verdict.FAIL
    assert state.consecutive_clean_rounds == 0
    assert state.earned_clean_window["cycles"] == []
    assert product_round_history(state.round_history)[-1]["dispositions"] == {
        "observed-product": "CONFIRMED"
    }
    assert earned_history_cycles(state, SOURCE, None) == []


@pytest.mark.parametrize("kind", ["zero", "partial"])
def test_returned_l1_receipt_failure_retains_actual_clean(review_workspace, monkeypatch, kind):
    import code_forge.receipt as receipt_module

    root = review_workspace
    machine = pipeline._machine(root, monkeypatch, rounds=1)
    real_writer = receipt_module.write_receipts
    primary = TimeoutBreaker("original post-L1 failure")

    def writer(**kwargs):
        if kind == "partial":
            real_writer(**kwargs)
            (root / ".code-forge" / "receipts" / "receipt-c1p2.json").unlink()
        raise OSError("receipt publication failed")

    def fail(findings):
        raise primary

    monkeypatch.setattr(receipt_module, "write_receipts", writer)
    monkeypatch.setattr(machine, "_check_l1_can_still_converge", fail)
    with pytest.raises(TimeoutBreaker) as caught:
        machine.run()
    assert caught.value is primary
    state = load_state(root / ".code-forge" / "state.json")
    row = state.round_history[-1]
    assert row["phase_status"]["l1"] == "returned"
    assert row["clean_credit_action"] == "unavailable"
    assert row["fixpoint"] == "CLEAN"
    assert row["reset_observed"] is False
    assert state.verdict == Verdict.FAIL
    assert state.consecutive_clean_rounds == 0
    assert state.earned_clean_window["cycles"] == []
    assert product_round_history(state.round_history) == [row]
    assert earned_history_cycles(state, SOURCE, None) == []


def test_returned_l1_receipt_gate_rejection_retains_actual_clean(review_workspace, monkeypatch):
    root = review_workspace

    def remove_receipt(round_index):
        (root / ".code-forge" / "receipts" / f"receipt-c{round_index + 1}p2.json").unlink()

    machine = pipeline._machine(root, monkeypatch, rounds=1, hook=remove_receipt)
    assert machine.run() == Verdict.FAIL
    state = load_state(root / ".code-forge" / "state.json")
    row = state.round_history[-1]
    assert set(row["phase_status"].values()) == {"returned"}
    assert row["clean_credit_action"] == "unavailable"
    assert row["fixpoint"] == "CLEAN"
    assert row["reset_observed"] is False
    assert state.consecutive_clean_rounds == 0
    assert state.earned_clean_window["cycles"] == []
    assert product_round_history(state.round_history) == [row]
    assert earned_history_cycles(state, SOURCE, None) == []


def test_returned_l1_survivor_limit_retains_actual_clean(review_workspace, monkeypatch):
    root = review_workspace
    added = [f"const value{index} = {index};\n" for index in range(20)]
    content = "const context = 1;\n" + "".join(added) + "const end = 3;\n"
    diff = (
        "diff --git a/control.ts b/control.ts\n--- a/control.ts\n+++ b/control.ts\n"
        "@@ -1,2 +1,22 @@\n const context = 1;\n"
        + "".join("+" + line for line in added)
        + " const end = 3;\n"
    )
    (root / "control.ts").write_text(content)
    (root / ".code-forge" / "gate.yaml").write_text(
        "verify:\n  required_cycles: 3\ntest:\n  command: ['true']\n"
    )
    payload = {
        "findings": [],
        "code_excerpts": [{"file": "control.ts", "start_line": 1, "end_line": 22, "content": content}],
    }
    machine = pipeline._machine(root, monkeypatch, rounds=3, diff=diff, payload=lambda *args: payload)

    def survivor(*args, **kwargs):
        return [
            StateFinding(
                "mutant-observed",
                "observed-survivor",
                "MUTANT",
                Disposition.CONFIRMED,
                "control.ts",
                [2, 2],
                "P3: recurring survivor",
            )
        ], []

    machine.l2_runner = survivor
    assert machine.run() == Verdict.FAIL
    state = load_state(root / ".code-forge" / "state.json")
    row = state.round_history[-1]
    assert state.consecutive_survivor_rounds == 3
    assert [entry["fixpoint"] for entry in state.round_history] == ["RESET", "CLEAN", "CLEAN"]
    assert row["phase_status"]["l1"] == "returned"
    assert row["clean_credit_action"] == "unavailable"
    assert row["reset_observed"] is False
    assert state.consecutive_clean_rounds == row["clean_rounds_after"] == 1
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == [2]
    assert product_round_history(state.round_history) == state.round_history
    with pytest.raises(CorruptedStateError, match="cannot prove"):
        earned_history_cycles(state, machine.source_hash, None)


@pytest.mark.parametrize("fixpoint", [None, _FixpointResult.CLEAN])
@pytest.mark.parametrize("stale_location", ["row", "snapshot"])
def test_finish_removes_stale_fixpoint_when_observation_is_absent(
    review_workspace, monkeypatch, fixpoint, stale_location
):
    machine = pipeline._machine(review_workspace, monkeypatch, rounds=1)
    machine._state.earned_clean_window = {
        "version": 1,
        "source_hash": SOURCE,
        "reviewed_repositories": None,
        "cycles": [],
    }
    machine._begin_host_attempt(0)
    row = machine._state.round_history[-1]
    if stale_location == "row":
        row["fixpoint"] = "CLEAN"
    else:
        machine._attempt_snapshot = {"fixpoint": "CLEAN"}
    window = deepcopy(machine._state.earned_clean_window)
    machine._finish_host_attempt("unavailable", fixpoint)
    assert "fixpoint" not in row
    assert row["reset_observed"] is False
    assert row["clean_rounds_after"] == machine._state.consecutive_clean_rounds == 0
    assert machine._state.earned_clean_window == window
    validate_round_history([row], 0)
