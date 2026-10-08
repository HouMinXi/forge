"""Bounded algorithms/protocol units; synthetic reports are not owner qualification."""

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from code_forge import _mutation_process as process


NONCE = "a1" * 16


def _pipes():
    first, first_write = os.pipe()
    second, second_write = os.pipe()
    return os.fdopen(first, "rb", buffering=0), first_write, os.fdopen(second, "rb", buffering=0), second_write


def test_bounded_capture_drains_full_streams_with_shared_prefix_budget():
    stdout, out_write, stderr, err_write = _pipes()
    capture = process._BoundedCapture(stdout, stderr, 17)
    expected = {"stdout": hashlib.sha256(), "stderr": hashlib.sha256()}
    try:
        for ordinal in range(200):
            for name, fd in (("stdout", out_write), ("stderr", err_write)):
                data = (f"{name}:{ordinal}:".encode() * 70)
                expected[name].update(data)
                os.write(fd, data)
                capture.drain(0.1)
            assert sum(map(len, capture.prefix.values())) <= 17
        os.close(out_write)
        os.close(err_write)
        capture.drain(0.1)
        metadata = capture.metadata()
        assert metadata["retained_bytes"] == 17
        assert metadata["diagnostic_truncated"] is True
        for name, row in metadata["streams"].items():
            assert row["eof"] is True
            assert row["sha256"] == expected[name].hexdigest()
            assert row["bytes"] > 100_000
    finally:
        capture.close()
        stdout.close()
        stderr.close()


def test_drain_is_fair_and_returns_after_one_read_per_ready_stream(monkeypatch):
    stdout, out_write, stderr, err_write = _pipes()
    capture = process._BoundedCapture(stdout, stderr, 1)
    reads = []
    real_read = os.read

    def bounded_read(fd, size):
        reads.append((fd, size))
        return real_read(fd, size)

    try:
        os.write(out_write, b"x")
        os.write(err_write, b"y")
        monkeypatch.setattr(process.os, "read", bounded_read)
        capture.drain(0.1)
        assert reads == [(stdout.fileno(), process._READ_BYTES), (stderr.fileno(), process._READ_BYTES)]
        assert capture.counts == {"stdout": 1, "stderr": 1}
        assert capture.retained == 1
    finally:
        capture.close()
        for fd in (out_write, err_write):
            os.close(fd)
        stdout.close()
        stderr.close()


def test_empty_capture_has_complete_empty_hashes():
    stdout, out_write, stderr, err_write = _pipes()
    capture = process._BoundedCapture(stdout, stderr, 1)
    try:
        os.close(out_write)
        os.close(err_write)
        capture.drain(0.1)
        assert capture.metadata() == {
            "retained_bytes": 0, "diagnostic_truncated": False,
            "streams": {name: {"bytes": 0, "sha256": hashlib.sha256(b"").hexdigest(),
                               "retained_bytes": 0, "eof": True}
                        for name in ("stdout", "stderr")},
        }
    finally:
        capture.close()
        stdout.close()
        stderr.close()


def _report(request, owner, stdout=b"hello", stderr=b"problem"):
    return {
        "capture_version": 1, "invocation_nonce": request["invocation_nonce"],
        "caller_pid": request["caller_pid"], "caller_start_ticks": request["caller_start_ticks"],
        "owner_pid": owner.pid, "owner_start_ticks": owner.start_ticks,
        "driver_pid": 222, "driver_start_ticks": 33,
        "cwd": request["cwd"], "argv_sha256": process._binding_digest(request["argv"]),
        "env_sha256": process._binding_digest(request["env"]),
        "cleanup_complete": True, "timed_out": False, "cancelled": False, "returncode": 0,
        "duration_seconds": 0.01,
        "owned": [{"pid": 222, "start_ticks": 33, "observed_parent": owner.pid,
                   "remaining": False, "reaped_status": 0}],
        "resolved_executable": {"path": "/runtime/python", "sha256": "a" * 64},
        "retained_bytes": len(stdout) + len(stderr), "diagnostic_truncated": False,
        "streams": {name: {"bytes": len(value), "retained_bytes": len(value),
                           "sha256": hashlib.sha256(value).hexdigest(), "eof": True}
                    for name, value in (("stdout", stdout), ("stderr", stderr))},
    }


@pytest.fixture
def protocol():
    request = {"argv": ["python", "-m", "pytest"], "env": {"LANG": "C"}, "cwd": "/source",
               "caller_pid": 100, "caller_start_ticks": 10,
               "invocation_nonce": NONCE, "output_limit_bytes": 1024}
    owner = process._Identity(111, 100, 22, "S")
    return request, owner, _report(request, owner)


def test_report_accepts_complete_bound_metadata(protocol):
    request, owner, report = protocol
    process._validate_bounded_report(report, request, owner, 12)


