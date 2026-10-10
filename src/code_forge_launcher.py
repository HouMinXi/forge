# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Bind public Forge entries to one verified installation or registered source tree."""

from __future__ import annotations

import os

# Record the parent before the remaining imports can outlive it.
# ruff: noqa: E402
_STARTUP_PID, _STARTUP_PPID = os.getpid(), os.getppid()

from collections.abc import Mapping, Sequence
import csv
from dataclasses import dataclass
from email.parser import BytesParser
import hashlib
import importlib
from importlib.machinery import (
    ExtensionFileLoader,
    ModuleSpec,
    PathFinder,
    SourceFileLoader,
    SourcelessFileLoader,
)
from importlib import metadata
from importlib.util import spec_from_file_location
import io
import json
import locale
from pathlib import Path, PosixPath, PurePosixPath
import re
import site
import stat
import subprocess
import sys
import tomllib
from types import CodeType, ModuleType
from typing import Literal
from urllib.parse import urlsplit
from urllib.request import url2pathname


class SourceError(RuntimeError):
    """Refuse ambiguous source identity before importing the business package."""


@dataclass(frozen=True)
class InstalledSource:
    package_root: Path
    checkout_root: Path | None
    common_dir: Path | None
    launcher_path: Path
    metadata_root: Path
    editable_layout: Literal["static", "link-tree", "finder", "wheel"]
    installed_version: str


@dataclass(frozen=True)
class SourceSelection:
    installed: InstalledSource
    package_root: Path
    checkout_root: Path | None
    mode: Literal["installed", "worktree"]
    declared_version: str
    source_sha256: str


@dataclass(frozen=True)
class CliInvocation:
    argv_prefix: tuple[str, ...]
    cwd: Path
    env: dict[str, str]


_GIT_ENV = frozenset(
    {
        "GIT_DIR",
        "GIT_COMMON_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_NAMESPACE",
        "GIT_CEILING_DIRECTORIES",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_CONFIG_NOSYSTEM",
    }
)
_REINSTALL = "Reinstall Forge in an isolated interpreter environment."


