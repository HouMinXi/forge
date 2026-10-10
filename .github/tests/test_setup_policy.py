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
import threading
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from forge_ci import setup_policy as a  # noqa: E402
facts = probes = c = a
ORDINARY_POPEN = subprocess.Popen

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


def audit_clock(*, monotonic_before=90, monotonic_after=210):
    return {"clock_id": 5, "clock_name": "CLOCK_REALTIME_COARSE", "resolution_ns": 1_000_000,
            "before_ns": 1700000000123000000, "after_ns": 1700000000124000000,
            "monotonic_before_ns": monotonic_before, "monotonic_after_ns": monotonic_after}


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
                audit_clock=audit_clock(),
                started=negative()["started"],
                ended=negative()["ended"],
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
    return copy.deepcopy(a.CONFIG)


def native_fixture(image="20261004.327.1"):
    cfg = config_fixture()
    context = {"GITHUB_EVENT_NAME": "push", "GITHUB_REF_TYPE": "branch", "GITHUB_REF": cfg["full_ref"],
               "GITHUB_REPOSITORY": "HouMinXi/forge", "GITHUB_REPOSITORY_OWNER": "HouMinXi", "GITHUB_REPOSITORY_ID": "1258832822",
               "GITHUB_REPOSITORY_OWNER_ID": "19586012", "GITHUB_ACTOR": "HouMinXi", "GITHUB_ACTOR_ID": "19586012",
               "GITHUB_TRIGGERING_ACTOR": "HouMinXi", "GITHUB_WORKFLOW_REF": "HouMinXi/forge/" + cfg["workflow_path"] + "@" + cfg["full_ref"],
               "GITHUB_WORKFLOW_SHA": "c" * 40, "GITHUB_SHA": "c" * 40, "GITHUB_RUN_NUMBER": "42", "GITHUB_RUN_ID": "123",
               "GITHUB_RUN_ATTEMPT": "1", "GITHUB_JOB": "linux-tests", "GITHUB_SERVER_URL": "https://github.com",
               "GITHUB_API_URL": "https://api.github.com", "GITHUB_EVENT_PATH": "/native-event.json", "RUNNER_OS": "Linux",
               "RUNNER_ARCH": "X64", "RUNNER_ENVIRONMENT": "github-hosted", "ImageOS": "ubuntu24", "ImageVersion": image,
               "FORGE_RUNNER_UID": "1001", "FORGE_RUNNER_GID": "1001"}
    event = {"created": False, "deleted": False, "forced": False, "ref": cfg["full_ref"], "before": "a" * 40,
             "after": context["GITHUB_SHA"], "head_commit": {"id": context["GITHUB_SHA"]},
             "repository": copy.deepcopy(a.REPOSITORY), "sender": copy.deepcopy(a.REPOSITORY["owner"])}
    return cfg, context, event


def binding_fixture():
    cfg, context, event = native_fixture()
    return {**cfg, "before_sha": event["before"], "candidate_sha": context["GITHUB_SHA"], "workflow_sha": context["GITHUB_SHA"],
            "run_id": 123, "run_number": 42, "run_attempt": 1, "job_key": "linux-tests",
            "boot_id": "11111111-1111-1111-1111-111111111111", "workflow_id": 987, "job_id": 456,
            "job_started_at": "2026-10-08T16:00:00Z", "job_started_ns": a.timestamp_ns("2026-10-08T16:00:00Z")}


@pytest.mark.parametrize("image", ["20261004.327.1", "20260927.320.1"])
def test_two_hosted_image_versions_are_evidence_not_admission_identity(image):
    cfg, context, event = native_fixture(image)
    binding = a.validate_initial_identity(cfg, context, event)
    assert binding["candidate_sha"] == "c" * 40 and binding["run_number"] == 42 and binding["run_attempt"] == 1
    assert set(binding) == a.NATIVE_BINDING_KEYS


@pytest.mark.parametrize("field", ["GITHUB_ACTOR_ID", "GITHUB_TRIGGERING_ACTOR", "GITHUB_REF", "GITHUB_WORKFLOW_SHA", "GITHUB_RUN_NUMBER",
                                   "GITHUB_REPOSITORY_ID", "GITHUB_REPOSITORY_OWNER_ID", "GITHUB_EVENT_NAME", "RUNNER_ENVIRONMENT"])
def test_native_identity_drift_stops_before_any_candidate_action(field):
    cfg, context, event = native_fixture()
    context[field] = "unreviewed"
    with pytest.raises(a.SetupError):
        a.validate_initial_identity(cfg, context, event)


@pytest.mark.parametrize("field,value", [("forced", True), ("created", True), ("deleted", True), ("before", "0" * 40),
                                        ("before", "c" * 40), ("after", "d" * 40)])
def test_invalid_nonforce_push_stops(field, value):
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
    assert not names & {"bootstrap", "command", "checked_command", "install_vendor", "download_archive", "runner_probe", "parser_argv", "kill_owned_command", "claim_activation", "sealed_write",
                        "stage_metadata_credential", "cleanup_metadata_credential", "_read_credential_ingress", "_credential_inventory", "_credential_parts", "reserve_bootstrap_stdin"}
    assert "_read_metadata_credential" in names
    assert b"--add" not in one and b"--install" not in one and b"subprocess.Popen(" not in one
    assert b"forge_ci" not in one and b"code_forge" not in one
    assert source.split("def observe_state(", 1)[1].split("\ndef stable_state", 1)[0].encode() in one


def test_observer_cli_rejects_arbitrary_modes_before_observation(monkeypatch):
    monkeypatch.setattr(a.sys, "argv", ["observer.py", "setup"])
    monkeypatch.setattr(a, "observe_sealed", lambda *_: pytest.fail("observer attempted setup"))
    with pytest.raises(a.SetupError):
        a.observer_main(config_fixture())


def test_fixed_observer_argv_is_privileged_timeout_isolated_system_python():
    binding = binding_fixture()
    argv = a.observer_argv(binding)
    assert argv[:6] == ["/usr/bin/sudo", "-n", "--", "/usr/bin/timeout", "--signal=KILL", "25s"]
    assert argv[-6:] == ["/usr/bin/python3", "-B", "-I", "-S", "/var/lib/forge-qualification/123/1/observer.py", "observe"]
    assert "-i" in argv and "HOME=/nonexistent" in argv


def test_native_observer_argv_requires_exact_native_binding_and_fixed_path():
    binding = binding_fixture()
    native = {key: binding[key] for key in a.NATIVE_BINDING_KEYS}
    assert a.observer_argv_native(native) == a.observer_argv(binding)
    for changed in (binding, dict(native, run_id="123"), dict(native, run_attempt=2), dict(native, run_id="../evil")):
        with pytest.raises(a.SetupError):
            a.observer_argv_native(changed)
    with pytest.raises(a.SetupError):
        a.observer_argv(native)


FAKE_CREDENTIAL = b"offline_FAKE_only-Credential-Sentinel_921"


def credential_pipe(raw, *, eof=True):
    reader, writer = os.pipe()
    assert os.write(writer, raw) == len(raw)
    if eof:
        os.close(writer)
    return reader, writer


@pytest.mark.parametrize("raw", [b"a", FAKE_CREDENTIAL, b"x" * 4096])
def test_credential_ingress_accepts_only_complete_bounded_eof_and_closes_reader(raw):
    reader, _ = credential_pipe(raw)
    assert a._read_credential_ingress(reader) == raw
    with pytest.raises(OSError):
        os.fstat(reader)


