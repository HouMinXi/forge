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
        "identity_mapping": {"uid_map_raw": "1001 0 1\n", "gid_map_raw": "1001 0 1\n",
                             "overflowuid_raw": "65534\n", "overflowgid_raw": "65534\n"},
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


def audit_clock(*, monotonic_before=90, monotonic_after=210):
    return {"clock_id": 5, "clock_name": "CLOCK_REALTIME_COARSE", "resolution_ns": 1_000_000,
            "before_ns": 1700000000123000000, "after_ns": 1700000000124000000,
            "monotonic_before_ns": monotonic_before, "monotonic_after_ns": monotonic_after}


def mock_audit_clock(monkeypatch):
    monkeypatch.setattr(payload.time, "clock_getres", lambda clock: 0.001 if clock == 5 else pytest.fail("wrong clock"))
    coarse = iter([1700000000123000000, 1700000000124000000])
    monotonic = iter([90, 210])
    monkeypatch.setattr(payload.time, "clock_gettime_ns", lambda clock: next(coarse) if clock == 5 else pytest.fail("wrong clock"))
    monkeypatch.setattr(payload.time, "monotonic_ns", lambda: next(monotonic))


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
        "audit_clock": audit_clock(),
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
        "net_audit_clock": audit_clock(monotonic_before=290, monotonic_after=410),
    }


def host_status(pid, ns_pid, ppid):
    return (f"Name:\tpython3\nPid:\t{pid}\nPPid:\t{ppid}\nNSpid:\t{pid}\t{ns_pid}\n"
            "Uid:\t1001\t1001\t1001\t1001\nGid:\t1001\t1001\t1001\t1001\n"
            "NoNewPrivs:\t1\nCapEff:\t0000000000000000\nCapPrm:\t0000000000000000\n"
            "CapInh:\t0000000000000000\nCapAmb:\t0000000000000000\n")


def host_mapping(caller, cgroup_path="/owned/forge-qualification-test"):
    reader = {key: caller[key] for key in ("pid", "uid", "euid", "gid", "egid", "userns")}
    observed = {"host_pid": 450, "namespace_pid": 2, "nspid": [450, 2],
                "status_raw": host_status(450, 2, 449),
                "stat_raw": "450 (python3) S 449 " + "0 " * 17 + "1234\n",
                "userns": "user:[11]", "pidns": "pid:[13]"}
    return {"reader_before": dict(reader), "reader_after": dict(reader),
            "process_before": observed, "process_after": copy.deepcopy(observed),
            "parent_status_raw": host_status(449, 1, 448),
            "uid_map_raw": "1001 1001 1\n", "gid_map_raw": "1001 1001 1\n",
            "cgroup_path": cgroup_path, "cgroup_procs_before_raw": "449\n450\n500\n",
            "cgroup_procs_after_raw": "449\n450\n500\n",
            "pidfd": {"supported": True, "alive_before": True, "alive_after": True}}


def boundary():
    caller = process_snapshot(netns="net:[1]", userns="user:[1]")
    caller["pidns"] = "pid:[1]"
    return {
        "returncode": 0,
        "token": "token",
        "caller": caller,
        "cgroup_path": "/owned/forge-qualification-test",
        "original_host_mapping": host_mapping(caller),
        "witness_mapping": {"host_pid": 500, "namespace_pid": 5, "nspid": [500, 5],
                            "status_raw": host_status(500, 5, 450)},
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
                audit_clock=audit_clock(),
                started=negative()["started"],
                ended=negative()["ended"],
                capability=12,
                capname="net_admin",
                profiles=("unprivileged_userns",),
            )






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


