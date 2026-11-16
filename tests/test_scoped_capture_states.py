"""Capture failure classification: three outcomes, and parity when the switch is off.

The switch selects whether a ticket that never executed is reported as such. With it
off, the notice must be exactly the one the base tree emits.
"""

import io
import uuid
from dataclasses import replace

import pytest

from code_forge.invocation_buffered_capture import _failure_notice
from code_forge.invocation_host import HostAuditSession
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


def test_uncertain_write_outranks_a_different_unexecuted_ticket():
    both = snapshot(failed=True, not_executed=True, write_uncertain=True)
    assert _failure_notice(True, both) == "capture_write_uncertain"


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


def test_host_threads_the_switch_into_its_captures(tmp_path, monkeypatch):
    import uuid

    from code_forge import invocation_io
    from code_forge.invocation_audit import InvocationContext
    from code_forge.invocation_host import HostAuditSession

    # The audit I/O supervisor is process-wide; take it over for this test and
    # put the module back the way the rest of the suite expects to find it.
    monkeypatch.setattr(invocation_io, "_PROCESS", None)
    context = InvocationContext(str(uuid.uuid4()), None, None, None, None, None, None, "review", None)
    session = HostAuditSession(artifact_root=tmp_path, context=context, scoped_fault_domain=True)
    try:
        session._ensure_io()
        assert session._captures._scoped_fault_domain is True
    finally:
        if session._io is not None:
            session._io.close()


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
        assert notices == ["audit_io_unresolved_process"]
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
    """An interrupt during release leaves the run unable to claim completeness."""
    rec = _opened_recorder(tmp_path, scoped=True)
    with pytest.MonkeyPatch.context() as patch:
        _fail_the_next_close(patch, KeyboardInterrupt())
        with pytest.raises(KeyboardInterrupt):
            _publish(rec)
    assert rec.snapshot().audit_complete is False


def test_the_switch_changes_the_notice_a_session_emits(supervisor):
    """A real expired submit warns early, then refines its outcome at seal."""
    import time

    from tests.test_invocation_io import shared

    process, _warnings = supervisor
    for scoped in (True, False):
        notices, writes = [], []
        session = shared(process, scoped_fault_domain=scoped)
        try:
            cap = session.new_capture(
                write=lambda data, writes=writes: writes.append(data) or len(data),
                warn=notices.append,
                limit_bytes=100,
                action_deadline=time.monotonic() - 1,
                chunk_bytes=128,
            )
            cap.append(b"data: {}\n\n")
            assert notices == ["capture_write_failed"]
            state = cap.seal()
            assert state.not_executed is True and not state.write_uncertain
            assert process._blocked is False and writes == []
            expected = ["capture_write_failed"] + (["capture_not_executed"] if scoped else [])
            assert notices == expected
            assert cap.seal() is state and notices == expected
        finally:
            session.close()


@pytest.mark.parametrize("strict", [False, True])
def test_uncertain_write_refines_an_earlier_capture_warning(supervisor, strict):
    import threading

    from code_forge.invocation_stream import CaptureUnavailable
    from tests.test_invocation_io import capture, shared

    process, _ = supervisor
    entered, release = threading.Event(), threading.Event()
    writes, notices = [], []

    def write(data):
        writes.append(data)
        entered.set()
        assert release.wait(2)
        raise OSError("private sink failure")

    session = shared(process, scoped_fault_domain=True)
    cap = capture(session, write, warn=notices.append, strict=strict)
    try:
        if strict:
            release.set()
            with pytest.raises(CaptureUnavailable):
                cap.append(b"abcd")
        else:
            cap.append(b"abcd")
            assert entered.wait(2)
            release.set()
        assert entered.wait(2)
        with process._condition:
            assert process._condition.wait_for(lambda: process._running is None, timeout=2)
        cap.append(b"efgh")
        assert notices == ["capture_write_failed"]
        if strict:
            with pytest.raises(CaptureUnavailable) as caught:
                cap.seal()
            assert caught.value.args == ("capture_write_uncertain",)
        else:
            state = cap.seal()
            assert state.write_uncertain and state.accepted_bytes == 0
        assert writes == [b"abcd"] and process._blocked is False
        assert notices == ["capture_write_failed", "capture_write_uncertain"]
    finally:
        release.set()
        session.close()


