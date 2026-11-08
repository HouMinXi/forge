"""Host epoch/factory controls; recorder is MOCK storage, never durability proof."""

from asyncio import CancelledError
from dataclasses import replace
import errno
import os
import json
from pathlib import Path
import threading
import time
import uuid

import pytest

from code_forge.invocation_host import HostAuditSession, HostAuditLimits
from code_forge.llm_invoke import AuditPersistenceError
from tests.test_invocation_host import MockRecorder, request

pytest_plugins = ["tests.test_invocation_host"]


def candidate(host, value="d"):
    context = replace(
        host._context, run_id=str(uuid.uuid4()), snapshot_id=str(uuid.uuid4()), source_hash=value * 64
    )
    manifest = json.loads(host._manifest)
    manifest.update(
        run_id=context.run_id,
        snapshot_id=context.snapshot_id,
        legacy_source_hash=context.source_hash,
        exact_diff_sha256=value * 64,
    )
    return context, manifest


def settled(host):
    scope = host.scope()
    attempt = scope.start_send(request())
    assert attempt.finish(
        outcome="failed", error_class="SendDeadlineBeforeNetwork", duration_s=0, usage=None
    )
    return scope, attempt


def advance(host, pair):
    return host.advance_source(pair[0], manifest=pair[1], deadline=time.monotonic() + 2)


def test_known_epoch_uses_owner_and_one_worker(setup):
    make, io = setup
    host, _ = make()
    old, attempt = settled(host)
    pair = candidate(host)
    assert advance(host, pair)
    assert host._context is pair[0]
    assert len(host._owner._epochs) == 2
    assert host._owner._epochs[0].terminal == "failed"
    assert host._owner._current.context is pair[0]
    fresh = host.scope().start_send(request())
    assert fresh._operation.source_epoch == pair[0].run_id
    assert all(name == io._worker.name for _, name in MockRecorder.calls)
    before = len(MockRecorder.calls)
    reserved = host._reserved
    assert old.start_send(request(parent=attempt, cause="retry")) is None
    assert len(MockRecorder.calls) == before
    assert host._reserved == reserved


@pytest.mark.parametrize("strict", [False, True])
def test_unknown_epoch_refuses_without_publishing(setup, strict):
    make, _ = setup
    host, _ = make(known=False, strict=strict)
    settled(host)
    context = replace(
        host._context, run_id=str(uuid.uuid4()), source_hash="d" * 64, snapshot_id=str(uuid.uuid4())
    )
    manifest = dict(
        schema_version=1,
        run_id=context.run_id,
        snapshot_id=context.snapshot_id,
        mode="diff",
        legacy_source_hash=context.source_hash,
        exact_diff_sha256="e" * 64,
        diff_bytes=7,
        files=[],
        repositories=[],
    )
    before = len(MockRecorder.calls)
    if strict:
        with pytest.raises(AuditPersistenceError):
            advance(host, (context, manifest))
    else:
        assert not advance(host, (context, manifest))
    assert host.failed and len(MockRecorder.calls) == before
    assert host._context.source_hash is None


def test_unsettled_attempt_blocks_advance(setup):
    host, _ = setup[0]()
    host.scope().start_send(request())
    assert not advance(host, candidate(host))
    assert len(host._owner._epochs) == 1


def test_same_identity_not_new_epoch_credit(setup):
    host, _ = setup[0]()
    settled(host)
    pair = candidate(host, "b")  # Original exact diff hash is b, source alias is irrelevant.
    before = host._context
    assert not advance(host, pair)
    assert host._context is before and host.failed


def test_epoch_count_bounded_without_additional_sends(setup):
    host, _ = setup[0](max_epochs=3)
    settled(host)
    assert advance(host, candidate(host, "d"))
    assert advance(host, candidate(host, "e"))
    assert not advance(host, candidate(host, "f"))
    assert len(host._owner._epochs) == 3
    with pytest.raises(ValueError):
        HostAuditLimits(max_epochs=65)


