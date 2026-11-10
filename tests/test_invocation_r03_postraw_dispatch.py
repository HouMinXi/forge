"""Post-ADOPT ACK bookkeeping failure through real dispatch, synthetic storage.

This seam naturally has an empty owner fault list: it latches refusal directly.
No unexecuted diagnostic-failure patch is presented as double-fault evidence.
"""

import threading
import time

import pytest

from code_forge.invocation_io import AuditPhase
from code_forge.llm_invoke import AuditPersistenceError, llm_invoke
from tests import test_invocation_r03_host_dispatch as controls
from tests.test_invocation_host import MockRecorder
from tests.test_invocation_send_audit import reply, scripted

pytest_plugins = ["tests.test_invocation_host"]
backend = controls.backend


@pytest.mark.parametrize("strict", [False, True], ids=["default", "strict"])
@pytest.mark.parametrize("fault", [False, True], ids=["healthy", "post-ack-credit-failure"])
def test_postraw_adopt_ack_does_not_grant_credit_after_bookkeeping_failure(
    setup,
    monkeypatch,
    backend,
    strict,
    fault,
):
    host, notices = setup[0](strict=strict, control_timeout_s=2)
    io = setup[1]
    host._ensure_open(time.monotonic() + 5)
    owner = host._owner
    responses = [reply(), reply()]
    expected_raw = bytes(responses[0].raw)  # Frozen before any transport/recorder call.
    transport = scripted(monkeypatch, responses)
    returned_acks = []
    raw_at_ack = []
    seam_records = []
    armed = threading.Event()
    original_adopt = MockRecorder.adopt_stage
    original_lease = owner._lease
    primary = RuntimeError("synthetic post-ACK local credit failure")

    def adopt(recorder, handle, **kwargs):
        ack = original_adopt(recorder, handle, **kwargs)
        assert threading.current_thread() is io._worker
        raw_at_ack.append(bytes(recorder.stages[handle]))
        assert raw_at_ack[-1] == expected_raw
        assert kwargs["complete"] is True
        assert ack.admitted and not ack.publication_uncertain and not ack.ref.partial
        returned_acks.append(ack)  # Actual synthetic sink result, before local credit.
        if fault:
            armed.set()
        return ack

    def lease(value):
        record = original_lease(value)
        if armed.is_set():
            armed.clear()  # One shot; do not interfere with real cleanup.
            assert threading.current_thread() is not io._worker
            ticket = io._handoff
            assert ticket is not None and ticket._phase is AuditPhase.ADOPT
            assert ticket._outcome.state == "ack"
            assert ticket._outcome.value is returned_acks[-1]
            assert not record.refs and not record.captures and not record.observations
            seam_records.append(record)
            raise primary
        return record

    monkeypatch.setattr(MockRecorder, "adopt_stage", adopt)
    monkeypatch.setattr(owner, "_lease", lease)

    def invoke():
        return llm_invoke(
            "fixture", backend, timeout_s=5, max_attempts=2, initial_delay_s=0, host_audit=host.scope()
        )

    if strict and fault:
        with pytest.raises(AuditPersistenceError):
            invoke()
    else:
        assert invoke().content["findings"] == []
    assert len(transport.requests) == 1  # No automatic retry of the failed audit.
    assert len(returned_acks) == len(raw_at_ack) == 1
    assert sum(name == "adopt" for name, _ in MockRecorder.calls) == 1
    assert sum(name == "start" for name, _ in MockRecorder.calls) == 1
    assert io._handoff is None and host._controllers == host._control_bytes == 0
    if fault:
        assert len(seam_records) == 1 and not armed.is_set()
        record = seam_records[0]
        assert owner._refused and owner._faults == []
        assert not any(recorder.finished for recorder in MockRecorder.instances)
        assert not record.refs and not record.captures and not record.observations
        assert not record.usage_known
        assert len(record.stages) == 1
        for stage in record.stages.values():
            assert stage._adopted is None and not stage._busy and not stage._tickets
        summary = host.safe_summary()
        assert summary.state == "incomplete" and not summary.audit_complete
        assert summary.adopted_responses == summary.accepted_bytes == summary.settled_sends == 0
        assert notices == ["invocation_audit_unavailable"]
        before = list(MockRecorder.calls)
        if strict:
            with pytest.raises(AuditPersistenceError):
                invoke()
            assert len(transport.requests) == 1
        else:
            assert invoke().content["findings"] == []
            assert len(transport.requests) == 2
        assert MockRecorder.calls == before
        assert owner._refused and owner._faults == []
        assert host.safe_summary().adopted_responses == 0
    else:
        assert not seam_records and not host.failed and not owner._refused
        assert host.safe_summary().adopted_responses == 1
        assert host.safe_summary().accepted_bytes == len(expected_raw)
        assert host.safe_summary().settled_sends == 1
        assert invoke().content["findings"] == []
        assert len(transport.requests) == len(returned_acks) == 2
        assert host.safe_summary().adopted_responses == host.safe_summary().settled_sends == 2
