"""Coverage and failure controls for runner-isolated CI partitioning."""

import copy
import os
from pathlib import Path
import subprocess

import pytest

from scripts import ci_test_shards as ci

SHA = "a" * 40


def collection(phase, nodes, skips=()):
    return {
        "sha": SHA,
        "phase": phase,
        "exitstatus": 0,
        "collected": list(nodes),
        "collection_skips": list(skips),
        "collection_skip_reasons": {p: "optional absent" for p in skips},
        "collection_errors": [],
        "reports": [],
        "subtests": [],
    }


@pytest.fixture
def example():
    owner = [f"{m}::test_owner" for m in ci.OWNERSHIP]
    ordinary = [f"tests/test_{n}.py::test_case[{i}]" for n in "abcdef" for i in range(2)]
    skipped = "tests/test_optional.py"
    policy = {
        "module_seconds": {"tests/test_a.py": 100},
        "runtime_skips": {ordinary[0]: "optional absent"},
        "collection_skips": [skipped],
        "collection_skip_reasons": {skipped: "optional absent"},
    }
    collections = {
        "full": collection("full", [*owner, *ordinary], [skipped]),
        "ownership": collection("ownership", owner),
        "cgroup": collection("cgroup", ["tests/test_mutation_isolate.py::test_real"]),
        "local": collection("local", ["tests/test_lock_signals.py::test_real"]),
    }
    plan = ci.build_plan(collections, SHA, policy)
    return policy, collections, plan


def execution(plan, phase):
    part = plan["phases"][phase]
    receipt = collection(phase, part["nodes"], part["collection_skips"])
    receipt["reports"] = [
        {"nodeid": n, "when": when, "outcome": "passed", "skip_reason": None, "wasxfail": False}
        for n in part["nodes"]
        for when in ("setup", "call", "teardown")
    ]
    return receipt


def materialize(directory, collections, plan):
    for phase, data in collections.items():
        ci.write(directory / f"collection-{phase}.json", data)
    for phase in ci.PHASES:
        ci.write(directory / f"receipt-{phase}.json", execution(plan, phase))


def needs():
    return {p: {"result": "success"} for p in ("plan", "process-contracts", "service-free")}


def test_partition_preserves_all_nodes_and_assigns_new_modules(example):
    policy, collections, plan = example
    reversed_collections = copy.deepcopy(collections)
    reversed_collections["full"]["collected"].reverse()
    assert ci.build_plan(reversed_collections, SHA, policy) == plan
    shards = [plan["phases"][f"shard-{n}"] for n in range(4)]
    nodes = [node for s in shards for node in s["nodes"]]
    assert len(nodes) == len(set(nodes)) == 12
    assert all(not set(s["modules"]) & set(ci.OWNERSHIP) for s in shards)
    assert set(nodes) | set(collections["ownership"]["collected"]) == set(plan["full_nodes"])
    assert "tests/test_optional.py" in {m for s in shards for m in s["modules"]}


