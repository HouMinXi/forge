"""Pure and mocked qualification tests: never exercise real sandbox/policy."""

from __future__ import annotations

import copy
import errno
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from forge_ci import payload, probes  # noqa: E402
from forge_ci.payload import ProbeError  # noqa: E402


def process_snapshot(pid=4, *, ppid=2, userns="user:[11]", netns="net:[12]", admin=False):
    status = {
        "NoNewPrivs": "1",
        "CapEff": f"{payload.CAP_SYS_ADMIN if admin else 0:016x}",
        "CapPrm": "0000000000000000",
        "CapInh": "0000000000000000",
        "CapAmb": "0000000000000000",
        "CapBnd": "000000ffffffffff",
        "Threads": "1",
    }
    return {
        "pid": pid,
        "ppid": ppid,
        "uid": 1001,
        "euid": 1001,
        "gid": 1001,
        "egid": 1001,
        "userns": userns,
        "netns": netns,
        "pidns": "pid:[13]",
        "label": payload.EXPECTED_LABEL,
        "status": status,
        "status_raw": "".join(key + ":\t" + value + "\n" for key, value in status.items()),
        "utc_ns": 1700000000000000000,
        "monotonic_ns": 1000000000,
    }


def audit(
    pid=456,
    *,
    cap=12,
    name="net_admin",
    profile="unprivileged_userns",
    instant="1700000000.123",
    serial=5,
):
    return f'audit: type=1400 audit({instant}:{serial}): apparmor="DENIED" operation="capable" class="cap" profile="{profile}" pid={pid} comm="bwrap" capability={cap} capname="{name}"'


def negative():
    original = probes.production_probe_argv()
    i = original.index("--")
    raw = b'{"child-pid":456}'
    return {
        "argv": original[:i] + ["--info-fd", "7"] + original[i:],
        "original_argv": original,
        "returncode": 1,
        "wrapper_pid": 111,
        "stderr": "bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted\n",
        "info_raw_hex": raw.hex(),
        "info_bytes": len(raw),
        "info": {"child-pid": 456},
        "started": {"utc_ns": 1700000000123000000, "monotonic_ns": 100},
        "ended": {"utc_ns": 1700000000124000000, "monotonic_ns": 200},
        "audit": [audit()],
    }


def ip_result(options, values):
    return {
        "argv": ["/usr/bin/ip", "-j", *options],
        "returncode": 0,
        "stdout": json.dumps(values),
        "stdout_hex": json.dumps(values).encode().hex(),
        "stderr": "",
        "json": values,
    }


def network(*, disabled=False):
    result = {
        "ip": "/usr/bin/ip",
        "before_netns": "net:[12]",
        "after_netns": "net:[12]",
        "ipv6_disabled": disabled,
        "ipv6_disable": {
            "/proc/sys/net/ipv6/conf/" + key + "/disable_ipv6": "1" if disabled else "0"
            for key in ("all", "default", "lo")
        },
        "links": ip_result(
            ["link", "show"], [{"ifname": "lo", "flags": ["LOOPBACK", "UP"], "link_type": "loopback"}]
        ),
    }
    for family in (4,) if disabled else (4, 6):
        result[f"addresses{family}"] = ip_result(
            [f"-{family}", "address", "show"],
            [{"ifname": "lo", "addr_info": [{"local": "127.0.0.1" if family == 4 else "::1"}]}],
        )
        result[f"routes{family}"] = ip_result([f"-{family}", "route", "show", "table", "all"], [])
    return result


def witness_record():
    return {
        "token": "token",
        "namespace_pid": 5,
        "before": process_snapshot(5, ppid=2),
        "after_user": process_snapshot(5, ppid=2, userns="user:[99]", admin=True),
        "after_net": process_snapshot(5, ppid=2, userns="user:[99]", admin=True),
        "net_errno": errno.EPERM,
        "net_started": {"utc_ns": 1700000000123000000, "monotonic_ns": 300},
        "net_ended": {"utc_ns": 1700000000124000000, "monotonic_ns": 400},
    }


def boundary():
    return {
        "returncode": 0,
        "token": "token",
        "caller": process_snapshot(netns="net:[1]"),
        "witness_mapping": {"host_pid": 500, "namespace_pid": 5},
        "audit": [audit(500, cap=21, name="sys_admin", profile="unpriv_bwrap")],
        "payload": {
            "initial": process_snapshot(2, ppid=1),
            "reexec": process_snapshot(4, ppid=2),
            "reexec_command": {
                "argv": [
                    "/usr/bin/python3",
                    "/workspace/probe.py",
                    "reexec",
                    "--workspace",
                    "/workspace",
                ],
                "returncode": 0,
            },
            "network": network(),
            "witness": witness_record(),
            "witness_command": {
                "argv": [
                    "/usr/bin/python3",
                    "/workspace/probe.py",
                    "witness",
                    "--workspace",
                    "/workspace",
                    "--token",
                    "token",
                    "--deadline",
                    "20.0",
                ],
                "returncode": 0,
            },
        },
    }


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{}",
        b'{"child-pid":0}',
        b'{"child-pid":-1}',
        b'{"child-pid":true}',
        b'{"child-pid":1.0}',
        b'{"child-pid":"5"}',
        b'{"child-pid":1,"child-pid":2}',
        b'{"child-pid":1',
        b'{"child-pid":1} {}',
        b"[]",
        b"\xff",
        b'{"child-pid":1,"x":NaN}',
        b" " * 4097,
    ],
)
def test_info_fd_rejects_missing_ambiguous_and_unbounded(raw):
    with pytest.raises(ProbeError):
        probes.parse_info_record(raw)


def test_info_fd_accepts_optional_namespace_fields():
    assert probes.parse_info_record(b'{"child-pid":123,"user-namespace":99}\n')["child-pid"] == 123


def test_original_production_flags_match_actual_supervisor():
    from code_forge.mutation_engines.isolate import SandboxSpec, Supervisor

    spec = SandboxSpec(
        run_id="probe",
        command=("/usr/bin/true",),
        cwd="/workspace",
        memory_mb=64,
        pids=32,
        workspace_mb=16,
    )
    assert Supervisor(spec, "/unused")._bwrap_argv() == probes.production_probe_argv()
    assert "--info-fd" not in probes.production_probe_argv()


def test_negative_requires_native_child_not_wrapper_pid():
    record = negative()
    probes.validate_negative_control(record)
    for wrong in (record["wrapper_pid"], record["wrapper_pid"] + 1):
        record["audit"] = [audit(wrong)]
        with pytest.raises(ProbeError, match="missing or ambiguous"):
            probes.validate_negative_control(record)


