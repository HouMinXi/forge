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


@pytest.mark.integration
class TestSigintCleansLock:
    """(a) SIGINT during held lock -> lock removed."""

    def test_sigint_removes_lock(self, tmp_path):
        lock_path = tmp_path / "code-forge.lock"
        proc = subprocess.Popen(
            [sys.executable, "-c", _lock_holder_script(str(lock_path))],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # Wait for READY
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
        proc = subprocess.Popen(
            [sys.executable, "-c", _lock_holder_script(str(lock_path))],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
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
        proc = subprocess.Popen(
            [sys.executable, "-c", _lock_holder_script(str(lock_path))],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
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
