# SPDX-License-Identifier: Apache-2.0
"""#114 in host-receipt mode: an L2 infrastructure error in a host attempt round.

tests/test_l2_infra_credit.py covers #114 without host receipts (no diff), so
those rounds never write a host attempt row. Here the review has a diff and
the real receipt writer, so every round is a host attempt. An L2 runner
exception, or a typed MUTATION_ERROR, must leave a row the loader accepts. A
round that otherwise completed is `interrupted` with fixpoint INCOMPLETE: the
earned window is kept and nothing new is earned. A round the receipt gate
refused, or an attempt that failed before it settled, is `unavailable`, as it
is with a healthy L2.
"""

from __future__ import annotations

import pytest

from code_forge.disposition import Disposition
from code_forge.errors import CorruptedStateError
from code_forge.llm_invoke import Usage
from code_forge.machine import TimeoutBreaker
from code_forge.state import ROUND_PHASES, StateFinding, Verdict, validate_round_history
from tests import test_review_interruption_source_controls as _sc

# Plain assignment so pytest registers the shared fixture under this name.
offline_workspace = _sc.offline_workspace

_MUTATION_ERROR_TEXT = "mutation baseline failed: constructed"


def _typed_mutation_error():
    return StateFinding(
        "MUTATION_ERROR",
        "mutation-evidence-error",
        "INFRA",
        Disposition.CONFIRMED,
        "",
        [],
        _MUTATION_ERROR_TEXT,
    )


def _l2_failing(how, rounds=None):
    """An l2_runner that fails in the given 1-based calls (all calls when None)."""
    calls = {"n": 0}

    def l2(*_args, **_kwargs):
        calls["n"] += 1
        if rounds is not None and calls["n"] not in rounds:
            return [], []
        if how == "raise":
            raise RuntimeError("constructed L2 runner failure")
        return [_typed_mutation_error()], [_MUTATION_ERROR_TEXT]

    return l2


@pytest.mark.parametrize("how", ["raise", "typed"])
def test_l2_infra_round_keeps_the_window_and_reloads(offline_workspace, monkeypatch, how):
    root = offline_workspace
    proof, original = _sc._earned_prefix(root, monkeypatch)
    machine, _ = _sc._new_machine(root, monkeypatch, rounds=1)
    machine.l2_runner = _l2_failing(how)
    assert machine.run() is Verdict.ESCALATED
    state, _ = _sc._state(root)  # loads, validates and round-trips
    assert state.consecutive_clean_rounds == 2 and state.earned_clean_window == proof
    row = state.round_history[-1]
    assert row["round"] == 2
    assert row["clean_credit_action"] == "interrupted" and row["fixpoint"] == "INCOMPLETE"
    assert row["l2_infra_error"] is True and row["phase_status"]["l2"] == "returned"
    assert row["reset_observed"] is False and row["clean_rounds_after"] == 2
    assert not _sc._verify(root).passed
    # The window was kept, not refreshed: the next healthy round earns cycle 4.
    _sc._recover(root, monkeypatch, original, [1, 2, 4])


@pytest.mark.parametrize("how", ["raise", "typed"])
def test_persistent_l2_infra_stops_with_the_breaker_not_corruption(offline_workspace, monkeypatch, how):
    root = offline_workspace
    proof, _ = _sc._earned_prefix(root, monkeypatch)
    machine, _ = _sc._new_machine(root, monkeypatch, rounds=5)
    machine.l2_runner = _l2_failing(how)
    with pytest.raises(TimeoutBreaker, match="mutation infrastructure error in 3 consecutive rounds"):
        machine.run()
    state, _ = _sc._state(root)
    assert state.consecutive_clean_rounds == 2 and state.earned_clean_window == proof
    rows = state.round_history[2:]
    assert [row["clean_credit_action"] for row in rows] == ["interrupted"] * 3
    assert all(row["l2_infra_error"] is True for row in rows)


