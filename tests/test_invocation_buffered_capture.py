"""Bounded default capture controls; no provider calls or persistence claims."""

import io
import threading
import time

import pytest

from code_forge.invocation_buffered_capture import BufferedCaptureSession


def capture(session, write, *, timeout=1, warn=None, limit=100, chunk=4):
    return session.new_capture(
        write=write,
        warn=warn or (lambda _: None),
        limit_bytes=limit,
        action_deadline=time.monotonic() + timeout,
        chunk_bytes=chunk,
    )


def test_flush_handoff_counts_real_ack_and_reuses_single_worker():
    session = BufferedCaptureSession(buffer_bytes=200, batch_bytes=100, max_delay=0.02)
    try:
        output = io.BytesIO()
        first = capture(session, output.write)
        assert list(first.chunks(io.BytesIO(b"abcdefgh"))) == [b"abcd", b"efgh"]
        state = first.seal()
        assert state.complete and state.accepted_bytes == first.buffered_bytes == 8
        assert output.getvalue() == b"abcdefgh"
        worker = session._worker
        second = capture(session, output.write)
        list(second.chunks(io.BytesIO(b"next")))
        assert second.seal().complete
        assert session._worker is worker
        assert session.retained_bytes == 0
    finally:
        session.close()


def test_hung_write_deadline_freezes_late_ack_and_disables_replacement():
    entered, release = threading.Event(), threading.Event()
    calls, warnings = [], []

    def blocked(data):
        calls.append(data)
        entered.set()
        release.wait(2)
        return len(data)

    session = BufferedCaptureSession(buffer_bytes=16, batch_bytes=8, max_delay=0.02)
    first = capture(session, blocked, timeout=0.05, warn=warnings.append)
    try:
        first.append(b"abcd")
        assert entered.wait(1)
        first.append(b"efgh")
        assert first.buffered_bytes == session.retained_bytes == 8
        state = first.seal()
        assert state.failed and state.write_uncertain and not state.complete
        assert state.accepted_bytes == 0
        assert session.disabled and session.retained_bytes == 4
        worker = session._worker
        second = capture(session, blocked)
        assert list(second.chunks(io.BytesIO(b"next"))) == [b"next"]
        assert second.seal().failed
        assert session._worker is worker
        release.set()
        worker.join(1)
        assert first.seal() is state and state.accepted_bytes == 0
        assert calls == [b"abcd"] and warnings == ["capture_write_failed"]
    finally:
        release.set()
        session.close()


def test_overflow_never_blocks_decoder_and_caps_inflight_plus_queue():
    entered, release = threading.Event(), threading.Event()

    def blocked(data):
        entered.set()
        release.wait(2)
        return len(data)

    session = BufferedCaptureSession(buffer_bytes=8, batch_bytes=4, max_delay=0.02)
    first = capture(session, blocked)
    try:
        first.append(b"abcd")
        assert entered.wait(1)
        first.append(b"efgh")
        assert session.retained_bytes == 4 and first.buffered_bytes == 4
        state = first.seal()
        assert state.observed_bytes == 8 and state.accepted_bytes == 0
        assert state.failed and state.write_uncertain
    finally:
        release.set()
        session.close()


@pytest.mark.parametrize("error", [MemoryError("fatal"), KeyboardInterrupt(), SystemExit(2)])
def test_fatal_identity_transferred_once_at_handoff(error):
    def broken(_):
        raise error

    session = BufferedCaptureSession(buffer_bytes=8, batch_bytes=4, max_delay=0.02)
    try:
        first = capture(session, broken)
        first.append(b"abcd")
        with pytest.raises(type(error)) as caught:
            first.seal()
        assert caught.value is error
        assert first.seal().failed and session.disabled
    finally:
        session.close()