def test_an_unexecuted_ticket_cannot_hide_a_real_unacknowledged_write(supervisor):
    import threading

    from tests.test_invocation_io import capture, shared

    process, _ = supervisor
    entered, release = threading.Event(), threading.Event()
    stored, notices = io.BytesIO(), []

    def write(data):
        stored.write(data)
        entered.set()
        assert release.wait(2)
        raise OSError("the write happened but its acknowledgement was lost")

    session = shared(process, scoped_fault_domain=True)
    cap = capture(session, write, warn=notices.append)
    try:
        cap.append(b"abcd")
        assert entered.wait(2)
        cap.append(b"efgh")
        release.set()
        with process._condition:
            assert process._condition.wait_for(lambda: process._running is None, timeout=2)
        state = cap.seal()
        assert state.not_executed and state.write_uncertain
        assert state.accepted_bytes == 0 and stored.getvalue() == b"abcd"
        assert process._blocked is False
        assert notices == ["capture_write_uncertain"]
    finally:
        release.set()
        session.close()


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


def test_a_blocked_process_with_the_switch_off_still_delivers_one_notice(supervisor):
    import time

    from code_forge.invocation_buffered_capture import SharedBufferedCaptureSession

    process, _ = supervisor
    delivered = []
    session = SharedBufferedCaptureSession(process, buffer_bytes=4096, batch_bytes=512, max_delay=0)
    try:
        capture = session.new_capture(
            write=len,
            warn=delivered.append,
            limit_bytes=100,
            action_deadline=time.monotonic() + 1,
            chunk_bytes=4,
        )
        capture._notice("capture_write_failed")
        process._blocked = True
        # The shared capture rewrites this to the terminal code before claiming;
        # with the switch off the earlier generic notice must still suppress it.
        capture._notice("capture_tail_limit")
        assert delivered == ["capture_write_failed"]
    finally:
        process._blocked = False
        session.close()


def test_a_raising_warning_consumer_marks_the_capture_failed():
    import time

    from code_forge.invocation_buffered_capture import BufferedCaptureSession

    def warn(code):
        raise RuntimeError("warning consumer failed")

    session = BufferedCaptureSession(buffer_bytes=8, batch_bytes=4, max_delay=0)
    try:
        capture = session.new_capture(
            write=len,
            warn=warn,
            limit_bytes=100,
            action_deadline=time.monotonic() + 1,
            chunk_bytes=4,
        )
        capture._notice("capture_write_failed")
        assert capture.seal().failed is True
    finally:
        session.close()


def test_a_clean_serial_capture_seals_without_a_notice():
    import time

    from code_forge.invocation_buffered_capture import BufferedCaptureSession

    delivered = []
    session = BufferedCaptureSession(buffer_bytes=8, batch_bytes=4, max_delay=0)
    try:
        capture = session.new_capture(
            write=len,
            warn=delivered.append,
            limit_bytes=100,
            action_deadline=time.monotonic() + 1,
            chunk_bytes=4,
        )
        capture.append(b"abcd")
        sealed = capture.seal()
        assert sealed.failed is False
        assert delivered == []
    finally:
        session.close()


def test_a_capture_opened_on_a_closed_lane_reports_a_write_failure(supervisor):
    import time

    from code_forge.invocation_buffered_capture import SharedBufferedCaptureSession

    process, _ = supervisor
    delivered = []
    session = SharedBufferedCaptureSession(process, buffer_bytes=4096, batch_bytes=512, max_delay=0)
    session.close()
    session.new_capture(
        write=len,
        warn=delivered.append,
        limit_bytes=100,
        action_deadline=time.monotonic() + 1,
        chunk_bytes=4,
    )
    assert delivered == ["capture_write_failed"]