@pytest.mark.parametrize("clock_failure", [None, "before", "after", "rollback"])
def test_actual_witness_calls_user_then_net_alone_without_exec(tmp_path, monkeypatch, clock_failure):
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
    original_end = payload.end_audit_clock
    def failed_clock(*args):
        raise ProbeError("unsupported coarse clock")
    def end_clock(clock):
        original_end(clock)
        if clock_failure == "rollback":
            clock["after_ns"] = clock["before_ns"] - 1
    if clock_failure == "before":
        monkeypatch.setattr(payload, "begin_audit_clock", failed_clock)
    else:
        monkeypatch.setattr(payload, "end_audit_clock", failed_clock if clock_failure == "after" else end_clock)
    payload.write_json(tmp_path / "witness-release.json", {"token": "token", "namespace_pid": 5})
    if clock_failure:
        with pytest.raises(ProbeError):
            payload.witness(tmp_path, "token", 1)
        expected_calls = [payload.os.CLONE_NEWUSER]
        if clock_failure != "before":
            expected_calls.append(payload.os.CLONE_NEWNET)
        assert calls == expected_calls
        failed = payload.read_json(tmp_path / "witness-result.json")
        assert failed["after_user"] == record["after_user"]
        if clock_failure != "before":
            assert "net_started" in failed and "net_ended" in failed and "net_audit_clock" in failed
        with pytest.raises(ProbeError):
            payload.validate_witness(failed)
        return
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
            payload.write_json(workspace / "initial.json", expected["payload"]["initial"])

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
        return expected["witness_mapping"]

    monkeypatch.setattr(probes, "map_witness_pid", mapping)
    def original_mapping(path, initial, caller, witness):
        assert not (Path(holder["supervisor"].spec.workspace_host) / "witness-release.json").exists()
        assert initial == expected["payload"]["initial"] and witness == expected["witness_mapping"]
        return host_mapping(caller, str(path))
    monkeypatch.setattr(probes, "observe_original_host_mapping", original_mapping)
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


@pytest.mark.parametrize("startup", ["late_success", "failure", "still_running", "teardown_error"])
def test_probe_late_start_is_owned_and_cleaned_after_cancellation(tmp_path, monkeypatch, startup):
    """Use real thread/teardown semantics; no fork, cgroup or sandbox action."""
    import threading
    from code_forge.mutation_engines.isolate import SandboxSpec, Supervisor as ActualSupervisor, SupervisorThread

    entered, release = threading.Event(), threading.Event()
    holder, events = {}, []
    caller = process_snapshot(netns="net:[1]")
    caller.update(uid=os.getuid(), euid=os.getuid(), gid=os.getgid(), egid=os.getgid())
    monkeypatch.setattr(probes, "require_runner", lambda: caller)
    monkeypatch.setattr(probes.shutil, "which", lambda *args, **kwargs: "/usr/bin/true")
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
            probes.run_boundary_probe(str(root), evidence, audit_reader=lambda *args: pytest.fail("no audit"))
        record = payload.read_json(evidence / "boundary-probe.json")
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








def test_shared_postload_sys_admin_validator_still_rejects_setpcap_companion():
    records = [audit(500, cap=8, name="setpcap", profile="unpriv_bwrap", serial=4),
               audit(500, cap=21, name="sys_admin", profile="unpriv_bwrap", serial=5)]
    with pytest.raises(ProbeError, match="competing capability/profile"):
        probes.validate_audit(records, pid=500, audit_clock=audit_clock(),
                              started=negative()["started"], ended=negative()["ended"],
                              capability=21, capname="sys_admin", profiles=("unpriv_bwrap",))
    record = boundary()
    record["audit"] = records
    with pytest.raises(ProbeError):
        probes.validate_boundary_record(record)


@pytest.mark.parametrize("which", ["initial", "reexec"])
@pytest.mark.parametrize("field,value", [
    ("uid_map_raw", "0 1001 1\n"), ("gid_map_raw", "1001 1001 1\n"),
    ("uid_map_raw", "1001 1001 2\n"), ("gid_map_raw", "1001 1001 1\n0 0 1\n"),
    ("uid_map_raw", ""), ("uid_map_raw", "١٠٠١ 1001 1\n"), ("gid_map_raw", True),
    ("overflowuid_raw", "1001\n"), ("overflowgid_raw", "99999\n"),
    ("overflowuid_raw", "4294967296\n"), ("overflowgid_raw", "0\n"),
    ("overflowuid_raw", "65534\nextra"), ("overflowgid_raw", None),
])
def test_boundary_retains_exact_single_caller_maps_and_host_overflow(which, field, value):
    record = boundary()
    record["payload"][which]["identity_mapping"][field] = value
    with pytest.raises(ProbeError):
        probes.validate_boundary_record(record)


