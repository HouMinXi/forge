"""Finite I/O and actual optional SSE consumers, not filesystem qualification."""

import io
import json
import threading
import time

import pytest

from code_forge import invocation_io as module
from code_forge import llm_invoke
from code_forge.invocation_buffered_capture import (
    BufferedCaptureSession,
    SharedBufferedCaptureSession,
)
from code_forge.invocation_io import AuditPhase, IoLimits, process_audit_io
from code_forge.invocation_stream import CaptureUnavailable


@pytest.fixture
def supervisor(monkeypatch, request):
    # Unit isolation only: never replace an unresolved live process worker.
    monkeypatch.setattr(module, "_PROCESS", None)
    warnings = []
    limits = getattr(
        request, "param", IoLimits(operations=32, payload_bytes=8192, batch_bytes=512, captures=8)
    )
    process = process_audit_io(limits=limits, warn=warnings.append)
    yield process, warnings
    process.close()
    if process._worker is not None:
        process._worker.join(2)
        assert not process._worker.is_alive(), "test left a real audit worker unresolved"


class Target:
    def __init__(self, *, enter=None, release=None, action=None):
        self.enter, self.release, self.action = enter, release, action
        self.calls = []

    def audit_io(self, phase, payload):
        self.calls.append((phase, payload))
        if self.enter is not None:
            self.enter.set()
        if self.release is not None:
            assert self.release.wait(2)
        return self.action(phase, payload) if self.action else len(payload)


def shared(process, **changes):
    settings = dict(buffer_bytes=4096, batch_bytes=512, max_delay=0, flush_timeout_s=0.15)
    settings.update(changes)
    return SharedBufferedCaptureSession(process, **settings)


def capture(session, write, *, strict=False, warn=None, timeout=2):
    return session.new_capture(
        write=write,
        warn=warn or (lambda _: None),
        limit_bytes=10000,
        action_deadline=time.monotonic() + timeout,
        chunk_bytes=128,
        strict=strict,
    )


def body(name):
    event = {"choices": [{"delta": {"reasoning_content": "private-marker", "content": name}}]}
    return b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\nraw-tail\n"


def read(captured, raw, results, name, *, barrier=None):
    class Reader(io.BytesIO):
        def read1(self, n):
            if barrier is not None and self.tell() == 0:
                barrier.wait(1)
            return super().read1(n)

    try:
        results[name] = llm_invoke._read_sse(
            Reader(raw),
            time.monotonic() + 2,
            name,
            wire_capture=captured,
        )
    except BaseException as error:  # noqa: BLE001 - assert exact cross-thread result
        results[name] = error


def test_process_factory_never_replaces_worker_or_changes_limits(supervisor):
    process, _ = supervisor
    assert process_audit_io(limits=process.limits) is process
    with pytest.raises(ValueError, match="already fixed"):
        process_audit_io(limits=IoLimits(4, 100, 10, 1))
    with pytest.raises(ValueError, match="use process"):
        module.ProcessAuditIo(limits=process.limits, warn=lambda _: None)
    process.close()
    assert process_audit_io(limits=process.limits) is process
    assert process.register_capture() is None


