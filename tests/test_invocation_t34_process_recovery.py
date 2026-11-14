"""T34 storage/owner slice: native publication followed by process death.

No external transport, whole-review verdict, or physical power-loss claim.
The writer owns no subprocesses; a new reader cannot reuse its in-memory state.
"""

import hashlib
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

import pytest

pytest_plugins = ["tests.test_invocation_audit"]
RAW = b'{"fixture":"completed-sibling","usage":7}'


def _writer(root, outcome):
    from code_forge.invocation_owner import InvocationOwner
    from tests.test_invocation_owner import (
        Clock,
        begin,
        bound_context,
        finish,
        manifest,
        usage,
        utc,
    )

    ctx = bound_context()
    owner = InvocationOwner.create(
        artifact_root=root, context=ctx, monotonic=Clock(), utc_now=utc, audit_schema_version=2
    )
    assert owner.publish_input(manifest(ctx)).admitted
    first, pending = begin(owner), begin(owner)
    first.entered("entered_api_send")
    pending.entered("entered_api_send")
    syncs = []
    real_sync = os.fsync

    def synced(fd):
        result = real_sync(fd)
        info = os.fstat(fd)
        syncs.append((os.readlink(f"/proc/self/fd/{fd}"), info.st_dev, info.st_ino))
        return result

    os.fsync = synced
    raw = first.acquired(layer="wire", data=RAW, complete=True)
    assert raw.admitted and not raw.publication_uncertain
    acquired_syncs = tuple(syncs)
    syncs.clear()
    assert first.observe(
        source_observation_id=raw.observation_id,
        frame_offset=0,
        frame_length=len(RAW),
        usage=usage(7),
        observed_backend=None,
    ).admitted
    observed_syncs = tuple(syncs)
    syncs.clear()
    ack = finish(first, outcome=outcome, error_class="FixtureFailure" if outcome == "failed" else None)
    assert ack.admitted and not ack.publication_uncertain
    run = root / ctx.run_id
    info = (run / ("raw-" + raw.ref.artifact_id)).stat()
    assert (info.st_dev, info.st_ino) in {row[1:] for row in acquired_syncs}
    for phase_syncs in (acquired_syncs, observed_syncs, syncs):
        assert str(run / "journal") in {row[0] for row in phase_syncs}
    assert str(run) in {row[0] for row in acquired_syncs}
    print(
        json.dumps(
            dict(
                run_id=ctx.run_id,
                first=first.attempt_id,
                pending=pending.attempt_id,
                artifact=raw.ref.artifact_id,
            )
        ),
        flush=True,
    )
    # Controller terminates only after all three real publication ACKs and
    # successful native fsync calls. No timing sleep or mocked durability.
    assert sys.stdin.readline() == "finish\n"
    assert finish(pending).admitted
    assert owner.finalize("completed").state == outcome


def _reader(root, identity, outcome, terminated):
    from code_forge.invocation_audit import AttemptRecorder
    from tests.test_ordinary_audit_native import _journal

    run = root / identity["run_id"]
    before = {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}
    assert before["raw-" + identity["artifact"]] == RAW, "raw-oracle"
    replay = AttemptRecorder.reopen_scoped(root, identity["run_id"])
    attempts = {a.attempt_id: a for a in replay.attempts}
    assert set(attempts) == {identity["first"], identity["pending"]}
    first, pending = attempts[identity["first"]], attempts[identity["pending"]]
    assert first.outcome == outcome
    assert len(first.raw_refs) == 1
    ref = first.raw_refs[0]
    assert ref.artifact_id == identity["artifact"]
    assert ref.sha256 == hashlib.sha256(RAW).hexdigest()
    assert (run / ("raw-" + ref.artifact_id)).read_bytes() == RAW
    assert ref.original_bytes == ref.retained_bytes == len(RAW) and not ref.partial
    assert first.usage["input_tokens"]["total"] == 7, "usage-oracle"
    assert first.usage["output_tokens"]["total"] == 7
    assert len(first.response_observations) == 1
    rows = _journal(run)
    finishes = [r for r in rows if r["event"] == "finish"]
    assert len(finishes) == (1 if terminated else 2)
    assert finishes[0]["attempt_id"] == first.attempt_id
    assert finishes[0]["error_class"] == ("FixtureFailure" if outcome == "failed" else None), (
        "error-oracle"
    )
    assert pending.outcome == ("unknown" if terminated else "completed")
    assert not pending.raw_refs
    assert pending.usage["input_tokens"]["total"] is None
    assert replay.state == ("active" if terminated else outcome)
    # audit_complete describes retained-prefix integrity, not host completion.
    assert replay.audit_complete
    assert (run / "final").exists() is (not terminated)
    assert {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()} == before
    print("verified", flush=True)


def _ready_line(fd):
    deadline, data = time.monotonic() + 15, bytearray()
    while b"\n" not in data:
        remaining = deadline - time.monotonic()
        assert remaining > 0 and select.select([fd], [], [], remaining)[0], "readiness deadline"
        block = os.read(fd, 2048 - len(data))
        assert block, "writer exited without readiness"
        data.extend(block)
        assert len(data) < 2048, "oversized readiness"
    assert data.count(b"\n") == 1 and data.endswith(b"\n")
    return bytes(data)


@pytest.mark.parametrize("damage", [None, "raw", "usage", "error"])
@pytest.mark.parametrize("terminated", [True, False], ids=["sigkill", "healthy-finalize"])
@pytest.mark.parametrize("outcome", ["completed", "failed"])
def test_terminal_sibling_survives_writer_sigkill(disk_root, outcome, terminated, damage):
    module = "tests.test_invocation_t34_process_recovery"
    child = subprocess.Popen(
        [sys.executable, "-B", "-m", module, "write", str(disk_root), outcome],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        identity = json.loads(_ready_line(child.stdout.fileno()))
        if terminated:
            child.kill()
        out, err = child.communicate(None if terminated else "finish\n", timeout=5)
        assert child.returncode == (-signal.SIGKILL if terminated else 0) and not out and not err
        reader = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                module,
                "read",
                str(disk_root),
                outcome,
                json.dumps(identity),
                str(int(terminated)),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert reader.returncode == 0, reader.stderr
        assert reader.stdout == "verified\n" and not reader.stderr
        if damage is not None:
            from tests.test_ordinary_audit_native import _journal

            run = disk_root / identity["run_id"]
            if damage == "raw":
                path = run / ("raw-" + identity["artifact"])
                path.write_bytes(b"!" + RAW[1:])
            else:
                rows = _journal(run)
                if damage == "usage":
                    row = next(r for r in rows if r["event"] == "response_observed")
                    row["usage"]["input_tokens"] = 8
                else:
                    row = next(r for r in rows if r["event"] == "finish")
                    row["error_class"] = "AlteredFixtureError"
                frames = []
                for row in rows:
                    payload = json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
                    frames.append(f"{len(payload):08x} ".encode() + payload + b"\n")
                (run / "journal").write_bytes(b"".join(frames))
            rejected = subprocess.run(reader.args, capture_output=True, text=True, timeout=15)
            assert rejected.returncode == 1 and not rejected.stdout
            assert f"AssertionError: {damage}-oracle" in rejected.stderr, rejected.stderr
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


if __name__ == "__main__":
    if sys.argv[1] == "write":
        _writer(Path(sys.argv[2]), sys.argv[3])
    else:
        _reader(Path(sys.argv[2]), json.loads(sys.argv[4]), sys.argv[3], bool(int(sys.argv[5])))