@pytest.mark.parametrize("which", ["initial", "reexec"])
@pytest.mark.parametrize("field", ["CapEff", "CapPrm", "CapInh", "CapAmb"])
def test_initial_and_reexec_cannot_inherit_capability_grants(which, field):
    record = boundary()
    process = record["payload"][which]
    process["status"][field] = "0000000000200000"
    process["status_raw"] = "".join(key + ":\t" + value + "\n" for key, value in process["status"].items())
    with pytest.raises(ProbeError, match="inherited capability"):
        probes.validate_boundary_record(record)


def test_nested_userns_witness_keeps_real_capability_positive_control():
    record = boundary()
    for name in ("after_user", "after_net"):
        record["payload"]["witness"][name]["identity_mapping"] = {
            "uid_map_raw": "", "gid_map_raw": "", "overflowuid_raw": "65534\n", "overflowgid_raw": "65534\n"}
    probes.validate_boundary_record(record)
    process = record["payload"]["witness"]["after_user"]
    process["status"]["CapEff"] = "0000000000000000"
    process["status_raw"] = "".join(key + ":\t" + value + "\n" for key, value in process["status"].items())
    with pytest.raises(ProbeError, match="lacks CAP_SYS_ADMIN"):
        probes.validate_boundary_record(record)


@pytest.mark.parametrize("change", ["missing_map", "extra_map_field", "missing_host_overflow", "host_overflow_invalid"])
def test_boundary_missing_or_unbounded_mapping_evidence_fails(change):
    record = boundary()
    if change == "missing_map":
        record["payload"]["initial"].pop("identity_mapping")
    elif change == "extra_map_field":
        record["payload"]["initial"]["identity_mapping"]["unexpected"] = "1"
    elif change == "missing_host_overflow":
        record["caller"]["identity_mapping"].pop("overflowuid_raw")
    else:
        record["caller"]["identity_mapping"]["overflowuid_raw"] = True
    with pytest.raises(ProbeError):
        probes.validate_boundary_record(record)


_CGROUP_MOUNT = (
    "29 20 0:26 / /sys/fs/cgroup rw,nosuid,nodev,noexec - cgroup2 cgroup2 rw\n"
)
_CGROUP_ROOT = "/sys/fs/cgroup/user.slice/user-1001.slice/user@1001.service"
_CGROUP_LEAF = _CGROUP_ROOT + "/forge-diagnostic"


@pytest.mark.parametrize(
    "source,common",
    [
        ("/system.slice/runner.service", "/sys/fs/cgroup"),
        (
            "/user.slice/user-1001.slice/user@1001.service/app.slice/job.service",
            _CGROUP_ROOT,
        ),
        ("/", "/sys/fs/cgroup"),
    ],
)
def test_diagnostic_maps_only_exact_current_cgroup_view(source, common):
    paths = probes._diagnostic_cgroup_paths(
        "0::" + source + "\n", _CGROUP_MOUNT, _CGROUP_ROOT, _CGROUP_LEAF
    )
    assert len(paths) == 4 and paths["common_ancestor"] == common
    assert (
        paths["destination"] == _CGROUP_LEAF
        and paths["destination_parent"] == _CGROUP_ROOT
    )


