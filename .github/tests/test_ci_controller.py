"""Pure/mocked controller tests. No sudo, parser, probes, API or policy operation."""
from __future__ import annotations

import builtins
import copy
import errno
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from forge_ci import controller as c, launch, outcomes, payload, probes  # noqa: E402


@pytest.fixture(autouse=True)
def never_execute(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("test attempted a live command/probe/API operation")
    monkeypatch.setattr(subprocess, "Popen", denied)
    monkeypatch.setattr(os, "waitid", denied)
    monkeypatch.setattr(launch, "fetch_public_attempt", denied)
    monkeypatch.setattr(launch.http.client, "HTTPSConnection", denied)
    for name in ("run_negative_control", "run_positive_probe", "run_boundary_probe",
                 "read_kernel_audit"):
        monkeypatch.setattr(probes, name, denied)


def command(argv, stdout=b"", stderr=b"", returncode=0):
    return {"argv": argv, "returncode": returncode, "wrapper_pid": 123,
            "stdout": stdout.decode("utf-8", errors="replace"), "stdout_hex": stdout.hex(),
            "stderr": stderr.decode("utf-8", errors="replace"), "stderr_hex": stderr.hex(),
            "started": {"utc_ns": 10**18, "monotonic_ns": 100},
            "ended": {"utc_ns": 10**18 + 10**6, "monotonic_ns": 10**6 + 100}}


def audit(pid=456, *, cap=12, name="net_admin", profile="unprivileged_userns"):
    return (f'audit: type=1400 audit(1700000000.123:5): apparmor="DENIED" operation="capable" class="cap" '
            f'profile="{profile}" pid={pid} comm="bwrap" capability={cap} capname="{name}"')


def snapshot(pid=4, ppid=2, userns="user:[11]", netns="net:[12]", admin=False):
    status = {"NoNewPrivs": "1", "CapEff": f"{payload.CAP_SYS_ADMIN if admin else 0:016x}",
              "CapPrm": "0000000000000000", "CapInh": "0000000000000000", "CapAmb": "0000000000000000",
              "CapBnd": "000000ffffffffff", "Threads": "1"}
    return {"pid": pid, "ppid": ppid, "uid": os.getuid(), "euid": os.getuid(),
            "gid": os.getgid(), "egid": os.getgid(), "userns": userns, "netns": netns,
            "pidns": "pid:[13]", "label": payload.EXPECTED_LABEL, "status": status,
            "status_raw": "".join(key + ":\t" + value + "\n" for key, value in status.items()),
            "identity_mapping": {"uid_map_raw": f"{os.getuid()} {os.getuid()} 1\n", "gid_map_raw": f"{os.getgid()} {os.getgid()} 1\n",
                             "overflowuid_raw": "65534\n", "overflowgid_raw": "65534\n"},
        "utc_ns": 1700000000000000000, "monotonic_ns": 1000000000}


def network():
    def ip(options, values):
        result = command(["/usr/bin/ip", "-j", *options], json.dumps(values).encode())
        return {**result, "json": values}
    result = {"ip": "/usr/bin/ip", "before_netns": "net:[12]", "after_netns": "net:[12]",
              "ipv6_disabled": False, "ipv6_disable": {
                  "/proc/sys/net/ipv6/conf/" + key + "/disable_ipv6": "0" for key in ("all", "default", "lo")},
              "links": ip(["link", "show"], [{"ifname": "lo", "flags": ["LOOPBACK", "UP"], "link_type": "loopback"}])}
    for family in (4, 6):
        result[f"addresses{family}"] = ip([f"-{family}", "address", "show"], [{
            "ifname": "lo", "addr_info": [{"local": "127.0.0.1" if family == 4 else "::1"}]}])
        result[f"routes{family}"] = ip([f"-{family}", "route", "show", "table", "all"], [])
    return result


def boundary():
    witness = {"token": "token", "namespace_pid": 5, "before": snapshot(5),
               "after_user": snapshot(5, userns="user:[99]", admin=True),
               "after_net": snapshot(5, userns="user:[99]", admin=True), "net_errno": errno.EPERM,
               "net_started": {"utc_ns": 1700000000123000000, "monotonic_ns": 300},
               "net_ended": {"utc_ns": 1700000000124000000, "monotonic_ns": 400}}
    return {"returncode": 0, "token": "token", "caller": snapshot(netns="net:[1]"),
            "witness_mapping": {"host_pid": 500, "namespace_pid": 5},
            "audit": [audit(500, cap=21, name="sys_admin", profile="unpriv_bwrap")],
            "payload": {"initial": snapshot(2, ppid=1), "reexec": snapshot(4), "network": network(),
                        "reexec_command": {"argv": ["/usr/bin/python3", "/workspace/probe.py", "reexec",
                                                   "--workspace", "/workspace"], "returncode": 0},
                        "witness": witness, "witness_command": {"argv": ["/usr/bin/python3", "/workspace/probe.py",
                            "witness", "--workspace", "/workspace", "--token", "token", "--deadline", "20.0"],
                                                               "returncode": 0}}}


class FakeRuntime:
    def __init__(self, calls):
        self.calls = calls
        self.boot = "12345678-1234-1234-1234-123456789012"
        self.launch_result = {"status": "PASS", "policy_authorized": False, "nonce": "a" * 32,
                              "control_sha": "b" * 40, "source_sha256": "c" * 64,
                              "run_id": 42, "run_attempt": 1, "job": "qualify"}
        self.positive_result = command(probes.production_probe_argv())
        self.boundary_result = boundary()
        self.fail = None
        self.launch_calls = 0

    def _call(self, name):
        self.calls.append(name)
        if self.fail == name:
            raise RuntimeError("injected " + name + " failure")

    def launch(self):
        self._call("launch")
        self.launch_calls += 1
        return copy.deepcopy(self.launch_result)

    def boot_id(self):
        self._call("boot")
        return self.boot


    def positive(self, evidence):
        self._call("positive")
        return copy.deepcopy(self.positive_result)

    def boundary(self, root, evidence):
        self._call("boundary")
        return copy.deepcopy(self.boundary_result)


class FakeGate:
    def __init__(self, calls, binding):
        self.calls = calls
        self.receipt = {"schema_version": 2, "status": "PASS", "setup_receipt_sha256": "d" * 64,
                        "setup_policy_sha256": "e" * 64,
                        "cgroup_root": f"/sys/fs/cgroup/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service",
                        "binding": binding}
        self.fail = None
        self.mutate = lambda name, result: None

    def _call(self, name):
        self.calls.append(name)
        if self.fail == name:
            raise RuntimeError("injected " + name + " failure")
        result = copy.deepcopy(self.receipt if name == "prepare" else {"status": "PASS", "binding": self.receipt["binding"]})
        self.mutate(name, result)
        return result

    def prepare(self):
        return self._call("prepare")


    def final_source_recheck(self, receipt):
        return self._call("source")


@pytest.fixture
def setup(tmp_path):
    calls = []
    runtime = FakeRuntime(calls)
    binding = c._binding(runtime.launch_result, runtime.boot)
    gate = FakeGate(calls, binding)
    instance = c.Controller(gate, runtime, c.Evidence(tmp_path / "evidence"))
    return instance, gate, runtime, calls


def stopped(instance, result):
    assert result["status"] == "STOP", result
    assert result["qualified"] is False
    assert result["qualification_complete"] is False
    assert json.loads((instance.evidence.path / "stop.json").read_text()) == result
    assert not (instance.evidence.path / "tests-passed.json").exists()


def test_success_ready_is_not_full_suite_or_artifact_pass(setup):
    instance, gate, runtime, calls = setup
    result = instance.qualify()
    assert result["status"] == "QUALIFICATION_READY", result
    assert result["boundary_proved"] is True
    assert result["setup_receipt_sha256"] == "d" * 64
    assert result["setup_policy_sha256"] == "e" * 64
    assert not result["qualified"] and not result["qualification_complete"] and not result["full_suite_passed"]
    assert not result["evidence_upload_verified"]
    assert calls == ["launch", "boot", "prepare", "positive", "boundary"]
    assert not (instance.evidence.path / "compile.stdout").exists()
    assert not (instance.evidence.path / "parser.conf").exists()
    assert stat_mode(instance.evidence.path) == 0o700


def stat_mode(path):
    return path.stat().st_mode & 0o777


@pytest.mark.parametrize("stage", ["launch", "boot", "prepare", "positive", "boundary"])
def test_every_failed_stage_stops_and_preserves_evidence(setup, stage):
    instance, gate, runtime, calls = setup
    (gate if stage == "prepare" else runtime).fail = stage
    stopped(instance, instance.qualify())
    assert calls[-1] == stage
    assert "load" not in calls
    assert instance.load_attempted is (True if stage in {"positive", "boundary"} else None)


@pytest.mark.parametrize("field,value", [("status", "COLLECTED_UNREVIEWED"), ("schema_version", True),
    ("setup_receipt_sha256", "0" * 64), ("setup_policy_sha256", True), ("cgroup_root", "/sys/fs/cgroup"),
    ("binding", {}), ("binding", {"run_id": 42})])
def test_invalid_admission_receipt_cannot_reach_production_probe(setup, field, value):
    instance, gate, runtime, calls = setup
    gate.receipt[field] = value
    stopped(instance, instance.qualify())
    assert calls[-1] == "prepare"


def test_boolean_run_binding_cannot_equal_integer_receipt(setup):
    instance, gate, runtime, calls = setup
    gate.receipt["binding"]["run_attempt"] = True
    stopped(instance, instance.qualify())
    assert "positive" not in calls


@pytest.mark.parametrize("gate_method", ["prepare"])
@pytest.mark.parametrize("result_kind", ["missing_status", "wrong_binding", "empty", "false_pass"])
def test_malformed_gate_results_are_never_truthy_authorization(setup, gate_method, result_kind):
    instance, gate, runtime, calls = setup
    def mutate(name, result):
        if name == gate_method:
            if result_kind == "missing_status":
                result.pop("status")
            elif result_kind == "wrong_binding":
                result["binding"]["boot_id"] = "22345678-1234-1234-1234-123456789012"
            elif result_kind == "false_pass":
                result["status"] = True
            else:
                result.clear()
    gate.mutate = mutate
    stopped(instance, instance.qualify())
    assert calls[-1] == gate_method
    assert "positive" not in calls


@pytest.mark.parametrize("kind", ["existing", "symlink", "parent_symlink"])
def test_evidence_must_be_fresh_owned_canonical_directory(tmp_path, kind):
    path = tmp_path / "evidence"
    if kind == "existing":
        path.mkdir()
    elif kind == "symlink":
        path.symlink_to(tmp_path, target_is_directory=True)
    else:
        (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
        path = tmp_path / "link" / "evidence"
    with pytest.raises((OSError, c.ControllerError)):
        c.Evidence(path)


@pytest.mark.parametrize("stage", ["launch", "positive", "boundary"])
@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit, c.Cancelled])
def test_cancellation_is_stop_not_a_retry(setup, monkeypatch, stage, error):
    instance, gate, runtime, calls = setup
    old = runtime._call
    def fail(name):
        old(name)
        if name == stage:
            raise error("cancelled")
    monkeypatch.setattr(runtime, "_call", fail)
    stopped(instance, instance.qualify())
    assert calls[-1] == stage
    assert "load" not in calls


