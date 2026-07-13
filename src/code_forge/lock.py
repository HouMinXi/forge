# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""STATE-11 file lock with PID liveness probing.

Race-safe atomic acquire via O_CREAT|O_EXCL. Stale-PID recovery via
_pid_alive() liveness check (platform-portable: POSIX kill(2) or
Windows WaitForSingleObject).
"""
from __future__ import annotations

import ctypes
import os
import signal
from pathlib import Path
from types import TracebackType
from typing import Optional, Type


def _pid_alive(pid: int) -> bool:
    """Check whether a process with *pid* is still running.

    POSIX: os.kill(pid, 0) -- no signal delivered, just probes.
      ProcessLookupError -> dead; PermissionError -> alive (owned
      by another user); success -> alive.

    Windows: ctypes OpenProcess + WaitForSingleObject.
      os.kill(pid, 0) on Windows calls TerminateProcess, which
      KILLS the target -- never safe as a probe.
    """
    if os.name == "nt":
        # use_last_error=True + ctypes.get_last_error() is the only
        # reliable error read; ctypes.GetLastError() via plain windll
        # can return a stale value from unrelated intervening calls.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [
            ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32,
        ]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.WaitForSingleObject.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32,
        ]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        # PROCESS_QUERY_LIMITED_INFORMATION (0x1000) opens almost any
        # PID; SYNCHRONIZE (0x00100000) is additionally required by
        # WaitForSingleObject -- without it the wait fails with
        # ERROR_ACCESS_DENIED and a live owner would read as dead.
        h = kernel32.OpenProcess(0x00100000 | 0x1000, False, pid)
        if not h:
            # ERROR_INVALID_PARAMETER (87) is the documented "no such
            # PID" error -> dead.  Anything else (ERROR_ACCESS_DENIED 5,
            # or an unexpected code) fails safe as alive: wrongly
            # reporting dead deletes a live process's lock and lets two
            # forge processes run concurrently, while wrongly reporting
            # alive only costs a spurious Busy error.
            return ctypes.get_last_error() != 87
        try:
            # WAIT_OBJECT_0 (0) -> the process has exited.  Anything
            # else fails safe as alive: WAIT_TIMEOUT (0x102) means
            # still running, and WAIT_FAILED (0xFFFFFFFF) means the
            # probe itself broke -- treating that as dead would delete
            # a live owner's lock.
            return kernel32.WaitForSingleObject(h, 0) != 0
        finally:
            kernel32.CloseHandle(h)
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True


class ForgeLockBusy(Exception):
    """Raised when another live forge process holds the lock.

    .pid attribute carries the holder PID for caller logging.
    """

    def __init__(self, pid: int, path: Path):
        self.pid = pid
        self.path = path
        super().__init__(
            "another forge process is running (PID %d, lock %s)"
            % (pid, path)
        )


class ForgeLock:
    """Context manager + explicit release().

    Usage:
      with ForgeLock(Path(".code-forge/forge.lock")) as lock:
          ... do forge work ...
      # released on normal exit OR on exception in body OR on
      # SIGINT/SIGTERM.
    """

    def __init__(self, path: Path):
        self.path = path
        self._held = False
        self._original_sigint = None
        self._original_sigterm = None

    def __enter__(self) -> "ForgeLock":
        acquire_lock(self.path)
        self._held = True
        self._install_signal_handlers()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.release()

    def release(self) -> None:
        if self._held and self.path.exists():
            try:
                stored = int(self.path.read_text(encoding="utf-8").strip())
                if stored == os.getpid():
                    self.path.unlink()
            except (ValueError, FileNotFoundError):
                pass
            self._held = False
        self._restore_signal_handlers()

    def _install_signal_handlers(self) -> None:
        """Save + chain previous handler per R1 B1.

        Signal handlers can only be set from the main thread. When running
        in a worker thread (e.g. MCP sampling via asyncio.to_thread), skip
        handler installation -- the lock file is still cleaned up by release()
        in __exit__, just without signal-interrupt protection.
        """
        import threading
        if threading.current_thread() is not threading.main_thread():
            return

        def _make_chained_handler(prev):
            def _handler(signum, frame):
                try:
                    self.release()
                except Exception:  # noqa: BLE001
                    pass
                if callable(prev):
                    prev(signum, frame)
                    return
                if prev == signal.SIG_IGN:
                    return
                raise KeyboardInterrupt
            return _handler

        prev_sigint = signal.getsignal(signal.SIGINT)
        prev_sigterm = signal.getsignal(signal.SIGTERM)
        self._original_sigint = prev_sigint
        self._original_sigterm = prev_sigterm
        signal.signal(
            signal.SIGINT, _make_chained_handler(prev_sigint)
        )
        signal.signal(
            signal.SIGTERM, _make_chained_handler(prev_sigterm)
        )

    def _restore_signal_handlers(self) -> None:
        if self._original_sigint is not None:
            signal.signal(signal.SIGINT, self._original_sigint)
            self._original_sigint = None
        if self._original_sigterm is not None:
            signal.signal(signal.SIGTERM, self._original_sigterm)
            self._original_sigterm = None


def acquire_lock(path: Path) -> None:
    """Acquire lock exclusively. Recovers from stale PID locks.

    On EEXIST: probe PID liveness; alive -> ForgeLockBusy; dead -> remove
    + retry (single retry; second EEXIST -> ForgeLockBusy with pid=-1).

    Raises:
      ForgeLockBusy: another live forge holds the lock.
      OSError: filesystem error (parent dir missing, EACCES, etc).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        try:
            fd = os.open(
                str(path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o644,
            )
            try:
                os.write(fd, ("%d\n" % os.getpid()).encode("ascii"))
            finally:
                os.close(fd)
            return
        except FileExistsError:
            if attempt == 1:
                raise ForgeLockBusy(-1, path)
            _handle_existing_lock(path)
        except IsADirectoryError:
            raise OSError(
                "lock path %s is a directory, not a file "
                "(remove it manually)" % path
            )


def _handle_existing_lock(path: Path) -> None:
    """Read PID; check liveness; remove if dead.

    ValueError (empty/unparseable file) is treated as "owner alive"
    -- the most likely cause is a concurrent O_CREAT|O_EXCL that
    succeeded but whose os.write(pid) hasn't landed yet.  Deleting
    the file in that window lets both contenders believe they won.
    """
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except FileNotFoundError:
        return
    except ValueError:
        # Empty or unparseable lock file -- the owner likely just
        # created it and hasn't written its PID yet.  Treat as
        # alive to avoid a race where we unlink the file and both
        # processes acquire the lock.
        raise ForgeLockBusy(-1, path)
    if _pid_alive(pid):
        raise ForgeLockBusy(pid, path)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