@pytest.mark.parametrize(
    "change",
    [
        "hybrid",
        "multiple",
        "relative",
        "dotdot",
        "empty_component",
        "dot",
        "control",
        "escape",
        "long",
        "deep",
        "mount_root",
        "duplicate_mount",
        "other_mount",
        "missing_mount",
        "bad_root",
        "bad_leaf",
    ],
)
def test_diagnostic_mapping_never_guesses_ambiguous_paths(change):
    raw, mount, root, leaf = (
        "0::/system.slice/runner.service\n",
        _CGROUP_MOUNT,
        _CGROUP_ROOT,
        _CGROUP_LEAF,
    )
    if change == "hybrid":
        raw = "1:memory:/x\n" + raw
    elif change == "multiple":
        raw += raw
    elif change == "relative":
        raw = "0::relative\n"
    elif change == "dotdot":
        raw = "0::/../outside\n"
    elif change == "empty_component":
        raw = "0::/system.slice//runner\n"
    elif change == "dot":
        raw = "0::/./runner\n"
    elif change == "control":
        raw = "0::/bad\tpath\n"
    elif change == "escape":
        raw = "0::/bad\\040path\n"
    elif change == "long":
        raw = "0::/" + "a" * 4097 + "\n"
    elif change == "deep":
        raw = "0::/" + "/".join(["a"] * 33) + "\n"
    elif change == "mount_root":
        mount = mount.replace(" / /sys", " /delegated /sys")
    elif change == "duplicate_mount":
        mount += mount
    elif change == "other_mount":
        mount = mount.replace("/sys/fs/cgroup", "/other")
    elif change == "missing_mount":
        mount = ""
    elif change == "bad_root":
        root = "/sys/fs/cgroup-other/user"
    else:
        leaf = root + "/nested/leaf"
    with pytest.raises(ValueError):
        probes._diagnostic_cgroup_paths(raw, mount, root, leaf)


@pytest.mark.parametrize(
    "kind",
    [
        "regular",
        "directory",
        "missing",
        "symlink",
        "parent_symlink",
        "fifo",
        "unreadable",
    ],
)
def test_diagnostic_metadata_is_bounded_read_only(tmp_path, monkeypatch, kind):
    path = tmp_path / "target"
    if kind == "directory":
        path.mkdir()
    elif kind == "symlink":
        path.symlink_to(tmp_path / "actual")
        (tmp_path / "actual").write_text("data")
    elif kind == "parent_symlink":
        (tmp_path / "actual").mkdir()
        (tmp_path / "actual" / "file").write_text("data")
        path.symlink_to(tmp_path / "actual")
        path = path / "file"
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind != "missing":
        path.write_text("data")
    if kind == "unreadable":
        monkeypatch.setattr(
            Path,
            "lstat",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                PermissionError(13, "denied")
            ),
        )
    result = probes._diagnostic_metadata(path)
    assert result["path"] == str(path)
    if kind in {"regular", "directory"}:
        assert set(result) == {
            "path",
            "uid",
            "gid",
            "mode",
            "device",
            "inode",
            "effective_write_access",
        }
        assert (
            result["uid"] == os.getuid()
            and type(result["effective_write_access"]) is bool
        )
    else:
        assert "error" in result and "effective_write_access" not in result


@pytest.mark.parametrize("kind", ["ok", "oversize", "nonascii", "symlink", "fifo"])
def test_diagnostic_file_reads_are_regular_nofollow_and_bounded(tmp_path, kind):
    path = tmp_path / "state"
    if kind == "fifo":
        os.mkfifo(path)
    elif kind == "symlink":
        path.symlink_to(tmp_path / "other")
        (tmp_path / "other").write_bytes(b"x")
    else:
        path.write_bytes(
            {"ok": b"memory pids\n", "oversize": b"x" * 65, "nonascii": b"\xff"}[kind]
        )
    if kind == "ok":
        assert probes._diagnostic_read(path, 64) == "memory pids\n"
    else:
        with pytest.raises((OSError, ValueError, UnicodeError)):
            probes._diagnostic_read(path, 64)


