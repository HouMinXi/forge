# SPDX-License-Identifier: Apache-2.0
"""#114 in host-receipt mode: an L2 infrastructure error interrupts the round.

tests/test_l2_infra_credit.py covers #114 without host receipts (no diff), so
those rounds never write a host attempt row. Here the review has a diff and
the real receipt writer, so every round is a host attempt. An L2 runner
exception, or a typed MUTATION_ERROR, must leave a row the loader accepts:
`interrupted`, fixpoint INCOMPLETE, the earned window kept and nothing new
earned. Before this, the machine wrote that row and its own loader refused it
as corrupt, so the review stopped with CorruptedStateError.
"""

from __future__ import annotations

import copy

import pytest

from code_forge.disposition import Disposition
from code_forge.errors import CorruptedStateError
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
    state, raw = _sc._state(root)  # loads, validates and round-trips
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
    INCOMPLETE from round A's L2 error (which also left a row the loader
    refused); it is `unavailable` with the CLEAN fixpoint its L1 observed.
    """
    root = offline_workspace
    _sc._earned_prefix(root, monkeypatch)
    machine, _ = _sc._new_machine(root, monkeypatch, rounds=2)
    machine.l2_runner = _l2_failing("raise", rounds={1})
    real = machine._downgrade_one_line_slips
    seen = {"n": 0}

    def downgrade(*args, **kwargs):
        seen["n"] += 1
        if seen["n"] == 2:
            raise RuntimeError("constructed failure after L1 returned")
        return real(*args, **kwargs)

    monkeypatch.setattr(machine, "_downgrade_one_line_slips", downgrade)
    with pytest.raises(RuntimeError, match="after L1 returned"):
        machine.run()
    state, _ = _sc._state(root)
    first, second = state.round_history[2:]
    assert first["clean_credit_action"] == "interrupted" and first["l2_infra_error"] is True
    assert second["clean_credit_action"] == "unavailable" and second["fixpoint"] == "CLEAN"
    assert second["phase_status"]["l2"] == "not_run" and "l2_infra_error" not in second
    assert state.consecutive_clean_rounds == 2


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


def test_loader_still_refuses_incomplete_without_a_cause():
    with pytest.raises(CorruptedStateError, match="lacks nonreset acquisition observation"):
        validate_round_history(_history(_row(2, "interrupted", 2, fixpoint="INCOMPLETE")), 2)


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


@pytest.mark.parametrize("action", ["earned", "evidence_rejected"])
def test_loader_refuses_credit_beside_an_l2_infra_error(action):
    extra = {"l2_infra_error": True}
    if action == "evidence_rejected":
        extra["evidence_rejections"] = [
            {"pass_name": "qodo", "attempt_index": 0, "artifact_sha256": "0" * 64}
        ]
    row = _row(2, action, 3, fixpoint="CLEAN", **extra)
    if action == "evidence_rejected":
        row["clean_rounds_after"] = 2
    with pytest.raises(CorruptedStateError, match="lacks CLEAN observation"):
        validate_round_history(_history(row), 2)
    del row["l2_infra_error"]  # the same row without the L2 error is valid
    validate_round_history(_history(row), 2)


def test_loader_keeps_reset_rows_with_an_l2_infra_error():
    """A product reset beside an L2 error records both; the reset still clears credit."""
    row = _row(
        2, "reset", 0, fixpoint="RESET", l2_infra_error=True, reset_observed=True, dispositions={}
    )
    validate_round_history(_history(copy.deepcopy(row)), 2)
