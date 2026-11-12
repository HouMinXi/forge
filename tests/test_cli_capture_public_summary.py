"""Synthetic private receipt projection; no capture, provider or tracer runs."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/cli_capture_public_summary.py"
CANARY = "PRIVATE-/home/account/api?token=SECRET ::error::PRIVATE"
KEYS = {
    "schema",
    "mode",
    "receipt_status",
    "harness_shell_exit",
    "worker_returncode",
    "cli_returncode",
    "cli_returncode_before_cleanup",
    "cleanup_induced_termination",
    "collector_delivery",
    "cleanup",
    "qualification",
    "raw_evidence",
    "controller_stage",
    "controller_error_category",
    "failure_observation",
    "launch_stderr_observation",
    "startup_evidence",
    "startup_stage",
    "startup_error_category",
}


@pytest.fixture(scope="module")
def public():
    spec = importlib.util.spec_from_file_location("cli_capture_public_summary", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def receipt(mode="diagnostic", code=7):
    """Independent fixture representing the existing controller's result contract."""
    return {
        "schema": 1,
        "mode": mode,
        "shell_exit": 128 - code if code < 0 else code,
        "qualification": "NOT_ASSESSED",
        "capture_scope": "delivery_framing_only",
        "ack_consistency": "NOT_ASSESSED",
        "stream_correlation": "NOT_ASSESSED",
        "outer_exception": None,
        "record_errors": [],
        "worker_returncode": 0,
        "ownership": {"cleanup_complete": True, "owned": [{"remaining": False, "path": CANARY}]},
        "worker": {
            "schema": 1,
            "mode": mode,
            "transport_status": "receipt_written",
            "child_pid": 1234,
            "child_returncode": code,
            "child_returncode_before_cleanup": code,
            "cleanup_induced_termination": False,
            "cleanup_errors": [],
            "primary_exception": None,
            "raw_path": CANARY,
        },
        "final_collector_health": {
            "stage": "after_owner_quiescence",
            "scope": "delivery_framing_only",
            "status": "healthy" if mode == "diagnostic" else "not_applicable",
            "errors": [],
            "cache_health": {"status": "healthy"},
            "private_extension": CANARY,
        },
        "private_extra": CANARY,
    }


def write_receipt(tmp_path, value=None, raw=None):
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    parent.chmod(0o700)
    path = parent / "result.json"
    path.write_bytes(raw if raw is not None else (json.dumps(value) + "\n").encode())
    path.chmod(0o600)
    return path


def assert_safe(value):
    assert set(value) == KEYS
    assert value["qualification"] == "NOT_ASSESSED"
    assert value["raw_evidence"] == "NOT_PUBLISHED"
    assert len(json.dumps(value)) < 1024
    assert CANARY not in json.dumps(value)
    assert "PASS" not in value.values()


@pytest.mark.parametrize("mode", ["baseline", "diagnostic"])
@pytest.mark.parametrize("code", [-127, -9, -1, 0, 7, 73, 124, 130, 255])
def test_original_signed_exit_is_preserved_without_success_claim(public, tmp_path, mode, code):
    value = receipt(mode, code)
    path = write_receipt(tmp_path, value)
    summary = public.summarize(str(path), mode, value["shell_exit"])
    assert_safe(summary)
    assert summary["receipt_status"] == "DELIVERED"
    assert summary["cli_returncode"] == summary["cli_returncode_before_cleanup"] == code
    assert summary["harness_shell_exit"] == value["shell_exit"]
    assert summary["worker_returncode"] == 0
    assert summary["cleanup_induced_termination"] is False
    assert summary["cleanup"] == "COMPLETE"
    assert summary["collector_delivery"] == ("HEALTHY" if mode == "diagnostic" else "NOT_APPLICABLE")


