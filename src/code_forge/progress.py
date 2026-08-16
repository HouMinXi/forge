# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Progress event stream for review runs.

One line per phase transition on stderr, each prefixed with the wall
clock since the run started.  The clock is what makes a stuck stage
visible: the last line before a silence says which stage was running
and how old the run was when it started, so a coder watching the log
can tell a hung call from a slow one at a glance.

stderr is the channel by design.  The MCP server tails stderr for
forge_job_status (mcp_server.py), and the CLI already writes its pass
summaries there (factories.py), so both entry points share one stream
and every consumer -- a human at a terminal, a log file, a job-status
poll -- sees the same events.

Every emit flushes.  Python block-buffers stderr when it is not a tty,
which is exactly the log-file case where a stuck stage hides its own
trace; without the flush the events pile up in the buffer and the
stream stays silent.
"""
from __future__ import annotations

import sys
import threading
import time

# One clock per process, reset at the start of each run. A review is
# driven from one thread and reset() runs before any worker thread
# starts, so the global is race-free under the current architecture
# (one CLI process per run; one subprocess per MCP job; the MCP
# server's inline outlet is single-threaded). A future design that
# runs reviews concurrently in one process must move the clock into
# the run context instead of sharing this global.
_t0 = time.monotonic()
_emit_lock = threading.Lock()


def reset() -> None:
    """Restart the wall clock.

    The default zero point is module import, which is the run start for
    a one-shot CLI process. A long-lived process (the MCP server's
    inline outlet) imports once and runs many reviews, so each run
    calls reset() before its first event or its clocks read as hours.
    """
    global _t0
    with _emit_lock:
        _t0 = time.monotonic()


def _elapsed() -> float:
    return time.monotonic() - _t0


def emit(msg: str) -> None:
    """Write one progress event line to stderr and flush it.

    Parallel review passes emit from worker threads; the lock keeps
    whole lines from interleaving so the stream stays readable.

    A broken stderr (closed stream, write failure) must not kill the
    review: progress output is auxiliary, never load-bearing.
    """
    try:
        with _emit_lock:
            if sys.stderr is None:
                return
            sys.stderr.write("[forge] t+%.1fs %s\n" % (_elapsed(), msg))
            sys.stderr.flush()
    except Exception:  # noqa: BLE001 -- progress output must never kill the review
        pass
