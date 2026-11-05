"""Lifecycle races on the real sender/host, with MOCK storage and no network.

These tests do not qualify any native filesystem for recording.
"""

import threading
import time
import urllib.request
from types import SimpleNamespace

import pytest

from code_forge import invocation_host as module
from code_forge.invocation_send_audit import OpenAiSendTransport, SendAuditScope, SendLifecycleClosed
from code_forge.llm_invoke import AuditPersistenceError
from tests.test_invocation_host import request
from tests.test_invocation_send_audit import Response

pytest_plugins = ["tests.test_invocation_host"]


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("boundary", ["entered_ack", "after_enter_return", "after_lifecycle_check"])
def test_finalization_never_claims_complete_with_sender_at_boundary(
    setup, monkeypatch, strict, boundary
):
    make, _ = setup
    host, notices = make(strict=strict)
    entered, release = threading.Event(), threading.Event()
    outcomes, opens = [], []
    transport = OpenAiSendTransport(
        SendAuditScope(host.scope()),
        cause="initial",
        backend_name="fixture",
        model="unit",
        read_with_deadline=lambda *a, **k: None,
    )
    transport._transport = SimpleNamespace(open=lambda *a, **k: opens.append(True) or Response(b"ok"))
    original_request = host._request
    original_enter = module._HostAttempt.enter_send

    def pause():
        entered.set()
        assert release.wait(3)

    def control(phase, command, *args, **kwargs):
        value = original_request(phase, command, *args, **kwargs)
        if command == "entered" and boundary == "entered_ack":
            pause()
        return value

    def enter(attempt):
        value = original_enter(attempt)
        if boundary == "after_enter_return":
            pause()
        return value

    lifecycle_checks = []
    original_check = transport.scope._check_lifecycle

    def check():
        original_check()
        lifecycle_checks.append(True)
        if boundary == "after_lifecycle_check" and len(lifecycle_checks) == 3:
            pause()

    monkeypatch.setattr(transport.scope, "_check_lifecycle", check)
    monkeypatch.setattr(host, "_request", control)
    monkeypatch.setattr(module._HostAttempt, "enter_send", enter)

    def send():
        try:
            outcomes.append(
                transport.open(
                    urllib.request.Request("https://provider.invalid/v1", data=b"private"),
                    deadline=time.monotonic() + 3,
                )
            )
        except BaseException as error:  # noqa: BLE001 - inspect cross-thread outcome
            outcomes.append(error)

    thread = threading.Thread(target=send)
    thread.start()
    try:
        assert entered.wait(1)
        before = time.monotonic()
        summary = host.finalize(primary=RuntimeError("outer cancellation"))
        assert time.monotonic() - before < 0.5
        assert summary.state == "incomplete" and not summary.audit_complete
        assert len(notices) == 1
        assert host.finalize() == summary
        host.close()
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive()
    attempt = next(iter(host._attempts))
    if boundary == "entered_ack":
        assert not attempt._entered  # A late ACK must not resurrect the capability.
    if boundary != "after_lifecycle_check":
        assert not opens and isinstance(outcomes[0], SendLifecycleClosed)
    else:
        # Cancellation cannot be atomic with an arbitrary transport's network call.
        # The admitted but unsettled attempt must keep finalization incomplete.
        assert opens == [True]
    assert not host.safe_summary().audit_complete
    assert len(notices) == 1


@pytest.mark.parametrize("strict", [False, True])
def test_finalization_of_idle_unsettled_attempt_is_incomplete(setup, strict):
    make, _ = setup
    host, notices = make(strict=strict)
    attempt = host.scope().start_send(request())
    assert attempt.enter_send()
    if strict:
        with pytest.raises(AuditPersistenceError):
            host.finalize()
    else:
        assert not host.finalize().audit_complete
    assert host._closed and not host.safe_summary().audit_complete
    assert host.safe_summary().state == "incomplete" and len(notices) == 1


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("command", ["start", "entered"])
def test_late_control_ack_after_close_does_not_revive_attempt(setup, monkeypatch, strict, command):
    make, _ = setup
    host, _ = make(strict=strict)
    scope = host.scope()
    attempt = scope.start_send(request()) if command == "entered" else None
    original = host._request

    def close_after_ack(phase, name, *args, **kwargs):
        result = original(phase, name, *args, **kwargs)
        if name == command:
            host.close(primary=RuntimeError("cancelled"))
        return result

    monkeypatch.setattr(host, "_request", close_after_ack)
    action = attempt.enter_send if attempt is not None else lambda: scope.start_send(request())
    with pytest.raises(SendLifecycleClosed):
        action()
    assert not host.safe_summary().audit_complete
    assert host.safe_summary().state == "incomplete"
    if attempt is not None:
        assert not attempt._entered
    else:
        assert not host._attempts and host.safe_summary().admitted_sends == 0


