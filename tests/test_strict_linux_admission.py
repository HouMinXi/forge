"""Closed read-only observation receipts cannot claim native qualification."""

import copy
import errno
import io
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace
from pathlib import Path

import pytest
import yaml

from scripts import strict_linux_admission as admission


@pytest.fixture
def observed():
    root = admission.delegated_root(1001)
    identity = {
        "repository": "HouMinXi/forge",
        "run_id": "123",
        "run_attempt": 1,
        "job_name": "observe",
        "workflow_ref": "HouMinXi/forge/.github/workflows/strict-linux-admission.yml@refs/heads/main",
        "workflow_sha": "a" * 40,
        "expected_source_sha": "b" * 40,
        "collector_sha256": "d" * 64,
        "authentication": "UNVERIFIED_DECLARED_CONTEXT",
    }
    facts = {
        "uid": 1001,
        "gid": 1001,
        "kernel": "6.11.0-test",
        "boot_id": "a" * 8 + "-" + "b" * 4 + "-" + "c" * 4 + "-" + "d" * 4 + "-" + "e" * 12,
        "cgroup2_mount": True,
        "source_cgroup": root + "/app.slice/worker.service",
        "delegated_root": root,
        "controllers": ["memory", "pids"],
        "subtree_control": ["memory", "pids"],
        "manager": {"active_state": "active", "control_group": root, "delegate": True},
        "common_ancestor": root,
        "access": {
            "delegated_directory": True,
            "destination_procs": True,
            "common_ancestor_procs": True,
        },
        "bwrap": {
            "path": "/usr/bin/bwrap",
            "realpath": "/usr/bin/bwrap",
            "uid": 0,
            "gid": 0,
            "mode": 0o100755,
            "size": 1234,
            "sha256": "e" * 64,
            "version": None,
        },
        "apparmor": {
            "enabled": True,
            "restrict_userns": 1,
            "process_profile": "confined",
            "bwrap_profiles": ["bwrap"],
        },
    }
    return {
        "schema": admission.SCHEMA,
        "identity": identity,
        "facts": facts,
        "diagnostics": {
            "bwrap": {
                "usr_bin": {"reason": "read_success", "errno": None},
                "bin": {"reason": "not_attempted", "errno": None},
            },
            "apparmor_profiles": {"reason": "read_success", "errno": None},
        },
        "status": "OBSERVED",
        "problems": [],
        "boundary": "NOT_RUN",
        "qualification": "NOT_RUN",
    }


def encode(receipt):
    return (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()


def recalculate(receipt):
    # Existing fact-mutation fixtures keep the newly required diagnostics
    # consistent. Dedicated diagnostic negative tests do not use this helper.
    if receipt["facts"]["bwrap"] is None:
        receipt["diagnostics"]["bwrap"] = {
            label: {"reason": "missing", "errno": errno.ENOENT} for label in admission.BWRAP_LABELS
        }
    if receipt["facts"]["apparmor"]["bwrap_profiles"] is None:
        receipt["diagnostics"]["apparmor_profiles"] = {"reason": "unreadable", "errno": errno.EACCES}
    receipt["problems"] = admission.derive_problems(receipt["identity"], receipt["facts"])
    receipt["status"] = "STOP" if receipt["problems"] else "OBSERVED"
    return receipt


def test_valid_observation_never_claims_admission(observed):
    raw = admission.canonical(observed)
    assert admission.parse(raw) == observed
    assert observed["boundary"] == observed["qualification"] == "NOT_RUN"
    assert observed["identity"]["authentication"] == "UNVERIFIED_DECLARED_CONTEXT"


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("identity",),
        ("facts",),
        ("facts", "manager"),
        ("facts", "access"),
        ("facts", "bwrap"),
        ("facts", "apparmor"),
    ],
)
def test_reject_unknown_keys_at_every_level(observed, path):
    target = observed
    for key in path:
        target = target[key]
    target["unrecognized"] = "secret"
    with pytest.raises(admission.ObservationError):
        admission.parse(encode(observed))


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "PASS"),
        ("status", "QUALIFIED"),
        ("status", "READY_FOR_LIVE_PROBE"),
        ("boundary", "PASS"),
        ("qualification", "PASS"),
    ],
)
def test_reject_qualification_and_unrecognized_status(observed, field, value):
    observed[field] = value
    with pytest.raises(admission.ObservationError):
        admission.parse(encode(observed))


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_attempt", True),
        ("run_attempt", 0),
        ("run_attempt", 1.0),
        ("run_id", "0"),
        ("run_id", "1" * 21),
        ("job_name", "observe\nsecret"),
        ("authentication", "AUTHENTICATED"),
        ("expected_source_sha", "x" * 40),
        ("workflow_ref", "other/repository/.github/workflows/evil.yml@main"),
    ],
)
def test_reject_invalid_identity(observed, field, value):
    observed["identity"][field] = value
    with pytest.raises(admission.ObservationError):
        admission.parse(encode(observed))


