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
    monkeypatch.setattr(s.http.client, "HTTPSConnection", denied)


def binding():
    from test_setup_policy import binding_fixture

    return binding_fixture()


def config():
    return copy.deepcopy(s.CONFIG)


def document():
    source = {
        "candidate_sha": binding()["candidate_sha"],
        "tree_oid": "d" * 40,
        "source_sha256": "b" * 64,
        "workflow_sha256": "e" * 64,
        "helper_sha256": dict.fromkeys(launch.HELPER_PATHS, "f" * 64),
    }
    source["helper_sha256"][".github/scripts/forge_ci/setup_policy.py"] = hashlib.sha256(
        Path(s.__file__).read_bytes()
    ).hexdigest()
    live = {
        "binding": binding(),
        "checked": s.stamp(),
        "tree_oid": source["tree_oid"],
        "metadata_sha256": dict.fromkeys(s.live_metadata_paths(binding(), binding()["workflow_id"]).values(), "a" * 64),
    }
    return {"schema_version": 1, "status": "PASS", "binding": binding(), "source": source, "live": live}


def observation():
    cfg = config()
    source = Path(s.__file__).read_text()
    observer = s.observer_source(source, cfg)
    policy = {"known": "synthetic policy"}
    seal = {
        "schema_version": 1,
        "status": "PASS",
        "binding": binding(),
        "config": cfg,
        "load_attempted": True,
        "positive_passed": True,
        "runner": {"uid": os.getuid(), "gid": os.getgid()},
        "setup_module_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "observer": {"sha256": hashlib.sha256(observer).hexdigest(), "bytes": len(observer)},
        "after": policy,
    }
    return {
        "schema_version": 1,
        "status": "PASS",
        "binding": binding(),
        "setup_receipt": seal,
        "setup_receipt_sha256": hashlib.sha256(s.canonical(seal) + b"\n").hexdigest(),
        "setup_policy_sha256": s.digest(policy),
        "observed": s.stamp(),
        "live": document()["live"],
        "policy": policy,
    }


def test_source_receipt_replaces_publication_manifest():
    doc = document()
    rule = a.source_rule(doc)
    assert set(rule) == {"schema_version", "setup_spec_sha256", "setup_module_sha256"}
    assert (
        rule["setup_module_sha256"]
        == doc["source"]["helper_sha256"][".github/scripts/forge_ci/setup_policy.py"]
    )
    assert not hasattr(a, "manifest_rule") and not hasattr(a, "validate_manifest")
    assert not hasattr(a.Gate, "verify_python_context") and not hasattr(a.Gate, "recheck_before_load")


@pytest.mark.parametrize("change", ["old_schema", "bool_schema", "extra", "source", "binding"])
def test_receipt_rejects_old_or_changed_schema(change):
    doc = document()
    if change == "old_schema":
        doc["schema_version"] = 2
    elif change == "bool_schema":
        doc["schema_version"] = True
    elif change == "extra":
        doc["manifest"] = {}
    elif change == "source":
        doc["source"]["candidate_sha"] = "9" * 40
    else:
        doc["binding"]["nonce"] = "1" * 32
    with pytest.raises((launch.LaunchError, s.SetupError)):
        a.source_rule(doc)


def test_sealed_observer_requires_exact_source_bound_read_only_code():
    value = observation()
    assert a.validate_observer(value, binding(), a.source_rule(document())) == value["setup_receipt"]


@pytest.mark.parametrize(
    "change",
    [
        "status",
        "missing",
        "extra",
        "schema",
        "bool_schema",
        "run",
        "attempt",
        "boot",
        "source",
        "job",
        "seal_status",
        "not_loaded",
        "no_positive",
        "seal_digest",
        "policy_digest",
        "policy",
        "module",
        "observer",
        "runner",
        "stale",
        "future",
        "config",
    ],
)
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
    elif change in {"run", "attempt", "boot", "source", "job"}:
        value["binding"][
            {
                "run": "run_id",
                "attempt": "run_attempt",
                "boot": "boot_id",
                "source": "candidate_sha",
                "job": "job_id",
            }[change]
        ] = "stale"
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
        value["setup_receipt"]["config"]["actor_id"] = 1
    with pytest.raises((a.AdmissionError, s.SetupError)):
        a.validate_observer(value, binding(), a.source_rule(document()))


def synthetic_gate(tmp_path, monkeypatch):
    repo, evidence = tmp_path / "repo", tmp_path / "evidence"
    repo.mkdir()
    evidence.mkdir()
    gate = a.Gate(document(), repo, evidence)
    calls = []
    monkeypatch.setattr(gate, "_source", lambda: calls.append("source") or document())
    monkeypatch.setattr(
        gate, "_source_bytes", lambda: calls.append("source_bytes") or document()["source"]
    )
    monkeypatch.setattr(gate, "_binding", lambda value: binding())
    monkeypatch.setattr(gate, "_observer", lambda value: calls.append("observer") or observation())
    contract = {
        "cgroup_root": f"/sys/fs/cgroup/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service",
        "finite": True,
    }
    monkeypatch.setattr(gate, "_paths", lambda: calls.append("paths") or copy.deepcopy(contract))
    return gate, calls, contract