def test_actual_sse_two_concurrent_captures_have_no_serial_writer(supervisor, monkeypatch):
    process, warnings = supervisor
    entered, release = threading.Event(), threading.Event()
    parsed = {name: threading.Event() for name in ("one", "two")}
    original_assemble = llm_invoke._assemble_sse

    def assemble(response, deadline, name, **kwargs):
        result = original_assemble(response, deadline, name, **kwargs)
        parsed[name].set()
        return result

    def forbidden_writer(_):
        pytest.fail("shared path started the serial buffered worker")

    monkeypatch.setattr(llm_invoke, "_assemble_sse", assemble)
    monkeypatch.setattr(BufferedCaptureSession, "_run", forbidden_writer)
    outputs = {name: io.BytesIO() for name in parsed}

    def first_write(data):
        entered.set()
        assert release.wait(2)
        return outputs["one"].write(data)

    session = shared(process, flush_timeout_s=1)
    captures = {"one": capture(session, first_write), "two": capture(session, outputs["two"].write)}
    barrier, results = threading.Barrier(2), {}
    readers = [
        threading.Thread(
            target=read, args=(captures[name], body(name), results, name), kwargs={"barrier": barrier}
        )
        for name in parsed
    ]
    try:
        for reader in readers:
            reader.start()
        assert entered.wait(1)
        assert all(event.wait(1) for event in parsed.values()), "default parser waited for durability"
        assert process._worker.name == "forge-audit-io"
        assert outputs["one"].getvalue() == b""
        release.set()
        for reader in readers:
            reader.join(2)
            assert not reader.is_alive()
        for name, captured in captures.items():
            assert results[name]["choices"][0]["message"]["content"] == name
            state = captured.seal()
            assert state.complete and state.accepted_bytes == len(body(name))
            assert outputs[name].getvalue() == body(name)
        assert process.retained_bytes == 0 and not warnings
    finally:
        release.set()
        for reader in readers:
            reader.join(2)
        session.close()


@pytest.mark.parametrize("phase", list(AuditPhase))
def test_every_phase_running_timeout_is_unknown_and_sticky_after_late_ack(supervisor, phase):
    process, warnings = supervisor
    entered, release = threading.Event(), threading.Event()
    target = Target(enter=entered, release=release)
    payload = b"secret-body" if phase is AuditPhase.APPEND else b""
    ticket = process.submit(target, phase, deadline=time.monotonic() + 0.03, payload=payload)
    try:
        assert entered.wait(1)
        outcome = process.wait(ticket)
        worker = process._worker
        assert outcome.state == "unknown" and outcome.publication_uncertain
        assert not outcome.not_executed
        assert process.retained_bytes == len(payload)
        assert warnings == ["audit_io_unresolved_process"]
        for _ in range(40):
            refused = process.submit(Target(), AuditPhase.OPEN, deadline=time.monotonic() + 1)
            assert process.wait(refused).not_executed
        assert process._worker is worker and process.register_capture() is None
    finally:
        release.set()
    with process._condition:
        assert process._condition.wait_for(lambda: process._running is None, timeout=1)
    assert process.retained_bytes == 0
    assert process.poll(ticket) is outcome
    assert process.unavailable and process.register_capture() is None
    assert process._worker is worker


def test_queued_cancel_is_definite_and_does_not_disable_healthy_work(supervisor):
    process, _ = supervisor
    entered, release = threading.Event(), threading.Event()
    first = process.submit(
        Target(enter=entered, release=release), AuditPhase.OPEN, deadline=time.monotonic() + 2
    )
    assert entered.wait(1)
    target = Target()
    queued = process.submit(target, AuditPhase.APPEND, payload=b"private", deadline=time.monotonic() + 2)
    process.cancel(queued)
    result = process.wait(queued)
    assert result.state == "cancelled" and result.not_executed and not result.publication_uncertain
    assert process.retained_bytes == 0 and not target.calls
    release.set()
    assert process.wait(first).state == "ack"
    assert not process.unavailable
    assert (
        process.wait(process.submit(target, AuditPhase.OPEN, deadline=time.monotonic() + 1)).state
        == "ack"
    )


def test_target_fifo_round_robin_does_not_starve_a_sibling(supervisor):
    process, _ = supervisor
    entered, release = threading.Event(), threading.Event()
    order = []

    class Ordered:
        def __init__(self, name):
            self.name = name
            self.count = 0

        def audit_io(self, phase, payload):
            if self.name == "a" and self.count == 0:
                entered.set()
                assert release.wait(2)
            order.append((self.name, self.count))
            self.count += 1
            return None

    a, b = Ordered("a"), Ordered("b")
    tickets = [process.submit(a, AuditPhase.OPEN, deadline=time.monotonic() + 2)]
    assert entered.wait(1)
    tickets.extend(
        process.submit(target, AuditPhase.START, deadline=time.monotonic() + 2)
        for target in (a, a, a, b, b)
    )
    release.set()
    assert all(process.wait(ticket).state == "ack" for ticket in tickets)
    assert order == [("a", 0), ("a", 1), ("b", 0), ("a", 2), ("b", 1), ("a", 3)]