def test_timeout_and_cleanup_kill_remain_separate(public, tmp_path):
    value = receipt(code=-9)
    value["shell_exit"] = 124
    value["worker"].update(
        child_returncode_before_cleanup=None,
        cleanup_induced_termination=True,
        primary_exception={"type": "TimeoutExpired", "message": CANARY},
    )
    value["final_collector_health"] = None
    path = write_receipt(tmp_path, value)
    summary = public.summarize(str(path), "diagnostic", 124)
    assert_safe(summary)
    assert summary["receipt_status"] == "DELIVERED"
    assert summary["harness_shell_exit"] == 124
    assert summary["cli_returncode"] == -9
    assert summary["cli_returncode_before_cleanup"] is None
    assert summary["cleanup_induced_termination"] is True
    assert summary["collector_delivery"] == "NOT_OBSERVED"


def test_setup_failure_has_no_invented_cli_or_capability_observation(public, tmp_path):
    value = receipt()
    value.update(
        shell_exit=73,
        worker=None,
        ownership=None,
        worker_returncode=None,
        final_collector_health=None,
        outer_exception={"type": "ValueError", "message": CANARY},
    )
    path = write_receipt(tmp_path, value)
    summary = public.summarize(str(path), "diagnostic", 73)
    assert_safe(summary)
    assert summary["receipt_status"] == "DELIVERED"
    assert summary["cli_returncode"] is summary["worker_returncode"] is None
    assert summary["cleanup"] == summary["collector_delivery"] == "NOT_OBSERVED"
    assert "UNSUPPORTED" not in summary.values()


@pytest.mark.parametrize("where", ["worker", "ownership"])
def test_cleanup_failure_cannot_be_reported_complete(public, tmp_path, where):
    value = receipt()
    if where == "worker":
        value["worker"]["cleanup_errors"] = [{"type": "OSError", "message": CANARY}]
    else:
        value["ownership"]["cleanup_complete"] = False
        value["ownership"]["owned"][0]["remaining"] = True
        value["final_collector_health"] = None
    path = write_receipt(tmp_path, value)
    summary = public.summarize(str(path), "diagnostic", 7)
    assert_safe(summary)
    assert summary["receipt_status"] == "DELIVERED"
    assert summary["cleanup"] == "INCOMPLETE"
    assert summary["cli_returncode"] == summary["harness_shell_exit"] == 7


def test_failed_final_framing_stays_failed(public, tmp_path):
    value = receipt()
    value["final_collector_health"].update(status="failed", errors=[CANARY])
    path = write_receipt(tmp_path, value)
    summary = public.summarize(str(path), "diagnostic", 7)
    assert_safe(summary)
    assert summary["collector_delivery"] == "FAILED"
    assert summary["cli_returncode"] == 7


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r.update(shell_exit=0),
        lambda r: r["worker"].update(child_returncode_before_cleanup=0),
        lambda r: r["worker"].update(cleanup_induced_termination=True),
        lambda r: r["ownership"]["owned"][0].update(remaining=True),
        lambda r: r["ownership"].update(cleanup_complete=False),
        lambda r: r["final_collector_health"].update(errors=[CANARY]),
        lambda r: r.update(worker=None),
    ],
)
def test_contradictions_discard_receipt_values_but_not_observed_shell(public, tmp_path, change):
    value = receipt()
    change(value)
    path = write_receipt(tmp_path, value)
    summary = public.summarize(str(path), "diagnostic", 7)
    assert_safe(summary)
    assert summary["receipt_status"] == "CONTRADICTORY"
    assert summary["harness_shell_exit"] == 7
    assert summary["cli_returncode"] is None
    assert summary["cleanup"] == summary["collector_delivery"] == "NOT_OBSERVED"


@pytest.mark.parametrize("field", ["schema", "shell_exit", "worker_returncode"])
@pytest.mark.parametrize("bad", [True, False, [], {}, 1.5, CANARY, -128, 256])
def test_root_numeric_types_are_strict(public, tmp_path, field, bad):
    value = receipt()
    value[field] = bad
    path = write_receipt(tmp_path, value)
    summary = public.summarize(str(path), "diagnostic", 7)
    assert_safe(summary)
    assert summary["receipt_status"] == "INVALID"
    assert summary["harness_shell_exit"] == 7