def test_a_post_done_drain_reason_outside_the_closed_set_is_refused():
    from code_forge.invocation_buffered_capture import BufferedCaptureSession
    from code_forge.invocation_stream import _CAPTURE_TAIL_CODES

    assert _CAPTURE_TAIL_CODES == frozenset(
        {"capture_tail_limit", "capture_tail_deadline", "capture_tail_interrupted"}
    )
    session = BufferedCaptureSession(buffer_bytes=8, batch_bytes=4, max_delay=0)
    try:
        capture = session.new_capture(
            write=len,
            warn=len,
            limit_bytes=100,
            action_deadline=__import__("time").monotonic() + 1,
            chunk_bytes=4,
        )
        with pytest.raises(ValueError):
            capture.report_incomplete("capture_tail_bogus")
    finally:
        session.close()


def test_a_sealed_capture_with_only_an_uncertain_write_still_warns():
    import time

    from code_forge.invocation_buffered_capture import BufferedCaptureSession

    delivered = []
    session = BufferedCaptureSession(buffer_bytes=64, batch_bytes=4, max_delay=0)
    try:
        capture = session.new_capture(
            write=len,
            warn=delivered.append,
            limit_bytes=100,
            action_deadline=time.monotonic() + 5,
            chunk_bytes=4,
        )
        capture.append(b"abcd")
        # The sink accepted the bytes without confirming publication: the seal
        # reports uncertainty with no failure, and the notice must still fire.
        capture._uncertain = True
        sealed = capture.seal()
        assert sealed.failed is False
        assert sealed.write_uncertain is True
        assert delivered == ["capture_write_failed"]
    finally:
        session.close()


def test_a_shared_sealed_capture_with_only_an_uncertain_write_still_warns(supervisor):
    import time

    from code_forge.invocation_buffered_capture import SharedBufferedCaptureSession

    process, _ = supervisor
    delivered = []
    session = SharedBufferedCaptureSession(process, buffer_bytes=4096, batch_bytes=512, max_delay=0)
    try:
        capture = session.new_capture(
            write=len,
            warn=delivered.append,
            limit_bytes=100,
            action_deadline=time.monotonic() + 5,
            chunk_bytes=4,
        )
        capture.append(b"abcd")
        capture._uncertain = True
        sealed = capture.seal()
        assert sealed.failed is False
        assert sealed.write_uncertain is True
        assert delivered == ["capture_write_failed"]
    finally:
        session.close()


def test_a_post_done_reason_of_the_wrong_type_is_refused_by_type():
    class Hostile(str):
        def __hash__(self):
            raise RuntimeError("hostile __hash__")

    from code_forge.invocation_buffered_capture import BufferedCaptureSession

    session = BufferedCaptureSession(buffer_bytes=8, batch_bytes=4, max_delay=0)
    try:
        capture = session.new_capture(
            write=len,
            warn=len,
            limit_bytes=100,
            action_deadline=__import__("time").monotonic() + 1,
            chunk_bytes=4,
        )
        with pytest.raises(ValueError):
            capture.report_incomplete(Hostile("capture_tail_limit"))
    finally:
        session.close()


def test_an_unconfirmed_ticket_alone_makes_the_capture_uncertain():
    from code_forge.invocation_buffered_capture import _settled_uncertain

    class Acquired:
        def __init__(self, write_uncertain):
            self.write_uncertain = write_uncertain

    assert _settled_uncertain(Acquired(True), False) is True
    assert _settled_uncertain(Acquired(False), True) is True
    assert _settled_uncertain(Acquired(False), False, True) is True
    assert _settled_uncertain(Acquired(False), False) is False


def test_the_shared_seal_reads_the_ticket_verdict_not_only_the_capture_flag():
    """The shared outlet is the production seam: a ticket whose publication was never
    acknowledged must still reach the notice, even when the capture flag is clear."""
    from pathlib import Path

    from code_forge import invocation_buffered_capture as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    shared = source[source.rindex("write_uncertain=_settled_uncertain") :]
    assert shared.startswith("write_uncertain=_settled_uncertain(acquired, self._uncertain)")


