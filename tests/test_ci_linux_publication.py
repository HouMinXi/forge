"""Offline Linux workflow publication checks, without executing its workloads.

Only the exact, allowlisted shell preamble runs. Every original command body is
replaced by harmless fixtures, including collection, pytest, installation,
bubblewrap and systemd commands. These tests do not qualify the real runner or
approve a public identity manifest.
"""

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/linux-tests.yml"
CANARY = "PRIVATE-LINUX-/home/fixture-user/unit.service-PID-731995"
SUMMARY_NAME = "Prepare fixed public CI summary"
PUBLIC = "forge-linux-public/"
SUMMARY_GATE = "${{ always() && steps.public_summary.outcome == 'success' }}"
PRODUCER_IDS = {
    "Collect original selections and partition every module": "public_plan",
    "Run ownership": "ownership_receipt",
    "Run cgroup": "cgroup_receipt",
    "Run local": "local_receipt",
    "Run serial service-free shard": "shard_receipt",
}
DATA_PRODUCERS = {
    "linux-collection-plan": ("public_plan",),
    "linux-process-evidence": ("ownership_receipt", "cgroup_receipt", "local_receipt"),
    "linux-shard-${{ matrix.shard }}-evidence": ("shard_receipt",),
}

# Snapshots of 76817fc, independently read before the publication-only change.
# The structural snapshot excludes command bodies and transfer destinations,
# which are checked separately. CI does not need this commit in its shallow
# checkout. All old timeouts, conditions, permissions, pins, matrix settings,
# step ordering and archive/overwrite semantics remain in this snapshot.
BASE_STRUCTURE = "1ac6b534df523f5911f9b91a43b86a82a28e07c31cbd1b3c2969982f501e80f1"
BASE_COMMANDS = {
    "Verify Linux process ownership prerequisites": "ac8ac8c871df8b2affd0b4233b87164eb04603406a99932d0e9be68d5cf46e97",
    "Remove the image's system git-lfs filters": "53a1561d0f600241f36f74fd2f51a38c341ed8f67577c296202cd26c491f69aa",
    "Install and verify bubblewrap": "6def43d0323da5532c9473eef6007debd9caad8e23b51ede582be423d47113db",
    "Install declared extras and qualified pytest": "edbc1b00399ad5589f7da824d106535939338f808c309442b7f771607558ddc4",
    "Collect original selections and partition every module": "0c47832471a89e5e8361fddd9fad9708fea122919a85b9efda4d958c2a82ecf2",
    "Run ownership": "2cb2df671bdff48bec58d69c9ad7b85f09136c9af2ec171abeee66e97a671817",
    "Run cgroup": "fdae76d7dfd46e5b4b4ae9c28f6b7791dc67cc55e1edbbcb0a077e94cbd39b27",
    "Run local": "af8e249d85e73aec8c06baa97eb154704939dbd105a4a553412bb7bc25cca7ec",
    "Run serial service-free shard": "60bf47ba3e2cf32e94b80a6fad487bc41a84254269c1e08d6a6edd9fc593f607",
    "Require all jobs and exact complete node coverage": "e611674f28aeb9ecb1c47eb00ce565be8ec836b6d8a161b3931b1b122be90edf",
}


def workflow():
    return yaml.safe_load(WORKFLOW.read_text())


RUNS = [
    (job_name, step["name"])
    for job_name, job in workflow()["jobs"].items()
    for step in job["steps"]
    if "run" in step
]


def run_step(job_name, step_name):
    return next(s for s in workflow()["jobs"][job_name]["steps"] if s["name"] == step_name)


