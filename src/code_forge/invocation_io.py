"""Finite process-wide audit I/O, not a general-purpose executor or durable ACK.

Only trusted host adapters supply targets. Provider work never belongs here.
Unknown running I/O retains its worker and payload charge until it really exits.
"""

from collections import OrderedDict, deque
from dataclasses import dataclass, replace
from enum import Enum
import logging
import math
import os
import threading
import time

from code_forge.invocation_stream import _TERMINAL_CAPTURE_NOTICE


class AuditPhase(Enum):
    OPEN = "open"
    INPUT = "input_manifest"
    OPERATION = "register_operation"
    START = "start"
    ENTERED = "entered"
    BEGIN = "begin_stage"
    APPEND = "append"
    SEAL = "seal"
    ADOPT = "adopt"
    OBSERVE = "observe"
    FINISH = "finish"
    FINALIZE = "finalize"
    MAINTAIN = "maintenance"


@dataclass(frozen=True)
class IoLimits:
    operations: int
    payload_bytes: int
    batch_bytes: int
    captures: int

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in vars(self).values()):
            raise ValueError("invalid audit I/O limits")
        if self.batch_bytes > self.payload_bytes // 2:
            raise ValueError("audit I/O needs a batch copy reserve")


@dataclass(frozen=True)
class IoOutcome:
    state: str
    value: object = None
    accepted_bytes: int = 0
    not_executed: bool = False
    publication_uncertain: bool = False
    error: BaseException | None = None


class IoTicket:
    """Opaque one-shot result; a queued ticket is not a storage acknowledgement."""

    def __init__(
        self, supervisor, target, phase, deadline, ready_at, capture, batch_bytes, *, _key=None
    ):
        if _key is not _KEY:
            raise ValueError("audit I/O tickets are supervisor-owned")
        self._io = supervisor
        self._identity = supervisor._identity
        self._target = target
        self._phase = phase
        self._deadline = deadline
        self._ready_at = ready_at
        self._capture = capture
        self._batch_bytes = batch_bytes
        self._pieces = []
        self._size = self._charge = 0
        self._running = False
        self._outcome = None
        self._handoff_requested = False


_KEY = object()
_PROCESS = None
_PROCESS_LOCK = threading.Lock()


def _warn(code):
    logging.warning("invocation audit I/O unavailable: %s", code)


