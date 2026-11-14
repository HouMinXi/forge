"""Actual host/owner/stage/supervisor consumers with a MOCK storage boundary.

These tests do not admit a native filesystem or prove durable storage behavior.
No provider, HTTP, filesystem qualification override or CLI entry point is used.
"""

from asyncio import CancelledError
from dataclasses import asdict, replace
import hashlib
import io
from pathlib import Path
from types import SimpleNamespace
import threading
import time
import uuid

import pytest

from code_forge import invocation_audit as audit
from code_forge import invocation_host as module
from code_forge import invocation_io, invocation_owner
from code_forge.invocation_host import HostAuditSession, HostAuditLimits
from code_forge.invocation_io import IoLimits, process_audit_io
from code_forge.invocation_send_audit import SendLifecycleClosed, SendRequest
from code_forge.invocation_stream import CaptureUnavailable, _CaptureNoticeDelivery
from code_forge.llm_invoke import AuditPersistenceError, Usage


class MockRecorder:
    """Controlled ACK issuer; only the storage boundary is replaced."""

    instances = []
    records = {}
    block = None
    fail = None
    error = None
    entered = None
    release = None
    calls = []

    def __init__(
        self, root, *, context, monotonic, utc_now, audit_schema_version, scoped_fault_domain=False
    ):
        audit._context(context)
        self.root, self.context, self.version = root, context, audit_schema_version
        self.scoped_fault_domain = scoped_fault_domain
        self.manifest = None
        self.stages = {}
        self.started = []
        self.finished = []
        self.instances.append(self)
        self.records[context.run_id] = self

    def touch(self, name):
        self.calls.append((name, threading.current_thread().name))
        if name == self.block:
            self.entered.set()
            assert self.release.wait(3)
        if name == self.fail:
            raise self.error or OSError("PRIVATE recorder diagnostic")

    def open(self):
        self.touch("open")
        return audit.AuditAcknowledgement(True, None)

    def publish_input_manifest(self, *, manifest):
        self.touch("input")
        audit._manifest(manifest, asdict(self.context))
        self.manifest = manifest
        return audit.AuditAcknowledgement(True, None)

    def scoped_snapshot(self):
        self.touch("snapshot")
        return SimpleNamespace(input_manifest=self.manifest)

    def start(self, **kwargs):
        self.touch("start")
        self.started.append(kwargs)
        return audit.StartAcknowledgement(True, str(uuid.uuid4()), None)

    def begin_stage(self, attempt_id, *, layer, action_deadline):
        self.touch("begin")
        identity = audit.StageIdentity(
            self.context.run_id, attempt_id, str(uuid.uuid4()), str(uuid.uuid4()), layer
        )
        handle = audit.StageHandle(identity)
        self.stages[handle] = bytearray()
        checkpoint = audit.StageCheckpoint(identity, 0, hashlib.sha256(b"").hexdigest())
        return audit.StageAcknowledgement(True, handle, checkpoint, 0, False, None)

    def append_stage(self, handle, *, offset, data, action_deadline):
        self.touch("append")
        body = self.stages[handle]
        assert len(body) == offset
        body.extend(data)
        checkpoint = audit.StageCheckpoint(handle.identity, len(body), hashlib.sha256(body).hexdigest())
        return audit.StageAcknowledgement(True, handle, checkpoint, len(data), False, None)

    def seal_stage(self, handle, *, checkpoint, action_deadline):
        self.touch("seal")
        return audit.StageAcknowledgement(True, handle, checkpoint, 0, False, None)

    def adopt_stage(
        self, handle, *, checkpoint, complete, original_bytes, usage, dispatch_state, action_deadline
    ):
        self.touch("adopt")
        assert usage is None, "Aggregate LLM usage must not manufacture native credit"
        return audit.AcquiredAcknowledgement(
            complete,
            handle.identity.observation_id,
            audit.RawRef(
                handle.identity.artifact_id,
                handle.identity.layer,
                checkpoint.sha256,
                checkpoint.retained_bytes,
                original_bytes,
                not complete,
            ),
            None
            if complete
            else audit.AuditFault("persistence_error", "acquired", handle.identity.attempt_id),
        )

    def cancel_stage(self, handle):
        # Explicit nonblocking signal, not recorder I/O.
        handle._cancel.set()
        return audit.AuditAcknowledgement(True, None)

    def observe_response(self, **kwargs):
        self.touch("observe")
        return audit.AuditAcknowledgement(True, None)

    def finish(self, **kwargs):
        self.touch("finish")
        self.finished.append(kwargs)
        return audit.AuditAcknowledgement(True, None)

    def finalize(self, **kwargs):
        self.touch("finalize")
        self.audit_incomplete = getattr(self, "audit_incomplete", False) or kwargs.get(
            "audit_incomplete", False
        )
        return audit.AuditAcknowledgement(True, None)

    @classmethod
    def reopen_scoped(cls, root, run_id):
        recorder = cls.records[run_id]
        recorder.touch("reopen")
        unknown = len(recorder.started)
        usage = {
            key: dict(
                known_subtotal=0, unknown_attempt_count=unknown, invalid_attempt_count=0, total=None
            )
            for key in ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens")
        }
        summary = dict(
            observed_api_send_count=unknown,
            observed_cli_launch_count=0,
            unknown_dispatch_count=0,
            usage=usage,
            timing=dict(known_work_s=0, unknown_duration_count=unknown),
            state="completed",
        )
        return SimpleNamespace(
            faults=(), audit_complete=not getattr(recorder, "audit_incomplete", False), summary=summary
        )


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(invocation_owner, "AttemptRecorder", MockRecorder)
    monkeypatch.setattr(invocation_io, "_PROCESS", None)
    for key, value in dict(
        instances=[],
        records={},
        block=None,
        fail=None,
        error=None,
        calls=[],
        entered=threading.Event(),
        release=threading.Event(),
    ).items():
        monkeypatch.setattr(MockRecorder, key, value)
    supervisor = process_audit_io(limits=IoLimits(32, 8192, 512, 8), warn=lambda _: None)
    hosts = []

    def make(*, strict=False, known=True, warn=None, scoped_fault_domain=False, **changes):
        context = audit.InvocationContext(
            str(uuid.uuid4()),
            "a" * 64 if known else None,
            str(uuid.uuid4()) if known else None,
            None,
            None,
            None,
            None,
            "review",
            None,
        )
        manifest = (
            dict(
                schema_version=1,
                run_id=context.run_id,
                snapshot_id=context.snapshot_id,
                mode="diff",
                legacy_source_hash=context.source_hash,
                exact_diff_sha256="b" * 64,
                diff_bytes=7,
                files=[],
                repositories=[],
            )
            if known
            else None
        )
        settings = dict(
            control_timeout_s=0.08,
            flush_timeout_s=0.08,
            batch_bytes=512,
            capture_buffer_bytes=4096,
            max_delay_s=0,
        )
        settings.update(changes)
        notices = []
        host = HostAuditSession(
            artifact_root=Path("/mock-not-native"),
            context=context,
            manifest=manifest,
            strict=strict,
            limits=HostAuditLimits(**settings),
            supervisor=supervisor,
            warn=warn or notices.append,
            scoped_fault_domain=scoped_fault_domain,
        )
        hosts.append(host)
        return host, notices

    yield make, supervisor
    MockRecorder.release.set()
    for host in hosts:
        host.close(primary=RuntimeError("fixture cleanup"))
    supervisor.close()
    if supervisor._worker:
        supervisor._worker.join(3)
        assert not supervisor._worker.is_alive()


def request(*, parent=None, cause="initial", deadline=None):
    return SendRequest(
        cause, parent, "c" * 64, 10, "POST", "fixture", "model", deadline or time.monotonic() + 2
    )


def admit(scope, request):
    attempt = scope.start_send(request)
    if attempt is not None:
        assert attempt.enter_send()
    return attempt


def complete(attempt, body=b"private reasoning_content"):
    captured = attempt.new_response(status=200, redirect_hop=None, deadline=attempt._deadline)
    assert b"".join(captured.chunks(io.BytesIO(body))) == body
    captured.seal()
    return attempt.finish(outcome="completed", error_class=None, duration_s=0.01, usage=Usage(13, 17))