@pytest.mark.parametrize("failure", [None, "read", "view", "namespace"])
def test_placement_diagnostics_fixed_paths_unknown_on_observation_failure(
    monkeypatch, failure
):
    reads, metadata = [], []

    def read(path, limit):
        path = str(path)
        reads.append((path, limit))
        if failure == "read":
            raise PermissionError(13, "denied")
        if path.endswith("/cgroup"):
            return (
                "0::/different\n"
                if failure == "view" and "/task/" in path
                else "0::/system.slice/runner.service\n"
            )
        if path.endswith("/mountinfo"):
            return _CGROUP_MOUNT
        return "domain\n" if path.endswith("cgroup.type") else "memory pids\n"

    monkeypatch.setattr(probes, "_diagnostic_read", read)
    monkeypatch.setattr(
        probes,
        "_diagnostic_metadata",
        lambda path: metadata.append(str(path)) or {"path": str(path)},
    )
    monkeypatch.setattr(probes.os.path, "realpath", lambda path, **kwargs: str(path))
    monkeypatch.setattr(
        probes.os,
        "readlink",
        lambda path: "x" * 129 if failure == "namespace" else "cgroup:[123]",
    )
    result = probes.cgroup_placement_diagnostics(_CGROUP_ROOT, _CGROUP_LEAF)
    assert result["observational_only"] is True and "status" not in result
    if failure is None:
        assert (
            result["mapping"] == "current-cgroup2-mount-view"
            and len(metadata) == 8
            and len(reads) == 15
        )
        assert set(result["locations"]) == {
            "source",
            "destination",
            "destination_parent",
            "common_ancestor",
        }
        assert all("/task/" in path for path, _ in reads if path.endswith("/mountinfo"))
    else:
        assert result["mapping"] == "unknown" and "error" in result and not metadata


def test_failed_start_preserves_cached_child_and_separate_cleanup_without_reaping(
    tmp_path, monkeypatch
):
    events = []

    class Child:
        _status = 127

        def poll(self):
            events.append("cleanup-poll")
            return self._status

    class Supervisor:
        def __init__(self, *args):
            self._process = None
            self.cgroup_path = str(tmp_path / "absent")
            self.payload_pid = 0
            self.limits_readback = {}
            self.gate_opened_monotonic_ns = 0

        def start(self):
            events.append("production-start")
            self._process = Child()
            self.payload_pid = 42
            self.limits_readback = {"pids.max": "32"}
            raise RuntimeError("original production error")

        def teardown(self):
            events.append("production-teardown")

    monkeypatch.setattr(
        probes,
        "cgroup_placement_diagnostics",
        lambda *args: events.append("observe") or {"mapping": "unknown"},
    )
    owner = probes._owned_probe_supervisor(Supervisor)(None, None)
    with pytest.raises(RuntimeError, match="original production error"):
        owner.start()
    assert events == ["observe", "production-start"]
    partial = owner.probe_diagnostics["partial_start"]
    assert partial == {
        "payload_pid": 42,
        "limits_readback": {"pids.max": "32"},
        "gate_opened_monotonic_ns": 0,
        "cached_child_exit": 127,
    }
    record = {}
    probes._finish_probe(
        owner,
        SimpleNamespace(join=lambda **kwargs: None, is_alive=lambda: False),
        True,
        record,
    )
    diag = record["startup_diagnostics"]
    assert (
        diag["production_start_started"]["monotonic_ns"]
        <= diag["production_start_ended"]["monotonic_ns"]
    )
    assert (
        diag["production_start_ended"]["monotonic_ns"]
        <= diag["cleanup_started"]["monotonic_ns"]
    )
    assert (
        diag["cleanup_started"]["monotonic_ns"] <= diag["cleanup_ended"]["monotonic_ns"]
    )
    assert events == [
        "observe",
        "production-start",
        "production-teardown",
        "cleanup-poll",
    ]
    assert record["cleanup"]["complete"] and "status" not in diag


@pytest.mark.parametrize("mount", [
    "bad ids bad / /sys/fs/cgroup nonsense - cgroup2 fake garbage\n",
    _CGROUP_MOUNT.rstrip() + " - cgroup2 fake garbage\n",
    "29 20 0:26 / /sys/fs/cgroup - cgroup2 cgroup2 rw\n",
    "29 20 0:26 / /sys/fs/cgroup rw - cgroup2 cgroup2\n",
    "29 20 0:26 / /sys/fs/cgroup rw - cgroup2 cgroup2 rw extra\n",
    "0 20 0:26 / /sys/fs/cgroup rw - cgroup2 cgroup2 rw\n",
    "29 -1 0:26 / /sys/fs/cgroup rw - cgroup2 cgroup2 rw\n",
    "29 20 nope / /sys/fs/cgroup rw - cgroup2 cgroup2 rw\n",
])
def test_diagnostic_rejects_malformed_mountinfo(mount):
    with pytest.raises(ValueError):
        probes._diagnostic_cgroup_paths("0::/system.slice/runner.service\n", mount, _CGROUP_ROOT, _CGROUP_LEAF)


