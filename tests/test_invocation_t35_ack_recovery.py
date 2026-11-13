"""R03-08 native host slice: durable finalization, then lost summary return.

Synthetic post-publication OSError; no transport, power loss or whole-review claim.
Default mode warns rather than requiring the product operation to fail.
This covers only R03-08, not T35 start/raw/finish-return boundaries.
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytest_plugins = ["tests.test_invocation_audit"]
RAW = b"synthetic R03-08 response"


class ForbiddenExternalOperation(BaseException):
    pass


def _no_external_operations():
    calls = []

    def audit(event, args):
        if event.startswith(
            ("socket.", "subprocess.Popen", "os.system", "os.fork", "os.posix_spawn", "os.exec")
        ):
            calls.append(event)
            raise ForbiddenExternalOperation(event)

    sys.addaudithook(audit)
    return calls


def _writer(root, strict, lose_return, suppress_loss=False):
    from code_forge import invocation_audit as audit
    from code_forge.invocation_host import HostAuditSession, HostAuditLimits
    from code_forge.invocation_io import IoLimits, ProcessAuditIo, _KEY
    from code_forge.llm_invoke import AuditPersistenceError
    from code_forge.invocation_send_audit import SendLifecycleClosed
    from tests.test_invocation_host import admit, complete, request
    from tests.test_invocation_owner import bound_context, manifest
    from tests.test_ordinary_audit_native import _journal

    forbidden = _no_external_operations()
    context = bound_context()
    process = ProcessAuditIo(limits=IoLimits(32, 8192, 512, 8), warn=lambda _: None, _key=_KEY)
    notices = []
    host = HostAuditSession(
        artifact_root=root,
        context=context,
        manifest=manifest(context),
        strict=strict,
        supervisor=process,
        warn=notices.append,
        limits=HostAuditLimits(capture_buffer_bytes=4096, batch_bytes=512),
    )
    patch = pytest.MonkeyPatch()
    try:
        attempt = admit(host.scope(pass_name="fixture"), request())
        assert attempt is not None and complete(attempt, RAW)
        owner = host._owner
        run = root / context.run_id
        calls, syncs, summary_calls = [], [], []
        original_finalize = audit.AttemptRecorder.finalize
        original_summary = type(owner)._summary
        original_sync = os.fsync

        def synced(fd):
            result = original_sync(fd)
            syncs.append(os.readlink(f"/proc/self/fd/{fd}"))
            return result

        def finalize(recorder, **kwargs):
            calls.append(recorder)
            ack = original_finalize(recorder, **kwargs)
            assert ack.admitted and not ack.publication_uncertain
            assert (run / "final").is_file()
            assert str(run / "journal") in syncs and str(run) in syncs
            assert len([r for r in _journal(run) if r["event"] == "run_final"]) == 1
            return ack

        def summary(current):
            summary_calls.append(current)
            assert len(calls) == 1 and (run / "final").is_file()
            if lose_return and not suppress_loss:
                raise OSError("synthetic post-finalization summary loss")
            return original_summary(current)

        patch.setattr(os, "fsync", synced)
        patch.setattr(audit.AttemptRecorder, "finalize", finalize)
        patch.setattr(type(owner), "_summary", summary)
        if lose_return and strict:
            with pytest.raises(AuditPersistenceError):
                host.finalize()
        else:
            host.finalize()
        result = host.safe_summary()
        assert result.state == ("incomplete" if lose_return else "completed"), "host-state-oracle"
        assert result.audit_complete is (not lose_return)
        assert result.publication_uncertain is lose_return
        assert host.failed is lose_return and host.strict_failed is (strict and lose_return)
        assert bool(notices) is lose_return
        before_retry = {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}
        assert host.finalize() == result
        with pytest.raises(SendLifecycleClosed):
            host.scope(pass_name="fixture").start_send(request())
        assert {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()} == before_retry
        assert forbidden == []
        assert len(calls) == len(summary_calls) == 1
        assert host._closed and host._finalize_started
        print(json.dumps({"run_id": context.run_id}), flush=True)
    finally:
        patch.undo()
        host.close(primary=RuntimeError("test cleanup"))
        process.close()
        if process._worker is not None:
            process._worker.join(3)
            assert not process._worker.is_alive()


def _reader(root, run_id):
    from code_forge.invocation_audit import AttemptRecorder
    from tests.test_ordinary_audit_native import _journal

    forbidden = _no_external_operations()
    run = root / run_id
    before = {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}
    snapshot = AttemptRecorder.reopen_scoped(root, run_id)
    assert snapshot.state == "completed" and snapshot.audit_complete
    assert len(snapshot.attempts) == 1
    attempt = snapshot.attempts[0]
    assert attempt.outcome == "completed" and len(attempt.raw_refs) == 1
    ref = attempt.raw_refs[0]
    assert before["raw-" + ref.artifact_id] == RAW
    assert ref.sha256 == hashlib.sha256(RAW).hexdigest()
    assert ref.retained_bytes == ref.original_bytes == len(RAW) and not ref.partial
    assert attempt.usage["input_tokens"]["total"] is None
    assert attempt.usage["output_tokens"]["total"] is None
    finishes = [r for r in _journal(run) if r["event"] == "finish"]
    assert len(finishes) == 1 and finishes[0]["error_class"] is None
    assert "final" in before
    assert len([r for r in _journal(run) if r["event"] == "run_final"]) == 1
    assert {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()} == before
    assert forbidden == []
    print("verified", flush=True)


@pytest.mark.parametrize("strict", [False, True], ids=["warning", "strict"])
@pytest.mark.parametrize("lose_return", [False, True], ids=["healthy", "lost-summary"])
def test_durable_finalize_survives_lost_host_return(disk_root, strict, lose_return):
    module = "tests.test_invocation_t35_ack_recovery"
    writer = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            module,
            "write",
            str(disk_root),
            str(int(strict)),
            str(int(lose_return)),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert writer.returncode == 0 and not writer.stderr, writer.stderr
    identity = json.loads(writer.stdout)
    reader = subprocess.run(
        [sys.executable, "-B", "-m", module, "read", str(disk_root), identity["run_id"]],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert reader.returncode == 0 and not reader.stderr, reader.stderr
    assert reader.stdout == "verified\n"


def test_lost_return_oracle_rejects_omitted_fault(disk_root):
    # Real healthy publication cannot pass the oracle for a lost host return.
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.test_invocation_t35_ack_recovery",
            "write-control",
            str(disk_root),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 1 and not result.stdout
    assert "AssertionError: host-state-oracle" in result.stderr, result.stderr


if __name__ == "__main__":
    if sys.argv[1] == "write":
        _writer(Path(sys.argv[2]), bool(int(sys.argv[3])), bool(int(sys.argv[4])))
    elif sys.argv[1] == "write-control":
        _writer(Path(sys.argv[2]), False, True, suppress_loss=True)
    else:
        _reader(Path(sys.argv[2]), sys.argv[3])
