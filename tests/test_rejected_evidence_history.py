# SPDX-License-Identifier: Apache-2.0
"""Rejected evidence preserves only the CLEAN credit established before it."""

from copy import deepcopy

import pytest

from code_forge.errors import CorruptedStateError
from code_forge.state import (
    ROUND_PHASES,
    State,
    earned_history_cycles,
    is_host_round,
    load_state,
    product_round_history,
    save_state,
    validate_round_history,
)


SOURCE = "a" * 64


def _rejection(role="adversarial", attempt_index=0):
    return {
        "pass_name": role,
        "attempt_index": attempt_index,
        "artifact_sha256": "b" * 64,
    }


def _row(index, action, count):
    row = {
        "round": index,
        "source_hash": SOURCE,
        "reviewed_repositories": None,
        "clean_credit_action": action,
        "phase_status": {name: "returned" for name in ROUND_PHASES},
        "acquisition_failures": [],
        "reset_observed": False,
        "clean_rounds_after": count,
        "fixpoint": "CLEAN",
        "dispositions": {},
    }
    if action == "evidence_rejected":
        row["evidence_rejections"] = [_rejection()]
    return row


def _state(rows, count):
    return State(
        round=rows[-1]["round"],
        source_hash=SOURCE,
        round_history=rows,
        consecutive_clean_rounds=count,
    )


def _prefix():
    return [_row(0, "earned", 1), _row(1, "earned", 2)]


def test_rejected_evidence_preserves_prefix_and_next_real_cycle(tmp_path):
    rows = [*_prefix(), _row(2, "evidence_rejected", 2)]
    state = _state(rows, 2)
    validate_round_history(rows, 2)
    assert earned_history_cycles(state, SOURCE, None) == [1, 2]
    assert product_round_history(rows) == rows
    path = tmp_path / "state.json"
    save_state(state, path)
    restored = load_state(path)
    assert restored.round_history == rows
    assert earned_history_cycles(restored, SOURCE, None) == [1, 2]
    restored.round_history.append(_row(3, "earned", 3))
    restored.round = 3
    restored.consecutive_clean_rounds = 3
    assert earned_history_cycles(restored, SOURCE, None) == [1, 2, 4]


def test_rejected_evidence_accepts_known_roles_and_distinct_attempts():
    row = _row(0, "evidence_rejected", 0)
    row["evidence_rejections"] = [
        _rejection("qodo", 0),
        _rejection("expert", 0),
        _rejection("adversarial", 0),
        _rejection("adversarial", 1),
    ]
    assert earned_history_cycles(_state([row], 0), SOURCE, None) == []


@pytest.mark.parametrize("value", [None, [], {}, (), "rejected", [None], [{}]])
def test_rejected_evidence_requires_nonempty_closed_entries(value):
    row = _row(0, "evidence_rejected", 0)
    row["evidence_rejections"] = value
    with pytest.raises(CorruptedStateError):
        validate_round_history([row])


@pytest.mark.parametrize("field", ["pass_name", "attempt_index", "artifact_sha256"])
def test_rejected_evidence_requires_each_entry_field(field):
    row = _row(0, "evidence_rejected", 0)
    row["evidence_rejections"][0].pop(field)
    with pytest.raises(CorruptedStateError):
        validate_round_history([row])


@pytest.mark.parametrize(
    "field,value",
    [
        ("pass_name", "unknown"),
        ("pass_name", "QODO"),
        ("pass_name", None),
        ("attempt_index", True),
        ("attempt_index", False),
        ("attempt_index", -1),
        ("attempt_index", 0.0),
        ("attempt_index", "0"),
        ("attempt_index", None),
        ("artifact_sha256", "B" * 64),
        ("artifact_sha256", "b" * 63),
        ("artifact_sha256", "b" * 65),
        ("artifact_sha256", "g" * 64),
        ("artifact_sha256", "b" * 63 + "\n"),
        ("artifact_sha256", 0),
        ("artifact_sha256", None),
    ],
)
def test_rejected_evidence_rejects_malformed_entry_values(field, value):
    row = _row(0, "evidence_rejected", 0)
    row["evidence_rejections"][0][field] = value
    with pytest.raises(CorruptedStateError):
        validate_round_history([row])


def test_rejected_evidence_rejects_extra_keys_and_duplicate_identity():
    row = _row(0, "evidence_rejected", 0)
    row["evidence_rejections"][0]["model_claim"] = True
    with pytest.raises(CorruptedStateError):
        validate_round_history([row])
    row = _row(0, "evidence_rejected", 0)
    duplicate = _rejection()
    duplicate["artifact_sha256"] = "c" * 64
    row["evidence_rejections"].append(duplicate)
    with pytest.raises(CorruptedStateError):
        validate_round_history([row])


def test_rejected_evidence_requires_the_authority_field():
    row = _row(0, "evidence_rejected", 0)
    row.pop("evidence_rejections")
    with pytest.raises(CorruptedStateError):
        validate_round_history([row])


@pytest.mark.parametrize("action", ["pending", "earned", "interrupted", "reset", "unavailable"])
def test_rejection_authority_is_illegal_on_other_actions(action):
    row = _row(0, "earned", 1)
    row["clean_credit_action"] = action
    if action == "pending":
        row.pop("fixpoint")
        row.pop("dispositions")
        row["phase_status"] = {name: "not_run" for name in ROUND_PHASES}
    elif action == "interrupted":
        row["acquisition_failures"] = [{
            "id": "l1-qodo-invoke-fail", "fingerprint": "invoke-fail-qodo",
            "pass_name": "qodo", "outcome": "error",
        }]
    elif action == "reset":
        row.update(reset_observed=True, clean_rounds_after=0, fixpoint="RESET")
    validate_round_history([row])
    row["evidence_rejections"] = [_rejection()]
    with pytest.raises(CorruptedStateError):
        validate_round_history([row])