def test_lazy_actual_owner_stage_consumer_all_io_on_single_worker(setup):
    make, supervisor = setup
    host, notices = make()
    assert not MockRecorder.instances and supervisor._worker is None
    scope = host.scope(pass_name="breaker")
    attempt = admit(scope, request())
    assert attempt is not None and complete(attempt)
    assert type(host._owner) is invocation_owner.InvocationOwner
    recorder = MockRecorder.instances[0]
    assert recorder.version == 2 and recorder.manifest is not None
    assert list(recorder.stages.values()) == [bytearray(b"private reasoning_content")]
    record = host._owner._leases[id(attempt._lease)]
    assert len(record.refs) == 1 and not record.usage_known
    summary = host.finalize()
    assert summary.audit_complete and summary.adopted_responses == summary.settled_sends == 1
    assert not summary.usage_known and not notices
    assert {name for _, name in MockRecorder.calls} == {"forge-audit-io"}
    assert not host._commands and host._control_bytes == supervisor.retained_bytes == 0
    assert "private" not in repr(summary) and "model" not in repr(summary)


def test_unknown_source_never_publishes_known_empty_manifest(setup):
    make, _ = setup
    host, _ = make(known=False)
    attempt = admit(host.scope(), request())
    assert complete(attempt)
    assert not host.safe_summary().source_known and MockRecorder.instances[0].manifest is None
    assert "input" not in [name for name, _ in MockRecorder.calls]
    host.finalize()


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("phase", ["open", "input", "start"])
def test_control_failure_latches_once_default_continues_strict_typed(setup, strict, phase):
    make, _ = setup
    host, notices = make(strict=strict)
    MockRecorder.fail = phase
    scope = host.scope()
    if strict:
        with pytest.raises(AuditPersistenceError) as caught:
            admit(scope, request())
        assert not caught.value.retryable
    else:
        assert admit(scope, request()) is None
    assert host.safe_summary().state == "incomplete" and notices == [module._WARNING]
    calls = list(MockRecorder.calls)
    if strict:
        with pytest.raises(AuditPersistenceError):
            admit(scope, request())
    else:
        assert admit(scope, request()) is None
    assert MockRecorder.calls == calls and notices == [module._WARNING]


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("strict", [False, True])
def test_capture_notice_refinement_reaches_host_consumer(setup, scoped, strict):
    make, supervisor = setup
    host, notices = make(scoped_fault_domain=scoped, strict=strict)
    host._ensure_io()
    writes = []
    captured = host._captures.new_capture(
        write=lambda data: writes.append(data) or len(data),
        warn=host.warn,
        limit_bytes=100,
        action_deadline=time.monotonic() - 1,
        chunk_bytes=128,
        strict=strict,
    )
    if strict:
        with pytest.raises(CaptureUnavailable):
            captured.append(b"private")
    else:
        captured.append(b"private")
    if strict:
        with pytest.raises(CaptureUnavailable):
            captured.seal()
    else:
        captured.seal()
    expected = [module._WARNING, "capture_not_executed"] if scoped else [module._WARNING]
    assert notices == expected and writes == [] and supervisor._blocked is False
    assert host.failed and not host.safe_summary().audit_complete


def test_queued_refinements_deliver_in_order_and_keep_the_first_error():
    delivery = _CaptureNoticeDelivery(scoped=True)
    delivered = []
    fatal = ValueError("first callback failure")

    def warn(code):
        delivered.append(code)
        if code == "capture_write_failed":
            delivery.emit("capture_not_executed", warn=warn)
            delivery.emit("capture_write_uncertain", warn=warn)
            raise fatal
        if code == "capture_write_uncertain":
            raise KeyError("later callback failure")

    caught = None
    try:
        delivery.emit("capture_write_failed", warn=warn)
    except BaseException as error:  # noqa: BLE001 - assert identity outside exception handling
        caught = error
    assert delivered == [
        "capture_write_failed",
        "capture_not_executed",
        "capture_write_uncertain",
    ]
    assert caught is fatal
    assert not delivery._draining and not delivery._pending


@pytest.mark.parametrize("scoped", [False, True])
def test_host_notice_codes_are_bounded_and_process_uncertainty_is_terminal(setup, scoped):
    host, notices = setup[0](scoped_fault_domain=scoped)
    codes = (
        "unrecognized private warning",
        "capture_not_executed",
        "capture_write_uncertain",
        "audit_io_unresolved_process",
    )
    for code in codes:
        host.warn(code)
        host.warn(code)
    expected = [module._WARNING] + list(codes[1:]) if scoped else [module._WARNING]
    assert notices == expected
    other, seen = setup[0](scoped_fault_domain=scoped)
    other.warn("audit_io_unresolved_process")
    other.warn("capture_not_executed")
    other.warn("capture_write_uncertain")
    assert seen == (["audit_io_unresolved_process"] if scoped else [module._WARNING])


class _HostileStr(str):
    """A string that raises if anything treats it as a member of a set."""

    def __hash__(self):
        raise RuntimeError("hostile __hash__")

    def __eq__(self, other):
        raise RuntimeError("hostile __eq__")


def test_only_exact_strings_can_reach_the_closed_set(setup, capsys):
    hostile = _HostileStr("capture_not_executed")
    host, notices = setup[0](scoped_fault_domain=True)
    host.warn(hostile)
    host.warn(0)
    assert notices == [module._WARNING]
    module._warning(hostile)
    assert capsys.readouterr().err == (
        "code-forge: invocation audit unavailable; recording is incomplete\n"
    )


def test_a_refinement_delivered_first_suppresses_a_later_unrecognized_code(setup):
    host, notices = setup[0](scoped_fault_domain=True)
    host.warn("capture_not_executed")
    host.warn("private unrecognized diagnostic")
    assert notices == ["capture_not_executed"]


def test_default_warning_renders_only_fixed_capture_codes(capsys):
    for code in (
        "capture_not_executed",
        "capture_write_uncertain",
        "audit_io_unresolved_process",
    ):
        module._warning(code)
        assert capsys.readouterr().err == (
            f"code-forge: invocation audit unavailable; recording is incomplete [{code}]\n"
        )
    module._warning("private diagnostic payload")
    assert capsys.readouterr().err == (
        "code-forge: invocation audit unavailable; recording is incomplete\n"
    )


@pytest.mark.parametrize("boundary", ["host", "capture"])
def test_terminal_notice_waits_for_inflight_delivery_without_blocking_producer(setup, boundary):
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    notices, errors = [], []

    def warn(code):
        if code == "capture_not_executed":
            entered.set()
            assert release.wait(3)
        notices.append(code)

    host, _ = setup[0](scoped_fault_domain=True, warn=warn)
    if boundary == "host":
        emit = host.warn
    else:
        host._ensure_io()
        captured = host._captures.new_capture(
            write=len,
            warn=warn,
            limit_bytes=100,
            action_deadline=time.monotonic() + 5,
            chunk_bytes=128,
        )
        emit = captured._notice

    def send(code, done=None):
        try:
            emit(code)
        except BaseException as error:  # noqa: BLE001 - assert thread failures below
            errors.append(error)
        finally:
            if done is not None:
                done.set()

    lower = threading.Thread(target=send, args=("capture_not_executed",))
    terminal = threading.Thread(target=send, args=("audit_io_unresolved_process", returned))
    try:
        lower.start()
        assert entered.wait(3)
        terminal.start()
        assert returned.wait(3), "terminal producer waited for the user callback"
        assert notices == [], "terminal callback overtook an active lower callback"
    finally:
        release.set()
        lower.join(3)
        if terminal.ident is not None:
            terminal.join(3)
    assert not lower.is_alive() and not terminal.is_alive() and not errors
    assert notices == ["capture_not_executed", "audit_io_unresolved_process"]
    emit("capture_write_uncertain")
    assert notices == ["capture_not_executed", "audit_io_unresolved_process"]


