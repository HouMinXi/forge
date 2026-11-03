"""Offline user-service publication contracts; no native collector is executed."""

import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/user_service_public_summary.py"
WORKFLOW = ROOT / ".github/workflows/strict-linux-user-service.yml"
WRAPPER = "scripts/run_user_service_observation.py"
PUBLIC_PATH = Path("forge-user-service-public/summary.json")
CANARY = "PRIVATE-unit.service-/home/private-user/token-PID-7654321"
PUBLIC_KEYS = {"schema", "mode", "status", "boundary", "qualification", "raw_evidence", "reason"}
PUBLIC_REASONS = {
    "authorization_required",
    "source_mismatch",
    "observation_complete",
    "collector_stop",
    "unavailable",
    "unspecified",
    "execution_failed",
}


@pytest.fixture(scope="module")
def public():
    spec = importlib.util.spec_from_file_location("user_service_public_summary", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def raw(status="OBSERVED"):
    return {
        "schema": "forge-user-service-wrapper-status-v1",
        "status": status,
        "receipt": "VALID",
        "cleanup": "OBSERVED",
        "source_rechecked": True,
        "boundary": "NOT_RUN",
        "qualification": "NOT_RUN",
        "reason": CANARY,
        "unit": CANARY,
        "path": CANARY,
        "pid": 7654321,
        "context": {"repository": CANARY, "run_id": CANARY},
        "observation": {"control_group": CANARY, "main_pid": 7654321},
        "stderr": CANARY,
        "collector_exit_code": 0,
        "collector_signal": None,
        CANARY: CANARY,
    }


def assert_public(value, expected=None):
    assert set(value) == PUBLIC_KEYS
    assert value["schema"] == "forge-public-user-service-summary-v1"
    assert value["mode"] == "user_service"
    assert value["status"] in {"OBSERVED", "STOP", "UNKNOWN"}
    assert value["boundary"] == value["qualification"] == "NOT_RUN"
    assert value["raw_evidence"] == "NOT_PUBLISHED"
    assert type(value["reason"]) is str
    assert value["reason"] in PUBLIC_REASONS
    assert CANARY not in json.dumps(value) and "7654321" not in json.dumps(value)
    if expected is not None:
        assert value["status"] == expected


@pytest.mark.parametrize("status", ["OBSERVED", "STOP", "UNKNOWN"])
@pytest.mark.parametrize("code", [0, 2, 17, 137, 255])
def test_projection_is_allowlisted_and_nonzero_cannot_be_observed(public, status, code):
    result = public.summary(raw(status), code)
    assert_public(result)
    if status == "OBSERVED" and code == 0:
        assert result["status"] == "OBSERVED"
    else:
        assert result["status"] != "OBSERVED"
    if status in {"OBSERVED", "STOP"} and code != 0:
        assert result["status"] == "STOP"


@pytest.mark.parametrize("value", [None, [], True, 0, CANARY, {"status": "OBSERVED"}])
def test_unrecognized_receipt_is_fixed_unknown(public, value):
    assert_public(public.summary(value, 0), "UNKNOWN")


@pytest.mark.parametrize(
    "key",
    ["schema", "status", "receipt", "cleanup", "source_rechecked", "boundary", "qualification"],
)
@pytest.mark.parametrize("value", [None, [], {}, 1, 0, CANARY])
def test_malformed_required_fields_never_claim_observed(public, key, value):
    result = public.summary({**raw(), key: value}, 0)
    assert_public(result)
    assert result["status"] != "OBSERVED"


@pytest.mark.parametrize(
    "key,value",
    [
        ("receipt", "MISSING"),
        ("receipt", "INVALID"),
        ("cleanup", "UNKNOWN"),
        ("cleanup", "NOT_RUN"),
        ("cleanup", "STOP"),
        ("source_rechecked", False),
        ("boundary", "OBSERVED"),
        ("qualification", "PASS"),
        ("status", "PASS"),
    ],
)
def test_incomplete_or_inconsistent_success_is_never_observed(public, key, value):
    result = public.summary({**raw(), key: value}, 0)
    assert_public(result)
    assert result["status"] != "OBSERVED"


@pytest.mark.parametrize("code", [None, True, False, "0", [], {}, -1, 256, 1.0])
def test_exit_status_requires_real_bounded_integer(public, code):
    assert_public(public.summary(raw(), code), "UNKNOWN")


@pytest.mark.parametrize("reason", [CANARY, None, [CANARY], {CANARY: CANARY}, "observed/" + CANARY])
@pytest.mark.parametrize("status,code", [("OBSERVED", 0), ("STOP", 2)])
def test_unknown_reason_is_not_copied(public, reason, status, code):
    result = public.summary({**raw(status), "reason": reason}, code)
    assert_public(result, status)
    assert result == public.summary(raw(status), code)


@pytest.mark.parametrize(
    "reason", ["authorization_required", "source_mismatch", "observation_complete", "collector_stop"]
)
def test_only_fixed_reason_labels_can_pass_through_stop(public, reason):
    result = public.summary({**raw("STOP"), "reason": reason}, 2)
    assert_public(result, "STOP")
    assert result["reason"] == reason


def test_stale_success_reason_is_replaced_after_later_process_failure(public):
    result = public.summary({**raw(), "reason": "observation_complete"}, 137)
    assert_public(result, "STOP")
    assert result["reason"] == "execution_failed"


@pytest.mark.parametrize("cleanup", ["OBSERVED", "STOP", "UNKNOWN", "NOT_RUN"])
@pytest.mark.parametrize("code", [0, 2])
def test_legal_cleanup_states_preserve_wrapper_stop(public, cleanup, code):
    value = {**raw("STOP"), "cleanup": cleanup, "reason": "collector_stop"}
    result = public.summary(value, code)
    assert_public(result, "STOP")
    assert result["reason"] == "collector_stop"


def invoke_summary(path, code="0"):
    return subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(SCRIPT), str(path), code],
        capture_output=True,
        timeout=3,
    )