@pytest.mark.parametrize("phase", ["open", "input", "finalize"])
def test_late_epoch_no_host_credit_or_retry(setup, phase):
    host, _ = setup[0](control_timeout_s=0.025)
    settled(host)
    old_context = host._context
    MockRecorder.block = phase
    pair = candidate(host)
    assert not advance(host, pair)
    assert MockRecorder.entered.is_set()
    assert host.failed and host.safe_summary().publication_uncertain
    assert host._context is old_context
    assert host._control_bytes > 0
    MockRecorder.release.set()
    end = time.monotonic() + 1
    while host._control_bytes and time.monotonic() < end:
        time.sleep(0.001)
    assert host._control_bytes == 0
    count = len(MockRecorder.calls)
    assert not advance(host, pair)
    assert host.scope().start_send(request()) is None
    assert len(MockRecorder.calls) == count
    assert host._context is old_context
    assert host.safe_summary().state == "incomplete"
    # Late worker publication is allowed to exist. Never claim cancellation.
    assert len(host._owner._epochs) == 2


def test_close_wins_epoch_ack(setup):
    host, _ = setup[0](control_timeout_s=1)
    settled(host)
    old_context = host._context
    MockRecorder.block = "input"
    results = []
    thread = threading.Thread(target=lambda: results.append(advance(host, candidate(host))))
    thread.start()
    assert MockRecorder.entered.wait(1)
    host.close()
    MockRecorder.release.set()
    thread.join(1)
    assert results == [False]
    assert host._context is old_context and host._generation == 0


def test_inflight_start_prevents_epoch_even_before_attempt_registration(setup):
    host, _ = setup[0](control_timeout_s=1)
    settled(host)
    MockRecorder.block = "start"
    result = []
    thread = threading.Thread(target=lambda: result.append(host.scope().start_send(request())))
    thread.start()
    assert MockRecorder.entered.wait(1)
    assert not advance(host, candidate(host))
    assert len(host._owner._epochs) == 1
    MockRecorder.release.set()
    thread.join(1)
    assert host._controllers == 0
    assert result == [None]
    assert host.safe_summary().admitted_sends == 1


def test_epoch_fatal_identity_charge_and_cleanup(setup):
    host, _ = setup[0]()
    settled(host)
    error = MemoryError("private")
    cause = RuntimeError("private cause")
    error.__cause__ = cause
    MockRecorder.fail, MockRecorder.error = "input", error
    with pytest.raises(MemoryError) as caught:
        advance(host, candidate(host))
    assert caught.value is error and error.__cause__ is cause
    assert host.failed and host._control_bytes == 0 and not host._transition
    primary = ValueError("primary")
    before = (primary.args, vars(primary).copy(), primary.__cause__, primary.__context__)
    host.close(primary=primary)
    assert (primary.args, vars(primary), primary.__cause__, primary.__context__) == before


@pytest.mark.parametrize(
    "env, expected",
    [
        ({"XDG_STATE_HOME": "/state", "HOME": "/other"}, "/state/code-forge"),
        ({"HOME": "/home/user"}, "/home/user/.local/state/code-forge"),
        ({"XDG_STATE_HOME": "", "HOME": "/home/user"}, "/home/user/.local/state/code-forge"),
    ],
)
def test_factory_selects_fixed_roots_without_io(setup, monkeypatch, env, expected):
    old, _ = setup[0]()

    def forbidden(*a, **kw):
        raise AssertionError("filesystem work in pure selector")

    with monkeypatch.context() as patch:
        for name in ("mkdir", "chmod", "resolve", "stat", "iterdir"):
            patch.setattr(Path, name, forbidden)
        host = HostAuditSession.from_user_state(
            context=old._context, manifest=json.loads(old._manifest), environ=env
        )
    assert host._root == Path(expected) / "invocations-v2"
    assert host._maintenance_roots == (host._root, Path(expected) / "invocations-v1")
    assert host._io is None and not MockRecorder.calls
    host.close()


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"HOME": "relative"},
        {"HOME": "/safe", "XDG_STATE_HOME": "relative"},
        {"XDG_STATE_HOME": "/safe/../other"},
        {"XDG_STATE_HOME": "/bad\x00"},
        {"XDG_STATE_HOME": 1},
    ],
)
@pytest.mark.parametrize("strict", [False, True])
def test_bad_config_real_factory_latches_no_fallback(setup, env, strict):
    old, _ = setup[0]()
    notices = []
    options = dict(
        context=old._context,
        manifest=json.loads(old._manifest),
        environ=env,
        strict=strict,
        warn=notices.append,
    )
    if strict:
        with pytest.raises(AuditPersistenceError):
            HostAuditSession.from_user_state(**options)
    else:
        host = HostAuditSession.from_user_state(**options)
        assert host.failed and host._root is None and host._io is None
        assert host.scope().start_send(request()) is None
        assert host._io is None
        host.finalize()
    assert len(notices) == 1 and not MockRecorder.calls