@pytest.mark.parametrize(
    "mutation", ["duplicate", "empty", "error", "status", "sha", "phase", "new-skip", "reason"]
)
def test_collection_rejects_incomplete_or_changed_evidence(example, mutation):
    policy, collections, _ = example
    value = collections["full"]
    if mutation == "duplicate":
        value["collected"].append(value["collected"][0])
    if mutation == "empty":
        value["collected"] = []
    if mutation == "error":
        value["collection_errors"] = ["broken.py"]
    if mutation == "status":
        value["exitstatus"] = 2
    if mutation == "sha":
        value["sha"] = "wrong"
    if mutation == "phase":
        value["phase"] = "wrong"
    if mutation == "new-skip":
        value["collection_skips"].append("tests/new.py")
    if mutation == "reason":
        value["collection_skip_reasons"]["tests/test_optional.py"] = "new reason"
    with pytest.raises(ValueError):
        ci.build_plan(collections, SHA, policy)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-phase",
        "extra-phase",
        "empty-phase",
        "duplicate-node",
        "missing-node",
        "extra-node",
        "wrong-module",
        "wrong-sha",
        "wrong-policy",
    ],
)
def test_partition_rejects_bad_plan(example, mutation):
    policy, _, plan = example
    part = plan["phases"]["shard-0"]
    if mutation == "missing-phase":
        del plan["phases"]["shard-3"]
    if mutation == "extra-phase":
        plan["phases"]["shard-4"] = copy.deepcopy(part)
    if mutation == "empty-phase":
        part["nodes"] = []
    if mutation == "duplicate-node":
        part["nodes"].append(part["nodes"][0])
    if mutation == "missing-node":
        part["nodes"].pop()
    if mutation == "extra-node":
        part["nodes"].append("tests/test_a.py::invented")
    if mutation == "wrong-module":
        part["modules"] = ["tests/wrong.py"]
    if mutation == "wrong-sha":
        plan["sha"] = "wrong"
    if mutation == "wrong-policy":
        plan["policy_sha256"] = "wrong"
    with pytest.raises(ValueError):
        ci.validate_plan(plan, SHA, policy)


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "missing",
        "unexpected",
        "failure",
        "bad-outcome",
        "new-skip",
        "xfail",
        "teardown-skip",
        "missing-call",
        "collection-change",
        "subtest-failure",
        "subtest-skip",
    ],
)
def test_execution_refuses_false_green_receipts(example, mutation):
    policy, _, plan = example
    receipt = execution(plan, "shard-0")
    report = receipt["reports"][0]
    if mutation == "duplicate":
        receipt["reports"].append(dict(report))
    if mutation == "missing":
        receipt["reports"].clear()
    if mutation == "unexpected":
        report["nodeid"] = "unexpected"
    if mutation == "failure":
        report["outcome"] = "failed"
    if mutation == "bad-outcome":
        report["outcome"] = "unknown"
    if mutation == "new-skip":
        report.update(outcome="skipped", skip_reason="new reason")
    if mutation == "xfail":
        report["wasxfail"] = True
    if mutation == "teardown-skip":
        receipt["reports"][2].update(outcome="skipped", skip_reason="optional absent")
    if mutation == "missing-call":
        receipt["reports"].pop(1)
    if mutation == "collection-change":
        receipt["collected"].append("missing")
    if mutation.startswith("subtest-"):
        receipt["subtests"].append(
            dict(report, outcome="failed" if mutation.endswith("failure") else "skipped")
        )
    with pytest.raises(ValueError):
        ci.validate_execution(receipt, "shard-0", plan, policy)


def test_known_skip_is_allowed_but_not_required_and_subtests_are_distinct(example):
    policy, _, plan = example
    receipt = execution(plan, "shard-0")
    node = receipt["collected"][0]
    policy["runtime_skips"][node] = "optional absent"
    ci.validate_execution(receipt, "shard-0", plan, policy)
    receipt["subtests"] = [dict(receipt["reports"][1]), dict(receipt["reports"][1])]
    ci.validate_execution(receipt, "shard-0", plan, policy)
    receipt["subtests"] = []
    receipt["reports"][0].update(outcome="skipped", skip_reason="optional absent")
    receipt["reports"].pop(1)
    ci.validate_execution(receipt, "shard-0", plan, policy)


def test_ownership_does_not_inherit_skip_allowance(example):
    policy, _, plan = example
    receipt = execution(plan, "ownership")
    node = receipt["collected"][0]
    policy["runtime_skips"][node] = "optional absent"
    receipt["reports"][0].update(outcome="skipped", skip_reason="optional absent")
    with pytest.raises(ValueError):
        ci.validate_execution(receipt, "ownership", plan, policy)


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "missing",
        "duplicate",
        "wrong-shard",
        "missing-manifest",
        "altered-plan",
        "failed-job",
        "skipped-job",
        "cancelled-job",
        "missing-job",
    ],
)
def test_aggregate_fails_closed(example, tmp_path, mutation):
    policy, collections, plan = example
    materialize(tmp_path, collections, plan)
    jobs = needs()
    if mutation == "missing":
        (tmp_path / "receipt-shard-0.json").unlink()
    if mutation == "duplicate":
        ci.write(tmp_path / "extra/receipt-shard-0.json", execution(plan, "shard-0"))
    if mutation == "wrong-shard":
        ci.write(tmp_path / "receipt-shard-0.json", execution(plan, "shard-1"))
    if mutation == "missing-manifest":
        (tmp_path / "collection-full.json").unlink()
    if mutation == "altered-plan":
        removed = plan["phases"]["shard-0"]["nodes"].pop()
        plan["full_nodes"].remove(removed)
    if mutation in ("failed-job", "skipped-job", "cancelled-job"):
        jobs["service-free"]["result"] = mutation.removesuffix("-job")
    if mutation == "missing-job":
        del jobs["service-free"]
    if mutation is None:
        ci.aggregate(plan, tmp_path, SHA, policy, jobs)
    else:
        with pytest.raises(ValueError):
            ci.aggregate(plan, tmp_path, SHA, policy, jobs)