@pytest.mark.parametrize(
    "field,value",
    [
        ("uid", True),
        ("uid", -1),
        ("gid", 2**32),
        ("kernel", "x" * 129),
        ("source_cgroup", "/a/../b"),
        ("source_cgroup", "/a//b"),
        ("source_cgroup", "/a/"),
        ("source_cgroup", "relative"),
        ("controllers", ["memory", "memory"]),
        ("controllers", ["pids", "memory"]),
        ("controllers", ["memory", "pids", "injected\ntext"]),
        ("cgroup2_mount", 1),
    ],
)
def test_reject_invalid_facts(observed, field, value):
    observed["facts"][field] = value
    with pytest.raises(admission.ObservationError):
        admission.parse(encode(observed))


@pytest.mark.parametrize(
    "field",
    [
        "uid",
        "gid",
        "cgroup2_mount",
        "source_cgroup",
        "controllers",
        "subtree_control",
        "bwrap",
        "kernel",
        "boot_id",
    ],
)
def test_missing_or_failed_prerequisite_stops(observed, field):
    observed["facts"][field] = 0 if field in {"uid", "gid"} else None
    if field == "uid":
        observed["facts"]["delegated_root"] = admission.delegated_root(0)
        observed["facts"]["common_ancestor"] = admission.common_ancestor(
            observed["facts"]["source_cgroup"], admission.delegated_root(0)
        )
    if field == "source_cgroup":
        observed["facts"]["common_ancestor"] = None
    recalculate(observed)
    assert admission.parse(encode(observed))["status"] == "STOP"


def test_destination_writable_does_not_hide_common_ancestor_failure(observed):
    observed["facts"]["access"]["common_ancestor_procs"] = False
    with pytest.raises(admission.ObservationError):
        admission.parse(encode(observed))  # stale claimed OBSERVED
    recalculate(observed)
    assert observed["problems"] == ["current_common_ancestor_access"]
    assert admission.parse(encode(observed))["status"] == "STOP"


def test_unknown_apparmor_is_not_bypassed(observed):
    observed["facts"]["apparmor"] = dict.fromkeys(admission.APPARMOR_KEYS)
    recalculate(observed)
    assert "apparmor_observed" in observed["problems"]
    assert admission.parse(encode(observed))["status"] == "STOP"


def test_cannot_invent_authenticated_source_binding(observed):
    observed["identity"]["observed_source_sha"] = "b" * 40
    with pytest.raises(admission.ObservationError):
        admission.parse(encode(observed))


@pytest.mark.parametrize(
    "raw",
    [
        b"{}",
        b"[]",
        b"null",
        b'{"schema":"one","schema":"two"}',
        b'{"a":NaN}',
        b"\xff",
        b"[" * 2000,
        b"x" * (admission.MAX_RECEIPT + 1),
    ],
)
def test_reject_malformed_and_oversized_receipts(raw):
    with pytest.raises(admission.ObservationError):
        admission.parse(raw)


def test_reject_noncanonical_json_and_duplicate_key(observed):
    with pytest.raises(admission.ObservationError):
        admission.parse(json.dumps(observed, indent=2).encode())
    raw = encode(observed).replace(b'"schema":', b'"schema":"duplicate","schema":')
    with pytest.raises(admission.ObservationError):
        admission.parse(raw)


@pytest.mark.parametrize("raw", [None, "", "0::relative", "0::/a/../b", "0::/a\n0::/b", "1:memory:/a"])
def test_unknown_membership(raw):
    assert admission.source_membership(raw) is None


def test_membership_and_common_ancestor_use_actual_source():
    assert (
        admission.source_membership("0::/system.slice/runner.service") == "/system.slice/runner.service"
    )
    assert (
        admission.common_ancestor("/system.slice/runner.service", admission.delegated_root(1001)) == "/"
    )


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "ActiveState=active\nControlGroup=/user.slice\nDelegate=maybe",
        "ActiveState=active\nActiveState=active\nDelegate=yes",
        "ActiveState=active\nControlGroup=/../escape\nDelegate=yes",
        "ActiveState=active\nControlGroup=/user.slice\nDelegate=yes\nSecret=value",
    ],
)
def test_manager_query_is_closed(raw):
    assert admission.manager_properties(raw) == dict.fromkeys(sorted(admission.MANAGER_KEYS))


def test_manager_properties_and_mount_parser():
    assert admission.manager_properties(
        "ActiveState=active\nControlGroup=/user.slice\nDelegate=yes"
    ) == {"active_state": "active", "control_group": "/user.slice", "delegate": True}
    assert admission.mounted_cgroup2("36 29 0:32 / /sys/fs/cgroup rw - cgroup2 cgroup rw") is True
    assert admission.mounted_cgroup2("36 29 0:32 /nested /sys/fs/cgroup rw - cgroup2 cgroup rw") is False
    assert admission.mounted_cgroup2("36 29 0:32 / /sys/fs/cgroup rw - tmpfs cgroup rw") is False
    assert admission.mounted_cgroup2(None) is None


