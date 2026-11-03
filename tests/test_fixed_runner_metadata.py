"""Offline controls: no test invokes sudo, systemd or the native observer CLI."""

import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import socket
import struct
import subprocess
import sys
import tempfile
import time
import types

import pytest
import yaml

PATH = Path(__file__).parents[1] / "scripts/fixed_runner_metadata.py"
p = types.ModuleType("fixed_metadata_test_source")
p.__file__ = str(PATH)
exec(compile(PATH.read_bytes(), str(PATH), "exec"), p.__dict__)  # noqa: S102 -- source-only local test loader.
REAL_ORDINARY = p.ordinary


@pytest.fixture(autouse=True)
def forbid_local_sudo(monkeypatch):
    """Local authority excludes sudo, even if another test accidentally loses a mock."""
    original, attempts = subprocess.Popen, []

    def guarded(argv, *args, **kwargs):
        blocked = (
            "sudo" in argv
            if isinstance(argv, str)
            else any(Path(str(arg)).name == "sudo" for arg in argv)
        )
        if blocked:
            attempts.append(True)
            pytest.fail("local sudo execution is forbidden")
        return original(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", guarded)
    yield
    assert not attempts, "a caught sudo attempt still invalidates this test"


@pytest.fixture
def context_env(monkeypatch):
    values = {
        "REPOSITORY": "HouMinXi/forge",
        "RUN_ID": "12345",
        "RUN_ATTEMPT": "1",
        "JOB": "fixed-metadata",
        "REF": "refs/heads/" + p.BRANCH,
        "WORKFLOW_REF": "HouMinXi/forge/" + p.WORKFLOW + "@refs/heads/" + p.BRANCH,
        "SOURCE_SHA": "a" * 40,
        "EXPECTED_SHA": "a" * 40,
        "WORKFLOW_SHA": "a" * 40,
        "EVENT": "workflow_dispatch",
    }
    for key, value in values.items():
        monkeypatch.setenv("CONTEXT_" + key, value)
    monkeypatch.setenv("PRIVATE_SECRET", "do-not-emit-or-forward")
    return values


def capture_result(stdout=b"", exit_code=0, **extra):
    return {
        "stdout": stdout,
        "exit_code": exit_code,
        "stderr_bytes": 0,
        "reason": None,
        "errno": None,
        "direct_sudo_reaped": True,
        "privileged_descendants": "NOT_INDEPENDENTLY_OBSERVED",
        **extra,
    }


@pytest.fixture
def fixture_observer(monkeypatch, context_env):
    state = {
        "calls": [],
        "fault": None,
        "tools": {name: {"sha256": "b" * 64, "identity": (1,)} for name in p.TOOLS},
    }
    monkeypatch.setattr(p, "tools_snapshot", lambda deadline=None: copy.deepcopy(state["tools"]))
    monkeypatch.setattr(
        p, "source_snapshot", lambda expected, deadline=None: {p.SOURCE: "c" * 64, p.WORKFLOW: "d" * 64}
    )
    monkeypatch.setattr(p, "ordinary", lambda *args, **kwargs: b"timeout (GNU coreutils) 9.4\nfixture\n")
    monkeypatch.setattr(
        p,
        "manager",
        lambda uid: {
            "pid": os.getpid(),
            "starttime": p.starttime(os.getpid()),
            "private_peer": {
                "credentials": {"pid": os.getpid(), "uid": os.getuid(), "gid": os.getgid()},
                "endpoint": (),
            },
            "fd": os.pidfd_open(os.getpid()),
        },
    )
    monkeypatch.setattr(p, "recheck_manager", lambda *args, **kwargs: None)

    def capture(argv, env, deadline, limit=4096, privileged=False, cwd="/"):
        state["calls"].append(argv)
        assert privileged and env == p.ENV and cwd == "/"
        assert time.monotonic() < deadline <= time.monotonic() + 4
        index = len(state["calls"]) - 1
        if state["fault"] is not None and index == state.get("fault_index", 0):
            if isinstance(state["fault"], Exception):
                raise state["fault"]
            return capture_result(**state["fault"])
        if "/usr/bin/stat" in argv:
            data = p.self_numbers()
            return capture_result(
                "".join(f"{data[k][0]}:{data[k][1]}\n" for k in ("mnt", "cgroup", "root")).encode()
            )
        return capture_result(exit_code=0 if "^bwrap " in argv[-2] else 1)

    monkeypatch.setattr(p, "capture", capture)
    return state


def test_exact_four_fixed_reads_offline_positive(fixture_observer):
    result = p.observe()
    assert result["status"] == "OBSERVED"
    assert len(fixture_observer["calls"]) == len(result["commands"]) == 4
    assert result["observations"]["known_profile_matches"] == {"bwrap": True, "unpriv_bwrap": False}
    assert all(result["observations"]["matches_caller"].values())
    assert result["direct_command_cleanup"] == "DIRECT_SUDO_REAPED"
    assert result["privileged_descendants"] == "NOT_INDEPENDENTLY_OBSERVED"
    assert result["service"] == result["boundary"] == result["qualification"] == "NOT_RUN"
    assert result["ordinary_gate"] == "NOT_REPLACED"
    assert "gid" not in result["manager"]
    assert "do-not-emit" not in json.dumps(result)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("EXPECTED_SHA", ""),
        ("EXPECTED_SHA", "b" * 40),
        ("EXPECTED_SHA", "private-value"),
        ("RUN_ATTEMPT", "2"),
        ("REF", "refs/heads/main"),
        ("EVENT", "pull_request"),
        ("REPOSITORY", "other/forge"),
        ("JOB", "observe"),
        ("WORKFLOW_REF", "wrong"),
        ("WORKFLOW_SHA", "b" * 40),
        ("RUN_ID", "0"),
    ],
)
def test_context_failure_never_reaches_sudo(fixture_observer, monkeypatch, key, value):
    monkeypatch.setenv("CONTEXT_" + key, value)
    result = p.observe()
    assert result["status"] == "STOP" and result["reason"] == "context"
    assert result["context"] is None and result["direct_command_cleanup"] == "NOT_RUN"
    assert not fixture_observer["calls"] and "private-value" not in json.dumps(result)


