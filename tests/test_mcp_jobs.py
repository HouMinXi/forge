# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for MCP job lifecycle module."""
from __future__ import annotations

import asyncio
import os
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from code_forge.mcp_jobs import (
    ForgeJobRef,
    ForgeResult,
    _evict_stale,
    _JOB_TTL_SECONDS,
    _jobs,
    _wait_for_job,
    cleanup_all,
    exit_to_verdict,
    get_job,
    start_job,
)


@pytest.fixture(autouse=True)
def clear_jobs():
    _jobs.clear()
    yield
    _jobs.clear()


# -- exit_to_verdict --


def test_exit_to_verdict_all_eight():
    assert exit_to_verdict(0) == "PASS"
    assert exit_to_verdict(1) == "FAIL"
    assert exit_to_verdict(2) == "CLI_ERROR"
    assert exit_to_verdict(3) == "BUSY"
    assert exit_to_verdict(4) == "ESCALATED"
    assert exit_to_verdict(5) == "DELEGATED"
    assert exit_to_verdict(6) == "TIMEOUT"
    assert exit_to_verdict(7) == "UNRELIABLE"
    assert exit_to_verdict(99).startswith("UNKNOWN")


# -- Pydantic models --


def test_forge_result_findings_count_default_none():
    r = ForgeResult(verdict="PASS", exit_code=0, duration_s=1.0, output="ok")
    assert r.findings_count is None


def test_forge_job_ref_fields():
    ref = ForgeJobRef(job_id="abc", status="running", poll_after_seconds=10)
    assert ref.result is None
    assert ref.poll_after_seconds == 10


# -- start_job --


@pytest.mark.asyncio
async def test_start_job_returns_uuid():
    async def _comm():
        return (b"output", b"")

    task = asyncio.ensure_future(_comm())
    proc = MagicMock()
    proc.returncode = None
    job_id = start_job(task, proc)
    # Valid UUID4 format
    import uuid

    uuid.UUID(job_id, version=4)
    assert _jobs[job_id]["status"] == "running"
    assert _jobs[job_id]["comm_task"] is task
    # Let the background _wait_for_job finish
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_start_job_stores_tempfile_path():
    async def _comm():
        return (b"output", b"")

    task = asyncio.ensure_future(_comm())
    proc = MagicMock()
    proc.returncode = 0
    job_id = start_job(task, proc, tempfile_path="/tmp/test.md")
    assert _jobs[job_id]["tempfile_path"] == "/tmp/test.md"
    await asyncio.sleep(0.05)


# -- get_job --


def test_get_job_unknown_returns_none():
    assert get_job("nonexistent") is None


def test_get_job_pops_completed():
    _jobs["j1"] = {
        "status": "completed",
        "result": {"stdout": "ok"},
        "created_at": time.monotonic(),
    }
    entry = get_job("j1")
    assert entry is not None
    assert entry["status"] == "completed"
    # Second call returns None (was popped)
    assert get_job("j1") is None


def test_get_job_keeps_running():
    _jobs["j2"] = {
        "status": "running",
        "result": None,
        "created_at": time.monotonic(),
    }
    assert get_job("j2") is not None
    assert get_job("j2") is not None  # still there


# -- _wait_for_job --


@pytest.mark.asyncio
async def test_wait_for_job_awaits_stored_task():
    async def _comm():
        return (b"output", b"")

    task = asyncio.ensure_future(_comm())
    proc = MagicMock()
    proc.returncode = 0
    job_id = start_job(task, proc)
    # Wait for background _wait_for_job that start_job spawned
    await asyncio.sleep(0.05)
    # The entry should now be completed (or popped by eviction)
    entry = _jobs.get(job_id)
    if entry:
        assert entry["status"] in ("completed", "failed")


@pytest.mark.asyncio
async def test_wait_for_job_deletes_tempfile():
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    )
    tmp.write("test contract")
    tmp.close()
    assert os.path.exists(tmp.name)

    async def _comm():
        return (b"output", b"")

    task = asyncio.ensure_future(_comm())
    proc = MagicMock()
    proc.returncode = 0
    job_id = start_job(task, proc, tempfile_path=tmp.name)
    # Wait for _wait_for_job to complete and delete the file
    await asyncio.sleep(0.1)
    assert not os.path.exists(tmp.name)


@pytest.mark.asyncio
async def test_wait_for_job_failed_nonzero_exit():
    async def _comm():
        return (b"output", b"")

    task = asyncio.ensure_future(_comm())
    proc = MagicMock()
    proc.returncode = 1
    job_id = start_job(task, proc)
    await asyncio.sleep(0.05)
    # Should not be "completed" with exit 1 -- wait, the code just checks
    # returncode for the verdict, status is still "completed" for non-zero
    # exits. "failed" is only for exceptions. Re-read the source:
    # entry["status"] = "completed" for all normal exits, "failed" for exceptions.
    entry = _jobs.get(job_id)
    if entry:
        assert entry["status"] == "completed"
        assert entry["result"]["exit_code"] == 1
        assert entry["result"]["verdict"] == "FAIL"


@pytest.mark.asyncio
async def test_wait_for_job_does_not_call_communicate():
    async def _comm():
        return (b"out", b"")

    task = asyncio.ensure_future(_comm())
    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = MagicMock()  # not AsyncMock -- if called, would fail
    job_id = start_job(task, proc)
    await asyncio.sleep(0.05)
    assert proc.communicate.call_count == 0


@pytest.mark.asyncio
async def test_wait_for_job_exception_sets_failed():
    async def _comm():
        raise RuntimeError("process died")

    task = asyncio.ensure_future(_comm())
    proc = MagicMock()
    proc.returncode = None
    job_id = start_job(task, proc)
    await asyncio.sleep(0.05)
    entry = _jobs.get(job_id)
    if entry:
        assert entry["status"] == "failed"
        assert "process died" in entry["result"]["stderr"]


# -- cleanup_all --


@pytest.mark.asyncio
async def test_cleanup_all_terminates_procs():
    proc1 = MagicMock()
    proc1.returncode = None
    proc1.wait = AsyncMock()
    proc2 = MagicMock()
    proc2.returncode = None
    proc2.wait = AsyncMock()
    _jobs["a"] = {"proc": proc1, "status": "running", "created_at": time.monotonic()}
    _jobs["b"] = {"proc": proc2, "status": "running", "created_at": time.monotonic()}
    await cleanup_all()
    proc1.terminate.assert_called_once()
    proc2.terminate.assert_called_once()
    assert len(_jobs) == 0


@pytest.mark.asyncio
async def test_cleanup_all_kills_on_wait_timeout():
    proc = MagicMock()
    proc.returncode = None
    proc.wait = AsyncMock(side_effect=asyncio.TimeoutError)
    _jobs["c"] = {"proc": proc, "status": "running", "created_at": time.monotonic()}
    await cleanup_all()
    proc.terminate.assert_called_once()
    proc.kill.assert_called_once()


# -- _evict_stale --


def test_evict_stale_removes_old_terminal():
    _jobs["old_done"] = {
        "status": "completed",
        "result": {"stdout": "ok"},
        "created_at": time.monotonic() - 7200,
    }
    _evict_stale()
    assert "old_done" not in _jobs


def test_evict_stale_kills_old_running_but_keeps_entry():
    proc = MagicMock()
    proc.returncode = None
    _jobs["old_run"] = {
        "status": "running",
        "proc": proc,
        "created_at": time.monotonic() - 7200,
    }
    _evict_stale()
    proc.kill.assert_called_once()
    # Entry stays -- _wait_for_job handles status update
    assert "old_run" in _jobs