@pytest.mark.parametrize(
    "change",
    [
        "no_audit",
        "duplicate_audit",
        "other_profile",
        "other_capability",
        "outside_interval",
        "changed_flags",
        "missing_refusal",
        "success",
        "truncated",
        "fd_stdout",
    ],
)
def test_negative_controls_fail_closed(change):
    record = negative()
    if change == "no_audit":
        record["audit"] = []
    elif change == "duplicate_audit":
        record["audit"] *= 2
    elif change == "other_profile":
        record["audit"] = [audit(profile="unconfined")]
    elif change == "other_capability":
        record["audit"] = [audit(cap=21, name="sys_admin")]
    elif change == "outside_interval":
        record["audit"] = [audit(instant="1700000001.123")]
    elif change == "changed_flags":
        record["argv"].remove("--unshare-net")
    elif change == "missing_refusal":
        record["stderr"] = "Operation not permitted"
    elif change == "success":
        record["returncode"] = 0
    elif change == "truncated":
        record["info_bytes"] += 1
    elif change == "fd_stdout":
        record["argv"][record["argv"].index("--info-fd") + 1] = "1"
    with pytest.raises(ProbeError):
        probes.validate_negative_control(record)


def test_audit_duplicate_key_and_unfiltered_content_rejected():
    for records in (
        [audit() + " pid=456"],
        ["unrelated private log"],
        [audit(), audit(cap=21, name="sys_admin", serial=6)],
    ):
        with pytest.raises(ProbeError):
            probes.validate_audit(
                records,
                pid=456,
                start_ns=1700000000123000000,
                end_ns=1700000000124000000,
                capability=12,
                capname="net_admin",
                profiles=("unprivileged_userns",),
            )


def test_negative_collection_uses_only_private_info_fd(tmp_path, monkeypatch):
    monkeypatch.setattr(probes, "require_runner", lambda: {"uid": 1001})

    def command(argv, timeout, *, pass_fds, env):
        assert len(pass_fds) == 1 and pass_fds[0] > 2
        assert env == {"PATH": probes.PRODUCTION_PATH}
        assert os.fstat(pass_fds[0]).st_mode & 0o077 == 0
        os.write(pass_fds[0], b'{"child-pid":456}')
        result = negative()
        result["argv"] = argv
        return result

    monkeypatch.setattr(probes, "bounded_command", command)
    result = probes.run_negative_control(tmp_path, audit_reader=lambda start, end, pid: [audit(pid)])
    probes.validate_negative_control(result)
    assert (tmp_path / "negative-command.json").is_file()
    assert (tmp_path / "negative-probe.json").is_file()


def test_already_capable_does_not_claim_refusal_or_read_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(probes, "require_runner", lambda: {"uid": 1001})

    def command(argv, timeout, *, pass_fds, env):
        os.write(pass_fds[0], b'{"child-pid":456}')
        result = negative()
        result.update(argv=argv, returncode=0)
        return result

    monkeypatch.setattr(probes, "bounded_command", command)
    result = probes.run_negative_control(
        tmp_path, audit_reader=lambda *args: pytest.fail("no audit expected")
    )
    assert result["already_capable"] is True
    with pytest.raises(ProbeError, match="expected refusal"):
        probes.validate_negative_control(result)


@pytest.mark.parametrize("disabled", [False, True])
def test_network_accepts_checked_empty_all_table_routes(disabled):
    payload.validate_network(network(disabled=disabled), "net:[1]", "net:[12]")


@pytest.mark.parametrize(
    "change",
    [
        "same_namespace",
        "moved_namespace",
        "missing_command",
        "route_failure",
        "bad_json",
        "non_array",
        "main_only",
        "external_interface",
        "empty_links",
        "gateway",
        "external_route",
        "multipath",
        "external_address",
        "ipv6_missing",
        "ipv6_falsely_disabled",
    ],
)
def test_network_facts_fail_closed(change):
    value = network()
    caller = "net:[1]"
    if change == "same_namespace":
        caller = "net:[12]"
    elif change == "moved_namespace":
        value["after_netns"] = "net:[99]"
    elif change == "missing_command":
        del value["routes4"]
    elif change == "route_failure":
        value["routes4"]["returncode"] = 2
    elif change == "bad_json":
        value["routes4"]["stdout"] = "["
    elif change == "non_array":
        value["routes4"]["stdout"] = "{}"
    elif change == "main_only":
        value["routes4"]["argv"] = ["/usr/bin/ip", "-j", "-4", "route"]
    elif change == "external_interface":
        value["links"]["json"].append({"ifname": "eth0"})
    elif change == "empty_links":
        value["links"]["json"] = []
    elif change == "gateway":
        value["routes4"]["json"] = [{"dev": "lo", "dst": "127.0.0.1", "gateway": "8.8.8.8"}]
    elif change == "external_route":
        value["routes4"]["json"] = [{"dev": "lo", "dst": "default"}]
    elif change == "multipath":
        value["routes4"]["json"] = [{"dev": "lo", "dst": "127.0.0.1", "multipath": []}]
    elif change == "external_address":
        value["addresses4"]["json"][0]["addr_info"] = [{"local": "10.0.0.1"}]
    elif change == "ipv6_missing":
        del value["routes6"]
    elif change == "ipv6_falsely_disabled":
        value["ipv6_disabled"] = True
    if change in (
        "external_interface",
        "empty_links",
        "gateway",
        "external_route",
        "multipath",
        "external_address",
    ):
        for observation in value.values():
            if isinstance(observation, dict) and "json" in observation:
                observation["stdout"] = json.dumps(observation["json"])
    with pytest.raises(ProbeError):
        payload.validate_network(value, caller, "net:[12]")


@pytest.mark.parametrize(
    "change",
    [
        "label",
        "nnp",
        "root",
        "no_capability",
        "no_userns",
        "changed_net_before",
        "net_success",
        "wrong_errno",
        "changed_net_after",
        "threads",
        "changed_pid",
    ],
)
def test_witness_requires_positive_opportunity_before_denial(change):
    value = witness_record()
    if change == "label":
        value["after_user"]["label"] = "unconfined"
    elif change == "nnp":
        value["after_user"]["status"]["NoNewPrivs"] = "0"
    elif change == "root":
        value["before"]["uid"] = 0
    elif change == "no_capability":
        value["after_user"]["status"]["CapEff"] = "0000000000000000"
    elif change == "no_userns":
        value["after_user"]["userns"] = value["before"]["userns"]
    elif change == "changed_net_before":
        value["after_user"]["netns"] = "net:[50]"
    elif change == "net_success":
        value["net_errno"] = 0
    elif change == "wrong_errno":
        value["net_errno"] = errno.EINVAL
    elif change == "changed_net_after":
        value["after_net"]["netns"] = "net:[90]"
    elif change == "threads":
        value["after_user"]["status"]["Threads"] = "2"
    elif change == "changed_pid":
        value["after_net"]["pid"] = 88
    for name in ("before", "after_user", "after_net"):
        value[name]["status_raw"] = "".join(
            key + ":\t" + field + "\n" for key, field in value[name]["status"].items()
        )
    with pytest.raises(ProbeError):
        payload.validate_witness(value)


