"""Offline static-publication contracts; never execute the native collector."""

import ast
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/static_public_summary.py"
WORKFLOW = ROOT / ".github/workflows/strict-linux-admission.yml"
COLLECTOR = "scripts/strict_linux_admission.py"
PUBLIC_PATH = Path("forge-static-public/summary.json")
CANARY = "PRIVATE-static-/home/private-user/unit.service-PID-7654321"
SOURCE_CANARY = "cafebabefeedface0123456789abcdef01234567"
PUBLIC_KEYS = {"schema", "mode", "status", "boundary", "qualification", "raw_evidence", "reason"}
PUBLIC_REASONS = {"observation_complete", "collector_stop", "execution_failed", "unavailable"}


@pytest.fixture(scope="module")
def public():
    spec = importlib.util.spec_from_file_location("static_public_summary", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def raw(status="OBSERVED"):
    return {
        "schema": "forge-linux-admission-observation-v2",
        "status": status,
        "boundary": "NOT_RUN",
        "qualification": "NOT_RUN",
        "reason": CANARY,
        "identity": {
            "repository": CANARY,
            "run_id": CANARY,
            "workflow_ref": CANARY,
            "expected_source_sha": SOURCE_CANARY,
            "collector_sha256": SOURCE_CANARY,
        },
        "facts": {"uid": 7654321, "boot_id": CANARY, "source_cgroup": CANARY},
        "diagnostics": {"private": CANARY},
        "problems": [CANARY],
        "stderr": CANARY,
        CANARY: CANARY,
    }


def assert_public(value, expected=None, reason=None):
    assert set(value) == PUBLIC_KEYS
    assert value["schema"] == "forge-public-static-summary-v1"
    assert value["mode"] == "static"
    assert value["status"] in {"OBSERVED", "STOP", "UNKNOWN"}
    assert value["boundary"] == value["qualification"] == "NOT_RUN"
    assert value["raw_evidence"] == "NOT_PUBLISHED"
    assert type(value["reason"]) is str and value["reason"] in PUBLIC_REASONS
    serialized = json.dumps(value)
    assert all(secret not in serialized for secret in (CANARY, SOURCE_CANARY, "7654321"))
    if expected is not None:
        assert value["status"] == expected
    if reason is not None:
        assert value["reason"] == reason


@pytest.mark.parametrize("status", ["OBSERVED", "STOP"])
@pytest.mark.parametrize("code", [0, 2, 17, 137, 255])
def test_projection_derives_fixed_status_and_reason(public, status, code):
    expected = "OBSERVED" if status == "OBSERVED" and code == 0 else "STOP"
    reason = (
        "collector_stop" if status == "STOP" else "execution_failed" if code else "observation_complete"
    )
    assert_public(public.summary(raw(status), code), expected, reason)


@pytest.mark.parametrize("value", [None, [], True, 0, CANARY, {"status": "OBSERVED"}])
@pytest.mark.parametrize("code", [0, 17])
def test_unrecognized_receipt_is_fixed_unknown(public, value, code):
    assert_public(public.summary(value, code), "UNKNOWN", "unavailable")


@pytest.mark.parametrize("key", ["schema", "status", "boundary", "qualification"])
@pytest.mark.parametrize("value", [None, [], {}, True, 1, 0, CANARY, "UNKNOWN", "PASS"])
def test_malformed_required_fields_are_unknown(public, key, value):
    assert_public(public.summary({**raw(), key: value}, 0), "UNKNOWN", "unavailable")


@pytest.mark.parametrize("key", ["schema", "status", "boundary", "qualification"])
def test_missing_required_fields_are_unknown(public, key):
    value = raw()
    del value[key]
    assert_public(public.summary(value, 0), "UNKNOWN", "unavailable")


@pytest.mark.parametrize("code", [None, True, False, "0", [], {}, -1, 256, 1.0])
def test_diagnostic_code_requires_real_bounded_integer(public, code):
    assert_public(public.summary(raw(), code), "UNKNOWN", "unavailable")


@pytest.mark.parametrize("status,code", [("OBSERVED", 0), ("STOP", 0), ("STOP", 17), ("OBSERVED", 137)])
@pytest.mark.parametrize(
    "reason",
    [CANARY, None, [CANARY], {CANARY: CANARY}, "authorization_required", "observation_complete"],
)
def test_raw_reasons_and_metadata_are_never_copied(public, status, code, reason):
    result = public.summary({**raw(status), "reason": reason}, code)
    assert result == public.summary(raw(status), code)
    assert_public(result)


def test_projection_does_not_import_or_run_collector_and_size_matches_source(public):
    source = SCRIPT.read_text()
    imports = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module)
    assert set(imports) <= {"json", "os", "stat", "sys"}
    collector_source = (ROOT / COLLECTOR).read_text()
    match = re.search(r"(?m)^MAX_RECEIPT\s*=\s*(\d+)\s*$", collector_source)
    assert match and public.MAX_INPUT == int(match[1]) == 16384


