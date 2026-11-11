"""Test-only bounded, allowlisted observations; never format audit objects."""

from collections import deque
import json
import time

from code_forge import invocation_audit as audit, invocation_host, invocation_io, invocation_owner
from code_forge.invocation_io import AuditPhase, IoOutcome, IoTicket
from code_forge.llm_invoke import LLMInvokeError


# Exact classes only: provider-controlled subclasses/names/messages are not public.
_ERRORS = {
    LLMInvokeError: "llm_invoke_error",
    OSError: "os_error",
    FileNotFoundError: "file_not_found_error",
    PermissionError: "permission_error",
    BlockingIOError: "blocking_io_error",
    RuntimeError: "runtime_error",
    AssertionError: "assertion_error",
    TypeError: "type_error",
    KeyError: "key_error",
    IndexError: "index_error",
    ValueError: "value_error",
    TimeoutError: "timeout_error",
    MemoryError: "memory_error",
    invocation_host._Unavailable: "host_unavailable",
}
_STATES = frozenset(
    ("ack", "unknown", "unavailable", "expired", "empty", "cancelled", "closed", "failed")
)


def error_kind(error):
    if error is None:
        return "none"
    # Identity matching does not call a private metaclass's hash/equality hooks.
    for known, name in _ERRORS.items():
        if type(error) is known:
            return name
    return "other_error"


def phase_kind(phase):
    return phase.value if type(phase) is AuditPhase else "other_phase"


def _boolean(value):
    return value if type(value) is bool else None


def _native_ack(value):
    kind = type(value)
    return (
        kind is audit.AuditAcknowledgement
        or kind is audit.StartAcknowledgement
        or kind is audit.StageAcknowledgement
        or kind is audit.AcquiredAcknowledgement
    )


def _clean_ack(outcome):
    if not (
        type(outcome) is IoOutcome
        and type(outcome.state) is str
        and outcome.state == "ack"
        and outcome.error is None
        and outcome.not_executed is False
        and outcome.publication_uncertain is False
    ):
        return False
    value = outcome.value
    # A summary is not a native ACK: its completeness is checked by finalize
    # after _request returns. Observe that exact flag, never its private fields.
    if type(value) is invocation_owner.InvocationSessionSummary:
        return value.audit_complete is True
    # Native denial can occur after transport ACK, outside the host request wrapper.
    return not _native_ack(value) or (value.admitted is True and value.publication_uncertain is False)