@pytest.mark.parametrize("result", [0, 2, -1, 5, True, None])
def test_bad_or_partial_ack_is_not_full_commit(result):
    session = BufferedCaptureSession(buffer_bytes=8, batch_bytes=4, max_delay=0.02)
    try:
        first = capture(session, lambda _: result)
        list(first.chunks(io.BytesIO(b"abcd")))
        state = first.seal()
        assert state.failed and not state.complete and session.disabled
        assert state.accepted_bytes == (result if type(result) is int and 0 <= result <= 4 else 0)
    finally:
        session.close()


def test_generic_failure_warns_without_replacing_decode_result():
    def broken(_):
        raise OSError("secret")

    warnings = []
    session = BufferedCaptureSession(buffer_bytes=8, batch_bytes=4, max_delay=0.02)
    try:
        first = capture(session, broken, warn=warnings.append)
        assert list(first.chunks(io.BytesIO(b"abcd"))) == [b"abcd"]
        assert first.seal().write_uncertain
        assert warnings == ["capture_write_failed"]
    finally:
        session.close()


def test_interruption_does_not_wait_for_drain():
    entered, release = threading.Event(), threading.Event()

    def blocked(data):
        entered.set()
        release.wait(2)
        return len(data)

    session = BufferedCaptureSession(buffer_bytes=8, batch_bytes=4, max_delay=0.02)
    try:
        first = capture(session, blocked, timeout=60)
        first.append(b"abcd")
        assert entered.wait(1)
        start = time.monotonic()
        state = first.seal(interrupted=True)
        assert time.monotonic() - start < 0.5
        assert state.interrupted and state.write_uncertain and not state.complete
    finally:
        release.set()
        session.close()


def test_unsealed_handoff_refused_and_disabled_empty_capture_not_complete():
    session = BufferedCaptureSession(buffer_bytes=8, batch_bytes=4, max_delay=0.02)
    first = capture(session, len)
    with pytest.raises(ValueError, match="previous capture"):
        capture(session, len)
    session.close()
    assert not first.seal().complete
    second = capture(session, len)
    list(second.chunks(io.BytesIO(b"")))
    assert second.seal().failed and not second.seal().complete


def test_expired_ack_before_seal_is_not_counted():
    entered, release = threading.Event(), threading.Event()

    def blocked(data):
        entered.set()
        release.wait(2)
        return len(data)

    session = BufferedCaptureSession(buffer_bytes=8, batch_bytes=4, max_delay=0.02)
    try:
        first = capture(session, blocked, timeout=0.02)
        first.append(b"abcd")
        assert entered.wait(1)
        time.sleep(0.03)
        release.set()
        session._worker.join(1)
        state = first.seal()
        assert state.accepted_bytes == 0 and state.failed and state.write_uncertain
    finally:
        release.set()
        session.close()


def test_capture_limit_is_distinct_from_buffer_capacity():
    session = BufferedCaptureSession(buffer_bytes=200, batch_bytes=100, max_delay=0.02)
    output = io.BytesIO()
    warnings = []
    try:
        first = capture(session, output.write, limit=3, warn=warnings.append)
        assert list(first.chunks(io.BytesIO(b"abcd"))) == [b"abcd"]
        state = first.seal()
        assert state.accepted_bytes == 3 and state.observed_bytes == 4
        assert state.truncated and not state.complete
        assert output.getvalue() == b"abc"
        assert warnings == ["capture_limit_reached"]
    finally:
        session.close()


def test_session_close_discards_pending_without_forging_completion():
    session = BufferedCaptureSession(buffer_bytes=8, batch_bytes=4, max_delay=0.02)
    first = capture(session, len)
    list(first.chunks(io.BytesIO(b"")))
    session.close()
    assert first.seal().failed and not first.seal().complete


@pytest.mark.parametrize("capacity", [0, -1, True, 2.5])
def test_capacity_must_be_explicit_positive_integer(capacity):
    with pytest.raises(ValueError):
        BufferedCaptureSession(buffer_bytes=capacity, batch_bytes=1, max_delay=0.02)


