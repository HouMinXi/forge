"""Offline checks of the actual registered entry and reusable workflow.

The small expression interpreter covers only the operators used by these YAML
routes, including Actions' implicit success() condition. It does not execute
GitHub Actions, schedule hosted runners, or qualify the installed wheel.
"""

import ast
import copy
import os
from pathlib import Path
import re
import subprocess

import pytest
import yaml

from tests.ci_workflow_contract import without_stock_dispatch

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / ".github/workflows/linux-tests.yml"
STOCK = ROOT / ".github/workflows/linux-stock-qualification.yml"
OLD_JOBS = {"plan", "process-contracts", "service-free", "linux-tests"}
SHA = "a" * 40


def workflow(path=ENTRY):
    return yaml.safe_load(path.read_text())


def expression_tree(expression):
    assert expression.startswith("${{ ") and expression.endswith(" }}")
    source = expression[3:-2].replace("&&", " and ").replace("||", " or ")
    source = re.sub(r"!(?!=)", " not ", source)
    return ast.parse(source.strip(), mode="eval").body


def evaluate(node, context, status):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Attribute):
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        assert isinstance(node, ast.Name)
        return context.get(".".join([node.id, *reversed(parts)]), "")
    if isinstance(node, ast.BoolOp):
        value = evaluate(node.values[0], context, status)
        for operand in node.values[1:]:
            if isinstance(node.op, ast.And) and not value:
                break
            if isinstance(node.op, ast.Or) and value:
                break
            value = evaluate(operand, context, status)
        return value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not evaluate(node.operand, context, status)
    if isinstance(node, ast.Compare):
        assert len(node.ops) == 1
        left = evaluate(node.left, context, status)
        right = evaluate(node.comparators[0], context, status)
        # All route comparisons have strings on both sides. Actions compares
        # strings case-insensitively; missing properties resolve to ''.
        assert isinstance(left, str) and isinstance(right, str)
        equal = left.casefold() == right.casefold()
        if isinstance(node.ops[0], ast.Eq):
            return equal
        assert isinstance(node.ops[0], ast.NotEq)
        return not equal
    if isinstance(node, ast.Call):
        assert isinstance(node.func, ast.Name) and not node.args and not node.keywords
        return {
            "always": True,
            "success": status == "success",
            "failure": status == "failure",
            "cancelled": status == "cancelled",
        }[node.func.id]
    raise AssertionError(f"Unsupported Actions expression: {ast.dump(node)}")


def selected(job, context, status="success"):
    node = expression_tree(job.get("if", "${{ success() }}"))
    has_status = any(isinstance(item, ast.Call) for item in ast.walk(node))
    return (has_status or status == "success") and bool(evaluate(node, context, status))


def render(template, context):
    return re.sub(
        r"\$\{\{.*?\}\}",
        lambda match: str(evaluate(expression_tree(match[0]), context, "success")),
        template,
    )


# Expected job sets are an independent truth table, not a copy of the YAML.
ROUTES = [
    ("pull_request", {}, OLD_JOBS),
    ("push", {}, OLD_JOBS),
    ("workflow_dispatch", {}, OLD_JOBS),
    ("workflow_dispatch", {"inputs.stock_runner_pilot": False}, OLD_JOBS),
    (
        "workflow_dispatch",
        {"inputs.stock_runner_pilot": False, "inputs.expected_source_sha": ""},
        OLD_JOBS,
    ),
    ("workflow_dispatch", {"inputs.stock_runner_pilot": True}, {"stock-runner-qualification"}),
    (
        "workflow_dispatch",
        {"inputs.stock_runner_pilot": True, "inputs.expected_source_sha": ""},
        {"stock-runner-qualification"},
    ),
    (
        "workflow_dispatch",
        {"inputs.stock_runner_pilot": True, "inputs.expected_source_sha": SHA},
        {"stock-runner-qualification"},
    ),
    (
        "workflow_dispatch",
        {"inputs.stock_runner_pilot": True, "inputs.expected_source_sha": "bad"},
        {"stock-runner-qualification"},
    ),
    (
        "workflow_dispatch",
        {"inputs.stock_runner_pilot": False, "inputs.expected_source_sha": SHA},
        {"reject-stock-inputs"},
    ),
    ("workflow_dispatch", {"inputs.expected_source_sha": "bad"}, {"reject-stock-inputs"}),
    (
        "workflow_dispatch",
        {"inputs.stock_runner_pilot": False, "inputs.expected_source_sha": " "},
        {"reject-stock-inputs"},
    ),
    # Inputs do not change PR/push behavior even in a synthetic context.
    ("pull_request", {"inputs.stock_runner_pilot": True, "inputs.expected_source_sha": SHA}, OLD_JOBS),
    ("push", {"inputs.stock_runner_pilot": True, "inputs.expected_source_sha": SHA}, OLD_JOBS),
]
ROUTE_IDS = (
    "pr",
    "push",
    "manual-missing",
    "manual-false",
    "manual-empty",
    "pilot-missing-sha",
    "pilot-empty-sha",
    "pilot-valid",
    "pilot-invalid",
    "contradictory",
    "sha-without-pilot",
    "whitespace-sha",
    "pr-synthetic-inputs",
    "push-synthetic-inputs",
)