def test_round_failing_before_l2_does_not_inherit_the_previous_round_l2_error(
    offline_workspace, monkeypatch
):
    """Round A's L2 fails; round B stops after L1, before its own L2 starts.

    Round B's failed attempt has no L2 observation. It must not be recorded as
    INCOMPLETE from round A's L2 error; it is `unavailable` with the CLEAN
    fixpoint its L1 observed.
    """
    root = offline_workspace
    _sc._earned_prefix(root, monkeypatch)
    machine, _ = _sc._new_machine(root, monkeypatch, rounds=2)
    machine.l2_runner = _l2_failing("raise", rounds={1})
    with pytest.raises(_sc._p7_raise_after_l1(monkeypatch, machine, nth=2), match="after L1 returned"):
        machine.run()
    state, _ = _sc._state(root)
    first, second = state.round_history[2:]
    assert first["clean_credit_action"] == "interrupted" and first["l2_infra_error"] is True
    assert second["clean_credit_action"] == "unavailable" and second["fixpoint"] == "CLEAN"
    assert second["phase_status"]["l2"] == "not_run" and "l2_infra_error" not in second
    # As for any attempt that failed after L1: the count stays, but an
    # unavailable round cannot prove the inherited window, so it is not restored.
    assert state.consecutive_clean_rounds == 2
    result = _sc._restore_result(root)
    assert not result.passed and "unavailable host attempt" in result.reason


@pytest.mark.parametrize("l2", ["healthy", "raise", "typed"])
def test_receipt_damage_beside_an_l2_error_fails_closed_as_with_a_healthy_l2(
    offline_workspace, monkeypatch, l2
):
    """Literal receipt damage makes the round unavailable; an L2 error beside it changes nothing.

    The L2 error alone makes the fixpoint INCOMPLETE, which on the receipt gate
    would read as an interruption that keeps the window. It must not: the
    damaged round is unavailable, the inherited window cannot be restored, and
    the next run fails without calling a reviewer, exactly as with a healthy L2.
    """
    root = offline_workspace
    _, original = _sc._earned_prefix(root, monkeypatch)
    machine, _ = _sc._new_machine(root, monkeypatch, qodo_fault="literal")
    if l2 != "healthy":
        machine.l2_runner = _l2_failing(l2)
    assert machine.run() is Verdict.FAIL
    state, _ = _sc._state(root)
    row = state.round_history[-1]
    assert row["clean_credit_action"] == "unavailable"
    if l2 == "healthy":
        assert row["fixpoint"] == "CLEAN" and "l2_infra_error" not in row
    else:
        assert row["fixpoint"] == "INCOMPLETE" and row["l2_infra_error"] is True
    result = _sc._restore_result(root)
    assert not result.passed and "unavailable host attempt" in result.reason
    recovery, recovery_calls = _sc._new_machine(root, monkeypatch)
    assert recovery.run() is Verdict.FAIL
    assert recovery_calls == []  # no blind healthy retry after unproved execution
    _sc._assert_original_receipts(original)


_FAILED_AFTER_L2 = {
    "crash-before-publish": (_sc._crash_before_publish, RuntimeError),
    "gate-read-failure": (_sc._raise_in_gate, OSError),
    "state-save-after-publish": (_sc._fail_state_save_after_publish, OSError),
}


@pytest.mark.parametrize("l2", ["healthy", "raise", "typed"])
@pytest.mark.parametrize("failure", list(_FAILED_AFTER_L2))
def test_attempt_failing_after_l2_is_unavailable_with_or_without_an_l2_error(
    offline_workspace, monkeypatch, failure, l2
):
    """An attempt that fails after its L2 returned and before the round settled proves nothing.

    With a healthy L2 it is unavailable: the inherited window cannot be
    restored and the next run fails without calling a reviewer. An L2 error in
    the same round makes the fixpoint INCOMPLETE, but must not turn that into
    an interruption that keeps the window.
    """
    root = offline_workspace
    _, original = _sc._earned_prefix(root, monkeypatch)
    machine, _ = _sc._new_machine(root, monkeypatch)
    if l2 != "healthy":
        machine.l2_runner = _l2_failing(l2)
    inject, error = _FAILED_AFTER_L2[failure]
    with monkeypatch.context() as mp:
        inject(mp, machine)
        with pytest.raises(error):
            machine.run()
    state, _ = _sc._state(root)
    row = state.round_history[-1]
    assert row["round"] == 2 and row["clean_credit_action"] == "unavailable", row
    if l2 == "healthy":
        assert "l2_infra_error" not in row
    else:
        assert row["fixpoint"] == "INCOMPLETE" and row["l2_infra_error"] is True
    result = _sc._restore_result(root)
    assert not result.passed and "unavailable host attempt" in result.reason
    recovery, recovery_calls = _sc._new_machine(root, monkeypatch)
    assert recovery.run() is Verdict.FAIL
    assert recovery_calls == []
    _sc._assert_original_receipts(original)


