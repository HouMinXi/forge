"""Native buffered-host SEAL/ADOPT return boundaries under process death.

Real HostAuditSession, supervisor and owner stage; synthetic bytes, no transport.
This is not power-loss, installed CLI, or whole-round qualification.
"""

import hashlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import pytest

pytest_plugins = ["tests.test_invocation_audit"]
RAW = b"synthetic T35 buffered host response"


def _writer(root, point, omit_pause=False):
    from code_forge.invocation_audit import AttemptRecorder
    from code_forge.invocation_host import HostAuditSession, HostAuditLimits
    from code_forge.invocation_io import IoLimits, ProcessAuditIo, _KEY
    from tests.test_invocation_host import admit, request
    from tests.test_invocation_owner import bound_context, manifest
    from tests.test_invocation_t35_ack_recovery import _no_external_operations
    from tests.test_ordinary_audit_native import _journal

    forbidden = _no_external_operations()
    context = bound_context()
    process = ProcessAuditIo(limits=IoLimits(32, 8192, 512, 8), warn=lambda _: None, _key=_KEY)
    notices = []
    host = HostAuditSession(
        artifact_root=root,
        context=context,
        manifest=manifest(context),
        supervisor=process,
        warn=notices.append,
        limits=HostAuditLimits(
            capture_buffer_bytes=4096, batch_bytes=512, control_timeout_s=10, flush_timeout_s=10
        ),
    )
    patch = pytest.MonkeyPatch()
    run = root / context.run_id
    returned, reached, syncs = [], [], []
    original_sync = os.fsync
    original = getattr(AttemptRecorder, point + "_stage")

    def synced(fd):
        result = original_sync(fd)
        info = os.fstat(fd)
        syncs.append((os.readlink(f"/proc/self/fd/{fd}"), info.st_dev, info.st_ino))
        return result

    def boundary(recorder, *args, **kwargs):
        sync_start = len(syncs)
        ack = original(recorder, *args, **kwargs)
        assert ack.admitted and not ack.publication_uncertain
        assert threading.current_thread() is process._worker
        if not omit_pause:
            assert not reached and not returned
            assert host._adopted == host._settled == host._accepted == 0
            assert attempt._adoption is None and not attempt._settled
            assert attempt._stage._adopted is None
            owner_record = host._owner._leases[id(attempt._lease)]
            assert not owner_record.refs and not owner_record.settled
            reached.append(point)
            rows = _journal(run)
            assert sum(r["event"] == "stage_seal" for r in rows) == 1
            assert sum(r["event"] == "settled_acquired" for r in rows) == int(point == "adopt")
            assert not any(r["event"] == "finish" for r in rows)
            paths = list(run.glob("raw-*"))
            assert len(paths) == 1 and paths[0].read_bytes() == RAW
            info = paths[0].stat()
            assert (info.st_dev, info.st_ino) in {r[1:] for r in syncs}
            assert str(run / "journal") in {r[0] for r in syncs[sync_start:]}
            assert not forbidden
            print(json.dumps({"run_id": context.run_id, "point": point, "returned": False}), flush=True)
            assert sys.stdin.readline() == "release\n"
        return ack

    patch.setattr(os, "fsync", synced)
    patch.setattr(AttemptRecorder, point + "_stage", boundary)
    try:
        attempt = admit(host.scope(pass_name="fixture"), request(deadline=time.monotonic() + 30))
        assert attempt is not None
        captured = attempt.new_response(status=200, redirect_hop=None, deadline=attempt._deadline)
        assert b"".join(captured.chunks(io.BytesIO(RAW))) == RAW
        captured.seal()
        assert attempt.finish(outcome="completed", error_class=None, duration_s=0.25, usage=None)
        returned.append(True)
        assert reached == [point], "cutpoint-oracle"
        assert host.safe_summary().adopted_responses == host.safe_summary().settled_sends == 1
        assert not notices and not forbidden
    finally:
        patch.undo()
        host.close(primary=RuntimeError("test cleanup"))
        process.close()
        if process._worker is not None:
            process._worker.join(3)
            assert not process._worker.is_alive()