@pytest.mark.parametrize("value", [None, [], [_rejection()]])
def test_rejection_field_alone_cannot_borrow_legacy_semantics(tmp_path, value):
    row = {"round": 0, "fixpoint": "CLEAN", "clean_rounds_after": 1}
    assert not is_host_round(row)
    row["evidence_rejections"] = value
    assert is_host_round(row)
    with pytest.raises(CorruptedStateError):
        validate_round_history([row])
    path = tmp_path / "state.json"
    save_state(_state([row], 1), path)
    with pytest.raises(CorruptedStateError):
        load_state(path)


@pytest.mark.parametrize("phase", ROUND_PHASES)
@pytest.mark.parametrize("status", ["not_run", "failed"])
def test_rejected_evidence_requires_every_phase_returned(phase, status):
    row = _row(0, "evidence_rejected", 0)
    row["phase_status"][phase] = status
    with pytest.raises(CorruptedStateError):
        validate_round_history([row])


@pytest.mark.parametrize(
    "field,value",
    [
        ("fixpoint", None),
        ("fixpoint", "RESET"),
        ("fixpoint", "INCOMPLETE"),
        ("reset_observed", True),
        ("reset_observed", 0),
        ("dispositions", None),
        ("dispositions", {"candidate": "invalid"}),
        ("acquisition_failures", [{
            "id": "l1-qodo-invoke-fail", "fingerprint": "invoke-fail-qodo",
            "pass_name": "qodo", "outcome": "error",
        }]),
        ("falsify_protocol_failures", ["candidate"]),
    ],
)
def test_rejected_evidence_requires_clean_nonreset_product_observation(field, value):
    row = _row(0, "evidence_rejected", 0)
    row[field] = value
    with pytest.raises(CorruptedStateError):
        validate_round_history([row])


def test_rejected_evidence_requires_a_finalized_product_snapshot():
    row = _row(0, "evidence_rejected", 0)
    row.pop("dispositions")
    with pytest.raises(CorruptedStateError):
        validate_round_history([row])


@pytest.mark.parametrize("count", [0, 3, True, -1])
def test_rejected_evidence_cannot_change_the_reconstructed_count(count):
    row = _row(2, "evidence_rejected", count)
    state = _state([*_prefix(), row], 2)
    with pytest.raises(CorruptedStateError):
        earned_history_cycles(state, SOURCE, None)


@pytest.mark.parametrize("count", [0, 3, True, -1])
def test_rejected_evidence_cannot_use_a_numeric_state_fallback(count):
    state = _state([*_prefix(), _row(2, "evidence_rejected", 2)], count)
    with pytest.raises(CorruptedStateError):
        earned_history_cycles(state, SOURCE, None)


@pytest.mark.parametrize("status", ["not_run", "returned"])
def test_unavailable_attempt_still_refuses_inherited_credit(status):
    row = _row(2, "unavailable", 2)
    row["phase_status"] = {name: status for name in ROUND_PHASES}
    if status == "not_run":
        row.pop("dispositions")
    with pytest.raises(CorruptedStateError, match="cannot prove"):
        earned_history_cycles(_state([*_prefix(), row], 2), SOURCE, None)


def test_rejected_evidence_preserves_source_and_scope_checks():
    row = _row(2, "evidence_rejected", 2)
    row["reviewed_repositories"] = {"other": "c" * 64}
    with pytest.raises(CorruptedStateError, match="scope mismatch"):
        earned_history_cycles(_state([*_prefix(), row], 2), SOURCE, None)
    row["reviewed_repositories"] = None
    row["source_hash"] = "other-source"
    assert earned_history_cycles(_state([*_prefix(), row], 2), SOURCE, None) == [1, 2]


def test_existing_legacy_and_modern_transition_semantics_stay_compatible():
    rows = _prefix()
    pending = _row(2, "pending", 2)
    pending.pop("fixpoint")
    pending.pop("dispositions")
    pending["phase_status"] = {name: "not_run" for name in ROUND_PHASES}
    rows.append(pending)
    assert earned_history_cycles(_state(rows, 2), SOURCE, None) == [1, 2]
    interrupted = _row(2, "interrupted", 2)
    interrupted["acquisition_failures"] = [{
        "id": "l1-qodo-invoke-fail", "fingerprint": "invoke-fail-qodo",
        "pass_name": "qodo", "outcome": "error",
    }]
    rows[-1] = interrupted
    assert earned_history_cycles(_state(rows, 2), SOURCE, None) == [1, 2]
    reset = _row(3, "reset", 0)
    reset.update(reset_observed=True, fixpoint="RESET")
    rows.append(reset)
    assert earned_history_cycles(_state(rows, 0), SOURCE, None) == []
    legacy = {"round": 0, "fixpoint": "CLEAN", "clean_rounds_after": 1}
    assert earned_history_cycles(_state([legacy], 1), SOURCE, None) == [1]
    assert all("evidence_rejections" not in row for row in rows)
    original = deepcopy(rows)
    validate_round_history(rows, 3)
    assert rows == original