def test_known_sink_failure_does_not_contaminate_other_capture(supervisor):
    process, _ = supervisor
    session = shared(process)

    def broken(_):
        raise OSError("private diagnostic")

    failed = capture(session, broken)
    healthy_output = io.BytesIO()
    healthy = capture(session, healthy_output.write)
    results = {}
    threads = [
        threading.Thread(target=read, args=(captured, body(name), results, name))
        for name, captured in (("bad", failed), ("good", healthy))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2)
        assert not thread.is_alive()
    assert results["bad"]["choices"] and results["good"]["choices"]
    assert failed.seal().failed and not failed.seal().complete
    assert healthy.seal().complete and healthy_output.getvalue() == body("good")
    assert not process.unavailable
    session.close()


@pytest.mark.parametrize("strict", [False, True])
def test_real_sse_hung_write_default_continues_strict_stops_and_process_warns(supervisor, strict):
    process, warnings = supervisor
    entered, release = threading.Event(), threading.Event()
    calls = []

    def blocked(data):
        calls.append(data)
        entered.set()
        assert release.wait(2)
        return len(data)

    session = shared(process, flush_timeout_s=0.03)
    notices = []
    captured = capture(session, blocked, strict=strict, warn=notices.append, timeout=0.1)
    results = {}
    reader = threading.Thread(target=read, args=(captured, body("hello"), results, "hello"))
    try:
        reader.start()
        assert entered.wait(1)
        reader.join(1)
        assert not reader.is_alive()
        if strict:
            assert isinstance(results["hello"], llm_invoke.LLMInvokeError)
            assert results["hello"].kind == "audit_persistence_error"
            assert not results["hello"].retryable
        else:
            assert results["hello"]["choices"][0]["message"]["content"] == "hello"
        frozen = captured.seal()
        assert frozen.write_uncertain and frozen.accepted_bytes == 0 and not frozen.complete
        assert warnings == ["audit_io_unresolved_process"]
        assert notices == ["audit_io_unresolved_process"]
        later = capture(session, len)
        assert list(later.chunks(io.BytesIO(b"later"))) == [b"later"]
        assert later.seal().failed
        with pytest.raises(CaptureUnavailable, match="audit_io_unresolved_process"):
            capture(session, len, strict=True)
        worker = process._worker
        release.set()
        with process._condition:
            assert process._condition.wait_for(lambda: process._running is None, timeout=1)
        assert captured.seal() is frozen and frozen.accepted_bytes == 0
        assert len(calls) == 1 and process._worker is worker and process.unavailable
    finally:
        release.set()
        reader.join(2)
        session.close()


def test_strict_bytes_do_not_reach_decoder_before_ack(supervisor):
    process, _ = supervisor
    entered, release, parsed = threading.Event(), threading.Event(), threading.Event()

    def blocked(data):
        entered.set()
        assert release.wait(2)
        return len(data)

    session = shared(process)
    captured = capture(session, blocked, strict=True)

    def consume():
        assert list(captured.chunks(io.BytesIO(b"abc"))) == [b"abc"]
        parsed.set()

    thread = threading.Thread(target=consume)
    try:
        thread.start()
        assert entered.wait(1)
        assert not parsed.is_set()
        release.set()
        thread.join(1)
        assert parsed.is_set() and captured.seal().complete
    finally:
        release.set()
        thread.join(2)
        session.close()


@pytest.mark.parametrize("fatal", [MemoryError("fatal"), KeyboardInterrupt(), SystemExit(3)])
def test_fatal_identity_transferred_once_and_payload_charge_released(supervisor, fatal):
    process, _ = supervisor
    session = shared(process)

    def broken(_):
        raise fatal

    captured = capture(session, broken)
    captured.append(b"private")
    with pytest.raises(type(fatal)) as caught:
        captured.seal()
    assert caught.value is fatal
    assert captured.seal().failed and process.retained_bytes == 0
    session.close()