def test_real_stage_sink_seals_only_drained_acknowledged_prefix():
    import hashlib
    from code_forge.invocation_audit import StageAcknowledgement, StageCheckpoint
    from code_forge.invocation_audit import StageHandle, StageIdentity
    from code_forge.invocation_stage_sink import RecorderStageSink

    handle = StageHandle(StageIdentity("run", "attempt", "observation", "artifact", "wire"))

    class Recorder:
        data = b""

        def checkpoint(self):
            return StageCheckpoint(
                handle.identity, len(self.data), hashlib.sha256(self.data).hexdigest()
            )

        def append_stage(self, given, *, offset, data, action_deadline):
            assert given is handle and offset == len(self.data)
            self.data += data
            return StageAcknowledgement(True, handle, self.checkpoint(), len(data), False, None, False)

        def seal_stage(self, given, *, checkpoint, action_deadline):
            assert checkpoint == self.checkpoint()
            return StageAcknowledgement(True, handle, self.checkpoint(), 0, False, None, False)

    recorder = Recorder()
    begin = StageAcknowledgement(True, handle, None, 0, False, None, False)
    sink = RecorderStageSink(recorder, begin, action_deadline=time.monotonic() + 1)
    session = BufferedCaptureSession(buffer_bytes=40, batch_bytes=20, max_delay=0.02)
    try:
        first = capture(session, sink.write)
        list(first.chunks(io.BytesIO(b"abcdefgh")))
        state = first.seal()
        assert state.complete and state.accepted_bytes == 8
        assert sink.sealed_checkpoint is None
        checkpoint = sink.seal(state)
        assert checkpoint.retained_bytes == 8 and recorder.data == b"abcdefgh"
    finally:
        session.close()


def test_small_reads_coalesce_and_seal_flushes_without_max_delay():
    writes = []
    session = BufferedCaptureSession(buffer_bytes=32, batch_bytes=16, max_delay=60)
    try:
        first = capture(session, lambda data: writes.append(data) or len(data), chunk=1)
        assert list(first.chunks(io.BytesIO(b"abcdefgh"))) == [bytes([c]) for c in b"abcdefgh"]
        assert writes == []
        assert session.retained_bytes == 8
        start = time.monotonic()
        assert first.seal().complete
        assert time.monotonic() - start < 0.5
        assert writes == [b"abcdefgh"]
        assert session.retained_bytes == 0
    finally:
        session.close()


def test_partial_batch_flushes_on_max_delay_before_seal():
    written = threading.Event()
    writes = []

    def write(data):
        writes.append(data)
        written.set()
        return len(data)

    session = BufferedCaptureSession(buffer_bytes=32, batch_bytes=16, max_delay=0.02)
    try:
        first = capture(session, write)
        first.append(b"ab")
        assert written.wait(1)
        assert writes == [b"ab"]
        assert first.seal().accepted_bytes == 2
    finally:
        session.close()


def test_full_batch_flushes_without_waiting_and_queue_objects_stay_bounded():
    entered, release = threading.Event(), threading.Event()
    writes = []

    def write(data):
        writes.append(data)
        entered.set()
        release.wait(2)
        return len(data)

    session = BufferedCaptureSession(buffer_bytes=16, batch_bytes=8, max_delay=60)
    try:
        first = capture(session, write)
        for _ in range(1000):
            first.append(b"")
        assert session._worker is None and not session._queue
        for value in b"abcdefgh":
            first.append(bytes([value]))
            assert len(session._queue) <= 8 and session.retained_bytes <= 16
        assert entered.wait(1)
        assert writes == [b"abcdefgh"]
        assert session.retained_bytes == 8
        first.append(b"i")
        assert session.disabled and session.retained_bytes == 8
        assert first.seal().failed
    finally:
        release.set()
        session.close()


