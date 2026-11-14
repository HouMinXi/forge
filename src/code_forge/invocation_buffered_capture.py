"""Default-only bounded capture: queued bytes are never a durable acknowledgement.

A session owns one writer. A failed or unresolved writer permanently disables
that session's audit; decoding may continue. Strict capture must use WireCapture
with its synchronous sink instead. A process crash can lose a buffered tail.
"""

from collections import deque
from dataclasses import replace
import math
import threading
import time

from code_forge.invocation_stream import WireCapture


class BufferedCaptureSession:
    """Serial invocation facade with a hard transport-payload-copy bound.

    ``batch_bytes`` is at most half ``buffer_bytes``: the rest bounds input
    payloads, reserving a full batch for concatenation. Python object overhead,
    arbitrary sink/traceback allocations, and decoder memory are not covered.
    The byte cap also bounds
    nonempty queue entries; empty appends allocate no entries. ``max_delay`` is
    measured from the oldest queued piece. Seal bypasses that delay. Acquisition
    reads are capped at batch_bytes; an oversized direct append is refused.
    ``flush_timeout_s`` defaults to 2.0 seconds. The first seal fixes a shared
    flush deadline no later than the original action deadline; later seals
    cannot extend it. This bounds drain waiting, not arbitrary warning callbacks.

    Call ``close`` at session exit. It never joins a potentially hung writer.
    The caller still owns stage seal/adoption after capture.seal() returns a
    known committed prefix. Neither buffering nor this facade grants audit credit.
    """

    def __init__(self, *, buffer_bytes, batch_bytes, max_delay, flush_timeout_s=2.0):
        if (
            type(buffer_bytes) is not int
            or buffer_bytes <= 0
            or type(batch_bytes) is not int
            or not 0 < batch_bytes <= buffer_bytes // 2
            or type(max_delay) not in (int, float)
            or not math.isfinite(max_delay)
            or max_delay < 0
            or type(flush_timeout_s) not in (int, float)
            or not math.isfinite(flush_timeout_s)
            or flush_timeout_s < 0
        ):
            raise ValueError("invalid buffer capacity")
        # Reserve room for a full concatenation copy even while all original
        # queued/in-flight payloads remain alive. Every entry is nonempty, so
        # this byte cap also bounds the number of queue objects.
        self._capacity = buffer_bytes - batch_bytes
        self._batch_bytes = batch_bytes
        self._max_delay = max_delay
        self._flush_timeout = flush_timeout_s
        self._condition = threading.Condition()
        self._queue = deque()
        self._retained = 0
        self._disabled = False
        self._worker = None
        self._active = None

    @property
    def disabled(self):
        with self._condition:
            return self._disabled

    @property
    def retained_bytes(self):
        """Owned payload bytes, including join reservation and a hung write."""
        with self._condition:
            return self._retained

    def new_capture(self, *, write, warn, limit_bytes, action_deadline, chunk_bytes=65536):
        if (
            type(action_deadline) not in (int, float)
            or not math.isfinite(action_deadline)
            or not callable(write)
        ):
            raise ValueError("invalid buffered capture configuration")
        with self._condition:
            if self._active is not None and self._active._snapshot is None:
                raise ValueError("previous capture must be sealed before handoff")
            capture = BufferedWireCapture(self, write, warn, limit_bytes, action_deadline, chunk_bytes)
            self._active = capture
            return capture

    def _disable(self):
        # Condition lock required. The in-flight payload remains accounted for.
        self._disabled = True
        if self._active is not None and self._active._snapshot is None:
            self._active._failed = True
        while self._queue:
            capture, data, _ = self._queue.popleft()
            capture._pending -= 1
            self._retained -= len(data)
        self._condition.notify_all()

    def close(self):
        with self._condition:
            self._disable()

    def _enqueue(self, capture, data):
        if not data:
            return 0
        with self._condition:
            if (
                self._disabled
                or capture._snapshot is not None
                or time.monotonic() >= capture._effective_deadline()
                or len(data) > self._batch_bytes
                or len(data) > self._capacity - self._retained
            ):
                capture._failed = True
                self._disable()
                return 0
            self._queue.append((capture, data, time.monotonic()))
            capture._pending += 1
            capture._buffered += len(data)
            self._retained += len(data)
            if self._worker is None:
                self._worker = threading.Thread(target=self._run, daemon=True)
                try:
                    self._worker.start()
                except BaseException:
                    self._disable()
                    raise
            self._condition.notify_all()
            return len(data)

    def _assemble_batch(self, capture):
        """Build without mutating the queue; release traceback-held originals."""
        fragments = []
        piece = None
        size = 0
        try:
            for next_capture, piece, _ in self._queue:
                if next_capture is not capture or size + len(piece) > self._batch_bytes:
                    break
                fragments.append(piece)
                size += len(piece)
            return b"".join(fragments), len(fragments)
        finally:
            fragments.clear()
            piece = None

    def _run(self):
        while True:
            with self._condition:
                while not self._queue and not self._disabled:
                    self._condition.wait()
                if self._disabled:
                    return
                capture = self._queue[0][0]
                queued_at = self._queue[0][2]
                while not self._disabled and not capture._flush:
                    ready = self._retained >= self._batch_bytes
                    remaining = (
                        min(queued_at + self._max_delay, capture._effective_deadline())
                        - time.monotonic()
                    )
                    if ready or remaining <= 0:
                        break
                    self._condition.wait(remaining)
                if self._disabled:
                    return
                if time.monotonic() >= capture._effective_deadline():
                    capture._failed = True
                    self._disable()
                    return
                data = None
                try:
                    # Originals remain queued until all fallible assembly has
                    # succeeded. Reserve a full output batch before allocating.
                    self._retained += self._batch_bytes
                    data, count_entries = self._assemble_batch(capture)
                    for _ in range(count_entries):
                        self._queue.popleft()
                    self._retained -= self._batch_bytes
                except BaseException as error:  # noqa: BLE001 - hand off assembly failure
                    # There is no other invocation or writer in this session.
                    # Reconcile from ownership rather than partial mutations:
                    # queue originals are dropped, a completed joined payload
                    # can remain referenced by the preserved fatal traceback.
                    self._queue.clear()
                    capture._pending = 0
                    self._retained = len(data) if data is not None else 0
                    capture._fatal_bytes = self._retained
                    capture._fatal = error
                    capture._failed = capture._uncertain = True
                    data = None
                    self._disable()
                    return
                capture._inflight = True
            count, error = 0, None
            try:
                count = capture._write(data)
                if type(count) is not int or not 0 <= count <= len(data):
                    raise ValueError("invalid capture acknowledgement")
            except BaseException as exc:  # noqa: BLE001 - hand off fatal identity to controller
                error = exc
            with self._condition:
                capture._pending -= count_entries
                capture._inflight = False
                fatal = isinstance(error, (MemoryError, KeyboardInterrupt, SystemExit))
                if fatal and capture._snapshot is None:
                    # A fatal traceback may retain the sink's input. Keep that
                    # payload charged until ownership passes to the caller.
                    capture._fatal_bytes = len(data)
                else:
                    self._retained -= len(data)
                if capture._snapshot is None:
                    expired = time.monotonic() >= capture._effective_deadline()
                    if expired:
                        capture._failed = capture._uncertain = True
                        self._disable()
                    if error is None and not expired:
                        capture._committed += count
                    elif error is not None:
                        capture._uncertain = True
                        if fatal:
                            capture._fatal = error
                    if error is not None or count != len(data):
                        capture._failed = True
                        self._disable()
                del data
                error = None
                self._condition.notify_all()


