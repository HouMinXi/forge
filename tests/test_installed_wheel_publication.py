"""Offline packaging publication tests; setup and qualification are harmless stubs."""

import ast
import importlib.util
import itertools
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/installed_wheel_public_summary.py"
WORKFLOW = ROOT / ".github/workflows/installed-wheel.yml"
PUBLIC_PATH = Path("forge-installed-wheel-public/summary.json")
CANARY = "PRIVATE-wheel-/home/private-user/unit.service-PID-7654321"
HASH_CANARY = "cafebabefeedface0123456789abcdef0123456789abcdef0123456789abcdef0123"
OUTCOMES = ("success", "failure", "cancelled", "skipped")
JOB_STATUSES = ("success", "failure", "cancelled")


@pytest.fixture(scope="module")
def public():
    spec = importlib.util.spec_from_file_location("installed_wheel_public_summary", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expected(setup="success", qualification="success", job="success"):
    status, reason = "UNKNOWN", "unavailable"
    if setup in OUTCOMES and qualification in OUTCOMES and job in JOB_STATUSES:
        if {setup, qualification, job} & {"failure", "cancelled"}:
            status, reason = "STOP", "execution_failed"
        elif setup == qualification == job == "success":
            status, reason = "PASS", "controls_complete"
        else:
            reason = "not_completed"
    return {
        "schema": "forge-public-installed-wheel-summary-v1",
        "mode": "installed-wheel",
        "scope": "PARTIAL_RUNTIME_ONLY",
        "status": status,
        "reason": reason,
        "raw_evidence": "NOT_PUBLISHED",
    }


def assert_public(value, outcomes=("success", "success", "success")):
    assert value == expected(*outcomes)
    serialized = json.dumps(value)
    assert all(secret not in serialized for secret in (CANARY, HASH_CANARY, "7654321"))
    assert len(serialized) < 1024


@pytest.mark.parametrize("outcomes", list(itertools.product(OUTCOMES, OUTCOMES, JOB_STATUSES)))
def test_projection_is_fixed_and_does_not_promote_partial_or_failed_outcomes(public, outcomes):
    assert_public(public.summary(*outcomes), outcomes)


@pytest.mark.parametrize("index", range(3))
@pytest.mark.parametrize("value", [None, [], {}, True, 0, 1.0, CANARY, "PASS", "", "Success"])
def test_invalid_input_is_fixed_unknown(public, index, value):
    outcomes = ["success"] * 3
    outcomes[index] = value
    assert public.summary(*outcomes) == expected(None, None, None)


def test_helper_is_stdlib_only_and_never_reads_raw_evidence():
    tree = ast.parse(SCRIPT.read_text())
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module)
    assert set(imports) <= {"json", "sys"}
    assert "qualify_installed_wheel" not in SCRIPT.read_text()


@pytest.mark.parametrize(
    "args",
    [[], [CANARY], ["success"] * 2, ["success"] * 4, [CANARY] * 3, ["success"] * 3],
)
def test_cli_arity_and_sensitive_arguments_never_escape(args):
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(SCRIPT), *args],
        capture_output=True,
        timeout=15,
    )
    want = expected() if args == ["success"] * 3 else expected(None, None, None)
    assert result.returncode == (0 if want["status"] == "PASS" else 2)
    assert result.stderr == b"" and result.stdout.count(b"\n") == 1
    assert json.loads(result.stdout) == want
    assert CANARY.encode() not in result.stdout


def workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def step(identifier):
    return next(
        item for item in workflow()["jobs"]["installed-wheel"]["steps"] if item.get("id") == identifier
    )


