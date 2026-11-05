"""Bounded raw-only acquisition after SSE DONE, using one reader worker.

The tail bounds are engineering defaults, not a transport-completion promise.
Only the capture adapter's observed transport EOF establishes completion.
"""

from http.client import IncompleteRead
import logging
import socket
import threading
import time

from .invocation_stream import CaptureUnavailable


TAIL_SECONDS = 0.250
TAIL_BYTES = 64 * 1024


def is_provider_error_envelope(result):
    """Only the assembled error-only shape that _check_body_error rejects.

    This does not classify errors or authorize a successful response. The
    caller must still run its ordinary provider error classifier.
    """
    return (
        isinstance(result, dict)
        and set(result) == {"error"}
        and isinstance(result["error"], dict)
        and bool(result["error"])
    )


class _TailLimit(Exception):
    pass


class _TailDeadline(Exception):
    pass


class _Lines:
    def __init__(self, response, capture, deadline):
        self.deadline = deadline
        self.response = response
        self.pending = bytearray()
        self.remaining = None
        self.done_at = None
        self.eof = False
        self.blocks = iter(capture.chunks(self))
        self.read = getattr(response, "read1", None)
        if not callable(self.read):
            self.read = response.read

    @property
    def length(self):
        return getattr(self.response, "length", None)

    def read1(self, n):
        if self.remaining is not None:
            until = self.done_at + TAIL_SECONDS
            if self.deadline is not None:
                until = min(until, self.deadline)
            if time.monotonic() >= until:
                raise _TailDeadline
            if self.remaining <= 0:
                raise _TailLimit
            n = min(n, self.remaining)
        block = self.read(n)
        if self.remaining is not None:
            self.remaining -= len(block)
        return block

    def __iter__(self):
        return self

    def __next__(self):
        while (end := self.pending.find(b"\n")) < 0:
            try:
                self.pending.extend(next(self.blocks))
            except StopIteration:
                self.eof = True
                if not self.pending:
                    raise
                line = bytes(self.pending)
                self.pending.clear()
                return line
        line = bytes(self.pending[: end + 1])
        del self.pending[: end + 1]
        return line

    def done(self):
        self.done_at = time.monotonic()
        self.remaining = TAIL_BYTES - len(self.pending)

    def drain(self):
        # Pending bytes have already been captured. Never append them twice,
        # and never feed any post-DONE bytes back into the JSON parser.
        self.pending.clear()
        if self.remaining <= 0:
            raise _TailLimit
        for _ in self.blocks:
            pass


def read_captured_sse(response, capture, assemble, deadline, backend_name, error_type, idle_seconds):
    """Return parsed content after raw drain, or freeze an incomplete prefix.

    The controller never waits for a timed-out reader to finish. A late read or
    sink write cannot upgrade the sealed acquisition snapshot.
    """
    lines = _Lines(response, capture, deadline)
    ready, finished = threading.Event(), threading.Event()
    parsed, errors = [], []
    sock = None
    try:
        sock = response.fp.raw._sock
    except MemoryError:
        raise
    except Exception:  # noqa: BLE001, S110 - custom readers need not expose a socket
        pass

    def timeout_error():
        return error_type(
            "%s backend exceeded total read deadline" % backend_name,
            is_timeout=True,
            retryable=False,
        )

    if deadline is not None and time.monotonic() >= deadline:
        raise timeout_error()
    if sock is not None:
        idle = idle_seconds if deadline is None else min(idle_seconds, deadline - time.monotonic())
        try:
            sock.settimeout(max(0.001, idle))
        except MemoryError:
            raise
        except Exception as error:  # noqa: BLE001 - best-effort socket idle guard
            logging.warning("could not install idle timeout on %s socket: %s", backend_name, error)

    def worker():
        try:
            parsed.append(assemble(lines, deadline, backend_name, _on_done=lines.done))
            ready.set()
            if lines.done_at is not None:
                try:
                    lines.drain()
                except _TailLimit:
                    capture.report_incomplete("capture_tail_limit")
                except _TailDeadline:
                    capture.report_incomplete("capture_tail_deadline")
                except (OSError, EOFError, IncompleteRead):
                    capture.report_incomplete("capture_tail_interrupted")
        except TimeoutError:
            errors.append(timeout_error())
        except BaseException as error:  # noqa: BLE001 - propagate fatal/parser/transport errors
            errors.append(error)
        finally:
            ready.set()
            finished.set()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    def wait(event, until):
        return event.wait(None if until is None else max(0.0, until - time.monotonic()))

    def interrupt(code=None, *, primary=None):
        # Seal even if strict mode rejects the warning. Do not call close(): a
        # custom reader may hold the buffered-reader lock indefinitely.
        try:
            if code is not None:
                capture.report_incomplete(code)
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                capture.seal(interrupted=True)
            except BaseException as error:
                if primary is None:
                    primary = error
                    raise
            finally:
                if sock is not None:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    except BaseException:
                        if primary is None:
                            raise

    if not wait(ready, deadline):
        error = timeout_error()
        interrupt(primary=error)
        raise error
    if lines.done_at is not None and parsed:
        tail_deadline = lines.done_at + TAIL_SECONDS
        until = tail_deadline if deadline is None else min(deadline, tail_deadline)
        if not wait(finished, until):
            try:
                interrupt("capture_tail_deadline")
            except CaptureUnavailable:
                if not is_provider_error_envelope(parsed[0]):
                    raise
            return parsed[0]
    if errors:
        if not (
            parsed
            and is_provider_error_envelope(parsed[0])
            and isinstance(errors[0], CaptureUnavailable)
        ):
            raise errors[0]
    return parsed[0]
