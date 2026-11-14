"""Capture failure classification: three outcomes, and parity when the switch is off.

The switch selects whether a ticket that never executed is reported as such. With it
off, the notice must be exactly the one the base tree emits.
"""

import io
from dataclasses import replace

import pytest

from code_forge.invocation_buffered_capture import _failure_notice
from code_forge.invocation_stream import CaptureSnapshot

pytest_plugins = ["tests.test_invocation_io"]


def snapshot(**changes: object) -> CaptureSnapshot:
    base: dict[str, object] = dict(
        observed_bytes=0,
        accepted_bytes=0,
        truncated=False,
        interrupted=False,
        failed=False,
        write_uncertain=False,
        eof_seen=False,
        sealed=True,
    )
    base.update(changes)
    return CaptureSnapshot(**base)  # type: ignore[arg-type]


def test_switch_off_reports_the_same_notice_as_before():
    for shape in (
        snapshot(failed=True),
        snapshot(failed=True, write_uncertain=True),
        snapshot(failed=True, not_executed=True),
        snapshot(failed=True, write_uncertain=True, not_executed=True),
    ):
        assert _failure_notice(False, shape) == "capture_write_failed"


def test_switch_on_separates_the_three_outcomes():
    assert _failure_notice(True, snapshot(failed=True, not_executed=True)) == "capture_not_executed"
    assert (
        _failure_notice(True, snapshot(failed=True, write_uncertain=True)) == "capture_write_uncertain"
    )
    assert _failure_notice(True, snapshot(failed=True)) == "capture_write_failed"


def test_never_executed_outranks_uncertain_when_both_are_set():
    both = snapshot(failed=True, not_executed=True, write_uncertain=True)
    assert _failure_notice(True, both) == "capture_not_executed"


def test_snapshot_defaults_to_not_executed_false():
    assert snapshot(failed=True).not_executed is False


def test_shared_session_rejects_a_non_bool_switch(supervisor):
    process, _ = supervisor
    from code_forge.invocation_buffered_capture import SharedBufferedCaptureSession

    with pytest.raises(TypeError):
        SharedBufferedCaptureSession(
            process,
            buffer_bytes=4096,
            batch_bytes=512,
            max_delay=0,
            scoped_fault_domain=1,
        )


def test_builder_carries_the_runtime_classification():
    from code_forge.invocation_buffered_capture import _snapshot_from

    built = _snapshot_from(
        snapshot(), accepted_bytes=0, failed=True, write_uncertain=False, not_executed=True
    )
    assert built.not_executed is True


def test_reconcile_records_the_never_executed_outcome():
    """White-box: the reconcile loop must keep the flag instead of folding it away."""
    from collections import deque
    from types import SimpleNamespace

    from code_forge.invocation_buffered_capture import _SharedWireCapture
    from code_forge.invocation_io import IoOutcome

    class FakeIo:
        outcome = IoOutcome("unavailable", not_executed=True)

        def _result_locked(self, ticket, handoff=False):
            return self.outcome

    capture = object.__new__(_SharedWireCapture)
    capture._session = SimpleNamespace(_io=FakeIo())
    capture._tickets = deque([object()])
    capture._snapshot = None
    capture._fatal_ticket = None
    capture._failed = False
    capture._uncertain = False
    capture._not_executed = False
    capture._committed = 0
    capture._pending = 0

    capture._reconcile_locked()

    assert capture._failed is True
    assert capture._not_executed is True
    assert capture._uncertain is False


def test_host_threads_the_switch_into_its_captures(tmp_path):
    import uuid

    from code_forge.invocation_audit import InvocationContext
    from code_forge.invocation_host import HostAuditSession

    context = InvocationContext(str(uuid.uuid4()), None, None, None, None, None, None, "review", None)
    session = HostAuditSession(artifact_root=tmp_path, context=context, scoped_fault_domain=True)
    session._ensure_io()
    assert session._captures._scoped_fault_domain is True


def _opened_recorder(root, *, scoped):
    import uuid

    from code_forge.invocation_audit import AttemptRecorder, InvocationContext

    # A manifest only validates when the context carries the hash and snapshot it names,
    # so a recorder built without them would deny every publish for the wrong reason.
    context = InvocationContext(
        str(uuid.uuid4()), "a" * 64, str(uuid.uuid4()), None, None, None, None, "review", None
    )
    recorder = AttemptRecorder(
        root,
        context=context,
        monotonic=lambda: 1.0,
        utc_now=lambda: "2026-11-12T00:00:00Z",
        audit_schema_version=2,
        scoped_fault_domain=scoped,
    )
    assert recorder.open().admitted
    return recorder


def _publish(rec):
    return rec.publish_input_manifest(
        dict(
            schema_version=1,
            run_id=rec._context["run_id"],
            snapshot_id=rec._context["snapshot_id"],
            mode="diff",
            legacy_source_hash=rec._context["source_hash"],
            exact_diff_sha256="d" * 64,
            diff_bytes=1,
            files=[],
            repositories=[],
        )
    )