@pytest.mark.parametrize("field", ["child_returncode", "child_returncode_before_cleanup"])
@pytest.mark.parametrize("bad", [True, False, [], {}, 1.5, CANARY, -128, 256])
def test_worker_signed_status_types_are_strict(public, tmp_path, field, bad):
    value = receipt()
    value["worker"][field] = bad
    path = write_receipt(tmp_path, value)
    summary = public.summarize(str(path), "diagnostic", 7)
    assert summary["receipt_status"] == "INVALID"
    assert_safe(summary)


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r.update(mode=CANARY),
        lambda r: r.update(qualification="PASS"),
        lambda r: r.update(capture_scope=CANARY),
        lambda r: r.update(ack_consistency="PASS"),
        lambda r: r.update(stream_correlation="PASS"),
        lambda r: r.update(record_errors={}),
        lambda r: r.update(outer_exception=CANARY),
        lambda r: r["worker"].update(mode="baseline"),
        lambda r: r["worker"].update(schema=True),
        lambda r: r["worker"].update(transport_status=CANARY),
        lambda r: r["worker"].update(primary_exception=[]),
        lambda r: r["worker"].update(cleanup_errors={}),
        lambda r: r["worker"].update(cleanup_induced_termination=1),
        lambda r: r["worker"].update(child_returncode=None, child_returncode_before_cleanup=None),
        lambda r: r["worker"].update(child_pid=True),
        lambda r: r["ownership"].update(owned={}),
        lambda r: r["ownership"].update(cleanup_complete=1),
        lambda r: r["ownership"]["owned"][0].update(remaining=0),
        lambda r: r["final_collector_health"].update(stage="preliminary_before_owner_cleanup"),
        lambda r: r["final_collector_health"].update(status=CANARY),
        lambda r: r["final_collector_health"].update(status=[]),
        lambda r: r["final_collector_health"].update(status="not_applicable"),
        lambda r: r["final_collector_health"].update(scope=CANARY),
    ],
)
def test_malformed_shapes_and_untrusted_text_fail_closed(public, tmp_path, change):
    value = receipt()
    change(value)
    summary = public.summarize(str(write_receipt(tmp_path, value)), "diagnostic", 7)
    assert_safe(summary)
    assert summary["receipt_status"] == "INVALID"


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{}",
        b"{}\n",
        b"[]\n",
        b"null\n",
        b"{bad}\n",
        b"\xff\n",
        b'{"schema":1,"schema":1}\n',
        b'{"value":NaN}\n',
        b'{"value":Infinity}\n',
        b'{"value":-Infinity}\n',
        b'{"value":1e999}\n',
        b'{"value":{"k":1,"k":2}}\n',
        b"[" * 2000 + b"]" * 2000 + b"\n",
    ],
)
def test_invalid_json_never_prints_private_bytes(public, tmp_path, raw):
    summary = public.summarize(str(write_receipt(tmp_path, raw=raw)), "diagnostic", 7)
    assert_safe(summary)
    assert summary["receipt_status"] == "INVALID"


@pytest.mark.parametrize("mutation", ["mode", "directory", "symlink", "parent_link", "hardlink", "fifo"])
def test_private_no_follow_input_contract(public, tmp_path, mutation):
    path = write_receipt(tmp_path, receipt())
    if mutation == "mode":
        path.chmod(0o644)
    elif mutation == "directory":
        path.parent.chmod(0o755)
    elif mutation == "symlink":
        target = path.with_name("target")
        path.rename(target)
        path.symlink_to(target)
    elif mutation == "parent_link":
        link = tmp_path / "linked"
        link.symlink_to(path.parent, target_is_directory=True)
        path = link / path.name
    elif mutation == "hardlink":
        os.link(path, path.with_name("other"))
    else:
        path.unlink()
        os.mkfifo(path, 0o600)
    summary = public.summarize(str(path), "diagnostic", 7)
    assert_safe(summary)
    assert summary["receipt_status"] == "INVALID"


