# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""R03-10/11 component evidence: real owner and synthetic recorder.

Admission assertions cover owner APIs and recorder calls, not host transport.
Neither schema variant qualifies native persistence or recovery.
"""

from dataclasses import FrozenInstanceError, replace

import pytest

from code_forge.invocation_audit import AuditAcknowledgement, AuditFault
from code_forge.invocation_owner import AttemptLease, InvocationOwner, InvocationSessionSummary
from code_forge.llm_invoke import LLMInvokeError
from tests.test_invocation_owner import begin, finish, manifest, operation
from tests import test_invocation_owner_refusal as refusal_controls


@pytest.fixture(params=[1, 2], ids=["schema1", "schema2"])
def setup_owner(monkeypatch, request):
    # Reuse the recorder double, but run the genuine constructor with the selected
    # version instead of changing a live owner's schema or refusal state.
    real_create = InvocationOwner.create

    def create_with_schema(**kwargs):
        return real_create(**kwargs, audit_schema_version=request.param)

    with monkeypatch.context() as patch:
        patch.setattr(InvocationOwner, "create", create_with_schema)
        result = refusal_controls.setup_owner.__wrapped__(monkeypatch)
    assert result[0]._audit_schema_version == request.param
    return result


def assert_incomplete(summary):
    assert isinstance(summary, InvocationSessionSummary)
    assert summary.state == "incomplete"
    assert summary.audit_complete is False
    assert "persistence_error" in summary.fault_codes


def assert_owner_admission_refused(owner, op, recorder):
    before = tuple(recorder.calls)
    for action in (lambda: begin(owner, op), lambda: operation(owner)):
        with pytest.raises(LLMInvokeError) as caught:
            action()
        assert caught.value.kind == "audit_persistence_error"
        assert caught.value.retryable is False
    assert tuple(recorder.calls) == before


@pytest.mark.parametrize("uncertain", [True, False], ids=["uncertain_then_success", "healthy"])
def test_inflight_success_cannot_erase_prior_uncertainty(setup_owner, monkeypatch, uncertain):
    owner, _, context, _ = setup_owner
    op = operation(owner)
    spare = operation(owner)
    lease = begin(owner, op)
    lease.entered("entered_api_send")
    recorder = owner._current.recorder
    if uncertain:
        ack = AuditAcknowledgement(
            False, AuditFault("persistence_error", "start", None), publication_uncertain=True
        )
        with monkeypatch.context() as patch:
            patch.setattr(recorder, "publish_input_manifest", lambda **kwargs: ack)
            assert owner.publish_input(manifest(context)) is ack
        assert ack.publication_uncertain
        assert_owner_admission_refused(owner, spare, recorder)

    # Already-entered work may record evidence and settle failed: this is not new admission.
    raw = lease.acquired(layer="wire", data=b"{}", complete=True)
    assert raw.admitted and raw.ref is not None
    assert finish(lease, outcome="failed").admitted
    assert [name for name, _ in recorder.calls[-2:]] == ["settle_acquired", "finish"]
    if uncertain:
        assert_owner_admission_refused(owner, spare, recorder)
        before = tuple(recorder.calls)
        # Identical terminal replay, not native recovery evidence.
        assert finish(lease, outcome="failed").admitted
        assert tuple(recorder.calls[:-1]) == before
        assert recorder.calls[-1] == before[-1]  # same terminal replay, no start
        assert_owner_admission_refused(owner, spare, recorder)
    else:
        # Removing only the first uncertain operation keeps ordinary admission available.
        next_lease = begin(owner, spare)
        assert next_lease._owner is owner
        assert finish(next_lease, outcome="cancelled").admitted

    summary = owner.finalize("failed")
    if uncertain:
        assert_incomplete(summary)
        assert_owner_admission_refused(owner, spare, recorder)
        assert_incomplete(owner.finalize("failed"))
        # Negative admission oracle: simulate an erroneously admitted API return,
        # without clearing the real owner latch or touching its recorder.
        with monkeypatch.context() as patch:
            patch.setitem(globals(), "begin", lambda *_args: None)
            with pytest.raises(pytest.fail.Exception):
                assert_owner_admission_refused(owner, spare, recorder)
        assert_owner_admission_refused(owner, spare, recorder)
        # Negative aggregate controls: a false clean result cannot satisfy the oracle.
        for broken in (
            replace(summary, state="completed", audit_complete=True, fault_codes=()),
            replace(summary, audit_complete=True),
        ):
            with pytest.raises(AssertionError):
                assert_incomplete(broken)
    else:
        assert summary.state == "failed"
        assert summary.audit_complete is True
        assert summary.fault_codes == ()
        assert not owner._refused


def assert_owner_return(value):
    assert isinstance(value, InvocationOwner)


def assert_lease_return(value, owner, op):
    assert isinstance(value, AttemptLease)
    assert value._owner is owner
    assert value.operation is op
    assert owner._leases[id(value)].value is value
    assert isinstance(value.attempt_id, str) and value.attempt_id


def assert_summary_return(value, owner):
    assert isinstance(value, InvocationSessionSummary)
    assert value.schema_version == 1
    assert value.session_id == owner._session_id
    assert value.state == "completed"
    assert value.audit_complete is True
    assert value.fault_codes == ()


def test_real_success_returns_have_identity_and_immutable_summary(setup_owner):
    owner, _, context, _ = setup_owner
    assert_owner_return(owner)  # fixture calls real InvocationOwner.create
    assert owner._current.context is context
    op = operation(owner)
    lease = begin(owner, op)
    assert_lease_return(lease, owner, op)
    lease.entered("entered_api_send")
    assert lease.acquired(layer="wire", data=b"{}", complete=True).admitted
    assert finish(lease).admitted
    result = owner.finalize("completed")
    assert_summary_return(result, owner)
    assert not owner._refused
    assert len(result.run_summaries) == 1
    with pytest.raises(FrozenInstanceError):
        result.session_id = "changed"
    with pytest.raises(TypeError):
        result.run_summaries[0]["state"] = "changed"
    with pytest.raises(TypeError):
        result.usage["input_tokens"]["known_subtotal"] = 123
    with pytest.raises(TypeError):
        result.timing["known_work_s"] = 123
    assert owner.finalize("completed") == result
    # Explicit None-return mutations must fail, not be mistaken for successful completion.
    for check in (
        lambda: assert_owner_return(None),
        lambda: assert_lease_return(None, owner, op),
        lambda: assert_summary_return(None, owner),
    ):
        with pytest.raises(AssertionError):
            check()