@pytest.mark.parametrize("scoped,expected", [(True, "identity_conflict"), (False, "integrity_error")])
def test_capability_mismatch_code_depends_on_the_switch(tmp_path, scoped, expected):
    rec = _opened_recorder(tmp_path, scoped=scoped)
    capability = rec._capability
    rec._capability = replace(capability, root_inode=capability.root_inode + 1)
    ack = _publish(rec)
    assert not ack.admitted
    assert ack.fault.code == expected


def _journal_events(tmp_path):
    """Decode the length-prefixed journal frames this recorder writes."""
    import json

    content = next(tmp_path.glob("*/journal")).read_bytes()
    events, offset = [], 0
    while offset < len(content):
        size = int(content[offset : offset + 8], 16)
        events.append(json.loads(content[offset + 9 : offset + 9 + size]))
        offset += size + 10
    return events


def _handle(root, *, create=True):
    """The root helper reports a fault instead of raising; tests need a live handle."""
    from code_forge import invocation_audit as audit

    prepared = audit._prepare_audit_root(root, create=create)
    assert isinstance(prepared, audit._RootHandle), prepared
    return prepared


def test_a_second_close_is_a_no_op(tmp_path, monkeypatch):
    """The number is spent on the first attempt, so a retry must not close twice."""
    from code_forge import invocation_audit as audit

    handle = _handle(tmp_path)
    closed = []
    real = audit.os.close

    def counting(fd):
        closed.append(fd)
        real(fd)

    monkeypatch.setattr(audit.os, "close", counting)
    handle.close()
    handle.close()
    assert closed == [closed[0]]


def _fail_the_next_close(monkeypatch, error=None):
    """Break the release itself, not every close in the process."""
    from code_forge import invocation_audit as audit

    def close(self):
        spent, self.fd = self.fd, -1
        if spent >= 0:
            audit.os.close(spent)
        raise error or OSError("release refused")

    monkeypatch.setattr(audit._RootHandle, "close", close)


@pytest.mark.parametrize("scoped,expected", [(True, "cleanup_unresolved"), (False, "persistence_error")])
def test_close_failure_code_depends_on_the_switch(tmp_path, monkeypatch, scoped, expected):
    rec = _opened_recorder(tmp_path, scoped=scoped)
    _fail_the_next_close(monkeypatch)
    ack = _publish(rec)
    assert not ack.admitted
    assert ack.fault.code == expected


def test_scoped_allocation_stops_after_an_unconfirmed_release(tmp_path):
    rec = _opened_recorder(tmp_path, scoped=True)
    # A scoped context: undo() would also tear down the conftest autouse patches.
    with pytest.MonkeyPatch.context() as patch:
        _fail_the_next_close(patch)
        assert _publish(rec).fault.code == "cleanup_unresolved"
    stopped = _publish(rec)
    assert not stopped.admitted
    assert stopped.fault.code == "cleanup_unresolved"


def test_unscoped_recorder_keeps_allocating_after_a_close_failure(tmp_path):
    rec = _opened_recorder(tmp_path, scoped=False)
    with pytest.MonkeyPatch.context() as patch:
        _fail_the_next_close(patch)
        assert _publish(rec).fault.code == "persistence_error"
    # No stop was installed, so the next allocation still runs.
    assert _publish(rec).admitted is True


def test_interrupts_still_propagate_through_a_close(tmp_path, monkeypatch):
    from code_forge import invocation_audit as audit

    handle = _handle(tmp_path)

    def interrupted(fd):
        raise KeyboardInterrupt()

    monkeypatch.setattr(audit.os, "close", interrupted)
    with pytest.raises(KeyboardInterrupt):
        handle.close()


def test_pending_ticket_at_seal_is_uncertain_not_merely_failed(supervisor):
    """A write still in flight at seal may have landed, so it is uncertainty.

    Pins existing behaviour: the session already marks the dispatch uncertain, so the
    seal site needs no extra term for outstanding tickets.
    """
    import threading

    from tests.test_invocation_io import capture, shared

    process, _warnings = supervisor
    release = threading.Event()
    entered = threading.Event()

    def blocking_write(data):
        entered.set()
        assert release.wait(2)
        return len(data)

    session = shared(process, flush_timeout_s=0.05, scoped_fault_domain=True)
    notices: list[str] = []
    cap = capture(session, blocking_write, warn=notices.append)
    writer = threading.Thread(target=lambda: list(cap.chunks(io.BytesIO(b"abcd"))))
    try:
        writer.start()
        assert entered.wait(2), "the write never started, so no ticket was outstanding"
        snapshot_state = cap.seal()
        assert snapshot_state.failed is True
        assert snapshot_state.write_uncertain is True, (
            "an outstanding write must not read as a clean failure"
        )
        # This path emits the chain diagnostic first, so the notice text is pinned
        # elsewhere; what belongs here is the snapshot state it would have been built from.
        assert notices and "capture_write_failed" not in notices
    finally:
        release.set()
        writer.join(2)
        session.close()


