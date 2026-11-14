# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Opt-in test timing metadata, not kernel causality or storage qualification.

No new locks, waits, retries or deadlines. Observers can perturb scheduling.
Only bounded primitive metadata is retained, never tickets/targets/payloads.
"""

from collections import OrderedDict, deque
from itertools import count
import time
import threading
import weakref

from code_forge import invocation_io
from tests.audit_diagnostic_probe import phase_kind

_STATES = frozenset(("ack", "unknown", "expired", "unavailable", "failed", "cancelled"))


class AuditTimingProbe:
    def __init__(self):
        self.events = deque(maxlen=128)
        self._tickets = OrderedDict()
        self._serial = count(1)
        self._event_serial = count(1)
        self.window_may_be_truncated = False
        self.dropped_events_lower_bound = 0
        self._first_failure = {}
        self.snapshot_failed = False
        self.observation_failed = False

    def _emit(self, boundary, ticket, outcome=None):
        if type(ticket) is not invocation_io.IoTicket:
            return
        # Exact IoTicket keys have identity equality. Dead weakrefs cannot alias
        # a recycled object id; setdefault chooses one ordinal under the GIL.
        key = weakref.ref(ticket)
        ordinal = self._tickets.setdefault(key, next(self._serial))
        if len(self._tickets) > 128:
            self._tickets.popitem(last=False)
        sequence = next(self._event_serial)
        if sequence >= self.events.maxlen:
            self.window_may_be_truncated = True
        now = time.monotonic_ns()
        event = dict(
            boundary=boundary,
            ticket=ordinal,
            phase=phase_kind(ticket._phase),
            at_ns=now,
            sequence=sequence,
        )
        deadline = ticket._deadline
        if type(deadline) in (int, float):
            event["deadline_remaining_ns"] = int(deadline * 1_000_000_000) - now
        if type(outcome) is invocation_io.IoOutcome:
            state = outcome.state
            event["state"] = state if type(state) is str and state in _STATES else "other_state"
        if len(self.events) == self.events.maxlen:
            self.dropped_events_lower_bound += 1
        self.events.append(event)
        if event.get("state") in ("unknown", "expired", "unavailable", "failed"):
            if self.first_failure_window is None:
                self._first_failure.setdefault("window", tuple(dict(item) for item in list(self.events)))

    @property
    def first_failure_window(self):
        return self._first_failure.get("window")

    def _note(self, *args):
        try:
            self._emit(*args)
        except BaseException:  # noqa: BLE001 - observation must not alter the real operation
            self.observation_failed = True

    def _running_ticket(self, supervisor, target=None):
        try:
            ticket = supervisor._running
            if (
                type(ticket) is invocation_io.IoTicket
                and threading.current_thread() is supervisor._worker
                and (target is None or ticket._target is target)
            ):
                return ticket
        except BaseException:  # noqa: BLE001 - qualification is also observation
            self.observation_failed = True
        return None

    def install(self, monkeypatch, supervisor):
        submit_locked = supervisor._submit_locked
        next_locked = supervisor._next_locked
        complete = supervisor._complete_locked
        unknown = supervisor._unknown_locked
        wait = supervisor.wait
        call = invocation_io._call_target

        def submitted(*args, **kwargs):
            ticket = submit_locked(*args, **kwargs)
            self._note("submitted", ticket)
            return ticket

        def selected():
            result = next_locked()
            self._note("worker_selected", result[0])
            return result

        def completed(ticket, outcome, **kwargs):
            result = complete(ticket, outcome, **kwargs)
            self._note("outcome_published", ticket, ticket._outcome)
            return result

        def uncertain(ticket):
            result = unknown(ticket)
            self._note("unknown_observed", ticket, ticket._outcome)
            return result

        def target_call(target, phase, data, size):
            ticket = self._running_ticket(supervisor, target)
            self._note("target_enter", ticket)
            try:
                return call(target, phase, data, size)
            finally:
                self._note("target_return", ticket)

        def waited(ticket):
            self._note("wait_enter", ticket)
            try:
                result = wait(ticket)
            except BaseException:
                self._note("wait_raised", ticket)
                raise
            self._note("wait_return", ticket, result)
            return result

        monkeypatch.setattr(supervisor, "_submit_locked", submitted)
        monkeypatch.setattr(supervisor, "_next_locked", selected)
        monkeypatch.setattr(supervisor, "_complete_locked", completed)
        monkeypatch.setattr(supervisor, "_unknown_locked", uncertain)
        monkeypatch.setattr(supervisor, "wait", waited)
        monkeypatch.setattr(invocation_io, "_call_target", target_call)

    def install_recorder(self, monkeypatch, supervisor, recorder):
        original = recorder.begin_stage

        def begin(instance, *args, **kwargs):
            ticket = self._running_ticket(supervisor)
            self._note("recorder_enter", ticket)
            try:
                return original(instance, *args, **kwargs)
            finally:
                self._note("recorder_return", ticket)

        monkeypatch.setattr(recorder, "begin_stage", begin)

    def snapshot(self):
        # Failure reporter owns serialization; successful tests publish nothing.
        try:
            events = list(self.events)
            return [dict(event) for event in events]
        except BaseException:  # noqa: BLE001 - do not replace an original failure
            self.snapshot_failed = True
            return []

    def report(self):
        events = self.snapshot()
        try:
            first = self.first_failure_window
            first = None if first is None else [dict(event) for event in first]
        except BaseException:  # noqa: BLE001 - explicit incomplete evidence
            self.snapshot_failed = True
            first = None
        return dict(
            events=events,
            first_failure_window=first,
            dropped_events_lower_bound=self.dropped_events_lower_bound,
            window_may_be_truncated=self.window_may_be_truncated,
            observation_failed=self.observation_failed,
            snapshot_failed=self.snapshot_failed,
        )
