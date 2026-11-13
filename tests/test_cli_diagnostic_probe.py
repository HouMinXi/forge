"""Passive identity diagnostics, not installed/native durability qualification."""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import errno
import json
import os
import stat
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import uuid

import pytest

from code_forge import invocation_audit as audit
from code_forge import invocation_host as host_module
from code_forge import invocation_io as io_module
from code_forge.invocation_buffered_capture import _SharedWireCapture
from code_forge.invocation_owner import InvocationOwner, _Epoch
from code_forge.invocation_owner_stage import OwnerCaptureStage, _Publication


_PROBE_PATH = Path(__file__).parents[1] / "qualification/installed_cli/diagnostic_probe.py"
_SPEC = importlib.util.spec_from_file_location("cli_diagnostic_probe_test", _PROBE_PATH)
probe_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(probe_module)


@pytest.fixture
def probe(tmp_path, monkeypatch):
    # Unit source origin only. Production install accepts only sysconfig's
    # installed package roots and never imports or overrides product code.
    root = str(Path(io_module.__file__).resolve().parent)
    monkeypatch.setattr(probe_module, "_package_roots", lambda: frozenset({root}))
    path = tmp_path / "events.jsonl"
    observer = probe_module.install(path)
    yield observer, path
    observer.uninstall()


@pytest.fixture
def native(tmp_path):
    supervisors = []

    def make():
        process = io_module.ProcessAuditIo(
            limits=io_module.IoLimits(32, 8192, 512, 8), warn=lambda _: None, _key=io_module._KEY
        )
        supervisors.append(process)
        context = audit.InvocationContext(
            str(uuid.uuid4()), None, None, None, None, None, None, "review", None
        )
        host = host_module.HostAuditSession(
            artifact_root=tmp_path,
            context=context,
            supervisor=process,
            limits=host_module.HostAuditLimits(capture_buffer_bytes=4096, batch_bytes=512),
        )
        return host, process

    yield make
    for process in supervisors:
        process.close()
        if process._worker is not None:
            process._worker.join(2)
            assert not process._worker.is_alive()


def records(observer, path):
    observer.finish()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["seq"] for row in rows] == list(range(1, len(rows) + 1))
    assert all(row["event"] in probe_module.EVENT_NAMES for row in rows)
    assert all(type(value) in (int, bool, str, type(None)) for row in rows for value in row.values())
    assert all(row["pid"] == os.getpid() and row["tid"] > 0 for row in rows)
    assert all(row["time_ns"] > 0 and row["monotonic_ns"] > 0 for row in rows)
    return rows


def entered(host, action=None, timeout=2):
    argument = SimpleNamespace(entered=action or (lambda _: None))
    return host._request(io_module.AuditPhase.ENTERED, "entered", argument, time.monotonic() + timeout)


def assert_observed_ticket_fields(rows):
    """Check emitted scalar identities only; no syscall or outcome inference."""
    assert rows[-1]["event"] == "diagnostic_footer" and rows[-1]["complete"] is True
    assert not any(row["event"] == "diagnostic_error" for row in rows)
    creates = [row for row in rows if row["event"] == "ticket_create"]
    assert len({row["ticket_id"] for row in creates}) == len(creates)
    for create in creates:
        assert type(create["ticket_id"]) is int and create["ticket_id"] > 0
        for row in rows:
            if row.get("ticket_id") == create["ticket_id"]:
                for field in (
                    "host_id",
                    "operation_id",
                    "attempt",
                    "phase",
                    "target_kind",
                    "deadline_ns",
                ):
                    assert row[field] == create[field]


def mapped_capture(host):
    """Native capture target identity with a synthetic, memory-only sink."""
    owner = InvocationOwner.__new__(InvocationOwner)
    recorder = audit.AttemptRecorder.__new__(audit.AttemptRecorder)
    owner._current = _Epoch(recorder, host._context)
    host._owner = owner
    assert entered(host) is True
    stage = OwnerCaptureStage.__new__(OwnerCaptureStage)
    stage._owner, stage._lock, stage._cancelled = owner, threading.Lock(), False
    stage._sink = SimpleNamespace(write=len)
    target = _SharedWireCapture.__new__(_SharedWireCapture)
    target._write = stage._write
    return target


def test_passive_probe_preserves_product_functions_and_objects(probe, native):
    observer, path = probe
    host, _ = native()
    original = host_module.HostAuditSession._request
    assert original.__module__ == "code_forge.invocation_host"
    assert original.__code__.co_filename == host_module.__file__
    assert entered(host) is True
    assert not any("diagnostic" in key for key in vars(host))
    rows = records(observer, path)
    assert rows[-1]["complete"] is True
    assert [row["event"] for row in rows if row["event"].startswith("host_request")] == [
        "host_request_begin",
        "host_request_end",
    ]
    assert host_module.HostAuditSession._request is original
    begin = next(row for row in rows if row["event"] == "worker_begin")
    end = next(row for row in rows if row["event"] == "worker_end")
    create = next(row for row in rows if row["event"] == "ticket_create")
    submit = next(row for row in rows if row["event"] == "ticket_submit")
    assert create["seq"] < submit["seq"] < begin["seq"] < end["seq"]
    assert begin["tid"] == end["tid"] != threading.get_native_id()
    assert create["deadline_ns"] == begin["deadline_ns"]
    assert begin["host_id"] == end["host_id"] == 1
    assert begin["operation_id"] == end["operation_id"] == 1
    assert begin["ticket_id"] == end["ticket_id"] == 1
    assert_observed_ticket_fields(rows)


def test_unselected_calls_do_not_make_pid_syscalls(probe, native, monkeypatch):
    observer, path = probe
    host, _ = native()
    original_getpid = os.getpid
    calls = []

    def counted_getpid():
        calls.append(True)
        return original_getpid()

    def unrelated(value):
        return value + 1

    with monkeypatch.context() as patch:
        patch.setattr(probe_module.os, "getpid", counted_getpid)
        for value in range(2000):
            assert unrelated(value) == value + 1
        assert calls == []
        assert entered(host) is True
        assert calls  # Selected callbacks still perform the fork-safety check.
    assert records(observer, path)[-1]["complete"] is True


def test_selected_fork_callbacks_are_ignored_before_locking(probe, native, monkeypatch):
    observer, path = probe
    host, _ = native()
    frame = SimpleNamespace(
        f_code=host_module.HostAuditSession._request.__code__,
        f_globals=vars(host_module),
        f_locals={"self": host, "phase": io_module.AuditPhase.ENTERED, "deadline": 1.0},
        f_back=None,
        f_trace_lines=True,
    )
    calls = []

    def fork_pid():
        calls.append(True)
        return observer._pid + 1

    class InheritedLock:
        def __enter__(self):
            pytest.fail("fork callback acquired an inherited lock")

        def __exit__(self, *args):
            return None

    with monkeypatch.context() as patch:
        patch.setattr(probe_module.os, "getpid", fork_pid)
        patch.setattr(observer, "_lock", InheritedLock())
        assert observer._trace(frame, "call", None) is None
        assert observer._trace(frame, "return", None) is None
    assert len(calls) == 2
    assert observer._seq == 1 and not observer._frames and not observer._requests
    assert records(observer, path)[-1]["complete"] is False