def private_prefix(run):
    """Reject unknown preamble commands before executing anything from YAML."""
    lines = run.splitlines(keepends=True)
    assert re.fullmatch(r'exec >"\$EVIDENCE/step-[0-9]{2}\.log" 2>&1\n', lines[7])
    expected = [
        "exec >/dev/null 2>&1\n",
        "set +x\n",
        "set -euo pipefail\n",
        "umask 077\n",
        'export TMPDIR="$RUNNER_TEMP/forge-tests"\n',
        'export EVIDENCE="$RUNNER_TEMP/forge-evidence"\n',
        'mkdir -p "$TMPDIR" "$EVIDENCE"\n',
        lines[7],
        'if [[ -x "$RUNNER_TEMP/forge-pytest-9.1.1/bin/python" ]]; then\n',
        '  export FORGE_QUALIFICATION_PYTHON="$RUNNER_TEMP/forge-pytest-9.1.1/bin/python"\n',
        "fi\n",
    ]
    assert lines[: len(expected)] == expected
    return "".join(expected), "".join(lines[len(expected) :])


def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


def stable_name(artifact_name):
    if artifact_name.startswith("archive-"):
        return artifact_name.removeprefix("archive-").removesuffix("-${{ github.run_attempt }}")
    return artifact_name


def archive_gate(artifact_name):
    producers = ("public_summary", *DATA_PRODUCERS.get(stable_name(artifact_name), ()))
    return "${{ always() && " + " && ".join(f"steps.{p}.outcome == 'success'" for p in producers) + " }}"


def fixed_summary_pair(artifact_name):
    archive = {
        "name": "Archive fixed public summary",
        "id": "archive_public_summary",
        "if": SUMMARY_GATE,
        "uses": "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9",
        "with": {
            "name": "archive-" + artifact_name + "-${{ github.run_attempt }}",
            "path": PUBLIC + "summary.json",
            "if-no-files-found": "error",
            "retention-days": 14,
        },
    }
    stable = copy.deepcopy(archive)
    del stable["id"]
    stable["name"] = "Preserve fixed public summary"
    stable["if"] = "${{ always() && steps.archive_public_summary.outcome == 'success' }}"
    stable["with"].update(name=artifact_name, overwrite=True)
    return [archive, stable]


@pytest.mark.parametrize("job_name,step_name", RUNS)
def test_every_shell_step_suppresses_output_before_expanding_private_paths(job_name, step_name):
    step = run_step(job_name, step_name)
    prefix, body = private_prefix(step["run"])
    assert body.strip()
    assert "continue-on-error" not in step
    assert prefix.index("exec >/dev/null") < prefix.index("$RUNNER_TEMP")
    assert "GITHUB_ENV" not in step["run"]
    assert "GITHUB_OUTPUT" not in step["run"]
    assert "GITHUB_STEP_SUMMARY" not in step["run"]
    assert "${{ runner.temp }}" not in WORKFLOW.read_text()


@pytest.mark.parametrize("job_name,step_name", RUNS)
def test_original_commands_change_only_private_transport_and_environment(job_name, step_name):
    _, body = private_prefix(run_step(job_name, step_name)["run"])
    if step_name == SUMMARY_NAME:
        assert body.strip() == (
            f"python scripts/ci_private_transport.py summary --job {job_name} \\\n"
            '  --status "$CI_JOB_STATUS" --public-directory forge-linux-public'
        )
        return
    # These exact reversals are the approved privacy wiring changes. They do
    # not discard shell operators, command options or arbitrary exports.
    transport_count = int(step_name in PRODUCER_IDS or step_name.startswith("Require all jobs"))
    assert body.count("scripts/ci_private_transport.py") == transport_count
    assert body.count(" --public-directory forge-linux-public") == int(step_name in PRODUCER_IDS)
    assert "scripts/ci_test_shards.py" not in body
    body = body.replace("scripts/ci_private_transport.py", "scripts/ci_test_shards.py")
    body = body.replace(" --public-directory forge-linux-public", "")
    body = body.replace(
        "--plan forge-linux-input/plan/plan.json", '--plan "$RUNNER_TEMP/forge-plan/plan.json"'
    )
    body = body.replace(
        "--plan forge-linux-input/all/linux-collection-plan/plan.json",
        '--plan "$RUNNER_TEMP/forge-all-evidence/linux-collection-plan/plan.json"',
    )
    body = body.replace(
        "--directory forge-linux-input/all", '--directory "$RUNNER_TEMP/forge-all-evidence"'
    )
    export = 'export FORGE_QUALIFICATION_PYTHON="$RUNNER_TEMP/forge-pytest-9.1.1/bin/python"\n'
    assert body.count(export) == int(step_name == "Install declared extras and qualified pytest")
    body = body.replace(export, "")
    if step_name in {"Run ownership", "Run cgroup", "Run local"}:
        phase = step_name.removeprefix("Run ")
        driver_log = f'| tee "$EVIDENCE/driver-{phase}.log"'
        assert body.count(driver_log) == 1
        body = body.replace(driver_log, f'| tee "$EVIDENCE/{phase}.log"')
    normalized = "\n".join(line.rstrip() for line in body.strip().splitlines())
    assert sha256(normalized) == BASE_COMMANDS[step_name], normalized


