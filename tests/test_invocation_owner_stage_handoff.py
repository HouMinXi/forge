"""Real worker/owner scheduling with synthetic recorder ACKs, not durability proof."""

from asyncio import CancelledError
from dataclasses import replace
import threading
import time
from types import SimpleNamespace

import pytest

from code_forge import invocation_audit as audit
from code_forge.invocation_io import AuditPhase
from code_forge.invocation_owner import _Lease
from code_forge.invocation_owner_stage import OwnerStageUnavailable
from tests.test_invocation_owner_stage import completed, stage_setup as _stage_setup

stage_setup = _stage_setup


@pytest.mark.parametrize("phase", [AuditPhase.ADOPT, AuditPhase.OBSERVE])
def test_ack_credit_precedes_later_finish_owner_lock(stage_setup, monkeypatch, phase):
    owner, lease, record, recorder, args = stage_setup
    io = args["supervisor"]
    stage = lease.begin_capture_stage(**args)
    completed(stage)
    if phase is AuditPhase.OBSERVE:
        stage.adopt()
        recorder.observe_response = lambda **kwargs: audit.AuditAcknowledgement(True, None)
    finish_entered, finish_release = threading.Event(), threading.Event()
    sibling = replace(lease, attempt_id="sibling")

    def finish(**kwargs):
        finish_entered.set()
        assert finish_release.wait(3)
        return audit.AuditAcknowledgement(True, None)

    owner._leases[id(sibling)] = _Lease(
        sibling, SimpleNamespace(finish=finish), dispatch="entered_api_send"
    )

    class Finish:
        def audit_io(self, selected, payload):
            assert selected is AuditPhase.FINISH and not payload
            return owner._finish(sibling, "completed", None, 0.0, None)

    original = io.wait
    queued = []
    follower = Finish()
    checked = threading.Event()
    decisions = []
    next_locked = io._next_locked

    def observed_next():
        pending = any(t._target is follower for lane in io._lanes.values() for t in lane)
        result = next_locked()
        if pending:
            decisions.append(result[0])
            checked.set()
        return result

    monkeypatch.setattr(io, "_next_locked", observed_next)

    def after_ack(ticket):
        outcome = original(ticket)
        if ticket._phase is phase:
            assert outcome.state == "ack"
            queued.append(io.submit(follower, AuditPhase.FINISH, deadline=time.monotonic() + 3))
            assert checked.wait(1), "worker did not examine queued FINISH"
            # If the scheduler dispatched FINISH (the baseline), prove it holds
            # the real owner mutex before allowing the controller to commit.
            if decisions[0] is not None:
                assert finish_entered.wait(1)
        return outcome

    monkeypatch.setattr(io, "wait", after_ack)
    try:
        ack = (
            stage.adopt()
            if phase is AuditPhase.ADOPT
            else stage.observe(
                frame_offset=0, frame_length=1, usage=SimpleNamespace(availability="known")
            )
        )
        assert ack.admitted and not owner._refused
        assert record.refs and record.observations
        if phase is AuditPhase.OBSERVE:
            assert record.usage_known
    finally:
        finish_release.set()
    assert queued and original(queued[0]).state == "ack"
    assert finish_entered.is_set()


def _prepared(stage_setup, phase):
    owner, lease, record, recorder, args = stage_setup
    stage = lease.begin_capture_stage(**args)
    completed(stage)
    if phase is AuditPhase.OBSERVE:
        stage.adopt()
        recorder.observe_response = lambda **kwargs: audit.AuditAcknowledgement(True, None)
    return owner, record, recorder, args["supervisor"], stage


def _invoke(stage, phase, *, usage=None):
    return (
        stage.adopt(usage=usage)
        if phase is AuditPhase.ADOPT
        else stage.observe(frame_offset=0, frame_length=1, usage=usage)
    )


def _released(io, stage):
    with io._condition:
        assert io._handoff is None
    assert not stage._busy and not stage._tickets


