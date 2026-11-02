"""Offline orchestration fixtures and real local Unix-channel negative controls.

These tests never invoke systemd, create a cgroup or constitute a native pass.
"""

import copy
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import pytest

SPEC = importlib.util.spec_from_file_location(
    "strict_user_service_probe", Path(__file__).parents[1] / "scripts/strict_user_service_probe.py"
)
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)


def context():
    return {
        "source_sha": "a" * 40,
        "collector_sha256": p.source_hash(),
        "run_id": "123",
        "run_attempt": 1,
    }


def topology(unit="forge-topology-" + "a" * 32 + ".service"):
    root = p.manager_root(1001)
    return {
        "source": root + "/app.slice/" + unit,
        "destination": root,
        "common": root,
        "controllers": ["memory", "pids"],
        "subtree_control": ["memory", "pids"],
        "directory_access": True,
        "destination_access": True,
        "common_access": True,
    }


def hello(unit, nonce):
    return {
        "kind": "hello",
        "nonce": nonce,
        "context": context(),
        "uid": 1001,
        "gid": 1001,
        "pid": os.getpid(),
        "starttime": p.starttime(os.getpid()),
        "boot_id": p.boot_id(),
        "namespaces": p.namespaces(),
        "topology": topology(unit),
    }


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1,"a":2}\n',
        b'{"a":NaN}\n',
        b"{}",
        b"{}\n{}\n",
        b'{"a":true} \n',
        b"\xff",
        b" " * (p.LIMIT + 1),
    ],
)
def test_strict_json_rejects(raw):
    with pytest.raises(p.ProbeError):
        p.decode(raw)


@pytest.mark.parametrize(
    "path", ["//a", "/a/../b", "/a/./b", "/a//b", "/a/", "relative", "/a\\b", "/a\nb"]
)
def test_paths_reject_ambiguity(path):
    with pytest.raises(p.ProbeError):
        p.cpath(path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("uid", True),
        ("pid", 0),
        ("starttime", False),
        ("nonce", "a" * 63),
        ("boot_id", "invalid"),
        ("extra", 1),
    ],
)
def test_hello_closed_contract(field, value):
    data = hello("forge-topology-" + "a" * 32 + ".service", "b" * 64)
    data[field] = value
    with pytest.raises(p.ProbeError):
        p.validate_hello(data)


def test_topology_never_uses_outer_caller():
    data = topology()
    data["source"] = "/system.slice/hosted-compute-agent.service"
    with pytest.raises(p.ProbeError):
        p.validate_topology(data, 1001)


@pytest.mark.parametrize(
    "field",
    ["controllers", "subtree_control", "directory_access", "destination_access", "common_access"],
)
def test_missing_prerequisite_is_not_ready(field):
    data = topology()
    data[field] = [] if isinstance(data[field], list) else False
    assert not p.topology_ready(p.validate_topology(data, 1001))


def test_real_unix_peer_and_roundtrip():
    left, right = socket.socketpair()
    with left, right:
        assert p.peer(left) == (os.getpid(), os.getuid(), os.getgid())
        p.send(left, {"message": "bounded"}, time.monotonic() + 1)
        assert p.receive(right, time.monotonic() + 1) == {"message": "bounded"}


@pytest.mark.parametrize(
    "mode,reason",
    [
        ("closed", "channel_closed"),
        ("timeout", "channel_timeout"),
        ("oversize", "invalid_data"),
        ("duplicate", "invalid_data"),
    ],
)
def test_real_unix_negative_controls(mode, reason):
    left, right = socket.socketpair()
    with left, right:
        if mode == "closed":
            left.close()
        elif mode == "oversize":
            left.sendall(b"x" * (p.LIMIT + 1))
        elif mode == "duplicate":
            left.sendall(b'{"kind":1,"kind":2}\n')
        with pytest.raises(p.ProbeError, match=reason):
            p.receive(right, time.monotonic() + 0.03)


def test_real_pidfd_live_then_dead():
    child = subprocess.Popen([sys.executable, "-I", "-S", "-c", "import time; time.sleep(2)"])
    fd = os.pidfd_open(child.pid)
    try:
        started = p.starttime(child.pid)
        p.live(fd, child.pid, started)
        with pytest.raises(p.ProbeError, match="identity_mismatch"):
            p.live(fd, child.pid, started + 1)
        child.terminate()
        child.wait(timeout=2)
        with pytest.raises(p.ProbeError, match="process_gone"):
            p.live(fd, child.pid, started)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=2)
        os.close(fd)