@pytest.mark.parametrize("kind", ["no_reexec", "witness_cap", "witness_success", "no_audit", "network_external", "network_command_failed", "label", "no_new_privs"])
def test_boundary_evidence_is_independently_revalidated(setup, kind):
    instance, gate, runtime, calls = setup
    record = runtime.boundary_result
    result = record["payload"]
    if kind == "no_reexec":
        result["reexec_command"]["returncode"] = 1
    elif kind == "witness_cap":
        result["witness"]["after_user"]["status"]["CapEff"] = "0" * 16
    elif kind == "witness_success":
        result["witness"]["net_errno"] = 0
    elif kind == "no_audit":
        record["audit"] = []
    elif kind == "network_external":
        result["network"]["links"]["json"].append({"ifname": "eth0"})
    elif kind == "network_command_failed":
        result["network"]["routes6"]["returncode"] = 127
    elif kind == "label":
        result["reexec"]["label"] = "unconfined"
    else:
        result["initial"]["status"]["NoNewPrivs"] = "0"
    stopped(instance, instance.qualify())
    assert "ready.json" not in {item.name for item in instance.evidence.path.iterdir()}


def test_missing_gate_module_cli_is_fail_closed(tmp_path, monkeypatch):
    real_import = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == "admission":
            raise ModuleNotFoundError("deliberately absent admission module")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", missing)
    path = tmp_path / "evidence"
    assert c.main(["--manifest", str(tmp_path / "not-read"), "--repo", str(tmp_path),
                   "--evidence", str(path)]) == 1
    assert json.loads((path / "stop.json").read_text())["status"] == "STOP"