def test_repeated_phase_concurrent_hosts_and_requests_never_fifo_match(probe, native):
    observer, path = probe
    first, _ = native()
    second, _ = native()
    barrier = threading.Barrier(2)

    def run(host):
        def action(_):
            barrier.wait(2)

        return [entered(host, action), entered(host, action)]

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run, host) for host in (first, second)]
        assert [future.result(3) for future in futures] == [[True, True], [True, True]]
    rows = records(observer, path)
    assert rows[-1]["complete"] is True
    tickets = [row for row in rows if row["event"] == "ticket_create"]
    assert len({row["ticket_id"] for row in tickets}) == 4
    assert len({row["operation_id"] for row in tickets}) == 4
    assert len({row["host_id"] for row in tickets}) == 2
    for ticket in tickets:
        events = [row for row in rows if row.get("ticket_id") == ticket["ticket_id"]]
        assert all(
            row["operation_id"] == ticket["operation_id"] and row["host_id"] == ticket["host_id"]
            for row in events
        )
        assert {row["phase"] for row in events} == {"entered"}
    assert_observed_ticket_fields(rows)


def test_retry_has_one_operation_two_tickets_and_exact_attempts(probe, native, monkeypatch):
    observer, path = probe
    host, process = native()
    submit = process.submit
    calls = []

    def reject_first(target, phase, **kwargs):
        calls.append(phase)
        if len(calls) == 1:
            kwargs["deadline"] = 0.0
        return submit(target, phase, **kwargs)

    monkeypatch.setattr(process, "submit", reject_first)
    assert entered(host) is True
    rows = records(observer, path)
    assert rows[-1]["complete"] is True
    tickets = [row for row in rows if row["event"] == "ticket_create"]
    assert len(tickets) == 2
    assert len({row["operation_id"] for row in tickets}) == 1
    assert [row["attempt"] for row in tickets] == [1, 2]
    assert tickets[0]["ticket_id"] != tickets[1]["ticket_id"]
    starts = [row for row in rows if row["event"] == "worker_begin"]
    assert len(starts) == 1 and starts[0]["ticket_id"] == tickets[1]["ticket_id"]


def test_fatal_exception_identity_and_private_text_preserved(probe, native):
    observer, path = probe
    host, _ = native()
    primary = KeyboardInterrupt("PRIVATE_PRIMARY_MARKER")

    def fail(_):
        raise primary

    with pytest.raises(KeyboardInterrupt) as caught:
        entered(host, fail)
    assert caught.value is primary
    rows = records(observer, path)
    assert rows[-1]["complete"] is True
    assert "PRIVATE_PRIMARY_MARKER" not in path.read_text()
    assert any(row["event"] == "host_request_error" for row in rows)
    target = next(row for row in rows if row["event"] == "target_end")
    assert target["error_present"] is True
    assert any(row["event"] == "wait_end" and row["error_present"] is True for row in rows)
    assert_observed_ticket_fields(rows)


def test_transport_ack_is_not_native_admission(probe, native):
    observer, path = probe
    host, process = native()
    owner = InvocationOwner.__new__(InvocationOwner)
    recorder = audit.AttemptRecorder.__new__(audit.AttemptRecorder)
    owner._current = _Epoch(recorder, host._context)
    host._owner = owner
    assert entered(host) is True  # Native _execute return learns the owner/recorder identity.
    stage = OwnerCaptureStage.__new__(OwnerCaptureStage)
    stage._owner = owner
    stage._lease = SimpleNamespace(attempt_id="PRIVATE_ATTEMPT")
    stage._layer, stage._deadline = "wire", time.monotonic() + 2
    stage._recorder = SimpleNamespace(
        begin_stage=lambda *args, **kwargs: audit.StageAcknowledgement(
            False, None, None, 0, False, None, True
        )
    )
    target = _Publication(stage, io_module.AuditPhase.BEGIN)
    ticket = process.submit(target, io_module.AuditPhase.BEGIN, deadline=stage._deadline)
    result = process.wait(ticket)
    assert result.state == "ack" and result.value.admitted is False
    rows = records(observer, path)
    assert rows[-1]["complete"] is True
    complete = next(
        row for row in rows if row["event"] == "ticket_complete" and row["target_kind"] == "owner_stage"
    )
    assert complete["transport_state"] == "ack"
    assert complete["native_admitted"] is False
    assert complete["native_publication_uncertain"] is True
    assert complete["host_id"] == 1 and complete["operation_id"] != 1
    assert_observed_ticket_fields(rows)


def test_capture_stage_mapping_uses_exact_bound_owner_not_phase(probe, native):
    observer, path = probe
    host, process = native()
    owner = InvocationOwner.__new__(InvocationOwner)
    recorder = audit.AttemptRecorder.__new__(audit.AttemptRecorder)
    owner._current = _Epoch(recorder, host._context)
    host._owner = owner
    assert entered(host) is True
    stage = OwnerCaptureStage.__new__(OwnerCaptureStage)
    stage._owner, stage._lock, stage._cancelled = owner, threading.Lock(), False
    stage._sink = SimpleNamespace(write=len)
    target = _SharedWireCapture.__new__(_SharedWireCapture)
    target._write = stage._write
    for _ in range(2):
        ticket = process.submit(
            target,
            io_module.AuditPhase.APPEND,
            deadline=time.monotonic() + 2,
            payload=b"PRIVATE_PAYLOAD",
        )
        assert process.wait(ticket).accepted_bytes == len(b"PRIVATE_PAYLOAD")
    rows = records(observer, path)
    assert rows[-1]["complete"] is True
    creates = [
        row for row in rows if row["event"] == "ticket_create" and row["target_kind"] == "capture_append"
    ]
    assert len(creates) == 2
    assert len({row["operation_id"] for row in creates}) == 1
    assert {row["host_id"] for row in creates} == {1}
    assert "PRIVATE_PAYLOAD" not in path.read_text()
    assert_observed_ticket_fields(rows)


def test_publish_uses_worker_ticket_and_does_not_emit_name_or_payload(probe, native, tmp_path):
    observer, path = probe
    host, _ = native()
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:

        def publish(_):
            audit._publish(directory, "final", b"PRIVATE_PUBLICATION_DATA")

        assert entered(host, publish) is True
    finally:
        os.close(directory)
    rows = records(observer, path)
    assert rows[-1]["complete"] is True
    publications = [row for row in rows if row["event"].startswith("publish_")]
    assert [row["event"] for row in publications] == ["publish_begin", "publish_end"]
    assert all(
        row["publish_id"] == 1
        and row["publication_kind"] == "final"
        and row["directory_fd"] == directory
        for row in publications
    )
    assert all(row["host_id"] == 1 and row["ticket_id"] == 1 for row in publications)
    assert "PRIVATE_PUBLICATION_DATA" not in path.read_text()
    assert str(tmp_path) not in path.read_text()
    assert (tmp_path / "final").read_bytes() == b"PRIVATE_PUBLICATION_DATA"
    assert_observed_ticket_fields(rows)


def test_probe_records_native_rejected_retries_without_worker(probe, native):
    observer, path = probe
    host, process = native()
    assert entered(host) is True
    process.close()
    process._worker.join(2)
    assert not process._worker.is_alive()
    with pytest.raises(host_module._Unavailable):
        entered(host)
    rows = records(observer, path)
    rejected = [
        row
        for row in rows
        if row["event"] == "ticket_complete" and row["transport_state"] == "unavailable"
    ]
    assert len(rejected) == 2 and [row["attempt"] for row in rejected] == [1, 2]
    assert all(row["not_executed"] for row in rejected)
    assert len({row["operation_id"] for row in rejected}) == 1
    assert_observed_ticket_fields(rows)
    assert len([row for row in rows if row["event"] == "ticket_create"]) == 3
    starts = [row for row in rows if row["event"] == "worker_begin"]
    assert len(starts) == 1
    assert starts[0]["ticket_id"] not in {row["ticket_id"] for row in rejected}