def invoke_summary(path, code="0"):
    return subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(SCRIPT), str(path), code],
        capture_output=True,
        timeout=3,
    )


@pytest.mark.parametrize("status", ["OBSERVED", "STOP"])
@pytest.mark.parametrize("code", [0, 2, 17, 137])
def test_cli_emits_one_bounded_summary_and_only_observed_is_success(tmp_path, status, code):
    path = tmp_path / "private.json"
    path.write_text(json.dumps(raw(status)))
    proc = invoke_summary(path, str(code))
    expected = "OBSERVED" if status == "OBSERVED" and code == 0 else "STOP"
    assert proc.returncode == (0 if expected == "OBSERVED" else 2)
    assert proc.stderr == b"" and proc.stdout.count(b"\n") == 1
    assert len(proc.stdout) < 1024
    assert_public(json.loads(proc.stdout), expected)
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
        "nan",
        "infinity",
        "negative_infinity",
        "overflow",
        "negative_overflow",
        "float",
        "underflow",
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
    elif kind in {
        "nan",
        "infinity",
        "negative_infinity",
        "overflow",
        "negative_overflow",
        "float",
        "underflow",
    }:
        token = {
            "nan": "NaN",
            "infinity": "Infinity",
            "negative_infinity": "-Infinity",
            "overflow": "1e400",
            "negative_overflow": "-1e400",
            "float": "1.0",
            "underflow": "1e-400",
        }[kind]
        path.write_text(json.dumps(raw())[:-1] + ',"private":' + token + "}")
    proc = invoke_summary(path)
    assert proc.returncode == 2 and proc.stderr == b""
    assert len(proc.stdout) < 1024
    assert_public(json.loads(proc.stdout), "UNKNOWN", "unavailable")
    assert str(path).encode() not in proc.stdout


@pytest.mark.parametrize("code", ["-1", "256", "0.0", CANARY, "", "True"])
def test_invalid_cli_code_is_fixed_unknown(tmp_path, code):
    path = tmp_path / "private.json"
    path.write_text(json.dumps(raw()))
    proc = invoke_summary(path, code)
    assert proc.returncode == 2 and proc.stderr == b""
    assert_public(json.loads(proc.stdout), "UNKNOWN", "unavailable")


@pytest.mark.parametrize("extra", [[], [CANARY], [CANARY, "0", CANARY]])
def test_wrong_cli_arity_does_not_echo_arguments(extra):
    proc = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(SCRIPT), *extra],
        capture_output=True,
        timeout=3,
    )
    assert proc.returncode == 2 and proc.stderr == b""
    assert_public(json.loads(proc.stdout), "UNKNOWN", "unavailable")


@pytest.mark.parametrize("size", [16384, 16385])
def test_receipt_size_limit_for_valid_json(tmp_path, public, size):
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


