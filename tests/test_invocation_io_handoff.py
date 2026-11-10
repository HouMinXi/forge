"""Bounded scheduler handoffs with synthetic ACKs, not storage qualification."""

import asyncio
import gc
import threading
import time
from types import SimpleNamespace
import weakref

import pytest

from code_forge import invocation_io as module
from code_forge.invocation_io import AuditPhase, IoLimits


@pytest.fixture
def supervisor(monkeypatch):
    monkeypatch.setattr(module, "_PROCESS", None)
    process = module.process_audit_io(limits=IoLimits(8, 2048, 128, 4), warn=lambda _: None)
    yield process
    process.close()
    if process._worker is not None:
        process._worker.join(3)
        assert not process._worker.is_alive()


class Target:
    def __init__(self, *, entered=None, release=None, error=None):
        self.entered, self.release, self.error = entered, release, error
        self.calls = []

    def audit_io(self, phase, payload):
        self.calls.append((phase, payload))
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            assert self.release.wait(3), "test did not release worker"
        if self.error is not None:
            raise self.error
        return len(payload)


def gated(process, phase=AuditPhase.ADOPT, *, deadline=None, target=None):
    ticket = process.submit(
        Target() if target is None else target,
        phase,
        deadline=time.monotonic() + 5 if deadline is None else deadline,
        handoff=True,
    )
    assert process.wait(ticket).state == "ack"
    return ticket


def wait_idle(process):
    with process._condition:
        assert process._condition.wait_for(lambda: process._running is None, 2)


def assert_queue_blocked(process, monkeypatch, target, *, deadline=None):
    """Observe an actual dequeue attempt; do not infer blocking from a short sleep."""
    checked, observations = threading.Event(), []
    original = process._next_locked

    def next_locked():
        pending = any(t._target is target for lane in process._lanes.values() for t in lane)
        result = original()
        if pending:
            observations.append(result)
            checked.set()
        return result

    monkeypatch.setattr(process, "_next_locked", next_locked)
    ticket = process.submit(
        target, AuditPhase.FINISH, deadline=time.monotonic() + 8 if deadline is None else deadline
    )
    assert checked.wait(2), "worker did not examine queued follower"
    with process._condition:
        assert observations[0][0] is None
        assert ticket._outcome is None
        assert not target.calls
    return ticket


@pytest.mark.parametrize("phase", [AuditPhase.ADOPT, AuditPhase.OBSERVE])
def test_ack_holds_follower_until_controller_releases(supervisor, monkeypatch, phase):
    first = gated(supervisor, phase)
    follower = Target()
    queued = assert_queue_blocked(supervisor, monkeypatch, follower)
    outcome = supervisor.poll(first)
    supervisor.release_handoff(first)
    assert supervisor.wait(queued).state == "ack"
    assert supervisor.poll(first) is outcome
    assert len(follower.calls) == 1
    supervisor.release_handoff(first)
    assert supervisor._handoff is None


@pytest.mark.parametrize("cleanup", ["cancel", "abandon", "close", "release_handoff"])
@pytest.mark.parametrize("phase", [AuditPhase.ADOPT, AuditPhase.OBSERVE])
def test_completed_handoff_cleanup_wakes_worker(supervisor, monkeypatch, phase, cleanup):
    first = gated(supervisor, phase)
    follower = Target()
    queued = assert_queue_blocked(supervisor, monkeypatch, follower)
    outcome = supervisor.poll(first)
    if cleanup == "close":
        supervisor.close()
        assert supervisor.wait(queued).state == "closed"
        assert not follower.calls
    else:
        getattr(supervisor, cleanup)(first)
        assert supervisor.wait(queued).state == "ack"
    assert supervisor.poll(first) is outcome
    assert supervisor._handoff is None


def test_handoff_expires_autonomously_on_original_deadline(supervisor, monkeypatch):
    deadline = time.monotonic() + 1
    first = gated(supervisor, deadline=deadline)
    follower = Target(entered=threading.Event())
    queued = assert_queue_blocked(supervisor, monkeypatch, follower)
    # No poll/submit/release is needed to wake the idle worker at the deadline.
    assert follower.entered.wait(3)
    assert time.monotonic() >= deadline
    assert supervisor.wait(queued).state == "ack"
    assert supervisor.poll(first).state == "ack"
    assert supervisor._handoff is None
    assert not supervisor.unavailable


