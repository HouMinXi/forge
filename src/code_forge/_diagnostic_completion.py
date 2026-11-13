"""Private one-invocation handoff, not serialized history or review authority."""

from dataclasses import dataclass
import os
import threading

from . import _advisory_diagnostics as storage


@dataclass(frozen=True, slots=True)
class Attempt:
    expected: storage.ExpectedContext
    publication: storage.PublicationResult


@dataclass(frozen=True, slots=True)
class Completion:
    execution_id: str
    worker_completed: bool
    capture_available: bool
    capture_cleanup_failed: bool
    attempts: tuple[Attempt, ...]
    delivery_status: str
    tickets: tuple[tuple[int, str | None], ...]


class DiagnosticCompletion:
    """The CLI owns this slot; a primary worker seals it before completion signal.

    Reads are restricted to the creating process/thread after the host returns or
    raises. No file loading, callbacks, resumption or current-run credit is added.
    """

    def __init__(self):
        self._owner = (os.getpid(), threading.get_ident())
        self._execution_id = None
        self._result = None
        self._sealed = False
        self._completion_event = None

    def claim(self, execution_id):
        if self._owner != (os.getpid(), threading.get_ident()) or self._execution_id is not None:
            raise ValueError("diagnostic completion already claimed or foreign owner")
        storage._uuid(execution_id)
        self._execution_id = execution_id

    def seal(self, capture, *, worker_completed, cleanup_failed, completion_event):
        if os.getpid() != self._owner[0] or self._execution_id is None or self._sealed:
            return
        self._completion_event = completion_event
        try:
            available = capture is not None and capture.execution_id == self._execution_id
            attempts = tuple(capture._publication_attempts) if available else ()
            if len(attempts) > storage.MAX_OBSERVATIONS:
                raise ValueError("diagnostic attempt budget")
            for attempt in attempts:
                if type(attempt) is not Attempt or attempt.expected.execution_id != self._execution_id:
                    raise ValueError("foreign diagnostic attempt")
                storage.validate_publication(attempt.publication, attempt.expected)
            names = (
                tuple(
                    (sequence, path.name if path is not None else None)
                    for sequence, path in capture.diagnostic_ticket_outcomes.items()
                )
                if available
                else ()
            )
            self._result = Completion(
                self._execution_id,
                worker_completed,
                available,
                cleanup_failed,
                attempts,
                capture.delivery_status if available else "not_attempted",
                names,
            )
        except Exception:  # noqa: BLE001 -- A missing handoff never changes review/exception behavior.
            self._result = None
        finally:
            self._sealed = True

    def read(self):
        if (
            self._owner != (os.getpid(), threading.get_ident())
            or not self._sealed
            or not self._completion_event.is_set()
        ):
            return None
        return self._result

    def notices(self):
        result = self.read()
        if result is None or not result.capture_available:
            return ("diagnostic-ticket: unavailable; no saved-ticket guarantee",)
        if not result.tickets:
            return (
                ("diagnostic-ticket: no receipt was issued",)
                if result.worker_completed
                else ("diagnostic-ticket: unavailable; no saved-ticket guarantee",)
            )
        attempts = {attempt.expected.observation_sequence: attempt for attempt in result.attempts}
        notices = []
        for sequence, name in result.tickets:
            available = False
            if name is not None:
                try:
                    attempt = attempts[sequence]
                    # The real CLI consumer reads the retained owner tuple, not
                    # the unsigned exported ticket's self-described expectation.
                    storage.read_observation(attempt.publication, attempt.expected)
                    available = True
                except Exception:  # noqa: BLE001, S110 -- Missing/corrupt history is not a gate failure.
                    pass
            notices.append(
                "diagnostic-ticket: exported "
                + name
                + "; one historical observation, not complete review history"
                if available
                else "diagnostic-ticket: unavailable; no saved-ticket guarantee"
            )
        return tuple(notices)
