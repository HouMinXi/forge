"""Opt-in MCP production reader uses original bytes, including queued jobs."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from code_forge import mcp_jobs as jobs, mcp_server as server
from code_forge.report_runtime_v2 import select_report_bytes, selection_metadata
from tests.test_report_reader_v2 import block, document, raw


def reader_evidence(selection):
    return {**selection_metadata(selection), "block": selection.block}


def cases():
    return [
        raw(document()),
        raw(document(block("v1"))),
        raw(document())[:-1] + b',"invalid":"\xff"}',
        b"",
    ]


@pytest.mark.parametrize("payload", cases(), ids=["v2", "v1-fallback", "invalid-utf8", "missing"])
def test_inline_reader_selects_original_bytes(payload):
    selected = select_report_bytes(payload)
    result = server._make_result(payload, 7, 1.25, report_version="v2")
    # Actual SDK serialization must retain reader evidence without converting bytes.
    encoded = json.loads(result.model_dump_json())
    structured = encoded["structuredContent"]
    assert structured["report_reader"] == reader_evidence(selected)
    assert structured["report_classification"] == server._report_classification_from_stdout(
        payload.decode(errors="replace")
    )
    assert structured["exit_code"] == 7 and structured["verdict"] == "UNRELIABLE"
    assert structured["output"] == payload.decode(errors="replace")


def test_v1_sdk_wire_has_no_opt_in_null_field():
    result = server._make_result(raw(document(block("v1"))).decode(), 0, 0)
    assert "report_reader" not in json.loads(result.model_dump_json())["structuredContent"]
    model = jobs.ForgeResult(verdict="PASS", exit_code=0, duration_s=0, output="")
    nested = jobs.ForgeJobRef(job_id="fixed", status="completed", result=model)
    assert "report_reader" not in json.loads(nested.model_dump_json())["result"]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", cases(), ids=["v2", "v1-fallback", "invalid-utf8", "missing"])
async def test_job_reader_uses_creation_version_and_original_bytes(payload):
    task = asyncio.create_task(asyncio.sleep(0, result=(payload, b"")))
    proc = SimpleNamespace(returncode=0)
    job_id = jobs.start_job(task, proc, report_version="v2")
    try:
        entry = jobs.get_job(job_id)
        await entry["wait_task"]
        assert entry["report_version"] == "v2"
        result = await server.forge_job_status(job_id)
        structured = json.loads(result.model_dump_json())["structuredContent"]["result"]
        expected = select_report_bytes(payload)
        assert structured["report_reader"] == reader_evidence(expected)
        assert structured["report_classification"] == server._report_classification_from_stdout(
            payload.decode(errors="replace")
        )
        assert structured["output"] == payload.decode(errors="replace")
    finally:
        jobs._jobs.pop(job_id, None)


@pytest.mark.asyncio
async def test_lost_job_stdout_is_missing_not_reencoded():
    jobs._jobs["lost"] = dict(
        status="failed",
        report_version="v2",
        created_at=0,
        finished_at=__import__("time").monotonic(),
        result=dict(stdout=raw(document()).decode(), exit_code=6),
    )
    try:
        result = await server.forge_job_status("lost")
        structured = result.structuredContent["result"]
        assert structured["report_reader"] == reader_evidence(select_report_bytes(b""))
        assert structured["report_classification"] == server._report_classification_from_stdout(
            raw(document()).decode()
        )
    finally:
        jobs._jobs.pop("lost", None)


@pytest.mark.asyncio
async def test_review_v2_requests_ci_bytes_before_decode(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_workspace_for", AsyncMock(return_value=tmp_path))
    monkeypatch.setattr(server, "_check_backend", lambda _: None)
    monkeypatch.setattr(server, "_validate_backend", lambda *_: None)
    payload = cases()[2]
    run = AsyncMock(return_value=(payload, 0, 0.1, ""))
    monkeypatch.setattr(server, "_run_cli_budgeted", run)
    result = await server.forge_review(report_version="v2", timeout_s=3)
    args, kwargs = run.call_args
    assert args[:2] == ("review", "--no-color")
    assert args[2:6] == ("--report-version", "v2", "--mode", "ci")
    assert kwargs["preserve_stdout_bytes"] is True
    assert result.structuredContent["report_reader"] == reader_evidence(select_report_bytes(payload))


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, [], 2, "v3"])
async def test_invalid_version_rejected_before_workspace(monkeypatch, version):
    workspace = AsyncMock()
    monkeypatch.setattr(server, "_workspace_for", workspace)
    with pytest.raises(server.ToolError, match="report_version"):
        await server.forge_review(report_version=version)
    workspace.assert_not_called()


@pytest.mark.asyncio
async def test_actual_sdk_review_and_gate_schema(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_workspace_for", AsyncMock(return_value=tmp_path))
    monkeypatch.setattr(server, "_check_backend", lambda _: None)
    monkeypatch.setattr(server, "_validate_backend", lambda *_: None)
    run = AsyncMock(return_value=(raw(document()), 0, 0.1, ""))
    monkeypatch.setattr(server, "_run_cli_budgeted", run)
    response = await server.mcp.call_tool("forge_review", {"report_version": "v2", "timeout_s": 3})
    # FastMCP returns the real SDK CallToolResult for this annotated tool.
    encoded = json.loads(response.model_dump_json())
    assert encoded["structuredContent"]["report_reader"]["requested_version"] == "v2"
    tools = await server.mcp.list_tools()
    gate = next(tool for tool in tools if tool.name == "forge_gate_check")
    assert gate.inputSchema["properties"]["report_version"]["default"] == "v1"
    run.reset_mock()
    with pytest.raises(server.ToolError, match="supports only report_version=v1"):
        await server.mcp.call_tool("forge_gate_check", {"report_version": "v2"})
    run.assert_not_called()


@pytest.mark.asyncio
async def test_background_dispatch_pins_v2(monkeypatch, tmp_path):
    task = asyncio.create_task(asyncio.sleep(0, result=(raw(document()), b"")))
    proc = SimpleNamespace(returncode=0)
    monkeypatch.setattr(server, "_run_cli_budgeted", AsyncMock(return_value=(task, proc, None)))
    result = await server._dispatch_cli(["review"], tmp_path, None, report_version="v2")
    job_id = result.structuredContent["job_id"]
    try:
        entry = jobs.get_job(job_id)
        assert entry["report_version"] == "v2"
        await entry["wait_task"]
        polled = await server.forge_job_status(job_id)
        assert polled.structuredContent["result"]["report_reader"]["selected_version"] == "v2"
    finally:
        jobs._jobs.pop(job_id, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("preserve", [False, True])
async def test_budgeted_runner_does_not_decode_opt_in_bytes(monkeypatch, tmp_path, preserve):
    payload = cases()[2]
    proc = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(payload, None)))
    monkeypatch.setattr(server.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    result = await server._run_cli_budgeted("review", workspace=tmp_path, preserve_stdout_bytes=preserve)
    assert result[0] == (payload if preserve else payload.decode(errors="replace"))
    if preserve:
        assert result[0] is payload


@pytest.mark.asyncio
async def test_timeout_boundary_job_has_explicit_missing_selection():
    task = asyncio.create_task(asyncio.sleep(60, result=(raw(document()), b"")))
    job_id = jobs.start_job(task, SimpleNamespace(returncode=0), report_version="v2", max_lifetime_s=0)
    try:
        entry = jobs.get_job(job_id)
        await entry["wait_task"]
        assert entry["result"]["stdout_lost"] is True
        response = await server.forge_job_status(job_id)
        assert response.structuredContent["result"]["report_reader"] == reader_evidence(
            select_report_bytes(b"")
        )
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        jobs._jobs.pop(job_id, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [{"canary": True}, {"whole_file": ["file.py"]}])
async def test_v2_unsupported_modes_rejected_before_workspace(monkeypatch, extra):
    workspace = AsyncMock()
    monkeypatch.setattr(server, "_workspace_for", workspace)
    with pytest.raises(server.ToolError, match="does not support"):
        await server.forge_review(report_version="v2", **extra)
    workspace.assert_not_called()


@pytest.mark.asyncio
async def test_actual_sdk_legacy_and_nested_job_wire(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_workspace_for", AsyncMock(return_value=tmp_path))
    monkeypatch.setattr(server, "_check_backend", lambda _: None)
    monkeypatch.setattr(server, "_validate_backend", lambda *_: None)
    legacy = raw(document(block("v1"))).decode()
    run = AsyncMock(return_value=(legacy, 0, 0.1, ""))
    monkeypatch.setattr(server, "_run_cli_budgeted", run)
    response = await server.mcp.call_tool("forge_review", {"timeout_s": 3})
    value = json.loads(response.model_dump_json())["structuredContent"]
    assert "report_reader" not in value
    assert value == server._make_result(legacy, 0, 0.1).structuredContent
    assert "--report-version" not in run.call_args.args
    tools = await server.mcp.list_tools()
    review = next(tool for tool in tools if tool.name == "forge_review")
    assert review.inputSchema["properties"]["report_version"]["default"] == "v1"
    for version in ("v1", "v2"):
        task = asyncio.create_task(asyncio.sleep(0, result=(raw(document()), b"")))
        job_id = jobs.start_job(task, SimpleNamespace(returncode=0), report_version=version)
        try:
            await jobs.get_job(job_id)["wait_task"]
            polled = await server.mcp.call_tool("forge_job_status", {"job_id": job_id})
            result = json.loads(polled.model_dump_json())["structuredContent"]["result"]
            assert ("report_reader" in result) is (version == "v2")
            if version == "v2":
                assert result["report_reader"] == reader_evidence(select_report_bytes(raw(document())))
        finally:
            jobs._jobs.pop(job_id, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["mutation", "mixed"])
@pytest.mark.parametrize("route", ["inline", "job"])
async def test_real_emitter_subprocess_bytes_reach_sdk(monkeypatch, tmp_path, kind, route):
    """Real saved-state emitter subprocess, not full review/provider qualification."""
    import subprocess
    import sys
    from pathlib import Path

    from code_forge.state import Verdict, save_state
    from tests.test_report_baseline_v2 import fixture_state

    state = fixture_state("mutation")
    if kind == "mixed":
        state.findings.extend(fixture_state("product").findings)
    state.verdict = Verdict.FAIL
    state_path = tmp_path / "state.json"
    save_state(state, state_path)
    source = Path(server.__file__).parents[1]
    code = """