@pytest.mark.parametrize("l2", ["healthy", "raise", "typed"])
def test_unverifiable_evidence_beside_an_l2_error_fails_closed_as_with_a_healthy_l2(
    offline_workspace, monkeypatch, l2
):
    """Receipt acceptance runs for a round that is INCOMPLETE only through its L2 error.

    The reviewer seam returns no excerpts, so no hunk is witnessed and receipt
    acceptance refuses the round. That is `unavailable` with a healthy L2; an
    L2 error beside it must not skip the check and keep the window.
    """
    root = offline_workspace
    _, original = _sc._earned_prefix(root, monkeypatch)
    machine, _ = _sc._new_machine(root, monkeypatch)
    machine.l1_provider = lambda: ([], [], Usage(), 0.0)
    if l2 != "healthy":
        machine.l2_runner = _l2_failing(l2)
    assert machine.run() is Verdict.FAIL
    state, _ = _sc._state(root)
    row = state.round_history[-1]
    assert row["round"] == 2 and row["clean_credit_action"] == "unavailable", row
    assert row["fixpoint"] == ("CLEAN" if l2 == "healthy" else "INCOMPLETE")
    assert [e for e in state.infra_errors if "receipt acceptance: unwitnessed hunk" in e]
    result = _sc._restore_result(root)
    assert not result.passed and "unavailable host attempt" in result.reason
    recovery, recovery_calls = _sc._new_machine(root, monkeypatch)
    assert recovery.run() is Verdict.FAIL
    assert recovery_calls == []
    _sc._assert_original_receipts(original)


def test_survivor_breaker_beside_an_l2_error_is_unavailable(offline_workspace, monkeypatch):
    """The third survivor round with an L2 error is `unavailable`, as a failed attempt would be.

    A recurring P3 survivor (density threshold raised so it does not reset)
    and a typed L2 error in every round: round 1 resets on the new survivor,
    round 2 is `interrupted`, round 3 stops at the survivor breaker. (With an
    acquisition failure in that round, receipt acceptance refuses it first.)
    """
    root = offline_workspace
    monkeypatch.setattr("code_forge.flow_contract.P3_DENSITY_THRESHOLD", 1.0)
    machine, _ = _sc._new_machine(root, monkeypatch, rounds=3)
    machine.l2_runner = lambda *_a, **_k: (
        [_sc._survivor("P3"), _typed_mutation_error()],
        [_MUTATION_ERROR_TEXT],
    )
    assert machine.run() is Verdict.FAIL
    state, _ = _sc._state(root)
    assert "mutation: surviving mutants reported in 3 consecutive rounds" in state.infra_errors
    rows = state.round_history
    assert [row["clean_credit_action"] for row in rows] == ["reset", "interrupted", "unavailable"]
    assert rows[2]["fixpoint"] == "INCOMPLETE" and rows[2]["l2_infra_error"] is True


def test_survivor_breaker_beside_a_protocol_stop_is_interrupted(offline_workspace, monkeypatch):
    """The survivor breaker keeps the window for a round stopped by a falsifier protocol failure.

    Every round has a recurring P3 survivor; only round 3 has a product
    candidate, which the falsifier answers with non-JSON. Round 3 is then
    INCOMPLETE through the protocol stop (receipt acceptance does not run for
    it) and stops at the survivor breaker as `interrupted`, the same rule as a
    failed attempt or a refused receipt gate.
    """
    root = offline_workspace
    monkeypatch.setattr("code_forge.flow_contract.P3_DENSITY_THRESHOLD", 1.0)
    machine, _ = _sc._new_machine(root, monkeypatch, rounds=3, falsifier_reply="not JSON")
    candidate = _sc._candidate_payload
    monkeypatch.setattr(
        _sc,
        "_candidate_payload",
        lambda body, *args: candidate(body, *args) if machine._state.round == 2 else body,
    )
    machine.l2_runner = lambda *_a, **_k: ([_sc._survivor("P3")], [])
    assert machine.run() is Verdict.FAIL
    state, _ = _sc._state(root)
    assert "mutation: surviving mutants reported in 3 consecutive rounds" in state.infra_errors
    last = state.round_history[-1]
    assert last["clean_credit_action"] == "interrupted" and last["fixpoint"] == "INCOMPLETE", last
    assert last["falsify_protocol_failures"] and "l2_infra_error" not in last