@pytest.mark.parametrize("raw", [b"", b"x" * 4097, b"a\nb", b"a\rb", b"a\x00b", b"a b", b"\t", b"\x7f", b"\x80"])
def test_credential_ingress_rejects_invalid_input_without_echo(raw, capsys):
    reader, _ = credential_pipe(raw)
    with pytest.raises(a.SetupError) as error:
        a._read_credential_ingress(reader)
    assert "metadata credential" in str(error.value) or "byte bound" in str(error.value)
    assert capsys.readouterr() == ("", "")
    with pytest.raises(OSError):
        os.fstat(reader)


@pytest.mark.parametrize("initial", [b"", FAKE_CREDENTIAL])
def test_credential_ingress_deadline_includes_empty_or_nonempty_without_eof(monkeypatch, initial):
    monkeypatch.setattr(a, "CREDENTIAL_INGRESS_SECONDS", 0.025)
    reader, writer = credential_pipe(initial, eof=False)
    started = time.monotonic()
    try:
        with pytest.raises(a.SetupError, match="deadline"):
            a._read_credential_ingress(reader)
        assert time.monotonic() - started < 1
        with pytest.raises(BrokenPipeError):
            os.write(writer, b"x")
    finally:
        os.close(writer)


def test_credential_ingress_trickling_never_restarts_deadline(monkeypatch):
    monkeypatch.setattr(a, "CREDENTIAL_INGRESS_SECONDS", 0.04)
    reader, writer = os.pipe()
    stopped = threading.Event()
    closed_reader = []
    def trickle():
        try:
            while not stopped.wait(0.003):
                try:
                    os.write(writer, b"x")
                except BrokenPipeError:
                    closed_reader.append(True)
                    return
        finally:
            os.close(writer)
    thread = threading.Thread(target=trickle)
    thread.start()
    started = time.monotonic()
    try:
        with pytest.raises(a.SetupError, match="deadline"):
            a._read_credential_ingress(reader)
        thread.join(timeout=1)
        assert closed_reader == [True] and not thread.is_alive()
        assert time.monotonic() - started < 1
    finally:
        stopped.set()
        thread.join(timeout=1)


def test_credential_ingress_overflow_does_not_wait_for_eof(monkeypatch):
    reader, writer = credential_pipe(b"x" * 4097, eof=False)
    try:
        with pytest.raises(a.SetupError, match="byte bound"):
            a._read_credential_ingress(reader)
        with pytest.raises(BrokenPipeError):
            os.write(writer, b"x")
    finally:
        os.close(writer)


def test_credential_ingress_cancellation_closes_selector_and_pipe(monkeypatch):
    reader, writer = os.pipe()
    closed = []
    class Selector:
        def register(self, *_):
            pass
        def select(self, *_):
            raise KeyboardInterrupt()
        def close(self):
            closed.append(True)
    monkeypatch.setattr(a.selectors, "DefaultSelector", Selector)
    try:
        with pytest.raises(KeyboardInterrupt):
            a._read_credential_ingress(reader)
        assert closed == [True]
        with pytest.raises(BrokenPipeError):
            os.write(writer, b"x")
    finally:
        os.close(writer)


@pytest.mark.parametrize("fault", [None, "root", "occupied", "vacancy_error", "missing", "symlink", "open_error",
                                  "unexpected_fd", "regular", "directory", "wrong_major", "wrong_minor",
                                  "fstat", "inheritable", "inheritance_error", "close_error"])
def test_bootstrap_stdin_reservation_is_fixed_inert_and_owns_only_new_fd(monkeypatch, fault):
    events = []
    def root():
        events.append("root")
        if fault == "root":
            raise a.SetupError("root context unavailable")
    def fstat(fd):
        events.append(("fstat", fd))
        if events.count(("fstat", fd)) == 1:
            if fault == "occupied":
                return SimpleNamespace(st_mode=stat.S_IFIFO)
            raise OSError(errno.EIO if fault == "vacancy_error" else errno.EBADF, "private diagnostic")
        if fault == "fstat":
            raise OSError(errno.EIO, "private diagnostic")
        mode = stat.S_IFREG if fault == "regular" else stat.S_IFDIR if fault == "directory" else stat.S_IFCHR
        device = os.makedev(2 if fault == "wrong_major" else 1, 5 if fault == "wrong_minor" else 3)
        return SimpleNamespace(st_mode=mode, st_rdev=device)
    def opening(path, flags):
        events.append(("open", path, flags))
        assert path == "/dev/null" and flags == os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        if fault in {"missing", "symlink", "open_error"}:
            raise OSError({"missing": errno.ENOENT, "symlink": errno.ELOOP, "open_error": errno.EACCES}[fault], "private diagnostic")
        return 7 if fault == "unexpected_fd" else 0
    def inheritable(fd):
        events.append(("get_inheritable", fd))
        if fault == "inheritance_error":
            raise OSError(errno.EIO, "private diagnostic")
        return fault in {"inheritable", "close_error"}
    def close(fd):
        events.append(("close", fd))
        if fault == "close_error":
            raise OSError(errno.EIO, "private diagnostic")
    monkeypatch.setattr(a, "require_clean_root", root)
    monkeypatch.setattr(a.os, "fstat", fstat)
    monkeypatch.setattr(a.os, "open", opening)
    monkeypatch.setattr(a.os, "get_inheritable", inheritable)
    monkeypatch.setattr(a.os, "close", close)
    if fault is None:
        assert a.reserve_bootstrap_stdin() is None
    else:
        with pytest.raises(a.SetupError) as caught:
            a.reserve_bootstrap_stdin()
        assert "private diagnostic" not in str(caught.value)
    assert events[0] == "root"
    closed = [item[1] for item in events if isinstance(item, tuple) and item[0] == "close"]
    if fault in {None, "root", "occupied", "vacancy_error", "missing", "symlink", "open_error"}:
        assert closed == []
    else:
        assert closed == [7 if fault == "unexpected_fd" else 0]
    if fault in {"root", "occupied", "vacancy_error"}:
        assert not any(isinstance(item, tuple) and item[0] == "open" for item in events)