def test_full_workflow_preserves_original_authority_failures_budgets_and_parallelism():
    value = copy.deepcopy(workflow())
    for job_name, job in value["jobs"].items():
        summaries = [s for s in job["steps"] if s["name"] == SUMMARY_NAME]
        assert len(summaries) == 1
        summary = summaries[0]
        assert {key: val for key, val in summary.items() if key != "run"} == {
            "name": SUMMARY_NAME,
            "id": "public_summary",
            "if": "${{ always() }}",
            "env": {"CI_JOB_STATUS": "${{ job.status }}"},
        }
        job["steps"].remove(summary)
        if job_name in {"process-contracts", "service-free"}:
            name = (
                "linux-process-summary"
                if job_name == "process-contracts"
                else "linux-shard-${{ matrix.shard }}-summary"
            )
            assert job["steps"][-2:] == fixed_summary_pair(name)
            del job["steps"][-2:]
        if job_name == "linux-tests":
            extra = job["steps"].pop()
            assert extra == {
                "name": "Preserve fixed aggregate summary",
                "if": SUMMARY_GATE,
                "uses": "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9",
                "with": {
                    "name": "linux-aggregate-summary",
                    "path": PUBLIC + "summary.json",
                    "if-no-files-found": "error",
                    "retention-days": 14,
                },
            }
        for step in job["steps"]:
            step.pop("run", None)
            if step["name"] in PRODUCER_IDS:
                assert step.pop("id") == PRODUCER_IDS[step["name"]]
            if step.get("uses", "").startswith("actions/upload-artifact@"):
                if not step["with"].get("overwrite"):
                    assert step["if"] == archive_gate(step["with"]["name"])
                    step["if"] = "${{ always() }}"
            step.get("with", {}).pop("path", None)
    assert sha256(json.dumps(value, sort_keys=True, separators=(",", ":"))) == BASE_STRUCTURE


def expected_paths(artifact_name):
    artifact_name = stable_name(artifact_name)
    if artifact_name == "linux-collection-plan":
        names = [
            "plan.json",
            *[f"collection-{p}.json" for p in ("full", "ownership", "cgroup", "local")],
        ]
    elif artifact_name == "linux-process-evidence":
        names = [*[f"receipt-{p}.json" for p in ("ownership", "cgroup", "local")], "summary.json"]
    elif artifact_name == "linux-shard-${{ matrix.shard }}-evidence":
        names = ["receipt-shard-${{ matrix.shard }}.json", "summary.json"]
    else:
        assert artifact_name in {
            "linux-plan-evidence",
            "linux-aggregate-summary",
            "linux-process-summary",
            "linux-shard-${{ matrix.shard }}-summary",
        }
        names = ["summary.json"]
    return [PUBLIC + name for name in names]