def _snapshot_from(acquired, *, accepted_bytes, failed, write_uncertain, not_executed):
    """Build the sealed snapshot from the acquisition plus this capture's own outcome."""
    return replace(
        acquired,
        accepted_bytes=accepted_bytes,
        failed=failed,
        write_uncertain=write_uncertain,
        not_executed=not_executed,
    )


def _failure_notice(scoped, snapshot):
    """Choose the capture failure notice.

    Off, this is the single notice the base tree emits, so switch-off behaviour does
    not change. On, a capture whose tickets never executed and which stored nothing is
    reported as such rather than as a write failure; committed bytes keep it a write
    failure, because calling that "never executed" invites a resend of stored data.
    """
    if not scoped:
        return "capture_write_failed"
    if snapshot.not_executed and snapshot.accepted_bytes == 0:
        # Committed bytes mean this capture did execute; reporting "never executed"
        # would invite a resend of data that is already on disk.
        return "capture_not_executed"
    if snapshot.write_uncertain:
        return "capture_write_uncertain"
    return "capture_write_failed"


class _BoundedWireCapture(WireCapture):
    """Guard both direct appends and chunks' IncompleteRead partial dispatch."""

    def __init__(self, owner, **kwargs):
        self._owner = owner
        self._deferring_notice = False
        self._deferred_notice = None
        super().__init__(**kwargs)

    def _notice(self, code):
        # Only the single acquisition reader calls this hook. The facade owns
        # the one-shot warning flag; no external callback may run under the
        # session condition used to serialize payload-copy allocation.
        if self._deferring_notice:
            self._deferred_notice = code
        else:
            self._owner._notice(code)

    def append(self, data):
        if type(data) is not bytes:
            raise TypeError("reader must return bytes")
        if len(data) <= self._owner._session._batch_bytes:
            # A raw-limit slice also consumes the copy reserve. Serialize it
            # with batch assembly so both copies cannot coexist unaccounted.
            # The inherited write only enqueues; no sink I/O occurs here.
            with self._owner._session._condition:
                self._deferring_notice = True
                try:
                    result = super().append(data)
                finally:
                    self._deferring_notice = False
                    notice = self._deferred_notice
                    self._deferred_notice = None
            if notice is not None:
                self._owner._notice(notice)
            return result
        # Never enter WireCapture's raw-limit slicing with an oversized input.
        # Count all observed bytes and preserve the already committed prefix.
        with self._lock:
            if self._sealed:
                return
            self._observed += len(data)
            self._failed = True
        session = self._owner._session
        with session._condition:
            session._disable()
        self._notice("capture_write_failed")