def ordinary_setup_child(script, *args):
    """Only a fresh ordinary interpreter may disturb its own real fd0."""
    prefix = "import sys\nsys.path.insert(0, sys.argv[1])\nfrom forge_ci import setup_policy as a\n"
    argv = [sys.executable, "-B", "-I", "-S", "-c", prefix + script,
            str(Path(a.__file__).resolve().parent.parent), *args]
    with ORDINARY_POPEN(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        try:
            stdout, stderr = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
            raise
        assert process.returncode == 0, stderr.decode(errors="replace")
    assert stderr == b""
    return json.loads(stdout)


@pytest.mark.parametrize("reserve", [False, True])
def test_real_ingress_then_snapshot_capture_requires_reserved_stdin(reserve):
    result = ordinary_setup_child(r'''
import errno, fcntl, json, os, stat, tempfile
assert os.getuid() == os.geteuid() > 0 and os.getgid() == os.getegid() > 0
a.require_clean_root = lambda: None
os.close(0)
reader, writer = os.pipe()
assert reader == 0
assert os.write(writer, b'offline-fake-credential') == 23
os.close(writer)
assert a._read_credential_ingress(reader) == b'offline-fake-credential'
try:
    os.fstat(0)
except OSError as error:
    assert error.errno == errno.EBADF
else:
    raise AssertionError('ingress retained fd0')
reserve = sys.argv[2] == 'yes'
if reserve:
    a.reserve_bootstrap_stdin()
    info = os.fstat(0)
    assert stat.S_ISCHR(info.st_mode) and (os.major(info.st_rdev), os.minor(info.st_rdev)) == (1, 3)
    assert not os.get_inheritable(0) and os.read(0, 1) == b''
    assert fcntl.fcntl(0, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY
with tempfile.TemporaryFile(mode='w+b') as caller, tempfile.TemporaryFile(mode='w+b') as info:
    child = "import json,os,sys;fd=int(sys.argv[1]);os.write(fd,json.dumps(dict(uid=os.getuid(),gid=os.getgid(),pid=os.getpid(),marker='harmless')).encode());os.close(fd)"
    record = a.command([sys.executable, '-B', '-I', '-S', '-c', child, str(caller.fileno())],
                       5, pass_fds=(caller.fileno(), info.fileno()))
    assert record['returncode'] == 0 and not record.get('error') and record['stderr'] == ''
    caller.seek(0)
    raw = caller.read(65537)
    if reserve:
        observed = a.parse_json(raw, limit=65536)
        assert observed == dict(uid=os.getuid(), gid=os.getgid(), pid=record['wrapper_pid'], marker='harmless')
        assert caller.fileno() >= 3 and info.fileno() >= 3
    else:
        assert caller.fileno() == 0 and raw == b''
        try:
            a.parse_json(raw, limit=65536)
        except a.SetupError:
            pass
        else:
            raise AssertionError('empty caller report admitted')
    print(json.dumps(dict(reserved=reserve, caller_fd=caller.fileno(), capture_bytes=len(raw),
                          returncode=record['returncode'], uid=os.getuid(), gid=os.getgid())))
''', "yes" if reserve else "no")
    assert result["reserved"] is reserve and result["returncode"] == 0
    assert result["uid"] > 0 and result["gid"] > 0
    assert result["capture_bytes"] > 0 if reserve else result["capture_bytes"] == 0
    assert result["caller_fd"] >= 3 if reserve else result["caller_fd"] == 0


def test_real_staging_disposes_ingress_before_reservation_and_snapshot_capture():
    native = {key: value for key, value in binding_fixture().items() if key in a.NATIVE_BINDING_KEYS}
    result = ordinary_setup_child(r'''
import errno, hashlib, json, os, stat, tempfile
from pathlib import Path
from types import SimpleNamespace
assert os.getuid() == os.geteuid() > 0 and os.getgid() == os.getegid() > 0
native = json.loads(sys.argv[2])
a.require_clean_root = lambda: None
a.trusted_boot_id = lambda: native['boot_id']
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary) / 'bootstrap'
    root.mkdir(mode=0o700)
    stage = root / '123-1'
    stage.mkdir(mode=0o700)
    a.BOOTSTRAP_ROOT = root
    part = b'fixed offline source'
    (stage / 'part-00.txt').write_bytes(part)
    (stage / 'part-00.txt').chmod(0o400)
    (stage / 'bootstrap-entry.py').write_bytes(b'# fixed offline entrypoint\n')
    (stage / 'bootstrap-entry.py').chmod(0o400)
    layout = dict(parts=[dict(name='part-00.txt', bytes=len(part), sha256=hashlib.sha256(part).hexdigest())])
    real_stat, real_fstat = os.stat, os.fstat
    ancestors = {path.stat().st_ino for path in root.parents}
    def root_metadata(info):
        fields = {name: getattr(info, name) for name in dir(info) if name.startswith('st_')}
        fields.update(st_uid=0, st_gid=0)
        if info.st_ino in ancestors:
            fields['st_mode'] &= ~0o022
        return SimpleNamespace(**fields)
    os.stat = lambda *args, **kwargs: root_metadata(real_stat(*args, **kwargs))
    os.fstat = lambda fd: root_metadata(real_fstat(fd))
    os.close(0)
    reader, writer = os.pipe()
    assert reader == 0
    fake = b'offline-fake-credential'
    assert os.write(writer, fake) == len(fake)
    os.close(writer)
    a.stage_metadata_credential(native, layout)
    try:
        os.fstat(0)
    except OSError as error:
        assert error.errno == errno.EBADF
    else:
        raise AssertionError('staging retained ingress')
    assert (stage / 'metadata-token').read_bytes() == fake
    assert stat.S_IMODE((stage / 'metadata-token').stat().st_mode) == 0o400
    a.reserve_bootstrap_stdin()
    null = real_fstat(0)
    assert stat.S_ISCHR(null.st_mode) and (os.major(null.st_rdev), os.minor(null.st_rdev)) == (1, 3)
    assert not os.get_inheritable(0) and os.read(0, 1) == b''
    with tempfile.TemporaryFile(mode='w+b', dir=stage) as caller, tempfile.TemporaryFile(mode='w+b', dir=stage) as info:
        assert caller.fileno() >= 3 and info.fileno() >= 3
        child = "import json,os,sys;os.write(int(sys.argv[1]),json.dumps(dict(uid=os.getuid(),gid=os.getgid(),pid=os.getpid())).encode())"
        record = a.command([sys.executable, '-B', '-I', '-S', '-c', child, str(caller.fileno())],
                           5, pass_fds=(caller.fileno(), info.fileno()))
        assert record['returncode'] == 0 and not record.get('error') and record['stderr'] == ''
        caller.seek(0)
        raw = caller.read(65537)
        observed = a.parse_json(raw, limit=65536)
        assert observed == dict(uid=os.getuid(), gid=os.getgid(), pid=record['wrapper_pid'])
    os.stat, os.fstat = real_stat, real_fstat
    print(json.dumps(dict(staged=True, ingress_closed=True, stdin_reserved=True, capture_bytes=len(raw))))
''', json.dumps(native))
    assert result["staged"] and result["ingress_closed"] and result["stdin_reserved"]
    assert 0 < result["capture_bytes"] <= 65536


@pytest.mark.parametrize("failure", ["overflow", "deadline", "cancel"])
def test_real_ingress_failure_disposes_pipe_and_settles_writer_before_fd_reuse(failure):
    result = ordinary_setup_child(r'''
import errno, json, os, signal, stat
assert os.getuid() == os.geteuid() > 0 and os.getgid() == os.getegid() > 0
os.close(0)
reader, writer = os.pipe()
assert reader == 0 and a.CREDENTIAL_INGRESS_SECONDS == 5
failure = sys.argv[2]
os.write(writer, b'x' * (4097 if failure == 'overflow' else 23))
def cancelled(*_):
    raise KeyboardInterrupt()
if failure == 'cancel':
    signal.signal(signal.SIGALRM, cancelled)
    signal.setitimer(signal.ITIMER_REAL, 0.025)
try:
    a._read_credential_ingress(0)
except (a.SetupError, KeyboardInterrupt) as error:
    assert isinstance(error, KeyboardInterrupt) if failure == 'cancel' else isinstance(error, a.SetupError)
else:
    raise AssertionError('failed ingress admitted')
finally:
    signal.setitimer(signal.ITIMER_REAL, 0)
try:
    os.fstat(0)
except OSError as error:
    assert error.errno == errno.EBADF
else:
    raise AssertionError('failed ingress retained fd0')
try:
    os.write(writer, b'x')
except BrokenPipeError:
    pass
else:
    raise AssertionError('writer still has an ingress reader')
os.close(writer)
replacement = os.open('/dev/null', os.O_RDONLY | os.O_CLOEXEC)
assert replacement == 0 and stat.S_ISCHR(os.fstat(replacement).st_mode)
os.close(replacement)
print(json.dumps(dict(failure=failure, writer_settled=True, ingress_closed=True)))
''', failure)
    assert result == {"failure": failure, "writer_settled": True, "ingress_closed": True}


def test_real_stdin_reservation_never_overwrites_an_occupied_pipe():
    result = ordinary_setup_child(r'''
import json, os, stat
assert os.getuid() == os.geteuid() > 0 and os.getgid() == os.getegid() > 0
a.require_clean_root = lambda: None
os.close(0)
reader, writer = os.pipe()
assert reader == 0
before = os.fstat(0)
try:
    a.reserve_bootstrap_stdin()
except a.SetupError:
    pass
else:
    raise AssertionError('occupied stdin overwritten')
after = os.fstat(0)
assert stat.S_ISFIFO(after.st_mode) and (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
assert os.write(writer, b'owned-pipe') == 10 and os.read(0, 10) == b'owned-pipe'
os.close(reader)
os.close(writer)
print(json.dumps(dict(occupied_pipe_preserved=True)))
''')
    assert result == {"occupied_pipe_preserved": True}


@pytest.fixture
def credential_world(tmp_path, monkeypatch):
    """Real temporary descriptors; root ownership is modeled, never acquired."""
    root = tmp_path / "bootstrap"
    root.mkdir(mode=0o700)
    stage = root / "123-1"
    stage.mkdir(mode=0o700)
    monkeypatch.setattr(a, "BOOTSTRAP_ROOT", root)
    faults = {}
    ancestor_inodes = {p.stat().st_ino for p in root.parents}
    real_stat, real_fstat = os.stat, os.fstat
    def modeled(info):
        values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
        values.update(st_uid=0, st_gid=0)
        if info.st_ino in ancestor_inodes:
            values["st_mode"] &= ~0o022
        values.update(faults.get(info.st_ino, {}))
        return SimpleNamespace(**values)
    monkeypatch.setattr(a.os, "stat", lambda *args, **kwargs: modeled(real_stat(*args, **kwargs)))
    monkeypatch.setattr(a.os, "fstat", lambda fd: modeled(real_fstat(fd)))
    for name in ("getuid", "geteuid", "getgid", "getegid"):
        monkeypatch.setattr(a.os, name, lambda: 0)
    monkeypatch.setattr(a, "require_clean_root", lambda: None)
    binding = binding_fixture()
    native = {key: binding[key] for key in a.NATIVE_BINDING_KEYS}
    monkeypatch.setattr(a, "trusted_boot_id", lambda: binding["boot_id"])
    parts = []
    for index, raw in enumerate((b"fixed source first part", b"fixed source second part")):
        name = f"part-{index:02d}.txt"
        (stage / name).write_bytes(raw)
        (stage / name).chmod(0o400)
        parts.append({"name": name, "bytes": len(raw), "sha256": checksum(raw)})
    (stage / "bootstrap-entry.py").write_bytes(b"# fixed literal entrypoint\n")
    (stage / "bootstrap-entry.py").chmod(0o400)
    world = SimpleNamespace(root=root, stage=stage, native=native, binding=binding, layout={"parts": parts}, faults=faults)
    def stage_token(raw=FAKE_CREDENTIAL):
        reader, _ = credential_pipe(raw)
        return a.stage_metadata_credential(native, world.layout, ingress_fd=reader)
    world.stage_token = stage_token
    world.cleanup = lambda: a.cleanup_metadata_credential(123, 1, native["boot_id"], credential_layout=world.layout)
    return world


def test_credential_staging_read_and_idempotent_cleanup_never_records_token(credential_world, capsys):
    world = credential_world
    assert world.stage_token() is None
    target = world.stage / "metadata-token"
    info = target.lstat()
    assert stat.S_IMODE(info.st_mode) == 0o400 and info.st_nlink == 1
    assert target.read_bytes() == FAKE_CREDENTIAL
    assert a._read_metadata_credential(world.binding) == FAKE_CREDENTIAL
    receipt = world.cleanup()
    assert receipt == {"schema_version": 1, "status": "PASS", "run_id": 123, "run_attempt": 1,
                       "boot_id": world.native["boot_id"], "token_absent": True}
    assert not target.exists() and world.cleanup() == receipt
    assert set(p.name for p in world.stage.iterdir()) == {"part-00.txt", "part-01.txt", "bootstrap-entry.py"}
    assert FAKE_CREDENTIAL.decode() not in repr(receipt)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("change", ["extra", "missing", "changed", "entry_mode", "entry_symlink", "owner", "group", "link"])
def test_credential_staging_requires_exact_verified_source_and_entry_before_read(credential_world, monkeypatch, change):
    world = credential_world
    path = world.stage / "part-00.txt"
    if change == "extra":
        (world.stage / "unreviewed").write_bytes(b"extra")
    elif change == "missing":
        path.unlink()
    elif change == "changed":
        path.chmod(0o600)
        path.write_bytes(b"x" * len(path.read_bytes()))
        path.chmod(0o400)
    elif change == "entry_mode":
        (world.stage / "bootstrap-entry.py").chmod(0o600)
    elif change == "entry_symlink":
        entry = world.stage / "bootstrap-entry.py"
        entry.unlink()
        entry.symlink_to(path)
    elif change == "link":
        os.link(path, world.root / "outside-link")
    else:
        world.faults[path.stat().st_ino] = {"st_uid" if change == "owner" else "st_gid": 1001}
    monkeypatch.setattr(a, "_read_credential_ingress", lambda *_: pytest.fail("read token before source validation"))
    with pytest.raises(a.SetupError):
        world.stage_token()
    assert not (world.stage / "metadata-token").exists()


def test_duplicate_credential_staging_rejects_before_ingress(credential_world, monkeypatch):
    world = credential_world
    world.stage_token()
    monkeypatch.setattr(a, "_read_credential_ingress", lambda *_: pytest.fail("duplicate ingress"))
    with pytest.raises(a.SetupError):
        world.stage_token()
    assert a._read_metadata_credential(world.binding) == FAKE_CREDENTIAL


@pytest.mark.parametrize("exception", [a.SetupError("credential ingress failed"), KeyboardInterrupt()])
def test_failed_ingress_cannot_close_a_reused_descriptor(credential_world, monkeypatch, exception):
    world = credential_world
    replacements = []
    def failed_reader(fd):
        os.close(fd)
        replacement = os.open("/dev/null", os.O_RDONLY)
        assert replacement == fd
        replacements.append(replacement)
        raise exception
    monkeypatch.setattr(a, "_read_credential_ingress", failed_reader)
    try:
        with pytest.raises(type(exception)):
            world.stage_token()
        assert len(replacements) == 1
        assert stat.S_ISCHR(os.fstat(replacements[0]).st_mode)
    finally:
        for replacement in replacements:
            os.close(replacement)


@pytest.mark.parametrize("change", ["symlink", "hardlink", "owner", "group", "mode", "overflow", "empty", "control"])
def test_credential_reads_reject_unsealed_or_malformed_token(credential_world, change):
    world = credential_world
    world.stage_token()
    path = world.stage / "metadata-token"
    if change == "symlink":
        path.unlink()
        path.symlink_to(world.stage / "part-00.txt")
    elif change == "hardlink":
        os.link(path, world.root / "outside-link")
    elif change in {"owner", "group"}:
        world.faults[path.stat().st_ino] = {"st_uid" if change == "owner" else "st_gid": 1001}
    else:
        path.chmod(0o600)
        if change != "mode":
            path.write_bytes({"overflow": b"x" * 4097, "empty": b"", "control": b"x\n"}[change])
            path.chmod(0o400)
    with pytest.raises(a.SetupError):
        a._read_metadata_credential(world.binding)


@pytest.mark.parametrize("change", ["extra", "changed_part", "missing_part", "missing_entry"])
def test_cleanup_removes_valid_token_before_reporting_unrelated_inventory_failure(credential_world, change):
    world = credential_world
    world.stage_token()
    if change == "extra":
        (world.stage / "unexpected").write_bytes(b"do not remove")
    elif change == "changed_part":
        path = world.stage / "part-00.txt"
        path.chmod(0o600)
    else:
        (world.stage / ("part-00.txt" if change == "missing_part" else "bootstrap-entry.py")).unlink()
    remaining = set(p.name for p in world.stage.iterdir()) - {"metadata-token"}
    with pytest.raises(a.SetupError):
        world.cleanup()
    assert not (world.stage / "metadata-token").exists()
    assert set(p.name for p in world.stage.iterdir()) == remaining


@pytest.mark.parametrize("change", ["symlink", "hardlink", "owner", "mode", "directory"])
def test_cleanup_does_not_unlink_untrusted_token(credential_world, change):
    world = credential_world
    world.stage_token()
    path = world.stage / "metadata-token"
    if change in {"symlink", "directory"}:
        path.unlink()
        path.symlink_to(world.stage / "part-00.txt") if change == "symlink" else path.mkdir()
    elif change == "hardlink":
        os.link(path, world.root / "outside-link")
    elif change == "owner":
        world.faults[path.stat().st_ino] = {"st_uid": 1001}
    else:
        path.chmod(0o600)
    with pytest.raises(a.SetupError):
        world.cleanup()
    assert path.exists() or path.is_symlink()


@pytest.mark.parametrize("raw", [b"", b"x" * 4097, FAKE_CREDENTIAL + b"\n"])
def test_rejected_ingress_never_creates_credential_file(credential_world, raw):
    with pytest.raises(a.SetupError):
        credential_world.stage_token(raw)
    assert not (credential_world.stage / "metadata-token").exists()


def test_no_eof_never_creates_file_and_cleanup_remains_safe(credential_world, monkeypatch):
    world = credential_world
    monkeypatch.setattr(a, "CREDENTIAL_INGRESS_SECONDS", 0.025)
    reader, writer = credential_pipe(FAKE_CREDENTIAL, eof=False)
    try:
        with pytest.raises(a.SetupError, match="deadline"):
            a.stage_metadata_credential(world.native, world.layout, ingress_fd=reader)
        assert not (world.stage / "metadata-token").exists()
        assert world.cleanup()["token_absent"] is True
        with pytest.raises(BrokenPipeError):
            os.write(writer, b"x")
    finally:
        os.close(writer)


def test_stage_write_failure_leaves_only_safely_removable_token(credential_world, monkeypatch):
    world = credential_world
    monkeypatch.setattr(a.os, "fsync", lambda _: (_ for _ in ()).throw(OSError("synthetic disk failure")))
    with pytest.raises(a.SetupError, match="staging unavailable"):
        world.stage_token()
    assert (world.stage / "metadata-token").exists()
    assert world.cleanup()["token_absent"] is True


@pytest.mark.parametrize("missing_root", [False, True])
def test_cleanup_accepts_stage_that_never_existed_without_admission_claim(credential_world, missing_root):
    world = credential_world
    for path in world.stage.iterdir():
        path.unlink()
    world.stage.rmdir()
    if missing_root:
        world.root.rmdir()
    receipt = world.cleanup()
    assert receipt["token_absent"] is True
    assert not set(receipt) & {"binding", "setup_receipt", "load_attempted", "positive_passed", "qualified"}


@pytest.mark.parametrize("action", ["read", "cleanup"])
def test_token_descriptor_path_identity_mismatch_never_reads_or_unlinks(credential_world, monkeypatch, action):
    world = credential_world
    world.stage_token()
    original_stat = os.stat
    def switched(*args, **kwargs):
        info = original_stat(*args, **kwargs)
        if args[0] == "metadata-token" and "dir_fd" in kwargs:
            return SimpleNamespace(**{**vars(info), "st_ino": info.st_ino + 1})
        return info
    monkeypatch.setattr(a.os, "stat", switched)
    monkeypatch.setattr(a, "_credential_read_fd", lambda *_: pytest.fail("read changed token"))
    with pytest.raises(a.SetupError, match="changed"):
        a._read_metadata_credential(world.binding) if action == "read" else world.cleanup()
    assert (world.stage / "metadata-token").exists()


@pytest.mark.parametrize("change", ["owner", "group", "mode"])
def test_credential_parent_chain_rejects_wrong_ownership_or_writable_ancestor(credential_world, change):
    world = credential_world
    ancestor = world.root.parent
    world.faults[ancestor.stat().st_ino] = {"st_uid": 1001} if change == "owner" else {"st_gid": 1001} if change == "group" else {"st_mode": stat.S_IFDIR | 0o777}
    with pytest.raises(a.SetupError, match="ancestor"):
        world.stage_token()


@pytest.mark.parametrize("change", ["empty", "too_many", "duplicate", "order", "traversal", "size", "boolean", "extra", "digest"])
def test_credential_part_manifest_is_finite_ordered_exact_and_cannot_choose_paths(credential_world, change):
    layout = copy.deepcopy(credential_world.layout)
    if change == "empty":
        layout["parts"] = []
    elif change == "too_many":
        layout["parts"] *= 17
    elif change == "duplicate":
        layout["parts"][1] = copy.deepcopy(layout["parts"][0])
    elif change == "order":
        layout["parts"].reverse()
    elif change == "traversal":
        layout["parts"][0]["name"] = "../metadata-token"
    elif change == "size":
        layout["parts"][0]["bytes"] = 32001
    elif change == "boolean":
        layout["parts"][0]["bytes"] = True
    elif change == "extra":
        layout["path"] = "/untrusted"
    else:
        layout["parts"][0]["sha256"] = "0" * 64
    with pytest.raises(a.SetupError):
        a._credential_parts(layout)


def test_fixed_six_metadata_gets_authenticate_only_at_transport_and_keep_receipts_clean(credential_world, monkeypatch, capsys):
    import base64
    import urllib.parse
    world = credential_world
    world.stage_token()
    calls = []
    class Connection:
        def __init__(self, host, **kwargs):
            assert host == "api.github.com" and 0 < kwargs["timeout"] <= 5
        def request(self, method, path, *, headers):
            calls.append((method, path, headers))
        def getresponse(self):
            return SimpleNamespace(status=200, getheader=lambda name, default=None: "application/json" if name == "Content-Type" else default,
                                   read=lambda _: b'{"result":"fixed"}')
        def close(self):
            pass
    monkeypatch.setattr(a.http.client, "HTTPSConnection", Connection)
    paths = a.live_metadata_paths(world.binding, world.binding["workflow_id"])
    reader = a.MetadataReader(set(paths.values()), binding=world.binding)
    for path in paths.values():
        assert reader.one(path) == {"result": "fixed"}
    assert reader.requests == 6 and len(calls) == 6
    assert all(method == "GET" and path in {"/repos/HouMinXi/forge" + p for p in paths.values()}
               and headers.pop("Authorization") == b"Bearer " + FAKE_CREDENTIAL for method, path, headers in calls)
    assert not set(vars(reader)) & {"token", "credential", "authorization", "headers"}
    output = repr((vars(reader), calls, capsys.readouterr(), world.cleanup(), a.observer_argv(world.binding), a.SYSTEM_ENV))
    for sentinel in (FAKE_CREDENTIAL.decode(), FAKE_CREDENTIAL.hex(), base64.b64encode(FAKE_CREDENTIAL).decode(), urllib.parse.quote(FAKE_CREDENTIAL.decode())):
        assert sentinel not in output


@pytest.mark.parametrize("failure", ["request", "response", "close"])
def test_transport_errors_never_serialize_credential_or_provider_exception(credential_world, monkeypatch, failure):
    import traceback
    world = credential_world
    world.stage_token()
    calls = []
    class Connection:
        def __init__(self, *_args, **_kwargs):
            pass
        def request(self, *_args, **_kwargs):
            calls.append("request")
            if failure == "request":
                raise RuntimeError(FAKE_CREDENTIAL.decode())
        def getresponse(self):
            if failure == "response":
                raise ValueError(FAKE_CREDENTIAL.decode())
            return SimpleNamespace(status=200, getheader=lambda name, default=None: "application/json" if name == "Content-Type" else default,
                                   read=lambda _: b"{}")
        def close(self):
            if failure == "close":
                raise OSError(FAKE_CREDENTIAL.decode())
    monkeypatch.setattr(a.http.client, "HTTPSConnection", Connection)
    reader = a.MetadataReader({"/actions/workflows/linux-tests.yml"}, binding=world.binding)
    with pytest.raises(a.SetupError) as error:
        reader.one("/actions/workflows/linux-tests.yml")
    assert FAKE_CREDENTIAL.decode() not in "".join(traceback.format_exception(error.value))
    assert calls == ["request"] and reader.requests == 1


def test_credential_is_never_read_for_nonfixed_authenticated_endpoint(credential_world, monkeypatch):
    world = credential_world
    reads = []
    monkeypatch.setattr(a, "_read_metadata_credential", lambda *_: reads.append(True) or FAKE_CREDENTIAL)
    reader = a.MetadataReader({"/anything"}, binding=world.binding)
    with pytest.raises(a.SetupError, match="unreviewed authenticated"):
        reader.one("/anything")
    assert reads == []


def test_vendor_download_has_no_credential_read_or_authorization(monkeypatch):
    calls = []
    name = next(iter(a.ARCHIVES))
    raw = b"offline vendor fixture"
    monkeypatch.setattr(a, "ARCHIVES", {name: checksum(raw)})
    monkeypatch.setattr(a, "_read_metadata_credential", lambda *_: pytest.fail("vendor requested credential"))
    class Connection:
        def __init__(self, host, **_kwargs):
            assert host == "security.ubuntu.com"
        def request(self, method, path, *, headers):
            calls.append((method, path, headers))
        def getresponse(self):
            return SimpleNamespace(status=200, getheader=lambda _name, default=None: default, read=lambda _: raw)
        def close(self):
            pass
    monkeypatch.setattr(a.http.client, "HTTPSConnection", Connection)
    assert a.download_archive(name) == raw
    assert len(calls) == 1 and "Authorization" not in calls[0][2]


@pytest.mark.parametrize("requested_name", ["python", "python3.12"])
@pytest.mark.parametrize("owner,passes", [(0, True), (1001, True), (1000, True), (54321, True), (True, False), (-1, False)])
def test_exact_provider_executable_records_numeric_build_owner_without_runner_equality(tmp_path, monkeypatch, owner, passes, requested_name):
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
        assert a.ordinary_executable(str(root / "bin" / requested_name))["uid"] == owner
    else:
        with pytest.raises(a.SetupError):
            a.ordinary_executable(str(root / "bin" / requested_name))


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
                            live_count=0, reads={}, host=host, before=before, after=after, credential_present=False,
                            credential_layout={"parts": [{"name": "part-00.txt", "bytes": 1, "sha256": checksum(b"x")}]})
    original_close = os.close
    monkeypatch.setattr(a.os, "close", lambda fd: None if fd == 0 else original_close(fd))
    def stage_credential(native, layout, *, ingress_fd):
        assert layout == world.credential_layout and set(native) == a.NATIVE_BINDING_KEYS
        assert ingress_fd == 0
        if world.credential_present:
            raise FileExistsError("credential already staged")
        world.credential_present = True
        world.stages.append("credential")
        if world.fail == "credential":
            raise a.SetupError("credential staging unavailable")
        if world.fail == "cancel_credential":
            a.signal.getsignal(a.signal.SIGTERM)(a.signal.SIGTERM, None)
    def reserve_stdin():
        assert world.credential_present and world.stages == ["credential"]
        world.stages.append("stdin")
        if world.fail == "stdin":
            raise a.SetupError("bootstrap stdin reservation unavailable")
    def cleanup_credential(run_id, run_attempt, boot_id, *, credential_layout):
        assert (run_id, run_attempt, boot_id) == (123, 1, host["boot_id"])
        assert credential_layout == world.credential_layout
        world.credential_present = False
        return {"schema_version": 1, "status": "PASS", "run_id": run_id, "run_attempt": run_attempt,
                "boot_id": boot_id, "token_absent": True}
    monkeypatch.setattr(a, "stage_metadata_credential", stage_credential)
    monkeypatch.setattr(a, "reserve_bootstrap_stdin", reserve_stdin)
    monkeypatch.setattr(a, "cleanup_metadata_credential", cleanup_credential)
    class Evidence:
        def __init__(self, enabled=True):
            self.enabled = enabled
        def emit(self, kind, value):
            world.events.append((kind, copy.deepcopy(value)))
    monkeypatch.setattr(a, "PublicEvidence", Evidence)
    monkeypatch.setattr(a, "require_clean_root", lambda: None)
    monkeypatch.setattr(a.pwd, "getpwuid", lambda _: SimpleNamespace(pw_gid=1001, pw_name="runner"))
    monkeypatch.setattr(a, "read_regular", lambda *_args, **_kwargs: a.canonical(event))
    monkeypatch.setattr(a, "trusted_boot_id", lambda: host["boot_id"])
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
        if world.fail == "live_initial" or world.fail == "live_preload" and world.live_count == 3:
            raise a.SetupError("live identity changed")
        return {"checked": a.stamp(), "binding": binding_fixture()}
    monkeypatch.setattr(a, "live_identity", live)
    def install(*_):
        live()
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
    result = a.bootstrap(world.config, world.source, credential_layout=world.credential_layout)
    assert result["status"] == "PASS" and result["load_attempted"] is True and result["positive_passed"] is True
    assert world.stages == ["credential", "stdin", "live", "snapshot", "live", "install", "before", "negative", "preprocess", "compile", "live", "recheck", "load", "after", "positive"]
    stage = a.stage_path(result["binding"])
    assert json.loads(world.files[str(stage / "setup.json")]) == result
    assert world.stages.count("load") == 1
    with pytest.raises(FileExistsError):
        a.bootstrap(world.config, world.source, credential_layout=world.credential_layout)
    assert world.stages.count("load") == 1


