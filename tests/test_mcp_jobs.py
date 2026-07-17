# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for MCP job lifecycle module."""
from __future__ import annotations

import asyncio
import os
import signal
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from code_forge.mcp_jobs import (
    ForgeJobRef,
    ForgeResult,
    _evict_stale,
    _jobs,
    cleanup_all,
    exit_to_verdict,
    get_job,
    snapshot_tempfile_paths,
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


def test_get_job_idempotent_completed():
    _jobs["j1"] = {
        "status": "completed",
        "result": {"stdout": "ok"},
        "created_at": time.monotonic(),
    }
    entry = get_job("j1")
    assert entry is not None
    assert entry["status"] == "completed"
    # Second call returns same entry (idempotent, MCP-55)
    second = get_job("j1")
    assert second is not None
    assert second["status"] == "completed"


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
    start_job(task, proc, tempfile_path=tmp.name)
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
    start_job(task, proc)
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


@pytest.mark.asyncio
async def test_cleanup_all_cancels_wait_tasks_before_terminating():
    """cleanup_all must cancel _wait_for_job tasks before terminating subprocesses,
    so watchers cannot race on _jobs entries during teardown."""
    order: list[str] = []

    proc1 = await asyncio.create_subprocess_exec(
        "sleep", "60",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    proc2 = await asyncio.create_subprocess_exec(
        "sleep", "60",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    task1 = asyncio.create_task(proc1.communicate())
    task2 = asyncio.create_task(proc2.communicate())
    jid1 = start_job(task1, proc1)
    jid2 = start_job(task2, proc2)

    wt1 = _jobs[jid1]["wait_task"]
    wt2 = _jobs[jid2]["wait_task"]

    # Patch _terminate_and_reap to record that it runs AFTER wait_tasks cancel.
    from code_forge.mcp_jobs import _terminate_and_reap as original_terminate

    async def _tracked_terminate(proc, grace=5.0):
        # wait_tasks should already be done (cancelled) by now
        if wt1.done() and wt2.done():
            order.append("terminate_after_cancel")
        return await original_terminate(proc, grace)

    with patch("code_forge.mcp_jobs._terminate_and_reap", side_effect=_tracked_terminate):
        await cleanup_all()

    # Both subprocesses reaped
    assert proc1.returncode is not None
    assert proc2.returncode is not None
    # Wait tasks were cancelled
    assert wt1.cancelled() or wt1.done()
    assert wt2.cancelled() or wt2.done()
    # Ordering: cancel happened before terminate
    assert "terminate_after_cancel" in order
    # State fully cleared
    assert len(_jobs) == 0


# -- _evict_stale --


def test_evict_stale_removes_old_terminal():
    _jobs["old_done"] = {
        "status": "completed",
        "result": {"stdout": "ok"},
        "created_at": time.monotonic() - 7200,
    }
    _evict_stale()
    assert "old_done" not in _jobs


def test_evict_stale_leaves_running_entry_untouched():
    """Running entries survive past TTL -- cleanup_all owns forced death."""
    proc = MagicMock()
    proc.returncode = None
    _jobs["old_run"] = {
        "status": "running",
        "proc": proc,
        "created_at": time.monotonic() - 7200,
    }
    _evict_stale()
    proc.kill.assert_not_called()
    assert "old_run" in _jobs


# -- snapshot_tempfile_paths --


def test_snapshot_tempfile_paths_empty():
    assert snapshot_tempfile_paths() == []


def test_snapshot_tempfile_paths_collects_both_keys():
    _jobs["j1"] = {
        "tempfile_path": "/tmp/a.md",
        "stderr_log_path": "/tmp/a.log",
        "status": "running",
        "created_at": time.monotonic(),
    }
    _jobs["j2"] = {
        "tempfile_path": None,
        "stderr_log_path": "/tmp/b.log",
        "status": "running",
        "created_at": time.monotonic(),
    }
    paths = snapshot_tempfile_paths()
    assert "/tmp/a.md" in paths
    assert "/tmp/a.log" in paths
    assert "/tmp/b.log" in paths


# -- _terminate_and_reap --


@pytest.mark.asyncio
async def test_terminate_and_reap_terminates():
    proc = MagicMock()
    proc.returncode = None
    proc.pid = 12345  # valid int so os.getpgid() does not TypeError

    async def _wait_sets_returncode():
        proc.returncode = -15
        return None

    proc.wait = AsyncMock(side_effect=_wait_sets_returncode)
    from code_forge.mcp_jobs import _terminate_and_reap
    await _terminate_and_reap(proc)
    proc.terminate.assert_called_once()
    proc.wait.assert_called_once()
    proc.kill.assert_not_called()


@pytest.mark.asyncio
async def test_terminate_and_reap_kills_on_timeout():
    proc = MagicMock()
    proc.returncode = None
    proc.pid = 12345  # valid int so os.getpgid() does not TypeError
    call_count = 0

    async def _wait_side_effect():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise asyncio.TimeoutError
        return None

    proc.wait = AsyncMock(side_effect=_wait_side_effect)
    from code_forge.mcp_jobs import _terminate_and_reap
    await _terminate_and_reap(proc)
    proc.terminate.assert_called_once()
    proc.kill.assert_called_once()


@pytest.mark.asyncio
async def test_terminate_and_reap_skips_already_dead():
    proc = MagicMock()
    proc.returncode = 0
    proc.pid = 12345  # valid int so os.getpgid() does not TypeError
    proc.wait = AsyncMock()
    from code_forge.mcp_jobs import _terminate_and_reap
    await _terminate_and_reap(proc)
    proc.terminate.assert_not_called()
    proc.kill.assert_not_called()


@pytest.mark.asyncio
async def test_terminate_and_reap_catches_oserror_on_terminate():
    """OSError from terminate (process died mid-check) is caught."""
    proc = MagicMock()
    proc.returncode = None
    proc.pid = 99999  # nonexistent PID
    proc.terminate.side_effect = OSError("No such process")
    proc.wait = AsyncMock(return_value=None)
    from code_forge.mcp_jobs import _terminate_and_reap
    # Must not raise -- OSError is swallowed
    await _terminate_and_reap(proc)


@pytest.mark.asyncio
async def test_terminate_and_reap_uses_killpg_for_session_leader():
    """Session leader (pid == pgid) gets os.killpg, not proc.terminate."""
    proc = MagicMock()
    proc.returncode = None
    proc.pid = 12345

    async def _wait_sets_returncode():
        proc.returncode = -15
        return None

    proc.wait = AsyncMock(side_effect=_wait_sets_returncode)
    from code_forge.mcp_jobs import _terminate_and_reap
    with patch("code_forge.mcp_jobs.os.getpgid", return_value=12345), \
         patch("code_forge.mcp_jobs.os.killpg") as mock_killpg:
        await _terminate_and_reap(proc)
        mock_killpg.assert_called_once_with(12345, signal.SIGTERM)
        proc.terminate.assert_not_called()


@pytest.mark.asyncio
async def test_terminate_and_reap_falls_back_to_terminate_when_killpg_fails():
    """If killpg fails (not session leader), fall back to terminate."""
    proc = MagicMock()
    proc.returncode = None
    proc.pid = 12345

    async def _wait_sets_returncode():
        proc.returncode = -15
        return None

    proc.wait = AsyncMock(side_effect=_wait_sets_returncode)
    from code_forge.mcp_jobs import _terminate_and_reap
    with patch("code_forge.mcp_jobs.os.getpgid", return_value=12345), \
         patch("code_forge.mcp_jobs.os.killpg", side_effect=OSError("not leader")):
        await _terminate_and_reap(proc)
        proc.terminate.assert_called_once()


@pytest.mark.asyncio
async def test_terminate_and_reap_sigkill_uses_killpg_for_session_leader():
    """Session leader (pid == pgid) gets os.killpg(SIGKILL), not proc.kill."""
    proc = MagicMock()
    proc.returncode = None
    proc.pid = 12345
    call_count = 0

    async def _wait_side_effect():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise asyncio.TimeoutError
        proc.returncode = -9
        return None

    proc.wait = AsyncMock(side_effect=_wait_side_effect)
    from code_forge.mcp_jobs import _terminate_and_reap
    with patch("code_forge.mcp_jobs.os.getpgid", return_value=12345), \
         patch("code_forge.mcp_jobs.os.killpg") as mock_killpg:
        await _terminate_and_reap(proc)
        # killpg called for both SIGTERM and SIGKILL; verify SIGKILL specifically
        assert mock_killpg.call_args_list[-1] == call(12345, signal.SIGKILL)
        proc.kill.assert_not_called()


@pytest.mark.asyncio
async def test_terminate_and_reap_sigkill_pgid_none_falls_back_to_kill():
    """When pgid is None, SIGKILL uses proc.kill instead of killpg."""
    proc = MagicMock()
    proc.returncode = None
    proc.pid = 12345
    call_count = 0

    async def _wait_side_effect():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise asyncio.TimeoutError
        proc.returncode = -9
        return None

    proc.wait = AsyncMock(side_effect=_wait_side_effect)
    from code_forge.mcp_jobs import _terminate_and_reap
    with patch("code_forge.mcp_jobs.os.getpgid", side_effect=OSError("no such process")):
        await _terminate_and_reap(proc)
        proc.kill.assert_called()


@pytest.mark.asyncio
async def test_terminate_and_reap_sigkill_pgid_not_equal_pid_falls_back_to_kill():
    """When pgid != pid (not session leader), SIGKILL uses proc.kill."""
    proc = MagicMock()
    proc.returncode = None
    proc.pid = 12345
    call_count = 0

    async def _wait_side_effect():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise asyncio.TimeoutError
        proc.returncode = -9
        return None

    proc.wait = AsyncMock(side_effect=_wait_side_effect)
    from code_forge.mcp_jobs import _terminate_and_reap
    with patch("code_forge.mcp_jobs.os.getpgid", return_value=99999):
        await _terminate_and_reap(proc)
        proc.kill.assert_called()


@pytest.mark.asyncio
async def test_terminate_and_reap_sigkill_killpg_oserror_falls_back_to_kill():
    """When killpg raises OSError during SIGKILL, falls back to proc.kill."""
    proc = MagicMock()
    proc.returncode = None
    proc.pid = 12345
    call_count = 0

    async def _wait_side_effect():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise asyncio.TimeoutError
        proc.returncode = -9
        return None

    proc.wait = AsyncMock(side_effect=_wait_side_effect)
    from code_forge.mcp_jobs import _terminate_and_reap
    with patch("code_forge.mcp_jobs.os.getpgid", return_value=12345), \
         patch("code_forge.mcp_jobs.os.killpg", side_effect=OSError("permission denied")):
        await _terminate_and_reap(proc)
        proc.kill.assert_called()


@pytest.mark.asyncio
async def test_terminate_and_reap_sigkill_both_fail_no_raise():
    """When both killpg and proc.kill raise OSError, no exception escapes."""
    proc = MagicMock()
    proc.returncode = None
    proc.pid = 12345
    call_count = 0

    async def _wait_side_effect():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise asyncio.TimeoutError
        return None  # stays None: D-state child

    proc.wait = AsyncMock(side_effect=_wait_side_effect)
    proc.kill.side_effect = OSError("process not found")
    from code_forge.mcp_jobs import _terminate_and_reap
    with patch("code_forge.mcp_jobs.os.getpgid", return_value=12345), \
         patch("code_forge.mcp_jobs.os.killpg", side_effect=OSError("permission denied")):
        # Must not raise
        await _terminate_and_reap(proc)


# -- watchdog --


@pytest.mark.asyncio
async def test_watchdog_kills_on_timeout():
    """Sleeping subprocess + tiny cap -> status failed, verdict TIMEOUT."""
    proc = await asyncio.create_subprocess_exec(
        "sleep", "60",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    inner_task = asyncio.create_task(proc.communicate())
    job_id = start_job(inner_task, proc, max_lifetime_s=0.5)
    # Wait for watchdog to fire
    await asyncio.sleep(1.0)
    entry = _jobs.get(job_id)
    assert entry is not None
    assert entry["status"] == "failed"
    assert entry["result"]["verdict"] == "TIMEOUT"
    assert entry["result"]["exit_code"] is not None


@pytest.mark.asyncio
async def test_watchdog_reaps_proc():
    """After timeout, proc.returncode is not None."""
    proc = await asyncio.create_subprocess_exec(
        "sleep", "60",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    inner_task = asyncio.create_task(proc.communicate())
    start_job(inner_task, proc, max_lifetime_s=0.5)
    await asyncio.sleep(1.0)
    assert proc.returncode is not None


@pytest.mark.asyncio
async def test_watchdog_stderr_tail_preserved():
    """Child stderr marker survives timeout into result.stderr.

    When stderr is redirected to a log file (the real production path via
    _run_cli_budgeted), the watchdog reads the tail before unlinking.
    The PIPE path cannot capture output after task cancellation, so this
    test uses a log file to mirror production behavior.
    """
    stderr_fh = tempfile.NamedTemporaryFile(
        mode="w", suffix=".log", delete=False, encoding="utf-8",
    )
    stderr_fh.close()
    stderr_fp = open(stderr_fh.name, "w")
    proc = await asyncio.create_subprocess_exec(
        "python3", "-c",
        "import sys, time; sys.stderr.write('MARKER_SENTINEL\\n'); "
        "sys.stderr.flush(); time.sleep(60)",
        stdout=asyncio.subprocess.PIPE,
        stderr=stderr_fp,
    )
    stderr_fp.close()
    inner_task = asyncio.create_task(proc.communicate())
    job_id = start_job(
        inner_task, proc,
        stderr_log_path=stderr_fh.name,
        max_lifetime_s=0.5,
    )
    await asyncio.sleep(1.0)
    entry = _jobs.get(job_id)
    assert entry is not None
    assert "MARKER_SENTINEL" in entry["result"]["stderr"]


@pytest.mark.asyncio
async def test_watchdog_stderr_tail_from_log_file():
    """Stderr redirected to log file, marker survives."""
    stderr_fh = tempfile.NamedTemporaryFile(
        mode="w", suffix=".log", delete=False, encoding="utf-8",
    )
    stderr_fh.close()
    proc = await asyncio.create_subprocess_exec(
        "python3", "-c",
        "import sys; sys.stderr.write('LOGFILE_MARKER\\n'); "
        "sys.stderr.flush(); import time; time.sleep(60)",
        stdout=asyncio.subprocess.PIPE,
        stderr=open(stderr_fh.name, "w"),
    )
    inner_task = asyncio.create_task(proc.communicate())
    job_id = start_job(
        inner_task, proc,
        stderr_log_path=stderr_fh.name,
        max_lifetime_s=0.5,
    )
    await asyncio.sleep(1.0)
    entry = _jobs.get(job_id)
    assert entry is not None
    assert "LOGFILE_MARKER" in entry["result"]["stderr"]


@pytest.mark.asyncio
async def test_watchdog_normal_completion_unaffected():
    """Normal (non-timeout) path still works with max_lifetime_s set."""
    async def _comm():
        return (b"ok", b"")

    task = asyncio.ensure_future(_comm())
    proc = MagicMock()
    proc.returncode = 0
    job_id = start_job(task, proc, max_lifetime_s=10.0)
    await asyncio.sleep(0.05)
    entry = _jobs.get(job_id)
    assert entry is not None
    assert entry["status"] == "completed"
    assert entry["result"]["verdict"] == "PASS"


@pytest.mark.asyncio
async def test_watchdog_measures_real_elapsed():
    """duration_s is measured elapsed, not the cap constant."""
    proc = await asyncio.create_subprocess_exec(
        "sleep", "60",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    inner_task = asyncio.create_task(proc.communicate())
    job_id = start_job(inner_task, proc, max_lifetime_s=0.5)
    await asyncio.sleep(1.0)
    entry = _jobs.get(job_id)
    assert entry is not None
    # Elapsed should be roughly 0.5s, not 0.5 exactly
    dur = entry["result"]["duration_s"]
    assert 0.3 < dur < 10.0


@pytest.mark.asyncio
async def test_watchdog_cancels_comm_task_on_timeout():
    """comm_task is cancelled after watchdog timeout, not left pending."""
    proc = await asyncio.create_subprocess_exec(
        "sleep", "60",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    inner_task = asyncio.create_task(proc.communicate())
    start_job(inner_task, proc, max_lifetime_s=0.5)
    await asyncio.sleep(1.0)
    # The inner task wrapping proc.communicate() must be cancelled
    assert inner_task.cancelled() or inner_task.done()


@pytest.mark.asyncio
async def test_watchdog_race_timeout_then_exit_is_not_false_timeout():
    """When subprocess exits between its last check and the timeout, the
    result should be a normal completion, not TIMEOUT."""
    proc = MagicMock()
    proc.returncode = None
    proc.pid = 12345

    # Create a comm_task that takes longer than the cap so wait_for
    # raises TimeoutError.  Set returncode BEFORE the timeout fires,
    # simulating the race where the process exited between its last
    # poll and the timeout.
    async def _slow_comm():
        await asyncio.sleep(0.05)
        proc.returncode = 0  # process exits
        await asyncio.sleep(5.0)  # pipe stays open (D-state-like)
        return (b"output", b"")

    with patch("code_forge.mcp_jobs.os.getpgid", return_value=12345):
        task = asyncio.create_task(_slow_comm())
        job_id = start_job(task, proc, max_lifetime_s=0.1)
        # Timeout fires at 0.1s; proc.returncode was set at 0.05s
        await asyncio.sleep(0.5)
        entry = _jobs.get(job_id)
        assert entry is not None
        # Should be completed (not TIMEOUT) because proc.returncode was set
        assert entry["status"] == "completed"
        assert entry["result"]["verdict"] == "PASS"
        assert entry["result"]["exit_code"] == 0
        # stdout is empty because comm_task was cancelled by wait_for
        assert entry["result"]["stdout"] == ""
        # stderr must include the stdout-lost marker
        assert "stdout lost: process exited at timeout boundary" in (
            entry["result"]["stderr"]
        )
        assert "duration_s" in entry["result"]


@pytest.mark.asyncio
async def test_watchdog_race_timeout_nonzero_exit():
    """Race path with non-zero exit code maps to correct verdict."""
    proc = MagicMock()
    proc.returncode = None
    proc.pid = 12345

    async def _slow_comm():
        await asyncio.sleep(0.05)
        proc.returncode = 1  # process exits with failure
        await asyncio.sleep(5.0)
        return (b"", b"")

    with patch("code_forge.mcp_jobs.os.getpgid", return_value=12345):
        task = asyncio.create_task(_slow_comm())
        job_id = start_job(task, proc, max_lifetime_s=0.1)
        await asyncio.sleep(0.5)
        entry = _jobs.get(job_id)
        assert entry is not None
        assert entry["status"] == "completed"
        assert entry["result"]["verdict"] == "FAIL"
        assert entry["result"]["exit_code"] == 1
        assert entry["result"]["stdout"] == ""
        # stderr must include the stdout-lost marker
        assert "stdout lost: process exited at timeout boundary" in (
            entry["result"]["stderr"]
        )
        assert "duration_s" in entry["result"]


# -- _read_stderr_tail --


def test_read_stderr_tail_no_log_path():
    from code_forge.mcp_jobs import _read_stderr_tail
    assert _read_stderr_tail({}) == ""


def test_read_stderr_tail_missing_file():
    """_read_stderr_tail returns '' when the log file does not exist."""
    from code_forge.mcp_jobs import _read_stderr_tail
    result = _read_stderr_tail(
        {"stderr_log_path": "/tmp/nonexistent_mcp_test_999.log"}
    )
    assert result == ""


def test_read_stderr_tail_reads_file():
    from code_forge.mcp_jobs import _read_stderr_tail
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".log", delete=False, encoding="utf-8",
    )
    f.write("hello world tail")
    f.close()
    result = _read_stderr_tail({"stderr_log_path": f.name})
    assert "hello world tail" in result
    os.unlink(f.name)


def test_read_stderr_tail_truncates():
    """Must read the TAIL, not the HEAD: head yields 'AAAA...';
    tail yields 'BBBB...'."""
    from code_forge.mcp_jobs import _read_stderr_tail
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".log", delete=False, encoding="utf-8",
    )
    f.write("A" * 4900)
    f.write("B" * 100)
    f.close()
    result = _read_stderr_tail({"stderr_log_path": f.name}, max_bytes=100)
    assert result == "B" * 100, (
        f"Expected tail (BBBB...), got HEAD or partial: {result[:20]}..."
    )
    os.unlink(f.name)


def test_read_stderr_tail_multibyte_boundary():
    """Multi-byte UTF-8 sequence straddling the max_bytes boundary must
    decode without raising; decode(errors='replace') handles the seam."""
    from code_forge.mcp_jobs import _read_stderr_tail
    max_bytes = 100
    # '中' is 3 bytes (E4 B8 AD).  4900 ASCII + 40 Chinese = 5020 bytes.
    # Tail of 100 bytes starts at byte 4920, which is 20 bytes into the
    # Chinese section.  20 / 3 = 6.67 -- the cut lands mid-character.
    filler = "A" * 4900
    chinese = "中" * 40  # U+4E2D = '中', 3 bytes each
    content = filler + chinese
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".log", delete=False, encoding="utf-8",
    )
    f.write(content)
    f.close()
    result = _read_stderr_tail({"stderr_log_path": f.name}, max_bytes=max_bytes)
    # (a) Must not raise -- decode(errors="replace") handles partial sequences
    assert isinstance(result, str)
    # (b) After the replacement char(s), the rest is clean Chinese.
    #     The tail starts at byte 4920, which is 20 bytes into the Chinese
    #     section.  20 / 3 = 6.67 -- byte 4920 is the last byte of the 7th
    #     char (AD), an orphan byte that decode(errors="replace") turns into
    #     U+FFFD.  Then 40 - 7 = 33 clean '中' chars follow.
    stripped = result.replace("�", "")
    assert stripped == "中" * 33, (
        f"Expected 33 clean Chinese chars, got {len(stripped)}: {stripped[:10]}..."
    )
    # (c) The raw tail read is exactly max_bytes.  Re-encoding may produce
    #     more bytes because U+FFFD (3 bytes) replaces a 1-byte orphan.
    #     Verify the decoded content is sensible, not that re-encoding fits.
    assert len(result) >= 1, "result must not be empty"
    assert result[0] == "�", (
        f"First char should be replacement, got {result[0]!r}"
    )
    os.unlink(f.name)


# SIGKILL reap timeout edge test


@pytest.mark.asyncio
async def test_watchdog_sigkill_reap_timeout_exit_code():
    """D-state child after SIGKILL -> exit_code=-1, verdict=TIMEOUT."""

    async def _comm():
        await asyncio.sleep(60)
        return (b"", b"")

    task = asyncio.ensure_future(_comm())
    proc = MagicMock()
    proc.returncode = None  # stays None even after SIGKILL

    async def _no_reap(p, grace=5.0):
        pass  # simulate D-state: SIGKILL sent but proc.returncode stays None

    job_id = start_job(task, proc, max_lifetime_s=0.1)
    with patch("code_forge.mcp_jobs._terminate_and_reap", side_effect=_no_reap):
        await asyncio.sleep(0.5)
    entry = _jobs.get(job_id)
    assert entry is not None
    assert entry["status"] == "failed"
    assert entry["result"]["exit_code"] == -1
    assert entry["result"]["verdict"] == "TIMEOUT"


# -- real-path group-kill --


@pytest.mark.asyncio
async def test_killpg_kills_entire_process_group():
    """When start_new_session=True, killpg must kill the entire group.

    This is a real-path test: spawns actual subprocesses that ignore
    SIGTERM and fork a child.  After timeout + cleanup, ALL processes
    must be gone -- not just the leader.

    With start_new_session=True, the child becomes a process group
    leader (pgid == pid), so _terminate_and_reap uses killpg to
    signal the entire group.  Without it, the child inherits the
    parent's pgid, pgid != pid, and only the leader gets SIGTERM --
    the background sleep(60) survives as an orphan.
    """
    # Spawn a process that ignores TERM and forks a background sleeper.
    # Both live in the same process group (start_new_session=True).
    proc = await asyncio.create_subprocess_exec(
        "sh", "-c",
        'trap "" TERM; sleep 60 & sleep 60',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    pgid = os.getpgid(proc.pid)
    # Sanity: new session means pgid == pid (process group leader)
    assert pgid == proc.pid, (
        "start_new_session must make proc the group leader"
    )

    inner_task = asyncio.create_task(proc.communicate())
    start_job(inner_task, proc, max_lifetime_s=0.5)
    # Wait for watchdog timeout (0.5s) + SIGTERM grace (5s) + SIGKILL + reap
    await asyncio.sleep(8.0)

    # Verify the leader is dead
    assert proc.returncode is not None, (
        "leader process must have been reaped"
    )

    # Verify no process in the group survives
    try:
        os.kill(proc.pid, 0)  # should raise ProcessLookupError
        assert False, "leader PID still alive after killpg cleanup"
    except ProcessLookupError:
        pass  # expected: leader is dead

    # Check that the background sleeper is also dead
    # (killpg kills the whole group, not just the leader)
    import subprocess
    result = subprocess.run(
        ["ps", "-o", "pid=", "--sid", str(pgid)],
        capture_output=True, text=True, timeout=5,
    )
    # If any processes remain in the session, the output is non-empty
    remaining = result.stdout.strip()
    assert not remaining, (
        f"process group {pgid} still has alive members: {remaining}"
    )


@pytest.mark.asyncio
async def test_run_cli_budgeted_sets_start_new_session():
    """Production spawn must set start_new_session=True so the child
    becomes a process group leader (pgid == pid).  Without this flag,
    _terminate_and_reap falls back to proc.terminate() and orphan
    children survive the kill."""
    from pathlib import Path

    from code_forge.mcp_server import _run_cli_budgeted

    captured_kwargs = {}
    real_proc = await asyncio.create_subprocess_exec(
        "true",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await real_proc.wait()

    class _Proc:
        """Proxy that delegates pid/returncode/communicate to a
        real subprocess, forwarding start_new_session capture."""
        @property
        def pid(self):
            return real_proc.pid
        @property
        def returncode(self):
            return real_proc.returncode
        async def communicate(self):
            return (b"", b"")

    async def _capture_exec(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return _Proc()

    with patch(
        "code_forge.mcp_server.asyncio.create_subprocess_exec",
        side_effect=_capture_exec,
    ):
        await _run_cli_budgeted(
            "review", workspace=Path("/tmp"), budget=10.0
        )

    assert captured_kwargs.get("start_new_session") is True, (
        "_run_cli_budgeted must pass start_new_session=True"
    )

    # Spawn a real process with the captured start_new_session to
    # verify the child becomes a group leader (pgid == pid).
    child = await asyncio.create_subprocess_exec(
        "sh", "-c", "sleep 60",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=captured_kwargs["start_new_session"],
    )
    try:
        assert os.getpgid(child.pid) == child.pid, (
            "production start_new_session must make child a group leader"
        )
    finally:
        child.kill()
        await child.wait()