@pytest.mark.parametrize("phase", [AuditPhase.ADOPT, AuditPhase.OBSERVE])
def test_cancel_after_ack_before_ticket_registration(stage_setup, monkeypatch, phase):
    owner, record, recorder, io, stage = _prepared(stage_setup, phase)
    refs = list(record.refs)
    original = io.submit
    seen = []

    def submit(target, selected, **kwargs):
        ticket = original(target, selected, **kwargs)
        if selected is phase:
            assert io.wait(ticket).state == "ack"
            assert not stage._tickets
            assert stage._busy
            stage.cancel()
            seen.append(ticket)
        return ticket

    monkeypatch.setattr(io, "submit", submit)
    with pytest.raises(OwnerStageUnavailable):
        _invoke(stage, phase, usage=SimpleNamespace(availability="known"))
    assert seen and stage._cancelled and owner._refused
    assert record.refs == refs and not record.usage_known
    _released(io, stage)


@pytest.mark.parametrize("phase", [AuditPhase.ADOPT, AuditPhase.OBSERVE])
def test_cancel_after_ack_before_credit(stage_setup, monkeypatch, phase):
    owner, record, recorder, io, stage = _prepared(stage_setup, phase)
    refs = list(record.refs)
    original = stage._request

    def request(selected, **kwargs):
        result = original(selected, **kwargs)
        if selected is phase:
            stage.cancel()
        return result

    monkeypatch.setattr(stage, "_request", request)
    with pytest.raises(OwnerStageUnavailable):
        _invoke(stage, phase, usage=SimpleNamespace(availability="known"))
    assert owner._refused and record.refs == refs and not record.usage_known
    _released(io, stage)


@pytest.mark.parametrize("phase", [AuditPhase.ADOPT, AuditPhase.OBSERVE])
@pytest.mark.parametrize("mutation", ["wrong_type", "uncertain", "not_admitted"])
def test_invalid_ack_does_not_strand_gate_or_credit(stage_setup, monkeypatch, phase, mutation):
    owner, record, recorder, io, stage = _prepared(stage_setup, phase)
    name = "adopt_stage" if phase is AuditPhase.ADOPT else "observe_response"
    original = getattr(recorder, name)
    refs = list(record.refs)

    def publish(*args, **kwargs):
        ack = original(*args, **kwargs)
        if mutation == "wrong_type":
            return object()
        if mutation == "uncertain":
            return replace(ack, publication_uncertain=True)
        return replace(ack, admitted=False)

    monkeypatch.setattr(recorder, name, publish)
    with pytest.raises(OwnerStageUnavailable):
        _invoke(stage, phase, usage=SimpleNamespace(availability="known"))
    assert owner._refused and record.refs == refs and not record.usage_known
    _released(io, stage)


@pytest.mark.parametrize("phase", [AuditPhase.ADOPT, AuditPhase.OBSERVE])
def test_original_ticket_deadline_rejects_local_credit(stage_setup, monkeypatch, phase):
    from code_forge import invocation_owner_stage as module

    owner, record, recorder, io, stage = _prepared(stage_setup, phase)
    refs = list(record.refs)
    original = stage._request
    seen = []

    def request(selected, **kwargs):
        result = original(selected, **kwargs)
        if selected is phase:
            seen.append(result.ticket)
            assert result.outcome.state == "ack"
            # Replace only the stage clock, not global time or the worker clock.
            monkeypatch.setattr(
                module, "time", SimpleNamespace(monotonic=lambda: result.ticket._deadline)
            )
        return result

    monkeypatch.setattr(stage, "_request", request)
    with pytest.raises(OwnerStageUnavailable):
        _invoke(stage, phase, usage=SimpleNamespace(availability="known"))
    assert seen and owner._refused and record.refs == refs and not record.usage_known
    assert seen[0]._outcome.state == "ack"
    _released(io, stage)


@pytest.mark.parametrize("phase", [AuditPhase.ADOPT, AuditPhase.OBSERVE])
def test_stage_busy_until_ack_credit_commit(stage_setup, monkeypatch, phase):
    owner, record, recorder, io, stage = _prepared(stage_setup, phase)
    original = stage._request
    checked = []

    def request(selected, **kwargs):
        result = original(selected, **kwargs)
        assert stage._busy
        assert len(stage._tickets) == 1
        with io._condition:
            assert io._handoff is stage._tickets[0]
        with pytest.raises(OwnerStageUnavailable, match="stage unavailable"):
            original(selected)
        checked.append(True)
        return result

    monkeypatch.setattr(stage, "_request", request)
    assert _invoke(stage, phase).admitted
    assert checked
    _released(io, stage)