def test_transition_gate_prevents_old_scope_admission(setup):
    host, _ = setup[0](control_timeout_s=1)
    scope, _ = settled(host)
    original = host._context
    MockRecorder.block = "input"
    result = []
    worker = threading.Thread(target=lambda: result.append(advance(host, candidate(host))))
    worker.start()
    assert MockRecorder.entered.wait(1)
    before = len(MockRecorder.calls)
    reserved = host._reserved
    start = time.monotonic()
    assert scope.start_send(request()) is None
    assert time.monotonic() - start < 0.1
    assert host._reserved == reserved
    assert len(MockRecorder.calls) == before
    MockRecorder.release.set()
    worker.join(1)
    assert result == [False] and host._context is original


def test_late_registration_deadline_rechecked(setup, monkeypatch):
    host, _ = setup[0](control_timeout_s=0.025)
    settled(host)
    original = host._context
    send = host._request

    def delayed(*args, **kwargs):
        result = send(*args, **kwargs)
        if args[1] == "advance":
            time.sleep(0.035)
        return result

    monkeypatch.setattr(host, "_request", delayed)
    assert not advance(host, candidate(host))
    assert host._context is original


def test_finalize_wins_transition_preserves_primary(setup):
    host, _ = setup[0](control_timeout_s=0.08)
    settled(host)
    original = host._context
    MockRecorder.block = "input"
    result = []
    worker = threading.Thread(target=lambda: result.append(advance(host, candidate(host))))
    worker.start()
    assert MockRecorder.entered.wait(1)
    primary = ValueError("private primary")
    primary.__cause__ = RuntimeError("private cause")
    before = (primary.args, vars(primary).copy(), primary.__cause__, primary.__context__)
    started = time.monotonic()
    host.finalize(primary=primary)
    assert time.monotonic() - started < 0.3
    MockRecorder.release.set()
    worker.join(1)
    assert result == [False] and host._context is original
    assert (primary.args, vars(primary), primary.__cause__, primary.__context__) == before


def test_advance_same_worker_fails_without_recursive_wait(setup):
    from code_forge.invocation_io import AuditPhase

    host, _ = setup[0]()
    settled(host)
    pair = candidate(host)

    # Use the supervisor's normal control protocol, not a second worker.
    from tests.test_invocation_io import Target

    target = Target(action=lambda *args: advance(host, pair))
    ticket = host._io.submit(target, AuditPhase.INPUT, deadline=time.monotonic() + 1)
    outcome = host._io.wait(ticket)
    assert outcome.state == "ack" and outcome.value is False
    assert host.failed and not host._transition


def test_bind_source_invalidates_prebound_scope(setup):
    host, _ = setup[0]()
    old = host.scope()
    pair = candidate(host)
    host.bind_source(pair[0], manifest=pair[1])
    assert old.start_send(request()) is None
    assert not MockRecorder.calls


def test_default_epoch_bound_exactly_64(setup):
    host, _ = setup[0]()
    settled(host)
    for i in range(63):
        pair = candidate(host, "d" if i % 2 == 0 else "e")
        assert advance(host, pair)
    assert not advance(host, candidate(host, "f"))
    assert len(host._owner._epochs) == 64


def test_factory_real_owner_uses_managed_root_only(setup):
    old, _ = setup[0]()
    host = HostAuditSession.from_user_state(
        context=old._context,
        manifest=json.loads(old._manifest),
        environ={"HOME": "/home/explicit"},
        supervisor=old._io,
        limits=old.limits,
    )
    settled(host)
    assert MockRecorder.instances[-1].root == Path(
        "/home/explicit/.local/state/code-forge/invocations-v2"
    )
    assert MockRecorder.instances[-1].version == 2
    assert host._maintenance_roots[1] == Path("/home/explicit/.local/state/code-forge/invocations-v1")
    host.close()


