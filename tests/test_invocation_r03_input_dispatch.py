"""Successful synthetic INPUT ACK followed by snapshot handoff failure.

Real host/owner/worker and dispatch, mock HTTP/storage. Sink publication is not
owner-cache publication or worker settlement, and none is native durability.
"""

import threading

import pytest

from code_forge.invocation_io import AuditPhase
from code_forge.invocation_owner import InvocationOwner
from code_forge.llm_invoke import AuditPersistenceError, llm_invoke
from tests import test_invocation_r03_host_dispatch as controls
from tests.test_invocation_host import MockRecorder
from tests.test_invocation_send_audit import reply, scripted

pytest_plugins = ["tests.test_invocation_host"]
backend = controls.backend


@pytest.mark.parametrize("strict", [False, True], ids=["default", "strict"])
@pytest.mark.parametrize("fail", [False, True], ids=["healthy", "snapshot-fault"])
def test_input_ack_snapshot_handoff_precedes_real_dispatch(setup, monkeypatch, backend, strict, fail):
    host, notices = setup[0](strict=strict, control_timeout_s=2)
    io = setup[1]
    transport = scripted(monkeypatch, [reply(), reply()])
    primary = RuntimeError("synthetic INPUT snapshot handoff failure")
    cause = ValueError("synthetic cause")
    primary.__cause__ = cause
    sink_acks, owner_returns, owner_errors, events = [], [], [], []
    sink_publish = MockRecorder.publish_input_manifest
    snapshot = MockRecorder.scoped_snapshot
    publish = InvocationOwner.publish_input

    def assert_input_worker():
        assert threading.current_thread() is io._worker
        assert io._running._phase is AuditPhase.INPUT
        assert io._running._outcome is None

    def sink(recorder, *, manifest):
        assert_input_worker()
        ack = sink_publish(recorder, manifest=manifest)
        assert ack.admitted and not ack.publication_uncertain
        assert recorder.manifest == manifest  # Synthetic sink already published.
        sink_acks.append(ack)
        events.append("sink-ack")
        return ack

    def read_snapshot(recorder):
        assert_input_worker()
        assert len(sink_acks) == 1 and not owner_returns
        assert recorder.manifest is not None
        assert host._owner._current.manifest is None
        events.append("snapshot")
        if fail:
            recorder.touch("snapshot")
            raise primary
        return snapshot(recorder)

    def owner_publish(owner, manifest):
        try:
            result = publish(owner, manifest)
        except BaseException as error:
            owner_errors.append(error)
            events.append("owner-error")
            raise
        else:
            assert result is sink_acks[0]
            owner_returns.append(result)
            events.append("owner-return")
            return result

    monkeypatch.setattr(MockRecorder, "publish_input_manifest", sink)
    monkeypatch.setattr(MockRecorder, "scoped_snapshot", read_snapshot)
    monkeypatch.setattr(InvocationOwner, "publish_input", owner_publish)

    def invoke():
        return llm_invoke(
            "fixture", backend, timeout_s=5, max_attempts=2, initial_delay_s=0, host_audit=host.scope()
        )

    if fail and strict:
        with pytest.raises(AuditPersistenceError):
            invoke()
    else:
        assert invoke().content["findings"] == []
    assert events == ["sink-ack", "snapshot", "owner-error" if fail else "owner-return"]
    assert len(sink_acks) == 1
    assert io._running is None and host._controllers == host._control_bytes == 0
    recorder = MockRecorder.instances[0]
    assert recorder.manifest is not None
    owner = host._owner
    if fail:
        assert owner_errors == [primary] and owner_errors[0] is primary
        assert primary.__cause__ is cause
        assert owner_returns == [] and owner._refused
        assert [(f.code, f.phase) for f in owner._faults] == [("persistence_error", "start")]
        assert owner._current.manifest is None and not host._opened
        assert host.failed and host._started == 0
        summary = host.safe_summary()
        assert summary.state == "incomplete" and not summary.audit_complete
        assert summary.adopted_responses == summary.settled_sends == summary.accepted_bytes == 0
        assert len(transport.requests) == (0 if strict else 1)
        before = list(MockRecorder.calls)
        if strict:
            with pytest.raises(AuditPersistenceError):
                invoke()
        else:
            assert invoke().content["findings"] == []
        assert len(transport.requests) == (0 if strict else 2)
        assert MockRecorder.calls == before
        assert notices == ["invocation_audit_unavailable"]
        assert recorder.started == recorder.finished == [] and recorder.stages == {}
    else:
        assert owner_errors == [] and owner_returns[0] is sink_acks[0]
        assert owner._current.manifest == recorder.manifest
        assert host._opened and not host.failed and not owner._refused
        assert invoke().content["findings"] == []
        assert len(transport.requests) == host._started == host._settled == 2
        assert notices == []
    assert sum(name == "open" for name, _ in MockRecorder.calls) == 1
    assert sum(name == "input" for name, _ in MockRecorder.calls) == 1
    assert sum(name == "snapshot" for name, _ in MockRecorder.calls) == 1
    assert len(sink_acks) == 1 and len(events) == 3