@pytest.mark.parametrize("key,value", [
    ("invocation_nonce", "b" * 32), ("caller_pid", 2), ("caller_start_ticks", 11),
    ("owner_pid", 112), ("owner_start_ticks", 23), ("driver_pid", 100),
    ("driver_start_ticks", 34), ("argv_sha256", "0" * 64), ("env_sha256", "0" * 64),
    ("capture_version", True), ("retained_bytes", 13), ("diagnostic_truncated", True),
    ("duration_seconds", float("nan")), ("duration_seconds", -1), ("owned", []),
])
def test_report_rejects_missing_or_contradictory_binding(protocol, key, value):
    request, owner, report = protocol
    report[key] = value
    with pytest.raises(ValueError):
        process._validate_bounded_report(report, request, owner, 12)


@pytest.mark.parametrize("key,value", [("eof", False), ("bytes", True), ("retained_bytes", -1),
                                      ("sha256", "xyz"), ("unexpected", 1)])
def test_report_rejects_broken_stream_evidence(protocol, key, value):
    request, owner, report = protocol
    report["streams"]["stdout"][key] = value
    with pytest.raises(ValueError):
        process._validate_bounded_report(report, request, owner, 12)


def test_report_allows_explicit_diagnostic_truncation(protocol):
    request, owner, report = protocol
    report["streams"]["stdout"]["bytes"] = 1_000_000_000
    report["diagnostic_truncated"] = True
    process._validate_bounded_report(report, request, owner, 12)


def test_control_report_overflow_is_explicit_failure(protocol):
    _, _, report = protocol
    report["unbounded"] = "x" * process._REPORT_LIMIT_BYTES
    bounded = process._bounded_report(report)
    assert bounded["report_overflow"] is True
    assert "overflow" in bounded["error"]
    assert len(json.dumps(bounded).encode()) < process._REPORT_LIMIT_BYTES


def test_duplicate_json_report_fields_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        json.loads('{"cleanup_complete": false, "cleanup_complete": true}',
                   object_pairs_hook=process._unique_owner_object)


@pytest.mark.parametrize("limit,nonce", [(None, NONCE), (True, NONCE), (0, NONCE),
                                        (1_048_577, NONCE), (1024, None), (1024, "0" * 31),
                                        (1024, "A" * 32)])
def test_bounded_mode_rejects_invalid_opt_in_before_launch(monkeypatch, limit, nonce):
    monkeypatch.setattr(process.subprocess, "Popen", lambda *a, **kw: pytest.fail("launched"))
    with pytest.raises(ValueError):
        process.run_owned_command(["python"], timeout=1, output_limit_bytes=limit, invocation_nonce=nonce)


