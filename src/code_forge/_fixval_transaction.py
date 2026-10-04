# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Preserve local entries across FIXVAL's working-tree-only Git transaction."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

import unidiff

from .diff import get_removed_files, patched_file_path


class TransactionError(RuntimeError):
    """The working tree cannot be changed or restored with known ownership."""


@dataclass
class _Entry:
    path: str
    parent: int | None
    leaf: str
    original: tuple | None
    saved: str
    retained: bool
    saved_identity: tuple | None = None
    reverted: tuple | None = None
    snapshot: str | None = None
    restored: tuple | None = None


def _identity(parent: int, leaf: str) -> tuple | None:
    try:
        info = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode):
        value = os.readlink(leaf, dir_fd=parent)
    elif stat.S_ISREG(info.st_mode):
        fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(fd, "rb") as source:
            opened = os.fstat(source.fileno())
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise TransactionError("source entry changed while opening")
            value = hashlib.file_digest(source, "sha256").hexdigest()
    else:
        raise TransactionError("source entry is neither a regular file nor a symlink")
    after = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
    before_meta = (info.st_dev, info.st_ino, info.st_mode, info.st_mtime_ns, info.st_size)
    after_meta = (after.st_dev, after.st_ino, after.st_mode, after.st_mtime_ns, after.st_size)
    if before_meta != after_meta:
        raise TransactionError("source entry changed while reading")
    return before_meta + (value,)


@contextmanager
def _directory_descriptor(path: str | Path, parent: int | None = None):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


