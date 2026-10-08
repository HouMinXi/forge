"""Offline pure/mocked service tests plus ordinary child/socket transport only.

No test calls systemd, changes policy, writes cgroups or activates a manager.
"""
from __future__ import annotations

import array
import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from forge_ci import user_service as s  # noqa: E402

REAL_POPEN = subprocess.Popen


@pytest.fixture(autouse=True)
def no_live_commands(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("test attempted an unmocked command or service operation")
    monkeypatch.setattr(subprocess, "Popen", denied)
    monkeypatch.setattr(s, "metadata", denied)


def binding_fixture():
    from datetime import datetime, timezone
    started = int(time.time()) - 100
    return {"schema_version": 1, "repository_id": 1258832822, "owner_id": 19586012,
            "actor_id": 19586012, "triggering_actor_id": 19586012, "event_name": "push",
            "full_ref": "refs/heads/fix/review-correctness-linux-ci", "before_sha": "a" * 40,
            "candidate_sha": "b" * 40, "workflow_sha": "b" * 40,
            "workflow_path": ".github/workflows/linux-tests.yml", "run_id": 42, "run_number": 23,
            "run_attempt": 1, "job_key": "linux-tests", "boot_id": "12345678-1234-1234-1234-123456789012",
            "workflow_id": 333, "job_id": 444,
            "job_started_at": datetime.fromtimestamp(started, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "job_started_ns": started * s.NS}


def source_fixture():
    return {"candidate_sha": "b" * 40, "tree_oid": "c" * 40, "source_sha256": "c" * 64,
            "workflow_sha256": "d" * 64, "helper_sha256": dict.fromkeys(s.HELPERS, "d" * 64)}


@pytest.fixture
def capsule():
    binding = binding_fixture()
    env = {name: "fixture" for name in s.REQUIRED_ENV}
    env.update(s.FIXED_ENV)
    env.update({"RUNNER_OS": "Linux", "RUNNER_ARCH": "X64", "GITHUB_ACTIONS": "true", "CI": "true",
                "GITHUB_WORKSPACE": "/checkout", "EVIDENCE": "/evidence", "GITHUB_SHA": "b" * 40,
                "GITHUB_WORKFLOW_SHA": "b" * 40, "GITHUB_RUN_ID": "42", "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_JOB": "linux-tests", "GITHUB_RUN_NUMBER": "23",
                "RUNNER_ENVIRONMENT": "github-hosted", "ImageOS": "ubuntu24"})
    mono, wall = time.monotonic_ns(), time.time_ns()
    return {"schema_version": 1, "kind": "qualification", "binding": binding, "source": source_fixture(),
            "owner": {"pid": 123, "uid": 1001, "gid": 1001, "start_ticks": 55, "pidns": "pid:[123]",
                      "userns": "user:[1]", "mntns": "mnt:[2]", "cgroupns": "cgroup:[3]", "boot_id": binding["boot_id"]},
            "repo": "/checkout", "cwd": "/checkout", "evidence": "/evidence/qualification",
            "receipt": "/evidence/launch-bootstrap.json",
            "entrypoint": {"python": s.PROVIDER, "helper_sha256": "d" * 64, "controller_sha256": "e" * 64,
                           "receipt_sha256": "f" * 64},
            "clock": {"started_utc_ns": wall, "started_monotonic_ns": mono,
                      "deadline_utc_ns": wall + s.STEP_SECONDS * s.NS, "deadline_monotonic_ns": mono + s.STEP_SECONDS * s.NS,
                      "job_deadline_utc_ns": binding["job_started_ns"] + s.JOB_SECONDS * s.NS,
                      "artifact_deadline_utc_ns": binding["job_started_ns"] + (s.JOB_SECONDS - s.ARTIFACT_SECONDS) * s.NS},
            "environment": env}


def test_canonical_capsule_roundtrip_and_closed_payload(capsule):
    raw = s.canonical(capsule)
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
    assert s.parse_capsule(raw) == capsule
    source = {**capsule["environment"], "SECRET": "bearer do not serialize", "LD_PRELOAD": "hostile"}
    assert s.payload_environment(source) == capsule["environment"]
    assert b"bearer" not in s.canonical(s.payload_environment(source))


@pytest.mark.parametrize("change", [
    lambda c: c.update(unknown="secret"), lambda c: c.update(schema_version=True),
    lambda c: c.update(kind="relay"), lambda c: c["owner"].update(pid=0),
    lambda c: c["owner"].update(uid=0), lambda c: c["owner"].update(gid=0),
    lambda c: c["owner"].update(start_ticks=True), lambda c: c["owner"].update(pidns="pid:[0]"),
    lambda c: c["owner"].update(boot_id="other"), lambda c: c["owner"].update(extra=1),
    lambda c: c["binding"].update(candidate_sha="0" * 40), lambda c: c["binding"].update(run_id=True),
    lambda c: c["binding"].update(run_attempt=-1), lambda c: c["binding"].update(job_key="bad;job"),
    lambda c: c["binding"].update(boot_id="missing"), lambda c: c.update(cwd="/elsewhere"),
    lambda c: c.update(repo="/x/../checkout"), lambda c: c.update(evidence="/evidence/not-qualification"),
    lambda c: c["entrypoint"].update(python="/bin/sh"), lambda c: c["entrypoint"].update(receipt_sha256="wrong"),
    lambda c: c["clock"].update(deadline_monotonic_ns=1), lambda c: c["clock"].update(extra=1),
    lambda c: c["environment"].pop("HOME"), lambda c: c["environment"].update(HOME="\0SECRET"),
    lambda c: c["environment"].update(HOME="x" * 16385), lambda c: c["environment"].update(LD_PRELOAD="SECRET"),
    lambda c: c["environment"].update({"INPUT_CONFIG-SHA256": "relay-only-secret"}),
    lambda c: c["environment"].update(PYTHONPATH="ambient"), lambda c: c["environment"].update(CI="false"),
    lambda c: c["environment"].update(GITHUB_SHA="a" * 40), lambda c: c["environment"].update(GITHUB_RUN_ID="043"),
    lambda c: c["environment"].update(EVIDENCE="/other"),
])
def test_capsule_negative_matrix(capsule, change):
    change(capsule)
    with pytest.raises(s.ServiceError) as error:
        s.parse_capsule(s.canonical(capsule))
    assert "SECRET" not in str(error.value) and "relay-only-secret" not in str(error.value)


@pytest.mark.parametrize("raw", [b"", b"{}", b"{}\n\n", b"{}\n{}\n", b'{"a":1,"a":2}\n',
                                      b'{"a":NaN}\n', b'{"a":Infinity}\n', b'{"a":"\xff"}\n',
                                      b"x" * (s.MAX_CAPSULE + 1)])
def test_bad_capsule_framing_and_json(raw):
    with pytest.raises(s.ServiceError):
        s.parse_capsule(raw)


def test_missing_field_and_noncanonical_spacing(capsule):
    for field in tuple(capsule):
        value = copy.deepcopy(capsule)
        del value[field]
        with pytest.raises(s.ServiceError):
            s.parse_capsule(s.canonical(value))
    with pytest.raises(s.ServiceError):
        s.parse_capsule(json.dumps(capsule).encode() + b"\n")


def test_optional_payload_values_preserved(capsule):
    source = {**capsule["environment"], "LANG": "en_US.UTF-8", "USER": "runner", "TZ": "Etc/UTC", "SHELL": "/bin/bash"}
    assert s.payload_environment(source) == source
    assert not s.OPTIONAL_ENV & s.payload_environment(capsule["environment"]).keys()


@pytest.mark.parametrize("key", sorted(s.FIXED_ENV))
def test_fixed_payload_values_mandatory(capsule, key):
    capsule["environment"][key] = "other"
    with pytest.raises(s.ServiceError):
        s.payload_environment(capsule["environment"])


def test_manager_private_values_discarded_and_inert_overrides():
    raw = b"PRIVATE_TOKEN='super secret bearer'\nPATH=/hostile\nPYTHONPATH=evil\nLD_PRELOAD=evil\n"
    names = s.manager_keys(raw)
    assert "PRIVATE_TOKEN" in names and "PYTHONPATH" in names and "LD_PRELOAD" in names
    assert not s.BOOT_ENV.keys() & set(names)
    assert "super secret" not in repr(names)
    assert s.manager_keys(b"") == sorted(s.FIXED_UNSET)


@pytest.mark.parametrize("raw", [b"NO_EQUALS", b"A-B=secret\n", b"1BAD=secret\n", b"A=1\nA=2\n",
                                  b"A\x00=secret\n", b"A" * 129 + b"=secret\n", b"=secret\n",
                                  b"A=secret\n\n", b"A=" + b"x" * s.MAX_METADATA,
                                  b"".join(f"KEY{i}=secret\n".encode() for i in range(257))])
def test_bad_manager_environment_no_value_leaks(raw):
    with pytest.raises(s.ServiceError) as error:
        s.manager_keys(raw)
    assert "secret" not in str(error.value)


def test_exact_fixed_service_and_child_argv(capsule):
    args = s.service_argv(capsule, s.manager_keys(b"PRIVATE=SECRET\n"))
    assert args[:7] == ["/usr/bin/systemd-run", "--user", "--no-ask-password", "--service-type=exec", "--wait", "--pipe", "--expand-environment=no"]
    assert args[-7:] == ["--", "/usr/bin/python3", "-B", "-I", "-S", "/checkout/" + s.HELPER, "bootstrap"]
    assert "--unit=forge-ci-42-1-444.service" in args
    assert "--property=RuntimeMaxSec=4380" in args and "--property=TimeoutStopSec=30" in args
    assert not any("SECRET" in arg or "--scope" in arg or "Delegate" in arg or "--collect" in arg for arg in args)
    assert s.controller_argv(capsule) == [s.PROVIDER, "-m", "forge_ci.controller", "--receipt", "/evidence/launch-bootstrap.json",
                                          "--repo", "/checkout", "--evidence", "/evidence/qualification"]
    assert s.STARTUP_SECONDS + s.RUNTIME_SECONDS + s.STOP_SECONDS < s.CLIENT_SECONDS < s.STEP_SECONDS
    assert s.CANCEL_SECONDS + s.GRACE_SECONDS + s.REAP_SECONDS < s.RUNTIME_SECONDS


@pytest.mark.parametrize("unset", [["BAD-NAME"], ["PATH"], ["LD_PRELOAD", "LD_PRELOAD"], []])
def test_invalid_unset_property(capsule, unset):
    with pytest.raises(s.ServiceError):
        s.service_argv(capsule, unset)


def test_socket_transport_requires_eof(capsule):
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        left.sendall(s.canonical(capsule))
        left.shutdown(socket.SHUT_WR)
        assert s.read_capsule(right) == capsule
    finally:
        left.close()
        right.close()


def test_socket_transport_truncation(capsule):
    left, right = socket.socketpair()
    try:
        left.sendall(s.canonical(capsule)[:-5])
        left.shutdown(socket.SHUT_WR)
        with pytest.raises(s.ServiceError):
            s.read_capsule(right)
    finally:
        left.close()
        right.close()


def test_capsule_timeout_and_overflow(monkeypatch):
    class Stream:
        def settimeout(self, timeout):
            assert timeout <= 5
        def recv(self, limit):
            return b"x" * limit
    with pytest.raises(s.ServiceError, match="byte bound"):
        s.read_capsule(Stream())
    values = iter([0, 6])
    monkeypatch.setattr(s.time, "monotonic", lambda: next(values))
    with pytest.raises(s.ServiceError, match="deadline"):
        s.read_capsule(Stream())


def test_real_socket_peer_is_kernel_owner_and_private():
    owner = s.process_identity(os.getpid())
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        fd = s.peer_owner(right, owner)
        try:
            assert not os.get_inheritable(fd) and not s.ready(fd)
            left.close()  # EOF does not end independently monitored process ownership.
            assert not s.ready(fd)
        finally:
            os.close(fd)
    finally:
        left.close()
        right.close()


class FakeSocket:
    family = socket.AF_UNIX
    def __init__(self, owner, *, kind=socket.SOCK_STREAM, peer=None, unsupported=False):
        self.owner, self.kind, self.peer, self.unsupported = owner, kind, peer, unsupported
    def getsockopt(self, level, name, *args):
        assert level == socket.SOL_SOCKET
        if name == socket.SO_TYPE:
            return self.kind
        if name == socket.SO_PEERCRED:
            return self.peer if self.peer is not None else struct.pack("3i", self.owner["pid"], self.owner["uid"], self.owner["gid"])
        assert name == 77
        if self.unsupported:
            raise OSError("unsupported")
        return os.open("/dev/null", os.O_RDONLY)


@pytest.mark.parametrize("failure", ["family", "type", "short", "foreign_pid", "foreign_uid", "unsupported", "dead", "start", "namespace"])
def test_peer_contract_negative_matrix(monkeypatch, failure):
    owner = s.process_identity(os.getpid())
    transport = FakeSocket(owner)
    if failure == "family":
        transport.family = socket.AF_INET
    elif failure == "type":
        transport.kind = socket.SOCK_DGRAM
    elif failure == "short":
        transport.peer = b"short"
    elif failure == "foreign_pid":
        transport.peer = struct.pack("3i", owner["pid"] + 1, owner["uid"], owner["gid"])
    elif failure == "foreign_uid":
        transport.peer = struct.pack("3i", owner["pid"], owner["uid"] + 1, owner["gid"])
    elif failure == "unsupported":
        transport.unsupported = True
    else:
        monkeypatch.setattr(s, "ready", lambda fd: failure == "dead")
        changed = {**owner, "start_ticks": owner["start_ticks"] + 1} if failure == "start" else {**owner, "pidns": "pid:[999]"}
        calls = iter([owner, changed]) if failure == "namespace" else iter([changed])
        monkeypatch.setattr(s, "process_identity", lambda pid: next(calls))
    with pytest.raises((s.ServiceError, OSError)):
        s.peer_owner(transport, owner)


def test_pipe_is_not_socket_transport():
    readfd, writefd = os.pipe()
    try:
        with pytest.raises(OSError):
            socket.socket(fileno=os.dup(readfd))
    finally:
        os.close(readfd)
        os.close(writefd)


def test_dirty_bootstrap_stops_before_capsule(monkeypatch):
    monkeypatch.setattr(s.os, "environ", {**s.BOOT_ENV, "LD_PRELOAD": "SECRET"})
    monkeypatch.setattr(s, "read_capsule", lambda *_: pytest.fail("read private capsule before closed environment check"))
    with pytest.raises(s.ServiceError, match="unclean"):
        s.bootstrap()


def test_startup_deadline_exhaustion(capsule):
    capsule["clock"]["started_monotonic_ns"] -= 31 * s.NS
    with pytest.raises(s.ServiceError, match="exhausted"):
        with s.startup_limit(capsule["clock"]):
            pytest.fail("entered expired startup")


def test_cancellation_handler_is_installed_before_work():
    original = signal.getsignal(signal.SIGTERM)
    with s.cancellation() as cancelled:
        os.kill(os.getpid(), signal.SIGTERM)
        assert cancelled["signal"] == signal.SIGTERM
    assert signal.getsignal(signal.SIGTERM) == original


def test_failed_exec_is_not_success(monkeypatch, capsule):
    monkeypatch.setattr(s, "ready", lambda fd: False)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("PRIVATE")))
    with pytest.raises(FileNotFoundError):
        s.watch_child(capsule, 9, {"signal": None})