def test_exchange_uses_bounded_reads_and_never_communicate(monkeypatch):
    """A plain pipe-protocol subprocess, deliberately not an ownership qualification."""
    helper = subprocess.Popen(
        [sys.executable, "-c", "import sys; n=len(sys.stdin.buffer.read()); print(n); sys.stderr.write('diag')"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    monkeypatch.setattr(helper, "communicate", lambda *a, **k: pytest.fail("unbounded communicate"))
    raw, diagnostic, interruption, overflow = process._exchange_bounded(helper, b"x" * 100_000, 3)
    assert raw == b"100000\n" and diagnostic == b"diag"
    assert interruption is None and overflow is False and helper.returncode == 0


def test_exchange_caps_untrusted_owner_control_output(monkeypatch):
    helper = subprocess.Popen(
        [sys.executable, "-c", "import os,sys; sys.stdin.buffer.read(); os.write(1,b'x'*1000000)"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    monkeypatch.setattr(helper, "communicate", lambda *a, **k: pytest.fail("unbounded communicate"))
    raw, diagnostic, interruption, overflow = process._exchange_bounded(helper, b"{}", 3)
    assert len(raw) == process._REPORT_LIMIT_BYTES
    assert len(diagnostic) <= process._READ_BYTES
    assert isinstance(interruption, ValueError) and overflow is True


@pytest.fixture
def synthetic_owner(monkeypatch):
    """Validate parent packaging only; never substitutes for execution qualification."""
    owner = process._Identity(111, os.getpid(), 22, "S")
    caller = process._Identity(os.getpid(), 1, 10, "S")
    monkeypatch.setattr(process, "_identity", lambda pid: owner if pid == owner.pid else caller)
    helper = SimpleNamespace(pid=owner.pid, stdin=io.BytesIO(), poll=lambda: 0)
    monkeypatch.setattr(process.subprocess, "Popen", lambda *a, **kw: helper)
    state = {}

    def exchange(_helper, payload, timeout, **kwargs):
        request = json.loads(payload)["request"]
        report = _report(request, owner)
        state["request"] = request
        if "alter" in state:
            state["alter"](report)
        os.write(request["diagnostic_fd"], b"helloproblem")
        return json.dumps(report).encode(), b"", state.get("interruption"), False

    monkeypatch.setattr(process, "_exchange_bounded", exchange)
    return state


def _synthetic_run(**kwargs):
    return process.run_owned_command(
        ["python", "-m", "pytest"], timeout=1, output_limit_bytes=1024, invocation_nonce=NONCE, **kwargs,
    )


def test_completed_result_exposes_bounded_prefixes_and_identity(synthetic_owner):
    result = _synthetic_run(text=True)
    assert result.stdout == "hello" and result.stderr == "problem"
    assert result.ownership["invocation_nonce"] == NONCE
    assert result.ownership["driver_start_ticks"] == 33
    assert result.ownership["env_sha256"] == process._binding_digest(synthetic_owner["request"]["env"])


def test_timeout_exposes_bounded_prefixes_and_identity(synthetic_owner):
    synthetic_owner["alter"] = lambda report: report.update(timed_out=True, returncode=-15)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        _synthetic_run()
    assert caught.value.output == b"hello" and caught.value.stderr == b"problem"
    assert caught.value.ownership["cleanup_complete"] is True
    assert caught.value.cleanup_complete is True


def test_missing_runner_retains_ownership_metadata(synthetic_owner):
    synthetic_owner["alter"] = lambda report: report.update(error="missing runner", error_kind="FileNotFoundError")
    with pytest.raises(FileNotFoundError) as caught:
        _synthetic_run()
    assert caught.value.cleanup_complete is True
    assert caught.value.ownership["invocation_nonce"] == NONCE


def test_control_interruption_is_preserved_with_cleanup_evidence(synthetic_owner):
    cancellation = KeyboardInterrupt("cancel")
    synthetic_owner["interruption"] = cancellation
    with pytest.raises(KeyboardInterrupt) as caught:
        _synthetic_run()
    assert caught.value is cancellation
    assert caught.value.cleanup_complete is True
    assert caught.value.ownership["owner_start_ticks"] == 22


def test_invalid_report_cannot_mask_control_interruption(synthetic_owner):
    cancellation = SystemExit(7)
    synthetic_owner["interruption"] = cancellation
    synthetic_owner["alter"] = lambda report: report.update(owner_start_ticks=23)
    with pytest.raises(SystemExit) as caught:
        _synthetic_run()
    assert caught.value is cancellation
    assert caught.value.cleanup_complete is False


def test_cancellation_evidence_failure_preserves_first_control(synthetic_owner, monkeypatch):
    cancellation = KeyboardInterrupt("first")
    secondary = SystemExit("second")
    synthetic_owner["interruption"] = cancellation
    binder = process._bind_cancellation_evidence

    def fail_once(error, **evidence):
        if evidence.get("cleanup_complete") is True:
            raise secondary
        return binder(error, **evidence)

    monkeypatch.setattr(process, "_bind_cancellation_evidence", fail_once)
    with pytest.raises(KeyboardInterrupt) as caught:
        _synthetic_run()
    assert caught.value is cancellation
    assert caught.value.cleanup_complete is False
    assert caught.value.cleanup_evidence_error is secondary


def test_completed_report_has_no_prefix_payload(synthetic_owner):
    result = _synthetic_run()
    assert "stdout" not in result.ownership and "stderr" not in result.ownership
    assert len(json.dumps(result.ownership).encode()) <= process._REPORT_LIMIT_BYTES


def test_prefix_corruption_fails_with_verified_cleanup_truth(synthetic_owner):
    synthetic_owner["alter"] = lambda report: report["streams"]["stdout"].update(sha256="0" * 64)
    with pytest.raises(process.MutationProcessError, match="hash mismatch") as caught:
        _synthetic_run()
    assert caught.value.cleanup_complete is True


def test_bounded_tree_inventory_overflow_is_never_silently_truncated():
    tree = SimpleNamespace(
        live=lambda: [], identities={pid: process._Identity(pid, 1, pid + 2, "S")
                                     for pid in range(2, 5000)}, reaped={},
    )
    with pytest.raises(ValueError, match="identity report overflow"):
        process._bounded_tree_report(tree)


def test_executable_binding_retains_selected_venv_symlink(tmp_path):
    executable = tmp_path / "python"
    executable.symlink_to(sys.executable)
    record = process._executable_binding(["python"], str(tmp_path), {"PATH": "."})
    assert record["path"] == str(executable)
    assert record["realpath"] == str(Path(sys.executable).resolve())
    assert record["sha256"] == hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()


def test_bounded_owner_refuses_missing_procfs_capability_before_native_launch(tmp_path):
    try:
        Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children").read_bytes()
    except OSError:
        pass
    else:
        pytest.skip("Refusal-only test: real owned-execution qualification belongs on supported Linux")
    marker = tmp_path / "must-not-launch"
    argv = [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"]
    with pytest.raises(process.MutationProcessError, match="ownership unavailable") as caught:
        process.run_owned_command(argv, timeout=3, output_limit_bytes=1024, invocation_nonce=NONCE)
    assert caught.value.cleanup_complete is True
    assert caught.value.report["owned"] == []
    assert "driver_pid" not in caught.value.report
    assert caught.value.report["invocation_nonce"] == NONCE
    assert not marker.exists()
