"""Optional trusted-host boundary for individual OpenAI HTTP sends.

This consumes opaque host results, not recorder acknowledgements. A capture is
never adoption or recorded usage. Host controls must use bounded process I/O,
without recursively waiting on the same supervisor from its writer. Persisting
redirect attempts requires a compatible v2 owner adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import time
import urllib.request
from typing import Any, Protocol

from .invocation_redirect import _NoRedirect, open_with_redirects
from .invocation_stream import CaptureUnavailable


class SendLifecycleClosed(CaptureUnavailable):
    """The host lifecycle was revoked; never degrade into an unrecorded send."""

    def __init__(self):
        super().__init__("send lifecycle is closed")


class SendDeadlineBeforeNetwork(TimeoutError):
    """Host control used the shared budget before the transport was called."""

    def __init__(self):
        super().__init__("send deadline expired before network entry")


@dataclass(frozen=True)
class SendRequest:
    """Request identity, never a retained Request, URL, prompt or headers."""

    cause: str
    parent: Any = field(repr=False)
    request_digest: str
    request_bytes: int
    method: str
    backend_name: str
    model: str
    deadline: float


class SendAttempt(Protocol):
    def enter_send(self) -> bool:
        """Admit the local send boundary, not proof of provider receipt."""

    def new_response(self, *, status: int | None, redirect_hop: Any, deadline: float) -> Any:
        """Return a fresh shared capture for this attempt's sole wire body."""

    def finish(self, *, outcome: str, error_class: str | None, duration_s: float, usage: Any) -> bool:
        """True only after known on-time host settlement, never capture credit."""


class HostSendAudit(Protocol):
    strict: bool

    def start_send(self, request: SendRequest) -> SendAttempt:
        """Admit one pending send; it is still not_dispatched on return."""

    def warn(self, code: str) -> None: ...


class SendAuditScope:
    """One invocation's settled-parent chain, not shared across calls."""

    def __init__(self, host: HostSendAudit):
        if type(host.strict) is not bool:
            raise TypeError("host audit strict must be a bool")
        if not callable(host.start_send) or not callable(host.warn):
            raise TypeError("invalid host audit interface")
        self.host = host
        self.strict = host.strict
        self.disabled = False
        self.parent = None

    def _failed(self, *, primary=None):
        first = not self.disabled
        self.disabled = True
        if first:
            try:
                self.host.warn("audit_send_unavailable")
            except MemoryError:
                if primary is None:
                    raise
            except Exception:  # noqa: BLE001, S110 - warning errors are secondary, no raw logs
                pass
            except BaseException:  # noqa: BLE001 - preserve an active primary
                if primary is None:
                    raise
        if self.strict and primary is None:
            raise CaptureUnavailable("audit_send_unavailable") from None

    def _check_lifecycle(self):
        # Optional on older trusted adapters; a recording failure alone is not
        # lifecycle revocation. Check even when this scope already degraded.
        if getattr(self.host, "lifecycle_closed", False) is True:
            raise SendLifecycleClosed()

    def start(self, request, *, cause, backend_name, model, deadline):
        self._check_lifecycle()
        if self.disabled:
            if self.strict:
                raise CaptureUnavailable("audit_send_unavailable")
            return None
        data = request.data or b""
        digest = hashlib.sha256()
        digest.update(request.get_method().encode("utf-8"))
        digest.update(b"\0")
        digest.update(request.full_url.encode("utf-8"))
        digest.update(b"\0")
        digest.update(data)
        metadata = SendRequest(
            cause=cause,
            parent=self.parent,
            request_digest=digest.hexdigest(),
            request_bytes=len(data),
            method=request.get_method(),
            backend_name=backend_name,
            model=model,
            deadline=deadline,
        )
        try:
            attempt = self.host.start_send(metadata)
            if attempt is None:
                raise ValueError("host send was not admitted")
        except SendLifecycleClosed:
            raise
        except MemoryError:
            self.disabled = True
            raise
        except Exception:  # noqa: BLE001 - never expose host diagnostic data
            self._check_lifecycle()
            self._failed()
            return None
        return _ActiveSend(self, attempt)