def test_boundary_requires_audit_and_reexec():
    record = boundary()
    probes.validate_boundary_record(record)
    for modify in (
        lambda value: value.update(audit=[]),
        lambda value: value["payload"]["reexec"].update(label="bwrap (enforce)"),
        lambda value: value["payload"]["reexec"].update(pid=2),
        lambda value: value["witness_mapping"].update(host_pid=501),
        lambda value: value["payload"]["witness"].update(token="other"),
    ):
        invalid = copy.deepcopy(record)
        modify(invalid)
        with pytest.raises(ProbeError):
            probes.validate_boundary_record(invalid)


def make_proc(tmp_path, pid, nspid):
    path = tmp_path / "proc" / str(pid)
    path.mkdir(parents=True)
    (path / "status").write_text("Name:\tpython3\nNSpid:\t" + nspid + "\n")


def test_pid_mapping_uses_owned_cgroup_and_allows_host_wrapper(tmp_path):
    cg = tmp_path / "owned"
    cg.mkdir()
    (cg / "cgroup.procs").write_text("400\n401\n500\n")
    make_proc(tmp_path, 400, "400")
    make_proc(tmp_path, 401, "401\t1")
    make_proc(tmp_path, 500, "500\t5")
    make_proc(tmp_path, 999, "999\t5")  # A foreign cgroup PID must never compete.
    result = probes.map_witness_pid(cg, 5, proc_root=tmp_path / "proc")
    assert result["host_pid"] == 500 and result["nspid"] == [500, 5]


@pytest.mark.parametrize("kind", ["missing", "ambiguous", "bad_chain", "duplicate_pid", "vanished"])
def test_pid_mapping_rejects_ambiguous_or_missing_facts(tmp_path, kind):
    cg = tmp_path / "owned"
    cg.mkdir()
    (cg / "cgroup.procs").write_text("500\n501\n" if kind != "duplicate_pid" else "500\n500\n")
    make_proc(
        tmp_path,
        500,
        "500\t5"
        if kind not in ("missing", "bad_chain")
        else ("500\t6" if kind == "missing" else "123\t5"),
    )
    if kind != "vanished":
        make_proc(tmp_path, 501, "501\t5" if kind == "ambiguous" else "501\t6")
    with pytest.raises(ProbeError):
        probes.map_witness_pid(cg, 5, proc_root=tmp_path / "proc")


def test_audit_reader_queries_only_kernel_denials_and_checks_status(monkeypatch):
    captured = []

    def run(argv, timeout, **kwargs):
        captured.append(argv)
        assert timeout == 5
        assert kwargs == {"limit": probes.AUDIT_LIMIT}
        assert argv[:7] == ["/usr/bin/sudo", "-n", "--", "/usr/bin/timeout",
                            "--signal=KILL", "4s", "/usr/bin/journalctl"]
        return {
            "returncode": 0,
            "stdout": json.dumps({"_TRANSPORT": "kernel", "MESSAGE": audit()}) + "\n",
            "stdout_hex": (json.dumps({"_TRANSPORT": "kernel", "MESSAGE": audit()}) + "\n").encode().hex(),
        }

    monkeypatch.setattr(probes, "bounded_command", run)
    assert probes.read_kernel_audit(1700000000123000000, 1700000000124000000, 456) == [audit()]
    assert "-k" in captured[0] and "--kernel" not in captured[0]
    assert '--grep=apparmor="DENIED".*(bwrap|unprivileged_userns|userns_create)' in captured[0]
    assert "--since=2023-11-14 22:13:20.122000 UTC" in captured[0]
    assert "--until=2023-11-14 22:13:20.125000 UTC" in captured[0]
    monkeypatch.setattr(
        probes, "bounded_command", lambda *args, **kwargs: {"returncode": 1, "stdout": ""}
    )
    with pytest.raises(ProbeError, match="unavailable"):
        probes.read_kernel_audit(1700000000123000000, 1700000000124000000, 456)


def test_actual_witness_calls_user_then_net_alone_without_exec(tmp_path, monkeypatch):
    record = witness_record()
    observations = iter([record["before"], record["after_user"], record["after_net"]])
    monkeypatch.setattr(payload, "snapshot", lambda: next(observations))
    monkeypatch.setattr(payload.os, "getpid", lambda: 5)
    original_iterdir = Path.iterdir
    monkeypatch.setattr(
        Path,
        "iterdir",
        lambda self: iter([Path("5")]) if str(self) == "/proc/self/task" else original_iterdir(self),
    )
    monkeypatch.setattr(payload.os, "CLONE_NEWUSER", 0x10000000, raising=False)
    monkeypatch.setattr(payload.os, "CLONE_NEWNET", 0x40000000, raising=False)
    calls = []

    def unshare(flag):
        calls.append(flag)
        if flag == payload.os.CLONE_NEWNET:
            raise OSError(errno.EPERM, "denied")

    monkeypatch.setattr(payload.os, "unshare", unshare, raising=False)
    monkeypatch.setattr(
        payload.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("no exec in witness")
    )
    payload.write_json(tmp_path / "witness-release.json", {"token": "token", "namespace_pid": 5})
    result = payload.witness(tmp_path, "token", 1)
    assert calls == [payload.os.CLONE_NEWUSER, payload.os.CLONE_NEWNET]
    payload.validate_witness(result)


def test_boundary_controller_runs_production_interface_and_releases_after_mapping(tmp_path, monkeypatch):
    expected = boundary()
    monkeypatch.setattr(probes, "require_runner", lambda: expected["caller"])
    monkeypatch.setattr(probes.shutil, "which", lambda *args, **kwargs: "/usr/bin/true")
    expected["payload"]["network"]["ip"] = "/usr/bin/true"
    for value in expected["payload"]["network"].values():
        if isinstance(value, dict) and "argv" in value:
            value["argv"][0] = "/usr/bin/true"
    events = []
    holder = {}
    from code_forge.mutation_engines.isolate import SandboxSpec

    class Supervisor:
        def __init__(self, spec, root):
            holder["supervisor"] = self
            self.spec = spec
            self.cgroup_path = str(tmp_path / ("forge-" + spec.run_id))
            self.payload_pid = 400
            self.limits_readback = {
                "memory.max": str(64 * 1024 * 1024),
                "pids.max": "32",
                "memory.swap.max": "0",
            }
            self.gate_opened_monotonic_ns = 10
            self._process = SimpleNamespace(poll=lambda: None)

        def _bwrap_argv(self):
            return ["bwrap", *self.spec.command]

        def start(self):
            pass

        def wait(self, timeout):
            assert "mapped" in events
            workspace = Path(self.spec.workspace_host)
            release = payload.read_json(workspace / "witness-release.json")
            token = self.spec.command[self.spec.command.index("--token") + 1]
            assert release == {"token": token, "namespace_pid": 5}
            expected["payload"]["witness"]["token"] = token
            expected["payload"]["witness_command"]["argv"][6] = token
            payload.write_json(workspace / "payload-result.json", expected["payload"])
            self._process = SimpleNamespace(poll=lambda: 0)
            return 0

        def teardown(self):
            events.append("teardown")

    class Thread:
        def __init__(self, sup):
            self.sup = sup

        def start(self):
            self.sup.start()
            workspace = Path(self.sup.spec.workspace_host)
            token = self.sup.spec.command[self.sup.spec.command.index("--token") + 1]
            ready = witness_record()
            ready["token"] = token
            payload.write_json(workspace / "witness-ready.json", ready)

        def wait_started(self, timeout):
            pass

        def join(self, timeout):
            events.append("joined")

        def is_alive(self):
            return False

    monkeypatch.setattr(probes, "_production_types", lambda: (SandboxSpec, Supervisor, Thread))

    def mapping(path, ns_pid):
        assert not (Path(holder["supervisor"].spec.workspace_host) / "witness-release.json").exists()
        events.append("mapped")
        return {"namespace_pid": ns_pid, "host_pid": 500}

    monkeypatch.setattr(probes, "map_witness_pid", mapping)
    result = probes.run_boundary_probe("/owned", tmp_path, audit_reader=lambda *args: expected["audit"])
    assert result["returncode"] == 0
    assert events == ["mapped", "teardown", "joined"]
    assert (tmp_path / "boundary-probe.json").is_file()
    assert (tmp_path / "boundary-workspace" / "payload-result.json").is_file()