def test_workflow_preserves_events_pins_build_and_failure_semantics():
    value = workflow()
    assert set(value["on"]) == {"pull_request", "push", "workflow_dispatch"}
    assert value["on"]["pull_request"] == value["on"]["push"] == {"branches": ["main"]}
    assert value["permissions"] == {"contents": "read"}
    assert set(value["jobs"]) == {"installed-wheel"}
    job = value["jobs"]["installed-wheel"]
    assert job["runs-on"] == "ubuntu-24.04" and job["timeout-minutes"] == 15
    assert job["steps"][0]["with"]["persist-credentials"] is False
    assert job["steps"][1]["with"]["python-version"] == "3.12"
    assert "continue-on-error" not in json.dumps(value)
    assert "if" not in step("provision") and "if" not in step("qualify")
    for identifier in ("provision", "qualify", "wheel_release", "public_summary"):
        item = step(identifier)
        assert item["shell"] == "bash"
        assert item["run"].startswith("exec >/dev/null 2>&1\nset +x\nset -euo pipefail\n")
        assert "umask 077" in item["run"]
        assert 'exec >"$private/commands.log" 2>&1' in item["run"]
    provision = step("provision")["run"]
    assert "python -m pip wheel --index-url https://pypi.org/simple" in provision
    for dependency in (
        "setuptools>=77",
        "wheel",
        "pytest==9.2.0",
        "pyyaml>=6.0",
        "unidiff>=0.7.5,<0.8.0",
    ):
        assert dependency in provision
    qualify = step("qualify")["run"]
    assert qualify.count("python scripts/qualify_installed_wheel.py") == 1
    assert '--wheelhouse "$RUNNER_TEMP/forge-wheelhouse"' in qualify
    assert '--output "$private/qualification"' in qualify
    summary = step("public_summary")
    assert summary["if"] == "${{ always() }}" and summary["timeout-minutes"] == 1
    assert summary["env"] == {
        "SETUP_OUTCOME": "${{ steps.provision.outcome }}",
        "QUALIFICATION_OUTCOME": "${{ steps.qualify.outcome }}",
        "JOB_STATUS": "${{ job.status }}",
    }
    uploads = [item for item in job["steps"] if "actions/upload-artifact@" in item.get("uses", "")]
    assert len(uploads) == 2
    upload = next(item for item in uploads if item["with"]["name"] == "installed-wheel-summary")
    assert upload["if"] == "${{ always() && steps.public_summary.outcome == 'success' }}"
    assert upload["with"] == {
        "name": "installed-wheel-summary",
        "path": str(PUBLIC_PATH),
        "if-no-files-found": "error",
        "retention-days": 14,
    }


def shell_case(tmp_path, *, outcomes=("success", "success", "success"), body="", helper=None):
    workspace = tmp_path / "workspace"
    scripts = workspace / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(SCRIPT, scripts / SCRIPT.name)
    if helper is not None:
        (scripts / SCRIPT.name).write_text(helper)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    marker = tmp_path / "stub-argv.json"
    stub = binaries / "python"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json,os,pathlib,signal,subprocess,sys,time\n"
        f"pathlib.Path({str(marker)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        f"print({CANARY!r},flush=True)\n"
        f"print({HASH_CANARY!r},file=sys.stderr,flush=True)\n" + body
    )
    stub.chmod(0o700)
    env = {
        **os.environ,
        "PATH": str(binaries) + os.pathsep + os.environ.get("PATH", os.defpath),
        "RUNNER_TEMP": str(tmp_path),
        "SETUP_OUTCOME": outcomes[0],
        "QUALIFICATION_OUTCOME": outcomes[1],
        "JOB_STATUS": outcomes[2],
    }
    return workspace, env, marker


def execute_shell(case, identifier="public_summary"):
    workspace, env, _ = case
    shell = workspace / "workflow-step.sh"
    shell.write_text(step(identifier)["run"])
    proc = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-e", "-o", "pipefail", str(shell)],
        cwd=workspace,
        env=env,
        capture_output=True,
        timeout=15,
    )
    assert proc.stdout == proc.stderr == b""
    return proc


def assert_private_logs(case):
    logs = list(Path(case[1]["RUNNER_TEMP"]).glob("forge-wheel-*/commands.log"))
    assert logs
    assert any(CANARY in path.read_text() for path in logs)
    assert any(HASH_CANARY in path.read_text() for path in logs)
    for path in logs:
        assert path.stat().st_mode & 0o077 == 0
        assert path.parent.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("outcomes", list(itertools.product(OUTCOMES, OUTCOMES, JOB_STATUSES)))
def test_actual_summary_shell_publishes_only_fixed_outcome_envelope(tmp_path, outcomes):
    case = shell_case(tmp_path, outcomes=outcomes)
    assert execute_shell(case).returncode == 0
    path = case[0] / PUBLIC_PATH
    assert_public(json.loads(path.read_bytes()), outcomes)
    assert path.stat().st_mode & 0o077 == path.parent.stat().st_mode & 0o077 == 0
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("identifier", ["provision", "qualify"])
@pytest.mark.parametrize("code", [0, 1, 2, 17, 124, 137])
def test_actual_setup_and_harness_shells_preserve_exit_and_hide_all_streams(tmp_path, identifier, code):
    body = "os.kill(os.getpid(),signal.SIGKILL)\n" if code == 137 else f"sys.exit({code})\n"
    case = shell_case(tmp_path, body=body)
    assert execute_shell(case, identifier).returncode == code
    assert_private_logs(case)
    assert not (case[0] / PUBLIC_PATH).exists()
    args = json.loads(case[2].read_text())
    if identifier == "provision":
        assert args[:4] == ["-m", "pip", "wheel", "--index-url"]
    else:
        assert args[0] == "scripts/qualify_installed_wheel.py"
        assert args[args.index("--wheelhouse") + 1] == case[1]["RUNNER_TEMP"] + "/forge-wheelhouse"
        output = Path(args[args.index("--output") + 1])
        assert not output.is_relative_to(case[0])
        assert not output.exists()