@pytest.mark.parametrize("code", [0, 2, 17, 137])
def test_cli_emits_only_one_fixed_summary_and_observed_is_only_success(tmp_path, code):
    path = tmp_path / "private.json"
    path.write_text(json.dumps(raw()))
    proc = invoke_summary(path, str(code))
    assert proc.returncode == (0 if code == 0 else 2)
    assert proc.stderr == b"" and proc.stdout.count(b"\n") == 1
    assert len(proc.stdout) < 1024
    assert_public(json.loads(proc.stdout), "OBSERVED" if code == 0 else "STOP")
    assert str(path).encode() not in proc.stdout


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "malformed",
        "duplicate",
        "nested_duplicate",
        "oversized",
        "symlink",
        "fifo",
        "directory",
        "nested",
        "invalid_utf8",
        "trailing",
        "nonfinite",
    ],
)
def test_reader_failure_is_bounded_private_and_unknown(tmp_path, public, kind):
    path = tmp_path / "private.json"
    if kind == "malformed":
        path.write_text(CANARY)
    elif kind == "duplicate":
        path.write_text(json.dumps(raw())[:-1] + ',"status":"OBSERVED"}')
    elif kind == "nested_duplicate":
        path.write_text(json.dumps(raw())[:-1] + ',"other":{"x":1,"x":2}}')
    elif kind == "oversized":
        assert public.MAX_INPUT == 16384
        path.write_bytes(b"x" * (public.MAX_INPUT + 1))
    elif kind == "symlink":
        target = tmp_path / "target"
        target.write_text(json.dumps(raw()))
        path.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "directory":
        path.mkdir()
    elif kind == "nested":
        path.write_text("[" * 3000 + "]" * 3000)
    elif kind == "invalid_utf8":
        path.write_bytes(b"\xff" + CANARY.encode())
    elif kind == "trailing":
        path.write_text(json.dumps(raw()) + CANARY)
    elif kind == "nonfinite":
        path.write_text(json.dumps(raw())[:-1] + ',"private":NaN}')
    proc = invoke_summary(path)
    assert proc.returncode == 2 and proc.stderr == b""
    assert len(proc.stdout) < 1024
    assert_public(json.loads(proc.stdout), "UNKNOWN")
    assert str(path).encode() not in proc.stdout


@pytest.mark.parametrize("token", ["1e400", "-1e400", "1.0", "1e-400"])
def test_reader_rejects_float_tokens_including_exponent_overflow(tmp_path, token):
    path = tmp_path / "private.json"
    path.write_text(json.dumps(raw())[:-1] + ',"private":' + token + "}")
    proc = invoke_summary(path)
    assert proc.returncode == 2 and proc.stderr == b""
    assert_public(json.loads(proc.stdout), "UNKNOWN")


