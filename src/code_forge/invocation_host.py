"""Lazy trusted-host consumer of the owner, stage bridge and finite audit I/O.

No CLI entry point is wired by this module. Raw recording is private/local; the
public summary is a closed metadata projection, not a recorder snapshot. Pure
storage-boundary tests do not qualify a filesystem for durable recording.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timezone
from functools import wraps
import json
import math
import os
from pathlib import Path
import re
import sys
import threading
import time

from .invocation_audit import (
    BackendObservation,
    InvocationContext,
    UsageObservation,
    maintain_audit_root,
)
from .invocation_buffered_capture import SharedBufferedCaptureSession
from .invocation_io import AuditPhase, IoLimits, process_audit_io
from .invocation_owner import InvocationOwner
from .invocation_send_audit import SendLifecycleClosed, SendRequest
from .invocation_stream import _CaptureNoticeDelivery, _fixed_capture_notice


_DEFAULT_IO = IoLimits(operations=32, payload_bytes=8 * 1024 * 1024, batch_bytes=65536, captures=16)
_WARNING = "invocation_audit_unavailable"
_CAUSES = frozenset(
    ("initial", "retry", "redirect", "correction", "continuation", "headroom", "excerpt_repair", "tool")
)


def _utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _process_warning(_code):
    # Each affected host emits its own safe, one-shot warning. Never retain a
    # bound host callback in the process-lifetime supervisor singleton.
    return None


def _warning(code):
    """Default renderer: generic text, plus the code only for a closed refinement.

    Membership alone is the right test here because the caller decides scope
    before forwarding: ``HostAuditSession.warn`` sends the generic constant when
    the fault domain is not scoped. A second caller passing a raw capture code
    has to apply that same gate first, or the default text becomes scoped text.
    """
    import sys

    suffix = f" [{code}]" if _fixed_capture_notice(code) else ""
    print(
        "code-forge: invocation audit unavailable; recording is incomplete" + suffix,
        file=sys.stderr,
    )


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _text(value, bound=256):
    return type(value) is str and len(value) <= bound and "\0" not in value


def _metadata_copy(value, *, limit):
    """Bound before copying/serializing, including container/object count.

    Only private metadata (never a prompt, headers, Request or raw body) may
    enter this helper. Retained encoded copies are separately charged.
    """
    nodes, chars = 0, 0

    def visit(item, depth=0):
        nonlocal nodes, chars
        nodes += 1
        if nodes > 8192 or depth > 8:
            raise ValueError("audit metadata exceeds capacity")
        if item is None or type(item) is bool:
            return
        if type(item) is int and abs(item) <= (1 << 63) - 1:
            return
        if type(item) is str:
            chars += len(item)
            if chars > limit:
                raise ValueError("audit metadata exceeds capacity")
            return
        if type(item) in (list, tuple):
            if len(item) > 1024:
                raise ValueError("audit metadata exceeds capacity")
            for child in item:
                visit(child, depth + 1)
            return
        if type(item) is dict:
            if len(item) > 1024 or any(type(key) is not str for key in item):
                raise ValueError("invalid audit metadata")
            for key, child in item.items():
                visit(key, depth + 1)
                visit(child, depth + 1)
            return
        raise TypeError("invalid audit metadata")

    visit(value)
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > limit:
        raise ValueError("audit metadata exceeds capacity")
    return encoded


@dataclass(frozen=True)
class HostAuditLimits:
    """Engineering defaults; disk quotas remain the persisted v2 policy."""

    control_timeout_s: float = 2.0
    flush_timeout_s: float = 2.0
    max_delay_s: float = 0.01
    max_sends: int = 1024
    max_epochs: int = 64
    max_observations_per_send: int = 32
    control_slots: int = 32
    control_bytes: int = 1024 * 1024
    manifest_bytes: int = 256 * 1024
    response_bytes: int = 8 * 1024 * 1024
    capture_buffer_bytes: int = 1024 * 1024
    batch_bytes: int = 65536

    def __post_init__(self):
        for key in ("control_timeout_s", "flush_timeout_s", "max_delay_s"):
            if not _finite(getattr(self, key)):
                raise ValueError("invalid host audit limits")
        for key in (
            "max_sends",
            "max_epochs",
            "max_observations_per_send",
            "control_slots",
            "control_bytes",
            "manifest_bytes",
            "response_bytes",
            "capture_buffer_bytes",
            "batch_bytes",
        ):
            if type(getattr(self, key)) is not int or getattr(self, key) <= 0:
                raise ValueError("invalid host audit limits")
        if (
            self.max_epochs > 64
            or self.response_bytes > 8 * 1024 * 1024
            or self.manifest_bytes > 256 * 1024
            or self.control_bytes < 2 * self.manifest_bytes
            or self.batch_bytes > self.capture_buffer_bytes // 2
        ):
            raise ValueError("invalid host audit limits")


@dataclass(frozen=True)
class HostAuditSummary:
    state: str
    audit_complete: bool
    source_known: bool
    admitted_sends: int
    settled_sends: int
    adopted_responses: int
    accepted_bytes: int
    publication_uncertain: bool
    usage_known: bool = False
    fault_code: str | None = None


class _Unavailable(Exception):
    pass


class _Control:
    """Fixed dispatch, not a user-selected callback or uncharged closure."""

    def __init__(self, host, phase, command, arguments, charge, deadline):
        self.host, self.phase, self.command = host, phase, command
        self.arguments, self.charge, self.deadline = arguments, charge, deadline
        self.released = self.done = self.abandoned = False

    def release(self):
        with self.host._lock:
            if not self.released:
                self.released = True
                self.arguments = None
                self.host._commands.discard(self)
                self.host._control_bytes -= self.charge

    def abandon(self, *, not_executed=False):
        with self.host._lock:
            self.abandoned = True
            releasable = self.done or not_executed
        if releasable:
            self.release()

    def audit_io(self, phase, payload):
        if phase is not self.phase or payload:
            raise ValueError("invalid host audit command")
        try:
            return self.host._execute(self.command, self.arguments, self.deadline)
        finally:
            with self.host._lock:
                self.done = True
                abandoned = self.abandoned
            # Completed fatal tracebacks remain charged until the controller
            # actually claims/discards them. Unknown work releases on real exit.
            if abandoned:
                self.release()


def _user_state_roots(environ):
    """Pure selection only. No filesystem inspection, creation or fallback I/O."""
    state = environ.get("XDG_STATE_HOME")
    if state is None or state == "":
        home = environ.get("HOME")
        if type(home) is not str or not home:
            raise ValueError("invalid state directory")
        state = home
        suffix = (".local", "state")
    else:
        suffix = ()
    if type(state) is not str or len(state) > 4000 or "\x00" in state:
        raise ValueError("invalid state directory")
    path = Path(state)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("invalid state directory")
    base = path.joinpath(*suffix, "code-forge")
    return base / "invocations-v2", base / "invocations-v1"


def _controller(method):
    """Count public send controllers across waits, not just queued commands."""

    @wraps(method)
    def invoke(self, *args, **kwargs):
        scope = self if isinstance(self, HostSendAudit) else self._scope
        host = scope._session
        host._check_process()
        with host._lock:
            if host._closed or host._finalize_started:
                raise SendLifecycleClosed()
            denied = host._transition or scope._generation != host._generation
            if not denied:
                host._controllers += 1
        if denied:
            host._failure()
            return None
        try:
            return method(self, *args, **kwargs)
        finally:
            with host._lock:
                host._controllers -= 1

    return invoke


class HostAuditSession:
    """One lazy review lifecycle; scope() creates small explicit send views.

    Constructor performs no storage I/O and starts no thread. Unknown source
    uses None, never the identity of an empty diff. Caller owns finalization.
    """

    _scoped_fault_domain: bool

    def __init__(
        self,
        *,
        artifact_root: Path,
        context: InvocationContext,
        manifest: dict | None = None,
        strict=False,
        limits=None,
        supervisor=None,
        warn=_warning,
        job_deadline=None,
        legacy_root: Path | None = None,
        scoped_fault_domain: bool = False,
    ):
        if type(context) is not InvocationContext or type(strict) is not bool or not callable(warn):
            raise TypeError("invalid host audit configuration")
        if type(scoped_fault_domain) is not bool:
            raise TypeError("invalid scoped fault domain switch")
        if job_deadline is not None and not _finite(job_deadline):
            raise ValueError("invalid host deadline")
        if (context.source_hash is None) != (context.snapshot_id is None):
            raise ValueError("invalid source identity")
        if context.source_hash is None and manifest is not None:
            raise ValueError("unknown source cannot have a known input manifest")
        if context.source_hash is not None and manifest is None:
            raise ValueError("known source requires its input manifest")
        self.limits = limits or HostAuditLimits()
        if type(self.limits) is not HostAuditLimits:
            raise TypeError("invalid host limits")
        # Context is closed metadata. Validate boundedness before retaining it.
        _metadata_copy({f.name: getattr(context, f.name) for f in fields(context)}, limit=4096)
        self._manifest = (
            _metadata_copy(manifest, limit=self.limits.manifest_bytes) if manifest is not None else None
        )
        self._root = Path(artifact_root) if artifact_root is not None else None
        self._context = context
        self._maintenance_roots = (
            (self._root,) if legacy_root is None else (self._root, Path(legacy_root))
        )
        if len(str(self._root)) > 4096:
            raise ValueError("audit root exceeds capacity")
        self.strict, self._warn = strict, warn
        self._scoped_fault_domain = scoped_fault_domain
        self._job_deadline, self._pid = job_deadline, os.getpid()
        self._io, self._captures, self._owner = supervisor, None, None
        self._lock, self._open_gate = threading.Lock(), threading.Lock()
        self._commands, self._attempts = set(), set()
        self._control_bytes = 0
        self._opened = self._disabled = self._closed = False
        self._notices = _CaptureNoticeDelivery(scoped=scoped_fault_domain)
        self._control_deadline = float("-inf")
        self._uncertain = self._complete = self._finalized = False
        self._reserved = self._started = self._settled = self._adopted = self._accepted = 0
        self._finalize_started = False
        self._maintenance_done = False
        self._managed_roots = False
        self._generation, self._epochs, self._controllers = 0, 1, 0
        self._transition = False

    @classmethod
    def from_user_state(cls, *, context, manifest=None, environ=None, **options):
        """Construct the real lazy host with fixed user-level v2/v1 roots.

        Existing custom roots are not discovered, migrated or quota-managed.
        A bad path disables this session, never selects another writable root.
        Native filesystem/OS admission remains the recorder's responsibility.
        """
        try:
            root, legacy = _user_state_roots(os.environ if environ is None else environ)
        except (TypeError, ValueError):
            host = cls(artifact_root=None, context=context, manifest=manifest, **options)
            host._failure()
            return host
        host = cls(artifact_root=root, legacy_root=legacy, context=context, manifest=manifest, **options)
        host._managed_roots = True
        return host

    def _check_process(self):
        if os.getpid() != self._pid:
            raise RuntimeError("inherited host audit session is unavailable")

    def _sequence_deadline(self, steps):
        """Budget for a run of controls: one window each, bounded by the job.

        A caller that hands one window to a sequence spends it on the first
        control; when that control stalls the rest inherit an expired deadline.
        The sequence is therefore budgeted for the controls it will issue.
        """
        result = time.monotonic() + steps * self.limits.control_timeout_s
        return min(result, self._job_deadline) if self._job_deadline is not None else result

    def _deadline(self, deadline):
        result = min(deadline, time.monotonic() + self.limits.control_timeout_s)
        return min(result, self._job_deadline) if self._job_deadline is not None else result

    def warn(self, code):
        self._check_process()
        with self._lock:
            self._disabled = True
        public_code = _WARNING
        if self._scoped_fault_domain:
            public_code = _fixed_capture_notice(code) or _WARNING
        self._notices.emit(public_code, warn=self._deliver_notice)

    def _deliver_notice(self, code):
        try:
            self._warn(code)
        except MemoryError:
            raise
        except Exception:  # noqa: BLE001, S110 - diagnostics never replace review outcome
            pass

    def _failure(self, *, uncertain=False, primary=None):
        with self._lock:
            self._disabled = True
            self._uncertain |= uncertain
        try:
            self.warn(_WARNING)
        except BaseException:
            if primary is None:
                raise
        if self.strict and primary is None:
            from .llm_invoke import AuditPersistenceError

            raise AuditPersistenceError() from None

    def _ensure_io(self):
        if self._io is None:
            self._io = process_audit_io(limits=_DEFAULT_IO, warn=_process_warning)
        if self._captures is None:
            self._captures = SharedBufferedCaptureSession(
                self._io,
                buffer_bytes=self.limits.capture_buffer_bytes,
                batch_bytes=self.limits.batch_bytes,
                max_delay=self.limits.max_delay_s,
                flush_timeout_s=self.limits.flush_timeout_s,
                scoped_fault_domain=self._scoped_fault_domain,
            )

    def _abandon_control(self, ticket, target):
        """Release a control even when the supervisor raises while abandoning.

        ``abandon()`` only releases when the command finished or provably never
        ran. Without the ``finally`` a raising abandon leaves the target never
        told it was abandoned, so its charge stays for the life of the session
        and later control calls fail against a budget nothing is using.
        """
        io = self._io
        if io is None:
            raise _Unavailable()
        try:
            io.abandon(ticket)
        finally:
            target.abandon(not_executed=ticket._outcome is not None and ticket._outcome.not_executed)

    def _request(self, phase, command, arguments, deadline, *, charge=4096):
        self._check_process()
        self._ensure_io()
        if threading.current_thread() is self._io._worker:
            raise _Unavailable()
        start = time.monotonic()
        caller_deadline = deadline
        deadline = self._deadline(deadline)
        window = max(0.0, deadline - start)
        outcome = None
        # Replaying a provably unexecuted ticket cannot apply work twice. A
        # replay may renew the per-control window only within the original
        # caller budget; an action or sequence deadline is never extended.
        for attempt in (0, 1):
            if attempt:
                grace = min(window, self.limits.control_timeout_s)
                if grace <= 0.0:
                    raise _Unavailable()
                deadline = self._deadline(min(caller_deadline, deadline + grace))
                if time.monotonic() >= deadline:
                    raise _Unavailable()
            with self._lock:
                # Replay admission and accounting share one lifecycle boundary.
                # A close/disable/finalize during re-arming must not slip between
                # the check and registration. Already registered work remains
                # visible to close() and keeps its existing accounting semantics.
                if attempt:
                    if self._closed or self._disabled:
                        raise _Unavailable()
                    if self._finalize_started and phase is not AuditPhase.FINALIZE:
                        raise _Unavailable()
                if (
                    len(self._commands) >= self.limits.control_slots
                    or charge > self.limits.control_bytes - self._control_bytes
                ):
                    raise _Unavailable()
                target = _Control(self, phase, command, arguments, charge, deadline)
                self._commands.add(target)
                self._control_bytes += charge
            try:
                ticket = self._io.submit(target, phase, deadline=deadline)
            except BaseException:
                target.release()
                raise
            try:
                outcome = self._io.wait(ticket)
            except BaseException as primary:
                try:
                    self._abandon_control(ticket, target)
                except BaseException as cleanup_error:  # noqa: BLE001 - preserve the wait interruption
                    # Retain private cleanup evidence without replacing the wait
                    # exception or its chain. Avoid subclass hooks and error text.
                    try:
                        state = BaseException.__dict__["__dict__"].__get__(primary)
                        previous = dict.get(state, "_invocation_audit_cleanup_errors", ())
                        if type(previous) is not tuple:
                            previous = (previous,)
                        dict.__setitem__(
                            state, "_invocation_audit_cleanup_errors", previous + (cleanup_error,)
                        )
                        note = "Invocation audit control cleanup also failed; see _invocation_audit_cleanup_errors."
                        if not dict.__contains__(state, "__notes__"):
                            dict.__setitem__(state, "__notes__", [note])
                        elif type(notes := dict.__getitem__(state, "__notes__")) is list:
                            list.append(notes, note)
                    except BaseException:  # noqa: BLE001, S110 - metadata cannot mask the primary
                        pass
                raise
            if outcome.state == "unknown":
                target.abandon()
            else:
                target.release()
            if not (
                outcome.error is None
                and outcome.state != "ack"
                and outcome.not_executed
                and attempt == 0
            ):
                break
        if outcome is None:
            raise _Unavailable()
        if outcome.error is not None:
            raise outcome.error
        if outcome.state != "ack":
            with self._lock:
                self._uncertain |= outcome.publication_uncertain
            raise _Unavailable()
        if time.monotonic() >= deadline:
            with self._lock:
                self._uncertain = True
            raise _Unavailable()
        value = outcome.value
        if hasattr(value, "admitted") and (value.admitted is not True or value.publication_uncertain):
            with self._lock:
                self._uncertain |= value.publication_uncertain
            raise _Unavailable()
        with self._lock:
            self._control_deadline = deadline
        return value

    def _execute(self, command, args, deadline):
        # This entire switch executes exclusively on the one audit worker.
        if command == "open":
            self._owner = InvocationOwner.create(
                artifact_root=self._root,
                context=self._context,
                monotonic=time.monotonic,
                utc_now=_utc,
                job_deadline=self._job_deadline,
                audit_schema_version=2,
                scoped_fault_domain=self._scoped_fault_domain,
            )
            return True
        if command == "advance":
            context, manifest = args
            return self._owner.advance_source(context, manifest=json.loads(manifest))
        if command == "input":
            return self._owner.publish_input(json.loads(args))
        if command == "context":
            return self._owner.context(**args)
        if command == "operation":
            context, cause, parent = args
            return self._owner.operation(
                context, cause=cause, parent=parent, max_attempts=self.limits.max_sends
            )
        if command == "start":
            operation, request = args
            remaining = max(0.0, request.deadline - time.monotonic())
            return self._owner.begin_attempt(
                operation,
                backend=BackendObservation(
                    "openai", request.backend_name, request.model or None, None, None
                ),
                request_digest=request.request_digest,
                request_bytes=request.request_bytes,
                timeout_s=max(1, math.ceil(remaining)),
                cause=request.cause,
                action_deadline=request.deadline,
            )
        if command == "entered":
            args.entered("entered_api_send")
            return True
        if command == "finish":
            lease, outcome, error_class, duration = args
            return lease.finish_once(outcome=outcome, error_class=error_class, duration_s=duration)
        if command == "finalize":
            outcome, incomplete = args
            return self._owner.finalize(outcome, audit_incomplete=incomplete)
        if command == "prepare_managed":
            if not sys.platform.startswith("linux"):
                # Preserve the recorder's explicit unsupported-profile denial;
                # do not attempt Linux descriptor flags or pick another root.
                return maintain_audit_root(
                    self._root, monotonic=self._managed_clock, utc_now=_utc, action_deadline=deadline
                )
            for root in self._maintenance_roots:
                if self._managed_root_exists(root, deadline):
                    self._managed_checkpoint(deadline)
                    ack = maintain_audit_root(
                        root, monotonic=self._managed_clock, utc_now=_utc, action_deadline=deadline
                    )
                    if not ack.admitted or ack.publication_uncertain:
                        return ack
            self._managed_checkpoint(deadline)
            return True
        if command == "maintain":
            root = args
            return maintain_audit_root(
                root, monotonic=time.monotonic, utc_now=_utc, action_deadline=deadline
            )
        raise ValueError("invalid host audit command")

    def _managed_clock(self):
        # Worker callback: only bounded host state, never user code under a lock.
        with self._lock:
            if self._closed or self._finalize_started or self._disabled:
                raise _Unavailable()
        return time.monotonic()

    def _managed_checkpoint(self, deadline):
        if self._managed_clock() >= deadline:
            raise _Unavailable()

    def _managed_root_exists(self, root, deadline):
        """Worker-only no-follow probe. Only ENOENT means absent this once.

        This is not native storage admission. Existing roots are independently
        qualified by maintain_audit_root; OPEN independently qualifies new runs.
        """
        self._managed_checkpoint(deadline)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open("/", flags)
        primary = None
        try:
            for component in root.parts[1:]:
                self._managed_checkpoint(deadline)
                try:
                    next_fd = os.open(component, flags, dir_fd=fd)
                except FileNotFoundError:
                    return False
                old_fd, fd = fd, next_fd
                os.close(old_fd)
            self._managed_checkpoint(deadline)
            return True
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                os.close(fd)
            except BaseException as close_error:  # noqa: BLE001 - keep the probe interruption
                if primary is None:
                    raise
                # The descriptor may already be closed: never retry. Preserve
                # private evidence without formatting errors or invoking hooks.
                try:
                    state = BaseException.__dict__["__dict__"].__get__(primary)
                    previous = dict.get(state, "_invocation_audit_close_errors", ())
                    if type(previous) is not tuple:
                        previous = (previous,)
                    dict.__setitem__(state, "_invocation_audit_close_errors", previous + (close_error,))
                    note = (
                        "Invocation audit probe close also failed; see _invocation_audit_close_errors."
                    )
                    if not dict.__contains__(state, "__notes__"):
                        dict.__setitem__(state, "__notes__", [note])
                    elif type(notes := dict.__getitem__(state, "__notes__")) is list:
                        list.append(notes, note)
                except BaseException:  # noqa: BLE001, S110 - metadata cannot mask the primary
                    pass

    def _ensure_open(self, deadline):
        if self._opened:
            return
        steps = 3 if self._manifest is not None else 2
        if self._managed_roots:
            steps += 1
        deadline = min(self._sequence_deadline(steps), deadline)
        remaining = max(0.0, deadline - time.monotonic())
        if not self._open_gate.acquire(timeout=remaining):
            raise _Unavailable()
        try:
            if self._opened:
                return
            if self._disabled or self._closed or self._root is None:
                raise _Unavailable()
            if self._managed_roots and not self._maintenance_done:
                self._request(AuditPhase.MAINTAIN, "prepare_managed", None, deadline)
                with self._lock:
                    if self._closed or self._finalize_started or self._disabled:
                        raise _Unavailable()
                    self._maintenance_done = True
            self._request(AuditPhase.OPEN, "open", None, deadline)
            if self._manifest is not None:
                self._request(
                    AuditPhase.INPUT,
                    "input",
                    self._manifest,
                    deadline,
                    charge=max(4096, 2 * len(self._manifest)),
                )
            self._opened = True
        finally:
            self._open_gate.release()

    def bind_source(self, context, *, manifest=None):
        """Bind the exact acquired source before the first send, without I/O.

        This supports constructing a lazy lifecycle before inline/subagent outlet
        selection. It does not rewrite an opened source epoch or infer an empty
        diff when acquisition failed. Post-send source changes need the owner's
        separate epoch protocol; they are deliberately not implicit here.
        """
        self._check_process()
        if type(context) is not InvocationContext:
            raise TypeError("expected InvocationContext")
        if (context.source_hash is None) != (context.snapshot_id is None):
            raise ValueError("invalid source identity")
        if (context.source_hash is None) != (manifest is None):
            raise ValueError("source and input manifest disagree")
        _metadata_copy({f.name: getattr(context, f.name) for f in fields(context)}, limit=4096)
        encoded = (
            _metadata_copy(manifest, limit=self.limits.manifest_bytes) if manifest is not None else None
        )
        with self._lock:
            if self._reserved or self._controllers or self._owner is not None or self._closed:
                raise ValueError("source binding is immutable after admission")
            self._context, self._manifest = context, encoded
            self._generation += 1

    def advance_source(self, context, *, manifest, deadline):
        """Advance known source only after on-time settlement of all old sends.

        Timeout does not cancel worker publication. It permanently disables this
        host: late owner state is never accepted as fresh host credit or retried.
        """
        self._check_process()
        if type(context) is not InvocationContext or not _finite(deadline):
            raise TypeError("invalid source transition")
        _metadata_copy({f.name: getattr(context, f.name) for f in fields(context)}, limit=4096)
        encoded = _metadata_copy(manifest, limit=self.limits.manifest_bytes)
        admitted = False
        deadline = self._deadline(deadline)
        try:
            with self._lock:
                if (
                    self._disabled
                    or self._closed
                    or self._finalize_started
                    or self._transition
                    or self._controllers
                    or self._commands
                    or not self._opened
                    or self._manifest is None
                    or context.source_hash is None
                    or context.snapshot_id is None
                    or type(manifest) is not dict
                    or self._epochs >= self.limits.max_epochs
                    or any(not attempt._settled for attempt in self._attempts)
                ):
                    raise _Unavailable()
                self._transition = True
                self._epochs += 1  # Reserve before publication; never refund unknown work.
                admitted = True
            self._request(
                AuditPhase.INPUT,
                "advance",
                (context, encoded),
                deadline,
                charge=max(4096, 2 * len(encoded) + 4096),
            )
            with self._lock:
                if (
                    self._disabled
                    or self._closed
                    or self._finalize_started
                    or time.monotonic() >= self._control_deadline
                ):
                    raise _Unavailable()
                self._context, self._manifest = context, encoded
                self._generation += 1
                return True
        except MemoryError as error:
            self._failure(primary=error)
            raise
        except Exception:  # noqa: BLE001 - fail closed without external diagnostics
            self._failure(uncertain=admitted)
            return False
        except BaseException as error:
            self._failure(uncertain=admitted, primary=error)
            raise
        finally:
            if admitted:
                with self._lock:
                    self._transition = False

    def scope(
        self,
        *,
        round_index=None,
        pass_name=None,
        group_id=None,
        group_diff_sha256=None,
        purpose="review",
        max_provider_sends=12,
        max_redirect_hops=10,
    ):
        self._check_process()
        options = dict(
            round_index=round_index,
            pass_name=pass_name,
            group_id=group_id,
            group_diff_sha256=group_diff_sha256,
            purpose=purpose,
        )
        _metadata_copy(options, limit=4096)
        if (
            type(max_provider_sends) is not int
            or not 0 < max_provider_sends <= self.limits.max_sends
            or type(max_redirect_hops) is not int
            or not 0 <= max_redirect_hops <= 10
        ):
            raise ValueError("invalid send scope budget")
        return HostSendAudit(self, options, max_provider_sends * (max_redirect_hops + 1))

    @property
    def failed(self):
        self._check_process()
        with self._lock:
            return self._disabled

    @property
    def strict_failed(self):
        return self.strict and self.failed

    def safe_summary(self):
        self._check_process()
        with self._lock:
            return HostAuditSummary(
                "incomplete" if self._disabled else "completed" if self._finalized else "active",
                self._complete and not self._disabled,
                self._context.source_hash is not None,
                self._started,
                self._settled,
                self._adopted,
                self._accepted,
                self._uncertain,
                False,
                _WARNING if self._disabled else None,
            )

    def finalize(self, *, outcome="completed", primary=None, maintain=False):
        """Bounded end of lifecycle; no raw exception is sent to the worker."""
        self._check_process()
        if outcome not in ("completed", "failed", "cancelled", "incomplete"):
            raise ValueError("invalid host outcome")
        with self._lock:
            already_started = self._finalize_started
            self._finalize_started = True
            attempts = tuple(self._attempts)
            # Controllers cover ACK waits; unsettled attempts cover the gap
            # after enter_send returns and the entire physical send. Closing
            # admission cannot atomically cancel a transport call. Never claim
            # a complete lifecycle while either boundary may still be active.
            active = bool(
                self._controllers
                or self._transition
                or self._commands
                or any(not attempt._settled for attempt in attempts)
            )
            if active:
                self._disabled = True
        if already_started:
            return self.safe_summary()
        deadline = self._sequence_deadline(1 + len(attempts) + len(self._maintenance_roots))
        cleanup_primary = primary
        try:
            if active:
                self.warn(_WARNING)
            if self._owner is not None:
                for attempt in attempts:
                    if not attempt._finished:
                        attempt._abandon_finish(deadline)
                summary = self._request(
                    AuditPhase.FINALIZE,
                    "finalize",
                    ("incomplete" if self._disabled else outcome, self._disabled),
                    deadline,
                )
                with self._lock:
                    self._complete = summary.audit_complete is True
                if not self._complete:
                    raise _Unavailable()
            if active:
                raise _Unavailable()
            self._finalized = True
        except BaseException as error:  # noqa: BLE001 - primary always wins over cleanup
            cleanup_primary = primary if primary is not None else error
            self._failure(uncertain=True, primary=primary if primary is not None else error)
            if primary is None and (isinstance(error, MemoryError) or not isinstance(error, Exception)):
                raise
            if primary is None and self.strict:
                self._failure()
        finally:
            self.close(primary=cleanup_primary)
        # Maintenance is explicit, bounded and only on fixed roots passed by the
        # host. It cannot spawn a worker or change roots to evade quota failure.
        if maintain and not self._maintenance_done and self._opened:
            self._maintenance_done = True
            for root in self._maintenance_roots:
                try:
                    self._request(AuditPhase.MAINTAIN, "maintain", root, deadline)
                except BaseException as error:  # noqa: BLE001 - maintenance is secondary
                    self._failure(primary=primary if primary is not None else error)
                    if primary is None and (
                        isinstance(error, MemoryError) or not isinstance(error, Exception)
                    ):
                        raise
                    if primary is None and self.strict:
                        self._failure()
        return self.safe_summary()

    def close(self, *, primary=None):
        self._check_process()
        with self._lock:
            if self._closed:
                return
            self._closed = True
            attempts = tuple(self._attempts)
            active = bool(
                self._controllers
                or self._transition
                or self._commands
                or any(not attempt._settled for attempt in attempts)
            )
            if active:
                self._disabled = True
        fatal = chain = None

        def remember(error):
            nonlocal fatal, chain
            if fatal is None and (isinstance(error, MemoryError) or not isinstance(error, Exception)):
                fatal = error
                chain = (
                    BaseException.__cause__.__get__(error),
                    BaseException.__context__.__get__(error),
                    BaseException.__suppress_context__.__get__(error),
                )
            # A diagnostic cannot replace either an entering primary or the
            # first fatal. All cancellation operations are nonblocking signals;
            # there is no join and no fresh per-attempt drain deadline here.
            self._failure(primary=primary if primary is not None else error)

        if active:
            try:
                self.warn(_WARNING)
            except BaseException as error:  # noqa: BLE001 - still cancel all captures
                remember(error)
        for attempt in attempts:
            if attempt._stage is not None and not attempt._settled:
                try:
                    attempt._stage.cancel()
                except BaseException as error:  # noqa: BLE001 - finish every bounded cleanup first
                    remember(error)
        if self._captures is not None:
            try:
                self._captures.close()
            except BaseException as error:  # noqa: BLE001 - preserve first fatal/primary after cleanup
                remember(error)
        # The supervisor is process-owned, never replaced or closed per session.
        if primary is None and fatal is not None:
            try:
                raise fatal
            finally:
                BaseException.__cause__.__set__(fatal, chain[0])
                BaseException.__context__.__set__(fatal, chain[1])
                BaseException.__suppress_context__.__set__(fatal, chain[2])


class HostSendAudit:
    """Concrete implementation of invocation_send_audit.HostSendAudit."""

    def __init__(self, session, options, physical_budget):
        self._session, self._options = session, options
        self._budget, self._used = physical_budget, 0
        self.strict = session.strict
        with session._lock:
            self._generation = session._generation

    @property
    def failed(self):
        return self._session.failed

    @property
    def lifecycle_closed(self):
        self._session._check_process()
        with self._session._lock:
            return self._session._closed or self._session._finalize_started

    @property
    def strict_failed(self):
        return self._session.strict_failed

    def warn(self, code):
        self._session.warn(code)

    @_controller
    def start_send(self, request):
        host = self._session
        host._check_process()
        if type(request) is not SendRequest:
            raise TypeError("invalid send request")
        if (
            request.cause not in _CAUSES
            or not _finite(request.deadline)
            or type(request.request_bytes) is not int
            or not 0 <= request.request_bytes < 1 << 63
            or not _text(request.request_digest, 64)
            or re.fullmatch("[0-9a-f]{64}", request.request_digest) is None
            or request.method not in ("GET", "POST")
            or not _text(request.backend_name)
            or not _text(request.model)
        ):
            raise ValueError("invalid send metadata")
        parent = request.parent
        if parent is not None and (
            type(parent) is not _HostAttempt or parent._scope is not self or not parent._settled
        ):
            raise ValueError("foreign or unsettled parent")
        try:
            with host._lock:
                if host._closed or host._finalize_started:
                    raise SendLifecycleClosed()
                if (
                    host._disabled
                    or host._reserved >= host.limits.max_sends
                    or self._used >= self._budget
                ):
                    raise _Unavailable()
                # Reserve before any wait so concurrent starts cannot exceed it.
                host._reserved += 1
                self._used += 1
            host._ensure_open(request.deadline)
            if parent is not None and request.cause in ("retry", "redirect"):
                operation = parent._operation
            else:
                options = dict(
                    self._options,
                    parent_logical_id=parent._operation.logical_id if parent is not None else None,
                )
                context = host._request(AuditPhase.OPERATION, "context", options, request.deadline)
                operation = host._request(
                    AuditPhase.OPERATION,
                    "operation",
                    (context, request.cause, parent._operation if parent is not None else None),
                    request.deadline,
                )
            lease = host._request(AuditPhase.START, "start", (operation, request), request.deadline)
            with host._lock:
                # START may publish while close/finalize wins on the controller.
                # Do not return a new capability, register it, or upgrade the
                # cached counts after that lifecycle boundary. No retry of START.
                if host._closed or host._finalize_started:
                    raise SendLifecycleClosed()
                if host._disabled:
                    raise _Unavailable()
                attempt = _HostAttempt(self, operation, lease, request.deadline)
                host._attempts.add(attempt)
                host._started += 1
            return attempt
        except SendLifecycleClosed:
            raise
        except MemoryError as error:
            host._failure(primary=error)
            raise
        except Exception:  # noqa: BLE001 - host-owned ordinary failures are safe degradation
            host._failure()
            return None
        except BaseException as error:
            host._failure(primary=error)
            raise


class _HostAttempt:
    def __init__(self, scope, operation, lease, deadline):
        self._scope, self._operation, self._lease = scope, operation, lease
        self._host, self._deadline = scope._session, deadline
        self._stage = self._adoption = None
        self._observations = 0
        self._response_requested = self._finished = self._settled = False
        self._entered = self._enter_requested = False

    @_controller
    def enter_send(self):
        """Enter the local send boundary, not proof of physical network delivery."""
        host = self._host
        host._check_process()
        with host._lock:
            if self._finished or host._closed or host._finalize_started:
                raise SendLifecycleClosed()
            elif self._enter_requested:
                return self._entered
            else:
                self._enter_requested = True
                unavailable = host._disabled
        try:
            if unavailable:
                raise _Unavailable()
            host._request(AuditPhase.ENTERED, "entered", self._lease, self._deadline)
            with host._lock:
                # An on-time worker ACK is not an on-time controller admission.
                # Finalization/close may have won while the controller waited.
                if host._closed or host._finalize_started or self._finished:
                    raise SendLifecycleClosed()
                if host._disabled:
                    raise _Unavailable()
                self._entered = True
                return True
        except SendLifecycleClosed:
            raise
        except MemoryError as error:
            host._failure(primary=error)
            raise
        except Exception:  # noqa: BLE001 - default transport may continue only within its own deadline
            host._failure()
            return False
        except BaseException as error:
            host._failure(primary=error)
            raise

    @_controller
    def new_response(self, *, status, redirect_hop, deadline):
        host = self._host
        host._check_process()
        if self._response_requested or self._finished or not self._entered:
            raise ValueError("response capture cannot be reused")
        if not _finite(deadline) or deadline > self._deadline:
            raise ValueError("response cannot extend action deadline")
        if status is not None and (type(status) is not int or not 100 <= status <= 599):
            raise ValueError("invalid response status")
        # URL/path values in redirect_hop are deliberately not retained or
        # exposed. Actual request identity is already bounded in SendRequest.
        self._response_requested = True
        try:
            if host._disabled or host._closed or host._finalize_started:
                raise _Unavailable()
            self._stage = self._lease.begin_capture_stage(
                supervisor=host._io,
                session=host._captures,
                layer="wire",
                warn=host.warn,
                limit_bytes=host.limits.response_bytes,
                strict=host.strict,
                chunk_bytes=host.limits.batch_bytes,
                control_timeout_s=host.limits.control_timeout_s,
            )
            if host._closed or host._finalize_started:
                self._stage.cancel()
                raise _Unavailable()
            return self._stage.capture
        except MemoryError as error:
            host._failure(primary=error)
            raise
        except Exception:  # noqa: BLE001 - default mode continues the same decoder
            host._failure(uncertain=True)
            return None
        except BaseException as error:
            host._failure(primary=error)
            raise

    def _adopt(self):
        if self._adoption is not None:
            return self._adoption
        snapshot = self._stage.seal()
        ack = self._stage.adopt(usage=None)
        if ack.ref is None or ack.publication_uncertain:
            raise _Unavailable()
        with self._host._lock:
            if self._host._closed or self._host._finalize_started:
                raise _Unavailable()
            self._adoption = ack
            self._host._adopted += 1
            self._host._accepted += ack.ref.retained_bytes
        if not ack.admitted or not snapshot.complete:
            self._host.warn(_WARNING)
        return ack

    @_controller
    def observe_response(self, *, frame_offset, frame_length, usage=None, observed_backend=None):
        """Optional trusted frame observation; never infer offsets from LLM Usage.

        Source identity comes only from this attempt's adopted stage. The stage
        bridge publishes OBSERVE on the same worker and gates credit on its ACK.
        """
        self._host._check_process()
        if self._stage is None or self._finished:
            raise ValueError("observation requires this attempt's live stage")
        if (
            type(frame_offset) is not int
            or type(frame_length) is not int
            or frame_offset < 0
            or frame_length < 0
        ):
            raise ValueError("invalid observation frame")
        if self._observations >= self._host.limits.max_observations_per_send:
            self._host._failure()
            return False
        self._observations += 1
        if usage is not None:
            if type(usage) is not UsageObservation:
                raise TypeError("expected trusted UsageObservation")
            encoded = _metadata_copy({f.name: getattr(usage, f.name) for f in fields(usage)}, limit=4096)
            usage = UsageObservation(**json.loads(encoded))
        if observed_backend is not None:
            if type(observed_backend) is not BackendObservation:
                raise TypeError("expected BackendObservation")
            _metadata_copy(
                {f.name: getattr(observed_backend, f.name) for f in fields(observed_backend)}, limit=4096
            )
        try:
            self._adopt()
            ack = self._stage.observe(
                frame_offset=frame_offset,
                frame_length=frame_length,
                usage=usage,
                observed_backend=observed_backend,
            )
            if not ack.admitted or ack.publication_uncertain:
                raise _Unavailable()
            return True
        except MemoryError as error:
            self._host._failure(primary=error)
            raise
        except Exception:  # noqa: BLE001 - observation failure cannot mint native credit
            self._host._failure(uncertain=True)
            return False
        except BaseException as error:
            self._host._failure(primary=error)
            raise

    def _abandon_finish(self, deadline):
        # Outer lifecycle cleanup never starts a fresh drain/adoption sequence
        # with this send's longer deadline. Preserve unfinished raw as incomplete.
        if self._finished:
            return
        try:
            if self._stage is not None:
                self._stage.cancel()
            self._host._request(
                AuditPhase.FINISH, "finish", (self._lease, "cancelled", None, None), deadline
            )
        finally:
            self._finished = True

    def _finish(self, *, outcome, error_class, duration_s):
        if self._finished:
            return self._settled
        try:
            if outcome == "completed" and self._stage is None:
                raise _Unavailable()
            if self._stage is not None:
                self._adopt()
            # Seal/adopt have their own bounded waits. Start the settlement
            # control window only now, not before those waits consume it.
            # _request still caps it by the job deadline; outer lifecycle
            # cleanup retains its separate shared deadline in _abandon_finish.
            deadline = time.monotonic() + self._host.limits.control_timeout_s
            self._host._request(
                AuditPhase.FINISH, "finish", (self._lease, outcome, error_class, duration_s), deadline
            )
            with self._host._lock:
                if self._host._closed or self._host._finalize_started:
                    raise _Unavailable()
                self._settled = True
                self._host._settled += 1
            return True
        finally:
            self._finished = True

    @_controller
    def finish(self, *, outcome, error_class, duration_s, usage):
        self._host._check_process()
        if outcome not in ("completed", "failed") or not _finite(duration_s):
            raise ValueError("invalid send settlement")
        # Aggregate LLM usage lacks a trustworthy wire frame association. Do not
        # invent observation offsets or native usage credit from it.
        safe_error = (
            error_class
            if error_class
            in (
                "TimeoutError",
                "HTTPError",
                "LLMInvokeError",
                "ValueError",
                "OSError",
                "KeyboardInterrupt",
                "SystemExit",
                "MemoryError",
                "SendDeadlineBeforeNetwork",
            )
            else ("Exception" if error_class is not None else None)
        )
        try:
            return self._finish(
                outcome=outcome,
                error_class=safe_error,
                duration_s=duration_s,
            )
        except MemoryError as error:
            self._host._failure(primary=error)
            raise
        except Exception:  # noqa: BLE001 - send scope owns primary-error precedence
            self._host._failure(uncertain=True)
            return False
        except BaseException as error:
            self._host._failure(primary=error)
            raise