def test_uploads_are_exact_public_files_and_require_successful_summary_and_archive():
    for job in workflow()["jobs"].values():
        steps = job["steps"]
        summary_index = next(i for i, s in enumerate(steps) if s.get("id") == "public_summary")
        for index, step in enumerate(steps):
            if not step.get("uses", "").startswith("actions/upload-artifact@"):
                continue
            assert index > summary_index
            options = step["with"]
            assert options["path"].splitlines() == expected_paths(options["name"])
            assert options["if-no-files-found"] == "error"
            assert options["retention-days"] == 14
            if options.get("overwrite"):
                archive = steps[index - 1]
                assert (
                    archive["with"]["name"]
                    == "archive-" + options["name"] + "-${{ github.run_attempt }}"
                )
                assert archive["with"]["path"] == options["path"]
                assert archive["if"] == archive_gate(archive["with"]["name"])
                assert "overwrite" not in archive["with"]
                assert (
                    step["if"] == "${{ always() && steps." + archive["id"] + ".outcome == 'success' }}"
                )
            else:
                assert step["if"] == archive_gate(options["name"])


def condition_allows(condition, outcomes):
    """Evaluate only the exact conjunction grammar used by these upload gates."""
    assert condition.startswith("${{ always() && ") and condition.endswith(" }}")
    clauses = condition.removeprefix("${{ always() && ").removesuffix(" }}").split(" && ")
    required = []
    for clause in clauses:
        match = re.fullmatch(r"steps\.([a-z_0-9]+)\.outcome == 'success'", clause)
        assert match, clause
        required.append(match[1])
    return all(outcomes.get(step) == "success" for step in required)


@pytest.mark.parametrize("status", ["failure", "cancelled", "skipped"])
@pytest.mark.parametrize("job_name", ["plan", "process-contracts", "service-free"])
def test_successful_summary_cannot_publish_stale_files_from_failed_producer(tmp_path, job_name, status):
    steps = workflow()["jobs"][job_name]["steps"]
    uploads = [s for s in steps if s.get("uses", "").startswith("actions/upload-artifact@")]
    data_archive = next(
        s for s in uploads if s.get("id") and stable_name(s["with"]["name"]) in DATA_PRODUCERS
    )
    producers = DATA_PRODUCERS[stable_name(data_archive["with"]["name"])]
    data_stable = next(
        s for s in uploads if s["with"]["name"] == stable_name(data_archive["with"]["name"])
    )
    for name in data_archive["with"]["path"].splitlines():
        path = tmp_path / name.replace("${{ matrix.shard }}", "0")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(CANARY)
    for failed_producer in ("public_summary", *producers):
        outcomes = dict.fromkeys(("public_summary", *producers), "success")
        outcomes[failed_producer] = status
        assert not condition_allows(data_archive["if"], outcomes)
        outcomes[data_archive["id"]] = "skipped"
        assert not condition_allows(data_stable["if"], outcomes)
        summary_archive = next(
            s for s in uploads if s.get("id") and s["with"]["path"] == PUBLIC + "summary.json"
        )
        assert condition_allows(summary_archive["if"], outcomes) == (failed_producer != "public_summary")
    assert condition_allows(data_archive["if"], dict.fromkeys(("public_summary", *producers), "success"))


def test_download_destinations_are_fixed_repository_relative_and_run_local():
    downloads = [
        (job_name, step["with"])
        for job_name, job in workflow()["jobs"].items()
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/download-artifact@")
    ]
    assert downloads == [
        ("process-contracts", {"name": "linux-collection-plan", "path": "forge-linux-input/plan"}),
        ("service-free", {"name": "linux-collection-plan", "path": "forge-linux-input/plan"}),
        (
            "linux-tests",
            {"pattern": "linux-*", "path": "forge-linux-input/all", "merge-multiple": False},
        ),
    ]