class BufferedWireCapture:
    """WireCapture-compatible acquisition, followed by bounded drain at seal.

    ``buffered_bytes`` is cumulative enqueue acceptance, NOT storage commitment.
    Only the immutable returned snapshot's accepted_bytes counts real sink ACKs.
    """

    # Serial captures belong to no scoped domain; the shared subclass overrides this with
    # the lane's flag, so a lane that forgets to carry it fails loudly instead of silently
    # reporting the switch as off.
    _scoped = False

    def __init__(self, session, write, warn, limit_bytes, deadline, chunk_bytes):
        if type(chunk_bytes) is not int or chunk_bytes <= 0 or not callable(warn):
            raise ValueError("invalid buffered capture configuration")
        self._session = session
        self._write = write
        self._warn = warn
        self._deadline = deadline
        self._buffered = self._committed = self._pending = 0
        self._not_executed = False
        self._inflight = self._uncertain = self._warned = False
        self._failed = session._disabled
        self._fatal = self._snapshot = None
        self._fatal_bytes = 0
        self._flush = False
        self._flush_deadline = None
        self._wire = _BoundedWireCapture(
            self,
            write=lambda data: session._enqueue(self, data),
            warn=self._notice,
            limit_bytes=limit_bytes,
            chunk_bytes=min(chunk_bytes, session._batch_bytes),
        )

    @property
    def buffered_bytes(self):
        with self._session._condition:
            return self._buffered

    def _notice(self, code):
        with self._session._condition:
            if self._warned:
                return
            self._warned = True
        try:
            self._warn(code)
        except MemoryError:
            raise
        except Exception:  # noqa: BLE001 - warnings must not replace review outcomes
            with self._session._condition:
                self._failed = True

    def append(self, data):
        return self._wire.append(data)

    def report_incomplete(self, code):
        """Forward bounded-tail reasons without manufacturing capture success."""
        return self._wire.report_incomplete(code)

    def chunks(self, response, *, preserve_primary=False):
        return self._wire.chunks(response, preserve_primary=preserve_primary)

    def lines(self, response):
        return self._wire.lines(response)

    def _effective_deadline(self):
        return self._deadline if self._flush_deadline is None else self._flush_deadline

    def seal(self, *, interrupted=False):
        seal_start = time.monotonic()
        acquired = self._wire.seal(interrupted=interrupted)
        session = self._session
        with session._condition:
            if self._snapshot is not None:
                return self._snapshot
            if self._flush_deadline is None:
                self._flush_deadline = min(self._deadline, seal_start + session._flush_timeout)
            self._flush = True
            session._condition.notify_all()
            while self._pending and not interrupted and not session._disabled and self._snapshot is None:
                remaining = self._effective_deadline() - time.monotonic()
                if remaining <= 0:
                    break
                session._condition.wait(remaining)
            if self._snapshot is not None:
                return self._snapshot
            unresolved = self._pending > 0
            if unresolved or self._failed or interrupted:
                session._disable()
            self._snapshot = _snapshot_from(
                acquired,
                accepted_bytes=self._committed,
                failed=acquired.failed or self._failed or unresolved,
                write_uncertain=acquired.write_uncertain or self._uncertain or self._inflight,
                not_executed=self._not_executed,
            )
            snapshot, fatal = self._snapshot, self._fatal
            self._fatal = None
            session._retained -= self._fatal_bytes
            self._fatal_bytes = 0
        if fatal is not None:
            raise fatal
        if snapshot.failed or snapshot.write_uncertain:
            self._notice(_failure_notice(self._scoped, snapshot))
        return snapshot