@pytest.mark.parametrize("failure", ["credential", "cancel_credential", "stdin", "root_directory", "live_initial", "snapshot", "install", "before", "negative", "preprocess", "compile", "live_preload", "recheck", "changed_preload", "compiler_preload", "cancel_compile"])
def test_every_early_failure_has_zero_add_and_no_seal(bootstrap_world, failure):
    world = bootstrap_world
    world.fail = failure
    with pytest.raises(a.SetupError):
        a.bootstrap(world.config, world.source, credential_layout=world.credential_layout)
    assert "load" not in world.stages and "positive" not in world.stages
    assert not any(name.endswith("/setup.json") for name in world.files)
    assert world.events[-1][0] == "setup-stop" and world.events[-1][1]["load_attempted"] is False
    assert world.credential_present is False
    if failure in {"credential", "cancel_credential", "stdin"}:
        assert world.stages == (["credential", "stdin"] if failure == "stdin" else ["credential"])
        assert world.files == {}


@pytest.mark.parametrize("failure", ["load", "after", "changed_after", "invalid_after", "positive", "cancel_load"])
def test_failed_or_uncertain_add_never_seals_or_qualifies(bootstrap_world, failure):
    world = bootstrap_world
    world.fail = failure
    with pytest.raises(a.SetupError):
        a.bootstrap(world.config, world.source, credential_layout=world.credential_layout)
    assert world.stages.count("load") == 1
    assert not any(name.endswith("/setup.json") for name in world.files)
    assert world.events[-1][0] == "setup-stop" and world.events[-1][1]["load_attempted"] is True
    assert world.credential_present is False
    if failure != "positive":
        assert "positive" not in world.stages


