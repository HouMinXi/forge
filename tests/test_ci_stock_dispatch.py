"""Offline tests of the permanent stock routing and real source admission.

Only the request step is executed: it reads Git HEAD and validates fixed event
context, never host prerequisites, package installation, or hosted Actions.
"""

import ast
import copy
import os
from pathlib import Path
import re
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / ".github/workflows/linux-tests.yml"
JOBS = {"plan", "process-contracts", "service-free", "linux-tests"}
SHA = "a" * 40
GROUP = "${{ github.event_name }}-${{ github.workflow }}-${{ github.event.pull_request.head.repo.full_name || github.repository }}-${{ github.head_ref || github.ref_name }}"
REQUEST = "exec >/dev/null 2>&1\nset +x\nset -euo pipefail\n/usr/bin/python3 -I -S -B scripts/ci_stock_runner.py request\n"


def workflow():
    return yaml.safe_load(ENTRY.read_text())


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


def assert_contract(value):
    assert value["name"] == "Linux tests"
    assert value["on"] == {
        "pull_request": {"branches": ["main"]},
        "push": {"branches": ["main"]},
        "workflow_dispatch": {
            "inputs": {
                "expected_source_sha": {
                    "description": "Reviewed exact commit SHA. No security settings are changed.",
                    "required": True,
                    "type": "string",
                }
            }
        },
    }
    assert value["permissions"] == {"contents": "read"}
    assert value["env"]["EXPECTED_SOURCE_SHA"] == "${{ inputs.expected_source_sha }}"
    assert value["concurrency"] == {"group": GROUP, "cancel-in-progress": True}
    assert set(value["jobs"]) == JOBS
    for name, job in value["jobs"].items():
        assert job["runs-on"] == "ubuntu-26.04"
        for field in ("uses", "secrets", "permissions", "environment", "continue-on-error"):
            assert field not in job
        if name == "linux-tests":
            assert job["name"] == "linux-tests"
            assert job["if"] == "${{ always() }}"
            assert job["needs"] == ["plan", "process-contracts", "service-free"]
        else:
            assert "if" not in job
        checkout, request = job["steps"][:2]
        assert checkout["uses"] == "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
        assert checkout["with"] == {"persist-credentials": False, "ref": "${{ github.sha }}"}
        assert request == {"name": "Require the exact event source", "run": REQUEST}
        assert not any("continue-on-error" in step for step in job["steps"])
    assert value["jobs"]["service-free"]["strategy"] == {
        "fail-fast": False,
        "matrix": {"shard": [0, 1, 2, 3]},
    }


def test_permanent_stock_graph_checkout_inputs_and_authority_are_bound():
    assert_contract(workflow())
    assert not (ROOT / ".github/workflows/linux-stock-qualification.yml").exists()
    text = ENTRY.read_text()
    assert not re.search(r"\$\{\{[^}]*\b(?:secrets|github\.token)\b", text)
    for forbidden in (
        "sysctl",
        "apparmor_parser",
        "aa-disable",
        "aa-complain",
        "pull_request_target",
        "stock_runner_pilot",
        "github.token",
        "GITHUB_TOKEN",
        "GH_TOKEN",
    ):
        assert forbidden not in text


@pytest.mark.parametrize("event", ["pull_request", "push", "workflow_dispatch"])
@pytest.mark.parametrize("status", ["success", "failure", "cancelled", "skipped"])
def test_all_events_keep_the_same_graph_and_fail_closed_aggregate(event, status):
    context = {"github.event_name": event}
    actual = {name for name, job in workflow()["jobs"].items() if selected(job, context, status)}
    assert actual == (JOBS if status == "success" else {"linux-tests"})


@pytest.mark.parametrize("event", ["pull_request", "push", "workflow_dispatch"])
@pytest.mark.parametrize("fork", [False, True])
def test_concurrency_keeps_manual_pr_push_and_repository_contexts_separate(event, fork):
    context = {
        "github.event_name": event,
        "github.workflow": "Linux tests",
        "github.repository": "HouMinXi/forge",
        "github.ref_name": "main",
    }
    suffix = "HouMinXi/forge-main"
    if fork and event == "pull_request":
        context.update(
            {"github.event.pull_request.head.repo.full_name": "fork/forge", "github.head_ref": "topic"}
        )
        suffix = "fork/forge-topic"
    assert render(workflow()["concurrency"]["group"], context) == event + "-Linux tests-" + suffix