@pytest.fixture
def managed(setup, tmp_path):
    old, _ = setup[0]()
    hosts = []

    def make(**kwargs):
        options = dict(
            context=old._context,
            manifest=json.loads(old._manifest),
            environ={"XDG_STATE_HOME": str(tmp_path)},
            supervisor=old._io,
            limits=old.limits,
        )
        options.update(kwargs)
        host = HostAuditSession.from_user_state(**options)
        hosts.append(host)
        return host

    yield make
    for host in hosts:
        host.close(primary=RuntimeError("fixture cleanup"))


def test_managed_absent_roots_not_created_or_claimed_pruned(managed, monkeypatch):
    from code_forge import invocation_host as module

    host = managed()
    roots = host._maintenance_roots
    calls = []
    monkeypatch.setattr(module, "maintain_audit_root", lambda *a, **kw: calls.append(a))
    settled(host)
    assert not calls and not any(root.exists() for root in roots)
    assert host._maintenance_done


def test_existing_managed_roots_maintained_before_open_once(managed, monkeypatch):
    from code_forge import invocation_host as module
    from code_forge.invocation_audit import AuditAcknowledgement

    host = managed()
    for root in host._maintenance_roots:
        root.mkdir(parents=True, mode=0o700)
    sequence = []
    original = MockRecorder.touch

    def touch(self, name):
        sequence.append(name)
        return original(self, name)

    def maintain(root, **kwargs):
        sequence.append(root.name)
        assert threading.current_thread() is host._io._worker
        assert kwargs["monotonic"]() < kwargs["action_deadline"]
        return AuditAcknowledgement(True, None)

    monkeypatch.setattr(MockRecorder, "touch", touch)
    monkeypatch.setattr(module, "maintain_audit_root", maintain)
    settled(host)
    settled(host)
    assert sequence[:3] == ["invocations-v2", "invocations-v1", "open"]
    host.finalize(maintain=True)
    assert sequence.count("invocations-v2") == sequence.count("invocations-v1") == 1


@pytest.mark.parametrize("kind", ["symlink", "file", "permission"])
@pytest.mark.parametrize("strict", [False, True])
def test_managed_nonmissing_errors_never_skipped(managed, monkeypatch, tmp_path, kind, strict):
    from code_forge import invocation_host as module

    host = managed(strict=strict, warn=lambda _: None)
    maintenance_calls = []

    def forbidden_maintenance(*args, **kwargs):
        maintenance_calls.append(args)
        raise AssertionError("invalid probe must not reach maintenance")

    monkeypatch.setattr(module, "maintain_audit_root", forbidden_maintenance)
    root = host._root
    root.parent.mkdir()
    if kind == "symlink":
        root.symlink_to(tmp_path, target_is_directory=True)
    elif kind == "file":
        root.write_text("not a directory")
    else:
        original = module.os.open

        def denied(path, *args, **kwargs):
            if path == "invocations-v2":
                raise PermissionError("controlled EACCES; not real permission qualification")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(module.os, "open", denied)
    if strict:
        with pytest.raises(AuditPersistenceError):
            host.scope().start_send(request())
    else:
        assert host.scope().start_send(request()) is None
    assert host.failed and not MockRecorder.instances and not host._maintenance_done
    assert not maintenance_calls


def test_existing_probe_then_disappears_maintenance_fault_propagates(managed, monkeypatch):
    from code_forge import invocation_host as module
    from code_forge.invocation_audit import AuditAcknowledgement, AuditFault

    host = managed(warn=lambda _: None)
    host._root.mkdir(parents=True)

    def maintain(root, **kwargs):
        root.rmdir()
        return AuditAcknowledgement(False, AuditFault("integrity_error", "maintenance", None))

    monkeypatch.setattr(module, "maintain_audit_root", maintain)
    assert host.scope().start_send(request()) is None
    assert host.failed and not MockRecorder.instances