def phase_evidence(instance):
    directory = instance.evidence.path
    events = {"schema_version": 1, "required_nodeids": list(outcomes.REQUIRED_NODEIDS),
              "session": {"started": True, "collection_complete": True,
                          "collected": dict.fromkeys(outcomes.REQUIRED_NODEIDS, 1), "finished": True, "exitstatus": 0},
              "events": [{"nodeid": nodeid, "when": phase, "outcome": "passed", "wasxfail_present": False,
                          "wasxfail": None} for nodeid in outcomes.REQUIRED_NODEIDS for phase in outcomes.PHASES],
              "errors": [], "overflow": False}
    events_raw = json.dumps(events).encode()
    (directory / "required-events.json").write_bytes(events_raw)
    receipts = []
    previous = instance.ready["ended"]
    for phase, filename in c.PHASE_FILES.items():
        suite = ET.Element("testsuite")
        nodeids = outcomes.REQUIRED_NODEIDS if phase == "full" else ("tests/synthetic.py::test_ok",)
        for nodeid in nodeids:
            file, name = nodeid.split("::")
            ET.SubElement(suite, "testcase", classname=file[:-3].replace("/", "."), name=name)
        raw = ET.tostring(suite)
        (directory / filename).write_bytes(raw)
        started = {name: value + 100 for name, value in previous.items()}
        ended = {name: value + 100 for name, value in started.items()}
        previous = ended
        log = b"synthetic pytest output\n"
        (directory / Path(filename).with_suffix(".log")).write_bytes(log)
        receipts.append({"phase": phase, "binding": copy.deepcopy(instance.binding),
                         "argv": c.phase_argv(phase, directory), "exit_code": 0, "completed": True,
                         "cancelled": False, "timed_out": False, "started": started, "ended": ended,
                         "junit_sha256": c.sha256(raw), "observer_sha256": c.sha256(events_raw) if phase == "full" else None,
                         "log_sha256": c.sha256(log), "log_bytes": len(log), "error": None})
    return receipts


def test_finalize_requires_all_three_and_eleven_but_not_yet_upload(setup):
    instance, gate, runtime, calls = setup
    assert instance.qualify()["status"] == "QUALIFICATION_READY"
    result = instance.finalize(phase_evidence(instance))
    assert result["status"] == "TESTS_PASSED", result
    assert result["qualification_complete"] and result["full_suite_passed"]
    assert not result["qualified"] and not result["evidence_upload_verified"]
    assert result["required_count"] == 11
    assert list(result["phases"]) == list(c.PHASE_FILES)
    assert calls[-3:] == ["launch", "boot", "source"]


