"""Pure/offline setup policy tests; no policy load or host securityfs access."""
from __future__ import annotations
import copy
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from forge_ci import setup_policy as a  # noqa: E402
facts = probes = c = a

@pytest.fixture(autouse=True)
def no_live_mutation(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("setup test attempted a live command/network")
    monkeypatch.setattr(subprocess, "Popen", denied)
    monkeypatch.setattr(a.http.client, "HTTPSConnection", denied)

def checksum(raw):
    return hashlib.sha256(raw).hexdigest()


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


SYNTHETIC_COMPILED = b"synthetic bwrap buffer" + b"synthetic unpriv_bwrap buffer"


def added(before):
    result = copy.deepcopy(before)
    for name, attach in (("bwrap", "/usr/bin/bwrap"), ("unpriv_bwrap", "<unknown>")):
        raw = ("synthetic " + name + " buffer").encode()
        result["profiles"].append({"namespace": "", "name": name, "mode": "enforce", "attachment": attach,
                                   "metadata": {"raw_data": {"sha256": checksum(raw), "bytes": len(raw)}}})
    result["profiles"].sort(key=lambda p: (p["namespace"], p["name"]))
    return result


def test_kernel_opaque_display_requires_exact_reviewed_digest(monkeypatch):
    fixture = kernel_fixture(monkeypatch)
    assert a.validate_kernel(fixture) == fixture["semantic"]
    changed = copy.deepcopy(fixture["semantic"])
    changed["profiles"][0]["metadata"]["raw_data"]["sha256"] = "7" * 64
    with pytest.raises(a.SetupError, match="independent review"):
        a.validate_kernel(kernel_record(changed))


def test_kernel_does_not_trust_self_reported_hash_or_absence(monkeypatch):
    fixture = kernel_fixture(monkeypatch)
    fixture["semantic"]["profiles"][0]["name"] = "bwrap"
    with pytest.raises(a.SetupError):
        a.validate_kernel(fixture)
    fixture = kernel_record(fixture["semantic"])
    fixture["conflicting_names"] = []
    with pytest.raises(a.SetupError):
        a.validate_kernel(fixture)


def test_after_add_exact_pair_and_prior_inventory(monkeypatch):
    before = kernel_fixture(monkeypatch)["semantic"]
    after = added(before)
    assert a.validate_kernel(kernel_record(after), before=before, compiled=SYNTHETIC_COMPILED) == after


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
    with pytest.raises(a.SetupError):
        a.validate_kernel(kernel_record(after), before=before, compiled=SYNTHETIC_COMPILED)


def test_loaded_listing_is_independent_required_crosscheck(monkeypatch):
    fixture = kernel_fixture(monkeypatch)
    fixture["loaded_profiles"] = []
    with pytest.raises(a.SetupError, match="listing disagreement"):
        a.validate_kernel(fixture)


def test_opaque_new_attachment_uses_exact_compiled_raw_blob(monkeypatch):
    before = kernel_fixture(monkeypatch)["semantic"]
    after = added(before)
    after["profiles"][0]["attachment"] = "<unknown>"
    assert a.validate_kernel(kernel_record(after), before=before, compiled=SYNTHETIC_COMPILED)
    with pytest.raises(a.SetupError):
        a.validate_kernel(kernel_record(after), before=before, compiled=b"changed " + SYNTHETIC_COMPILED)


ACTUAL_COMPILED_ZLIB_BASE64 = (
    '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'
)


def actual_compiled_fixture(monkeypatch):
    import base64
    import zlib
    raw = zlib.decompress(base64.b64decode(ACTUAL_COMPILED_ZLIB_BASE64, validate=True))
    assert len(raw) == 14259
    assert checksum(raw) == "94019d5a10f8fee508a1ca5e1cb8a32143758f688ae4a93a3c81a6ece814b5ae"
    exports = {
        "bwrap": {"bytes": 7833, "sha256": "dec0dc4bd716f3758114cfbf05217d241adf4c5f1819008e93e6904c784b1bd5"},
        "unpriv_bwrap": {"bytes": 6426, "sha256": "d0a32dc327fe0844176b877d9283d587995d51a4ea4495a9bc764d9d53be0fee"},
    }
    before = kernel_fixture(monkeypatch)["semantic"]
    after = added(before)
    for profile in after["profiles"]:
        if profile["name"] in exports:
            profile["metadata"]["raw_data"] = copy.deepcopy(exports[profile["name"]])
    return before, after, raw


def test_actual_two_kernel_export_blobs_exhaust_authenticated_compile_stream(monkeypatch):
    before, after, raw = actual_compiled_fixture(monkeypatch)
    assert checksum(raw[:7833]) == "dec0dc4bd716f3758114cfbf05217d241adf4c5f1819008e93e6904c784b1bd5"
    assert checksum(raw[7833:]) == "d0a32dc327fe0844176b877d9283d587995d51a4ea4495a9bc764d9d53be0fee"
    assert a.validate_kernel(kernel_record(after), before=before, compiled=raw) == after


@pytest.mark.parametrize("change", [
    "missing_profile", "extra_profile", "duplicate_profile", "swapped_names", "swapped_raw_links", "duplicate_raw_link", "missing_raw_data",
    "missing_hash", "extra_raw_field", "changed_hash", "zero_size", "negative_size", "bool_size", "float_size", "overflow_size",
    "wrong_boundary", "child_complain", "wrong_namespace", "changed_prior", "changed_namespaces", "altered_parent", "altered_child",
    "swapped_stream", "trailing", "leading", "truncated", "aggregate_twice", "empty", "summary_instead_of_bytes",
])
def test_actual_two_blob_partition_rejects_every_unaccounted_or_misattributed_input(monkeypatch, change):
    before, after, raw = actual_compiled_fixture(monkeypatch)
    parent = next(p for p in after["profiles"] if p["name"] == "bwrap")
    child = next(p for p in after["profiles"] if p["name"] == "unpriv_bwrap")
    if change == "missing_profile":
        after["profiles"].remove(child)
    elif change == "extra_profile":
        after["profiles"].append(dict(child, name="extra"))
    elif change == "duplicate_profile":
        after["profiles"].append(copy.deepcopy(parent))
    elif change == "swapped_names":
        parent["name"], child["name"] = child["name"], parent["name"]
    elif change == "swapped_raw_links":
        parent["metadata"]["raw_data"], child["metadata"]["raw_data"] = child["metadata"]["raw_data"], parent["metadata"]["raw_data"]
    elif change == "duplicate_raw_link":
        child["metadata"]["raw_data"] = copy.deepcopy(parent["metadata"]["raw_data"])
    elif change == "missing_raw_data":
        parent["metadata"].pop("raw_data")
    elif change == "missing_hash":
        parent["metadata"]["raw_data"].pop("sha256")
    elif change == "extra_raw_field":
        parent["metadata"]["raw_data"]["trust"] = True
    elif change == "changed_hash":
        parent["metadata"]["raw_data"]["sha256"] = "9" * 64
    elif change in {"zero_size", "negative_size", "bool_size", "float_size", "overflow_size", "wrong_boundary"}:
        parent["metadata"]["raw_data"]["bytes"] = {
            "zero_size": 0, "negative_size": -1, "bool_size": True, "float_size": 7833.0,
            "overflow_size": len(raw) + 1, "wrong_boundary": 7834,
        }[change]
    elif change == "child_complain":
        child["mode"] = "complain"
    elif change == "wrong_namespace":
        child["namespace"] = "other"
    elif change == "changed_prior":
        next(p for p in after["profiles"] if p["name"] == "reviewed")["metadata"] = {}
    elif change == "changed_namespaces":
        after["namespaces"].append("other")
    elif change in {"altered_parent", "altered_child"}:
        offset = 30 if change == "altered_parent" else 7900
        raw = raw[:offset] + bytes([raw[offset] ^ 1]) + raw[offset + 1:]
    elif change == "swapped_stream":
        raw = raw[7833:] + raw[:7833]
    elif change == "trailing":
        raw += b"unaccounted bytes"
    elif change == "leading":
        raw = b"unaccounted bytes" + raw
    elif change == "truncated":
        raw = raw[:-1]
    elif change == "aggregate_twice":
        raw *= 2
    elif change == "empty":
        raw = b""
    else:
        raw = {"sha256": checksum(raw), "bytes": len(raw)}
    after["profiles"].sort(key=lambda p: (p["namespace"], p["name"]))
    with pytest.raises((a.SetupError, a.SetupError)):
        a.validate_kernel(kernel_record(after), before=before, compiled=raw)


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


def test_negative_requires_native_child_not_wrapper_pid():
    record = negative()
    probes.validate_negative_control(record)
    for wrong in (record["wrapper_pid"], record["wrapper_pid"] + 1):
        record["audit"] = [audit(wrong)]
        with pytest.raises(a.SetupError, match="missing or ambiguous"):
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
    with pytest.raises(a.SetupError):
        probes.validate_negative_control(record)


def test_audit_duplicate_key_and_unfiltered_content_rejected():
    for records in (
        [audit() + " pid=456"],
        ["unrelated private log"],
        [audit(), audit(cap=21, name="sys_admin", serial=6)],
    ):
        with pytest.raises(a.SetupError):
            probes.validate_audit(
                records,
                pid=456,
                start_ns=1700000000123000000,
                end_ns=1700000000124000000,
                capability=12,
                capname="net_admin",
                profiles=("unprivileged_userns",),
            )


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
    with pytest.raises(a.SetupError):
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
    with pytest.raises(a.SetupError):
        probes.validate_negative_control(record)


def command(argv, stdout=b"", stderr=b"", returncode=0):
    return {"argv": argv, "returncode": returncode, "wrapper_pid": 123,
            "stdout": stdout.decode("utf-8", errors="replace"), "stdout_hex": stdout.hex(),
            "stderr": stderr.decode("utf-8", errors="replace"), "stderr_hex": stderr.hex(),
            "started": {"utc_ns": 10**18, "monotonic_ns": 100},
            "ended": {"utc_ns": 10**18 + 10**6, "monotonic_ns": 10**6 + 100}}


def io_uring_warnings(profile):
    return (f"Warning from profile bwrap ({profile}): io_uring rules not enforced\n"
            f"Warning from profile unpriv_bwrap ({profile}): io_uring rules not enforced\n").encode()


@pytest.mark.parametrize("operation", ["compile", "load"])
@pytest.mark.parametrize("reverse", [False, True])
def test_only_exact_two_io_uring_warning_lines_are_accepted_and_preserved(tmp_path, operation, reverse):
    profile, config = tmp_path / "bwrap-userns-restrict", tmp_path / "parser.conf"
    argv = c.parser_argv(operation, profile, config)
    warnings = io_uring_warnings(profile)
    if reverse:
        warnings = b"".join(reversed(warnings.splitlines(keepends=True)))
    output = b"\x00\xfftrusted parser bytes" if operation == "compile" else b""
    record = command(argv, output, warnings)
    original = copy.deepcopy(record)
    assert c.validate_parser(record, argv, operation) == (output, warnings)
    assert record == original



def config_fixture():
    owner = {"id": 19586012, "login": "HouMinXi", "type": "User"}
    return {"schema_version": 1, "nonce": "1" * 32, "seed_sha": "a" * 40, "source_sha256": "b" * 64,
            "repository": {"id": 1258832822, "name": "forge", "full_name": "HouMinXi/forge", "owner": owner},
            "publisher": dict(owner), "authorized_retrier": dict(owner)}


def native_fixture(image="20261004.327.1"):
    cfg = config_fixture()
    contract = a.validate_config(cfg)
    context = {"GITHUB_EVENT_NAME": "push", "GITHUB_REF_TYPE": "branch", "GITHUB_REF": contract["ref"],
               "GITHUB_REPOSITORY": "HouMinXi/forge", "GITHUB_REPOSITORY_OWNER": "HouMinXi", "GITHUB_REPOSITORY_ID": "1258832822",
               "GITHUB_REPOSITORY_OWNER_ID": "19586012", "GITHUB_ACTOR": "HouMinXi", "GITHUB_ACTOR_ID": "19586012",
               "GITHUB_TRIGGERING_ACTOR": "HouMinXi", "GITHUB_WORKFLOW_REF": "HouMinXi/forge/" + contract["workflow_path"] + "@" + contract["ref"],
               "GITHUB_WORKFLOW_SHA": "c" * 40, "GITHUB_SHA": "c" * 40, "GITHUB_RUN_NUMBER": "1", "GITHUB_RUN_ID": "123",
               "GITHUB_RUN_ATTEMPT": "2", "GITHUB_JOB": "qualification", "GITHUB_SERVER_URL": "https://github.com",
               "GITHUB_API_URL": "https://api.github.com", "GITHUB_EVENT_PATH": "/native-event.json", "RUNNER_OS": "Linux",
               "RUNNER_ARCH": "X64", "RUNNER_ENVIRONMENT": "github-hosted", "ImageOS": "ubuntu24", "ImageVersion": image,
               "FORGE_RUNNER_UID": "1001", "FORGE_RUNNER_GID": "1001"}
    event = {"created": False, "deleted": False, "forced": False, "ref": contract["ref"], "before": cfg["seed_sha"],
             "after": context["GITHUB_SHA"], "repository": cfg["repository"], "sender": cfg["publisher"]}
    return cfg, context, event


@pytest.mark.parametrize("image", ["20261004.327.1", "20260927.320.1"])
def test_two_hosted_image_versions_are_evidence_not_admission_identity(image):
    cfg, context, event = native_fixture(image)
    assert a.validate_initial_identity(cfg, context, event) == {"sha": "c" * 40, "run_id": 123, "run_attempt": 2, "job": "qualification"}


@pytest.mark.parametrize("field", ["GITHUB_ACTOR_ID", "GITHUB_TRIGGERING_ACTOR", "GITHUB_REF", "GITHUB_WORKFLOW_SHA", "GITHUB_RUN_NUMBER",
                                   "GITHUB_REPOSITORY_ID", "GITHUB_REPOSITORY_OWNER_ID", "GITHUB_EVENT_NAME", "RUNNER_ENVIRONMENT"])
def test_native_identity_drift_stops_before_any_candidate_action(field):
    cfg, context, event = native_fixture()
    context[field] = "unreviewed"
    with pytest.raises(a.SetupError):
        a.validate_initial_identity(cfg, context, event)


@pytest.mark.parametrize("field,value", [("forced", True), ("created", True), ("deleted", True), ("before", "d" * 40), ("after", "d" * 40)])
def test_non_exact_seed_event_stops(field, value):
    cfg, context, event = native_fixture()
    event[field] = value
    with pytest.raises(a.SetupError):
        a.validate_initial_identity(cfg, context, event)


def test_observer_contains_no_bootstrap_loader_package_or_command_interface():
    import ast
    source = Path(a.__file__).read_text()
    cfg = config_fixture()
    one = a.observer_source(source, cfg)
    two = a.observer_source(source, dict(reversed(list(cfg.items()))))
    assert one == two
    parsed = ast.parse(one)
    names = {n.name for n in parsed.body if isinstance(n, ast.FunctionDef)}
    assert {"observe_sealed", "observer_main", "validate_kernel"} <= names
    assert not names & {"bootstrap", "command", "checked_command", "install_vendor", "download_archive", "runner_probe", "parser_argv", "kill_owned_command"}
    assert b"--add" not in one and b"--install" not in one and b"subprocess.Popen(" not in one
    assert b"forge_ci" not in one and b"code_forge" not in one
    assert source.split("def observe_state(", 1)[1].split("\ndef stable_state", 1)[0].encode() in one


def test_observer_cli_rejects_arbitrary_modes_before_observation(monkeypatch):
    monkeypatch.setattr(a.sys, "argv", ["observer.py", "setup"])
    monkeypatch.setattr(a, "observe_sealed", lambda *_: pytest.fail("observer attempted setup"))
    with pytest.raises(a.SetupError):
        a.observer_main(config_fixture())


def test_fixed_observer_argv_is_privileged_timeout_isolated_system_python():
    binding = {"nonce": "1" * 32, "control_sha": "c" * 40, "source_sha256": "b" * 64,
               "run_id": 123, "run_attempt": 2, "job": "qualification", "boot_id": "1" * 36}
    argv = a.observer_argv(binding)
    assert argv[:6] == ["/usr/bin/sudo", "-n", "--", "/usr/bin/timeout", "--signal=KILL", "25s"]
    assert argv[-6:] == ["/usr/bin/python3", "-B", "-I", "-S", "/var/lib/forge-qualification/" + "1" * 32 + "/123-2/observer.py", "observe"]
    assert "-i" in argv and "HOME=/nonexistent" in argv


@pytest.mark.parametrize("owner,passes", [(0, True), (1001, True), (1002, False)])
def test_cached_root_or_fresh_runner_provider_executable_has_same_finite_path_contract(tmp_path, monkeypatch, owner, passes):
    root = tmp_path / "provider"
    (root / "bin").mkdir(parents=True)
    exe = root / "bin/python3.12"
    exe.write_bytes(b"\x7fELFtrusted provider")
    exe.chmod(0o755)
    (root / "bin/python").symlink_to("python3.12")
    monkeypatch.setattr(a, "PROVIDER_ROOT", str(root))
    original = Path.stat
    def metadata(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == exe:
            return SimpleNamespace(st_mode=info.st_mode, st_uid=owner, st_gid=owner)
        return info
    monkeypatch.setattr(Path, "stat", metadata)
    def no_caps(*_):
        raise OSError(errno.ENODATA, "no capabilities")
    monkeypatch.setattr(a.os, "getxattr", no_caps)
    if passes:
        assert a.ordinary_executable(str(root / "bin/python"), provider_uid=1001)["uid"] == owner
    else:
        with pytest.raises(a.SetupError):
            a.ordinary_executable(str(root / "bin/python"), provider_uid=1001)


@pytest.mark.parametrize("change", [None, "bytes", "missing", "extra", "symlink"])
def test_finite_57_feature_snapshot_is_still_exact(change):
    root = a.APPARMOR_ROOT + "/features"
    class Reader:
        def tree(self, _):
            result = {root: {"type": "d"}, **{root + "/" + name: {"type": "f"} for name in a.EXPECTED_FEATURES}}
            if change == "missing":
                result.pop(root + "/io_uring/mask")
            elif change == "extra":
                result[root + "/unreviewed"] = {"type": "f"}
            elif change == "symlink":
                result[root + "/io_uring/mask"] = {"type": "l"}
            return result
        def read(self, path, **_):
            raw = a.EXPECTED_FEATURES[path[len(root) + 1:]]["text"].encode()
            return raw + b"changed" if change == "bytes" and path.endswith("io_uring/mask") else raw
    if change is None:
        assert a.digest(a.observe_features(Reader())) == a.FEATURES_SHA256
    else:
        with pytest.raises(a.SetupError):
            a.observe_features(Reader())


@pytest.mark.parametrize("state", ["reaped", "reap_gap", "live", "exited_unreaped", "vanished"])
def test_command_cleanup_signals_only_still_owned_child_group(monkeypatch, state):
    calls = []
    process = SimpleNamespace(pid=777, returncode=0 if state == "reaped" else None, wait=lambda **_: calls.append("wait"))
    def ownership(*args):
        calls.append("ownership")
        assert args[-1] & os.WNOWAIT
        if state == "reap_gap":
            raise ChildProcessError
        return SimpleNamespace(si_pid=777) if state == "exited_unreaped" else None
    def kill(pid, sig):
        calls.append("kill")
        assert pid == 777 and sig == a.signal.SIGKILL
        if state == "vanished":
            raise ProcessLookupError
    monkeypatch.setattr(a.os, "waitid", ownership)
    monkeypatch.setattr(a.os, "killpg", kill)
    a.kill_owned_command(process)
    assert ("kill" in calls) is (state not in {"reaped", "reap_gap"})


@pytest.mark.parametrize("operation", ["preprocess", "compile", "load"])
@pytest.mark.parametrize("change", ["warnings", "missing", "duplicate", "wrong_path", "unknown_class", "nonzero", "timeout", "bad_raw"])
def test_early_parser_exact_diagnostics_and_failure_matrix(tmp_path, operation, change):
    profile, config = tmp_path / "bwrap-userns-restrict", tmp_path / "parser.conf"
    argv = a.parser_argv(operation, profile, config)
    warning = io_uring_warnings(profile)
    stderr = b"" if operation == "preprocess" else warning
    output = b"compiled" if operation == "compile" else b"policy text" if operation == "preprocess" else b""
    record = command(argv, output, stderr)
    if change == "warnings":
        record.update(stderr="unknown warning", stderr_hex=b"unknown warning".hex())
    elif change in {"missing", "duplicate", "wrong_path", "unknown_class"}:
        if operation == "preprocess":
            raw = warning
        elif change == "missing":
            raw = b""
        elif change == "duplicate":
            raw = warning.splitlines(keepends=True)[0] * 2
        elif change == "wrong_path":
            raw = warning.replace(str(profile).encode(), b"/wrong")
        else:
            raw = warning.replace(b"io_uring", b"userns")
        record.update(stderr=raw.decode(), stderr_hex=raw.hex())
    elif change == "nonzero":
        record["returncode"] = 1
    elif change == "timeout":
        record["error"] = "timeout"
    else:
        record["stdout_hex"] = "zz"
    with pytest.raises(a.SetupError):
        a.validate_parser(record, argv, operation)


@pytest.fixture
def bootstrap_world(tmp_path, monkeypatch):
    cfg, context, event = native_fixture()
    for key, value in context.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(a, "STAGE_ROOT", tmp_path / "root-stage")
    source = Path(a.__file__).read_text()
    before = {"schema": 1, "namespaces": [""], "scope": {"ns_level": "0", "ns_name": "root", "stacked": "no", "ns_stacked": "no"},
              "profiles": [{"namespace": "", "name": f"reviewed{i:03d}", "mode": "enforce", "attachment": "<unknown>",
                            "metadata": {"raw_data": {"sha256": "6" * 64, "bytes": 12}}} for i in range(123)]}
    monkeypatch.setattr(a, "INVENTORY_SHA256", a.digest(before))
    after = added(before)
    host = {"boot_id": "11111111-1111-1111-1111-111111111111", "kernel": "reviewed", "userns_restriction": "1"}
    world = SimpleNamespace(config=cfg, source=source, fail=None, events=[], files={}, stages=[], observations=0,
                            live_count=0, reads={}, host=host, before=before, after=after)
    class Evidence:
        def __init__(self, enabled=True):
            self.enabled = enabled
        def emit(self, kind, value):
            world.events.append((kind, copy.deepcopy(value)))
    monkeypatch.setattr(a, "PublicEvidence", Evidence)
    monkeypatch.setattr(a, "require_clean_root", lambda: None)
    monkeypatch.setattr(a.pwd, "getpwuid", lambda _: SimpleNamespace(pw_gid=1001, pw_name="runner"))
    monkeypatch.setattr(a, "read_regular", lambda *_args, **_kwargs: a.canonical(event))
    monkeypatch.setattr(a, "host_prerequisites", lambda: copy.deepcopy(host))
    def directory(path, **_):
        if world.fail == "root_directory":
            raise a.SetupError("unowned stage")
    monkeypatch.setattr(a, "root_directory", directory)
    def write(path, raw):
        if str(path) in world.files:
            raise FileExistsError(str(path))
        world.files[str(path)] = raw
        return {"sha256": checksum(raw), "bytes": len(raw)}
    def read(path, **_):
        key = str(path)
        world.reads[key] = world.reads.get(key, 0) + 1
        raw = world.files[key]
        if world.fail == "compiler_preload" and path.name == "compile.stdout" and world.reads[key] > 1:
            raw += b"changed"
        return raw
    monkeypatch.setattr(a, "sealed_write", write)
    monkeypatch.setattr(a, "sealed_read", read)
    def live(*_):
        world.live_count += 1
        world.stages.append("live")
        if world.fail == "live_initial" or world.fail == "live_preload" and world.live_count == 2:
            raise a.SetupError("live identity changed")
        return {"checked": a.stamp()}
    monkeypatch.setattr(a, "live_identity", live)
    def install(*_):
        world.stages.append("install")
        if world.fail == "install":
            raise a.SetupError("package failed")
    monkeypatch.setattr(a, "install_vendor", install)
    def probe(_stage, _uid, _gid, _evidence, *, negative=False, snapshot_only=False):
        name = "snapshot" if snapshot_only else "negative" if negative else "positive"
        world.stages.append(name)
        if world.fail == name:
            raise a.SetupError("invalid " + name)
        return {"returncode": 1 if negative else 0}
    monkeypatch.setattr(a, "runner_probe", probe)
    def observe(*_):
        index = world.observations
        world.observations += 1
        name = "before" if index == 0 else "recheck" if index == 1 else "after"
        world.stages.append(name)
        if world.fail == name:
            raise a.SetupError("missing " + name)
        value = {"host": copy.deepcopy(host), "vendor": {}, "features": {}, "includes": {}, "paths": {},
                 "kernel": kernel_record(copy.deepcopy(before if index < 2 else after))}
        if world.fail == "changed_preload" and index == 1 or world.fail == "changed_after" and index >= 2:
            value["features"]["changed"] = True
        if world.fail == "invalid_after" and index >= 2:
            value["kernel"]["semantic"]["profiles"][0]["mode"] = "complain"
        return value
    monkeypatch.setattr(a, "observe_state", observe)
    def parser(argv, *_args, **_kwargs):
        operation = "preprocess" if "--preprocess" in argv else "compile" if "--stdout" in argv else "load"
        world.stages.append(operation)
        if world.fail == "cancel_" + operation:
            a.signal.getsignal(a.signal.SIGTERM)(a.signal.SIGTERM, None)
        output = b"preprocessed" if operation == "preprocess" else SYNTHETIC_COMPILED if operation == "compile" else b""
        warnings = b"" if operation == "preprocess" else io_uring_warnings(argv[-1])
        return command(argv, output, warnings, returncode=1 if world.fail == operation else 0)
    monkeypatch.setattr(a, "command", parser)
    return world


def test_bootstrap_orders_all_security_work_before_pass_and_seals_once(bootstrap_world):
    world = bootstrap_world
    result = a.bootstrap(world.config, world.source)
    assert result["status"] == "PASS" and result["load_attempted"] is True and result["positive_passed"] is True
    assert world.stages == ["live", "snapshot", "install", "before", "negative", "preprocess", "compile", "live", "recheck", "load", "after", "positive"]
    stage = a.stage_path(result["binding"])
    assert json.loads(world.files[str(stage / "setup.json")]) == result
    assert world.stages.count("load") == 1
    with pytest.raises(FileExistsError):
        a.bootstrap(world.config, world.source)
    assert world.stages.count("load") == 1


@pytest.mark.parametrize("failure", ["root_directory", "live_initial", "snapshot", "install", "before", "negative", "preprocess", "compile", "live_preload", "recheck", "changed_preload", "compiler_preload", "cancel_compile"])
def test_every_early_failure_has_zero_add_and_no_seal(bootstrap_world, failure):
    world = bootstrap_world
    world.fail = failure
    with pytest.raises(a.SetupError):
        a.bootstrap(world.config, world.source)
    assert "load" not in world.stages and "positive" not in world.stages
    assert not any(name.endswith("/setup.json") for name in world.files)
    assert world.events[-1][0] == "setup-stop" and world.events[-1][1]["load_attempted"] is False


@pytest.mark.parametrize("failure", ["load", "after", "changed_after", "invalid_after", "positive", "cancel_load"])
def test_failed_or_uncertain_add_never_seals_or_qualifies(bootstrap_world, failure):
    world = bootstrap_world
    world.fail = failure
    with pytest.raises(a.SetupError):
        a.bootstrap(world.config, world.source)
    assert world.stages.count("load") == 1
    assert not any(name.endswith("/setup.json") for name in world.files)
    assert world.events[-1][0] == "setup-stop" and world.events[-1][1]["load_attempted"] is True
    if failure != "positive":
        assert "positive" not in world.stages


@pytest.mark.parametrize("change", [None, "compiled", "observer", "boot", "inputs", "policy", "live", "seal"])
def test_read_only_observer_rechecks_sealed_whole_policy_and_binding(bootstrap_world, monkeypatch, change):
    world = bootstrap_world
    seal = a.bootstrap(world.config, world.source)
    stage = a.stage_path(seal["binding"])
    if change in {"compiled", "observer"}:
        name = "compile.stdout" if change == "compiled" else "observer.py"
        world.files[str(stage / name)] += b"changed"
    elif change == "boot":
        world.host["boot_id"] = "22222222-2222-2222-2222-222222222222"
    elif change == "inputs":
        world.fail = "changed_after"
    elif change == "policy":
        world.fail = "invalid_after"
    elif change == "live":
        world.fail = "live_initial"
    elif change == "seal":
        seal["positive_passed"] = False
        world.files[str(stage / "setup.json")] = a.canonical(seal)
    previous = world.stages.count("load")
    if change is None:
        assert a.observe_sealed(stage, world.config)["status"] == "PASS"
    else:
        with pytest.raises(a.SetupError):
            a.observe_sealed(stage, world.config)
    assert world.stages.count("load") == previous


def test_termination_guard_raises_and_restores_both_signal_handlers():
    previous = {x: a.signal.getsignal(x) for x in (a.signal.SIGINT, a.signal.SIGTERM)}
    with pytest.raises(a.SetupError, match="cancelled"):
        with a.cancellation_guard():
            a.signal.getsignal(a.signal.SIGTERM)(a.signal.SIGTERM, None)
    assert {x: a.signal.getsignal(x) for x in previous} == previous


@pytest.mark.parametrize("failure", ["timeout", "overflow", "cancel"])
def test_bounded_command_cleans_up_owned_child_on_every_interruption(monkeypatch, failure):
    calls = []
    output = SimpleNamespace(fileno=lambda: 10)
    error = SimpleNamespace(fileno=lambda: 11)
    class Process:
        pid = 777
        returncode = None
        stdout, stderr = output, error
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def wait(self, **_):
            if failure == "cancel" and "cleanup" not in calls:
                raise KeyboardInterrupt
            self.returncode = -9 if "cleanup" in calls else 0
            return self.returncode
    process = Process()
    class Selector:
        def __init__(self): self.values = {}
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def register(self, pipe, _events, target):
            self.values[pipe.fileno()] = SimpleNamespace(fd=pipe.fileno(), fileobj=pipe, data=target)
        def unregister(self, pipe): self.values.pop(pipe.fileno())
        def get_map(self): return self.values
        def select(self, *_): return [(x, None) for x in list(self.values.values())]
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(a.selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(a.os, "set_blocking", lambda *_: None)
    monkeypatch.setattr(a.os, "read", lambda fd, size: b"too much" if failure == "overflow" and fd == 10 else b"")
    ticks = iter([0, 100])
    if failure == "timeout":
        monkeypatch.setattr(a.time, "monotonic", lambda: next(ticks, 100))
    monkeypatch.setattr(a, "kill_owned_command", lambda p: calls.append("cleanup"))
    result = a.command(["/fixed"], 1, limit=3)
    assert result["error"] and calls == ["cleanup"] and result["returncode"] == -9


ACTUAL_INCLUDE_FIXTURE = (
    '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'
    'PDn+//wvOHjq+g=='
)


@pytest.mark.parametrize("change", [None, "owner", "group", "mode", "type", "link", "ancestor"])
def test_root_staging_requires_private_immutable_root_path_chain(monkeypatch, change):
    path = Path("/var/lib/forge-qualification/nonce/run")
    monkeypatch.setattr(Path, "resolve", lambda self, **_: Path("/elsewhere") if change == "link" and self == path else self)
    def metadata(self):
        mode, uid, gid = stat.S_IFDIR | 0o700, 0, 0
        if self == path:
            if change == "owner":
                uid = 1001
            if change == "group":
                gid = 1001
            if change == "mode":
                mode = stat.S_IFDIR | 0o755
            if change == "type":
                mode = stat.S_IFREG | 0o600
        if change == "ancestor" and self == path.parent:
            mode = stat.S_IFDIR | 0o777
        return SimpleNamespace(st_mode=mode, st_uid=uid, st_gid=gid)
    monkeypatch.setattr(Path, "lstat", metadata)
    if change is None:
        a.root_directory(path)
    else:
        with pytest.raises(a.SetupError):
            a.root_directory(path)


@pytest.mark.parametrize("change", [None, "owner", "group", "mode", "hardlink", "symlink"])
def test_sealed_files_require_root_readonly_regular_single_link(monkeypatch, change):
    path = Path("/fixed-root/setup.json")
    monkeypatch.setattr(a, "root_directory", lambda *_: None)
    monkeypatch.setattr(Path, "lstat", lambda _: SimpleNamespace(
        st_mode=(stat.S_IFLNK | 0o400) if change == "symlink" else stat.S_IFREG | (0o600 if change == "mode" else 0o400),
        st_uid=1001 if change == "owner" else 0, st_gid=1001 if change == "group" else 0,
        st_nlink=2 if change == "hardlink" else 1))
    reads = []
    monkeypatch.setattr(a, "read_regular", lambda *args, **kwargs: reads.append(args) or b"sealed")
    if change is None:
        assert a.sealed_read(path) == b"sealed"
    else:
        with pytest.raises(a.SetupError):
            a.sealed_read(path)
        assert reads == []


@pytest.mark.parametrize("change", [None, "bytes", "metadata", "membership", "symlink", "optional", "disable_alias", "missing", "unreadable"])
def test_actual_19_entry_include_closure_keeps_bytes_metadata_membership_and_absences(tmp_path, monkeypatch, change):
    import base64
    import zlib
    bodies = json.loads(zlib.decompress(base64.b64decode(ACTUAL_INCLUDE_FIXTURE, validate=True)))
    root = tmp_path / "apparmor.d"
    root.mkdir()
    for relative, encoded in bodies.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(encoded, validate=True))
        target.chmod(0o644)
    monkeypatch.setattr(a, "PROFILE_ROOT", str(root))
    original = Path.lstat
    def metadata(path):
        info = original(path)
        return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_gid=0, st_size=info.st_size)
    monkeypatch.setattr(Path, "lstat", metadata)
    target = root / "abi/4.0"
    if change == "bytes":
        target.write_bytes(target.read_bytes() + b"changed")
    elif change == "metadata":
        target.chmod(0o660)
    elif change == "membership":
        (root / "tunables/home.d/unreviewed").touch()
    elif change == "symlink":
        target.unlink()
        target.symlink_to(root / "tunables/global")
    elif change == "optional":
        (root / "local").mkdir()
        (root / "local/unpriv_bwrap").symlink_to(root / "absent")
    elif change == "disable_alias":
        (root / "disable").mkdir()
        (root / "disable/alias").symlink_to("unpriv_bwrap")
    elif change == "missing":
        target.unlink()
    class Reader:
        def read(self, path, **_):
            if change == "unreadable" and path == str(target):
                raise PermissionError("unreadable policy input")
            return Path(path).read_bytes()
    if change is None:
        assert a.digest(a.observe_includes(Reader())) == a.INCLUDE_SHA256
    else:
        with pytest.raises((a.SetupError, OSError)):
            a.observe_includes(Reader())


@pytest.mark.parametrize("requested", ["/sbin/ip", "/usr/sbin/ip"])
@pytest.mark.parametrize("change", [None, "guessed_relative", "other_target", "wrong_endpoint", "wrong_owner", "writable", "setid", "capabilities"])
def test_packaged_ip_alias_has_exact_known_target_and_certified_endpoint(monkeypatch, requested, change):
    """Ubuntu6.1.0-1ubuntu6.4's sbin/ip symlink is absolute /bin/ip."""
    import io

    expected_target = "/bin/ip"
    assert a.SYSTEM_LINKS[requested] == expected_target
    endpoint = "/usr/bin/other" if change == "wrong_endpoint" else "/usr/bin/ip"
    monkeypatch.setattr(Path, "resolve", lambda self, **_: Path(endpoint))
    links = {requested: expected_target}
    if requested.startswith("/sbin/"):
        links["/sbin"] = "usr/sbin"
    if change == "guessed_relative":
        links[requested] = "../bin/ip"
    elif change == "other_target":
        links[requested] = "/unreviewed/ip"
    monkeypatch.setattr(Path, "is_symlink", lambda self: str(self) in links)
    monkeypatch.setattr(a.os, "readlink", lambda path: links[str(path)])
    mode = 0o777 if change == "writable" else 0o4755 if change == "setid" else 0o755
    monkeypatch.setattr(Path, "stat", lambda self, **_: SimpleNamespace(
        st_mode=stat.S_IFREG | mode, st_uid=1001 if change == "wrong_owner" else 0, st_gid=0))
    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: io.BytesIO(b"\x7fELFfixed-provider-binary"))
    def capabilities(*_):
        if change == "capabilities":
            return b"file-capability"
        raise OSError(errno.ENODATA, "no capabilities")
    monkeypatch.setattr(a.os, "getxattr", capabilities)
    if change is None:
        result = a.ordinary_executable(requested)
        assert result["canonical"] == "/usr/bin/ip"
        assert result["symlinks"][0] == {"path": requested, "target": "/bin/ip"}
    else:
        with pytest.raises(a.SetupError) as caught:
            a.ordinary_executable(requested)
        if change in {"guessed_relative", "other_target"}:
            assert "component=" + repr(requested) in str(caught.value)
            assert "expected='/bin/ip'" in str(caught.value)
            assert "observed=" + repr(links[requested]) in str(caught.value)