def test_probe_records_native_queued_cancellation(probe, native):
    observer, path = probe
    host, process = native()
    target = mapped_capture(host)
    deadline = time.monotonic() + 2
    # Readiness at the existing ticket deadline leaves it queued until the
    # explicit native cancellation; this adds no product timeout or policy.
    with process._condition:
        ticket = process.submit(
            target, io_module.AuditPhase.APPEND, deadline=deadline, ready_at=deadline, payload=b"unit"
        )
        process.cancel(ticket)
    assert process.wait(ticket).state == "cancelled"
    rows = records(observer, path)
    cancelled = next(
        row
        for row in rows
        if row["event"] == "ticket_complete" and row["transport_state"] == "cancelled"
    )
    assert cancelled["not_executed"] is True
    assert not any(
        row["event"] == "worker_begin" and row["ticket_id"] == cancelled["ticket_id"] for row in rows
    )
    assert_observed_ticket_fields(rows)


def test_probe_records_native_coalesced_append_submissions(probe, native):
    observer, path = probe
    host, process = native()
    target = mapped_capture(host)
    deadline = time.monotonic() + 2
    with process._condition:
        first = process.submit(
            target, io_module.AuditPhase.APPEND, deadline=deadline, ready_at=deadline, payload=b"one"
        )
        second = process.submit(
            target, io_module.AuditPhase.APPEND, deadline=deadline, ready_at=deadline, payload=b"two"
        )
        assert first is second
        process._flush_locked(first, deadline)
    assert process.wait(first).accepted_bytes == 6
    rows = records(observer, path)
    submissions = [row for row in rows if row["event"] == "ticket_submit" and row["phase"] == "append"]
    assert len(submissions) == 2
    assert [row["created"] for row in submissions] == [True, False]
    assert len({row["ticket_id"] for row in submissions}) == 1
    assert_observed_ticket_fields(rows)


def test_probe_records_native_unknown_then_late_completion(probe, native):
    observer, path = probe
    host, process = native()
    release = threading.Event()
    try:
        with pytest.raises(host_module._Unavailable):
            entered(host, lambda _: release.wait(2), timeout=0.05)
    finally:
        release.set()
        process.close()
        process._worker.join(2)
        assert not process._worker.is_alive()
    rows = records(observer, path)
    unknown = next(row for row in rows if row["event"] == "ticket_unknown")
    wait = next(row for row in rows if row["event"] == "wait_end")
    complete = next(row for row in rows if row["event"] == "ticket_complete")
    assert unknown["seq"] < wait["seq"] < complete["seq"]
    assert complete["transport_state"] == "unknown" and complete["io_publication_uncertain"] is True
    assert_observed_ticket_fields(rows)


@pytest.mark.parametrize("error_type", [ValueError, KeyboardInterrupt])
def test_probe_records_native_target_failure_and_fatal_error(probe, native, error_type):
    observer, path = probe
    host, _ = native()
    primary = error_type("PRIVATE_TARGET_FAILURE")

    def fail(_):
        raise primary

    fatal = error_type is KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt if fatal else host_module._Unavailable) as caught:
        entered(host, fail)
    if fatal:
        assert caught.value is primary
    rows = records(observer, path)
    target = next(row for row in rows if row["event"] == "target_end")
    complete = next(row for row in rows if row["event"] == "ticket_complete")
    assert target["error_present"] is True
    assert complete["transport_state"] == "failed" and complete["error_present"] is fatal
    assert "PRIVATE_TARGET_FAILURE" not in path.read_text()
    assert_observed_ticket_fields(rows)


def test_unknown_target_and_malicious_ack_cannot_be_false_green(probe, native):
    observer, path = probe
    _, process = native()

    class Secret:
        def __repr__(self):
            pytest.fail("diagnostic attempted repr")

        @property
        def admitted(self):
            pytest.fail("diagnostic attempted arbitrary property")

    target = SimpleNamespace(audit_io=lambda *_: Secret())
    ticket = process.submit(target, io_module.AuditPhase.START, deadline=time.monotonic() + 2)
    assert type(process.wait(ticket).value) is Secret
    rows = records(observer, path)
    assert rows[-1]["complete"] is False and rows[-1]["unknown_targets"] == 1
    create = next(row for row in rows if row["event"] == "ticket_create")
    assert create["host_id"] == create["operation_id"] == 0
    assert create["target_kind"] == "unknown"


def test_unknown_deadline_keeps_unfinished_worker_in_footer(probe, native):
    observer, path = probe
    host, process = native()
    release = threading.Event()
    try:
        with pytest.raises(host_module._Unavailable):
            entered(host, lambda _: release.wait(2), timeout=0.05)
        rows = records(observer, path)
        assert rows[-1]["complete"] is False
        assert rows[-1]["open_workers"] == rows[-1]["open_tickets"] == 1
        assert any(row["event"] == "ticket_unknown" for row in rows)
        assert not any(row["event"] == "worker_end" for row in rows)
    finally:
        release.set()
        process.close()
        process._worker.join(2)


def test_diagnostic_emission_failure_does_not_change_native_result(probe, native, monkeypatch):
    observer, path = probe
    host, _ = native()
    write = observer._write

    def fail(event, **fields):
        if event == "worker_begin":
            raise KeyboardInterrupt("PRIVATE_DIAGNOSTIC_FAILURE")
        return write(event, **fields)

    monkeypatch.setattr(observer, "_write", fail)
    assert entered(host) is True
    rows = records(observer, path)
    assert rows[-1]["complete"] is False
    assert any(row["event"] == "diagnostic_error" for row in rows)
    assert "PRIVATE_DIAGNOSTIC_FAILURE" not in path.read_text()


def test_bounded_output_has_drop_marker_and_incomplete_footer(tmp_path, monkeypatch, native):
    monkeypatch.setattr(
        probe_module, "_package_roots", lambda: frozenset({str(Path(io_module.__file__).parent)})
    )
    path = tmp_path / "bounded.jsonl"
    observer = probe_module.install(path, max_events=8)
    process = None
    try:
        host, process = native()
        assert entered(host) is True
        rows = records(observer, path)
        assert len(rows) <= 8
        assert rows[-1]["complete"] is False
        assert any(row["event"] == "diagnostic_dropped" for row in rows)
    finally:
        if process is not None:
            process.close()
            if process._worker is not None:
                process._worker.join(2)
                assert not process._worker.is_alive()
        observer.uninstall()