@pytest.mark.parametrize("l2", ["healthy", "raise", "typed"])
@pytest.mark.parametrize("fault", ["missing-excerpts", "excerpt-line-count"])
def test_pure_excerpt_rejection_loses_its_kept_window_beside_an_l2_error(
    offline_workspace, monkeypatch, fault, l2
):
    """A pure excerpt rejection keeps the window as `evidence_rejected`, but not beside an L2 error.

    `evidence_rejected` needs a CLEAN fixpoint; the L2 error makes it
    INCOMPLETE, so the round is `unavailable` and the window is not restored.
    Stricter than either cause alone, on purpose (fail closed).
    """
    root = offline_workspace
    _sc._earned_prefix(root, monkeypatch)
    machine, _ = _sc._new_machine(root, monkeypatch, fault=fault)
    if l2 != "healthy":
        machine.l2_runner = _l2_failing(l2)
    assert machine.run() is Verdict.FAIL
    state, _ = _sc._state(root)
    row = state.round_history[-1]
    result = _sc._restore_result(root)
    if l2 == "healthy":
        assert row["clean_credit_action"] == "evidence_rejected" and row["fixpoint"] == "CLEAN"
        assert result.passed
    else:
        assert row["clean_credit_action"] == "unavailable" and row["fixpoint"] == "INCOMPLETE"
        assert row["l2_infra_error"] is True
        assert not result.passed and "unavailable host attempt" in result.reason


def test_attempt_failing_before_its_round_carries_no_earlier_l2_error(offline_workspace, monkeypatch):
    """Round A's L2 fails; round B's attempt fails before `_execute_round` starts.

    Round B records no L2 observation (its L2 never ran), so its row carries
    no L2 error and the state reloads. The clear when the attempt begins is
    the guard (also pinned by the round-B test above); the row writer's check
    that this round's L2 returned is a second line of defence.
    """
    root = offline_workspace
    _sc._earned_prefix(root, monkeypatch)
    machine, _ = _sc._new_machine(root, monkeypatch, rounds=2)
    machine.l2_runner = _l2_failing("raise", rounds={1})
    real = machine._start_host_execution
    seen = {"n": 0}

    def start():
        seen["n"] += 1
        if seen["n"] == 2:
            raise RuntimeError("constructed failure before the round")
        return real()

    monkeypatch.setattr(machine, "_start_host_execution", start)
    with pytest.raises(RuntimeError, match="before the round"):
        machine.run()
    state, _ = _sc._state(root)
    first, second = state.round_history[2:]
    assert first["l2_infra_error"] is True
    assert second["phase_status"]["l2"] == "not_run" and "l2_infra_error" not in second


@pytest.mark.parametrize("l2", ["healthy", "raise", "typed"])
def test_acquisition_failure_beside_an_l2_error_keeps_the_window(offline_workspace, monkeypatch, l2):
    """An acquisition failure interrupts a round receipt acceptance refuses, L2 error or not.

    The adversarial pass fails to acquire (no JSON). Receipt acceptance refuses
    the incomplete round, and the acquisition failure makes it `interrupted`:
    the window is kept and restorable, with or without an L2 error beside it.
    """
    root = offline_workspace
    proof, _ = _sc._earned_prefix(root, monkeypatch)
    machine, _ = _sc._new_machine(root, monkeypatch, fault="no-json")
    if l2 != "healthy":
        machine.l2_runner = _l2_failing(l2)
    assert machine.run() is Verdict.FAIL
    state, _ = _sc._state(root)
    row = state.round_history[-1]
    assert row["clean_credit_action"] == "interrupted" and row["acquisition_failures"], row
    if l2 == "healthy":
        assert row["fixpoint"] == "CLEAN" and "l2_infra_error" not in row
    else:
        assert row["fixpoint"] == "INCOMPLETE" and row["l2_infra_error"] is True
    assert state.consecutive_clean_rounds == 2 and state.earned_clean_window == proof
    assert _sc._restore_result(root).passed