def shell_case(tmp_path, run, *, trace=False, setup_failure=None):
    prefix, _ = private_prefix(run)
    runner = tmp_path / ("private-runner-" + CANARY.replace("/", "_"))
    runner.mkdir()
    evidence = runner / "forge-evidence"
    interpreter = runner / "forge-pytest-9.1.1/bin/python"
    interpreter.parent.mkdir(parents=True)
    # Executability is inspected by the real preamble, never invoked.
    interpreter.write_text("#!/bin/sh\nexit 99\n")
    interpreter.chmod(0o700)
    log_name = re.search(r"step-[0-9]{2}\.log", prefix).group()
    if setup_failure == "mkdir":
        evidence.write_text(CANARY)
    elif setup_failure == "redirect":
        (evidence / log_name).mkdir(parents=True)
    sinks = {name: tmp_path / name for name in ("GITHUB_ENV", "GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY")}
    for path in sinks.values():
        path.touch()
    env = {
        "PATH": os.defpath,
        "HOME": str(tmp_path),
        "RUNNER_TEMP": str(runner),
        "CANARY": CANARY,
        **{name: str(path) for name, path in sinks.items()},
    }
    argv = ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail"]
    if trace:
        argv.append("-x")
    return prefix, argv, env, evidence, evidence / log_name, sinks, interpreter


NOISY_FIXTURE = (
    "set -x\n"
    'printf \'%s\\n\' "$CANARY" "$TMPDIR" "$EVIDENCE" "$FORGE_QUALIFICATION_PYTHON"\n'
    "printf '%s\\n' \"$CANARY\" >&2\n"
    'printf \'%s\' "$CANARY" > "$EVIDENCE/raw-plan.json"\n'
    'printf \'%s\' "$CANARY" > "$EVIDENCE/raw-receipt.json"\n'
    'printf \'%s\' "$CANARY" > "$EVIDENCE/raw-junit.xml"\n'
)
TERMINATIONS = {
    "success": ":\n",
    "failure": "false\n",
    "pipeline": "false | true\n",
    "unset": 'printf "%s" "$UNSET_PRIVATE_CANARY"\n',
    "syntax": "if then\n",
    "signal": 'kill -KILL "$BASHPID"\n',
    # exec gives the timeout one owned process to kill and reap, no orphan.
    "timeout": 'printf "%s" "$CANARY" > "$EVIDENCE/timeout-ready"\nexec sleep 30\n',
}


def expire_ready_fixture(argv, script, tmp_path, env, evidence):
    """Time the running fixture, not scheduler delay while starting bash."""
    ready = evidence / "timeout-ready"
    assert not ready.exists()
    with subprocess.Popen(
        [*argv, "-c", script], cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ) as proc:
        try:
            startup_deadline = time.monotonic() + 5
            while not (ready.is_file() and ready.read_text() == CANARY):
                assert proc.poll() is None, "Timeout fixture exited before its readiness marker"
                assert time.monotonic() < startup_deadline, "Timeout fixture did not start within 5s"
                time.sleep(0.01)
            with pytest.raises(subprocess.TimeoutExpired) as caught:
                proc.communicate(timeout=0.2)
            assert not caught.value.stdout and not caught.value.stderr
        finally:
            # The fixture execs sleep rather than spawning a descendant. Kill
            # and reap that one owned process, including on startup failures.
            if proc.poll() is None:
                proc.kill()
            stdout, stderr = proc.communicate(timeout=3)
        assert proc.returncode == -9
        assert stdout == stderr == b""


@pytest.mark.parametrize("job_name,step_name", RUNS)
@pytest.mark.parametrize("termination", TERMINATIONS)
def test_real_shell_prefix_contains_output_and_preserves_failure(
    tmp_path, job_name, step_name, termination
):
    case = shell_case(tmp_path, run_step(job_name, step_name)["run"])
    prefix, argv, env, evidence, log, sinks, interpreter = case
    script = prefix + NOISY_FIXTURE + TERMINATIONS[termination] + 'touch "$EVIDENCE/reached"\n'
    # The original command body never enters the executed script.
    assert "ci_private_transport.py" not in script
    assert "sudo " not in script and "systemd-run" not in script and "bwrap " not in script
    if termination == "timeout":
        expire_ready_fixture(argv, script, tmp_path, env, evidence)
    else:
        proc = subprocess.run(
            [*argv, "-c", script], cwd=tmp_path, env=env, capture_output=True, timeout=3
        )
        assert proc.stdout == proc.stderr == b""
        assert (proc.returncode == 0) == (termination == "success")
        if termination == "signal":
            assert proc.returncode == -9
    assert (evidence / "reached").exists() == (termination == "success")
    assert all(path.read_bytes() == b"" for path in sinks.values())
    assert CANARY in log.read_text()
    assert str(interpreter) in log.read_text()
    for filename in ("raw-plan.json", "raw-receipt.json", "raw-junit.xml"):
        path = evidence / filename
        assert path.read_text() == CANARY
        assert path.stat().st_mode & 0o777 == 0o600
    assert evidence.stat().st_mode & 0o777 == 0o700
    assert log.stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / "forge-linux-public").exists()


