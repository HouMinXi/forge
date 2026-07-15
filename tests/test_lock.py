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

from code_forge.lock import (
    ForgeLock,
    ForgeLockBusy,
    _handle_existing_lock,
    _pid_alive,
    _write_all,
    acquire_lock,
)


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

    def test_lock_file_not_group_or_other_accessible(self, tmp_path):
        """The lock file grants no group/other access.

        No consumer in this codebase reads a forge lock file across a
        user boundary, so there is no reason to widen permissions --
        doing so would need a chmod call after the file is created,
        opening a TOCTOU window between close() and chmod() for no
        actual benefit. Checking the group/other bits (rather than
        asserting the exact mode) keeps this test correct under an
        unusual umask that clears additional owner bits.
        """
        lock_path = tmp_path / "code-forge.lock"
        acquire_lock(lock_path)
        mode = lock_path.stat().st_mode & 0o777
        assert mode & 0o077 == 0


class TestAcquireLivePid:
    """(b) acquire on existing lock with live PID -> ForgeLockBusy."""

    def test_live_pid_raises_busy(self, tmp_path):
        lock_path = tmp_path / "code-forge.lock"
        lock_path.write_text("%d\n" % os.getpid())
        with pytest.raises(ForgeLockBusy) as exc_info:
            acquire_lock(lock_path)
        assert exc_info.value.pid == os.getpid()