def test_reader_uses_nonblocking_no_follow_open_and_closes_descriptor(tmp_path, public, monkeypatch):
    path = tmp_path / "private.json"
    path.write_text(json.dumps(raw()))
    real_open, real_close = public.os.open, public.os.close
    opened, closed = [], []

    def tracked_open(name, flags):
        fd = real_open(name, flags)
        opened.append((fd, flags))
        return fd

    def tracked_close(fd):
        closed.append(fd)
        return real_close(fd)

    monkeypatch.setattr(public.os, "open", tracked_open)
    monkeypatch.setattr(public.os, "close", tracked_close)
    assert public.read_receipt(path) == raw()
    assert len(opened) == 1
    fd, flags = opened[0]
    assert flags & os.O_NOFOLLOW and flags & os.O_NONBLOCK
    assert flags & os.O_ACCMODE == os.O_RDONLY
    assert closed == [fd]


@pytest.mark.parametrize("field", ["st_size", "st_mtime_ns", "st_ctime_ns"])
def test_reader_rejects_changes_during_read_and_closes_fd(tmp_path, public, monkeypatch, field):
    path = tmp_path / "private.json"
    path.write_text(json.dumps(raw()))
    real_fstat, real_close = public.os.fstat, public.os.close
    calls, closed = [], []

    def changed_stat(fd):
        observed = real_fstat(fd)
        calls.append(fd)
        fields = {
            key: getattr(observed, key) for key in ("st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
        }
        if len(calls) == 2:
            fields[field] += 1
        return SimpleNamespace(**fields)

    def tracked_close(fd):
        closed.append(fd)
        return real_close(fd)

    monkeypatch.setattr(public.os, "fstat", changed_stat)
    monkeypatch.setattr(public.os, "close", tracked_close)
    with pytest.raises(ValueError):
        public.read_receipt(path)
    assert len(calls) == 2 and calls[0] == calls[1]
    assert closed == [calls[0]]


def workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def observation_step():
    matches = [
        step
        for step in workflow()["jobs"]["observe"]["steps"]
        if "static_public_summary.py" in step.get("run", "")
    ]
    assert len(matches) == 1
    return matches[0]


def test_workflow_retains_read_only_source_route_and_exact_public_artifact():
    value = workflow()
    assert set(value["on"]) == {"pull_request", "workflow_dispatch"}
    assert value["permissions"] == {"contents": "read"}
    assert set(value["jobs"]) == {"observe", "fixed-metadata"}
    assert value["on"]["workflow_dispatch"]["inputs"]["fixed_metadata_read"]["default"] is False
    assert value["on"]["workflow_dispatch"]["inputs"]["expected_source_sha"]["default"] == ""
    assert value["jobs"]["fixed-metadata"]["if"] == (
        "${{ github.event_name == 'workflow_dispatch' && inputs.fixed_metadata_read }}"
    )
    job = value["jobs"]["observe"]
    assert "github.event_name == 'workflow_dispatch'" in job["if"]
    assert "github.event.pull_request.head.repo.full_name == github.repository" in job["if"]
    assert job["runs-on"] == "ubuntu-24.04" and job["timeout-minutes"] == 3
    checkout = [step for step in job["steps"] if "actions/checkout@" in step.get("uses", "")]
    assert len(checkout) == 1 and checkout[0]["with"]["persist-credentials"] is False
    assert checkout[0]["with"]["ref"] == "${{ github.event.pull_request.head.sha || github.sha }}"
    uploads = [step for step in job["steps"] if "actions/upload-artifact@" in step.get("uses", "")]
    assert len(uploads) == 1
    assert uploads[0]["with"]["path"] == str(PUBLIC_PATH)
    assert uploads[0]["if"] == "${{ always() && env.ADMISSION_PUBLIC_READY == 'true' }}"
    assert uploads[0]["with"]["if-no-files-found"] == "error"
    paths = value["on"]["pull_request"]["paths"]
    assert str(SCRIPT.relative_to(ROOT)) in paths
    assert str(Path(__file__).relative_to(ROOT)) in paths
    serialized = json.dumps(value)
    assert "${{ secrets." not in serialized and "pull_request_target" not in serialized
    runs = "\n".join(step.get("run", "") for step in job["steps"])
    assert "ADMISSION_EVIDENCE=" not in runs
    assert not re.search(r"(?m)^\s*(?:sudo|systemctl|systemd-run|pip|apt-get)\b", runs)
    step = observation_step()
    assert step["env"]["CONTEXT_SOURCE_SHA"] == "${{ github.event.pull_request.head.sha || github.sha }}"
    assert step["run"].startswith("exec 2>/dev/null\n")
    assert "set +x" in step["run"] and "diagnostic_code=$?" in step["run"]


def shell_case(tmp_path, body, *, with_helper=True):
    """Execute the actual YAML shell with only a harmless fixture collector."""
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
        "output=pathlib.Path(args[args.index('--output')+1])\n"
        "directory=output.parent\n"
        f"print({CANARY!r},flush=True)\n"
        f"print({CANARY!r},file=sys.stderr,flush=True)\n" + body
    )
    step = observation_step()
    run = step["run"]
    assert run.count(COLLECTOR) == 1
    run = run.replace(COLLECTOR, str(fixture))
    assert COLLECTOR not in run and str(fixture) in run
    assert "strict_user_service_probe.py" not in run
    assert "run_user_service_observation.py" not in run
    assert not (scripts / Path(COLLECTOR).name).exists()
    env = {
        **os.environ,
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_ENV": str(tmp_path / "github-env"),
    }
    for key in step.get("env", {}):
        env[key] = SOURCE_CANARY if key == "CONTEXT_SOURCE_SHA" else CANARY
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
    assert env_file.read_text() == "ADMISSION_PUBLIC_READY=true\n"
    assert len(list((case[0] / PUBLIC_PATH.parent).iterdir())) == 1
    result_path = case[0] / PUBLIC_PATH
    assert result_path.stat().st_mode & 0o077 == 0
    assert len(result_path.read_bytes()) < 1024
    assert_public(json.loads(result_path.read_bytes()))


