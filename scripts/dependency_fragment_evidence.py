"""Hash supplied candidate fragments only; this does not establish systemd semantics.

This helper performs no discovery or collection. The deadline is an absolute
``time.monotonic()`` value shared with the caller. Deadline checks are
cooperative: a blocked filesystem syscall cannot be interrupted here.
"""

import hashlib
import math
import os
import stat
import time

MAX_FILES = 64
MAX_ROOTS = 64
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_PATH_BYTES = 4096
CHUNK_SIZE = 64 * 1024
NOT_ESTABLISHED = "NOT_ESTABLISHED"


class _Unknown(Exception):
    pass


def _result(status, hashes=None):
    return {
        "status": status,
        "fragment_sha256": hashes if hashes is not None else [],
        "drop_ins": NOT_ESTABLISHED,
        "precedence": NOT_ESTABLISHED,
        "manager_disk_equivalence": NOT_ESTABLISHED,
    }


def _parts(path):
    if type(path) is not str or len(path) > MAX_PATH_BYTES or not path.startswith("/") or "\x00" in path:
        raise _Unknown
    try:
        encoded = os.fsencode(path)
    except (UnicodeError, ValueError):
        raise _Unknown from None
    if len(encoded) > MAX_PATH_BYTES:
        raise _Unknown
    if path == "/":
        return ()
    parts = path[1:].split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise _Unknown
    return tuple(parts)


def _check_deadline(deadline):
    if time.monotonic() >= deadline:
        raise _Unknown


def _identity(st):
    return (
        st.st_dev,
        st.st_ino,
        st.st_mode,
        st.st_uid,
        st.st_gid,
        st.st_nlink,
        st.st_size,
        st.st_mtime_ns,
        st.st_ctime_ns,
    )


def _hash_path(parts, deadline):
    """Open every component from / without following links, then hash once."""
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    file_flags = os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptors = []
    try:
        _check_deadline(deadline)
        fd = os.open("/", directory_flags)
        descriptors.append(fd)
        directories = [_identity(os.fstat(fd))]
        for component in parts[:-1]:
            _check_deadline(deadline)
            fd = os.open(component, directory_flags, dir_fd=fd)
            descriptors.append(fd)
            directories.append(_identity(os.fstat(fd)))
        _check_deadline(deadline)
        fd = os.open(parts[-1], file_flags, dir_fd=fd)
        descriptors.append(fd)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
            raise _Unknown
        # An O_PATH descriptor does not open a device or FIFO for I/O. Reopen
        # only the verified regular inode through Linux procfs, then verify it
        # again before reading. Missing or unsupported procfs fails closed.
        _check_deadline(deadline)
        readable_fd = os.open(f"/proc/self/fd/{fd}", os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            if _identity(os.fstat(readable_fd)) != _identity(before):
                raise _Unknown
        except (OSError, _Unknown):
            os.close(readable_fd)
            raise
        descriptors.append(readable_fd)
        fd = readable_fd
        digest = hashlib.sha256()
        count = 0
        while True:
            _check_deadline(deadline)
            chunk = os.read(fd, min(CHUNK_SIZE, MAX_FILE_BYTES + 1 - count))
            if not chunk:
                break
            count += len(chunk)
            if count > MAX_FILE_BYTES:
                raise _Unknown
            digest.update(chunk)
        _check_deadline(deadline)
        after = os.fstat(fd)
        if count != before.st_size or _identity(before) != _identity(after):
            raise _Unknown
        if [_identity(os.fstat(item)) for item in descriptors[:-2]] != directories:
            raise _Unknown
        return (digest.hexdigest(), count, _identity(after), tuple(directories))
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def _stat_path(parts, deadline):
    """Reopen and restat an entry and its ancestors without reading bytes."""
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    file_flags = os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptors = []
    try:
        _check_deadline(deadline)
        fd = os.open("/", directory_flags)
        descriptors.append(fd)
        for component in parts[:-1]:
            _check_deadline(deadline)
            fd = os.open(component, directory_flags, dir_fd=fd)
            descriptors.append(fd)
        directories = tuple(_identity(os.fstat(item)) for item in descriptors)
        _check_deadline(deadline)
        fd = os.open(parts[-1], file_flags, dir_fd=fd)
        descriptors.append(fd)
        current = os.fstat(fd)
        if not stat.S_ISREG(current.st_mode):
            raise _Unknown
        if tuple(_identity(os.fstat(item)) for item in descriptors[:-1]) != directories:
            raise _Unknown
        _check_deadline(deadline)
        return _identity(current), directories
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def collect_fragment_hashes(candidate_paths, unit_path_roots, *, deadline):
    """Return ordered hashes or fixed UNKNOWN, never fragment bytes or paths.

    Only canonical absolute lexical paths under a supplied root are accepted.
    This is a conservative observation, not proof of effective unit contents.
    Concurrent changes after the final check cannot be ruled out.
    """
    try:
        if (
            not isinstance(candidate_paths, (list, tuple))
            or not isinstance(unit_path_roots, (list, tuple))
            or not 0 < len(candidate_paths) <= MAX_FILES
            or not 0 < len(unit_path_roots) <= MAX_ROOTS
            or isinstance(deadline, bool)
            or not isinstance(deadline, (int, float))
            or not math.isfinite(deadline)
        ):
            raise _Unknown
        if not all(
            hasattr(os, flag)
            for flag in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK", "O_CLOEXEC", "O_PATH")
        ):
            raise _Unknown
        if os.open not in os.supports_dir_fd:
            raise _Unknown
        _check_deadline(deadline)
        roots = [_parts(root) for root in unit_path_roots]
        paths = [_parts(path) for path in candidate_paths]
        if len(set(paths)) != len(paths):
            raise _Unknown
        if any(
            not parts or not any(len(parts) > len(root) and parts[: len(root)] == root for root in roots)
            for parts in paths
        ):
            raise _Unknown
        observations = []
        total = 0
        for parts in paths:
            observation = _hash_path(parts, deadline)
            total += observation[1]
            if total > MAX_TOTAL_BYTES:
                raise _Unknown
            observations.append(observation)
        # Reopen, rehash and restat all files after the first pass. This also
        # detects replacement of any ancestor or entry between passes.
        for parts, previous in zip(paths, observations, strict=True):
            if _hash_path(parts, deadline) != previous:
                raise _Unknown
        for parts, previous in zip(paths, observations, strict=True):
            if _stat_path(parts, deadline) != previous[2:]:
                raise _Unknown
        _check_deadline(deadline)
        return _result("HASHED", [item[0] for item in observations])
    except (OSError, ValueError, TypeError, OverflowError, _Unknown):
        return _result("UNKNOWN")