def test_handoff_uses_remaining_budget_not_fresh_timeout(supervisor, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    entered, release = threading.Event(), threading.Event()
    first = supervisor.submit(
        Target(entered=entered, release=release), AuditPhase.ADOPT, deadline=110.0, handoff=True
    )
    try:
        assert entered.wait(2)
        clock[0] = 108.0
        release.set()
        assert supervisor.wait(first).state == "ack"
        with supervisor._condition:
            ticket, delay = supervisor._next_locked()
            assert ticket is None and delay == 2.0
            assert first._deadline == 110.0
            clock[0] = 110.0
            supervisor._condition.notify_all()
        with supervisor._condition:
            assert supervisor._condition.wait_for(lambda: supervisor._handoff is None, 2)
        assert supervisor.poll(first).state == "ack"
    finally:
        release.set()


@pytest.mark.parametrize("phase", list(AuditPhase))
def test_non_opted_in_commands_do_not_gate(supervisor, phase):
    payload = b"bytes" if phase is AuditPhase.APPEND else b""
    first = supervisor.submit(Target(), phase, deadline=time.monotonic() + 5, payload=payload)
    assert supervisor.wait(first).state == "ack"
    follower = supervisor.submit(Target(), AuditPhase.FINISH, deadline=time.monotonic() + 5)
    assert supervisor.wait(follower).state == "ack"
    assert supervisor._handoff is None


@pytest.mark.parametrize(
    "phase", [p for p in AuditPhase if p not in (AuditPhase.ADOPT, AuditPhase.OBSERVE)]
)
def test_only_adopt_observe_can_request_handoff(supervisor, phase):
    with pytest.raises((ValueError, TypeError)):
        supervisor.submit(Target(), phase, deadline=time.monotonic() + 5, handoff=True)
    assert supervisor._operations == 0 and supervisor._worker is None


@pytest.mark.parametrize("value", [None, 0, 1, "yes", [], object()])
def test_handoff_flag_requires_bool(supervisor, value):
    with pytest.raises((ValueError, TypeError)):
        supervisor.submit(Target(), AuditPhase.ADOPT, deadline=time.monotonic() + 5, handoff=value)
    assert supervisor._operations == 0 and supervisor._worker is None


def test_foreign_release_cannot_clear_gate(supervisor):
    first = gated(supervisor)
    foreign = module.ProcessAuditIo(limits=supervisor.limits, warn=lambda _: None, _key=module._KEY)
    try:
        with pytest.raises(ValueError, match="foreign"):
            foreign.release_handoff(first)
        with pytest.raises(ValueError, match="foreign"):
            supervisor.release_handoff(object())
        assert supervisor._handoff is first
        supervisor.release_handoff(first)
    finally:
        foreign.close()


def test_premature_release_does_not_waive_future_gate(supervisor, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    first = supervisor.submit(
        Target(entered=entered, release=release),
        AuditPhase.ADOPT,
        deadline=time.monotonic() + 5,
        handoff=True,
    )
    try:
        assert entered.wait(2)
        with pytest.raises(ValueError):
            supervisor.release_handoff(first)
        release.set()
        assert supervisor.wait(first).state == "ack"
        queued = assert_queue_blocked(supervisor, monkeypatch, Target())
        supervisor.release_handoff(first)
        assert supervisor.wait(queued).state == "ack"
    finally:
        release.set()


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("ordinary"),
        MemoryError("memory"),
        KeyboardInterrupt("interrupt"),
        SystemExit("exit"),
        asyncio.CancelledError("cancel"),
    ],
)
def test_failed_or_fatal_control_never_installs_gate(supervisor, error):
    ticket = supervisor.submit(
        Target(error=error), AuditPhase.ADOPT, deadline=time.monotonic() + 5, handoff=True
    )
    outcome = supervisor.wait(ticket)
    assert outcome.state == "failed"
    if isinstance(error, MemoryError) or not isinstance(error, Exception):
        assert outcome.error is error
    else:
        assert outcome.error is None
    follower = supervisor.submit(Target(), AuditPhase.FINISH, deadline=time.monotonic() + 5)
    assert supervisor.wait(follower).state == "ack"
    assert supervisor._handoff is None


@pytest.mark.parametrize("cleanup", ["cancel", "abandon"])
def test_running_unknown_never_installs_late_gate(supervisor, cleanup):
    entered, release = threading.Event(), threading.Event()
    first = supervisor.submit(
        Target(entered=entered, release=release),
        AuditPhase.ADOPT,
        deadline=time.monotonic() + 5,
        handoff=True,
    )
    try:
        assert entered.wait(2)
        getattr(supervisor, cleanup)(first)
        outcome = supervisor.poll(first)
        assert outcome.state == "unknown" and outcome.publication_uncertain
        assert supervisor._operations == 1
        assert supervisor._handoff is None
        release.set()
        wait_idle(supervisor)
        assert supervisor.poll(first) is outcome
        assert supervisor._operations == 0 and supervisor._handoff is None
        assert supervisor.unavailable
    finally:
        release.set()