def test_real_process_block_cannot_overtake_capture_notice_delivery(setup):
    entered, release = threading.Event(), threading.Event()
    notices, errors = [], []

    def warn(code):
        if code == "capture_not_executed":
            entered.set()
            assert release.wait(3)
        notices.append(code)

    host, _ = setup[0](scoped_fault_domain=True, warn=warn)
    process = setup[1]
    host._ensure_io()
    cap = host._captures.new_capture(
        write=len,
        warn=host.warn,
        limit_bytes=100,
        action_deadline=time.monotonic() - 1,
        chunk_bytes=128,
    )
    cap.append(b"private")
    assert notices == [module._WARNING]
    io_entered, io_release = threading.Event(), threading.Event()

    class Blocker:
        def audit_io(self, phase, payload):
            io_entered.set()
            assert io_release.wait(3)
            return True

    def seal():
        try:
            cap.seal()
        except BaseException as error:  # noqa: BLE001 - assert thread failures below
            errors.append(error)

    reader = threading.Thread(target=seal)
    try:
        reader.start()
        assert entered.wait(3)
        ticket = process.submit(
            Blocker(), invocation_io.AuditPhase.MAINTAIN, deadline=time.monotonic() + 0.1
        )
        assert io_entered.wait(3)
        assert process.wait(ticket).publication_uncertain and process._blocked
        host._captures.new_capture(
            write=len,
            warn=host.warn,
            limit_bytes=100,
            action_deadline=time.monotonic() + 1,
            chunk_bytes=128,
        )
        assert notices == [module._WARNING], "process diagnostic overtook active delivery"
    finally:
        release.set()
        io_release.set()
        reader.join(3)
    assert not reader.is_alive() and not errors
    assert notices == [module._WARNING, "capture_not_executed", "audit_io_unresolved_process"]


@pytest.mark.parametrize("boundary", ["host", "capture"])
@pytest.mark.parametrize(
    "fatal_type,metadata_field",
    [
        (None, None),
        (MemoryError, None),
        (KeyboardInterrupt, None),
        (MemoryError, "__cause__"),
        (MemoryError, "__context__"),
        (MemoryError, "__suppress_context__"),
    ],
)
def test_reentrant_terminal_delivery_drains_before_original_callback_error(
    setup, boundary, fatal_type, metadata_field
):
    notices, unlocked = [], []
    if metadata_field is not None:

        class CallbackFatal(MemoryError):
            def __getattribute__(self, name):
                if name == metadata_field:
                    raise RuntimeError("metadata accessor must not replace fatal")
                return super().__getattribute__(name)

        fatal = CallbackFatal("private warning failure")
    else:
        fatal = fatal_type("private warning failure") if fatal_type else None
    cause, context = ValueError("original cause"), LookupError("original context")
    if fatal is not None:
        BaseException.__dict__["__cause__"].__set__(fatal, cause)
        BaseException.__dict__["__context__"].__set__(fatal, context)
        BaseException.__dict__["__suppress_context__"].__set__(fatal, False)
    host, _ = setup[0](scoped_fault_domain=True)

    def warn(code):
        def check_lock():
            lock = host._lock if boundary == "host" else captured._session._condition
            available = lock.acquire(blocking=False)
            unlocked.append(available)
            if available:
                lock.release()

        checker = threading.Thread(target=check_lock)
        checker.start()
        checker.join(2)
        assert not checker.is_alive() and unlocked[-1]
        notices.append(code)
        if code == "capture_not_executed":
            emit("capture_write_uncertain")
            emit("audit_io_unresolved_process")
            emit("capture_not_executed")
            if fatal is not None:
                raise fatal

    if boundary == "host":
        host._warn = warn
        emit = host.warn
        delivery = host._notices
    else:
        host._ensure_io()
        captured = host._captures.new_capture(
            write=len,
            warn=warn,
            limit_bytes=100,
            action_deadline=time.monotonic() + 5,
            chunk_bytes=128,
        )
        emit = captured._notice
        delivery = captured._notices
    if fatal is None:
        emit("capture_not_executed")
    else:
        caught = None
        try:
            emit("capture_not_executed")
        except BaseException as error:  # noqa: BLE001 - assert exact identity outside exception handling
            caught = error
        assert caught is fatal, "callback fatal identity was replaced"
        assert (
            BaseException.__dict__["__cause__"].__get__(fatal),
            BaseException.__dict__["__context__"].__get__(fatal),
            BaseException.__dict__["__suppress_context__"].__get__(fatal),
        ) == (cause, context, False)
    assert notices == ["capture_not_executed", "audit_io_unresolved_process"]
    assert not delivery._draining and not delivery._pending
    emit("capture_write_uncertain")
    assert notices == ["capture_not_executed", "audit_io_unresolved_process"]


def test_hung_open_remains_single_worker_and_control_owned_after_timeout(setup):
    make, supervisor = setup
    MockRecorder.block = "open"
    host, _ = make()
    assert admit(host.scope(), request()) is None
    assert MockRecorder.entered.is_set()
    worker = supervisor._worker
    assert len(host._commands) == 1 and host._control_bytes == 4096
    for _ in range(20):
        other, _ = make()
        assert admit(other.scope(), request()) is None
    assert supervisor._worker is worker and supervisor.unavailable
    assert host.safe_summary().publication_uncertain
    MockRecorder.release.set()
    with supervisor._condition:
        assert supervisor._condition.wait_for(lambda: supervisor._running is None, timeout=1)
    assert not host._commands and host._control_bytes == 0
    assert host.safe_summary().state == "incomplete" and not host.safe_summary().audit_complete


def test_redirect_parent_identity_and_no_new_owner(setup):
    make, _ = setup
    host, _ = make()
    scope = host.scope()
    first = admit(scope, request())
    assert complete(first, b"redirect body")
    second = admit(scope, request(parent=first, cause="redirect"))
    assert complete(second, b"final")
    assert len(MockRecorder.instances) == 1 and first._operation is second._operation
    starts = MockRecorder.instances[0].started
    assert starts[1]["parent_id"] == first._lease.attempt_id and starts[1]["cause"] == "redirect"
    with pytest.raises(ValueError, match="foreign"):
        admit(host.scope(), request(parent=second, cause="retry"))


def test_late_adopt_never_creates_refs_usage_or_safe_summary_credit(setup):
    make, supervisor = setup
    host, _ = make()
    attempt = admit(host.scope(), request())
    captured = attempt.new_response(status=200, redirect_hop=None, deadline=attempt._deadline)
    list(captured.chunks(io.BytesIO(b"private")))
    captured.seal()
    MockRecorder.block = "adopt"
    assert (
        attempt.finish(outcome="completed", error_class=None, duration_s=0.01, usage=Usage(1, 2))
        is False
    )
    assert MockRecorder.entered.is_set()
    record = host._owner._leases[id(attempt._lease)]
    assert not record.refs and not record.usage_known
    before = host.safe_summary()
    assert before.adopted_responses == before.accepted_bytes == 0
    MockRecorder.release.set()
    with supervisor._condition:
        assert supervisor._condition.wait_for(lambda: supervisor._running is None, timeout=1)
    assert not record.refs and host.safe_summary() == before


def test_primary_identity_and_chain_survive_secondary_finalize_and_warning(setup):
    make, _ = setup

    def broken_warning(_):
        raise MemoryError("PRIVATE warning")

    host, _ = make(strict=True, warn=broken_warning)
    attempt = admit(host.scope(), request())
    assert complete(attempt)
    primary = ValueError("PRIMARY")
    primary.__cause__ = LookupError("cause")
    cause, context = primary.__cause__, primary.__context__
    MockRecorder.fail = "finalize"
    host.finalize(primary=primary)
    assert primary.__cause__ is cause and primary.__context__ is context
    assert host.safe_summary().state == "incomplete"


def test_failed_warning_observes_latch_before_callback(setup):
    make, _ = setup
    seen = []

    def warning(code):
        seen.append((code, host.safe_summary().state))
        raise OSError("PRIVATE")

    host, _ = make(warn=warning)
    MockRecorder.fail = "open"
    assert admit(host.scope(), request()) is None
    assert seen == [(module._WARNING, "incomplete")]


def test_metadata_bounds_and_unknown_source_do_not_manufacture_identity(setup):
    make, supervisor = setup
    host, _ = make()
    with pytest.raises(ValueError):
        host.scope(pass_name="x" * 5000)
    with pytest.raises(ValueError):
        admit(host.scope(), replace(request(), model="x" * 10000))
    context = replace(host._context, source_hash=None, snapshot_id=None)
    with pytest.raises(ValueError, match="unknown source"):
        HostAuditSession(artifact_root=Path("unused"), context=context, manifest={})
    assert supervisor._worker is None


def test_fork_refuses_before_touching_inherited_locks(setup, monkeypatch):
    make, _ = setup
    host, _ = make()
    scope = host.scope()
    with monkeypatch.context() as patch:
        patch.setattr(module.os, "getpid", lambda: host._pid + 1)
        for action in (host.safe_summary, host.close, lambda: admit(scope, request())):
            with pytest.raises(RuntimeError, match="inherited"):
                action()


