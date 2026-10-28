# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Conventions digest module.

Extracts naming conventions from the current repository to populate the
conventions-digest slot in criteria payloads. The digest is derived
independently of the implementer.

Exported symbols:
  _extract_python_public_names -- shared AST helper (also used by conventions_resolver.py)
  get_same_repo_digest         -- same-repo naming conventions digest
  get_digest                   -- full conventions digest
"""

from __future__ import annotations

import ast
import ctypes
import errno
import hashlib
import os
import stat
import sys
from pathlib import Path
from typing import Optional

_SKIP_DIRS = frozenset(
    {
        "__pycache__",
        ".git",
        "venv",
        ".venv",
        ".tox",
        "build",
        "dist",
        "node_modules",
        ".eggs",
    }
)
_MAX_ROSTER_FILES = 20_000
_FileStamp = tuple[int, int, int, int, int, int]
_DirectoryStamp = tuple[int, int, int]


def _is_convention_file(name: str) -> bool:
    return name.endswith((".py", ".js", ".go", ".rs")) or (
        name.endswith(".ts") and not name.endswith(".d.ts")
    )


def _file_stamp(info: os.stat_result) -> _FileStamp:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_size,
        info.st_mtime_ns, info.st_ctime_ns,
    )


def _directory_stamp(info: os.stat_result) -> _DirectoryStamp:
    """Identify a directory by device, inode and ctime.

    Known limit (#197): on kernels and filesystems without multigrain
    timestamps, the kernel stamps ctime from a coarse clock that advances once
    per scheduler tick. Moving a directory away and back inside the same tick
    leaves this stamp unchanged, so that swap cannot be seen here.
    """
    return info.st_dev, info.st_ino, info.st_ctime_ns


_OPENAT2_SYSCALL_NUMBER = (
    437 if sys.platform == "linux" and os.uname().machine in {"x86_64", "aarch64", "riscv64"}
    else None
)
_RESOLVE_NO_SYMLINKS = 0x04
_RESOLVE_BENEATH = 0x08
_HAS_DIR_FD_OPEN = os.open in getattr(os, "supports_dir_fd", ())


class _ScanStatus:
    """Record whether a source scan omitted a selected candidate."""

    def __init__(self) -> None:
        self.complete = True
        self.seen_files: set[Path] = set()
        self.candidates: dict[Path, Path] = {}
        self.admitted_files: dict[Path, tuple[int, int, int, _FileStamp]] = {}
        self.read_blobs: dict[Path, tuple[str, str, str, str]] = {}
        self.skipped_files: dict[Path, int] = {}
        self.python_walk_root: Optional[Path] = None
        self.committed_alias = False
        self.unproven_bytes = False
        self._walks: dict[
            tuple[int, int], list[tuple[
                Path, dict[Path, _DirectoryStamp], dict[Path, _FileStamp],
            ]],
        ] = {}

    def fail(self, _error: Optional[OSError] = None) -> None:
        self.complete = False

    def record_candidate(self, path: Path, real_path: Path, scan_root: Path) -> None:
        """Bind a selected walk entry to the path that its reader opened."""
        try:
            logical = path.relative_to(scan_root)
            resolved = real_path.relative_to(scan_root)
        except ValueError:
            self.fail()
            return
        previous = self.candidates.setdefault(logical, resolved)
        if previous != resolved:
            self.fail()

    def record_admitted_file(
        self, path: Path, scan_root: Path, scan_root_fd: int, observed: os.stat_result,
    ) -> None:
        """Bind a size-checked candidate to this root fd and its exact inode."""
        try:
            relative = path.relative_to(scan_root)
            root_info = os.fstat(scan_root_fd)
        except (OSError, ValueError):
            self.fail()
            return
        proof = (
            scan_root_fd, root_info.st_dev, root_info.st_ino, _file_stamp(observed),
        )
        previous = self.admitted_files.setdefault(relative, proof)
        if previous != proof:
            self.fail()

    def matches_admitted_file(
        self, relative: Path, scan_root_fd: int, fd: int,
    ) -> bool:
        """Allow a moved root only for a candidate proved before the move."""
        expected = self.admitted_files.get(relative)
        if expected is None or expected[0] != scan_root_fd:
            return False
        try:
            root_info = os.fstat(scan_root_fd)
            file_info = os.fstat(fd)
        except OSError:
            return False
        return expected[1:] == (
            root_info.st_dev, root_info.st_ino, _file_stamp(file_info),
        )

    def record_read(self, path: Path, scan_root: Path, data: bytes) -> None:
        """Remember Git object IDs of the exact source bytes used by parsers."""
        try:
            relative = path.relative_to(scan_root)
        except ValueError:
            self.fail()
            return
        normalized = data.replace(b"\r\n", b"\n")
        raw_blob = b"blob " + str(len(data)).encode("ascii") + b"\0" + data
        normalized_blob = (
            b"blob " + str(len(normalized)).encode("ascii") + b"\0" + normalized
        )
        hashes = (
            hashlib.sha1(raw_blob).hexdigest(), hashlib.sha256(raw_blob).hexdigest(),
            hashlib.sha1(normalized_blob).hexdigest(),
            hashlib.sha256(normalized_blob).hexdigest(),
        )
        previous = self.read_blobs.setdefault(relative, hashes)
        if previous != hashes:
            self.fail()

    def record_skip(self, path: Path, scan_root: Path, size: int) -> None:
        """Keep the observed size when a selected source is intentionally omitted."""
        try:
            relative = path.relative_to(scan_root)
        except ValueError:
            self.fail()
            return
        previous = self.skipped_files.setdefault(relative, size)
        if previous != size:
            self.fail()

    def record_walk(
        self, root_identity: tuple[int, int], walk_root: Path,
        directories: dict[Path, _DirectoryStamp],
        files: dict[Path, _FileStamp],
    ) -> None:
        """Reject selected trees that changed between language passes."""
        self.seen_files.update(files)
        for previous_root, previous_dirs, previous_files in self._walks.get(root_identity, []):
            if previous_root == walk_root:
                if previous_dirs != directories or previous_files != files:
                    self.fail()
            elif previous_root in walk_root.parents:
                overlapping_dirs = {
                    path: identity for path, identity in previous_dirs.items()
                    if path == walk_root or walk_root in path.parents
                }
                overlapping_files = {
                    path: stamp for path, stamp in previous_files.items()
                    if walk_root in path.parents
                }
                if overlapping_dirs != directories or overlapping_files != files:
                    self.fail()
            elif walk_root in previous_root.parents:
                overlapping_dirs = {
                    path: identity for path, identity in directories.items()
                    if path == previous_root or previous_root in path.parents
                }
                overlapping_files = {
                    path: stamp for path, stamp in files.items()
                    if previous_root in path.parents
                }
                if previous_dirs != overlapping_dirs or previous_files != overlapping_files:
                    self.fail()
        self._walks.setdefault(root_identity, []).append((walk_root, directories, files))


class _OpenHow(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_uint64),
        ("mode", ctypes.c_uint64),
        ("resolve", ctypes.c_uint64),
    ]


_SYSCALL = None
if _OPENAT2_SYSCALL_NUMBER is not None:
    _SYSCALL = ctypes.CDLL(None, use_errno=True).syscall
    _SYSCALL.restype = ctypes.c_long


def _openat2_beneath(directory_fd: int, relative: Path, flags: int) -> int:
    """Resolve an entire path beneath one directory in a single Linux syscall."""
    assert _SYSCALL is not None and _OPENAT2_SYSCALL_NUMBER is not None
    how = _OpenHow(flags, 0, _RESOLVE_BENEATH | _RESOLVE_NO_SYMLINKS)
    result = _SYSCALL(
        ctypes.c_long(_OPENAT2_SYSCALL_NUMBER),
        ctypes.c_int(directory_fd),
        ctypes.c_char_p(os.fsencode(relative)),
        ctypes.byref(how),
        ctypes.c_size_t(ctypes.sizeof(how)),
    )
    if result < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(relative))
    return int(result)


def _openat_beneath_portable(directory_fd: int, relative: Path, flags: int) -> int:
    """Open each component from a pinned descriptor without following links."""
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise OSError(errno.EINVAL, "invalid rooted path", str(relative))
    directory_flags = (
        getattr(os, "O_PATH", getattr(os, "O_SEARCH", os.O_RDONLY))
        | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    )
    current_fd = os.dup(directory_fd)
    try:
        for component in relative.parts[:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return os.open(
            relative.parts[-1], flags | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=current_fd,
        )
    finally:
        os.close(current_fd)


def _open_beneath(directory_fd: int, relative: Path, flags: int) -> int:
    """Prefer openat2, falling back only when the syscall is unavailable."""
    if _OPENAT2_SYSCALL_NUMBER is not None and _SYSCALL is not None:
        try:
            return _openat2_beneath(directory_fd, relative, flags)
        except OSError as exc:
            if exc.errno != errno.ENOSYS:
                raise
    return _openat_beneath_portable(directory_fd, relative, flags)


def _path_fallback_supported() -> bool:
    """Windows has no Python dir-fd walk, but exposes opened handle paths."""
    return sys.platform == "win32"


def _opened_file_path(fd: int) -> Optional[Path]:
    """Return the final Windows path of an opened file, or fail closed."""
    if not _path_fallback_supported():
        return None
    import msvcrt

    get_path = ctypes.windll.kernel32.GetFinalPathNameByHandleW
    get_path.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar), ctypes.c_uint32, ctypes.c_uint32,
    ]
    get_path.restype = ctypes.c_uint32
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_path(ctypes.c_void_p(msvcrt.get_osfhandle(fd)), buffer, len(buffer), 0)
    if not length or length >= len(buffer):
        return None
    path = buffer.value
    if path.startswith("\\\\?\\UNC\\"):
        path = "\\\\" + path[8:]
    elif path.startswith("\\\\?\\"):
        path = path[4:]
    else:
        return None
    return Path(path)


def _linux_opened_fd_path(fd: int) -> Optional[Path]:
    """Identify an open handle by its current Linux path, if it has one."""
    try:
        path = os.readlink(f"/proc/self/fd/{fd}")
    except OSError:
        return None
    if not path.startswith("/") or path.endswith(" (deleted)"):
        return None
    return Path(path)


def _darwin_opened_fd_path(fd: int) -> Optional[Path]:
    """Use macOS F_GETPATH to identify an opened handle's current path."""
    import fcntl

    command = getattr(fcntl, "F_GETPATH", None)
    if command is None:
        return None
    try:
        # Darwin's MAXPATHLEN is 1024; F_GETPATH requires at least that much.
        raw = fcntl.fcntl(fd, command, bytes(1024))
    except OSError:
        return None
    if not isinstance(raw, bytes) or len(raw) != 1024 or b"\0" not in raw:
        return None
    path = raw.split(b"\0", 1)[0]
    if not path.startswith(b"/"):
        return None
    return Path(os.fsdecode(path))


def _opened_posix_fd_path(fd: int) -> Optional[Path]:
    if sys.platform.startswith("linux"):
        return _linux_opened_fd_path(fd)
    if sys.platform == "darwin":
        return _darwin_opened_fd_path(fd)
    return None


def _root_fd_matches_path(root_fd: int, scan_root: Path) -> bool:
    """Conservatively retain a direct root-fd leaf when handle paths are absent."""
    try:
        opened = os.fstat(root_fd)
        named = os.stat(scan_root, follow_symlinks=False)
    except OSError:
        return False
    return (
        stat.S_ISDIR(opened.st_mode)
        and (opened.st_dev, opened.st_ino) == (named.st_dev, named.st_ino)
    )


def _file_fd_matches_path(fd: int, path: Path) -> bool:
    """Reject a deleted or replaced leaf even if an OS reports its old path."""
    try:
        opened = os.fstat(fd)
        named = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    return (opened.st_dev, opened.st_ino) == (named.st_dev, named.st_ino)


def _path_beneath(path: Path, root: Path) -> bool:
    """Compare full path components, including the Windows drive."""
    try:
        normalized_root = os.path.normcase(str(root))
        normalized_path = os.path.normcase(str(path))
        return os.path.commonpath((normalized_root, normalized_path)) == normalized_root
    except ValueError:
        return False


def _open_scan_root(scan_root: Path) -> Optional[int]:
    """Open the named root without following a symlink in any component."""
    if not scan_root.is_absolute():
        return None
    search_flag = getattr(os, "O_PATH", getattr(os, "O_SEARCH", os.O_RDONLY))
    if (
        os.name != "posix"
        or not _HAS_DIR_FD_OPEN
        or not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "O_DIRECTORY")
    ):
        return None

    directory_flags = search_flag | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    filesystem_fd = os.open("/", directory_flags)
    try:
        relative = scan_root.relative_to(Path("/"))
        if not relative.parts:
            return os.dup(filesystem_fd)
        return _open_beneath(filesystem_fd, relative, directory_flags)
    finally:
        os.close(filesystem_fd)