def test_diagnostic_symlink_loop_is_preserved_as_unknown(tmp_path):
    path = tmp_path / "loop"
    path.symlink_to(path)
    record = probes._diagnostic_metadata(path)
    assert record["error"]["errno"] == errno.ELOOP
    assert "effective_write_access" not in record


@pytest.mark.parametrize("failure", ["sampling", "partial", "cancel", "interrupt"])
def test_diagnostic_failures_preserve_production_error_cleanup_and_cancellation(tmp_path, monkeypatch, failure):
    from forge_ci.controller import Cancelled
    events = []
    production_error = RuntimeError("original production exception")
    class BadMapping:
        def keys(self):
            raise RuntimeError("diagnostic copy failed")
    class Supervisor:
        def __init__(self, *args):
            self._process = None
            self.cgroup_path = str(tmp_path / "absent")
            self.limits_readback = BadMapping() if failure == "partial" else {}
        def start(self):
            events.append("production-start")
            raise production_error
        def teardown(self):
            events.append("production-teardown")
    def observe(*args):
        if failure == "sampling":
            raise RuntimeError("unexpected diagnostic exception")
        if failure == "cancel":
            raise Cancelled("stop requested")
        if failure == "interrupt":
            raise KeyboardInterrupt()
        return {}
    monkeypatch.setattr(probes, "cgroup_placement_diagnostics", observe)
    owner = probes._owned_probe_supervisor(Supervisor)(None, None)
    error = Cancelled if failure == "cancel" else KeyboardInterrupt if failure == "interrupt" else RuntimeError
    with pytest.raises(error) as caught:
        owner.start()
    if failure in {"sampling", "partial"}:
        assert caught.value is production_error
    assert owner.probe_start_complete is True
    if failure in {"sampling", "partial"}:
        key = "placement_context" if failure == "sampling" else "partial_start"
        assert owner.probe_diagnostics[key]["error"]["type"] == "RuntimeError"
        assert events == ["production-start"]
    else:
        assert events == []
    owner.cancel_probe_start()
    assert owner.probe_cleanup_complete and events[-1] == "production-teardown"


# The self-contained setup bootstrap must enforce the same audit contract as
# the regular helper; neither is allowed a fine-clock or missing-data fallback.
from forge_ci import setup_policy  # noqa: E402


@pytest.mark.parametrize("implementation", [setup_policy])
@pytest.mark.parametrize("instant,accepted", [
    ("1700000000.122", False), ("1700000000.123", True),
    ("1700000000.124", True), ("1700000000.125", False),
])
def test_coarse_audit_uses_exact_inclusive_floored_endpoints(implementation, instant, accepted):
    record = negative()
    record["audit_clock"].update(before_ns=1700000000123999999, after_ns=1700000000124000000)
    record["audit"] = [audit(instant=instant)]
    if accepted:
        implementation.validate_negative_control(record)
    else:
        with pytest.raises((ProbeError, setup_policy.SetupError), match="outside"):
            implementation.validate_negative_control(record)


@pytest.mark.parametrize("implementation", [setup_policy])
def test_coarse_audit_accepts_real_coarse_fine_drift_without_fine_fallback(implementation):
    record = negative()
    record["started"]["utc_ns"] = 1791466300338883060
    record["ended"]["utc_ns"] = 1791466300338910261
    record["audit_clock"].update(before_ns=1791466300337123000, after_ns=1791466300337123000)
    record["audit"] = [audit(instant="1791466300.337")]
    implementation.validate_negative_control(record)
    record["audit"] = [audit(instant="1791466300.338")]
    with pytest.raises((ProbeError, setup_policy.SetupError), match="outside"):
        implementation.validate_negative_control(record)