def receipt_body(status="OBSERVED", code=0):
    return (
        f"output.write_text(json.dumps({raw(status)!r}))\n"
        f"os.kill(os.getpid(),signal.SIGKILL) if {code} == 137 else None\n"
        f"sys.exit({code})\n"
    )


@pytest.mark.parametrize("code", [0, 2, 17, 137])
@pytest.mark.parametrize("status", ["OBSERVED", "STOP"])
def test_actual_shell_contains_private_streams_and_preserves_collector_exit(tmp_path, status, code):
    case = shell_case(tmp_path, receipt_body(status, code))
    proc = execute_shell(case)
    assert proc.returncode == (code or (0 if status == "OBSERVED" else 2))
    assert_ready(case)
    expected = "OBSERVED" if status == "OBSERVED" and code == 0 else "STOP"
    assert_public(json.loads((case[0] / PUBLIC_PATH).read_text()), expected)
    args = json.loads(case[3].read_text())
    private_receipt = Path(args[args.index("--output") + 1])
    assert private_receipt.is_file() and CANARY in private_receipt.read_text()
    assert private_receipt.parent != case[0] / PUBLIC_PATH.parent
    assert private_receipt.parent.stat().st_mode & 0o077 == 0
    for path in private_receipt.parent.iterdir():
        assert path.stat().st_mode & 0o077 == 0
    if code == 137:
        assert any(b"Killed" in path.read_bytes() for path in tmp_path.rglob("private*stderr.txt"))


@pytest.mark.parametrize(
    "body,code",
    [
        (f"raise RuntimeError({CANARY!r})\n", 1),
        ("os.kill(os.getpid(),signal.SIGKILL)\n", 137),
        ("sys.exit(17)\n", 17),
        ("sys.exit(0)\n", 0),
        (f"output.write_text({CANARY!r})\nsys.exit(2)\n", 2),
        ('output.write_text(\'{"status":"OBSERVED"}\')\n', 0),
    ],
)
def test_actual_shell_missing_or_malformed_receipt_is_private_unknown(tmp_path, body, code):
    case = shell_case(tmp_path, body)
    assert execute_shell(case).returncode == (code or 2)
    assert_ready(case)
    assert_public(json.loads((case[0] / PUBLIC_PATH).read_text()), "UNKNOWN", "unavailable")