@pytest.mark.parametrize("code", ["-1", "256", "0.0", CANARY, "", "True"])
def test_invalid_cli_exit_status_is_finite_unknown(tmp_path, code):
    path = tmp_path / "private.json"
    path.write_text(json.dumps(raw()))
    proc = invoke_summary(path, code)
    assert proc.returncode == 2 and proc.stderr == b""
    assert_public(json.loads(proc.stdout), "UNKNOWN")


@pytest.mark.parametrize("size", [16384, 16385])
def test_receipt_limit_is_enforced_for_otherwise_valid_json(tmp_path, public, size):
    assert public.MAX_INPUT == 16384
    value = {**raw(), "private_padding": ""}
    value["private_padding"] = "x" * (size - len(json.dumps(value).encode()))
    data = json.dumps(value).encode()
    assert len(data) == size
    path = tmp_path / "private.json"
    path.write_bytes(data)
    proc = invoke_summary(path)
    expected = "OBSERVED" if size == public.MAX_INPUT else "UNKNOWN"
    assert_public(json.loads(proc.stdout), expected)
    assert proc.returncode == (0 if expected == "OBSERVED" else 2)
    assert proc.stderr == b""


@pytest.mark.parametrize("extra", [[], [CANARY], [CANARY, "0", CANARY]])
def test_cli_wrong_arity_emits_fixed_unknown_without_argument_echo(extra):
    proc = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(SCRIPT), *extra],
        capture_output=True,
        timeout=3,
    )
    assert proc.returncode == 2 and proc.stderr == b""
    assert_public(json.loads(proc.stdout), "UNKNOWN")


def workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def observation_step():
    matches = [
        step
        for step in workflow()["jobs"]["observe"]["steps"]
        if "user_service_public_summary.py" in step.get("run", "")
    ]
    assert len(matches) == 1
    return matches[0]


def test_workflow_is_manual_explicit_single_run_and_publishes_exact_summary():
    value = workflow()
    assert set(value["on"]) == {"workflow_dispatch"}
    inputs = value["on"]["workflow_dispatch"]["inputs"]
    assert inputs["allow_transient_service"]["type"] == "boolean"
    assert inputs["allow_transient_service"]["default"] is False
    assert inputs["expected_source_sha"]["type"] == "string"
    assert inputs["expected_source_sha"]["default"] == ""
    assert value["permissions"] == {"contents": "read"}
    assert value["concurrency"]["cancel-in-progress"] is False
    assert set(value["jobs"]) == {"observe"}
    job = value["jobs"]["observe"]
    assert "github.event_name == 'workflow_dispatch'" in job["if"]
    assert job["runs-on"] == "ubuntu-24.04" and job["timeout-minutes"] == 3
    checkout = [step for step in job["steps"] if "actions/checkout@" in step.get("uses", "")]
    assert len(checkout) == 1
    assert checkout[0]["with"]["persist-credentials"] is False
    assert "github.sha" in checkout[0]["with"]["ref"]
    uploads = [step for step in job["steps"] if "actions/upload-artifact@" in step.get("uses", "")]
    assert len(uploads) == 1
    upload = uploads[0]
    assert upload["with"]["path"] == str(PUBLIC_PATH)
    assert all(not part.startswith(".") for part in Path(upload["with"]["path"]).parts)
    assert upload["if"] == "${{ always() && env.SERVICE_PUBLIC_READY == 'true' }}"
    assert upload["with"]["if-no-files-found"] == "error"
    serialized = json.dumps(value)
    assert "${{ secrets." not in serialized
    assert "pull_request" not in serialized
    runs = "\n".join(step.get("run", "") for step in job["steps"])
    assert not re.search(r"(?m)^\s*(?:sudo|systemctl|systemd-run)\b", runs)
    assert "SERVICE_EVIDENCE=" not in runs
    step = observation_step()
    assert step["env"]["ALLOW_TRANSIENT_SERVICE"] == "${{ inputs.allow_transient_service }}"
    assert step["env"]["EXPECTED_SOURCE_SHA"] == "${{ inputs.expected_source_sha }}"
    assert step["run"].startswith("exec 2>/dev/null\n")
    assert "set +x" in step["run"] and "diagnostic_code=$?" in step["run"]
    assert "--allow-transient-service" in step["run"]
    assert "--expected-source-sha" in step["run"]