@pytest.mark.parametrize(
    "body,reason",
    [
        ("import time;time.sleep(2)", "command_timeout"),
        ("import sys;sys.stderr.write('private')", "command_output"),
        ("print('x'*20000)", "command_output"),
        ("raise SystemExit(7)", "command_failed"),
    ],
)
def test_real_bounded_command_controls(body, reason):
    with pytest.raises(p.ProbeError, match=reason) as error:
        p.command(
            [sys.executable, "-I", "-S", "-c", body],
            {},
            time.monotonic() + (0.1 if reason == "command_timeout" else 1),
        )
    if reason == "command_failed":
        assert p.error_record(error.value)["exit_code"] == 7


@pytest.fixture
def fixture_probe(monkeypatch, tmp_path):
    """Fake systemd/proc topology only; Unix exchange and pidfd are real locally."""
    state = {"launched": False, "stopped": False, "commands": [], "threads": [], "fault": None}
    for name in ("getuid", "geteuid", "getgid", "getegid"):
        monkeypatch.setattr(p.os, name, lambda: 1001)
    monkeypatch.setattr(
        p, "manager_environment", lambda tool, env, deadline: (["LD_PRELOAD", "PATH"], "a" * 64)
    )
    monkeypatch.setattr(p, "verified_tool", lambda path: path)
    monkeypatch.setattr(p, "verified_source", lambda uid: str(Path(p.__file__).resolve()))
    monkeypatch.setattr(p, "environment", lambda uid: {"XDG_RUNTIME_DIR": str(tmp_path)})
    monkeypatch.setattr(p, "peer", lambda channel: (os.getpid(), 1001, 1001))
    monkeypatch.setattr(p, "membership", lambda pid="self": topology(state["unit"])["source"])
    monkeypatch.setattr(
        p,
        "empty_group",
        lambda group, deadline: (
            p.require(state["fault"] != "descendant", "cleanup_unverified") or "empty"
        ),
    )

    def show(tool, env, unit, deadline, user=True):
        if not user:
            return {"ActiveState": "active", "Delegate": "yes", "ControlGroup": p.manager_root(1001)}
        state["unit"] = unit
        active = state["launched"] and not state["stopped"]
        return {
            "Id": unit,
            "LoadState": "loaded" if state["launched"] else "not-found",
            "ActiveState": "active" if active else "inactive",
            "SubState": "running" if active else "dead",
            "MainPID": os.getpid() if active else 0,
            "ControlGroup": topology(unit)["source"] if active else "",
            "Delegate": "no",
            "Transient": "yes",
            "Description": "collision" if state["fault"] == "ownership" else state.get("marker", ""),
            "InvocationID": "c" * 32,
        }

    def actor(path, unit, nonce):
        try:
            with socket.socket(socket.AF_UNIX) as channel:
                channel.settimeout(1)
                channel.connect(path)
                data = hello(unit, nonce)
                if state["fault"] in {"nonce", "double"}:
                    data["nonce"] = "0" * 64
                if state["fault"] == "namespace":
                    data["namespaces"]["mnt"] += 1
                if state["fault"] == "boot":
                    data["boot_id"] = "00000000-0000-0000-0000-000000000000"
                if state["fault"] == "pid":
                    data["pid"] += 1
                if state["fault"] == "gid":
                    data["gid"] += 1
                if state["fault"] == "collector":
                    data["context"]["collector_sha256"] = "0" * 64
                if state["fault"] == "source":
                    data["context"]["source_sha"] = "0" * 40
                if state["fault"] == "starttime":
                    data["starttime"] += 1
                if state["fault"] == "access":
                    data["topology"]["common_access"] = False
                p.send(channel, data, time.monotonic() + 1)
                challenge = p.receive(channel, time.monotonic() + 1)
                p.send(
                    channel,
                    {
                        "kind": "ack",
                        "nonce": nonce,
                        "challenge": "0" * 64 if state["fault"] == "ack" else challenge["challenge"],
                    },
                    time.monotonic() + 1,
                )
                p.receive(channel, time.monotonic() + 1)
        except (OSError, p.ProbeError):
            pass  # Deliberate parent rejection/close in negative fixtures.

    def command(argv, env, deadline):
        state["commands"].append(argv)
        if argv[0] == p.SYSTEMD_RUN:
            state["launched"] = True
            state["marker"] = next(
                arg.split("=", 1)[1] for arg in argv if arg.startswith("--description=")
            )
            thread = threading.Thread(
                target=actor,
                args=(argv[argv.index("--service") + 1], state["unit"], argv[argv.index("--nonce") + 1]),
            )
            state["threads"].append(thread)
            thread.start()
        else:
            assert argv == [p.SYSTEMCTL, "--user", "stop", "--", state["unit"]]
            if state["fault"] in {"stop", "double"}:
                raise p.ProbeError("command_failed", 1)
            state["stopped"] = True
        return ""

    # This executor permits socketpair but denies AF_UNIX bind/listen (EPERM).
    # Fake ONLY the listener/path plumbing; channel bytes use a real socketpair.
    left, right = socket.socketpair()
    calls = []

    class FixtureSocket:
        def __init__(self, endpoint, label):
            self.endpoint = endpoint
            self.label = label

        def __getattr__(self, key):
            return getattr(self.endpoint, key)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.endpoint.close()

        def bind(self, path):
            Path(path).touch()

        def listen(self, size):
            pass

        def accept(self):
            return FixtureSocket(self.endpoint, "peer"), None

        def connect(self, path):
            pass

        def close(self):
            state.setdefault("closed", []).append(self.label)
            self.endpoint.close()
            if self.label in state.get("close_fault", []):
                raise OSError(5, "fixture close failure")

    def fixture_socket(*args):
        calls.append(1)
        return FixtureSocket(left, "listener") if len(calls) == 1 else FixtureSocket(right, "actor")

    monkeypatch.setattr(p.socket, "socket", fixture_socket)
    monkeypatch.setattr(p.stat, "S_ISSOCK", lambda mode: True)

    monkeypatch.setattr(p, "show", show)
    monkeypatch.setattr(p, "command", command)
    yield state
    left.close()
    right.close()
    for thread in state["threads"]:
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_offline_positive_contract_not_native(fixture_probe):
    result = p.probe(context())
    assert result["primary"] is None, result["primary"]
    assert result["status"] == "OBSERVED", result
    assert result["boundary"] == result["qualification"] == "NOT_RUN"
    assert p.parse_receipt(p.encode(result)) == result
    assert fixture_probe["stopped"]
    assert "nonce" not in result["observation"]
    launch = fixture_probe["commands"][0]
    assert not any("Delegate=" in arg for arg in launch)
    assert "--property=KillMode=control-group" in launch
    assert ["-I", "-S", "-B"] == launch[launch.index(p.PYTHON) + 1 : launch.index(p.PYTHON) + 4]


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("nonce", "identity_mismatch"),
        ("boot", "identity_mismatch"),
        ("namespace", "identity_mismatch"),
        ("pid", "identity_mismatch"),
        ("gid", "identity_mismatch"),
        ("collector", "identity_mismatch"),
        ("source", "identity_mismatch"),
        ("starttime", "identity_mismatch"),
        ("ack", "identity_mismatch"),
        ("access", "topology_prerequisites"),
    ],
)
def test_offline_binding_rejects_and_cleans(fixture_probe, fault, reason):
    fixture_probe["fault"] = fault
    result = p.probe(context())
    assert result["status"] == "STOP"
    assert result["primary"]["reason"] == reason
    assert result["cleanup"]["status"] == "OBSERVED"
    assert fixture_probe["stopped"]