class TestAcquireAtomicLink:
    """Write-then-link never exposes a transiently-empty lock path."""

    def test_content_written_before_link(self, tmp_path, monkeypatch):
        """os.link() must only run once the temp file has real content.

        Core invariant of the atomic-link design: a concurrent reader
        can never observe *path* existing but empty, because linking
        only happens after the PID is already on disk.
        """
        lock_path = tmp_path / "code-forge.lock"
        real_link = os.link
        seen = {}

        def _spy_link(src, dst):
            seen["content"] = Path(src).read_text(encoding="utf-8")
            return real_link(src, dst)

        monkeypatch.setattr(os, "link", _spy_link)
        acquire_lock(lock_path)
        assert seen["content"] == "%d\n" % os.getpid()

    def test_write_all_loops_on_partial_write(self, tmp_path, monkeypatch):
        """_write_all must retry until every byte is written, not just
        whatever a single os.write() call happened to accept."""
        data = b"12345\n"
        chunks_written = []
        real_write = os.write

        def _short_write(fd, buf):
            n = min(2, len(buf))
            chunks_written.append(bytes(buf[:n]))
            return real_write(fd, bytes(buf[:n]))

        monkeypatch.setattr(os, "write", _short_write)
        target = tmp_path / "write_all_target"
        fd = os.open(str(target), os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            _write_all(fd, data)
        finally:
            os.close(fd)
        assert target.read_bytes() == data
        # Confirms the loop actually ran more than once (not a fluke
        # single call that happened to accept everything).
        assert len(chunks_written) == 3

    def test_write_all_raises_on_zero_progress_write(
        self, tmp_path, monkeypatch
    ):
        """A zero-byte os.write() return with data still pending must
        raise, not spin forever -- nothing changes between iterations
        for a subsequent retry to have any chance of progressing.

        The fake stops returning 0 after a few calls so that a version
        of _write_all without the guard fails this test cleanly (via
        pytest.raises' "DID NOT RAISE") instead of hanging the run.
        """
        real_write = os.write
        calls = {"n": 0}

        def _zero_for_a_while(fd, buf):
            calls["n"] += 1
            if calls["n"] <= 3:
                return 0
            return real_write(fd, buf)

        monkeypatch.setattr(os, "write", _zero_for_a_while)
        target = tmp_path / "write_all_zero_target"
        fd = os.open(str(target), os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            with pytest.raises(OSError, match="returned 0"):
                _write_all(fd, b"12345\n")
        finally:
            os.close(fd)

    def test_no_leftover_tmp_file_on_success(self, tmp_path):
        """No .tmp-<pid> file remains after a successful acquire."""
        lock_path = tmp_path / "code-forge.lock"
        acquire_lock(lock_path)
        assert list(tmp_path.glob("*.tmp-*")) == []

    def test_no_leftover_tmp_file_on_busy(self, tmp_path):
        """The temp file is also cleaned up when acquisition fails busy."""
        lock_path = tmp_path / "code-forge.lock"
        lock_path.write_text("%d\n" % os.getpid())
        with pytest.raises(ForgeLockBusy):
            acquire_lock(lock_path)
        assert list(tmp_path.glob("*.tmp-*")) == []

    def test_tmp_name_unpredictable_across_calls(self, tmp_path, monkeypatch):
        """Two acquires (same PID, after releasing) use different temp
        names.

        A name derived only from path + our own PID would be identical
        every time and let another user with write access to the same
        directory pre-plant a symlink at that exact path. mkstemp's
        random suffix means the name can't be predicted in advance.
        """
        lock_path = tmp_path / "code-forge.lock"
        seen_names = []
        real_link = os.link

        def _spy_link(src, dst):
            seen_names.append(Path(src).name)
            return real_link(src, dst)

        monkeypatch.setattr(os, "link", _spy_link)
        acquire_lock(lock_path)
        lock_path.unlink()
        acquire_lock(lock_path)
        assert len(seen_names) == 2
        assert seen_names[0] != seen_names[1]

    def test_link_source_gone_raises_clear_oserror(
        self, tmp_path, monkeypatch
    ):
        """If tmp_path itself vanishes before os.link() can run (e.g.
        something else with write access to the directory removed it),
        the resulting FileNotFoundError from os.link() must surface as
        a clear OSError, not an unexplained "no such file"."""
        lock_path = tmp_path / "code-forge.lock"

        def _source_already_gone(src, dst):
            raise FileNotFoundError(
                "simulated: tmp file removed before linking"
            )

        monkeypatch.setattr(os, "link", _source_already_gone)
        with pytest.raises(OSError, match="disappeared before"):
            acquire_lock(lock_path)

    def test_path_removed_right_after_link_raises_clear_oserror(
        self, tmp_path, monkeypatch
    ):
        """If *path* is removed by something else in the instant
        between os.link() succeeding and our own inode-verification
        stat(), the resulting FileNotFoundError from os.stat() must
        surface as a clear OSError, not an unexplained "no such
        file"."""
        lock_path = tmp_path / "code-forge.lock"
        real_link = os.link

        def _link_then_vanish(src, dst):
            real_link(src, dst)
            os.unlink(dst)

        monkeypatch.setattr(os, "link", _link_then_vanish)
        with pytest.raises(OSError, match="disappeared immediately"):
            acquire_lock(lock_path)

    def test_write_failure_does_not_leak_tmp_file(
        self, tmp_path, monkeypatch
    ):
        """os.write() raising must not leave the temp file behind."""
        lock_path = tmp_path / "code-forge.lock"

        def _boom_write(fd, data):
            raise OSError("disk full (injected)")

        monkeypatch.setattr(os, "write", _boom_write)
        with pytest.raises(OSError):
            acquire_lock(lock_path)
        assert list(tmp_path.glob("*.tmp-*")) == []

    def test_inode_mismatch_after_link_rejected_not_returned_success(
        self, tmp_path, monkeypatch
    ):
        """If the path linked into place does not share the temp
        file's inode -- e.g. because something replaced the temp file
        between it being written and being linked -- acquire_lock must
        not return success. It must remove the bad link and raise.

        Simulates the swap by making the mocked os.link() ignore our
        real temp file and link an unrelated decoy file into *path*
        instead, the same observable effect a symlink-swap attack
        would have (verified separately: a real symlink swap makes
        os.link() hard-link *path* to the swapped-in file's inode).
        """
        lock_path = tmp_path / "code-forge.lock"
        decoy = tmp_path / "decoy.txt"
        decoy.write_text("not a pid\n")
        real_link = os.link

        def _link_to_decoy_instead(src, dst):
            real_link(str(decoy), dst)

        monkeypatch.setattr(os, "link", _link_to_decoy_instead)
        with pytest.raises(OSError, match="did not match"):
            acquire_lock(lock_path)
        assert not lock_path.exists()
        # The decoy itself is untouched -- only *path*'s link to it
        # was removed, not the file the attack pointed at.
        assert decoy.read_text() == "not a pid\n"

    def test_symlink_swap_after_link_rejected_not_returned_success(
        self, tmp_path, monkeypatch
    ):
        """A genuine hard link to our own temp file must not be
        confused with a symlink that resolves to the same content.

        Simulates an attacker who, in the instant right after our
        os.link() succeeds, removes the real hard link at *path* and
        replaces it with a symlink pointing back at tmp_path (still
        present at that point). stat() would follow the symlink and
        see the correct inode, wrongly treating this as a match --
        confirmed empirically that only lstat() (which inspects the
        directory entry itself, not what it resolves to) tells the
        two apart. Without that distinction, acquire_lock would return
        success while *path* is actually a symlink that goes dangling
        the moment tmp_path is cleaned up.
        """
        lock_path = tmp_path / "code-forge.lock"
        real_link = os.link

        def _link_then_swap_for_symlink(src, dst):
            real_link(src, dst)
            os.unlink(dst)
            os.symlink(src, dst)

        monkeypatch.setattr(os, "link", _link_then_swap_for_symlink)
        with pytest.raises(OSError, match="did not match"):
            acquire_lock(lock_path)
        # The bad symlink must not be left behind masquerading as a
        # valid lock.
        assert not lock_path.is_symlink()
        assert not lock_path.exists()

    def test_inode_mismatch_unlink_race_does_not_mask_tampering_error(
        self, tmp_path, monkeypatch
    ):
        """If *path* is removed by something else between the
        inode-check stat() and our own cleanup unlink(), that race
        must not replace the informative tampering OSError with a
        bare FileNotFoundError from the unlink call itself.
        """
        lock_path = tmp_path / "code-forge.lock"
        decoy = tmp_path / "decoy.txt"
        decoy.write_text("not a pid\n")
        real_link = os.link
        real_unlink = os.unlink

        def _link_to_decoy_instead(src, dst):
            real_link(str(decoy), dst)

        def _unlink_path_already_gone(target):
            if target == str(lock_path):
                raise FileNotFoundError(
                    "simulated: someone else already removed it"
                )
            return real_unlink(target)

        monkeypatch.setattr(os, "link", _link_to_decoy_instead)
        monkeypatch.setattr(os, "unlink", _unlink_path_already_gone)
        with pytest.raises(OSError, match="did not match"):
            acquire_lock(lock_path)

    def test_no_hard_link_support_propagates_as_plain_oserror(
        self, tmp_path, monkeypatch
    ):
        """A filesystem without hard-link support (e.g. FAT32) makes
        os.link() raise OSError -- documented as unsupported with no
        fallback, so that OSError must reach the caller as-is rather
        than being swallowed or misread as a busy/stale lock."""
        lock_path = tmp_path / "code-forge.lock"

        def _no_hardlink_support(src, dst):
            raise OSError(
                "simulated: filesystem does not support hard links"
            )

        monkeypatch.setattr(os, "link", _no_hardlink_support)
        with pytest.raises(OSError, match="does not support hard links"):
            acquire_lock(lock_path)
        assert list(tmp_path.glob("*.tmp-*")) == []

    def test_directory_at_lock_path_raises_friendly_oserror(
        self, tmp_path
    ):
        """A directory sitting at the lock path -> friendly OSError,
        no leftover temp file.

        os.link() fails with FileExistsError (not IsADirectoryError)
        when the destination is an existing directory, which routes
        through _handle_existing_lock -- confirmed empirically to still
        surface the same friendly message as the old direct-open path,
        with no temp file left behind.
        """
        lock_path = tmp_path / "code-forge.lock"
        lock_path.mkdir()
        with pytest.raises(OSError, match="is a directory"):
            acquire_lock(lock_path)
        assert list(tmp_path.glob("*.tmp-*")) == []

    def test_directory_at_lock_path_permission_error_variant(
        self, tmp_path, monkeypatch
    ):
        """Same as above, but simulating a platform where os.link()
        raises PermissionError instead of FileExistsError for a
        directory destination (observed on some non-Linux Unixes).
        """
        lock_path = tmp_path / "code-forge.lock"
        lock_path.mkdir()
        real_link = os.link

        def _link_as_permission_error(src, dst):
            try:
                real_link(src, dst)
            except FileExistsError:
                raise PermissionError("simulated non-Linux errno")

        monkeypatch.setattr(os, "link", _link_as_permission_error)
        with pytest.raises(OSError, match="is a directory"):
            acquire_lock(lock_path)
        assert list(tmp_path.glob("*.tmp-*")) == []

    def test_genuine_permission_error_propagates_raw(
        self, tmp_path, monkeypatch
    ):
        """A PermissionError unrelated to *path* being a directory must
        propagate as-is, not get misread as a busy or stale lock.

        Distinguishes the macOS directory-destination quirk (handled
        above) from a real EACCES on the target directory, where
        misclassifying it as ForgeLockBusy would hide the actual cause.
        """
        lock_path = tmp_path / "code-forge.lock"

        def _boom_link(src, dst):
            raise PermissionError("simulated EACCES, unrelated to path")

        monkeypatch.setattr(os, "link", _boom_link)
        with pytest.raises(PermissionError, match="simulated EACCES"):
            acquire_lock(lock_path)
        assert list(tmp_path.glob("*.tmp-*")) == []

    def test_is_dir_probe_failure_does_not_mask_original_error(
        self, tmp_path, monkeypatch
    ):
        """If path.is_dir() itself raises while deciding whether a
        PermissionError from os.link() was the directory-destination
        case, the ORIGINAL PermissionError must still propagate --
        not the probe's own, unrelated failure.

        Distinguishes a probe-time error (e.g. EACCES on a directory
        component that changed since os.link() ran) from a genuine
        answer of False, so a second exception never silently replaces
        the first one a caller actually needs to see.
        """
        lock_path = tmp_path / "code-forge.lock"
        real_is_dir = Path.is_dir

        def _boom_link(src, dst):
            raise PermissionError("simulated EACCES from os.link")

        def _boom_is_dir(self):
            # Only fake a probe failure for *lock_path* itself -- Path.mkdir
            # (called earlier in acquire_lock for path.parent, which is
            # tmp_path and genuinely exists) also calls is_dir() internally
            # under exist_ok=True, and must keep seeing real answers.
            if self == lock_path:
                raise OSError("simulated EACCES probing is_dir")
            return real_is_dir(self)

        monkeypatch.setattr(os, "link", _boom_link)
        monkeypatch.setattr(Path, "is_dir", _boom_is_dir)
        with pytest.raises(
            PermissionError, match="simulated EACCES from os.link"
        ):
            acquire_lock(lock_path)
        assert list(tmp_path.glob("*.tmp-*")) == []


class TestAcquireDeadPid:
    """(c) acquire on existing lock with dead PID -> stale removed."""

    def test_dead_pid_recovers(self, tmp_path):
        lock_path = tmp_path / "code-forge.lock"
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        dead_pid = proc.pid
        lock_path.write_text("%d\n" % dead_pid, encoding="utf-8")
        acquire_lock(lock_path)
        content = lock_path.read_text(encoding="utf-8").strip()
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

    @pytest.mark.skipif(
        os.name == "nt",
        reason="POSIX kill(0) EPERM path; the Windows "
               "ACCESS_DENIED analogue is the ctypes branch "
               "covered by TestPidAliveWindowsBranch",
    )
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


# ---------------------------------------------------------------------------
# D2: _pid_alive platform-portable liveness check
# ---------------------------------------------------------------------------


class TestPidAlive:
    """_pid_alive returns True for running, False for dead."""

    def test_current_pid_alive(self):
        """Our own PID is alive."""
        assert _pid_alive(os.getpid()) is True

    def test_dead_pid_not_alive(self):
        """A PID that never existed is dead."""
        # PID 0 is "swapper" on Linux, never a real user process.
        # Use a very high PID unlikely to exist.
        assert _pid_alive(999999999) is False

    def test_sleeper_alive_then_dead(self, tmp_path):
        """Spawn sleeper -> alive; kill -> dead (real process lifecycle)."""
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(300)"],
        )
        try:
            assert _pid_alive(proc.pid) is True
        finally:
            proc.terminate()
            proc.wait(timeout=5)
        assert _pid_alive(proc.pid) is False

    def test_handle_existing_lock_busy(self, tmp_path):
        """Sleeper PID in lock -> ForgeLockBusy AND sleeper survives."""
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(300)"],
        )
        lock_path = tmp_path / "forge.lock"
        lock_path.write_text(str(proc.pid))
        try:
            with pytest.raises(ForgeLockBusy) as exc_info:
                _handle_existing_lock(lock_path)
            assert exc_info.value.pid == proc.pid
            # Sleeper must still be alive (the probe did not kill it)
            assert proc.poll() is None
            # Busy must not remove the live owner's lock: deleting it
            # would let a second forge instance start alongside the
            # running one.
            assert lock_path.exists()
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_handle_existing_lock_removes_stale(self, tmp_path):
        """Dead PID in lock -> lock file removed, no raise."""
        proc = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.exit(0)"],
        )
        proc.wait(timeout=5)
        lock_path = tmp_path / "forge.lock"
        lock_path.write_text(str(proc.pid))
        _handle_existing_lock(lock_path)
        assert not lock_path.exists()

    def test_handle_existing_lock_empty_file_reclaimed_immediately(
        self, tmp_path
    ):
        """Empty/unparseable lock file -> reclaimed right away, no raise.

        Under the atomic write-then-link design, path is never linked
        into place until its content is fully written, so an empty path
        can only come from outside this module (an older forge version,
        manual tampering) -- there is no legitimate in-progress writer
        to wait for.
        """
        lock_path = tmp_path / "forge.lock"
        lock_path.write_text("")
        _handle_existing_lock(lock_path)
        assert not lock_path.exists()

    @pytest.mark.skipif(os.name != "nt", reason="Windows-only")
    def test_windows_pid_alive_ctypes(self):
        """Windows branch uses ctypes OpenProcess (skip on Linux)."""
        # This test runs on gpu-win W2; skipped on Linux CI.
        assert _pid_alive(os.getpid()) is True