@pytest.mark.parametrize("failure", [None, "overflow", "deadline"])
def test_bounded_command_drains_both_pipes_or_kills_and_reaps(monkeypatch, failure):
    handles = []
    for content in (b"hello", b"stderr"):
        reader, writer = os.pipe()
        os.write(writer, content)
        os.close(writer)
        handles.append(os.fdopen(reader, "rb"))
    events = []

    class Process:
        pid = 456
        stdout, stderr = handles
        returncode = None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            for handle in handles:
                handle.close()

        def kill(self):
            events.append("kill")
            self.returncode = -9

        def wait(self, timeout):
            events.append("wait")
            self.returncode = self.returncode if self.returncode is not None else 0
            return self.returncode

    monkeypatch.setattr(payload.subprocess, "Popen", lambda *args, **kwargs: Process())
    if failure == "deadline":
        ticks = iter([0, 2])
        monkeypatch.setattr(payload.time, "monotonic", lambda: next(ticks))
    record = payload.bounded_command(["/unused/mock"], 1, limit=5 if failure == "overflow" else 100)
    assert all(handle.closed for handle in handles)
    if failure is None:
        assert events == ["wait"]
        assert record["stdout"] == "hello" and record["stderr"] == "stderr"
        assert record["stdout_hex"] == b"hello".hex() and record["returncode"] == 0
    else:
        assert events == ["kill", "wait"] and record["error"]
        assert record["returncode"] == -9


def test_ip_collection_rejects_invalid_utf8_in_original_bytes(monkeypatch):
    raw = b'[{"ifname":"lo","extra":"\xff"}]'
    record = {"argv": ["/usr/bin/ip", "-j", "link", "show"], "returncode": 0,
              "stdout": raw.decode("utf-8", errors="replace"), "stdout_hex": raw.hex()}
    monkeypatch.setattr(payload, "bounded_command", lambda *args: record)
    with pytest.raises(ProbeError, match="invalid UTF-8"):
        payload._checked_ip("/usr/bin/ip", ["link", "show"])


def test_network_validation_rejects_repaired_utf8_evidence():
    record = network()
    raw = (b'[{"ifname":"lo","flags":["LOOPBACK","UP"],'
           b'"link_type":"loopback","extra":"\xff"}]')
    record["links"].update(stdout_hex=raw.hex(), stdout=raw.decode("utf-8", errors="replace"),
                           json=json.loads(raw.decode("utf-8", errors="replace")))
    with pytest.raises(ProbeError, match="invalid UTF-8"):
        payload.validate_network(record, "net:[1]", "net:[12]")


@pytest.mark.parametrize("kind", ["missing", "invalid_hex", "whitespace", "oversized", "display_mismatch"])
def test_network_requires_bounded_exact_raw_command_evidence(kind):
    record = network()
    result = record["links"]
    if kind == "missing":
        del result["stdout_hex"]
    elif kind == "invalid_hex":
        result["stdout_hex"] = "zz"
    elif kind == "whitespace":
        result["stdout_hex"] += " "
    elif kind == "oversized":
        result["stdout_hex"] = "20" * (payload.MAX_COMMAND_OUTPUT + 1)
    else:
        result["stdout"] += " "
    with pytest.raises(ProbeError):
        payload.validate_network(record, "net:[1]", "net:[12]")


def test_audit_reader_rejects_invalid_utf8_original_bytes(monkeypatch):
    raw = (json.dumps({"_TRANSPORT": "kernel", "MESSAGE": audit(), "extra": "MARKER"})
           .encode().replace(b"MARKER", b"\xff") + b"\n")
    result = {"returncode": 0, "stdout": raw.decode("utf-8", errors="replace"), "stdout_hex": raw.hex()}
    monkeypatch.setattr(probes, "bounded_command", lambda *args, **kwargs: result)
    with pytest.raises(ProbeError, match="invalid UTF-8"):
        probes.read_kernel_audit(1700000000123000000, 1700000000124000000, 456)


@pytest.mark.parametrize("kind", ["missing", "oversized", "mismatch"])
def test_audit_reader_requires_exact_bounded_raw_jsonl(monkeypatch, kind):
    raw = json.dumps({"_TRANSPORT": "kernel", "MESSAGE": audit()}).encode() + b"\n"
    result = {"returncode": 0, "stdout": raw.decode(), "stdout_hex": raw.hex()}
    if kind == "missing":
        del result["stdout_hex"]
    elif kind == "oversized":
        result["stdout_hex"] = "20" * (probes.AUDIT_LIMIT + 1)
    else:
        result["stdout"] += " "
    monkeypatch.setattr(probes, "bounded_command", lambda *args, **kwargs: result)
    with pytest.raises(ProbeError):
        probes.read_kernel_audit(1700000000123000000, 1700000000124000000, 456)


def context_record():
    caller = process_snapshot(netns="net:[1]")
    context = {
        "schema_version": 1, "kind": "corpus-python-context", "complete": True,
        "process": process_snapshot(), "executable": "/usr/bin/python3", "version": "3.12.3",
        "prefix": "/usr", "base_prefix": "/usr", "path": ["/workspace", "/opt/extra-0"],
        "site_paths": ["/usr/lib/python3/dist-packages"], "enable_user_site": True,
        "environment": dict.fromkeys(payload.PYTHON_CONTEXT_ENV),
        "loaded_hooks": {"sitecustomize": None, "usercustomize": None},
        "startup_inputs": [], "distributions": [{"name": "pytest"}],
        "identity_mapping": {"uid_map_raw": "1001 1001 1\n", "gid_map_raw": "1001 1001 1\n",
                             "overflowuid_raw": "65534\n", "overflowgid_raw": "65534\n"},
        "module_resolution": dict.fromkeys(payload.PYTHON_CONTEXT_MODULES, {}),
    }
    return {"schema_version": 1, "kind": "corpus-python-context", "caller": caller,
            "returncode": 0, "command": ["/usr/bin/python3", "/workspace/probe.py", "python-context"],
            "context": context}


