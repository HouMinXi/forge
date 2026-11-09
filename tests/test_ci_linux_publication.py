"""Offline Linux workflow publication checks, without executing its workloads.

Only the exact, allowlisted shell preamble runs. Every original command body is
replaced by harmless fixtures, including collection, pytest, installation,
bubblewrap and systemd commands. These tests do not qualify the real runner or
approve a public identity manifest.
"""

import copy
import fnmatch
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
DIAGNOSTICS_NAME = "Prepare fixed public CI diagnostics"
DIAGNOSTICS_UPLOAD_NAME = "Preserve fixed public diagnostics"
DIAGNOSTICS_PUBLIC = "forge-linux-diagnostics/diagnostics.json"
DIAGNOSTICS_GATE = "${{ always() && steps.public_diagnostics.outcome == 'success' }}"
JOBS = ("plan", "process-contracts", "service-free", "linux-tests")
PUBLIC = "forge-linux-public/"
SUMMARY_GATE = "${{ always() && steps.public_summary.outcome == 'success' }}"
FAILURE_NAME = "Prepare fixed public failure diagnostic"
FAILURE_PUBLIC = "forge-linux-failure-public/"
FAILURE_ARTIFACT = "forge-ci-failure-shard-${{ matrix.shard }}"
FAILURE_ARCHIVE = "archive-" + FAILURE_ARTIFACT + "-${{ github.run_attempt }}"
FAILURE_GATE = "${{ always() && steps.shard_receipt.outcome == 'failure' }}"
FAILURE_COMMAND = (
    'python scripts/ci_failure_diagnostic.py --sha "$GITHUB_SHA" --phase "shard-${{ matrix.shard }}" \\\n'
    '  --plan forge-linux-input/plan/plan.json --directory "$EVIDENCE" \\\n'
    "  --public-directory forge-linux-failure-public --native-details"
)
PRODUCER_IDS = {
    "Collect original selections and partition every module": "public_plan",
    "Run ownership": "ownership_receipt",
    "Run cgroup": "cgroup_receipt",
    "Run local": "local_receipt",
    "Run serial service-free shard": "shard_receipt",
}
DIAGNOSTIC_PRODUCER_IDS = {
    **PRODUCER_IDS,
    "Require all jobs and exact complete node coverage": "coverage_verification",
}
# A fresh non-secret correlation value, unrelated to paths, PIDs, commands or
# credentials. It is never an authentication key or a source of pass authority.
INVOCATION_EXPORT = (
    "diagnostic_invocation=$(python -c 'import secrets; print(secrets.token_hex(16))')\n"
    '[[ "$diagnostic_invocation" =~ ^[0-9a-f]{32}$ ]]\n'
    'printf \'diagnostic_invocation=%s\\n\' "$diagnostic_invocation" >> "$GITHUB_OUTPUT"\n'
)
DIAGNOSTIC_OPERATIONS = {
    "plan": (("plan", "CI_PLAN_INVOCATION", "public_plan"),),
    "process-contracts": (
        ("ownership", "CI_OWNERSHIP_INVOCATION", "ownership_receipt"),
        ("cgroup", "CI_CGROUP_INVOCATION", "cgroup_receipt"),
        ("local", "CI_LOCAL_INVOCATION", "local_receipt"),
    ),
    "service-free": (("shard-${{ matrix.shard }}", "CI_SHARD_INVOCATION", "shard_receipt"),),
    "linux-tests": (("verify", "CI_VERIFY_INVOCATION", "coverage_verification"),),
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
# Refreshed for #421: only the three download-artifact pins change from
# 634f93cb (v5.0.0) to 9000827c (v8.0.2); every other structural field is retained.
BASE_STRUCTURE = "9680042bdcb148c11a2ec4aaea91c387d86c4c0f5af700f7670d9ba723509912"
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
    value = yaml.safe_load(WORKFLOW.read_text())
    # Authorized failure transcript sidecars are asserted independently by
    # test_ci_publish_failure_log; the original authority snapshot stays intact.
    for job in value["jobs"].values():
        job["steps"] = [
            s for s in job["steps"] if not s["name"].startswith("Publish failed pytest transcript (")
        ]
    return value


RUNS = [
    (job_name, step["name"])
    for job_name, job in workflow()["jobs"].items()
    for step in job["steps"]
    if "run" in step
]
ORIGINAL_RUNS = [(job, name) for job, name in RUNS if name != DIAGNOSTICS_NAME]
INVOCATION_RUNS = [(job, name) for job, name in RUNS if name in DIAGNOSTIC_PRODUCER_IDS]


def run_step(job_name, step_name):
    return next(s for s in workflow()["jobs"][job_name]["steps"] if s["name"] == step_name)


def private_prefix(run):
    """Reject unknown preamble commands before executing anything from YAML."""
    lines = run.splitlines(keepends=True)
    step_log = re.search(r'^exec >"\$EVIDENCE/(step-[0-9]{2}\.log)" 2>&1$', run, re.M).group(1)
    tee_logs = re.findall(r'\| \(umask 077; exec tee "\$EVIDENCE/([a-z0-9.-]+)"\)', run)
    logs = [step_log, *tee_logs]
    assert len(logs) == len(set(logs))
    expected = [
        "exec >/dev/null 2>&1\n",
        "set +x\n",
        "set -euo pipefail\n",
        "original_umask=$(umask)\n",
        "umask 077\n",
        'export TMPDIR="$RUNNER_TEMP/forge-tests"\n',
        'export EVIDENCE="$RUNNER_TEMP/forge-evidence"\n',
        'mkdir -p "$TMPDIR" "$EVIDENCE"\n',
        '[[ -d "$TMPDIR" && ! -L "$TMPDIR" && -d "$EVIDENCE" && ! -L "$EVIDENCE" ]]\n',
        'chmod 700 "$TMPDIR" "$EVIDENCE"\n',
        "for private_log in " + " ".join(logs) + "; do\n",
        '  [[ ! -e "$EVIDENCE/$private_log" && ! -L "$EVIDENCE/$private_log" ]]\n',
        '  (set -o noclobber; : >"$EVIDENCE/$private_log")\n',
        "done\n",
        "unset private_log\n",
        f'exec >"$EVIDENCE/{step_log}" 2>&1\n',
        'umask "$original_umask"\n',
        "unset original_umask\n",
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


def fixed_failure_pair():
    archive = {
        "name": "Archive fixed public failure diagnostic",
        "id": "archive_failure_diagnostic",
        "if": "${{ always() && steps.failure_diagnostic.outcome == 'success' }}",
        "uses": "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9",
        "with": {
            "name": FAILURE_ARCHIVE,
            "path": FAILURE_PUBLIC + "failure.json",
            "if-no-files-found": "error",
            "retention-days": 14,
        },
    }
    stable = copy.deepcopy(archive)
    del stable["id"]
    stable["name"] = "Preserve fixed public failure diagnostic"
    stable["if"] = "${{ always() && steps.archive_failure_diagnostic.outcome == 'success' }}"
    stable["with"].update(name=FAILURE_ARTIFACT, overwrite=True)
    return [archive, stable]


def assert_failure_diagnostic_steps(steps):
    """Approve exact additive wiring before removing it from the base snapshot."""
    exporter = steps[-3]
    assert {key: val for key, val in exporter.items() if key != "run"} == {
        "name": FAILURE_NAME,
        "id": "failure_diagnostic",
        "if": FAILURE_GATE,
    }
    prefix, body = private_prefix(exporter["run"])
    assert 'exec >"$EVIDENCE/step-09.log" 2>&1\n' in prefix
    assert body == FAILURE_COMMAND + "\n"
    assert steps[-2:] == fixed_failure_pair()
    assert steps[-5:-3] == fixed_summary_pair("linux-shard-${{ matrix.shard }}-summary")


@pytest.mark.parametrize("job_name,step_name", RUNS)
def test_every_shell_step_suppresses_output_before_expanding_private_paths(job_name, step_name):
    step = run_step(job_name, step_name)
    prefix, body = private_prefix(step["run"])
    assert body.strip()
    assert "continue-on-error" not in step
    assert prefix.index("exec >/dev/null") < prefix.index("$RUNNER_TEMP")
    assert "GITHUB_ENV" not in step["run"]
    if step_name in DIAGNOSTIC_PRODUCER_IDS:
        assert body.startswith(INVOCATION_EXPORT)
        assert body.count(INVOCATION_EXPORT) == 1
        assert "GITHUB_OUTPUT" not in prefix + body.removeprefix(INVOCATION_EXPORT)
    else:
        assert "GITHUB_OUTPUT" not in step["run"]
    assert "GITHUB_STEP_SUMMARY" not in step["run"]
    assert "${{ runner.temp }}" not in WORKFLOW.read_text()


@pytest.mark.parametrize("job_name,step_name", ORIGINAL_RUNS)
def test_original_commands_change_only_private_transport_and_environment(job_name, step_name):
    _, body = private_prefix(run_step(job_name, step_name)["run"])
    if step_name == SUMMARY_NAME:
        assert body.strip() == (
            f"python scripts/ci_private_transport.py summary --job {job_name} \\\n"
            '  --status "$CI_JOB_STATUS" --public-directory forge-linux-public'
        )
        return
    if step_name == FAILURE_NAME:
        assert job_name == "service-free"
        assert body == FAILURE_COMMAND + "\n"
        return
    # The consumer alone gets a private umask; the producer and its argv stay intact.
    body = re.sub(r'\| \(umask 077; exec tee ("\$EVIDENCE/[a-z0-9.-]+")\)', r"| tee \1", body)
    assert "umask" not in body
    # These exact reversals are the approved privacy wiring changes. They do
    # not discard shell operators, command options or arbitrary exports.
    transport_count = int(step_name in DIAGNOSTIC_PRODUCER_IDS)
    assert body.count("scripts/ci_private_transport.py") == transport_count
    assert body.count(INVOCATION_EXPORT) == transport_count
    if transport_count:
        assert body.startswith(INVOCATION_EXPORT)
        body = body.removeprefix(INVOCATION_EXPORT)
    for diagnostic_flag in (
        ' --diagnostic-directory "$EVIDENCE/diagnostics"',
        ' --diagnostic-invocation "$diagnostic_invocation"',
    ):
        assert body.count(diagnostic_flag) == transport_count
        body = body.replace(diagnostic_flag, "")
    assert body.count(" --public-directory forge-linux-public") == int(step_name in PRODUCER_IDS)
    assert body.count("--failure-diagnostic") == int(step_name == "Run serial service-free shard")
    if step_name == "Run serial service-free shard":
        assert job_name == "service-free"
        diagnostic_option = '--directory "$EVIDENCE" --failure-diagnostic --public-directory'
        assert body.count(diagnostic_option) == 1
        body = body.replace(diagnostic_option, '--directory "$EVIDENCE" --public-directory')
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
        # The independent sidecar is asserted exactly below. Do not weaken the
        # original structural snapshot or omit any original step or field.
        diagnostics = [
            s for s in job["steps"] if s["name"] in {DIAGNOSTICS_NAME, DIAGNOSTICS_UPLOAD_NAME}
        ]
        assert [s["name"] for s in diagnostics] == [DIAGNOSTICS_NAME, DIAGNOSTICS_UPLOAD_NAME]
        for step in diagnostics:
            job["steps"].remove(step)
        if job_name == "service-free":
            assert_failure_diagnostic_steps(job["steps"])
            del job["steps"][-3:]
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
            if step["name"] in DIAGNOSTIC_PRODUCER_IDS:
                assert step.pop("id") == DIAGNOSTIC_PRODUCER_IDS[step["name"]]
            if step.get("uses", "").startswith("actions/upload-artifact@"):
                if not step["with"].get("overwrite"):
                    assert step["if"] == archive_gate(step["with"]["name"])
                    step["if"] = "${{ always() }}"
            step.get("with", {}).pop("path", None)
    assert sha256(json.dumps(value, sort_keys=True, separators=(",", ":"))) == BASE_STRUCTURE


def expected_paths(artifact_name):
    if artifact_name in {FAILURE_ARTIFACT, FAILURE_ARCHIVE}:
        return [FAILURE_PUBLIC + "failure.json"]
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
    for job_name, job in workflow()["jobs"].items():
        steps = job["steps"]
        summary_index = next(i for i, s in enumerate(steps) if s.get("id") == "public_summary")
        for index, step in enumerate(steps):
            if not step.get("uses", "").startswith("actions/upload-artifact@"):
                continue
            if step["name"] == DIAGNOSTICS_UPLOAD_NAME:
                continue  # Exact schema, path and independent gate are asserted separately.
            assert index > summary_index
            options = step["with"]
            assert options["path"].splitlines() == expected_paths(options["name"])
            assert options["if-no-files-found"] == "error"
            assert options["retention-days"] == 14
            if options["name"] in {FAILURE_ARTIFACT, FAILURE_ARCHIVE}:
                assert job_name == "service-free"
                assert_failure_diagnostic_steps(steps)
                continue
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
        match = re.fullmatch(r"steps\.([a-z_0-9]+)\.outcome == '(success|failure)'", clause)
        assert match, clause
        required.append((match[1], match[2]))
    return all(outcomes.get(step) == expected for step, expected in required)


@pytest.mark.parametrize("job_name", JOBS)
def test_diagnostics_step_is_exact_independent_private_and_fail_closed(job_name):
    steps = workflow()["jobs"][job_name]["steps"]
    summary_index = next(i for i, s in enumerate(steps) if s.get("id") == "public_summary")
    step = steps[summary_index + 1]
    assert {key: value for key, value in step.items() if key != "run"} == {
        "name": DIAGNOSTICS_NAME,
        "id": "public_diagnostics",
        "if": "${{ always() }}",
        "env": {
            "CI_JOB_STATUS": "${{ job.status }}",
            **{
                variable: "${{ steps." + producer + ".outputs.diagnostic_invocation }}"
                for _, variable, producer in DIAGNOSTIC_OPERATIONS[job_name]
            },
        },
    }
    prefix, body = private_prefix(step["run"])
    assert 'exec >"$EVIDENCE/step-90.log" 2>&1\n' in prefix
    expected = (
        f"python scripts/ci_private_transport.py diagnostics --job {job_name} \\\n"
        '  --status "$CI_JOB_STATUS" --directory "$EVIDENCE/diagnostics" \\\n'
        "  --public-directory forge-linux-diagnostics"
    )
    if job_name == "service-free":
        expected += ' --phase "shard-${{ matrix.shard }}"'
    for operation, variable, _ in DIAGNOSTIC_OPERATIONS[job_name]:
        expected += f' \\\n  --invocation "{operation}=${variable}"'
    assert body.strip() == expected
    logs = [
        re.search(r'^exec >"\$EVIDENCE/(step-[0-9]{2}\.log)" 2>&1$', s["run"], re.M)[1]
        for s in steps
        if "run" in s
    ]
    assert len(logs) == len(set(logs))
    assert steps[summary_index]["if"] == "${{ always() }}"
    # Neither the original summary nor any coverage command can consume the
    # diagnostic projection or use its outcome to turn a failure into success.
    for original in steps:
        if original["name"] in {DIAGNOSTICS_NAME, DIAGNOSTICS_UPLOAD_NAME}:
            continue
        assert "public_diagnostics" not in json.dumps(original)
        assert "forge-linux-diagnostics" not in json.dumps(original)


@pytest.mark.parametrize("job_name", JOBS)
def test_diagnostic_invocations_bind_only_same_job_operation_outputs(job_name):
    job = workflow()["jobs"][job_name]
    steps = job["steps"]
    diagnostic_index = next(i for i, s in enumerate(steps) if s.get("id") == "public_diagnostics")
    diagnostic = steps[diagnostic_index]
    producers = []
    for operation, variable, producer in DIAGNOSTIC_OPERATIONS[job_name]:
        index = next(i for i, s in enumerate(steps) if s.get("id") == producer)
        assert index < diagnostic_index
        assert DIAGNOSTIC_PRODUCER_IDS[steps[index]["name"]] == producer
        _, body = private_prefix(steps[index]["run"])
        assert body.startswith(INVOCATION_EXPORT)
        assert diagnostic["env"][variable] == (
            "${{ steps." + producer + ".outputs.diagnostic_invocation }}"
        )
        assert diagnostic["run"].count(f'--invocation "{operation}=${variable}"') == 1
        if job_name == "service-free":
            assert job["strategy"]["matrix"]["shard"] == [0, 1, 2, 3]
            assert '--phase "shard-${{ matrix.shard }}"' in body
            assert operation == "shard-${{ matrix.shard }}"
        else:
            assert "matrix." not in operation
        producers.append(producer)
    assert len(producers) == len(set(producers))
    # These values are scoped to diagnostics, not propagated as job outputs,
    # credentials, original summary input or aggregate coverage authority.
    assert "outputs" not in job
    summary = run_step(job_name, SUMMARY_NAME)
    assert "invocation" not in json.dumps(summary)


@pytest.mark.parametrize("job_name", JOBS)
def test_diagnostic_upload_has_one_fixed_file_and_separate_attempt_identity(tmp_path, job_name):
    steps = workflow()["jobs"][job_name]["steps"]
    index = next(i for i, s in enumerate(steps) if s.get("id") == "public_diagnostics")
    shard = "-${{ matrix.shard }}" if job_name == "service-free" else ""
    expected = {
        "name": DIAGNOSTICS_UPLOAD_NAME,
        "if": DIAGNOSTICS_GATE,
        "uses": "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9",
        "with": {
            "name": f"diagnostics-linux-{job_name}{shard}-" + "${{ github.run_attempt }}",
            "path": DIAGNOSTICS_PUBLIC,
            "if-no-files-found": "error",
            "retention-days": 14,
        },
    }
    assert steps[index + 1] == expected
    assert sum(s["name"] == DIAGNOSTICS_UPLOAD_NAME for s in steps) == 1
    # Upload selection is exact, even when raw siblings exist beside it.
    public_file = tmp_path / expected["with"]["path"]
    public_file.parent.mkdir()
    public_file.write_text('{"synthetic_public_fixture":true}\n')
    for name in ("private.log", "raw-plan.json", "checkpoint.json", "nested/raw.json"):
        sibling = public_file.parent / name
        sibling.parent.mkdir(exist_ok=True)
        sibling.write_text(CANARY)
        assert sibling != public_file
    assert not any(char in expected["with"]["path"] for char in "*?[]!$")
    assert CANARY not in public_file.read_text()


def test_diagnostic_artifacts_are_distinct_for_every_job_shard_and_attempt():
    names = []
    for job in workflow()["jobs"].values():
        upload = next(s for s in job["steps"] if s["name"] == DIAGNOSTICS_UPLOAD_NAME)
        for attempt in (1, 2):
            for shard in job.get("strategy", {}).get("matrix", {}).get("shard", [None]):
                name = upload["with"]["name"].replace("${{ github.run_attempt }}", str(attempt))
                if shard is not None:
                    name = name.replace("${{ matrix.shard }}", str(shard))
                assert name.startswith("diagnostics-") and not name.startswith("linux-")
                assert "${{" not in name
                names.append(name)
    assert len(names) == len(set(names)) == 14


@pytest.mark.parametrize("job_name", JOBS)
@pytest.mark.parametrize("diagnostic_status", ["success", "failure", "cancelled", "skipped"])
def test_diagnostic_outcome_cannot_change_original_publication_authority(job_name, diagnostic_status):
    steps = workflow()["jobs"][job_name]["steps"]
    all_success = {s["id"]: "success" for s in steps if "id" in s}
    all_success["public_diagnostics"] = diagnostic_status
    for step in steps:
        if not step.get("uses", "").startswith("actions/upload-artifact@"):
            continue
        if step["name"] == DIAGNOSTICS_UPLOAD_NAME:
            assert condition_allows(step["if"], all_success) == (diagnostic_status == "success")
            # A failed original summary cannot suppress independently valid diagnostics.
            assert condition_allows(step["if"], {**all_success, "public_summary": "failure"}) == (
                diagnostic_status == "success"
            )
            continue
        assert condition_allows(step["if"], all_success)
        for required in re.findall(r"steps\.([a-z_0-9]+)\.outcome", step["if"]):
            assert required != "public_diagnostics"
            # Even successful diagnostics cannot authorize a failed original gate.
            outcomes = {**all_success, "public_diagnostics": "success", required: "failure"}
            assert not condition_allows(step["if"], outcomes)


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


@pytest.mark.parametrize("shard_status", ["success", "failure", "cancelled", "skipped"])
@pytest.mark.parametrize("summary_status", ["success", "failure", "cancelled", "skipped"])
def test_failure_exporter_runs_only_after_shard_failure_independent_of_summary(
    shard_status, summary_status
):
    steps = workflow()["jobs"]["service-free"]["steps"]
    assert_failure_diagnostic_steps(steps)
    exporter = steps[-3]
    outcomes = {"shard_receipt": shard_status, "public_summary": summary_status}
    assert condition_allows(exporter["if"], outcomes) == (shard_status == "failure")
    success_archive = next(s for s in steps if s.get("id") == "archive_9")
    assert condition_allows(success_archive["if"], outcomes) == (
        shard_status == summary_status == "success"
    )


@pytest.mark.parametrize("exporter_status", ["success", "failure", "cancelled", "skipped"])
@pytest.mark.parametrize("archive_status", ["success", "failure", "cancelled", "skipped"])
def test_stale_failure_file_cannot_upload_without_successful_exporter(
    tmp_path, exporter_status, archive_status
):
    steps = workflow()["jobs"]["service-free"]["steps"]
    assert_failure_diagnostic_steps(steps)
    archive, stable = steps[-2:]
    path = tmp_path / FAILURE_PUBLIC / "failure.json"
    path.parent.mkdir()
    path.write_text(CANARY)
    outcomes = {
        "failure_diagnostic": exporter_status,
        "public_summary": "failure",
        "archive_public_summary": "failure",
        "shard_receipt": "failure",
    }
    assert condition_allows(archive["if"], outcomes) == (exporter_status == "success")
    # A skipped archive cannot report success. Model the actual dependency
    # before deciding whether the stable upload is eligible to run.
    outcomes["archive_failure_diagnostic"] = (
        archive_status if condition_allows(archive["if"], outcomes) else "skipped"
    )
    assert condition_allows(stable["if"], outcomes) == (exporter_status == archive_status == "success")


@pytest.mark.parametrize(
    "change",
    ["success-exporter", "summary-dependency", "unchecked-upload", "broad-path", "aggregate-name"],
)
def test_failure_snapshot_rejects_expanded_or_weakened_wiring(change):
    steps = copy.deepcopy(workflow()["jobs"]["service-free"]["steps"])
    if change == "success-exporter":
        steps[-3]["if"] = FAILURE_GATE.replace("'failure'", "'success'")
    elif change == "summary-dependency":
        steps[-3]["if"] = FAILURE_GATE.replace(" }}", " && steps.public_summary.outcome == 'success' }}")
    elif change == "unchecked-upload":
        steps[-1]["if"] = "${{ always() && steps.failure_diagnostic.outcome == 'success' }}"
    elif change == "broad-path":
        steps[-2]["with"]["path"] = FAILURE_PUBLIC + "*"
    else:
        steps[-1]["with"]["name"] = "linux-failure-${{ matrix.shard }}"
    with pytest.raises(AssertionError):
        assert_failure_diagnostic_steps(steps)


def test_failure_diagnostic_artifacts_are_never_aggregate_inputs():
    value = workflow()
    assert_failure_diagnostic_steps(value["jobs"]["service-free"]["steps"])
    aggregate_download = next(
        s
        for s in value["jobs"]["linux-tests"]["steps"]
        if s.get("uses", "").startswith("actions/download-artifact@")
    )
    for step in value["jobs"]["service-free"]["steps"][-2:]:
        assert not fnmatch.fnmatchcase(step["with"]["name"], aggregate_download["with"]["pattern"])


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


@pytest.mark.parametrize("job_name,step_name", INVOCATION_RUNS)
@pytest.mark.parametrize(
    "value,generator_status,valid",
    [
        pytest.param("0123456789abcdef" * 2, 0, True, id="fresh-correlation"),
        pytest.param("", 0, False, id="empty"),
        pytest.param("A" * 32, 0, False, id="uppercase"),
        pytest.param("a" * 31, 0, False, id="short"),
        pytest.param("a" * 33, 0, False, id="long"),
        pytest.param("a" * 32 + "\ninjected=value", 0, False, id="newline-injection"),
        pytest.param(CANARY, 0, False, id="private-canary"),
        pytest.param("a" * 32, 17, False, id="generator-failure"),
    ],
)
def test_only_valid_nonsecret_correlation_reaches_step_output(
    tmp_path, job_name, step_name, value, generator_status, valid
):
    run = run_step(job_name, step_name)["run"]
    prefix, argv, env, evidence, _, sinks, _ = shell_case(tmp_path, run, trace=True)
    _, body = private_prefix(run)
    assert body.startswith(INVOCATION_EXPORT)
    commands = tmp_path / "commands"
    commands.mkdir()
    fake_python = commands / "python"
    fake_python.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$SYNTHETIC_INVOCATION\"\n" + f"exit {generator_status}\n"
    )
    fake_python.chmod(0o700)
    env["PATH"] = str(commands) + os.pathsep + env["PATH"]
    env["SYNTHETIC_INVOCATION"] = value
    # No workload or Python generator executes. Only the exact checked shell
    # preamble/export and the harmless fixture command above can execute.
    script = prefix + INVOCATION_EXPORT + 'touch "$EVIDENCE/reached"\n'
    proc = subprocess.run([*argv, "-c", script], cwd=tmp_path, env=env, capture_output=True, timeout=3)
    assert proc.stdout == b"" and proc.stderr == b"+ exec\n"
    assert (proc.returncode == 0) == valid
    assert (evidence / "reached").exists() == valid
    expected_output = f"diagnostic_invocation={value}\n" if valid else ""
    assert sinks["GITHUB_OUTPUT"].read_text() == expected_output
    assert sinks["GITHUB_ENV"].read_bytes() == sinks["GITHUB_STEP_SUMMARY"].read_bytes() == b""
    assert CANARY not in sinks["GITHUB_OUTPUT"].read_text()


NOISY_FIXTURE = (
    # Model the private writer's explicit 0600 allocation, not a suite-wide mask.
    # The real transport's raw receipt/JUnit allocation is covered in its tests.
    '(umask 077; : > "$EVIDENCE/raw-plan.json"; '
    ': > "$EVIDENCE/raw-receipt.json"; : > "$EVIDENCE/raw-junit.xml")\n'
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
    assert "ci_failure_diagnostic.py" not in script
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
    assert not (tmp_path / "forge-linux-failure-public").exists()


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
        and s["name"] != DIAGNOSTICS_UPLOAD_NAME
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
    for directory in (PUBLIC, FAILURE_PUBLIC):
        for name in (
            "raw-plan.json",
            "raw-receipt.json",
            "nodeids.txt",
            "private.log",
            "junit.xml",
            "nested/raw.json",
        ):
            path = tmp_path / directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(CANARY)
            assert path not in selected
    # One plan, four collections, four receipts, one summary and one diagnostic.
    assert len(selected) == 11
    assert all(CANARY not in path.read_text() for path in selected)


@pytest.mark.parametrize("job_name,step_name", RUNS)
@pytest.mark.parametrize("mask", [0o002, 0o022, 0o077])
def test_private_initialization_preserves_original_workload_umask(tmp_path, job_name, step_name, mask):
    run = run_step(job_name, step_name)["run"]
    prefix, argv, env, evidence, log, sinks, _ = shell_case(tmp_path, run)
    script = prefix + (
        'umask > "$TMPDIR/workload-mask"\n'
        'sh -c \'umask > "$TMPDIR/child-mask"; mkdir "$TMPDIR/workload-dir"\'\n'
    )
    proc = subprocess.run(
        [*argv, "-c", script], cwd=tmp_path, env=env, capture_output=True, timeout=3, umask=mask
    )
    assert proc.returncode == 0 and proc.stdout == proc.stderr == b""
    scratch = evidence.parent / "forge-tests"
    for name in ("workload-mask", "child-mask"):
        assert int((scratch / name).read_text().strip(), 8) == mask
        assert (scratch / name).stat().st_mode & 0o777 == 0o666 & ~mask
    assert (scratch / "workload-dir").stat().st_mode & 0o777 == 0o777 & ~mask
    assert evidence.stat().st_mode & 0o777 == 0o700
    assert log.stat().st_mode & 0o777 == 0o600
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in evidence.iterdir())
    assert all(path.read_bytes() == b"" for path in sinks.values())


@pytest.mark.parametrize("target", ["step", "tee"])
@pytest.mark.parametrize("kind", ["regular", "symlink", "dangling", "fifo", "directory"])
def test_preexisting_private_log_is_rejected_before_any_workload(tmp_path, target, kind):
    run = run_step(*RUNS[0])["run"]
    prefix, argv, env, evidence, log, sinks, _ = shell_case(tmp_path, run)
    evidence.mkdir()
    victim = log if target == "step" else evidence / "preflight.log"
    sentinel = tmp_path / "unchanged-sentinel"
    sentinel.write_text(CANARY)
    sentinel.chmod(0o644)
    if kind == "regular":
        victim.write_text(CANARY)
        victim.chmod(0o644)
    elif kind in {"symlink", "dangling"}:
        victim.symlink_to(sentinel if kind == "symlink" else tmp_path / "absent-target")
    elif kind == "fifo":
        os.mkfifo(victim)
    else:
        victim.mkdir()
    proc = subprocess.run(
        [*argv, "-c", prefix + 'touch "$TMPDIR/should-not-run"\n'],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=3,
        umask=0o002,
    )
    assert proc.returncode != 0 and proc.stdout == proc.stderr == b""
    assert not (evidence.parent / "forge-tests/should-not-run").exists()
    assert sentinel.read_text() == CANARY and sentinel.stat().st_mode & 0o777 == 0o644
    if kind == "regular":
        assert victim.read_text() == CANARY and victim.stat().st_mode & 0o777 == 0o644
    assert all(path.read_bytes() == b"" for path in sinks.values())


@pytest.mark.parametrize("mask", [0o002, 0o022, 0o077])
@pytest.mark.parametrize(
    "termination,expected", [("exit 0", 0), ("exit 17", 17), ('kill -KILL "$BASHPID"', 137)]
)
def test_private_tee_preserves_producer_mask_failure_and_private_live_log(
    tmp_path, mask, termination, expected
):
    run = run_step(*RUNS[0])["run"]
    prefix, argv, env, evidence, log, sinks, _ = shell_case(tmp_path, run)
    consumer = re.search(r'\(umask 077; exec tee "\$EVIDENCE/preflight\.log"\)', run).group()
    # Only a harmless producer and the exact allowlisted tee expression execute.
    producer = (
        '(umask > "$TMPDIR/producer-mask"; '
        'test "$(stat -c %a "$EVIDENCE/preflight.log")" = 600; '
        'printf "%s" "$CANARY"; ' + termination + ")"
    )
    proc = subprocess.run(
        [*argv, "-c", prefix + producer + " | " + consumer + "\n"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=3,
        umask=mask,
    )
    assert proc.returncode == expected and proc.stdout == proc.stderr == b""
    assert int((evidence.parent / "forge-tests/producer-mask").read_text().strip(), 8) == mask
    assert (evidence / "preflight.log").read_text() == CANARY
    assert (evidence / "preflight.log").stat().st_mode & 0o777 == 0o600
    assert log.stat().st_mode & 0o777 == 0o600
    assert all(path.read_bytes() == b"" for path in sinks.values())


@pytest.mark.parametrize("producer_code", [0, 19])
def test_private_tee_failure_keeps_original_pipefail_semantics(tmp_path, producer_code):
    run = run_step(*RUNS[0])["run"]
    prefix, argv, env, evidence, log, sinks, _ = shell_case(tmp_path, run)
    consumer = re.search(r'\(umask 077; exec tee "\$EVIDENCE/preflight\.log"\)', run).group()
    commands = tmp_path / "commands"
    commands.mkdir()
    fake_tee = commands / "tee"
    fake_tee.write_text("#!/bin/sh\ncat >/dev/null\nexit 23\n")
    fake_tee.chmod(0o700)
    env["PATH"] = str(commands) + os.pathsep + env["PATH"]
    script = prefix + f'(printf "%s" "$CANARY"; exit {producer_code}) | {consumer}\n'
    proc = subprocess.run(
        [*argv, "-c", script], cwd=tmp_path, env=env, capture_output=True, timeout=3, umask=0o002
    )
    # bash pipefail returns the rightmost failure, including when both fail.
    assert proc.returncode == 23 and proc.stdout == proc.stderr == b""
    assert (evidence / "preflight.log").read_bytes() == b""
    assert (evidence / "preflight.log").stat().st_mode & 0o777 == 0o600
    assert log.stat().st_mode & 0o777 == 0o600
    assert all(path.read_bytes() == b"" for path in sinks.values())


def test_native_detail_publication_has_no_explicit_credentials_or_broader_trigger():
    """Keep the temporary exception publication tied to a credential-free job."""
    workflow = yaml.safe_load(WORKFLOW.read_text())
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["on"]) == {"pull_request", "push", "workflow_dispatch"}
    text = WORKFLOW.read_text()
    for forbidden in (
        "github.token",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "ACTIONS_RUNTIME_TOKEN",
    ):
        assert forbidden not in text
    assert not re.search(r"\$\{\{[^}]*\bsecrets(?:\.|\[)", text)
    for job in workflow["jobs"].values():
        assert "permissions" not in job
        for step in job["steps"]:
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False
                assert "token" not in step["with"]
    assert text.count("--native-details") == 1
    assert "--native-details" in run_step("service-free", FAILURE_NAME)["run"]