class SharedBufferedCaptureSession:
    """Concurrent capture consumer of the one process audit I/O supervisor.

    No serial BufferedCaptureSession worker is created here. Each capture has
    its own FIFO and committed prefix; the shared supervisor owns all writes.
    An unresolved running operation disables subsequent process-wide audit, not
    just this session. Existing provider decoding may continue in default mode.
    This optional consumer does not wire CLI/owner lifecycle automatically.
    """

    def __init__(
        self,
        supervisor,
        *,
        buffer_bytes,
        batch_bytes,
        max_delay,
        flush_timeout_s=2.0,
        scoped_fault_domain=False,
    ):
        from .invocation_io import ProcessAuditIo

        if not isinstance(supervisor, ProcessAuditIo):
            raise TypeError("expected process audit I/O supervisor")
        if type(scoped_fault_domain) is not bool:
            raise TypeError("invalid scoped fault domain switch")
        self._scoped_fault_domain = scoped_fault_domain
        # Reuse the reviewed configuration validation, not its serial writer.
        settings = BufferedCaptureSession(
            buffer_bytes=buffer_bytes,
            batch_bytes=batch_bytes,
            max_delay=max_delay,
            flush_timeout_s=flush_timeout_s,
        )
        if batch_bytes > supervisor.limits.batch_bytes:
            raise ValueError("capture batch exceeds process batch reserve")
        self._io = supervisor
        self._capacity = settings._capacity
        self._batch_bytes = batch_bytes
        self._max_delay = max_delay
        self._flush_timeout = flush_timeout_s
        self._captures = set()
        self._closed = False

    def new_capture(self, *, write, warn, limit_bytes, action_deadline, chunk_bytes=65536, strict=False):
        self._io._check_process()
        if type(strict) is not bool:
            raise ValueError("invalid strict capture policy")
        if (
            type(action_deadline) not in (int, float)
            or not math.isfinite(action_deadline)
            or not callable(write)
            or not callable(warn)
        ):
            raise ValueError("invalid shared capture configuration")
        token = None if self._closed else self._io.register_capture()
        lane = _SharedCaptureLane(self, token)
        try:
            capture = _SharedWireCapture(
                lane,
                write,
                warn,
                limit_bytes,
                action_deadline,
                chunk_bytes,
                strict=strict,
            )
        except BaseException:
            self._io.release_capture(token)
            raise
        lane._capture = capture
        with self._io._condition:
            if self._closed:
                capture._failed = lane._disabled = True
                self._io._release_capture_locked(token)
            elif token is not None:
                self._captures.add(capture)
        if lane._disabled:
            capture._notice(
                "audit_io_unresolved_process" if self._io._blocked else "capture_write_failed"
            )
        return capture

    def close(self):
        self._io._check_process()
        with self._io._condition:
            self._closed = True
            for capture in list(self._captures):
                capture._session._disable()
                capture._discard_locked()
                self._io._release_capture_locked(capture._session._token)
            self._captures.clear()
        self._io.emit_warning()


