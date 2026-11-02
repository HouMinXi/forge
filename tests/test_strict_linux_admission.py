"""Closed read-only observation receipts cannot claim native qualification."""

import copy
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace
from pathlib import Path

import pytest

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
        "status": "OBSERVED",
        "problems": [],
        "boundary": "NOT_RUN",
        "qualification": "NOT_RUN",
    }


def encode(receipt):
    return (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()


def recalculate(receipt):
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
    monkeypatch.setattr(admission, "bwrap_identity", lambda: None)
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


def test_workflow_is_manual_read_only_and_not_existing_relaxed_lane():
    text = (Path(__file__).parents[1] / ".github/workflows/strict-linux-admission.yml").read_text()
    assert "workflow_dispatch: null" in text
    assert "pull_request:" not in text and "push:" not in text
    assert "contents: read" in text and "persist-credentials: false" in text
    assert "ref: ${{ github.sha }}" in text
    assert "always()" in text
    assert "sudo " not in text and "sysctl " not in text
    assert "pip install" not in text and "systemd-run" not in text
    assert "strict_linux_admission.py" in text


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
    return raw.rsplit(")", 1)[1].split()[0] != "Z"


@pytest.mark.parametrize("scenario", ["timeout", "overflow", "leader_exit"])
def test_query_cleanup_stops_actual_group_descendant(monkeypatch, tmp_path, scenario):
    children = []
    real_popen = subprocess.Popen

    def record(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(admission.subprocess, "Popen", record)
    monkeypatch.setattr(admission, "COMMAND_SECONDS", 0.3)
    monkeypatch.setattr(admission, "COMMAND_BYTES", 32)
    pidfile = tmp_path / "descendant.pid"
    program = (
        "import os,time\n"
        "child=os.fork()\n"
        "if child == 0:\n"
        f"    open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
        "    time.sleep(60)\n"
        "else:\n"
        f"    while not os.path.exists({str(pidfile)!r}): time.sleep(.001)\n"
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
        if pidfile.exists():
            pid = int(pidfile.read_text())
            if _live_process(pid):
                os.kill(pid, 9)


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