@pytest.mark.parametrize("change", [None, "compiled", "observer", "boot", "inputs", "policy", "live", "seal"])
def test_read_only_observer_rechecks_sealed_whole_policy_and_binding(bootstrap_world, monkeypatch, change):
    world = bootstrap_world
    seal = a.bootstrap(world.config, world.source, credential_layout=world.credential_layout)
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


@pytest.mark.parametrize("path", ["/usr/bin/python3.12", "/usr/bin/ip", "/bin/true"])
def test_numeric_build_owner_is_never_an_owner_bypass_for_system_paths(monkeypatch, path):
    endpoint = "/usr/bin/" + Path(path).name
    monkeypatch.setattr(Path, "resolve", lambda self, **_: Path(endpoint))
    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    monkeypatch.setattr(Path, "stat", lambda self, **_: SimpleNamespace(st_mode=stat.S_IFREG | 0o755, st_uid=54321, st_gid=54321))
    with pytest.raises(a.SetupError) as caught:
        a.ordinary_executable(path)
    message = str(caught.value)
    for item in ("requested=" + repr(path), "canonical=" + repr(endpoint), "uid=54321", "gid=54321", "mode=0o100755", "owner_rule=system-uid-0"):
        assert item in message


@pytest.mark.parametrize("change", ["setuid", "setgid", "file_caps", "caps_unreadable", "nonelf", "wrong_link", "wrong_endpoint", "extra_alias"])
def test_exact_provider_owner_class_keeps_every_other_executable_guard(tmp_path, monkeypatch, change):
    root = tmp_path / "provider"
    (root / "bin").mkdir(parents=True)
    exe = root / "bin/python3.12"
    exe.write_bytes(b"not ELF" if change == "nonelf" else b"\x7fELFtrusted provider")
    modes = {"setuid": 0o4755, "setgid": 0o2755, "file_caps": 0o777, "caps_unreadable": 0o777}
    exe.chmod(modes.get(change, 0o755))
    link = root / "bin/python"
    link.symlink_to("other" if change == "wrong_link" else "python3.12")
    if change == "wrong_link":
        (root / "bin/other").write_bytes(b"\x7fELFwrong")
    monkeypatch.setattr(a, "PROVIDER_ROOT", str(root))
    original_stat = Path.stat
    def metadata(path, *args, **kwargs):
        info = original_stat(path, *args, **kwargs)
        return SimpleNamespace(st_mode=info.st_mode, st_uid=54321, st_gid=32100) if path == exe else info
    monkeypatch.setattr(Path, "stat", metadata)
    def caps(*_):
        if change == "file_caps":
            return b"file-capabilities"
        if change == "caps_unreadable":
            raise OSError(errno.EACCES, "unreadable capabilities")
        raise OSError(errno.ENODATA, "no capabilities")
    monkeypatch.setattr(a.os, "getxattr", caps)
    requested = link
    if change == "wrong_endpoint":
        monkeypatch.setattr(Path, "resolve", lambda self, **_: Path("/unreviewed/python3.12"))
    elif change == "extra_alias":
        requested = root / "bin/python-extra"
        requested.symlink_to("python3.12")
    with pytest.raises((a.SetupError, OSError)):
        a.ordinary_executable(str(requested))