def test_install_does_not_preimport_forge_and_atexit_emits_footer(tmp_path):
    output = tmp_path / "subprocess.jsonl"
    code = (
        "import importlib.util, sys\n"
        f"s=importlib.util.spec_from_file_location('probe', {_PROBE_PATH.as_posix()!r})\n"
        "p=importlib.util.module_from_spec(s); s.loader.exec_module(p)\n"
        f"p.install({str(output)!r})\n"
        "assert not any(k == 'code_forge' or k.startswith('code_forge.') for k in sys.modules)\n"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert rows[0]["event"] == "diagnostic_start"
    assert rows[-1]["event"] == "diagnostic_footer" and rows[-1]["complete"] is False


def test_abrupt_exit_has_no_success_footer(tmp_path):
    output = tmp_path / "abrupt.jsonl"
    code = (
        "import importlib.util, os\n"
        f"s=importlib.util.spec_from_file_location('probe', {_PROBE_PATH.as_posix()!r})\n"
        "p=importlib.util.module_from_spec(s); s.loader.exec_module(p)\n"
        f"p.install({str(output)!r})\n"
        "os._exit(0)\n"
    )
    assert subprocess.run([sys.executable, "-I", "-c", code]).returncode == 0
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert rows and not any(row["event"] == "diagnostic_footer" for row in rows)


def _assert_install_rejected_cleanly(destination, error_type, match):
    observer = None
    try:
        with pytest.raises(error_type, match=match):
            observer = probe_module.install(destination)
    except BaseException as primary:
        if observer is not None:
            try:
                observer.uninstall()
            except BaseException as cleanup:  # noqa: BLE001 - cleanup must not replace the primary assertion
                primary.add_note("Unexpected observer cleanup also failed: " + type(cleanup).__name__)
        raise


@pytest.mark.parametrize("cleanup_error", [False, True], ids=["clean", "cleanup-error"])
def test_security_rejection_cleanup_preserves_primary_assertion(monkeypatch, cleanup_error):
    calls = []

    def uninstall():
        calls.append("uninstall")
        if cleanup_error:
            raise OSError("synthetic cleanup failure")

    observer = SimpleNamespace(uninstall=uninstall)
    monkeypatch.setattr(probe_module, "install", lambda destination: observer)
    with pytest.raises(pytest.fail.Exception, match="DID NOT RAISE") as caught:
        _assert_install_rejected_cleanly("synthetic-only", ValueError, "private")
    assert calls == ["uninstall"]
    assert getattr(caught.value, "__notes__", []) == (
        ["Unexpected observer cleanup also failed: OSError"] if cleanup_error else []
    )


def test_output_security_fd_ownership_and_existing_trace_rejection(tmp_path):
    path = tmp_path / "private.jsonl"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(fd, 0o600)
        assert stat.S_IMODE(os.fstat(fd).st_mode) == 0o600
        observer = probe_module.install(fd)
        observer.uninstall()
        assert stat.S_IMODE(os.fstat(fd).st_mode) == 0o600
        _assert_install_rejected_cleanly(path, FileExistsError, None)
    finally:
        os.close(fd)
    world = tmp_path / "world.jsonl"
    fd = os.open(world, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        # Creation mode is filtered by umask. Establish and inspect the exact
        # descriptor mode before exercising the production privacy rejection.
        os.fchmod(fd, 0o644)
        assert stat.S_IMODE(os.fstat(fd).st_mode) == 0o644
        _assert_install_rejected_cleanly(fd, ValueError, "private")
    finally:
        os.close(fd)

    def hook(*args):
        return None

    sys.settrace(hook)
    try:
        _assert_install_rejected_cleanly(tmp_path / "conflict.jsonl", RuntimeError, "existing trace")
        assert sys.gettrace() is hook
    finally:
        sys.settrace(None)


@pytest.mark.parametrize("mask", [0o022, 0o077], ids=["umask-022", "umask-077"])
def test_output_security_fixture_is_mask_independent_in_child(tmp_path, mask):
    before, after = tmp_path / "parent-before", tmp_path / "parent-after"
    before.touch(mode=0o666)
    parent_mode = stat.S_IMODE(before.stat().st_mode)
    child_dir = tmp_path / "child"
    child_dir.mkdir(mode=0o700)
    # Only the fresh child changes umask. Run the actual original test body,
    # not a parallel implementation of its security oracle.
    code = """
import importlib.util, json, os, pathlib, stat, sys, threading
os.umask(int(sys.argv[3]))
source = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(source.parents[1] / 'src'))
spec = importlib.util.spec_from_file_location('probe_umask_regression', source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
root = pathlib.Path(sys.argv[2])
hooks = sys.gettrace(), threading.gettrace()
threads = set(threading.enumerate())
try:
    module.test_output_security_fd_ownership_and_existing_trace_rejection(root)
finally:
    assert module.probe_module._INSTALLED is None
    assert (sys.gettrace(), threading.gettrace()) == hooks
    assert set(threading.enumerate()) == threads
    print(json.dumps({'umask': os.umask(int(sys.argv[3])),
                      'private_mode': stat.S_IMODE((root / 'private.jsonl').stat().st_mode),
                      'negative_mode': stat.S_IMODE((root / 'world.jsonl').stat().st_mode),
                      'cleanup': 'clean'}))
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            code,
            str(Path(__file__).resolve()),
            str(child_dir),
            str(mask),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env={"PATH": os.defpath, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=15,
    )
    after.touch(mode=0o666)
    assert stat.S_IMODE(after.stat().st_mode) == parent_mode
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "umask": mask,
        "private_mode": 0o600,
        "negative_mode": 0o644,
        "cleanup": "clean",
    }


# Explicit IDs are source-reviewed: each recognized native class must distinguish
# paired boolean metadata from malformed ACKs and genuine non-ACK return values.
_ACK_CLASSES = [
    pytest.param(audit.AuditAcknowledgement, id="audit"),
    pytest.param(audit.StartAcknowledgement, id="start"),
    pytest.param(audit.AcquiredAcknowledgement, id="acquired"),
    pytest.param(audit.StageAcknowledgement, id="stage"),
]
_MISSING_ACK_FIELD = object()
_VALID_ACK_FIELDS = [
    pytest.param(True, False, id="admitted-clear"),
    pytest.param(False, False, id="denied-clear"),
    pytest.param(True, True, id="admitted-uncertain"),
    pytest.param(False, True, id="denied-uncertain"),
]
_INVALID_ACK_FIELDS = [
    pytest.param(None, False, id="null-admission"),
    pytest.param(True, None, id="null-uncertainty"),
    pytest.param(None, None, id="both-null"),
    pytest.param(1, False, id="integer-admission"),
    pytest.param(True, 0, id="integer-uncertainty"),
    pytest.param("PRIVATE_ACK_FIELD", False, id="string-admission"),
    pytest.param(True, "PRIVATE_ACK_FIELD", id="string-uncertainty"),
    pytest.param([], False, id="list-admission"),
    pytest.param(True, {}, id="dict-uncertainty"),
    pytest.param(_MISSING_ACK_FIELD, False, id="missing-admission"),
    pytest.param(True, _MISSING_ACK_FIELD, id="missing-uncertainty"),
    pytest.param(_MISSING_ACK_FIELD, _MISSING_ACK_FIELD, id="both-missing"),
]


def native_ack_value(ack_class, admitted, uncertain):
    # Construct exact native identities without patching product classes or
    # methods. Missing instance fields intentionally do not use class defaults.
    value = ack_class.__new__(ack_class)
    for name, field in (("admitted", admitted), ("publication_uncertain", uncertain)):
        if field is not _MISSING_ACK_FIELD:
            object.__setattr__(value, name, field)
    return value


@pytest.mark.parametrize("ack_class", _ACK_CLASSES)
@pytest.mark.parametrize("admitted,uncertain", _VALID_ACK_FIELDS)
def test_native_ack_retains_all_boolean_pairs(ack_class, admitted, uncertain):
    value = native_ack_value(ack_class, admitted, uncertain)
    before = dict(vars(value))
    assert probe_module.Probe._native_ack(value) == {
        "native_admitted": admitted,
        "native_publication_uncertain": uncertain,
    }
    assert vars(value) == before


@pytest.mark.parametrize("ack_class", _ACK_CLASSES)
@pytest.mark.parametrize("admitted,uncertain", _INVALID_ACK_FIELDS)
def test_native_ack_rejects_malformed_recognized_fields(ack_class, admitted, uncertain):
    value = native_ack_value(ack_class, admitted, uncertain)
    before = dict(vars(value))
    with pytest.raises(ValueError, match="native ACK metadata"):
        probe_module.Probe._native_ack(value)
    assert vars(value) == before


@pytest.mark.parametrize("value", [True, None, 1, "ordinary"], ids=["bool", "none", "int", "str"])
def test_native_ack_non_ack_values_keep_null_pair(value):
    assert probe_module.Probe._native_ack(value) == {
        "native_admitted": None,
        "native_publication_uncertain": None,
    }


def test_native_ack_does_not_inspect_foreign_properties():
    class Foreign:
        @property
        def admitted(self):
            pytest.fail("foreign property inspected")

        @property
        def publication_uncertain(self):
            pytest.fail("foreign property inspected")

        def __repr__(self):
            pytest.fail("foreign value represented")

    assert probe_module.Probe._native_ack(Foreign()) == {
        "native_admitted": None,
        "native_publication_uncertain": None,
    }


def finish_ack_request(host, value):
    return host._request(
        io_module.AuditPhase.FINISH,
        "finish",
        (SimpleNamespace(finish_once=lambda **kwargs: value), "completed", None, 0.01),
        time.monotonic() + 2,
    )


def ack_request_outcome(native, value):
    host, process = native()
    try:
        try:
            result = finish_ack_request(host, value)
        except (host_module._Unavailable, TypeError) as error:
            # Native request exceptions must retain their native origin, type,
            # arguments and chaining. No diagnostic exception may replace them.
            trace = error.__traceback__
            while trace.tb_next is not None:
                trace = trace.tb_next
            assert trace.tb_frame.f_code is host_module.HostAuditSession._request.__code__
            assert error.__cause__ is None and error.__context__ is None
            return type(error), error.args
        assert result is value
        return "same-value", None
    finally:
        process.close()
        if process._worker is not None:
            process._worker.join(2)
            assert not process._worker.is_alive()


@pytest.mark.parametrize("ack_class", _ACK_CLASSES)
@pytest.mark.parametrize("admitted,uncertain", _VALID_ACK_FIELDS + _INVALID_ACK_FIELDS)
def test_native_ack_passive_containment_preserves_host_outcome(
    tmp_path, monkeypatch, native, ack_class, admitted, uncertain
):
    value = native_ack_value(ack_class, admitted, uncertain)
    original_functions = (
        host_module.HostAuditSession._request,
        host_module.HostAuditSession._execute,
        io_module.ProcessAuditIo._run,
        io_module._call_target,
    )
    baseline = ack_request_outcome(native, value)
    monkeypatch.setattr(
        probe_module, "_package_roots", lambda: frozenset({str(Path(io_module.__file__).parent)})
    )
    path = tmp_path / "ack-containment.jsonl"
    observer = probe_module.install(path)
    try:
        assert ack_request_outcome(native, value) == baseline
        rows = records(observer, path)
    finally:
        observer.uninstall()
    valid = type(admitted) is bool and type(uncertain) is bool
    assert rows[-1]["complete"] is valid
    errors = [row for row in rows if row["event"] == "diagnostic_error"]
    assert len(errors) == (0 if valid else 1)
    assert "PRIVATE_ACK_FIELD" not in path.read_text()
    if valid:
        target = next(row for row in rows if row["event"] == "target_end")
        assert target["native_admitted"] is admitted
        assert target["native_publication_uncertain"] is uncertain
        assert_observed_ticket_fields(rows)
    assert original_functions == (
        host_module.HostAuditSession._request,
        host_module.HostAuditSession._execute,
        io_module.ProcessAuditIo._run,
        io_module._call_target,
    )


@pytest.mark.parametrize("value", [True, None, 1, "ordinary"], ids=["bool", "none", "int", "str"])
def test_native_ack_passive_non_ack_keeps_complete_evidence(probe, native, value):
    observer, path = probe
    assert ack_request_outcome(native, value) == ("same-value", None)
    rows = records(observer, path)
    assert rows[-1]["complete"] is True
    target = next(row for row in rows if row["event"] == "target_end")
    assert target["native_admitted"] is target["native_publication_uncertain"] is None
    assert_observed_ticket_fields(rows)


@pytest.mark.parametrize("entry", ["install", "direct"])
@pytest.mark.parametrize("destination_kind", ["path", "caller_fd"])
@pytest.mark.parametrize("failure", ["none", "roots", "weakmap"])
def test_probe_constructs_state_before_acquiring_private_descriptor(
    tmp_path, monkeypatch, entry, destination_kind, failure
):
    path = tmp_path / "events.jsonl"
    original_open, original_dup, original_close = os.open, os.dup, os.close
    caller = (
        original_open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        if destination_kind == "caller_fd"
        else None
    )
    destination = path if caller is None else caller
    hooks = sys.gettrace(), threading.gettrace()
    allocated, closed = [], []
    primary = MemoryError("PRIVATE_CONSTRUCTOR_FAILURE")

    def opening(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        allocated.append(descriptor)
        return descriptor

    def duplicating(*args, **kwargs):
        descriptor = original_dup(*args, **kwargs)
        allocated.append(descriptor)
        return descriptor

    def closing(descriptor):
        if descriptor in allocated:
            closed.append(descriptor)
        return original_close(descriptor)

    def fail():
        raise primary

    monkeypatch.setattr(probe_module.os, "open", opening)
    monkeypatch.setattr(probe_module.os, "dup", duplicating)
    monkeypatch.setattr(probe_module.os, "close", closing)
    if failure == "roots":
        monkeypatch.setattr(probe_module, "_package_roots", fail)
    elif failure == "weakmap":
        monkeypatch.setattr(probe_module.weakref, "WeakKeyDictionary", fail)
    observer = None
    try:
        try:
            observer = (
                probe_module.install(destination)
                if entry == "install"
                else probe_module.Probe(destination, probe_module.MAX_EVENTS)
            )
        except MemoryError as error:
            assert failure != "none" and error is primary
        else:
            assert failure == "none"
            assert len(allocated) == 1
            if caller is not None:
                assert observer._fd != caller
            observer.uninstall()
            assert closed == allocated
            with pytest.raises(OSError) as caught:
                os.fstat(allocated[0])
            assert caught.value.errno == errno.EBADF
        if failure != "none":
            assert allocated == closed == []
            assert path.exists() is (caller is not None)
        assert probe_module._INSTALLED is None
        assert (sys.gettrace(), threading.gettrace()) == hooks
        if caller is not None:
            os.fstat(caller)
        if path.exists():
            assert path.stat().st_mode & 0o777 == 0o600
            assert "PRIVATE_CONSTRUCTOR_FAILURE" not in path.read_text()
    finally:
        if observer is not None:
            observer.uninstall()
        if caller is not None:
            original_close(caller)


@pytest.mark.parametrize("entry", ["install", "direct"])
@pytest.mark.parametrize("destination_kind", ["path", "caller_fd"])
@pytest.mark.parametrize("close_failure", [False, True], ids=["close_ok", "close_error"])
def test_probe_descriptor_validation_failure_preserves_primary_and_caller_fd(
    tmp_path, monkeypatch, entry, destination_kind, close_failure
):
    path = tmp_path / "events.jsonl"
    original_open, original_close, original_fstat = os.open, os.close, os.fstat
    caller = (
        original_open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        if destination_kind == "caller_fd"
        else None
    )
    destination = path if caller is None else caller
    hooks = sys.gettrace(), threading.gettrace()
    primary = MemoryError("PRIVATE_VALIDATION_FAILURE")
    state = {"owned": None, "close_calls": 0, "replacement": None}

    def fail_validation(descriptor):
        state["owned"] = descriptor
        assert descriptor != caller
        raise primary

    def close_then_reuse(descriptor):
        if descriptor != state["owned"]:
            return original_close(descriptor)
        state["close_calls"] += 1
        original_close(descriptor)
        replacement = original_open(os.devnull, os.O_RDONLY)
        state["replacement"] = replacement
        assert replacement == descriptor
        if close_failure:
            raise OSError(errno.EIO, "private close failure")

    monkeypatch.setattr(probe_module.os, "fstat", fail_validation)
    monkeypatch.setattr(probe_module.os, "close", close_then_reuse)
    try:
        with pytest.raises(MemoryError) as caught:
            if entry == "install":
                probe_module.install(destination)
            else:
                probe_module.Probe(destination, probe_module.MAX_EVENTS)
        assert caught.value is primary
        assert state["close_calls"] == 1
        assert state["replacement"] == state["owned"]
        original_fstat(state["replacement"])
        if caller is not None:
            original_fstat(caller)
        assert probe_module._INSTALLED is None
        assert (sys.gettrace(), threading.gettrace()) == hooks
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.read_bytes() == b""
    finally:
        # End the injected process-global faults before pytest captures output.
        monkeypatch.setattr(probe_module.os, "fstat", original_fstat)
        monkeypatch.setattr(probe_module.os, "close", original_close)
        if state["replacement"] is not None:
            original_close(state["replacement"])
        if caller is not None:
            original_close(caller)


@pytest.mark.parametrize("stage", ["before_hooks", "after_thread_hook", "after_both_hooks"])
@pytest.mark.parametrize("destination_kind", ["path", "caller_fd"])
@pytest.mark.parametrize("close_failure", [False, True], ids=["close_ok", "close_error"])
def test_probe_start_failure_restores_hooks_and_preserves_reused_descriptor(
    tmp_path, monkeypatch, stage, destination_kind, close_failure
):
    path = tmp_path / "events.jsonl"
    original_open, original_close = os.open, os.close
    original_descriptor, original_start = probe_module._private_descriptor, probe_module.Probe.start
    original_settrace = sys.settrace
    caller = (
        original_open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        if destination_kind == "caller_fd"
        else None
    )
    destination = path if caller is None else caller
    hooks = sys.gettrace(), threading.gettrace()
    primary = MemoryError("PRIVATE_START_FAILURE")
    state = {"owned": None, "close_calls": 0, "replacement": None}

    def allocate(target):
        descriptor = original_descriptor(target)
        state["owned"] = descriptor
        assert descriptor != caller
        return descriptor

    def fail_start(observer):
        if stage == "before_hooks":
            raise primary
        if stage == "after_thread_hook":

            def fail_sys_hook(hook):
                if hook is observer._hook:
                    raise primary
                return original_settrace(hook)

            monkeypatch.setattr(probe_module.sys, "settrace", fail_sys_hook)
        original_start(observer)
        assert stage == "after_both_hooks"
        assert sys.gettrace() is threading.gettrace() is observer._hook
        raise primary

    def close_then_reuse(descriptor):
        if descriptor != state["owned"]:
            return original_close(descriptor)
        state["close_calls"] += 1
        original_close(descriptor)
        replacement = original_open(os.devnull, os.O_RDONLY)
        state["replacement"] = replacement
        assert replacement == descriptor
        if close_failure:
            raise OSError(errno.EIO, "private close failure")

    monkeypatch.setattr(probe_module, "_private_descriptor", allocate)
    monkeypatch.setattr(probe_module.Probe, "start", fail_start)
    monkeypatch.setattr(probe_module.os, "close", close_then_reuse)
    try:
        with pytest.raises(MemoryError) as caught:
            probe_module.install(destination)
        assert caught.value is primary
        assert state["close_calls"] == 1
        assert state["replacement"] == state["owned"]
        os.fstat(state["replacement"])
        if caller is not None:
            os.fstat(caller)
        assert probe_module._INSTALLED is None
        assert (sys.gettrace(), threading.gettrace()) == hooks
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        assert rows[-1]["event"] == "diagnostic_footer" and rows[-1]["complete"] is False
        assert "PRIVATE_START_FAILURE" not in path.read_text()
    finally:
        monkeypatch.setattr(probe_module.os, "close", original_close)
        monkeypatch.setattr(probe_module.sys, "settrace", original_settrace)
        if state["replacement"] is not None:
            original_close(state["replacement"])
        if caller is not None:
            original_close(caller)


@pytest.mark.parametrize(
    "registration", ["success", "before_memory", "before_os", "after_memory", "after_os"]
)
@pytest.mark.parametrize("destination_kind", ["path", "caller_fd"])
@pytest.mark.parametrize(
    "cleanup", ["normal", "close_os", "close_interrupt", "unregister", "close_and_unregister"]
)
def test_probe_registration_is_atomic_and_cleanup_preserves_original_error(
    tmp_path, monkeypatch, registration, destination_kind, cleanup
):
    path = tmp_path / "events.jsonl"
    original_open, original_close = os.open, os.close
    original_descriptor = probe_module._private_descriptor
    original_register = probe_module.atexit.register
    original_unregister = probe_module.atexit.unregister
    caller = (
        original_open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        if destination_kind == "caller_fd"
        else None
    )
    hooks = sys.gettrace(), threading.gettrace()
    assert probe_module._INSTALLED is None
    primary = (
        MemoryError("PRIVATE_REGISTRATION_FAILURE")
        if registration.endswith("memory")
        else OSError("PRIVATE_REGISTRATION_FAILURE")
    )
    close_error = (
        KeyboardInterrupt("PRIVATE_CLOSE_FAILURE")
        if cleanup in {"close_interrupt", "close_and_unregister"}
        else OSError(errno.EIO, "PRIVATE_CLOSE_FAILURE")
    )
    unregister_error = OSError("PRIVATE_UNREGISTER_FAILURE")
    state = {
        "owned": None,
        "observer": None,
        "close_calls": 0,
        "unregister_calls": 0,
        "registered": False,
        "replacement": None,
    }

    def allocate(destination):
        descriptor = original_descriptor(destination)
        state["owned"] = descriptor
        assert descriptor != caller
        return descriptor

    def register(callback):
        observer = callback.__self__
        state["observer"] = observer
        assert sys.gettrace() is threading.gettrace() is observer._hook
        assert probe_module._INSTALLED is None
        if registration.startswith("before"):
            raise primary
        original_register(callback)
        state["registered"] = True
        if registration.startswith("after"):
            raise primary
        return callback

    def unregister(callback):
        state["unregister_calls"] += 1
        original_unregister(callback)
        state["registered"] = False
        if cleanup in {"unregister", "close_and_unregister"}:
            raise unregister_error

    def close_then_reuse(descriptor):
        if descriptor != state["owned"]:
            return original_close(descriptor)
        state["close_calls"] += 1
        original_close(descriptor)
        replacement = original_open(os.devnull, os.O_RDONLY)
        state["replacement"] = replacement
        assert replacement == descriptor
        if cleanup in {"close_os", "close_interrupt", "close_and_unregister"}:
            raise close_error

    monkeypatch.setattr(probe_module, "_private_descriptor", allocate)
    monkeypatch.setattr(probe_module.atexit, "register", register)
    monkeypatch.setattr(probe_module.atexit, "unregister", unregister)
    monkeypatch.setattr(probe_module.os, "close", close_then_reuse)
    expected = (
        primary
        if registration != "success"
        else close_error
        if cleanup in {"close_interrupt", "close_and_unregister"}
        else unregister_error
        if cleanup == "unregister"
        else None
    )
    try:
        try:
            observer = probe_module.install(path if caller is None else caller)
            assert registration == "success"
            assert probe_module._INSTALLED is observer
            assert state["registered"] is True
            assert sys.gettrace() is threading.gettrace() is observer._hook
            observer.uninstall()
        except (MemoryError, OSError, KeyboardInterrupt) as error:
            assert expected is not None and error is expected
        else:
            assert expected is None
        assert state["close_calls"] == state["unregister_calls"] == 1
        assert state["registered"] is False
        assert state["replacement"] == state["owned"]
        os.fstat(state["replacement"])
        if caller is not None:
            os.fstat(caller)
        assert (sys.gettrace(), threading.gettrace()) == hooks
        assert probe_module._INSTALLED is None
        assert "PRIVATE_" not in path.read_text()
    finally:
        monkeypatch.setattr(probe_module.os, "close", original_close)
        monkeypatch.setattr(probe_module.atexit, "register", original_register)
        monkeypatch.setattr(probe_module.atexit, "unregister", original_unregister)
        if state["observer"] is not None:
            state["observer"].uninstall()
        if state["replacement"] is not None:
            original_close(state["replacement"])
        if caller is not None:
            original_close(caller)


@pytest.mark.parametrize("entry", ["direct", "install"])
@pytest.mark.parametrize("destination_kind", ["path", "caller_fd"])
@pytest.mark.parametrize("rejections", [0, 1, 2])
def test_repeated_start_rejects_without_corrupting_restoration_state(
    tmp_path, entry, destination_kind, rejections
):
    path = tmp_path / "events.jsonl"
    caller = (
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        if destination_kind == "caller_fd"
        else None
    )
    destination = path if caller is None else caller
    original_hooks = sys.gettrace(), threading.gettrace()
    observer = None
    try:
        observer = (
            probe_module.install(destination)
            if entry == "install"
            else probe_module.Probe(destination, probe_module.MAX_EVENTS)
        )
        if entry == "direct":
            observer.start()
        saved_hooks = observer._previous_sys, observer._previous_threading
        owned = observer._fd
        for _ in range(rejections):
            with pytest.raises(RuntimeError) as caught:
                observer.start()
            assert caught.value.args == ("diagnostic probe cannot replace existing trace hooks",)
            assert (observer._previous_sys, observer._previous_threading) == saved_hooks
            assert sys.gettrace() is threading.gettrace() is observer._hook
            assert observer._fd == owned and observer._finished is False
            os.fstat(owned)
            assert probe_module._INSTALLED is (observer if entry == "install" else None)
        observer.uninstall()
        assert (sys.gettrace(), threading.gettrace()) == original_hooks
        assert probe_module._INSTALLED is None
        with pytest.raises(OSError) as caught:
            os.fstat(owned)
        assert caught.value.errno == errno.EBADF
        if caller is not None:
            os.fstat(caller)
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        assert [row["event"] for row in rows] == ["diagnostic_start", "diagnostic_footer"]
    finally:
        if observer is not None:
            observer.uninstall()
        # Also contain hooks if deliberately run as an old-source red control.
        sys.settrace(original_hooks[0])
        threading.settrace(original_hooks[1])
        if caller is not None:
            os.close(caller)


@pytest.mark.parametrize("conflict", ["sys_hook", "thread_hook", "both_hooks", "other_thread"])
def test_start_validates_preconditions_before_saving_hooks(tmp_path, monkeypatch, conflict):
    original_hooks = sys.gettrace(), threading.gettrace()
    observer = probe_module.Probe(tmp_path / "events.jsonl", probe_module.MAX_EVENTS)
    saved_hooks = observer._previous_sys, observer._previous_threading

    def foreign_hook(*args):
        return None

    try:
        if conflict in {"sys_hook", "both_hooks"}:
            sys.settrace(foreign_hook)
        if conflict in {"thread_hook", "both_hooks"}:
            threading.settrace(foreign_hook)
        if conflict == "other_thread":
            monkeypatch.setattr(probe_module.threading, "enumerate", lambda: [object(), object()])
        current_hooks = sys.gettrace(), threading.gettrace()
        with pytest.raises(RuntimeError) as caught:
            observer.start()
        expected = (
            "diagnostic probe must be installed before threads start"
            if conflict == "other_thread"
            else "diagnostic probe cannot replace existing trace hooks"
        )
        assert caught.value.args == (expected,)
        assert (observer._previous_sys, observer._previous_threading) == saved_hooks
        assert (sys.gettrace(), threading.gettrace()) == current_hooks
        assert observer._seq == 0 and observer._finished is False
        os.fstat(observer._fd)
        observer.uninstall()
        assert (sys.gettrace(), threading.gettrace()) == current_hooks
        assert probe_module._INSTALLED is None
        with pytest.raises(OSError) as closed:
            os.fstat(observer._fd)
        assert closed.value.errno == errno.EBADF
    finally:
        observer.uninstall()
        sys.settrace(original_hooks[0])
        threading.settrace(original_hooks[1])


def lifecycle_snapshot(observer, path):
    """Raw local lifecycle state only, without any native-outcome inference."""
    return (
        dict(vars(observer)),
        path.read_bytes(),
        sys.gettrace(),
        threading.gettrace(),
        probe_module._INSTALLED,
    )


@pytest.mark.parametrize("entry", ["direct_unstarted", "direct", "install"])
@pytest.mark.parametrize("destination_kind", ["path", "caller_fd"])
def test_wrong_thread_uninstall_rejects_before_any_cleanup(
    tmp_path, monkeypatch, entry, destination_kind
):
    path = tmp_path / "events.jsonl"
    caller = (
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        if destination_kind == "caller_fd"
        else None
    )
    original_hooks = sys.gettrace(), threading.gettrace()
    observer = (
        probe_module.install(path if caller is None else caller)
        if entry == "install"
        else probe_module.Probe(path if caller is None else caller, probe_module.MAX_EVENTS)
    )
    if entry == "direct":
        observer.start()
    original_finish = observer.finish
    owner = threading.current_thread()
    errors, callback_calls, close_calls = [], [], []
    original_unregister, original_close = probe_module.atexit.unregister, os.close

    def forbidden_finish():
        pytest.fail("wrong-thread uninstall reached finish")

    def unregister(callback):
        callback_calls.append(callback)
        return original_unregister(callback)

    def close(descriptor):
        if descriptor == observer._fd:
            close_calls.append(descriptor)
        return original_close(descriptor)

    def attempt():
        for _ in range(2):
            before = sys.gettrace()
            try:
                observer.uninstall()
            except BaseException as error:  # noqa: BLE001 - relay the exact wrong-thread outcome to owner assertions
                errors.append(error)
            assert sys.gettrace() is before

    worker = threading.Thread(target=attempt)
    try:
        assert observer._owner_thread is owner
        with monkeypatch.context() as patch:
            patch.setattr(observer, "finish", forbidden_finish)
            patch.setattr(probe_module.atexit, "unregister", unregister)
            patch.setattr(probe_module.os, "close", close)
            before = lifecycle_snapshot(observer, path)
            worker.start()
            worker.join(3)
            assert not worker.is_alive()
            assert len(errors) == 2
            assert all(
                type(error) is RuntimeError
                and error.args == ("diagnostic probe must be uninstalled on its owning thread",)
                for error in errors
            )
            assert lifecycle_snapshot(observer, path) == before
            assert callback_calls == close_calls == []
            os.fstat(observer._fd)
            if caller is not None:
                os.fstat(caller)
            patch.setattr(observer, "finish", original_finish)
            observer.uninstall()
            assert len(callback_calls) == len(close_calls) == 1
        assert (sys.gettrace(), threading.gettrace()) == original_hooks
        assert probe_module._INSTALLED is None
        assert observer._hooks_exposed is False
        with pytest.raises(OSError) as closed:
            os.fstat(observer._fd)
        assert closed.value.errno == errno.EBADF
        if caller is not None:
            os.fstat(caller)
    finally:
        worker.join(3) if worker.ident is not None else None
        observer.uninstall()
        if caller is not None:
            original_close(caller)


@pytest.mark.parametrize("entry", ["direct", "install"])
@pytest.mark.parametrize("destination_kind", ["path", "caller_fd"])
@pytest.mark.parametrize("worker_state", ["joined", "live"])
def test_owner_uninstall_requires_all_traced_python_threads_stopped(
    tmp_path, monkeypatch, entry, destination_kind, worker_state
):
    path = tmp_path / "events.jsonl"
    caller = (
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        if destination_kind == "caller_fd"
        else None
    )
    original_hooks = sys.gettrace(), threading.gettrace()
    observer = (
        probe_module.install(path if caller is None else caller)
        if entry == "install"
        else probe_module.Probe(path if caller is None else caller, probe_module.MAX_EVENTS)
    )
    if entry == "direct":
        observer.start()
    ready, release = threading.Event(), threading.Event()
    observed = []

    def work():
        observed.append((sys.gettrace() is observer._hook, observer._finished))
        ready.set()
        assert release.wait(3)
        observed.append((sys.gettrace() is observer._hook, observer._finished))

    def forbidden_finish():
        pytest.fail("live-thread rejection reached finish")

    worker = threading.Thread(target=work)
    try:
        worker.start()
        assert ready.wait(3)
        if worker_state == "live":
            with monkeypatch.context() as patch:
                patch.setattr(observer, "finish", forbidden_finish)
                before = lifecycle_snapshot(observer, path)
                with pytest.raises(RuntimeError) as caught:
                    observer.uninstall()
                assert caught.value.args == (
                    "diagnostic probe must be uninstalled after other Python threads stop",
                )
                assert lifecycle_snapshot(observer, path) == before
                assert worker.is_alive()
                os.fstat(observer._fd)
                if caller is not None:
                    os.fstat(caller)
        release.set()
        worker.join(3)
        assert not worker.is_alive()
        assert observed == [(True, False), (True, False)]
        observer.uninstall()
        assert observer._finished is True and observer._hooks_exposed is False
        assert (sys.gettrace(), threading.gettrace()) == original_hooks
        assert probe_module._INSTALLED is None
        with pytest.raises(OSError) as closed:
            os.fstat(observer._fd)
        assert closed.value.errno == errno.EBADF
        if caller is not None:
            os.fstat(caller)
        following = probe_module.install(tmp_path / "following.jsonl")
        following.uninstall()
        assert (sys.gettrace(), threading.gettrace()) == original_hooks
        assert probe_module._INSTALLED is None
    finally:
        release.set()
        worker.join(3) if worker.ident is not None else None
        observer.uninstall()
        if caller is not None:
            os.close(caller)


@pytest.mark.parametrize("destination_kind", ["path", "caller_fd"])
def test_successful_start_binds_actual_thread_after_direct_construction_elsewhere(
    tmp_path, destination_kind
):
    path = tmp_path / "events.jsonl"
    caller = (
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        if destination_kind == "caller_fd"
        else None
    )
    created = []

    def construct():
        created.append(probe_module.Probe(path if caller is None else caller, probe_module.MAX_EVENTS))

    creator = threading.Thread(target=construct)
    observer = None
    try:
        creator.start()
        creator.join(3)
        assert not creator.is_alive() and len(created) == 1
        observer = created[0]
        assert observer._owner_thread is creator
        observer.start()
        assert observer._owner_thread is threading.current_thread()
        observer.uninstall()
        assert sys.gettrace() is threading.gettrace() is None
        assert observer._hooks_exposed is False
        if caller is not None:
            os.fstat(caller)
    finally:
        creator.join(3) if creator.ident is not None else None
        if observer is not None:
            observer.uninstall()
        if caller is not None:
            os.close(caller)


def test_thread_ownership_uses_object_identity_not_reused_integer(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    observer = probe_module.Probe(path, probe_module.MAX_EVENTS)
    owner = threading.current_thread()
    impostor = threading.Thread()
    impostor._ident = owner.ident  # Explicit same-integer/different-object control; no OS reuse claim.
    try:
        assert observer._owner_thread is owner
        assert impostor is not owner and impostor.ident == owner.ident
        before = lifecycle_snapshot(observer, path)
        with monkeypatch.context() as patch:
            patch.setattr(probe_module.threading, "current_thread", lambda: impostor)
            with pytest.raises(RuntimeError, match="owning thread"):
                observer.uninstall()
        assert lifecycle_snapshot(observer, path) == before
        os.fstat(observer._fd)
        observer.uninstall()
    finally:
        observer.uninstall()


@pytest.mark.parametrize("entry", ["direct", "install"])
@pytest.mark.parametrize("destination_kind", ["path", "caller_fd"])
def test_never_started_cleanup_preserves_rejection_with_preexisting_threads(
    tmp_path, monkeypatch, entry, destination_kind
):
    path = tmp_path / "events.jsonl"
    caller = (
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        if destination_kind == "caller_fd"
        else None
    )
    ready, release = threading.Event(), threading.Event()
    seen, errors, worker_hooks = [], [], []
    original_start = probe_module.Probe.start

    def work():
        worker_hooks.append(sys.gettrace())
        ready.set()
        assert release.wait(3)
        worker_hooks.append(sys.gettrace())

    def start(observer):
        seen.append(observer)
        try:
            original_start(observer)
        except RuntimeError as error:
            errors.append(error)
            raise

    worker = threading.Thread(target=work)
    try:
        worker.start()
        assert ready.wait(3)
        monkeypatch.setattr(probe_module.Probe, "start", start)
        with pytest.raises(RuntimeError) as caught:
            if entry == "install":
                probe_module.install(path if caller is None else caller)
            else:
                observer = probe_module.Probe(
                    path if caller is None else caller, probe_module.MAX_EVENTS
                )
                try:
                    observer.start()
                finally:
                    observer.uninstall()
        assert len(seen) == len(errors) == 1 and caught.value is errors[0]
        assert caught.value.args == ("diagnostic probe must be installed before threads start",)
        observer = seen[0]
        assert observer._hooks_exposed is False and observer._finished is True
        assert worker.is_alive()
        assert sys.gettrace() is threading.gettrace() is None
        assert probe_module._INSTALLED is None
        with pytest.raises(OSError) as closed:
            os.fstat(observer._fd)
        assert closed.value.errno == errno.EBADF
        if caller is not None:
            os.fstat(caller)
        release.set()
        worker.join(3)
        assert not worker.is_alive() and worker_hooks == [None, None]
    finally:
        release.set()
        worker.join(3) if worker.ident is not None else None
        if seen:
            seen[0].uninstall()
        if caller is not None:
            os.close(caller)


def test_real_probe_unsupported_family_does_not_hide_corrupt_suffix(probe, native):
    observer, path = probe
    host, _ = native()
    assert entered(host) is True
    rows = records(observer, path)
    assert rows[-1]["complete"] is True
    assert any(row["event"] == "ticket_create" for row in rows)
    spec = importlib.util.spec_from_file_location(
        "probe_offline_observations", _PROBE_PATH.parents[2] / "scripts/cli_capture_observations.py"
    )
    offline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(offline)
    with pytest.raises(offline.UnsupportedEvent):
        offline.events(path.read_bytes(), os.getpid())
    rows[-1]["seq"] += 1
    corrupt = b"".join((json.dumps(row) + "\n").encode() for row in rows)
    with pytest.raises(ValueError) as caught:
        offline.events(corrupt, os.getpid())
    assert not isinstance(caught.value, offline.UnsupportedEvent)