def test_duplicate_adoption_reuses_credit_without_new_gate(stage_setup, monkeypatch):
    owner, record, recorder, io, stage = _prepared(stage_setup, AuditPhase.ADOPT)
    usage = SimpleNamespace(availability="known")
    ack = stage.adopt(usage=usage)
    before = list(record.refs)

    def unexpected(*args, **kwargs):
        raise AssertionError("duplicate adoption submitted work")

    monkeypatch.setattr(io, "submit", unexpected)
    assert stage.adopt(usage=usage) is ack
    assert record.refs == before and record.usage_known and not owner._refused
    stage.cancel()  # Settled adoption cancellation remains a no-op.
    assert not stage._cancelled
    _released(io, stage)


@pytest.mark.parametrize("phase", [AuditPhase.ADOPT, AuditPhase.OBSERVE])
@pytest.mark.parametrize(
    "kind", [RuntimeError, MemoryError, KeyboardInterrupt, SystemExit, CancelledError]
)
def test_bookkeeping_failure_releases_gate_preserving_primary(stage_setup, monkeypatch, phase, kind):
    owner, record, recorder, io, stage = _prepared(stage_setup, phase)
    refs = list(record.refs)
    error = kind("controlled bookkeeping failure")

    def failed(value):
        raise error

    monkeypatch.setattr(owner, "_lease", failed)
    with pytest.raises(kind) as caught:
        _invoke(stage, phase, usage=SimpleNamespace(availability="known"))
    assert caught.value is error
    assert owner._refused and record.refs == refs and not record.usage_known
    _released(io, stage)


@pytest.mark.parametrize("phase", [AuditPhase.ADOPT, AuditPhase.OBSERVE])
@pytest.mark.parametrize("boundary", ["wait", "credit"])
@pytest.mark.parametrize(
    "kind", [RuntimeError, MemoryError, KeyboardInterrupt, SystemExit, CancelledError]
)
def test_primary_survives_cleanup_failure_with_evidence(stage_setup, monkeypatch, phase, boundary, kind):
    owner, record, recorder, io, stage = _prepared(stage_setup, phase)
    primary = kind("controlled primary")
    cause, context = ValueError("cause"), LookupError("context")
    primary.__cause__, primary.__context__ = cause, context
    primary.__suppress_context__ = True
    secondary = OSError("controlled cleanup")
    refs = list(record.refs)
    cleanup_name = "abandon" if boundary == "wait" else "release_handoff"
    original_cleanup = getattr(io, cleanup_name)

    def cleanup(ticket):
        original_cleanup(ticket)
        raise secondary

    monkeypatch.setattr(io, cleanup_name, cleanup)
    if boundary == "wait":
        original_wait = io.wait

        def wait(ticket):
            result = original_wait(ticket)
            assert result.state == "ack"
            raise primary

        monkeypatch.setattr(io, "wait", wait)
    else:

        def failed(value):
            raise primary

        monkeypatch.setattr(owner, "_lease", failed)
    with pytest.raises(kind) as caught:
        _invoke(stage, phase, usage=SimpleNamespace(availability="known"))
    assert caught.value is primary
    assert primary.__cause__ is cause and primary.__context__ is context
    assert primary.__suppress_context__
    assert primary._invocation_audit_cleanup_errors == (secondary,)
    assert owner._refused and record.refs == refs and not record.usage_known
    _released(io, stage)


@pytest.mark.parametrize("phase", [AuditPhase.ADOPT, AuditPhase.OBSERVE])
def test_release_failure_without_primary_is_not_swallowed(stage_setup, monkeypatch, phase):
    owner, record, recorder, io, stage = _prepared(stage_setup, phase)
    original = io.release_handoff
    error = OSError("controlled cleanup")

    def release(ticket):
        original(ticket)
        raise error

    monkeypatch.setattr(io, "release_handoff", release)
    with pytest.raises(OSError) as caught:
        _invoke(stage, phase, usage=SimpleNamespace(availability="known"))
    assert caught.value is error and owner._refused
    # Already committed durable credit is retained, not rolled back by cleanup.
    assert record.refs and record.observations and record.usage_known
    _released(io, stage)