def test_original_selection_contract_is_unchanged():
    assert ci.selection("ownership") == ci.OWNERSHIP
    assert ci.selection("cgroup") == [*ci.CGROUP, "-m", "not real_api and not integration"]
    assert ci.selection("local") == [*ci.LOCAL, "-m", "integration"]
    assert ci.selection("shard-0") == ci.selection("full")
    assert len(ci.DESELECT) == 2


@pytest.mark.parametrize("kind", ["pass", "skip", "fail", "subtests", "git-guard", "collection-skip"])
def test_real_pytest_plugin_reports_outcomes_and_final_exit(tmp_path, kind):
    root = Path(__file__).resolve().parents[1]
    project = tmp_path / "project"
    project.mkdir()
    (project / "pytest.ini").write_text("[pytest]\n")
    scripts = {
        "pass": "def test_ok(): assert True\n",
        "skip": "import pytest\ndef test_skip(): pytest.skip('optional absent')\n",
        "fail": "def test_fail(): assert False\n",
        "subtests": "import unittest\nclass T(unittest.TestCase):\n def test_sub(self):\n  for i in range(2):\n   with self.subTest(i=i): self.assertEqual(i,i)\n",
        "git-guard": "def test_ok(): assert True\n",
        "collection-skip": "import pytest\npytest.skip('optional absent', allow_module_level=True)\n",
    }
    (project / "test_sample.py").write_text(scripts[kind])
    if kind == "git-guard":
        (project / "conftest.py").write_text(
            "def pytest_sessionfinish(session): session.exitstatus = 1\n"
        )
    if kind == "collection-skip":
        (project / "test_other.py").write_text("def test_ok(): assert True\n")
    report = tmp_path / "report.json"
    env = {**os.environ, "PYTHONPATH": str(root)}
    result = subprocess.run(
        ci.command_for_pytest("shard-0", SHA, report, [str(project), "-c", str(project / "pytest.ini")]),
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    data = ci.read(report)
    assert data["exitstatus"] == result.returncode == (1 if kind in ("fail", "git-guard") else 0), (
        result.stdout + result.stderr
    )
    if kind == "subtests":
        assert len(data["subtests"]) == 2
        assert len(data["reports"]) == 3
    if kind == "skip":
        assert data["reports"][1]["skip_reason"] == "optional absent"
    if kind == "collection-skip":
        assert data["collection_skips"] == ["test_sample.py"]


def test_workflow_preserves_timeouts_and_required_gate():
    import yaml

    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/linux-tests.yml").read_text())
    jobs = workflow["jobs"]
    assert jobs["linux-tests"]["name"] == "linux-tests"
    assert jobs["linux-tests"]["if"] == "${{ always() }}"
    assert jobs["service-free"]["strategy"] == {"fail-fast": False, "matrix": {"shard": [0, 1, 2, 3]}}
    assert jobs["service-free"]["timeout-minutes"] == 150
    process = {s["name"]: s for s in jobs["process-contracts"]["steps"]}
    assert [process[f"Run {p}"]["timeout-minutes"] for p in ("ownership", "cgroup", "local")] == [
        20,
        15,
        5,
    ]
    assert "systemd-run --user --scope" in process["Run cgroup"]["run"]
    assert (
        next(s for s in jobs["service-free"]["steps"] if s["name"] == "Run serial service-free shard")[
            "timeout-minutes"
        ]
        == 90
    )


def test_unknown_skip_without_reason_never_matches_missing_policy_entry(example):
    policy, _, plan = example
    receipt = execution(plan, "shard-0")
    node = receipt["collected"][0]
    policy["runtime_skips"].pop(node, None)
    receipt["reports"][0].update(outcome="skipped", skip_reason=None)
    receipt["reports"].pop(1)
    with pytest.raises(ValueError, match="Unexpected runtime skip"):
        ci.validate_execution(receipt, "shard-0", plan, policy)


def test_boolean_exit_status_is_not_zero(example):
    policy, collections, _ = example
    collections["full"]["exitstatus"] = False
    with pytest.raises(ValueError):
        ci.build_plan(collections, SHA, policy)


def test_setup_steps_match_original_budget_and_do_not_execute_tests():
    import yaml

    root = Path(__file__).resolve().parents[1]
    jobs = yaml.safe_load((root / ".github/workflows/linux-tests.yml").read_text())["jobs"]
    expected = {
        "Verify Linux process ownership prerequisites": 2,
        "Remove the image's system git-lfs filters": 2,
        "Install and verify bubblewrap": 5,
        "Install declared extras and qualified pytest": 10,
    }
    for name in ("plan", "process-contracts", "service-free"):
        steps = jobs[name]["steps"]
        setup = steps[1:6]
        assert {s["name"]: s["timeout-minutes"] for s in setup if "run" in s} == expected
        assert not any("python -m pytest" in s.get("run", "") for s in setup)


def test_retry_preserves_attempt_archives_before_replacing_current_artifacts():
    import yaml

    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/linux-tests.yml").read_text())
    for job in workflow["jobs"].values():
        steps = job["steps"]
        for index, step in enumerate(steps):
            if step.get("with", {}).get("overwrite"):
                archive = steps[index - 1]
                assert (
                    archive["with"]["name"]
                    == "archive-" + step["with"]["name"] + "-${{ github.run_attempt }}"
                )
                assert archive["with"]["path"] == step["with"]["path"]
                assert "overwrite" not in archive["with"]
                assert (
                    step["if"] == "${{ always() && steps." + archive["id"] + ".outcome == 'success' }}"
                )


def test_quiet_collection_keeps_exact_receipt_without_printing_every_node(tmp_path):
    root = Path(__file__).resolve().parents[1]
    project = tmp_path / "project"
    project.mkdir()
    (project / "pytest.ini").write_text("[pytest]\n")
    (project / "test_many.py").write_text(
        "import pytest\n@pytest.mark.parametrize('number', range(80))\n"
        "def test_number(number): assert number >= 0\n"
    )
    receipts, logs = [], []
    for quiet in (False, True):
        report = tmp_path / f"collect-{quiet}.json"
        args = ["--collect-only", str(project), "-c", str(project / "pytest.ini")]
        if quiet:
            args.append("-q")
        result = subprocess.run(
            ci.command_for_pytest("full", SHA, report, args),
            cwd=project,
            env={**os.environ, "PYTHONPATH": str(root)},
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        receipts.append(ci.read(report))
        logs.append(result.stdout)
    assert receipts[0] == receipts[1]
    assert len(receipts[0]["collected"]) == 80
    assert "::test_number" in logs[0] and "::test_number" not in logs[1]
    assert "test_many.py: 80" in logs[1]


def test_quiet_collection_failure_remains_visible_and_nonzero(tmp_path):
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_broken.py").write_text("raise RuntimeError('visible collection failure')\n")
    report = tmp_path / "report.json"
    result = subprocess.run(
        ci.command_for_pytest(
            "full",
            SHA,
            report,
            ["--collect-only", "-q", str(tmp_path), "-c", str(tmp_path / "pytest.ini")],
        ),
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    data = ci.read(report)
    assert result.returncode == data["exitstatus"] == 2
    assert data["collection_errors"] == ["test_broken.py"]
    assert "visible collection failure" in result.stdout


def test_ci_command_pins_repository_config_before_external_receipt_path(tmp_path):
    root = Path(__file__).resolve().parents[1]
    command = ci.command_for_pytest("full", SHA, tmp_path / "receipt.json", [])
    assert command[command.index("-c") + 1] == str(root / "pyproject.toml")


def test_long_parameter_identity_is_preserved_by_repository_policy(tmp_path):
    import tomllib

    root = Path(__file__).resolve().parents[1]
    options = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]
    assert options["parametrize_long_str_id_strategy"] == "legacy"
    value = "long-value-" * 15
    (tmp_path / "pytest.ini").write_text("[pytest]\nparametrize_long_str_id_strategy = legacy\n")
    (tmp_path / "test_ids.py").write_text(
        f"import pytest\n@pytest.mark.parametrize('value', [{value!r}])\ndef test_long(value): pass\n"
    )
    report = tmp_path / "report.json"
    result = subprocess.run(
        ci.command_for_pytest(
            "shard-0", SHA, report, ["--collect-only", str(tmp_path), "-c", str(tmp_path / "pytest.ini")]
        ),
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(root)},
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert ci.read(report)["collected"] == [f"test_ids.py::test_long[{value}]"]