@pytest.mark.parametrize("phase", [0, 1, 2])
@pytest.mark.parametrize("kind", ["exit", "missing", "duplicate", "cancel", "timeout", "incomplete", "argv", "binding", "bool_binding", "hash", "bool_exit", "times", "before_boundary", "extra_field"])
def test_finalize_rejects_incomplete_or_stale_phase_receipts(setup, phase, kind):
    instance, gate, runtime, calls = setup
    instance.qualify()
    receipts = phase_evidence(instance)
    receipt = receipts[phase]
    if kind == "exit":
        receipt["exit_code"] = 1
    elif kind == "missing":
        receipts.pop(phase)
    elif kind == "duplicate":
        receipts[phase] = receipts[(phase + 1) % 3]
    elif kind == "cancel":
        receipt["cancelled"] = True
    elif kind == "timeout":
        receipt["timed_out"] = True
    elif kind == "incomplete":
        receipt["completed"] = False
    elif kind == "argv":
        receipt["argv"].append("-k=no_real")
    elif kind == "binding":
        receipt["binding"]["nonce"] = "d" * 32
    elif kind == "bool_binding":
        receipt["binding"]["run_attempt"] = True
    elif kind == "hash":
        receipt["junit_sha256"] = "0" * 64
    elif kind == "bool_exit":
        receipt["exit_code"] = False
    elif kind == "times":
        receipt["ended"]["monotonic_ns"] += 2500 * 10**9
    elif kind == "before_boundary":
        receipt["started"]["monotonic_ns"] = 1
    else:
        receipt["arbitrary_file"] = "/unapproved/other.xml"
    stopped(instance, instance.finalize(receipts))


@pytest.mark.parametrize("phase", [0, 1, 2])
@pytest.mark.parametrize("kind", ["failure", "error", "empty", "entity", "malformed", "symlink", "summary"])
def test_all_three_junits_are_validated_and_hash_bound(setup, phase, kind):
    instance, gate, runtime, calls = setup
    instance.qualify()
    receipts = phase_evidence(instance)
    path = instance.evidence.path / c.PHASE_FILES[receipts[phase]["phase"]]
    if kind in {"failure", "error"}:
        suite = ET.fromstring(path.read_bytes())  # noqa: S314 - synthetic local test fixture
        ET.SubElement(suite.find("testcase"), kind)
        raw = ET.tostring(suite)
    elif kind == "empty":
        raw = b"<testsuite/>"
    elif kind == "entity":
        raw = b'<!DOCTYPE x [<!ENTITY a "something">]><testsuite/>'
    elif kind == "malformed":
        raw = b"<testsuite"
    elif kind == "summary":
        suite = ET.fromstring(path.read_bytes())  # noqa: S314 - synthetic local test fixture
        suite.set("tests", "999")
        raw = ET.tostring(suite)
    else:
        target = path.with_suffix(".other")
        path.rename(target)
        path.symlink_to(target)
        raw = target.read_bytes()
    if kind != "symlink":
        path.write_bytes(raw)
    receipts[phase]["junit_sha256"] = c.sha256(raw)
    stopped(instance, instance.finalize(receipts))


@pytest.mark.parametrize("phase,success", [(0, False), (2, True)])
def test_only_ownership_forbids_all_skips(setup, phase, success):
    instance, gate, runtime, calls = setup
    instance.qualify()
    receipts = phase_evidence(instance)
    path = instance.evidence.path / c.PHASE_FILES[receipts[phase]["phase"]]
    suite = ET.fromstring(path.read_bytes())  # noqa: S314 - synthetic local test fixture
    ET.SubElement(suite.find("testcase"), "skipped")
    raw = ET.tostring(suite)
    path.write_bytes(raw)
    receipts[phase]["junit_sha256"] = c.sha256(raw)
    result = instance.finalize(receipts)
    assert (result["status"] == "TESTS_PASSED") is success


@pytest.mark.parametrize("kind", ["missing", "duplicate", "xfail", "xpass", "skip", "wrong_hash"])
def test_full_eleven_observer_gate_cannot_be_replaced_by_exit_zero(setup, kind):
    instance, gate, runtime, calls = setup
    instance.qualify()
    receipts = phase_evidence(instance)
    path = instance.evidence.path / "required-events.json"
    document = json.loads(path.read_bytes())
    if kind == "missing":
        document["events"].pop()
    elif kind == "duplicate":
        document["events"].append(document["events"][0])
    elif kind in {"xfail", "xpass"}:
        document["events"][0].update(wasxfail_present=True, wasxfail="expected")
    elif kind == "skip":
        document["events"][0]["outcome"] = "skipped"
    raw = json.dumps(document).encode()
    path.write_bytes(raw)
    receipts[1]["observer_sha256"] = c.sha256(raw) if kind != "wrong_hash" else "0" * 64
    stopped(instance, instance.finalize(receipts))


@pytest.mark.parametrize("kind", ["source_failure", "source_mismatch", "boot", "attempt"])
def test_post_test_source_and_current_identity_rechecked(setup, kind):
    instance, gate, runtime, calls = setup
    instance.qualify()
    receipts = phase_evidence(instance)
    if kind == "source_failure":
        gate.fail = "source"
    elif kind == "source_mismatch":
        gate.receipt["binding"]["source_sha256"] = "d" * 64
    elif kind == "boot":
        runtime.boot = "22345678-1234-1234-1234-123456789012"
    else:
        runtime.launch_result["run_attempt"] += 1
    stopped(instance, instance.finalize(receipts))


