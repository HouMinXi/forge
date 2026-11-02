"""Wrapper contracts with source-derived fixtures; no native service is run."""

import hashlib
import json
import os
from pathlib import Path
import sys
import time

import pytest
import yaml

from scripts import run_user_service_observation as wrapper
from scripts import strict_user_service_probe as probe

ROOT = Path(__file__).resolve().parents[1]


def declared_context():
    return {
        "repository": "HouMinXi/forge",
        "run_id": "123",
        "run_attempt": 1,
        "job_name": "observe",
        "workflow_ref": "HouMinXi/forge/" + wrapper.WORKFLOW + "@refs/pull/1/merge",
        "workflow_sha": "b" * 40,
        "source_sha": "a" * 40,
        "event_name": "pull_request",
    }


def fixture_receipt(context, status="OBSERVED"):
    uid, gid = os.getuid(), os.getgid()
    unit = "forge-topology-" + "d" * 32 + ".service"
    destination = probe.manager_root(uid)
    group = destination + "/app.slice/" + unit
    actor = {
        "context": context,
        "uid": uid,
        "gid": gid,
        "pid": 123,
        "starttime": 1,
        "boot_id": "00000000-0000-0000-0000-000000000000",
        "namespaces": {"mnt": 1, "cgroup": 2},
        "topology": {
            "source": group,
            "destination": destination,
            "common": destination,
            "controllers": ["memory", "pids"],
            "subtree_control": ["memory", "pids"],
            "directory_access": True,
            "destination_access": True,
            "common_access": True,
        },
    }
    error = {"reason": None, "errno": None, "exit_code": None, "signal": None, "child_cleanup": None}
    value = {
        "schema": probe.SCHEMA,
        "context": context,
        "context_authentication": "UNVERIFIED_DECLARED_CONTEXT",
        "unit": unit,
        "owner_uid": uid,
        "owner_gid": gid,
        "status": status,
        "probe_stage": "observed",
        "observation_status": "OBSERVED",
        "observation": actor,
        "binding": {
            "peer_pid": 123,
            "peer_uid": uid,
            "peer_gid": gid,
            "main_pid": 123,
            "starttime": 1,
            "boot_id": actor["boot_id"],
            "namespaces": actor["namespaces"],
            "control_group": group,
            "invocation": "c" * 32,
            "pidfd_before_ack": True,
            "pidfd_after_ack": True,
            "challenge_ack": True,
            "collector_rechecked": True,
        },
        "cleanup_evidence": {
            "unit": unit,
            "load_state": "not-found",
            "active_state": "inactive",
            "sub_state": "dead",
            "main_pid": 0,
            "control_group": group,
            "invocation": "c" * 32,
            "group_status": "absent",
        },
        "channel_cleanup_errors": [],
        "configuration_gate": {
            "status": "OBSERVED_CLEAN",
            "manager_pid": 100,
            "manager_starttime": 1,
            "version": "255.4",
            "unit_path_count": 1,
            "comment_file_count": 0,
            "fingerprint": "f" * 64,
            "prelaunch_rechecked": True,
            "postlaunch_rechecked": True,
        },
        "primary": None,
        "cleanup": {"status": "OBSERVED", **error},
        "duration_ms": 1,
        "boundary": "NOT_RUN",
        "qualification": "NOT_RUN",
    }
    if status == "STOP":
        value["primary"] = {**error, "reason": "topology_prerequisites"}
    return probe.validate_receipt(value)


def tree_bytes(root):
    rows = []
    for name in wrapper.EXECUTION_FILES:
        data = (root / name).read_bytes()
        digest = hashlib.sha1(
            b"blob " + str(len(data)).encode("ascii") + b"\0" + data, usedforsecurity=False
        ).hexdigest()
        rows.append(f"100644 blob {digest}\t{name}".encode("ascii") + b"\0")
    return b"".join(rows)


def capture_result(stdout=b"", returncode=0, reason="completed", stderr_bytes=0, cleanup_error=False):
    return {
        "stdout": stdout,
        "returncode": returncode,
        "reason": reason,
        "stderr_bytes": stderr_bytes,
        "cleanup_error": cleanup_error,
    }