def _open_scan_file(
    path: Path, scan_root: Path, flags: int, *, scan_root_fd: Optional[int] = None,
    scan_status: Optional[_ScanStatus] = None,
) -> Optional[int]:
    """Open beneath a pinned root and verify an available final-handle path."""
    if not path.is_absolute() or not scan_root.is_absolute():
        return None
    try:
        relative = path.relative_to(scan_root)
    except ValueError:
        return None
    if not relative.parts or ".." in relative.parts:
        return None

    root_fd = scan_root_fd if scan_root_fd is not None else _open_scan_root(scan_root)
    if root_fd is None:
        if not _path_fallback_supported():
            return None
        fd = os.open(path, flags)
        try:
            final_path = _opened_file_path(fd)
            if final_path is not None and _path_beneath(final_path, scan_root):
                return fd
        except OSError:
            pass
        os.close(fd)
        return None
    try:
        fd = _open_beneath(
            root_fd, relative, flags | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        admitted = False
        try:
            opened_root = _opened_posix_fd_path(root_fd)
            opened_file = _opened_posix_fd_path(fd)
            # A component fd can move outside the root between the portable
            # walk and leaf open. Match both pinned handles to their expected
            # full paths before any source bytes are read.
            bound = True
            if opened_root is not None and opened_root != scan_root:
                bound = False
            if opened_file is not None and opened_file != path:
                bound = False
            if (opened_root is None or opened_file is None) and (
                len(relative.parts) > 1 or not _root_fd_matches_path(root_fd, scan_root)
            ):
                # Without final-handle paths, a multi-component portable
                # walk cannot prove containment. A direct root-fd leaf is
                # still safe to admit while the named root matches its fd.
                bound = False
            if not _file_fd_matches_path(fd, path):
                bound = False
            # A stamp proves which candidate was opened, but a child directory
            # may move outside the pinned root after its size check. Require
            # the final leaf handle to remain at that exact rooted path when
            # both handle paths are available. A direct root-fd leaf remains
            # safe when this host cannot expose final handle paths.
            admitted_path_is_rooted = (
                opened_root is not None and opened_file is not None
                and opened_file == opened_root / relative
            )
            if not bound and not (
                scan_status is not None and scan_root_fd is not None
                and (
                    admitted_path_is_rooted
                    or (
                        len(relative.parts) == 1
                        and (opened_root is None or opened_file is None)
                    )
                )
                and scan_status.matches_admitted_file(relative, scan_root_fd, fd)
            ):
                return None
            admitted = True
            return fd
        finally:
            if not admitted:
                os.close(fd)
    finally:
        if scan_root_fd is None:
            os.close(root_fd)


def _walk_source(
    root: Path, scan_root: Path, scan_root_fd: Optional[int],
    scan_status: Optional[_ScanStatus],
):
    """Walk the selected directory by descriptor when a bound root is available.

    With a bound root, the walk compares stamps taken before and after each
    yield and marks the scan incomplete when they differ. Directories are
    stamped with device, inode and ctime. Files are stamped (device, inode,
    mode, size, mtime and ctime) only when they are convention files.

    Known limit (#197): the ctime in both stamps is, on kernels and filesystems
    without multigrain timestamps, taken from a coarse clock that advances once
    per scheduler tick. A directory or convention file that is moved away and
    back within one tick, with nothing else changed, has the same stamp
    afterwards, so the scan still reports complete (the parent directory of a
    moved file has the same limit). Where the kernel stamps ctime finely (Linux
    6.13 and later, on filesystems that support multigrain timestamps) the
    window is closed. The tests wait for a tick before injecting a move
    (``_await_ctime_tick`` in tests/test_conventions.py), and the
    inside-one-tick test skips where every move is visible. Closing the gap on
    the other kernels needs a mechanism that does not depend on the clock; see
    #197.
    """
    onerror = scan_status.fail if scan_status is not None else None
    if scan_root_fd is None:
        yield from os.walk(str(root), onerror=onerror)
        return
    try:
        relative = root.relative_to(scan_root)
        walk_path = str(relative) if relative.parts else "."
        root_info = os.stat(walk_path, dir_fd=scan_root_fd, follow_symlinks=False)
        if not stat.S_ISDIR(root_info.st_mode):
            if scan_status is not None:
                scan_status.fail()
            return
        expected: dict[Path, _DirectoryStamp] = {
            Path(walk_path): _directory_stamp(root_info)
        }
        visited: set[Path] = set()
        inventory: dict[Path, _DirectoryStamp] = {}
        file_inventory: dict[Path, _FileStamp] = {}
        scan_info = os.fstat(scan_root_fd)
        scan_identity = (scan_info.st_dev, scan_info.st_ino)
        scan_stamp = _directory_stamp(scan_info)
        for dirpath, dirs, files, dir_fd in os.fwalk(
            walk_path, dir_fd=scan_root_fd,
            follow_symlinks=False, onerror=onerror,
        ):
            current = Path(dirpath)
            visited.add(current)
            info = os.fstat(dir_fd)
            observed_stamp = _directory_stamp(info)
            if expected.get(current) != observed_stamp:
                if scan_status is not None:
                    scan_status.fail()
                dirs[:] = []
                continue
            inventory[current] = observed_stamp
            before: dict[str, os.stat_result] = {}
            for name in dirs:
                try:
                    before[name] = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                except OSError as exc:
                    if scan_status is not None:
                        scan_status.fail(exc)
            before_files: dict[str, _FileStamp] = {}
            if scan_status is not None:
                for name in files:
                    if not _is_convention_file(name):
                        continue
                    if len(file_inventory) >= _MAX_ROSTER_FILES:
                        scan_status.fail()
                        continue
                    try:
                        stamp = _file_stamp(os.stat(
                            name, dir_fd=dir_fd, follow_symlinks=False,
                        ))
                    except OSError as exc:
                        scan_status.fail(exc)
                        continue
                    before_files[name] = stamp
                    file_inventory[current / name] = stamp
            yield (str(scan_root / dirpath), dirs, files)
            if scan_status is not None:
                if _directory_stamp(os.fstat(dir_fd)) != observed_stamp:
                    scan_status.fail()
                for name, stamp in before_files.items():
                    try:
                        now = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                    except OSError as exc:
                        scan_status.fail(exc)
                        continue
                    if _file_stamp(now) != stamp:
                        scan_status.fail()
            for name in dirs:
                old = before.get(name)
                if old is None:
                    continue
                if not stat.S_ISDIR(old.st_mode) and scan_status is not None:
                    scan_status.fail()
                try:
                    now = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                except OSError as exc:
                    if scan_status is not None:
                        scan_status.fail(exc)
                    continue
                if (old.st_mode, _directory_stamp(old)) != (
                    now.st_mode, _directory_stamp(now)
                ) and scan_status is not None:
                    scan_status.fail()
                if stat.S_ISDIR(old.st_mode):
                    expected[current / name] = _directory_stamp(old)
        if scan_status is not None:
            if (
                _directory_stamp(os.fstat(scan_root_fd)) != scan_stamp
                or _directory_stamp(os.stat(
                    walk_path, dir_fd=scan_root_fd, follow_symlinks=False,
                )) != _directory_stamp(root_info)
            ):
                scan_status.fail()
            if expected.keys() - visited:
                scan_status.fail()
            scan_status.record_walk(
                scan_identity, Path(walk_path), inventory, file_inventory,
            )
    except OSError as exc:
        if scan_status is not None:
            scan_status.fail(exc)


def _source_file_size(
    path: Path, scan_root: Path, scan_root_fd: Optional[int],
    *, observed: Optional[os.stat_result] = None,
) -> int:
    """Use the admitted root for size decisions and reject a changed path."""
    path_info = path.stat()
    if observed is not None and _file_stamp(observed) != _file_stamp(path_info):
        raise OSError(errno.ESTALE, "source changed during scan", str(path))
    if scan_root_fd is None and not _path_fallback_supported():
        return path_info.st_size
    flags = getattr(os, "O_PATH", os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    fd = _open_scan_file(path, scan_root, flags, scan_root_fd=scan_root_fd)
    if fd is None:
        raise OSError(errno.ESTALE, "source path left admitted root", str(path))
    try:
        root_info = os.fstat(fd)
    finally:
        os.close(fd)
    if _file_stamp(path_info) != _file_stamp(root_info) or not stat.S_ISREG(root_info.st_mode):
        raise OSError(errno.ESTALE, "source root changed during scan", str(path))
    return root_info.st_size


def _read_regular_text(
    path: Path,
    *,
    max_bytes: Optional[int] = None,
    errors: str = "replace",
    scan_root: Optional[Path] = None,
    scan_root_fd: Optional[int] = None,
    scan_status: Optional[_ScanStatus] = None,
) -> Optional[str]:
    """Read a regular file, recording access failures for source scans."""
    try:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags) if scan_root is None else _open_scan_file(
            path, scan_root, flags, scan_root_fd=scan_root_fd, scan_status=scan_status,
        )
        if fd is None:
            if scan_status is not None:
                scan_status.fail()
            return None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                if scan_status is not None:
                    scan_status.fail()
                return None
            if max_bytes is not None and info.st_size > max_bytes:
                if scan_status is not None:
                    scan_status.fail()
                return None
            if max_bytes is None:
                with os.fdopen(fd, "r", encoding="utf-8", errors=errors, closefd=False) as stream:
                    return stream.read()

            data = bytearray()
            while len(data) <= max_bytes:
                chunk = os.read(fd, max_bytes + 1 - len(data))
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) > max_bytes:
                if scan_status is not None:
                    scan_status.fail()
                return None
            if scan_status is not None and scan_root is not None:
                scan_status.record_read(path, scan_root, bytes(data))
            return data.decode("utf-8", errors=errors).replace("\r\n", "\n").replace("\r", "\n")
        finally:
            os.close(fd)
    except OSError:
        if scan_status is not None:
            scan_status.fail()
        return None



