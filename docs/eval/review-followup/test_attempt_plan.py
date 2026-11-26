"""Offline preflight: reserve a real wire slot before contacting any model."""

from dataclasses import replace

import pytest

from code_forge.eval.review_followup.attempt_plan import AttemptSlot, PairedAttemptGate
from code_forge.eval.review_followup.fixtures import InputError, digest

ORDER = ("targeted", "baseline")
PASSES = ("qodo", "expert", "adversarial")


def pinned(groups):
    return [
        (AttemptSlot(arm, group, name, digest(body)), body)
        for arm in ORDER
        for group in groups
        for name in PASSES
        for body in [f"{arm}:{group}:{name}".encode()]
    ]


def gate(groups, rows, **changes):
    options = dict(arm_order=ORDER, max_body_bytes=64, attempt_ceiling=len(rows))
    return PairedAttemptGate(groups, (s for s, _ in rows), **(options | changes))


def reserve(admission, row):
    slot, body = row
    return admission.reserve(slot.arm, slot.group, slot.pass_name, body)


def test_one_pair_hard_ceiling_counts_failure_and_preserves_unstarted_slots():
    rows = pinned(["storage"])
    admission = gate(["storage"], rows)
    ticket = reserve(admission, rows[0])
    assert ticket == 1
    assert [r.state for r in admission.rows] == ["unknown_outcome", *["not_started"] * 5]
    with pytest.raises(InputError, match="attempt-in-flight"):
        reserve(admission, rows[1])
    admission.complete(ticket, success=False)
    with pytest.raises(InputError, match="attempt-slot-drift"):
        reserve(admission, rows[0])
    for expected, row in enumerate(rows[1:], 2):
        assert reserve(admission, row) == expected
        admission.complete(expected, success=True)
    with pytest.raises(InputError, match="attempt-ceiling-reached"):
        reserve(admission, rows[-1])
    assert [r.state for r in admission.rows] == ["failed", *["completed"] * 5]


def test_hidden_continuation_or_changed_wire_body_is_refused_before_charge():
    rows = pinned(["storage"])
    admission = gate(["storage"], rows)
    slot, body = rows[0]
    with pytest.raises(InputError, match="attempt-body-drift"):
        reserve(admission, (slot, body + b" altered"))
    with pytest.raises(InputError, match="attempt-body-over-budget"):
        reserve(admission, (slot, body * 100))
    assert all(r.state == "not_started" for r in admission.rows)
    assert reserve(admission, rows[0]) == 1
    with pytest.raises(InputError, match="attempt-ticket-mismatch"):
        admission.complete(True, success=True)
    assert admission.rows[0].state == "unknown_outcome"


def test_plan_requires_every_slot_in_frozen_order_and_one_attempt_per_slot():
    groups = ["storage", "tests"]
    rows = pinned(groups)
    assert len(gate(groups, rows).rows) == 12
    with pytest.raises(InputError, match="attempt_ceiling"):
        gate(groups, rows, attempt_ceiling=13)
    with pytest.raises(InputError, match="attempt_plan.slots"):
        gate(groups, rows[:-1], attempt_ceiling=12)
    with pytest.raises(InputError, match="attempt_plan.slots"):
        gate(groups, rows[::-1])
    with pytest.raises(InputError, match="attempt_plan.slots"):
        gate(groups, [(replace(rows[0][0], request_sha256="not-a-digest"), b""), *rows[1:]])
