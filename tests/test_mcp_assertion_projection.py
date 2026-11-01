# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Optional assertion presentation shares the accepted SARIF projection boundary."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import socket
import subprocess
import time
from unittest.mock import Mock

import pytest
from mcp.types import JSONRPCMessage, JSONRPCResponse

from code_forge import mcp_server as server
from code_forge.mcp_jobs import ForgeResult


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    network = Mock(side_effect=AssertionError("unexpected network operation"))
    spawn = Mock(side_effect=AssertionError("unexpected subprocess"))
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, network)
    for name in ("create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, network)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", spawn)
    monkeypatch.setattr(subprocess, "Popen", spawn)
    yield
    network.assert_not_called()
    spawn.assert_not_called()


VALID = [
    {"status": "completed_empty", "finding_count": 0},
    {"status": "completed_findings", "finding_count": 9},
    {"status": "not_applicable", "finding_count": 0, "stage": "eligibility", "kind": "no_test_files"},
    {"status": "skipped", "finding_count": None, "stage": "eligibility", "kind": "no_git_diff"},
    *[
        {"status": "failed", "finding_count": None, "stage": stage, "kind": kind}
        for stage, kind in [
            ("preparation", "error"),
            ("invocation", "error"),
            ("invocation", "timeout"),
            ("validation", "error"),
            ("conversion", "error"),
            ("reporting", "inconsistent_outcome"),
        ]
    ],
]
ROUTES = ["inline", "handler", "sdk"]
MISSING = object()


def document(assertion=MISSING):
    properties = {"receiptAudit": [{"ruleId": "audit", "message": {"text": "audit note"}}]}
    if assertion is not MISSING:
        properties["testAssertion"] = deepcopy(assertion)
    return {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "code-forge"}},
                "results": [{"ruleId": "product", "message": {"text": "product note"}}],
                "properties": properties,
            }
        ],
    }


def wire_result(result):
    response = JSONRPCResponse(
        jsonrpc="2.0",
        id=1,
        result=result.model_dump(by_alias=True, mode="json", exclude_none=True),
    )
    wire = JSONRPCMessage(response).model_dump_json(by_alias=True, exclude_none=True)
    return json.loads(wire.encode("utf-8"))["result"]


async def consume(monkeypatch, route, stdout, *, exit_code=1, status="completed", stderr=""):
    if route == "inline":
        result = server._make_result(stdout, exit_code, 12.345, stderr)
        data = result.structuredContent
    else:
        entry = {
            "status": status,
            "result": {
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": exit_code,
                "verdict": server.exit_to_verdict(exit_code),
                "duration_s": 12.345,
            },
        }
        before = deepcopy(entry)
        monkeypatch.setattr(server, "get_job", lambda job_id: entry)
        result = (
            await server.mcp.call_tool("forge_job_status", {"job_id": "job"})
            if route == "sdk"
            else await server.forge_job_status("job")
        )
        assert entry == before
        assert result.structuredContent["status"] == status
        assert result.structuredContent["poll_after_seconds"] is None
        data = result.structuredContent["result"]
    assert result.isError is False
    wire = wire_result(result)
    assert wire["structuredContent"] == result.structuredContent
    return result, data


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("status", ["completed", "failed"])
@pytest.mark.parametrize("exit_code", [0, 1])
@pytest.mark.parametrize("assertion", VALID)
async def test_valid_assertions_all_routes(monkeypatch, route, status, exit_code, assertion):
    report = document(assertion)
    stdout = " \n" + json.dumps(report) + "\n"
    result, data = await consume(monkeypatch, route, stdout, status=status, exit_code=exit_code)
    assert data["test_assertion"] == assertion
    # Advisory finding_count is independent of this report's one product result.
    assert data["findings_count"] == 1
    assert data["findings_count_status"] is None
    assert data["findings"] == report["runs"][0]["results"]
    assert data["receipt_audit"] == report["runs"][0]["properties"]["receiptAudit"]
    assert data["verdict"] == ("PASS" if exit_code == 0 else "FAIL")
    assert data["exit_code"] == exit_code
    assert data["duration_s"] == (12.35 if route == "inline" else 12.345)
    assert data["output"] == stdout
    assert stdout in result.content[0].text


