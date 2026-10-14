# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Structured MCP presentation follows the actual CLI SARIF document."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import socket
from unittest.mock import Mock

import pytest
from mcp.types import JSONRPCMessage, JSONRPCResponse

from code_forge import mcp_server as server
from code_forge.disposition import Disposition
from code_forge.llm_invoke import LLMInvokeError
from code_forge.sarif import build_sarif_log
from code_forge.state import Mode, State, StateFinding, Verdict, record_provider_failure


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    network = Mock(side_effect=AssertionError("unexpected network operation"))
    spawn = Mock(side_effect=AssertionError("unexpected CLI launch"))
    monkeypatch.setattr(socket.socket, "connect", network)
    monkeypatch.setattr(socket.socket, "connect_ex", network)
    monkeypatch.setattr(socket, "create_connection", network)
    monkeypatch.setattr(socket, "getaddrinfo", network)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", spawn)
    yield
    network.assert_not_called()
    spawn.assert_not_called()


@pytest.fixture
def report():
    product = StateFinding("bug", "bug-fp", "L1", Disposition.CONFIRMED,
                           "a.py", [1, 3], "product finding")
    audit = StateFinding("RECEIPT_UNTRUSTED", "audit-fp", "UNTRUSTED",
                         Disposition.UNCERTAIN, "a.py", [1, 3], "receipt audit")
    capacity = StateFinding("l1-qodo-invoke-fail", "invoke-fail-qodo", "INFRA",
                            Disposition.CONFIRMED, "<llm-invoke>", [0, 0], "capacity")
    record_provider_failure(capacity, LLMInvokeError("capacity", kind="truncated"))
    generic = StateFinding("l1-expert-invoke-fail", "invoke-fail-expert", "INFRA",
                           Disposition.CONFIRMED, "<llm-invoke>", [0, 0], "no JSON")
    record_provider_failure(generic, LLMInvokeError("no JSON", kind="no_json"))
    state = State(mode=Mode.CI, verdict=Verdict.FAIL,
                  findings=[product, audit, capacity, generic])
    document = build_sarif_log(state, {}, "fixture")
    assert len(document["runs"][0]["results"]) == 2
    assert len(document["runs"][0]["properties"]["receiptAudit"]) == 1
    assert len(document["runs"][0]["properties"]["providerDiagnostics"]) == 1
    return document


def test_inline_public_result_preserves_sarif_projection(report):
    stdout = " \n" + json.dumps(report) + "\n\t"
    stderr = " diagnostic\n\n"
    result = server._make_result(stdout, 1, 12.345, stderr)
    data = result.structuredContent
    assert data["findings_count"] == 2, "published SARIF findings must be counted"
    assert data["findings"] == report["runs"][0]["results"]
    assert data["receipt_audit"] == report["runs"][0]["properties"]["receiptAudit"]
    assert data["verdict"] == "FAIL" and data["exit_code"] == 1
    assert data["duration_s"] == 12.35
    assert data["output"] == stdout + "\n--- stderr ---\n" + stderr
    assert result.content[0].text == data["output"]


async def consume(monkeypatch, route, stdout, *, status="completed", exit_code=1,
                  verdict="FAIL", stderr="", duration=12.345):
    if route == "inline":
        result = server._make_result(stdout, exit_code, duration, stderr)
        return result, result.structuredContent
    entry = {"status": status, "result": {
        "stdout": stdout, "stderr": stderr, "exit_code": exit_code,
        "verdict": verdict, "duration_s": duration,
    }}
    before = deepcopy(entry)
    monkeypatch.setattr(server, "get_job", lambda job_id: entry)
    result = (await server.mcp.call_tool("forge_job_status", {"job_id": "job"})
              if route == "sdk" else await server.forge_job_status("job"))
    assert result.isError is False
    assert entry == before
    return result, result.structuredContent["result"]