@pytest.mark.parametrize("identifier", ["provision", "qualify"])
@pytest.mark.parametrize(
    "body",
    [f"raise RuntimeError({CANARY!r})\n", f"raise subprocess.TimeoutExpired({CANARY!r},1)\n"],
)
def test_exceptions_and_harness_timeouts_remain_private_failures(tmp_path, identifier, body):
    case = shell_case(tmp_path, body=body)
    assert execute_shell(case, identifier).returncode == 1
    assert_private_logs(case)
    case[1].update(SETUP_OUTCOME="failure" if identifier == "provision" else "success")
    case[1].update(QUALIFICATION_OUTCOME="skipped" if identifier == "provision" else "failure")
    case[1]["JOB_STATUS"] = "failure"
    assert execute_shell(case).returncode == 0
    assert json.loads((case[0] / PUBLIC_PATH).read_text())["status"] == "STOP"
    # The original step result above stays failed; there is no continue-on-error.


def fixture_helper(payload, code=0):
    return (
        "import sys\n"
        f"print({CANARY!r},file=sys.stderr,flush=True)\n"
        f"sys.stdout.write({payload!r})\n"
        f"sys.exit({code})\n"
    )


@pytest.mark.parametrize(
    "payload",
    [
        "",
        CANARY,
        "{}",
        "[]",
        "null",
        "true",
        "x" * 1025,
        json.dumps(expected())[:-1],
        json.dumps(expected()) + CANARY,
        json.dumps({**expected(), "path": CANARY}),
        json.dumps({**expected(), "status": CANARY}),
        json.dumps({**expected(), "reason": HASH_CANARY}),
        json.dumps(expected())[:-1] + ',"status":"PASS"}',
        json.dumps(expected())[:-1] + ',"private":{"x":1,"x":2}}',
        json.dumps(expected())[:-1] + ',"private":NaN}',
        json.dumps(expected())[:-1] + ',"private":1e400}',
        json.dumps({**expected(), "scope": True}),
        "[" * 500 + "]" * 500,
    ],
)
def test_actual_gate_rejects_partial_malformed_and_private_helper_output(tmp_path, payload):
    case = shell_case(tmp_path, helper=fixture_helper(payload))
    assert execute_shell(case).returncode != 0
    assert not (case[0] / PUBLIC_PATH).exists()


@pytest.mark.parametrize("helper_code", [1, 2, 17, 124, 137])
def test_helper_failure_after_valid_pass_cannot_publish(tmp_path, helper_code):
    helper = fixture_helper(json.dumps(expected()), helper_code)
    if helper_code == 137:
        helper = helper.replace("sys.exit(137)", "import os,signal; os.kill(os.getpid(),signal.SIGKILL)")
    case = shell_case(tmp_path, helper=helper)
    assert execute_shell(case).returncode != 0
    assert not (case[0] / PUBLIC_PATH).exists()


@pytest.mark.parametrize(
    "outcomes",
    [
        ("success", "failure", "failure"),
        ("success", "cancelled", "cancelled"),
        ("success", "skipped", "success"),
    ],
)
@pytest.mark.parametrize("helper_code", [0, 2])
def test_fake_success_cannot_override_runner_failure_cancellation_or_missing_run(
    tmp_path, outcomes, helper_code
):
    case = shell_case(
        tmp_path, outcomes=outcomes, helper=fixture_helper(json.dumps(expected()), helper_code)
    )
    assert execute_shell(case).returncode != 0
    assert not (case[0] / PUBLIC_PATH).exists()


@pytest.mark.parametrize(
    "kind", ["missing", "exception", "symlink", "fifo", "directory", "invalid_utf8"]
)
def test_helper_and_candidate_failures_are_private_and_never_publish(tmp_path, kind):
    case = shell_case(tmp_path)
    helper = case[0] / "scripts" / SCRIPT.name
    if kind == "missing":
        helper.unlink()
    elif kind == "exception":
        helper.write_text(f"raise RuntimeError({CANARY!r})\n")
    elif kind == "invalid_utf8":
        helper.write_text("import os; os.write(1,b'\\xff')\n")
    else:
        helper.write_text(
            "import os,pathlib\n"
            "path=pathlib.Path(os.readlink('/proc/self/fd/1'))\n"
            "path.unlink()\n"
            + {
                "symlink": f"path.symlink_to({CANARY!r})\n",
                "fifo": "os.mkfifo(path)\n",
                "directory": "path.mkdir()\n",
            }[kind]
        )
    assert execute_shell(case).returncode != 0
    assert not (case[0] / PUBLIC_PATH).exists()


