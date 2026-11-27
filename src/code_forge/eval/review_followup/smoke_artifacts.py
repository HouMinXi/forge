"""Opt-in local storage for bounded smoke payloads, never HTTP headers."""

import os
import stat
from contextlib import contextmanager
from pathlib import Path

from .fixtures import digest, require

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


@contextmanager
def private_root(path: Path):
    """Require a caller-owned, private POSIX directory with trusted ancestors."""
    fd = os.open(path, _DIR_FLAGS)
    try:
        info = os.fstat(fd)
        require(
            info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700,
            "smoke_artifacts.private_root",
        )
        yield fd
    finally:
        os.close(fd)


@contextmanager
def run_artifacts(root: int, name: str):
    # Never use an existing directory, including one left by an interrupted run.
    os.mkdir(name, 0o700, dir_fd=root)
    os.fsync(root)
    fd = os.open(name, _DIR_FLAGS, dir_fd=root)
    try:
        yield fd
    finally:
        os.close(fd)


def store_payload(directory: int, filename: str, data: bytes) -> dict:
    fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.fsync(directory)
    return {"file": filename, "bytes": len(data), "sha256": digest(data)}