def test_absent_probe_then_created_is_not_claimed_maintained(managed, monkeypatch):
    from code_forge import invocation_host as module

    host = managed()
    probe = host._managed_root_exists
    maintained = []

    def replaced(root, deadline):
        found = probe(root, deadline)
        if root == host._root:
            root.mkdir(parents=True)
        return found

    monkeypatch.setattr(host, "_managed_root_exists", replaced)
    monkeypatch.setattr(module, "maintain_audit_root", lambda root, **kwargs: maintained.append(root))
    settled(host)
    assert host._root.exists() and not maintained


def test_managed_total_deadline_no_open_after_slow_maintenance(managed, monkeypatch):
    from code_forge import invocation_host as module
    from code_forge.invocation_audit import AuditAcknowledgement

    host = managed(
        limits=HostAuditLimits(control_timeout_s=0.03, batch_bytes=512, capture_buffer_bytes=4096),
        warn=lambda _: None,
    )
    for root in host._maintenance_roots:
        root.mkdir(parents=True)
    deadlines = []

    def maintain(root, **kwargs):
        deadlines.append(kwargs["action_deadline"])
        time.sleep(0.022)
        return AuditAcknowledgement(True, None)

    monkeypatch.setattr(module, "maintain_audit_root", maintain)
    start = time.monotonic()
    assert host.scope().start_send(request()) is None
    assert time.monotonic() - start < 0.12
    assert not MockRecorder.instances and host.failed
    end = time.monotonic() + 1
    while host._control_bytes and time.monotonic() < end:
        time.sleep(0.001)
    assert host._control_bytes == 0
    assert len(deadlines) == 2 and deadlines[0] == deadlines[1]


def test_close_during_managed_probe_prevents_open_and_closes_fds(managed, monkeypatch):
    from code_forge import invocation_host as module

    host = managed()
    host._root.mkdir(parents=True)
    maintenance_calls = []
    monkeypatch.setattr(module, "maintain_audit_root", lambda *a, **kw: maintenance_calls.append(a))
    entered, release = threading.Event(), threading.Event()
    original = module.os.open
    observed = []

    def blocked(path, *args, **kwargs):
        fd = original(path, *args, **kwargs)
        if path == "invocations-v2":
            observed.append(fd)
            entered.set()
            assert release.wait(1)
        return fd

    monkeypatch.setattr(module.os, "open", blocked)
    result = []
    controller = threading.Thread(target=lambda: result.append(host.scope().start_send(request())))
    controller.start()
    assert entered.wait(1)
    host.close()
    release.set()
    controller.join(1)
    assert result == [None] and not MockRecorder.instances
    assert not maintenance_calls
    for fd in observed:
        with pytest.raises(OSError):
            module.os.fstat(fd)


def test_managed_fatal_identity_and_no_worker_replacement(managed, monkeypatch):
    from code_forge import invocation_host as module

    host = managed(warn=lambda _: None)
    host._root.mkdir(parents=True)
    error = MemoryError("private maintenance")
    error.__cause__ = RuntimeError("private cause")
    workers = []

    def fail(*a, **kwargs):
        workers.append(threading.current_thread())
        raise error

    monkeypatch.setattr(module, "maintain_audit_root", fail)
    with pytest.raises(MemoryError) as caught:
        host.scope().start_send(request())
    assert caught.value is error and error.__cause__.args == ("private cause",)
    assert host.failed and host._control_bytes == 0 and not MockRecorder.instances
    assert workers == [host._io._worker]
    assert host.scope().start_send(request()) is None
    assert workers == [host._io._worker]


def test_managed_quota_recovery_order_is_preopen_not_after_failure(managed, monkeypatch):
    """MOCK quota flag proves ordering, not actual prune eligibility/durability."""
    from code_forge import invocation_host as module
    from code_forge.invocation_audit import AuditAcknowledgement

    host = managed()
    host._root.mkdir(parents=True)
    quota_full = [True]
    original = MockRecorder.open

    def open_if_reclaimed(self):
        assert not quota_full[0], "OPEN attempted before reclaim"
        return original(self)

    def reclaim(root, **kwargs):
        quota_full[0] = False
        return AuditAcknowledgement(True, None)

    monkeypatch.setattr(MockRecorder, "open", open_if_reclaimed)
    monkeypatch.setattr(module, "maintain_audit_root", reclaim)
    settled(host)
    assert not quota_full[0] and host._opened