def test_an_interrupt_during_release_is_written_to_the_journal(tmp_path):
    """The latch stops allocation, so the run needs a record of why it stopped."""
    rec = _opened_recorder(tmp_path, scoped=True)
    with pytest.MonkeyPatch.context() as patch:
        _fail_the_next_close(patch, error=KeyboardInterrupt("signal during release"))
        with pytest.raises(KeyboardInterrupt):
            _publish(rec)
    faults = [event for event in _journal_events(tmp_path) if event["event"] == "audit_fault"]
    assert [event["code"] for event in faults] == ["cleanup_unresolved"]
    assert rec._complete is False


def test_the_unresolved_release_belongs_to_the_root_not_the_recorder(tmp_path):
    """A descriptor leaked by one recorder is still open for the next one."""
    from code_forge.invocation_audit import AttemptRecorder

    rec = _opened_recorder(tmp_path, scoped=True)
    with pytest.MonkeyPatch.context() as patch:
        _fail_the_next_close(patch)
        assert _publish(rec).fault.code == "cleanup_unresolved"
    second = AttemptRecorder(
        tmp_path,
        context=rec._context,
        monotonic=lambda: 1.0,
        utc_now=lambda: "2026-11-12T00:00:00Z",
        audit_schema_version=2,
        scoped_fault_domain=True,
    )
    ack = second.open()
    assert not ack.admitted
    assert ack.fault.code == "cleanup_unresolved"


def test_a_leak_on_one_root_does_not_stop_another(tmp_path):
    """The latch is per root: an unrelated audit root keeps working."""
    leaked = _opened_recorder(tmp_path / "leaked", scoped=True)
    with pytest.MonkeyPatch.context() as patch:
        _fail_the_next_close(patch)
        assert _publish(leaked).fault.code == "cleanup_unresolved"
    healthy = _opened_recorder(tmp_path / "healthy", scoped=True)
    assert _publish(healthy).admitted


def test_the_refinement_vocabulary_is_opted_into_from_the_environment(tmp_path):
    """The switch ships off; an operator setting it is the only way to reach it."""
    from code_forge.invocation_audit import InvocationContext

    context = InvocationContext(str(uuid.uuid4()), None, None, None, None, None, None, "review", None)

    def switch(value):
        environ = {"XDG_STATE_HOME": str(tmp_path)}
        if value is not None:
            environ["FORGE_SCOPED_FAULT_DOMAIN"] = value
        host = HostAuditSession.from_user_state(context=context, manifest=None, environ=environ)
        return host._scoped_fault_domain

    assert switch(None) is False
    assert switch("0") is False
    assert switch("perhaps") is False
    assert switch("1") is True
    assert switch("ON") is True


def test_a_host_opted_in_from_the_environment_delivers_a_refinement_code(tmp_path, monkeypatch):
    """Reachability: the switch, the notices and the captures all follow one env var."""
    from code_forge import invocation_io
    from code_forge.invocation_audit import InvocationContext

    monkeypatch.setattr(invocation_io, "_PROCESS", None)
    delivered = []
    context = InvocationContext(str(uuid.uuid4()), None, None, None, None, None, None, "review", None)
    host = HostAuditSession.from_user_state(
        context=context,
        manifest=None,
        environ={"XDG_STATE_HOME": str(tmp_path), "FORGE_SCOPED_FAULT_DOMAIN": "1"},
        warn=delivered.append,
    )
    try:
        host.warn("capture_write_uncertain")
        assert delivered == ["capture_write_uncertain"]
        host._ensure_io()
        assert host._captures._scoped_fault_domain is True
    finally:
        if host._io is not None:
            host._io.close()


def test_a_close_failure_during_a_normal_publish_is_written_to_the_journal(tmp_path):
    """The common path, not only the interrupt: an OSError release leaves the code."""
    rec = _opened_recorder(tmp_path, scoped=True)
    with pytest.MonkeyPatch.context() as patch:
        _fail_the_next_close(patch)
        assert _publish(rec).fault.code == "cleanup_unresolved"
    faults = [event for event in _journal_events(tmp_path) if event["event"] == "audit_fault"]
    assert [event["code"] for event in faults] == ["cleanup_unresolved"]