def _deadline(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("invalid audit I/O deadline")
    return value


def process_audit_io(*, limits, warn=_warn):
    """One immutable supervisor per PID; close/failure never replaces its worker."""
    global _PROCESS  # noqa: PLW0603 - this singleton must outlive every review session
    if _PROCESS is not None:
        _PROCESS._check_process()
    with _PROCESS_LOCK:
        if _PROCESS is None:
            _PROCESS = ProcessAuditIo(limits=limits, warn=warn, _key=_KEY)
        elif _PROCESS.limits != limits:
            raise ValueError("process audit I/O limits already fixed")
        return _PROCESS


class ProcessAuditIo:
    """One fixed lazy worker, bounded tickets/bytes, FIFO within each target.

    Targets implement audit_io(phase, payload). They are trusted host adapters,
    never commands or callbacks selected by source/model/provider data. Control
    arguments belong to a bounded adapter, not an uncharged payload closure.
    """

    def __init__(self, *, limits, warn, _key=None):
        if _key is not _KEY or type(limits) is not IoLimits or not callable(warn):
            raise ValueError("use process_audit_io with explicit limits")
        self.limits = limits
        self._warn = warn
        self._pid = os.getpid()
        self._identity = object()
        self._condition = threading.Condition()
        self._lanes = OrderedDict()
        self._errors = set()
        self._captures = {}
        self._operations = self._retained = 0
        self._running = self._worker = self._handoff = None
        self._closed = self._blocked = self._start_failed = False
        self._warning_pending = False

    def _check_process(self):
        # A child can inherit a lock held by a thread absent after fork.
        if os.getpid() != self._pid:
            raise RuntimeError("inherited audit I/O supervisor is unavailable")

    @property
    def retained_bytes(self):
        self._check_process()
        with self._condition:
            return self._retained

    @property
    def unavailable(self):
        self._check_process()
        with self._condition:
            self._expire_locked()
            unavailable = self._closed or self._blocked or self._start_failed or os.getpid() != self._pid
        self.emit_warning()
        return unavailable

    def emit_warning(self):
        self._check_process()
        with self._condition:
            pending = self._warning_pending
            self._warning_pending = False
        if pending:
            try:
                self._warn(_TERMINAL_CAPTURE_NOTICE)
            except MemoryError:
                raise
            except Exception:  # noqa: BLE001 - warning text cannot replace review outcome
                return

    def register_capture(self):
        self._check_process()
        with self._condition:
            self._expire_locked()
            if (
                self._closed
                or self._blocked
                or self._start_failed
                or os.getpid() != self._pid
                or len(self._captures) >= self.limits.captures
            ):
                token = None
            else:
                token = object()
                self._captures[token] = [0, False]
        self.emit_warning()
        return token

    def _release_capture_locked(self, token):
        state = self._captures.get(token)
        if state is not None:
            state[1] = True
            if state[0] == 0:
                del self._captures[token]

    def release_capture(self, token):
        self._check_process()
        with self._condition:
            self._release_capture_locked(token)

    def _uncharge_locked(self, ticket):
        self._retained -= ticket._charge
        ticket._charge = 0
        ticket._pieces.clear()

    def _complete_locked(self, ticket, outcome, *, retain_error=False):
        ticket._outcome = outcome
        self._operations -= 1
        if not retain_error:
            self._uncharge_locked(ticket)
        else:
            self._errors.add(ticket)
        token = ticket._capture
        if token in self._captures:
            state = self._captures[token]
            state[0] -= 1
            if state == [0, True]:
                del self._captures[token]
        ticket._target = None
        self._condition.notify_all()

    def _drop_queued_locked(self, outcome):
        lanes, self._lanes = self._lanes, OrderedDict()
        for queue in lanes.values():
            while queue:
                self._complete_locked(queue.popleft(), outcome)

    def _unknown_locked(self, ticket):
        if ticket._outcome is not None:
            return
        ticket._outcome = IoOutcome("unknown", publication_uncertain=True)
        self._blocked = True
        self._warning_pending = True
        self._drop_queued_locked(IoOutcome("unavailable", not_executed=True))
        self._condition.notify_all()

    def _expire_locked(self):
        now = time.monotonic()
        if self._handoff is not None and now >= self._handoff._deadline:
            self._release_handoff_locked(self._handoff)
        if self._running is not None and now >= self._running._deadline:
            self._unknown_locked(self._running)
        for key in list(self._lanes):
            queue = self._lanes.get(key)
            if queue is None:
                continue
            retained = deque()
            while queue:
                ticket = queue.popleft()
                if now >= ticket._deadline:
                    self._complete_locked(ticket, IoOutcome("expired", not_executed=True))
                else:
                    retained.append(ticket)
            if retained:
                self._lanes[key] = retained
            else:
                self._lanes.pop(key, None)

    def _submit_locked(
        self,
        target,
        phase,
        *,
        deadline,
        payload=b"",
        ready_at=0.0,
        capture=None,
        batch_bytes=None,
        handoff=False,
    ):
        if type(phase) is not AuditPhase or type(payload) is not bytes:
            raise TypeError("invalid audit I/O command")
        if type(handoff) is not bool or (
            handoff and phase not in (AuditPhase.ADOPT, AuditPhase.OBSERVE)
        ):
            raise ValueError("invalid audit I/O handoff")
        _deadline(deadline)
        _deadline(ready_at)
        if phase is not AuditPhase.APPEND and payload:
            raise ValueError("control payload must be held by its bounded host adapter")
        batch_bytes = self.limits.batch_bytes if batch_bytes is None else batch_bytes
        if type(batch_bytes) is not int or not 0 < batch_bytes <= self.limits.batch_bytes:
            raise ValueError("invalid operation batch size")
        self._expire_locked()
        if phase is AuditPhase.APPEND and not payload:
            # Empty acquisition cannot consume a queue entry or grow a coalesced
            # fragment list without spending either byte or operation credit.
            ticket = IoTicket(self, target, phase, deadline, ready_at, capture, batch_bytes, _key=_KEY)
            ticket._target = None
            ticket._outcome = IoOutcome("empty", not_executed=True)
            return ticket
        rejected = (
            self._closed
            or self._blocked
            or self._start_failed
            or os.getpid() != self._pid
            or time.monotonic() >= deadline
            or (capture is not None and (capture not in self._captures or self._captures[capture][1]))
            or len(payload) > batch_bytes
            or len(payload) > self.limits.payload_bytes - self.limits.batch_bytes - self._retained
        )
        key = id(target)
        queue = self._lanes.get(key)
        last = queue[-1] if queue else None
        if (
            not rejected
            and phase is AuditPhase.APPEND
            and last is not None
            and last._phase is phase
            and last._capture is capture
            and last._deadline == deadline
            and last._batch_bytes == batch_bytes
            and last._size + len(payload) <= batch_bytes
        ):
            last._pieces.append(payload)
            last._size += len(payload)
            last._charge += len(payload)
            self._retained += len(payload)
            self._condition.notify_all()
            return last
        ticket = IoTicket(self, target, phase, deadline, ready_at, capture, batch_bytes, _key=_KEY)
        ticket._handoff_requested = handoff
        used = self._operations + len(self._errors) + (self._handoff is not None)
        full = used >= self.limits.operations
        # Keep one bounded control slot out of reach of append flooding.
        full |= phase is AuditPhase.APPEND and used >= self.limits.operations - 1
        if rejected or full:
            ticket._target = None
            ticket._outcome = IoOutcome("unavailable", not_executed=True)
            return ticket
        ticket._pieces.append(payload)
        ticket._size = ticket._charge = len(payload)
        self._retained += len(payload)
        self._operations += 1
        if capture is not None:
            self._captures[capture][0] += 1
        self._lanes.setdefault(key, deque()).append(ticket)
        if self._worker is None:
            self._worker = threading.Thread(target=self._run, name="forge-audit-io", daemon=True)
            try:
                self._worker.start()
            except BaseException:
                self._start_failed = True
                self._drop_queued_locked(IoOutcome("unavailable", not_executed=True))
                raise
        self._condition.notify_all()
        return ticket

    def submit(self, target, phase, *, deadline, payload=b"", ready_at=0.0, handoff=False):
        self._check_process()
        if not callable(getattr(target, "audit_io", None)):
            raise TypeError("invalid audit I/O target")
        with self._condition:
            result = self._submit_locked(
                target, phase, deadline=deadline, payload=payload, ready_at=ready_at, handoff=handoff
            )
        self.emit_warning()
        return result

    def _cancel_locked(self, ticket):
        if (
            type(ticket) is not IoTicket
            or ticket._io is not self
            or ticket._identity is not self._identity
        ):
            raise ValueError("foreign audit I/O ticket")
        if ticket._outcome is not None:
            self._release_handoff_locked(ticket)
            return
        if ticket._running:
            self._unknown_locked(ticket)
            return
        queue = self._lanes.get(id(ticket._target))
        if queue is not None:
            queue.remove(ticket)
            if not queue:
                del self._lanes[id(ticket._target)]
        self._complete_locked(ticket, IoOutcome("cancelled", not_executed=True))

    def cancel(self, ticket):
        self._check_process()
        with self._condition:
            self._cancel_locked(ticket)
        self.emit_warning()

    def _release_handoff_locked(self, ticket):
        if self._handoff is ticket:
            self._handoff = None
            self._condition.notify_all()

    def release_handoff(self, ticket):
        """Release scheduling ownership after local ACK bookkeeping.

        This does not change the storage outcome or extend its original deadline.
        An ACK gate is one bounded operation slot, never retained payload credit.
        """
        self._check_process()
        with self._condition:
            if (
                type(ticket) is not IoTicket
                or ticket._io is not self
                or ticket._identity is not self._identity
            ):
                raise ValueError("foreign audit I/O ticket")
            if ticket._outcome is None:
                raise ValueError("audit I/O handoff has not completed")
            self._release_handoff_locked(ticket)

    def _result_locked(self, ticket, *, handoff=True):
        if (
            type(ticket) is not IoTicket
            or ticket._io is not self
            or ticket._identity is not self._identity
        ):
            raise ValueError("foreign audit I/O ticket")
        self._expire_locked()
        outcome = ticket._outcome
        if handoff and outcome is not None and outcome.error is not None:
            self._discard_error_locked(ticket)
        return outcome

    def _discard_error_locked(self, ticket):
        if ticket in self._errors:
            ticket._outcome = replace(ticket._outcome, error=None)
            self._errors.remove(ticket)
            self._uncharge_locked(ticket)

    def _abandon_locked(self, ticket):
        # Cancellation of running unknown work deliberately keeps its charge.
        self._cancel_locked(ticket)
        self._discard_error_locked(ticket)

    def abandon(self, ticket):
        """Cancel pending work and discard an unclaimed completed fatal result.

        Unlike poll/wait, this transfers no exception to the caller. A running
        unknown operation keeps its bytes until its actual function returns.
        """
        self._check_process()
        with self._condition:
            self._abandon_locked(ticket)
        self.emit_warning()

    def poll(self, ticket):
        self._check_process()
        with self._condition:
            outcome = self._result_locked(ticket)
        self.emit_warning()
        return outcome

    def wait(self, ticket):
        self._check_process()
        with self._condition:
            while (outcome := self._result_locked(ticket)) is None:
                self._condition.wait(max(0.0, ticket._deadline - time.monotonic()))
        self.emit_warning()
        return outcome

    def _flush_locked(self, ticket, deadline):
        ticket._deadline = min(ticket._deadline, deadline)
        ticket._ready_at = 0.0
        self._expire_locked()
        self._condition.notify_all()

    def close(self):
        self._check_process()
        with self._condition:
            self._closed = True
            if self._handoff is not None:
                self._release_handoff_locked(self._handoff)
            if self._running is not None:
                self._unknown_locked(self._running)
            self._drop_queued_locked(IoOutcome("closed", not_executed=True))
            for ticket in list(self._errors):
                self._discard_error_locked(ticket)
            for token in list(self._captures):
                self._release_capture_locked(token)
            self._condition.notify_all()
        self.emit_warning()

    def _next_locked(self):
        self._expire_locked()
        now, delay = time.monotonic(), None
        if self._handoff is not None:
            return None, max(0.0, self._handoff._deadline - now)
        for key in list(self._lanes):
            ticket = self._lanes[key][0]
            remaining = min(ticket._ready_at, ticket._deadline) - now
            if ticket._size >= ticket._batch_bytes or remaining <= 0:
                queue = self._lanes.pop(key)
                queue.popleft()
                if queue:
                    self._lanes[key] = queue
                ticket._running = True
                self._running = ticket
                return ticket, 0.0
            delay = remaining if delay is None else min(delay, remaining)
        return None, delay

    def _run(self):
        while True:
            data = error = value = ticket = outcome = None
            with self._condition:
                while not self._closed:
                    ticket, delay = self._next_locked()
                    if ticket is not None:
                        break
                    self._condition.wait(delay)
                if ticket is None:
                    return
                # Enqueue reserves room for this single worker's join copy.
                self._retained += self.limits.batch_bytes
                try:
                    data, error = _join_pieces(ticket)
                finally:
                    self._retained -= self.limits.batch_bytes
            if error is None:
                value, error = _call_target(ticket._target, ticket._phase, data, ticket._size)
            with self._condition:
                self._expire_locked()
                key = id(ticket._target)
                if error is not None or (ticket._phase is AuditPhase.APPEND and value != ticket._size):
                    queue = self._lanes.pop(key, ())
                    for queued in queue:
                        self._complete_locked(queued, IoOutcome("failed", not_executed=True))
                fatal = error is not None and (
                    isinstance(error, MemoryError) or not isinstance(error, Exception)
                )
                if ticket._outcome is None:
                    if error is None:
                        count = value if ticket._phase is AuditPhase.APPEND else 0
                        outcome = IoOutcome("ack", value=value, accepted_bytes=count)
                    else:
                        outcome = IoOutcome(
                            "failed", publication_uncertain=True, error=error if fatal else None
                        )
                    if ticket._handoff_requested and outcome.state == "ack":
                        self._handoff = ticket
                    self._complete_locked(ticket, outcome, retain_error=fatal)
                else:
                    # A timed-out write may have executed. Its late result is never credit.
                    self._complete_locked(ticket, ticket._outcome)
                self._running = None
                self._condition.notify_all()
            # Drop raw data, target/ACK and traceback references before waiting.
            data = error = value = ticket = outcome = None


def _join_pieces(ticket):
    # Completed fatal tracebacks must not retain the reusable worker loop frame.
    try:
        return b"".join(ticket._pieces), None
    except BaseException as error:  # noqa: BLE001 - original fatal identity is handed to its controller
        return None, error
    finally:
        ticket._pieces.clear()


def _call_target(target, phase, data, size):
    try:
        value = target.audit_io(phase, data)
        if phase is AuditPhase.APPEND and (type(value) is not int or not 0 <= value <= size):
            raise ValueError("invalid audit append acknowledgement")
        return value, None
    except BaseException as error:  # noqa: BLE001 - safe result metadata, fatal identity kept separately
        return None, error