class _SharedCaptureLane:
    def __init__(self, parent, token):
        self._parent = parent
        self._io = parent._io
        self._token = token
        self._condition = self._io._condition
        self._batch_bytes = parent._batch_bytes
        self._flush_timeout = parent._flush_timeout
        # The captures created on this lane read the flag from here, so it has to travel
        # with the lane rather than stay on the session one level above it.
        self._scoped_fault_domain = parent._scoped_fault_domain
        self._disabled = token is None
        self._capture = None

    def _disable(self):
        self._disabled = True
        capture = self._capture
        if capture is not None:
            capture._failed = True
            for ticket in capture._tickets:
                self._io._cancel_locked(ticket)
        self._condition.notify_all()

    def _enqueue(self, capture, data):
        from .invocation_io import AuditPhase

        if not data:
            return 0
        # Called under the acquisition copy-reserve condition. No callbacks here.
        capture._reconcile_locked()
        if (
            self._disabled
            or capture._snapshot is not None
            or capture._failed
            or len(data) > self._batch_bytes
            or sum(ticket._charge for ticket in capture._tickets) + len(data) > self._parent._capacity
        ):
            self._disable()
            return 0
        ticket = self._io._submit_locked(
            capture,
            AuditPhase.APPEND,
            payload=data,
            deadline=capture._effective_deadline(),
            ready_at=time.monotonic() + self._parent._max_delay,
            capture=self._token,
            batch_bytes=self._batch_bytes,
        )
        if not capture._tickets or capture._tickets[-1] is not ticket:
            capture._tickets.append(ticket)
        if ticket._outcome is not None:
            capture._reconcile_locked()
            self._disable()
            return 0
        capture._buffered += len(data)
        if capture._strict:
            self._io._flush_locked(ticket, capture._effective_deadline())
            while ticket._outcome is None:
                self._io._expire_locked()
                if ticket._outcome is None:
                    self._condition.wait(max(0.0, ticket._deadline - time.monotonic()))
            capture._reconcile_locked()
            if capture._fatal_ticket is not None:
                capture._raise_fatal_locked()
            return len(data) if not capture._failed else 0
        return len(data)