def test_controller_between_commands_blocks_epoch(setup, monkeypatch):
    host, _ = setup[0](control_timeout_s=1)
    settled(host)
    entered, release = threading.Event(), threading.Event()
    original = host._ensure_open
    result = []

    def paused(deadline):
        entered.set()
        assert release.wait(1)
        return original(deadline)

    monkeypatch.setattr(host, "_ensure_open", paused)
    thread = threading.Thread(target=lambda: result.append(host.scope().start_send(request())))
    thread.start()
    try:
        assert entered.wait(1)
        assert not host._commands and host._controllers == 1
        assert not advance(host, candidate(host))
        assert len(host._owner._epochs) == 1
    finally:
        release.set()
        thread.join(1)
    assert host._controllers == 0


@pytest.mark.parametrize("strict", [False, True])
def test_unsupported_os_uses_real_admission_denial_no_probe(managed, monkeypatch, strict):
    from code_forge import invocation_host as module

    host = managed(strict=strict, warn=lambda _: None)
    monkeypatch.setattr(module.sys, "platform", "darwin")

    def forbidden(*args, **kwargs):
        raise AssertionError("unsupported platform must use explicit admission denial")

    monkeypatch.setattr(host, "_managed_root_exists", forbidden)
    if strict:
        with pytest.raises(AuditPersistenceError):
            host.scope().start_send(request())
    else:
        assert host.scope().start_send(request()) is None
    assert host.failed and not MockRecorder.instances


@pytest.mark.parametrize(
    "kind", [RuntimeError, KeyboardInterrupt, SystemExit, MemoryError, CancelledError]
)
@pytest.mark.parametrize("close_fails", [False, True])
def test_managed_probe_preserves_primary_and_close_evidence(monkeypatch, kind, close_fails):
    """Private real FDs, not a recorder/filesystem admission override."""
    from code_forge import invocation_host as module

    host = HostAuditSession.__new__(HostAuditSession)
    primary, secondary = kind("private primary"), OSError(errno.EIO, "private close")
    primary.__cause__ = LookupError("existing cause")
    primary.__context__ = ValueError("existing context")
    chain = (primary.__cause__, primary.__context__, primary.__suppress_context__)
    calls = 0
    real_close = os.close
    closed = []

    def checkpoint(_deadline):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise primary

    def close(fd):
        real_close(fd)
        closed.append(fd)
        if close_fails:
            raise secondary

    host._managed_checkpoint = checkpoint
    before = set(os.listdir("/proc/self/fd"))
    with monkeypatch.context() as patch:
        patch.setattr(module.os, "close", close)
        with pytest.raises(BaseException) as caught:
            host._managed_root_exists(Path("/tmp/probe"), time.monotonic() + 1)
    assert caught.value is primary
    assert (primary.__cause__, primary.__context__, primary.__suppress_context__) == chain
    assert len(closed) == 1
    assert set(os.listdir("/proc/self/fd")) == before
    if close_fails:
        assert primary._invocation_audit_close_errors == (secondary,)
        assert "private" not in " ".join(primary.__notes__)
    else:
        assert not hasattr(primary, "_invocation_audit_close_errors")


@pytest.mark.parametrize("two_failures", [False, True])
def test_managed_probe_close_failure_during_fd_transfer(monkeypatch, two_failures):
    from code_forge import invocation_host as module

    host = HostAuditSession.__new__(HostAuditSession)
    host._managed_checkpoint = lambda _deadline: None
    first, second = OSError(errno.EIO, "first"), OSError(errno.EIO, "second")
    real_close = os.close
    closed = []

    def close(fd):
        real_close(fd)
        closed.append(fd)
        if len(closed) == 1:
            raise first
        if two_failures:
            raise second

    before = set(os.listdir("/proc/self/fd"))
    with monkeypatch.context() as patch:
        patch.setattr(module.os, "close", close)
        with pytest.raises(OSError) as caught:
            host._managed_root_exists(Path("/tmp"), time.monotonic() + 1)
    assert caught.value is first
    assert len(closed) == 2
    assert set(os.listdir("/proc/self/fd")) == before
    if two_failures:
        assert first._invocation_audit_close_errors == (second,)
    else:
        assert not hasattr(first, "_invocation_audit_close_errors")