@pytest.mark.parametrize(
    "fault",
    [
        {"exit_code": 124},
        {"exit_code": 137},
        {"exit_code": -9},
        {"exit_code": 2},
        {"stderr_bytes": 1},
        {"reason": "timeout", "exit_code": None, "direct_sudo_reaped": False},
        {"reason": "output"},
        {"stdout": b"private-path"},
        PermissionError(13, "private-path"),
        RuntimeError("private-value"),
    ],
)
@pytest.mark.parametrize("index", [0, 1, 3])
def test_failure_stops_all_later_privileged_calls(fixture_observer, fault, index):
    fixture_observer.update(fault=fault, fault_index=index)
    result = p.observe()
    assert result["status"] == "STOP" and len(fixture_observer["calls"]) == index + 1
    assert result["observations"] is None
    assert all(marker not in json.dumps(result) for marker in ("private-path", "private-value"))
    if isinstance(fault, Exception) or fault.get("direct_sudo_reaped") is False:
        assert result["direct_command_cleanup"] == "UNKNOWN"


def test_unknown_timeout_version_is_not_run(fixture_observer, monkeypatch):
    monkeypatch.setattr(p, "ordinary", lambda *args, **kwargs: b"timeout (GNU coreutils) 9.5\n")
    result = p.observe()
    assert result["reason"] == "version" and not fixture_observer["calls"]


def test_post_read_manager_change_is_not_admission(fixture_observer, monkeypatch):
    monkeypatch.setattr(
        p, "recheck_manager", lambda *args, **kwargs: (_ for _ in ()).throw(p.Stop("changed"))
    )
    result = p.observe()
    assert len(fixture_observer["calls"]) == 4 and result["reason"] == "changed"
    assert result["observations"] is None


def test_exact_root_argv_and_no_generic_interface():
    prefix = [
        "/usr/bin/sudo",
        "-n",
        "--",
        "/usr/bin/timeout",
        "--signal=TERM",
        "--kill-after=1s",
        "--",
        "2s",
    ]
    assert p.root_argv("stat", 123) == [
        *prefix,
        "/usr/bin/stat",
        "--dereference",
        "--printf=%d:%i\\n",
        "--",
        "/proc/123/ns/mnt",
        "/proc/123/ns/cgroup",
        "/proc/123/root",
    ]
    for name in p.NAMES:
        assert p.root_argv(name) == [
            *prefix,
            "/usr/bin/grep",
            "-E",
            "-q",
            "--",
            "^" + name + " [(][^[:cntrl:]()]+[)]$",
            "/sys/kernel/security/apparmor/profiles",
        ]
    for kind, pid in [("other", None), ("stat", -1), ("stat", True), ("stat", "123/root"), ("bwrap", 1)]:
        with pytest.raises(p.Stop):
            p.root_argv(kind, pid)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"1:2\n",
        b"1:2\n3:4\n5:6\n7:8\n",
        b"+1:2\n3:4\n5:6\n",
        b"1:2\r\n3:4\n5:6\n",
        b"18446744073709551616:2\n3:4\n5:6\n",
        b"secret",
        b"1" * 257,
    ],
)
def test_numeric_parser_rejects_noncanonical_or_overflow(raw):
    with pytest.raises(p.Stop, match="format"):
        p.numbers(raw)


