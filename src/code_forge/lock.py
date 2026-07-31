# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""STATE-11 file lock with PID liveness probing.

Race-safe atomic acquire via write-to-temp-file + os.link(): the lock
path only ever comes into existence already containing its PID, never
transiently empty. Stale-PID recovery via _pid_alive() liveness check
(platform-portable: POSIX kill(2) or Windows WaitForSingleObject).
"""
from __future__ import annotations

import ctypes
import os
import signal
import tempfile
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

    The message spells out what to do because the short form did not.
    Naming a PID and a file path and stopping there reads as an
    invitation to delete the file, and that is the one action that
    breaks the guarantee this lock exists to provide: acquire_lock
    already reclaims a lock whose holder is dead, so anything that
    reaches here has a LIVE holder, and unlinking it puts two forge
    runs on one workspace. Observed in the field -- a session hit this,
    read it as leftover residue, and reached for rm.

    pid < 0 means the holder identity was lost to a race (the lock was
    reclaimed as stale, then recreated before the retry). The advice
    is the same minus the ps line, which needs a real PID.
    """

    def __init__(self, pid: int, path: Path):
        self.pid = pid
        self.path = path
        if pid < 0:
            who = "another forge process took the lock while it was being reclaimed"
            probe = ""
        else:
            who = "another forge process is running (PID %d)" % pid
            probe = (
                "\n  Check what it is doing:\n"
                "      ps -p %d -o etime=,cmd=" % pid
            )
        super().__init__(
            "%s, lock %s.%s\n"
            "  A review holds this lock for its whole convergence run, which can\n"
            "  take several minutes. Do not delete the lock file: it is released\n"
            "  when the holder exits, and removing it while the holder is alive\n"
            "  lets two forge runs share one workspace. A lock whose holder has\n"
            "  died is reclaimed automatically and never needs deleting. If the\n"
            "  holder is genuinely hung, kill it and the lock clears."
            % (who, path, probe)
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


def _write_all(fd: int, data: bytes) -> None:
    """os.write(fd, data) that loops until every byte lands.

    write() to a regular file essentially never returns a short count
    for a buffer this small, but nothing in the os.write() contract
    guarantees it -- looping costs nothing and removes the theoretical
    gap where a partial write leaves a truncated PID in the temp file.

    A zero-byte return with data still pending is not a short write to
    retry against -- there is no forward progress, so looping again
    would spin forever. Raise instead of trusting the loop to recover.
    """
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written == 0:
            raise OSError(
                "os.write() returned 0 with %d byte(s) still pending"
                % len(view)
            )
        view = view[written:]


def acquire_lock(path: Path) -> None:
    """Acquire lock exclusively. Recovers from stale PID locks.

    The PID is written to a fresh temp file first, then linked into
    place with os.link() -- link() is atomic and fails with EEXIST if
    the destination exists, exactly like the old O_CREAT|O_EXCL open,
    but *path* only ever comes into existence already containing its
    final content. There is no "created but still empty" moment for a
    concurrent reader to observe, unlike a plain create-then-write.

    The temp file itself is created via tempfile.mkstemp(), which uses
    an unpredictable name and O_CREAT|O_EXCL under the hood -- a fixed,
    guessable temp name opened with O_TRUNC would let another user with
    write access to the same directory pre-plant a symlink there and
    have their target truncated when we write the PID.

    On EEXIST: probe PID liveness; alive -> ForgeLockBusy; dead -> remove
    + retry (single retry; second EEXIST -> ForgeLockBusy with pid=-1).

    Not supported: a forge process on this atomic-link scheme racing a
    forge process from before this change (which created *path* via a
    plain O_CREAT|O_EXCL open, with its own brief empty-file window).
    _handle_existing_lock reclaims an empty file unconditionally, which
    is correct once every live process uses this scheme, but could
    theoretically unlink an old-scheme process's file mid-write during
    a rolling upgrade. Accepted: this requires two different forge
    versions racing the exact same lock file at the same instant, which
    does not arise from a normal single-version install.

    Also not supported: a lock directory on a filesystem without hard
    link support (e.g. FAT32). os.link() raises OSError there, and
    this function does not fall back to a non-atomic create -- doing
    so would reintroduce the empty-file race this design exists to
    remove. dir=path.parent for the temp file rules out EXDEV (temp
    file and lock always share a filesystem); a git working tree
    cannot usefully live on such a filesystem anyway.

    A directory entry with write access to path.parent could delete
    the temp file and replace it with a symlink to an unrelated file
    between the write above and the os.link() call below -- confirmed
    empirically that this makes os.link() silently hard-link *path* to
    that file's inode instead of ours. The inode comparison right after
    a successful link catches this and undoes it rather than returning
    a hijacked lock as a success; it cannot prevent the swap itself
    (that would need keeping the fd open and linking through
    /proc/self/fd, which is Linux-only), only refuse to treat it as
    acquired. This requires the same write access to path.parent as
    every other risk in this function's threat model.

    A process killed with SIGKILL, or any termination the interpreter
    cannot intercept (a segfault in a C extension, power loss, an
    OOM-killer SIGKILL), between creating the temp file and this
    function's own finally-block cleanup leaves an orphaned
    ".tmp-<random>" file in the lock directory. This does not affect
    *path* or the mutual-exclusion guarantee: mkstemp's random suffix
    means every call's name is unique, so a leaked file from a past
    crash is never read or reinterpreted by a later acquire_lock()
    call -- it is inert disk clutter, not a correctness risk. No
    cleanup hook (atexit, a signal handler) closes this window either,
    since none of them run under SIGKILL -- that would trade one
    unfixable gap for another that only looks safer. An external
    periodic sweep of stale ".tmp-*" files is the appropriate fix if
    this ever becomes a practical nuisance, not a change here.

    On Windows, the lstat()-based inode comparison above resolves
    through GetFileInformationByHandle to the NTFS file reference
    number under normal conditions, giving the same tamper-detection
    guarantee as a POSIX inode. It is documented to read as 0 in
    narrower cases -- a WebDAV-mounted network drive, and a
    since-addressed historical bug on the newer ReFS filesystem --
    where the symlink-swap detection above would not distinguish a
    genuine link from a same-named replacement. A git working tree
    does not normally live on a WebDAV mount; this is the same
    category of accepted, filesystem-specific limitation as the FAT32
    case above.

    Raises:
      ForgeLockBusy: another live forge holds the lock.
      OSError: filesystem error (parent dir missing, EACCES, no
        hard-link support, inode mismatch after linking, etc).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    pid_bytes = ("%d\n" % os.getpid()).encode("ascii")
    try:
        for attempt in range(2):
            fd, tmp_name = tempfile.mkstemp(
                dir=str(path.parent), prefix=path.name + ".tmp-"
            )
            tmp_path = Path(tmp_name)
            try:
                try:
                    _write_all(fd, pid_bytes)
                    # Captured from the still-open fd -- immune to any
                    # later swap of the *name* tmp_path refers to.
                    expected_ino = os.fstat(fd).st_ino
                finally:
                    os.close(fd)
                try:
                    os.link(str(tmp_path), str(path))
                except FileExistsError:
                    pass
                except FileNotFoundError:
                    # tmp_path itself is gone -- something with write
                    # access to path.parent removed our own temp file
                    # before we could link it. Not a busy/stale lock at
                    # *path*; report it as what it is instead of
                    # letting a bare "no such file" propagate.
                    raise OSError(
                        "temp file for %s disappeared before it could "
                        "be linked into place (concurrent activity in "
                        "%s?)" % (path, path.parent)
                    )
                except PermissionError as exc:
                    # A directory at *path* reliably raises
                    # FileExistsError on Linux; some other Unixes have
                    # been observed to raise PermissionError for the
                    # same case. Only reroute that specific case --
                    # a genuine permission problem (EACCES on the
                    # parent directory, unrelated to *path* itself)
                    # must propagate raw rather than being misread as
                    # a busy or stale lock. path.is_dir() itself does a
                    # stat() and can raise (e.g. EACCES on a directory
                    # component that changed since os.link() ran) --
                    # that probe failure must not replace the original
                    # error with a second, unrelated one.
                    try:
                        found_dir = path.is_dir()
                    except OSError:
                        raise exc from None
                    if not found_dir:
                        raise
                else:
                    # A directory entry with write access to *path*'s
                    # parent could, between the write above and this
                    # link, replace tmp_path with a symlink to an
                    # unrelated file -- os.link() would then silently
                    # hard-link *path* to that file's inode instead of
                    # ours. Refuse to treat that as a successful
                    # acquire: verified empirically that a swapped
                    # source produces a *path* whose inode differs from
                    # what we actually wrote.
                    #
                    # lstat(), not stat(): the same attacker could also
                    # remove our genuine hard link right after it is
                    # created and replace *path* with a symlink back to
                    # tmp_path (still present until our own cleanup
                    # below runs) -- stat() follows that symlink and
                    # would see the correct inode, wrongly passing this
                    # check, right before cleanup deletes tmp_path and
                    # leaves *path* a dangling symlink. Confirmed
                    # empirically: stat() cannot tell a genuine hard
                    # link from a symlink pointing at the same content;
                    # lstat() does, because it looks at the directory
                    # entry itself rather than what it resolves to.
                    try:
                        linked_ino = os.lstat(str(path)).st_ino
                    except FileNotFoundError:
                        # *path* existed right after os.link() returned
                        # but is gone by the time we check it -- some
                        # other concurrent activity removed it. We no
                        # longer hold a stable lock either way; say so
                        # plainly instead of letting a bare "no such
                        # file" surface from the stat call itself.
                        raise OSError(
                            "lock file %s disappeared immediately "
                            "after being linked into place (concurrent "
                            "removal?)" % path
                        )
                    if linked_ino != expected_ino:
                        # A third party could remove *path* between the
                        # stat above and this unlink -- that race must
                        # not swap out the informative tampering error
                        # for a bare FileNotFoundError.
                        try:
                            os.unlink(str(path))
                        except FileNotFoundError:
                            pass
                        raise OSError(
                            "lock file %s did not match the expected "
                            "content after linking (concurrent "
                            "tampering with the temp file?)" % path
                        )
                    return
                if attempt == 1:
                    raise ForgeLockBusy(-1, path)
                _handle_existing_lock(path)
                continue
            finally:
                try:
                    os.unlink(str(tmp_path))
                except FileNotFoundError:
                    pass
    except IsADirectoryError:
        raise OSError(
            "lock path %s is a directory, not a file "
            "(remove it manually)" % path
        )


def _handle_existing_lock(path: Path) -> None:
    """Read PID; check liveness; remove if dead.

    ValueError (empty/unparseable file) means *path* was not created by
    this module's acquire_lock (which never links a path into place
    until its content is fully written) -- most likely a leftover from
    an older forge version or manual tampering. Reclaim it like any
    other stale lock; there is no legitimate in-progress writer to wait
    for.
    """
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except FileNotFoundError:
        return
    except ValueError:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    if _pid_alive(pid):
        raise ForgeLockBusy(pid, path)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