def test_source_and_metadata_are_private_separate_arguments(tmp_path):
    case = shell_case(tmp_path, receipt_body())
    assert execute_shell(case).returncode == 0
    args = json.loads(case[3].read_text())
    assert args[args.index("--expected-source-sha") + 1] == SOURCE_CANARY
    for argument in (
        "--repository",
        "--run-id",
        "--run-attempt",
        "--job-name",
        "--workflow-ref",
        "--workflow-sha",
    ):
        assert args.count(argument) == 1 and args[args.index(argument) + 1] == CANARY
    assert_ready(case)


@pytest.mark.parametrize("code", [0, 2, 17, 137])
def test_missing_helper_never_masks_collector_failure_or_enables_upload(tmp_path, code):
    case = shell_case(tmp_path, receipt_body(code=code), with_helper=False)
    result = execute_shell(case)
    if code:
        assert result.returncode == code
    else:
        assert result.returncode != 0
    assert_ready(case, False)
    output = case[0] / PUBLIC_PATH
    assert not output.exists() or output.read_bytes() == b""


@pytest.mark.parametrize("collector_code", [0, 17])
@pytest.mark.parametrize("helper_code", [0, 2])
@pytest.mark.parametrize("size", [0, 1025])
def test_empty_or_oversized_helper_output_never_enables_upload(
    tmp_path, collector_code, helper_code, size
):
    case = shell_case(tmp_path, receipt_body(code=collector_code))
    helper = case[0] / "scripts" / SCRIPT.name
    helper.write_text(f"import sys\nsys.stdout.write('x'*{size})\nsys.exit({helper_code})\n")
    proc = execute_shell(case)
    assert proc.returncode == (collector_code or 2)
    assert_ready(case, False)


@pytest.mark.parametrize("code", [0, 17])
def test_helper_exception_is_private_and_never_enables_upload(tmp_path, code):
    case = shell_case(tmp_path, receipt_body(code=code))
    helper = case[0] / "scripts" / SCRIPT.name
    helper.write_text(f"raise RuntimeError({CANARY!r})\n")
    proc = execute_shell(case)
    assert proc.returncode == (code or 1)
    assert_ready(case, False)


def test_symlinked_public_summary_never_enables_upload(tmp_path):
    body = (
        f"output.write_text(json.dumps({raw()!r}))\n"
        f"pathlib.Path({str(PUBLIC_PATH)!r}).symlink_to(directory/'output-target')\n"
    )
    case = shell_case(tmp_path, body)
    assert execute_shell(case).returncode != 0
    assert_ready(case, False)


def test_private_directory_failure_runs_no_collector_or_publication(tmp_path):
    case = shell_case(tmp_path, "raise AssertionError('fixture must not run')\n")
    case[2]["RUNNER_TEMP"] = str(tmp_path / CANARY / "missing")
    assert execute_shell(case).returncode != 0
    assert not case[3].exists()
    assert_ready(case, False)
    assert not (case[0] / PUBLIC_PATH).exists()


@pytest.mark.parametrize("code", [0, 17])
def test_public_directory_failure_is_private_and_never_sets_ready(tmp_path, code):
    case = shell_case(tmp_path, receipt_body(code=code))
    (case[0] / PUBLIC_PATH.parent).write_text(CANARY)
    result = execute_shell(case)
    assert result.returncode != 0
    if case[3].exists() and code:
        assert result.returncode == code
    assert_ready(case, False)