def test_close_releases_queued_sensitive_bytes_but_holds_running_charge(supervisor):
    process, _ = supervisor
    entered, release = threading.Event(), threading.Event()
    running = process.submit(
        Target(enter=entered, release=release),
        AuditPhase.APPEND,
        payload=b"running",
        deadline=time.monotonic() + 2,
    )
    assert entered.wait(1)
    target = Target()
    queued = process.submit(
        target, AuditPhase.APPEND, payload=b"queued-private", deadline=time.monotonic() + 2
    )
    worker = process._worker
    process.close()
    assert process.poll(running).state == "unknown"
    assert process.poll(queued).not_executed and not target.calls
    assert not queued._pieces and queued._target is None
    assert process.retained_bytes == len(b"running") and process._worker is worker
    release.set()
    worker.join(1)
    assert not worker.is_alive() and process.retained_bytes == 0
    assert process_audit_io(limits=process.limits) is process


def test_append_flood_reserves_control_credit_and_drops_only_queued_work(supervisor):
    process, _ = supervisor
    entered, release = threading.Event(), threading.Event()
    first = process.submit(
        Target(enter=entered, release=release),
        AuditPhase.APPEND,
        payload=b"a",
        deadline=time.monotonic() + 2,
    )
    assert entered.wait(1)
    tickets = [
        process.submit(Target(), AuditPhase.APPEND, payload=b"b", deadline=time.monotonic() + 2)
        for _ in range(30)
    ]
    refused = process.submit(Target(), AuditPhase.APPEND, payload=b"c", deadline=time.monotonic() + 2)
    control = process.submit(Target(), AuditPhase.FINISH, deadline=time.monotonic() + 2)
    assert process.poll(refused).not_executed
    assert control._outcome is None and process._operations == 32
    assert process.retained_bytes == 31
    for ticket in [*tickets, control]:
        process.cancel(ticket)
        assert process.poll(ticket).not_executed
        assert not ticket._pieces and ticket._target is None
    assert process.retained_bytes == 1
    release.set()
    assert process.wait(first).state == "ack" and process.retained_bytes == 0


def test_global_payload_and_capture_limits_are_not_per_session(supervisor):
    process, _ = supervisor
    entered, release = threading.Event(), threading.Event()
    first = process.submit(
        Target(enter=entered, release=release),
        AuditPhase.APPEND,
        payload=b"x" * 512,
        deadline=time.monotonic() + 2,
    )
    assert entered.wait(1)
    tickets = [
        process.submit(Target(), AuditPhase.APPEND, payload=b"y" * 512, deadline=time.monotonic() + 2)
        for _ in range(40)
    ]
    assert process.retained_bytes == process.limits.payload_bytes - process.limits.batch_bytes
    assert any(process.poll(ticket) is not None for ticket in tickets)
    tokens = [process.register_capture() for _ in range(9)]
    assert all(token is not None for token in tokens[:8]) and tokens[-1] is None
    for token in tokens:
        process.release_capture(token)
    for ticket in tickets:
        process.cancel(ticket)
    release.set()
    assert process.wait(first).state == "ack"
    assert not process._captures and process.retained_bytes == 0


def test_shared_local_buffer_limit_keeps_running_payload_charged(supervisor):
    process, _ = supervisor
    entered, release = threading.Event(), threading.Event()

    def blocked(data):
        entered.set()
        assert release.wait(2)
        return len(data)

    session = shared(process, buffer_bytes=8, batch_bytes=4)
    captured = capture(session, blocked)
    try:
        captured.append(b"abcd")
        assert entered.wait(1)
        captured.append(b"efgh")
        state = captured.seal()
        assert state.observed_bytes == 8 and state.accepted_bytes == 0
        assert state.failed and state.write_uncertain
        assert process.retained_bytes == 4
    finally:
        release.set()
        session.close()