def test_late_prepare_and_final_recheck_have_no_loader_and_are_one_shot(tmp_path, monkeypatch):
    gate, calls, _ = synthetic_gate(tmp_path, monkeypatch)
    receipt = gate.prepare()
    assert set(receipt) == {
        "schema_version",
        "status",
        "cgroup_root",
        "binding",
        "setup_receipt_sha256",
        "setup_policy_sha256",
        "source",
    }
    assert receipt["schema_version"] == 2
    final = gate.final_source_recheck(receipt)
    assert (
        final["status"] == "PASS"
        and final["binding"] == binding()
        and final["source"] == document()["source"]
    )
    assert final["live"]["binding"] == binding()
    assert (
        final["setup_final_observation_sha256"]
        == hashlib.sha256((gate.evidence / "setup-final-observation.json").read_bytes()).hexdigest()
    )
    assert calls == ["source", "observer", "paths", "source_bytes"] * 2
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
        monkeypatch.setattr(
            gate, "_observer", lambda _: dict(observation(), setup_policy_sha256="9" * 64)
        )
    elif change == "contract":
        contract["finite"] = False
    else:
        monkeypatch.setattr(
            gate, "_source", lambda: (_ for _ in ()).throw(a.AdmissionError("changed source"))
        )
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
    assert contract["extra_ro_binds"][1:] == [
        [p, "/opt/extra-" + str(i)] for i, p in enumerate(a.CORPUS_SITE_PATHS)
    ]
    from code_forge.mutation_engines.adapters import python_mutmut

    monkeypatch.setattr(
        python_mutmut.MutmutAdapter, "_extra_binds", lambda *_: (("/unreviewed", "/bin"),)
    )
    with pytest.raises(a.AdmissionError):
        a.corpus_contract(repo)


@pytest.mark.parametrize("field,value", [("uid", 54322), ("gid", 32101), ("mode", 0o775)])
def test_recorded_provider_metadata_cannot_drift_before_final_success(
    tmp_path, monkeypatch, field, value
):
    gate, _calls, contract = synthetic_gate(tmp_path, monkeypatch)
    contract["paths"] = {s.PROVIDER_ROOT + "/bin/python": {"uid": 54321, "gid": 32100, "mode": 0o755}}
    receipt = gate.prepare()
    contract["paths"][s.PROVIDER_ROOT + "/bin/python"][field] = value
    with pytest.raises(a.AdmissionError, match="final finite path"):
        gate.final_source_recheck(receipt)
    assert not (gate.evidence / "setup-final-observation.json").exists()


@pytest.mark.parametrize("failure", [None, "rate_limit", "malformed", "wrong_job", "stale_live"])
def test_observer_preserves_bounded_failure_diagnostics_without_qualifying(
    tmp_path, monkeypatch, failure
):
    repo = tmp_path / "repo"
    repo.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    gate = a.Gate(document(), repo, evidence)
    observed = observation()
    if failure == "wrong_job":
        observed["binding"]["job_id"] += 1
    if failure == "stale_live":
        observed["live"]["checked"]["monotonic_ns"] = 1
    raw = b"not JSON" if failure == "malformed" else s.canonical(observed)
    stderr = (
        b'metadata HTTP/rate-limit failure {"status":403,"X-RateLimit-Remaining":"0"}'
        if failure == "rate_limit"
        else b""
    )

    def command(argv, *_args, **_kwargs):
        return {
            "argv": argv,
            "returncode": 1 if failure == "rate_limit" else 0,
            "stdout": raw.decode(),
            "stdout_hex": raw.hex(),
            "stderr": stderr.decode(),
            "stderr_hex": stderr.hex(),
        }

    monkeypatch.setattr(a.payload, "bounded_command", command)
    if failure:
        with pytest.raises((a.AdmissionError, s.SetupError)):
            gate._observer(binding())
        assert (evidence / "setup-observer-prepare.stdout").read_bytes() == raw
    else:
        assert gate._observer(binding())["status"] == "PASS"
        assert not (evidence / "setup-observer-prepare.stdout").exists()
    assert (evidence / "setup-observer-prepare.stderr").read_bytes() == stderr
    diagnostic = s.parse_json((evidence / "setup-observer-prepare.json").read_bytes())
    assert diagnostic["streams"]["stderr"]["sha256"] == hashlib.sha256(stderr).hexdigest()


def test_forged_local_receipt_cannot_match_root_sealed_numeric_job(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    forged = document()
    forged["binding"]["job_id"] += 1
    forged["live"]["binding"]["job_id"] = forged["binding"]["job_id"]
    gate = a.Gate(forged, repo, evidence)
    monkeypatch.setattr(
        gate, "_source", lambda: {"binding": forged["binding"], "source": forged["source"]}
    )
    monkeypatch.setattr(
        gate, "_paths", lambda: pytest.fail("payload path admission passed forged root binding")
    )
    raw = s.canonical(observation())

    def command(argv, *_args, **_kwargs):
        return {
            "argv": argv,
            "returncode": 0,
            "stdout": raw.decode(),
            "stdout_hex": raw.hex(),
            "stderr": "",
            "stderr_hex": "",
        }

    monkeypatch.setattr(a.payload, "bounded_command", command)
    with pytest.raises(a.AdmissionError, match="mismatch"):
        gate.prepare()
    assert gate.state == "STOP" and gate.receipt is None