@pytest.mark.parametrize("strict", [False, True])
def test_finalization_during_epoch_ack_keeps_old_host_identity(setup, monkeypatch, strict):
    from tests.test_invocation_host_epoch import advance, candidate, settled

    host, notices = setup[0](strict=strict)
    old_scope, _ = settled(host)
    old_context, old_generation = host._context, host._generation
    pair = candidate(host)
    entered, release = threading.Event(), threading.Event()
    original = host._request
    results = []

    def control(phase, command, *args, **kwargs):
        value = original(phase, command, *args, **kwargs)
        if command == "advance":
            entered.set()
            assert release.wait(3)
        return value

    monkeypatch.setattr(host, "_request", control)

    def transition():
        try:
            results.append(advance(host, pair))
        except BaseException as error:  # noqa: BLE001 - inspect cross-thread outcome
            results.append(error)

    thread = threading.Thread(target=transition)
    thread.start()
    try:
        assert entered.wait(1)
        summary = host.finalize(primary=RuntimeError("outer cancellation"))
        assert summary.state == "incomplete" and not summary.audit_complete
        assert old_scope._generation == host.scope()._generation == old_generation
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive() and not host._transition
    assert host._context is old_context and host._generation == old_generation
    assert len(notices) == 1
    assert isinstance(results[0], AuditPersistenceError) if strict else results == [False]
    assert not host.finalize().audit_complete


@pytest.mark.parametrize("strict", [False, True])
def test_settled_serial_finalization_remains_complete(setup, strict):
    from tests.test_invocation_host_epoch import settled

    host, notices = setup[0](strict=strict)
    settled(host)
    summary = host.finalize()
    assert summary.state == "completed" and summary.audit_complete
    assert host.finalize() == summary and not notices


@pytest.mark.parametrize("strict", [False, True])
def test_empty_finalization_does_not_invent_active_work(setup, strict):
    host, notices = setup[0](strict=strict)
    summary = host.finalize()
    assert summary.state == "completed" and not host.failed
    assert host._owner is None and not notices
    assert host.finalize() == summary


@pytest.mark.parametrize("strict", [False, True])
def test_already_degraded_scope_still_obeys_lifecycle_revocation(setup, strict):
    host, _ = setup[0](strict=strict)
    scope = SendAuditScope(host.scope())
    scope.disabled = True
    host.finalize()
    with pytest.raises(SendLifecycleClosed):
        scope.start(
            urllib.request.Request("https://provider.invalid/v1", data=b"private"),
            cause="retry",
            backend_name="fixture",
            model="unit",
            deadline=time.monotonic() + 1,
        )


def test_ordinary_recording_failure_still_permits_default_send(setup):
    host, notices = setup[0]()
    host.warn("audit_send_unavailable")
    transport = OpenAiSendTransport(
        SendAuditScope(host.scope()),
        cause="initial",
        backend_name="fixture",
        model="unit",
        read_with_deadline=lambda *a, **k: None,
    )
    opens = []
    transport._transport = SimpleNamespace(open=lambda *a, **k: opens.append(True) or Response(b"ok"))
    response = transport.open(
        urllib.request.Request("https://provider.invalid/v1", data=b"private"),
        deadline=time.monotonic() + 1,
    )
    assert isinstance(response, Response) and opens == [True] and len(notices) == 1


@pytest.mark.parametrize("strict", [False, True])
def test_real_invoke_treats_observed_revocation_as_nonretryable(setup, monkeypatch, strict):
    from code_forge import llm_invoke as invoke
    from code_forge.backend import BackendConfig
    from tests.test_invocation_send_audit import reply, scripted

    monkeypatch.setenv("HOST_TEST_KEY", "mock-secret")
    backend = BackendConfig(
        name="fixture",
        type="api",
        format="openai",
        model="unit",
        base_url="https://provider.invalid/v1",
        api_key_env="HOST_TEST_KEY",
        max_tokens=8,
        output_token_limit=64,
    )
    transport = scripted(monkeypatch, [reply()])
    host, notices = setup[0](strict=strict)
    original = host._request

    def finalize_after_entered_ack(phase, command, *args, **kwargs):
        result = original(phase, command, *args, **kwargs)
        if command == "entered":
            host.finalize(primary=RuntimeError("outer cancellation"))
        return result

    monkeypatch.setattr(host, "_request", finalize_after_entered_ack)
    with pytest.raises(AuditPersistenceError) as caught:
        invoke._invoke_api("private prompt", backend, 3, initial_delay_s=0, host_audit=host.scope())
    assert isinstance(caught.value.__cause__, SendLifecycleClosed)
    assert not transport.requests and len(notices) == 1
    assert host._reserved == 1 and not host.safe_summary().audit_complete


def test_explicit_incomplete_only_marks_unfinished_source_epoch(setup):
    from tests.test_invocation_host_epoch import advance, candidate, settled

    host, _ = setup[0]()
    settled(host)
    assert advance(host, candidate(host))
    host.scope().start_send(request())
    summary = host.finalize()
    old, current = host._owner._epochs
    assert not old.recorder.audit_incomplete
    assert current.recorder.audit_incomplete
    assert not host._owner._summary().audit_complete and not summary.audit_complete


@pytest.mark.parametrize("invalid", [0, 1, None, "true"])
def test_owner_explicit_incomplete_requires_bool_without_writes(setup, invalid):
    from tests.test_invocation_host import MockRecorder
    from tests.test_invocation_host_epoch import settled

    host, _ = setup[0]()
    settled(host)
    before = list(MockRecorder.calls)
    with pytest.raises(TypeError):
        host._owner.finalize("incomplete", audit_incomplete=invalid)
    assert MockRecorder.calls == before and not host._owner._refused


def test_owner_v1_explicit_incomplete_rejects_without_writes(setup):
    from tests.test_invocation_host import MockRecorder
    from tests.test_invocation_host_epoch import settled

    host, _ = setup[0]()
    settled(host)
    host._owner._audit_schema_version = 1
    before = list(MockRecorder.calls)
    with pytest.raises(ValueError):
        host._owner.finalize("incomplete", audit_incomplete=True)
    assert MockRecorder.calls == before and not host._owner._refused