class _ActiveSend:
    def __init__(self, scope, attempt):
        self.scope, self.attempt = scope, attempt
        self.started = time.monotonic()
        self.response_requested = False
        self.capture = None

    def enter(self):
        self.scope._check_lifecycle()
        if self.scope.disabled:
            return
        try:
            admitted = self.attempt.enter_send()
        except SendLifecycleClosed:
            raise
        except MemoryError:
            self.scope.disabled = True
            raise
        except Exception:  # noqa: BLE001 - default host failure preserves authorized send
            self.scope._check_lifecycle()
            self.scope._failed()
            return
        except BaseException:
            self.scope.disabled = True
            raise
        self.scope._check_lifecycle()
        if admitted is not True:
            self.scope._failed()

    def response(self, response, *, hop, deadline):
        if self.response_requested:
            raise RuntimeError("response capture cannot be reused")
        self.response_requested = True
        if self.scope.disabled:
            return None
        status = getattr(response, "status", getattr(response, "code", None))
        status = status if type(status) is int and 100 <= status <= 599 else None
        try:
            capture = self.attempt.new_response(status=status, redirect_hop=hop, deadline=deadline)
            if capture is None:
                raise ValueError("host response was not admitted")
        except MemoryError:
            self.scope.disabled = True
            raise
        except Exception:  # noqa: BLE001 - default host failure preserves decoding
            self.scope._failed()
            return None
        except BaseException:
            self.scope.disabled = True
            raise
        self.capture = capture
        return capture

    def finish(self, *, primary=None, usage=None):
        if self.scope.disabled:
            return
        try:
            settled = self.attempt.finish(
                outcome="completed" if primary is None else "failed",
                error_class=None if primary is None else type(primary).__name__,
                duration_s=max(0.0, time.monotonic() - self.started),
                usage=usage,
            )
        except BaseException as error:  # noqa: BLE001 - preserve active primary
            self.scope._failed(primary=primary if primary is not None else error)
            if primary is None and (isinstance(error, MemoryError) or not isinstance(error, Exception)):
                raise
            if primary is None and self.scope.strict:
                raise CaptureUnavailable("audit_send_unavailable") from None
            return
        if settled is not True:
            self.scope._failed(primary=primary)
            return
        self.scope.parent = self.attempt


class OpenAiSendTransport:
    """One format dispatch, with a new attempt for each physical redirect hop."""

    def __init__(self, scope, *, cause, backend_name, model, read_with_deadline):
        self.scope = scope
        self.cause, self.backend_name, self.model = cause, backend_name, model
        self._read = read_with_deadline
        self._transport = urllib.request.build_opener(_NoRedirect())
        self._active = None
        self._deadline = None
        self._opens = 0

    def open(self, request, *, deadline):
        self._deadline = deadline
        return open_with_redirects(
            request, deadline=deadline, capture_hop=self._capture_hop, opener=_SendOpener(self)
        )

    def response_capture(self, response):
        return (
            self._active.response(
                response, hop=getattr(response, "redirect_hop", None), deadline=self._deadline
            )
            if self._active is not None
            else None
        )

    def finish(self, *, primary=None, usage=None):
        active, self._active = self._active, None
        if active is not None:
            active.finish(primary=primary, usage=usage)

    def _capture_hop(self, response, hop, deadline):
        captured = (
            self._active.response(response, hop=hop, deadline=deadline)
            if self._active is not None
            else None
        )
        primary = None
        try:

            def drain():
                if captured is not None:
                    for _chunk in captured.chunks(response):
                        pass
                else:
                    while response.read(65536):
                        pass

            self._read(response, deadline, self.backend_name, read=drain)
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                if captured is not None:
                    captured.seal(interrupted=primary is not None)
            except BaseException as error:
                if primary is None:
                    primary = error
                    raise
            finally:
                # A normal redirect completes before the next actual send.
                self.finish(primary=primary)


class _SendOpener:
    def __init__(self, parent):
        self.parent = parent

    def open(self, request, *, timeout):
        parent = self.parent
        parent._active = parent.scope.start(
            request,
            cause=parent.cause if parent._opens == 0 else "redirect",
            backend_name=parent.backend_name,
            model=parent.model,
            deadline=parent._deadline,
        )
        if parent._deadline - time.monotonic() <= 0:
            raise SendDeadlineBeforeNetwork()
        if parent._active is not None:
            parent._active.enter()
        # Both admission and entered controls can consume the shared budget.
        # ENTERED is a local boundary, never proof that this call reached wire.
        remaining = parent._deadline - time.monotonic()
        if remaining <= 0:
            raise SendDeadlineBeforeNetwork()
        parent._opens += 1
        return parent._transport.open(request, timeout=remaining)
