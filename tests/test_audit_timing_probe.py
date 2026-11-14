# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Controlled private experiments; no inference of historical CI causality."""

import threading
import time
import pytest
from code_forge import invocation_io as io
from tests.audit_timing_probe import AuditTimingProbe


@pytest.fixture
def process(monkeypatch):
    monkeypatch.setattr(io, "_PROCESS", None)
    process = io.process_audit_io(limits=io.IoLimits(32, 8192, 512, 8), warn=lambda _: None)
    yield process
    process.close()
    if process._worker:
        process._worker.join(3)
        assert not process._worker.is_alive()


class Target:
    def __init__(self, function=object):
        self.function = function

    def audit_io(self, phase, data):
        return self.function()


def test_clean_ack_and_deadline_unchanged(process, monkeypatch):
    probe = AuditTimingProbe()
    probe.install(monkeypatch, process)
    value = object()
    deadline = time.monotonic() + 2
    ticket = process.submit(Target(lambda: value), io.AuditPhase.BEGIN, deadline=deadline)
    outcome = process.wait(ticket)
    assert outcome.state == "ack" and outcome.value is value
    assert ticket._deadline == deadline
    events = probe.snapshot()
    assert len({e["ticket"] for e in events}) == 1
    boundaries = [e["boundary"] for e in events]
    assert (
        boundaries.index("submitted")
        < boundaries.index("worker_selected")
        < boundaries.index("target_enter")
        < boundaries.index("target_return")
        < boundaries.index("outcome_published")
        < boundaries.index("wait_return")
    )
    assert all(e["phase"] == "begin_stage" for e in events)