def test_file_read_has_a_byte_limit(tmp_path):
    path = tmp_path / "data"
    path.write_bytes(b"x" * 17)
    assert admission.read_text(str(path), 16) is None
    assert admission.read_text(str(path), 17) == "x" * 17
    path.write_bytes(b"\xff")
    assert admission.read_text(str(path)) is None


def test_collect_does_not_launch_a_payload_or_dump_environment(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("static collector launched a process")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(admission, "_verified_systemctl", lambda: False)
    monkeypatch.setattr(admission, "read_text", lambda *args: None)
    monkeypatch.setattr(
        admission,
        "bwrap_identity",
        lambda: (
            None,
            {label: {"reason": "missing", "errno": errno.ENOENT} for label in admission.BWRAP_LABELS},
        ),
    )
    monkeypatch.setattr(
        admission,
        "apparmor_profiles_observation",
        lambda: (None, {"reason": "unreadable", "errno": errno.EACCES}),
    )
    monkeypatch.setattr(admission, "writable", lambda *args: None)
    monkeypatch.setenv("TOKEN_DO_NOT_CAPTURE", "super-secret")
    identity = {
        "repository": "HouMinXi/forge",
        "run_id": "123",
        "run_attempt": 1,
        "job_name": "observe",
        "workflow_ref": "HouMinXi/forge/.github/workflows/strict-linux-admission.yml@main",
        "workflow_sha": "a" * 40,
        "expected_source_sha": "a" * 40,
    }
    raw = admission.canonical(admission.collect(identity))
    assert b"super-secret" not in raw
    assert admission.parse(raw)["status"] == "STOP"
    receipt = admission.parse(raw)
    assert "observed_source_sha" not in receipt["identity"]
    assert "source_tree_sha" not in receipt["identity"]
    assert all(value is None for value in receipt["facts"]["manager"].values())
    assert {"existing_manager", "bwrap_identity"} <= set(receipt["problems"])


def test_output_is_exclusive_and_stop_is_preserved(observed, monkeypatch, tmp_path):
    receipt = copy.deepcopy(observed)
    receipt["facts"]["bwrap"] = None
    recalculate(receipt)
    monkeypatch.setattr(admission, "collect", lambda identity: receipt)
    output = tmp_path / "receipt.json"
    args = [
        "--repository",
        "HouMinXi/forge",
        "--run-id",
        "123",
        "--run-attempt",
        "1",
        "--job-name",
        "observe",
        "--workflow-ref",
        "unused",
        "--workflow-sha",
        "a" * 40,
        "--expected-source-sha",
        "b" * 40,
        "--output",
        str(output),
    ]
    assert admission.main(args) == 2
    assert admission.parse(output.read_bytes())["status"] == "STOP"
    assert admission.main(args) == 2
    assert admission.parse(output.read_bytes())["status"] == "STOP"


def test_workflow_limits_pr_scope_and_binds_actual_head():
    path = Path(__file__).parents[1] / ".github/workflows/strict-linux-admission.yml"
    text = path.read_text()
    workflow = yaml.safe_load(text)
    assert workflow["on"] == {
        "pull_request": {
            "branches": ["main"],
            "paths": [
                ".github/workflows/strict-linux-admission.yml",
                "scripts/strict_linux_admission.py",
                "scripts/static_public_summary.py",
                "scripts/fixed_metadata_public_summary.py",
                "tests/test_strict_linux_admission.py",
                "tests/test_static_publication.py",
                "tests/test_fixed_metadata_publication.py",
            ],
        },
        "workflow_dispatch": {
            "inputs": {
                "fixed_metadata_read": {
                    "description": "Explicit one-run fixed privileged metadata diagnostic (not qualification)",
                    "type": "boolean",
                    "default": False,
                },
                "ordinary_dependency_snapshot": {
                    "description": "Explicit ordinary-user dependency observation (not qualification)",
                    "type": "boolean",
                    "default": False,
                },
                "dependency_view_read": {
                    "description": "Explicit one-run two-stat and ordinary fragment diagnostic (not qualification)",
                    "type": "boolean",
                    "default": False,
                },
                "expected_source_sha": {
                    "description": "Reviewed exact source SHA; required for either explicit diagnostic",
                    "type": "string",
                    "default": "",
                },
            }
        },
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {
        "observe",
        "fixed-metadata",
        "ordinary-dependency",
        "dependency-view",
        "conflicting-modes",
    }
    assert workflow["jobs"]["fixed-metadata"]["if"] == (
        "${{ github.event_name == 'workflow_dispatch' && inputs.fixed_metadata_read && !inputs.ordinary_dependency_snapshot && !inputs.dependency_view_read }}"
    )
    job = workflow["jobs"]["observe"]
    assert job["if"] == (
        "${{ (github.event_name == 'workflow_dispatch' || "
        "github.event.pull_request.head.repo.full_name == github.repository) && "
        "!(github.event_name == 'workflow_dispatch' && (inputs.fixed_metadata_read || inputs.ordinary_dependency_snapshot || inputs.dependency_view_read)) }}"
    )
    steps = job["steps"]
    assert len(steps) == 3
    head_expression = "${{ github.event.pull_request.head.sha || github.sha }}"
    assert steps[0]["with"]["ref"] == head_expression
    assert steps[0]["with"]["persist-credentials"] is False
    assert steps[1]["env"]["CONTEXT_SOURCE_SHA"] == head_expression
    assert steps[1]["env"]["CONTEXT_WORKFLOW_SHA"] == "${{ github.workflow_sha }}"
    assert steps[1]["env"]["CONTEXT_WORKFLOW_REF"] == "${{ github.workflow_ref }}"
    assert steps[2]["if"] == "${{ always() && env.ADMISSION_PUBLIC_READY == 'true' }}"
    assert steps[2]["with"]["path"] == "forge-static-public/summary.json"
    assert "sudo " not in text and "sysctl " not in text
    assert "pip install" not in text and "systemd-run" not in text
    assert "strict_linux_admission.py" in steps[1]["run"]


def test_ambiguous_mount_topology_is_unknown():
    base = "36 29 0:32 / /sys/fs/cgroup rw - cgroup2 cgroup rw"
    overlay = "37 29 0:33 / /sys/fs/cgroup rw - tmpfs tmpfs rw"
    assert admission.mounted_cgroup2(base + "\n" + overlay) is None
    assert admission.mounted_cgroup2(overlay + "\n" + base) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("uid", 1001),
        ("mode", 0o100777),
        ("mode", 0o104755),
        ("mode", 0o102755),
        ("mode", 0o100644),
        ("size", 0),
    ],
)
def test_untrusted_tool_identity_cannot_produce_observed(observed, field, value):
    observed["facts"]["bwrap"][field] = value
    with pytest.raises(admission.ObservationError):
        admission.parse(encode(observed))
    recalculate(observed)
    assert admission.parse(encode(observed))["status"] == "STOP"


