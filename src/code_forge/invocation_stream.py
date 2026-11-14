"""Bounded raw acquisition adapter; sink acceptance is not a durable audit ACK."""

from dataclasses import dataclass
from http.client import HTTPResponse, IncompleteRead
from types import MethodType
import threading
from typing import Callable


class CaptureUnavailable(RuntimeError):
    """Strict capture policy refused further decoding."""


@dataclass(frozen=True)
class CaptureSnapshot:
    observed_bytes: int
    accepted_bytes: int
    truncated: bool
    interrupted: bool
    failed: bool
    write_uncertain: bool
    eof_seen: bool
    sealed: bool
    not_executed: bool = False

    @property
    def complete(self) -> bool:
        """All received body bytes were accepted through actual transport EOF.

        This describes acquisition only. Sink acceptance is not independently
        a durable publication claim, and an SSE DONE marker is not HTTP EOF.
        """
        return (
            self.sealed
            and self.eof_seen
            and self.accepted_bytes == self.observed_bytes
            and not (self.truncated or self.interrupted or self.failed or self.write_uncertain)
        )


_CAPTURE_WRITE_FAILED = "capture_write_failed"
_CAPTURE_NOT_EXECUTED = "capture_not_executed"
_CAPTURE_WRITE_UNCERTAIN = "capture_write_uncertain"
_TERMINAL_CAPTURE_NOTICE = "audit_io_unresolved_process"
_CAPTURE_LIMIT_REACHED = "capture_limit_reached"
_CAPTURE_TAIL_LIMIT = "capture_tail_limit"
_CAPTURE_TAIL_DEADLINE = "capture_tail_deadline"
_CAPTURE_TAIL_INTERRUPTED = "capture_tail_interrupted"

# Returned by the claim when the code was already spent; distinct from the generic
# slot's own ``None`` key.
_CLAIM_LOST = object()
# The three post-DONE drain reasons, accepted as a closed set.
_CAPTURE_TAIL_CODES = frozenset((_CAPTURE_TAIL_LIMIT, _CAPTURE_TAIL_DEADLINE, _CAPTURE_TAIL_INTERRUPTED))

_SCOPED_CAPTURE_NOTICES = frozenset(
    (_CAPTURE_NOT_EXECUTED, _CAPTURE_WRITE_UNCERTAIN, _TERMINAL_CAPTURE_NOTICE)
)


def _fixed_capture_notice(code):
    """Return ``code`` when it is one of the closed refinements, otherwise ``None``.

    Exact string type keeps hostile objects and subclass overrides out of the
    membership test; callers apply their own scope decision on top.
    """
    return code if type(code) is str and code in _SCOPED_CAPTURE_NOTICES else None


