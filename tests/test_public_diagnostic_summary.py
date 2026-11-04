"""Offline publication tests; fixtures never collect host state or invoke sudo."""

import importlib.util
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/public_diagnostic_summary.py"
spec = importlib.util.spec_from_file_location("public_summary", SCRIPT)
public = importlib.util.module_from_spec(spec)
spec.loader.exec_module(public)
CANARY = "PRIVATE-unit.service-/home/private-user/token-PID-7654321"


def raw(mode, status="OBSERVED"):
    inner = {
        "schema": "forge-dependency-snapshot-v1" if mode == "ordinary" else "forge-dependency-view-v1",
        "qualification": "NOT_RUN",
        "snapshot": {"units": [CANARY], "jobs": []},
        "candidates": {"candidates": [CANARY], "edges": [[CANARY] * 3]},
        "fragments": {"fragment_sha256": [CANARY], "unit_ids": [CANARY]},
        "stat_attempts": [{"pid": 7654321, "stderr": CANARY}],
        "secret": CANARY,
    }
    return {
        "schema": public.SCHEMAS[mode],
        "status": status,
        "qualification": "NOT_RUN",
        "boundary": "NOT_RUN",
        "reason": CANARY,
        "context": {"REF": CANARY},
        "manager": {"pid": 7654321},
        "source_sha256": CANARY,
        "probe" if mode == "ordinary" else "observation": inner,
    }


@pytest.mark.parametrize("mode", public.SCHEMAS)
@pytest.mark.parametrize("status", ["OBSERVED", "STOP", "UNKNOWN"])
def test_projection_never_copies_private_keys_values_or_errors(mode, status):
    result = public.summary(raw(mode, status), mode)
    assert result["status"] == status
    assert result["qualification"] == "NOT_RUN"
    assert result["raw_evidence"] == "NOT_PUBLISHED"
    assert CANARY not in json.dumps(result) and "7654321" not in json.dumps(result)
    allowed = {
        "schema",
        "mode",
        "status",
        "qualification",
        "raw_evidence",
        "reason",
        "unit_count",
        "job_count",
        "candidate_count",
        "fragment_count",
        "reported_stat_attempt_count",
    }
    assert set(result) <= allowed
    if mode in ("ordinary", "view"):
        assert result["unit_count"] == 1 and result["job_count"] == 0
    if mode == "view":
        assert (
            result["candidate_count"]
            == result["fragment_count"]
            == result["reported_stat_attempt_count"]
            == 1
        )


@pytest.mark.parametrize("value", [None, [], True, "private", {"status": "OBSERVED"}])
def test_unrecognized_receipt_is_fixed_unknown(value):
    assert public.summary(value, "view")["status"] == "UNKNOWN"


def test_untrusted_status_types_and_oversized_counts_are_not_forwarded():
    value = raw("view")
    for key in ("schema", "status", "qualification"):
        bad = {**value, key: [CANARY]}
        result = public.summary(bad, "view")
        assert result["status"] == "UNKNOWN" and CANARY not in repr(result)
    observation = value["observation"]
    observation["snapshot"]["units"] *= 257
    observation["fragments"]["fragment_sha256"] *= 65
    observation["stat_attempts"] *= 3
    result = public.summary(value, "view")
    assert "unit_count" not in result and "fragment_count" not in result
    assert "reported_stat_attempt_count" not in result


@pytest.mark.parametrize(
    "kind", ["missing", "malformed", "duplicate", "oversized", "symlink", "fifo", "nested"]
)
def test_reader_failure_is_finite_and_never_logs_private_input(tmp_path, kind):
    path = tmp_path / "private.json"
    if kind == "malformed":
        path.write_text(CANARY)
    elif kind == "duplicate":
        path.write_text('{"status":"OBSERVED","status":"' + CANARY + '"}')
    elif kind == "oversized":
        path.write_bytes(b"x" * (public.MAX_INPUT + 1))
    elif kind == "symlink":
        target = tmp_path / "target"
        target.write_text(CANARY)
        path.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "nested":
        path.write_text("[" * 3000 + "]" * 3000)
    proc = subprocess.run(
        [sys.executable, "-I", "-S", str(SCRIPT), "view", str(path)], capture_output=True, timeout=3
    )
    assert proc.returncode == 2 and proc.stderr == b""
    assert json.loads(proc.stdout)["status"] == "UNKNOWN"
    assert CANARY.encode() not in proc.stdout and str(path).encode() not in proc.stdout


def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/strict-linux-admission.yml").read_text())


def summary_script(job):
    return {
        "observe": "static_public_summary.py",
        "fixed-metadata": "fixed_metadata_public_summary.py",
    }.get(job, "public_diagnostic_summary.py")


def public_output(job):
    return {
        "observe": "forge-static-public/summary.json",
        "fixed-metadata": "forge-fixed-metadata-public/summary.json",
    }.get(job)