@pytest.mark.parametrize("close_fails", [False, True])
def test_managed_probe_success_or_single_close_failure(monkeypatch, close_fails):
    from code_forge import invocation_host as module

    host = HostAuditSession.__new__(HostAuditSession)
    host._managed_checkpoint = lambda _deadline: None
    secondary = OSError(errno.EIO, "single close")
    real_close = os.close

    def close(fd):
        real_close(fd)
        if close_fails:
            raise secondary

    before = set(os.listdir("/proc/self/fd"))
    with monkeypatch.context() as patch:
        patch.setattr(module.os, "close", close)
        if close_fails:
            with pytest.raises(OSError) as caught:
                host._managed_root_exists(Path("/"), time.monotonic() + 1)
            assert caught.value is secondary
        else:
            assert host._managed_root_exists(Path("/"), time.monotonic() + 1)
    assert set(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize("kind", [KeyboardInterrupt, SystemExit, MemoryError, CancelledError])
@pytest.mark.parametrize("close_fails", [False, True])
def test_managed_probe_public_start_keeps_fatal_and_blocks_later_send(
    setup, monkeypatch, kind, close_fails
):
    from code_forge import invocation_host as module

    host, _ = setup[0]()
    host._managed_roots = True
    host._maintenance_roots = (Path("/tmp/probe"),)
    primary, secondary = kind("private primary"), OSError(errno.EIO, "private close")
    calls = 0
    owned = []
    real_open, real_close = os.open, os.close

    def checkpoint(_deadline):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise primary

    def opening(path, *args, **kwargs):
        fd = real_open(path, *args, **kwargs)
        if path == "/":
            owned.append(fd)
        return fd

    def closing(fd):
        real_close(fd)
        if close_fails and fd in owned:
            raise secondary

    host._managed_checkpoint = checkpoint
    before = set(os.listdir("/proc/self/fd"))
    with monkeypatch.context() as patch:
        patch.setattr(module.os, "open", opening)
        patch.setattr(module.os, "close", closing)
        with pytest.raises(BaseException) as caught:
            host.scope().start_send(request())
    assert caught.value is primary
    if close_fails:
        assert primary._invocation_audit_close_errors == (secondary,)
    assert host.failed
    assert not host._commands and host._control_bytes == 0
    assert host.scope().start_send(request()) is None
    assert not MockRecorder.instances
    assert set(os.listdir("/proc/self/fd")) == before


def test_managed_probe_preserves_existing_evidence_without_exception_hooks(monkeypatch):
    from code_forge import invocation_host as module

    class Hostile(RuntimeError):
        def __getattribute__(self, name):
            if name in ("__dict__", "__notes__", "_invocation_audit_close_errors"):
                raise AssertionError("subclass getter")
            return super().__getattribute__(name)

        def __setattr__(self, name, value):
            raise AssertionError("subclass setter")

    class HostileNotes(list):
        def append(self, value):
            raise AssertionError("subclass append")

    host = HostAuditSession.__new__(HostAuditSession)
    primary = Hostile("primary")
    first, second = OSError("existing"), OSError(errno.EIO, "new")
    state = BaseException.__dict__["__dict__"].__get__(primary)
    state["_invocation_audit_close_errors"] = (first,)
    notes = HostileNotes(["original note"])
    state["__notes__"] = notes
    calls = 0
    real_close = os.close

    def checkpoint(_deadline):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise primary

    def close(fd):
        real_close(fd)
        raise second

    host._managed_checkpoint = checkpoint
    with monkeypatch.context() as patch:
        patch.setattr(module.os, "close", close)
        with pytest.raises(Hostile) as caught:
            host._managed_root_exists(Path("/"), time.monotonic() + 1)
    assert caught.value is primary
    assert state["_invocation_audit_close_errors"] == (first, second)
    assert state["__notes__"] is notes and notes == ["original note"]