@pytest.mark.parametrize(
    "phase", ["open", "input", "start", "begin", "append", "seal", "adopt", "finish", "finalize"]
)
def test_fatal_storage_error_preserves_original_identity_in_all_phases(setup, phase):
    make, _ = setup
    # This test isolates fatal-error identity, not deadline expiry. The fixture's
    # 80 ms fault-test budget can expire during ordinary worker scheduling before
    # the target fault is armed. Use the production control/flush budgets here;
    # deadline behavior remains covered by the dedicated timeout tests.
    defaults = HostAuditLimits()
    host, _ = make(
        control_timeout_s=defaults.control_timeout_s,
        flush_timeout_s=defaults.flush_timeout_s,
    )
    original = MemoryError("PRIVATE fatal")
    original.__cause__ = ValueError("PRIVATE cause")
    cause = original.__cause__
    if phase in ("open", "input", "start"):
        MockRecorder.fail, MockRecorder.error = phase, original
        with pytest.raises(MemoryError) as caught:
            admit(host.scope(), request())
    else:
        attempt = admit(host.scope(), request())
        if phase == "begin":
            MockRecorder.fail, MockRecorder.error = phase, original
            with pytest.raises(MemoryError) as caught:
                attempt.new_response(status=200, redirect_hop=None, deadline=attempt._deadline)
        else:
            captured = attempt.new_response(status=200, redirect_hop=None, deadline=attempt._deadline)
            if phase == "append":
                MockRecorder.fail, MockRecorder.error = phase, original
                list(captured.chunks(io.BytesIO(b"body")))
                with pytest.raises(MemoryError) as caught:
                    captured.seal()
            else:
                list(captured.chunks(io.BytesIO(b"body")))
                captured.seal()
                if phase == "finalize":
                    assert attempt.finish(
                        outcome="completed", error_class=None, duration_s=0.1, usage=None
                    )
                    MockRecorder.fail, MockRecorder.error = phase, original
                    with pytest.raises(MemoryError) as caught:
                        host.finalize()
                else:
                    MockRecorder.fail, MockRecorder.error = phase, original
                    with pytest.raises(MemoryError) as caught:
                        attempt.finish(outcome="completed", error_class=None, duration_s=0.1, usage=None)
    assert caught.value is original and original.__cause__ is cause
    assert not host.safe_summary().audit_complete


def test_trusted_observe_consumes_stage_source_and_same_worker_not_aggregate_usage(setup):
    make, _ = setup
    host, _ = make()
    attempt = admit(host.scope(), request())
    captured = attempt.new_response(status=200, redirect_hop=None, deadline=attempt._deadline)
    list(captured.chunks(io.BytesIO(b"body")))
    captured.seal()
    with pytest.raises(TypeError, match="trusted"):
        attempt.observe_response(frame_offset=0, frame_length=4, usage=Usage(1, 2))
    usage = audit.UsageObservation(
        1, 2, None, None, "partial", "unknown", "unknown", "final", "native", "attempt", {}
    )
    assert attempt.observe_response(frame_offset=0, frame_length=4, usage=usage)
    record = host._owner._leases[id(attempt._lease)]
    assert record.usage_known and len(record.refs) == 1
    assert ("observe", "forge-audit-io") in MockRecorder.calls
    assert attempt.finish(outcome="completed", error_class=None, duration_s=0.1, usage=None)
    assert host.safe_summary().adopted_responses == 1


def test_maintenance_fixed_roots_same_worker_once_and_primary_precedence(setup, monkeypatch):
    make, _ = setup
    host, _ = make()
    attempt = admit(host.scope(), request())
    assert complete(attempt)
    calls = []

    def maintenance(root, *, monotonic, utc_now, action_deadline):
        calls.append((root, threading.current_thread().name, action_deadline))
        return audit.AuditAcknowledgement(
            False, audit.AuditFault("persistence_error", "maintenance", None)
        )

    monkeypatch.setattr(module, "maintain_audit_root", maintenance)
    primary = ValueError("unchanged")
    summary = host.finalize(primary=primary, maintain=True)
    assert len(calls) == 1 and calls[0][:2] == (Path("/mock-not-native"), "forge-audit-io")
    assert summary.state == "incomplete" and not summary.audit_complete
    assert host.finalize(primary=primary, maintain=True) == summary and len(calls) == 1


def test_control_slot_bound_and_hung_input_preserve_manifest_charge(setup):
    make, supervisor = setup
    host, _ = make(control_slots=1)
    MockRecorder.block = "input"
    assert admit(host.scope(), request()) is None
    assert MockRecorder.entered.is_set() and len(host._commands) == 1
    command = next(iter(host._commands))
    assert command.command == "input" and command.arguments == host._manifest
    assert host._control_bytes == command.charge and command.charge >= 2 * len(command.arguments)
    with pytest.raises(module._Unavailable):
        host._request(invocation_io.AuditPhase.MAINTAIN, "maintain", None, time.monotonic() + 1)
    assert len(host._commands) == 1
    MockRecorder.release.set()
    with supervisor._condition:
        assert supervisor._condition.wait_for(lambda: supervisor._running is None, timeout=1)
    assert not host._commands and host._control_bytes == 0 and command.arguments is None


def test_scope_and_total_send_budgets_are_admitted_before_start(setup):
    make, _ = setup
    host, notices = make(max_sends=2)
    scope = host.scope(max_provider_sends=1, max_redirect_hops=0)
    attempt = admit(scope, request())
    assert complete(attempt)
    assert admit(scope, request(parent=attempt, cause="retry")) is None
    assert len(MockRecorder.instances[0].started) == 1
    assert notices == [module._WARNING]


def test_lazy_default_singleton_does_not_retain_first_host_warning_callback(monkeypatch):
    import gc
    import weakref

    monkeypatch.setattr(invocation_io, "_PROCESS", None)
    ctx = audit.InvocationContext(str(uuid.uuid4()), None, None, None, None, None, None, "review", None)
    host = HostAuditSession(artifact_root=Path("unused"), context=ctx)
    reference = weakref.ref(host)
    host._ensure_io()
    process = host._io
    assert process._warn is module._process_warning and process._worker is None
    host.close()
    del host
    gc.collect()
    assert reference() is None
    process.close()


def test_completed_fatal_control_metadata_charged_until_actual_handoff(setup, monkeypatch):
    make, supervisor = setup
    host, _ = make()
    MockRecorder.fail, MockRecorder.error = "input", MemoryError("private")
    entered, release = threading.Event(), threading.Event()
    original_wait = supervisor.wait
    results = []

    def wait(ticket):
        if ticket._phase is invocation_io.AuditPhase.INPUT:
            with supervisor._condition:
                assert supervisor._condition.wait_for(lambda: ticket._outcome is not None, timeout=1)
            entered.set()
            assert release.wait(2)
        return original_wait(ticket)

    monkeypatch.setattr(supervisor, "wait", wait)

    def call():
        try:
            admit(host.scope(), request())
        except BaseException as error:  # noqa: BLE001 - assert same fatal across thread handoff
            results.append(error)

    thread = threading.Thread(target=call)
    thread.start()
    try:
        assert entered.wait(1)
        command = next(iter(host._commands))
        assert command.done and not command.released and command.command == "input"
        assert command.arguments == host._manifest
        assert host._control_bytes == command.charge and supervisor._errors
    finally:
        release.set()
        thread.join(2)
    assert results == [MockRecorder.error]
    assert not host._commands and host._control_bytes == 0 and not supervisor._errors


def test_finalization_does_not_extend_unfinished_capture_drain_budget(setup, monkeypatch):
    make, _ = setup
    host, _ = make()
    attempt = admit(host.scope(), request())
    captured = attempt.new_response(status=200, redirect_hop=None, deadline=attempt._deadline)
    list(captured.chunks(io.BytesIO(b"private")))

    # Outer cleanup must abandon, not call a new controller drain/adopt sequence.
    drain_calls = []

    def forbidden():
        drain_calls.append(True)
        raise AssertionError("outer cleanup started a fresh drain budget")

    monkeypatch.setattr(attempt._stage, "seal", forbidden)
    summary = host.finalize()
    assert not summary.audit_complete and attempt._stage._cancelled
    assert not drain_calls
    assert not host._owner._leases[id(attempt._lease)].refs


