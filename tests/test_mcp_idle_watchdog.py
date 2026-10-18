"""#165: a background MCP job is bounded by an inactivity watchdog.

A LOCAL review runs the mutation baseline every round, for as many rounds as
it needs, so no derived total fits. The job is killed only when its stderr
log (forge's progress lines) stops growing for idle_timeout_s; a total cap
applies only when one is explicitly set.
"""

import asyncio
import sys
from pathlib import Path

import pytest

from code_forge import mcp_jobs

# Prints a progress line every `step` seconds, `count` times, then exits 0.
_CHILD = (
    "import sys, time\n"
    "step, count = float(sys.argv[1]), int(sys.argv[2])\n"
    "for i in range(count):\n"
    "    time.sleep(step); print('[forge] t+%d progress' % i, file=sys.stderr, flush=True)\n"
    "time.sleep(float(sys.argv[3]))\n"
)


async def _job(tmp_path, monkeypatch, *, step, count, tail, **start):
    monkeypatch.setattr(mcp_jobs, "_WATCHDOG_POLL_S", 0.05)
    log = tmp_path / "stderr.log"
    handle = open(log, "wb")  # noqa: SIM115 - the child owns it
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", _CHILD, str(step), str(count), str(tail),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=handle,
    )
    handle.close()
    task = asyncio.create_task(proc.communicate())
    job_id = mcp_jobs.start_job(task, proc, stderr_log_path=str(log), **start)
    job = mcp_jobs.get_job(job_id)
    await asyncio.wait_for(asyncio.shield(job["wait_task"]), timeout=20)
    return job, proc


@pytest.mark.asyncio
async def test_progressing_job_outlives_the_idle_window(tmp_path, monkeypatch):
    # 12 progress lines 0.15s apart (~1.8s total) under a 0.6s idle window.
    job, proc = await _job(tmp_path, monkeypatch, step=0.15, count=12, tail=0, idle_timeout_s=0.6)
    assert job["status"] == "completed", job["result"]
    assert job["result"]["exit_code"] == 0
    assert job["result"]["duration_s"] > 0.6 * 2


@pytest.mark.asyncio
async def test_silent_job_is_killed_after_the_idle_window(tmp_path, monkeypatch):
    job, proc = await _job(tmp_path, monkeypatch, step=0.05, count=2, tail=30, idle_timeout_s=0.6)
    assert job["status"] == "failed"
    assert job["result"]["verdict"] == "TIMEOUT"
    assert job["result"]["stderr"].startswith("job made no progress for 0s")
    assert "[forge] t+1 progress" in job["result"]["stderr"]
    assert job["result"]["duration_s"] < 5
    assert proc.returncode is not None


@pytest.mark.asyncio
async def test_explicit_total_cap_still_applies_while_progressing(tmp_path, monkeypatch):
    job, proc = await _job(
        tmp_path, monkeypatch, step=0.1, count=100, tail=0, idle_timeout_s=5, max_lifetime_s=1.0,
    )
    assert job["status"] == "failed"
    assert job["result"]["stderr"].startswith("job exceeded 1s cap")
    assert job["result"]["duration_s"] < 4
    assert proc.returncode is not None


_SRC = str(Path(__file__).resolve().parents[1] / "src")

# A passing 3-run baseline guard whose total silent time (3 x 0.4s) is longer
# than the idle window (0.6s), while each single run fits inside it.
_GUARD_CHILD = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from code_forge.baseline_guard import _run_baseline_guard\n"
    "cmd = [sys.executable, '-c', 'import time; time.sleep(0.4)']\n"
    "status, _f, _i = _run_baseline_guard(cmd, {}, '.', allow_strip_retry=False)\n"
    "sys.exit(0 if status == 'passed' else 3)\n"
)


@pytest.mark.asyncio
async def test_passing_three_run_baseline_guard_is_not_killed(tmp_path, monkeypatch):
    """#165 re-review: the guard emits one progress line per run, so the
    watchdog sized to one run never kills a healthy 3-run baseline."""
    monkeypatch.setattr(mcp_jobs, "_WATCHDOG_POLL_S", 0.05)
    src = _SRC
    log = tmp_path / "stderr.log"
    handle = open(log, "wb")  # noqa: SIM115 - the child owns it
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", _GUARD_CHILD, src,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=handle,
    )
    handle.close()
    task = asyncio.create_task(proc.communicate())
    job_id = mcp_jobs.start_job(task, proc, stderr_log_path=str(log), idle_timeout_s=0.6)
    job = mcp_jobs.get_job(job_id)
    await asyncio.wait_for(asyncio.shield(job["wait_task"]), timeout=20)
    assert job["status"] == "completed", job["result"]
    assert job["result"]["exit_code"] == 0
    assert job["result"]["duration_s"] > 0.6
