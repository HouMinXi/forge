# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Terminal MCP text must retain the already-built structured output."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import socket
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from mcp.types import CallToolResult, TextContent

from code_forge import mcp_server as server


TERMINAL_CASES = [
    pytest.param("completed", "PASS", 0, id="completed-pass-0"),
    pytest.param("completed", "FAIL", 1, id="completed-fail-1"),
    pytest.param("failed", "CLI_ERROR", 0, id="failed-cli-error-0"),
    pytest.param("failed", "TIMEOUT", 124, id="failed-timeout-124"),
]
OUTPUT = " \tReview output: café\n[advisory] Phase 3 skipped\n--- stderr ---\n\n"
STDERR = " \tdiagnostic with trailing whitespace \n\n"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """These formatting fixtures must not contact a provider or launch a CLI."""
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


async def poll(sdk: bool) -> CallToolResult:
    if sdk:
        result = await server.mcp.call_tool("forge_job_status", {"job_id": "job"})
    else:
        result = await server.forge_job_status("job")
    assert isinstance(result, CallToolResult)
    assert len(result.content) == 1
    assert isinstance(result.content[0], TextContent)
    assert result.isError is False
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("sdk", [False, True], ids=["handler", "sdk"])
@pytest.mark.parametrize("status,verdict,exit_code", TERMINAL_CASES)
async def test_terminal_text_preserves_exact_structured_output(
    monkeypatch, sdk, status, verdict, exit_code
):
    entry = {
        "status": status,
        "result": {
            "stdout": OUTPUT,
            "stderr": STDERR,
            "verdict": verdict,
            "exit_code": exit_code,
            "duration_s": 12.345,
        },
    }
    before = deepcopy(entry)
    monkeypatch.setattr(server, "get_job", lambda job_id: entry)

    result = await poll(sdk)

    expected_output = OUTPUT
    if status == "failed" or exit_code != 0:
        expected_output += "\n--- stderr ---\n" + STDERR
    assert result.structuredContent == {
        "job_id": "job",
        "status": status,
        "poll_after_seconds": None,
        "result": {
            "verdict": verdict,
            "exit_code": exit_code,
            "findings_count": None,
            "findings": None,
            "receipt_audit": None,
            "duration_s": 12.345,
            "output": expected_output,
        },
    }
    header = f"Job job {status}: {verdict} (exit {exit_code})"
    assert result.content[0].text == header + "\n" + result.structuredContent["result"]["output"]
    assert entry == before


@pytest.mark.asyncio
@pytest.mark.parametrize("status,verdict,exit_code", TERMINAL_CASES)
@pytest.mark.parametrize("output", ["", " \t\r\n"], ids=["empty", "whitespace"])
async def test_terminal_empty_and_whitespace_output(monkeypatch, status, verdict, exit_code, output):
    monkeypatch.setattr(server, "get_job", lambda job_id: {
        "status": status,
        "result": {"stdout": output, "stderr": " \t\n", "verdict": verdict, "exit_code": exit_code},
    })

    result = await poll(True)

    assert result.structuredContent["result"]["output"] == output
    header = f"Job job {status}: {verdict} (exit {exit_code})"
    assert result.content[0].text == header + ("\n" + output if output else "")


@pytest.mark.parametrize("exit_code,verdict", [(0, "PASS"), (1, "FAIL"), (2, "CLI_ERROR"), (6, "TIMEOUT")])
@pytest.mark.parametrize("stdout", [OUTPUT, "", " \t\n"], ids=["advisory", "empty", "whitespace"])
def test_immediate_result_keeps_current_text_format(exit_code, verdict, stdout):
    result = server._make_result(stdout, exit_code, 12.345, STDERR)
    expected_output = stdout if exit_code == 0 else stdout + "\n--- stderr ---\n" + STDERR
    assert isinstance(result, CallToolResult)
    assert result.content == [TextContent(type="text", text=expected_output)]
    assert result.structuredContent == {
        "verdict": verdict,
        "exit_code": exit_code,
        "findings_count": None,
        "findings": None,
        "receipt_audit": None,
        "duration_s": 12.35,
        "output": expected_output,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("sdk", [False, True], ids=["handler", "sdk"])
@pytest.mark.parametrize("progress", ["", " \tprogress only\n\n"], ids=["empty", "progress"])
async def test_running_status_keeps_progress_format(monkeypatch, tmp_path, sdk, progress):
    log = tmp_path / "stderr.log"
    log.write_text(progress, encoding="utf-8")
    entry = {"status": "running", "result": None, "created_at": 10, "stderr_log_path": str(log)}
    before = deepcopy(entry)
    monkeypatch.setattr(server, "get_job", lambda job_id: entry)
    monkeypatch.setattr(server, "time", SimpleNamespace(monotonic=lambda: 15))

    result = await poll(sdk)

    expected_text = "Job job: running (5s)"
    if progress:
        expected_text += "\n--- progress ---\n" + progress.strip()
    assert result.content[0].text == expected_text
    assert result.structuredContent == {
        "job_id": "job", "status": "running", "poll_after_seconds": 10, "result": None,
    }
    assert entry == before
    assert log.read_text(encoding="utf-8") == progress


@pytest.mark.asyncio
async def test_cancelled_poll_does_not_change_running_job(monkeypatch):
    entry = {"status": "running", "result": None, "created_at": 10, "stderr_log_path": "owned.log"}
    before = deepcopy(entry)
    loop = asyncio.get_running_loop()
    entered, finished = asyncio.Event(), asyncio.Event()
    release = threading.Event()
    monkeypatch.setattr(server, "get_job", lambda job_id: entry)
    monkeypatch.setattr(server, "time", SimpleNamespace(monotonic=lambda: 15))

    def read_tail(snapshot):
        loop.call_soon_threadsafe(entered.set)
        try:
            assert snapshot == {"stderr_log_path": "owned.log"}
            assert snapshot is not entry
            assert release.wait(3), "poll worker was not released"
            return "late progress"
        finally:
            loop.call_soon_threadsafe(finished.set)

    monkeypatch.setattr(server, "_read_stderr_tail", read_tail)
    task = asyncio.create_task(server.forge_job_status("job"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert entry == before
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.wait_for(finished.wait(), timeout=3)
    assert entry == before