@pytest.mark.parametrize("change", ["exit", "incomplete", "error", "same_net", "label", "nnp",
                                   "uid", "interpreter", "argv", "environment", "modules", "hooks", "roots"])
def test_python_context_record_requires_complete_enforcing_fixed_context(change):
    value = context_record()
    probes.validate_python_context_record(value)
    context = value["context"]
    if change == "exit":
        value["returncode"] = 1
    elif change == "incomplete":
        context["complete"] = False
    elif change == "error":
        context["error"] = "partial"
    elif change == "same_net":
        context["process"]["netns"] = value["caller"]["netns"]
    elif change == "label":
        context["process"]["label"] = "unconfined"
    elif change == "nnp":
        context["process"]["status"]["NoNewPrivs"] = "0"
    elif change == "uid":
        context["process"]["uid"] += 1
    elif change == "interpreter":
        context["executable"] = "/opt/hostedtoolcache/python"
    elif change == "argv":
        value["command"] = ["/usr/bin/python3", "-c", "unreviewed"]
    elif change == "environment":
        del context["environment"]["PYTHONPATH"]
    elif change == "modules":
        del context["module_resolution"]["pytest"]
    elif change == "hooks":
        del context["loaded_hooks"]["usercustomize"]
    elif change == "roots":
        context["path"] = []
    with pytest.raises(ProbeError):
        probes.validate_python_context_record(value)


def test_python_context_contract_calls_actual_adapter_and_rejects_contract_drift(monkeypatch):
    from code_forge.mutation_engines.adapters import python_mutmut

    repo = Path(__file__).resolve().parents[2]
    real_is_dir, real_exists = Path.is_dir, Path.exists
    monkeypatch.setattr(Path, "is_dir", lambda self: True if str(self) in probes.CORPUS_SITE_PATHS else real_is_dir(self))
    monkeypatch.setattr(Path, "exists", lambda self: False if str(self) == "/usr/lib/python3.12/dist-packages" else real_exists(self))
    result = probes._python_context_contract(repo, "corpus-context-0123456789ab")
    assert result["extra_ro_binds"] == [[str(repo / "src"), "/opt/forge-src"], *[
        [root, "/opt/extra-" + str(i)] for i, root in enumerate(probes.CORPUS_SITE_PATHS)
    ]]
    assert dict(result["env"])["HOME"] == "/workspace"
    assert dict(result["env"])["PYTHONPATH"] == "/opt/forge-src:/opt/extra-0:/opt/extra-1:/opt/extra-2"
    monkeypatch.setattr(python_mutmut.MutmutAdapter, "_sandbox_env", lambda *args: (("PYTHONPATH", "/host"),))
    with pytest.raises(ProbeError, match="contract changed"):
        probes._python_context_contract(repo, "corpus-context-0123456789ab")
    with pytest.raises(ProbeError, match="bound repository"):
        probes._python_context_contract(repo / "other", "corpus-context-0123456789ab")


def fake_context_payload(tmp_path, monkeypatch):
    import importlib
    import importlib.metadata
    import site

    source = tmp_path / "site"
    source.mkdir()
    for name in payload.PYTHON_CONTEXT_MODULES:
        parts = name.split(".")
        for i in range(1, len(parts) + 1):
            directory = source.joinpath(*parts[:i])
            directory.mkdir(exist_ok=True)
            (directory / "__init__.py").write_text("raise RuntimeError('must never execute synthetic source')\n")
    (source / "z.pth").write_text("# observed, not executed\n")
    (source / "a.pth").write_text("# observed, not executed\n")
    metadata = source / "pytest-9.1.1.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: pytest\nVersion: 9.1.1\n")
    (metadata / "entry_points.txt").write_text("[pytest11]\nunexpected = unexpected.module\n")
    distribution = SimpleNamespace(
        _path=metadata, locate_file=lambda value: source / value,
        metadata={"Name": "pytest"}, version="9.1.1",
        entry_points=[SimpleNamespace(name="unexpected", value="unexpected.module", group="pytest11",
                                      load=lambda: pytest.fail("must not load entry points"))],
    )
    monkeypatch.setattr(sys, "path", [str(source), str(source)])
    monkeypatch.setattr(sys, "executable", "/usr/bin/python3")
    monkeypatch.setattr(site, "getsitepackages", lambda: [str(source)])
    monkeypatch.setattr(site, "getusersitepackages", lambda: str(tmp_path / "absent"))
    monkeypatch.setattr(payload, "snapshot", process_snapshot)
    original_read_bytes = payload.read_bytes
    proc = {
        "/proc/self/uid_map": b"1001 1001 1\n", "/proc/self/gid_map": b"1001 1001 1\n",
        "/proc/sys/kernel/overflowuid": b"65534\n", "/proc/sys/kernel/overflowgid": b"65534\n",
    }
    monkeypatch.setattr(payload, "read_bytes", lambda path, limit=payload.MAX_RECORD:
                        proc[str(path)] if str(path) in proc else original_read_bytes(path, limit))
    monkeypatch.delitem(sys.modules, "sitecustomize", raising=False)
    monkeypatch.delitem(sys.modules, "usercustomize", raising=False)
    imports = []

    def discover(*, path):
        assert path == [str(source), str(source)]
        return [distribution, distribution]

    def fixed_import(name):
        imports.append(name)
        assert name in payload.PYTHON_CONTEXT_MODULES
        spec = payload._python_context_spec(name)
        return SimpleNamespace(__file__=spec["origin"], __spec__=SimpleNamespace(
            origin=spec["origin"], submodule_search_locations=spec["search_locations"]))

    monkeypatch.setattr(importlib.metadata, "distributions", discover)
    monkeypatch.setattr(importlib, "import_module", fixed_import)
    return source, imports, distribution


def test_python_context_payload_keeps_order_duplicates_and_unexpected_entrypoints(tmp_path, monkeypatch):
    source, imports, _ = fake_context_payload(tmp_path, monkeypatch)
    record = payload.python_context(tmp_path)
    assert record["complete"] is True
    assert record["path"] == [str(source), str(source)]
    assert len(record["distributions"]) == 2
    assert record["distributions"][0] == record["distributions"][1]
    assert record["distributions"][0]["pytest_entry_points"] == [
        {"name": "unexpected", "value": "unexpected.module", "group": "pytest11"}]
    assert record["distributions"][0]["entry_points_file"]["sha256"]
    assert [Path(x["path"]).name for x in record["startup_inputs"]] == ["a.pth", "z.pth"]
    assert imports == list(payload.PYTHON_CONTEXT_MODULES)
    assert payload.read_json(tmp_path / "python-context.json") == record
    assert "PKG-INFO" not in (tmp_path / "python-context.json").read_text()