@pytest.mark.parametrize("implementation", [setup_policy])
@pytest.mark.parametrize("fraction", ["1", "12", "1234", "123456", "123456789", "1234567890", "１２３"])
def test_audit_accepts_only_kernel_three_ascii_fraction_digits(implementation, fraction):
    record = negative()
    record["audit"] = [audit(instant="1700000000." + fraction)]
    with pytest.raises((ProbeError, setup_policy.SetupError), match="timestamp"):
        implementation.validate_negative_control(record)


def damage_clock(record, change, max_interval_ns):
    if change == "missing":
        record.pop("audit_clock")
        return
    clock = record["audit_clock"]
    if change.startswith("missing:"):
        clock.pop(change.split(":", 1)[1])
    elif change.startswith("bool:"):
        clock[change.split(":", 1)[1]] = True
    elif change.startswith("float:"):
        field = change.split(":", 1)[1]
        clock[field] = float(clock[field])
    elif change == "extra":
        clock["supported"] = True
    elif change == "unsupported":
        clock["resolution_ns"] = 0
    elif change == "wrong_id":
        clock["clock_id"] = 0
    elif change == "wrong_name":
        clock["clock_name"] = "CLOCK_REALTIME"
    elif change == "negative":
        clock["before_ns"] = -1
    elif change == "negative_outer":
        clock["monotonic_before_ns"] = -1
    elif change == "backward":
        clock["after_ns"] = clock["before_ns"] - 1
    elif change == "backward_outer":
        clock["monotonic_after_ns"] = clock["monotonic_before_ns"] - 1
    elif change == "overbound":
        clock["after_ns"] = clock["before_ns"] + max_interval_ns + 1
    elif change == "overbound_outer":
        clock["monotonic_after_ns"] = clock["monotonic_before_ns"] + max_interval_ns + 1
    elif change == "overbound_resolution":
        clock["resolution_ns"] = max_interval_ns + 1
    elif change == "late_outer_start":
        clock["monotonic_before_ns"] = record["started"]["monotonic_ns"] + 1
    elif change == "early_outer_end":
        clock["monotonic_after_ns"] = record["ended"]["monotonic_ns"] - 1
    elif change.startswith("fine_"):
        action, which = change[5:].split(":")
        if action == "missing":
            record["started"].pop(which)
        elif action == "bool":
            record["started"][which] = True
        elif action == "float":
            record["started"][which] = float(record["started"][which])
        elif action == "negative":
            record["started"][which] = -1
        elif action == "backward":
            record["ended"][which] = record["started"][which] - 1
        elif action == "overbound":
            record["ended"][which] = record["started"][which] + max_interval_ns + 1
        else:
            pytest.fail("unhandled fine clock mutation")
    else:
        pytest.fail("unhandled clock mutation")


CLOCK_FAILURES = [
    "missing", "extra", "unsupported", "wrong_id", "wrong_name", "negative", "negative_outer",
    "backward", "backward_outer", "overbound", "overbound_outer", "overbound_resolution",
    "late_outer_start", "early_outer_end",
    *("missing:" + key for key in audit_clock()),
    *(kind + ":" + key for key in audit_clock() if key != "clock_name" for kind in ("bool", "float")),
    *("fine_" + kind + ":" + key for key in ("utc_ns", "monotonic_ns")
      for kind in ("missing", "bool", "float", "negative", "backward", "overbound")),
]


@pytest.mark.parametrize("implementation", [setup_policy])
@pytest.mark.parametrize("change", CLOCK_FAILURES)
def test_both_negative_implementations_reject_invalid_clock_evidence(implementation, change):
    record = negative()
    damage_clock(record, change, 30_000_000_000)
    with pytest.raises((ProbeError, setup_policy.SetupError)):
        implementation.validate_negative_control(record)