def test_persistence_error_is_a_fixed_message_without_path(monkeypatch, capsys):
    def unavailable(argv):
        raise OSError("sensitive/path/and/content")

    monkeypatch.setattr(admission, "_main", unavailable)
    assert admission.main([]) == 2
    captured = capsys.readouterr()
    assert captured.err == "STOP: observation_unavailable; qualification NOT_RUN\n"
    assert not captured.out


@pytest.mark.parametrize(
    "bad_path,mode,uid",
    [
        ("/", 0o40777, 0),
        ("/usr", 0o40755, 1001),
        ("/usr/bin", 0o120777, 0),
        (admission.SYSTEMCTL, 0o120777, 0),
        (admission.SYSTEMCTL, 0o104755, 0),
        (admission.SYSTEMCTL, 0o102755, 0),
        (admission.SYSTEMCTL, 0o100775, 0),
        (admission.SYSTEMCTL, 0o100755, 1001),
        (admission.SYSTEMCTL, 0o100644, 0),
    ],
)
def test_metadata_tool_and_every_ancestor_must_be_verified(monkeypatch, bad_path, mode, uid):
    def info(path):
        if path == bad_path:
            return SimpleNamespace(st_mode=mode, st_uid=uid)
        return SimpleNamespace(st_mode=0o100755 if path == admission.SYSTEMCTL else 0o40755, st_uid=0)

    monkeypatch.setattr(admission.os, "lstat", info)
    assert admission._verified_systemctl() is False


def test_manager_command_is_fixed_and_missing_trust_never_executes(monkeypatch):
    calls = []
    monkeypatch.setattr(admission, "_verified_systemctl", lambda: True)
    monkeypatch.setattr(admission, "_bounded_query", calls.append)
    admission.query_manager(1001)
    assert calls == [
        (
            "/usr/bin/systemctl",
            "--system",
            "--no-pager",
            "show",
            "user@1001.service",
            "--property=ActiveState,ControlGroup,Delegate",
        )
    ]
    calls.clear()
    monkeypatch.setattr(admission, "_verified_systemctl", lambda: False)
    assert admission.query_manager(1001) == dict.fromkeys(sorted(admission.MANAGER_KEYS))
    assert calls == []