def test_sequential_captures_and_completed_tickets_do_not_accumulate_registry(supervisor):
    import gc
    import weakref

    process, _ = supervisor
    session = shared(process)
    references = []
    for _ in range(100):
        target = Target()
        reference = weakref.ref(target)
        ticket = process.submit(target, AuditPhase.OPEN, deadline=time.monotonic() + 1)
        assert process.wait(ticket).state == "ack"
        del target
        assert reference() is None and ticket._target is None
        captured = capture(session, len)
        list(captured.chunks(io.BytesIO(b"private")))
        assert captured.seal().complete
        references.append(weakref.ref(captured))
        del captured
    gc.collect()
    assert all(reference() is None for reference in references)
    assert not session._captures and not process._captures and not process._errors
    assert not process._lanes and process._operations == process.retained_bytes == 0
    session.close()


def test_close_discards_unclaimed_fatal_payload_without_permanent_error_history(supervisor):
    process, _ = supervisor

    def fail(_phase, _payload):
        raise MemoryError("fatal")

    ticket = process.submit(
        Target(action=fail), AuditPhase.APPEND, payload=b"private", deadline=time.monotonic() + 1
    )
    with process._condition:
        assert process._condition.wait_for(lambda: ticket._outcome is not None, timeout=1)
    assert process.retained_bytes == 7 and ticket in process._errors
    process.close()
    assert process.retained_bytes == 0 and not process._errors
    assert ticket._outcome.error is None


def test_process_warning_and_capture_warning_never_hold_scheduler_lock(supervisor):
    process, _ = supervisor
    entered, release = threading.Event(), threading.Event()
    warned, warning_release, controller_done = threading.Event(), threading.Event(), threading.Event()

    def warning(_):
        warned.set()
        assert warning_release.wait(2)

    process._warn = warning
    ticket = process.submit(
        Target(enter=entered, release=release), AuditPhase.OPEN, deadline=time.monotonic() + 0.03
    )
    assert entered.wait(1)
    result = []
    waiter = threading.Thread(target=lambda: result.append(process.wait(ticket)))
    waiter.start()
    try:
        assert warned.wait(1)

        def controller():
            assert process.register_capture() is None
            controller_done.set()

        thread = threading.Thread(target=controller)
        thread.start()
        assert controller_done.wait(0.2), "warning held the process scheduler lock"
        thread.join(1)
    finally:
        warning_release.set()
        release.set()
        waiter.join(2)
    assert result[0].state == "unknown"


def test_capture_warning_callback_is_outside_copy_reserve_lock(supervisor):
    process, _ = supervisor
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def warning(_):
        entered.set()
        assert release.wait(2)

    session = shared(process)
    captured = session.new_capture(
        write=len, warn=warning, limit_bytes=1, action_deadline=time.monotonic() + 2
    )
    producer = threading.Thread(target=lambda: captured.append(b"ab"))
    producer.start()
    try:
        assert entered.wait(1)

        def controller():
            ticket = process.submit(Target(), AuditPhase.OPEN, deadline=time.monotonic() + 1)
            assert process.wait(ticket).state == "ack"
            finished.set()

        thread = threading.Thread(target=controller)
        thread.start()
        assert finished.wait(0.3), "capture warning held the scheduler/copy-reserve lock"
        thread.join(1)
    finally:
        release.set()
        producer.join(2)
        session.close()


def test_shared_concurrent_seals_freeze_same_snapshot_and_flush_deadline(supervisor):
    process, _ = supervisor
    entered, release = threading.Event(), threading.Event()
    writes = []

    def blocked(data):
        writes.append(data)
        entered.set()
        assert release.wait(2)
        return len(data)

    session = shared(process, max_delay=60, flush_timeout_s=0.5)
    captured = capture(session, blocked)
    for value in b"abcdefgh":
        captured.append(bytes([value]))
    states = []
    first = threading.Thread(target=lambda: states.append(captured.seal()))
    second = threading.Thread(target=lambda: states.append(captured.seal()))
    try:
        first.start()
        assert entered.wait(1)
        deadline = captured._flush_deadline
        second.start()
        release.set()
        first.join(1)
        second.join(1)
        assert len(states) == 2 and states[0] is states[1]
        assert captured._flush_deadline == deadline and writes == [b"abcdefgh"]
        assert states[0].accepted_bytes == 8
    finally:
        release.set()
        first.join(2)
        if second.ident is not None:
            second.join(2)
        session.close()