def shell_case(tmp_path, body, *, opt_in="true", with_helper=True):
    """Run the workflow shell in a scratch cwd containing no native collector."""
    workspace = tmp_path / "workspace"
    scripts = workspace / "scripts"
    scripts.mkdir(parents=True)
    if with_helper:
        shutil.copyfile(SCRIPT, scripts / SCRIPT.name)
    marker = tmp_path / "fixture-argv.json"
    fixture = tmp_path / "fixture.py"
    fixture.write_text(
        "import json,os,pathlib,signal,sys\n"
        f"pathlib.Path({str(marker)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        "args=sys.argv[1:]\n"
        "directory=pathlib.Path(args[args.index('--output-dir')+1])\n"
        f"print({CANARY!r},flush=True)\n"
        f"print({CANARY!r},file=sys.stderr,flush=True)\n" + body
    )
    step = observation_step()
    run = step["run"]
    assert run.count(WRAPPER) == 1
    run = run.replace(WRAPPER, str(fixture))
    assert WRAPPER not in run and str(fixture) in run
    assert "strict_user_service_probe.py" not in run
    assert not (scripts / Path(WRAPPER).name).exists()
    env = {
        **os.environ,
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_ENV": str(tmp_path / "github-env"),
    }
    for key, value in step.get("env", {}).items():
        if "inputs.allow_transient_service" in value:
            env[key] = opt_in
        elif "inputs.expected_source_sha" in value:
            env[key] = "a" * 40
        elif "github.event_name" in value:
            env[key] = "workflow_dispatch"
        elif "github.sha" in value:
            env[key] = "a" * 40
        else:
            env[key] = "public-context"
    return workspace, run, env, marker


def execute_shell(case):
    workspace, run, env, _ = case
    proc = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-c", run],
        cwd=workspace,
        env=env,
        capture_output=True,
        timeout=5,
    )
    assert proc.stdout == proc.stderr == b""
    return proc


def assert_ready(case, expected=True):
    env_file = Path(case[2]["GITHUB_ENV"])
    if not expected:
        assert not env_file.exists() or env_file.read_text() == ""
        return
    assert env_file.read_text() == "SERVICE_PUBLIC_READY=true\n"
    assert len(list((case[0] / PUBLIC_PATH.parent).iterdir())) == 1
    result_path = case[0] / PUBLIC_PATH
    assert result_path.stat().st_mode & 0o077 == 0
    assert len(result_path.read_bytes()) < 1024


@pytest.mark.parametrize("code", [2, 17])
def test_actual_workflow_preserves_cleanup_stop(tmp_path, code):
    value = {**raw("STOP"), "cleanup": "STOP", "reason": "collector_stop"}
    body = f"(directory/'collector-status.json').write_text(json.dumps({value!r}))\nsys.exit({code})\n"
    case = shell_case(tmp_path, body)
    proc = execute_shell(case)
    assert proc.returncode == code
    assert_ready(case)
    result = json.loads((case[0] / PUBLIC_PATH).read_text())
    assert_public(result, "STOP")
    assert result["reason"] == "collector_stop"


@pytest.mark.parametrize("code", [0, 2, 17, 137])
@pytest.mark.parametrize("status", ["OBSERVED", "STOP"])
def test_actual_workflow_shell_contains_raw_streams_and_preserves_exit(tmp_path, code, status):
    body = (
        f"(directory/'collector-status.json').write_text(json.dumps({raw(status)!r}))\n"
        f"os.kill(os.getpid(),signal.SIGKILL) if {code} == 137 else None\n"
        f"sys.exit({code})\n"
    )
    case = shell_case(tmp_path, body)
    proc = execute_shell(case)
    expected_code = code if code else (0 if status == "OBSERVED" else 2)
    assert proc.returncode == expected_code
    assert_ready(case)
    expected_status = "OBSERVED" if status == "OBSERVED" and code == 0 else "STOP"
    assert_public(json.loads((case[0] / PUBLIC_PATH).read_text()), expected_status)
    if code == 137:
        private_logs = list(tmp_path.rglob("private*stderr.txt"))
        assert any(b"Killed" in path.read_bytes() for path in private_logs)


@pytest.mark.parametrize(
    "body,code",
    [
        (f"raise RuntimeError({CANARY!r})\n", 1),
        ("os.kill(os.getpid(),signal.SIGKILL)\n", 137),
        ("sys.exit(17)\n", 17),
        ("sys.exit(0)\n", 0),
        (f"(directory/'collector-status.json').write_text({CANARY!r})\nsys.exit(2)\n", 2),
    ],
)
def test_actual_shell_missing_and_hostile_receipts_are_private_unknown(tmp_path, body, code):
    case = shell_case(tmp_path, body)
    assert execute_shell(case).returncode == (code or 2)
    assert_ready(case)
    assert_public(json.loads((case[0] / PUBLIC_PATH).read_text()), "UNKNOWN")


