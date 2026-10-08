"""Pure/mocked Gate tests. Never touch securityfs, sudo, parser, probes or APIs.

Synthetic lifecycle PASS tests replace the intentionally closed Python data gate
and all runtime discovery. They are tests of the protocol, never admission data.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from forge_ci import admission as a, facts, launch  # noqa: E402


@pytest.fixture(autouse=True)
def never_execute(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("admission test attempted a live command or API")
    monkeypatch.setattr(subprocess, "Popen", denied)
    monkeypatch.setattr(launch, "fetch_public_attempt", denied)
    monkeypatch.setattr(launch.http.client, "HTTPSConnection", denied)


def checksum(raw):
    return hashlib.sha256(raw).hexdigest()


def file_record(path="/opt/hostedtoolcache/Python/3.12.14/x64/lib/libpython3.12.so.1.0"):
    return {"path": path, "canonical": path, "sha256": "1" * 64, "bytes": 10,
            "mode": 0o755, "uid": 1001, "gid": 1001, "symlinks": []}


def manifest():
    nonce = "1" * 32
    actor = {"id": 456, "login": "owner", "type": "User"}
    launch_rule = {
        "nonce": nonce, "repository": {"id": 123, "name": "project", "full_name": "owner/project", "owner": actor},
        "publisher": copy.deepcopy(actor), "authorized_retrier": copy.deepcopy(actor), "seed_sha": "a" * 40,
        "ref": "refs/heads/ci/qualify-apparmor-" + nonce,
        "workflow_path": ".github/workflows/qualify-apparmor-" + nonce + ".yml", "workflow_sha256": "b" * 64,
        "source_sha256": "c" * 64, "helper_sha256": dict.fromkeys(launch.HELPER_PATHS, "d" * 64),
    }
    rule = {field: "2" * 64 for field in a.DIGEST_FIELDS}
    rule.update(schema_version=1, executables_sha256=a.EXECUTABLE_MANIFEST_SHA256,
                reviewed_inventory_sha256=a.INVENTORY_SHA256,
                reviewed_executable_manifest_sha256=a.EXECUTABLE_MANIFEST_SHA256,
                loader_libraries=[file_record()], python={"schema_version": 1, "classification_sha256": "3" * 64,
                "contexts": {name: {"interpreter": exe, "inventory_sha256": "4" * 64, "files_sha256": "5" * 64}
                             for name, exe in a.CONTEXTS.items()}})
    return {"schema_version": 1, "launch": launch_rule, "admission": rule}


def binding():
    return {"nonce": "1" * 32, "control_sha": "e" * 40, "source_sha256": "c" * 64,
            "run_id": 789, "run_attempt": 1, "job": "qualify", "boot_id": "11111111-1111-1111-1111-111111111111"}


def gate_dirs(tmp_path):
    repo, vendor, evidence = (tmp_path / name for name in ("repo", "vendor", "evidence"))
    repo.mkdir()
    vendor.mkdir()
    evidence.mkdir(mode=0o700)
    config = evidence / "parser.conf"
    config.write_bytes(b"")
    config.chmod(0o600)
    return repo, vendor, evidence


def make_gate(tmp_path):
    document = manifest()
    dirs = gate_dirs(tmp_path)
    return a.Gate(document, *dirs), document, dirs


def test_schema_is_concrete_but_not_an_approval():
    doc = manifest()
    assert a.validate_manifest(doc) == doc["admission"]
    assert a.REVIEWED_PYTHON_CLASSIFICATIONS == frozenset()
    with pytest.raises(a.AdmissionError, match="not independently reviewed"):
        a.require_python_classification(doc["admission"]["python"])


@pytest.mark.parametrize("field", sorted(a.DIGEST_FIELDS | {"python", "loader_libraries", "reviewed_inventory_sha256"}))
def test_manifest_missing_field_stops(field):
    doc = manifest()
    del doc["admission"][field]
    with pytest.raises(a.AdmissionError):
        a.validate_manifest(doc)


@pytest.mark.parametrize("bad", [None, "", "*", "TODO", "0" * 64, "A" * 64, True, 42])
def test_manifest_digest_cannot_be_placeholder_or_weak_type(bad):
    doc = manifest()
    doc["admission"]["host_projection_sha256"] = bad
    with pytest.raises(a.AdmissionError):
        a.validate_manifest(doc)


@pytest.mark.parametrize("change", [
    lambda x: x.update(unrecognized="accept"),
    lambda x: x.update(schema_version=True),
    lambda x: x.update(reviewed_inventory_sha256="9" * 64),
    lambda x: x.update(reviewed_executable_manifest_sha256="9" * 64),
    lambda x: x.update(executables_sha256="9" * 64),
    lambda x: x["python"].update(allow_duplicates=True),
    lambda x: x["python"]["contexts"].pop("payload"),
    lambda x: x["python"]["contexts"]["host"].update(interpreter="/usr/bin/python3"),
    lambda x: x["python"]["contexts"]["system"].update(ignore_unhashed_pyc=True),
    lambda x: x.update(loader_libraries=[]),
    lambda x: x["loader_libraries"].append(copy.deepcopy(x["loader_libraries"][0])),
    lambda x: x["loader_libraries"][0].update(path="/unreviewed/libpython.so"),
    lambda x: x["loader_libraries"][0].update(canonical="/unreviewed/libpython.so"),
    lambda x: x["loader_libraries"][0].update(mode=0o777),
    lambda x: x["loader_libraries"][0].update(mode=True),
])
def test_manifest_rejects_unknown_policy_and_widening(change):
    doc = manifest()
    change(doc["admission"])
    with pytest.raises(a.AdmissionError):
        a.validate_manifest(doc)


def test_digest_insertion_alone_never_enables_python(monkeypatch):
    policy = manifest()["admission"]["python"]
    monkeypatch.setattr(a, "REVIEWED_PYTHON_CLASSIFICATIONS", frozenset({policy["classification_sha256"]}))
    with pytest.raises(a.AdmissionError, match="not implemented"):
        a.require_python_classification(policy)


def test_unreviewed_prepare_stops_before_any_runtime_read(tmp_path, monkeypatch):
    gate, _, _ = make_gate(tmp_path)
    for name in ("_source", "_reader", "_binding", "_prepare_python", "_observe"):
        monkeypatch.setattr(gate, name, lambda *args, **kwargs: pytest.fail("unreviewed runtime discovery"))
    with pytest.raises(a.AdmissionError, match="not independently reviewed"):
        gate.prepare()
    assert gate._state == "STOP"
    with pytest.raises(a.AdmissionError):
        gate.prepare()


@pytest.mark.parametrize("value", [float("nan"), 1.2, {"x": object()}, {1: "value"}, (1, 2)])
def test_snapshot_accepts_only_strict_json(value):
    with pytest.raises(a.AdmissionError):
        a.canonical(value)


def test_objection_resolution_is_exact_and_individual():
    expected = {"complete": True}
    def objection():
        raise facts.FactError("exact objection", observations=expected)
    assert a.observation(objection, resolved_error="exact objection") is expected
    for allowed in (None, "", "other objection"):
        with pytest.raises(a.AdmissionError, match="unresolved collection error"):
            a.observation(objection, resolved_error=allowed)


@pytest.mark.parametrize("error,observation", [("exact objection plus unknown failure", {}), ("exact objection", None)])
def test_partial_or_extra_failures_cannot_be_waived(error, observation):
    def failure():
        raise facts.FactError(error, observations=observation)
    with pytest.raises(a.AdmissionError):
        a.observation(failure, resolved_error="exact objection")


def host_fixture(monkeypatch):
    for name in ("getuid", "geteuid", "getgid", "getegid"):
        monkeypatch.setattr(a.os, name, lambda: 1001)
    run = binding()
    host = {
        "identity": {"GITHUB_REPOSITORY": "owner/project", "GITHUB_REPOSITORY_ID": "123",
                     "GITHUB_SHA": run["control_sha"], "GITHUB_WORKFLOW_SHA": run["control_sha"],
                     "GITHUB_RUN_ID": "789", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_JOB": "qualify",
                     "GITHUB_EVENT_NAME": "push", "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64",
                     "RUNNER_ENVIRONMENT": "github-hosted", "ImageOS": "ubuntu24", "ImageVersion": "20261004.327.1"},
        "uname": ["Linux", "fresh-host", "6.17.0-1022-azure", "build", "x86_64"], "os_release": "reviewed Ubuntu bytes",
        "boot_id": run["boot_id"], "caller": {
            "pid": 12, "ppid": 10, "label": "unconfined", "groups": [1001], "namespaces": {"net": "net:[1]"},
            "status": {"Uid": "1001\t1001\t1001\t1001", "Gid": "1001\t1001\t1001\t1001",
                       "CapEff": "0000000000000000", "CapPrm": "0000000000000000", "CapInh": "0000000000000000",
                       "CapAmb": "0000000000000000", "CapBnd": "000001ffffffffff", "NoNewPrivs": "0", "Seccomp": "0"}},
        "apparmor_enabled": "Y", "userns_restriction": "1", "cgroup_controllers": "memory pids",
        "cgroup": {"path": "/sys/fs/cgroup/user.slice/user-1001.slice/user@1001.service", "kind": "isdir",
                   "exists": True, "matches": True, "mode": 0o755, "uid": 1001, "gid": 1001},
        "loader_environment": {"LD_PRELOAD": None, "LD_AUDIT": None, "LD_LIBRARY_PATH": a.TOOLCACHE + "/lib"},
        "python_roots": {"pythonLocation": a.TOOLCACHE, "Python_ROOT_DIR": a.TOOLCACHE,
                         "Python3_ROOT_DIR": a.TOOLCACHE, "RUNNER_TOOL_CACHE": "/opt/hostedtoolcache"},
    }
    projection = copy.deepcopy(host)
    projection.pop("boot_id")
    projection["uname"][1] = None
    projection["identity"] = {k: v for k, v in host["identity"].items() if not k.startswith("GITHUB_")}
    for key in ("pid", "ppid", "namespaces"):
        projection["caller"].pop(key)
    return host, {"host_projection_sha256": a.digest(projection)}, run


def test_reviewed_host_predicates_and_exact_projection(monkeypatch):
    host, rule, run = host_fixture(monkeypatch)
    assert a.digest(a.validate_host(host, rule, run)) == rule["host_projection_sha256"]
    host["uname"][1] = "another-fresh-host"
    host["caller"]["pid"] = 40
    assert a.validate_host(host, rule, run)


@pytest.mark.parametrize("mutate", [
    lambda x: x.update(apparmor_enabled="N"),
    lambda x: x.update(userns_restriction="0"),
    lambda x: x.update(boot_id="stale"),
    lambda x: x["identity"].update(GITHUB_EVENT_NAME="pull_request"),
    lambda x: x["identity"].update(GITHUB_RUN_ATTEMPT="2"),
    lambda x: x["identity"].update(ImageVersion="next-image"),
    lambda x: x["uname"].__setitem__(2, "other-kernel"),
    lambda x: x["caller"].update(label="bwrap (enforce)"),
    lambda x: x["caller"]["status"].update(CapEff="0000000000000001"),
    lambda x: x["caller"]["status"].update(CapPrm="0000000000000001"),
    lambda x: x["caller"]["status"].update(CapAmb="0000000000000001"),
    lambda x: x["caller"]["status"].update(CapInh="0000000000000001"),
    lambda x: x["caller"]["status"].update(Uid="0 0 0 0"),
    lambda x: x["caller"]["status"].update(Gid="1001 0 1001 1001"),
    lambda x: x["caller"]["status"].update(NoNewPrivs="1"),
    lambda x: x["caller"]["status"].update(Seccomp="2"),
    lambda x: x["cgroup"].update(matches=False),
    lambda x: x["cgroup"].update(uid=0),
    lambda x: x["loader_environment"].update(LD_PRELOAD=""),
    lambda x: x["loader_environment"].update(LD_AUDIT="evil.so"),
    lambda x: x["loader_environment"].update(LD_LIBRARY_PATH=a.TOOLCACHE + "/lib:"),
    lambda x: x["loader_environment"].update(LD_LIBRARY_PATH=a.TOOLCACHE + "/lib:/tmp"),
    lambda x: x["python_roots"].update(pythonLocation="/unreviewed"),
    lambda x: x.update(unknown_failure=True),
])
def test_host_failure_is_never_a_whole_stage_exception(monkeypatch, mutate):
    host, rule, run = host_fixture(monkeypatch)
    mutate(host)
    with pytest.raises(a.AdmissionError):
        a.validate_host(host, rule, run)


def kernel_fixture(monkeypatch):
    semantic = {"schema": 1, "namespaces": [""], "scope": {"ns_level": "0", "ns_name": "root", "stacked": "no", "ns_stacked": "no"},
                "profiles": [{"namespace": "", "name": "reviewed", "mode": "enforce", "attachment": "<unknown>",
                              "metadata": {"raw_data": {"sha256": "6" * 64, "bytes": 12}}}]}
    monkeypatch.setattr(a, "INVENTORY_SHA256", a.digest(semantic))
    return kernel_record(semantic)


def kernel_record(semantic):
    return {"semantic": semantic, "semantic_sha256": a.digest(semantic), "scope": semantic["scope"],
            "conflicting_names": [p["name"] for p in semantic["profiles"] if p["name"].split("//")[0] in {"bwrap", "unpriv_bwrap"}],
            "loaded_profiles": sorted([
                {"qualified_name": (f":{p['namespace']}://" if p["namespace"] else "") + p["name"], "mode": p["mode"]}
                for p in semantic["profiles"]], key=lambda x: x["qualified_name"])}


def added(before):
    result = copy.deepcopy(before)
    for name, attach in (("bwrap", "/usr/bin/bwrap"), ("unpriv_bwrap", "<unknown>")):
        result["profiles"].append({"namespace": "", "name": name, "mode": "enforce", "attachment": attach, "metadata": {"raw_data": {"sha256": "7" * 64, "bytes": 123}}})
    result["profiles"].sort(key=lambda p: (p["namespace"], p["name"]))
    return result


def test_kernel_opaque_display_requires_exact_reviewed_digest(monkeypatch):
    fixture = kernel_fixture(monkeypatch)
    assert a.validate_kernel(fixture) == fixture["semantic"]
    changed = copy.deepcopy(fixture["semantic"])
    changed["profiles"][0]["metadata"]["raw_data"]["sha256"] = "7" * 64
    with pytest.raises(a.AdmissionError, match="independent review"):
        a.validate_kernel(kernel_record(changed))


def test_kernel_does_not_trust_self_reported_hash_or_absence(monkeypatch):
    fixture = kernel_fixture(monkeypatch)
    fixture["semantic"]["profiles"][0]["name"] = "bwrap"
    with pytest.raises(a.AdmissionError):
        a.validate_kernel(fixture)
    fixture = kernel_record(fixture["semantic"])
    fixture["conflicting_names"] = []
    with pytest.raises(a.AdmissionError):
        a.validate_kernel(fixture)


def test_after_add_exact_pair_and_prior_inventory(monkeypatch):
    before = kernel_fixture(monkeypatch)["semantic"]
    after = added(before)
    assert a.validate_kernel(kernel_record(after), before=before, compiled={"sha256": "7" * 64, "bytes": 123}) == after


@pytest.mark.parametrize("mutate", [
    lambda x: x["profiles"].pop(),
    lambda x: x["profiles"][0].update(mode="complain"),
    lambda x: x["profiles"][0]["metadata"]["raw_data"].update(sha256="8" * 64),
    lambda x: x["profiles"][1].update(metadata={"changed": True}),
    lambda x: x["profiles"].append({"namespace": "", "name": "extra", "mode": "enforce", "attachment": "/extra", "metadata": {}}),
    lambda x: x["profiles"].append({"namespace": "", "name": "bwrap//child", "mode": "enforce", "attachment": "/extra", "metadata": {}}),
    lambda x: x["namespaces"].append("extra"),
    lambda x: x["scope"].update(stacked="yes"),
])
def test_after_add_rejects_partial_extra_changed_or_non_enforcing(monkeypatch, mutate):
    before = kernel_fixture(monkeypatch)["semantic"]
    after = added(before)
    mutate(after)
    after["profiles"].sort(key=lambda p: (p["namespace"], p["name"]))
    with pytest.raises(a.AdmissionError):
        a.validate_kernel(kernel_record(after), before=before, compiled={"sha256": "7" * 64, "bytes": 123})


def test_loaded_listing_is_independent_required_crosscheck(monkeypatch):
    fixture = kernel_fixture(monkeypatch)
    fixture["loaded_profiles"] = []
    with pytest.raises(a.AdmissionError, match="listing disagreement"):
        a.validate_kernel(fixture)


def policy_fixture(tmp_path, monkeypatch):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    profile = vendor / a.PROFILE_MEMBER
    profile.parent.mkdir(parents=True)
    profile.write_bytes(b"#" * 1936)
    monkeypatch.setattr(facts, "VENDOR_PROFILE_SHA256", checksum(profile.read_bytes()))
    archives = {"apparmor.deb": b"reviewed archive"}
    monkeypatch.setattr(a, "ARCHIVES", {name: checksum(raw) for name, raw in archives.items()})
    for name, raw in archives.items():
        (vendor / name).write_bytes(raw)
    raw = {facts.PROFILE_ROOT + "/" + name: b"# synthetic reviewed comment\n" for name in a.GENERATED}
    generated = {name: (len(raw[facts.PROFILE_ROOT + "/" + name]), checksum(raw[facts.PROFILE_ROOT + "/" + name])) for name in a.GENERATED}
    monkeypatch.setattr(a, "GENERATED", generated)
    raw["/var/lib/dpkg/info/apparmor.postinst"] = b"reviewed generator, never executed"
    monkeypatch.setattr(a, "POSTINST_SHA256", checksum(raw["/var/lib/dpkg/info/apparmor.postinst"]))
    closure = {name: {"type": "file", "sha256": digest, "bytes": size, "includes": [], "matches_vendor_package": False,
                      "metadata": {"type": "f", "target": "", "mode": 0o644, "uid": 0, "gid": 0, "size": size}}
               for name, (size, digest) in generated.items()}
    monkeypatch.setattr(a, "INCLUDE_SHA256", a.digest(closure))
    text = "# reviewed host config\n"
    monkeypatch.setattr(a, "PARSER_CONF_SHA256", checksum(text.encode()))
    fixture = {"filesystem_inventory": {}, "include_closure": closure, "include_closure_sha256": a.INCLUDE_SHA256,
               "absent_optional": a.ABSENT_OPTIONAL, "forbidden_overrides": [],
               "parser_conf": {"sha256": a.PARSER_CONF_SHA256, "text": text},
               "vendor_comparison": "STOP: include mismatches require review", "unresolved_include_mismatches": sorted(generated)}
    class Reader:
        def read(self, path):
            return raw[path]
    return fixture, Reader(), vendor, raw


def test_two_generated_defaults_are_individually_bound(tmp_path, monkeypatch):
    fixture, reader, vendor, _ = policy_fixture(tmp_path, monkeypatch)
    assert a.validate_policy(fixture, reader, vendor)["postinst_sha256"] == a.POSTINST_SHA256


@pytest.mark.parametrize("mutate", [
    lambda x: x["unresolved_include_mismatches"].append("tunables/evil"),
    lambda x: x["unresolved_include_mismatches"].pop(),
    lambda x: x.update(forbidden_overrides=["/etc/apparmor.d/local/bwrap-userns-restrict"]),
    lambda x: x.update(absent_optional=[]),
    lambda x: x["include_closure"]["tunables/home.d/ubuntu"]["metadata"].update(uid=1001),
    lambda x: x["include_closure"]["tunables/home.d/ubuntu"]["metadata"].update(mode=0o666),
    lambda x: x["include_closure"]["tunables/home.d/ubuntu"].update(sha256="9" * 64),
    lambda x: x["parser_conf"].update(text="quiet\n"),
    lambda x: x.update(vendor_comparison="matched include closure"),
    lambda x: x.update(unknown_error="missing ABI"),
])
def test_generated_defaults_never_waive_other_objections(tmp_path, monkeypatch, mutate):
    fixture, reader, vendor, _ = policy_fixture(tmp_path, monkeypatch)
    mutate(fixture)
    with pytest.raises(a.AdmissionError):
        a.validate_policy(fixture, reader, vendor)


@pytest.mark.parametrize("target", ["generator", "member", "archive", "extra_archive", "generated"])
def test_policy_actual_bytes_and_archive_set_are_rechecked(tmp_path, monkeypatch, target):
    fixture, reader, vendor, raw = policy_fixture(tmp_path, monkeypatch)
    if target == "generator":
        raw["/var/lib/dpkg/info/apparmor.postinst"] += b"changed"
    elif target == "member":
        (vendor / a.PROFILE_MEMBER).write_bytes(b"changed")
    elif target == "archive":
        (vendor / "apparmor.deb").write_bytes(b"changed")
    elif target == "extra_archive":
        (vendor / "other.deb").write_bytes(b"new")
    else:
        raw[facts.PROFILE_ROOT + "/tunables/home.d/ubuntu"] += b"changed"
    with pytest.raises(a.AdmissionError):
        a.validate_policy(fixture, reader, vendor)


def test_file_identity_binds_bytes_mode_owner_and_symlink_alias(tmp_path):
    target = tmp_path / "source.py"
    target.write_bytes(b"print('fixture')\n")
    alias = tmp_path / "alias.py"
    alias.symlink_to(target.name)
    identity = a.file_identity(alias)
    assert identity["sha256"] == checksum(target.read_bytes())
    assert identity["canonical"] == str(target)
    assert identity["symlinks"] == [{"path": str(alias), "target": target.name}]
    a.validate_file_identity(identity)
    target.write_bytes(b"print('mutated')\n")
    assert a.file_identity(alias) != identity


def synthetic_gate(tmp_path, monkeypatch):
    gate, doc, dirs = make_gate(tmp_path)
    source = dirs[0] / "bound.py"
    source.write_bytes(b"synthetic whole-byte bound source\n")
    baseline = {"host": {"cgroup": {"path": f"/sys/fs/cgroup/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service"}},
                "tools": {"reviewed": True}, "policy": {}, "guards": {}, "kernel": {"reviewed": "synthetic"}}
    monkeypatch.setattr(a, "require_python_classification", lambda policy: None)
    monkeypatch.setattr(gate, "_binding", binding)
    monkeypatch.setattr(gate, "_source", lambda: None)
    monkeypatch.setattr(gate, "_prepare_python", lambda: [a.file_identity(source)])
    monkeypatch.setattr(gate, "_load_python_data", lambda: {})
    monkeypatch.setattr(gate, "_observe", lambda **kwargs: copy.deepcopy(baseline))
    monkeypatch.setattr(gate, "_validate_payload_context", lambda record: None)
    compiled = dirs[2] / "compile.stdout"
    compiled.write_bytes(b"synthetic compiled policy")
    compiled.chmod(0o600)
    return gate, doc, dirs, source, baseline


def test_synthetic_lifecycle_returns_fixed_json_receipts(tmp_path, monkeypatch):
    gate, _, dirs, _, _ = synthetic_gate(tmp_path, monkeypatch)
    receipt = gate.prepare()
    assert set(receipt) == {"schema_version", "status", "binding", "vendor_profile", "cgroup_root"}
    assert receipt["vendor_profile"] == str(dirs[1] / a.PROFILE_MEMBER)
    assert set(receipt["binding"]) == a.BINDING_FIELDS
    for operation in (gate.recheck_before_load, gate.verify_after_load, lambda r: gate.verify_python_context(r, {}), gate.final_source_recheck):
        result = operation(receipt)
        assert result == {"schema_version": 1, "status": "PASS", "binding": binding()}
        assert json.loads(a.canonical(result)) == result
    assert gate._state == "FINAL"
    with pytest.raises(a.AdmissionError):
        gate.final_source_recheck(receipt)
    assert gate._state == "STOP"


def test_manifest_is_copied_at_construction(tmp_path, monkeypatch):
    gate, document, _, _, _ = synthetic_gate(tmp_path, monkeypatch)
    original = gate._document_bytes
    document["admission"]["host_projection_sha256"] = "9" * 64
    document["admission"]["python"]["contexts"]["host"]["inventory_sha256"] = "8" * 64
    assert gate._document_bytes == original
    assert gate._document() != document
    gate.prepare()


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(vendor_profile="/unreviewed/evil"),
    lambda r: r.update(cgroup_root="/sys/fs/cgroup"),
    lambda r: r.update(schema_version=True),
    lambda r: r.update(extra="ignored?"),
    lambda r: r["binding"].update(run_attempt=2),
    lambda r: r["binding"].update(run_attempt=True),
    lambda r: r["binding"].update(nonce="9" * 32),
])
def test_receipt_mutation_is_terminal_even_with_matching_other_binding_fields(tmp_path, monkeypatch, mutation):
    gate, _, _, _, _ = synthetic_gate(tmp_path, monkeypatch)
    receipt = gate.prepare()
    original = json.loads(gate._receipt_bytes)
    mutation(receipt)
    with pytest.raises(a.AdmissionError):
        gate.recheck_before_load(receipt)
    assert gate._state == "STOP"
    assert json.loads(gate._receipt_bytes) == original
    with pytest.raises(a.AdmissionError):
        gate.recheck_before_load(original)


@pytest.mark.parametrize("first", ["recheck_before_load", "verify_after_load", "final_source_recheck"])
def test_forged_binding_alone_never_authorizes_any_state(tmp_path, monkeypatch, first):
    gate, _, _, _, _ = synthetic_gate(tmp_path, monkeypatch)
    with pytest.raises(a.AdmissionError):
        getattr(gate, first)({"status": "PASS", "binding": binding()})
    assert gate._state == "STOP"


def test_after_load_cannot_precede_before_load(tmp_path, monkeypatch):
    gate, _, _, _, _ = synthetic_gate(tmp_path, monkeypatch)
    receipt = gate.prepare()
    with pytest.raises(a.AdmissionError):
        gate.verify_after_load(receipt)
    assert gate._state == "STOP"


@pytest.mark.parametrize("operation", ["source", "file", "input", "time", "config", "directory"])
def test_each_recheck_failure_is_terminal(tmp_path, monkeypatch, operation):
    gate, _, dirs, source, baseline = synthetic_gate(tmp_path, monkeypatch)
    receipt = gate.prepare()
    if operation == "source":
        def stop():
            raise a.AdmissionError("source changed")
        monkeypatch.setattr(gate, "_source", stop)
    elif operation == "file":
        source.write_bytes(b"changed")
    elif operation == "input":
        baseline["tools"]["reviewed"] = False
    elif operation == "time":
        ticks = iter((0, 0, 46))
        monkeypatch.setattr(a.time, "monotonic", lambda: next(ticks))
    elif operation == "config":
        (dirs[2] / "parser.conf").write_bytes(b"quiet\n")
    else:
        dirs[2].chmod(0o755)
    with pytest.raises(a.AdmissionError):
        gate.recheck_before_load(receipt)
    assert gate._state == "STOP"


def test_command_wrapper_rejects_parser_shell_python_and_mutating_utilities(tmp_path):
    commands = a._Commands(a._PrivateEvidence(tmp_path / "raw"), total_seconds=1)
    for argv in (["/usr/sbin/apparmor_parser", "--version"], ["/usr/bin/python3", "-c", "1"],
                 ["/bin/sh", "-c", "true"], ["/usr/bin/apt-get", "install", "apparmor"],
                 ["/usr/bin/sudo", "-n", "--", "/usr/bin/rm", "-rf", "/etc/apparmor.d"]):
        with pytest.raises(a.AdmissionError, match="unapproved Gate command"):
            commands.run(argv)


def test_command_wrapper_forces_sanitized_environment(tmp_path, monkeypatch):
    commands = a._Commands(a._PrivateEvidence(tmp_path / "raw"), total_seconds=1)
    calls = []
    monkeypatch.setattr(facts.Commands, "run", lambda self, argv, **kwargs: calls.append((argv, kwargs)) or b"ok")
    argv = facts.Reader.prefix() + ["/usr/bin/head", "-c", "32", "--", "/etc/apparmor.d/tunables/global"]
    assert commands.run(argv) == b"ok"
    assert calls[0][1]["env"] == a.SANITIZED_ENV
    with pytest.raises(a.AdmissionError, match="environment"):
        commands.run(argv, env={"LD_PRELOAD": "bad"})


def test_repeated_prepare_is_a_terminal_sequence_violation(tmp_path, monkeypatch):
    gate, _, _, _, _ = synthetic_gate(tmp_path, monkeypatch)
    receipt = gate.prepare()
    with pytest.raises(a.AdmissionError, match="one-shot"):
        gate.prepare()
    assert gate._state == "STOP"
    with pytest.raises(a.AdmissionError):
        gate.recheck_before_load(receipt)


@pytest.mark.parametrize("name,value", [("COVERAGE_PROCESS_START", "coverage.rc"),
                                       ("COVERAGE_PROCESS_CONFIG", "on"),
                                       ("SETUPTOOLS_USE_DISTUTILS", "stdlib"),
                                       ("SETUPTOOLS_USE_DISTUTILS", "")])
def test_unreviewed_startup_activation_stops(monkeypatch, name, value):
    for key in ("COVERAGE_PROCESS_START", "COVERAGE_PROCESS_CONFIG", "SETUPTOOLS_USE_DISTUTILS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(name, value)
    with pytest.raises(a.AdmissionError):
        a.activation_environment()


def test_default_startup_conditions_are_recorded(monkeypatch):
    for key in ("COVERAGE_PROCESS_START", "COVERAGE_PROCESS_CONFIG", "SETUPTOOLS_USE_DISTUTILS"):
        monkeypatch.delenv(key, raising=False)
    assert all(value is None for value in a.activation_environment().values())
    monkeypatch.setenv("COVERAGE_PROCESS_CONFIG", "")
    monkeypatch.setenv("SETUPTOOLS_USE_DISTUTILS", "local")
    assert a.activation_environment()["SETUPTOOLS_USE_DISTUTILS"] == "local"


def test_only_finite_provider_library_class_is_accepted():
    doc = manifest()
    doc["admission"]["loader_libraries"] = {"class": "setup-python-provider", "paths": a.PROVIDER_LIBRARY_PATHS}
    a.validate_manifest(doc)
    doc["admission"]["loader_libraries"]["paths"] = [a.TOOLCACHE + "/lib/*"]
    with pytest.raises(a.AdmissionError):
        a.validate_manifest(doc)


def test_provider_library_captures_exact_alias_and_target(monkeypatch):
    target = a.PROVIDER_LIBRARY_PATHS[1]
    records = [dict(file_record(path), canonical=target, uid=os.getuid(), gid=os.getgid())
               for path in a.PROVIDER_LIBRARY_PATHS]
    records[0]["symlinks"] = [{"path": records[0]["path"], "target": Path(target).name}]
    monkeypatch.setattr(a, "file_identity", lambda path: copy.deepcopy(next(r for r in records if r["path"] == str(path))))
    def no_capabilities(*args):
        raise OSError(a.errno.ENODATA, "no capabilities")
    monkeypatch.setattr(a.os, "getxattr", no_capabilities)
    assert a.provider_libraries() == records
    records[0]["canonical"] = "/unreviewed/libpython.so"
    with pytest.raises(a.AdmissionError):
        a.provider_libraries()


def test_compiler_bytes_are_captured_once_and_rechecked(tmp_path, monkeypatch):
    gate, _, dirs, _, _ = synthetic_gate(tmp_path, monkeypatch)
    receipt = gate.prepare()
    gate.recheck_before_load(receipt)
    (dirs[2] / "compile.stdout").write_bytes(b"changed policy")
    with pytest.raises(a.AdmissionError, match="compiler output changed"):
        gate.verify_after_load(receipt)
    assert gate._state == "STOP"


def test_opaque_new_attachment_uses_exact_compiled_raw_blob(monkeypatch):
    before = kernel_fixture(monkeypatch)["semantic"]
    after = added(before)
    after["profiles"][0]["attachment"] = "<unknown>"
    assert a.validate_kernel(kernel_record(after), before=before, compiled={"sha256": "7" * 64, "bytes": 123})
    with pytest.raises(a.AdmissionError):
        a.validate_kernel(kernel_record(after), before=before, compiled={"sha256": "8" * 64, "bytes": 123})


def test_v2_policy_requires_exact_reviewed_class_and_manifest_hash():
    doc = manifest()
    doc["admission"]["python"] = {"schema_version": 2, "policy_sha256": a.PYTHON_POLICY_SHA256, "manifest_sha256": "6" * 64}
    a.validate_manifest(doc)
    a.require_python_classification(doc["admission"]["python"])
    doc["admission"]["python"]["policy_sha256"] = "7" * 64
    with pytest.raises(a.AdmissionError):
        a.validate_manifest(doc)


def test_record_decoding_keeps_exact_paths_order_and_real_digest():
    import base64
    raw_sha = bytes.fromhex("ab" * 32)
    encoded = base64.urlsafe_b64encode(raw_sha).decode().rstrip("=")
    rows = a.parse_record(("module.py,sha256=" + encoded + ",17\nexample.dist-info/RECORD,,\n").encode(), "sha256-urlsafe-base64")
    assert rows[0][0] == "module.py" and rows[1] == ["example.dist-info/RECORD", "", ""]
    assert a.record_hash("sha256=" + encoded, "sha256-urlsafe-base64") == "ab" * 32
    assert a.record_hash("sha256=" + "ab" * 32, "sha256-hex") == "ab" * 32
    with pytest.raises(a.AdmissionError):
        a.record_hash("sha256=" + "ab" * 32, "sha256-urlsafe-base64")


@pytest.mark.parametrize("raw", [b"", b"module.py,,\nmodule.py,,\n", b"/module.py,,\n", b"a//b,,\n", b"./module.py,,\n",
                                  b"a,b,c,d\n", b"a,sha1=abc,2\n", b"a,sha256=invalid,2\n", b"a,,-1\n"])
def test_malformed_record_never_becomes_a_missing_file_exception(raw):
    with pytest.raises(a.AdmissionError):
        a.parse_record(raw, "sha256-urlsafe-base64")


def instance_fixture(tmp_path):
    import base64
    import csv
    import io
    import struct
    root = tmp_path / "site"
    root.mkdir()
    metadata = root / "example-1.dist-info"
    metadata.mkdir()
    source = root / "module.py"
    source.write_bytes(b"VALUE = 1\n")
    source.chmod(0o644)
    cache = root / "__pycache__/module.cpython-312.pyc"
    cache.parent.mkdir()
    cache.write_bytes(bytes.fromhex("cb0d0d0a") + struct.pack("<III", 0, int(source.stat().st_mtime), source.stat().st_size) + b"opaque trusted installer output")
    cache.chmod(0o644)
    source_sha = checksum(source.read_bytes())
    encoded = base64.urlsafe_b64encode(bytes.fromhex(source_sha)).decode().rstrip("=")
    rows = [["module.py", "sha256=" + encoded, str(source.stat().st_size)],
            ["__pycache__/module.cpython-312.pyc", "", ""], ["example-1.dist-info/RECORD", "", ""]]
    output = io.StringIO(newline="")
    csv.writer(output).writerows(rows)
    record = metadata / "RECORD"
    record.write_bytes(output.getvalue().encode())
    record.chmod(0o644)
    def compact(path):
        identity = a.file_identity(Path(path))
        return {**{key: identity[key] for key in a.PYTHON_FILE_FIELDS}, "path": str(path), "uid": 1001, "gid": 1001}
    entry = {"class": "current-job-pip", "name": "example", "normalized_name": "example", "version": "1",
             "location": str(root), "metadata_canonical": str(metadata), "metadata_inputs": [], "install_receipt": "toolcache-install",
             "observed_complete": True, "observed_error": None, "pytest_entry_points": [],
             "record": {"path": str(record), "state": "observed", "identity": compact(record), "row_count": len(rows),
                        "rows_sha256": a.digest(rows), "observation_rows": {"observed": [0, 1, 2]}},
             "record_hash_format": "sha256-urlsafe-base64", "unused_wrappers": [], "stable_file_rows": [0, 2],
             "stable_files_scope": "observed-subset", "stable_files_sha256": a.digest([compact(source), compact(record)]),
             "cache_binding": {"class": "pip-generated-cpython312", "uid": 1001, "gid": 1001,
                               "interpreter": a.TOOLCACHE + "/bin/python3.12", "install_receipt": "toolcache-install"},
             "cache_source_pairs": [[rows[1][0], "module.py", source_sha, "observed", 0o644]]}
    class SyntheticInputs(a._PythonInputs):
        def capture(self, spelling, expected=None):
            result = compact(spelling)
            if expected is not None:
                a.need(a.canonical(result) == a.canonical(expected), "synthetic reviewed file changed")
            return result
    inputs = SyntheticInputs({"install_receipts": [{"id": "toolcache-install", "installed": [{"normalized_name": "example", "version": "1"}]}]})
    return inputs, metadata, entry, source, cache, record


def test_finite_generated_cache_and_all_nonwrapper_rows_are_checked(tmp_path):
    inputs, metadata, entry, _, _, _ = instance_fixture(tmp_path)
    inputs.instance(str(metadata), entry)


@pytest.mark.parametrize("mutation", ["source_bytes", "cache_magic", "cache_source_timestamp", "cache_owner_mode", "cache_unclassified",
                                      "cache_source_missing", "unknown_class", "wrong_installer", "new_wrapper", "wrong_stable_digest", "pending_missing_module"])
def test_positive_python_classifier_rejects_each_unresolved_input(tmp_path, mutation):
    inputs, metadata, entry, source, cache, _ = instance_fixture(tmp_path)
    if mutation == "source_bytes":
        source.write_bytes(b"changed\n")
    elif mutation == "cache_magic":
        cache.write_bytes(b"xxxx" + cache.read_bytes()[4:])
    elif mutation == "cache_source_timestamp":
        raw = bytearray(cache.read_bytes())
        raw[8:12] = b"\x00" * 4
        cache.write_bytes(raw)
    elif mutation == "cache_owner_mode":
        cache.chmod(0o666)
    elif mutation == "cache_unclassified":
        entry["cache_source_pairs"] = []
    elif mutation == "cache_source_missing":
        entry["cache_source_pairs"][0][1] = "unknown.py"
    elif mutation == "unknown_class":
        entry["class"] = "accept-all-provider-files"
    elif mutation == "wrong_installer":
        entry["install_receipt"] = "unreviewed-install"
    elif mutation == "new_wrapper":
        entry["unused_wrappers"] = [{"path": "module.py"}]
    elif mutation == "wrong_stable_digest":
        entry["stable_files_sha256"] = "9" * 64
    else:
        source.unlink()
    with pytest.raises((a.AdmissionError, FileNotFoundError)):
        inputs.instance(str(metadata), entry)


def install_fixture():
    steps = [{"id": name, "argv": list(argv)} for name, argv in a.INSTALL_ARGV.items()]
    value = {"schema_version": 3, **{key: binding()[key] for key in ("control_sha", "run_id", "run_attempt", "job")},
             "pip_version": "26.2.1", "wheel_sha256": a.PIP_WHEEL_SHA256,
             "setup_python": {"action": a.SETUP_ACTION, "python_version": "3.12.14", "outcome": "success"},
             "steps": [{"name": step["id"], "argv": list(step["argv"]), "exit_code": 0, "completed": True} for step in steps]}
    return value, {"install_receipts": steps}


def test_fixed_successful_install_receipt_is_bound_to_current_run():
    value, data = install_fixture()
    a.validate_install_receipt(value, binding(), data)


@pytest.mark.parametrize("mutate", [lambda x: x.update(run_attempt=2), lambda x: x.update(run_attempt=True),
                                      lambda x: x.update(control_sha="9" * 40), lambda x: x.update(pip_version="next"),
                                      lambda x: x.update(wheel_sha256="9" * 64), lambda x: x["steps"].pop(),
                                      lambda x: x["steps"][0].update(completed=False), lambda x: x["steps"][0].update(exit_code=1),
                                      lambda x: x["steps"][0].update(exit_code=False), lambda x: x["steps"][0]["argv"].append("--upgrade")])
def test_failed_stale_changed_or_unproven_install_stops(mutate):
    value, data = install_fixture()
    value = copy.deepcopy(value)
    mutate(value)
    with pytest.raises(a.AdmissionError):
        a.validate_install_receipt(value, binding(), data)


def test_unrelated_distribution_order_canonicalized_but_duplicate_precedence_preserved():
    instances = {"/one/a": {"location": "/one"}, "/one/b": {"location": "/one"}, "/two/a": {"location": "/two"}}
    first = {"path": ["/one", "/two"], "distribution_order": ["/one/a", "/one/b", "/two/a"],
             "duplicate_distributions": [{"normalized_name": "a", "instances": [0, 2]}]}
    reordered = {"path": ["/one", "/two"], "distribution_order": ["/one/b", "/one/a", "/two/a"],
                 "duplicate_distributions": [{"normalized_name": "a", "instances": [1, 2]}]}
    assert a.context_comparison_view(first, instances) == a.context_comparison_view(reordered, instances)
    reordered["duplicate_distributions"][0]["instances"] = [2, 1]
    assert a.context_comparison_view(first, instances) != a.context_comparison_view(reordered, instances)


def test_payload_provider_ownership_translated_only_through_verified_singleton_map():
    expected = {"files": [dict(file_record("/usr/lib/provider.py"), uid=0, gid=0), dict(file_record("/opt/extra-2/module.py"), uid=1001, gid=1001)]}
    for item in expected["files"]:
        item.pop("symlinks")
    mapping = {"uid_map_raw": "      1001       1001          1\n", "gid_map_raw": "1001 1001 1\n",
               "overflowuid_raw": "65534\n", "overflowgid_raw": "65534\n"}
    translated = a.translate_payload_owners(expected, mapping, {"uid": 1001, "gid": 1001}, {"uid": 1001, "gid": 1001})
    assert translated["files"][0]["uid"] == 65534 and translated["files"][0]["gid"] == 65534
    assert translated["files"][1]["uid"] == translated["files"][1]["gid"] == 1001
    mapping["uid_map_raw"] = "1001 1001 1\n0 0 1\n"
    with pytest.raises(a.AdmissionError):
        a.translate_payload_owners(expected, mapping, {"uid": 1001, "gid": 1001}, {"uid": 1001, "gid": 1001})


def test_payload_alias_order_keeps_each_distinct_mount():
    value = {"path": ["/opt/extra-0", "/opt/extra-1"], "distributions": [
        {"location": "/opt/extra-0", "metadata_path": "/opt/extra-0/b.dist-info"},
        {"location": "/opt/extra-0", "metadata_path": "/opt/extra-0/a.dist-info"},
        {"location": "/opt/extra-1", "metadata_path": "/opt/extra-1/a.dist-info"}]}
    other = copy.deepcopy(value)
    other["distributions"][:2] = reversed(other["distributions"][:2])
    assert a.payload_comparison_view(value) == a.payload_comparison_view(other)
    other["path"].reverse()
    assert a.payload_comparison_view(value) != a.payload_comparison_view(other)


def test_missing_postload_python_context_cannot_finalize(tmp_path, monkeypatch):
    gate, _, _, _, _ = synthetic_gate(tmp_path, monkeypatch)
    receipt = gate.prepare()
    gate.recheck_before_load(receipt)
    gate.verify_after_load(receipt)
    with pytest.raises(a.AdmissionError):
        gate.final_source_recheck(receipt)
    assert gate._state == "STOP"


@pytest.mark.parametrize("name", ["usercustomize.cpython-312-x86_64-linux-gnu.so", "sitecustomize.abi3.so", "usercustomize.so"])
def test_extension_startup_addition_changes_finite_directory_state(tmp_path, name):
    root = tmp_path / "site"
    root.mkdir()
    data = {"contexts": {"host": {"search_path_states": [{"path": str(root), "state": "directory"}]}}}
    before = a.python_directory_state(data)
    (root / name).write_bytes(b"unapproved extension")
    assert a.python_directory_state(data) != before


def test_absent_python_archive_and_user_site_roots_are_bound(tmp_path):
    archive, user_site = tmp_path / "python312.zip", tmp_path / "user-site"
    data = {"contexts": {"host": {"search_path_states": [{"path": str(p), "state": "absent"} for p in (archive, user_site)]}}}
    inputs = a._PythonInputs(data)
    a.bind_python_search_roots(inputs, data)
    assert inputs.absent == {str(archive), str(user_site)}
    archive.write_bytes(b"unapproved import archive")
    with pytest.raises(a.AdmissionError, match="appeared"):
        a.bind_python_search_roots(inputs, data)


def test_recheck_rejects_previously_absent_archive_and_extension_hook(tmp_path, monkeypatch):
    gate, _, dirs, _, _ = synthetic_gate(tmp_path, monkeypatch)
    gate.prepare()
    archive = dirs[0] / "python312.zip"
    data = {"contexts": {"host": {"search_path_states": [{"path": str(dirs[0]), "state": "directory"},
                                                           {"path": str(archive), "state": "absent"}]}}}
    gate._python_data_bytes = a.canonical(data)
    gate._python_state_bytes = a.canonical({"absent": [str(archive)], "directories": a.python_directory_state(data), "environment": {}})
    gate._recheck_files()
    archive.write_bytes(b"new archive")
    with pytest.raises(a.AdmissionError, match="appeared"):
        gate._recheck_files()
    archive.unlink()
    (dirs[0] / "sitecustomize.cpython-312-x86_64-linux-gnu.so").write_bytes(b"new extension hook")
    with pytest.raises(a.AdmissionError, match="startup"):
        gate._recheck_files()


@pytest.mark.parametrize("name", ["usercustomize.cpython-312-x86_64-linux-gnu.so", "sitecustomize.abi3.so", "usercustomize.so", "unreviewed.pth"])
def test_preexisting_unapproved_startup_input_cannot_be_baselined(tmp_path, name):
    root = tmp_path / "site"
    root.mkdir()
    (root / name).write_bytes(b"code that could raise ImportError after executing")
    data = {"contexts": {"host": {"search_path_states": [{"path": str(root), "state": "directory"}],
                                   "startup_inputs": [], "loaded_hooks": {"sitecustomize": None, "usercustomize": None}}}}
    baseline = a.python_directory_state(data)
    with pytest.raises(a.AdmissionError, match="unapproved initial"):
        a.validate_initial_startup(data, baseline)


def test_initial_startup_requires_exact_declared_path(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    hook = root / "reviewed.pth"
    hook.write_bytes(b"# declared and separately byte-checked\n")
    data = {"contexts": {"host": {"search_path_states": [{"path": str(root), "state": "directory"}],
                                   "startup_inputs": [{"path": str(hook)}], "loaded_hooks": {}}}}
    a.validate_initial_startup(data, a.python_directory_state(data))



def setup_classification_fixture():
    return json.loads((Path(__file__).resolve().parents[1] / "qualification-python.json").read_bytes())


def test_only_exact_provider_pip_gains_404_source_equivalence_pairs():
    data = setup_classification_fixture()
    a.validate_setup_provenance(data)
    pip = data["instances"][a.PIP_INSTANCE]
    assert pip["class"] == "provider" and pip["install_receipt"] is None
    assert pip["cache_binding"]["class"] == "provider-source-equivalent-cpython312"
    assert len(pip["cache_source_pairs"]) == 404 and len(pip["stable_file_rows"]) == 481
    assert pip["record"]["row_count"] == 885
    assert a.digest({k: v for k, v in data["instances"].items() if k != a.PIP_INSTANCE}) == "f0c504957dcf1b66c6e11c63ff6db670a5580ed3fec6f4deeab57cba6f249231"
    assert data["setup_python"]["historical_raw_cache_headers"] == "not collected"
    assert data["installer"]["wheel_install_source"]["sha256"] == a.PIP_WHEEL_SHA256
    assert [x["id"] for x in data["install_receipts"]] == ["toolcache-install", "system-user-site-install"]


@pytest.mark.parametrize("mutation", [
    "path", "canonical", "location", "name", "version", "class", "receipt", "pair_extra", "pair_missing", "pair_source",
    "pair_hash", "pair_cache", "pair_mode", "stable_hash", "stable_row", "metadata", "record", "other_pip", "other_provider",
])
def test_provider_source_equivalence_cannot_expand_or_replace_original_sources(mutation):
    data = setup_classification_fixture()
    entry = data["instances"][a.PIP_INSTANCE]
    if mutation == "path":
        data["instances"][a.PIP_INSTANCE + "-other"] = data["instances"].pop(a.PIP_INSTANCE)
    elif mutation in {"canonical", "location", "name", "version", "class", "receipt"}:
        field = {"canonical": "metadata_canonical", "receipt": "install_receipt"}.get(mutation, mutation)
        entry[field] = {"class": "current-job-pip", "version": "26.2.2", "receipt": "toolcache-install"}.get(mutation, "/unreviewed")
    elif mutation == "pair_extra":
        entry["cache_source_pairs"].append(list(entry["cache_source_pairs"][0]))
    elif mutation == "pair_missing":
        entry["cache_source_pairs"].pop()
    elif mutation.startswith("pair_"):
        column = {"pair_source": 1, "pair_hash": 2, "pair_cache": 0, "pair_mode": 4}[mutation]
        entry["cache_source_pairs"][0][column] = 0o644 if column == 4 else "unreviewed"
    elif mutation == "stable_hash":
        entry["stable_files_sha256"] = "9" * 64
    elif mutation == "stable_row":
        entry["stable_file_rows"].pop()
    elif mutation == "metadata":
        next(x for x in entry["metadata_inputs"] if x["state"] == "observed")["identity"]["sha256"] = "9" * 64
    elif mutation == "record":
        entry["record"]["identity"]["sha256"] = "9" * 64
    else:
        other = copy.deepcopy(entry)
        if mutation == "other_provider":
            other.update(name="other", normalized_name="other", version="1")
        data["instances"]["/usr/lib/python3/dist-packages/other.dist-info"] = other
    with pytest.raises(a.AdmissionError):
        a.validate_setup_provenance(data)


@pytest.mark.parametrize("field", ["action", "python_version", "release_url", "setup_template_blob", "setup_template_source",
                                   "installer_version", "installer_source", "installer_source_blob", "evidence", "historical_raw_cache_headers", "qualification_requirement"])
def test_setup_python_reviewed_source_provenance_is_immutable(field):
    data = setup_classification_fixture()
    data["setup_python"][field] = "unreviewed"
    with pytest.raises(a.AdmissionError, match="source provenance"):
        a.validate_setup_provenance(data)


@pytest.mark.parametrize("mutation", ["missing", "action", "version", "failed", "skipped", "extra", "old_schema", "missing_step", "extra_step", "reordered_steps", "mutated_reference"])
def test_setup_python_and_two_original_install_receipts_remain_required(mutation):
    value, data = install_fixture()
    setup = value["setup_python"]
    if mutation == "missing":
        value.pop("setup_python")
    elif mutation == "action":
        setup["action"] = "actions/setup-python@main"
    elif mutation == "version":
        setup["python_version"] = "3.12"
    elif mutation in {"failed", "skipped"}:
        setup["outcome"] = mutation
    elif mutation == "extra":
        setup["fresh"] = {"target_absent": True}
    elif mutation == "old_schema":
        value["schema_version"] = 2
    elif mutation == "missing_step":
        value["steps"].pop(0)
    elif mutation == "extra_step":
        value["steps"].insert(0, dict(value["steps"][0], name="pip-reinstall"))
    elif mutation == "reordered_steps":
        value["steps"][:2] = reversed(value["steps"][:2])
    else:
        value["steps"][0]["argv"].append("--no-compile")
        data["install_receipts"][0]["argv"].append("--no-compile")
    with pytest.raises(a.AdmissionError):
        a.validate_install_receipt(value, binding(), data)


def test_unchanged_system_provider_pip24_retains_original_exact_byte_class():
    data = setup_classification_fixture()
    path = "/usr/lib/python3/dist-packages/pip-24.0.dist-info"
    entry = data["instances"][path]
    assert entry["class"] == "provider" and entry["install_receipt"] is None
    assert entry["cache_source_pairs"] == [] and entry["cache_binding"] is None
    a.validate_setup_pip_instance(path, entry)
    entry["class"] = "current-job-pip"
    with pytest.raises(a.AdmissionError, match="provider pip classification"):
        a.validate_setup_pip_instance(path, entry)


def provider_cache_fixture(tmp_path, *, optimize=0, dfile=None):
    import py_compile
    source = tmp_path / "module.py"
    source.write_bytes(b'"""provider documentation"""\nassert True\nVALUE = __debug__\nraise RuntimeError("must never execute")\n')
    path = Path(py_compile.compile(str(source), dfile=dfile, doraise=True, optimize=optimize,
                                 invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP))
    identity = a.file_identity(source)
    return source, path, identity


@pytest.mark.parametrize("provider_case", ["cached", "fresh"])
def test_real_py_compile_writer_body_compares_without_execution_or_unmarshal(tmp_path, monkeypatch, provider_case):
    import py_compile
    source, cache, identity = provider_cache_fixture(tmp_path)
    if provider_case == "fresh":
        os.utime(source, (source.stat().st_atime, source.stat().st_mtime + 100))
        py_compile.compile(str(source), doraise=True, optimize=0, invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
    def forbidden(*args, **kwargs):
        pytest.fail("candidate cache was unmarshalled")
    monkeypatch.setattr(a.marshal, "loads", forbidden)
    raw = cache.read_bytes()
    a.generated_cache_header(raw, identity, source.stat().st_mtime)
    a.verify_provider_cache_body(raw, source.read_bytes(), identity)


@pytest.mark.parametrize("mutation", ["body", "trailing", "truncated", "wrong_source", "wrong_path", "optimized", "stripped_docstring", "magic", "flags", "size", "mtime"])
def test_source_equivalence_rejects_body_header_path_source_and_optimization_drift(tmp_path, mutation):
    source, cache, identity = provider_cache_fixture(tmp_path, optimize=1 if mutation == "optimized" else 2 if mutation == "stripped_docstring" else 0,
                                                     dfile="/unreviewed/module.py" if mutation == "wrong_path" else None)
    raw = bytearray(cache.read_bytes())
    source_raw = source.read_bytes()
    if mutation == "body":
        raw[-1] ^= 1
    elif mutation == "trailing":
        raw.extend(b"unchecked trailing code")
    elif mutation == "truncated":
        raw = raw[:-1]
    elif mutation == "wrong_source":
        source_raw += b"NEW = True\n"
    elif mutation in {"magic", "flags", "size", "mtime"}:
        index = {"magic": 0, "flags": 4, "size": 12, "mtime": 8}[mutation]
        raw[index] ^= 1
    with pytest.raises(a.AdmissionError):
        a.generated_cache_header(bytes(raw), identity, source.stat().st_mtime)
        a.verify_provider_cache_body(bytes(raw), source_raw, identity)


def test_pip_provider_instance_uses_body_check_in_addition_to_existing_header(tmp_path, monkeypatch):
    import py_compile
    inputs, metadata, entry, source, cache, _ = instance_fixture(tmp_path)
    py_compile.compile(str(source), cfile=str(cache), doraise=True, optimize=0,
                       invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
    entry.update({"class": "provider", "install_receipt": None})
    entry["cache_binding"].update({"class": "provider-source-equivalent-cpython312", "install_receipt": None})
    # This synthetic finite instance exercises runtime body checks only. The
    # separate real-manifest tests retain every concrete pip path/class pin.
    monkeypatch.setattr(a, "PIP_INSTANCE", str(metadata))
    monkeypatch.setattr(a, "validate_setup_pip_instance", lambda *_: None)
    inputs.instance(str(metadata), entry)
    raw = bytearray(cache.read_bytes())
    raw[-1] ^= 1
    cache.write_bytes(raw)
    with pytest.raises(a.AdmissionError, match="body differs"):
        inputs.instance(str(metadata), entry)


@pytest.mark.parametrize("mutation", [None, "path", "version", "implementation", "optimization", "bytes", "identity"])
def test_cache_compiler_must_be_exact_matched_interpreter(monkeypatch, mutation):
    from types import SimpleNamespace
    data = setup_classification_fixture()
    actual = copy.deepcopy(data["installer"]["interpreter"])
    monkeypatch.setattr(a.sys, "executable", "/unreviewed/python" if mutation == "path" else a.CONTEXTS["host"])
    monkeypatch.setattr(a.sys, "version_info", (3, 12, 13) if mutation == "version" else (3, 12, 14))
    monkeypatch.setattr(a.sys, "implementation", SimpleNamespace(name="pypy" if mutation == "implementation" else "cpython"))
    monkeypatch.setattr(a.sys, "flags", SimpleNamespace(optimize=1 if mutation == "optimization" else 0))
    if mutation == "bytes":
        actual["sha256"] = "9" * 64
    elif mutation == "identity":
        actual["canonical"] = "/unreviewed/python"
    monkeypatch.setattr(a.facts, "executable_identity", lambda *_args, **_kwargs: actual)
    if mutation is None:
        a.verify_cache_body_interpreter(data)
    else:
        with pytest.raises(a.AdmissionError):
            a.verify_cache_body_interpreter(data)


def test_same_attempt_provider_cache_whole_bytes_remain_bound(tmp_path):
    path = tmp_path / "module.cpython-312.pyc"
    path.write_bytes(b"captured provider cache bytes")
    inputs = a._PythonInputs({})
    original = inputs.capture(str(path))
    assert original["sha256"] == checksum(path.read_bytes())
    path.write_bytes(b"new provider cache bytes")
    with pytest.raises(a.AdmissionError, match="changed during admission"):
        inputs.capture(str(path))