def test_missing_receipt_retains_independent_status(public, tmp_path):
    summary = public.summarize(str(tmp_path / "absent"), "diagnostic", 73)
    assert_safe(summary)
    assert summary["receipt_status"] == "UNAVAILABLE"
    assert summary["harness_shell_exit"] == 73


def test_bounded_read_rejects_oversize_before_read(public, tmp_path, monkeypatch):
    path = write_receipt(tmp_path, receipt())
    monkeypatch.setattr(public, "MAX_BYTES", 10)
    monkeypatch.setattr(public.os, "read", lambda *_: pytest.fail("oversize input was read"))
    assert public.summarize(str(path), "diagnostic", 7)["receipt_status"] == "INVALID"


def test_file_replacement_during_read_rejected(public, tmp_path, monkeypatch):
    path = write_receipt(tmp_path, receipt())
    original = os.read
    replaced = False

    def read(fd, count):
        nonlocal replaced
        data = original(fd, count)
        if not replaced:
            replaced = True
            path.rename(path.with_name("old"))
            path.write_bytes(b"{}\n")
            path.chmod(0o600)
        return data

    monkeypatch.setattr(public.os, "read", read)
    assert public.summarize(str(path), "diagnostic", 7)["receipt_status"] == "INVALID"


@pytest.mark.parametrize("mode", [None, [], {}, True, "", CANARY])
@pytest.mark.parametrize("code", [None, [], {}, True, -1, 256, "7", CANARY, 7])
def test_invalid_arguments_do_not_echo(public, mode, code):
    value = public.summarize(CANARY, mode, code)
    assert_safe(value)
    assert value["receipt_status"] == "INVALID_ARGUMENTS"


@pytest.mark.parametrize("code", [0, 7, 73, 124, 130, 255])
def test_cli_reports_valid_projection_without_replacing_capture_exit(tmp_path, code):
    path = write_receipt(tmp_path, receipt(code=code))
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            str(SCRIPT),
            "--result",
            str(path),
            "--mode",
            "diagnostic",
            "--shell-exit",
            str(code),
        ],
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0
    assert result.stderr == b"" and result.stdout.count(b"\n") == 1
    summary = json.loads(result.stdout)
    assert_safe(summary)
    assert summary["harness_shell_exit"] == summary["cli_returncode"] == code


@pytest.mark.parametrize(
    "args",
    [
        [],
        [CANARY],
        ["--result", CANARY] * 3,
        ["--result", CANARY, "--mode", "diagnostic", "--shell-exit", "007"],
        ["--result", CANARY, "--mode", "diagnostic", "--shell-exit", "256"],
        ["--result", CANARY, "--mode", CANARY, "--shell-exit", "7"],
    ],
)
def test_cli_invalid_args_emit_only_fixed_summary(args):
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(SCRIPT), *args],
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 2
    assert result.stderr == b"" and result.stdout.count(b"\n") == 1
    summary = json.loads(result.stdout)
    assert_safe(summary)
    assert summary["receipt_status"] == "INVALID_ARGUMENTS"


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
def test_nonfinite_in_otherwise_valid_ignored_extension_rejected(public, tmp_path, literal):
    raw = (json.dumps(receipt())[:-1] + ',"ignored_number":' + literal + "}\n").encode()
    result = public.summarize(str(write_receipt(tmp_path, raw=raw)), "diagnostic", 7)
    assert result["receipt_status"] == "INVALID"
    assert_safe(result)


def test_finite_extension_and_exact_read_limit_accepted(public, tmp_path, monkeypatch):
    value = receipt()
    value["ignored_number"] = 1.25
    path = write_receipt(tmp_path, value)
    monkeypatch.setattr(public, "MAX_BYTES", path.stat().st_size)
    result = public.summarize(str(path), "diagnostic", 7)
    assert result["receipt_status"] == "DELIVERED"
    assert_safe(result)