def test_early_provider_exclusion_and_late_exact_two_path_membership_stay_fixed(monkeypatch):
    seen = []
    monkeypatch.setattr(a, "ordinary_executable", lambda path: seen.append(path) or {"path": path})
    a.finite_paths()
    early = set(seen)
    assert not any(path.startswith(a.PROVIDER_ROOT + "/") for path in early)
    seen.clear()
    a.finite_paths(include_provider=True)
    assert set(seen) - early == {a.PROVIDER_ROOT + "/bin/python", a.PROVIDER_ROOT + "/bin/python3.12"}
    assert set(seen) == set(a.CERTIFIED_PATHS)


@pytest.mark.parametrize("requested_name", ["python", "python3.12"])
@pytest.mark.parametrize("mode,accepted", [(0o755, True), (0o775, True), (0o757, True), (0o777, True), (0o4777, False), (0o2777, False)])
def test_only_exact_host_provider_paths_record_writable_modes_without_setid(tmp_path, monkeypatch, requested_name, mode, accepted):
    root = tmp_path / "provider"
    (root / "bin").mkdir(parents=True)
    exe = root / "bin/python3.12"
    exe.write_bytes(b"\x7fELFtrusted host interpreter")
    (root / "bin/python").symlink_to("python3.12")
    monkeypatch.setattr(a, "PROVIDER_ROOT", str(root))
    original = Path.stat
    def metadata(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        return SimpleNamespace(st_mode=stat.S_IFREG | mode, st_uid=1001, st_gid=1000) if path == exe else info
    monkeypatch.setattr(Path, "stat", metadata)
    def no_caps(*_):
        raise OSError(errno.ENODATA, "no capabilities")
    monkeypatch.setattr(a.os, "getxattr", no_caps)
    path = str(root / "bin" / requested_name)
    if accepted:
        record = a.ordinary_executable(path)
        assert (record["uid"], record["gid"], record["mode"]) == (1001, 1000, mode)
        assert record["elf"] and record["file_capabilities"] is False
    else:
        with pytest.raises(a.SetupError):
            a.ordinary_executable(path)


@pytest.mark.parametrize("path", ["/usr/bin/python3", "/usr/bin/bwrap", "/usr/bin/ip", "/bin/true",
                                  a.PROVIDER_ROOT + "/bin/python-extra"])
@pytest.mark.parametrize("mode", [0o775, 0o757, 0o777, 0o4755, 0o2755])
def test_provider_mode_rule_never_relaxes_system_or_lookalike_paths(monkeypatch, path, mode):
    endpoint = a.PROVIDER_ROOT + "/bin/python3.12" if path.startswith(a.PROVIDER_ROOT) else "/usr/bin/" + ("python3.12" if Path(path).name == "python3" else Path(path).name)
    monkeypatch.setattr(Path, "resolve", lambda self, **_: Path(endpoint))
    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    monkeypatch.setattr(Path, "stat", lambda self, **_: SimpleNamespace(st_mode=stat.S_IFREG | mode, st_uid=0, st_gid=0))
    with pytest.raises(a.SetupError, match="owner_rule=system-uid-0"):
        a.ordinary_executable(path)


@pytest.mark.parametrize("failure", [None, "unavailable_after", "rollback"])
def test_setup_runner_records_coarse_clock_around_unchanged_command(tmp_path, monkeypatch, failure):
    calls = []
    fine = iter([1700000000123000000, 1700000000124000000])
    coarse = iter([1700000000123000000, 1700000000122999999 if failure == "rollback" else 1700000000124000000])
    monotonic = iter([90, 100, 200, 210])
    def gettime(clock):
        assert clock == 5
        if failure == "unavailable_after" and calls.count("coarse") == 1:
            raise OSError(errno.EINVAL, "unsupported after operation")
        calls.append("coarse")
        return next(coarse)
    def mono():
        calls.append("monotonic")
        return next(monotonic)
    def utc():
        calls.append("fine")
        return next(fine)
    monkeypatch.setattr(a.time, "clock_getres", lambda clock: 0.001 if clock == 5 else pytest.fail("wrong clock"))
    monkeypatch.setattr(a.time, "clock_gettime_ns", gettime)
    monkeypatch.setattr(a.time, "monotonic_ns", mono)
    monkeypatch.setattr(a.time, "time_ns", utc)
    monkeypatch.setattr(a.pwd, "getpwuid", lambda uid: SimpleNamespace(pw_name="runner"))
    monkeypatch.setattr(a.os, "getgrouplist", lambda name, gid: [gid])
    def run(argv, timeout, *, user, group, extra_groups, pass_fds):
        assert calls == ["monotonic", "coarse"]
        assert timeout == 15 and user == group == 1001 and extra_groups == [1001]
        result = negative()
        result.update(argv=argv, started=a.stamp())
        calls.append("operation")
        result["ended"] = a.stamp()
        assert argv[:5] == ["/usr/bin/python3", "-B", "-I", "-S", "-c"]
        observed = {"uid": 1001, "gid": 1001, "pid": result["wrapper_pid"], "label": "unconfined",
                    "exec_argv": argv[9:], "pending_exec_absent": True}
        os.write(pass_fds[0], json.dumps(observed).encode())
        os.write(pass_fds[1], b'{"child-pid":456}')
        return result
    def journal(argv, evidence, timeout):
        assert calls == ["monotonic", "coarse", "fine", "monotonic", "operation", "fine", "monotonic", "coarse", "monotonic"]
        assert timeout == 5 and argv[3] == "/usr/bin/journalctl"
        return (json.dumps({"_TRANSPORT": "kernel", "MESSAGE": audit()}) + "\n").encode()
    emitted = []
    evidence = SimpleNamespace(emit=lambda kind, value: emitted.append((kind, copy.deepcopy(value))))
    monkeypatch.setattr(a, "command", run)
    monkeypatch.setattr(a, "checked_command", journal)
    if failure:
        with pytest.raises(a.SetupError):
            a.runner_probe(tmp_path, 1001, 1001, evidence, negative=True)
        assert len(emitted) == 1 and emitted[0][0] == "negative-probe"
        result = emitted[0][1]
        assert "audit clock:" in result["error"]
        assert result["audit_clock"]["before_ns"] == audit_clock()["before_ns"]
        assert result["started"] == negative()["started"] and result["ended"] == negative()["ended"]
        with pytest.raises(a.SetupError):
            a.validate_negative_control(result)
        return
    result = a.runner_probe(tmp_path, 1001, 1001, evidence, negative=True)
    assert result["audit_clock"] == audit_clock()
    assert result["started"] == negative()["started"] and result["ended"] == negative()["ended"]
    assert emitted == [("negative-probe", result)]
    a.validate_negative_control(result)


def test_setup_clock_is_absent_from_sealed_observer_call_graph():
    import ast
    source = a.observer_source(Path(a.__file__).read_text(), config_fixture())
    names = {node.name for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)}
    assert not names & {"begin_audit_clock", "end_audit_clock", "validate_audit_clock"}