def test_actual_sse_primary_parser_error_survives_secondary_memory_error(supervisor, monkeypatch):
    process, _ = supervisor
    session = shared(process)
    fatal, originals = MemoryError("private sink fatal"), []

    def fail(_):
        raise fatal

    captured = capture(session, fail)
    original_assemble = llm_invoke._assemble_sse

    def assemble(response, deadline, name, **kwargs):
        import itertools

        line = next(response)
        with process._condition:
            assert process._condition.wait_for(lambda: bool(process._errors), timeout=1)
        try:
            return original_assemble(itertools.chain([line], response), deadline, name, **kwargs)
        except BaseException as error:  # noqa: BLE001 - observe exact parser primary, not a substitute
            originals.append(error)
            raise

    monkeypatch.setattr(llm_invoke, "_assemble_sse", assemble)
    with pytest.raises(llm_invoke.LLMInvokeError) as caught:
        llm_invoke._read_sse(
            io.BytesIO(b"data: not-json\n\n"), time.monotonic() + 2, "fixture", wire_capture=captured
        )
    assert caught.value is originals[0] and caught.value.kind == "conn"
    assert caught.value is not fatal and not process._errors
    session.close()


@pytest.mark.parametrize("phase", ["append", {"phase": "append"}, 1, None])
def test_source_values_cannot_construct_a_phase_or_ticket(supervisor, phase):
    process, _ = supervisor
    with pytest.raises(TypeError, match="command"):
        process.submit(Target(), phase, deadline=time.monotonic() + 1)
    with pytest.raises(ValueError, match="foreign"):
        process.poll({"state": "ack", "accepted_bytes": 1})
    with pytest.raises(ValueError, match="supervisor-owned"):
        module.IoTicket(process, Target(), AuditPhase.OPEN, 1, 0, None, 1)
    assert process._worker is None


def test_inherited_process_objects_refuse_before_waiting_on_inherited_locks(supervisor, monkeypatch):
    process, _ = supervisor
    session = shared(process)
    captured = capture(session, len)
    ticket = process.submit(Target(), AuditPhase.OPEN, deadline=time.monotonic() + 1)
    assert process.wait(ticket).state == "ack"
    with monkeypatch.context() as patch:
        patch.setattr(module.os, "getpid", lambda: process._pid + 1)
        for action in (
            lambda: process_audit_io(limits=process.limits),
            process.register_capture,
            lambda: process.wait(ticket),
            lambda: process.poll(ticket),
            lambda: captured.append(b"a"),
            lambda: captured.chunks(io.BytesIO(b"a")),
            captured.seal,
            session.close,
        ):
            with pytest.raises(RuntimeError, match="inherited"):
                action()
    session.close()


@pytest.mark.parametrize("with_pending_payload", [False, True])
def test_ten_thousand_empty_direct_appends_store_no_fragments_or_operations(
    supervisor, with_pending_payload
):
    process, _ = supervisor
    target = Target()
    deadline = time.monotonic() + 120
    pending = None
    if with_pending_payload:
        pending = process.submit(
            target, AuditPhase.APPEND, payload=b"kept", deadline=deadline, ready_at=deadline - 1
        )
    for _ in range(10000):
        empty = process.submit(
            target, AuditPhase.APPEND, payload=b"", deadline=deadline, ready_at=deadline - 1
        )
    assert not empty._pieces and empty._target is None
    assert process.poll(empty).not_executed
    assert process._operations == int(with_pending_payload)
    assert process.retained_bytes == (4 if with_pending_payload else 0)
    assert not target.calls and len(process._lanes) == int(with_pending_payload)
    if pending is None:
        assert process._worker is None
    else:
        assert pending._pieces == [b"kept"]
        process.cancel(pending)
        assert process.retained_bytes == 0