def test_no_recursive_controller_wait_on_single_worker(setup):
    make, supervisor = setup
    host, _ = make()

    class Target:
        def audit_io(self, phase, payload):
            return host._request(invocation_io.AuditPhase.OPEN, "open", None, time.monotonic() + 1)

    ticket = supervisor.submit(Target(), invocation_io.AuditPhase.OPEN, deadline=time.monotonic() + 1)
    assert supervisor.wait(ticket).state == "failed"
    assert not supervisor.unavailable and not MockRecorder.instances and not host._commands


@pytest.mark.parametrize("scenario_name", ["initial", "retry", "correction", "continuation", "headroom"])
def test_real_send_protocol_to_real_host_owner_stage_with_mock_transport_storage(
    setup, monkeypatch, scenario_name
):
    from code_forge import llm_invoke as invoke
    from code_forge.backend import BackendConfig
    from tests.test_invocation_send_audit import scenario, scripted

    monkeypatch.setenv("HOST_TEST_KEY", "mock-secret-not-a-provider")
    monkeypatch.setattr(invoke, "_CONTINUE_DELAY_S", 0)
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
    responses, causes = scenario(scenario_name)
    transport = scripted(monkeypatch, responses)
    make, _ = setup
    host, _ = make()
    result = invoke._invoke_api("private prompt", backend, 3, initial_delay_s=0, host_audit=host.scope())
    assert result.content["findings"] == []
    recorder = MockRecorder.instances[0]
    assert [row["cause"] for row in recorder.started] == causes
    assert len(transport.requests) == len(recorder.stages) == len(recorder.finished) == len(causes)
    summary = host.finalize()
    assert summary.adopted_responses == len(causes) and not summary.usage_known
    assert summary.audit_complete and "private" not in repr(summary)


@pytest.mark.parametrize("strict", [False, True])
def test_real_stream_with_real_adapter_keeps_private_reasoning_and_fails_by_policy(
    setup, monkeypatch, strict
):
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
        stream=True,
        max_tokens=64,
        output_token_limit=128,
    )
    response = reply(stream=True)
    transport = scripted(monkeypatch, [response])
    make, _ = setup
    host, notices = make(strict=strict)
    MockRecorder.fail = "finish"
    if strict:
        with pytest.raises(AuditPersistenceError):
            invoke._invoke_api("private prompt", backend, 3, host_audit=host.scope())
        assert host.strict_failed
    else:
        assert (
            invoke._invoke_api("private prompt", backend, 3, host_audit=host.scope()).content["findings"]
            == []
        )
    assert len(transport.requests) == 1
    assert b"private-thinking" in bytes(next(iter(MockRecorder.instances[0].stages.values())))
    assert notices == [module._WARNING]
    assert "private" not in repr(host.safe_summary())


def test_real_http_primary_with_strict_secondary_does_not_mutate_retryable_or_send_again(
    setup, monkeypatch
):
    from code_forge import llm_invoke as invoke
    from code_forge.backend import BackendConfig
    from tests.test_invocation_send_audit import http_error, reply, scripted

    monkeypatch.setenv("HOST_TEST_KEY", "mock-secret")
    backend = BackendConfig(
        name="fixture",
        type="api",
        format="openai",
        model="unit",
        base_url="https://provider.invalid/v1",
        api_key_env="HOST_TEST_KEY",
    )
    transport = scripted(monkeypatch, [http_error(), reply()])
    make, _ = setup
    host, _ = make(strict=True)
    MockRecorder.fail = "finish"
    with pytest.raises(invoke.LLMInvokeError) as caught:
        invoke._invoke_api("prompt", backend, 3, initial_delay_s=0, host_audit=host.scope())
    assert not isinstance(caught.value, AuditPersistenceError)
    assert caught.value.retryable is True and len(transport.requests) == 1 and host.strict_failed


def test_completed_send_without_response_capture_cannot_manufacture_complete_audit(setup):
    make, _ = setup
    host, _ = make()
    attempt = admit(host.scope(), request())
    assert not attempt.finish(outcome="completed", error_class=None, duration_s=0.01, usage=None)
    assert host.failed and host.safe_summary().adopted_responses == 0
    assert not host.safe_summary().audit_complete


def test_admission_is_not_entered_and_expired_no_network_settlement_keeps_not_dispatched(
    setup, monkeypatch
):
    make, _ = setup
    host, _ = make()
    deadline = time.monotonic() + 1
    attempt = host.scope().start_send(request(deadline=deadline))
    record = host._owner._leases[id(attempt._lease)]
    assert record.dispatch == "not_dispatched"
    with monkeypatch.context() as patch:
        patch.setattr(module.time, "monotonic", lambda: deadline + 1)
        assert attempt.finish(
            outcome="failed", error_class="SendDeadlineBeforeNetwork", duration_s=1.0, usage=None
        )
    assert record.dispatch == "not_dispatched" and record.settled
    assert MockRecorder.instances[0].finished[0]["error_class"] == "SendDeadlineBeforeNetwork"
    assert not record.refs and not MockRecorder.instances[0].stages


def test_entered_is_monotonic_local_boundary_not_physical_send_claim(setup):
    make, _ = setup
    host, _ = make()
    attempt = host.scope().start_send(request())
    assert attempt.enter_send() and attempt.enter_send()
    assert attempt.finish(
        outcome="failed", error_class="SendDeadlineBeforeNetwork", duration_s=0.01, usage=None
    )
    record = host._owner._leases[id(attempt._lease)]
    assert record.dispatch == "entered_api_send" and not record.refs
    assert MockRecorder.instances[0].finished[0]["error_class"] == "SendDeadlineBeforeNetwork"


def test_lazy_source_binding_before_branch_send_is_explicit_and_immutable(setup):
    make, supervisor = setup
    host, _ = make(known=False)
    context = replace(host._context, source_hash="a" * 64, snapshot_id=str(uuid.uuid4()))
    manifest = dict(
        schema_version=1,
        run_id=context.run_id,
        snapshot_id=context.snapshot_id,
        mode="diff",
        legacy_source_hash=context.source_hash,
        exact_diff_sha256=hashlib.sha256(b"").hexdigest(),
        diff_bytes=0,
        files=[],
        repositories=[],
    )
    host.bind_source(context, manifest=manifest)
    assert supervisor._worker is None and not MockRecorder.instances and host.safe_summary().source_known
    attempt = admit(host.scope(), request())
    assert MockRecorder.instances[0].manifest["diff_bytes"] == 0
    with pytest.raises(ValueError, match="immutable"):
        host.bind_source(replace(context, source_hash=None, snapshot_id=None))
    assert complete(attempt)


@pytest.mark.parametrize("with_primary", [False, True])
def test_close_retains_first_fatal_but_cleans_two_real_stage_capture_slots(
    setup, monkeypatch, with_primary
):
    make, supervisor = setup
    host, _ = make()
    for _ in range(2):
        attempt = admit(host.scope(), request())
        attempt.new_response(status=200, redirect_hop=None, deadline=attempt._deadline)
    order = tuple(host._attempts)
    recorder = MockRecorder.instances[0]
    first, second = MemoryError("first cancel fatal"), KeyboardInterrupt("second cancel fatal")
    first.__cause__ = ValueError("cause")
    first.__context__ = LookupError("context")
    first.__suppress_context__ = True
    chain = first.__cause__, first.__context__, first.__suppress_context__
    calls = []

    def cancel(handle):
        calls.append(handle)
        raise first if handle is order[0]._stage._handle else second

    monkeypatch.setattr(recorder, "cancel_stage", cancel)
    primary = RuntimeError("primary") if with_primary else None
    primary_before = (
        (vars(primary).copy(), primary.args, primary.__cause__, primary.__context__) if primary else None
    )
    if primary is None:
        with pytest.raises(MemoryError) as caught:
            host.close()
        assert caught.value is first
    else:
        host.close(primary=primary)
        assert (vars(primary), primary.args, primary.__cause__, primary.__context__) == primary_before
    assert (first.__cause__, first.__context__, first.__suppress_context__) == chain
    assert len(calls) == 2
    assert not supervisor._captures and not host._captures._captures
    assert host._closed and host.failed
    host.close(primary=primary)
    assert len(calls) == 2 and not supervisor._captures