@pytest.mark.parametrize("early", ["signal", "owner", "startup"])
def test_early_cancellation_never_spawns(monkeypatch, capsule, early):
    monkeypatch.setattr(s, "ready", lambda fd: early == "owner")
    monkeypatch.setattr(s, "remaining", lambda *a: 0 if early == "startup" else 20)
    with pytest.raises(s.ServiceError, match="before child"):
        s.watch_child(capsule, 9, {"signal": signal.SIGTERM if early == "signal" else None})


def ordinary_child_context(monkeypatch, capsule, script):
    owner = s.process_identity(os.getpid())
    capsule["owner"] = owner
    capsule["binding"]["boot_id"] = owner["boot_id"]
    capsule["cwd"] = str(Path.cwd())
    capsule["environment"] = {"PATH": "/usr/bin:/bin"}
    calls = []
    def spawn(argv, **kwargs):
        assert argv == s.controller_argv(capsule)
        assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["close_fds"] is True
        assert "pass_fds" not in kwargs and "stdout" not in kwargs and "stderr" not in kwargs
        process = REAL_POPEN([sys.executable, "-c", script], **kwargs)
        calls.append(process)
        return process
    monkeypatch.setattr(subprocess, "Popen", spawn)
    return owner, calls


@pytest.mark.parametrize("exit_kind", ["success", "nonzero", "signal"])
def test_actual_child_exit_and_stream_contract(monkeypatch, capsule, exit_kind):
    script = {"success": "import time;time.sleep(.15)", "nonzero": "import time;time.sleep(.15);raise SystemExit(7)",
              "signal": "import os,signal,time;time.sleep(.15);os.kill(os.getpid(),signal.SIGUSR1)"}[exit_kind]
    _, calls = ordinary_child_context(monkeypatch, capsule, script)
    fd = os.pidfd_open(os.getpid())
    try:
        result = s.watch_child(capsule, fd, {"signal": None})
    finally:
        os.close(fd)
    expected = {"success": 0, "nonzero": 7, "signal": -signal.SIGUSR1}[exit_kind]
    assert result["returncode"] == expected and result["child_pid"] == calls[0].pid
    assert result["signal"] == (-expected if expected < 0 else None)
    assert result["exit_code"] == (expected if expected >= 0 else None)
    assert result["owned_child_reaped"] is True and calls[0].poll() == expected
    assert result["sibling_cgroup_cleanup"] == "not_certified_by_service"