class _SharedWireCapture(BufferedWireCapture):
    def __init__(self, *args, strict=False):
        self._tickets = deque()
        self._fatal_ticket = None
        self._strict = strict
        super().__init__(*args)

    @property
    def _scoped(self):
        return self._session._scoped_fault_domain

    def append(self, data):
        self._session._io._check_process()
        return super().append(data)

    def chunks(self, response, *, preserve_primary=False):
        self._session._io._check_process()
        return super().chunks(response, preserve_primary=preserve_primary)

    def lines(self, response):
        self._session._io._check_process()
        return super().lines(response)

    def report_incomplete(self, code):
        self._session._io._check_process()
        return super().report_incomplete(code)

    def audit_io(self, phase, payload):
        from .invocation_io import AuditPhase

        if phase is not AuditPhase.APPEND:
            raise ValueError("capture target accepts append only")
        return self._write(payload)

    def _reconcile_locked(self):
        io = self._session._io
        while self._tickets:
            ticket = self._tickets[0]
            outcome = io._result_locked(ticket, handoff=False)
            if outcome is None:
                break
            self._tickets.popleft()
            if self._snapshot is not None:
                continue
            if outcome.state == "ack":
                self._committed += outcome.accepted_bytes
                if outcome.accepted_bytes != ticket._size:
                    self._failed = True
            else:
                self._failed = True
                self._not_executed |= outcome.not_executed
                self._uncertain |= outcome.publication_uncertain
                if outcome.error is not None:
                    # The supervisor remains the sole owner of the exception
                    # and its charged traceback payload until handoff/discard.
                    self._fatal_ticket = ticket
        self._pending = len(self._tickets)

    def _raise_fatal_locked(self):
        # No callback or wait between transferring the retained error/payload
        # out of scheduler ownership and raising the original exception.
        ticket, self._fatal_ticket = self._fatal_ticket, None
        if ticket is not None:
            outcome = self._session._io._result_locked(ticket)
            if outcome.error is not None:
                raise outcome.error

    def _discard_locked(self):
        io = self._session._io
        # Close has cancelled pending work. Preserve known ACK/unknown metadata
        # before dropping tickets, without transferring any retained exception.
        self._reconcile_locked()
        for ticket in self._tickets:
            io._abandon_locked(ticket)
        self._tickets.clear()
        if self._fatal_ticket is not None:
            io._abandon_locked(self._fatal_ticket)
        self._fatal = self._fatal_ticket = None
        self._pending = 0

    def _notice(self, code):
        from .invocation_stream import CaptureUnavailable

        self._session._io.emit_warning()
        if self._session._io._blocked:
            code = "audit_io_unresolved_process"
        super()._notice(code)
        if self._strict:
            raise CaptureUnavailable(code)

    def seal(self, *, interrupted=False):
        self._session._io._check_process()
        seal_start = time.monotonic()
        acquired = self._wire.seal(interrupted=interrupted)
        lane, io = self._session, self._session._io
        with io._condition:
            if self._snapshot is not None:
                return self._snapshot
            if self._flush_deadline is None:
                self._flush_deadline = min(self._deadline, seal_start + lane._flush_timeout)
            self._flush = True
            for ticket in self._tickets:
                io._flush_locked(ticket, self._flush_deadline)
            self._reconcile_locked()
            while (
                self._tickets
                and not interrupted
                and not lane._disabled
                and not self._failed
                and self._snapshot is None
            ):
                remaining = self._effective_deadline() - time.monotonic()
                if remaining <= 0:
                    break
                io._condition.wait(remaining)
                self._reconcile_locked()
            if self._snapshot is not None:
                return self._snapshot
            if self._tickets or self._failed or interrupted:
                lane._disable()
                self._reconcile_locked()
            self._snapshot = _snapshot_from(
                acquired,
                accepted_bytes=self._committed,
                failed=acquired.failed or self._failed or bool(self._tickets),
                write_uncertain=acquired.write_uncertain or self._uncertain,
                not_executed=self._not_executed,
            )
            snapshot = self._snapshot
            io._release_capture_locked(lane._token)
            lane._parent._captures.discard(self)
            if self._fatal_ticket is not None:
                self._raise_fatal_locked()
        io.emit_warning()
        if snapshot.failed or snapshot.write_uncertain:
            self._notice(_failure_notice(self._scoped, snapshot))
        return snapshot
