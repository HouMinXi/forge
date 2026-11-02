"""Deterministic Linux CI partitioning and fail-closed execution receipts.

Also loaded as an explicit pytest plugin. No environment-based activation:
child pytest processes launched by tests must not overwrite their parent's receipt.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

OWNERSHIP = [
    "tests/test_mutation_process.py",
    "tests/test_mutation_cancellation.py",
    "tests/test_mcp_simple_cancel.py",
    "tests/test_mcp_budgeted_cancel.py",
]
CGROUP = ["tests/test_mutation_isolate.py", "tests/test_mutation_patchcorpus.py"]
LOCAL = ["tests/test_lock_signals.py", "tests/test_mutation_detach_integration.py"]
DESELECT = [
    "tests/test_integration.py::TestIntegrationPass::test_pass_on_clean_code",
    "tests/test_integration.py::TestIntegrationBaseline::test_baseline_preexisting_not_shown",
]
POLICY = Path(__file__).with_name("ci_test_policy.json")
PHASES = ["ownership", "cgroup", "local", *[f"shard-{i}" for i in range(4)]]


def read(path):
    return json.loads(Path(path).read_text())


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def unique(values):
    if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
        raise ValueError("Node lists must contain strings")
    if len(set(values)) != len(values):
        raise ValueError("Duplicate test node")
    return set(values)


def selection(phase):
    if phase == "ownership":
        return OWNERSHIP
    if phase == "cgroup":
        return [*CGROUP, "-m", "not real_api and not integration"]
    if phase == "local":
        return [*LOCAL, "-m", "integration"]
    return [
        "-m",
        "not real_api and not integration",
        *[f"--ignore={p}" for p in CGROUP],
        *[f"--deselect={n}" for n in DESELECT],
    ]


class Recorder:
    def __init__(self, config):
        self.path = config.getoption("ci_report")
        self.data = {
            "sha": config.getoption("ci_sha"),
            "phase": config.getoption("ci_phase"),
            "collected": [],
            "collection_skips": [],
            "collection_skip_reasons": {},
            "collection_errors": [],
            "reports": [],
            "subtests": [],
        }

    def pytest_collection_finish(self, session):
        self.data["collected"] = [item.nodeid for item in session.items]

    def pytest_collectreport(self, report):
        if report.skipped:
            self.data["collection_skips"].append(report.nodeid)
            self.data["collection_skip_reasons"][report.nodeid] = skip_reason(report)
        elif report.failed:
            self.data["collection_errors"].append(report.nodeid)

    def pytest_runtest_logreport(self, report):
        from _pytest.subtests import SubtestReport

        item = {
            "nodeid": report.nodeid,
            "when": report.when,
            "outcome": report.outcome,
            "skip_reason": skip_reason(report) if report.skipped else None,
            "wasxfail": hasattr(report, "wasxfail"),
        }
        self.data["subtests" if isinstance(report, SubtestReport) else "reports"].append(item)

    def pytest_sessionfinish(self, session, exitstatus):
        yield
        self.data["exitstatus"] = int(session.exitstatus)
        write(self.path, self.data)


def pytest_addoption(parser):
    group = parser.getgroup("forge-ci")
    group.addoption("--ci-report", default=None)
    group.addoption("--ci-phase", default=None)
    group.addoption("--ci-sha", default=None)


def pytest_configure(config):
    if config.getoption("ci_report"):
        import pytest

        pytest.hookimpl(wrapper=True, tryfirst=True)(Recorder.pytest_sessionfinish)
        config.pluginmanager.register(Recorder(config), "forge-ci-recorder")


def skip_reason(report):
    value = report.longrepr
    return str(value[2] if isinstance(value, tuple) else value).removeprefix("Skipped: ")


def command_for_pytest(phase, sha, report, args):
    return [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-ra",
        "-p",
        "no:cacheprovider",
        "-p",
        "scripts.ci_test_shards",
        "--ci-report",
        str(report),
        "--ci-phase",
        phase,
        "--ci-sha",
        sha,
        *args,
    ]


def check_collection(receipt, sha, phase, policy):
    if receipt.get("sha") != sha or receipt.get("phase") != phase:
        raise ValueError("Receipt SHA/phase mismatch")
    if (
        type(receipt.get("exitstatus")) is not int
        or receipt["exitstatus"] != 0
        or receipt.get("collection_errors")
    ):
        raise ValueError("Failed collection or pytest session")
    nodes = unique(receipt["collected"])
    skipped = unique(receipt["collection_skips"])
    if not nodes or not skipped <= set(policy["collection_skips"]):
        raise ValueError("Empty collection or unexpected collection skip")
    if receipt.get("collection_skip_reasons", {}) != {
        n: policy["collection_skip_reasons"][n] for n in skipped
    }:
        raise ValueError("Unexpected collection skip reason")
    if phase == "ownership" and skipped:
        raise ValueError("Ownership collection must not skip")
    return nodes, skipped


def build_plan(collections, sha, policy):
    checked = {p: check_collection(r, sha, p, policy) for p, r in collections.items()}
    if set(checked) != {"full", "ownership", "cgroup", "local"}:
        raise ValueError("Missing collection phase")
    full, skips = checked["full"]
    owner, owner_skips = checked["ownership"]
    if not owner <= full or owner_skips:
        raise ValueError("Ownership nodes must occur in the original full selection")
    remainder = full - owner
    modules = {n.split("::", 1)[0] for n in remainder} | skips
    if set(OWNERSHIP) & modules:
        raise ValueError("Ownership module partition is incomplete")
    weights = policy["module_seconds"]
    bins = [{"weight": 0.0, "modules": []} for _ in range(4)]
    for module in sorted(modules, key=lambda m: (-weights.get(m, 1.0), m)):
        weight = weights.get(module, 1.0)
        if not isinstance(weight, (float, int)) or not math.isfinite(weight) or weight < 0:
            raise ValueError("Invalid module weight")
        target = min(enumerate(bins), key=lambda pair: (pair[1]["weight"], pair[0]))[1]
        target["modules"].append(module)
        target["weight"] += weight
    phases = {}
    for phase in ("ownership", "cgroup", "local"):
        nodes, skipped = checked[phase]
        phases[phase] = {"nodes": sorted(nodes), "collection_skips": sorted(skipped)}
    for index, entry in enumerate(bins):
        assigned = set(entry["modules"])
        phases[f"shard-{index}"] = {
            "modules": sorted(assigned),
            "nodes": sorted(n for n in remainder if n.split("::", 1)[0] in assigned),
            "collection_skips": sorted(skips & assigned),
        }
    plan = {"sha": sha, "full_nodes": sorted(full), "phases": phases, "policy_sha256": digest(policy)}
    validate_plan(plan, sha, policy)
    return plan


def validate_plan(plan, sha, policy):
    if plan.get("sha") != sha or plan.get("policy_sha256") != digest(policy):
        raise ValueError("Plan SHA/policy mismatch")
    if set(plan["phases"]) != set(PHASES):
        raise ValueError("Missing or extra plan phase")
    full = unique(plan["full_nodes"])
    union = set()
    for phase in PHASES:
        part = plan["phases"][phase]
        nodes = unique(part["nodes"])
        skips = unique(part["collection_skips"])
        if not nodes or not skips <= set(policy["collection_skips"]):
            raise ValueError("Empty phase or unexpected collection skips")
        if phase in ("cgroup", "local"):
            continue
        if union & nodes:
            raise ValueError("Duplicate nodes across full-selection partitions")
        union |= nodes
        if phase.startswith("shard-"):
            modules = unique(part["modules"])
            if any(n.split("::", 1)[0] not in modules for n in nodes) or not skips <= modules:
                raise ValueError("Shard module/node mismatch")
    if union != full:
        raise ValueError("Full-selection coverage mismatch")


def validate_execution(receipt, phase, plan, policy):
    nodes, skips = check_collection(receipt, plan["sha"], phase, policy)
    expected = plan["phases"][phase]
    if nodes != unique(expected["nodes"]) or skips != unique(expected["collection_skips"]):
        raise ValueError("Executed collection does not match plan")
    reports = {}
    for item in receipt["reports"]:
        key = (item["nodeid"], item["when"])
        if (
            key in reports
            or item["nodeid"] not in nodes
            or item["when"] not in ("setup", "call", "teardown")
        ):
            raise ValueError("Duplicate or unexpected execution report")
        if item.get("wasxfail") or item["outcome"] not in ("passed", "skipped"):
            raise ValueError("Failed or invalid test report")
        if item["outcome"] == "skipped" and (
            phase == "ownership"
            or item["nodeid"] not in policy["runtime_skips"]
            or not isinstance(item.get("skip_reason"), str)
            or policy["runtime_skips"].get(item["nodeid"]) != item.get("skip_reason")
        ):
            raise ValueError("Unexpected runtime skip")
        reports[key] = item["outcome"]
    for item in receipt["subtests"]:
        if item["nodeid"] not in nodes or item["outcome"] != "passed" or item.get("wasxfail"):
            raise ValueError("Failed, skipped, or unexpected subtest")
    for node in nodes:
        setup = reports.get((node, "setup"))
        call = reports.get((node, "call"))
        if reports.get((node, "teardown")) != "passed":
            raise ValueError("Missing or failed teardown")
        if setup == "skipped":
            if call is not None:
                raise ValueError("Call after skipped setup")
        elif setup != "passed" or call not in ("passed", "skipped"):
            raise ValueError("Missing setup/call execution")


def collect_plan(output, sha, policy):
    collections = {}
    for phase in ("full", "ownership", "cgroup", "local"):
        report = output / f"collection-{phase}.json"
        subprocess.run(
            command_for_pytest(phase, sha, report, ["--collect-only", *selection(phase)]), check=True
        )
        collections[phase] = read(report)
    write(output / "plan.json", build_plan(collections, sha, policy))


def run_phase(phase, plan, output, policy):
    validate_plan(plan, plan["sha"], policy)
    args = selection(phase)
    if phase.startswith("shard-"):
        args = [*plan["phases"][phase]["modules"], *args]
    report = output / f"receipt-{phase}.json"
    result = subprocess.run(
        command_for_pytest(
            phase,
            plan["sha"],
            report,
            [
                *args,
                "--durations=20",
                f"--junitxml={output / (phase + '.xml')}",
            ],
        ),
        check=False,
    )
    if result.returncode:
        raise SystemExit(result.returncode)
    validate_execution(read(report), phase, plan, policy)


def aggregate(plan, directory, sha, policy, needs):
    validate_plan(plan, sha, policy)
    if set(needs) != {"plan", "process-contracts", "service-free"} or any(
        value.get("result") != "success" for value in needs.values()
    ):
        raise ValueError("A required CI job failed, was skipped, cancelled, or is missing")
    collections = {}
    for phase in ("full", "ownership", "cgroup", "local"):
        paths = list(directory.rglob(f"collection-{phase}.json"))
        if len(paths) != 1:
            raise ValueError("Missing or duplicate original collection manifest")
        collections[phase] = read(paths[0])
    if build_plan(collections, sha, policy) != plan:
        raise ValueError("Plan differs from original collection manifests")
    files = list(directory.rglob("receipt-*.json"))
    if len(files) != len(PHASES) or {p.name for p in files} != {f"receipt-{p}.json" for p in PHASES}:
        raise ValueError("Missing, duplicate, or extra execution receipts")
    for path in files:
        phase = path.name.removeprefix("receipt-").removesuffix(".json")
        validate_execution(read(path), phase, plan, policy)
    print(
        f"PASS: all {len(PHASES)} phases verified; {len(plan['full_nodes'])} full-selection nodes covered"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "run", "verify"))
    parser.add_argument("--sha", required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--phase", choices=PHASES)
    args = parser.parse_args()
    policy = read(POLICY)
    args.directory.mkdir(parents=True, exist_ok=True)
    if args.command == "plan":
        collect_plan(args.directory, args.sha, policy)
    else:
        plan = read(args.plan)
        validate_plan(plan, args.sha, policy)
        if args.command == "run":
            run_phase(args.phase, plan, args.directory, policy)
        else:
            aggregate(plan, args.directory, args.sha, policy, json.loads(os.environ["CI_NEEDS_JSON"]))


if __name__ == "__main__":
    main()