def test_python_context_payload_preserves_partial_record_before_import_failure(tmp_path, monkeypatch):
    import importlib

    fake_context_payload(tmp_path, monkeypatch)
    monkeypatch.setattr(importlib, "import_module", lambda name: (_ for _ in ()).throw(ImportError("fixed import failed")))
    with pytest.raises(ImportError):
        payload.python_context(tmp_path)
    record = payload.read_json(tmp_path / "python-context.json")
    assert record["complete"] is False and record["error"] == "ImportError: fixed import failed"
    assert record["distributions"] and record["module_resolution"]["pytest"]


@pytest.mark.parametrize("kind", ["oversized", "directory", "fifo"])
def test_python_context_identity_rejects_unbounded_or_nonregular_inputs(tmp_path, kind):
    path = tmp_path / "input"
    if kind == "oversized":
        path.write_bytes(b"x" * (1024 * 1024 + 1))
    elif kind == "directory":
        path.mkdir()
    else:
        os.mkfifo(path)
    with pytest.raises((ProbeError, OSError)):
        payload._python_context_identity(path)


@pytest.mark.parametrize("failure", [None, "flags", "wait", "exit", "join", "teardown"])
def test_python_context_controller_is_fixed_bounded_and_preserves_failure_evidence(tmp_path, monkeypatch, failure):
    from code_forge.mutation_engines.isolate import SandboxSpec, Supervisor as ActualSupervisor

    expected = context_record()
    for key in ("uid", "euid"):
        expected["caller"][key] = expected["context"]["process"][key] = os.getuid()
    for key in ("gid", "egid"):
        expected["caller"][key] = expected["context"]["process"][key] = os.getgid()
    expected["context"]["identity_mapping"].update(
        uid_map_raw=f"{os.getuid()} {os.getuid()} 1\n", gid_map_raw=f"{os.getgid()} {os.getgid()} 1\n")
    root = tmp_path / "cgroup"
    root.mkdir()
    evidence = tmp_path / "evidence"
    events = []
    monkeypatch.setattr(probes, "require_runner", lambda: expected["caller"])
    contract = {"env": [["HOME", "/workspace"]], "extra_ro_binds": [[str(tmp_path), "/opt/forge-src"]],
                "memory_mb": 256, "pids": 64, "workspace_mb": 64, "cwd": "/workspace"}
    monkeypatch.setattr(probes, "_python_context_contract", lambda *args: contract)

    class Supervisor:
        def __init__(self, spec, cgroup):
            self.spec = spec
            self.cgroup_path = str(root / ("forge-" + spec.run_id))
            self.payload_pid = 300
            self._process = SimpleNamespace(poll=lambda: 0)
            self.limits_readback = {"memory.max": str(256 * 1024 * 1024), "pids.max": "64", "memory.swap.max": "0"}
            self.gate_opened_monotonic_ns = 100
            assert cgroup == str(root)
            assert spec.command == ("/usr/bin/python3", "/workspace/probe.py", "python-context")
            assert spec.runtime_root is None and spec.extra_rw_binds == ()
            assert spec.memory_mb == 256 and spec.pids == 64 and spec.workspace_mb == 64
            assert (Path(spec.workspace_host) / "probe.py").read_bytes() == Path(payload.__file__).read_bytes()

        def _bwrap_argv(self):
            argv = ActualSupervisor._bwrap_argv(self)
            if failure == "flags":
                argv.remove("--unshare-net")
            return argv

        def start(self):
            pass

        def wait(self, timeout):
            events.append("wait")
            assert 0 < timeout <= 30
            payload.write_json(Path(self.spec.workspace_host) / "python-context.json", expected["context"])
            if failure == "wait":
                raise TimeoutError("bounded timeout")
            return 1 if failure == "exit" else 0

        def teardown(self):
            events.append("teardown")
            if failure == "teardown":
                raise RuntimeError("bounded teardown failure")

    class Thread:
        def __init__(self, supervisor):
            self.supervisor = supervisor

        def start(self):
            events.append("start")
            self.supervisor.start()

        def wait_started(self, timeout):
            assert timeout <= 10

        def join(self, timeout):
            assert timeout == 10
            events.append("join")

        def is_alive(self):
            return failure == "join"

    monkeypatch.setattr(probes, "_production_types", lambda: (SandboxSpec, Supervisor, Thread))
    if failure:
        with pytest.raises(ProbeError):
            probes.run_python_context_probe(str(root), evidence, repo=tmp_path)
    else:
        result = probes.run_python_context_probe(str(root), evidence, repo=tmp_path)
        assert result["returncode"] == 0
        assert "--unshare-net" in result["argv"]
    record = payload.read_json(evidence / "python-context-probe.json")
    assert bool(record.get("error")) == bool(failure)
    assert events == ([] if failure == "flags" else ["start", "wait", "teardown", "join"])
    if failure != "flags":
        assert (evidence / "python-context-workspace/python-context.json").is_file()


def test_python_context_contract_rejects_bind_order_and_unexpected_site(monkeypatch):
    from code_forge.mutation_engines.adapters import python_mutmut

    repo = Path(__file__).resolve().parents[2]
    real_is_dir, real_exists = Path.is_dir, Path.exists
    monkeypatch.setattr(Path, "is_dir", lambda self: True if str(self) in probes.CORPUS_SITE_PATHS else real_is_dir(self))
    monkeypatch.setattr(Path, "exists", lambda self: False if str(self) == "/usr/lib/python3.12/dist-packages" else real_exists(self))
    original = python_mutmut.MutmutAdapter._extra_binds
    monkeypatch.setattr(python_mutmut.MutmutAdapter, "_extra_binds", lambda self, context: tuple(reversed(original(self, context))))
    with pytest.raises(ProbeError, match="contract changed"):
        probes._python_context_contract(repo, "corpus-context-0123456789ab")
    monkeypatch.setattr(Path, "exists", lambda self: True if str(self) == "/usr/lib/python3.12/dist-packages" else real_exists(self))
    with pytest.raises(ProbeError, match="site-path"):
        probes._python_context_contract(repo, "corpus-context-0123456789ab")


@pytest.mark.parametrize("change", ["path", "environment", "label", "active_origin", "distribution_count"])
def test_python_context_payload_rejects_import_drift_and_unbounded_metadata(tmp_path, monkeypatch, change):
    import importlib
    import importlib.metadata

    _, _, distribution = fake_context_payload(tmp_path, monkeypatch)
    original = importlib.import_module

    def importing(name):
        module = original(name)
        if change == "path":
            sys.path.append("/unreviewed")
        elif change == "environment":
            monkeypatch.setenv("PYTEST_PLUGINS", "unreviewed")
        elif change == "active_origin":
            module.__spec__.origin = "/unreviewed.py"
        return module

    monkeypatch.setattr(importlib, "import_module", importing)
    if change == "label":
        first, second = process_snapshot(), process_snapshot()
        second["label"] = "unconfined"
        values = iter([first, second])
        monkeypatch.setattr(payload, "snapshot", lambda: next(values))
    elif change == "distribution_count":
        monkeypatch.setattr(importlib.metadata, "distributions", lambda **kwargs: [distribution] * 257)
    with pytest.raises(ProbeError):
        payload.python_context(tmp_path)
    # A deliberately excessive metadata list may itself exceed the bounded JSON
    # envelope. Successful bounded failures always preserve complete=False.
    path = tmp_path / "python-context.json"
    if path.exists():
        assert payload.read_json(path)["complete"] is False


