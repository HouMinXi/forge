"""Test-only bounded, allowlisted observations; never format audit objects."""

from collections import deque
import json

from code_forge import invocation_host, invocation_io
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
    return "none" if error is None else _ERRORS.get(type(error), "other_error")


def phase_kind(phase):
    return phase.value if type(phase) is AuditPhase else "other_phase"


class AuditDiagnosticProbe:
    """Observe results and worker error categories; no extra waits or deadlines."""

    def __init__(self):
        self.events = deque(maxlen=64)

    def observe_outcome(self, phase, outcome):
        event = dict(event="wait", phase=phase_kind(phase))
        if type(outcome) is IoOutcome:
            state = outcome.state
            event.update(
                state=state if type(state) is str and state in _STATES else "other_state",
                error=error_kind(outcome.error),
                not_executed=outcome.not_executed is True,
                publication_uncertain=outcome.publication_uncertain is True,
            )
        else:
            event["state"] = "other_outcome"
        self.events.append(event)

    def install(self, monkeypatch, supervisor):
        original_call = invocation_io._call_target
        original_wait = supervisor.wait
        original_request = invocation_host.HostAuditSession._request
        events = self.events

        def call(target, phase, *args, **kwargs):
            value, error = original_call(target, phase, *args, **kwargs)
            if error is not None:
                events.append(
                    dict(event="worker_error", phase=phase_kind(phase), error=error_kind(error))
                )
            return value, error

        def wait(ticket):
            phase = ticket._phase if type(ticket) is IoTicket else None
            try:
                result = original_wait(ticket)
            except BaseException as error:
                events.append(dict(event="wait_error", phase=phase_kind(phase), error=error_kind(error)))
                raise
            self.observe_outcome(phase, result)
            return result

        def request(host, phase, *args, **kwargs):
            try:
                return original_request(host, phase, *args, **kwargs)
            except BaseException as error:
                events.append(
                    dict(event="request_error", phase=phase_kind(phase), error=error_kind(error))
                )
                raise

        monkeypatch.setattr(invocation_io, "_call_target", call)
        monkeypatch.setattr(supervisor, "wait", wait)
        monkeypatch.setattr(invocation_host.HostAuditSession, "_request", request)

    def summary(self):
        return "safe audit trace: " + json.dumps(list(self.events), sort_keys=True)