@pytest.mark.parametrize("change", CLOCK_FAILURES)
def test_boundary_witness_requires_five_second_closed_coarse_contract(change):
    record = boundary()
    witness = record["payload"]["witness"]
    evidence = {"audit_clock": witness["net_audit_clock"],
                "started": witness["net_started"], "ended": witness["net_ended"]}
    damage_clock(evidence, change, 5_000_000_000)
    if "audit_clock" not in evidence:
        witness.pop("net_audit_clock")
    with pytest.raises(ProbeError):
        payload.validate_witness(witness)
    with pytest.raises(ProbeError):
        probes.validate_boundary_record(record)


def test_boundary_audit_uses_coarse_bracket_not_fine_or_journal_bounds():
    record = boundary()
    witness = record["payload"]["witness"]
    witness["net_audit_clock"].update(before_ns=1791466300337123000, after_ns=1791466300337123000)
    witness["net_started"]["utc_ns"] = 1791466300338883060
    witness["net_ended"]["utc_ns"] = 1791466300338910261
    record["audit"] = [audit(500, cap=21, name="sys_admin", profile="unpriv_bwrap", instant="1791466300.337")]
    probes.validate_boundary_record(record)
    for instant in ("1791466300.338", "1791466300.337123456"):
        record["audit"] = [audit(500, cap=21, name="sys_admin", profile="unpriv_bwrap", instant=instant)]
        with pytest.raises(ProbeError):
            probes.validate_boundary_record(record)


@pytest.mark.parametrize("implementation", [payload, setup_policy])
@pytest.mark.parametrize("maximum", [5_000_000_000, 30_000_000_000])
def test_audit_clock_exact_finite_bound_is_inclusive(implementation, maximum):
    value = audit_clock(monotonic_before=100, monotonic_after=100 + maximum)
    value.update(after_ns=value["before_ns"] + maximum, resolution_ns=maximum)
    started = {"utc_ns": value["before_ns"], "monotonic_ns": 100}
    ended = {"utc_ns": value["after_ns"], "monotonic_ns": 100 + maximum}
    implementation.validate_audit_clock(value, started, ended, maximum)


@pytest.mark.parametrize("implementation", [payload, setup_policy])
@pytest.mark.parametrize("failure", ["platform", "unavailable", "zero", "negative", "infinite", "nan", "fraction", "overbound", "bad_sample"])
def test_coarse_sampling_fails_without_supported_clock(implementation, failure, monkeypatch):
    monkeypatch.setattr(implementation.time, "time_ns", lambda: pytest.fail("no fine fallback"))
    monkeypatch.setattr(implementation.time, "clock_gettime_ns", lambda clock: True if failure == "bad_sample" else 1)
    if failure == "platform":
        monkeypatch.setattr(implementation.sys, "platform", "unsupported")
    def resolution(clock):
        assert clock == 5
        if failure == "unavailable":
            raise OSError(errno.EINVAL, "unsupported")
        return {"zero": 0.0, "negative": -0.001, "infinite": float("inf"), "nan": float("nan"),
                "fraction": 0.0000000005, "overbound": 31.0}.get(failure, 0.001)
    monkeypatch.setattr(implementation.time, "clock_getres", resolution)
    with pytest.raises((ProbeError, setup_policy.SetupError)):
        implementation.begin_audit_clock(30_000_000_000)


@pytest.mark.parametrize("implementation", [payload, setup_policy])
def test_coarse_sampling_brackets_precise_clock_order(implementation, monkeypatch):
    calls = []
    def coarse(clock):
        assert clock == 5
        calls.append("coarse")
        return 1700000000123000000
    def monotonic():
        calls.append("monotonic")
        return 100
    monkeypatch.setattr(implementation.time, "clock_getres", lambda clock: 0.001 if clock == 5 else pytest.fail("wrong ID"))
    monkeypatch.setattr(implementation.time, "clock_gettime_ns", coarse)
    monkeypatch.setattr(implementation.time, "monotonic_ns", monotonic)
    value = implementation.begin_audit_clock(30_000_000_000)
    calls.extend(["precise_start", "operation", "precise_end"])
    implementation.end_audit_clock(value)
    assert calls == ["monotonic", "coarse", "precise_start", "operation", "precise_end", "coarse", "monotonic"]
    assert set(value) == set(audit_clock())