@pytest.mark.parametrize("field,value", [
    ("uid_map_raw", "0 1001 1\n"), ("gid_map_raw", "1001 1001 2\n"),
    ("uid_map_raw", "1001 1001 1\n0 0 1\n"), ("uid_map_raw", "1001 1002 1\n"),
    ("uid_map_raw", "1001 1001 ١\n"), ("overflowuid_raw", "1001\n"),
    ("overflowgid_raw", "4294967296\n"), ("overflowgid_raw", "-1\n"),
    ("overflowgid_raw", "65534\nextra\n"),
])
def test_python_context_requires_exact_single_mapping_and_unambiguous_overflow(field, value):
    record = context_record()
    record["context"]["identity_mapping"][field] = value
    with pytest.raises(ProbeError):
        probes.validate_python_context_record(record)


def test_probe_owner_rejects_start_after_cancellation_without_launching(tmp_path):
    events = []

    class Supervisor:
        def __init__(self, *args):
            self._process = None
            self.cgroup_path = str(tmp_path / "never-created-cgroup")

        def start(self):
            events.append("launch")

        def teardown(self):
            events.append("teardown")

    owner = probes._owned_probe_supervisor(Supervisor)(None, None)
    owner.cancel_probe_start()
    assert events == []
    with pytest.raises(ProbeError, match="cancelled before owner entry"):
        owner.start()
    owner.cancel_probe_start()
    assert events == ["teardown"]
    assert owner.probe_cleanup_complete is True


@pytest.mark.parametrize("probe", ["boundary", "context"])
@pytest.mark.parametrize("startup", ["late_success", "failure", "still_running", "teardown_error"])
def test_probe_late_start_is_owned_and_cleaned_after_cancellation(tmp_path, monkeypatch, probe, startup):
    """Use real thread/teardown semantics; no fork, cgroup or sandbox action."""
    import threading
    from code_forge.mutation_engines.isolate import SandboxSpec, Supervisor as ActualSupervisor, SupervisorThread

    entered, release = threading.Event(), threading.Event()
    holder, events = {}, []
    caller = process_snapshot(netns="net:[1]")
    caller.update(uid=os.getuid(), euid=os.getuid(), gid=os.getgid(), egid=os.getgid())
    monkeypatch.setattr(probes, "require_runner", lambda: caller)
    monkeypatch.setattr(probes.shutil, "which", lambda *args, **kwargs: "/usr/bin/true")
    monkeypatch.setattr(probes, "_python_context_contract", lambda *args: {
        "env": [], "extra_ro_binds": [], "memory_mb": 256, "pids": 64,
        "workspace_mb": 64, "cwd": "/workspace",
    })
    root = tmp_path / "cgroup"
    root.mkdir()

    class Child:
        def __init__(self):
            self.killed = threading.Event()

        def poll(self):
            return -9 if self.killed.is_set() else None

        def wait(self, timeout=None):
            assert self.killed.wait(timeout=2 if timeout is None else min(timeout, 2))
            return -9

        def kill(self):
            events.append("kill")
            self.killed.set()

    class Supervisor(ActualSupervisor):
        def __init__(self, *args):
            super().__init__(*args)
            holder["supervisor"] = self

        def start(self):
            events.append("start_entered")
            entered.set()
            assert release.wait(timeout=2)
            if startup == "failure":
                raise RuntimeError("startup failed before process publication")
            self._process = holder["child"] = Child()
            events.append("child_published")

        def _remove_cgroup(self, force=False):
            events.append("owned_cleanup")

        def teardown(self):
            super().teardown()
            if startup == "teardown_error":
                raise RuntimeError("synthetic teardown failure")

    class Thread(SupervisorThread):
        def __init__(self, supervisor):
            super().__init__(supervisor)
            holder["thread"] = self

        def wait_started(self, timeout):
            assert entered.wait(timeout=2)
            raise TimeoutError("synthetic startup deadline")

        def join(self, timeout=None):
            assert timeout == 10
            # This is the critical historical race: the timeout must never
            # poison teardown's idempotence before the delayed child exists.
            assert holder["supervisor"]._torn_down is False
            if startup != "still_running":
                release.set()
                super().join(timeout=2)

    monkeypatch.setattr(probes, "_production_types", lambda: (SandboxSpec, Supervisor, Thread))
    evidence = tmp_path / "evidence"
    try:
        with pytest.raises(ProbeError):
            if probe == "context":
                probes.run_python_context_probe(str(root), evidence, repo=tmp_path)
            else:
                probes.run_boundary_probe(str(root), evidence, audit_reader=lambda *args: pytest.fail("no audit"))
        filename = "python-context-probe.json" if probe == "context" else "boundary-probe.json"
        record = payload.read_json(evidence / filename)
        assert record["error"] and record["cleanup"]["cancelled"] is True
        if startup == "still_running":
            assert holder["thread"].is_alive()
            assert record["cleanup"]["complete"] is False
            assert holder["supervisor"]._torn_down is False
        elif startup == "teardown_error":
            assert record["cleanup"]["error"] == "synthetic teardown failure"
        else:
            assert record["cleanup"]["complete"] is True
    finally:
        release.set()
        # Call the production join directly so a timed-out owner can finish
        # without pretending the helper's earlier join established cleanup.
        threading.Thread.join(holder["thread"], timeout=2)
    assert not holder["thread"].is_alive()
    assert holder["supervisor"]._torn_down is True
    assert events.count("owned_cleanup") == 1
    if startup != "failure":
        assert holder["child"].killed.is_set()
        assert events.count("kill") == 1