@pytest.mark.parametrize("event, inputs, expected", ROUTES, ids=ROUTE_IDS)
@pytest.mark.parametrize("status", ["success", "failure", "cancelled", "skipped"])
def test_actual_yaml_routes_every_old_job_and_preserves_status_semantics(
    event, inputs, expected, status
):
    context = {"github.event_name": event, **inputs}
    jobs = workflow()["jobs"]
    assert set(jobs) == OLD_JOBS | {"stock-runner-qualification", "reject-stock-inputs"}
    result = {name for name, job in jobs.items() if selected(job, context, status)}
    if status != "success":
        expected = expected & {"linux-tests"}
    assert result == expected


@pytest.mark.parametrize("event, inputs, expected", ROUTES, ids=ROUTE_IDS)
@pytest.mark.parametrize("fork", [False, True])
def test_effective_concurrency_keeps_ordinary_ci_and_nested_pilot_separate(
    event, inputs, expected, fork
):
    context = {
        "github.event_name": event,
        "github.workflow": "Linux tests",  # Reusable workflows inherit this name.
        "github.repository": "HouMinXi/forge",
        "github.ref_name": "reviewed-stock",
        **inputs,
    }
    if fork and event == "pull_request":
        context.update(
            {"github.event.pull_request.head.repo.full_name": "fork/forge", "github.head_ref": "topic"}
        )
        original = "Linux tests-fork/forge-topic"
    else:
        original = "Linux tests-HouMinXi/forge-reviewed-stock"
    caller = workflow()["concurrency"]
    callee = workflow(STOCK)["concurrency"]
    assert caller["cancel-in-progress"] is callee["cancel-in-progress"] is True
    actual = render(caller["group"], context)
    assert actual == (original if expected == OLD_JOBS else "stock-pilot-" + original)
    nested = render(callee["group"], context)
    assert nested == "stock-qualification-" + original
    assert nested != actual and nested != original