class _FakeCtypes:
    """Minimal ctypes stub for exercising the Windows _pid_alive branch."""

    c_void_p = type("c_void_p", (), {})
    c_uint32 = type("c_uint32", (), {})
    c_int = type("c_int", (), {})

    class _Func:
        """Callable that supports .restype/.argtypes attribute assignment."""
        def __init__(self, return_value):
            self._return_value = return_value
            self.restype = None
            self.argtypes = None

        def __call__(self, *args):
            return self._return_value

    class _FakeDLL:
        def __init__(self, open_result, wait_result):
            self._close_calls = []
            self.OpenProcess = _FakeCtypes._Func(open_result)
            self.WaitForSingleObject = _FakeCtypes._Func(wait_result)
            self.CloseHandle = self._make_close()

        def _make_close(self):
            # Closure captures this _FakeDLL instance so __call__ can
            # append to _close_calls -- one CloseHandle per DLL instance.
            dll = self

            class _CloseFunc:
                def __init__(self):
                    self.restype = None
                    self.argtypes = None

                def __call__(self, handle):
                    dll._close_calls.append(handle)

            return _CloseFunc()

    def __init__(self, open_result=1, wait_result=0, last_error=0):
        self._open_result = open_result
        self._wait_result = wait_result
        self._last_error = last_error
        self._dll = None

    def WinDLL(self, name, use_last_error=False):
        self._dll = self._FakeDLL(
            self._open_result, self._wait_result,
        )
        return self._dll

    def get_last_error(self):
        return self._last_error