def sanitized_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy child settings without caller-controlled Python or Git routing."""
    source = os.environ if env is None else env
    if not isinstance(source, Mapping) or (env is not None and not source):
        raise ValueError("child environment must be a nonempty string mapping")
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in source.items()):
        raise ValueError("child environment keys and values must be strings")
    return {
        key: value
        for key, value in source.items()
        if key not in _GIT_ENV
        and key not in {"PYTHONPATH", "PYTHONHOME"}
        and not key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
    }


def _directory(path: Path) -> Path:
    if not isinstance(path, Path):
        raise SourceError("workspace must be an existing Path directory")
    try:
        physical = path.resolve(strict=True)
        if not physical.is_dir() or not os.access(physical, os.R_OK | os.X_OK):
            raise SourceError("workspace must be a readable directory")
    except SourceError:
        raise
    except (OSError, RuntimeError) as exc:
        raise SourceError("workspace directory cannot be resolved") from exc
    return physical


def _file(path: Path, *, within: Path | None = None) -> Path:
    try:
        physical = path.resolve(strict=True)
        if not physical.is_file() or not os.access(physical, os.R_OK):
            raise SourceError("source input is not a readable regular file")
        if within is not None and not physical.is_relative_to(within):
            raise SourceError("source input escapes its owner")
        physical.read_bytes()
    except SourceError:
        raise
    except (OSError, RuntimeError) as exc:
        raise SourceError("source input is missing, unreadable or cyclic") from exc
    return physical


def _same_file(first: Path, second: Path) -> bool:
    try:
        return first.samefile(second)
    except OSError as exc:
        raise SourceError("source file identity cannot be compared") from exc


def _project(checkout: Path) -> dict[str, object]:
    try:
        with _file(checkout / "pyproject.toml", within=checkout).open("rb") as stream:
            data = tomllib.load(stream)
        project = data.get("project")
        if not isinstance(project, dict) or project.get("name") != "code-review-forge":
            raise SourceError("expected Forge project declaration is missing")
        if not isinstance(project.get("version"), str) or not project["version"].strip():
            raise SourceError("Forge project version is missing")
        _file(checkout / "src/code_forge/__init__.py", within=checkout)
        package = _directory(checkout / "src/code_forge")
        if not package.is_relative_to(checkout):
            raise SourceError("Forge package escapes its checkout")
    except (OSError, ValueError) as exc:
        raise SourceError("Forge project declaration cannot be read") from exc
    return project


def _git(
    root: Path, *args: str, env: Mapping[str, str] | None = None, ordinary: bool = False
) -> bytes | None:
    child_env = sanitized_env() if env is None else dict(env)
    child_env["GIT_OPTIONAL_LOCKS"] = "0"
    child_env["LC_ALL"] = "C"
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            env=child_env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SourceError("Forge Git identity query failed or exceeded 2 seconds") from exc
    if ordinary and result.returncode == 128 and b"not a git repository" in result.stderr.lower():
        return None
    if result.returncode != 0 or not result.stdout:
        raise SourceError("Forge Git identity is unavailable or inconsistent")
    return result.stdout


def _git_path(raw: bytes, relative_to: Path) -> Path:
    try:
        text = raw.decode("utf-8").removesuffix("\n")
        if not text or "\n" in text or "\r" in text or "\0" in text:
            raise SourceError("malformed Git identity path")
        path = Path(text)
        return _directory(path if path.is_absolute() else relative_to / path)
    except UnicodeError as exc:
        raise SourceError("Git identity path is not UTF-8") from exc


def _checkout_common_dir(checkout: Path, env: Mapping[str, str] | None = None) -> Path:
    top = _git_path(
        _git(checkout, "rev-parse", "--path-format=absolute", "--show-toplevel", env=env), checkout
    )
    if top != checkout:
        raise SourceError("installed Forge checkout has a broken Git identity")
    return _git_path(
        _git(checkout, "rev-parse", "--path-format=absolute", "--git-common-dir", env=env), checkout
    )


def _registered(checkout: Path, env: Mapping[str, str] | None = None) -> set[Path]:
    raw = _git(checkout, "worktree", "list", "--porcelain", "-z", env=env)
    assert raw is not None
    if not raw.endswith(b"\0\0"):
        raise SourceError("malformed Git worktree registry")
    roots: set[Path] = set()
    for record in raw.split(b"\0\0")[:-1]:
        fields = record.split(b"\0")
        if not fields or not fields[0].startswith(b"worktree "):
            raise SourceError("malformed Git worktree registry entry")
        try:
            spelling = fields[0][len(b"worktree ") :].decode("utf-8")
            path = Path(spelling)
            if not path.is_absolute() or not spelling:
                raise SourceError("worktree registry root is not absolute")
            physical = path.resolve()
        except SourceError:
            raise
        except (OSError, RuntimeError, UnicodeError) as exc:
            raise SourceError("worktree registry root cannot be resolved") from exc
        if physical in roots or any(field.startswith(b"worktree ") for field in fields[1:]):
            raise SourceError("duplicate worktree registry root")
        if any(
            not field.startswith((b"HEAD ", b"branch ", b"locked ", b"prunable "))
            and field not in {b"bare", b"detached", b"locked", b"prunable"}
            for field in fields[1:]
        ):
            raise SourceError("unexpected Git worktree registry field")
        roots.add(physical)
    if checkout not in roots:
        raise SourceError("installed checkout is missing from its Git worktree registry")
    return roots


def _sites() -> tuple[Path, ...]:
    locations = list(site.getsitepackages())
    if site.ENABLE_USER_SITE:
        user = site.getusersitepackages()
        locations.extend([user] if isinstance(user, str) else user)
    physical: list[Path] = []
    for spelling in locations:
        path = Path(spelling).resolve()
        if path.is_dir() and path not in physical:
            physical.append(path)
    return tuple(physical)


def _normalized_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _distribution_name(dist: metadata.Distribution) -> str | None:
    name = _physical_distribution(dist).metadata.get("Name")
    return name if isinstance(name, str) else None


def _source_ownership_names(names: Sequence[str]) -> list[str]:
    return [name for name in names if PurePosixPath(name).suffix != ".pyc"]


def _metadata_descriptor(path: Path) -> int | None:
    if os.name != "nt":
        return os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    import ctypes
    import msvcrt

    class AttributeTag(ctypes.Structure):
        _fields_ = [("FileAttributes", ctypes.c_uint32), ("ReparseTag", ctypes.c_uint32)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.GetFileType.argtypes = [ctypes.c_void_p]
    kernel.GetFileType.restype = ctypes.c_uint32
    kernel.GetFileInformationByHandleEx.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int32,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    kernel.GetFileInformationByHandleEx.restype = ctypes.c_int32
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int32
    # Read existing objects without following the final reparse point. Permit
    # directory handles so the standard missing-text fallback remains intact.
    handle = kernel.CreateFileW(str(path), 0x80000000, 7, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if kernel.GetFileType(handle) != 1:
            raise SourceError("installed metadata is not a disk file")
        attributes = AttributeTag()
        if not kernel.GetFileInformationByHandleEx(handle, 9, ctypes.byref(attributes), 8):
            raise ctypes.WinError(ctypes.get_last_error())
        if attributes.FileAttributes & 0x400:
            raise SourceError("installed metadata is a reparse point")
        if attributes.FileAttributes & 0x10:
            return None
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
        handle = None  # The CRT descriptor now exclusively owns this handle.
        return descriptor
    finally:
        if handle is not None and not kernel.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())


def _metadata_bytes(path: Path, *, within: Path) -> tuple[Path, bytes] | None:
    descriptor = None
    try:
        physical = path.resolve(strict=True)
        owner = within.resolve(strict=True)
        if not physical.is_relative_to(owner):
            raise SourceError("installed metadata escapes its owner")
        descriptor = _metadata_descriptor(physical)
        if descriptor is None:
            return None
        first = os.fstat(descriptor)
        if stat.S_ISDIR(first.st_mode):
            return None
        if not stat.S_ISREG(first.st_mode):
            raise SourceError("installed metadata is not a regular file")

        def identity(value: os.stat_result) -> tuple[int, ...]:
            return (
                value.st_dev,
                value.st_ino,
                value.st_mode,
                value.st_nlink,
                value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns,
            )

        remaining = first.st_size
        chunks = []
        while remaining >= 0:
            block = os.read(descriptor, min(65536, remaining + 1))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
        if (
            remaining < 0
            or identity(first) != identity(os.fstat(descriptor))
            or path.resolve(strict=True) != physical
            or identity(first) != identity(physical.stat())
        ):
            raise SourceError("installed metadata changed while being read")
        return physical, b"".join(chunks)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except SourceError:
        raise
    except (OSError, RuntimeError) as exc:
        raise SourceError("installed metadata cannot be read safely") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


class _PhysicalDistribution(metadata.PathDistribution):
    def read_text(self, filename: str) -> str | None:
        if filename not in {"METADATA", "PKG-INFO", "RECORD", "installed-files.txt", "SOURCES.txt", ""}:
            raise SourceError("unexpected installed metadata input")
        selected = _metadata_bytes(self._path / filename, within=self._path)
        if selected is None:
            return None
        try:
            with io.TextIOWrapper(io.BytesIO(selected[1]), encoding="utf-8") as stream:
                return stream.read()
        except UnicodeError as exc:
            raise SourceError("installed metadata is not UTF-8") from exc


def _physical_distribution(
    dist: metadata.Distribution, site_root: Path | None = None
) -> _PhysicalDistribution:
    if type(dist) is _PhysicalDistribution:
        bound = dist
    elif type(dist) is metadata.PathDistribution and isinstance(dist._path, Path):
        bound = _PhysicalDistribution(dist._path)
    else:
        raise SourceError("installed distribution has no physical metadata owner")
    if site_root is not None:
        try:
            if not bound._path.resolve(strict=True).is_relative_to(site_root):
                raise SourceError("installed distribution metadata escapes its interpreter site")
        except SourceError:
            raise
        except (OSError, RuntimeError) as exc:
            raise SourceError("installed distribution metadata owner cannot be resolved") from exc
    return bound


def _distribution_files(dist: metadata.Distribution, *, include_cache: bool = False) -> list[str] | None:
    try:
        entries = _physical_distribution(dist).files
        if entries is None:
            return None
        names = [str(entry).replace(os.sep, "/") for entry in entries]
        return names if include_cache else _source_ownership_names(names)
    except (OSError, UnicodeError, csv.Error, ValueError, TypeError) as exc:
        raise SourceError(
            "installed distribution ownership metadata cannot be read; " + _REINSTALL
        ) from exc


def _manifest(dist: metadata.Distribution, site_root: Path) -> tuple[Path, list[str]]:
    names = _distribution_files(dist)
    if names is None:
        raise SourceError("installed Forge has no ownership manifest; " + _REINSTALL)
    metadata_names = [
        name
        for name in names
        if PurePosixPath(name).name == "METADATA"
        and PurePosixPath(name).parent.name.endswith(".dist-info")
    ]
    if len(metadata_names) != 1:
        raise SourceError("Forge metadata ownership is ambiguous; " + _REINSTALL)
    metadata_root = _directory(Path(dist.locate_file(metadata_names[0])).parent)
    if metadata_root.parent != site_root:
        raise SourceError("Forge metadata is outside its interpreter site directory")
    metadata_file = _metadata_bytes(metadata_root / "METADATA", within=metadata_root)
    record = _metadata_bytes(metadata_root / "RECORD", within=metadata_root)
    if metadata_file is None or record is None:
        raise SourceError("installed Forge metadata inputs are missing")
    try:
        with io.TextIOWrapper(io.BytesIO(record[1]), encoding="utf-8") as stream:
            rows = list(csv.reader(stream, strict=True))
        if not rows or any(len(item) != 3 or not item[0] for item in rows):
            raise SourceError("installed Forge RECORD is malformed")
        record_names = [item[0] for item in rows]
        if _source_ownership_names(record_names) != names or len(set(record_names)) != len(record_names):
            raise SourceError("installed Forge RECORD ownership is inconsistent")
    except (OSError, UnicodeError, csv.Error) as exc:
        raise SourceError("installed Forge RECORD cannot be read") from exc
    return metadata_root, names


def _editable_layout(
    dist: metadata.Distribution, names: list[str], site_root: Path, checkout: Path
) -> Literal["static", "link-tree", "finder"]:
    paths = [name for name in names if PurePosixPath(name).suffix == ".pth"]
    if not paths:
        return "finder"
    plain: list[Path] = []
    try:
        for name in paths:
            data = _file(Path(dist.locate_file(name)), within=site_root).read_bytes()
            # CPython backported UTF-8/BOM decoding to 3.12.4 (GH-119509).
            # Earlier 3.12 releases still use the locale-only text stream.
            if sys.version_info >= (3, 12, 4):
                try:
                    text = data.decode("utf-8-sig")
                except UnicodeDecodeError:
                    text = data.decode(locale.getencoding())
                lines = text.splitlines()
            else:
                with io.TextIOWrapper(io.BytesIO(data), encoding="locale") as stream:
                    lines = list(stream)
            for line in lines:
                if not line.strip() or line.startswith("#"):
                    continue
                if line.startswith(("import ", "import\t")):
                    return "finder"
                raw = Path(line.rstrip())
                path = raw if raw.is_absolute() else site_root / raw
                if path.is_dir():
                    physical = _directory(path)
                    if physical not in plain:
                        plain.append(physical)
    except (OSError, UnicodeError) as exc:
        raise SourceError("editable Forge path artifacts cannot be read") from exc
    if len(plain) != 1:
        return "finder"
    if plain[0] == checkout / "src":
        return "static"
    try:
        init = _file(plain[0] / "code_forge/__init__.py", within=checkout)
        launcher = _file(plain[0] / "code_forge_launcher.py", within=checkout)
        expected_init = _file(checkout / "src/code_forge/__init__.py", within=checkout)
        expected_launcher = _file(checkout / "src/code_forge_launcher.py", within=checkout)
        if _same_file(init, expected_init) and _same_file(launcher, expected_launcher):
            return "link-tree"
    except SourceError:
        return "finder"
    return "finder"


def _installed_distributions() -> list[tuple[metadata.Distribution, Path]]:
    distributions: list[tuple[metadata.Distribution, Path]] = []
    for standard_site in _sites():
        distributions.extend(
            (_physical_distribution(dist, standard_site), standard_site)
            for dist in metadata.distributions(path=[str(standard_site)])
        )
    return distributions


def _claimed_within_package(claimed: Path, package: Path, package_parts: tuple[str, ...] | None) -> bool:
    if package_parts is not None and type(claimed) is PosixPath and claimed.is_absolute():
        return claimed.parts[: len(package_parts)] == package_parts
    return claimed.is_relative_to(package)


def _competing_ownership(
    package: Path,
    launcher: Path,
    metadata_root: Path,
    distributions: list[tuple[metadata.Distribution, Path]],
) -> None:
    _, package_identities = _package_entries(package)
    package_parts = package.parts if type(package) is PosixPath and package.is_absolute() else None
    for other, _root in distributions:
        try:
            if other._path.resolve(strict=True) == metadata_root:
                continue
            for entry in _distribution_files(other, include_cache=True) or ():
                try:
                    claimed = Path(other.locate_file(entry)).resolve(strict=True)
                except FileNotFoundError:
                    if PurePosixPath(entry).suffix == ".pyc":
                        continue
                    raise
                physical = claimed.stat()
                if (
                    claimed == launcher
                    or _claimed_within_package(claimed, package, package_parts)
                    or (physical.st_dev, physical.st_ino) in package_identities
                    or claimed.samefile(launcher)
                ):
                    raise SourceError("another distribution claims Forge-owned files; " + _REINSTALL)
        except SourceError:
            raise
        except (OSError, RuntimeError) as exc:
            raise SourceError("installed file ownership cannot be resolved") from exc


def discover_install() -> InstalledSource:
    """Read exactly one physical standard-site distribution without importing Forge."""
    distributions = _installed_distributions()
    matches = [
        (dist, root)
        for dist, root in distributions
        if _normalized_name(_distribution_name(dist) or "") == "code-review-forge"
    ]
    if len(matches) != 1:
        raise SourceError("exactly one interpreter-site Forge installation is required; " + _REINSTALL)
    dist, standard_site = matches[0]
    metadata_root, names = _manifest(dist, standard_site)
    try:
        installed_metadata = _metadata_bytes(metadata_root / "METADATA", within=metadata_root)
        if installed_metadata is None:
            raise SourceError("installed Forge METADATA is missing")
        parsed = BytesParser().parsebytes(installed_metadata[1])
        version = parsed.get("Version")
        if (
            len(parsed.get_all("Name", [])) != 1
            or len(parsed.get_all("Version", [])) != 1
            or not isinstance(version, str)
            or not version.strip()
            or parsed.get("Name") != _distribution_name(dist)
        ):
            raise SourceError("installed Forge METADATA version or name is inconsistent")
        direct_bytes = _metadata_bytes(metadata_root / "direct_url.json", within=metadata_root)
        direct = json.loads(direct_bytes[1]) if direct_bytes is not None else None
    except (OSError, ValueError, UnicodeError) as exc:
        raise SourceError("installed Forge metadata cannot be read") from exc
    if direct is not None and not isinstance(direct, dict):
        raise SourceError("installed Forge direct_url.json is malformed")
    directory_info = direct.get("dir_info") if direct else None
    editable = isinstance(directory_info, dict) and directory_info.get("editable") is True
    if editable:
        try:
            if not isinstance(direct.get("url"), str) or not direct["url"]:
                raise SourceError("editable Forge checkout URL is malformed")
            parsed_url = urlsplit(direct["url"])
            if (
                parsed_url.scheme != "file"
                or parsed_url.netloc not in {"", "localhost"}
                or parsed_url.query
                or parsed_url.fragment
            ):
                raise SourceError("editable Forge requires a local checkout URL")
            checkout = _directory(Path(url2pathname(parsed_url.path)))
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceError("editable Forge checkout URL is malformed") from exc
        _project(checkout)
        package = _directory(checkout / "src/code_forge")
        launcher = _file(checkout / "src/code_forge_launcher.py", within=checkout)
        if not _same_file(_file(Path(__file__), within=checkout), launcher):
            raise SourceError("running launcher does not belong to the installed editable Forge")
        common = _checkout_common_dir(checkout)
        _registered(checkout)
        layout = _editable_layout(dist, names, standard_site, checkout)
        _competing_ownership(package, launcher, metadata_root, distributions)
        return InstalledSource(package, checkout, common, launcher, metadata_root, layout, version)
    init_names = [name for name in names if name == "code_forge/__init__.py"]
    if len(init_names) != 1 or names.count("code_forge_launcher.py") != 1:
        raise SourceError("installed Forge package or launcher ownership is ambiguous; " + _REINSTALL)
    initializer = Path(dist.locate_file(init_names[0]))
    _file(initializer, within=standard_site)
    package = _directory(initializer.parent)
    launcher = _file(Path(dist.locate_file("code_forge_launcher.py")), within=standard_site)
    if launcher.parent != standard_site or _file(Path(__file__)) != launcher:
        raise SourceError("running launcher does not belong to the installed Forge wheel")
    for name in names:
        raw = PurePosixPath(name)
        if raw.parts and raw.parts[0] == "code_forge":
            _file(Path(dist.locate_file(name)), within=package)
    _competing_ownership(package, launcher, metadata_root, distributions)
    return InstalledSource(package, None, None, launcher, metadata_root, "wheel", version)


def _has_forge_marker(workspace: Path, *, walk_parents: bool = True) -> bool:
    roots = (workspace, *workspace.parents) if walk_parents else (workspace,)
    for root in roots:
        if (root / "src/code_forge").exists():
            return True
        project = root / "pyproject.toml"
        if project.is_file():
            try:
                with project.open("rb") as stream:
                    declared = tomllib.load(stream).get("project", {})
                if isinstance(declared, dict) and declared.get("name") == "code-review-forge":
                    return True
            except (OSError, ValueError):
                if (root / ".git").exists():
                    return True
    return False


def _package_entries(
    package: Path,
) -> tuple[list[tuple[str, str, str, str, bytes]], set[tuple[int, int]]]:
    entries: list[tuple[str, str, str, str, bytes]] = []
    identities: set[tuple[int, int]] = set()

    def visit(directory: Path, ancestors: frozenset[tuple[int, int]]) -> None:
        try:
            children = list(directory.iterdir())
            for child in children:
                if child.name == "__pycache__" or child.suffix == ".pyc":
                    continue
                mode = child.lstat().st_mode
                relative = child.relative_to(package).as_posix()
                if stat.S_ISLNK(mode):
                    target = os.readlink(child)
                    resolved = child.resolve(strict=True)
                    if not resolved.is_relative_to(package):
                        raise SourceError("selected package link escapes its root")
                    if not resolved.is_file():
                        raise SourceError("selected package link must resolve to a regular file")
                    entries.append(
                        (
                            "package",
                            relative,
                            "symlink",
                            target,
                            _file(child, within=package).read_bytes(),
                        )
                    )
                elif stat.S_ISREG(mode):
                    entries.append(
                        ("package", relative, "regular", "", _file(child, within=package).read_bytes())
                    )
                elif stat.S_ISDIR(mode):
                    physical = child.resolve(strict=True)
                    identity = physical.stat()
                    key = (identity.st_dev, identity.st_ino)
                    if key in ancestors:
                        raise SourceError("selected package directory cycle")
                    visit(child, ancestors | {key})
                    continue
                else:
                    raise SourceError("selected package contains an unsupported file type")
                physical_file = child.stat()
                identities.add((physical_file.st_dev, physical_file.st_ino))
        except SourceError:
            raise
        except (OSError, RuntimeError) as exc:
            raise SourceError("selected package cannot be hashed safely") from exc

    try:
        identity = package.stat()
    except (OSError, RuntimeError) as exc:
        raise SourceError("selected package root identity cannot be read") from exc
    visit(package, frozenset({(identity.st_dev, identity.st_ino)}))
    return entries, identities


def _digest(installed: InstalledSource, package: Path, checkout: Path | None) -> str:
    entries, _ = _package_entries(package)
    mode = "wheel" if checkout is None else "editable"
    if checkout is None:
        for name in ("METADATA", "RECORD"):
            selected = _metadata_bytes(installed.metadata_root / name, within=installed.metadata_root)
            if selected is None:
                raise SourceError("installed Forge binding metadata is missing")
            entries.append(("wheel-metadata", name, "regular", "", selected[1]))
    else:
        path = _file(checkout / "pyproject.toml", within=checkout)
        entries.append(("editable-manifest", "pyproject.toml", "regular", "", path.read_bytes()))
    digest = hashlib.sha256(b"forge-source-binding-v1\0")

    def frame(value: bytes) -> None:
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)

    frame(mode.encode("ascii"))
    for domain, relative, kind, link, content in sorted(
        entries, key=lambda item: (item[0].encode(), item[1].encode())
    ):
        for value in (domain.encode(), relative.encode(), kind.encode(), link.encode(), content):
            frame(value)
    return digest.hexdigest()


def select_source(workspace: Path, *, env: Mapping[str, str] | None = None) -> SourceSelection:
    """Select a registered same-repository tree or the exact installed package."""
    workspace = _directory(workspace)
    child_env = sanitized_env(env)
    installed = discover_install()
    package, checkout, mode = installed.package_root, installed.checkout_root, "installed"
    version = installed.installed_version
    if checkout is not None:
        roots = _registered(checkout, child_env)
        anchor = _checkout_common_dir(checkout, child_env)
        if anchor != installed.common_dir:
            raise SourceError("installed Forge Git identity changed during selection")
        top_raw = _git(
            workspace,
            "rev-parse",
            "--path-format=absolute",
            "--show-toplevel",
            env=child_env,
            ordinary=True,
        )
        if top_raw is None:
            if any(workspace.is_relative_to(root) for root in roots) or _has_forge_marker(workspace):
                raise SourceError("expected Forge workspace has a broken Git identity")
        else:
            top = _git_path(top_raw, workspace)
            expected = next((root for root in (workspace, *workspace.parents) if root in roots), None)
            if expected is not None and expected != top and expected.is_relative_to(top):
                raise SourceError("expected Forge workspace has a broken Git identity")
            common = _git_path(
                _git(
                    workspace, "rev-parse", "--path-format=absolute", "--git-common-dir", env=child_env
                ),
                workspace,
            )
            if common == installed.common_dir:
                if top not in roots:
                    raise SourceError("Forge workspace is not an exactly registered worktree")
                project = _project(top)
                if top != checkout:
                    if installed.editable_layout == "finder":
                        raise SourceError(
                            "editable finder cannot switch worktrees; use a dedicated environment or static editable install"
                        )
                    trusted = _project(checkout)
                    for key, default in (
                        ("requires-python", None),
                        ("dependencies", []),
                        ("optional-dependencies", {}),
                    ):
                        if project.get(key, default) != trusted.get(key, default):
                            raise SourceError(
                                "worktree dependency declarations differ; use a dedicated environment"
                            )
                    package, checkout, mode = _directory(top / "src/code_forge"), top, "worktree"
                version = project["version"]
            elif _has_forge_marker(top, walk_parents=False):
                raise SourceError("foreign Forge checkout is not trusted by this installation")
        if mode == "installed":
            version = _project(checkout)["version"]
    if mode == "worktree":
        selected_launcher = _file(checkout / "src/code_forge_launcher.py", within=checkout)
        _competing_ownership(
            package, selected_launcher, installed.metadata_root, _installed_distributions()
        )
    source_hash = _digest(installed, package, checkout)
    return SourceSelection(installed, package, checkout, mode, version, source_hash)


class _SelectedSourceLoader(SourceFileLoader):
    """Execute source directly because the binding deliberately excludes bytecode."""

    def __init__(self, name: str, path: str, *, resource_path: str | None = None):
        super().__init__(name, path)
        self._resource_path = path if resource_path is None else resource_path

    def get_resource_reader(self, fullname: str):
        return SourceFileLoader(self.name, self._resource_path).get_resource_reader(fullname)

    def get_code(self, fullname: str) -> CodeType:
        path = self.get_filename(fullname)
        try:
            data = self.get_data(path)
        except OSError as exc:
            raise SourceError("selected Forge source cannot be read") from exc
        return self.source_to_code(data, path)


class _SelectedPackageFinder:
    def __init__(self, root: Path):
        self.root = root

    def find_spec(
        self, fullname: str, path: Sequence[str] | None = None, target: ModuleType | None = None
    ) -> ModuleSpec | None:
        if fullname != "code_forge" and not fullname.startswith("code_forge."):
            return None
        if "__pycache__" in fullname.split(".")[1:]:
            raise SourceError("selected Forge cache directories are excluded from its source binding")
        if fullname == "code_forge":
            spec = spec_from_file_location(
                fullname, self.root / "__init__.py", submodule_search_locations=[str(self.root)]
            )
        else:
            if not path:
                raise ModuleNotFoundError("selected Forge child has no bound parent path", name=fullname)
            try:
                parents = [_directory(Path(item)) for item in path]
            except (TypeError, SourceError) as exc:
                raise ModuleNotFoundError(
                    "selected Forge parent path is invalid", name=fullname
                ) from exc
            if any(not item.is_relative_to(self.root) for item in parents):
                raise ModuleNotFoundError("selected Forge parent escapes its source", name=fullname)
            if any(
                part == "__pycache__" or Path(part).suffix == ".pyc"
                for parent in parents
                for part in parent.relative_to(self.root).parts
            ):
                raise SourceError(
                    "selected Forge cache directories are excluded from its source binding"
                )
            search = [str(item) for item in parents]
            spec = PathFinder.find_spec(fullname, search, target)
        if spec is None:
            raise ModuleNotFoundError("module is missing from selected Forge source", name=fullname)
        if spec.origin is not None:
            origin = _file(Path(spec.origin), within=self.root)
            if isinstance(spec.loader, SourceFileLoader):
                spec.loader = _SelectedSourceLoader(fullname, str(origin), resource_path=spec.origin)
            elif isinstance(spec.loader, SourcelessFileLoader):
                raise SourceError("selected Forge bytecode has no bound source file")
            elif not isinstance(spec.loader, ExtensionFileLoader):
                raise SourceError("selected Forge module has an unsupported loader")
        elif spec.loader is not None:
            raise SourceError("selected Forge module has no bound origin")
        if spec.submodule_search_locations is not None:
            for location in spec.submodule_search_locations:
                if not _directory(Path(location)).is_relative_to(self.root):
                    raise SourceError("selected Forge namespace escapes its source")
        return spec


def bind_source(source: SourceSelection) -> None:
    if any(name == "code_forge" or name.startswith("code_forge.") for name in sys.modules):
        raise SourceError("Forge is already imported; start a fresh launcher process")
    sys.meta_path.insert(0, _SelectedPackageFinder(source.package_root))


def prepare_cli(workspace: Path, *, env: Mapping[str, str] | None = None) -> CliInvocation:
    cwd = _directory(workspace)
    child_env = sanitized_env(env)
    child_env["PYTHONDONTWRITEBYTECODE"] = "1"
    executable = Path(sys.executable)
    if not executable.is_absolute():
        raise SourceError("running interpreter path is not absolute")
    _file(executable)
    launcher = _file(Path(__file__))
    return CliInvocation((str(executable), "-P", str(launcher), "--cli", "--"), cwd, child_env)


def _managed_package(source: SourceSelection) -> ModuleType:
    sys.dont_write_bytecode = True
    bind_source(source)
    package = importlib.import_module("code_forge")
    expected_init = _file(source.package_root / "__init__.py", within=source.package_root)
    try:
        spec = package.__spec__
        if (
            _file(Path(package.__file__)) != expected_init
            or spec is None
            or spec.origin is None
            or _file(Path(spec.origin)) != expected_init
            or tuple(Path(item).resolve() for item in package.__path__) != (source.package_root,)
            or spec.submodule_search_locations is None
            or tuple(Path(item).resolve() for item in spec.submodule_search_locations)
            != (source.package_root,)
        ):
            raise SourceError("loaded Forge package origin disagrees with selected source")
    except SourceError:
        raise
    except (AttributeError, TypeError, OSError, RuntimeError) as exc:
        raise SourceError("loaded Forge package origin is malformed") from exc
    version = getattr(package, "__version__", None)
    if not isinstance(version, str) or not version or version != source.declared_version:
        raise SourceError("loaded Forge package version disagrees with selected declaration")
    if source.mode == "worktree":
        print(
            f"Forge source: {source.checkout_root} (package {version}; installed {source.installed.installed_version})",
            file=sys.stderr,
        )
    return package


def _entry(stdio: bool) -> int:
    try:
        source = select_source(Path.cwd())
        package = _managed_package(source)
        if stdio:
            package.STARTUP_PID, package.STARTUP_PPID = _STARTUP_PID, _STARTUP_PPID
        module = importlib.import_module("code_forge.mcp_server" if stdio else "code_forge.cli")
        main = getattr(module, "main", None)
        if not callable(main):
            raise SourceError("selected Forge does not provide the required entry function")
        result = main()
        if stdio:
            return 0
        if not isinstance(result, int) or isinstance(result, bool):
            raise SourceError("selected Forge CLI returned an invalid exit code")
        return result
    except (SourceError, ModuleNotFoundError) as exc:
        print(f"Forge source error: {exc}", file=sys.stderr)
        return 2


def cli_main() -> int:
    return _entry(False)


def stdio_main() -> int:
    return _entry(True)


def _worker_main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1:3] == ["--cli", "--"]:
        sys.argv = [sys.argv[0], *sys.argv[3:]]
        return cli_main()
    print("Forge source error: unsupported private launcher invocation", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_worker_main())