def test_unknown_append_keeps_bytes_until_actual_return(supervisor):
    entered, release = threading.Event(), threading.Event()
    ticket = supervisor.submit(
        Target(entered=entered, release=release),
        AuditPhase.APPEND,
        payload=b"private payload",
        deadline=time.monotonic() + 5,
    )
    try:
        assert entered.wait(2)
        supervisor.abandon(ticket)
        assert supervisor.poll(ticket).state == "unknown"
        assert supervisor.retained_bytes == len(b"private payload")
        assert supervisor._operations == 1
        release.set()
        wait_idle(supervisor)
        assert supervisor.retained_bytes == 0 and supervisor._operations == 0
        assert supervisor.poll(ticket).state == "unknown"
    finally:
        release.set()


def test_gate_and_queue_share_operation_limit(supervisor):
    first = gated(supervisor)
    tickets = [
        supervisor.submit(Target(), AuditPhase.FINISH, deadline=time.monotonic() + 8)
        for _ in range(supervisor.limits.operations - 1)
    ]
    rejected = supervisor.submit(Target(), AuditPhase.FINISH, deadline=time.monotonic() + 8)
    assert supervisor.poll(rejected).state == "unavailable"
    assert supervisor.poll(rejected).not_executed
    assert supervisor.retained_bytes == 0 and not supervisor._captures
    supervisor.release_handoff(first)
    assert all(supervisor.wait(ticket).state == "ack" for ticket in tickets)
    assert supervisor._operations == 0 and supervisor.retained_bytes == 0


def test_stale_release_does_not_release_newer_gate(supervisor, monkeypatch):
    old = gated(supervisor)
    supervisor.release_handoff(old)
    current = gated(supervisor, AuditPhase.OBSERVE)
    supervisor.release_handoff(old)
    queued = assert_queue_blocked(supervisor, monkeypatch, Target())
    assert supervisor._handoff is current
    supervisor.release_handoff(current)
    assert supervisor.wait(queued).state == "ack"


def test_completed_gate_drops_target_and_release_drops_ticket(supervisor):
    target = Target()
    target_ref = weakref.ref(target)
    first = gated(supervisor, target=target)
    ticket_ref = weakref.ref(first)
    del target
    gc.collect()
    assert target_ref() is None
    assert first._target is None and not first._pieces and first._charge == 0
    supervisor.release_handoff(first)
    follower = supervisor.submit(Target(), AuditPhase.FINISH, deadline=time.monotonic() + 5)
    assert supervisor.wait(follower).state == "ack"
    del first
    gc.collect()
    assert ticket_ref() is None


def test_queued_handoff_cancellation_never_installs_gate(supervisor):
    entered, release = threading.Event(), threading.Event()
    running = supervisor.submit(
        Target(entered=entered, release=release),
        AuditPhase.MAINTAIN,
        deadline=time.monotonic() + 5,
    )
    try:
        assert entered.wait(2)
        cancelled_target = Target()
        cancelled = supervisor.submit(
            cancelled_target, AuditPhase.ADOPT, deadline=time.monotonic() + 5, handoff=True
        )
        supervisor.cancel(cancelled)
        assert supervisor.poll(cancelled).state == "cancelled"
        assert supervisor.poll(cancelled).not_executed
        release.set()
        assert supervisor.wait(running).state == "ack"
        follower = supervisor.submit(Target(), AuditPhase.FINISH, deadline=time.monotonic() + 5)
        assert supervisor.wait(follower).state == "ack"
        assert not cancelled_target.calls and supervisor._handoff is None
    finally:
        release.set()


def test_release_non_handoff_completed_ticket_is_harmless(supervisor):
    ordinary = supervisor.submit(Target(), AuditPhase.MAINTAIN, deadline=time.monotonic() + 5)
    assert supervisor.wait(ordinary).state == "ack"
    first = gated(supervisor)
    supervisor.release_handoff(ordinary)
    assert supervisor._handoff is first
    supervisor.release_handoff(first)


def test_process_identity_checked_before_gate_release(supervisor, monkeypatch):
    first = gated(supervisor)
    monkeypatch.setattr(module.os, "getpid", lambda: supervisor._pid + 1)
    try:
        with pytest.raises(RuntimeError, match="inherited"):
            supervisor.release_handoff(first)
        assert supervisor._handoff is first
    finally:
        monkeypatch.undo()
    supervisor.release_handoff(first)


def test_expired_handoff_request_never_runs(supervisor):
    target = Target()
    ticket = supervisor.submit(target, AuditPhase.ADOPT, deadline=time.monotonic() - 1, handoff=True)
    assert supervisor.wait(ticket).not_executed
    assert not target.calls and supervisor._handoff is None
    assert supervisor._worker is None
