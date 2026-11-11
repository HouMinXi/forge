"""Test-only bounded, allowlisted observations; never format audit objects."""

from collections import deque
import json

from code_forge import invocation_audit as audit, invocation_host, invocation_io
from code_forge.invocation_io import AuditPhase, IoOutcome, IoTicket
from code_forge.llm_invoke import LLMInvokeError


# Exact classes only: provider-controlled subclasses/names/messages are not public.
_ERRORS = {
    LLMInvokeError: "llm_invoke_error",
    OSError: "os_error",
    RuntimeError: "runtime_error",
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
    # Native denial can occur after transport ACK, outside the host request wrapper.
    return not _native_ack(value) or (value.admitted is True and value.publication_uncertain is False)


class AuditDiagnosticProbe:
    """Observe abnormal results/errors without extra waits, locks or deadlines.

    Clean flags omit event construction, not controller validation. Wrappers still
    have overhead, and an empty trace does not establish audit completeness.
    First means successful metadata insertion order, not proven causal order.
    """

    def __init__(self):
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
        else:
            event["state"] = "other_outcome"
        self._record(event)

    def install(self, monkeypatch, supervisor):
        original_call = invocation_io._call_target
        original_wait = supervisor.wait
        original_request = invocation_host.HostAuditSession._request

        def call(target, phase, *args, **kwargs):
            result = original_call(target, phase, *args, **kwargs)
            try:
                if result[1] is not None:
                    self._record(
                        dict(event="worker_error", phase=phase_kind(phase), error=error_kind(result[1]))
                    )
            except BaseException:  # noqa: BLE001 - Diagnostic failure must preserve the worker result.
                pass
            return result

        def wait(ticket):
            try:
                result = original_wait(ticket)
            except BaseException as error:
                try:
                    phase = ticket._phase if type(ticket) is IoTicket else None
                    self._record(
                        dict(event="wait_error", phase=phase_kind(phase), error=error_kind(error))
                    )
                except BaseException:  # noqa: BLE001 - Preserve the primary wait exception.
                    pass
                raise
            try:
                if not _clean_ack(result):
                    phase = ticket._phase if type(ticket) is IoTicket else None
                    self.observe_outcome(phase, result)
            except BaseException:  # noqa: BLE001 - Return the original outcome even if diagnostics fail.
                pass
            return result

        def request(host, phase, *args, **kwargs):
            try:
                return original_request(host, phase, *args, **kwargs)
            except BaseException as error:
                try:
                    self._record(
                        dict(event="request_error", phase=phase_kind(phase), error=error_kind(error))
                    )
                except BaseException:  # noqa: BLE001 - Preserve the primary request exception.
                    pass
                raise

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