class TestPidAliveWindowsBranch:
    """Linux-runnable tests for the Windows ctypes liveness branch."""

    def _run_with_stub(self, monkeypatch, open_result, wait_result, last_error):
        fake = _FakeCtypes(open_result, wait_result, last_error)
        monkeypatch.setattr("code_forge.lock.os.name", "nt")
        monkeypatch.setattr("code_forge.lock.ctypes", fake)
        return _pid_alive(12345), fake

    def test_openprocess_zero_error87_dead(self, monkeypatch):
        """OpenProcess -> 0, ERROR_INVALID_PARAMETER (87) -> dead."""
        result, _ = self._run_with_stub(monkeypatch, 0, 0, 87)
        assert result is False

    def test_openprocess_zero_error5_alive(self, monkeypatch):
        """OpenProcess -> 0, ERROR_ACCESS_DENIED (5) -> alive."""
        result, _ = self._run_with_stub(monkeypatch, 0, 0, 5)
        assert result is True

    def test_wait_object_0_dead(self, monkeypatch):
        """handle ok, WAIT_OBJECT_0 (0) -> dead."""
        result, _ = self._run_with_stub(monkeypatch, 1, 0, 0)
        assert result is False

    def test_wait_timeout_alive(self, monkeypatch):
        """handle ok, WAIT_TIMEOUT (0x102) -> alive."""
        result, _ = self._run_with_stub(monkeypatch, 1, 0x102, 0)
        assert result is True

    def test_wait_failed_alive(self, monkeypatch):
        """handle ok, WAIT_FAILED (0xFFFFFFFF) -> alive (fail-safe)."""
        result, _ = self._run_with_stub(
            monkeypatch, 1, 0xFFFFFFFF, 0,
        )
        assert result is True

    def test_close_handle_called_once(self, monkeypatch):
        """CloseHandle called exactly once when a handle was returned."""
        _, fake = self._run_with_stub(monkeypatch, 42, 0, 0)
        assert fake._dll._close_calls == [42]