@pytest.mark.parametrize("code", [0, 17])
def test_public_summary_redirection_failure_never_sets_ready(tmp_path, code):
    body = f"pathlib.Path({str(PUBLIC_PATH)!r}).mkdir()\n" + receipt_body(code=code)
    case = shell_case(tmp_path, body)
    result = execute_shell(case)
    assert result.returncode != 0 and case[3].exists()
    if code:
        assert result.returncode == code
    assert_ready(case, False)


@pytest.mark.parametrize("code", [0, 2, 17, 137])
def test_ready_flag_write_failure_preserves_original_failure(tmp_path, code):
    case = shell_case(tmp_path, receipt_body(code=code))
    flag = Path(case[2]["GITHUB_ENV"])
    flag.mkdir()
    proc = execute_shell(case)
    assert proc.returncode != 0
    if code:
        assert proc.returncode == code
    assert flag.is_dir() and list(flag.iterdir()) == []
    assert_public(json.loads((case[0] / PUBLIC_PATH).read_text()))


@pytest.mark.parametrize(
    "filename", ["private-shell-stderr.txt", "private-stdout.txt", "private-stderr.txt"]
)
def test_private_redirection_failure_never_runs_collector(tmp_path, filename):
    case = shell_case(tmp_path, "raise AssertionError('fixture must not run')\n")
    private = tmp_path / "private-canary"
    private.mkdir(parents=True, mode=0o700)
    (private / filename).mkdir()
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
        assert_public(json.loads((case[0] / PUBLIC_PATH).read_text()), "UNKNOWN", "unavailable")


@pytest.mark.parametrize("collector_code", [0, 17])
@pytest.mark.parametrize("failure", ["exception", "kill"])
def test_partial_helper_output_with_failure_never_enables_upload(tmp_path, collector_code, failure):
    case = shell_case(tmp_path, receipt_body(code=collector_code))
    helper = case[0] / "scripts" / SCRIPT.name
    action = (
        f"raise RuntimeError({CANARY!r})\n"
        if failure == "exception"
        else "os.kill(os.getpid(),signal.SIGKILL)\n"
    )
    helper.write_text(
        "import os,signal,sys\n"
        f"print({CANARY!r},flush=True)\n"
        f"print({CANARY!r},file=sys.stderr,flush=True)\n" + action
    )
    proc = execute_shell(case)
    helper_code = 1 if failure == "exception" else 137
    assert proc.returncode == (collector_code or helper_code)
    assert_ready(case, False)
    assert CANARY in (case[0] / PUBLIC_PATH).read_text()