def test_query_environment_is_closed_and_stderr_is_refused(monkeypatch):
    calls = []
    real_popen = subprocess.Popen

    def record(*args, **kwargs):
        calls.append(kwargs)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(admission.subprocess, "Popen", record)
    monkeypatch.setenv("LD_PRELOAD", "secret-do-not-inherit")
    monkeypatch.setenv("DBUS_SYSTEM_BUS_ADDRESS", "secret-do-not-inherit")
    assert admission._bounded_query((sys.executable, "-c", "print('bounded')")) == "bounded"
    assert calls[-1]["env"] == {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
    assert calls[-1]["start_new_session"] is True
    assert admission._bounded_query((sys.executable, "-c", "import os; os.write(2,b'secret')")) is None


def _live_process(pid):
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return False
    except ProcessLookupError as exc:
        if exc.errno == errno.ESRCH:
            return False
        raise
    return raw.rsplit(")", 1)[1].split()[0] != "Z"


def _live_group_process(pid, group):
    if not _live_process(pid):
        return False
    try:
        return os.getpgid(pid) == group
    except ProcessLookupError as exc:
        if exc.errno != errno.ESRCH:
            raise
        return False


def _kill_live_group_process(pid, group):
    if not _live_group_process(pid, group):
        return
    try:
        os.kill(pid, 9)
    except ProcessLookupError as exc:
        if exc.errno != errno.ESRCH:
            raise


@pytest.mark.parametrize("operation", ["getpgid", "kill"])
@pytest.mark.parametrize(
    "error",
    [
        ProcessLookupError(errno.ESRCH, "gone"),
        ProcessLookupError(errno.EIO, "io"),
        PermissionError(errno.EACCES, "denied"),
    ],
)
def test_group_process_handles_only_disappearance(monkeypatch, error, operation):
    monkeypatch.setattr(sys.modules[__name__], "_live_process", lambda pid: True)

    def disappeared(*args):
        raise error

    monkeypatch.setattr(os, "getpgid", lambda pid: 123)
    monkeypatch.setattr(os, operation, disappeared)
    check = _live_group_process if operation == "getpgid" else _kill_live_group_process
    if error.errno == errno.ESRCH:
        assert not check(123, 123)
    else:
        with pytest.raises(type(error)) as caught:
            check(123, 123)
        assert caught.value is error


@pytest.mark.parametrize("startup_delay", [0, 0.45], ids=["ready", "delayed-start"])
@pytest.mark.parametrize("scenario", ["timeout", "overflow", "leader_exit"])
def test_query_cleanup_stops_actual_group_descendant(monkeypatch, tmp_path, scenario, startup_delay):
    children = []
    real_popen = subprocess.Popen
    pidfile = tmp_path / "descendant.pid"

    def record(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        # Popen only confirms exec, not the fixture's fork/pidfile readiness.
        # Keep fixture startup outside the unchanged 0.3s query/cleanup budget.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if pidfile.exists():
                raw_pid = pidfile.read_text()
                if raw_pid.isdecimal():
                    pid = int(raw_pid)
                    if _live_group_process(pid, child.pid):
                        return child
            time.sleep(0.01)
        pytest.fail("descendant fixture did not become ready within 2s")

    monkeypatch.setattr(admission.subprocess, "Popen", record)
    monkeypatch.setattr(admission, "COMMAND_SECONDS", 0.3)
    monkeypatch.setattr(admission, "COMMAND_BYTES", 32)
    program = (
        "import os,time\n"
        f"time.sleep({startup_delay!r})\n"
        "child=os.fork()\n"
        "if child == 0:\n"
        f"    open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
        "    time.sleep(60)\n"
        "else:\n"
        f"    while not os.path.exists({str(pidfile)!r}) or os.path.getsize({str(pidfile)!r}) == 0: time.sleep(.001)\n"
        + (
            "    os.write(1,b'x'*33)\n    time.sleep(60)\n"
            if scenario == "overflow"
            else "    os._exit(0)\n"
            if scenario == "leader_exit"
            else "    time.sleep(60)\n"
        )
    )
    started = time.monotonic()
    try:
        assert admission._bounded_query((sys.executable, "-c", program)) is None
        assert time.monotonic() - started < 3
        pid = int(pidfile.read_text())
        deadline = time.monotonic() + 1
        while _live_process(pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not _live_process(pid)
    finally:
        # Test-owned cleanup also makes deliberately broken group-cleanup
        # controls safe: neither their leader nor their descendant is left live.
        for child in children:
            try:
                os.killpg(child.pid, 9)
            except ProcessLookupError:
                pass
            child.wait(timeout=3)
            for pipe in (child.stdout, child.stderr):
                if pipe is not None:
                    pipe.close()
        if pidfile.exists():
            raw_pid = pidfile.read_text()
            if raw_pid.isdecimal():
                pid = int(raw_pid)
                for child in children:
                    _kill_live_group_process(pid, child.pid)


@pytest.mark.parametrize(
    "failure",
    [
        OSError("secret path"),
        admission.ObservationError("secret data"),
        subprocess.TimeoutExpired("secret argv", 3),
    ],
)
def test_all_expected_cli_failures_use_fixed_label(monkeypatch, capsys, failure):
    def fail(argv):
        raise failure

    monkeypatch.setattr(admission, "_main", fail)
    assert admission.main([]) == 2
    assert capsys.readouterr().err == "STOP: observation_unavailable; qualification NOT_RUN\n"


def test_argparse_does_not_echo_rejected_value(capsys):
    assert admission.main(["--run-attempt", "secret-data-do-not-print"]) == 2
    captured = capsys.readouterr()
    assert "secret-data" not in captured.err
    assert captured.err == "STOP: observation_unavailable; qualification NOT_RUN\n"


def test_double_slash_is_not_a_cgroup_path():
    with pytest.raises(admission.ObservationError):
        admission._path("//user.slice")


def test_query_refuses_oversized_output_even_when_process_exits(monkeypatch):
    monkeypatch.setattr(admission, "COMMAND_BYTES", 32)
    assert admission._bounded_query((sys.executable, "-c", "import os; os.write(1,b'x'*33)")) is None


def test_unexecuted_version_cannot_be_invented(observed):
    observed["facts"]["bwrap"]["version"] = "bubblewrap 0.9.0"
    with pytest.raises(admission.ObservationError):
        admission.parse(encode(observed))


def _fake_bwrap_files(monkeypatch, attempts, resolved=None):
    """Only the admission module sees these two finite candidate files."""
    opened = []
    reads = []
    resolved = resolved or {}

    class Handle(io.BytesIO):
        def __init__(self, index, data):
            super().__init__(data)
            self.index = index

        def fileno(self):
            return self.index

        def read(self, limit):
            reads.append(limit)
            return super().read(limit)

    def fake_open(path, mode):
        assert mode == "rb"
        opened.append(path)
        index = admission.BWRAP_PATHS.index(path)
        value = attempts[index]
        if isinstance(value, OSError):
            raise value
        return Handle(index, value[0])

    def fstat(index):
        data, overrides = attempts[index]
        fields = {"st_mode": 0o100755, "st_uid": 0, "st_gid": 0, "st_size": len(data)}
        fields.update(overrides)
        return SimpleNamespace(**fields)

    monkeypatch.setattr(admission, "open", fake_open, raising=False)
    monkeypatch.setattr(
        admission,
        "os",
        SimpleNamespace(
            path=SimpleNamespace(
                realpath=lambda path: resolved.get(path, path), commonpath=os.path.commonpath
            ),
            fstat=fstat,
        ),
    )
    return opened, reads


@pytest.mark.parametrize(
    "code,reason",
    [
        (errno.ENOENT, "missing"),
        (errno.ENOTDIR, "missing"),
        (errno.EACCES, "unreadable"),
        (errno.EPERM, "unreadable"),
        (errno.EIO, "io_error"),
    ],
)
def test_bwrap_failure_reason_keeps_only_bounded_errno(monkeypatch, code, reason):
    error = OSError(code, "secret exception content")
    _fake_bwrap_files(monkeypatch, [error, error])
    fact, diagnostics = admission.bwrap_identity()
    assert fact is None
    assert diagnostics == {label: {"reason": reason, "errno": code} for label in admission.BWRAP_LABELS}
    assert "secret" not in json.dumps(diagnostics)


@pytest.mark.parametrize("code", [None, True, False, -1, 0, 4096, 10**100])
def test_errno_is_finite_and_not_a_boolean(code):
    assert admission._os_diagnostic(OSError(code, "secret")) == {"reason": "io_error", "errno": None}


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"st_uid": 1001}, "unsafe_metadata"),
        ({"st_mode": 0o100777}, "unsafe_metadata"),
        ({"st_mode": 0o100644}, "unsafe_metadata"),
        ({"st_size": 0}, "unsafe_metadata"),
        ({"st_size": 16 * 1024 * 1024 + 1}, "oversize"),
        ({"st_uid": 1001, "st_size": 16 * 1024 * 1024 + 1}, "oversize"),
    ],
)
def test_bwrap_metadata_failures_do_not_read_or_relax_predicates(monkeypatch, overrides, reason):
    _, reads = _fake_bwrap_files(monkeypatch, [(b"abc", overrides)] * 2)
    fact, diagnostics = admission.bwrap_identity()
    assert fact is None and reads == []
    assert diagnostics == {label: {"reason": reason, "errno": None} for label in admission.BWRAP_LABELS}


