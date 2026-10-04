# SPDX-License-Identifier: Apache-2.0
"""Bind one legacy mutation invocation to a fresh mirror and preserved cache."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import stat
import sys
import uuid
from pathlib import Path

from ._mutation_process import _identity


class MutationWorkspaceError(RuntimeError):
    pass


def _node_identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _snapshot(fd: int) -> dict:
    """Hash regular bytes and describe modes/types; never follow cache links."""
    directory_before = os.fstat(fd)
    entries = {".": {"type": "directory", "mode": stat.S_IMODE(directory_before.st_mode)}}
    with os.scandir(fd) as scan:
        children = sorted(scan, key=lambda item: item.name)
    for entry in children:
        info = entry.stat(follow_symlinks=False)
        row = {"mode": stat.S_IMODE(info.st_mode)}
        if stat.S_ISDIR(info.st_mode):
            child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                if _node_identity(os.fstat(child)) != _node_identity(info):
                    raise MutationWorkspaceError("cache directory identity changed while snapshotting")
                nested = _snapshot(child)
            finally:
                os.close(child)
            for name, value in nested.items():
                entries[entry.name if name == "." else entry.name + "/" + name] = value
        elif stat.S_ISREG(info.st_mode):
            child = os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                before = os.fstat(child)
                if not stat.S_ISREG(before.st_mode) or _node_identity(before) != _node_identity(info):
                    raise MutationWorkspaceError("cache file identity changed while snapshotting")
                digest = hashlib.sha256()
                while chunk := os.read(child, 1024 * 1024):
                    digest.update(chunk)
                after = os.fstat(child)
                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ):
                    raise MutationWorkspaceError("cache bytes changed while snapshotting")
                row.update(type="file", size=after.st_size, sha256=digest.hexdigest())
            finally:
                os.close(child)
            entries[entry.name] = row
        elif stat.S_ISLNK(info.st_mode):
            row.update(type="link", target=os.readlink(entry.name, dir_fd=fd))
            entries[entry.name] = row
        else:
            raise MutationWorkspaceError(f"unsafe cache node type: {entry.name}")
        current = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
        if _node_identity(current) != _node_identity(info) or current.st_mode != info.st_mode:
            raise MutationWorkspaceError("cache node identity changed while snapshotting")
    directory_after = os.fstat(fd)
    if (directory_before.st_mtime_ns, directory_before.st_ctime_ns) != (
        directory_after.st_mtime_ns,
        directory_after.st_ctime_ns,
    ):
        raise MutationWorkspaceError("cache directory changed while snapshotting")
    return entries


class MutationWorkspace:
    """A kernel lease plus fail-closed journal, without stale-owner takeover."""

    def __init__(self, root: str):
        self.root = Path(root)
        self.token = uuid.uuid4().hex
        self.quarantine = ".mutants-forge-" + self.token
        self.disposal = ".mutants-forge-clean-" + self.token
        self.root_fd = self.folder_fd = self.lock_fd = None
        self.config_fd = None
        self.config_backup = "mutation-config-" + self.token
        self.config_identity = None
        self.config_started = self.config_restored = self.config_removed = False
        self.configs: dict = {}
        self.config_data: dict[str, bytes] = {}
        self.config_files: dict = {}
        self.containers = {"mirror": None, "config": None}
        self.acquired = False
        self.prepared = False
        self.quarantined = False
        self.restored = False
        self.cleanup_complete = True
        self.original_identity = self.fresh_identity = None
        self.original_snapshot = None
        self.journal: dict = {}

    def _stat(self, name: str):
        try:
            return os.stat(name, dir_fd=self.root_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None

    def _cache_snapshot(self, name: str):
        info = self._stat(name)
        if info is None:
            return None
        if not stat.S_ISDIR(info.st_mode):
            raise MutationWorkspaceError("mutation cache must be a regular directory")
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.root_fd)
        try:
            if _node_identity(os.fstat(fd)) != _node_identity(info):
                raise MutationWorkspaceError("mutation cache directory identity changed")
            return _snapshot(fd)
        finally:
            os.close(fd)

    def _matches(self, name: str, expected) -> bool:
        info = self._stat(name)
        return info is not None and _node_identity(info) == expected

    def _verify_lease(self):
        if _node_identity(os.stat(self.root, follow_symlinks=False)) != _node_identity(
            os.fstat(self.root_fd)
        ):
            raise MutationWorkspaceError("mutation execution root identity changed")
        folder = os.stat(".code-forge", dir_fd=self.root_fd, follow_symlinks=False)
        lock = os.stat("mutation-owner.lock", dir_fd=self.folder_fd, follow_symlinks=False)
        if (
            _node_identity(folder) != _node_identity(os.fstat(self.folder_fd))
            or _node_identity(lock) != _node_identity(os.fstat(self.lock_fd))
            or not stat.S_ISREG(lock.st_mode)
            or lock.st_nlink != 1
        ):
            raise MutationWorkspaceError("mutation lock namespace identity changed")

    def _write_journal(self):
        self._verify_lease()
        content = json.dumps(self.journal, sort_keys=True).encode()
        os.lseek(self.lock_fd, 0, os.SEEK_SET)
        os.ftruncate(self.lock_fd, 0)
        with os.fdopen(os.dup(self.lock_fd), "wb", closefd=True) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

    def acquire(self):
        try:
            if not sys.platform.startswith("linux") or not hasattr(ctypes.CDLL(None), "renameat2"):
                raise MutationWorkspaceError("mutation workspace requires Linux no-replace renames")
            import fcntl

            self.root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.mkdir(".code-forge", 0o700, dir_fd=self.root_fd)
            except FileExistsError:
                pass
            self.folder_fd = os.open(
                ".code-forge", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.root_fd
            )
            flags = os.O_RDWR | os.O_NOFOLLOW
            created = False
            try:
                self.lock_fd = os.open(
                    "mutation-owner.lock", flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=self.folder_fd
                )
                created = True
            except FileExistsError:
                self.lock_fd = os.open("mutation-owner.lock", flags, dir_fd=self.folder_fd)
            info = os.fstat(self.lock_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
                raise MutationWorkspaceError("mutation lease requires an owned regular single-link file")
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise MutationWorkspaceError("mutation workspace is busy: " + str(self.root)) from exc
            self._verify_lease()
            previous = {}
            if not created:
                raw = os.read(self.lock_fd, 1024 * 1024)
                try:
                    previous = json.loads(raw)
                except (ValueError, TypeError) as exc:
                    raise MutationWorkspaceError(
                        "mutation lease journal is invalid; manual recovery required"
                    ) from exc
                if not isinstance(previous, dict) or previous.get("phase") != "complete":
                    raise MutationWorkspaceError(
                        "unfinished mutation workspace; no stale takeover: "
                        + str(self.root / ".code-forge/mutation-owner.lock")
                    )
            else:
                # A refused read-only preflight has not acquired mutation
                # state. Do not leave a newly created empty invalid journal.
                self.journal = {"phase": "complete", "token": self.token}
                self._write_journal()
            stored = previous.get("empty_containers", {})
            if not isinstance(stored, dict):
                raise MutationWorkspaceError("invalid owned container journal")
            for kind in self.containers:
                expected = stored.get(kind)
                if expected is not None:
                    if (
                        not isinstance(expected, list)
                        or len(expected) != 2
                        or any(type(n) is not int for n in expected)
                    ):
                        raise MutationWorkspaceError("invalid owned container identity")
                    self.containers[kind] = tuple(expected)
                    fd = self._open_container(kind, self.containers[kind], empty=True)
                    os.close(fd)
                elif self._folder_stat(self._container_name(kind)) is not None:
                    raise MutationWorkspaceError(
                        "foreign mutation container has no completed ownership record"
                    )
            self.original_snapshot = self._cache_snapshot("mutants")
            cache = self._stat("mutants")
            self.original_identity = None if cache is None else _node_identity(cache)
            for name in ("setup.cfg", "pyproject.toml"):
                info = self._stat(name)
                if info is None:
                    original = {"exists": False, "type": "missing"}
                else:
                    original, data = self._read_config(self.root_fd, name)
                    original["exists"] = True
                    self.config_data[name] = data
                self.configs[name] = {"original": original, "held": False, "live": False}
            owner = _identity(os.getpid())
            if owner is None:
                raise MutationWorkspaceError("cannot identify mutation workspace owner")
            self.journal = {
                "phase": "active",
                "token": self.token,
                "pid": owner.pid,
                "start_ticks": owner.start_ticks,
                "root": str(self.root),
                "active_mirror": str(self.root / "mutants"),
                "quarantine": str(self.root / self.quarantine),
                "disposal": str(self.root / self.disposal),
                "cache_identity": self.original_identity,
                "cache_snapshot_sha256": hashlib.sha256(
                    json.dumps(self.original_snapshot, sort_keys=True).encode()
                ).hexdigest(),
                "configs": self.configs,
                "empty_containers": self.containers,
                "config_backup": str(self.root / ".code-forge" / self.config_backup),
            }
            self.acquired = True
            self._write_journal()
            self._begin_config_backup()
        except (OSError, MutationWorkspaceError) as exc:
            message = str(exc)
            if self.acquired:
                self.cleanup_complete = False
                self.journal["phase"] = "incomplete"
                try:
                    self._write_journal()
                except (OSError, MutationWorkspaceError) as journal_error:
                    message += "; journal update failed: " + str(journal_error)
                message += "; recovery journal=" + str(self.root / ".code-forge/mutation-owner.lock")
                message += "; config_backup=" + str(self.root / ".code-forge" / self.config_backup)
            self._close_fds()
            self.acquired = False
            raise MutationWorkspaceError(message) from exc

    def _rename(self, source: str, target: str):
        self._rename_between(self.root_fd, source, self.root_fd, target)

    def _rename_between(self, source_fd: int, source: str, target_fd: int, target: str):
        self._verify_lease()
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.renameat2(source_fd, os.fsencode(source), target_fd, os.fsencode(target), 1) != 0:
            code = ctypes.get_errno()
            raise MutationWorkspaceError(
                f"mutation no-replace rename refused {source} -> {target}: {os.strerror(code)}"
            )

    @staticmethod
    def _container_name(kind: str):
        return "mutation-empty-" + kind

    def _folder_stat(self, name: str):
        try:
            return os.stat(name, dir_fd=self.folder_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None

    def _open_container(self, kind: str, expected, *, empty: bool = False):
        self._verify_lease()
        name = self._container_name(kind)
        info = self._folder_stat(name)
        if info is None or not stat.S_ISDIR(info.st_mode) or _node_identity(info) != expected:
            raise MutationWorkspaceError("foreign mutation container: " + name)
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.folder_fd)
        try:
            self._verify_container(kind, fd, expected)
            if empty:
                with os.scandir(fd) as scan:
                    if next(scan, None) is not None:
                        raise MutationWorkspaceError("owned mutation container is not empty: " + name)
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _verify_container(self, kind: str, fd: int, expected):
        self._verify_lease()
        info = self._folder_stat(self._container_name(kind))
        bound = os.fstat(fd)
        if (
            info is None
            or not stat.S_ISDIR(info.st_mode)
            or _node_identity(info) != expected
            or _node_identity(bound) != expected
            or stat.S_IMODE(info.st_mode) != 0o700
            or stat.S_IMODE(bound.st_mode) != 0o700
        ):
            raise MutationWorkspaceError("mutation container identity/mode changed: " + kind)

    def _empty_container(self, kind: str, expected):
        """Delete contents through the owned inode; never remove its final name."""
        fd = self._open_container(kind, expected)
        try:
            with os.scandir(fd) as scan:
                children = list(scan)
            for entry in children:
                self._verify_container(kind, fd, expected)
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    # stdlib's fd-safe traversal stays inside the pinned
                    # owned container. A replacement of the container name
                    # cannot redirect this parent descriptor.
                    if not shutil.rmtree.avoids_symlink_attacks:
                        raise MutationWorkspaceError("fd-safe mutation content cleanup unavailable")
                    shutil.rmtree(entry.name, dir_fd=fd)
                else:
                    os.unlink(entry.name, dir_fd=fd)
                self._verify_container(kind, fd, expected)
            os.lseek(fd, 0, os.SEEK_SET)
            with os.scandir(fd) as scan:
                if next(scan, None) is not None:
                    raise MutationWorkspaceError("mutation container changed during contents cleanup")
            self._verify_container(kind, fd, expected)
        finally:
            os.close(fd)

    def _read_config(self, directory_fd: int, name: str, expected: dict | None = None):
        """Read a bound regular single-link node, with no content in diagnostics."""
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise MutationWorkspaceError("configuration requires a regular single-link file: " + name)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or _node_identity(before) != _node_identity(info)
            ):
                raise MutationWorkspaceError("configuration identity changed while opening: " + name)
            with os.fdopen(os.dup(fd), "rb") as stream:
                data = stream.read()
            after = os.fstat(fd)
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if (
                (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_mode, before.st_nlink)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_mode, after.st_nlink)
                or _node_identity(current) != _node_identity(after)
                or current.st_mode != after.st_mode
                or current.st_nlink != 1
            ):
                raise MutationWorkspaceError("configuration changed while reading: " + name)
            metadata = {
                "type": "file",
                "identity": _node_identity(after),
                "mode": stat.S_IMODE(after.st_mode),
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            if expected is not None and any(metadata[key] != expected[key] for key in metadata):
                raise MutationWorkspaceError("foreign or changed configuration: " + name)
            return metadata, data
        finally:
            os.close(fd)

    def _verify_config_directory(self):
        self._verify_lease()
        info = os.stat(self.config_backup, dir_fd=self.folder_fd, follow_symlinks=False)
        if (
            self.config_fd is None
            or not stat.S_ISDIR(info.st_mode)
            or _node_identity(info) != self.config_identity
            or _node_identity(os.fstat(self.config_fd)) != self.config_identity
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise MutationWorkspaceError("foreign configuration recovery directory")

    def _write_private_config(self, name: str, data: bytes):
        self._verify_config_directory()
        fd = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.config_fd
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(os.dup(fd), "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            identity = _node_identity(os.fstat(fd))
        finally:
            os.close(fd)
        metadata, _ = self._read_config(self.config_fd, name)
        if metadata["identity"] != identity or metadata["sha256"] != hashlib.sha256(data).hexdigest():
            raise MutationWorkspaceError("configuration backup identity or bytes disagrees: " + name)
        self.config_files[name] = metadata
        self.journal["config_files"] = self.config_files
        self._write_journal()

    def _verify_original_config(self, name: str, state: dict):
        original = state["original"]
        if original["exists"]:
            self._read_config(self.root_fd, name, original)
        elif self._stat(name) is not None:
            raise MutationWorkspaceError("foreign configuration appeared: " + name)

    def _begin_config_backup(self):
        """Durable recovery exists before the first baseline command."""
        self._verify_lease()
        self.config_started = True
        if self.containers["config"] is None:
            os.mkdir(self.config_backup, 0o700, dir_fd=self.folder_fd)
            self.config_identity = _node_identity(
                os.stat(self.config_backup, dir_fd=self.folder_fd, follow_symlinks=False)
            )
        else:
            self.config_identity = self.containers["config"]
            self._rename_between(
                self.folder_fd, self._container_name("config"), self.folder_fd, self.config_backup
            )
            if _node_identity(self._folder_stat(self.config_backup)) != self.config_identity:
                raise MutationWorkspaceError("foreign moved configuration container")
            self.containers["config"] = None
        self.config_fd = os.open(
            self.config_backup, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.folder_fd
        )
        if _node_identity(os.fstat(self.config_fd)) != self.config_identity:
            raise MutationWorkspaceError("configuration recovery directory changed while opening")
        os.fchmod(self.config_fd, 0o700)
        self.journal["config_backup_identity"] = self.config_identity
        self._verify_config_directory()
        self._write_journal()
        # Retain independent byte clones until both configuration and cache
        # restoration are confirmed, including partial-install failures.
        for name, data in self.config_data.items():
            self._write_private_config(name + ".original-bytes", data)

    def install_configs(self, setup: bytes, transform_pyproject):
        """Preserve original nodes and bytes before publishing scoped files."""
        self._verify_lease()
        generated = {"setup.cfg": setup}
        pyproject = self.config_data.get("pyproject.toml")
        if pyproject is not None:
            replacement = transform_pyproject(pyproject)
            if replacement is not None:
                generated["pyproject.toml"] = replacement
        for name, state in self.configs.items():
            self._verify_original_config(name, state)
        for name, data in generated.items():
            state = self.configs[name]
            self._verify_original_config(name, state)
            if state["original"]["exists"]:
                held = name + ".original-node"
                self._rename_between(self.root_fd, name, self.config_fd, held)
                state["held"] = True
                self.config_files[held] = {
                    key: value for key, value in state["original"].items() if key != "exists"
                }
                original, _ = self._read_config(self.config_fd, held, state["original"])
                fd = os.open(held, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.config_fd)
                try:
                    opened = os.fstat(fd)
                    if (
                        not stat.S_ISREG(opened.st_mode)
                        or opened.st_nlink != 1
                        or _node_identity(opened) != original["identity"]
                    ):
                        raise MutationWorkspaceError(
                            "original configuration node changed before privacy"
                        )
                    os.fchmod(fd, 0o600)
                finally:
                    os.close(fd)
                original["mode"] = 0o600
                self.config_files[held] = original
            temporary = name + ".generated-node"
            self._write_private_config(temporary, data)
            state["generated"] = self.config_files[temporary]
            self._rename_between(self.config_fd, temporary, self.root_fd, name)
            state["live"] = True
            del self.config_files[temporary]
            self._read_config(self.root_fd, name, state["generated"])
            self._write_journal()

    def restore_configs(self):
        """Refuse foreign namespaces before changing any configuration path."""
        if not self.cleanup_complete:
            raise MutationWorkspaceError("owned mutation teardown is incomplete; configuration retained")
        if not self.config_started:
            return
        self._verify_config_files()
        for name, state in self.configs.items():
            if state["original"]["exists"]:
                if name + ".original-bytes" not in self.config_files:
                    raise MutationWorkspaceError("configuration byte backup incomplete: " + name)
                self._read_config(
                    self.config_fd, name + ".original-bytes", self.config_files[name + ".original-bytes"]
                )
        for name, state in self.configs.items():
            if state["live"]:
                self._read_config(self.root_fd, name, state["generated"])
            elif state["held"]:
                if self._stat(name) is not None:
                    raise MutationWorkspaceError("foreign configuration blocks restoration: " + name)
            else:
                self._verify_original_config(name, state)
        for name, state in self.configs.items():
            if state["live"]:
                private = name + ".generated-node"
                self._rename_between(self.root_fd, name, self.config_fd, private)
                state["live"] = False
                self._read_config(self.config_fd, private, state["generated"])
                self.config_files[private] = state["generated"]
            if state["held"]:
                held = name + ".original-node"
                fd = os.open(held, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.config_fd)
                try:
                    opened = os.fstat(fd)
                    if (
                        not stat.S_ISREG(opened.st_mode)
                        or opened.st_nlink != 1
                        or _node_identity(opened) != state["original"]["identity"]
                    ):
                        raise MutationWorkspaceError(
                            "original configuration node changed before restore"
                        )
                    self._rename_between(self.config_fd, held, self.root_fd, name)
                    state["held"] = False
                    del self.config_files[held]
                    os.fchmod(fd, state["original"]["mode"])
                finally:
                    os.close(fd)
            self._verify_original_config(name, state)
            self._write_journal()
        self.config_restored = True

    def remove_config_backup(self):
        if not self.cleanup_complete or not self.config_restored or not self.restored:
            raise MutationWorkspaceError("configuration backup retained until complete restoration")
        self._verify_config_files()
        self._rename_between(
            self.folder_fd, self.config_backup, self.folder_fd, self._container_name("config")
        )
        self._empty_container("config", self.config_identity)
        self.containers["config"] = self.config_identity
        self.config_files.clear()
        self.config_removed = True

    def _verify_config_files(self):
        self._verify_config_directory()
        os.lseek(self.config_fd, 0, os.SEEK_SET)
        with os.scandir(self.config_fd) as scan:
            names = {entry.name for entry in scan}
        if names != set(self.config_files):
            raise MutationWorkspaceError("foreign file in configuration recovery directory")
        for name, expected in self.config_files.items():
            self._read_config(self.config_fd, name, expected)

    def prepare(self):
        self._verify_lease()
        current = self._stat("mutants")
        if (None if current is None else _node_identity(current)) != self.original_identity:
            raise MutationWorkspaceError("mutation cache identity changed before quarantine")
        if self._cache_snapshot("mutants") != self.original_snapshot:
            raise MutationWorkspaceError("mutation cache bytes/modes/types changed before quarantine")
        self.prepared = True
        if current is not None:
            self._rename("mutants", self.quarantine)
            self.quarantined = True
            if not self._matches(self.quarantine, self.original_identity):
                self.cleanup_complete = False
                raise MutationWorkspaceError("mutation quarantine identity changed")
        if self.containers["mirror"] is not None:
            expected = self.containers["mirror"]
            fd = self._open_container("mirror", expected, empty=True)
            os.close(fd)
            self._rename_between(self.folder_fd, self._container_name("mirror"), self.root_fd, "mutants")
            if not self._matches("mutants", expected):
                self.cleanup_complete = False
                raise MutationWorkspaceError("foreign moved mirror container")
            self.fresh_identity = expected
            self.containers["mirror"] = None
        else:
            try:
                os.mkdir("mutants", 0o700, dir_fd=self.root_fd)
            except OSError as exc:
                self.cleanup_complete = False
                raise MutationWorkspaceError("cannot establish owned fresh mutation mirror") from exc
            self.fresh_identity = _node_identity(self._stat("mutants"))
        self.journal["fresh_identity"] = self.fresh_identity
        self._write_journal()

    def restore(self):
        if not self.cleanup_complete:
            raise MutationWorkspaceError("owned mutation teardown is incomplete")
        self._verify_lease()
        if self.quarantined:
            if (
                not self._matches(self.quarantine, self.original_identity)
                or self._cache_snapshot(self.quarantine) != self.original_snapshot
            ):
                self.cleanup_complete = False
                raise MutationWorkspaceError("quarantined mutation cache changed; retained for recovery")
        if self.fresh_identity is not None:
            fresh = self._stat("mutants")
            if fresh is None or _node_identity(fresh) != self.fresh_identity:
                self.cleanup_complete = False
                raise MutationWorkspaceError(
                    "foreign mutation mirror replacement; refusing removal/restore"
                )
            self._rename("mutants", self.disposal)
            if not self._matches(self.disposal, self.fresh_identity):
                self.cleanup_complete = False
                raise MutationWorkspaceError("foreign mutation disposal identity; refusing removal")
            self._rename_between(
                self.root_fd, self.disposal, self.folder_fd, self._container_name("mirror")
            )
            self._empty_container("mirror", self.fresh_identity)
            self.containers["mirror"] = self.fresh_identity
        if self.quarantined:
            self._rename(self.quarantine, "mutants")
            if (
                not self._matches("mutants", self.original_identity)
                or self._cache_snapshot("mutants") != self.original_snapshot
            ):
                self.cleanup_complete = False
                raise MutationWorkspaceError("restored mutation cache identity or snapshot disagrees")
        self.restored = True

    def finish(self):
        """One recovery sequence for every public baseline/native return."""
        if not self.cleanup_complete:
            raise MutationWorkspaceError("owned mutation teardown is incomplete")
        if not self.acquired:
            return
        if not self.config_removed:
            self.restore_configs()
        if self.prepared and not self.restored:
            self.restore()
        elif not self.prepared:
            cache = self._stat("mutants")
            if (
                None if cache is None else _node_identity(cache)
            ) != self.original_identity or self._cache_snapshot("mutants") != self.original_snapshot:
                raise MutationWorkspaceError("unprepared mutation cache changed; refusing recovery")
            self.restored = True  # The original namespace/snapshot is still intact.
        if self.config_started and not self.config_removed:
            self.remove_config_backup()

    def _retain_config_backup(self):
        """Replenish only missing byte clones after partial owned cleanup."""
        if self.config_fd is None or self.config_removed:
            return
        self._verify_lease()
        info = os.fstat(self.config_fd)
        location = os.readlink(f"/proc/self/fd/{self.config_fd}")
        if _node_identity(info) != self.config_identity or stat.S_IMODE(info.st_mode) != 0o700:
            raise MutationWorkspaceError("cannot retain bytes in an unbound config recovery directory")
        try:
            Path(location).relative_to(self.root / ".code-forge")
        except ValueError as exc:
            raise MutationWorkspaceError(
                "config recovery directory moved outside the approved checkout"
            ) from exc
        if location.endswith(" (deleted)"):
            raise MutationWorkspaceError("config recovery directory has been unlinked")
        for name, data in self.config_data.items():
            clone = name + ".original-bytes"
            try:
                metadata, _ = self._read_config(self.config_fd, clone, self.config_files[clone])
            except FileNotFoundError:
                fd = os.open(
                    clone,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=self.config_fd,
                )
                try:
                    os.fchmod(fd, 0o600)
                    with os.fdopen(os.dup(fd), "wb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    identity = _node_identity(os.fstat(fd))
                finally:
                    os.close(fd)
                metadata, _ = self._read_config(self.config_fd, clone)
                if (
                    metadata["identity"] != identity
                    or metadata["sha256"] != hashlib.sha256(data).hexdigest()
                ):
                    raise MutationWorkspaceError(
                        "retained configuration byte clone disagrees: " + clone
                    ) from None
                self.config_files[clone] = metadata
        self.journal["retained_config_identity"] = self.config_identity
        self.journal["retained_config_path"] = location

    def release(self):
        failure = None
        recovery_error = None
        try:
            if self.acquired:
                if self.cleanup_complete:
                    try:
                        self.finish()
                    except (MutationWorkspaceError, OSError) as exc:
                        self.cleanup_complete = False
                        recovery_error = str(exc)
                        try:
                            self._retain_config_backup()
                        except (MutationWorkspaceError, OSError, KeyError) as retention_error:
                            recovery_error += "; byte backup retention failed: " + str(retention_error)
                if self.prepared and not self.restored:
                    self.cleanup_complete = False
                if self.config_started and not self.config_removed:
                    self.cleanup_complete = False
                self.journal["phase"] = "complete" if self.cleanup_complete else "incomplete"
                self._write_journal()
                if not self.cleanup_complete:
                    failure = MutationWorkspaceError(
                        ("" if recovery_error is None else recovery_error + "; ")
                        + "mutation workspace retained for recovery; active="
                        + str(self.root / "mutants")
                        + "; quarantine="
                        + str(self.root / self.quarantine)
                        + "; disposal="
                        + str(self.root / self.disposal)
                        + "; journal="
                        + str(self.root / ".code-forge/mutation-owner.lock")
                        + "; config_backup="
                        + str(self.root / ".code-forge" / self.config_backup)
                        + "; owned_containers="
                        + str(self.root / ".code-forge/mutation-empty-mirror")
                        + ","
                        + str(self.root / ".code-forge/mutation-empty-config")
                    )
        finally:
            self._close_fds()
            self.acquired = False
        if failure is not None:
            raise failure

    def _close_fds(self):
        for name in ("config_fd", "lock_fd", "folder_fd", "root_fd"):
            fd = getattr(self, name)
            if fd is not None:
                os.close(fd)
                setattr(self, name, None)