CASES = [
    ("manual-branch", "workflow_dispatch", "HEAD", "HEAD", "refs/heads/pilot", "", True),
    ("manual-tag", "workflow_dispatch", "HEAD", "HEAD", "refs/tags/v1", "", True),
    ("pr-merge", "pull_request", "", "HEAD", "refs/pull/42/merge", "main", True),
    ("push-main", "push", "", "HEAD", "refs/heads/main", "", True),
    ("manual-empty", "workflow_dispatch", "", "HEAD", "refs/heads/pilot", "", False),
    ("manual-short", "workflow_dispatch", "short", "HEAD", "refs/heads/pilot", "", False),
    ("manual-uppercase", "workflow_dispatch", "UPPER", "HEAD", "refs/heads/pilot", "", False),
    ("manual-wrong-input", "workflow_dispatch", "b" * 40, "HEAD", "refs/heads/pilot", "", False),
    ("manual-wrong-checkout", "workflow_dispatch", "b" * 40, "b" * 40, "refs/heads/pilot", "", False),
    ("manual-missing-ref", "workflow_dispatch", "HEAD", "HEAD", "", "", False),
    ("manual-pr-ref", "workflow_dispatch", "HEAD", "HEAD", "refs/pull/42/merge", "", False),
    ("manual-base-ref", "workflow_dispatch", "HEAD", "HEAD", "refs/heads/pilot", "main", False),
    ("pr-head-not-merge", "pull_request", "", "HEAD", "refs/pull/42/head", "main", False),
    ("pr-zero-number", "pull_request", "", "HEAD", "refs/pull/0/merge", "main", False),
    ("pr-leading-zero", "pull_request", "", "HEAD", "refs/pull/042/merge", "main", False),
    ("pr-wrong-base", "pull_request", "", "HEAD", "refs/pull/42/merge", "topic", False),
    ("pr-missing-base", "pull_request", "", "HEAD", "refs/pull/42/merge", "", False),
    ("pr-manual-input", "pull_request", "HEAD", "HEAD", "refs/pull/42/merge", "main", False),
    ("pr-substituted-head", "pull_request", "", "b" * 40, "refs/pull/42/merge", "main", False),
    ("push-topic", "push", "", "HEAD", "refs/heads/topic", "", False),
    ("push-tag", "push", "", "HEAD", "refs/tags/main", "", False),
    ("push-base-ref", "push", "", "HEAD", "refs/heads/main", "main", False),
    ("push-manual-input", "push", "HEAD", "HEAD", "refs/heads/main", "", False),
    ("push-wrong-checkout", "push", "", "b" * 40, "refs/heads/main", "", False),
    ("push-deletion", "push", "", "0" * 40, "refs/heads/main", "", False),
    ("event-short-sha", "push", "", "short", "refs/heads/main", "", False),
    ("event-uppercase-sha", "push", "", "UPPER", "refs/heads/main", "", False),
    ("pull-request-target", "pull_request_target", "HEAD", "HEAD", "refs/heads/main", "", False),
    ("merge-group", "merge_group", "HEAD", "HEAD", "refs/heads/main", "", False),
    ("workflow-call-event", "workflow_call", "HEAD", "HEAD", "refs/heads/main", "", False),
    ("missing-event", "", "HEAD", "HEAD", "refs/heads/main", "", False),
]


@pytest.mark.parametrize(
    "case,event,expected,actual,ref,base,accepted", CASES, ids=[c[0] for c in CASES]
)
def test_actual_request_command_enforces_event_and_checked_out_source(
    case, event, expected, actual, ref, base, accepted
):
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()

    def resolve(value):
        return {"HEAD": head, "UPPER": head.upper(), "short": head[:7]}.get(value, value)

    env = dict(
        os.environ,
        GITHUB_EVENT_NAME=event,
        GITHUB_SHA=resolve(actual),
        GITHUB_REF=ref,
        GITHUB_BASE_REF=base,
        EXPECTED_SOURCE_SHA=resolve(expected),
    )
    result = subprocess.run(
        ["bash", "-c", workflow()["jobs"]["plan"]["steps"][1]["run"]],
        cwd=ROOT,
        env=env,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == (0 if accepted else 2)
    assert result.stdout == result.stderr == b""


MUTATIONS = [
    (("on", "pull_request", "branches"), ["topic"]),
    (("on", "push", "branches"), ["*"]),
    (("on", "workflow_dispatch", "inputs", "expected_source_sha", "required"), False),
    (("on", "workflow_dispatch", "inputs", "expected_source_sha", "default"), SHA),
    (("on", "workflow_dispatch", "inputs", "extra"), {"type": "string"}),
    (("permissions", "contents"), "write"),
    (("concurrency", "group"), "collision"),
    (("concurrency", "cancel-in-progress"), False),
    (("env", "EXPECTED_SOURCE_SHA"), "${{ github.sha }}"),
    (("jobs", "linux-tests", "name"), "different-gate"),
    (("jobs", "linux-tests", "if"), "${{ success() }}"),
    (("jobs", "linux-tests", "needs"), ["plan"]),
    (("jobs", "plan", "runs-on"), "ubuntu-24.04"),
    (("jobs", "plan", "steps", 0, "with", "ref"), "${{ github.event.pull_request.head.sha }}"),
    (("jobs", "plan", "steps", 0, "with", "persist-credentials"), True),
    (("jobs", "plan", "steps", 1, "run"), "exit 0\n"),
    (("jobs", "service-free", "strategy", "fail-fast"), True),
    (("jobs", "service-free", "strategy", "matrix", "shard"), [0, 1, 2]),
    (("jobs", "plan", "if"), "${{ always() }}"),
    (("jobs", "unreviewed-job"), {"runs-on": "ubuntu-26.04", "steps": []}),
]


@pytest.mark.parametrize(
    "path,replacement", MUTATIONS, ids=["-".join(map(str, p)) for p, _ in MUTATIONS]
)
def test_raw_workflow_contract_rejects_changed_routing_and_authority(path, replacement):
    value = copy.deepcopy(workflow())
    node = value
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = replacement
    with pytest.raises(AssertionError):
        assert_contract(value)
