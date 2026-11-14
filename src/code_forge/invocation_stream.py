"""Bounded raw acquisition adapter; sink acceptance is not a durable audit ACK."""

from dataclasses import dataclass
from http.client import IncompleteRead
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
            self._notice("capture_write_failed" if failed else "capture_limit_reached")

    def report_incomplete(self, code):
        """Report a bounded post-DONE drain without exposing payload data."""
        if code not in {"capture_tail_limit", "capture_tail_deadline", "capture_tail_interrupted"}:
            raise ValueError("invalid incomplete capture reason")
        with self._lock:
            if self._sealed:
                return
            if code == "capture_tail_limit":
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
        while True:
            with self._lock:
                if self._sealed:
                    return
            try:
                block = read(self._chunk)
            except IncompleteRead as error:
                if type(error.partial) is bytes:
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