@pytest.mark.parametrize("kind", ["directory", "symlink", "stale_pass", "file"])
def test_preexisting_public_paths_fail_closed_instead_of_reusing_stale_success(tmp_path, kind):
    case = shell_case(tmp_path)
    parent = case[0] / PUBLIC_PATH.parent
    if kind == "symlink":
        parent.symlink_to(tmp_path, target_is_directory=True)
    elif kind == "file":
        parent.write_text(CANARY)
    else:
        parent.mkdir()
        if kind == "stale_pass":
            (case[0] / PUBLIC_PATH).write_text(json.dumps(expected()))
    assert execute_shell(case).returncode != 0


@pytest.mark.parametrize("identifier", ["provision", "qualify", "public_summary"])
def test_early_shell_redirection_failure_has_no_public_diagnostics(tmp_path, identifier):
    case = shell_case(tmp_path)
    case[1]["RUNNER_TEMP"] = str(tmp_path / CANARY / "missing")
    assert execute_shell(case, identifier).returncode != 0
    assert not case[2].exists()
    assert not (case[0] / PUBLIC_PATH).exists()


@pytest.mark.parametrize("identifier", ["qualify", "public_summary"])
def test_real_timeout_before_completion_cannot_publish_or_leak(tmp_path, identifier):
    hanging = f"print({CANARY!r},flush=True)\nimport time\ntime.sleep(60)\n"
    case = shell_case(tmp_path, body=hanging, helper=hanging)
    workspace, env, _ = case
    shell = workspace / "workflow-step.sh"
    shell.write_text(step(identifier)["run"])
    proc = subprocess.Popen(
        ["/bin/bash", "--noprofile", "--norc", "-e", "-o", "pipefail", str(shell)],
        cwd=workspace,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            proc.communicate(timeout=0.2)
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        stdout, stderr = proc.communicate(timeout=5)
    assert proc.returncode != 0 and stdout == stderr == b""
    assert not (workspace / PUBLIC_PATH).exists()
    if identifier == "qualify":
        # Runner reports cancellation, never success, even if the child printed PASS.
        shutil.copyfile(SCRIPT, workspace / "scripts" / SCRIPT.name)
        env.update(QUALIFICATION_OUTCOME="cancelled", JOB_STATUS="cancelled")
        assert execute_shell(case).returncode == 0
        assert_public(
            json.loads((workspace / PUBLIC_PATH).read_text()), ("success", "cancelled", "cancelled")
        )


def test_private_wheel_and_all_raw_evidence_are_outside_the_only_public_artifact(tmp_path):
    body = (
        "args=sys.argv[1:]\n"
        "output=pathlib.Path(args[args.index('--output')+1])\n"
        "output.mkdir()\n"
        "(output/'dist').mkdir()\n"
        "for name in ('commands.log','build-proof.json','runtime-proof.json','pytest.xml','dist/private.whl'):\n"
        f"    (output/name).write_text({CANARY!r}+{HASH_CANARY!r})\n"
        f"print('PASS: 15 installed-wheel controls; evidence: '+str(output)+{CANARY!r})\n"
    )
    case = shell_case(tmp_path, body=body)
    assert execute_shell(case, "qualify").returncode == 0
    assert_private_logs(case)
    args = json.loads(case[2].read_text())
    private = Path(args[args.index("--output") + 1])
    for path in private.rglob("*"):
        assert path.stat().st_mode & 0o077 == 0
        if path.is_file():
            assert CANARY in path.read_text() and HASH_CANARY in path.read_text()
    assert execute_shell(case).returncode == 0
    assert_public(json.loads((case[0] / PUBLIC_PATH).read_text()))
    assert list((case[0] / PUBLIC_PATH.parent).iterdir()) == [case[0] / PUBLIC_PATH]


@pytest.mark.parametrize("field", ["SETUP_OUTCOME", "QUALIFICATION_OUTCOME", "JOB_STATUS"])
def test_invalid_runner_metadata_is_never_public_and_cannot_claim_pass(tmp_path, field):
    case = shell_case(tmp_path)
    case[1][field] = CANARY
    assert execute_shell(case).returncode == 0
    assert json.loads((case[0] / PUBLIC_PATH).read_text()) == expected(None, None, None)