@pytest.mark.parametrize("field", ["worker", "ownership", "final_collector_health", "worker_returncode"])
def test_missing_required_nullable_field_is_invalid(public, tmp_path, field):
    value = receipt()
    del value[field]
    result = public.summarize(str(write_receipt(tmp_path, value)), "diagnostic", 7)
    assert result["receipt_status"] == "INVALID"
    assert_safe(result)


@pytest.mark.parametrize("mode", ["baseline", "diagnostic"])
@pytest.mark.parametrize("code", [-9, 0, 7, 73, 124, 130, 255])
def test_projection_accepted_by_independent_publication(public, tmp_path, mode, code):
    validator_path = SCRIPT.with_name("cli_capture_publication.py")
    spec = importlib.util.spec_from_file_location("independent_capture_publication", validator_path)
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    value = receipt(mode, code)
    projected = public.summarize(str(write_receipt(tmp_path, value)), mode, value["shell_exit"])
    assert projected["receipt_status"] == "DELIVERED"
    assert validator.validate(projected, mode + ".json") == projected
    assert_safe(projected)


def test_unavailable_projection_accepted_without_invented_receipt(public, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "independent_capture_publication", SCRIPT.with_name("cli_capture_publication.py")
    )
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    projected = public.summarize(str(tmp_path / "absent"), "diagnostic", 73)
    assert projected["receipt_status"] == "UNAVAILABLE"
    assert validator.validate(projected, "diagnostic.json") == projected
    assert projected["harness_shell_exit"] == 73
    assert_safe(projected)


def test_legacy_private_receipt_has_unknown_stage_not_success(public, tmp_path):
    value = public.summarize(str(write_receipt(tmp_path, receipt())), "diagnostic", 7)
    assert value["schema"] == "forge-public-cli-capture-summary-v4"
    assert value["controller_stage"] == value["controller_error_category"] == "UNKNOWN"
    assert value["failure_observation"] == "NOT_OBSERVED"
    assert value["harness_shell_exit"] == value["cli_returncode"] == 7


@pytest.mark.parametrize(
    "stage",
    [
        "INITIALIZE",
        "SANITIZE",
        "PREFLIGHT",
        "LIMITS",
        "INSTALLATION",
        "UTILITY_BINDING",
        "WORKER_BINDING",
        "OWNER_LOAD",
        "WORKER_EXECUTION",
        "WORKER_OUTPUT",
        "WORKER_RECEIPT",
        "FINAL_DELIVERY",
    ],
)
@pytest.mark.parametrize(
    "category",
    [
        "PERMISSION_DENIED",
        "NOT_FOUND",
        "NOT_DIRECTORY",
        "TIMEOUT",
        "INTERRUPTED",
        "INVALID_DATA",
        "OS_ERROR",
        "OTHER_ERROR",
    ],
)
def test_explicit_stage_category_only_fixed_enums(public, tmp_path, stage, category):
    source = receipt()
    source.update(controller_stage=stage, controller_error_category=category)
    value = public.summarize(str(write_receipt(tmp_path, source)), "diagnostic", 7)
    assert value["receipt_status"] == "DELIVERED"
    assert value["controller_stage"] == stage
    assert value["controller_error_category"] == category
    spec = importlib.util.spec_from_file_location(
        "stage_publication", SCRIPT.with_name("cli_capture_publication.py")
    )
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    assert validator.validate(value, "diagnostic.json") == value
    assert_safe(value)


def test_finalize_means_no_controller_exception_not_cli_pass(public, tmp_path):
    source = receipt()
    source.update(controller_stage="FINALIZE", controller_error_category="NONE")
    value = public.summarize(str(write_receipt(tmp_path, source)), "diagnostic", 7)
    assert value["controller_stage"] == "FINALIZE"
    assert value["cli_returncode"] == 7
    assert value["qualification"] == "NOT_ASSESSED"


