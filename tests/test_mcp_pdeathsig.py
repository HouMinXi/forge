# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Real-path acceptance tests for PDEATHSIG orphan guard.

These tests spawn actual processes and verify kernel signal delivery.
Linux-only (PR_SET_PDEATHSIG). Marked as integration tests.
"""
from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="PDEATHSIG is Linux-only"),
]


# -- helper: intermediary parent script --

_PARENT_SCRIPT = textwrap.dedent("""\
    import os, subprocess, sys, time

    stdin_fd = int(sys.argv[1])
    report_fd = int(sys.argv[2])
    server_cmd = sys.argv[3:]

    proc = subprocess.Popen(
        server_cmd,
        stdin=stdin_fd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.close(stdin_fd)

    os.write(report_fd, (str(proc.pid) + "\\n").encode())
    os.close(report_fd)

    time.sleep(1)
    os._exit(0)
""")


# -- helper: minimal server script that sets PDEATHSIG and sleeps --

_SERVER_SCRIPT = textwrap.dedent("""\
    import ctypes, os, signal, sys, time

    PR_SET_PDEATHSIG = 1
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    rc = libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)
    if rc != 0:
        sys.exit(99)

    if os.getppid() == 1:
        sys.exit(98)

    # Block until signal arrives
    while True:
        time.sleep(60)
""")


# -- helper: server script WITHOUT pdeathsig (bug-inject) --

_SERVER_SCRIPT_NO_PDEATHSIG = textwrap.dedent("""\
    import os, signal, sys, time

    # PDEATHSIG deliberately NOT set (bug-inject RED case)

    while True:
        time.sleep(60)
""")


def _spawn_via_parent(server_script: str, timeout: float = 10.0):
    """Start server as child of a throwaway parent.

    Returns (server_pid, stdin_write_fd).
    The caller holds stdin_write_fd to keep server stdin open
    after parent dies (prevents EOF-based shutdown, isolates
    PDEATHSIG as the only exit trigger).
    """
    stdin_r, stdin_w = os.pipe()
    report_r, report_w = os.pipe()

    parent = subprocess.Popen(
        [sys.executable, "-c", _PARENT_SCRIPT,
         str(stdin_r), str(report_w),
         sys.executable, "-c", server_script],
        pass_fds=(stdin_r, report_w),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.close(stdin_r)
    os.close(report_w)

    with os.fdopen(report_r, "r") as f:
        line = f.readline().strip()
    server_pid = int(line)

    parent.wait(timeout=timeout)
    return server_pid, stdin_w


def _wait_for_exit(pid: int, max_seconds: float = 5.0) -> bool:
    """Poll until process exits. Returns True if exited."""
    deadline = time.monotonic() + max_seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            time.sleep(0.1)
        except ProcessLookupError:
            return True
    return False


class TestPdeathsigRealPath:
    """Real server + throwaway parent + kill parent + assert exit."""

    def test_server_exits_when_parent_dies(self):
        """GREEN: PDEATHSIG set -> server exits after parent death."""
        server_pid, stdin_w = _spawn_via_parent(_SERVER_SCRIPT)
        try:
            exited = _wait_for_exit(server_pid, max_seconds=5.0)
            assert exited, (
                "Server (pid %d) survived parent death -- "
                "PDEATHSIG did not fire or was not handled" % server_pid
            )
        finally:
            os.close(stdin_w)
            try:
                os.kill(server_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def test_server_survives_without_pdeathsig(self):
        """RED (bug-inject): no PDEATHSIG -> server survives parent death."""
        server_pid, stdin_w = _spawn_via_parent(_SERVER_SCRIPT_NO_PDEATHSIG)
        try:
            survived = not _wait_for_exit(server_pid, max_seconds=3.0)
            assert survived, (
                "Server exited even without PDEATHSIG -- "
                "test isolation broken (EOF or stray signal reached it)"
            )
        finally:
            os.close(stdin_w)
            try:
                os.kill(server_pid, signal.SIGKILL)
                os.waitpid(server_pid, 0)
            except (ProcessLookupError, ChildProcessError):
                pass


class TestPdeathsigChildInheritance:
    """Verify exec clears PDEATHSIG in child subprocesses."""

    def test_exec_clears_pdeathsig_in_child(self):
        """Children of a process with PDEATHSIG do not inherit it after exec.

        This confirms review-job subprocesses are not killed early by
        an inherited PDEATHSIG when the server is still alive.
        """
        child_script = textwrap.dedent("""\
            import ctypes, sys
            PR_GET_PDEATHSIG = 2
            sig = ctypes.c_int(0)
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            rc = libc.prctl(PR_GET_PDEATHSIG, ctypes.byref(sig), 0, 0, 0)
            print(sig.value)
        """)

        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        old_sig = ctypes.c_int(0)
        libc.prctl(2, ctypes.byref(old_sig), 0, 0, 0)

        libc.prctl(1, signal.SIGTERM, 0, 0, 0)
        try:
            result = subprocess.run(
                [sys.executable, "-c", child_script],
                capture_output=True, text=True, timeout=5,
            )
            child_pdeathsig = int(result.stdout.strip())
            assert child_pdeathsig == 0, (
                "Child inherited PDEATHSIG=%d after exec -- "
                "review-job subprocesses would be killed early" % child_pdeathsig
            )
        finally:
            libc.prctl(1, old_sig.value, 0, 0, 0)