import sys
from pathlib import Path
from code_forge.cli import _emit_ci_output

def offline(event, args):
    if event.startswith(('socket.', 'subprocess.', 'os.system', 'os.exec', 'os.fork', 'os.posix_spawn')):
        raise AssertionError('unexpected external operation')
sys.addaudithook(offline)
_emit_ci_output(Path(sys.argv[1]), {}, report_version='v2')
# Fixture-selected FAIL exit; this deliberately does not execute the review pipeline.
sys.exit(1)
"""
    child = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-B", "-c", code, str(state_path)],
        cwd=tmp_path,
        env={"PYTHONPATH": str(source), "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        timeout=15,
    )
    assert child.returncode == 1 and b"Traceback" not in child.stderr
    selected = select_report_bytes(child.stdout)
    assert selected.selected_version == "v2"
    assert selected.block["execution"]
    assert bool(selected.block["product"]) is (kind == "mixed")
    if route == "inline":
        monkeypatch.setattr(server, "_workspace_for", AsyncMock(return_value=tmp_path))
        monkeypatch.setattr(server, "_check_backend", lambda _: None)
        monkeypatch.setattr(server, "_validate_backend", lambda *_: None)
        monkeypatch.setattr(
            server,
            "_run_cli_budgeted",
            AsyncMock(return_value=(child.stdout, child.returncode, 0.1, child.stderr.decode())),
        )
        result = await server.mcp.call_tool("forge_review", {"report_version": "v2", "timeout_s": 3})
        structured = json.loads(result.model_dump_json())["structuredContent"]
    else:
        task = asyncio.create_task(asyncio.sleep(0, result=(child.stdout, child.stderr)))
        job_id = jobs.start_job(task, SimpleNamespace(returncode=child.returncode), report_version="v2")
        try:
            await jobs.get_job(job_id)["wait_task"]
            result = await server.mcp.call_tool("forge_job_status", {"job_id": job_id})
            structured = json.loads(result.model_dump_json())["structuredContent"]["result"]
        finally:
            jobs._jobs.pop(job_id, None)
    assert structured["exit_code"] == child.returncode and structured["verdict"] == "FAIL"
    assert structured["report_reader"] == reader_evidence(selected)
    assert structured["report_classification"] == server._report_classification_from_stdout(
        child.stdout.decode()
    )
    assert structured["output"] == child.stdout.decode() + "\n--- stderr ---\n" + child.stderr.decode()