def test_every_upload_has_a_safe_exact_destination_and_mode_guards_stay_closed():
    jobs = workflow()["jobs"]
    for name, job in jobs.items():
        uploads = [s for s in job["steps"] if "actions/upload-artifact@" in s.get("uses", "")]
        assert len(uploads) == 1
        path = uploads[0]["with"]["path"]
        assert "*" not in path and "\n" not in path
        if public_output(name):
            assert path == public_output(name)
        else:
            assert path.endswith(
                "/conflict.json" if name == "conflicting-modes" else "/public-summary.json"
            )
        if name != "conflicting-modes":
            runs = [s["run"] for s in job["steps"] if summary_script(name) in s.get("run", "")]
            assert len(runs) == 1 and "private-stderr.txt" in runs[0]
            assert "set +x" in runs[0] and "diagnostic_code=$?" in runs[0]
            assert runs[0].startswith("exec 2>/dev/null\n")
            assert "private-shell-stderr.txt" in runs[0]
    assert jobs["dependency-view"]["if"].endswith(
        "&& !inputs.fixed_metadata_read && !inputs.ordinary_dependency_snapshot }}"
    )
    assert "github.event_name == 'workflow_dispatch'" in jobs["ordinary-dependency"]["if"]
    assert "actions/checkout" not in json.dumps(jobs["conflicting-modes"])


@pytest.mark.parametrize(
    "mode,job,script,env_name",
    [
        ("static", "observe", "strict_linux_admission.py", "ADMISSION_EVIDENCE"),
        ("fixed", "fixed-metadata", "fixed_runner_metadata.py", "METADATA_EVIDENCE"),
        ("ordinary", "ordinary-dependency", "dependency_snapshot_runner.py", "DEPENDENCY_EVIDENCE"),
        ("view", "dependency-view", "dependency_view_runner.py", "VIEW_EVIDENCE"),
    ],
)
@pytest.mark.parametrize("code", [0, 2, 17, 137])
def test_workflow_shell_keeps_raw_stdout_stderr_private_and_preserves_exit(
    tmp_path, mode, job, script, env_name, code
):
    # Execute the actual workflow shell with ONLY its collector replaced by a
    # deterministic local fixture. Never execute a diagnostic or privileged code.
    step = next(s for s in workflow()["jobs"][job]["steps"] if summary_script(job) in s.get("run", ""))
    value = raw(mode, "OBSERVED" if code == 0 else "STOP")
    if mode == "static":
        value["problems"] = [] if code == 0 else ["bwrap_identity"]
        value["facts"] = {"bwrap": None}
        value["diagnostics"] = {
            "bwrap": {name: {"reason": "missing", "errno": 2} for name in ("usr_bin", "bin")}
        }
    fixture = tmp_path / "fixture.py"
    fixture.write_text(
        "import json,sys,pathlib,os,signal\n"
        f"value={value!r}\n"
        f"print({CANARY!r},file=sys.stderr)\n"
        "args=sys.argv[1:]\n"
        "if '--output' in args:\n"
        " path=pathlib.Path(args[args.index('--output')+1])\n"
        "elif '--output-dir' in args:\n"
        " path=pathlib.Path(args[args.index('--output-dir')+1])/'metadata-observation.json'\n"
        "else: path=None\n"
        "if path is None: print(json.dumps(value), flush=True)\n"
        f"else: path.write_text(json.dumps(value)); print({CANARY!r})\n"
        f"os.kill(os.getpid(), signal.SIGKILL) if {code} == 137 else None\n"
        f"sys.exit({code})\n"
    )
    run = step["run"].replace("scripts/" + script, str(fixture))
    assert str(fixture) in run and "scripts/" + script not in run
    # The stronger static/fixed publications write only fixed workspace paths.
    # Run every mode in its own workspace with just the public projection helper.
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(ROOT / "scripts" / summary_script(job), tmp_path / "scripts" / summary_script(job))
    private = tmp_path / "private"
    private.mkdir(parents=True)
    env = {
        **os.environ,
        env_name: str(private),
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_ENV": str(tmp_path / "env"),
    }
    env.update({key: "public-context" for key in step.get("env", {})})
    proc = subprocess.run(
        ["/bin/bash", "-c", run], cwd=tmp_path, env=env, capture_output=True, timeout=5
    )
    assert proc.returncode == code
    assert CANARY.encode() not in proc.stdout + proc.stderr
    assert proc.stdout == proc.stderr == b""
    outputs = (
        [tmp_path / public_output(job)]
        if public_output(job)
        else list(tmp_path.rglob("public-summary.json"))
    )
    assert len(outputs) == 1
    result = json.loads(outputs[0].read_text())
    expected = value["status"]
    assert result["status"] == expected and CANARY not in repr(result)
    if mode == "static":
        assert result["schema"] == "forge-public-static-summary-v3"
        assert result["problems"] == value["problems"]
        assert result["details"] == ({} if code == 0 else {"bwrap_identity": ["missing"]})
    if code == 137:
        private_stderr = "private-stderr.txt" if public_output(job) else "private-shell-stderr.txt"
        assert any(b"Killed" in path.read_bytes() for path in tmp_path.rglob(private_stderr))
    assert outputs[0].stat().st_mode & 0o077 == 0