@pytest.mark.parametrize("fault", ["descendant", "stop"])
def test_cleanup_failure_keeps_socket(fixture_probe, tmp_path, fault):
    fixture_probe["fault"] = fault
    result = p.probe(context())
    assert result["status"] == "STOP"
    assert result["cleanup"]["status"] == "STOP"
    assert len(list(tmp_path.glob("forge-topology-*/channel"))) == 1


def test_ownership_mismatch_never_stops_unit(fixture_probe):
    fixture_probe["fault"] = "ownership"
    result = p.probe(context())
    assert result["status"] == "STOP"
    assert result["primary"]["reason"] == "identity_mismatch"
    assert result["cleanup"]["status"] == "STOP"
    assert len(fixture_probe["commands"]) == 1


@pytest.mark.parametrize(
    "change",
    [
        "qualification",
        "status",
        "extra",
        "bool_duration",
        "observation_extra",
        "nonce_in_observation",
        "cleanup_lie",
    ],
)
def test_receipt_rejects_forged_claims(fixture_probe, change):
    result = copy.deepcopy(p.probe(context()))
    if change == "qualification":
        result["qualification"] = "PASS"
    elif change == "status":
        result["status"] = "PASS"
    elif change == "extra":
        result["raw_output"] = "secret"
    elif change == "bool_duration":
        result["duration_ms"] = True
    elif change == "observation_extra":
        result["observation"]["extra"] = "secret"
    elif change == "nonce_in_observation":
        result["observation"]["nonce"] = "0" * 64
    else:
        result["cleanup"]["reason"] = "cleanup_unverified"
    with pytest.raises(p.ProbeError):
        p.parse_receipt(p.encode(result))