class _PrivateImage:
    """Hold the producer namespace through Git, copying and verified retirement."""

    def __init__(self, transaction):
        self.transaction = transaction
        self.fd = None
        self.opened = []
        self.files = {}
        self.directories = []
        self.sealed = False
        transaction._check_directory()
        allocated = Path(
            tempfile.mkdtemp(prefix=".fixval-image-", dir="/proc/self/fd/%s" % transaction.saved_fd)
        )
        self.name = allocated.name
        try:
            observed = os.stat(self.name, dir_fd=transaction.saved_fd, follow_symlinks=False)
            self.fd = os.open(
                self.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=transaction.saved_fd,
            )
            bound = os.fstat(self.fd)
            if (observed.st_dev, observed.st_ino) != (bound.st_dev, bound.st_ino):
                raise TransactionError("private image changed during allocation")
            self.check()
        except BaseException:
            self.close()
            raise

    @property
    def cwd(self):
        return "/proc/%s/fd/%s" % (os.getpid(), self.fd)

    def check(self):
        self.transaction._check_directory()
        observed = os.stat(self.name, dir_fd=self.transaction.saved_fd, follow_symlinks=False)
        bound = os.fstat(self.fd)
        if (observed.st_dev, observed.st_ino) != (bound.st_dev, bound.st_ino):
            raise TransactionError("private image directory changed")
        for _, parent, leaf, fd in self.directories:
            observed = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            bound = os.fstat(fd)
            if (observed.st_dev, observed.st_ino) != (bound.st_dev, bound.st_ino):
                raise TransactionError("private image container changed: " + leaf)

    def parent(self, path, stack):
        current = self.fd
        for part in Path(path).parent.parts:
            try:
                os.mkdir(part, 0o755, dir_fd=current)
            except FileExistsError:
                pass
            current = stack.enter_context(_directory_descriptor(part, current))
        return current

    def seal(self):
        self.check()
        if self.sealed:
            return
        allowed = {entry.path for entry in self.transaction.entries} | {".reverse.patch"}
        containers = {str(p) for name in allowed for p in Path(name).parents if str(p) != "."}

        def walk(parent, prefix):
            os.lseek(parent, 0, os.SEEK_SET)
            for leaf in os.listdir(parent):
                name = str(prefix / leaf)
                observed = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
                if stat.S_ISDIR(observed.st_mode) and name in containers:
                    fd = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                    self.opened.append(fd)
                    bound = os.fstat(fd)
                    if (observed.st_dev, observed.st_ino) != (bound.st_dev, bound.st_ino):
                        raise TransactionError("private image container changed: " + name)
                    self.directories.append((name, parent, leaf, fd))
                    walk(fd, prefix / leaf)
                elif name in allowed:
                    self.files[name] = (parent, leaf, _identity(parent, leaf))
                else:
                    raise TransactionError("private image has an unexpected entry: " + name)

        walk(self.fd, Path())
        self.check()
        self.sealed = True

    def _retire_directory(self, parent, leaf, fd):
        transaction = self.transaction
        saved = ".image-directory-%s" % transaction.parent_serial
        transaction.parent_serial += 1
        transaction._rename_between(parent, leaf, transaction.saved_fd, saved)
        expected = os.fstat(fd)
        observed = os.stat(saved, dir_fd=transaction.saved_fd, follow_symlinks=False)
        try:
            if (observed.st_dev, observed.st_ino) != (expected.st_dev, expected.st_ino):
                raise TransactionError("private image directory changed during retirement")
            os.rmdir(saved, dir_fd=transaction.saved_fd)
        except BaseException:
            try:
                transaction._rename_between(transaction.saved_fd, saved, parent, leaf)
            except OSError:
                pass
            raise

    def cleanup(self):
        self.seal()
        self.check()
        transaction = self.transaction
        retired = ".retired-image-%s" % transaction.parent_serial
        transaction.parent_serial += 1
        previous = self.name
        transaction._rename_between(transaction.saved_fd, previous, transaction.saved_fd, retired)
        self.name = retired
        try:
            self.check()
        except BaseException:
            try:
                transaction._rename_between(
                    transaction.saved_fd, retired, transaction.saved_fd, previous
                )
                self.name = previous
            except OSError:
                pass
            raise
        for _, (parent, leaf, expected) in sorted(self.files.items()):
            saved = "image-payload-%s" % transaction.parent_serial
            transaction.parent_serial += 1
            if _identity(parent, leaf) != expected:
                raise TransactionError("private image payload changed before retirement: " + leaf)
            transaction._move(parent, leaf, saved, expected)
        for _, parent, leaf, fd in sorted(
            self.directories, key=lambda row: len(Path(row[0]).parts), reverse=True
        ):
            self._retire_directory(parent, leaf, fd)
        self._retire_directory(transaction.saved_fd, self.name, self.fd)

    def close(self):
        for fd in self.opened:
            os.close(fd)
        self.opened.clear()
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class FixvalTransaction:
    """Restore original content and retained inode without replacing new entries."""

    def __init__(self, root: Path, patch: str, *, recovery_parent: Path | None = None) -> None:
        if not sys.platform.startswith("linux"):
            raise TransactionError("FIXVAL preservation requires Linux no-replace renames")
        self.libc = ctypes.CDLL(None, use_errno=True)
        if not hasattr(self.libc, "renameat2"):
            raise TransactionError("FIXVAL preservation requires renameat2")
        required = {
            os.open,
            os.stat,
            os.readlink,
            os.rename,
            os.symlink,
            os.link,
            os.unlink,
            os.rmdir,
            os.mkdir,
        }
        if (
            not required <= os.supports_dir_fd
            or os.listdir not in os.supports_fd
            or not all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW", "fchmod"))
        ):
            raise TransactionError("platform cannot safely bind FIXVAL directory entries")
        self.patch = patch
        removed = set(get_removed_files(patch))
        paths = set()
        self.source_paths: set[str] = set()
        self.target_paths: set[str] = set()
        for changed in unidiff.PatchSet(patch):
            target = patched_file_path(changed)
            paths.add(target)
            if changed.target_file != "/dev/null":
                self.target_paths.add(target)
            original = patched_file_path(changed, source=True)
            if original:
                paths.add(original)
                self.source_paths.add(original)
        for name in paths:
            path = Path(name)
            if path.is_absolute() or ".." in path.parts or ".git" in path.parts or not path.parts:
                raise TransactionError("patch has an unsafe working-tree path")
        self.source_directories = {
            str(parent)
            for name in self.source_paths
            for parent in Path(name).parents
            if str(parent) != "."
        }
        self.target_directories = {
            str(parent)
            for name in self.target_paths
            for parent in Path(name).parents
            if str(parent) != "."
        }
        self.current_paths = self.target_paths
        self.current_directories = self.target_directories
        self.root = Path(os.path.abspath(root))
        self.root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.parents: dict[str, int] = {".": self.root_fd}
        self.removed_parents: dict[str, int] = {}
        self.original_parent_modes: dict[str, int] = {}
        self.parent_serial = 0
        self.entries: list[_Entry] = []
        self.reverted = False
        self.recovery_needed = False
        self.saved_entries: dict[str, tuple] = {}
        self.image_errors: list[str] = []
        self.saved_fd = None
        self.recovery_parent = self.root.parent
        self.recovery_parent_fd = None
        self.recovery_namespace = hashlib.sha256(os.fsencode(self.root)).hexdigest()[:12]
        self.directory = None
        self.recovery_location = None
        try:
            if recovery_parent is not None:
                self.recovery_parent = Path(os.path.abspath(recovery_parent))
            if self.recovery_parent.resolve().is_relative_to(self.root.resolve()):
                raise TransactionError("FIXVAL needs a recovery parent outside the source root")
            self.recovery_parent_fd = os.open(
                self.recovery_parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            self._check_recovery_parent()
            self._bind_directories()
            for index, name in enumerate(sorted(paths)):
                path = Path(name)
                parent = self._parent(path.parent)
                original = self._entry_identity(name, parent)
                self.entries.append(
                    _Entry(name, parent, path.name, original, str(index), name in removed)
                )
            # Locally retained deletions remain physical postimage entries even
            # though the Git packet removes them from the semantic target image.
            self.target_directories.update(
                str(parent)
                for entry in self.entries
                if entry.retained and entry.original is not None
                for parent in Path(entry.path).parents
                if str(parent) != "."
            )
            self.original_parent_modes = {
                name: stat.S_IMODE(os.fstat(fd).st_mode)
                for name, fd in self.parents.items()
                if name != "."
            }
            self._check_parents()
        except BaseException:
            self._close_descriptors()
            raise

    def _close_descriptors(self) -> None:
        descriptors = set(self.parents.values())
        descriptors.update(fd for fd in (self.saved_fd, self.recovery_parent_fd) if fd is not None)
        for fd in descriptors:
            os.close(fd)

    def _parent(self, path: Path) -> int | None:
        current = self.root_fd
        prefix = Path()
        for part in path.parts:
            prefix /= part
            key = str(prefix)
            if key not in self.parents:
                try:
                    self.parents[key] = os.open(
                        part,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=current,
                    )
                except FileNotFoundError:
                    return None
                except NotADirectoryError:
                    # A leaf in this image can block an ancestor from the other
                    # image. It remains a no-follow payload, never a directory.
                    if key not in self.current_paths:
                        raise
                    _identity(current, part)
                    return None
            current = self.parents[key]
        return current

    def _bind_directories(self) -> None:
        for name in sorted(self.current_directories, key=lambda value: len(Path(value).parts)):
            fd = self._parent(Path(name))
            for entry in self.entries:
                if entry.parent is None and str(Path(entry.path).parent) == name:
                    entry.parent = fd

    def _entry_identity(self, name: str, parent: int | None) -> tuple | None:
        if parent is None:
            return None
        leaf = Path(name).name
        if name in self.current_directories:
            try:
                observed = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return None
            if stat.S_ISDIR(observed.st_mode):
                fd = self.parents.get(name)
                if fd is None or (observed.st_dev, observed.st_ino) != (
                    os.fstat(fd).st_dev,
                    os.fstat(fd).st_ino,
                ):
                    raise TransactionError("structural directory changed: " + name)
                return None
        return _identity(parent, leaf)

    def _check_parents(self) -> None:
        for name, fd in self.parents.items():
            observed = (self.root / name).stat(follow_symlinks=False)
            bound = os.fstat(fd)
            if (observed.st_dev, observed.st_ino) != (bound.st_dev, bound.st_ino):
                raise TransactionError("source directory changed: " + name)
        for name in self.removed_parents:
            path = Path(name)
            ancestor = self.parents.get(str(path.parent))
            if ancestor is None:
                continue
            try:
                os.stat(path.name, dir_fd=ancestor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            entry = next((entry for entry in self.entries if entry.path == name), None)
            if (
                self.current_paths is self.source_paths
                and entry is not None
                and entry.reverted is not None
            ):
                try:
                    current = _identity(ancestor, path.name)
                except (OSError, TransactionError):
                    current = None
                if current == entry.reverted:
                    continue
            raise TransactionError("foreign entry replaced removed directory: " + name)

    def _check_recovery_parent(self) -> None:
        observed = self.recovery_parent.stat(follow_symlinks=False)
        bound = os.fstat(self.recovery_parent_fd)
        if (observed.st_dev, observed.st_ino) != (bound.st_dev, bound.st_ino):
            raise TransactionError("recovery parent changed: " + str(self.recovery_parent))
        if bound.st_dev != os.fstat(self.root_fd).st_dev:
            raise TransactionError("FIXVAL recovery must share the source filesystem")

    def _allocate_recovery(self, kind: str) -> tuple[Path, int]:
        self._check_recovery_parent()
        prefix = ".fixval-%s-%s-" % (kind, self.recovery_namespace)
        allocated = Path(
            tempfile.mkdtemp(prefix=prefix, dir="/proc/self/fd/%s" % self.recovery_parent_fd)
        )
        directory = self.recovery_parent / allocated.name
        fd = None
        observed = None
        try:
            observed = os.stat(allocated.name, dir_fd=self.recovery_parent_fd, follow_symlinks=False)
            fd = os.open(
                allocated.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=self.recovery_parent_fd,
            )
            bound = os.fstat(fd)
            if (observed.st_dev, observed.st_ino) != (bound.st_dev, bound.st_ino):
                raise TransactionError("allocated recovery directory changed")
            self._check_recovery_parent()
        except BaseException:
            try:
                if observed is not None:
                    current = os.stat(
                        allocated.name, dir_fd=self.recovery_parent_fd, follow_symlinks=False
                    )
                    if (current.st_dev, current.st_ino) == (observed.st_dev, observed.st_ino):
                        os.rmdir(allocated.name, dir_fd=self.recovery_parent_fd)
            finally:
                if fd is not None:
                    os.close(fd)
            raise
        return directory, fd

    def _check_directory(self) -> None:
        self._check_recovery_parent()
        observed = self.directory.stat(follow_symlinks=False)
        bound = os.fstat(self.saved_fd)
        if (observed.st_dev, observed.st_ino) != (bound.st_dev, bound.st_ino):
            raise TransactionError("recovery directory changed: " + str(self.directory))

    def _move(
        self,
        source_parent: int,
        source: str,
        saved: str,
        expected: tuple,
        *,
        check_namespace: bool = True,
    ) -> None:
        if check_namespace:
            self._check_directory()
        if _identity(self.saved_fd, saved) is not None:
            raise TransactionError("recovery entry already exists: " + saved)
        self._rename_between(source_parent, source, self.saved_fd, saved)
        moved = _identity(self.saved_fd, saved)
        self.saved_entries[saved] = moved
        if moved != expected:
            # A writer swapped the source between the identity check and rename.
            # Keep its inode and link it back only if its former name is free.
            os.link(
                saved, source, src_dir_fd=self.saved_fd, dst_dir_fd=source_parent, follow_symlinks=False
            )
            raise TransactionError("source entry changed during preservation: " + source)

    def _rename_between(self, source_fd: int, source: str, target_fd: int, target: str) -> None:
        # Same bound-FD primitive as MutationWorkspace's no-replace rename;
        # importing its unmerged lease/cache owner would widen this transaction.
        if self.libc.renameat2(source_fd, os.fsencode(source), target_fd, os.fsencode(target), 1) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), source)

    def _check_no_replace(self) -> None:
        probe = ".rename-probe"
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=self.saved_fd)
        os.close(fd)
        self.saved_entries[probe] = _identity(self.saved_fd, probe)
        try:
            self._rename_between(self.saved_fd, probe, self.saved_fd, probe)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise TransactionError("kernel lacks FIXVAL no-replace rename support") from exc
        else:
            raise TransactionError("kernel did not enforce FIXVAL no-replace rename")
        if _identity(self.saved_fd, probe) != self.saved_entries[probe]:
            raise TransactionError("no-replace probe was replaced")
        os.unlink(probe, dir_fd=self.saved_fd)
        del self.saved_entries[probe]

    def _copy_payload(
        self,
        parent: int,
        leaf: str,
        saved: str,
        expected: tuple,
        *,
        check_namespace: bool = True,
        target_parent: int | None = None,
    ) -> tuple:
        """Keep recovery content separate from every inode published to live source."""
        destination = self.saved_fd if target_parent is None else target_parent
        if check_namespace:
            self._check_directory()
        if _identity(parent, leaf) != expected:
            raise TransactionError("source changed before snapshot: " + leaf)
        if stat.S_ISLNK(expected[2]):
            os.symlink(expected[-1], saved, dir_fd=destination)
        else:
            with ExitStack() as stack:
                source_fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                source = stack.enter_context(os.fdopen(source_fd, "rb"))
                opened = os.fstat(source_fd)
                if (opened.st_dev, opened.st_ino) != expected[:2]:
                    raise TransactionError("source changed while opening snapshot: " + leaf)
                saved_fd = os.open(
                    saved, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=destination
                )
                target = stack.enter_context(os.fdopen(saved_fd, "wb"))
                shutil.copyfileobj(source, target, 1024 * 1024)
                os.fchmod(saved_fd, stat.S_IMODE(expected[2]))
        copied = _identity(destination, saved)
        if target_parent is None:
            self.saved_entries[saved] = copied
        if _identity(parent, leaf) != expected or copied[2] != expected[2] or copied[-1] != expected[-1]:
            raise TransactionError("source changed during snapshot: " + leaf)
        return copied

    def prepare(self) -> None:
        self._check_recovery_parent()
        self._check_parents()
        self.directory, self.saved_fd = self._allocate_recovery("recovery")
        self.recovery_location = str(self.directory)
        self._check_directory()
        self._check_no_replace()
        for entry in self.entries:
            if entry.original is None:
                continue
            snapshot = "snapshot-" + entry.saved if entry.retained else entry.saved
            copied = self._copy_payload(entry.parent, entry.leaf, snapshot, entry.original)
            entry.snapshot = snapshot
            if not entry.retained:
                entry.saved_identity = copied
        for entry in self.entries:
            if not entry.retained or entry.original is None:
                continue
            if _identity(entry.parent, entry.leaf) != entry.original:
                raise TransactionError("retained entry changed before preservation: " + entry.path)
            self._move(entry.parent, entry.leaf, entry.saved, entry.original)
            entry.saved_identity = self.saved_entries[entry.saved]

    def git_environment(self, image=None):
        """Scope local apply to its explicit working tree, without inherited routing."""
        environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        environment.update(
            GIT_CONFIG_GLOBAL="/dev/null",
            GIT_CONFIG_SYSTEM="/dev/null",
            GIT_CONFIG_NOSYSTEM="1",
            GIT_OPTIONAL_LOCKS="0",
            GIT_TERMINAL_PROMPT="0",
            GIT_DISCOVERY_ACROSS_FILESYSTEM="0",
            GIT_CEILING_DIRECTORIES=str(
                Path(image.cwd).resolve().parent if image is not None else self.root.parent
            ),
        )
        return environment

    @contextmanager
    def _private_image(self):
        image = _PrivateImage(self)
        try:
            yield image
        finally:
            active = sys.exception()
            try:
                image.cleanup()
            except (OSError, TransactionError) as exc:
                location = os.readlink(image.cwd)
                message = "private image cleanup failed: %s; owned image: %s" % (exc, location)
                self.image_errors.append(message)
                self.recovery_needed = True
                if active is None:
                    raise TransactionError(message) from exc
                active.add_note(message)
            finally:
                image.close()

    def reverse(self, patch_path: str | None = None) -> subprocess.CompletedProcess:
        """Generate a private preimage, then publish only transaction-owned inodes."""
        self._check_directory()
        self._check_parents()
        with self._private_image() as image:
            with ExitStack() as stack:
                for entry in self.entries:
                    if entry.original is None or entry.retained:
                        continue
                    parent = image.parent(entry.path, stack)
                    self._copy_payload(
                        self.saved_fd,
                        entry.snapshot,
                        entry.leaf,
                        self.saved_entries[entry.snapshot],
                        target_parent=parent,
                    )
            if patch_path is None:
                fd = os.open(
                    ".reverse.patch",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=image.fd,
                )
                with os.fdopen(fd, "w", encoding="utf-8") as output:
                    output.write(self.patch)
                patch_path = image.cwd + "/.reverse.patch"
            result = subprocess.run(
                ["git", "apply", "-R", str(Path(patch_path).absolute())],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                cwd=image.cwd,
                env=self.git_environment(image),
            )
            image.seal()
            if result.returncode != 0:
                return result
            payloads = {}
            for entry in self.entries:
                if entry.path not in self.source_paths:
                    continue
                payload = image.files.get(entry.path)
                if payload is None:
                    raise TransactionError("reverse image omitted source entry: " + entry.path)
                parent, leaf, identity = payload
                saved = "reverse-" + entry.saved
                payloads[entry.path] = (saved, self._copy_payload(parent, leaf, saved, identity))
            self._check_directory()
            self._check_parents()
            for entry in self.entries:
                expected = None if entry.retained else entry.original
                if self._entry_identity(entry.path, entry.parent) != expected:
                    raise TransactionError("source changed before reverse publication: " + entry.path)
            self.reverted = True
            for entry in self.entries:
                entry.reverted = None if entry.retained else entry.original
            for entry in self.entries:
                if entry.reverted is not None:
                    self._move(entry.parent, entry.leaf, "postimage-" + entry.saved, entry.reverted)
                    entry.reverted = None
            self._retire_directories(self.target_directories - self.source_directories, reverse=True)
            self.current_paths = self.source_paths
            self.current_directories = self.source_directories
            self._publish_source_directories()
            for entry in self.entries:
                entry.parent = self._parent(Path(entry.path).parent)
                payload = payloads.get(entry.path)
                if payload is None:
                    continue
                saved, expected = payload
                entry.reverted = expected
                os.link(
                    saved,
                    entry.leaf,
                    src_dir_fd=self.saved_fd,
                    dst_dir_fd=entry.parent,
                    follow_symlinks=False,
                )
                self._check_parents()
                if _identity(entry.parent, entry.leaf) != expected:
                    raise TransactionError("reverse publication changed: " + entry.path)
        return result

    def _publish_source_directories(self) -> None:
        for name in sorted(self.source_directories, key=lambda value: len(Path(value).parts)):
            if name in self.parents:
                continue
            self._check_parents()
            path = Path(name)
            ancestor = self.parents.get(str(path.parent))
            if ancestor is None:
                raise TransactionError("source ancestor is not bound: " + name)
            saved = ".source-parent-%s" % self.parent_serial
            self.parent_serial += 1
            os.mkdir(saved, 0o755, dir_fd=self.saved_fd)
            fd = os.open(saved, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.saved_fd)
            try:
                self._rename_between(self.saved_fd, saved, ancestor, path.name)
                observed = os.stat(path.name, dir_fd=ancestor, follow_symlinks=False)
                bound = os.fstat(fd)
                if (observed.st_dev, observed.st_ino) != (bound.st_dev, bound.st_ino):
                    raise TransactionError("source directory changed during publication: " + name)
                self.parents[name] = fd
                fd = None
            finally:
                if fd is not None:
                    os.close(fd)

    def mark_reverted(self) -> None:
        if not self.reverted:
            raise TransactionError("reverse image was not published by this transaction")
        self._check_parents()
        for entry in self.entries:
            if self._entry_identity(entry.path, entry.parent) != entry.reverted:
                raise TransactionError("reverse entry changed before validation: " + entry.path)

    def _rebind_removed_parents(self) -> None:
        # Git can remove an emptied parent during a type replacement and then
        # recreate it. A renamed, still-linked parent is a foreign namespace.
        for name, previous in list(self.parents.items()):
            if name == "." or os.fstat(previous).st_nlink != 0:
                continue
            parent_path = Path(name)
            changing_role = name in (self.source_directories ^ self.target_directories)
            ancestor = self._parent(parent_path.parent)
            try:
                if ancestor is None:
                    raise FileNotFoundError("Git removed source ancestor")
                replacement = os.open(
                    parent_path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=ancestor
                )
            except (FileNotFoundError, NotADirectoryError) as exc:
                if isinstance(exc, NotADirectoryError) and (
                    not changing_role or name not in self.current_paths
                ):
                    raise TransactionError("foreign entry replaced removed directory: " + name) from exc
                if not changing_role and any(
                    entry.parent == previous and entry.original is not None for entry in self.entries
                ):
                    raise TransactionError(
                        "Git removed a parent with an original source entry: " + name
                    ) from None
                replacement = None
                if name in self.original_parent_modes and changing_role:
                    self.removed_parents[name] = stat.S_IMODE(os.fstat(previous).st_mode)
                del self.parents[name]
            else:
                if name not in self.current_directories:
                    os.close(replacement)
                    raise TransactionError("foreign entry replaced removed directory: " + name)
                self.parents[name] = replacement
            for entry in self.entries:
                if entry.parent == previous:
                    entry.parent = replacement
            os.close(previous)

    def _restore_removed_parents(self) -> None:
        """Publish only owned empty directories through no-replace renames."""
        for name in sorted(self.removed_parents, key=lambda value: len(Path(value).parts)):
            self._check_parents()
            self._check_directory()
            path = Path(name)
            ancestor = self.parents.get(str(path.parent))
            if ancestor is None:
                if self.current_paths is self.source_paths and any(
                    entry.reverted is not None
                    and entry.path in self.source_paths
                    and Path(entry.path) in path.parents
                    and entry.parent is not None
                    and _identity(entry.parent, entry.leaf) == entry.reverted
                    for entry in self.entries
                ):
                    continue
                raise TransactionError("source ancestor is not bound: " + name)
            entry = next((entry for entry in self.entries if entry.path == name), None)
            if (
                self.current_paths is self.source_paths
                and entry is not None
                and entry.reverted is not None
                and _identity(ancestor, path.name) == entry.reverted
            ):
                # Forward Git removes this known preimage leaf before creating
                # its postimage directory. Recovery without Git removes it first.
                continue
            saved = ".parent-%s" % self.parent_serial
            self.parent_serial += 1
            os.mkdir(saved, 0o700, dir_fd=self.saved_fd)
            replacement = os.open(
                saved, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.saved_fd
            )
            try:
                os.fchmod(replacement, self.removed_parents[name])
                self._rename_between(self.saved_fd, saved, ancestor, path.name)
                observed = os.stat(path.name, dir_fd=ancestor, follow_symlinks=False)
                bound = os.fstat(replacement)
                if (observed.st_dev, observed.st_ino) != (bound.st_dev, bound.st_ino):
                    raise TransactionError("source directory changed during restoration: " + name)
            except BaseException:
                os.close(replacement)
                raise
            self.parents[name] = replacement
            del self.removed_parents[name]
            for entry in self.entries:
                if entry.parent is None and str(Path(entry.path).parent) == name:
                    entry.parent = replacement

    def can_apply_forward(self) -> bool:
        self._check_parents()
        unchanged = all(
            self._entry_identity(entry.path, entry.parent) == entry.reverted for entry in self.entries
        )
        if unchanged:
            self._restore_removed_parents()
            self._check_parents()
        return unchanged

    def _restore_entry(self, entry: _Entry, forward_applied: bool) -> None:
        if entry.parent is None:
            if entry.original is not None:
                raise TransactionError("original source parent is not bound: " + entry.path)
            return
        current = self._entry_identity(entry.path, entry.parent)
        if current == entry.restored and entry.restored is not None:
            self._check_parents()
            return
        if current == entry.original and (not entry.retained or entry.saved_identity is None):
            entry.restored = current
            return
        if entry.original is not None and entry.saved_identity is None:
            raise TransactionError("source has no validated recovery entry: " + entry.path)
        if entry.original is not None and _identity(self.saved_fd, entry.saved) != entry.saved_identity:
            raise TransactionError("saved entry changed: " + entry.path)
        if current is not None:
            expected = entry.reverted if self.reverted else entry.original
            if current != expected:
                raise TransactionError("foreign entry prevents restoration: " + entry.path)
            self._move(
                entry.parent, entry.leaf, "reverted-" + entry.saved, expected, check_namespace=False
            )
        expected = None
        if entry.original is not None:
            publication = entry.saved
            expected = entry.original
            if not entry.retained:
                publication = "published-" + entry.saved
                expected = self.saved_entries.get(publication)
                if expected is None:
                    expected = self._copy_payload(
                        self.saved_fd,
                        entry.saved,
                        publication,
                        entry.saved_identity,
                        check_namespace=False,
                    )
                elif _identity(self.saved_fd, publication) != expected:
                    raise TransactionError("publication payload changed: " + entry.path)
            entry.restored = expected
            os.link(
                publication,
                entry.leaf,
                src_dir_fd=self.saved_fd,
                dst_dir_fd=entry.parent,
                follow_symlinks=False,
            )
        self._check_parents()
        restored = _identity(entry.parent, entry.leaf)
        if restored != expected:
            raise TransactionError("source identity, content or mode was not restored: " + entry.path)
        entry.restored = restored

    def _retire_source_directories(self) -> None:
        self._retire_directories(self.source_directories - self.target_directories)

    def _retire_directories(self, names: set[str], *, reverse: bool = False) -> None:
        """Remove only bound empty containers, preserving original directory modes."""
        for name in sorted(names, key=lambda value: len(Path(value).parts), reverse=True):
            fd = self.parents.get(name)
            if fd is None:
                continue
            self._check_parents()
            if os.listdir(fd):
                if reverse and name not in self.source_paths:
                    # Git leaves an existing container with unrelated entries.
                    # Keep its bound namespace in both physical images.
                    self.source_directories.add(name)
                    continue
                raise TransactionError("foreign entries prevent directory restoration: " + name)
            path = Path(name)
            ancestor = self.parents.get(str(path.parent))
            if ancestor is None:
                raise TransactionError("source ancestor is not bound: " + name)
            saved = ".retired-parent-%s" % self.parent_serial
            self.parent_serial += 1
            expected = os.fstat(fd)
            self._check_directory()
            self._rename_between(ancestor, path.name, self.saved_fd, saved)
            moved = os.stat(saved, dir_fd=self.saved_fd, follow_symlinks=False)
            if (moved.st_dev, moved.st_ino) != (expected.st_dev, expected.st_ino):
                # The writer's directory stays intact. Publish it back only if
                # its former name is free; otherwise keep it in recovery.
                self._rename_between(self.saved_fd, saved, ancestor, path.name)
                raise TransactionError("source directory changed during preservation: " + name)
            try:
                os.rmdir(saved, dir_fd=self.saved_fd)
            except OSError:
                self._rename_between(self.saved_fd, saved, ancestor, path.name)
                raise
            if reverse:
                self.removed_parents[name] = self.original_parent_modes[name]
            del self.parents[name]
            for entry in self.entries:
                if entry.parent == fd:
                    entry.parent = None
            os.close(fd)

    def restore(self, forward_applied: bool) -> list[str]:
        if self.directory is None and self.saved_fd is None:
            # No working-tree entry can move before recovery allocation succeeds.
            return []
        errors = []
        if forward_applied:
            self.recovery_needed = True
            return ["unowned forward restoration is not supported"]
        try:
            self._check_directory()
        except (OSError, TransactionError) as exc:
            errors.append(str(exc))
        try:
            self._rebind_removed_parents()
            self._check_parents()
            if self.reverted and not forward_applied:
                for entry in self.entries:
                    if entry.original is None:
                        self._restore_entry(entry, False)
                self._retire_source_directories()
            self._restore_removed_parents()
            if self.reverted and not forward_applied:
                self.current_paths = self.target_paths
                self.current_directories = self.target_directories
        except (OSError, TransactionError) as exc:
            errors.append(str(exc))
        for entry in self.entries:
            if self.reverted and not forward_applied and entry.original is None:
                continue
            try:
                self._restore_entry(entry, forward_applied)
            except (OSError, TransactionError) as exc:
                errors.append(str(exc))
        self.recovery_needed = bool(errors)
        return errors

    def _locate_recovery(self) -> str:
        try:
            return os.readlink("/proc/self/fd/%s" % self.saved_fd)
        except OSError:
            bound = os.fstat(self.saved_fd)
            return "%s (recovery inode %s:%s)" % (
                self.directory,
                bound.st_dev,
                bound.st_ino,
            )

    def _check_restored_sources(self) -> None:
        self._check_parents()
        for entry in self.entries:
            if (
                entry.restored is not None
                and self._entry_identity(entry.path, entry.parent) != entry.restored
            ):
                raise TransactionError("restored source changed before cleanup: " + entry.path)

    def _cleanup_recovery(self) -> None:
        """Retire original names before deleting only validated private entries."""
        retirement, retirement_fd = self._allocate_recovery("retired")
        retired_directory = False
        pending = {}
        try:
            expected = os.fstat(self.saved_fd)
            self._rename_between(self.recovery_parent_fd, self.directory.name, retirement_fd, "recovery")
            retired_directory = True
            moved = os.stat("recovery", dir_fd=retirement_fd, follow_symlinks=False)
            if (moved.st_dev, moved.st_ino) != (expected.st_dev, expected.st_ino):
                raise TransactionError("recovery directory changed during cleanup")
            snapshots = {entry.snapshot for entry in self.entries if entry.snapshot is not None}
            ordered = sorted(self.saved_entries.items(), key=lambda item: item[0] in snapshots)
            snapshots_checked = False
            for index, (name, identity) in enumerate(ordered):
                if name in snapshots and not snapshots_checked:
                    self._check_restored_sources()
                    snapshots_checked = True
                private_name = "payload-%s" % index
                self._rename_between(self.saved_fd, name, retirement_fd, private_name)
                pending[private_name] = name
                if _identity(retirement_fd, private_name) != identity:
                    raise TransactionError("recovery entry changed during cleanup: " + name)
                os.unlink(private_name, dir_fd=retirement_fd)
                del pending[private_name]
            os.rmdir("recovery", dir_fd=retirement_fd)
            retired_directory = False
            os.rmdir(retirement.name, dir_fd=self.recovery_parent_fd)
        except BaseException:
            self.recovery_needed = True
            for private_name, name in pending.items():
                try:
                    self._rename_between(retirement_fd, private_name, self.saved_fd, name)
                except OSError:
                    pass
            if retired_directory:
                try:
                    self._rename_between(
                        retirement_fd, "recovery", self.recovery_parent_fd, self.directory.name
                    )
                except OSError:
                    pass
            if retirement_fd is not None and not os.listdir(retirement_fd):
                try:
                    os.rmdir(retirement.name, dir_fd=self.recovery_parent_fd)
                except OSError:
                    pass
            self.recovery_location = self._locate_recovery()
            if retirement.exists():
                self.recovery_location += "; retirement: " + str(retirement)
            raise
        finally:
            if retirement_fd is not None:
                os.close(retirement_fd)

    def close(self) -> None:
        try:
            if self.saved_fd is not None:
                self.recovery_location = self._locate_recovery()
            if self.recovery_needed:
                return
            if self.directory is None:
                return
            self._check_parents()
            self._check_directory()
            self._check_restored_sources()
            os.lseek(self.saved_fd, 0, os.SEEK_SET)
            if set(os.listdir(self.saved_fd)) != set(self.saved_entries):
                raise TransactionError("recovery contains an unexpected entry")
            for name, expected in self.saved_entries.items():
                if _identity(self.saved_fd, name) != expected:
                    raise TransactionError("recovery entry changed before cleanup: " + name)
            self._cleanup_recovery()
        finally:
            self._close_descriptors()