def test_fatal_preserves_original_traceback_cause_and_releases_owned_reference():
    cause = ValueError("original cause")
    error = MemoryError("allocation failed")

    def broken(_):
        raise error from cause

    session = BufferedCaptureSession(buffer_bytes=8, batch_bytes=4, max_delay=0)
    try:
        first = capture(session, broken)
        first.append(b"abcd")
        with pytest.raises(MemoryError) as caught:
            first.seal()
        assert caught.value is error and error.__cause__ is cause
        frames, trace = [], error.__traceback__
        while trace is not None:
            frames.append(trace.tb_frame.f_code.co_name)
            trace = trace.tb_next
        assert "broken" in frames
        assert first._fatal is None and session.retained_bytes == 0
    finally:
        session.close()


@pytest.mark.parametrize("batch,delay", [(0, 0), (5, 0), (True, 0), (2, -1), (2, float("inf"))])
def test_invalid_batch_configuration_rejected(batch, delay):
    with pytest.raises(ValueError):
        BufferedCaptureSession(buffer_bytes=8, batch_bytes=batch, max_delay=delay)


def test_disable_preserves_already_acknowledged_prefix():
    entered, release = threading.Event(), threading.Event()
    writes = []

    def write(data):
        writes.append(data)
        if len(writes) == 2:
            entered.set()
            release.wait(2)
        return len(data)

    session = BufferedCaptureSession(buffer_bytes=8, batch_bytes=4, max_delay=0)
    try:
        first = capture(session, write)
        first.append(b"abcd")
        with session._condition:
            assert session._condition.wait_for(lambda: first._committed == 4, timeout=1)
        first.append(b"efgh")
        assert entered.wait(1)
        first.append(b"i")
        state = first.seal()
        assert state.accepted_bytes == 4 and state.observed_bytes == 9
        assert state.failed and state.write_uncertain and session.disabled
        release.set()
        session._worker.join(1)
        assert first.seal() is state and state.accepted_bytes == 4
    finally:
        release.set()
        session.close()


@pytest.mark.parametrize(
    "statement",
    ["fragments = []", "fragments.append(piece)", "self._queue.popleft()", 'return b"".join(fragments)'],
)
def test_assembly_allocation_failure_hands_off_same_fatal(monkeypatch, statement):
    import inspect
    import code_forge.invocation_buffered_capture as module

    error = MemoryError("injected assembly allocation failure")
    source, start = inspect.getsourcelines(module)
    targets = {(start or 1) + index for index, line in enumerate(source) if statement in line}
    injected = threading.Event()

    def trace(frame, event, arg):
        if (
            event == "line"
            and frame.f_code.co_filename == module.__file__
            and frame.f_code.co_name in ("_run", "_assemble_batch")
            and frame.f_lineno in targets
            and not injected.is_set()
        ):
            injected.set()
            raise error
        return trace

    monkeypatch.setattr(threading, "_trace_hook", trace)
    session = BufferedCaptureSession(buffer_bytes=8, batch_bytes=4, max_delay=0)
    try:
        first = capture(session, len, timeout=0.1)
        first.append(b"abcd")
        assert injected.wait(1)
        with pytest.raises(MemoryError) as caught:
            first.seal()
        assert caught.value is error
        assert first._pending == 0 and session.retained_bytes == 0
        assert session.disabled and first.seal().failed
    finally:
        session.close()


@pytest.mark.parametrize("partial", [False, True])
def test_oversize_is_rejected_before_raw_limit_slice(partial):
    from http.client import IncompleteRead
    import tracemalloc

    data = b"x" * 10_000_000
    original = IncompleteRead(data, 1)

    class Response:
        def read(self, _):
            raise original

    session = BufferedCaptureSession(buffer_bytes=8, batch_bytes=4, max_delay=0)
    try:
        first = capture(session, len, limit=5_000_000)
        tracemalloc.start()
        if partial:
            with pytest.raises(IncompleteRead) as caught:
                list(first.chunks(Response()))
            assert caught.value is original
        else:
            first.append(data)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert peak < 1_000_000
        state = first.seal()
        assert state.observed_bytes == len(data) and state.accepted_bytes == 0
        assert state.failed and not state.complete
        assert session.retained_bytes == 0 and session._worker is None
    finally:
        tracemalloc.stop()
        session.close()