@pytest.mark.parametrize("reason", ["signal", "deadline", "owner_lost"])
def test_owned_child_grace_then_exact_kill(monkeypatch, capsule, reason):
    _, calls = ordinary_child_context(monkeypatch, capsule, "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(10)")
    start = time.monotonic()
    monkeypatch.setattr(s, "GRACE_SECONDS", .1)
    monkeypatch.setattr(s, "ready", lambda fd: reason == "owner_lost" and time.monotonic() - start > .2)
    real_remaining = s.remaining
    monkeypatch.setattr(s, "remaining", lambda clock, seconds: 0 if reason == "deadline" and seconds == s.CANCEL_SECONDS
                        and time.monotonic() - start > .2 else real_remaining(clock, seconds))
    cancel = {"signal": None}
    timer = threading.Timer(.2, lambda: cancel.update(signal=signal.SIGTERM) if reason == "signal" else None)
    timer.start()
    try:
        result = s.watch_child(capsule, 999, cancel)
    finally:
        timer.join()
    assert result["cancel_reason"] == reason and result["returncode"] == -signal.SIGKILL
    assert calls[0].poll() == -signal.SIGKILL and result["owned_child_reaped"] is True


def test_kernel_owner_sigkill_drives_private_pidfd(monkeypatch, capsule):
    # Child creates the actual capsule socketpair. SCM_RIGHTS is test-only
    # transport for giving the observer that original endpoint; production uses
    # systemd --pipe and has no listener or second protocol.
    handoff, child_handoff = socket.socketpair()
    owner_pid = os.fork()
    if owner_pid == 0:
        handoff.close()
        left, right = socket.socketpair()
        child_handoff.sendmsg([b"x"], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [right.fileno()]))])
        right.close()
        try:
            time.sleep(10)
        finally:
            os._exit(0)
    child_handoff.close()
    fd, transport = None, None
    try:
        _, ancillary, _, _ = handoff.recvmsg(1, socket.CMSG_SPACE(array.array("i").itemsize))
        rawfds = array.array("i")
        rawfds.frombytes(ancillary[0][2][:rawfds.itemsize])
        transport = socket.socket(fileno=rawfds[0])
        owner = s.process_identity(owner_pid)
        fd = s.peer_owner(transport, owner)
        _, calls = ordinary_child_context(monkeypatch, capsule, "import time;time.sleep(10)")
        timer = threading.Timer(.2, lambda: os.kill(owner_pid, signal.SIGKILL))
        timer.start()
        try:
            result = s.watch_child(capsule, fd, {"signal": None})
        finally:
            timer.join()
        assert result["cancel_reason"] == "owner_lost" and result["returncode"] == -signal.SIGTERM
        assert calls[0].poll() == -signal.SIGTERM and s.ready(fd)
    finally:
        if fd is not None:
            os.close(fd)
        if transport is not None:
            transport.close()
        handoff.close()
        try:
            os.kill(owner_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        os.waitpid(owner_pid, 0)


def test_closed_private_descriptor_not_inherited(monkeypatch, capsule, tmp_path):
    ownerfd = os.pidfd_open(os.getpid())
    output = tmp_path / "fd-result"
    script = ("import os,time;time.sleep(.1);"
              f"p='/proc/self/fd/{ownerfd}';open({str(output)!r},'w').write(str(os.path.exists(p)))")
    ordinary_child_context(monkeypatch, capsule, script)
    try:
        result = s.watch_child(capsule, ownerfd, {"signal": None})
    finally:
        os.close(ownerfd)
    assert result["returncode"] == 0 and output.read_text() == "False"


def test_service_receipts_are_beside_fresh_controller_evidence(capsule, tmp_path):
    capsule["evidence"] = str(tmp_path / "qualification")
    s.receipt(capsule, "setup", {"status": "ADMITTED"})
    assert not (tmp_path / "qualification").exists()
    saved = json.loads((tmp_path / "service-setup.json").read_bytes())
    assert saved["spec_sha256"] == s.SPEC_SHA256 and saved["binding"] == capsule["binding"]
    assert (tmp_path / "service-setup.json").stat().st_mode & 0o777 == 0o600
    assert "environment" not in saved
    with pytest.raises(FileExistsError):
        s.receipt(capsule, "setup", {"status": "ADMITTED"})


def test_private_exception_is_redacted(monkeypatch, capsys):
    monkeypatch.setattr(s, "bootstrap", lambda: (_ for _ in ()).throw(RuntimeError("secret bearer capsule")))
    assert s.main(["bootstrap"]) == 1
    result = capsys.readouterr()
    assert "STOP" in result.err and "secret" not in result.err and "capsule" not in result.err


def test_anonymous_unit_absence_is_not_cleanup_proof(monkeypatch, capsule):
    client = SimpleNamespace(poll=lambda: 0, returncode=0)
    monkeypatch.setattr(s, "load_receipt", lambda *a: {"returncode": 0})
    monkeypatch.setattr(s, "unit_facts", lambda *a, **k: {"LoadState": "not-found"})
    with pytest.raises(s.ServiceError, match="terminal proof"):
        s.reconcile_client(capsule, client, {}, {"signal": None})


def test_actual_child_failure_not_replaced_by_client_success(monkeypatch, capsule):
    client = SimpleNamespace(poll=lambda: 0, returncode=0)
    monkeypatch.setattr(s, "load_receipt", lambda *a: {"returncode": 7, "owned_child_reaped": True})
    monkeypatch.setattr(s, "unit_facts", lambda *a, **k: {"LoadState": "not-found"})
    with pytest.raises(s.ServiceError, match="disagree"):
        s.reconcile_client(capsule, client, {}, {"signal": None})


@pytest.mark.parametrize("failure", ["id", "invocation", "pid"])
def test_foreign_unit_not_owned(monkeypatch, capsule, failure):
    facts = {"Id": s.unit_name(capsule["binding"]), "InvocationID": "a" * 32, "MainPID": "123"}
    facts[{"id": "Id", "invocation": "InvocationID", "pid": "MainPID"}[failure]] = "foreign"
    monkeypatch.setattr(s.os, "pidfd_open", lambda *_: pytest.fail("opened unowned PID"))
    with pytest.raises(s.ServiceError, match="ownership"):
        s.owned_watcher(capsule, facts)


def test_bootstrap_source_binding_before_import(monkeypatch, capsule, tmp_path):
    capsule["repo"] = str(tmp_path)
    capsule["cwd"] = str(tmp_path)
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / ".github/scripts/forge_ci"
    directory.mkdir(parents=True)
    names = ("__init__", "facts", "launch", "admission", "setup_policy", "controller", "outcomes", "payload", "probes", "pytest_observer", "user_service")
    helpers = {}
    for name in names:
        relative = ".github/scripts/forge_ci/" + name + ".py"
        (tmp_path / relative).write_bytes(b"# fixture\n")
        helpers[relative] = hashlib.sha256(b"# fixture\n").hexdigest()
    capsule["source"]["helper_sha256"] = helpers
    receipt_path = tmp_path / "launch-bootstrap.json"
    capsule["receipt"] = str(receipt_path)
    receipt_path.write_bytes(s.canonical({"binding": capsule["binding"], "source": capsule["source"]}))
    capsule["entrypoint"] = s.entrypoint_identity(tmp_path, receipt_path)
    monkeypatch.setattr(s.sys, "modules", {name: module for name, module in sys.modules.items() if not name.startswith("forge_ci")})
    s.verify_checkout_imports(capsule)
    (directory / "launch.py").write_bytes(b"# injected\n")
    with pytest.raises(s.ServiceError, match="helper bytes"):
        s.verify_checkout_imports(capsule)


def test_exact_not_found_query_keeps_empty_fields(monkeypatch, capsule):
    unit = s.unit_name(capsule["binding"])
    facts = {"Id": unit, "LoadState": "not-found", "ActiveState": "inactive", "SubState": "dead", "MainPID": "0",
             "InvocationID": "", "ControlGroup": "", "Result": "success", "ExecMainCode": "0", "ExecMainStatus": "0"}
    calls = []
    def metadata(argv, env, timeout=5):
        calls.append(argv)
        return "".join(key + "=" + value + "\n" for key, value in facts.items()).encode()
    monkeypatch.setattr(s, "metadata", metadata)
    assert s.unit_facts(unit, {}) == facts
    assert calls[0] == s.systemctl("show", "--all", "--property=" + ",".join(sorted(s.UNIT_FIELDS)), "--", unit)
    facts["MainPID"] = "123"
    with pytest.raises(s.ServiceError, match="ambiguous"):
        s.unit_facts(unit, {})
    facts["MainPID"] = "0"
    facts.pop("InvocationID")
    with pytest.raises(s.ServiceError, match="missing"):
        s.unit_facts(unit, {})


@pytest.mark.parametrize("failure", ["missing", "foreign_uid", "foreign_gid", "not_socket", "mode", "symlink"])
def test_manager_object_admission_fails_closed(monkeypatch, failure):
    import stat
    owner = {"uid": 1001, "gid": 1001}
    def info(path):
        if failure == "missing":
            raise FileNotFoundError("missing runtime")
        is_socket = str(path) != "/run/user/1001"
        mode = (stat.S_IFSOCK | 0o600) if is_socket else (stat.S_IFDIR | 0o700)
        if failure == "not_socket" and is_socket:
            mode = stat.S_IFREG | 0o600
        if failure == "mode" and not is_socket:
            mode = stat.S_IFDIR | 0o777
        return SimpleNamespace(st_uid=1002 if failure == "foreign_uid" else 1001,
                               st_gid=1002 if failure == "foreign_gid" else 1001, st_mode=mode)
    monkeypatch.setattr(Path, "resolve", lambda self, **kwargs: Path("/elsewhere") if failure == "symlink" else self)
    monkeypatch.setattr(Path, "lstat", info)
    with pytest.raises((s.ServiceError, OSError)):
        s.manager_environment(owner)


def test_exact_manager_environment_no_ambient_secrets(monkeypatch):
    import stat
    monkeypatch.setattr(Path, "resolve", lambda self, **kwargs: self)
    monkeypatch.setattr(Path, "lstat", lambda self: SimpleNamespace(st_uid=1001, st_gid=1001,
                       st_mode=(stat.S_IFDIR | 0o700) if str(self) == "/run/user/1001" else (stat.S_IFSOCK | 0o600)))
    result = s.manager_environment({"uid": 1001, "gid": 1001})
    assert result == {**s.BOOT_ENV, "XDG_RUNTIME_DIR": "/run/user/1001", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1001/bus"}


@pytest.mark.parametrize("failure", [None, "old_system_ancestry", "different_thread", "wrong_unit", "mount", "readonly", "foreign", "controllers"])
def test_ancestry_exact_finite_paths(monkeypatch, capsule, failure):
    from forge_ci import probes
    uid = os.getuid()
    root = f"/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service"
    source = root.removeprefix("/sys/fs/cgroup") + "/app.slice/" + s.unit_name(capsule["binding"])
    if failure == "old_system_ancestry":
        source = "/system.slice/hosted-compute-agent.service"
    elif failure == "wrong_unit":
        source = root.removeprefix("/sys/fs/cgroup") + "/other.service"
    reads, observations = [], []
    def read(path, limit):
        reads.append(str(path))
        if str(path).endswith("/cgroup"):
            return ("0::" + ("/different" if failure == "different_thread" and "/task/" in str(path) else source) + "\n").encode()
        if str(path).endswith("/mountinfo"):
            return ("29 23 0:26 /bad /sys/fs/cgroup rw - cgroup2 cgroup rw\n" if failure == "mount" else
                    "29 23 0:26 / /sys/fs/cgroup rw - cgroup2 cgroup rw\n").encode()
        return b"memory\n" if failure == "controllers" else b"memory pids\n"
    def details(path):
        observations.append(path)
        return {"uid": uid + (failure == "foreign"), "gid": os.getgid(), "mode": 0o40755, "device": 4, "inode": 10,
                "effective_write_access": failure != "readonly"}
    monkeypatch.setattr(s, "read_regular", read)
    monkeypatch.setattr(probes, "_diagnostic_metadata", details)
    if failure:
        with pytest.raises((s.ServiceError, ValueError)):
            s.ancestry(capsule)
    else:
        result = s.ancestry(capsule)
        assert result["paths"]["common_ancestor"] == root and len(observations) == 6
        assert all(path in {root, root + "/cgroup.procs", "/sys/fs/cgroup" + source,
                            "/sys/fs/cgroup" + source + "/cgroup.procs"} for path in observations)
    assert not any("forge-uncreated" in path for path in reads + observations)


@pytest.mark.parametrize("failure", ["label", "capabilities", "nnp", "identity"])
def test_runner_label_capabilities_and_restrictions(monkeypatch, failure):
    owner = s.process_identity(os.getpid())
    monkeypatch.setattr(s, "process_identity", lambda pid: {**owner, "uid": owner["uid"] + 1} if failure == "identity" else owner)
    raw = "NoNewPrivs:\t" + ("1" if failure == "nnp" else "0") + "\n"
    raw += "".join(name + ":\t" + ("0000000000000001" if failure == "capabilities" and name == "CapEff" else "0" * 16) + "\n"
                   for name in ("CapEff", "CapPrm", "CapInh", "CapAmb"))
    monkeypatch.setattr(s, "read_regular", lambda path, limit: raw.encode() if str(path).endswith("status") else
                        b"confined" if failure == "label" else b"unconfined")
    with pytest.raises(s.ServiceError):
        s.require_runner()


def prepare_mock_launcher(monkeypatch, capsule, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    capsule["repo"] = capsule["cwd"] = str(repo)
    capsule["evidence"] = str(evidence / "qualification")
    capsule["receipt"] = str(evidence / "launch-bootstrap.json")
    capsule["environment"].update(GITHUB_WORKSPACE=str(repo), EVIDENCE=str(evidence))
    monkeypatch.setattr(s.sys, "executable", s.PROVIDER)
    monkeypatch.setattr(s.sys, "version_info", (3, 12, 14))
    monkeypatch.setattr(s.os, "environ", capsule["environment"])
    monkeypatch.setattr(s, "require_runner", lambda: capsule["owner"])
    monkeypatch.setattr(s, "local_receipt", lambda *a: {"binding": capsule["binding"], "source": capsule["source"]})
    monkeypatch.setattr(s, "entrypoint_identity", lambda *a: capsule["entrypoint"])
    monkeypatch.setattr(s, "manager_environment", lambda *a: s.BOOT_ENV)
    monkeypatch.setattr(s, "unit_facts", lambda *a, **k: {"LoadState": "not-found"})
    def metadata(argv, environment, timeout=5):
        if "--property=Version,SystemState" in argv:
            return b"Version=255 (255.4-ubuntu)\nSystemState=running\n"
        if "--help" in argv:
            return b"--expand-environment=BOOL --service-type=TYPE --pipe --wait"
        if "show-environment" in argv:
            return b"SECRET=do-not-disclose\n"
        pytest.fail("unexpected metadata action")
    monkeypatch.setattr(s, "metadata", metadata)
    return repo, evidence


@pytest.mark.parametrize("moment", ["before_popen", "during_popen", "during_capsule"])
def test_startup_cancellation_with_explicit_socketpair(monkeypatch, capsule, tmp_path, moment):
    repo, evidence = prepare_mock_launcher(monkeypatch, capsule, tmp_path)
    created, sent, reconciled = [], [], []
    client = SimpleNamespace(poll=lambda: 0, returncode=1)
    if moment == "before_popen":
        def unit_facts(*a, **k):
            os.kill(os.getpid(), signal.SIGTERM)
            return {"LoadState": "not-found"}
        monkeypatch.setattr(s, "unit_facts", unit_facts)
    def popen(argv, **kwargs):
        fd = kwargs["stdin"]
        assert isinstance(fd, socket.socket) and fd.family == socket.AF_UNIX and fd.type == socket.SOCK_STREAM
        assert kwargs["close_fds"] is True and "pass_fds" not in kwargs
        created.append(argv)
        if moment == "during_popen":
            os.kill(os.getpid(), signal.SIGTERM)
        return client
    monkeypatch.setattr(subprocess, "Popen", popen)
    real_sendall = socket.socket.sendall
    def sendall(self, data, *args):
        sent.append(s.parse_capsule(data))
        if moment == "during_capsule":
            os.kill(os.getpid(), signal.SIGTERM)
        # The mock service has no endpoint; no actual send is necessary here.
    monkeypatch.setattr(socket.socket, "sendall", sendall)
    def reconcile(c, observed_client, env, cancelled):
        reconciled.append(cancelled["signal"])
        assert observed_client is client
        return 1
    monkeypatch.setattr(s, "reconcile_client", reconcile)
    if moment == "before_popen":
        with pytest.raises(s.ServiceError, match="before service"):
            s.launcher(evidence / "launch-bootstrap.json", repo, evidence / "qualification")
        assert not created and not sent and not reconciled
    else:
        assert s.launcher(evidence / "launch-bootstrap.json", repo, evidence / "qualification") == 1
        assert len(created) == len(sent) == len(reconciled) == 1 and reconciled == [signal.SIGTERM]
    assert not (evidence / "qualification").exists()
    monkeypatch.setattr(socket.socket, "sendall", real_sendall)


@pytest.mark.parametrize("failure", ["collision", "old_manager", "unreachable", "missing_flag"])
def test_launcher_preflight_stops_before_service_creation(monkeypatch, capsule, tmp_path, failure):
    repo, evidence = prepare_mock_launcher(monkeypatch, capsule, tmp_path)
    real_metadata = s.metadata
    if failure == "collision":
        monkeypatch.setattr(s, "unit_facts", lambda *a, **k: {"LoadState": "loaded"})
    else:
        def metadata(argv, environment, timeout=5):
            if failure == "unreachable":
                raise s.ServiceError("metadata command failed")
            if failure == "old_manager" and "--property=Version,SystemState" in argv:
                return b"Version=254\nSystemState=running\n"
            if failure == "missing_flag" and "--help" in argv:
                return b"--pipe --wait --service-type=TYPE"
            return real_metadata(argv, environment, timeout)
        monkeypatch.setattr(s, "metadata", metadata)
    with pytest.raises(s.ServiceError):
        s.launcher(evidence / "launch-bootstrap.json", repo, evidence / "qualification")
    assert not (evidence / "qualification").exists()


def test_outer_cancellation_signals_verified_main_before_owned_unit_stop(monkeypatch, capsule):
    events, tick = [], [0]
    class Client:
        returncode = 1
        def poll(self):
            return None if tick[0] < 4 else 1
    def advance(seconds):
        tick[0] += 1
    monkeypatch.setattr(s.time, "sleep", advance)
    monkeypatch.setattr(s.time, "monotonic", lambda: tick[0] * 100)
    monkeypatch.setattr(s, "remaining", lambda *a: 1000)
    facts = {"Id": s.unit_name(capsule["binding"]), "InvocationID": "a" * 32, "MainPID": "321", "LoadState": "loaded", "ActiveState": "active"}
    def unit(*a, **k):
        return facts if tick[0] < 4 else {"LoadState": "not-found"}
    monkeypatch.setattr(s, "unit_facts", unit)
    fd = os.open("/dev/null", os.O_RDONLY)
    monkeypatch.setattr(s, "owned_watcher", lambda *a: events.append("ownership") or fd)
    monkeypatch.setattr(signal, "pidfd_send_signal", lambda handle, sig: events.append(("main_signal", handle, sig)))
    monkeypatch.setattr(s, "metadata", lambda argv, env, **k: events.append(("stop", argv)) or b"")
    monkeypatch.setattr(s, "load_receipt", lambda *a: {"returncode": 1, "owned_child_reaped": True, "cancel_reason": "signal"})
    monkeypatch.setattr(s, "receipt", lambda *a: events.append("receipt"))
    assert s.reconcile_client(capsule, Client(), {}, {"signal": signal.SIGINT}) == 1
    assert events[:2] == ["ownership", ("main_signal", fd, signal.SIGTERM)]
    assert events[2] == ("stop", s.systemctl("stop", "--no-block", "--", s.unit_name(capsule["binding"])))
    assert events[3] == "receipt" and len(events) == 4


def test_exhausted_outer_reserve_never_starts_metadata(monkeypatch, capsule):
    monkeypatch.setattr(s, "remaining", lambda *a: 5)
    with pytest.raises(s.ServiceError, match="outer client deadline"):
        s.reconcile_client(capsule, SimpleNamespace(poll=lambda: None), {}, {"signal": signal.SIGTERM})


def test_all_action_budgets_clamp_to_original_clock(monkeypatch, capsule):
    monkeypatch.setattr(s, "remaining", lambda clock, seconds: .02)
    assert s.budget(capsule["clock"]) == .02
    monkeypatch.setattr(s, "remaining", lambda clock, seconds: 0)
    with pytest.raises(s.ServiceError, match="exhausted original"):
        s.budget(capsule["clock"])
    monkeypatch.setattr(s, "remaining", lambda clock, seconds: -1)
    with pytest.raises(s.ServiceError, match="exhausted original"):
        s.budget(capsule["clock"])


def test_final_metadata_is_not_started_after_deadline(monkeypatch, capsule):
    monkeypatch.setattr(s, "remaining", lambda *a: 0)
    monkeypatch.setattr(s, "load_receipt", lambda *a: {"returncode": 0, "owned_child_reaped": True})
    monkeypatch.setattr(s, "unit_facts", lambda *a, **k: pytest.fail("late metadata"))
    with pytest.raises(s.ServiceError, match="original deadline"):
        s.reconcile_client(capsule, SimpleNamespace(poll=lambda: 0, returncode=0), {}, {"signal": None})


def test_exception_client_reap_uses_only_original_reserve(monkeypatch, capsule, tmp_path):
    repo, evidence = prepare_mock_launcher(monkeypatch, capsule, tmp_path)
    events = []
    class Client:
        def poll(self):
            return None
        def kill(self):
            events.append("kill-owned-client")
        def wait(self, timeout):
            events.append(("wait", timeout))
            return -signal.SIGKILL
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: Client())
    monkeypatch.setattr(socket.socket, "sendall", lambda *a: None)
    def reconcile(*args):
        monkeypatch.setattr(s, "remaining", lambda *a: .125)
        raise s.ServiceError("failed reconciliation")
    monkeypatch.setattr(s, "reconcile_client", reconcile)
    with pytest.raises(s.ServiceError, match="failed reconciliation"):
        s.launcher(evidence / "launch-bootstrap.json", repo, evidence / "qualification")
    assert events == ["kill-owned-client", ("wait", .125)]


def test_unauthenticated_capsule_never_writes_receipts(monkeypatch, capsule):
    monkeypatch.setattr(s.os, "environ", s.BOOT_ENV)
    monkeypatch.setattr(s.sys, "executable", "/usr/bin/python3")
    monkeypatch.setattr(s.sys, "flags", SimpleNamespace(isolated=1, no_site=1, dont_write_bytecode=1))
    monkeypatch.setattr(s, "read_capsule", lambda *a: capsule)
    monkeypatch.setattr(s, "require_runner", lambda: capsule["owner"])
    monkeypatch.setattr(s, "peer_owner", lambda *a: (_ for _ in ()).throw(s.ServiceError("unauthenticated peer")))
    monkeypatch.setattr(s, "receipt", lambda *a: pytest.fail("untrusted capsule produced external receipt"))
    monkeypatch.setattr(s.os, "dup", lambda fd: 12345)
    monkeypatch.setattr(s.os, "close", lambda fd: None)
    monkeypatch.setattr(s.socket, "socket", lambda **kwargs: SimpleNamespace(close=lambda: None))
    with pytest.raises(s.ServiceError, match="unauthenticated peer"):
        s.bootstrap()


def test_public_gate_registry_covers_only_fixed_local_messages():
    import ast
    import re
    tree = ast.parse(Path(s.__file__).read_text())
    expected = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id == "need" and len(node.args) == 2 and isinstance(node.args[1], ast.Constant):
            expected.add(node.args[1].value)
        elif node.func.id == "ServiceError" and len(node.args) == 1 and isinstance(node.args[0], ast.Constant):
            expected.add(node.args[0].value)
        elif node.func.id == "keys" and len(node.args) == 3 and isinstance(node.args[2], ast.Constant):
            expected.add("invalid " + node.args[2].value + " fields")
    assert set(s.PUBLIC_GATES) == expected
    assert len(set(s.PUBLIC_GATES.values())) == len(expected)
    assert all(re.fullmatch(r"US[0-9]{3}", gate) and gate != "US000" for gate in s.PUBLIC_GATES.values())
    for message, gate in s.PUBLIC_GATES.items():
        assert s.public_gate(s.ServiceError(message)) == gate


@pytest.mark.parametrize("message", ["unclean bootstrap environment", "invalid binding fields", "foreign capsule peer",
                                     "wrong service or delegated ancestry", "startup deadline", "unit already exists"])
def test_known_public_gate_in_pre_admission_stderr(monkeypatch, capsys, message):
    monkeypatch.setattr(s, "bootstrap", lambda: (_ for _ in ()).throw(s.ServiceError(message)))
    assert s.main(["bootstrap"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "STOP: fixed user-service admission, lifecycle or evidence failed; gate=" + s.PUBLIC_GATES[message] + "\n"
    assert not captured.out and message not in captured.err


@pytest.mark.parametrize("error", [s.ServiceError("secret capsule bearer"), s.ServiceError("unclean bootstrap environment SECRET"),
                                   s.ServiceError("startup deadline", "SECRET"), RuntimeError("startup deadline"),
                                   RuntimeError("secret manager value")])
def test_unknown_exception_uses_one_fixed_gate_without_values(monkeypatch, capsys, error):
    monkeypatch.setattr(s, "bootstrap", lambda: (_ for _ in ()).throw(error))
    assert s.public_gate(error) == "US000"
    assert s.main(["bootstrap"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "STOP: fixed user-service admission, lifecycle or evidence failed; gate=US000\n"
    assert "secret" not in captured.err.lower() and "startup deadline" not in captured.err


def test_public_gate_never_calls_exception_stringification():
    class PrivateException(s.ServiceError):
        def __str__(self):
            raise AssertionError("attempted to stringify untrusted exception")
    assert s.public_gate(PrivateException("startup deadline")) == "US000"


@pytest.mark.parametrize("known", [True, False])
def test_launcher_stop_receipt_has_redacted_public_gate(monkeypatch, capsule, tmp_path, known):
    repo, evidence = prepare_mock_launcher(monkeypatch, capsule, tmp_path)
    message = "unit already exists" if known else "secret-capsule-value"
    monkeypatch.setattr(s, "manager_environment", lambda *a: (_ for _ in ()).throw(s.ServiceError(message)))
    with pytest.raises(s.ServiceError):
        s.launcher(evidence / "launch-bootstrap.json", repo, evidence / "qualification")
    raw = (evidence / "service-launcher-stop.json").read_text()
    result = json.loads(raw)
    assert result["gate"] == (s.PUBLIC_GATES[message] if known else "US000")
    assert result["error_type"] == "ServiceError" and result["status"] == "STOP" and result["qualified"] is False
    assert message not in raw and "secret-capsule-value" not in raw


@pytest.mark.parametrize("known", [True, False])
def test_admitted_bootstrap_stop_receipt_has_redacted_public_gate(monkeypatch, capsule, tmp_path, known):
    capsule["repo"] = str(Path(s.__file__).resolve().parents[3])
    capsule["evidence"] = str(tmp_path / "qualification")
    monkeypatch.setattr(s.os, "environ", s.BOOT_ENV)
    monkeypatch.setattr(s.sys, "executable", "/usr/bin/python3")
    monkeypatch.setattr(s.sys, "flags", SimpleNamespace(isolated=1, no_site=1, dont_write_bytecode=1))
    monkeypatch.setattr(s, "read_capsule", lambda *a: capsule)
    monkeypatch.setattr(s, "require_runner", lambda: capsule["owner"])
    monkeypatch.setattr(s, "peer_owner", lambda *a: 12346)
    monkeypatch.setattr(s, "verify_checkout_imports", lambda *a: None)
    monkeypatch.setattr(s, "local_receipt", lambda *a: {"binding": capsule["binding"], "source": capsule["source"]})
    monkeypatch.setattr(s, "entrypoint_identity", lambda *a: capsule["entrypoint"])
    monkeypatch.setattr(s, "ancestry", lambda *a: {})
    monkeypatch.setattr(s, "ready", lambda *a: False)
    monkeypatch.setattr(s.os, "dup", lambda fd: 12345)
    monkeypatch.setattr(s.os, "close", lambda fd: None)
    monkeypatch.setattr(s.socket, "socket", lambda **kwargs: SimpleNamespace(close=lambda: None))
    message = "cancelled before child spawn" if known else "secret-manager-value"
    monkeypatch.setattr(s, "watch_child", lambda *a: (_ for _ in ()).throw(s.ServiceError(message)))
    with pytest.raises(s.ServiceError):
        s.bootstrap()
    raw = (tmp_path / "service-bootstrap-stop.json").read_text()
    result = json.loads(raw)
    assert result["gate"] == (s.PUBLIC_GATES[message] if known else "US000")
    assert result["status"] == "STOP" and result["qualified"] is False
    assert message not in raw and "secret-manager-value" not in raw
    assert not (tmp_path / "qualification").exists()


def test_real_pre_admission_environment_failure_is_attributable(monkeypatch, capsys):
    monkeypatch.setattr(s.os, "environ", {**s.BOOT_ENV, "PRIVATE_MANAGER_KEY": "SECRET"})
    monkeypatch.setattr(s, "read_capsule", lambda *a: pytest.fail("read capsule before environment admission"))
    assert s.main(["bootstrap"]) == 1
    result = capsys.readouterr()
    assert "gate=" + s.PUBLIC_GATES["unclean bootstrap environment"] in result.err
    assert "SECRET" not in result.err and "PRIVATE_MANAGER_KEY" not in result.err


@pytest.mark.parametrize("elapsed,accepted", [(0, True), (599, True), (600, True), (601, False), (-1, False)])
def test_authenticated_job_budget_reserves_full_qualification_and_artifacts(capsule, elapsed, accepted):
    binding = capsule["binding"]
    now = binding["job_started_ns"] + elapsed * s.NS
    if accepted:
        s.require_job_headroom(binding, now)
    else:
        with pytest.raises(s.ServiceError, match="authenticated job headroom"):
            s.require_job_headroom(binding, now)


def test_remaining_clamps_to_authenticated_job_artifact_deadline(monkeypatch, capsule):
    clock = capsule["clock"]
    now = clock["artifact_deadline_utc_ns"] - 5 * s.NS
    monkeypatch.setattr(s.time, "time_ns", lambda: now)
    monkeypatch.setattr(s.time, "monotonic_ns", lambda: clock["started_monotonic_ns"])
    # Move the local relative deadline forward only in this pure test to show
    # it cannot extend the externally authenticated artifact reservation.
    clock["deadline_utc_ns"] = now + 100 * s.NS
    clock["started_utc_ns"] = now
    assert s.remaining(clock, s.CLIENT_SECONDS) == 5


@pytest.mark.parametrize("field", ["source_sha256", "workflow_sha256", "tree_oid", "candidate_sha"])
def test_source_capsule_rejects_malformed_full_source(capsule, field):
    capsule["source"][field] = "wrong"
    with pytest.raises(s.ServiceError):
        s.parse_capsule(s.canonical(capsule))


@pytest.mark.parametrize("field", ["job_id", "workflow_id", "run_number", "job_started_ns"])
@pytest.mark.parametrize("value", [True, 0, -1, "123", 2**63])
def test_external_numeric_job_binding_is_strict(capsule, field, value):
    capsule["binding"][field] = value
    with pytest.raises(s.ServiceError):
        s.parse_capsule(s.canonical(capsule))


def test_exhausted_authenticated_job_budget_stops_with_evidence_before_service(monkeypatch, capsule, tmp_path):
    repo, evidence = prepare_mock_launcher(monkeypatch, capsule, tmp_path)
    capsule["binding"]["job_started_ns"] = time.time_ns() - 601 * s.NS
    monkeypatch.setattr(s, "manager_environment", lambda *a: pytest.fail("started manager operation with insufficient job budget"))
    with pytest.raises(s.ServiceError, match="authenticated job headroom"):
        s.launcher(evidence / "launch-bootstrap.json", repo, evidence / "qualification")
    record = json.loads((evidence / "service-launcher-stop.json").read_bytes())
    assert record["status"] == "STOP" and record["qualified"] is False
    assert record["gate"] == s.PUBLIC_GATES["insufficient authenticated job headroom"]
    assert record["binding"]["job_id"] == 444
    assert record["source"] == capsule["source"]


def test_live_receipt_recheck_does_not_claim_activation(monkeypatch, capsule, tmp_path):
    from forge_ci import launch
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    environment = {**capsule["environment"], "EVIDENCE": str(tmp_path)}
    document = {"binding": capsule["binding"], "source": capsule["source"]}
    calls = []
    monkeypatch.setattr(launch, "load_receipt", lambda path: calls.append("load") or document)
    monkeypatch.setattr(launch, "inspect_checkout", lambda path, sha: calls.append("source") or capsule["source"])
    monkeypatch.setattr(launch, "read_regular", lambda *a, **k: b"{}")
    monkeypatch.setattr(launch, "validate_local_launch", lambda *a: calls.append("local") or document)
    assert s.local_receipt(repo, tmp_path / "launch-bootstrap.json", environment) == document
    assert calls == ["load", "source", "local"]


@pytest.mark.parametrize("target", ["binding", "source"])
def test_live_receipt_recheck_rejects_provider_or_source_drift(monkeypatch, capsule, tmp_path, target):
    from forge_ci import launch
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    environment = {**capsule["environment"], "EVIDENCE": str(tmp_path)}
    document = {"binding": capsule["binding"], "source": capsule["source"]}
    current = copy.deepcopy(document)
    current[target]["job_id" if target == "binding" else "source_sha256"] = 999 if target == "binding" else "e" * 64
    monkeypatch.setattr(launch, "load_receipt", lambda path: document)
    monkeypatch.setattr(launch, "inspect_checkout", lambda path, sha: capsule["source"])
    monkeypatch.setattr(launch, "read_regular", lambda *a, **k: b"{}")
    monkeypatch.setattr(launch, "validate_local_launch", lambda *a: current)
    with pytest.raises(s.ServiceError, match="source binding mismatch"):
        s.local_receipt(repo, tmp_path / "launch-bootstrap.json", environment)


@pytest.mark.parametrize("change", ["schema_bool", "binding_bool", "binding_extra", "job_changed", "source_changed", "receipt_changed"])
def test_service_receipt_strictly_binds_schema_provider_and_source(capsule, tmp_path, change):
    capsule["evidence"] = str(tmp_path / "qualification")
    s.receipt(capsule, "terminal", {"status": "EXITED", "returncode": 0})
    path = tmp_path / "service-terminal.json"
    record = json.loads(path.read_bytes())
    if change == "schema_bool":
        record["schema_version"] = True
    elif change == "binding_bool":
        record["binding"]["run_attempt"] = True
    elif change == "binding_extra":
        record["binding"]["nonce"] = "a" * 32
    elif change == "job_changed":
        record["binding"]["job_id"] += 1
    elif change == "source_changed":
        record["source"]["source_sha256"] = "e" * 64
    else:
        record["receipt_sha256"] = "e" * 64
    path.write_bytes(s.canonical(record))
    with pytest.raises(s.ServiceError):
        s.load_receipt(capsule, "terminal")