def test_no_finalize_without_positive_boundary_ready(setup):
    instance, gate, runtime, calls = setup
    stopped(instance, instance.finalize([]))
    assert calls == []


def test_cli_cancellation_handler_restores_original_handlers():
    before = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    with c.cancellation_guard():
        handler = signal.getsignal(signal.SIGTERM)
        with pytest.raises(c.Cancelled):
            handler(signal.SIGTERM, None)
    assert all(signal.getsignal(number) == handler for number, handler in before.items())


@pytest.mark.parametrize("phase", ["positive"])
@pytest.mark.parametrize("kind", ["missing_pid", "bad_utf8", "missing_raw", "raw_mismatch", "reversed_time"])
def test_probe_records_need_bounded_original_bytes_and_actual_command_metadata(setup, phase, kind):
    instance, gate, runtime, calls = setup
    record = getattr(runtime, phase + "_result")
    if kind == "missing_pid":
        record.pop("wrapper_pid")
    elif kind == "bad_utf8":
        raw = record["stderr"].encode() + b"\xff"
        record.update(stderr=raw.decode("utf-8", errors="replace"), stderr_hex=raw.hex())
    elif kind == "missing_raw":
        record.pop("stdout_hex")
    elif kind == "raw_mismatch":
        record["stdout"] = "text not present in original bytes"
    else:
        record["ended"]["utc_ns"] = record["started"]["utc_ns"] - 1
    stopped(instance, instance.qualify())
    assert calls[-1] == phase


def test_returned_ready_cannot_mutate_live_binding(setup):
    instance, gate, runtime, calls = setup
    result = instance.qualify()
    result["binding"]["run_id"] = 999
    result["ended"]["monotonic_ns"] = 1
    assert instance.binding["run_id"] == 42
    assert instance.ready["ended"]["monotonic_ns"] != 1


def test_outcome_second_read_must_match_phase_digest(setup, monkeypatch):
    instance, gate, runtime, calls = setup
    instance.qualify()
    receipts = phase_evidence(instance)
    real = outcomes.validate_outcomes
    def changed(*args):
        result = real(*args)
        result["sha256"]["events"] = "f" * 64
        return result
    monkeypatch.setattr(outcomes, "validate_outcomes", changed)
    stopped(instance, instance.finalize(receipts))
    assert "source" not in calls


def test_evidence_replacement_or_mode_change_is_not_silently_accepted(tmp_path):
    evidence = c.Evidence(tmp_path / "evidence")
    evidence.path.rename(tmp_path / "old-evidence")
    evidence.path.mkdir(mode=0o700)
    with pytest.raises(c.ControllerError, match="identity"):
        evidence.json("result.json", {"status": "PASS"})


@pytest.mark.parametrize("phase", ["prepare", "source"])
def test_gate_exception_never_fabricates_pass_evidence(setup, phase):
    instance, gate, runtime, calls = setup
    if phase == "source":
        instance.qualify()
        receipts = phase_evidence(instance)
        gate.fail = phase
        result = instance.finalize(receipts)
    else:
        gate.fail = phase
        result = instance.qualify()
    stopped(instance, result)
    assert result["error_type"] == "RuntimeError"


def test_failed_attempt_cannot_retry_qualification(setup):
    instance, gate, runtime, calls = setup
    runtime.fail = "boundary"
    stopped(instance, instance.qualify())
    previous = list(calls)
    with pytest.raises(FileExistsError):
        instance.qualify()
    assert calls == previous and "load" not in calls


def test_root_caller_cannot_start_identity_or_admission(setup, monkeypatch):
    instance, gate, runtime, calls = setup
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    stopped(instance, instance.qualify())
    assert calls == []


def test_ownership_failure_does_not_change_full_and_integration_contract(setup):
    instance, gate, runtime, calls = setup
    instance.qualify()
    receipts = phase_evidence(instance)
    # All phases ran under the original workflow prerequisites. The finalizer
    # consumes those outcomes; it does not execute or suppress later phases.
    receipts[0]["exit_code"] = 1
    stopped(instance, instance.finalize(receipts))
    assert (instance.evidence.path / "pytest.xml").exists()
    assert (instance.evidence.path / "local-integration.xml").exists()


def test_live_driver_runs_all_three_then_finalizes_without_resume(setup, monkeypatch):
    instance, gate, runtime, calls = setup
    instance.qualify()
    receipts = phase_evidence(instance)
    def phase(name, evidence, binding):
        calls.append("phase-" + name)
        assert binding == instance.binding and evidence is instance.evidence
        return copy.deepcopy(receipts[list(c.PHASE_FILES).index(name)])
    monkeypatch.setattr(runtime, "phase", phase, raising=False)
    result = instance.run_tests()
    assert result["status"] == "TESTS_PASSED", result
    assert [name for name in calls if name.startswith("phase-")] == ["phase-" + name for name in c.PHASE_FILES]
    assert "load" not in calls


@pytest.mark.parametrize("failed_phase", [0, 1])
def test_live_driver_continues_later_phases_after_ordinary_failure(setup, monkeypatch, failed_phase):
    instance, gate, runtime, calls = setup
    instance.qualify()
    receipts = phase_evidence(instance)
    receipts[failed_phase]["exit_code"] = 1
    seen = []
    def phase(name, evidence, binding):
        seen.append(name)
        return copy.deepcopy(receipts[list(c.PHASE_FILES).index(name)])
    monkeypatch.setattr(runtime, "phase", phase, raising=False)
    stopped(instance, instance.run_tests())
    assert seen == list(c.PHASE_FILES)