def test_bwrap_size_mismatch_is_not_a_permission_or_package_claim(monkeypatch):
    _, reads = _fake_bwrap_files(monkeypatch, [(b"abc", {"st_size": 4})] * 2)
    fact, diagnostics = admission.bwrap_identity()
    assert fact is None
    assert all(value == {"reason": "size_mismatch", "errno": None} for value in diagnostics.values())
    assert reads == [16 * 1024 * 1024 + 1] * 2


def test_bwrap_growth_past_read_cap_is_bounded(monkeypatch):
    content = b"x" * (16 * 1024 * 1024 + 1)
    _, reads = _fake_bwrap_files(monkeypatch, [(content, {"st_size": 1})] * 2)
    fact, diagnostics = admission.bwrap_identity()
    assert fact is None
    assert reads == [16 * 1024 * 1024 + 1] * 2
    assert all(value == {"reason": "oversize", "errno": None} for value in diagnostics.values())


def test_first_bwrap_success_preserves_short_circuit(monkeypatch, observed):
    opened, reads = _fake_bwrap_files(monkeypatch, [(b"abc", {}), OSError(errno.EIO, "must not read")])
    fact, diagnostics = admission.bwrap_identity()
    assert opened == ["/usr/bin/bwrap"]
    assert reads == [16 * 1024 * 1024 + 1]
    assert diagnostics == {
        "usr_bin": {"reason": "read_success", "errno": None},
        "bin": {"reason": "not_attempted", "errno": None},
    }
    observed["facts"]["bwrap"] = fact
    observed["diagnostics"]["bwrap"] = diagnostics
    assert admission.parse(encode(observed))["status"] == "OBSERVED"