def test_the_switch_reaches_the_captures(supervisor):
    """The lane is what every shared capture reads, so the flag has to travel with it."""
    from tests.test_invocation_io import capture, shared

    process, _warnings = supervisor
    on = shared(process, scoped_fault_domain=True)
    off = shared(process)
    try:
        for session, expected in ((on, True), (off, False)):
            cap = capture(session, len)
            assert cap._scoped is expected, (expected, cap._session.__class__.__name__)
    finally:
        on.close()
        off.close()


def test_settlement_still_reaches_the_journal_after_an_unconfirmed_release(tmp_path):
    """Stopping allocation must not stop the run from recording that it ended."""
    rec = _opened_recorder(tmp_path, scoped=True)
    with pytest.MonkeyPatch.context() as patch:
        _fail_the_next_close(patch)
        assert _publish(rec).fault.code == "cleanup_unresolved"
    # The run must still be able to record that it ended, and must report itself as
    # incomplete rather than as a clean run.
    final = rec.finalize("incomplete")
    assert final.admitted is True
    assert rec.snapshot().audit_complete is False


def test_an_interrupt_from_close_also_stops_allocation(tmp_path):
    rec = _opened_recorder(tmp_path, scoped=True)
    with pytest.MonkeyPatch.context() as patch:
        _fail_the_next_close(patch, KeyboardInterrupt())
        with pytest.raises(KeyboardInterrupt):
            _publish(rec)
    stopped = _publish(rec)
    assert stopped.fault.code == "cleanup_unresolved"


def test_cleanup_unresolved_is_part_of_the_fault_vocabulary():
    from code_forge.invocation_audit import _FAULTS

    assert "cleanup_unresolved" in _FAULTS


def test_an_unconfirmed_release_keeps_the_run_from_claiming_completeness(tmp_path):
    """The terminal record has to say the run was not clean, not just deny it in memory."""
    rec = _opened_recorder(tmp_path, scoped=True)
    with pytest.MonkeyPatch.context() as patch:
        _fail_the_next_close(patch)
        assert _publish(rec).fault.code == "cleanup_unresolved"
    assert rec.snapshot().audit_complete is False


def test_an_interrupt_keeps_the_run_from_claiming_completeness(tmp_path):
    """Nothing is journaled on the interrupt path, so the flag is the only evidence."""
    rec = _opened_recorder(tmp_path, scoped=True)
    with pytest.MonkeyPatch.context() as patch:
        _fail_the_next_close(patch, KeyboardInterrupt())
        with pytest.raises(KeyboardInterrupt):
            _publish(rec)
    assert rec.snapshot().audit_complete is False


def test_the_switch_changes_the_notice_a_session_emits(supervisor, monkeypatch):
    """Stub only the io outcome: the session, the reconcile and the notice are real."""
    from code_forge import invocation_io
    from tests.test_invocation_io import capture, shared

    process, _warnings = supervisor
    real = invocation_io.ProcessAuditIo._result_locked

    def expired(self, ticket, handoff=False):
        if getattr(ticket, "_target", None) is not None:
            return invocation_io.IoOutcome("expired", not_executed=True)
        return real(self, ticket, handoff=handoff)

    monkeypatch.setattr(invocation_io.ProcessAuditIo, "_result_locked", expired)

    seen = {}
    for scoped in (True, False):
        notices: list[str] = []
        session = shared(process, flush_timeout_s=0.02, scoped_fault_domain=scoped)
        try:
            cap = capture(session, len, warn=notices.append)
            cap.append(b"data: {}\n\n")
            state = cap.seal()
            assert state.not_executed is True
            assert process._blocked is False
            seen[scoped] = notices[-1]
        finally:
            session.close()
    assert seen[True] == "capture_not_executed"
    assert seen[False] == "capture_write_failed"


def test_settlement_writes_the_terminal_record(tmp_path):
    """The terminal record is on disk, not only in memory."""

    rec = _opened_recorder(tmp_path, scoped=True)
    with pytest.MonkeyPatch.context() as patch:
        _fail_the_next_close(patch)
        assert _publish(rec).fault.code == "cleanup_unresolved"
    assert rec.finalize("incomplete").admitted
    events = _journal_events(tmp_path)
    finals = [event for event in events if event.get("event") == "run_final"]
    assert finals and finals[-1]["audit_complete"] is False
    # The terminal marker is a separate write; a run that recorded the event but lost
    # the marker reads as unfinished to every later reader.
    assert list(tmp_path.glob("*/final")), "the terminal marker was not published"


def test_freeze_is_reachable_after_an_unconfirmed_release(tmp_path):
    """Freezing writes terminal evidence, so the stop must not refuse it."""
    rec = _opened_recorder(tmp_path, scoped=True)
    with pytest.MonkeyPatch.context() as patch:
        _fail_the_next_close(patch)
        assert _publish(rec).fault.code == "cleanup_unresolved"
    assert rec.set_frozen(True).admitted
    events = _journal_events(tmp_path)
    assert any(event.get("event") == "freeze" for event in events), [e.get("event") for e in events]


def test_committed_bytes_are_not_reported_as_never_executed():
    """A capture that stored data did execute; the warning would invite a resend."""
    assert (
        _failure_notice(True, snapshot(failed=True, not_executed=True, accepted_bytes=4))
        == "capture_write_failed"
    )