INVALID = [
    *[
        {"status": "failed", "finding_count": None, "stage": stage, "kind": kind}
        for stage, kind in [
            ("reporting", "error"),
            ("reporting", "timeout"),
            ("invocation", "inconsistent_outcome"),
            ("unknown", "inconsistent_outcome"),
        ]
    ],
    None,
    [],
    "completed_empty",
    0,
    True,
    {},
    {"finding_count": 0},
    {"status": "completed_empty"},
    {"status": "skipped", "stage": "eligibility", "kind": "no_git_diff"},
    {"status": "failed", "stage": "invocation", "kind": "error"},
    *[
        {"status": "completed_empty", "finding_count": value}
        for value in [False, True, -1, 0.0, "0", [], {}]
    ],
    *[
        {"status": status, "finding_count": 0}
        for status in [None, False, 1, [], {}, "COMPLETED_EMPTY", "unknown"]
    ],
    {"status": "completed_findings", "finding_count": 0},
    {"status": "completed_empty", "finding_count": None},
    {"status": "completed_empty", "finding_count": 1},
    {"status": "completed_empty", "finding_count": 0, "stage": "eligibility"},
    {"status": "completed_empty", "finding_count": 0, "kind": "error"},
    {"status": "failed", "finding_count": 0, "stage": "invocation", "kind": "error"},
    *[
        {"status": "failed", "finding_count": None, "stage": stage, "kind": kind}
        for stage, kind in [
            (None, None),
            ("eligibility", "error"),
            ("preparation", "timeout"),
            ("validation", "timeout"),
            ("conversion", "timeout"),
            ("unknown", "error"),
            ("invocation", "unknown"),
            ([], "error"),
            ("invocation", {}),
            (True, "error"),
            ("invocation", False),
        ]
    ],
    {"status": "not_applicable", "finding_count": 0, "stage": "eligibility", "kind": "no_git_diff"},
    {"status": "skipped", "finding_count": 0, "stage": "eligibility", "kind": "no_git_diff"},
]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("assertion", INVALID)
async def test_invalid_optional_assertion_keeps_legacy_projection(monkeypatch, route, assertion):
    report = document(assertion)
    stdout = json.dumps(report)
    _, data = await consume(monkeypatch, route, stdout)
    assert data["test_assertion"] is None
    assert data["findings_count"] == 1
    assert data["findings"] == report["runs"][0]["results"]
    assert data["receipt_audit"] == report["runs"][0]["properties"]["receiptAudit"]
    assert data["findings_count_status"] is None
    assert data["output"] == stdout


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
async def test_missing_assertion_and_stderr_cannot_supply_metadata(monkeypatch, route):
    stdout = json.dumps(document())
    stderr = "testAssertion: " + json.dumps(VALID[0])
    _, data = await consume(monkeypatch, route, stdout, stderr=stderr)
    assert data["test_assertion"] is None
    assert data["findings_count"] == 1
    assert data["output"] == stdout + "\n--- stderr ---\n" + stderr


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("optional_null", [False, True])
async def test_canonical_whitelist_ignores_unknown_payload(monkeypatch, route, optional_null):
    assertion = {**VALID[0], "reviewer_payload": {"secret_like": ["fixture-private-marker"]}}
    if optional_null:
        assertion.update(stage=None, kind=None)
    stdout = json.dumps(document(assertion))
    _, data = await consume(monkeypatch, route, stdout)
    assert data["test_assertion"] == VALID[0]
    assert "fixture-private-marker" not in json.dumps(data["test_assertion"])
    assert data["output"] == stdout
    assert "fixture-private-marker" in data["output"]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
async def test_capacity_incomplete_keeps_assertion(monkeypatch, route):
    report = document(VALID[6])
    report["runs"][0]["properties"]["providerDiagnostics"] = [{"diagnostic_kind": "capacity-incomplete"}]
    _, data = await consume(monkeypatch, route, json.dumps(report))
    assert data["test_assertion"] == VALID[6]
    assert data["findings_count"] is None
    assert data["findings_count_status"] == server._CAPACITY_INCOMPLETE_COUNT_STATUS
    assert data["findings"] == report["runs"][0]["results"]
    assert data["receipt_audit"] == report["runs"][0]["properties"]["receiptAudit"]