@pytest.mark.parametrize("field", ["controller_stage", "controller_error_category"])
@pytest.mark.parametrize(
    "bad",
    [None, True, 0, [], {}, "UNKNOWN", CANARY],
    ids=["null", "bool", "int", "list", "dict", "unknown", "canary"],
)
def test_invalid_private_stage_pair_fails_closed(public, tmp_path, field, bad):
    source = receipt()
    source.update(controller_stage="WORKER_RECEIPT", controller_error_category="NOT_FOUND")
    source[field] = bad
    value = public.summarize(str(write_receipt(tmp_path, source)), "diagnostic", 7)
    assert value["receipt_status"] == "INVALID"
    assert value["controller_stage"] == value["controller_error_category"] == "UNKNOWN"
    assert value["harness_shell_exit"] == 7
    assert_safe(value)


@pytest.mark.parametrize("field", ["controller_stage", "controller_error_category"])
def test_partial_stage_pair_fails_closed(public, tmp_path, field):
    source = receipt()
    source.update(controller_stage="WORKER_RECEIPT", controller_error_category="NOT_FOUND")
    del source[field]
    assert (
        public.summarize(str(write_receipt(tmp_path, source)), "diagnostic", 7)["receipt_status"]
        == "INVALID"
    )


@pytest.mark.parametrize("stage,category", [("FINALIZE", "NOT_FOUND"), ("WORKER_RECEIPT", "NONE")])
def test_contradictory_stage_pair_is_not_success(public, tmp_path, stage, category):
    source = receipt()
    source.update(controller_stage=stage, controller_error_category=category)
    value = public.summarize(str(write_receipt(tmp_path, source)), "diagnostic", 7)
    assert value["receipt_status"] == "CONTRADICTORY"
    assert value["controller_stage"] == "UNKNOWN"
    assert value["harness_shell_exit"] == 7


@pytest.mark.parametrize("code", [None, 0, 1, -9, 73])
def test_nonzero_worker_without_receipt_is_observation_not_root_cause(public, tmp_path, code):
    source = receipt()
    source.update(
        worker=None,
        worker_returncode=code,
        final_collector_health=None,
        controller_stage="WORKER_RECEIPT",
        controller_error_category="NOT_FOUND",
    )
    value = public.summarize(str(write_receipt(tmp_path, source)), "diagnostic", 7)
    expected = "NOT_OBSERVED" if code in (None, 0) else "WORKER_NONZERO_NO_RECEIPT"
    assert value["failure_observation"] == expected
    assert value["controller_error_category"] == "NOT_FOUND"
    assert value["cli_returncode"] is None
    assert_safe(value)


@pytest.mark.parametrize(
    "observation",
    [
        "NOT_RECOGNIZED",
        "STDERR_EXACT_UNSHARE_OPERATION_NOT_PERMITTED",
        "STDERR_EXACT_UNSHARE_PERMISSION_DENIED",
    ],
)
def test_launch_enum_projection_and_independent_publication(public, tmp_path, observation):
    source = receipt()
    source.update(
        worker_returncode=1,
        worker=None,
        final_collector_health=None,
        launch_stderr_observation=observation,
    )
    value = public.summarize(str(write_receipt(tmp_path, source)), "diagnostic", 7)
    assert value["receipt_status"] == "DELIVERED"
    assert value["launch_stderr_observation"] == observation
    spec = importlib.util.spec_from_file_location(
        "launch_publication", SCRIPT.with_name("cli_capture_publication.py")
    )
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    assert validator.validate(value, "diagnostic.json") == value
    assert_safe(value)