@pytest.mark.parametrize("opt_in", ["false", "", "True", "TRUE", "1", "true ", CANARY])
def test_shell_never_forwards_service_opt_in_unless_exactly_true(tmp_path, opt_in):
    body = f"(directory/'collector-status.json').write_text(json.dumps({raw()!r}))\n"
    case = shell_case(tmp_path, body, opt_in=opt_in)
    execute_shell(case)
    if case[3].exists():
        args = json.loads(case[3].read_text())
        assert "--allow-transient-service" not in args


def test_shell_explicit_opt_in_and_source_are_passed_as_separate_arguments(tmp_path):
    body = f"(directory/'collector-status.json').write_text(json.dumps({raw()!r}))\n"
    case = shell_case(tmp_path, body)
    assert execute_shell(case).returncode == 0
    args = json.loads(case[3].read_text())
    assert args.count("--allow-transient-service") == 1
    assert args[args.index("--expected-source-sha") + 1] == "a" * 40
    assert_ready(case)


@pytest.mark.parametrize("code", [0, 2, 17, 137])
def test_summary_process_failure_never_masks_wrapper_failure_or_enables_upload(tmp_path, code):
    body = (
        f"(directory/'collector-status.json').write_text(json.dumps({raw()!r}))\n"
        f"os.kill(os.getpid(),signal.SIGKILL) if {code} == 137 else None\n"
        f"sys.exit({code})\n"
    )
    case = shell_case(tmp_path, body, with_helper=False)
    result = execute_shell(case)
    if code:
        assert result.returncode == code
    else:
        assert result.returncode != 0
    assert_ready(case, False)
    output = case[0] / PUBLIC_PATH
    assert not output.exists() or output.read_bytes() == b""


@pytest.mark.parametrize("wrapper_code", [0, 17])
@pytest.mark.parametrize("helper_code", [0, 2])
@pytest.mark.parametrize("size", [0, 1025])
def test_empty_or_oversized_helper_output_never_enables_publication(
    tmp_path, wrapper_code, helper_code, size
):
    body = (
        f"(directory/'collector-status.json').write_text(json.dumps({raw()!r}))\n"
        f"sys.exit({wrapper_code})\n"
    )
    case = shell_case(tmp_path, body)
    # Fault-inject only the scratch copy, never the source helper or collector.
    helper = case[0] / "scripts" / SCRIPT.name
    helper.write_text(f"import sys\nsys.stdout.write('x'*{size})\nsys.exit({helper_code})\n")
    proc = execute_shell(case)
    assert proc.returncode == (wrapper_code or 2)
    assert_ready(case, False)


@pytest.mark.parametrize("wrapper_code", [0, 17])
def test_helper_exception_is_private_and_does_not_enable_upload(tmp_path, wrapper_code):
    body = (
        f"(directory/'collector-status.json').write_text(json.dumps({raw()!r}))\n"
        f"sys.exit({wrapper_code})\n"
    )
    case = shell_case(tmp_path, body)
    helper = case[0] / "scripts" / SCRIPT.name
    helper.write_text(f"raise RuntimeError({CANARY!r})\n")
    proc = execute_shell(case)
    assert proc.returncode == (wrapper_code or 1)
    assert_ready(case, False)


def test_symlinked_public_summary_never_enables_upload(tmp_path):
    body = (
        f"(directory/'collector-status.json').write_text(json.dumps({raw()!r}))\n"
        f"pathlib.Path({str(PUBLIC_PATH)!r}).symlink_to(directory/'output-target')\n"
    )
    case = shell_case(tmp_path, body)
    assert execute_shell(case).returncode != 0
    assert_ready(case, False)


def test_private_directory_creation_failure_runs_no_wrapper_or_publication(tmp_path):
    case = shell_case(tmp_path, "raise AssertionError('fixture must not run')\n")
    case[2]["RUNNER_TEMP"] = str(tmp_path / CANARY / "missing")
    assert execute_shell(case).returncode != 0
    assert not case[3].exists()
    assert_ready(case, False)
    assert not (case[0] / PUBLIC_PATH).exists()