def _reader(root, run_id, point, killed):
    from code_forge.invocation_audit import AttemptRecorder
    from tests.test_invocation_t35_ack_recovery import _no_external_operations
    from tests.test_ordinary_audit_native import _journal

    forbidden = _no_external_operations()
    run = root / run_id
    before = {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}
    rows = _journal(run)
    assert sum(r["event"] == "start" for r in rows) == 1
    assert sum(r["event"] == "stage_seal" for r in rows) == 1
    adopted = not killed or point == "adopt"
    assert sum(r["event"] == "settled_acquired" for r in rows) == int(adopted), "adoption-oracle"
    assert not any(r["event"] == "acquired" for r in rows)
    finishes = [r for r in rows if r["event"] == "finish"]
    assert len(finishes) == int(not killed)
    snapshot = AttemptRecorder.reopen_scoped(root, run_id)
    assert len(snapshot.attempts) == 1
    attempt = snapshot.attempts[0]
    assert attempt.outcome == ("unknown" if killed else "completed")
    assert attempt.duration_s == (None if killed else 0.25)
    assert attempt.dispatch_state == ("entered_api_send" if adopted else "unknown")
    assert len(attempt.raw_refs) == int(adopted)
    assert [v for k, v in before.items() if k.startswith("raw-")] == [RAW]
    if adopted:
        ref = attempt.raw_refs[0]
        assert before["raw-" + ref.artifact_id] == RAW
        assert ref.sha256 == hashlib.sha256(RAW).hexdigest()
        assert ref.original_bytes == ref.retained_bytes == len(RAW) and not ref.partial
    if finishes:
        event = finishes[0]
        assert event["attempt_id"] == attempt.attempt_id and event["outcome"] == "completed"
        assert event["duration_s"] == 0.25 and event["error_class"] is None
        assert event["dispatch_state"] == "entered_api_send"
    assert attempt.usage["input_tokens"]["total"] is None
    assert attempt.usage["output_tokens"]["total"] is None
    stages = AttemptRecorder.reopen_stages(root, run_id)
    assert stages.prefixes_verified and len(stages.stages) == 1
    stage = stages.stages[0]
    assert stage.identity.attempt_id == attempt.attempt_id
    assert stage.identity.run_id == run_id and stage.identity.layer == "wire"
    assert before["raw-" + stage.identity.artifact_id] == RAW
    if adopted:
        assert stage.identity.artifact_id == ref.artifact_id
        assert ref.layer == stage.identity.layer
        adoption = next(r for r in rows if r["event"] == "settled_acquired")
        assert adoption["attempt_id"] == attempt.attempt_id
        assert adoption["observation_id"] == stage.identity.observation_id
        assert adoption["layer"] == stage.identity.layer
        assert adoption["ref"]["artifact_id"] == ref.artifact_id
    assert stage.sealed and stage.adopted is adopted
    assert stage.checkpoint_bytes == len(RAW)
    assert stage.checkpoint_sha256 == hashlib.sha256(RAW).hexdigest()
    assert stage.unverified_tail_bytes == 0
    assert snapshot.state == ("active" if adopted else "incomplete")
    assert snapshot.audit_complete is adopted
    assert "final" not in before and not forbidden
    assert {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()} == before
    print("verified", flush=True)


@pytest.mark.parametrize("point", ["seal", "adopt"])
@pytest.mark.parametrize("killed", [True, False], ids=["sigkill", "healthy-release"])
def test_buffered_host_native_stage_return_boundary(disk_root, point, killed):
    from tests.test_invocation_t34_process_recovery import _ready_line

    module = "tests.test_invocation_t35_host_recovery"
    child = subprocess.Popen(
        [sys.executable, "-B", "-m", module, "write", str(disk_root), point],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        identity = json.loads(_ready_line(child.stdout.fileno()))
        assert identity["point"] == point and identity["returned"] is False
        if killed:
            child.kill()
        out, err = child.communicate(None if killed else b"release\n", timeout=15)
        assert child.returncode == (-signal.SIGKILL if killed else 0) and not out and not err, err
        args = [
            sys.executable,
            "-B",
            "-m",
            module,
            "read",
            str(disk_root),
            identity["run_id"],
            point,
            str(int(killed)),
        ]
        reader = subprocess.run(args, capture_output=True, text=True, timeout=15)
        assert reader.returncode == 0 and not reader.stderr, reader.stderr
        assert reader.stdout == "verified\n"
        if killed and point == "seal":
            args[-2] = "adopt"
            control = subprocess.run(args, capture_output=True, text=True, timeout=15)
            assert control.returncode == 1 and not control.stdout
            assert "AssertionError: adoption-oracle" in control.stderr, control.stderr
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


def test_omitted_host_cutpoint_cannot_advertise_readiness(disk_root):
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.test_invocation_t35_host_recovery",
            "write-control",
            str(disk_root),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 1 and not result.stdout
    assert "AssertionError: cutpoint-oracle" in result.stderr, result.stderr


if __name__ == "__main__":
    if sys.argv[1] == "write":
        _writer(Path(sys.argv[2]), sys.argv[3])
    elif sys.argv[1] == "write-control":
        _writer(Path(sys.argv[2]), "adopt", omit_pause=True)
    else:
        _reader(Path(sys.argv[2]), sys.argv[3], sys.argv[4], bool(int(sys.argv[5])))
