"""Recorder FINISH ACK/owner commit precedes a mutex-exit fault; mock storage/HTTP only.

Owner-settled evidence is distinct from host settlement acknowledgement. These
controls do not qualify native persistence, replay, or full recovery.
"""

import sys
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
@pytest.mark.parametrize("mode", ["healthy", "exit-fault", "exit-and-diagnostic-fault"])
def test_finish_ack_settled_owner_survives_failed_handoff(setup, monkeypatch, backend, strict, mode):
    host, notices = setup[0](strict=strict, control_timeout_s=2)
    io = setup[1]
    host._ensure_open(time.monotonic() + 5)
    owner = host._owner
    response = reply()
    expected_raw = bytes(response.raw)
    transport = scripted(monkeypatch, [response, reply()])
    primary = RuntimeError("synthetic FINISH mutex exit failure")
    diagnostic = OSError("synthetic diagnostic failure")
    armed = threading.Event()
    acks, exits, faults, interpreted = [], [], [], []
    real_mutex = owner._mutex
    original_finish = MockRecorder.finish
    original_fault = owner._fault
    original_call = owner._call

    def finish(recorder, **kwargs):
        assert threading.current_thread() is io._worker
        assert io._running._phase is AuditPhase.FINISH
        assert io._running._outcome is None  # Recorder ACK is not worker ticket settlement.
        ack = original_finish(recorder, **kwargs)
        assert ack.admitted and not ack.publication_uncertain
        acks.append((ack, dict(kwargs)))
        if mode != "healthy":
            armed.set()
        return ack

    def owner_call(function, phase, **kwargs):
        result = original_call(function, phase, **kwargs)
        if phase == "finish":
            assert result is acks[-1][0]
            interpreted.append(result)
        return result

    class Mutex:
        def acquire(self, *args, **kwargs):
            return real_mutex.acquire(*args, **kwargs)

        def release(self):
            return real_mutex.release()

        def __enter__(self):
            return real_mutex.__enter__()

        def __exit__(self, *args):
            result = real_mutex.__exit__(*args)
            if armed.is_set():
                armed.clear()
                assert threading.current_thread() is io._worker
                assert io._running._phase is AuditPhase.FINISH
                assert io._running._outcome is None
                assert args[0] is None
                ack, terminal = acks[-1]
                assert ack.admitted
                record = next(iter(owner._leases.values()))
                assert record.settled and record.settlement_started
                assert record.terminal == terminal and terminal["outcome"] == "completed"
                exits.append(record)
                raise primary
            return result

    def fault(code, phase, attempt_id=None):
        assert sys.exception() is primary
        assert code == "persistence_error" and phase == "finish"
        assert attempt_id == exits[0].value.attempt_id
        faults.append((code, phase, attempt_id))
        if mode == "exit-and-diagnostic-fault":
            raise diagnostic
        return original_fault(code, phase, attempt_id)

    monkeypatch.setattr(owner, "_call", owner_call)
    monkeypatch.setattr(owner, "_mutex", Mutex())
    monkeypatch.setattr(MockRecorder, "finish", finish)
    monkeypatch.setattr(owner, "_fault", fault)

    def invoke():
        return llm_invoke(
            "fixture", backend, timeout_s=5, max_attempts=2, initial_delay_s=0, host_audit=host.scope()
        )

    if strict and mode != "healthy":
        with pytest.raises(AuditPersistenceError):
            invoke()
    else:
        assert invoke().content["findings"] == []
    assert len(transport.requests) == len(acks) == len(interpreted) == 1
    assert sum(name == "start" for name, _ in MockRecorder.calls) == 1
    recorder = MockRecorder.instances[0]
    assert len(recorder.finished) == 1
    assert list(recorder.stages.values()) == [bytearray(expected_raw)]
    record = next(iter(owner._leases.values()))
    assert record.settled and record.terminal == recorder.finished[0] == acks[0][1]
    assert len(record.refs) == 1 and record.captures["wire"] == (len(expected_raw), True)
    assert host.safe_summary().adopted_responses == 1
    assert host.safe_summary().accepted_bytes == len(expected_raw)
    assert host._controllers == host._control_bytes == 0 and io._running is None
    if mode == "healthy":
        assert exits == faults == [] and not owner._refused and not host.failed
        assert host.safe_summary().settled_sends == 1
        assert invoke().content["findings"] == []
        assert len(transport.requests) == len(acks) == len(interpreted) == 2
        assert host.safe_summary().settled_sends == 2
    else:
        assert exits == [record] and len(faults) == 1 and owner._refused
        assert len(owner._faults) == (0 if mode == "exit-and-diagnostic-fault" else 1)
        summary = host.safe_summary()
        assert summary.state == "incomplete" and not summary.audit_complete
        assert summary.settled_sends == 0  # Host did not receive FINISH success.
        assert notices == ["invocation_audit_unavailable"]
        before = list(MockRecorder.calls)
        if strict:
            with pytest.raises(AuditPersistenceError):
                invoke()
            assert len(transport.requests) == 1
        else:
            assert invoke().content["findings"] == []
            assert len(transport.requests) == 2
        assert MockRecorder.calls == before and len(acks) == 1 and len(faults) == 1
        assert owner._refused and record.settled
        assert record.terminal == acks[0][1] and len(record.refs) == 1