ROUTES = ["inline", "handler", "sdk"]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("status,exit_code,verdict", [
    ("completed", 1, "FAIL"), ("completed", 0, "PASS"),
    ("failed", 6, "TIMEOUT"), ("failed", 0, "CLI_ERROR"),
])
async def test_actual_sarif_routes_keep_text_and_metadata(
    monkeypatch, report, route, status, exit_code, verdict
):
    stdout = " \n" + json.dumps(report) + "\n\t"
    stderr = " diagnostic\n\n"
    result, data = await consume(monkeypatch, route, stdout, status=status,
                                 exit_code=exit_code, verdict=verdict, stderr=stderr)
    assert data["findings_count"] == len(report["runs"][0]["results"])
    assert data["findings"] == report["runs"][0]["results"]
    assert data["receipt_audit"] == report["runs"][0]["properties"]["receiptAudit"]
    assert data["verdict"] == (server.exit_to_verdict(exit_code) if route == "inline" else verdict)
    assert data["exit_code"] == exit_code
    assert data["duration_s"] == (12.35 if route == "inline" else 12.345)
    expected = stdout
    if exit_code != 0 or (route != "inline" and status == "failed"):
        expected += "\n--- stderr ---\n" + stderr
    assert data["output"] == expected
    if route == "inline":
        assert result.content[0].text == expected
    else:
        assert result.content[0].text == f"Job job {status}: {verdict} (exit {exit_code})\n" + expected
    assert result.model_dump_json().encode("utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("count", [0, 1, 21])
@pytest.mark.parametrize("audit", ["missing", "empty", "present"])
async def test_known_results_and_independent_audit(monkeypatch, report, route, count, audit):
    document = deepcopy(report)
    run = document["runs"][0]
    run["results"] = [deepcopy(run["results"][0]) for _ in range(count)]
    if audit == "missing":
        del run["properties"]["receiptAudit"]
    elif audit == "empty":
        run["properties"]["receiptAudit"] = []
    result, data = await consume(monkeypatch, route, json.dumps(document))
    assert data["findings_count"] == count
    assert data["findings"] == run["results"]
    assert data["receipt_audit"] == run["properties"].get("receiptAudit")
    assert "disposition" not in data["findings"][0] if count else data["findings"] == []
    assert "providerDiagnostics" not in data
    assert result.model_dump_json().encode("utf-8")


def damaged_report(report, damage):
    document = deepcopy(report)
    run = document["runs"][0]
    if damage == "root-list":
        return json.dumps([document])
    if damage == "version":
        document["version"] = "2.0.0"
    elif damage == "missing-runs":
        del document["runs"]
    elif damage == "runs-null":
        document["runs"] = None
    elif damage == "runs-object":
        document["runs"] = {}
    elif damage == "runs-empty":
        document["runs"] = []
    elif damage == "runs-many":
        document["runs"].append(deepcopy(run))
    elif damage == "run-null":
        document["runs"] = [None]
    elif damage == "tool-null":
        run["tool"] = None
    elif damage == "driver-null":
        run["tool"]["driver"] = None
    elif damage == "producer":
        run["tool"]["driver"]["name"] = "other-tool"
    elif damage == "missing-results":
        del run["results"]
    elif damage == "results-null":
        run["results"] = None
    elif damage == "results-object":
        run["results"] = {}
    elif damage == "result-string":
        run["results"].append("not a result")
    elif damage == "properties-null":
        run["properties"] = None
    elif damage == "properties-list":
        run["properties"] = []
    elif damage == "audit-null":
        run["properties"]["receiptAudit"] = None
    elif damage == "audit-object":
        run["properties"]["receiptAudit"] = {}
    elif damage == "audit-string":
        run["properties"]["receiptAudit"].append("not an audit")
    elif damage == "surrogate-result":
        run["results"][0]["message"]["text"] = "\ud800"
    elif damage == "surrogate-audit":
        run["properties"]["receiptAudit"][0]["description"] = "\udfff"
    else:
        raise AssertionError(damage)
    return json.dumps(document)


DAMAGES = [
    "root-list", "version", "missing-runs", "runs-null", "runs-object", "runs-empty",
    "runs-many", "run-null", "tool-null", "driver-null", "producer", "missing-results",
    "results-null", "results-object", "result-string", "properties-null", "properties-list",
    "audit-null", "audit-object", "audit-string", "surrogate-result", "surrogate-audit",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("damage", DAMAGES)
async def test_malformed_reports_remain_unknown(monkeypatch, report, route, damage):
    stdout = damaged_report(report, damage)
    result, data = await consume(monkeypatch, route, stdout, stderr=" original stderr\n")
    assert data["findings_count"] is None
    assert data["findings"] is None and data["receipt_audit"] is None
    assert data["output"] == stdout + "\n--- stderr ---\n original stderr\n"
    assert result.model_dump_json().encode("utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("invalid", ["empty", "fenced", "trailing", "two-documents", "duplicate",
                                      "nested-duplicate", "NaN", "Infinity", "-Infinity",
                                      "1e309", "-1e309", "deep"])
async def test_invalid_json_is_not_extracted(monkeypatch, report, route, invalid):
    stdout = json.dumps(report)
    if invalid == "empty":
        stdout = ""
    elif invalid == "fenced":
        stdout = "```json\n" + stdout + "\n```"
    elif invalid == "trailing":
        stdout += "\n[advisory] not another JSON document"
    elif invalid == "two-documents":
        stdout += stdout
    elif invalid == "duplicate":
        stdout = stdout.replace('"version": "2.1.0"', '"version": "2.1.0", "version": "2.1.0"')
    elif invalid == "nested-duplicate":
        stdout = stdout.replace('"message": {', '"message": {"text": "duplicate", ', 1)
    elif invalid == "deep":
        stdout = "[" * 1100 + "0" + "]" * 1100
    else:
        stdout = stdout[:-1] + ', "ignored": {"nested": ' + invalid + "}}"
    result, data = await consume(monkeypatch, route, stdout)
    assert data["findings_count"] is None
    assert data["findings"] is None and data["receipt_audit"] is None
    assert data["output"] == stdout
    assert result.model_dump_json().encode("utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
async def test_stderr_sarif_is_not_structured_input(monkeypatch, report, route):
    stderr = json.dumps(report)
    _result, data = await consume(monkeypatch, route, "plain CLI diagnostic", stderr=stderr)
    assert data["findings_count"] is None
    assert data["findings"] is None and data["receipt_audit"] is None
    assert data["output"] == "plain CLI diagnostic\n--- stderr ---\n" + stderr


def test_valid_utf8_and_finite_metadata_are_preserved(report):
    document = deepcopy(report)
    document["runs"][0]["results"][0]["message"]["text"] = "\U0001f600 caf\u00e9"
    document["runs"][0]["results"][0]["properties"]["number"] = 1.25
    result = server._make_result(json.dumps(document, ensure_ascii=False), 1, 0)
    assert result.structuredContent["findings"] == document["runs"][0]["results"]
    assert result.model_dump_json().encode("utf-8")


@pytest.mark.parametrize("extra", [0, 1], ids=["exact-character-cap", "over-character-cap"])
def test_report_character_limit_keeps_original_text(report, extra):
    stdout = json.dumps(report)
    stdout += " " * (server._MAX_SARIF_REPORT_CHARS - len(stdout) + extra)
    result = server._make_result(stdout, 1, 0)
    assert result.structuredContent["findings_count"] == (None if extra else 2)
    assert result.structuredContent["output"] == stdout
    if extra:
        assert result.structuredContent["findings"] is None
        assert result.structuredContent["receipt_audit"] is None


@pytest.mark.parametrize("operation", ["loads", "dumps"])
def test_memory_error_propagates(report, monkeypatch, operation):
    stdout = json.dumps(report)
    def fail(*args, **kwargs):
        raise MemoryError("fixture allocation")
    monkeypatch.setattr(server.json, operation, fail)
    with pytest.raises(MemoryError, match="fixture allocation"):
        server._make_result(stdout, 1, 0)


def test_nonstring_projection_is_unknown():
    assert server._sarif_result_projection(None) == (None, None, None)


def test_sdk_serialization_rejects_unpaired_structured_surrogate():
    from pydantic_core import PydanticSerializationError

    result = server._make_inprocess_result(
        Verdict.FAIL, 1, 0, findings=[{"message": {"text": "\ud800"}}]
    )
    with pytest.raises((UnicodeEncodeError, PydanticSerializationError)):
        result.model_dump_json().encode("utf-8")


def full_wire_result(result):
    # Match BaseSession._send_response and the stdio writer's final envelope.
    response = JSONRPCResponse(
        jsonrpc="2.0", id=1,
        result=result.model_dump(by_alias=True, mode="json", exclude_none=True),
    )
    wire = JSONRPCMessage(response).model_dump_json(by_alias=True, exclude_none=True)
    return json.loads(wire.encode("utf-8"))["result"]


def nested_containers(count, kind, leaf=0):
    value = leaf
    for index in range(count):
        value = {"child": value} if kind == "dict" or (kind == "mixed" and index % 2) else [value]
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
async def test_deep_report_keeps_full_mcp_response_serializable(monkeypatch, route):
    document = build_sarif_log(State(mode=Mode.CI, verdict=Verdict.FAIL), {}, "probe")
    document["runs"][0]["results"] = [{
        "message": {"text": "depth witness"},
        "properties": {"nested": nested_containers(300, "list")},
    }]
    stdout = json.dumps(document)
    assert len(stdout) == 920
    result, data = await consume(monkeypatch, route, stdout)
    error = None
    try:
        full_wire_result(result)
    except (ValueError, UnicodeError) as exc:
        error = type(exc).__name__
    assert error is None, "SDK_SAFE_PROJECTION: valid raw output must retain serializable MCP result"
    assert data["findings_count"] is None
    assert data["findings"] is None and data["receipt_audit"] is None
    assert data["output"] == stdout
    assert data["verdict"] == "FAIL" and data["exit_code"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("section", ["results", "receiptAudit"])
@pytest.mark.parametrize("kind", ["list", "dict", "mixed"])
@pytest.mark.parametrize("depth", [64, 65])
async def test_projection_container_depth_boundary(
    monkeypatch, report, route, section, kind, depth
):
    document = deepcopy(report)
    run = document["runs"][0]
    rows = run["results"] if section == "results" else run["properties"]["receiptAudit"]
    # Projected list + row dict + properties dict are three containers.
    # The raw report adds three wrappers for results and four for receiptAudit.
    rows[0]["properties"] = {"nested": nested_containers(depth - 3, kind)}
    stdout = json.dumps(document)
    result, data = await consume(monkeypatch, route, stdout)
    if depth == 64:
        assert data["findings_count"] == len(run["results"])
        assert data["findings"] == run["results"]
        assert data["receipt_audit"] == run["properties"]["receiptAudit"]
    else:
        assert data["findings_count"] is None
        assert data["findings"] is None and data["receipt_audit"] is None
    assert data["output"] == stdout
    wire = full_wire_result(result)
    wire_data = wire["structuredContent"]
    if route != "inline":
        wire_data = wire_data["result"]
    assert wire_data["findings"] == data["findings"]
    assert wire_data["receipt_audit"] == data["receipt_audit"]
    assert wire_data["output"] == stdout


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("leaf", [[], {}], ids=["empty-list", "empty-dict"])
async def test_empty_container_still_counts_at_depth_limit(monkeypatch, report, route, leaf):
    document = deepcopy(report)
    # Three projection wrappers +61 array wrappers +the empty leaf =65.
    document["runs"][0]["results"][0]["properties"] = {
        "nested": nested_containers(61, "list", leaf),
    }
    stdout = json.dumps(document)
    result, data = await consume(monkeypatch, route, stdout)
    assert data["findings_count"] is None
    assert data["findings"] is None and data["receipt_audit"] is None
    assert data["output"] == stdout
    assert full_wire_result(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
async def test_unprojected_depth_does_not_refuse_shallow_findings(monkeypatch, report, route):
    document = deepcopy(report)
    document["runs"][0]["properties"]["providerDiagnostics"][0]["ignored"] = nested_containers(300, "list")
    stdout = json.dumps(document)
    result, data = await consume(monkeypatch, route, stdout)
    assert data["findings_count"] == 2
    assert data["findings"] == document["runs"][0]["results"]
    assert data["receipt_audit"] == document["runs"][0]["properties"]["receiptAudit"]
    assert data["output"] == stdout
    assert full_wire_result(result)