@pytest.mark.parametrize("where", ["inside_target", "after_target"])
def test_delayed_target_and_delayed_publication_have_different_evidence(process, monkeypatch, where):
    from types import SimpleNamespace

    clock = [time.monotonic()]
    monkeypatch.setattr(io, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    entered, release = threading.Event(), threading.Event()

    def hold():
        entered.set()
        assert release.wait(3)

    probe = AuditTimingProbe()
    probe.install(monkeypatch, process)
    if where == "after_target":
        observed_call = io._call_target

        def delayed(*args):
            result = observed_call(*args)
            hold()
            return result

        monkeypatch.setattr(io, "_call_target", delayed)
    ticket = process.submit(
        Target(hold if where == "inside_target" else lambda: None),
        io.AuditPhase.BEGIN,
        deadline=clock[0] + 0.08,
    )
    try:
        assert entered.wait(1)
        clock[0] += 0.09
        outcome = process.wait(ticket)
        assert outcome.state == "unknown" and outcome.publication_uncertain
        before_release = probe.snapshot()
        assert any(e["boundary"] == "target_return" for e in before_release) is (where == "after_target")
        assert not any(e["boundary"] == "outcome_published" for e in before_release)
        assert any(e["boundary"] == "wait_return" and e["state"] == "unknown" for e in before_release)
    finally:
        release.set()
    with process._condition:
        assert process._condition.wait_for(lambda: process._running is None, timeout=1)
    assert process.poll(ticket) is outcome
    assert any(
        e["boundary"] == "outcome_published" and e["state"] == "unknown" for e in probe.snapshot()
    )


@pytest.mark.parametrize("diagnostic_error", [MemoryError, KeyboardInterrupt, SystemExit])
def test_diagnostic_failure_preserves_real_ack(process, monkeypatch, diagnostic_error):
    probe = AuditTimingProbe()

    def broken(*args):
        raise diagnostic_error("PRIVATE")

    monkeypatch.setattr(probe, "_emit", broken)
    probe.install(monkeypatch, process)
    value = object()
    ticket = process.submit(Target(lambda: value), io.AuditPhase.BEGIN, deadline=time.monotonic() + 2)
    assert process.wait(ticket).value is value
    assert probe.report()["observation_failed"] is True


def test_fatal_target_and_wait_exception_identity_preserved(process, monkeypatch):
    original = KeyboardInterrupt("PRIVATE")

    def fail():
        raise original

    probe = AuditTimingProbe()
    probe.install(monkeypatch, process)
    ticket = process.submit(Target(fail), io.AuditPhase.BEGIN, deadline=time.monotonic() + 2)
    outcome = process.wait(ticket)
    assert outcome.error is original
    assert outcome.state == "failed"
    assert "PRIVATE" not in repr(probe.snapshot())


def test_bounded_metadata_and_no_target_retention(process, monkeypatch):
    import gc
    import weakref

    probe = AuditTimingProbe()
    probe.install(monkeypatch, process)
    refs = []
    for _ in range(150):
        target = Target()
        refs.append(weakref.ref(target))
        ticket = process.submit(target, io.AuditPhase.BEGIN, deadline=time.monotonic() + 2)
        process.wait(ticket)
    del target, ticket
    gc.collect()
    assert all(ref() is None for ref in refs)
    assert len(probe.events) == 128 and len(probe._tickets) <= 128
    assert all(
        set(e) <= {"boundary", "ticket", "phase", "at_ns", "deadline_remaining_ns", "sequence", "state"}
        for e in probe.snapshot()
    )


def test_original_wait_exception_identity(process, monkeypatch):
    original = SystemExit("PRIVATE")

    def fail(ticket):
        raise original

    monkeypatch.setattr(process, "wait", fail)
    probe = AuditTimingProbe()
    probe.install(monkeypatch, process)
    with pytest.raises(SystemExit) as caught:
        process.wait(object())
    assert caught.value is original
    assert probe.snapshot() == []


def test_recorder_boundary_nested_in_target(process, monkeypatch):
    class Recorder:
        def begin_stage(self):
            return object()

    recorder = Recorder()
    probe = AuditTimingProbe()
    probe.install(monkeypatch, process)
    probe.install_recorder(monkeypatch, process, Recorder)
    ticket = process.submit(
        Target(recorder.begin_stage), io.AuditPhase.BEGIN, deadline=time.monotonic() + 2
    )
    assert process.wait(ticket).state == "ack"
    b = [e["boundary"] for e in probe.snapshot()]
    assert (
        b.index("target_enter")
        < b.index("recorder_enter")
        < b.index("recorder_return")
        < b.index("target_return")
    )


def test_foreign_worker_is_not_observed(process, monkeypatch):
    probe = AuditTimingProbe()
    probe.install(monkeypatch, process)
    other = io.ProcessAuditIo(limits=io.IoLimits(32, 8192, 512, 8), warn=lambda _: None, _key=io._KEY)
    try:
        ticket = other.submit(Target(), io.AuditPhase.BEGIN, deadline=time.monotonic() + 2)
        assert other.wait(ticket).state == "ack"
        assert probe.snapshot() == []
    finally:
        other.close()
        other._worker.join(3)
        assert not other._worker.is_alive()


def test_hostile_ticket_never_inspected(process, monkeypatch):
    class Private(io.IoTicket):
        def __getattribute__(self, name):
            raise AssertionError("PRIVATE")

    probe = AuditTimingProbe()
    probe._note("wait_enter", object.__new__(Private))
    assert probe.snapshot() == []


def test_first_failure_window_survives_eviction(process, monkeypatch):
    probe = AuditTimingProbe()
    probe.install(monkeypatch, process)
    ticket = process.submit(Target(), io.AuditPhase.BEGIN, deadline=time.monotonic() + 2)
    process.wait(ticket)
    probe._note("unknown_observed", ticket, io.IoOutcome("unknown", publication_uncertain=True))
    first = probe.first_failure_window
    assert first[-1]["state"] == "unknown"
    for _ in range(200):
        probe._note("wait_enter", ticket)
    assert probe.first_failure_window is first
    assert len(first) <= 128 and len(probe.events) == 128
    assert probe.dropped_events_lower_bound > 0


def test_concurrent_first_observation_uses_one_ordinal(process, monkeypatch):
    probe = AuditTimingProbe()
    ticket = process.submit(Target(), io.AuditPhase.BEGIN, deadline=time.monotonic() + 2)
    process.wait(ticket)
    barrier = threading.Barrier(2)

    class InterleavedSerial:
        def __next__(self):
            barrier.wait(timeout=2)
            return threading.get_ident()

    probe._serial = InterleavedSerial()
    threads = [threading.Thread(target=probe._note, args=("wait_enter", ticket)) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3)
        assert not thread.is_alive()
    assert len(probe.events) == 2
    assert len({event["ticket"] for event in probe.snapshot()}) == 1


def test_snapshot_failure_is_explicit_and_not_raised():
    probe = AuditTimingProbe()

    class Broken:
        def __iter__(self):
            raise MemoryError("PRIVATE")

    probe.events = Broken()
    assert probe.snapshot() == [] and probe.snapshot_failed


def test_report_exports_integrity_metadata():
    probe = AuditTimingProbe()
    probe.dropped_events_lower_bound = 4
    probe.observation_failed = True
    probe.snapshot_failed = True
    assert probe.report() == dict(
        events=[],
        first_failure_window=None,
        dropped_events_lower_bound=4,
        window_may_be_truncated=False,
        observation_failed=True,
        snapshot_failed=True,
    )


def test_concurrent_capacity_crossing_cannot_claim_complete(process):
    from collections import deque

    probe = AuditTimingProbe()
    ticket = process.submit(Target(), io.AuditPhase.BEGIN, deadline=time.monotonic() + 2)
    process.wait(ticket)
    for _ in range(127):
        probe._note("wait_enter", ticket)
    barrier = threading.Barrier(2)

    class InterleavedAppend(deque):
        def append(self, event):
            barrier.wait(timeout=2)
            return super().append(event)

    probe.events = InterleavedAppend(probe.events, maxlen=128)
    threads = [threading.Thread(target=probe._note, args=("wait_enter", ticket)) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3)
        assert not thread.is_alive()
    assert probe.report()["window_may_be_truncated"] is True
    assert not probe.observation_failed


@pytest.mark.parametrize("site", ["target", "recorder"])
def test_qualification_failure_cannot_prevent_original_call(process, monkeypatch, site):
    from types import SimpleNamespace
    import tests.audit_timing_probe as module

    probe = AuditTimingProbe()

    class Recorder:
        def begin_stage(self):
            return value

    value = object()
    target = Target(lambda: value)
    ticket = process.submit(target, io.AuditPhase.BEGIN, deadline=time.monotonic() + 2)
    process.wait(ticket)
    monkeypatch.setattr(process, "_running", ticket)
    monkeypatch.setattr(ticket, "_target", target)

    def fail():
        raise MemoryError("PRIVATE")

    monkeypatch.setattr(module, "threading", SimpleNamespace(current_thread=fail))
    probe.install(monkeypatch, process)
    probe.install_recorder(monkeypatch, process, Recorder)
    if site == "target":
        assert io._call_target(target, io.AuditPhase.BEGIN, b"", 0) == (value, None)
    else:
        assert Recorder().begin_stage() is value
    assert probe.report()["observation_failed"] is True
    monkeypatch.setattr(process, "_running", None)


def test_generic_detailed_probe_does_not_install_unused_timing(process, monkeypatch):
    from tests.audit_diagnostic_probe import AuditDiagnosticProbe

    probe = AuditDiagnosticProbe(detailed=True)
    original = process._next_locked
    probe.install(monkeypatch, process)
    assert probe.timing is None and process._next_locked == original