def test_local_reusable_resolution_inputs_credentials_and_checkout_are_bound():
    caller = workflow()
    inputs = caller["on"]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"stock_runner_pilot", "expected_source_sha"}
    assert inputs["stock_runner_pilot"]["type"] == "boolean"
    assert inputs["stock_runner_pilot"]["default"] is False
    assert inputs["expected_source_sha"]["type"] == "string"
    assert inputs["expected_source_sha"]["default"] == ""
    call = caller["jobs"]["stock-runner-qualification"]
    assert set(call) == {"if", "uses", "with"}
    assert call["uses"] == "./.github/workflows/linux-stock-qualification.yml"
    assert (ROOT / call["uses"]).resolve() == STOCK
    assert "@" not in call["uses"]  # Local reusable files resolve at the caller's commit.
    assert call["with"] == {"expected_source_sha": "${{ inputs.expected_source_sha }}"}
    context = {"inputs.expected_source_sha": SHA, "github.sha": SHA}
    passed_sha = render(call["with"]["expected_source_sha"], context)
    callee = workflow(ROOT / call["uses"])
    assert callee["on"]["workflow_call"] == {
        "inputs": {"expected_source_sha": {"type": "string", "required": True}}
    }
    assert set(callee["on"]) == {"workflow_dispatch", "workflow_call"}
    assert set(callee["jobs"]) == OLD_JOBS
    assert (
        render(callee["env"]["EXPECTED_SOURCE_SHA"], {"inputs.expected_source_sha": passed_sha}) == SHA
    )
    for job in callee["jobs"].values():
        checkout, request = job["steps"][:2]
        assert checkout["with"] == {"persist-credentials": False, "ref": "${{ github.sha }}"}
        assert render(checkout["with"]["ref"], context) == SHA
        assert request == {
            "name": "Require the explicitly reviewed source",
            "run": "exec >/dev/null 2>&1\nset +x\nset -euo pipefail\n/usr/bin/python3 -I -S -B scripts/ci_stock_runner.py request\n",
        }
    for path, value in ((ENTRY, caller), (STOCK, callee)):
        assert value["permissions"] == {"contents": "read"}
        text = path.read_text()
        assert not re.search(r"\$\{\{[^}]*\b(?:secrets|github\.token)\b", text)
        assert not re.search(r"\b(?:GITHUB_TOKEN|GH_TOKEN|ACTIONS_RUNTIME_TOKEN)\b", text)
        for job in value["jobs"].values():
            assert "permissions" not in job and "secrets" not in job and "environment" not in job
            for step in job.get("steps", []):
                if step.get("uses", "").startswith("actions/checkout@"):
                    assert step["with"]["persist-credentials"] is False
                    assert "token" not in step["with"]


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "empty",
        "missing",
        "short",
        "uppercase",
        "mismatch",
        "checkout-mismatch",
        "reusable-event",
        "push-event",
        "pr-event",
    ],
)
def test_actual_stock_request_command_enforces_expected_event_and_checkout(case):
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    expected, actual, event = head, head, "workflow_dispatch"
    if case in ("empty", "missing"):
        expected = ""
    elif case == "short":
        expected = head[:7]
    elif case == "uppercase":
        expected = head.upper()
    elif case == "mismatch":
        expected = "b" * 40
    elif case == "checkout-mismatch":
        expected = actual = "b" * 40
    elif case in ("reusable-event", "push-event", "pr-event"):
        event = {"reusable-event": "workflow_call", "push-event": "push", "pr-event": "pull_request"}[
            case
        ]
    context = {"inputs.expected_source_sha": expected}
    callee = workflow(STOCK)
    env = dict(os.environ, GITHUB_SHA=actual, GITHUB_EVENT_NAME=event)
    env["EXPECTED_SOURCE_SHA"] = render(callee["env"]["EXPECTED_SOURCE_SHA"], context)
    if case == "missing":
        env.pop("EXPECTED_SOURCE_SHA")
    # Execute the unchanged request step, including its real git HEAD read.
    # It does not call the host prerequisite operation or alter host settings.
    result = subprocess.run(
        ["bash", "-c", callee["jobs"]["plan"]["steps"][1]["run"]],
        cwd=ROOT,
        env=env,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == (0 if case == "valid" else 2)
    assert result.stdout == result.stderr == b""


def test_contradictory_input_job_is_bounded_fixed_failure_without_checkout():
    job = workflow()["jobs"]["reject-stock-inputs"]
    assert set(job) == {"if", "runs-on", "timeout-minutes", "steps"}
    assert job["timeout-minutes"] == 1
    assert len(job["steps"]) == 1
    assert set(job["steps"][0]) == {"name", "run"}
    run = job["steps"][0]["run"]
    assert "${{" not in run
    result = subprocess.run(["bash", "-c", run], capture_output=True, timeout=10, check=False)
    assert result.returncode == 1
    assert result.stdout == b""
    assert result.stderr == b"expected_source_sha requires stock_runner_pilot=true\n"


MUTATIONS = [
    (("on", "workflow_dispatch", "inputs", "stock_runner_pilot", "default"), True),
    (("on", "workflow_dispatch", "inputs", "stock_runner_pilot", "type"), "string"),
    (("on", "workflow_dispatch", "inputs", "expected_source_sha", "default"), SHA),
    (("on", "workflow_dispatch", "inputs", "expected_source_sha", "required"), True),
    (("on", "workflow_dispatch", "inputs", "extra"), {"type": "string"}),
    (("concurrency", "group"), "collision"),
    (("concurrency", "cancel-in-progress"), False),
    (("jobs", "reject-stock-inputs", "if"), "${{ success() }}"),
    (("jobs", "reject-stock-inputs", "steps", 0, "run"), "exit 0\n"),
    (("jobs", "reject-stock-inputs", "timeout-minutes"), 20),
    (("jobs", "reject-stock-inputs", "env"), {"UNREVIEWED": "value"}),
    (("jobs", "stock-runner-qualification", "uses"), "owner/repo/.github/workflows/run.yml@main"),
    (("jobs", "stock-runner-qualification", "secrets"), "inherit"),
    (("jobs", "stock-runner-qualification", "with", "expected_source_sha"), "${{ github.sha }}"),
    (("jobs", "stock-runner-qualification", "with", "extra"), True),
    (("jobs", "stock-runner-qualification", "needs"), "plan"),
    *(((("jobs", name, "if"), "${{ always() }}")) for name in sorted(OLD_JOBS)),
    (("jobs", "unreviewed-job"), {"runs-on": "ubuntu-24.04", "steps": []}),
]


@pytest.mark.parametrize(
    "path, replacement", MUTATIONS, ids=["-".join(map(str, path)) for path, _ in MUTATIONS]
)
def test_normalizer_cannot_hide_changed_routing_or_additional_authority(path, replacement):
    value = workflow()
    node = value
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = replacement
    with pytest.raises(AssertionError):
        without_stock_dispatch(value)


def test_normalizer_keeps_unrelated_fields_commands_coverage_and_budget_drift_visible():
    value = workflow()
    original = copy.deepcopy(value)
    value["permissions"]["contents"] = "write"
    value["jobs"]["plan"]["steps"][0]["with"]["ref"] = "unreviewed"
    value["jobs"]["service-free"]["timeout-minutes"] = 1
    value["jobs"]["process-contracts"]["steps"][2]["run"] = "exit 0"
    restored = without_stock_dispatch(value)
    assert restored["permissions"]["contents"] == "write"
    assert restored["jobs"]["plan"]["steps"][0]["with"]["ref"] == "unreviewed"
    assert restored["jobs"]["service-free"]["timeout-minutes"] == 1
    assert restored["jobs"]["process-contracts"]["steps"][2]["run"] == "exit 0"
    assert restored != without_stock_dispatch(original)
    for name in OLD_JOBS:
        assert restored["jobs"][name]["steps"] == value["jobs"][name]["steps"]
    assert without_stock_dispatch(original)["on"]["workflow_dispatch"] is None
    assert original == workflow()  # The normalizer never mutates its input.