@pytest.mark.parametrize("code", [0, 17])
def test_public_directory_failure_stays_private_and_never_sets_ready(tmp_path, code):
    body = f"(directory/'collector-status.json').write_text(json.dumps({raw()!r}))\nsys.exit({code})\n"
    case = shell_case(tmp_path, body)
    (case[0] / PUBLIC_PATH.parent).write_text(CANARY)
    result = execute_shell(case)
    assert result.returncode != 0
    if case[3].exists() and code:
        assert result.returncode == code
    assert_ready(case, False)


@pytest.mark.parametrize("code", [0, 17])
def test_public_summary_redirection_failure_is_private_and_never_sets_ready(tmp_path, code):
    body = (
        f"pathlib.Path({str(PUBLIC_PATH)!r}).mkdir()\n"
        f"(directory/'collector-status.json').write_text(json.dumps({raw()!r}))\n"
        f"sys.exit({code})\n"
    )
    case = shell_case(tmp_path, body)
    result = execute_shell(case)
    assert result.returncode != 0
    assert case[3].exists()
    if code:
        assert result.returncode == code
    assert_ready(case, False)


@pytest.mark.parametrize("code", [0, 2, 17, 137])
def test_publication_flag_write_failure_cannot_hide_original_failure(tmp_path, code):
    body = (
        f"(directory/'collector-status.json').write_text(json.dumps({raw()!r}))\n"
        f"os.kill(os.getpid(),signal.SIGKILL) if {code} == 137 else None\n"
        f"sys.exit({code})\n"
    )
    case = shell_case(tmp_path, body)
    flag = Path(case[2]["GITHUB_ENV"])
    flag.mkdir()
    proc = execute_shell(case)
    assert proc.returncode != 0
    if code:
        assert proc.returncode == code
    assert flag.is_dir() and list(flag.iterdir()) == []
    assert_public(json.loads((case[0] / PUBLIC_PATH).read_text()))


@pytest.mark.parametrize("code", [0, 17])
@pytest.mark.parametrize(
    "size_output,size_code",
    [
        ("190\n", 42),
        ("", 42),
        (CANARY + "\n", 0),
        ("1+1\n", 0),
        ("18446744073709551806\n", 0),
        ("1025\n", 0),
    ],
)
def test_failed_or_invalid_byte_count_never_sets_ready(tmp_path, code, size_output, size_code):
    body = f"(directory/'collector-status.json').write_text(json.dumps({raw()!r}))\nsys.exit({code})\n"
    case = shell_case(tmp_path, body)
    commands = tmp_path / "commands"
    commands.mkdir()
    counter = commands / "wc"
    counter.write_text(
        f"#!/usr/bin/python3\nimport sys\nsys.stdout.write({size_output!r})\nsys.exit({size_code})\n"
    )
    counter.chmod(0o700)
    case[2]["PATH"] = str(commands) + os.pathsep + case[2]["PATH"]
    proc = execute_shell(case)
    assert proc.returncode == (code or 2)
    assert_ready(case, False)
    assert_public(json.loads((case[0] / PUBLIC_PATH).read_text()))


@pytest.mark.parametrize(
    "filename", ["private-shell-stderr.txt", "private-stdout.txt", "private-stderr.txt"]
)
def test_private_redirection_failure_stays_private_and_never_runs_wrapper(tmp_path, filename):
    case = shell_case(tmp_path, "raise AssertionError('fixture must not run')\n")
    private = tmp_path / CANARY
    private.mkdir(parents=True, mode=0o700)
    (private / filename).mkdir()
    # Inject only a deterministic mktemp fixture through PATH. The real shell
    # redirections remain unchanged and the collector path is still replaced.
    commands = tmp_path / "commands"
    commands.mkdir()
    fake_mktemp = commands / "mktemp"
    fake_mktemp.write_text("#!/bin/sh\nprintf '%s\\n' \"$FORGE_TEST_PRIVATE\"\n")
    fake_mktemp.chmod(0o700)
    assert "evidence=$(mktemp -d " in case[1]
    case[2]["PATH"] = str(commands) + os.pathsep + case[2]["PATH"]
    case[2]["FORGE_TEST_PRIVATE"] = str(private)
    assert execute_shell(case).returncode != 0
    assert not case[3].exists()
    if filename == "private-shell-stderr.txt":
        assert_ready(case, False)
        assert not (case[0] / PUBLIC_PATH).exists()
    else:
        assert_ready(case)
        assert_public(json.loads((case[0] / PUBLIC_PATH).read_text()), "UNKNOWN")