def test_oversized_partial_preserves_committed_prefix_and_primary_failure():
    from http.client import IncompleteRead

    session = BufferedCaptureSession(buffer_bytes=8, batch_bytes=4, max_delay=0)
    original = IncompleteRead(b"x" * 100, 1)

    class Response:
        def read(self, _):
            raise original

    try:
        first = capture(session, len)
        first.append(b"abcd")
        with session._condition:
            assert session._condition.wait_for(lambda: first._committed == 4, timeout=1)
        with pytest.raises(IncompleteRead) as caught:
            list(first.chunks(Response()))
        assert caught.value is original
        state = first.seal()
        assert state.accepted_bytes == 4 and state.observed_bytes == 104
        assert state.failed and not state.complete
    finally:
        session.close()


def test_oversized_append_concurrent_with_hung_write_freezes_once():
    entered, release = threading.Event(), threading.Event()
    writes = []

    def blocked(data):
        writes.append(data)
        entered.set()
        release.wait(2)
        return len(data)

    session = BufferedCaptureSession(buffer_bytes=8, batch_bytes=4, max_delay=0)
    try:
        first = capture(session, blocked)
        first.append(b"abcd")
        assert entered.wait(1)
        first.append(b"x" * 100)
        state = first.seal()
        assert state.failed and state.write_uncertain and state.observed_bytes == 104
        assert state.accepted_bytes == 0 and session.retained_bytes == 4
        first.append(b"y" * 100)
        release.set()
        session._worker.join(1)
        assert first.seal() is state and state.observed_bytes == 104
        assert writes == [b"abcd"] and session.retained_bytes == 0
    finally:
        release.set()
        session.close()


@pytest.mark.parametrize("operation", ["seal", "close"])
def test_blocking_warning_does_not_hold_controller_lock(operation):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    states, warnings = [], []

    def warn(code):
        warnings.append(code)
        entered.set()
        release.wait(2)

    session = BufferedCaptureSession(buffer_bytes=8, batch_bytes=4, max_delay=60)
    first = capture(session, len, limit=1, timeout=0.05, warn=warn)
    reader = threading.Thread(target=lambda: first.append(b"ab"))

    def control():
        if operation == "seal":
            states.append(first.seal(interrupted=True))
        else:
            session.close()
        finished.set()

    controller = threading.Thread(target=control)
    try:
        reader.start()
        assert entered.wait(1)
        controller.start()
        assert finished.wait(0.15), "controller blocked behind an external warning"
        assert warnings == ["capture_limit_reached"]
        if states:
            assert states[0].interrupted and not states[0].complete
    finally:
        release.set()
        reader.join(1)
        controller.join(1)
        session.close()


@pytest.mark.parametrize("error", [MemoryError("warning fatal"), KeyboardInterrupt(), SystemExit(3)])
def test_deferred_warning_fatal_identity_and_controller_lock_release(error):
    def warn(_):
        raise error

    session = BufferedCaptureSession(buffer_bytes=8, batch_bytes=4, max_delay=60)
    first = capture(session, len, limit=1, warn=warn)
    finished = threading.Event()
    try:
        with pytest.raises(type(error)) as caught:
            first.append(b"ab")
        assert caught.value is error
        controller = threading.Thread(target=lambda: (session.close(), finished.set()))
        controller.start()
        assert finished.wait(0.15)
        controller.join(1)
        assert first.seal(interrupted=True).failed
    finally:
        session.close()