def damaged_stdout(damage):
    report = document(VALID[0])
    run = report["runs"][0]
    if damage == "wrong-tool":
        run["tool"]["driver"]["name"] = "not-code-forge"
    elif damage == "multi-run":
        report["runs"].append(deepcopy(run))
    elif damage == "invalid-results":
        run["results"] = [None]
    elif damage == "invalid-audit":
        run["properties"]["receiptAudit"] = [None]
    elif damage == "invalid-properties":
        run["properties"] = []
    elif damage == "nonfinite":
        run["results"][0]["value"] = float("nan")
    elif damage == "utf8":
        run["results"][0]["message"]["text"] = "\ud800"
    elif damage in ("deep-results", "deep-audit"):
        value = 0
        for _ in range(70):
            value = [value]
        rows = run["results"] if damage == "deep-results" else run["properties"]["receiptAudit"]
        rows[0]["nested"] = value
    stdout = json.dumps(report)
    if damage == "duplicate-key":
        stdout = stdout.replace(
            '"status": "completed_empty"', '"status": "completed_empty", "status": "completed_empty"'
        )
    elif damage == "trailing-document":
        stdout += " {}"
    elif damage == "oversize":
        stdout += " " * (server._MAX_SARIF_REPORT_CHARS + 1 - len(stdout))
    elif damage == "parser-recursion":
        # Python 3.12 accepts 1200 levels; this bounded 20 KiB fixture reaches
        # the decoder limit without changing global recursion settings.
        stdout = stdout[:-1] + ', "nested": ' + "[" * 10000 + "0" + "]" * 10000 + "}"
    return stdout


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    "damage",
    [
        "wrong-tool",
        "multi-run",
        "duplicate-key",
        "trailing-document",
        "invalid-results",
        "invalid-audit",
        "invalid-properties",
        "nonfinite",
        "utf8",
        "deep-results",
        "deep-audit",
        "oversize",
        "parser-recursion",
    ],
)
async def test_rejected_envelope_cannot_project_assertion(monkeypatch, route, damage):
    stdout = damaged_stdout(damage)
    _, data = await consume(monkeypatch, route, stdout)
    assert data["test_assertion"] is None
    assert data["findings_count"] is None
    assert data["findings"] is None
    assert data["receipt_audit"] is None
    assert data["findings_count_status"] is not None
    assert data["output"] == stdout


def test_exact_legacy_projection_tuple_shapes():
    stdout = json.dumps(document(VALID[0]))
    count, findings, audit = server._sarif_result_projection(stdout)
    count4, findings4, audit4, status = server._sarif_projection_with_status(stdout, 0)
    assert (count, findings, audit) == (count4, findings4, audit4)
    assert count == 1 and status is None
    assert server._sarif_result_projection(None) == (None, None, None)
    assert server._sarif_projection_with_status(None) == (None, None, None, None)


def test_model_default_assertion_is_none():
    result = ForgeResult(verdict="PASS", exit_code=0, duration_s=0, output="")
    assert result.test_assertion is None
    assert result.model_dump()["test_assertion"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["handler", "sdk"])
async def test_running_result_remains_none_and_entry_immutable(monkeypatch, route):
    entry = {
        "status": "running",
        "created_at": time.monotonic(),
        "stderr_log_path": None,
        "result": {"stdout": json.dumps(document(VALID[0]))},
    }
    before = deepcopy(entry)
    monkeypatch.setattr(server, "get_job", lambda job_id: entry)
    result = (
        await server.mcp.call_tool("forge_job_status", {"job_id": "job"})
        if route == "sdk"
        else await server.forge_job_status("job")
    )
    assert result.structuredContent == {
        "job_id": "job",
        "status": "running",
        "poll_after_seconds": 10,
        "result": None,
    }
    assert entry == before
    assert wire_result(result)["structuredContent"] == result.structuredContent