def test_reconciled_fatal_stays_charged_until_original_exception_handoff(supervisor):
    process, _ = supervisor
    session = shared(process)
    fatal, cause = MemoryError("original fatal"), ValueError("original cause")
    payload = b"p" * 512

    def broken(data):
        assert data == payload
        try:
            raise cause
        except ValueError:
            raise fatal from cause

    captured = capture(session, broken)
    captured.append(payload)
    with process._condition:
        ticket = captured._tickets[0]
        assert process._condition.wait_for(lambda: ticket._outcome is not None, timeout=1)
    # A subsequent producer call reconciles the completed fatal without handing
    # it to the caller. The capture and traceback still own the 512-byte body.
    captured.append(b"next")
    assert captured._fatal is None and captured._fatal_ticket is ticket
    assert process.retained_bytes == ticket._charge == 512 and ticket in process._errors
    assert ticket._outcome.error is fatal and not captured._tickets
    with pytest.raises(MemoryError) as caught:
        captured.seal()
    assert caught.value is fatal and fatal.__cause__ is cause
    assert captured._fatal is captured._fatal_ticket is None
    assert process.retained_bytes == 0 and not process._errors
    assert ticket._outcome.error is None
    assert captured.seal().failed
    session.close()


@pytest.mark.parametrize("supervisor", [IoLimits(4, 4096, 512, 2)], indirect=True)
@pytest.mark.parametrize("reconcile_before_close", [False, True])
def test_repeated_session_close_discards_unclaimed_fatal_and_all_capture_owners(
    supervisor, reconcile_before_close
):
    import gc
    import weakref

    process, _ = supervisor

    class BrokenWriter:
        def __call__(self, data):
            assert len(data) == 512
            raise MemoryError("unclaimed fatal")

    for _ in range(10):
        session = shared(process)
        writer = BrokenWriter()
        captured = capture(session, writer)
        writer_ref, capture_ref = weakref.ref(writer), weakref.ref(captured)
        captured.append(b"x" * 512)
        with process._condition:
            assert process._condition.wait_for(lambda: bool(process._errors), timeout=1)
        if reconcile_before_close:
            captured.append(b"later")
            assert captured._fatal is None and captured._fatal_ticket is not None
        assert process.retained_bytes == 512 and len(process._errors) == 1
        session.close()
        session.close()
        assert not process._errors and process.retained_bytes == 0
        assert not process._captures and not captured._tickets
        assert captured._fatal is captured._fatal_ticket is None
        del captured, session, writer
        # A following control completion also proves the worker released the
        # previous operation's stack-local exception/payload before its next wait.
        ticket = process.submit(Target(), AuditPhase.OPEN, deadline=time.monotonic() + 1)
        assert process.wait(ticket).state == "ack"
        gc.collect()
        assert writer_ref() is None and capture_ref() is None
        assert not process.unavailable and process._operations == 0


def test_explicit_abandon_discards_completed_fatal_once_without_handoff(supervisor):
    process, _ = supervisor

    def broken(_phase, _data):
        raise MemoryError("unclaimed")

    ticket = process.submit(
        Target(action=broken), AuditPhase.APPEND, payload=b"x" * 512, deadline=time.monotonic() + 1
    )
    with process._condition:
        assert process._condition.wait_for(lambda: bool(process._errors), timeout=1)
    assert process.retained_bytes == 512
    process.abandon(ticket)
    process.abandon(ticket)
    process.cancel(ticket)
    assert process.retained_bytes == 0 and not process._errors
    outcome = process.poll(ticket)
    assert outcome.state == "failed" and outcome.error is None
    assert outcome.publication_uncertain and not process.unavailable


