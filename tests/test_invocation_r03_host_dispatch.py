"""R03 host/worker evidence with synthetic ACK storage and mock HTTP only.

Not native recovery qualification. Strict trusted-host paths and a narrow default
fail-open control are tested here; general ordinary review, L2 mutation scheduling
and fallback routing are not claimed covered by these consumer tests.
"""

import time

import pytest

from code_forge.backend import BackendConfig
from code_forge.disposition import Disposition
from code_forge.falsify_real import RealFalsifier
from code_forge.llm_invoke import AuditPersistenceError, llm_invoke
from code_forge.state import StateFinding
from tests.test_invocation_host import MockRecorder
from tests.test_invocation_owner_handoff import FailDict, FailList
from tests.test_invocation_send_audit import reply, scripted

pytest_plugins = ["tests.test_invocation_host"]


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setenv("R03_SYNTHETIC_KEY", "synthetic-not-a-credential")
    return BackendConfig(
        name="r03-fixture",
        type="api",
        format="openai",
        model="fixture",
        base_url="https://provider.invalid/v1",
        api_key_env="R03_SYNTHETIC_KEY",
    )


def inject(host, monkeypatch, site, diagnostic_failure):
    # Real OPEN/input control and worker before touching owner container seams.
    host._ensure_open(time.monotonic() + 5)
    owner = host._owner
    error = RuntimeError("synthetic post-ACK bookkeeping failure")
    if site == "lease_dict":
        owner._leases = FailDict(error)
    else:
        original = owner.operation

        def operation(*args, **kwargs):
            value = original(*args, **kwargs)
            owner._operations[id(value)].leases = FailList(error, site == "lease_list_after")
            return value

        monkeypatch.setattr(owner, "operation", operation)
    if diagnostic_failure:

        def broken_fault(*args, **kwargs):
            raise OSError("synthetic diagnostic failure")

        monkeypatch.setattr(owner, "_fault", broken_fault)
    return owner


def invoke(host, backend):
    return llm_invoke(
        "synthetic review",
        backend,
        timeout_s=5,
        max_attempts=1,
        initial_delay_s=0,
        host_audit=host.scope(),
    )


def assert_failed_start(host, owner, supervisor, transport):
    assert sum(name == "start" for name, _ in MockRecorder.calls) == 1
    assert transport.requests == []
    assert host._started == 0 and not host._attempts
    assert owner._refused
    assert not owner._leases
    assert all(not record.finished for record in MockRecorder.instances)
    assert host._controllers == 0 and host._control_bytes == 0
    assert supervisor._operations == 0 and supervisor._running is None
    assert all(thread == supervisor._worker.name for _, thread in MockRecorder.calls)


@pytest.mark.parametrize("site", ["lease_list_before", "lease_list_after", "lease_dict"])
@pytest.mark.parametrize("diagnostic_failure", [False, True])
def test_start_ack_bookkeeping_refuses_real_strict_dispatch(
    setup,
    monkeypatch,
    backend,
    site,
    diagnostic_failure,
):
    host, _ = setup[0](strict=True, control_timeout_s=2)
    supervisor = setup[1]
    transport = scripted(monkeypatch, [reply()])
    owner = inject(host, monkeypatch, site, diagnostic_failure)
    with pytest.raises(AuditPersistenceError):
        invoke(host, backend)
    assert_failed_start(host, owner, supervisor, transport)
    if diagnostic_failure:
        assert owner._faults == []
    before = list(MockRecorder.calls)
    with pytest.raises(AuditPersistenceError):
        invoke(host, backend)
    assert MockRecorder.calls == before
    assert_failed_start(host, owner, supervisor, transport)
    host.close(primary=RuntimeError("test cleanup"))
    supervisor.close()
    supervisor._worker.join(2)
    assert not supervisor._worker.is_alive()


def test_strict_dispatch_healthy_control_reaches_one_transport(setup, monkeypatch, backend):
    host, _ = setup[0](strict=True, control_timeout_s=2)
    transport = scripted(monkeypatch, [reply()])
    assert invoke(host, backend).content["findings"] == []
    assert len(transport.requests) == 1
    assert host._started == 1
    assert sum(name == "start" for name, _ in MockRecorder.calls) == 1
    assert sum(len(recorder.finished) for recorder in MockRecorder.instances) == 1


def falsify(host, backend):
    finding = StateFinding(
        "fixture",
        "synthetic-fingerprint",
        "L1",
        Disposition.UNCERTAIN,
        "sample.py",
        [1, 1],
        "synthetic finding",
    )
    return RealFalsifier(backend=backend).falsify(
        finding,
        host_audit=host.scope(purpose="falsify"),
    )


def test_real_strict_falsifier_refuses_after_unrecorded_handoff_fault(setup, monkeypatch, backend):
    host, _ = setup[0](strict=True, control_timeout_s=2)
    transport = scripted(monkeypatch, [reply('{"verdict":"DISMISSED","reasoning":"fixture"}')])
    owner = inject(host, monkeypatch, "lease_list_after", True)
    with pytest.raises(AuditPersistenceError):
        invoke(host, backend)
    assert owner._faults == [] and owner._refused
    before = list(MockRecorder.calls)
    with pytest.raises(AuditPersistenceError):
        falsify(host, backend)
    assert MockRecorder.calls == before
    assert_failed_start(host, owner, setup[1], transport)
    assert owner._faults == []


def test_real_strict_falsifier_healthy_control_reaches_one_transport(setup, monkeypatch, backend):
    host, _ = setup[0](strict=True, control_timeout_s=2)
    transport = scripted(monkeypatch, [reply('{"verdict":"DISMISSED","reasoning":"fixture"}')])
    assert falsify(host, backend) is Disposition.DISMISSED
    assert len(transport.requests) == 1
    assert host._started == 1


def test_default_dispatch_continues_without_recording_credit_after_handoff_failure(
    setup,
    monkeypatch,
    backend,
):
    host, notices = setup[0](strict=False, control_timeout_s=2)
    owner = inject(host, monkeypatch, "lease_list_after", True)
    transport = scripted(monkeypatch, [reply(), reply()])
    assert invoke(host, backend).content["findings"] == []
    assert owner._refused and owner._faults == []
    assert invoke(host, backend).content["findings"] == []
    assert len(transport.requests) == 2
    assert sum(name == "start" for name, _ in MockRecorder.calls) == 1
    assert notices == ["invocation_audit_unavailable"]
    assert host._started == 0 and not host._attempts
    assert not any(recorder.finished for recorder in MockRecorder.instances)
    summary = host.safe_summary()
    assert summary.state == "incomplete" and not summary.audit_complete