@pytest.fixture
def workflow_case(monkeypatch, tmp_path):
    directory = tmp_path / "evidence"
    directory.mkdir(mode=0o700)
    context = declared_context()
    collector_context = {
        "source_sha": context["source_sha"],
        "collector_sha256": wrapper.source_hashes(ROOT)[wrapper.COLLECTOR],
        "run_id": context["run_id"],
        "run_attempt": context["run_attempt"],
    }
    state = {
        "directory": directory,
        "context": context,
        "calls": [],
        "collector": capture_result(probe.encode(fixture_receipt(collector_context))),
        "head": context["source_sha"],
    }
    monkeypatch.setattr(wrapper, "verified_tool", lambda path: path)

    def capture(argv, seconds, cwd):
        state["calls"].append(argv)
        if "rev-parse" in argv:
            return capture_result((state["head"] + "\n").encode("ascii"))
        if "ls-tree" in argv:
            return capture_result(tree_bytes(ROOT))
        assert "--allow-transient-service" in argv
        assert seconds == 90
        return state["collector"]

    monkeypatch.setattr(wrapper, "capture", capture)
    return state


def test_wrapper_offline_observed_preserves_nonqualification(workflow_case):
    state = workflow_case
    assert wrapper.run(ROOT, state["directory"], state["context"]) == 0
    status = json.loads((state["directory"] / "collector-status.json").read_text())
    envelope = json.loads((state["directory"] / "declared-context.json").read_text())
    assert status["status"] == "OBSERVED" and status["cleanup"] == "OBSERVED"
    assert status["boundary"] == status["qualification"] == "NOT_RUN"
    assert envelope["authentication"] == "UNVERIFIED_DECLARED_CONTEXT"
    assert set(envelope["checked_out_files_sha256"]) == set(wrapper.EXECUTION_FILES)
    assert "NOT_EXECUTED_WORKFLOW_AUTHENTICATION" in envelope["hash_scope"]
    assert all("diff" not in argv and "status" not in argv for argv in state["calls"])
    assert "--no-optional-locks" in state["calls"][0]


def test_wrapper_stop_is_nonzero_not_green(workflow_case):
    state = workflow_case
    receipt = probe.parse_receipt(state["collector"]["stdout"])
    receipt["status"] = "STOP"
    receipt["primary"] = {
        "reason": "topology_prerequisites",
        "errno": None,
        "exit_code": None,
        "signal": None,
        "child_cleanup": None,
    }
    state["collector"] = capture_result(probe.encode(probe.validate_receipt(receipt)), 2)
    assert wrapper.run(ROOT, state["directory"], state["context"]) == 2
    status = json.loads((state["directory"] / "collector-status.json").read_text())
    assert status["reason"] == "collector_stop" and status["cleanup"] == "OBSERVED"


@pytest.mark.parametrize(
    "fault",
    [
        "timeout",
        "output_limit",
        "capture_error",
        "reap",
        "stderr",
        "missing",
        "invalid",
        "wrong_context",
        "wrong_exit",
    ],
)
def test_wrapper_uncertainty_stays_stop(workflow_case, fault):
    state = workflow_case
    if fault in {"timeout", "output_limit", "capture_error"}:
        state["collector"]["reason"] = fault
    elif fault == "reap":
        state["collector"]["cleanup_error"] = True
    elif fault == "stderr":
        state["collector"]["stderr_bytes"] = 1
    elif fault == "missing":
        state["collector"]["stdout"] = b""
    elif fault == "invalid":
        state["collector"]["stdout"] = b'{"status":"PASS","qualification":"PASS"}\n'
    elif fault == "wrong_exit":
        state["collector"]["returncode"] = 2
    else:
        value = probe.parse_receipt(state["collector"]["stdout"])
        value["context"]["run_id"] = "999"
        value["observation"]["context"]["run_id"] = "999"
        state["collector"]["stdout"] = probe.encode(value)
    assert wrapper.run(ROOT, state["directory"], state["context"]) == 2
    status = json.loads((state["directory"] / "collector-status.json").read_text())
    assert status["status"] == "STOP"
    if fault != "wrong_exit":
        assert status["cleanup"] == "UNKNOWN"
        assert not (state["directory"] / "observation.json").exists()


def test_wrong_head_never_imports_or_starts_collector(workflow_case, monkeypatch):
    state = workflow_case
    state["head"] = "0" * 40
    monkeypatch.setattr(
        wrapper, "load_collector", lambda root: pytest.fail("collector import before source binding")
    )
    assert wrapper.run(ROOT, state["directory"], state["context"]) == 2
    assert len(state["calls"]) == 2
    status = json.loads((state["directory"] / "collector-status.json").read_text())
    assert status["cleanup"] == "NOT_RUN" and status["reason"] == "checkout_mismatch"


def test_source_change_after_collector_does_not_emit_observation(workflow_case, monkeypatch):
    original = wrapper.source_hashes
    calls = []

    def hashes(root):
        calls.append(1)
        value = original(root)
        if len(calls) == 3:
            value[wrapper.WRAPPER] = "0" * 64
        return value

    monkeypatch.setattr(wrapper, "source_hashes", hashes)
    state = workflow_case
    assert wrapper.run(ROOT, state["directory"], state["context"]) == 2
    assert not (state["directory"] / "observation.json").exists()


@pytest.mark.parametrize("fault", ["symlink", "mode", "blob", "missing", "duplicate"])
def test_git_tree_verification_rejects_mismatch(fault):
    raw = tree_bytes(ROOT)
    if fault == "symlink":
        raw = raw.replace(b"100644", b"120000", 1)
    elif fault == "mode":
        raw = raw.replace(b"100644", b"100755", 1)
    elif fault == "blob":
        raw = raw.replace(raw.split(b" ", 2)[2][:40], b"0" * 40, 1)
    elif fault == "missing":
        raw = raw.split(b"\0", 1)[1]
    else:
        raw += raw.split(b"\0", 1)[0] + b"\0"
    with pytest.raises(wrapper.WrapperError):
        wrapper.verify_tree(ROOT, raw)


def test_git_tree_verification_matches_real_local_bytes():
    wrapper.verify_tree(ROOT, tree_bytes(ROOT))


def test_real_capture_drains_overflow_until_child_cleanup(tmp_path):
    marker = tmp_path / "finished"
    body = f"import os;os.write(1,b'x'*20000);open({str(marker)!r},'w').write('done')"
    result = wrapper.capture([sys.executable, "-I", "-S", "-c", body], 2, tmp_path)
    assert result["reason"] == "output_limit"
    assert len(result["stdout"]) == wrapper.LIMIT
    assert result["returncode"] == 0 and marker.read_text() == "done"


def test_real_capture_discards_stderr(tmp_path):
    result = wrapper.capture(
        [sys.executable, "-I", "-S", "-c", "import os;os.write(2,b'private-secret'*1000)"], 2, tmp_path
    )
    assert result["reason"] == "output_limit"
    assert result["stderr_bytes"] == wrapper.STDERR_LIMIT + 1
    assert "private-secret" not in repr(result)


def test_real_capture_timeout_reaps_only_direct_child(tmp_path):
    start = time.monotonic()
    result = wrapper.capture(
        [sys.executable, "-I", "-S", "-c", "import time;time.sleep(10)"], 0.05, tmp_path
    )
    assert result["reason"] == "timeout" and result["returncode"] < 0
    assert not result["cleanup_error"] and time.monotonic() - start < 3


def test_private_artifact_names_cannot_overwrite(tmp_path):
    tmp_path.chmod(0o700)
    wrapper.write_record(tmp_path, "collector-status.json", {"status": "STOP"})
    with pytest.raises(FileExistsError):
        wrapper.write_record(tmp_path, "collector-status.json", {"status": "OBSERVED"})


@pytest.mark.parametrize(
    "key,value",
    [
        ("repository", "other/forge"),
        ("source_sha", "$(bad)"),
        ("run_attempt", "0"),
        ("event_name", "pull_request_target"),
        ("workflow_ref", "bad"),
        ("job_name", "other"),
    ],
)
def test_context_closed_contract(monkeypatch, key, value):
    context = declared_context()
    context[key] = value
    for name, env_name in wrapper.CONTEXT_FIELDS.items():
        monkeypatch.setenv(env_name, str(context[name]))
    with pytest.raises(wrapper.WrapperError):
        wrapper.context_from_environment()


def test_context_is_declaration_and_does_not_read_other_environment(monkeypatch):
    context = declared_context()
    for name, env_name in wrapper.CONTEXT_FIELDS.items():
        monkeypatch.setenv(env_name, str(context[name]))
    monkeypatch.setenv("PRIVATE_SECRET", "do-not-copy")
    assert wrapper.context_from_environment() == context
    assert "do-not-copy" not in repr(wrapper.ENV)
    assert wrapper.ENV["GIT_CONFIG_NOSYSTEM"] == "1"
    assert wrapper.ENV["GIT_CONFIG_GLOBAL"] == "/dev/null"


def test_workflow_narrow_effects_and_always_artifacts():
    raw = (ROOT / wrapper.WORKFLOW).read_text()
    workflow = yaml.safe_load(raw)
    assert set(workflow["on"]) == {"pull_request", "workflow_dispatch"}
    assert set(workflow["on"]["pull_request"]["paths"]) == {
        wrapper.WORKFLOW,
        wrapper.COLLECTOR,
        wrapper.WRAPPER,
        "tests/test_strict_user_service_probe.py",
        "tests/test_run_user_service_observation.py",
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] is False
    job = workflow["jobs"]["observe"]
    assert job["timeout-minutes"] == 3 and job["runs-on"] == "ubuntu-24.04"
    assert "head.repo.full_name == github.repository" in job["if"]
    checkout = job["steps"][1]
    assert checkout["with"]["persist-credentials"] is False
    assert "head.sha || github.sha" in checkout["with"]["ref"]
    upload = job["steps"][-1]
    assert upload["if"] == "${{ always() && env.SERVICE_EVIDENCE != '' }}"
    assert upload["with"]["if-no-files-found"] == "error"
    assert set(Path(line).name for line in upload["with"]["path"].splitlines()) == {
        "declared-context.json",
        "observation.json",
        "collector-status.json",
    }
    assert "${{ secrets." not in raw and "pull_request_target" not in raw


def test_wrapper_rejects_receipt_for_different_ordinary_uid(workflow_case, monkeypatch):
    state = workflow_case
    collector_context = probe.parse_receipt(state["collector"]["stdout"])["context"]
    actual_uid = os.getuid()
    with monkeypatch.context() as patch:
        patch.setattr(os, "getuid", lambda: actual_uid + 1)
        value = fixture_receipt(collector_context)
    state["collector"]["stdout"] = probe.encode(value)
    assert wrapper.run(ROOT, state["directory"], state["context"]) == 2
    result = json.loads((state["directory"] / "collector-status.json").read_text())
    assert result["reason"] == "receipt_identity_mismatch"
    assert result["cleanup"] == "UNKNOWN"


def test_source_only_loader_ignores_valid_header_unverified_pyc(tmp_path):
    import importlib.util
    from importlib import _bootstrap_external

    source = tmp_path / wrapper.COLLECTOR
    source.parent.mkdir()
    data = b"VALUE = 'verified-source'\n"
    source.write_bytes(data)
    cached = Path(importlib.util.cache_from_source(str(source)))
    cached.parent.mkdir()
    malicious = compile("raise AssertionError('unverified cache executed')", str(source), "exec")
    cached.write_bytes(
        _bootstrap_external._code_to_timestamp_pyc(malicious, int(source.stat().st_mtime), len(data))
    )
    loaded = wrapper.load_collector(tmp_path, hashlib.sha256(data).hexdigest())
    assert loaded.VALUE == "verified-source"
    assert loaded.__name__ != "__main__" and loaded.__file__ == str(source)


def test_source_only_loader_hash_failure_never_executes(tmp_path):
    source = tmp_path / wrapper.COLLECTOR
    source.parent.mkdir()
    source.write_text("raise AssertionError('unverified source executed')\n")
    with pytest.raises(wrapper.WrapperError, match="source_changed"):
        wrapper.load_collector(tmp_path, "0" * 64)


@pytest.mark.parametrize("fault", ["link", "writable", "oversize"])
def test_source_reader_rejects_unsafe_bytes(tmp_path, fault):
    source = tmp_path / wrapper.COLLECTOR
    source.parent.mkdir()
    if fault == "link":
        source.symlink_to(ROOT / wrapper.COLLECTOR)
    else:
        source.write_bytes(b"x" * 131073 if fault == "oversize" else b"VALUE=1\n")
        if fault == "writable":
            source.chmod(0o666)
    with pytest.raises(wrapper.WrapperError):
        wrapper.read_source(tmp_path, wrapper.COLLECTOR)


def test_source_reader_detects_change_during_read(tmp_path, monkeypatch):
    source = tmp_path / wrapper.COLLECTOR
    source.parent.mkdir()
    source.write_bytes(b"VALUE=1\n")
    original = wrapper.os.read

    def mutate(fd, limit):
        data = original(fd, limit)
        source.write_bytes(b"VALUE=222\n")
        return data

    monkeypatch.setattr(wrapper.os, "read", mutate)
    with pytest.raises(wrapper.WrapperError, match="source_changed"):
        wrapper.read_source(tmp_path, wrapper.COLLECTOR)


def test_unexpected_loader_error_is_finite_not_raw(workflow_case, monkeypatch):
    def fail(*args):
        raise RuntimeError("private-secret-from-unexpected-error")

    monkeypatch.setattr(wrapper, "load_collector", fail)
    state = workflow_case
    assert wrapper.run(ROOT, state["directory"], state["context"]) == 2
    raw = (state["directory"] / "collector-status.json").read_text()
    assert "private-secret" not in raw
    assert json.loads(raw)["reason"] == "wrapper_internal_error"


def test_wrapper_error_labels_never_serialize_unrecognized_values():
    assert str(wrapper.WrapperError("private-secret")) == "wrapper_internal_error"
    assert str(wrapper.WrapperError({"private": "secret"})) == "wrapper_internal_error"
