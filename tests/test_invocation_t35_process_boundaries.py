"""Native owner publication cut points, not power loss or transport qualification."""

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

pytest_plugins = ["tests.test_invocation_audit"]
RAW = b"synthetic T35 boundary response"


def _writer(root, point, omit_pause=False):
    from code_forge.invocation_audit import AttemptRecorder
    from code_forge.invocation_owner import InvocationOwner
    from tests.test_invocation_owner import Clock, begin, bound_context, finish, manifest, utc
    from tests.test_invocation_t35_ack_recovery import _no_external_operations
    from tests.test_ordinary_audit_native import _journal

    forbidden = _no_external_operations()
    ctx = bound_context()
    owner = InvocationOwner.create(
        artifact_root=root, context=ctx, monotonic=Clock(), utc_now=utc, audit_schema_version=2
    )
    assert owner.publish_input(manifest(ctx)).admitted
    run = root / ctx.run_id
    patch = pytest.MonkeyPatch()
    original_append, original_stage, original_sync = (
        AttemptRecorder._append_event,
        AttemptRecorder._stage,
        os.fsync,
    )
    syncs, reached = [], []
    returned = {"start": False, "raw": False, "acquired": False, "finish": False}

    def synced(fd):
        result = original_sync(fd)
        info = os.fstat(fd)
        syncs.append((os.readlink(f"/proc/self/fd/{fd}"), info.st_dev, info.st_ino))
        return result

    def stop():
        if omit_pause:
            return
        assert not reached and not returned[point]
        reached.append(point)
        rows = _journal(run)
        assert len([r for r in rows if r["event"] == "start"]) == 1
        if point == "raw":
            assert not any(r["event"] in ("acquired", "settled_acquired") for r in rows)
        if point != "start":
            paths = list(run.glob("raw-*"))
            assert len(paths) == 1 and paths[0].read_bytes() == RAW
            info = paths[0].stat()
            assert (info.st_dev, info.st_ino) in {r[1:] for r in syncs}
            assert str(run) in {r[0] for r in syncs}
        if point != "raw":
            assert str(run / "journal") in {r[0] for r in syncs}
        assert forbidden == []
        print(
            json.dumps({"run_id": ctx.run_id, "point": point, "returned": returned[point]}), flush=True
        )
        assert sys.stdin.readline() == "release\n"

    def append(recorder, handle, fd, event):
        result = original_append(recorder, handle, fd, event)
        target = event["event"]
        if target == point or (point == "acquired" and target == "settled_acquired"):
            stop()
        return result

    def stage(recorder, fd, name, data):
        result = original_stage(recorder, fd, name, data)
        if point == "raw" and name.startswith("raw-"):
            stop()
        return result

    patch.setattr(os, "fsync", synced)
    patch.setattr(AttemptRecorder, "_append_event", append)
    patch.setattr(AttemptRecorder, "_stage", stage)
    try:
        lease = begin(owner)
        returned["start"] = True
        lease.entered("entered_api_send")
        # Clear earlier journal syncs so each acquisition cut proves its own sync.
        if point != "start":
            syncs.clear()
        ack = lease.acquired(layer="wire", data=RAW, complete=True)
        assert ack.admitted and not ack.publication_uncertain
        returned["raw"] = returned["acquired"] = True
        if point == "finish":
            syncs.clear()
        assert finish(lease).admitted
        returned["finish"] = True
        assert reached == [point], "cutpoint-oracle"
        assert returned[point] and not forbidden
    finally:
        patch.undo()


def _reader(root, run_id, point, killed):
    from code_forge.invocation_audit import AttemptRecorder
    from tests.test_invocation_t35_ack_recovery import _no_external_operations
    from tests.test_ordinary_audit_native import _journal

    forbidden = _no_external_operations()
    run = root / run_id
    before = {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}
    rows = _journal(run)
    assert len([r for r in rows if r["event"] == "start"]) == 1
    terminal = not killed or point == "finish"
    finishes = [r for r in rows if r["event"] == "finish"]
    assert len(finishes) == int(terminal), "finish-oracle"
    acquired = not killed or point in ("acquired", "finish")
    assert len([r for r in rows if r["event"] in ("acquired", "settled_acquired")]) == int(acquired)
    snapshot = AttemptRecorder.reopen_scoped(root, run_id)
    assert len(snapshot.attempts) == 1
    attempt = snapshot.attempts[0]
    assert attempt.outcome == ("completed" if terminal else "unknown")
    assert attempt.duration_s == (3.0 if terminal else None)
    assert attempt.dispatch_state == (
        "unknown" if killed and point in ("start", "raw") else "entered_api_send"
    )
    if terminal:
        event = finishes[0]
        assert event["attempt_id"] == attempt.attempt_id
        assert event["outcome"] == "completed" and event["error_class"] is None
        assert event["duration_s"] == 3.0 and event["dispatch_state"] == "entered_api_send"
    assert len(attempt.raw_refs) == int(acquired)
    assert attempt.usage["input_tokens"]["total"] is None
    assert attempt.usage["output_tokens"]["total"] is None
    assert not any(r["event"] == "acquired" for r in rows)
    assert sum(r["event"] == "settled_acquired" for r in rows) == int(acquired)
    if killed and point == "start":
        assert attempt.dispatch_state == "unknown"
    if acquired:
        ref = attempt.raw_refs[0]
        assert before["raw-" + ref.artifact_id] == RAW
        assert ref.sha256 == hashlib.sha256(RAW).hexdigest()
        assert ref.original_bytes == ref.retained_bytes == len(RAW) and not ref.partial
    raw_files = [data for name, data in before.items() if name.startswith("raw-")]
    assert raw_files == ([] if killed and point == "start" else [RAW])
    orphan = killed and point == "raw"
    assert snapshot.state == ("incomplete" if orphan else "active"), snapshot.state
    assert snapshot.audit_complete is (not orphan)
    if orphan:
        assert any(fault.code == "integrity_error" for fault in snapshot.faults)
    assert "final" not in before and not forbidden
    assert {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()} == before
    print("verified", flush=True)


@pytest.mark.parametrize("point", ["start", "raw", "acquired", "finish"])
@pytest.mark.parametrize("killed", [True, False], ids=["sigkill", "healthy-release"])
def test_native_publication_before_owner_return(disk_root, point, killed):
    from tests.test_invocation_t34_process_recovery import _ready_line

    module = "tests.test_invocation_t35_process_boundaries"
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
        out, err = child.communicate(None if killed else b"release\n", timeout=10)
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
        if killed and point == "start":
            # Same real prefix, intentionally wrong expected boundary: precise oracle sensitivity.
            args[-2] = "finish"
            negative = subprocess.run(args, capture_output=True, text=True, timeout=15)
            assert negative.returncode == 1 and not negative.stdout
            assert "AssertionError: finish-oracle" in negative.stderr, negative.stderr
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


def test_omitted_cutpoint_cannot_advertise_readiness(disk_root):
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.test_invocation_t35_process_boundaries",
            "write-control",
            str(disk_root),
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 1 and not result.stdout
    assert "AssertionError: cutpoint-oracle" in result.stderr, result.stderr


if __name__ == "__main__":
    if sys.argv[1] == "write":
        _writer(Path(sys.argv[2]), sys.argv[3])
    elif sys.argv[1] == "write-control":
        _writer(Path(sys.argv[2]), "acquired", omit_pause=True)
    else:
        _reader(Path(sys.argv[2]), sys.argv[3], sys.argv[4], bool(int(sys.argv[5])))