# Loader: the L2 infrastructure observation on a host row.
_SOURCE = "a" * 64


def _phases(**overrides):
    phases = {name: "returned" for name in ROUND_PHASES}
    phases.update(overrides)
    return phases


def _row(index, action, count, *, fixpoint, **extra):
    row = {
        "round": index,
        "source_hash": _SOURCE,
        "reviewed_repositories": None,
        "clean_credit_action": action,
        "phase_status": _phases(),
        "acquisition_failures": [],
        "reset_observed": False,
        "clean_rounds_after": count,
        "fixpoint": fixpoint,
        "dispositions": {},
    }
    row.update(extra)
    return row


def _history(last):
    return [_row(0, "earned", 1, fixpoint="CLEAN"), _row(1, "earned", 2, fixpoint="CLEAN"), last]


def test_loader_accepts_an_interrupted_l2_infra_round():
    validate_round_history(
        _history(_row(2, "interrupted", 2, fixpoint="INCOMPLETE", l2_infra_error=True)), 2
    )


def test_loader_refuses_incomplete_without_a_cause():
    with pytest.raises(CorruptedStateError, match="lacks nonreset acquisition observation"):
        validate_round_history(_history(_row(2, "interrupted", 2, fixpoint="INCOMPLETE")), 2)
    with pytest.raises(CorruptedStateError, match="lacks a protocol or L2 cause"):
        validate_round_history(_history(_row(2, "unavailable", 2, fixpoint="INCOMPLETE")), 2)


def test_loader_accepts_an_unavailable_l2_infra_round():
    """The receipt gate refused this round's evidence; its INCOMPLETE comes from the L2 error."""
    row = _row(2, "unavailable", 2, fixpoint="INCOMPLETE", l2_infra_error=True)
    validate_round_history(_history(row), 2)


@pytest.mark.parametrize("value", [False, 1, "true", None, {}])
def test_loader_refuses_a_non_true_l2_infra_value(value):
    row = _row(2, "interrupted", 2, fixpoint="INCOMPLETE", l2_infra_error=value)
    with pytest.raises(CorruptedStateError, match="invalid L2 infrastructure observation"):
        validate_round_history(_history(row), 2)


@pytest.mark.parametrize("status", ["not_run", "failed"])
def test_loader_refuses_l2_infra_without_a_returned_l2(status):
    row = _row(2, "interrupted", 2, fixpoint="INCOMPLETE", l2_infra_error=True)
    row["phase_status"] = _phases(l2=status)
    with pytest.raises(CorruptedStateError, match="invalid L2 infrastructure observation"):
        validate_round_history(_history(row), 2)


_ACQUISITION_FAILURE = {
    "id": "l1-qodo-invoke-fail",
    "fingerprint": "invoke-fail-qodo",
    "pass_name": "qodo",
    "outcome": "error",
}


@pytest.mark.parametrize("action", ["earned", "evidence_rejected", "interrupted", "unavailable"])
def test_loader_refuses_an_l2_infra_error_beside_clean(action):
    """An L2 error turns CLEAN into INCOMPLETE, so no row records both."""
    extra = {}
    if action == "evidence_rejected":
        extra["evidence_rejections"] = [
            {"pass_name": "qodo", "attempt_index": 0, "artifact_sha256": "0" * 64}
        ]
    if action == "interrupted":
        extra["acquisition_failures"] = [dict(_ACQUISITION_FAILURE)]
    row = _row(2, action, 3 if action == "earned" else 2, fixpoint="CLEAN", **extra)
    validate_round_history(_history(row), 2)  # the same row without the L2 error is valid
    row["l2_infra_error"] = True
    with pytest.raises(CorruptedStateError, match="invalid L2 infrastructure observation"):
        validate_round_history(_history(row), 2)


def test_loader_keeps_reset_rows_with_an_l2_infra_error():
    """A product reset beside an L2 error records both; the reset still clears credit."""
    row = _row(2, "reset", 0, fixpoint="RESET", l2_infra_error=True, reset_observed=True)
    validate_round_history(_history(row), 2)
