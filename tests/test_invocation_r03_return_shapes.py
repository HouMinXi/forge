"""START sink-return controls through real owner/host/worker and mock HTTP.

Synthetic ACK issuers, not native persistence. Sink returns are recorded before
owner interpretation; an escaped sink exception is not called a returned ACK.
"""

import time
import uuid

import pytest

from code_forge.invocation_audit import AuditFault, StartAcknowledgement
from code_forge.llm_invoke import AuditPersistenceError
from tests.test_invocation_host import MockRecorder
from tests import test_invocation_r03_host_dispatch as controls
from tests.test_invocation_send_audit import reply, scripted

pytest_plugins = ["tests.test_invocation_host"]
backend = controls.backend
invoke = controls.invoke


@pytest.mark.parametrize("shape", ["none", "malformed", "denied", "uncertain", "healthy", "no-return"])
def test_strict_start_return_shape_precedes_transport(setup, monkeypatch, backend, shape):
    host, _ = setup[0](strict=True, control_timeout_s=2)
    host._ensure_open(time.monotonic() + 5)
    fault = AuditFault("persistence_error", "start", None)
    ack = {
        "none": None,
        "malformed": object(),
        "denied": StartAcknowledgement(False, None, fault),
        "uncertain": StartAcknowledgement(False, None, fault, publication_uncertain=True),
        "healthy": StartAcknowledgement(True, str(uuid.uuid4()), None),
        "no-return": None,
    }[shape]
    sink_error = OSError("synthetic sink escape")
    returned = []
    escaped = []
    interpreted = []

    def start(recorder, **kwargs):
        recorder.touch("start")
        recorder.started.append(kwargs)
        if shape == "no-return":
            escaped.append(sink_error)
            raise sink_error
        returned.append(ack)
        return ack

    original = host._owner._call

    def owner_call(function, phase, **kwargs):
        result = original(function, phase, **kwargs)
        if getattr(function, "__name__", None) == "start":
            interpreted.append(result)
        return result

    monkeypatch.setattr(MockRecorder, "start", start)
    monkeypatch.setattr(host._owner, "_call", owner_call)
    transport = scripted(monkeypatch, [reply()])
    if shape == "healthy":
        assert invoke(host, backend).content["findings"] == []
        assert len(transport.requests) == host._started == 1
    else:
        with pytest.raises(AuditPersistenceError):
            invoke(host, backend)
        assert transport.requests == []
        assert host._started == 0 and not host._attempts
        assert not any(recorder.finished for recorder in MockRecorder.instances)
        assert host.failed and host.strict_failed
        summary = host.safe_summary()
        assert summary.state == "incomplete" and summary.audit_complete is False
        before = list(MockRecorder.calls)
        with pytest.raises(AuditPersistenceError):
            invoke(host, backend)
        assert MockRecorder.calls == before and transport.requests == []
    assert sum(name == "start" for name, _ in MockRecorder.calls) == 1
    assert host._controllers == host._control_bytes == 0
    if shape == "no-return":
        assert returned == [] and escaped == [sink_error]
        assert len(interpreted) == 1
        assert interpreted[0].admitted is False and interpreted[0].publication_uncertain is True
        assert interpreted[0] is not ack
    else:
        assert len(returned) == 1 and returned[0] is ack and escaped == []
        if shape in ("none", "malformed"):
            assert interpreted == []  # Owner attribute access failed before returning.
        else:
            assert len(interpreted) == 1 and interpreted[0] is ack
