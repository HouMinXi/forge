# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Job lifecycle for MCP budgeted-start pattern.

When a forge review exceeds the inline budget (20s), the subprocess
continues in background and the handler returns a job_id for polling.
This module manages that state.

Migration seam: when the MCP Python SDK ships SEP-2663 Tasks support
(tracked issue #2806, ~2026-07-28), replace _jobs with
enable_tasks() InMemoryTaskStore.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel


# -- verdict mapping (all 8 exit codes) --

_EXIT_TO_VERDICT: dict[int, str] = {
    0: "PASS",
    1: "FAIL",
    2: "CLI_ERROR",
    3: "BUSY",
    4: "ESCALATED",
    5: "DELEGATED",
    6: "TIMEOUT",
    7: "UNRELIABLE",
}


def exit_to_verdict(code: int) -> str:
    """Map CLI exit code to human-readable verdict string."""
    return _EXIT_TO_VERDICT.get(code, "UNKNOWN(%d)" % code)


# -- Pydantic models --


class ForgeResult(BaseModel):
    """Terminal result of a forge CLI invocation."""

    verdict: str
    exit_code: int
    findings_count: int | None = None  # None = not counted, never 0 as surrogate
    findings: list[dict] | None = None  # compact finding summaries for MCP
    duration_s: float
    output: str


class ForgeJobRef(BaseModel):
    """Job reference returned to MCP clients for polling."""

    job_id: str
    status: str  # running / completed / failed
    poll_after_seconds: int | None = None  # 10 when running, None when terminal
    result: ForgeResult | None = None


# -- module-level state --

_jobs: dict[str, dict[str, Any]] = {}
_JOB_TTL_SECONDS: float = 3600.0


# -- public API --


def start_job(
    comm_task: asyncio.Task[Any],
    proc: asyncio.subprocess.Process,
    tempfile_path: str | None = None,
    stderr_log_path: str | None = None,
) -> str:
    """Register a background job. Returns job_id (UUID4).

    comm_task is the inner asyncio.Task wrapping proc.communicate(),
    NOT the cancelled shield wrapper.
    """
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "comm_task": comm_task,
        "proc": proc,
        "status": "running",
        "result": None,
        "created_at": time.monotonic(),
        "tempfile_path": tempfile_path,
        "stderr_log_path": stderr_log_path,
    }
    asyncio.create_task(_wait_for_job(job_id))
    return job_id


def get_job(job_id: str) -> dict[str, Any] | None:
    """Retrieve job state (read-only). TTL eviction handles cleanup.

    Returns None for unknown job_id.
    """
    _evict_stale()
    return _jobs.get(job_id)


def snapshot_tempfile_paths() -> list[str]:
    """Return all tempfile paths from job entries for pre-shutdown cleanup."""
    paths: list[str] = []
    for entry in _jobs.values():
        for key in ("tempfile_path", "stderr_log_path"):
            p = entry.get(key)
            if p:
                paths.append(p)
    return paths


async def cleanup_all() -> None:
    """Terminate all running subprocesses. Called from lifespan teardown."""
    for entry in list(_jobs.values()):
        proc = entry.get("proc")
        if proc is None or proc.returncode is not None:
            continue
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
    _jobs.clear()


# -- internal helpers --


async def _wait_for_job(job_id: str) -> None:
    """Await the comm_task and update job state on completion."""
    entry = _jobs.get(job_id)
    if entry is None:
        return
    elapsed = time.monotonic() - entry["created_at"]
    try:
        stdout_bytes, stderr_bytes = await entry["comm_task"]
        elapsed = time.monotonic() - entry["created_at"]
        proc = entry["proc"]
        exit_code = proc.returncode if proc.returncode is not None else -1
        # stderr is None when redirected to a file (Python 3.14 verified)
        if stderr_bytes is None:
            log_path = entry.get("stderr_log_path")
            if log_path:
                try:
                    stderr_text = Path(log_path).read_text(
                        encoding="utf-8", errors="replace"
                    )
                except OSError:
                    stderr_text = ""
            else:
                stderr_text = ""
        else:
            stderr_text = stderr_bytes.decode(errors="replace")
        entry["status"] = "completed"
        entry["result"] = {
            "stdout": stdout_bytes.decode(errors="replace"),
            "stderr": stderr_text,
            "exit_code": exit_code,
            "verdict": exit_to_verdict(exit_code),
            "duration_s": elapsed,
        }
    except BaseException as exc:
        entry["status"] = "failed"
        entry["result"] = {
            "stdout": "",
            "stderr": str(exc),
            "exit_code": -1,
            "verdict": "UNKNOWN(-1)",
            "duration_s": elapsed,
        }
    finally:
        for key in ("tempfile_path", "stderr_log_path"):
            p = entry.get(key)
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass
        _evict_stale()


def _evict_stale() -> None:
    """Remove terminal entries past TTL; unlink their tempfiles.

    Running entries are left untouched -- their sole forced-death owner
    is cleanup_all() at server shutdown.  A stuck child stays visible
    via forge_job_status progress tail until the operator acts.
    """
    now = time.monotonic()
    to_remove: list[str] = []
    for jid, entry in _jobs.items():
        age = now - entry["created_at"]
        if age <= _JOB_TTL_SECONDS:
            continue
        if entry["status"] in ("completed", "failed"):
            to_remove.append(jid)
    for jid in to_remove:
        entry = _jobs.pop(jid, None)
        if entry is None:
            continue
        for key in ("tempfile_path", "stderr_log_path"):
            p = entry.get(key)
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass
