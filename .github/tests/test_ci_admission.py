"""Offline late admission tests. No source/API/policy operation occurs live."""
from __future__ import annotations
import copy
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from forge_ci import admission as a, launch, setup_policy as s  # noqa: E402


@pytest.fixture(autouse=True)
def no_live_commands(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("late admission test attempted live command/API")
    monkeypatch.setattr(subprocess, "Popen", denied)
    monkeypatch.setattr(launch.http.client, "HTTPSConnection", denied)


def binding():
    return {"nonce": "1" * 32, "control_sha": "c" * 40, "source_sha256": "b" * 64,
            "run_id": 123, "run_attempt": 2, "job": "qualification", "boot_id": "11111111-1111-1111-1111-111111111111"}


def config():
    owner = {"id": 19586012, "login": "HouMinXi", "type": "User"}
    return {"schema_version": 1, "nonce": "1" * 32, "seed_sha": "a" * 40, "source_sha256": "b" * 64,
            "repository": {"id": 1258832822, "name": "forge", "full_name": "HouMinXi/forge", "owner": owner},
            "publisher": dict(owner), "authorized_retrier": dict(owner)}


def document():
    cfg = config()
    repo = Path(s.__file__).resolve().parents[3]
    rule = a.manifest_rule(repo)
    hashes = dict.fromkeys(launch.HELPER_PATHS, "d" * 64)
    hashes[".github/scripts/forge_ci/setup_policy.py"] = rule["setup_module_sha256"]
    launch_rule = {k: cfg[k] for k in ("nonce", "seed_sha", "source_sha256", "repository", "publisher", "authorized_retrier")}
    launch_rule.update(ref="refs/heads/ci/qualify-apparmor-" + cfg["nonce"],
                       workflow_path=".github/workflows/qualify-apparmor-" + cfg["nonce"] + ".yml",
                       workflow_sha256="e" * 64, helper_sha256=hashes)
    return {"schema_version": 1, "launch": launch_rule, "admission": rule}


def observation():
    cfg = config()
    source = Path(s.__file__).read_text()
    observer = s.observer_source(source, cfg)
    policy = {"known": "synthetic policy"}
    seal = {"schema_version": 1, "status": "PASS", "binding": binding(), "config": cfg,
            "load_attempted": True, "positive_passed": True, "runner": {"uid": os.getuid(), "gid": os.getgid()},
            "setup_module_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "observer": {"sha256": hashlib.sha256(observer).hexdigest(), "bytes": len(observer)}, "after": policy}
    return {"schema_version": 1, "status": "PASS", "binding": binding(), "setup_receipt": seal,
            "setup_receipt_sha256": hashlib.sha256(s.canonical(seal) + b"\n").hexdigest(),
            "setup_policy_sha256": s.digest(policy), "observed": s.stamp(), "live": {}, "policy": policy}


def test_manifest_is_small_setup_schema_without_provider_inventory():
    doc = document()
    assert a.validate_manifest(doc) == doc["admission"]
    assert set(doc["admission"]) == {"schema_version", "setup_spec_sha256", "setup_module_sha256"}
    assert ".github/scripts/forge_ci/setup_policy.py" in launch.HELPER_PATHS
    assert not (Path(s.__file__).resolve().parents[2] / "qualification-python.json").exists()
    assert not hasattr(a.Gate, "verify_python_context") and not hasattr(a.Gate, "recheck_before_load")
    assert not hasattr(a.Gate, "verify_after_load")


@pytest.mark.parametrize("change", ["old_schema", "bool_schema", "extra", "spec", "module"])
def test_manifest_rejects_old_or_changed_admission(change):
    doc = document()
    if change == "old_schema":
        doc["admission"]["schema_version"] = 1
    elif change == "bool_schema":
        doc["admission"]["schema_version"] = True
    elif change == "extra":
        doc["admission"]["python"] = {"accept_current": True}
    elif change == "spec":
        doc["admission"]["setup_spec_sha256"] = "9" * 64
    else:
        doc["admission"]["setup_module_sha256"] = "9" * 64
    with pytest.raises(a.AdmissionError):
        a.validate_manifest(doc)


def test_sealed_observer_requires_exact_source_bound_read_only_code():
    value = observation()
    assert a.validate_observer(value, binding(), document()["admission"]) == value["setup_receipt"]


@pytest.mark.parametrize("change", ["status", "missing", "extra", "schema", "bool_schema", "run", "attempt", "boot", "source", "nonce",
                                      "seal_status", "not_loaded", "no_positive", "seal_digest", "policy_digest", "policy", "module",
                                      "observer", "runner", "stale", "future", "config"])
def test_seal_and_observer_mismatch_cannot_qualify(change):
    value = observation()
    if change == "status":
        value["status"] = "STOP"
    elif change == "missing":
        value.pop("live")
    elif change == "extra":
        value["accept"] = True
    elif change in {"schema", "bool_schema"}:
        value["schema_version"] = True if change == "bool_schema" else 2
    elif change in {"run", "attempt", "boot", "source", "nonce"}:
        value["binding"][{"run": "run_id", "attempt": "run_attempt", "boot": "boot_id", "source": "source_sha256", "nonce": "nonce"}[change]] = "stale"
    elif change == "seal_status":
        value["setup_receipt"]["status"] = "STOP"
    elif change == "not_loaded":
        value["setup_receipt"]["load_attempted"] = False
    elif change == "no_positive":
        value["setup_receipt"]["positive_passed"] = False
    elif change == "seal_digest":
        value["setup_receipt_sha256"] = "9" * 64
    elif change == "policy_digest":
        value["setup_policy_sha256"] = "9" * 64
    elif change == "policy":
        value["policy"] = {"different": True}
    elif change == "module":
        value["setup_receipt"]["setup_module_sha256"] = "9" * 64
    elif change == "observer":
        value["setup_receipt"]["observer"]["sha256"] = "9" * 64
    elif change == "runner":
        value["setup_receipt"]["runner"]["uid"] += 1
    elif change == "stale":
        value["observed"]["monotonic_ns"] = time.monotonic_ns() - 31 * 10**9
    elif change == "future":
        value["observed"]["monotonic_ns"] = time.monotonic_ns() + 10**9
    else:
        value["setup_receipt"]["config"]["source_sha256"] = "9" * 64
    with pytest.raises((a.AdmissionError, s.SetupError)):
        a.validate_observer(value, binding(), document()["admission"])


def synthetic_gate(tmp_path, monkeypatch):
    repo, evidence = tmp_path / "repo", tmp_path / "evidence"
    repo.mkdir()
    evidence.mkdir()
    gate = a.Gate(document(), repo, evidence)
    calls = []
    monkeypatch.setattr(gate, "_source", lambda: calls.append("source") or binding())
    monkeypatch.setattr(gate, "_binding", lambda value: binding())
    monkeypatch.setattr(gate, "_observer", lambda value: calls.append("observer") or observation())
    contract = {"cgroup_root": f"/sys/fs/cgroup/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service", "finite": True}
    monkeypatch.setattr(gate, "_paths", lambda: calls.append("paths") or copy.deepcopy(contract))
    return gate, calls, contract


def test_late_prepare_and_final_recheck_have_no_loader_and_are_one_shot(tmp_path, monkeypatch):
    gate, calls, _ = synthetic_gate(tmp_path, monkeypatch)
    receipt = gate.prepare()
    assert set(receipt) == {"schema_version", "status", "cgroup_root", "binding", "setup_receipt_sha256", "setup_policy_sha256"}
    assert receipt["schema_version"] == 2
    assert gate.final_source_recheck(receipt) == {"status": "PASS", "binding": binding()}
    assert calls == ["source", "observer", "paths", "source"] * 2
    with pytest.raises(a.AdmissionError):
        gate.final_source_recheck(receipt)
    assert (gate.evidence / "setup-final-observation.json").is_file()


@pytest.mark.parametrize("change", ["receipt", "binding", "policy", "contract", "source_failure"])
def test_final_source_policy_paths_and_binding_all_remain_mandatory(tmp_path, monkeypatch, change):
    gate, calls, contract = synthetic_gate(tmp_path, monkeypatch)
    receipt = gate.prepare()
    if change == "receipt":
        receipt["setup_policy_sha256"] = "9" * 64
    elif change == "binding":
        monkeypatch.setattr(gate, "_binding", lambda _: dict(binding(), run_attempt=3))
    elif change == "policy":
        monkeypatch.setattr(gate, "_observer", lambda _: dict(observation(), setup_policy_sha256="9" * 64))
    elif change == "contract":
        contract["finite"] = False
    else:
        monkeypatch.setattr(gate, "_source", lambda: (_ for _ in ()).throw(a.AdmissionError("changed source")))
    with pytest.raises(a.AdmissionError):
        gate.final_source_recheck(receipt)
    assert gate.state in {"STOP", "PREPARED"}


def test_corpus_calculation_and_actual_supervisor_argv_still_checked(monkeypatch):
    repo = Path(a.__file__).resolve().parents[3]
    sys.path.insert(0, str(repo / "src"))
    original_isdir, original_resolve = Path.is_dir, Path.resolve
    def isdir(path):
        return True if str(path) in a.CORPUS_SITE_PATHS else original_isdir(path)
    def resolve(path, *args, **kwargs):
        return path if str(path) in a.CORPUS_SITE_PATHS else original_resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, "is_dir", isdir)
    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(a.os.path, "lexists", lambda _: False)
    contract = a.corpus_contract(repo)
    assert contract["argv"].count("--ro-bind") == 10
    assert contract["extra_ro_binds"][1:] == [[p, "/opt/extra-" + str(i)] for i, p in enumerate(a.CORPUS_SITE_PATHS)]
    from code_forge.mutation_engines.adapters import python_mutmut
    monkeypatch.setattr(python_mutmut.MutmutAdapter, "_extra_binds", lambda *_: (("/unreviewed", "/bin"),))
    with pytest.raises(a.AdmissionError):
        a.corpus_contract(repo)


@pytest.mark.parametrize("field,value", [("uid", 54322), ("gid", 32101), ("mode", 0o775)])
def test_recorded_provider_metadata_cannot_drift_before_final_success(tmp_path, monkeypatch, field, value):
    gate, _calls, contract = synthetic_gate(tmp_path, monkeypatch)
    contract["paths"] = {s.PROVIDER_ROOT + "/bin/python": {"uid": 54321, "gid": 32100, "mode": 0o755}}
    receipt = gate.prepare()
    contract["paths"][s.PROVIDER_ROOT + "/bin/python"][field] = value
    with pytest.raises(a.AdmissionError, match="final finite path"):
        gate.final_source_recheck(receipt)
    assert not (gate.evidence / "setup-final-observation.json").exists()