class AuditDiagnosticProbe:
    """Observe abnormal results/errors without extra waits, locks or deadlines.

    Clean flags omit event construction, not controller validation. Wrappers still
    have overhead, and an empty trace does not establish audit completeness.
    First means successful metadata insertion order, not proven causal order.
    """

    def __init__(self, *, detailed=False):
        self.detailed = detailed
        self.events = deque(maxlen=64)
        self._first_failure = {}

    def _record(self, event):
        # One fixed-key built-in insertion on CPython avoids check-then-set races.
        # Pin separately so later tail eviction cannot erase the first observation.
        self._first_failure.setdefault("event", event)
        self.events.append(event)

    def observe_outcome(self, phase, outcome):
        if _clean_ack(outcome):
            return
        event = dict(event="wait", phase=phase_kind(phase))
        if type(outcome) is IoOutcome:
            state = outcome.state
            event.update(
                state=state if type(state) is str and state in _STATES else "other_state",
                error=error_kind(outcome.error),
                not_executed=_boolean(outcome.not_executed),
                publication_uncertain=_boolean(outcome.publication_uncertain),
            )
            if _native_ack(outcome.value):
                value = outcome.value
                event.update(
                    ack_admitted=_boolean(value.admitted),
                    ack_uncertain=_boolean(value.publication_uncertain),
                )
            elif type(outcome.value) is invocation_owner.InvocationSessionSummary:
                event["summary_audit_complete"] = _boolean(outcome.value.audit_complete)
        else:
            event["state"] = "other_outcome"
        self._record(event)

    def _error_event(self, event, phase, error):
        result = dict(event=event, phase=phase_kind(phase), error=error_kind(error))
        if self.detailed:
            result["exception_chain"] = exception_chain(error)
        return result

    def install(self, monkeypatch, supervisor):
        original_call = invocation_io._call_target
        original_wait = supervisor.wait
        original_request = invocation_host.HostAuditSession._request

        def call(target, phase, *args, **kwargs):
            result = original_call(target, phase, *args, **kwargs)
            try:
                if result[1] is not None:
                    self._record(self._error_event("worker_error", phase, result[1]))
            except BaseException:  # noqa: BLE001 - Diagnostic failure must preserve the worker result.
                pass
            return result

        def wait(ticket):
            started = None
            if self.detailed:
                try:
                    started = time.monotonic_ns()
                except BaseException:  # noqa: BLE001 - Diagnostics must not prevent the real wait.
                    pass
            try:
                result = original_wait(ticket)
            except BaseException as error:
                try:
                    phase = ticket._phase if type(ticket) is IoTicket else None
                    self._record(self._error_event("wait_error", phase, error))
                except BaseException:  # noqa: BLE001 - Preserve the primary wait exception.
                    pass
                raise
            try:
                if not _clean_ack(result):
                    phase = ticket._phase if type(ticket) is IoTicket else None
                    self.observe_outcome(phase, result)
                    if started is not None:
                        self._record(
                            dict(
                                event="wait_timing",
                                phase=phase_kind(phase),
                                elapsed_ns=max(0, time.monotonic_ns() - started),
                            )
                        )
            except BaseException:  # noqa: BLE001 - Return the original outcome even if diagnostics fail.
                pass
            return result

        def request(host, phase, *args, **kwargs):
            try:
                return original_request(host, phase, *args, **kwargs)
            except BaseException as error:
                try:
                    self._record(self._error_event("request_error", phase, error))
                except BaseException:  # noqa: BLE001 - Preserve the primary request exception.
                    pass
                raise

        if self.detailed:
            original_owner_call = invocation_owner.InvocationOwner._call

            def owner_call(owner, function, phase, **kwargs):
                def observed(**arguments):
                    try:
                        return function(**arguments)
                    except BaseException as error:
                        try:
                            event = self._error_event("recorder_error", None, error)
                            event["phase"] = (
                                phase if type(phase) is str and phase in _OWNER_PHASES else "other_phase"
                            )
                            self._record(event)
                        except BaseException:  # noqa: BLE001 - Preserve the recorder's exception.
                            pass
                        raise

                return original_owner_call(owner, observed, phase, **kwargs)

            monkeypatch.setattr(invocation_owner.InvocationOwner, "_call", owner_call)
        monkeypatch.setattr(invocation_io, "_call_target", call)
        monkeypatch.setattr(supervisor, "wait", wait)
        monkeypatch.setattr(invocation_host.HostAuditSession, "_request", request)

    def summary(self):
        try:
            return "safe audit trace: " + json.dumps(
                dict(first_failure=self._first_failure.get("event"), events=list(self.events)),
                sort_keys=True,
            )
        except BaseException:  # noqa: BLE001 - Rendering must not replace the failed assertion.
            return "safe audit trace: unavailable"


# Exact repository filenames only. No source text, function arguments, locals,
# paths outside this list, exception messages or arbitrary exception class names.
# Built-in descriptors bypass private exception subclass attribute hooks.
_SOURCE_FILES = {
    invocation_io.__file__: "src/code_forge/invocation_io.py",
    invocation_host.__file__: "src/code_forge/invocation_host.py",
    invocation_owner.__file__: "src/code_forge/invocation_owner.py",
    audit.__file__: "src/code_forge/invocation_audit.py",
    __file__.replace(
        "audit_diagnostic_probe.py", "test_invocation_host.py"
    ): "tests/test_invocation_host.py",
}
_OWNER_PHASES = frozenset(("open", "start", "acquired", "observe", "finish", "finalize"))
_OS_ERRORS = (OSError, FileNotFoundError, PermissionError, TimeoutError, BlockingIOError)


def exception_chain(error):
    """Bounded snapshot of an observed exception, never a rendered traceback.

    Preserve suppressed context as diagnostic metadata too, but label the link.
    A source coordinate identifies the observed frame, not a proven root cause.
    """
    result, seen = [], set()
    relation = "observed"
    while error is not None and id(error) not in seen and len(result) < 4:
        seen.add(id(error))
        item = dict(relation=relation, error=error_kind(error), frames=[])
        if any(type(error) is kind for kind in _OS_ERRORS):
            number = OSError.errno.__get__(error)
            if type(number) is int and 0 <= number <= 4095:
                item["errno"] = number
        tb = BaseException.__traceback__.__get__(error)
        dropped = False
        for _ in range(32):
            if tb is None:
                break
            path = _SOURCE_FILES.get(tb.tb_frame.f_code.co_filename)
            if path is not None:
                item["frames"].append(dict(path=path, line=tb.tb_lineno))
                dropped = dropped or len(item["frames"]) > 8
                item["frames"] = item["frames"][-8:]
            tb = tb.tb_next
        item["frames_truncated"] = dropped or tb is not None
        result.append(item)
        cause = BaseException.__cause__.__get__(error)
        error = cause if cause is not None else BaseException.__context__.__get__(error)
        relation = "cause" if cause is not None else "context"
    return dict(exceptions=result, truncated=error is not None)