@pytest.mark.parametrize(
    "job,env_name",
    [
        ("fixed-metadata", "METADATA_EVIDENCE"),
        ("ordinary-dependency", "DEPENDENCY_EVIDENCE"),
        ("dependency-view", "VIEW_EVIDENCE"),
    ],
)
def test_private_shell_redirection_failure_never_exposes_path_or_runs_collector(tmp_path, job, env_name):
    step = next(s for s in workflow()["jobs"][job]["steps"] if summary_script(job) in s.get("run", ""))
    # Absent parent makes Bash itself reject its private stderr redirection.
    # This occurs before the collector; no native command can run.
    env = {**os.environ, env_name: str(tmp_path / CANARY / "missing")}
    if job == "fixed-metadata":
        # This lane no longer imports a private evidence path from GITHUB_ENV.
        env["RUNNER_TEMP"] = str(tmp_path / CANARY / "missing")
    env.update({key: "public-context" for key in step.get("env", {})})
    fixture = tmp_path / "never_collect.py"
    fixture.write_text("raise SystemExit(75)\n")
    run = step["run"]
    for name in (
        "strict_linux_admission.py",
        "fixed_runner_metadata.py",
        "dependency_snapshot_runner.py",
        "dependency_view_runner.py",
    ):
        run = run.replace("scripts/" + name, str(fixture))
    proc = subprocess.run(["/bin/bash", "-c", run], cwd=ROOT, env=env, capture_output=True, timeout=3)
    assert proc.returncode != 0
    assert proc.stdout == proc.stderr == b""
    assert not list(tmp_path.rglob("public-summary.json"))


def test_static_directory_creation_failure_is_private(tmp_path):
    step = workflow()["jobs"]["observe"]["steps"][1]
    env = {
        **os.environ,
        "RUNNER_TEMP": str(tmp_path / CANARY / "missing"),
        "GITHUB_ENV": str(tmp_path / "env"),
    }
    env.update({key: "public-context" for key in step.get("env", {})})
    fixture = tmp_path / "never_collect.py"
    fixture.write_text("raise SystemExit(75)\n")
    run = step["run"]
    for name in (
        "strict_linux_admission.py",
        "fixed_runner_metadata.py",
        "dependency_snapshot_runner.py",
        "dependency_view_runner.py",
    ):
        run = run.replace("scripts/" + name, str(fixture))
    proc = subprocess.run(["/bin/bash", "-c", run], cwd=ROOT, env=env, capture_output=True, timeout=3)
    assert proc.returncode != 0 and proc.stdout == proc.stderr == b""


@pytest.mark.parametrize(
    "job,env_name,raw_name,script",
    [
        (
            "ordinary-dependency",
            "DEPENDENCY_EVIDENCE",
            "dependency-observation.json",
            "dependency_snapshot_runner.py",
        ),
        ("dependency-view", "VIEW_EVIDENCE", "view-observation.json", "dependency_view_runner.py"),
    ],
)
def test_collector_output_redirection_failure_is_private(tmp_path, job, env_name, raw_name, script):
    step = next(s for s in workflow()["jobs"][job]["steps"] if summary_script(job) in s.get("run", ""))
    private = tmp_path / CANARY
    private.mkdir(parents=True)
    (private / raw_name).mkdir()  # Bash cannot redirect stdout to a directory.
    marker = tmp_path / "ran"
    fixture = tmp_path / "fixture.py"
    fixture.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    run = step["run"].replace("scripts/" + script, str(fixture))
    env = {**os.environ, env_name: str(private)}
    env.update({key: "public-context" for key in step.get("env", {})})
    proc = subprocess.run(["/bin/bash", "-c", run], cwd=ROOT, env=env, capture_output=True, timeout=3)
    assert proc.returncode != 0 and not marker.exists()
    assert proc.stdout == proc.stderr == b""
    assert CANARY in (private / "private-shell-stderr.txt").read_text()
    assert json.loads((private / "public-summary.json").read_text())["status"] == "UNKNOWN"


@pytest.mark.parametrize(
    "job,env_name,script",
    [
        ("ordinary-dependency", "DEPENDENCY_EVIDENCE", "dependency_snapshot_runner.py"),
        ("dependency-view", "VIEW_EVIDENCE", "dependency_view_runner.py"),
    ],
)
def test_kill_before_receipt_produces_unknown_without_public_shell_diagnostics(
    tmp_path, job, env_name, script
):
    step = next(s for s in workflow()["jobs"][job]["steps"] if summary_script(job) in s.get("run", ""))
    fixture = tmp_path / "fixture.py"
    fixture.write_text("import os,signal\nos.kill(os.getpid(), signal.SIGKILL)\n")
    run = step["run"].replace("scripts/" + script, str(fixture))
    private = tmp_path / "private"
    private.mkdir()
    env = {**os.environ, env_name: str(private)}
    env.update({key: "public-context" for key in step.get("env", {})})
    proc = subprocess.run(["/bin/bash", "-c", run], cwd=ROOT, env=env, capture_output=True, timeout=3)
    assert proc.returncode == 137 and proc.stdout == proc.stderr == b""
    assert b"Killed" in (private / "private-shell-stderr.txt").read_bytes()
    assert json.loads((private / "public-summary.json").read_text())["status"] == "UNKNOWN"