def test_closed_vendor_install_only_fixed_bubblewrap_and_fresh_preinstall_live(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(a, "root_directory", lambda *_args, **_kw: None)
    monkeypatch.setattr(a, "download_archive", lambda name: events.append(("download", name)) or b"authenticated data")
    monkeypatch.setattr(a, "sealed_write", lambda *_args: None)
    monkeypatch.setattr(a, "live_identity", lambda cfg, binding: events.append(("live", copy.deepcopy(binding))))
    def command(argv, _evidence, **_kwargs):
        events.append(("command", argv))
        if argv[:2] == ["/usr/bin/dpkg-deb", "--field"]:
            return ("Package: " + Path(argv[2]).name.split("_", 1)[0] + "\n").encode()
        if argv[0] == "/usr/bin/dpkg-query":
            return b"apparmor 4.0.1really4.0.1-0ubuntu0.24.04.8\nbubblewrap 0.9.0-1ubuntu0.3\n"
        return b""
    monkeypatch.setattr(a, "checked_command", command)
    a.install_vendor(tmp_path, a.PublicEvidence(enabled=False), binding_fixture())
    commands = [value for kind, value in events if kind == "command"]
    installs = [argv for argv in commands if argv[0] == "/usr/bin/dpkg"]
    assert installs == [["/usr/bin/dpkg", "--install", str(tmp_path / "vendor/bubblewrap_0.9.0-1ubuntu0.3_amd64.deb")]]
    assert len([argv for argv in commands if "--extract" in argv]) == 3
    install_index = events.index(("command", installs[0]))
    assert events[install_index - 1] == ("live", binding_fixture())
    assert a.ARCHIVES["bubblewrap_0.9.0-1ubuntu0.3_amd64.deb"] == "2461f1beee9cb04c8942739fe1a2b37e7b7c2a3d518f0779dc75f9245baa3094"



def test_real_stdin_reservation_rejects_fifo_without_waiting_for_writer():
    result = ordinary_setup_child(r'''
import errno, json, os, tempfile
assert os.getuid() == os.geteuid() > 0 and os.getgid() == os.getegid() > 0
a.require_clean_root = lambda: None
with tempfile.TemporaryDirectory() as directory:
    fifo = directory + '/not-null'
    os.mkfifo(fifo, 0o600)
    real_open = os.open
    def redirect_only_fixed_open(path, flags, *args, **kwargs):
        if path == '/dev/null':
            assert flags & os.O_NONBLOCK
            path = fifo
        return real_open(path, flags, *args, **kwargs)
    os.close(0)
    a.os.open = redirect_only_fixed_open
    try:
        try:
            a.reserve_bootstrap_stdin()
        except a.SetupError:
            pass
        else:
            raise AssertionError('FIFO admitted as null device')
    finally:
        a.os.open = real_open
    try:
        os.fstat(0)
    except OSError as error:
        assert error.errno == errno.EBADF
    else:
        raise AssertionError('rejected FIFO descriptor retained')
    print(json.dumps(dict(fifo_rejected=True, stdin_closed=True)))
''')
    assert result == {"fifo_rejected": True, "stdin_closed": True}