def test_owned_cgroup_descendants_and_symlinks(monkeypatch, tmp_path):
    monkeypatch.setattr(p, "CGROUP", tmp_path)
    root = tmp_path / "owned"
    root.mkdir()
    for directory in (root, root / "child"):
        directory.mkdir(exist_ok=True)
        (directory / "cgroup.procs").write_text("")
        (directory / "cgroup.events").write_text("populated 0\nfrozen 0\n")
    assert p.empty_group("/owned", time.monotonic() + 1)
    (root / "child" / "cgroup.procs").write_text("123\n")
    with pytest.raises(p.ProbeError, match="cleanup_unverified"):
        p.empty_group("/owned", time.monotonic() + 1)
    (root / "child" / "cgroup.procs").write_text("")
    (root / "sibling").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(p.ProbeError, match="cleanup_unverified"):
        p.empty_group("/owned", time.monotonic() + 1)


def test_primary_failure_survives_cleanup_failure(fixture_probe):
    fixture_probe["fault"] = "double"
    result = p.probe(context())
    assert result["primary"]["reason"] == "identity_mismatch"
    assert result["cleanup"]["reason"] == "command_failed"
    assert result["status"] == "STOP"


@pytest.mark.parametrize("reason", [[], {}, 1, False])
def test_error_reason_rejects_wrong_types(reason):
    with pytest.raises(p.ProbeError):
        p.validate_error({"reason": reason, "errno": None, "exit_code": None, "signal": None})