class _CaptureNoticeDelivery:
    """Serialize bounded callbacks without holding a caller lock or waiting on one.

    The scope belongs to the owner, not to the call: it decides whether a fixed
    refinement is claimed under its own key or collapses into the single generic
    claim, and it is fixed for the lifetime of the outlet.
    """

    def __init__(self, *, scoped, warn):
        # The callback belongs to the outlet, not to a caller: a code queued by
        # one thread is delivered by whichever thread is draining, and it must
        # land in the same place either way.
        self._warn = warn
        self._lock = threading.Lock()
        self._scoped = scoped
        self._seen = set()
        self._pending = []  # (code, claimed key) pairs, in delivery order
        self._drain_owner = None

    @property
    def _draining(self):
        return self._drain_owner is not None

    def _claim_locked(self, fixed):
        """Under ``self._lock``: one generic notice, then each fixed refinement once.

        Returns the key this call now holds, or ``_CLAIM_LOST`` when the code was
        already spent. The key is what an interrupted drain has to hand back.
        """
        key = fixed if self._scoped else None
        if _TERMINAL_CAPTURE_NOTICE in self._seen or key in self._seen:
            return _CLAIM_LOST
        if key is None and self._seen:
            return _CLAIM_LOST
        self._seen.add(key)
        return key

    def emit(self, code):
        warn = self._warn
        token = threading.get_ident()
        # One derivation, through the exact-type gate: a caller-supplied str subclass
        # with a raising __eq__ must not reach a comparison, or it spends the slot
        # and kills the terminal with no callback.
        fixed = _fixed_capture_notice(code)
        terminal = fixed == _TERMINAL_CAPTURE_NOTICE
        primary = None
        released = False
        honoured = False
        current_key = _CLAIM_LOST
        queued_only = False
        try:
            with self._lock:
                key = self._claim_locked(fixed)
                if key is _CLAIM_LOST:
                    return
                if terminal:
                    for _, pending_key in self._pending:
                        self._seen.discard(pending_key)
                    self._pending.clear()
                self._pending.append((code, key))
                if self._drain_owner is None:
                    self._drain_owner = token
                else:
                    queued_only = True
            if queued_only:
                return
            while True:
                with self._lock:
                    if not self._pending:
                        self._drain_owner = None
                        released = True
                        break
                    current, current_key = self._pending.pop(0)
                # Spent before the callback runs, not after it returns: a signal
                # that lands between the two would otherwise hand the claim back
                # after the notice had already been delivered, and the retry would
                # deliver it twice. At most once beats at least once here.
                honoured = True
                try:
                    warn(current)
                except BaseException as error:  # noqa: BLE001 - drain queued terminal evidence before raising
                    if primary is None:
                        primary = error
        finally:
            # The claim, the arming and the drain all sit inside the try, so a signal
            # anywhere on that path finds its owner here. Release exactly the claims
            # this drain did not honour, and leave delivered ones spent.
            if not released and self._drain_owner is token:
                with self._lock:
                    if self._drain_owner is token:
                        self._drain_owner = None
                        if current_key is not _CLAIM_LOST and not honoured:
                            self._seen.discard(current_key)
                        for _, pending_key in self._pending:
                            self._seen.discard(pending_key)
                        self._pending.clear()
        if primary is not None:
            # Raised on the draining thread. A notice another thread queued while
            # this drain was active runs here, so its fatal surfaces here rather
            # than in its producer; only fatal-tier callback errors escape the
            # delivery wrappers, and the producer has already returned.
            raise primary