@pytest.mark.parametrize("state", ["success", "child_live", "cgroup_retained", "unreadable", "unknown_child"])
def test_probe_cleanup_requires_terminal_child_and_exact_cgroup_absence(tmp_path, monkeypatch, state):
    """Production teardown's suppressed failures must not become a cleanup PASS."""
    import subprocess
    from code_forge.mutation_engines.isolate import Supervisor as ActualSupervisor

    cgroup = tmp_path / "forge-owned"
    cgroup.mkdir()
    events = []

    class Child:
        def poll(self):
            return None if state == "child_live" else (False if state == "unknown_child" else 0)

        def kill(self):
            events.append("kill")

        def wait(self, timeout):
            assert timeout == 10
            events.append("wait")
            # This exact exception is deliberately suppressed by production.
            raise subprocess.TimeoutExpired("mock-owned-child", timeout)

    class Supervisor(ActualSupervisor):
        def _remove_cgroup(self, force=False):
            events.append("remove_attempt")
            if state != "cgroup_retained":
                cgroup.rmdir()

    owner = probes._owned_probe_supervisor(Supervisor)(SimpleNamespace(run_id="owned"), str(tmp_path))
    owner._process = Child()
    owner.probe_start_complete = True
    original_lstat = os.lstat
    if state == "unreadable":
        def unreadable(path, *args, **kwargs):
            if str(path) == str(cgroup):
                raise PermissionError("cannot establish owned cgroup absence")
            return original_lstat(path, *args, **kwargs)
        monkeypatch.setattr(os, "lstat", unreadable)
    thread = SimpleNamespace(join=lambda timeout: None, is_alive=lambda: False)
    record = {}
    probes._finish_probe(owner, thread, True, record)
    assert record["cleanup"]["complete"] is (state == "success")
    assert bool(record.get("error")) is (state != "success")
    assert owner._torn_down is True
    assert events == (["kill", "wait", "remove_attempt"] if state == "child_live" else ["remove_attempt"])
    if state == "cgroup_retained":
        assert cgroup.is_dir() and "cgroup still exists" in record["error"]


def setpcap_companion(**changes):
    return audit(cap=8, name="setpcap", serial=4, **changes)


@pytest.mark.parametrize("with_companion", [False, True, "reverse_journal_order"])
def test_negative_accepts_only_optional_preceding_attributable_setpcap(with_companion):
    record = negative()
    if with_companion:
        record["audit"].insert(0, setpcap_companion())
    if with_companion == "reverse_journal_order":
        record["audit"].reverse()
    preserved = json.dumps(record, sort_keys=True)
    probes.validate_negative_control(record)
    assert json.dumps(record, sort_keys=True) == preserved


@pytest.mark.parametrize("change", [
    "duplicate_companion", "duplicate_required", "wrong_pid", "wrapper_pid", "wrong_profile", "wrong_capability", "wrong_capname",
    "wrong_operation", "wrong_comm", "wrong_class", "missing_class", "outside_before", "outside_after",
    "later_timestamp", "later_serial", "same_event", "reused_serial", "malformed_quote", "duplicate_field", "duplicate_timestamp",
    "unparsed_suffix", "unparsed_prefix", "unknown_field", "wrong_type", "missing_capability", "missing_required", "unrelated_record",
    "invalid_precision", "bool_pid", "bad_start", "bad_end", "unbounded_window", "oversized", "not_list", "not_string",
])
def test_negative_companion_is_exact_bounded_and_never_a_global_audit_waiver(change):
    record = negative()
    companion = setpcap_companion()
    required = audit()
    records = [companion, required]
    if change == "duplicate_companion":
        records.insert(0, companion)
    elif change == "duplicate_required":
        records.append(required)
    elif change in {"wrong_pid", "wrapper_pid"}:
        records[0] = setpcap_companion(pid=999 if change == "wrong_pid" else record["wrapper_pid"])
    elif change == "wrong_profile":
        records[0] = setpcap_companion(profile="unpriv_bwrap")
    elif change in {"wrong_capability", "wrong_capname", "wrong_operation", "wrong_comm", "wrong_class", "missing_class", "wrong_type", "missing_capability"}:
        before, after = {
            "wrong_capability": ('capability=8', 'capability=21'),
            "wrong_capname": ('capname="setpcap"', 'capname="net_admin"'),
            "wrong_operation": ('operation="capable"', 'operation="userns_create"'),
            "wrong_comm": ('comm="bwrap"', 'comm="python"'),
            "wrong_class": ('class="cap"', 'class="file"'),
            "missing_class": ('class="cap" ', ''),
            "wrong_type": ('type=1400', 'type=1300'),
            "missing_capability": ('capability=8 ', ''),
        }[change]
        records[0] = companion.replace(before, after)
    elif change in {"outside_before", "outside_after", "later_timestamp", "invalid_precision"}:
        instant = {"outside_before": "1700000000.122", "outside_after": "1700000000.125",
                   "later_timestamp": "1700000000.124", "invalid_precision": "1700000000.1230000001"}[change]
        records[0] = setpcap_companion(instant=instant)
    elif change in {"later_serial", "same_event", "reused_serial"}:
        records[0] = companion.replace(':4)', ':6)' if change == "later_serial" else ':5)')
        if change == "reused_serial":
            records[0] = records[0].replace('1700000000.123', '1700000000.1229')
    elif change == "malformed_quote":
        records[0] += ' extra="unterminated'
    elif change == "duplicate_field":
        records[0] += ' pid=456'
    elif change == "duplicate_timestamp":
        records[0] += ' audit(1700000000.123:9)'
    elif change == "unparsed_suffix":
        records[0] += ' dropped-unparsed-content'
    elif change == "unparsed_prefix":
        records[0] = 'unknown-prefix ' + companion
    elif change == "unknown_field":
        records[0] += ' unrelated=1'
    elif change == "missing_required":
        records = [companion]
    elif change == "unrelated_record":
        records.append(audit(pid=999, cap=21, name="sys_admin", serial=7))
    elif change == "bool_pid":
        record["info"] = {"child-pid": True}
        raw = b'{"child-pid":true}'
        record.update(info_raw_hex=raw.hex(), info_bytes=len(raw))
    elif change == "bad_start":
        record["started"]["utc_ns"] = True
    elif change == "bad_end":
        record["ended"]["utc_ns"] = 1
    elif change == "unbounded_window":
        record["ended"]["utc_ns"] += 31_000_000_000
    elif change == "oversized":
        records[0] += ' ' * probes.AUDIT_LIMIT
    elif change == "not_list":
        records = tuple(records)
    elif change == "not_string":
        records[0] = None
    record["audit"] = records
    with pytest.raises(ProbeError):
        probes.validate_negative_control(record)


@pytest.mark.parametrize("field,value", [
    ("returncode", 2), ("returncode", -1), ("returncode", True),
    ("stderr", "other: RTM_NEWADDR: Operation not permitted\n"),
    ("stderr", "bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted\nextra error\n"),
])
def test_negative_requires_exact_exit_one_and_loopback_refusal_even_with_companion(field, value):
    record = negative()
    record["audit"].insert(0, setpcap_companion())
    record[field] = value
    with pytest.raises(ProbeError):
        probes.validate_negative_control(record)


def test_shared_postload_sys_admin_validator_still_rejects_setpcap_companion():
    records = [audit(500, cap=8, name="setpcap", profile="unpriv_bwrap", serial=4),
               audit(500, cap=21, name="sys_admin", profile="unpriv_bwrap", serial=5)]
    with pytest.raises(ProbeError, match="competing capability/profile"):
        probes.validate_audit(records, pid=500, start_ns=1700000000123000000, end_ns=1700000000124000000,
                              capability=21, capname="sys_admin", profiles=("unpriv_bwrap",))
    record = boundary()
    record["audit"] = records
    with pytest.raises(ProbeError):
        probes.validate_boundary_record(record)