def test_session_close_preserves_completed_ack_in_later_seal(supervisor):
    process, _ = supervisor
    session = shared(process)
    captured = capture(session, len)
    captured.append(b"written")
    with process._condition:
        ticket = captured._tickets[0]
        assert process._condition.wait_for(lambda: ticket._outcome is not None, timeout=1)
    assert ticket._outcome.state == "ack" and ticket._outcome.accepted_bytes == 7
    session.close()
    session.close()
    snapshot = captured.seal()
    assert snapshot.accepted_bytes == 7 and snapshot.failed
    assert not snapshot.write_uncertain and not snapshot.complete
    assert not captured._tickets and captured._fatal_ticket is None
    assert process.retained_bytes == 0 and not process._errors
    assert captured.seal() is snapshot and not process.unavailable


def test_session_abandon_keeps_running_unknown_credit_until_function_really_returns(supervisor):
    process, _ = supervisor
    entered, release = threading.Event(), threading.Event()

    class BlockedWriter:
        def __call__(self, data):
            entered.set()
            assert release.wait(2)
            return len(data)

    session = shared(process)
    writer = BlockedWriter()
    captured = capture(session, writer)
    captured.append(b"x" * 512)
    assert entered.wait(1)
    ticket = captured._tickets[0]
    worker = process._worker
    session.close()
    session.close()
    process.abandon(ticket)
    assert process.poll(ticket).state == "unknown" and process.unavailable
    assert process.retained_bytes == ticket._charge == 512
    assert process._worker is worker and not captured._tickets
    snapshot = captured.seal()
    assert snapshot.write_uncertain and snapshot.failed and not snapshot.complete
    assert snapshot.accepted_bytes == 0
    # Late completion can release the running charge, not upgrade a frozen seal.
    release.set()
    with process._condition:
        assert process._condition.wait_for(lambda: process._running is None, timeout=1)
    assert captured.seal() is snapshot and snapshot.accepted_bytes == 0
    assert snapshot.write_uncertain and process.unavailable
    assert process.retained_bytes == ticket._charge == 0
    # A separate still-running case below checks that close does not falsely
    # release the target before physical completion.
    session.close()


def test_session_abandon_keeps_running_capture_owned_until_function_really_returns(supervisor):
    import gc
    import weakref

    process, _ = supervisor
    entered, release = threading.Event(), threading.Event()

    class BlockedWriter:
        def __call__(self, data):
            entered.set()
            assert release.wait(2)
            return len(data)

    session = shared(process)
    writer = BlockedWriter()
    captured = capture(session, writer)
    captured.append(b"x" * 512)
    assert entered.wait(1)
    ticket = captured._tickets[0]
    worker = process._worker
    reference = weakref.ref(captured)
    session.close()
    session.close()
    assert process.retained_bytes == ticket._charge == 512
    del captured, writer, session
    gc.collect()
    assert reference() is not None, "running target was falsely released"
    process.close()
    release.set()
    worker.join(1)
    assert not worker.is_alive()
    gc.collect()
    assert reference() is None and process.retained_bytes == 0
    assert process.poll(ticket).state == "unknown"
    assert process.unavailable and process._worker is worker


def test_process_close_discards_reconciled_fatal_without_a_capture_exception_cache(supervisor):
    import gc
    import weakref

    process, _ = supervisor
    session = shared(process)

    class Fatal(MemoryError):
        pass

    def broken(_data):
        raise Fatal("private fatal")

    captured = capture(session, broken)
    captured.append(b"x" * 512)
    with process._condition:
        assert process._condition.wait_for(lambda: bool(process._errors), timeout=1)
    captured.append(b"later")
    ticket = captured._fatal_ticket
    assert ticket is not None and captured._fatal is None
    reference = weakref.ref(ticket._outcome.error)
    assert process.retained_bytes == 512
    process.close()
    process._worker.join(1)
    assert not process._worker.is_alive()
    gc.collect()
    assert reference() is None
    assert process.retained_bytes == 0 and not process._errors
    assert ticket._outcome.error is None and captured._fatal is None
    # Explicit process discard cannot replay the old fatal at a later seal.
    assert captured.seal().failed and captured._fatal_ticket is None
    session.close()