class WireCapture:
    """One reader writes; the controller may seal without waiting on sink I/O.

    ``write`` returns the number of bytes accepted, not a persistence assertion.
    A snapshot only covers its fixed accepted prefix. A write completing after
    sealing cannot extend that prefix. The sink owner must defer resource cleanup
    until its outstanding write finishes. No raw data appears in warnings.
    """

    def __init__(
        self,
        *,
        write: Callable[[bytes], int],
        warn: Callable[[str], None],
        limit_bytes: int,
        chunk_bytes: int = 65536,
        strict: bool = False,
    ):
        if (
            type(limit_bytes) is not int
            or limit_bytes < 0
            or type(chunk_bytes) is not int
            or chunk_bytes <= 0
            or type(strict) is not bool
            or not callable(write)
            or not callable(warn)
        ):
            raise ValueError("invalid capture configuration")
        self._write = write
        self._warn = warn
        self._limit = limit_bytes
        self._chunk = chunk_bytes
        self._strict = strict
        self._lock = threading.Lock()
        self._observed = self._accepted = 0
        self._truncated = self._interrupted = self._failed = False
        self._uncertain = self._eof = self._sealed = False
        self._warned = False
        self._bound = False
        self._writing = False

    def _notice(self, code):
        with self._lock:
            if self._sealed:
                return
            notify = not self._warned
            self._warned = True
        if notify:
            try:
                self._warn(code)
            except MemoryError:
                raise
            except Exception:  # noqa: BLE001 - reporting failure cannot replace review outcome
                with self._lock:
                    if not self._sealed:
                        self._failed = True
        if self._strict:
            raise CaptureUnavailable(code)

    def append(self, data):
        if type(data) is not bytes:
            raise TypeError("reader must return bytes")
        with self._lock:
            if self._sealed:
                return
            self._observed += len(data)
            if self._failed or self._truncated or self._uncertain:
                return
            piece = data[: max(0, self._limit - self._accepted)]
            overflow = len(piece) < len(data)
            self._truncated |= overflow
            self._writing = bool(piece)
        accepted = 0
        uncertain = False
        failed = False
        if piece:
            try:
                accepted = self._write(piece)
                if type(accepted) is not int or not 0 <= accepted <= len(piece):
                    uncertain = failed = True
                    accepted = 0
                else:
                    failed = accepted != len(piece)
            except MemoryError:
                with self._lock:
                    self._writing = False
                    self._uncertain = True
                raise
            except Exception:  # noqa: BLE001 - default audit failure does not change decoding
                uncertain = failed = True
            except BaseException:
                with self._lock:
                    self._writing = False
                    self._uncertain = True
                raise
        with self._lock:
            self._writing = False
            if self._sealed:
                return
            self._accepted += accepted
            self._failed |= failed
            self._uncertain |= uncertain
            self._truncated |= overflow
        if failed or overflow:
            self._notice(_CAPTURE_WRITE_FAILED if failed else _CAPTURE_LIMIT_REACHED)

    def report_incomplete(self, code):
        """Report a bounded post-DONE drain without exposing payload data."""
        if type(code) is not str or code not in _CAPTURE_TAIL_CODES:
            raise ValueError("invalid incomplete capture reason")
        with self._lock:
            if self._sealed:
                return
            if code == _CAPTURE_TAIL_LIMIT:
                self._truncated = True
            else:
                self._interrupted = True
        self._notice(code)

    def seal(self, *, interrupted=False):
        with self._lock:
            if not self._sealed:
                self._interrupted |= interrupted
                self._uncertain |= self._writing
                self._sealed = True
            return CaptureSnapshot(
                self._observed,
                self._accepted,
                self._truncated,
                self._interrupted,
                self._failed,
                self._uncertain,
                self._eof,
                self._sealed,
            )

    def chunks(self, response, *, preserve_primary=False):
        """Acquire fixed-size bytes before yielding them to a decoder."""
        with self._lock:
            if self._bound or self._sealed:
                raise ValueError("capture cannot be reused")
            self._bound = True
        read = getattr(response, "read1", None)
        if not callable(read):
            read = response.read
        native_chunk_read1 = (
            type(response) is HTTPResponse
            and isinstance(read, MethodType)
            and read.__self__ is response
            and read.__func__ is HTTPResponse.read1
        )
        while True:
            with self._lock:
                if self._sealed:
                    return
            # CPython read1 returns entity bytes before consuming the trailing
            # chunk CRLF on the next call. At that boundary IncompleteRead.partial
            # belongs to framing, not to the decoded body being captured.
            chunk_delimiter = (
                native_chunk_read1 and response.chunked is True and response.chunk_left == 0
            )
            try:
                block = read(self._chunk)
            except IncompleteRead as error:
                if type(error.partial) is bytes and not chunk_delimiter:
                    try:
                        self.append(error.partial)
                    except CaptureUnavailable:
                        # Preserve the already-observed transport failure. The
                        # capture's failed/truncated state remains inspectable.
                        pass
                raise
            if type(block) is not bytes or len(block) > self._chunk:
                raise ValueError("reader violated bounded acquisition contract")
            if not block:
                # HTTPResponse.read1/read(n), unlike read(), do not raise on
                # premature EOF against Content-Length. Preserve that check.
                remaining = getattr(response, "length", None)
                if type(remaining) is int and remaining > 0:
                    raise IncompleteRead(b"", remaining)
                with self._lock:
                    if self._sealed:
                        return
                    self._eof = True
                return
            try:
                self.append(block)
            except CaptureUnavailable:
                if not preserve_primary:
                    raise
            with self._lock:
                if self._sealed:
                    return
            yield block

    def lines(self, response):
        """Preserve byte-line decoding, without claiming bounded parser memory."""
        pending = bytearray()
        for block in self.chunks(response):
            pending.extend(block)
            while (end := pending.find(b"\n")) >= 0:
                with self._lock:
                    if self._sealed:
                        return
                line = bytes(pending[: end + 1])
                del pending[: end + 1]
                yield line
        with self._lock:
            if self._sealed:
                return
        if pending:
            yield bytes(pending)