def test_the_latch_record_does_not_add_a_second_fault_marker(tmp_path):
    """A run carries one fault marker; a second write would leave torn state.

    Pins the in-memory guard. The on-disk check beside it is defense in depth
    with no reachable state of its own, so nothing here turns red without it.
    """
    rec = _opened_recorder(tmp_path, scoped=True)
    assert _publish(rec).admitted
    other = dict(
        schema_version=1,
        run_id=rec._context["run_id"],
        snapshot_id=rec._context["snapshot_id"],
        mode="diff",
        legacy_source_hash=rec._context["source_hash"],
        exact_diff_sha256="e" * 64,
        diff_bytes=2,
        files=[],
        repositories=[],
    )
    assert rec.publish_input_manifest(other).fault.code == "identity_conflict"
    run_dir = next(tmp_path.glob("*/"))
    before = sorted(p.name for p in run_dir.iterdir())
    assert "fault" in before
    rec._journal_latch("finish", None)
    after = sorted(p.name for p in run_dir.iterdir())
    assert after == before, f"the latch left state behind: {set(after) - set(before)}"
    codes = [e["code"] for e in _journal_events(tmp_path) if e["event"] == "audit_fault"]
    assert codes == ["identity_conflict"]


def test_nothing_is_appended_after_the_terminal_event(tmp_path):
    """A post-terminal fault would make the run unreadable, so it is not written.

    The window is real: the terminal event is appended and verified before the
    recorder marks itself closed, so a release that fails in between would append
    past it. A rejected fsync closes that window too; the guard itself is pinned
    by the marker-publish variant, which this patch does not mask.
    """
    from code_forge import invocation_audit as audit

    rec = _opened_recorder(tmp_path, scoped=True)
    manifest = dict(
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
    assert rec.publish_input_manifest(manifest).admitted
    real_fsync = audit.os.fsync

    def after_the_terminal_write(fd):
        # Fail the verification that follows the terminal append, which is the one
        # place the recorder is still open with the terminal event already on disk.
        if any(event["event"] == "run_final" for event in rec._events):
            raise audit._Rejected("integrity_error")
        return real_fsync(fd)

    class _Signal(BaseException):
        """Stands in for a signal: the branch only asks whether it is an OSError."""

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(audit.os, "fsync", after_the_terminal_write)
        _fail_the_next_close(patch, error=_Signal("signal after the terminal write"))
        with pytest.raises(_Signal):
            rec.finalize("completed")
    events = _journal_events(tmp_path)
    assert any(e["event"] == "run_final" for e in events)
    assert not [e for e in events if e["event"] == "audit_fault"]
    from code_forge.invocation_audit import AttemptRecorder

    reopened = AttemptRecorder.reopen_scoped(tmp_path, rec._context["run_id"])
    assert reopened is not None


def test_a_rebuilt_root_is_not_the_root_that_leaked(tmp_path):
    """The latch follows the directory, not the name it had."""
    import shutil

    from code_forge import invocation_audit as audit

    leaked = tmp_path / "root"
    rec = _opened_recorder(leaked, scoped=True)
    with pytest.MonkeyPatch.context() as patch:
        _fail_the_next_close(patch)
        assert _publish(rec).fault.code == "cleanup_unresolved"
    before = leaked.stat()
    before_latched = audit._root_unresolved(leaked)
    shutil.rmtree(leaked)
    fresh = _opened_recorder(leaked, scoped=True)
    after = leaked.stat()
    after_latched = audit._root_unresolved(leaked)
    ack = _publish(fresh)
    # Failure-only identity evidence: no paths, run identifiers, journal contents,
    # environment values or unrelated process latch entries are disclosed.
    assert ack.admitted, {
        "before": (before.st_dev, before.st_ino),
        "after": (after.st_dev, after.st_ino),
        "same_identity": (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino),
        "before_latched": before_latched,
        "after_latched": after_latched,
        "before_filesystem": rec._capability.filesystem_type,
        "after_filesystem": fresh._capability.filesystem_type,
    }


def test_another_spelling_of_the_same_directory_is_the_same_root(tmp_path):
    """Two paths to one inode must not open a second clean slate."""
    from code_forge.invocation_audit import AttemptRecorder

    rec = _opened_recorder(tmp_path, scoped=True)
    with pytest.MonkeyPatch.context() as patch:
        _fail_the_next_close(patch)
        assert _publish(rec).fault.code == "cleanup_unresolved"
    alias = type(tmp_path)(str(tmp_path) + "/.")
    second = AttemptRecorder(
        alias,
        context=rec._context,
        monotonic=lambda: 1.0,
        utc_now=lambda: "2026-11-12T00:00:00Z",
        audit_schema_version=2,
        scoped_fault_domain=True,
    )
    ack = second.open()
    assert not ack.admitted and ack.fault.code == "cleanup_unresolved"


def test_an_unscoped_recorder_is_refused_on_a_latched_root(tmp_path):
    """The descriptor belongs to the process, so the promise is not per switch."""
    from code_forge.invocation_audit import AttemptRecorder

    rec = _opened_recorder(tmp_path, scoped=True)
    with pytest.MonkeyPatch.context() as patch:
        _fail_the_next_close(patch)
        assert _publish(rec).fault.code == "cleanup_unresolved"
    plain = AttemptRecorder(
        tmp_path,
        context=rec._context,
        monotonic=lambda: 1.0,
        utc_now=lambda: "2026-11-12T00:00:00Z",
        audit_schema_version=2,
        scoped_fault_domain=False,
    )
    ack = plain.open()
    assert not ack.admitted and ack.fault.code == "cleanup_unresolved"


def test_an_explicit_switch_beats_the_environment(tmp_path):
    """A caller that passes the switch decides, whatever the environment says."""
    from code_forge.invocation_audit import InvocationContext

    context = InvocationContext(str(uuid.uuid4()), None, None, None, None, None, None, "review", None)
    environ = {"XDG_STATE_HOME": str(tmp_path), "FORGE_SCOPED_FAULT_DOMAIN": "1"}
    off = HostAuditSession.from_user_state(
        context=context, manifest=None, environ=environ, scoped_fault_domain=False
    )
    assert off._scoped_fault_domain is False
    environ["FORGE_SCOPED_FAULT_DOMAIN"] = "0"
    on = HostAuditSession.from_user_state(
        context=context, manifest=None, environ=environ, scoped_fault_domain=True
    )
    assert on._scoped_fault_domain is True


def test_a_forked_child_does_not_inherit_a_lock_it_cannot_release():
    """The map's lock is replaced in the child, so a held lock cannot survive the fork."""
    import os

    from code_forge import invocation_audit as audit

    parent_lock = audit._unresolved_lock()
    reader, writer = os.pipe()
    pid = os.fork()
    if pid == 0:  # pragma: no cover - child
        try:
            os.close(reader)
            child_lock = audit._unresolved_lock()
            os.write(writer, b"same" if child_lock is parent_lock else b"fresh")
        finally:
            os._exit(0)
    os.close(writer)
    try:
        answer = os.read(reader, 8)
    finally:
        os.close(reader)
        os.waitpid(pid, 0)
    assert answer == b"fresh"


def test_a_signal_while_arming_still_leaves_the_run_incomplete(tmp_path, monkeypatch):
    """The incomplete flag is stored before any syscall, so arming cannot skip it."""
    from code_forge import invocation_audit as audit

    rec = _opened_recorder(tmp_path, scoped=True)

    class _Signal(BaseException):
        """A signal stand-in: the branch only asks whether it is an OSError."""

    def raiser(root):
        raise _Signal("signal while arming")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(audit, "_arm_unresolved", raiser)
        _fail_the_next_close(patch, error=RuntimeError("release refused"))
        # The caller's error is what reaches it; the signal during arming is what
        # must not swallow the incomplete flag.
        with pytest.raises(RuntimeError):
            _publish(rec)
    assert rec._complete is False


def test_a_root_whose_identity_cannot_be_read_is_not_an_error(tmp_path):
    """The key builder answers with a placeholder instead of raising."""
    from code_forge import invocation_audit as audit

    class Hostile:
        def __fspath__(self):
            raise RuntimeError("no path for you")

        def __str__(self):
            raise RuntimeError("no name for you")

    assert audit._unresolved_key(Hostile()) == ("path", "")
    assert audit._root_unresolved(Hostile()) is False


def test_a_switch_value_that_cannot_be_read_stays_off():
    """A value whose text cannot be produced does not turn a switch on."""
    from code_forge import _env_flags

    class Hostile:
        def __str__(self):
            raise RuntimeError("no text")

    assert _env_flags.truthy(Hostile()) is False
    assert _env_flags.requested({"X": Hostile()}, "X") is False


def test_switching_off_keeps_the_old_code_and_writes_no_fault(tmp_path):
    """Off is the shipped default: the release failure stays what it always was."""
    rec = _opened_recorder(tmp_path, scoped=False)
    with pytest.MonkeyPatch.context() as patch:
        _fail_the_next_close(patch)
        assert _publish(rec).fault.code == "persistence_error"
    assert not [e for e in _journal_events(tmp_path) if e["event"] == "audit_fault"]


def test_the_latch_record_waits_for_the_root_lock(tmp_path):
    """A write that cannot take the lock is skipped, not forced past it."""
    import fcntl
    import os

    rec = _opened_recorder(tmp_path, scoped=True)
    ran = [event for event in _journal_events(tmp_path) if event["event"] == "audit_fault"]
    assert ran == []
    holder = os.open(tmp_path / "root.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(holder, fcntl.LOCK_EX)
        rec._journal_latch("finish", None)
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        os.close(holder)
    assert not [e for e in _journal_events(tmp_path) if e["event"] == "audit_fault"]


def test_a_failed_publish_does_not_leave_a_fault_after_the_terminal_event(tmp_path):
    """Nothing lands past the terminal event when the terminal write itself fails.

    The failing publish is the terminal one, which leaves `run_final` in the journal
    while the recorder is still open: that is the window the guard covers, and
    deleting the guard turns this test red.
    """
    from code_forge import invocation_audit as audit

    rec = _opened_recorder(tmp_path, scoped=True)
    manifest = dict(
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
    assert rec.publish_input_manifest(manifest).admitted
    real_publish = audit._publish
    state = {"failed": False}

    def once(fd, name, data, **kwargs):
        # The write that must fail is the terminal one: it leaves run_final in the
        # journal while the recorder is still open, which is the window the guard
        # exists for. Failing the marker write instead never gets that far.
        if name == "final" and not state["failed"]:
            state["failed"] = True
            raise OSError("the terminal write fails once")
        return real_publish(fd, name, data, **kwargs)

    class _Signal(BaseException):
        """A signal stand-in."""

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(audit, "_publish", once)
        _fail_the_next_close(patch, error=_Signal("signal"))
        with pytest.raises(_Signal):
            rec.finalize("completed")
    events = _journal_events(tmp_path)
    final_at = next(i for i, e in enumerate(events) if e["event"] == "run_final")
    assert not [e for e in events[final_at + 1 :] if e["event"] == "audit_fault"]


def test_the_latch_record_is_visible_in_the_snapshot(tmp_path):
    """The run's own summary has to agree with the journal it wrote."""
    rec = _opened_recorder(tmp_path, scoped=True)
    with pytest.MonkeyPatch.context() as patch:
        _fail_the_next_close(patch, error=KeyboardInterrupt("signal"))
        with pytest.raises(KeyboardInterrupt):
            _publish(rec)
    snapshot = rec.snapshot()
    assert [fault.code for fault in snapshot.faults] == ["cleanup_unresolved"]
    assert snapshot.state == "incomplete"
    assert snapshot.audit_complete is False


def test_the_latch_record_is_not_written_to_a_replaced_root(tmp_path):
    """A swapped root is an identity refusal, not a place to leave a record."""
    rec = _opened_recorder(tmp_path, scoped=True)
    capability = rec._capability
    rec._capability = replace(capability, root_inode=capability.root_inode + 1)
    rec._journal_latch("finish", None)
    assert not [e for e in _journal_events(tmp_path) if e["event"] == "audit_fault"]


def test_a_refused_recorder_cannot_finalize_as_complete(tmp_path):
    """A refusal is the recorder's own verdict, not only the caller's."""
    from code_forge.invocation_audit import AttemptRecorder

    first = _opened_recorder(tmp_path, scoped=True)
    from code_forge.invocation_audit import InvocationContext

    context = InvocationContext(
        str(uuid.uuid4()), "a" * 64, str(uuid.uuid4()), None, None, None, None, "review", None
    )
    second = AttemptRecorder(
        tmp_path,
        context=context,
        monotonic=lambda: 1.0,
        utc_now=lambda: "2026-11-12T00:00:00Z",
        audit_schema_version=2,
        scoped_fault_domain=True,
    )
    assert second.open().admitted
    with pytest.MonkeyPatch.context() as patch:
        _fail_the_next_close(patch)
        assert _publish(first).fault.code == "cleanup_unresolved"
    # The second recorder is mid-flight when the root latches: its next call is
    # refused, and that refusal has to leave it unable to claim completeness.
    refused = _publish(second)
    assert refused.fault.code == "cleanup_unresolved"
    assert second._complete is False
    assert [fault.code for fault in second.snapshot().faults] == ["cleanup_unresolved"]
    assert second.snapshot().audit_complete is False


def test_the_refusal_reports_the_record_it_wrote(tmp_path):
    """The latch writes on the failed release's behalf, so the call says so."""
    recorder = _opened_recorder(tmp_path, scoped=True)
    assert _publish(recorder).admitted
    # A second, identical manifest changes nothing in the journal, so the action's
    # own write is not what makes this call uncertain: the latch record is.
    with pytest.MonkeyPatch.context() as patch:
        _fail_the_next_close(patch)
        ack = _publish(recorder)
    assert ack.fault.code == "cleanup_unresolved"
    assert ack.publication_uncertain is True
    assert [event["event"] for event in _journal_events(tmp_path)].count("audit_fault") == 1


def test_the_latch_record_never_creates_the_root_it_reports_on(tmp_path):
    """Reporting on a root must not resurrect it."""
    recorder = _opened_recorder(tmp_path / "audit", scoped=True)
    root = tmp_path / "audit"
    for path in sorted(root.rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    root.rmdir()
    assert recorder._journal_latch("finalize", None) is False
    assert not root.exists()


def test_the_latch_record_publishes_the_fault_marker(tmp_path):
    """A reader learns this run faulted from the marker, not only the journal."""
    recorder = _opened_recorder(tmp_path, scoped=True)
    with pytest.MonkeyPatch.context() as patch:
        _fail_the_next_close(patch)
        assert _publish(recorder).fault.code == "cleanup_unresolved"
    run_dir = next(tmp_path.glob("*/"))
    assert (run_dir / "fault").exists()


def test_the_latch_belongs_to_the_directory_it_armed_not_the_path(tmp_path):
    """The capability branch keys the inode that failed, not whatever sits there now."""
    from code_forge.invocation_audit import _arm_unresolved, _root_unresolved

    original = tmp_path / "audit"
    recorder = _opened_recorder(original, scoped=True)
    capability = recorder._capability
    assert capability is not None
    moved = tmp_path / "audit-moved"
    original.rename(moved)
    original.mkdir()
    _arm_unresolved(original, capability=capability)
    # The replacement directory was never touched by this process: it is not latched.
    assert _root_unresolved(original) is False
    # The directory whose release failed is, and it is recognised by identity.
    assert _root_unresolved(moved) is True
