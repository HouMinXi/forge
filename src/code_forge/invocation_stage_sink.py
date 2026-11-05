"""Internal WireCapture sink for recorder checkpoints, not acquired audit credit.

The caller begins the stage and owns later adoption. These checks validate the
recorder's acknowledgement contract; they do not independently prove storage
persistence or upgrade a capture snapshot to acquired usage.
"""

import hashlib
import threading
from typing import Any, Protocol

from code_forge.invocation_stream import CaptureSnapshot


class StageSinkUnavailable(RuntimeError):
    """A stage cannot safely accept bytes or be sealed."""


class _StageRecorder(Protocol):
    def append_stage(self, handle: Any, *, offset: int, data: bytes, action_deadline: float) -> Any: ...

    def seal_stage(self, handle: Any, *, checkpoint: Any, action_deadline: float) -> Any: ...

    def cancel_stage(self, handle: Any) -> Any: ...


class RecorderStageSink:
    """One capture writer, with cancellation allowed from a controller thread.

    ``write`` counts only validated, known checkpoint bytes. Cancellation and
    uncertain acknowledgements are terminal. No lock spans recorder I/O, and
    late results after cancellation cannot change the accepted prefix. Use
    ``sealed_checkpoint`` for owner-controlled adoption only after ``seal``.
    A sealed prefix need not be a complete transport body.
    """

    def __init__(self, recorder: _StageRecorder, begin: Any, *, action_deadline: float):
        self._recorder = recorder
        self._deadline = action_deadline
        self._lock = threading.Lock()
        self._handle = getattr(begin, "handle", None)
        self._identity = getattr(self._handle, "identity", None)
        self._hash = hashlib.sha256()
        self._accepted = 0
        self._checkpoint = None
        self._pending = self._cancelled = self._uncertain = self._sealed = False
        self._refused = False
        try:
            if (
                self._identity is None
                or begin.admitted is not True
                or begin.partial is not False
                or begin.publication_uncertain is not False
                or begin.fault is not None
                or type(begin.accepted_bytes) is not int
                or begin.accepted_bytes != 0
            ):
                raise ValueError
            if begin.checkpoint is not None:
                self._validate_checkpoint(begin.checkpoint, 0, self._hash.hexdigest())
                self._checkpoint = begin.checkpoint
        except (AttributeError, TypeError, ValueError):
            raise StageSinkUnavailable("invalid stage admission") from None

    def _validate_checkpoint(self, checkpoint, size, digest):
        if (
            checkpoint.identity != self._identity
            or type(checkpoint.retained_bytes) is not int
            or checkpoint.retained_bytes != size
            or checkpoint.sha256 != digest
        ):
            raise ValueError

    def _validate_ack(self, ack, *, size, digest, maximum, sealing=False):
        if (
            ack.handle is not self._handle
            or self._handle.identity != self._identity
            or ack.publication_uncertain is not False
            or type(ack.admitted) is not bool
            or type(ack.partial) is not bool
            or type(ack.accepted_bytes) is not int
            or (ack.admitted and (ack.fault is not None or (ack.partial and not sealing)))
            or not 0 <= ack.accepted_bytes <= maximum
        ):
            raise ValueError
        self._validate_checkpoint(ack.checkpoint, size, digest)

    @property
    def sealed_checkpoint(self):
        """Validated seal checkpoint, or None; never an adoption acknowledgement."""
        with self._lock:
            return self._checkpoint if self._sealed and not self._cancelled else None

    def _require_idle(self):
        if self._pending or self._cancelled or self._uncertain or self._sealed or self._refused:
            raise StageSinkUnavailable("stage unavailable")

    def write(self, data: bytes) -> int:
        if type(data) is not bytes:
            raise TypeError("stage input must be bytes")
        with self._lock:
            self._require_idle()
            if not data:
                return 0
            self._pending = True
            offset = self._accepted
        try:
            ack = self._recorder.append_stage(
                self._handle, offset=offset, data=data, action_deadline=self._deadline
            )
            with self._lock:
                if self._cancelled:
                    raise StageSinkUnavailable("stage cancelled")
                count = ack.accepted_bytes
                if type(count) is not int or not 0 <= count <= len(data):
                    raise ValueError
                # A known refusal confirms no new bytes, but may omit a
                # checkpoint. Keep the confirmed prefix, but do not assume
                # this non-quota failure leaves the recorder stage sealable.
                if ack.admitted is False and count == 0 and ack.checkpoint is None:
                    if (
                        ack.handle is not self._handle
                        or self._handle.identity != self._identity
                        or ack.publication_uncertain is not False
                        or type(ack.partial) is not bool
                    ):
                        raise ValueError
                    self._refused = True
                    return 0
                candidate = self._hash.copy()
                candidate.update(data[:count])
                self._validate_ack(
                    ack, size=offset + count, digest=candidate.hexdigest(), maximum=len(data)
                )
                if ack.partial and count == len(data):
                    raise ValueError
                if not (ack.admitted or ack.partial) and count:
                    raise ValueError
                self._hash = candidate
                self._accepted += count
                self._checkpoint = ack.checkpoint
                return count
        except MemoryError:
            with self._lock:
                self._uncertain = True
            raise
        except Exception:  # noqa: BLE001 - never expose recorder diagnostics/raw response
            with self._lock:
                self._uncertain = True
            raise StageSinkUnavailable("stage write unavailable") from None
        except BaseException:
            with self._lock:
                self._uncertain = True
            raise
        finally:
            with self._lock:
                self._pending = False

    def cancel(self):
        """Invalidate locally before issuing the recorder's nonblocking cancellation."""
        with self._lock:
            if self._cancelled:
                return
            self._cancelled = True
        try:
            self._recorder.cancel_stage(self._handle)
        except MemoryError:
            raise
        except Exception:  # noqa: BLE001 - cancellation stays terminal even on recorder failure
            raise StageSinkUnavailable("stage cancellation unavailable") from None

    def seal(self, snapshot: CaptureSnapshot):
        """Seal the matching capture prefix, without adopting or asserting completeness."""
        with self._lock:
            self._require_idle()
            if (
                not isinstance(snapshot, CaptureSnapshot)
                or snapshot.sealed is not True
                or snapshot.write_uncertain is not False
                or type(snapshot.accepted_bytes) is not int
                or snapshot.accepted_bytes != self._accepted
                or type(snapshot.observed_bytes) is not int
                or snapshot.observed_bytes < self._accepted
            ):
                raise StageSinkUnavailable("capture prefix unavailable")
            self._pending = True
            checkpoint = self._checkpoint
        try:
            ack = self._recorder.seal_stage(
                self._handle, checkpoint=checkpoint, action_deadline=self._deadline
            )
            with self._lock:
                if self._cancelled:
                    raise StageSinkUnavailable("stage cancelled")
                self._validate_ack(
                    ack, size=self._accepted, digest=self._hash.hexdigest(), maximum=0, sealing=True
                )
                if not ack.admitted:
                    raise ValueError
                # Seal admission confirms the checkpoint, not body completeness.
                # A capped stage legitimately seals with partial=True.
                self._checkpoint = ack.checkpoint
                self._sealed = True
                return self._checkpoint
        except MemoryError:
            with self._lock:
                self._uncertain = True
            raise
        except Exception:  # noqa: BLE001 - safe diagnostics only
            with self._lock:
                self._uncertain = True
            raise StageSinkUnavailable("stage seal unavailable") from None
        except BaseException:
            with self._lock:
                self._uncertain = True
            raise
        finally:
            with self._lock:
                self._pending = False