def test_second_bwrap_success_retains_first_failure(monkeypatch, observed):
    _fake_bwrap_files(monkeypatch, [PermissionError(errno.EACCES, "secret"), (b"abc", {})])
    fact, diagnostics = admission.bwrap_identity()
    assert fact["path"] == "/bin/bwrap"
    assert diagnostics == {
        "usr_bin": {"reason": "unreadable", "errno": errno.EACCES},
        "bin": {"reason": "read_success", "errno": None},
    }
    observed["facts"]["bwrap"] = fact
    observed["diagnostics"]["bwrap"] = diagnostics
    assert admission.parse(encode(observed))["status"] == "OBSERVED"


def test_outside_allowlist_has_no_raw_resolved_path(monkeypatch):
    opened, _ = _fake_bwrap_files(
        monkeypatch,
        [OSError(), FileNotFoundError(errno.ENOENT, "secret")],
        {"/usr/bin/bwrap": "/private/secret/tool"},
    )
    fact, diagnostics = admission.bwrap_identity()
    assert fact is None
    assert opened == ["/bin/bwrap"]
    assert diagnostics["usr_bin"] == {"reason": "path_outside_allowlist", "errno": None}
    assert "private" not in json.dumps(diagnostics) and "secret" not in json.dumps(diagnostics)


@pytest.mark.parametrize(
    "value,reason,code",
    [
        (FileNotFoundError(errno.ENOENT, "secret"), "missing", errno.ENOENT),
        (PermissionError(errno.EACCES, "secret"), "unreadable", errno.EACCES),
        (OSError(errno.EIO, "secret"), "io_error", errno.EIO),
        (b"x" * (65536 + 1), "oversize", None),
        (b"\xff", "invalid_utf8", None),
    ],
)
def test_profiles_failure_reasons_do_not_capture_contents(monkeypatch, value, reason, code):
    reads = []

    class Handle(io.BytesIO):
        def read(self, limit):
            reads.append(limit)
            return super().read(limit)

    def fake_open(path, mode):
        assert path == "/sys/kernel/security/apparmor/profiles" and mode == "rb"
        if isinstance(value, OSError):
            raise value
        return Handle(value)

    monkeypatch.setattr(admission, "open", fake_open, raising=False)
    data, diagnostic = admission.apparmor_profiles_observation()
    assert data is None
    assert diagnostic == {"reason": reason, "errno": code}
    assert reads == ([] if isinstance(value, OSError) else [65536 + 1])
    assert "secret" not in json.dumps(diagnostic)


def test_empty_profiles_is_a_successful_read_not_missing_policy(monkeypatch, observed):
    monkeypatch.setattr(admission, "open", lambda *args: io.BytesIO(b""), raising=False)
    data, diagnostic = admission.apparmor_profiles_observation()
    assert data == "" and diagnostic == {"reason": "read_success", "errno": None}
    observed["facts"]["apparmor"]["bwrap_profiles"] = []
    observed["diagnostics"]["apparmor_profiles"] = diagnostic
    assert admission.parse(encode(observed))["status"] == "OBSERVED"


@pytest.mark.parametrize(
    "reason,code",
    [
        ("unrecognized", None),
        ("missing", errno.EACCES),
        ("unreadable", errno.ENOENT),
        ("io_error", errno.ENOENT),
        ("io_error", errno.EACCES),
        ("read_success", errno.EIO),
        ("io_error", True),
        ("io_error", 4096),
    ],
)
def test_parser_rejects_unknown_or_contradictory_diagnostics(observed, reason, code):
    observed["diagnostics"]["bwrap"]["usr_bin"] = {"reason": reason, "errno": code}
    with pytest.raises(admission.ObservationError):
        admission.parse(encode(observed))


@pytest.mark.parametrize("section", ["root", "bwrap", "candidate", "profiles"])
def test_diagnostic_shape_is_closed(observed, section):
    target = {
        "root": observed["diagnostics"],
        "bwrap": observed["diagnostics"]["bwrap"],
        "candidate": observed["diagnostics"]["bwrap"]["usr_bin"],
        "profiles": observed["diagnostics"]["apparmor_profiles"],
    }[section]
    target["raw_path"] = "/private/secret"
    with pytest.raises(admission.ObservationError):
        admission.parse(encode(observed))