def test_numeric_parser_accepts_exact_pairs():
    assert p.numbers(b"1:2\n3:4\n5:6\n") == {"mnt": [1, 2], "cgroup": [3, 4], "root": [5, 6]}


def test_actual_ordinary_capture_discards_stderr():
    result = p.capture(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            "import sys; print('ok'); print('private-value',file=sys.stderr)",
        ],
        p.ENV,
        time.monotonic() + 3,
    )
    assert result["stdout"] == b"ok\n" and result["stderr_bytes"] > 0
    assert "private-value" not in str(result)


def test_actual_ordinary_output_overflow_drains_and_reaps(tmp_path):
    marker = tmp_path / "finished"
    code = f"import os; os.write(1,b'x'*20000); open({str(marker)!r},'w').write('done')"
    result = p.capture([sys.executable, "-I", "-S", "-c", code], p.ENV, time.monotonic() + 3, limit=8)
    assert result["reason"] == "output" and len(result["stdout"]) == 8
    assert marker.read_text() == "done" and result["direct_sudo_reaped"]


def test_actual_ordinary_timeout_reaps_only_test_child():
    result = p.capture(
        [sys.executable, "-I", "-S", "-c", "import time;time.sleep(5)"], p.ENV, time.monotonic() + 0.05
    )
    assert result["reason"] == "timeout" and result["direct_sudo_reaped"]


def test_real_pidfd_detects_dead_ordinary_process():
    proc = subprocess.Popen([sys.executable, "-I", "-S", "-c", "pass"])
    fd = os.pidfd_open(proc.pid)
    try:
        proc.wait(timeout=3)
        with pytest.raises(p.Stop, match="identity"):
            p.live({"fd": fd, "pid": proc.pid, "starttime": 1}, os.getuid())
    finally:
        os.close(fd)


def test_file_identity_rejects_links_writable_and_size():
    with tempfile.TemporaryDirectory(dir=PATH.parents[1]) as directory:
        root = Path(directory)
        file = root / "source"
        file.write_bytes(b"source")
        assert p.file_bytes(file, os.getuid(), 6)[0] == b"source"
        link = root / "link"
        link.symlink_to(file)
        for candidate, limit in [(link, 6), (file, 5)]:
            with pytest.raises(p.Stop):
                p.file_bytes(candidate, os.getuid(), limit)
        file.chmod(0o666)
        with pytest.raises(p.Stop):
            p.file_bytes(file, os.getuid(), 6)


def test_source_tree_binds_only_fixed_regular_files(monkeypatch):
    payload = b"fixture source"
    blob = hashlib.sha1(b"blob 14\0" + payload, usedforsecurity=False).hexdigest()
    calls = []
    monkeypatch.setattr(p, "file_bytes", lambda *args: (payload, (1, 2, stat.S_IFREG | 0o644)))

    def ordinary(argv, env, cwd, deadline=None, *, operation):
        calls.append(argv)
        assert env["GIT_CONFIG_NOSYSTEM"] == "1" and env["GIT_CONFIG_GLOBAL"] == "/dev/null"
        assert "core.fsmonitor=false" in argv
        if "rev-parse" in argv:
            return b"a" * 40 + b"\n"
        return b"".join(f"100644 blob {blob}\t{name}\0".encode() for name in (p.SOURCE, p.WORKFLOW))

    monkeypatch.setattr(p, "ordinary", ordinary)
    assert set(p.source_snapshot("a" * 40)) == {p.SOURCE, p.WORKFLOW}
    assert len(calls) == 2 and all("diff" not in call and "status" not in call for call in calls)


def test_private_directory_and_exclusive_artifact(tmp_path):
    tmp_path.chmod(0o700)
    fd = p.evidence_directory(tmp_path)
    try:
        p.save(fd, {"status": "STOP"})
        with pytest.raises(FileExistsError):
            p.save(fd, {"status": "OBSERVED"})
        assert (tmp_path / "metadata-observation.json").stat().st_mode & 0o777 == 0o600
    finally:
        os.close(fd)


def test_workflow_requires_explicit_dispatch_and_has_failure_artifact():
    workflow = yaml.safe_load((PATH.parents[1] / p.WORKFLOW).read_text())
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert inputs["fixed_metadata_read"]["default"] is False
    assert inputs["expected_source_sha"]["default"] == ""
    job = workflow["jobs"]["fixed-metadata"]
    assert (
        job["if"]
        == "${{ github.event_name == 'workflow_dispatch' && inputs.fixed_metadata_read && !inputs.ordinary_dependency_snapshot && !inputs.dependency_view_read }}"
    )
    assert "inputs.fixed_metadata_read" in workflow["jobs"]["observe"]["if"]
    assert workflow["permissions"] == {"contents": "read"}
    assert job["concurrency"]["cancel-in-progress"] is False
    assert job["steps"][-1]["if"] == "${{ always() && env.METADATA_EVIDENCE != '' }}"
    run = job["steps"][2]["run"]
    assert "/usr/bin/env -i" in run and "--allow-fixed-privileged-metadata" in run
    assert "sudo" not in run and "strict_user_service_probe" not in run