def test_start_ack_after_close_cannot_register_return_attempt_or_change_summary(setup):
    make, _ = setup
    host, _ = make()
    MockRecorder.block = "start"
    returned = []

    def start_until_revoked():
        try:
            returned.append(host.scope().start_send(request()))
        except SendLifecycleClosed as error:
            returned.append(error)

    thread = threading.Thread(target=start_until_revoked)
    thread.start()
    try:
        assert MockRecorder.entered.wait(1)
        host.close()
        before = host.safe_summary()
        MockRecorder.release.set()
        thread.join(1)
        assert not thread.is_alive()
        assert len(returned) == 1 and isinstance(returned[0], SendLifecycleClosed)
        assert not host._attempts
        assert host.safe_summary() == before
    finally:
        MockRecorder.release.set()
        thread.join(2)


def test_fatal_close_does_not_wait_for_hung_other_writer_or_release_its_payload_early(
    setup, monkeypatch
):
    make, supervisor = setup
    host, _ = make()
    for _ in range(2):
        attempt = admit(host.scope(), request())
        attempt.new_response(status=200, redirect_hop=None, deadline=attempt._deadline)
    first_attempt, running = tuple(host._attempts)
    MockRecorder.block = "append"
    running._stage.capture.append(b"x" * 512)
    assert MockRecorder.entered.wait(1)
    original_cancel = MockRecorder.instances[0].cancel_stage
    fatal = MemoryError("cancel fatal")
    calls = []

    def cancel(handle):
        calls.append(handle)
        if handle is first_attempt._stage._handle:
            raise fatal
        return original_cancel(handle)

    monkeypatch.setattr(MockRecorder.instances[0], "cancel_stage", cancel)
    started = time.monotonic()
    try:
        with pytest.raises(MemoryError) as caught:
            host.close()
        assert caught.value is fatal and time.monotonic() - started < 0.2
        assert len(calls) == 2 and supervisor.retained_bytes == 512
        assert len(supervisor._captures) == 1
    finally:
        MockRecorder.release.set()
    with supervisor._condition:
        assert supervisor._condition.wait_for(lambda: supervisor._running is None, timeout=1)
    assert supervisor.retained_bytes == 0 and not supervisor._captures


@pytest.mark.parametrize(
    "primary_type", [RuntimeError, KeyboardInterrupt, SystemExit, MemoryError, CancelledError]
)
@pytest.mark.parametrize("cleanup_type", [OSError, KeyboardInterrupt])
def test_wait_primary_survives_abandon_failure(setup, monkeypatch, primary_type, cleanup_type):
    make, supervisor = setup
    host, _ = make()
    primary = primary_type("private wait failure")
    secondary = cleanup_type("private abandon failure")
    primary.__cause__ = LookupError("original cause")
    primary.__context__ = ValueError("original context")
    before = (primary.__cause__, primary.__context__, primary.__suppress_context__)
    ticket = SimpleNamespace(_outcome=invocation_io.IoOutcome("expired", not_executed=True))
    monkeypatch.setattr(supervisor, "submit", lambda *_args, **_kwargs: ticket)

    def wait(_ticket):
        raise primary

    def abandon(_ticket):
        raise secondary

    monkeypatch.setattr(supervisor, "wait", wait)
    monkeypatch.setattr(supervisor, "abandon", abandon)
    with pytest.raises(BaseException) as caught:
        host._request(invocation_io.AuditPhase.OPEN, "open", {}, time.monotonic() + 1)
    assert caught.value is primary
    assert (primary.__cause__, primary.__context__, primary.__suppress_context__) == before
    assert primary._invocation_audit_cleanup_errors == (secondary,)
    assert not host._commands and host._control_bytes == 0


@pytest.mark.parametrize("state", ["unknown", "done", "not-executed"])
def test_wait_cleanup_failure_retains_unknown_work_until_real_exit(setup, monkeypatch, state):
    make, supervisor = setup
    host, _ = make()
    primary, secondary = CancelledError("wait"), OSError("cleanup")
    targets = []
    ticket = SimpleNamespace(
        _outcome=invocation_io.IoOutcome("expired", not_executed=True)
        if state == "not-executed"
        else None
    )

    def submit(target, *_args, **_kwargs):
        targets.append(target)
        target.done = state == "done"
        return ticket

    def wait(_ticket):
        raise primary

    def abandon(_ticket):
        raise secondary

    monkeypatch.setattr(supervisor, "submit", submit)
    monkeypatch.setattr(supervisor, "wait", wait)
    monkeypatch.setattr(supervisor, "abandon", abandon)
    with pytest.raises(CancelledError) as caught:
        host._request(invocation_io.AuditPhase.OPEN, "open", {}, time.monotonic() + 1)
    assert caught.value is primary
    assert targets[0].abandoned
    if state == "unknown":
        assert host._commands == {targets[0]} and host._control_bytes == 4096
        monkeypatch.setattr(host, "_execute", lambda *_: None)
        targets[0].audit_io(invocation_io.AuditPhase.OPEN, b"")
    assert not host._commands and host._control_bytes == 0


def test_wait_single_failure_and_success_controls(setup, monkeypatch):
    make, supervisor = setup
    host, _ = make()
    primary = RuntimeError("wait")
    ticket = SimpleNamespace(_outcome=invocation_io.IoOutcome("expired", not_executed=True))
    monkeypatch.setattr(supervisor, "submit", lambda *_args, **_kwargs: ticket)
    monkeypatch.setattr(supervisor, "abandon", lambda *_: None)

    def wait(_ticket):
        raise primary

    monkeypatch.setattr(supervisor, "wait", wait)
    with pytest.raises(RuntimeError) as caught:
        host._request(invocation_io.AuditPhase.OPEN, "open", {}, time.monotonic() + 1)
    assert caught.value is primary and not hasattr(primary, "_invocation_audit_cleanup_errors")
    monkeypatch.setattr(supervisor, "wait", lambda *_: invocation_io.IoOutcome("ack", value=7))
    assert host._request(invocation_io.AuditPhase.OPEN, "open", {}, time.monotonic() + 1) == 7
    assert not host._commands and host._control_bytes == 0


def test_public_start_preserves_wait_cancellation_and_blocks_later_send(setup, monkeypatch):
    make, supervisor = setup
    host, _ = make()
    primary, secondary = CancelledError("wait"), OSError("cleanup")
    ticket = SimpleNamespace(_outcome=invocation_io.IoOutcome("expired", not_executed=True))
    calls = []

    def submit(*_args, **_kwargs):
        calls.append(1)
        return ticket

    def wait(_ticket):
        raise primary

    def abandon(_ticket):
        raise secondary

    monkeypatch.setattr(supervisor, "submit", submit)
    monkeypatch.setattr(supervisor, "wait", wait)
    monkeypatch.setattr(supervisor, "abandon", abandon)
    scope = host.scope()
    with pytest.raises(CancelledError) as caught:
        scope.start_send(request())
    assert caught.value is primary
    assert host.failed and scope.start_send(request()) is None and len(calls) == 1
    assert not host._commands and host._control_bytes == 0


def test_wait_cleanup_evidence_bypasses_exception_hooks_and_keeps_previous(setup, monkeypatch):
    class Hostile(CancelledError):
        def __getattribute__(self, name):
            if name in ("__dict__", "__notes__", "_invocation_audit_cleanup_errors"):
                raise AssertionError("exception read hook invoked")
            return super().__getattribute__(name)

        def __setattr__(self, name, value):
            raise AssertionError("exception write hook invoked")

        def __str__(self):
            raise AssertionError("exception formatted")

        def add_note(self, _note):
            raise AssertionError("exception note hook invoked")

    make, supervisor = setup
    host, _ = make()
    primary, secondary, previous = Hostile(), OSError("private"), OSError("earlier")
    state = BaseException.__dict__["__dict__"].__get__(primary)
    state["_invocation_audit_cleanup_errors"] = (previous,)
    state["__notes__"] = ["old note"]
    ticket = SimpleNamespace(_outcome=invocation_io.IoOutcome("expired", not_executed=True))
    monkeypatch.setattr(supervisor, "submit", lambda *_args, **_kwargs: ticket)

    def wait(_ticket):
        raise primary

    def abandon(_ticket):
        raise secondary

    monkeypatch.setattr(supervisor, "wait", wait)
    monkeypatch.setattr(supervisor, "abandon", abandon)
    with pytest.raises(Hostile) as caught:
        host._request(invocation_io.AuditPhase.OPEN, "open", {}, time.monotonic() + 1)
    assert caught.value is primary
    assert state["_invocation_audit_cleanup_errors"] == (previous, secondary)
    assert state["__notes__"][0] == "old note" and len(state["__notes__"]) == 2
    assert "private" not in state["__notes__"][1]