@pytest.mark.parametrize("status", ["OBSERVED", "STOP"])
def test_real_validated_collector_receipt_flows_through_projection(
    tmp_path, public, monkeypatch, capsys, status
):
    # Only the serialization/validation path is real. All host collection is
    # replaced before main can run; no process, service or probe is invoked.
    from scripts import strict_linux_admission as admission

    root = admission.delegated_root(1001)
    identity = {
        "repository": "example/private-project",
        "run_id": "7654321",
        "run_attempt": 1,
        "job_name": "observe",
        "workflow_ref": "example/private-project/.github/workflows/static.yml@refs/heads/main",
        "workflow_sha": "a" * 40,
        "expected_source_sha": SOURCE_CANARY,
        "collector_sha256": "d" * 64,
        "authentication": "UNVERIFIED_DECLARED_CONTEXT",
    }
    facts = {
        "uid": 1001,
        "gid": 1001,
        "kernel": "6.11.0-private-fixture",
        "boot_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "cgroup2_mount": status == "OBSERVED",
        "source_cgroup": root + "/app.slice/private-worker.service",
        "delegated_root": root,
        "controllers": ["memory", "pids"],
        "subtree_control": ["memory", "pids"],
        "manager": {"active_state": "active", "control_group": root, "delegate": True},
        "common_ancestor": root,
        "access": {
            "delegated_directory": True,
            "destination_procs": True,
            "common_ancestor_procs": True,
        },
        "bwrap": {
            "path": "/usr/bin/bwrap",
            "realpath": "/usr/bin/bwrap",
            "uid": 0,
            "gid": 0,
            "mode": 0o100755,
            "size": 1234,
            "sha256": "e" * 64,
            "version": None,
        },
        "apparmor": {
            "enabled": True,
            "restrict_userns": 1,
            "process_profile": "confined",
            "bwrap_profiles": ["bwrap"],
        },
    }
    receipt = {
        "schema": "forge-linux-admission-observation-v2",
        "identity": identity,
        "facts": facts,
        "diagnostics": {
            "bwrap": {
                "usr_bin": {"reason": "read_success", "errno": None},
                "bin": {"reason": "not_attempted", "errno": None},
            },
            "apparmor_profiles": {"reason": "read_success", "errno": None},
        },
        "status": status,
        "problems": admission.derive_problems(identity, facts),
        "boundary": "NOT_RUN",
        "qualification": "NOT_RUN",
    }
    assert admission.validate(receipt) == receipt
    calls = []

    def fixture_collection(args):
        calls.append(args)
        return receipt

    monkeypatch.setattr(admission, "collect", fixture_collection)
    path = tmp_path / "private-observation.json"
    argv = []
    for field in (
        "repository",
        "run_id",
        "run_attempt",
        "job_name",
        "workflow_ref",
        "workflow_sha",
        "expected_source_sha",
    ):
        argv.extend(["--" + field.replace("_", "-"), str(identity[field])])
    argv.extend(["--output", str(path)])
    diagnostic_code = admission.main(argv)
    assert len(calls) == 1
    assert calls[0]["expected_source_sha"] == SOURCE_CANARY
    assert diagnostic_code == (0 if status == "OBSERVED" else 2)
    assert capsys.readouterr().err == ""
    assert admission.parse(path.read_bytes()) == receipt
    result = public.summary(public.read_receipt(path), diagnostic_code)
    assert_public(result, status, "observation_complete" if status == "OBSERVED" else "collector_stop")
    assert "private-project" not in json.dumps(result)
    assert "private-worker" not in json.dumps(result)
    proc = invoke_summary(path, str(diagnostic_code))
    assert proc.returncode == diagnostic_code and proc.stderr == b""
    assert json.loads(proc.stdout) == result


@pytest.mark.parametrize("code", [0, 17])
def test_summary_stderr_redirection_failure_never_sets_ready(tmp_path, code):
    body = "(directory/'private-summary-stderr.txt').mkdir()\n" + receipt_body(code=code)
    case = shell_case(tmp_path, body)
    proc = execute_shell(case)
    assert proc.returncode != 0 and case[3].exists()
    if code:
        assert proc.returncode == code
    assert_ready(case, False)
    assert (case[0] / PUBLIC_PATH).read_bytes() == b""


@pytest.mark.parametrize("code", [0, 17])
@pytest.mark.parametrize(
    "output,counter_code",
    [
        ("190", 42),
        ("", 42),
        ("", 0),
        (CANARY, 0),
        ("1025", 0),
        ("999999999999999999999999", 0),
        ("1+1", 0),
        ("-1", 0),
    ],
)
def test_invalid_or_failed_size_check_never_sets_ready(tmp_path, code, output, counter_code):
    case = shell_case(tmp_path, receipt_body(code=code))
    commands = tmp_path / "commands"
    commands.mkdir()
    fake_wc = commands / "wc"
    fake_wc.write_text('#!/bin/sh\nprintf "%s\\n" "$FORGE_TEST_SIZE"\nexit "$FORGE_TEST_WC_CODE"\n')
    fake_wc.chmod(0o700)
    case[2]["PATH"] = str(commands) + os.pathsep + case[2]["PATH"]
    case[2]["FORGE_TEST_SIZE"] = output
    case[2]["FORGE_TEST_WC_CODE"] = str(counter_code)
    proc = execute_shell(case)
    assert proc.returncode != 0
    if code:
        assert proc.returncode == code
    assert_ready(case, False)