@pytest.mark.parametrize("phase_index", [0, 1, 2])
@pytest.mark.parametrize("kind", ["cancelled", "timed_out", "output_overflow", "reap_failure", "exception"])
def test_live_driver_never_starts_later_phase_after_interruption(setup, monkeypatch, phase_index, kind):
    instance, gate, runtime, calls = setup
    instance.qualify()
    receipts = phase_evidence(instance)
    seen = []
    def phase(name, evidence, binding):
        seen.append(name)
        index = list(c.PHASE_FILES).index(name)
        result = copy.deepcopy(receipts[index])
        if index == phase_index:
            if kind == "exception":
                raise KeyboardInterrupt("cancelled while collecting direct phase receipt")
            result["completed"] = False
            if kind in {"cancelled", "timed_out"}:
                result[kind] = True
            result["error"] = kind
        return result
    monkeypatch.setattr(runtime, "phase", phase, raising=False)
    stopped(instance, instance.run_tests())
    assert seen == list(c.PHASE_FILES)[:phase_index + 1]


def test_live_driver_requires_ready_before_any_phase(setup):
    instance, gate, runtime, calls = setup
    stopped(instance, instance.run_tests())
    assert calls == []


@pytest.fixture
def phase_process(tmp_path, monkeypatch):
    evidence = c.Evidence(tmp_path / "phase-evidence")
    control = {"mode": "success", "chunks": [b"pytest output\n", b""], "killed": [], "waits": [],
               "popen": [], "clock": 100.0, "exit": 0, "phase": "ownership", "evidence": evidence, "ownership_checks": [], "waitid_gone": False}
    class Pipe:
        closed = False
        def fileno(self):
            return 900
        def close(self):
            self.closed = True
    class Process:
        pid = 12345
        returncode = None
        stdout = Pipe()
        def wait(self, *, timeout):
            control["waits"].append(timeout)
            if control["mode"] == "reap_failure" or (control["mode"] == "wait_timeout" and len(control["waits"]) == 1):
                raise subprocess.TimeoutExpired("fixed pytest phase", timeout)
            self.returncode = -9 if control["killed"] else control["exit"]
            path = control["evidence"].path / c.PHASE_FILES[control["phase"]]
            path.write_bytes(b'<testsuite><testcase classname="synthetic" name="ok"/></testsuite>')
            if control["phase"] == "full":
                (control["evidence"].path / "required-events.json").write_bytes(b"{}")
            return self.returncode
    process = Process()
    class Selector:
        active = False
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def register(self, pipe, flags):
            assert pipe is process.stdout and flags == c.selectors.EVENT_READ
            self.active = True
        def unregister(self, pipe):
            assert pipe is process.stdout
            self.active = False
        def get_map(self):
            return {900: True} if self.active else {}
        def select(self, timeout):
            if control["mode"] == "deadline":
                control["clock"] += c.PHASE_SECONDS[control["phase"]] + 1
                return []
            if control["mode"] == "interrupt":
                raise KeyboardInterrupt("phase interrupted")
            if control["mode"] == "reap_failure":
                raise c.Cancelled("cancelled with a stuck reaper")
            return [(SimpleNamespace(fileobj=process.stdout), c.selectors.EVENT_READ)]
    def popen(argv, **kwargs):
        control["popen"].append((argv, kwargs))
        if control["mode"] == "startup":
            raise OSError("cannot start fixed test interpreter")
        return process
    def read(fd, limit):
        assert fd == 900 and limit == 65536
        return control["chunks"].pop(0)
    def waitid(idtype, pid, options):
        assert idtype == os.P_PID and pid == process.pid
        assert options == os.WEXITED | os.WNOHANG | os.WNOWAIT
        control["ownership_checks"].append((idtype, pid, options))
        if control["waitid_gone"]:
            raise ChildProcessError("leader already reaped before returncode assignment")
        return None
    def killpg(pid, signum):
        assert pid == process.pid and signum == signal.SIGKILL
        control["killed"].append((pid, signum))
        if control["mode"] == "already_exited":
            raise ProcessLookupError("already exited")
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(c.selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(c.os, "set_blocking", lambda fd, blocking: None)
    monkeypatch.setattr(c.os, "read", read)
    monkeypatch.setattr(c.os, "killpg", killpg)
    monkeypatch.setattr(c.os, "waitid", waitid)
    monkeypatch.setattr(c.time, "monotonic", lambda: control["clock"])
    return evidence, process, control


@pytest.mark.parametrize("name", list(c.PHASE_FILES))
def test_phase_supervisor_uses_only_fixed_argv_owned_group_and_current_interpreter(phase_process, name, monkeypatch):
    offline = {"SEMGREP_SEND_METRICS": "off", "SEMGREP_ENABLE_VERSION_CHECK": "0", "OTEL_SDK_DISABLED": "true"}
    for key, value in offline.items():
        monkeypatch.setenv(key, value)
    evidence, process, control = phase_process
    control["phase"] = name
    binding = {"synthetic": "binding"}
    result = c.run_phase(name, evidence.path.parent, evidence, binding)
    assert result["completed"] and result["exit_code"] == 0, result
    assert not result["cancelled"] and not result["timed_out"] and result["error"] is None
    assert result["binding"] == binding
    argv, kwargs = control["popen"][0]
    assert argv == c.phase_argv(name, evidence.path)
    assert kwargs["executable"] == sys.executable
    assert {key: kwargs["env"].get(key) for key in offline} == offline
    assert kwargs["cwd"] == evidence.path.parent
    assert kwargs["start_new_session"] is True and kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.STDOUT and kwargs["stdout"] is subprocess.PIPE
    assert kwargs["env"].get("FORGE_CI_REQUIRED_EVENTS") == (str(evidence.path / "required-events.json") if name == "full" else None)
    assert control["waits"] == [c.PHASE_SECONDS[name]]
    assert process.stdout.closed and control["killed"] == []
    assert result["log_bytes"] == len(b"pytest output\n")
    assert result["log_sha256"] == c.sha256(b"pytest output\n")
    assert json.loads((evidence.path / (name + "-phase.json")).read_bytes()) == result


@pytest.mark.parametrize("mode", ["deadline", "wait_timeout", "interrupt", "reap_failure", "startup", "overflow", "already_exited"])
def test_phase_supervisor_interrupts_bound_output_and_reaps_only_owned_group(phase_process, monkeypatch, mode):
    evidence, process, control = phase_process
    control["mode"] = mode
    if mode in {"overflow", "already_exited"}:
        monkeypatch.setattr(c, "PHASE_LOG_LIMIT", 4)
    result = c.run_phase("ownership", evidence.path.parent, evidence, {})
    assert result["completed"] is False and result["error"], result
    assert result["timed_out"] is (mode in {"deadline", "wait_timeout"})
    assert result["cancelled"] is (mode in {"interrupt", "reap_failure"})
    assert len(control["killed"]) == (0 if mode == "startup" else 1)
    if mode != "startup":
        assert control["waits"][-1] == 5
        assert process.stdout.closed
    if mode in {"overflow", "already_exited"}:
        assert result["log_bytes"] == 4
        assert (evidence.path / "ownership.log").read_bytes() == b"pyte"
    if mode == "reap_failure":
        assert "failed to terminate/reap owned phase" in result["error"]
    assert (evidence.path / "ownership-phase.json").exists()


def test_phase_nonzero_exit_is_a_completed_ordinary_failure(phase_process):
    evidence, process, control = phase_process
    control["exit"] = 1
    result = c.run_phase("ownership", evidence.path.parent, evidence, {})
    assert result["completed"] and result["exit_code"] == 1
    assert result["error"] is None and not control["killed"]


def test_phase_does_not_accept_preexisting_junit(phase_process):
    evidence, process, control = phase_process
    (evidence.path / "ownership.xml").write_bytes(b"stale")
    result = c.run_phase("ownership", evidence.path.parent, evidence, {})
    assert not result["completed"] and "fresh" in result["error"]
    assert control["popen"] == []


@pytest.mark.parametrize("kind", ["missing_log", "changed_hash", "changed_length", "symlink"])
def test_finalizer_verifies_direct_log_identity(setup, kind):
    instance, gate, runtime, calls = setup
    instance.qualify()
    receipts = phase_evidence(instance)
    path = instance.evidence.path / "ownership.log"
    if kind == "missing_log":
        path.unlink()
    elif kind == "changed_hash":
        receipts[0]["log_sha256"] = "f" * 64
    elif kind == "changed_length":
        receipts[0]["log_bytes"] += 1
    else:
        other = path.with_suffix(".other")
        path.rename(other)
        path.symlink_to(other)
    stopped(instance, instance.finalize(receipts))


@pytest.mark.parametrize("exit_code", [-signal.SIGTERM, -signal.SIGKILL, 2, 3, 4, 5, 127, None, False])
def test_actual_phase_supervisor_classifies_only_zero_and_one_as_completed(phase_process, exit_code):
    evidence, process, control = phase_process
    control["exit"] = exit_code
    result = c.run_phase("ownership", evidence.path.parent, evidence, {})
    assert result["completed"] is False and result["error"], result
    assert result["exit_code"] == exit_code
    assert result["cancelled"] is (type(exit_code) is int and (exit_code < 0 or exit_code == 2))
    assert control["killed"] == []  # The leader was already reaped by wait.


@pytest.mark.parametrize("exit_code", [-signal.SIGTERM, -signal.SIGKILL, 2, 3])
@pytest.mark.parametrize("failed_phase", [0, 1])
def test_actual_mocked_phase_signal_or_interrupt_never_starts_later_phase(setup, phase_process, monkeypatch,
                                                                       exit_code, failed_phase):
    instance, gate, runtime, calls = setup
    evidence, process, control = phase_process
    assert instance.qualify()["status"] == "QUALIFICATION_READY"
    control["evidence"] = instance.evidence
    seen = []
    def phase(name, current_evidence, binding):
        seen.append(name)
        control["phase"] = name
        control["exit"] = exit_code if len(seen) - 1 == failed_phase else 0
        control["chunks"] = [b"pytest operation evidence\n", b""]
        process.returncode = None
        return c.run_phase(name, current_evidence.path.parent, current_evidence, binding)
    monkeypatch.setattr(runtime, "phase", phase, raising=False)
    stopped(instance, instance.run_tests())
    assert seen == list(c.PHASE_FILES)[:failed_phase + 1]
    assert len(control["popen"]) == failed_phase + 1
    assert control["killed"] == []
    receipt = json.loads((instance.evidence.path / (seen[-1] + "-phase.json")).read_bytes())
    assert receipt["exit_code"] == exit_code and receipt["completed"] is False


@pytest.mark.parametrize("error", [OSError, KeyboardInterrupt, c.Cancelled])
def test_post_reap_fsync_failure_or_cancellation_never_signals_reused_group(phase_process, monkeypatch, error):
    evidence, process, control = phase_process
    original = c.os.fsync
    injected = False
    def failing(fd):
        nonlocal injected
        if not injected:
            injected = True
            assert process.returncode == 0  # wait already reaped the leader.
            raise error("injected after successful reap")
        return original(fd)
    monkeypatch.setattr(c.os, "fsync", failing)
    result = c.run_phase("ownership", evidence.path.parent, evidence, {})
    assert injected and result["completed"] is False and result["error"]
    assert result["exit_code"] == 0
    assert result["cancelled"] is (error in {KeyboardInterrupt, c.Cancelled})
    assert control["killed"] == []
    assert control["waits"] == [c.PHASE_SECONDS["ownership"]]  # No second wait/kill.


def test_kill_helper_checks_existing_returncode_without_poll_or_wait():
    def forbidden(*args, **kwargs):
        raise AssertionError("a reaped process must never be polled, waited, or signaled again")
    process = SimpleNamespace(pid=12345, returncode=0, poll=forbidden, wait=forbidden)
    c._kill_owned_phase(process)


def test_interrupted_waitpid_before_returncode_assignment_does_not_signal_reused_group(phase_process, monkeypatch):
    evidence, process, control = phase_process
    original_wait = process.wait
    def reaped_then_interrupted(*, timeout):
        original_wait(timeout=timeout)
        assert process.returncode == 0
        # Simulate waitpid having consumed the child, followed by a Python
        # signal handler raising before Popen's returncode assignment.
        process.returncode = None
        control["waitid_gone"] = True
        raise KeyboardInterrupt("interrupted returncode assignment")
    monkeypatch.setattr(process, "wait", reaped_then_interrupted)
    result = c.run_phase("ownership", evidence.path.parent, evidence, {})
    assert not result["completed"] and result["cancelled"] and result["error"]
    assert result["exit_code"] is None
    assert len(control["ownership_checks"]) == 1
    assert control["killed"] == []
    assert control["waits"] == [c.PHASE_SECONDS["ownership"]]


@pytest.mark.parametrize("exited_zombie", [False, True])
def test_owned_phase_signal_uses_nonreaping_check_before_kill_then_reap(monkeypatch, exited_zombie):
    seen = []
    process = SimpleNamespace(pid=12345, returncode=None)
    def waitid(idtype, pid, flags):
        seen.append("nonreaping-ownership")
        assert (idtype, pid, flags) == (os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        return SimpleNamespace(si_pid=pid) if exited_zombie else None
    def killpg(pid, signum):
        seen.append("signal-owned-group")
        assert pid == process.pid and signum == signal.SIGKILL
    def wait(*, timeout):
        seen.append("reap")
        assert timeout == 5
        process.returncode = -9
    def poll():
        raise AssertionError("poll would reap before the owned signal")
    process.wait, process.poll = wait, poll
    monkeypatch.setattr(c.os, "waitid", waitid)
    monkeypatch.setattr(c.os, "killpg", killpg)
    c._kill_owned_phase(process)
    assert seen == ["nonreaping-ownership", "signal-owned-group", "reap"]


def test_late_controller_exposes_no_policy_mutation_interface():
    assert not hasattr(c, "parser_argv")
    assert not hasattr(c, "validate_parser")
    assert not hasattr(c.Runtime, "parser")
    assert not hasattr(c.Runtime, "negative")
    assert not hasattr(c.Runtime, "python_context")
    assert not hasattr(c.Controller, "_parser")
    assert not hasattr(c.Controller, "_fresh_load_window")


@pytest.mark.parametrize("field", ["nonce", "control_sha", "source_sha256", "run_id", "run_attempt", "job", "boot_id"])
def test_early_sealed_setup_must_match_every_late_binding_field(setup, field):
    instance, gate, runtime, calls = setup
    value = gate.receipt["binding"][field]
    gate.receipt["binding"][field] = value + 1 if type(value) is int else "mismatch"
    stopped(instance, instance.qualify())
    assert not instance.load_attempted and "positive" not in calls


@pytest.mark.parametrize("field", ["setup_receipt_sha256", "setup_policy_sha256"])
@pytest.mark.parametrize("value", [None, False, 42, "", "0" * 64, "A" * 64, "a" * 63, "a" * 65])
def test_sealed_setup_digests_are_strict(setup, field, value):
    instance, gate, runtime, calls = setup
    gate.receipt[field] = value
    stopped(instance, instance.qualify())
    assert not instance.load_attempted and "positive" not in calls


def test_unverified_late_admission_does_not_claim_earlier_load_absence(setup):
    instance, gate, runtime, calls = setup
    gate.fail = "prepare"
    result = instance.qualify()
    stopped(instance, result)
    assert result["load_attempted"] is None
    assert result["late_load_attempted"] is False
