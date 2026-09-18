# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for STATE-11 signal handling (integration, subprocess-based).

Marked @pytest.mark.integration.
"""

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

SRC_DIR = str(Path(__file__).resolve().parent.parent / "src")


def _lock_holder_script(lock_path: str) -> str:
    """Python script that acquires lock and blocks until signaled."""
    return (
        "import sys, os, time, signal\n"
        "sys.path.insert(0, %r)\n"
        "from pathlib import Path\n"
        "from code_forge.lock import ForgeLock\n"
        "lock_path = Path(%r)\n"
        "with ForgeLock(lock_path):\n"
        "    sys.stdout.write('READY\\n')\n"
        "    sys.stdout.flush()\n"
        "    time.sleep(30)\n"  # Long enough for signal delivery
    ) % (SRC_DIR, lock_path)


class _TimedPopen:
    """Popen context that kills the child if wait() has no timeout."""

    def __init__(self, proc, timeout=5):
        self.proc = proc
        self.timeout = timeout

    def __enter__(self):
        return self.proc

    def __exit__(self, exc_type, exc, tb):
        if self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                pass
        if self.proc.stdout is not None:
            self.proc.stdout.close()
        if self.proc.stderr is not None:
            self.proc.stderr.close()
        return False


def _run_lock_holder(lock_path: Path):
    return _TimedPopen(subprocess.Popen(
        [sys.executable, "-c", _lock_holder_script(str(lock_path))],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ))


@pytest.mark.integration
class TestSigintCleansLock:
    """(a) SIGINT during held lock -> lock removed."""

    def test_sigint_removes_lock(self, tmp_path):
        lock_path = tmp_path / "code-forge.lock"
        with _run_lock_holder(lock_path) as proc:
            assert proc.stdout is not None
            line = proc.stdout.readline().decode().strip()
            assert line == "READY"
            assert lock_path.exists()
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=5)
        assert not lock_path.exists()


@pytest.mark.integration
class TestSigtermCleansLock:
    """(b) SIGTERM during held lock -> lock removed."""

    def test_sigterm_removes_lock(self, tmp_path):
        lock_path = tmp_path / "code-forge.lock"
        with _run_lock_holder(lock_path) as proc:
            assert proc.stdout is not None
            line = proc.stdout.readline().decode().strip()
            assert line == "READY"
            assert lock_path.exists()
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)
        assert not lock_path.exists()


@pytest.mark.integration
class TestSigkillLeavesStale:
    """(c) SIGKILL during held lock -> lock NOT removed."""

    def test_sigkill_leaves_stale(self, tmp_path):
        lock_path = tmp_path / "code-forge.lock"
        with _run_lock_holder(lock_path) as proc:
            assert proc.stdout is not None
            line = proc.stdout.readline().decode().strip()
            assert line == "READY"
            assert lock_path.exists()
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=5)
            # Lock file still exists (stale)
            assert lock_path.exists()
            # Verify next acquire recovers the stale lock
            from code_forge.lock import acquire_lock
            acquire_lock(lock_path)
            content = lock_path.read_text().strip()
            assert content == str(os.getpid())


@pytest.mark.integration
def test_context_kills_child_when_assertion_fails(tmp_path):
    """Leaving the helper on a failed assertion must not leak the child."""
    lock_path = tmp_path / "code-forge.lock"
    leaked = None
    try:
        with _run_lock_holder(lock_path) as proc:
            assert proc.stdout is not None
            line = proc.stdout.readline().decode().strip()
            assert line == "READY"
            leaked = proc
            raise AssertionError("force cleanup")
    except AssertionError:
        pass
    assert leaked is not None
    assert leaked.poll() is not None