@pytest.mark.parametrize("fault", ["read", "select", "wait", "close", "timeout"])
def test_privileged_capture_faults_never_kill_or_claim_descendants(monkeypatch, fault):
    class Stream:
        def __init__(self, number):
            self.number, self.closed = number, False

        def fileno(self):
            return self.number

        def close(self):
            self.closed = True
            if fault == "close" and self.number == 10:
                raise PermissionError(13, "private-close-value")

    class Process:
        def __init__(self):
            self.stdout, self.stderr = Stream(10), Stream(11)
            self.returncode, self.killed = None, False

        def wait(self, timeout):
            if fault == "timeout":
                raise subprocess.TimeoutExpired("fixed", timeout)
            if fault == "wait":
                raise PermissionError(13, "private-wait-value")
            self.returncode = 0

        def kill(self):
            self.killed = True
            pytest.fail("ordinary observer must not claim it can kill root process")

    class Selector:
        def __init__(self):
            self.streams = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def register(self, stream, event):
            self.streams.append(stream)

        def unregister(self, stream):
            self.streams.remove(stream)

        def get_map(self):
            return self.streams

        def select(self, timeout):
            if fault == "select":
                raise PermissionError(13, "private-select-value")
            if fault == "timeout":
                return []
            return [(types.SimpleNamespace(fileobj=s), 1) for s in list(self.streams)]

    process = Process()

    def popen(argv, **kwargs):
        assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["cwd"] == "/"
        assert kwargs["env"] == p.ENV
        return process

    def read(fd, size):
        if fault == "read":
            raise PermissionError(13, "private-read-value")
        return b""

    monkeypatch.setattr(p.subprocess, "Popen", popen)
    monkeypatch.setattr(p.selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(p.os, "set_blocking", lambda *args: None)
    monkeypatch.setattr(p.os, "read", read)
    result = p.capture(p.root_argv("stat", 123), p.ENV, time.monotonic() + 0.002, privileged=True)
    assert result["reason"] == ("timeout" if fault == "timeout" else "io")
    assert result["direct_sudo_reaped"] == (fault == "close")
    assert process.stdout.closed and process.stderr.closed and not process.killed
    assert result["privileged_descendants"] == "NOT_INDEPENDENTLY_OBSERVED"
    assert "private" not in str(result)


def test_tools_allow_only_sudo_setuid_and_require_elf(monkeypatch):
    state = {"fault": None}

    def file_bytes(path, owner, limit):
        mode = stat.S_IFREG | 0o755 | (0o4000 if path.name == "sudo" else 0)
        if state["fault"] == "other_setuid" and path.name == "stat":
            mode |= 0o4000
        return (b"wrong" if state["fault"] == "magic" else b"\x7fELF"), (1, 2, mode)

    monkeypatch.setattr(p, "file_bytes", file_bytes)
    assert set(p.tools_snapshot()) == set(p.TOOLS)
    for fault in ("other_setuid", "magic"):
        state["fault"] = fault
        with pytest.raises(p.Stop, match="tool"):
            p.tools_snapshot()


def test_shared_postcheck_deadline_cannot_restart_budget(fixture_observer, monkeypatch):
    passed = []

    def recheck(identity, uid, deadline):
        passed.append(deadline)

    monkeypatch.setattr(p, "recheck_manager", recheck)
    assert p.observe()["status"] == "OBSERVED"
    assert len(passed) == 1 and passed[0] is not None


def test_main_invalid_output_directory_never_observes(monkeypatch, tmp_path):
    tmp_path.chmod(0o755)
    monkeypatch.setattr(
        sys,
        "argv",
        ["observer", "--output-dir", str(tmp_path), "--allow-fixed-privileged-metadata"],
    )
    monkeypatch.setattr(p, "observe", lambda: pytest.fail("unsafe output path must stop before reads"))
    assert p.main() == 2


@pytest.mark.parametrize(
    ("name", "line", "matched"),
    [
        ("bwrap", "bwrap (enforce)", True),
        ("unpriv_bwrap", "unpriv_bwrap (complain)", True),
        ("bwrap", "notbwrap (enforce)", False),
        ("bwrap", "/usr/bin/bwrap (enforce)", False),
        ("bwrap", "bwrap ()", False),
        ("bwrap", "bwrap (unconfined)", True),
        ("unpriv_bwrap", "unpriv_bwrap_extra (enforce)", False),
    ],
)
def test_known_name_pattern_on_ordinary_temporary_file(tmp_path, name, line, matched):
    profile_file = tmp_path / "profiles-fixture"
    profile_file.write_text(line + "\n")
    # No sudo and no kernel profile path: validates only fixed GNU grep syntax.
    pattern = p.root_argv(name)[-2]
    result = subprocess.run(
        ["/usr/bin/grep", "-E", "-q", "--", pattern, str(profile_file)],
        env=p.ENV,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=2,
        check=False,
    )
    assert result.returncode == (0 if matched else 1)
    assert result.stdout == result.stderr == b""


def test_actual_file_change_during_read_is_rejected(monkeypatch):
    with tempfile.TemporaryDirectory(dir=PATH.parents[1]) as directory:
        file = Path(directory) / "source"
        file.write_bytes(b"original")
        original_read = p.os.read
        changed = False

        def read(fd, count):
            nonlocal changed
            data = original_read(fd, count)
            if not changed:
                changed = True
                file.write_bytes(b"modified")
            return data

        monkeypatch.setattr(p.os, "read", read)
        with pytest.raises(p.Stop, match="changed"):
            p.file_bytes(file, os.getuid(), 20)


def test_tool_drift_after_reads_is_not_observed(fixture_observer, monkeypatch):
    capture = p.capture

    def altered(*args, **kwargs):
        result = capture(*args, **kwargs)
        fixture_observer["tools"]["stat"]["sha256"] = "f" * 64
        return result

    monkeypatch.setattr(p, "capture", altered)
    result = p.observe()
    assert result["status"] == "STOP" and result["reason"] == "changed"
    assert result["observations"] is None


def test_manager_loss_prevents_next_privileged_read(fixture_observer, monkeypatch):
    live = p.live
    calls = 0

    def changed(identity, uid):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise p.Stop("identity")
        return live(identity, uid)

    monkeypatch.setattr(p, "live", changed)
    result = p.observe()
    assert result["reason"] == "identity" and len(fixture_observer["calls"]) == 1


def test_invalid_attempt_writes_failure_artifact_before_sudo(fixture_observer, monkeypatch, tmp_path):
    tmp_path.chmod(0o700)
    monkeypatch.setenv("CONTEXT_RUN_ATTEMPT", "2")
    monkeypatch.setattr(
        sys, "argv", ["observer", "--output-dir", str(tmp_path), "--allow-fixed-privileged-metadata"]
    )
    assert p.main() == 2
    result = json.loads((tmp_path / "metadata-observation.json").read_text())
    assert result["status"] == "STOP" and result["context"] is None
    assert result["direct_command_cleanup"] == result["service"] == "NOT_RUN"
    assert not fixture_observer["calls"]


def test_grep_rc1_with_diagnostic_is_unknown_not_absent(fixture_observer):
    fixture_observer.update(fault={"exit_code": 1, "stderr_bytes": 12}, fault_index=1)
    result = p.observe()
    assert result["status"] == "STOP" and result["observations"] is None
    assert len(fixture_observer["calls"]) == 2


@pytest.mark.parametrize("fault", ["blob", "mode", "missing", "duplicate", "head"])
def test_source_tree_rejects_mismatched_or_ambiguous_blobs(monkeypatch, fault):
    payload = b"fixture"
    blob = hashlib.sha1(b"blob 7\0" + payload, usedforsecurity=False).hexdigest()
    monkeypatch.setattr(p, "file_bytes", lambda *args: (payload, (1, 2, stat.S_IFREG | 0o644)))
    records = [f"100644 blob {blob}\t{name}\0" for name in (p.SOURCE, p.WORKFLOW)]
    if fault == "blob":
        records[0] = records[0].replace(blob, "f" * 40)
    elif fault == "mode":
        records[0] = records[0].replace("100644", "120000")
    elif fault == "missing":
        records.pop()
    elif fault == "duplicate":
        records[1] = records[0]

    def ordinary(argv, *args, **kwargs):
        if "rev-parse" in argv:
            return (b"b" if fault == "head" else b"a") * 40 + b"\n"
        return "".join(records).encode()

    monkeypatch.setattr(p, "ordinary", ordinary)
    with pytest.raises(p.Stop, match="source"):
        p.source_snapshot("a" * 40)


def test_system_manager_command_failure_is_finite(monkeypatch):
    monkeypatch.setattr(
        p,
        "capture",
        lambda *args, **kwargs: capture_result(stdout=b"private-value", exit_code=1, stderr_bytes=9),
    )
    with pytest.raises(p.Stop, match="command") as raised:
        p.system_manager_pid(os.getuid())
    record = raised.value.command_failure
    assert record["operation"] == "systemctl_manager" and record["stderr_bytes"] == 9
    assert "private-value" not in json.dumps(record)


@pytest.mark.parametrize("operation", ["source_head", "source_tree"])
def test_actual_source_command_sites_preserve_failure(monkeypatch, operation):
    monkeypatch.setattr(p, "file_bytes", lambda *args: (b"fixture", (1, 2, stat.S_IFREG | 0o644)))

    def capture(argv, *args, **kwargs):
        label = "source_head" if "rev-parse" in argv else "source_tree"
        return (
            capture_result(stdout=b"a" * 40 + b"\n")
            if label != operation
            else capture_result(exit_code=-9)
        )

    monkeypatch.setattr(p, "capture", capture)
    with pytest.raises(p.Stop, match="command") as raised:
        p.source_snapshot("a" * 40)
    assert raised.value.command_failure["operation"] == operation
    assert raised.value.command_failure["exit_code"] is None
    assert raised.value.command_failure["signal"] == 9


def test_actual_timeout_version_site_preserves_failure(fixture_observer, monkeypatch):
    monkeypatch.setattr(p, "ordinary", REAL_ORDINARY)
    monkeypatch.setattr(
        p,
        "capture",
        lambda *args, **kwargs: capture_result(
            reason="io", errno=13, exit_code=None, direct_sudo_reaped=False
        ),
    )
    result = p.observe()
    assert result["ordinary_failure"]["operation"] == "timeout_version"
    assert result["ordinary_failure"]["failure_reason"] == "io"
    assert result["ordinary_failure"]["errno"] == 13
    assert result["commands"] == [] and result["status"] == "STOP"


@pytest.mark.parametrize("failure", [p.Stop("timeout"), PermissionError(13, "private-value")])
def test_ordinary_capture_exception_keeps_site_without_raw_text(monkeypatch, failure):
    def capture(*args, **kwargs):
        raise failure

    monkeypatch.setattr(p, "capture", capture)
    with pytest.raises((p.Stop, OSError)) as raised:
        p.ordinary(["/usr/bin/timeout", "--version"], operation="timeout_version")
    assert raised.value is failure
    assert raised.value.command_failure["operation"] == "timeout_version"
    assert "private-value" not in json.dumps(raised.value.command_failure)


def test_unknown_ordinary_label_never_invokes_command(monkeypatch):
    monkeypatch.setattr(
        p, "capture", lambda *args, **kwargs: pytest.fail("unknown label invoked command")
    )
    with pytest.raises(p.Stop, match="format"):
        p.ordinary(["/usr/bin/timeout", "--version"], operation="private-value")


def test_real_socketpair_credentials_and_pidfd():
    left, right = socket.socketpair()
    identity = {
        "pid": os.getpid(),
        "starttime": p.starttime(os.getpid()),
        "fd": os.pidfd_open(os.getpid()),
    }
    try:
        assert p.peer_credentials(left) == {"pid": os.getpid(), "uid": os.getuid(), "gid": os.getgid()}
        p.live(identity, os.getuid())
    finally:
        left.close()
        right.close()
        os.close(identity["fd"])


@pytest.mark.parametrize(
    "frame",
    [
        b"",
        b"x" * 11,
        b"x" * 13,
        struct.pack("3i", 0, 1, 1),
        struct.pack("3i", 1, -1, 1),
        struct.pack("3i", 1, 1, -1),
    ],
)
def test_peer_frame_rejects_malformed(frame):
    channel = types.SimpleNamespace(getsockopt=lambda *args: frame)
    with pytest.raises(p.Stop, match="private_credentials"):
        p.peer_credentials(channel)


@pytest.fixture
def private_fixture(monkeypatch):
    uid, pid = os.getuid(), os.getpid()
    state = {
        "closed": 0,
        "connects": [],
        "frame": struct.pack("3i", pid, uid, os.getgid()),
        "fault": None,
    }

    class Channel:
        def settimeout(self, value):
            assert 0 < value <= 2

        def connect(self, path):
            state["connects"].append(path)
            assert path == f"/run/user/{uid}/systemd/private"
            if state["fault"] == "connect":
                raise PermissionError(13, "private-value")

        def getsockopt(self, level, option, size):
            assert (level, option, size) == (socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            return state["frame"]

        def close(self):
            state["closed"] += 1
            if state["fault"] == "close":
                raise OSError(5, "private-value")

    monkeypatch.setattr(p.socket, "socket", lambda *args: Channel())
    monkeypatch.setattr(p, "endpoint_snapshot", lambda uid: (1,))
    identity = {"pid": pid, "starttime": p.starttime(pid), "fd": os.pidfd_open(pid)}
    state["identity"] = identity
    yield state
    os.close(identity["fd"])


def test_private_peer_fixed_endpoint_no_protocol(private_fixture):
    result = p.private_peer(private_fixture["identity"], os.getuid())
    assert result["credentials"]["pid"] == os.getpid()
    assert private_fixture["closed"] == 1 and len(private_fixture["connects"]) == 1


@pytest.mark.parametrize("fault", ["pid", "uid", "connect", "close", "drift", "timeout", "dead"])
def test_private_peer_stops_and_closes(private_fixture, monkeypatch, fault):
    state = private_fixture
    state["fault"] = fault
    if fault in ("pid", "uid"):
        state["frame"] = struct.pack(
            "3i", os.getpid() + (fault == "pid"), os.getuid() + (fault == "uid"), os.getgid()
        )
    if fault == "drift":
        values = iter([(1,), (2,)])
        monkeypatch.setattr(p, "endpoint_snapshot", lambda uid: next(values))
    if fault == "dead":
        monkeypatch.setattr(p, "live", lambda *args: (_ for _ in ()).throw(p.Stop("identity")))
    with pytest.raises(p.Stop) as raised:
        p.private_peer(
            state["identity"], os.getuid(), time.monotonic() - 1 if fault == "timeout" else None
        )
    assert state["closed"] == (0 if fault in ("timeout", "dead") else 1)
    assert "private-value" not in str(raised.value)
    expected_operation = {
        "pid": "private_credentials",
        "uid": "private_credentials",
        "connect": "private_connect",
        "close": "private_credentials",
        "drift": "private_path",
        "timeout": "private_connect",
        "dead": "identity_recheck",
    }[fault]
    assert raised.value.identity_failure["operation"] == expected_operation
    assert raised.value.identity_failure["timeout"] == (fault == "timeout")
    if fault == "connect":
        assert raised.value.reason == "private_connect" and raised.value.errno == 13
    if fault == "close":
        assert raised.value.cleanup_errors == ["private_socket"]


def test_socket_close_does_not_replace_primary(private_fixture):
    private_fixture["fault"] = "close"
    private_fixture["frame"] = struct.pack("3i", os.getpid() + 1, os.getuid(), os.getgid())
    with pytest.raises(p.Stop, match="private_credentials") as raised:
        p.private_peer(private_fixture["identity"], os.getuid())
    assert raised.value.cleanup_errors == ["private_socket"]


@pytest.mark.parametrize("fault", [None, "missing", "symlink", "writable", "owner", "socket_type"])
def test_endpoint_metadata_contract(monkeypatch, fault):
    uid = os.getuid()

    def lstat(path):
        name = str(path)
        socket_path = name.endswith("/private")
        user_path = name in (f"/run/user/{uid}", f"/run/user/{uid}/systemd")
        mode = stat.S_IFSOCK | 0o600 if socket_path else stat.S_IFDIR | (0o700 if user_path else 0o755)
        owner = uid if user_path or socket_path else 0
        if name == f"/run/user/{uid}/systemd":
            if fault == "missing":
                raise FileNotFoundError(2, "private-value")
            if fault == "symlink":
                mode = stat.S_IFLNK | 0o777
            if fault == "writable":
                mode |= 0o002
            if fault == "owner":
                owner += 1
        if socket_path and fault == "socket_type":
            mode = stat.S_IFREG | 0o600
        return types.SimpleNamespace(
            st_dev=1,
            st_ino=2,
            st_mode=mode,
            st_uid=owner,
            st_gid=0,
            st_size=0,
            st_mtime_ns=0,
            st_ctime_ns=0,
        )

    monkeypatch.setattr(Path, "lstat", lstat)
    if fault is None:
        assert len(p.endpoint_snapshot(uid)) == 6
    else:
        with pytest.raises(p.Stop, match="private_endpoint"):
            p.endpoint_snapshot(uid)


def test_manager_failure_closes_owned_pidfd(monkeypatch):
    monkeypatch.setattr(p, "system_manager_pid", lambda uid: os.getpid())
    failure = p.Stop("private_connect")
    monkeypatch.setattr(p, "private_peer", lambda *args: (_ for _ in ()).throw(failure))
    original = os.pidfd_open
    owned = []

    def pidfd(pid):
        fd = original(pid)
        owned.append(fd)
        return fd

    monkeypatch.setattr(p.os, "pidfd_open", pidfd)
    with pytest.raises(p.Stop) as raised:
        p.manager(os.getuid())
    assert raised.value is failure
    with pytest.raises(OSError):
        os.fstat(owned[0])


def test_recheck_passes_single_deadline(monkeypatch):
    identity = {"pid": 123, "private_peer": {"credentials": {}, "endpoint": ()}}
    calls = []
    monkeypatch.setattr(p, "system_manager_pid", lambda uid, deadline: (calls.append(deadline), 123)[1])
    monkeypatch.setattr(
        p,
        "private_peer",
        lambda identity, uid, deadline: (calls.append(deadline), identity["private_peer"])[1],
    )
    monkeypatch.setattr(p, "live", lambda *args: None)
    p.recheck_manager(identity, 1000, 42)
    assert calls == [42, 42]


def test_real_manager_identity_path_with_mock_existing_peer(monkeypatch):
    pid, uid = os.getpid(), os.getuid()
    monkeypatch.setattr(
        p,
        "ordinary",
        lambda *args, **kwargs: f"Id=user@{uid}.service\nActiveState=active\nMainPID={pid}\n".encode(),
    )
    expected = {"credentials": {"pid": pid, "uid": uid, "gid": os.getgid()}, "endpoint": ()}
    monkeypatch.setattr(p, "private_peer", lambda identity, uid: expected)
    identity = p.manager(uid)
    try:
        assert identity["pid"] == pid and identity["private_peer"] == expected
        p.live(identity, uid)
    finally:
        os.close(identity["fd"])


@pytest.mark.parametrize("primary", [None, "identity"])
def test_pidfd_close_failure_secondary(primary, monkeypatch):
    error = p.Stop(primary) if primary else None
    monkeypatch.setattr(p.os, "close", lambda fd: (_ for _ in ()).throw(OSError(5, "private-value")))
    result = p.close_owned(123, "manager_pidfd", error)
    assert result.reason == (primary or "io") and result.cleanup_errors == ["manager_pidfd"]
    if error is not None:
        assert result is error


@pytest.mark.parametrize("fault", ["pid", "peer"])
def test_recheck_rejects_identity_changes(monkeypatch, fault):
    identity = {"pid": 123, "private_peer": {"credentials": {}, "endpoint": ()}}
    monkeypatch.setattr(p, "system_manager_pid", lambda *args: 124 if fault == "pid" else 123)
    monkeypatch.setattr(p, "private_peer", lambda *args: {"credentials": {}, "endpoint": (1,)})
    monkeypatch.setattr(p, "live", lambda *args: None)
    with pytest.raises(p.Stop, match="changed"):
        p.recheck_manager(identity, 1000, time.monotonic() + 1)


@pytest.mark.parametrize("failure", [KeyboardInterrupt(), SystemExit(2)])
def test_manager_interruption_closes_pidfd(monkeypatch, failure):
    monkeypatch.setattr(p, "system_manager_pid", lambda uid: os.getpid())
    monkeypatch.setattr(p, "private_peer", lambda *args: (_ for _ in ()).throw(failure))
    original, owned = os.pidfd_open, []

    def pidfd(pid):
        fd = original(pid)
        owned.append(fd)
        return fd

    monkeypatch.setattr(p.os, "pidfd_open", pidfd)
    with pytest.raises(type(failure)) as raised:
        p.manager(os.getuid())
    assert raised.value is failure
    with pytest.raises(OSError):
        os.fstat(owned[0])


def test_peer_interruption_still_closes_socket(private_fixture, monkeypatch):
    failure = KeyboardInterrupt()
    monkeypatch.setattr(p, "peer_credentials", lambda *args: (_ for _ in ()).throw(failure))
    with pytest.raises(KeyboardInterrupt) as raised:
        p.private_peer(private_fixture["identity"], os.getuid())
    assert raised.value is failure and private_fixture["closed"] == 1


@pytest.mark.parametrize(
    "operation",
    ["private_path", "private_connect", "private_credentials", "identity_recheck", "manager_process"],
)
@pytest.mark.parametrize(
    "failure", [PermissionError(13, "private-value"), TimeoutError(110, "private-value")]
)
def test_identity_site_labels_preserve_primary(operation, failure):
    def action():
        raise failure

    with pytest.raises(type(failure)) as raised:
        p.identity_step(operation, action)
    assert raised.value is failure
    assert raised.value.identity_failure == {
        "operation": operation,
        "errno": failure.errno,
        "timeout": isinstance(failure, TimeoutError),
    }
    assert "private-value" not in json.dumps(raised.value.identity_failure)
    del failure.identity_failure


def test_identity_nested_label_not_overwritten():
    failure = PermissionError(13, "private-value")

    def inner():
        raise failure

    with pytest.raises(PermissionError):
        p.identity_step("identity_recheck", p.identity_step, "private_path", inner)
    assert failure.identity_failure["operation"] == "private_path"


def test_missing_endpoint_failure_retains_site(private_fixture, monkeypatch):
    failure = FileNotFoundError(2, "private-value")
    monkeypatch.setattr(p, "endpoint_snapshot", lambda uid: (_ for _ in ()).throw(failure))
    with pytest.raises(FileNotFoundError) as raised:
        p.private_peer(private_fixture["identity"], os.getuid())
    assert raised.value.identity_failure == {"operation": "private_path", "errno": 2, "timeout": False}
    assert not private_fixture["connects"] and not private_fixture["closed"]
