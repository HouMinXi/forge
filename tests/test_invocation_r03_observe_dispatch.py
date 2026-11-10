"""Explicit trusted-frame OBSERVE handoff, not automatic llm_invoke observation.

Real host/owner/stage/worker with synthetic ACK storage. Fixed acquired bytes
supply frame identity; aggregate LLM Usage is never treated as native evidence.
"""

import io as bytes_io
import threading

import pytest

from code_forge.invocation_audit import UsageObservation
from code_forge.invocation_io import AuditPhase
from code_forge.llm_invoke import AuditPersistenceError, llm_invoke
from tests import test_invocation_r03_host_dispatch as controls
from tests.test_invocation_host import MockRecorder, admit, request
from tests.test_invocation_send_audit import reply, scripted

pytest_plugins = ["tests.test_invocation_host"]
backend = controls.backend


@pytest.mark.parametrize("strict", [False, True], ids=["default", "strict"])
@pytest.mark.parametrize("fail", [False, True], ids=["healthy", "post-ack-credit-failure"])
def test_explicit_observe_ack_failure_keeps_raw_without_usage_credit(
    setup,
    monkeypatch,
    backend,
    strict,
    fail,
):
    host, notices = setup[0](strict=strict, control_timeout_s=2)
    supervisor = setup[1]
    raw = b'{"usage":{"prompt_tokens":1,"completion_tokens":2}}'
    attempt = admit(host.scope(), request())
    capture = attempt.new_response(status=200, redirect_hop=None, deadline=attempt._deadline)
    assert b"".join(capture.chunks(bytes_io.BytesIO(raw))) == raw
    capture.seal()
    owner = host._owner
    record = owner._leases[id(attempt._lease)]
    usage = UsageObservation(
        1,
        2,
        None,
        None,
        "partial",
        "unknown",
        "unknown",
        "final",
        "native",
        "attempt",
        {},
    )
    original_observe = MockRecorder.observe_response
    original_lease = owner._lease
    returned, requests, seam = [], [], []
    armed = threading.Event()
    primary = RuntimeError("synthetic OBSERVE post-ACK credit failure")

    def observe(recorder, **kwargs):
        assert threading.current_thread() is supervisor._worker
        assert supervisor._running._phase is AuditPhase.OBSERVE
        assert supervisor._running._outcome is None
        assert kwargs["attempt_id"] == attempt._lease.attempt_id
        assert kwargs["source_observation_id"] == attempt._stage._adopted[1].observation_id
        assert kwargs["source_observation_id"] in record.observations
        assert kwargs["frame_offset"] == 0 and kwargs["frame_length"] == len(raw)
        assert kwargs["usage"] == usage and kwargs["observed_backend"] is None
        assert list(recorder.stages.values()) == [bytearray(raw)]
        assert len(record.refs) == 1 and not record.usage_known
        ack = original_observe(recorder, **kwargs)
        assert ack.admitted and not ack.publication_uncertain
        returned.append(ack)
        requests.append(kwargs)
        if fail:
            armed.set()
        return ack

    def lease(value):
        result = original_lease(value)
        if armed.is_set():
            armed.clear()
            assert result is record
            assert threading.current_thread() is not supervisor._worker
            ticket = supervisor._handoff
            assert ticket is not None and ticket._phase is AuditPhase.OBSERVE
            assert ticket._outcome.state == "ack" and ticket._outcome.value is returned[0]
            assert attempt._stage._adopted is not None and len(record.refs) == 1
            assert not record.usage_known
            seam.append(value)
            raise primary
        return result

    monkeypatch.setattr(MockRecorder, "observe_response", observe)
    monkeypatch.setattr(owner, "_lease", lease)
    if fail and strict:
        with pytest.raises(AuditPersistenceError):
            attempt.observe_response(frame_offset=0, frame_length=len(raw), usage=usage)
    else:
        assert attempt.observe_response(frame_offset=0, frame_length=len(raw), usage=usage) is not fail
    assert len(returned) == len(requests) == 1
    assert sum(name == "observe" for name, _ in MockRecorder.calls) == 1
    assert sum(name == "adopt" for name, _ in MockRecorder.calls) == 1
    assert record.captures["wire"] == (len(raw), True) and len(record.refs) == 1
    assert host.safe_summary().adopted_responses == 1
    assert host.safe_summary().accepted_bytes == len(raw)
    assert supervisor._handoff is None and not attempt._stage._busy and not attempt._stage._tickets
    transport = scripted(monkeypatch, [reply()])
    if fail:
        assert seam == [attempt._lease] and owner._refused and owner._faults == []
        assert not record.usage_known and not record.settled
        summary = host.safe_summary()
        assert summary.state == "incomplete" and not summary.audit_complete
        assert summary.settled_sends == 0
        before = list(MockRecorder.calls)
        if strict:
            with pytest.raises(AuditPersistenceError):
                llm_invoke(
                    "fixture", backend, max_attempts=2, initial_delay_s=0, host_audit=host.scope()
                )
            assert transport.requests == []
        else:
            result = llm_invoke(
                "fixture", backend, max_attempts=2, initial_delay_s=0, host_audit=host.scope()
            )
            assert result.content["findings"] == [] and len(transport.requests) == 1
        assert MockRecorder.calls == before and len(returned) == 1
        assert not record.usage_known and record.captures["wire"] == (len(raw), True)
        assert notices == ["invocation_audit_unavailable"]
    else:
        assert seam == [] and record.usage_known and not owner._refused
        assert attempt.finish(outcome="completed", error_class=None, duration_s=0.1, usage=None)
        assert record.settled and host.safe_summary().settled_sends == 1
        assert (
            llm_invoke(
                "fixture", backend, max_attempts=2, initial_delay_s=0, host_audit=host.scope()
            ).content["findings"]
            == []
        )
        assert len(transport.requests) == 1 and host.safe_summary().settled_sends == 2
        assert notices == []