def test_a_failing_abandon_still_releases_a_provably_unexecuted_command(setup, monkeypatch):
    """A raised abandon must not strand the charge of a ticket that never ran.

    abandon() only releases when the command finished or provably never ran. If
    the abandon call itself raises, the target is never told it was abandoned, so
    the charge would stay for the life of the session and later control calls
    would fail against a budget nothing is using.
    """
    make, supervisor = setup
    host, notices = make()
    expired = invocation_io.IoOutcome("expired", not_executed=True)

    class Stub:
        _outcome = expired

    def submit(target, phase, *, deadline, payload=b"", ready_at=0.0):
        return Stub()

    def wait(ticket):
        raise RuntimeError("wait failed")

    def abandon(ticket):
        raise RuntimeError("abandon failed")

    monkeypatch.setattr(supervisor, "submit", submit)
    monkeypatch.setattr(supervisor, "wait", wait)
    monkeypatch.setattr(supervisor, "abandon", abandon)

    with pytest.raises(RuntimeError):
        host._request(invocation_io.AuditPhase.OPEN, "open", {}, time.monotonic() + 0.2)

    assert not host._commands
    assert host._control_bytes == 0


def test_a_replayed_control_reaches_the_worker_with_the_re_armed_deadline(setup, monkeypatch):
    """The deadline must have one source: the dispatched control.

    A control used to carry a second copy of its deadline inside the command
    arguments, so a replay could re-arm the ticket while the worker still saw the
    expired copy and refused. The replayed control must carry the re-armed value.
    """
    make, supervisor = setup
    host, notices = make()
    host._managed_roots = True
    expired = invocation_io.IoOutcome("expired", not_executed=True)
    acked = invocation_io.IoOutcome("ack", value=True)
    seen = []

    def submit(target, phase, *, deadline, payload=b"", ready_at=0.0):
        seen.append((target.deadline, deadline))
        return object()

    def wait(ticket):
        return expired if len(seen) == 1 else acked

    monkeypatch.setattr(supervisor, "submit", submit)
    monkeypatch.setattr(supervisor, "wait", wait)

    host._request(invocation_io.AuditPhase.MAINTAIN, "prepare_managed", None, time.monotonic() + 0.5)

    assert len(seen) == 2
    assert seen[1][0] > seen[0][0], "the replayed control must carry a re-armed deadline"
    assert seen[1][0] == seen[1][1], "the worker must see the ticket's own deadline"


def test_a_replay_is_refused_once_the_session_has_closed(setup, monkeypatch):
    """A control that expired must not be replayed past the lifecycle boundary."""
    make, supervisor = setup
    host, notices = make()
    expired = invocation_io.IoOutcome("expired", not_executed=True)
    submitted = []

    class Stub:
        _outcome = expired

    def submit(target, phase, *, deadline, payload=b"", ready_at=0.0):
        submitted.append(phase)
        host.close()
        return Stub()

    monkeypatch.setattr(supervisor, "submit", submit)
    monkeypatch.setattr(supervisor, "wait", lambda ticket: ticket._outcome)

    with pytest.raises(module._Unavailable):
        host._request(invocation_io.AuditPhase.OPEN, "open", None, time.monotonic() + 0.2)

    assert submitted == [invocation_io.AuditPhase.OPEN]


def test_the_final_record_may_be_replayed_while_finalising(setup, monkeypatch):
    """The finalise request is the one control finalising must not block."""
    make, supervisor = setup
    host, notices = make()
    expired = invocation_io.IoOutcome("expired", not_executed=True)
    phases = []

    class Stub:
        _outcome = expired

    def submit(target, phase, *, deadline, payload=b"", ready_at=0.0):
        phases.append(phase)
        return Stub()

    monkeypatch.setattr(supervisor, "submit", submit)
    monkeypatch.setattr(supervisor, "wait", lambda ticket: ticket._outcome)

    host._finalize_started = True
    with pytest.raises(module._Unavailable):
        host._request(
            invocation_io.AuditPhase.FINALIZE, "finalize", (None, True), time.monotonic() + 0.2
        )

    assert phases == [invocation_io.AuditPhase.FINALIZE, invocation_io.AuditPhase.FINALIZE]


def test_a_replay_spends_at_most_one_more_window(setup, monkeypatch):
    """Advance past window one so the per-control clamp cannot hide extra grace."""
    make, supervisor = setup
    host, _ = make(control_timeout_s=2.0)
    clock = [100.0]
    deadlines = []
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def submit(target, phase, *, deadline, **kwargs):
        deadlines.append(deadline)
        return object()

    def wait(ticket):
        clock[0] = 103.0
        return invocation_io.IoOutcome("expired", not_executed=True)

    monkeypatch.setattr(supervisor, "submit", submit)
    monkeypatch.setattr(supervisor, "wait", wait)
    with pytest.raises(module._Unavailable):
        host._request(invocation_io.AuditPhase.OPEN, "open", None, 110.0)
    assert deadlines == [102.0, 104.0]


def test_a_replayed_control_runs_on_the_worker_with_a_live_deadline(setup, monkeypatch):
    """Release the real worker only after observing a provably unexecuted expiry."""
    make, supervisor = setup
    host, _ = make(control_timeout_s=0.5)
    executed = []
    release = threading.Event()
    held = threading.Event()

    class Blocker:
        def audit_io(self, phase, payload):
            held.set()
            assert release.wait(5.0)
            return True

    def execute(command, args, deadline):
        executed.append((deadline, time.monotonic()))
        return True

    monkeypatch.setattr(host, "_execute", execute)
    blocker = supervisor.submit(
        Blocker(), invocation_io.AuditPhase.MAINTAIN, deadline=time.monotonic() + 5.0
    )
    assert held.wait(2.0)
    original_wait = supervisor.wait
    outcomes = []

    def wait(ticket):
        outcome = original_wait(ticket)
        outcomes.append(outcome)
        if len(outcomes) == 1:
            assert outcome.not_executed and outcome.state == "expired"
            release.set()
        return outcome

    monkeypatch.setattr(supervisor, "wait", wait)
    try:
        host._request(invocation_io.AuditPhase.MAINTAIN, "prepare_managed", None, time.monotonic() + 3.0)
    finally:
        release.set()
        original_wait(blocker)
    assert len(outcomes) == 2 and outcomes[1].state == "ack"
    assert len(executed) == 1
    deadline, ran_at = executed[0]
    assert deadline > ran_at


def test_ensure_open_stays_inside_the_callers_deadline(setup, monkeypatch):
    """One window per control must never become budget the caller never granted."""
    make, supervisor = setup
    host, notices = make(control_timeout_s=0.08)
    submitted = []

    def request(phase, command, arguments, deadline, *, charge=4096):
        submitted.append((command, deadline))
        return True

    monkeypatch.setattr(host, "_request", request)

    caller_deadline = time.monotonic() + 0.05
    host._ensure_open(caller_deadline)

    assert submitted, "the open sequence must issue its controls"
    assert max(deadline for _, deadline in submitted) <= caller_deadline + 0.001


@pytest.mark.parametrize("phase", [invocation_io.AuditPhase.OPEN, invocation_io.AuditPhase.FINALIZE])
@pytest.mark.parametrize("late_ack", [False, True])
def test_replay_cannot_extend_explicit_caller_deadline(setup, monkeypatch, phase, late_ack):
    make, supervisor = setup
    host, _ = make(control_timeout_s=1.0)
    clock = [100.0]
    submitted = []
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def submit(target, phase, *, deadline, **kwargs):
        submitted.append(deadline)
        return object()

    def wait(ticket):
        if len(submitted) == 1:
            clock[0] = 100.05 if late_ack else 100.1
            return invocation_io.IoOutcome("expired", not_executed=True)
        clock[0] = 100.11
        return invocation_io.IoOutcome("ack", value=True)

    monkeypatch.setattr(supervisor, "submit", submit)
    monkeypatch.setattr(supervisor, "wait", wait)
    with pytest.raises(module._Unavailable):
        host._request(phase, "open", None, 100.1)
    assert all(deadline <= 100.1 for deadline in submitted)
    assert len(submitted) == (2 if late_ack else 1)
    assert not host._commands and host._control_bytes == 0


