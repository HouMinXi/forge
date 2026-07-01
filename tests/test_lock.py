# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for STATE-11 file lock (unit-level, a-j)."""

import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from code_forge.lock import ForgeLock, ForgeLockBusy, acquire_lock


class TestAcquireFresh:
    """(a) acquire on missing lock -> creates with own PID."""

    def test_creates_lock_with_pid(self, tmp_path):
        lock_path = tmp_path / "code-forge.lock"
        acquire_lock(lock_path)
        content = lock_path.read_text().strip()
        assert content == str(os.getpid())

    """(f) PID format: file contains exactly "<int>\\n"."""

    def test_pid_format(self, tmp_path):
        lock_path = tmp_path / "code-forge.lock"
        acquire_lock(lock_path)
        raw = lock_path.read_text()
        assert raw == "%d\n" % os.getpid()


class TestAcquireLivePid:
    """(b) acquire on existing lock with live PID -> ForgeLockBusy."""

    def test_live_pid_raises_busy(self, tmp_path):
        lock_path = tmp_path / "code-forge.lock"
        lock_path.write_text("%d\n" % os.getpid())
        with pytest.raises(ForgeLockBusy) as exc_info:
            acquire_lock(lock_path)
        assert exc_info.value.pid == os.getpid()


class TestAcquireDeadPid:
    """(c) acquire on existing lock with dead PID -> stale removed."""

    def test_dead_pid_recovers(self, tmp_path):
        lock_path = tmp_path / "code-forge.lock"
        # Fork a child that exits immediately to get a dead PID
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        os.waitpid(pid, 0)
        lock_path.write_text("%d\n" % pid)
        acquire_lock(lock_path)
        content = lock_path.read_text().strip()
        assert content == str(os.getpid())


class TestContextManager:
    """(d) context manager releases on normal exit."""

    def test_release_on_normal_exit(self, tmp_path):
        lock_path = tmp_path / "code-forge.lock"
        with ForgeLock(lock_path):
            assert lock_path.exists()
        assert not lock_path.exists()

    """(e) context manager releases on exception in body."""

    def test_release_on_exception(self, tmp_path):
        lock_path = tmp_path / "code-forge.lock"
        with pytest.raises(RuntimeError):
            with ForgeLock(lock_path):
                assert lock_path.exists()
                raise RuntimeError("boom")
        assert not lock_path.exists()


class TestRace:
    """(g) race: two simultaneous O_EXCL -- only one wins."""

    def test_race_only_one_wins(self, tmp_path):
        lock_path = tmp_path / "code-forge.lock"
        src_dir = str(Path(__file__).resolve().parent.parent / "src")
        script = "\n".join([
            "import sys, time",
            "from pathlib import Path",
            "sys.path.insert(0, %r)" % src_dir,
            "from code_forge.lock import acquire_lock, ForgeLockBusy",
            "p = Path(%r)" % str(lock_path),
            "try:",
            "    acquire_lock(p)",
            "    print('WON')",
            "    time.sleep(2)",
            "except ForgeLockBusy:",
            "    print('LOST')",
        ])
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for _ in range(2)
        ]
        results = []
        for p in procs:
            out, _ = p.communicate(timeout=10)
            results.append(out.decode().strip())
        assert results.count("WON") == 1
        assert results.count("LOST") == 1


class TestEperm:
    """(h) EPERM treated as alive -> ForgeLockBusy."""

    def test_eperm_raises_busy(self, tmp_path):
        lock_path = tmp_path / "code-forge.lock"
        lock_path.write_text("99999\n")

        def mock_kill(pid, sig):
            raise PermissionError("EPERM")

        with patch("code_forge.lock.os.kill", side_effect=mock_kill):
            with pytest.raises(ForgeLockBusy) as exc_info:
                acquire_lock(lock_path)
            assert exc_info.value.pid == 99999


class TestSignalChainSigIgn:
    """(i) prev handler = SIG_IGN -> release happens, no exception."""

    def test_sig_ign_preserved(self, tmp_path):
        lock_path = tmp_path / "code-forge.lock"
        old = signal.getsignal(signal.SIGINT)
        try:
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            with ForgeLock(lock_path) as lock:
                assert lock_path.exists()
                # Simulate signal delivery via the installed handler
                handler = signal.getsignal(signal.SIGINT)
                handler(signal.SIGINT, None)
                # Lock should be released, no exception
                assert not lock_path.exists()
        finally:
            signal.signal(signal.SIGINT, old)


class TestSignalChainCallable:
    """(j) prev handler = callable -> release then prev called."""

    def test_callable_chain_order(self, tmp_path):
        lock_path = tmp_path / "code-forge.lock"
        call_log = []

        def prev_handler(signum, frame):
            call_log.append(("prev", lock_path.exists()))

        old = signal.getsignal(signal.SIGINT)
        try:
            signal.signal(signal.SIGINT, prev_handler)
            with ForgeLock(lock_path) as lock:
                assert lock_path.exists()
                handler = signal.getsignal(signal.SIGINT)
                handler(signal.SIGINT, None)
            # prev_handler was called; lock was already released when
            # prev was invoked
            assert len(call_log) == 1
            assert call_log[0] == ("prev", False)
        finally:
            signal.signal(signal.SIGINT, old)


@pytest.mark.asyncio
async def test_forgelock_worker_thread():
    """TEST-1: ForgeLock in asyncio.to_thread does not crash."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        lock_path = Path(d) / "test.lock"

        def run():
            with ForgeLock(lock_path):
                return "ok"

        result = await asyncio.to_thread(run)
        assert result == "ok"
        assert not lock_path.exists()