def test_timeout_window_starts_after_delayed_fixture_is_ready(tmp_path):
    prefix, argv, env, evidence, log, sinks, _ = shell_case(tmp_path, run_step(*RUNS[0])["run"])
    # Deliberately take longer than the execution timeout to write the canary.
    # This reproduces the old race without relying on a busy CI scheduler.
    script = prefix + "sleep 0.3\n" + NOISY_FIXTURE + TERMINATIONS["timeout"]
    expire_ready_fixture(argv, script, tmp_path, env, evidence)
    assert (evidence / "timeout-ready").read_text() == CANARY
    assert CANARY in log.read_text()
    assert all(path.read_bytes() == b"" for path in sinks.values())


@pytest.mark.parametrize("job_name,step_name", RUNS)
@pytest.mark.parametrize("failure", ["mkdir", "redirect", "unset-runner"])
def test_setup_failure_before_private_log_is_silent(tmp_path, job_name, step_name, failure):
    prefix, argv, env, evidence, _, sinks, _ = shell_case(
        tmp_path, run_step(job_name, step_name)["run"], setup_failure=failure
    )
    if failure == "unset-runner":
        del env["RUNNER_TEMP"]
    proc = subprocess.run(
        [*argv, "-c", prefix + NOISY_FIXTURE], cwd=tmp_path, env=env, capture_output=True, timeout=3
    )
    assert proc.returncode != 0 and proc.stdout == proc.stderr == b""
    assert all(path.read_bytes() == b"" for path in sinks.values())
    assert not (evidence / "raw-plan.json").exists()


@pytest.mark.parametrize("job_name,step_name", RUNS)
def test_inherited_xtrace_cannot_expand_private_values_before_suppression(tmp_path, job_name, step_name):
    prefix, argv, env, _, log, sinks, _ = shell_case(
        tmp_path, run_step(job_name, step_name)["run"], trace=True
    )
    proc = subprocess.run(
        [*argv, "-c", prefix + NOISY_FIXTURE], cwd=tmp_path, env=env, capture_output=True, timeout=3
    )
    assert proc.returncode == 0 and proc.stdout == b""
    # bash traces the first literal exec itself, before it redirects stderr.
    # No operand includes an expanded path or any private value at that point.
    assert proc.stderr == b"+ exec\n"
    assert CANARY in log.read_text()
    assert all(path.read_bytes() == b"" for path in sinks.values())


@pytest.mark.parametrize("shard", range(4))
def test_explicit_upload_selection_excludes_synthetic_private_siblings(tmp_path, shard):
    uploads = [
        s
        for job in workflow()["jobs"].values()
        for s in job["steps"]
        if s.get("uses", "").startswith("actions/upload-artifact@")
    ]
    selected = set()
    for step in uploads:
        paths = step["with"]["path"].splitlines()
        assert paths == expected_paths(step["with"]["name"])
        for name in paths:
            name = name.replace("${{ matrix.shard }}", str(shard))
            assert not any(char in name for char in "*?[]!$")
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"synthetic_public_fixture":true}\n')
            selected.add(path)
    for name in (
        "raw-plan.json",
        "raw-receipt.json",
        "nodeids.txt",
        "private.log",
        "junit.xml",
        "nested/raw.json",
    ):
        path = tmp_path / PUBLIC / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(CANARY)
        assert path not in selected
    assert len(selected) == 10  # One plan, four collections, four receipts and one summary.
    assert all(CANARY not in path.read_text() for path in selected)