def _extract_python_public_names(
    dirpath: Path,
    *,
    scan_status: Optional[_ScanStatus] = None,
    scan_root: Optional[Path] = None,
    scan_root_fd: Optional[int] = None,
) -> tuple[list[str], list[str]]:
    """Extract public function and class names from Python source files.

    Shared private helper used by get_same_repo_digest (this file) and
    extract_conventions (conventions_resolver.py). Single source of
    truth for Python AST extraction -- no duplicated AST logic.

    Scans for .py files under dirpath/src (fallback to dirpath if no src/
    subdir). Prunes _SKIP_DIRS during traversal.

    Per-file guards:
    - Skips files larger than 100KB
    - Path.parents containment precheck, followed by rooted no-follow path
      resolution and final-handle validation on POSIX, or final-handle
      containment on Windows
    - Iterates only tree.body (top-level module children, NOT ast.walk which
      picks up nested functions)
    - Collects ast.FunctionDef AND ast.AsyncFunctionDef
    - Skips unparseable files silently (SyntaxError)

    Args:
        dirpath: root directory to scan.
        scan_status: optional completeness signal for cache admission.

    Returns:
        (public_functions, public_classes) -- each sorted, capped at 50.
    """
    scan_root = scan_root if scan_root is not None else dirpath.resolve()

    # Prefer src/ subdir; fall back to dirpath itself.
    src_dir = scan_root / "src"
    walk_root = scan_root
    if scan_root_fd is None:
        if src_dir.is_dir():
            walk_root = src_dir
    else:
        try:
            src_info = os.stat("src", dir_fd=scan_root_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        except OSError as exc:
            if scan_status is not None:
                scan_status.fail(exc)
            return ([], [])
        else:
            if stat.S_ISDIR(src_info.st_mode):
                walk_root = src_dir
            elif stat.S_ISLNK(src_info.st_mode):
                try:
                    target = src_dir.resolve(strict=True)
                    relative = target.relative_to(scan_root)
                    target_info = os.stat(relative, dir_fd=scan_root_fd, follow_symlinks=False)
                except FileNotFoundError as exc:
                    if scan_status is not None:
                        scan_status.fail(exc)
                except (OSError, ValueError, RuntimeError) as exc:
                    if scan_status is not None:
                        scan_status.fail(exc)
                    return ([], [])
                else:
                    if stat.S_ISDIR(target_info.st_mode):
                        walk_root = target

    public_functions: set[str] = set()
    public_classes: set[str] = set()
    if scan_status is not None:
        selected_root = walk_root.relative_to(scan_root)
        if (
            scan_status.python_walk_root is not None
            and scan_status.python_walk_root != selected_root
        ):
            scan_status.fail()
        scan_status.python_walk_root = selected_root

    for root, dirs, files in _walk_source(walk_root, scan_root, scan_root_fd, scan_status):
        # Prune _SKIP_DIRS in-place so os.walk does not descend into them.
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

        for filename in files:
            if not filename.endswith(".py"):
                continue

            py_file = Path(root) / filename

            # Symlink traversal guard:
            # Use Path.parents for path-component-safe containment check
            # instead of str.startswith (which is a substring check that
            # matches prefix collisions like "/tmp/repo_evil" vs "/tmp/repo").
            try:
                candidate_info = py_file.stat()
                real_path = Path(os.path.realpath(str(py_file))).resolve()
            except (OSError, ValueError, RuntimeError):
                if scan_status is not None:
                    scan_status.fail()
                continue
            if real_path != scan_root and scan_root not in real_path.parents:
                if scan_status is not None:
                    scan_status.fail()
                continue
            if scan_status is not None:
                scan_status.record_candidate(py_file, real_path, scan_root)

            # Only an admitted file may be skipped as intentionally oversized.
            try:
                size = _source_file_size(
                    real_path, scan_root, scan_root_fd, observed=candidate_info,
                )
                if scan_status is not None and scan_root_fd is not None:
                    scan_status.record_admitted_file(
                        real_path, scan_root, scan_root_fd, candidate_info,
                    )
                if size > 100_000:
                    if scan_status is not None:
                        scan_status.record_skip(py_file, scan_root, size)
                    continue
            except OSError:
                if scan_status is not None:
                    scan_status.fail()
                continue

            # Parse file content.
            try:
                content = _read_regular_text(
                    real_path, max_bytes=100_000, scan_root=scan_root,
                    scan_root_fd=scan_root_fd,
                    scan_status=scan_status,
                )
                if content is None:
                    continue
                tree = ast.parse(content)
            except SyntaxError:
                continue

            # ast.walk would pick up nested functions -- we want module-level only.
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not node.name.startswith("_"):
                        public_functions.add(node.name)
                elif isinstance(node, ast.ClassDef):
                    if not node.name.startswith("_"):
                        public_classes.add(node.name)

    sorted_functions = sorted(public_functions)[:50]
    sorted_classes = sorted(public_classes)[:50]
    return (sorted_functions, sorted_classes)


def get_same_repo_digest(cwd: Path) -> str:
    """Build same-repo naming conventions digest.

    Extracts public Python function and class names from the current repo
    to give the reviewer naming convention context.

    Args:
        cwd: working directory of the repo under review.

    Returns:
        Formatted digest string, or "" if no Python sources found.
    """
    funcs, classes = _extract_python_public_names(cwd)
    if not funcs and not classes:
        return ""
    parts = []
    if funcs:
        parts.append("- public functions: " + ", ".join(funcs))
    if classes:
        parts.append("- public classes: " + ", ".join(classes))
    return "## Same-repo naming conventions\n" + "\n".join(parts) + "\n"


def get_digest(cwd: Path, backend: Optional[object] = None) -> str:
    """Build conventions digest for criteria payload.

    Args:
        cwd: working directory of the repo under review.
        backend: reserved for AI-summarization pass (unused).

    Returns:
        Conventions digest string, or "" if nothing extractable.
    """
    parts = []
    same = get_same_repo_digest(cwd)
    if same:
        parts.append(same)
    from .conventions_resolver import get_cross_repo_digest

    cross_repo = get_cross_repo_digest(cwd)
    if cross_repo:
        parts.append(cross_repo)
    return "\n\n".join(parts)