def test_command_preserves_primary_and_closes_streams_on_kill_error(monkeypatch):
    original_popen, original_kill = p.subprocess.Popen, p.os.killpg
    children = []

    def launch(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        children.append(child)
        return child

    def kill_then_error(pid, signum):
        original_kill(pid, signum)
        raise PermissionError(1, "fixture denied")

    monkeypatch.setattr(p.subprocess, "Popen", launch)
    monkeypatch.setattr(p.os, "killpg", kill_then_error)
    with pytest.raises(p.ProbeError, match="command_output") as caught:
        p.command(
            [sys.executable, "-I", "-S", "-c", "import sys;sys.stderr.write('private')"],
            {},
            time.monotonic() + 1,
        )
    record = p.error_record(caught.value)
    assert record["reason"] == "command_output"
    assert record["child_cleanup"] == {"reason": "cleanup_unverified", "errno": 1}
    assert children[0].stdout.closed and children[0].stderr.closed
    assert children[0].returncode is not None


def test_command_preserves_primary_and_closes_streams_on_reap_timeout(monkeypatch):
    original_popen = p.subprocess.Popen
    children, waits = [], []

    def launch(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        children.append(child)
        original_wait = child.wait
        waits.append(original_wait)

        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired("fixture", 1)

        child.wait = timeout
        return child

    monkeypatch.setattr(p.subprocess, "Popen", launch)
    try:
        with pytest.raises(p.ProbeError, match="command_output") as caught:
            p.command(
                [sys.executable, "-I", "-S", "-c", "import sys;sys.stderr.write('private')"],
                {},
                time.monotonic() + 1,
            )
        assert p.error_record(caught.value)["child_cleanup"]["reason"] == "cleanup_unverified"
        assert children[0].stdout.closed and children[0].stderr.closed
    finally:
        for wait in waits:
            wait(timeout=2)


def test_stacked_mountpoint_is_rejected(monkeypatch):
    mounts = b"10 2 0:8 / /sys/fs/cgroup rw - cgroup2 cgroup rw\n11 2 0:9 / /sys/fs/cgroup rw - tmpfs tmpfs rw\n"
    monkeypatch.setattr(p, "read", lambda path, limit=p.LIMIT: mounts)
    with pytest.raises(p.ProbeError, match="topology_unavailable"):
        p.topology()


@pytest.mark.parametrize("raw", ["LD_PRELOAD=private.so\nPATH=/bin\n", "EMPTY=\n", ""])
def test_environment_snapshot_only_names_and_internal_digest(monkeypatch, raw):
    calls = []

    def command(argv, env, deadline):
        calls.append((argv, env))
        return raw

    monkeypatch.setattr(p, "command", command)
    names, digest = p.manager_environment(p.SYSTEMCTL, {"PATH": "/usr/bin:/bin"}, 1)
    assert names == sorted(line.split("=", 1)[0] for line in raw.splitlines())
    assert len(digest) == 64
    assert "private.so" not in repr((names, digest))
    assert calls[0][0] == [p.SYSTEMCTL, "--user", "show-environment"]


@pytest.mark.parametrize(
    "raw", ["BAD-NAME=x\n", "X=1\nX=2\n", "=bad\n", "A" * 129 + "=1\n", "NO_EQUALS\n"]
)
def test_environment_snapshot_rejects_ambiguous_names(monkeypatch, raw):
    monkeypatch.setattr(p, "command", lambda *args: raw)
    with pytest.raises(p.ProbeError):
        p.manager_environment(p.SYSTEMCTL, {}, 1)


def test_inherited_loader_names_are_explicitly_unset(fixture_probe):
    result = p.probe(context())
    assert result["status"] == "OBSERVED"
    assert "--property=UnsetEnvironment=LD_PRELOAD PATH" in fixture_probe["commands"][0]
    assert "LD_PRELOAD" not in p.encode(result).decode("ascii")
    assert result["binding"]["main_pid"] == result["observation"]["pid"]
    assert result["cleanup_evidence"]["main_pid"] == 0
    assert result["cleanup_evidence"]["group_status"] == "empty"


def test_manager_environment_change_stops(fixture_probe, monkeypatch):
    calls = []

    def snapshot(*args):
        calls.append(1)
        return ["PATH"], ("a" if len(calls) == 1 else "b") * 64

    monkeypatch.setattr(p, "manager_environment", snapshot)
    result = p.probe(context())
    assert result["primary"]["reason"] == "identity_mismatch"
    assert result["cleanup"]["status"] == "OBSERVED"
    assert result["observation_status"] == "UNKNOWN"


@pytest.mark.parametrize(
    "field", ["main_pid", "peer_gid", "control_group", "pidfd_after_ack", "invocation"]
)
def test_binding_receipt_tampering_rejected(fixture_probe, field):
    result = p.probe(context())
    result["binding"][field] = False
    with pytest.raises(p.ProbeError):
        p.parse_receipt(p.encode(result))


@pytest.mark.parametrize("primary_fault", [None, "nonce"])
def test_socket_close_failures_never_skip_owned_unit_stop(fixture_probe, primary_fault):
    fixture_probe["fault"] = primary_fault
    fixture_probe["close_fault"] = ["peer", "listener"]
    result = p.probe(context())
    assert fixture_probe["stopped"]
    assert "peer" in fixture_probe["closed"] and "listener" in fixture_probe["closed"]
    assert result["cleanup_evidence"]["main_pid"] == 0
    assert result["cleanup"]["status"] == "STOP"
    assert result["channel_cleanup_errors"] == [
        {"channel": "peer", "errno": 5},
        {"channel": "listener", "errno": 5},
    ]
    assert result["status"] == "STOP"
    if primary_fault:
        assert result["primary"]["reason"] == "identity_mismatch"
    else:
        assert result["primary"] is None