def test_no_success_cannot_hide_unattempted_candidate(observed):
    observed["facts"]["bwrap"] = None
    recalculate(observed)
    observed["diagnostics"]["bwrap"]["bin"] = {"reason": "not_attempted", "errno": None}
    with pytest.raises(admission.ObservationError):
        admission.parse(encode(observed))


def test_success_candidate_must_match_fact_and_short_circuit(observed):
    observed["diagnostics"]["bwrap"]["usr_bin"] = {"reason": "missing", "errno": errno.ENOENT}
    observed["diagnostics"]["bwrap"]["bin"] = {"reason": "read_success", "errno": None}
    with pytest.raises(admission.ObservationError):
        admission.parse(encode(observed))


def test_profiles_null_cannot_be_successful_read(observed):
    observed["facts"]["apparmor"]["bwrap_profiles"] = None
    recalculate(observed)
    observed["diagnostics"]["apparmor_profiles"] = {"reason": "read_success", "errno": None}
    with pytest.raises(admission.ObservationError):
        admission.parse(encode(observed))


def test_v1_receipts_are_not_reinterpreted(observed):
    assert admission.SCHEMA == "forge-linux-admission-observation-v2"
    observed["schema"] = "forge-linux-admission-observation-v1"
    with pytest.raises(admission.ObservationError):
        admission.parse(encode(observed))
    observed.pop("diagnostics")
    with pytest.raises(admission.ObservationError):
        admission.parse(encode(observed))


@pytest.mark.parametrize(
    "failure, disappeared",
    [
        (FileNotFoundError(errno.ENOENT, "fixture"), True),
        (ProcessLookupError(errno.ESRCH, "fixture"), True),
        (PermissionError(errno.EACCES, "fixture"), False),
        (OSError(errno.EIO, "fixture"), False),
        (OSError(errno.EINVAL, "fixture"), False),
        (ProcessLookupError(errno.EACCES, "wrong errno fixture"), False),
    ],
)
def test_live_process_read_failure_contract(monkeypatch, failure, disappeared):
    def read_failure(self, *args, **kwargs):
        raise failure

    monkeypatch.setattr(Path, "read_text", read_failure)
    if disappeared:
        assert _live_process(123) is False
    else:
        with pytest.raises(type(failure)) as caught:
            _live_process(123)
        assert caught.value is failure


@pytest.mark.parametrize("state, expected", [("R", True), ("S", True), ("Z", False)])
def test_live_process_state_contract_unchanged(monkeypatch, state, expected):
    monkeypatch.setattr(Path, "read_text", lambda self: f"123 (fixture) {state} 1 2 3")
    assert _live_process(123) is expected


@pytest.mark.parametrize("event", ["workflow_dispatch", "pull_request"])
@pytest.mark.parametrize("fixed", [False, True])
@pytest.mark.parametrize("ordinary", [False, True])
@pytest.mark.parametrize("same_repository", [False, True])
@pytest.mark.parametrize("view", [False, True])
def test_diagnostic_job_selection_matrix(event, fixed, ordinary, same_repository, view):
    import ast

    workflow = yaml.safe_load(
        (Path(__file__).parents[1] / ".github/workflows/strict-linux-admission.yml").read_text()
    )

    def evaluate(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.BoolOp):
            values = [evaluate(item) for item in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not evaluate(node.operand)
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
            return evaluate(node.left) == evaluate(node.comparators[0])
        raise AssertionError("Unsupported workflow condition")

    selected = set()
    substitutions = {
        "github.event.pull_request.head.repo.full_name": repr(
            "HouMinXi/forge" if same_repository else "fork/forge"
        ),
        "github.repository": repr("HouMinXi/forge"),
        "github.event_name": repr(event),
        "inputs.fixed_metadata_read": str(fixed),
        "inputs.ordinary_dependency_snapshot": str(ordinary),
        "inputs.dependency_view_read": str(view),
    }
    for name, job in workflow["jobs"].items():
        expression = job["if"][3:-2]
        for token, value in substitutions.items():
            expression = expression.replace(token, value)
        expression = expression.replace("&&", " and ").replace("||", " or ").replace("!", " not ")
        if evaluate(ast.parse(expression.strip(), mode="eval").body):
            selected.add(name)
    expected = (
        ({"observe"} if same_repository else set())
        if event == "pull_request"
        else {
            (False, False, False): {"observe"},
            (True, False, False): {"fixed-metadata"},
            (False, True, False): {"ordinary-dependency"},
            (False, False, True): {"dependency-view"},
            (True, True, False): {"conflicting-modes"},
            (True, False, True): {"conflicting-modes"},
            (False, True, True): {"conflicting-modes"},
            (True, True, True): {"conflicting-modes"},
        }[fixed, ordinary, view]
    )
    assert selected == expected
