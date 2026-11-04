"""Offline tests for reviewed-source and dispatch fencing; no worker execution."""

import importlib.util
from pathlib import Path

import pytest

path = Path(__file__).resolve().parents[1] / "scripts/dependency_view_runner.py"
spec = importlib.util.spec_from_file_location("view_runner", path)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def valid_context():
    sha = "a" * 40
    return {
        "REPOSITORY": "HouMinXi/forge",
        "RUN_ID": "123",
        "RUN_ATTEMPT": "1",
        "JOB": "dependency-view",
        "REF": "refs/heads/" + runner.BRANCH,
        "WORKFLOW_SHA": sha,
        "WORKFLOW_REF": "HouMinXi/forge/" + runner.WORKFLOW + "@refs/heads/" + runner.BRANCH,
        "SOURCE_SHA": sha,
        "EXPECTED_SHA": sha,
        "EVENT": "workflow_dispatch",
        "ORDINARY": "false",
        "FIXED": "false",
        "VIEW": "true",
    }


def put_context(monkeypatch, values):
    for key, value in values.items():
        monkeypatch.setenv("CONTEXT_" + key, value)


def test_exact_single_mode_context_and_conflicts(monkeypatch):
    valid = valid_context()
    put_context(monkeypatch, valid)
    assert runner.context() == valid
    for key, wrong in (
        ("FIXED", "true"),
        ("ORDINARY", "true"),
        ("VIEW", "false"),
        ("RUN_ATTEMPT", "2"),
        ("EVENT", "pull_request"),
        ("EXPECTED_SHA", "b" * 40),
        ("JOB", "ordinary-dependency"),
        ("REF", "refs/heads/main"),
    ):
        with monkeypatch.context() as patch:
            patch.setenv("CONTEXT_" + key, wrong)
            with pytest.raises(ValueError):
                runner.context()


def test_mode_refusal_precedes_source_binding_or_worker(monkeypatch):
    put_context(monkeypatch, {**valid_context(), "FIXED": "true"})
    monkeypatch.setattr(runner.sys, "argv", ["runner"])
    monkeypatch.setattr(runner.os, "getuid", lambda: 1000)
    monkeypatch.setattr(runner.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(runner.os, "getgid", lambda: 1000)
    monkeypatch.setattr(runner.os, "getegid", lambda: 1000)
    monkeypatch.setattr(runner, "bind", lambda _: pytest.fail("must not bind or launch"))
    result = runner.observe()
    assert result["status"] == "STOP" and result["native"] == "NOT_RUN"


def test_bind_tree_uses_immutable_sha_and_rejects_head_drift(monkeypatch):
    import hashlib
    from types import SimpleNamespace

    sha = "a" * 40
    source = b"fixture"
    blob = hashlib.sha1(b"blob 7\0" + source, usedforsecurity=False).hexdigest()
    records = b"\0".join(f"100644 blob {blob}\t{name}".encode() for name in runner.FILES) + b"\0"
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if "ls-tree" in args:
            assert args[args.index("ls-tree") + 2] == sha
            return SimpleNamespace(returncode=0, stderr=b"", stdout=records)
        heads = sum("rev-parse" in call for call in calls)
        return SimpleNamespace(
            returncode=0, stderr=b"", stdout=((sha if heads == 1 else "b" * 40) + "\n").encode()
        )

    class FakeGit:
        def stat(self):
            return SimpleNamespace(st_mode=0o100755, st_uid=0)

        def read_bytes(self):
            return b"\x7fELFfixture"

        def __str__(self):
            return "/usr/bin/git"

    monkeypatch.setattr(runner, "Path", lambda _: FakeGit())
    monkeypatch.setattr(runner, "read_source", lambda name: source)
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    with pytest.raises(ValueError):
        runner.bind(sha)
    assert len(calls) == 3


def test_exact_worker_bytes_and_post_run_source_drift(monkeypatch):
    import builtins
    import hashlib
    from unittest.mock import Mock

    valid = valid_context()
    put_context(monkeypatch, valid)
    monkeypatch.setattr(runner.sys, "argv", ["runner"])
    for name in ("getuid", "geteuid", "getgid", "getegid"):
        monkeypatch.setattr(runner.os, name, lambda: 1000)
    base = b"import builtins\nENV={}\ncapture=builtins._view_test_capture\n"
    worker = b"# exact reviewed worker bytes\n"
    hashes = {
        "scripts/fixed_runner_metadata.py": hashlib.sha256(base).hexdigest(),
        "scripts/dependency_view.py": hashlib.sha256(worker).hexdigest(),
    }
    observed = b'{"schema":"forge-dependency-view-v1","status":"OBSERVED","qualification":"NOT_RUN"}'
    capture = Mock(return_value={"reason": None, "stderr_bytes": 0, "exit_code": 0, "stdout": observed})
    monkeypatch.setattr(builtins, "_view_test_capture", capture, raising=False)
    monkeypatch.setattr(runner, "bind", Mock(side_effect=[hashes, {**hashes, "changed": "yes"}]))
    monkeypatch.setattr(
        runner, "read_source", lambda name: base if name.endswith("metadata.py") else worker
    )
    result = runner.observe()
    assert result["status"] == "STOP" and result["native"] == "ATTEMPTED"
    args, env = capture.call_args.args[:2]
    assert args[:5] == ["/usr/bin/python3", "-I", "-S", "-B", "-c"]
    assert args[5].endswith(worker.decode())
    assert "BOUND_SOURCE_SHA256" in env


def test_dispatch_overflow_keeps_stat_receipt_without_large_payload():
    import json

    value = {
        "native": "RETURNED",
        "observation": {
            "phase": "complete",
            "stat_attempts": [{"sequence": 1}, {"sequence": 2}],
            "snapshot": "x" * 400000,
        },
    }
    raw, overflow = runner.bounded_output(value)
    result = json.loads(raw)
    assert overflow and result["status"] == "STOP"
    assert result["observation"]["stat_attempts"] == [{"sequence": 1}, {"sequence": 2}]
    assert "snapshot" not in result["observation"] and len(raw) < 4096


@pytest.mark.parametrize("length,overflow", [(299999, False), (300000, True), (300001, True)])
def test_runner_output_reserves_newline_at_exact_boundary(length, overflow):
    import json

    base = {
        "native": "RETURNED",
        "observation": {"phase": "complete", "stat_attempts": [{"sequence": 1}, {"sequence": 2}]},
        "payload": "",
    }
    overhead = len(json.dumps(base, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode())
    base["payload"] = "x" * (length - overhead)
    raw, exceeded = runner.bounded_output(base)
    assert exceeded is overflow and len(raw) + 1 <= 300000
    if overflow:
        assert json.loads(raw)["observation"]["stat_attempts"] == base["observation"]["stat_attempts"]