def test_open_replay_cannot_admit_after_caller_deadline(setup, monkeypatch):
    make, supervisor = setup
    host, _ = make(known=False, control_timeout_s=1.0)
    clock = [100.0]
    submitted = []
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def submit(target, phase, *, deadline, **kwargs):
        submitted.append(deadline)
        return object()

    def wait(ticket):
        clock[0] = 100.1 if len(submitted) == 1 else 100.11
        return (
            invocation_io.IoOutcome("expired", not_executed=True)
            if len(submitted) == 1
            else invocation_io.IoOutcome("ack", value=True)
        )

    monkeypatch.setattr(supervisor, "submit", submit)
    monkeypatch.setattr(supervisor, "wait", wait)
    with pytest.raises(module._Unavailable):
        host._ensure_open(100.1)
    assert not host._opened
    assert submitted == [100.1]


@pytest.mark.parametrize("boundary", ["close", "disable", "finalize"])
@pytest.mark.parametrize("phase", [invocation_io.AuditPhase.OPEN, invocation_io.AuditPhase.FINALIZE])
def test_retry_registration_observes_lifecycle_after_rearming(setup, monkeypatch, boundary, phase):
    """Cross the boundary after replay preparation, before its registration."""
    make, supervisor = setup
    host, _ = make()
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: 100.0))
    expired = invocation_io.IoOutcome("expired", not_executed=True)
    ack = invocation_io.IoOutcome("ack", value=True)
    submitted = []
    rearming = threading.Event()
    transitioned = threading.Event()
    real_deadline = host._deadline
    calls = 0
    transition_errors = []

    def deadline(value):
        nonlocal calls
        result = real_deadline(value)
        calls += 1
        if calls == 2:
            rearming.set()
            assert transitioned.wait(3)
        return result

    def transition():
        try:
            assert rearming.wait(3)
            if boundary == "close":
                host.close()
            elif boundary == "disable":
                host.warn("unavailable")
            else:
                with host._lock:
                    host._finalize_started = True
        except BaseException as error:  # noqa: BLE001 - assert thread failures in the caller
            transition_errors.append(error)
        finally:
            transitioned.set()

    def submit(target, phase, *, deadline):
        submitted.append((target, phase))
        return SimpleNamespace(_outcome=expired if len(submitted) == 1 else ack)

    monkeypatch.setattr(host, "_deadline", deadline)
    monkeypatch.setattr(supervisor, "submit", submit)
    monkeypatch.setattr(supervisor, "wait", lambda ticket: ticket._outcome)
    thread = threading.Thread(target=transition)
    thread.start()
    try:
        if boundary == "finalize" and phase is invocation_io.AuditPhase.FINALIZE:
            assert host._request(phase, "finalize", (None, True), 101.0) is True
            assert len(submitted) == 2
        else:
            with pytest.raises(module._Unavailable):
                host._request(phase, "open", None, 101.0)
            assert len(submitted) == 1
        assert host._commands == set()
        assert host._control_bytes == 0
    finally:
        rearming.set()
        thread.join(3)
        assert not thread.is_alive()
        assert transition_errors == []


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("outcome", ["completed", "failed"])
def test_finish_control_budget_starts_after_stage_adoption(setup, monkeypatch, strict, outcome):
    """A host-only clock models elapsed adoption without slowing the real worker."""
    make, supervisor = setup
    host, notices = make(strict=strict, control_timeout_s=1)
    attempt = admit(host.scope(), request(deadline=time.monotonic() + 10))
    captured = attempt.new_response(status=200, redirect_hop=None, deadline=attempt._deadline)
    assert b"".join(captured.chunks(io.BytesIO(b"body"))) == b"body"
    clock = [time.monotonic()]
    original_adopt, original_submit = attempt._adopt, supervisor.submit
    submitted = []

    def adopt():
        ack = original_adopt()
        clock[0] += 2  # Earlier stage operations consumed the old FINISH budget.
        return ack

    def submit(target, phase, **kwargs):
        if phase is invocation_io.AuditPhase.FINISH:
            submitted.append(kwargs["deadline"])
        return original_submit(target, phase, **kwargs)

    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(attempt, "_adopt", adopt)
    monkeypatch.setattr(supervisor, "submit", submit)
    original_action = attempt._deadline
    assert attempt.finish(outcome=outcome, error_class=None, duration_s=0.01, usage=None)
    assert submitted == [clock[0] + host.limits.control_timeout_s]
    assert len(MockRecorder.instances[0].finished) == 1
    assert host.safe_summary().settled_sends == host.safe_summary().adopted_responses == 1
    assert attempt._deadline == original_action
    assert not host.failed and not notices
    assert not host._commands and host._control_bytes == 0
    assert attempt.finish(outcome=outcome, error_class=None, duration_s=0.01, usage=None)
    assert len(submitted) == 1


@pytest.mark.parametrize("expired", [False, True])
def test_finish_fresh_control_window_still_obeys_job_deadline(setup, monkeypatch, expired):
    make, supervisor = setup
    host, _ = make(control_timeout_s=1)
    attempt = admit(host.scope(), request(deadline=time.monotonic() + 10))
    captured = attempt.new_response(status=200, redirect_hop=None, deadline=attempt._deadline)
    assert b"".join(captured.chunks(io.BytesIO(b"body"))) == b"body"
    original_adopt, original_submit = attempt._adopt, supervisor.submit
    submitted = []

    def adopt():
        ack = original_adopt()
        host._job_deadline = time.monotonic() + (-1 if expired else 0.5)
        return ack

    def submit(target, phase, **kwargs):
        if phase is invocation_io.AuditPhase.FINISH:
            submitted.append(kwargs["deadline"])
        return original_submit(target, phase, **kwargs)

    monkeypatch.setattr(attempt, "_adopt", adopt)
    monkeypatch.setattr(supervisor, "submit", submit)
    assert attempt.finish(outcome="completed", error_class=None, duration_s=0, usage=None) is not expired
    assert submitted == [host._job_deadline]
    assert host.failed is expired
    assert host.safe_summary().settled_sends == int(not expired)
    assert len(MockRecorder.instances[0].finished) == int(not expired)
    assert not host._commands and host._control_bytes == 0


def test_finish_fresh_control_window_does_not_retry_unknown_work(setup, monkeypatch):
    make, supervisor = setup
    host, _ = make(control_timeout_s=0.5)
    attempt = admit(host.scope(), request())
    monkeypatch.setattr(MockRecorder, "block", "finish")
    assert not attempt.finish(outcome="failed", error_class="OSError", duration_s=0, usage=None)
    assert MockRecorder.entered.is_set()
    assert host.failed and host.safe_summary().publication_uncertain
    assert not attempt.finish(outcome="failed", error_class="OSError", duration_s=0, usage=None)
    assert [name for name, _ in MockRecorder.calls].count("finish") == 1
    assert host._control_bytes > 0  # Unknown running work remains charged.
    MockRecorder.release.set()
    with supervisor._condition:
        assert supervisor._condition.wait_for(lambda: supervisor._running is None, timeout=1)
    assert not host._commands and host._control_bytes == 0
    assert host.safe_summary().settled_sends == 0


@pytest.mark.parametrize("fatal", [False, True])
def test_adoption_failure_never_starts_finish_control(setup, monkeypatch, fatal):
    make, supervisor = setup
    host, _ = make()
    attempt = admit(host.scope(), request())
    captured = attempt.new_response(status=200, redirect_hop=None, deadline=attempt._deadline)
    assert b"".join(captured.chunks(io.BytesIO(b"body"))) == b"body"
    error = KeyboardInterrupt("adoption interrupted") if fatal else OSError("adoption failed")
    monkeypatch.setattr(MockRecorder, "fail", "adopt")
    monkeypatch.setattr(MockRecorder, "error", error)
    if fatal:
        with pytest.raises(KeyboardInterrupt) as caught:
            attempt.finish(outcome="completed", error_class=None, duration_s=0, usage=None)
        assert caught.value is error
    else:
        assert not attempt.finish(outcome="completed", error_class=None, duration_s=0, usage=None)
    assert "finish" not in [name for name, _ in MockRecorder.calls]
    assert host.failed and host.safe_summary().settled_sends == 0
    assert not host._commands and host._control_bytes == 0