@pytest.mark.parametrize("action_timeout,flush_timeout", [(600, 0.03), (0.03, 600), (600, 0)])
def test_flush_timeout_never_extends_action_deadline_or_waits_for_hung_writer(
    action_timeout, flush_timeout
):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    states = []

    def blocked(data):
        entered.set()
        release.wait(2)
        return len(data)

    session = BufferedCaptureSession(
        buffer_bytes=8, batch_bytes=4, max_delay=0, flush_timeout_s=flush_timeout
    )
    first = capture(session, blocked, timeout=action_timeout)
    controller = threading.Thread(target=lambda: (states.append(first.seal()), finished.set()))
    try:
        first.append(b"abcd")
        assert entered.wait(1)
        start = time.monotonic()
        controller.start()
        assert finished.wait(0.5), "flush ignored the shorter deadline"
        assert first._flush_deadline <= first._deadline
        assert first._flush_deadline <= start + flush_timeout + 0.1
        state = states[0]
        assert state.failed and state.write_uncertain and state.accepted_bytes == 0
        assert session.disabled
        release.set()
        session._worker.join(1)
        assert first.seal() is state and state.accepted_bytes == 0
    finally:
        release.set()
        controller.join(1)
        session.close()


def test_concurrent_seals_share_fixed_flush_deadline_and_preserve_known_prefix():
    entered, release = threading.Event(), threading.Event()
    writes, states = [], []
    first_done, second_done = threading.Event(), threading.Event()

    def write(data):
        writes.append(data)
        if len(writes) == 2:
            entered.set()
            release.wait(2)
        return len(data)

    session = BufferedCaptureSession(buffer_bytes=8, batch_bytes=4, max_delay=0, flush_timeout_s=0.1)
    first = capture(session, write, timeout=600)
    controllers = [
        threading.Thread(target=lambda: (states.append(first.seal()), first_done.set())),
        threading.Thread(target=lambda: (states.append(first.seal()), second_done.set())),
    ]
    try:
        first.append(b"abcd")
        with session._condition:
            assert session._condition.wait_for(lambda: first._committed == 4, timeout=1)
        first.append(b"efgh")
        assert entered.wait(1)
        controllers[0].start()
        with session._condition:
            assert session._condition.wait_for(lambda: first._flush_deadline is not None, timeout=1)
            fixed = first._flush_deadline
        controllers[1].start()
        assert first_done.wait(0.5) and second_done.wait(0.5)
        assert first._flush_deadline == fixed
        assert states[0] is states[1]
        assert states[0].accepted_bytes == 4 and states[0].write_uncertain
        release.set()
        session._worker.join(1)
        assert first.seal() is states[0] and states[0].accepted_bytes == 4
    finally:
        release.set()
        for controller in controllers:
            if controller.ident is not None:
                controller.join(1)
        session.close()


@pytest.mark.parametrize("timeout", [-1, True, float("inf"), float("nan"), "2"])
def test_invalid_flush_timeout_rejected(timeout):
    with pytest.raises(ValueError):
        BufferedCaptureSession(buffer_bytes=8, batch_bytes=4, max_delay=0, flush_timeout_s=timeout)


def test_tail_incomplete_reason_forwards_to_acquisition(monkeypatch):
    session = BufferedCaptureSession(buffer_bytes=8, batch_bytes=4, max_delay=0)
    first = capture(session, len)
    reasons = []
    monkeypatch.setattr(first._wire, "report_incomplete", reasons.append, raising=False)
    try:
        first.report_incomplete("capture_tail_limit")
        assert reasons == ["capture_tail_limit"]
    finally:
        session.close()


@pytest.mark.parametrize(
    "code,flag",
    [
        ("capture_tail_limit", "truncated"),
        ("capture_tail_deadline", "interrupted"),
        ("capture_tail_interrupted", "interrupted"),
    ],
)
def test_real_tail_incomplete_flags_survive_buffered_flush(code, flag):
    warnings = []
    session = BufferedCaptureSession(buffer_bytes=8, batch_bytes=4, max_delay=0)
    try:
        first = capture(session, len, warn=warnings.append)
        list(first.chunks(io.BytesIO(b"abcd")))
        first.report_incomplete(code)
        state = first.seal()
        assert getattr(state, flag) is True
        assert not state.complete and state.accepted_bytes == 4
        assert warnings == [code]
        first.report_incomplete("capture_tail_limit")
        assert first.seal() is state and warnings == [code]
    finally:
        session.close()