@pytest.mark.parametrize(
    "bad", [None, True, 1, {}, [], CANARY], ids=["null", "bool", "int", "dict", "list", "canary"]
)
def test_launch_unknown_private_value_fails_closed(public, tmp_path, bad):
    source = receipt()
    source["launch_stderr_observation"] = bad
    value = public.summarize(str(write_receipt(tmp_path, source)), "diagnostic", 7)
    assert value["receipt_status"] == "INVALID"
    assert value["launch_stderr_observation"] == "NOT_ASSESSED"
    assert value["harness_shell_exit"] == 7


@pytest.mark.parametrize("fault", ["code", "receipt", "outer"])
def test_launch_private_contradiction_discards_observation(public, tmp_path, fault):
    source = receipt()
    source.update(
        worker_returncode=1,
        worker=None,
        final_collector_health=None,
        launch_stderr_observation="STDERR_EXACT_UNSHARE_OPERATION_NOT_PERMITTED",
    )
    if fault == "code":
        source["worker_returncode"] = 0
    elif fault == "receipt":
        source["worker"] = receipt()["worker"]
    else:
        source["outer_exception"] = {"type": "TimeoutExpired", "message": CANARY}
    value = public.summarize(str(write_receipt(tmp_path, source)), "diagnostic", 7)
    assert value["receipt_status"] == "CONTRADICTORY"
    assert value["launch_stderr_observation"] == "NOT_ASSESSED"
    assert value["harness_shell_exit"] == 7


@pytest.mark.parametrize(
    "status,stage,category",
    [
        ("NOT_OBSERVED", "NOT_OBSERVED", "NOT_OBSERVED"),
        ("INVALID", "NOT_OBSERVED", "NOT_OBSERVED"),
        ("PARTIAL", "BOOTSTRAP_ENTERED", "INVALID_DATA"),
        ("PARTIAL", "WORKER_SOURCE_VERIFIED", "SYNTAX_ERROR"),
        ("OBSERVED", "WORKER_EXECUTION_ENTERED", "IMPORT_ERROR"),
        ("OBSERVED", "WORKER_EXECUTION_ENTERED", "NONE"),
    ],
)
def test_startup_fixed_observation_projects_without_qualification(
    public, tmp_path, status, stage, category
):
    source = receipt()
    source.update(startup_evidence=status, startup_stage=stage, startup_error_category=category)
    value = public.summarize(str(write_receipt(tmp_path, source)), "diagnostic", 7)
    assert value["receipt_status"] == "DELIVERED"
    assert value["startup_evidence"] == status and value["startup_stage"] == stage
    assert value["startup_error_category"] == category
    assert value["harness_shell_exit"] == value["cli_returncode"] == 7
    spec = importlib.util.spec_from_file_location(
        "startup_publisher", SCRIPT.with_name("cli_capture_publication.py")
    )
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    assert validator.validate(value, "diagnostic.json") == value
    assert_safe(value)


@pytest.mark.parametrize(
    "fault", ["partial_keys", "bool", "text", "full_prefix", "empty_error", "missing_error"]
)
def test_startup_invalid_private_contract_fails_closed(public, tmp_path, fault):
    source = receipt()
    source.update(
        startup_evidence="PARTIAL", startup_stage="BOOTSTRAP_ENTERED", startup_error_category="NONE"
    )
    if fault == "partial_keys":
        del source["startup_stage"]
    elif fault == "bool":
        source["startup_evidence"] = True
    elif fault == "text":
        source["startup_error_category"] = CANARY
    elif fault == "full_prefix":
        source["startup_evidence"] = "OBSERVED"
    elif fault == "empty_error":
        source.update(startup_evidence="NOT_OBSERVED", startup_stage="NOT_OBSERVED")
    else:
        source["startup_error_category"] = "NOT_OBSERVED"
    value = public.summarize(str(write_receipt(tmp_path, source)), "diagnostic", 7)
    assert value["receipt_status"] == "INVALID"
    assert all(value[key] == "NOT_OBSERVED" for key in public.STARTUP_KEYS)
    assert value["harness_shell_exit"] == 7
