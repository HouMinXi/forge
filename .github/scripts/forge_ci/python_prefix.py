"""Finite private-Python construction and passive provenance checks.

This module is stdlib-only. The authenticated user service owns all process
launches, producer completion, original deadlines, source checks and receipts.
Nothing here imports packages from the prefix or executes an entry point.
"""
from __future__ import annotations

import base64
import configparser
import csv
import ctypes
import errno
import hashlib
import http.client
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import ssl
import stat
import sys
import time
import zipfile

PROVIDER = "/opt/hostedtoolcache/Python/3.12.14/x64/bin/python"
BASE_PREFIX = "/opt/hostedtoolcache/Python/3.12.14/x64"
BASE_REALPATH = BASE_PREFIX + "/bin/python3.12"
VERSION = (3, 12, 14)
PROFILE = "first-B-auth-v3-private-python"
NS = 1_000_000_000
INVENTORY_LIMIT = 8 * 1024 * 1024
FILE_LIMIT = 512 * 1024 * 1024
TOTAL_LIMIT = 2 * 1024 * 1024 * 1024
ENTRY_LIMIT = 50000
PROOF_LIMIT = 16 * 1024
DIST_LIMIT = 4096
RECORD_LIMIT = 1024 * 1024
RECORD_TOTAL = 8 * 1024 * 1024
RECORD_ROW_LIMIT = 16 * 1024
ENTRY_POINTS_LIMIT = 64 * 1024
ENTRY_POINTS_TOTAL = 1024 * 1024
BIN_LIMIT = 256
PIP_VERSION = "26.2.1"
WHEEL_NAME = "pip-26.2.1-py3-none-any.whl"
WHEEL_BYTES = 1816632
WHEEL_SHA256 = "71138adf1f4ca900cdb7d289c21b7494329f2332b6d85f0e1c42108c0384ed3e"
WHEEL_ENTRY_BYTES = 874
WHEEL_ENTRY_SHA256 = "ace651b6b5e30c1cd8db89e26b14e777d6471d62fb07411ac1574ba09de723a7"
WHEEL_HOST = "files.pythonhosted.org"
WHEEL_URL_PATH = "/packages/f3/6e/1736e5b4ae2b778ef2f81c47d797de9f891d4d8acb047a24ca37a60294dd/" + WHEEL_NAME
WHEEL_URL = "https://" + WHEEL_HOST + WHEEL_URL_PATH
PIP_FLAGS = ("--isolated", "--disable-pip-version-check", "--no-input", "--no-cache-dir")
FACTORY_DIRS = ("", "bin", "include", "include/python3.12", "lib", "lib/python3.12", "lib/python3.12/site-packages")
TEMPLATES = {"activate": "common/activate", "Activate.ps1": "common/Activate.ps1",
             "activate.csh": "posix/activate.csh", "activate.fish": "posix/activate.fish"}
FACTORY_FILES = ("pyvenv.cfg", *("bin/" + name for name in TEMPLATES))
TRUSTED_PROVIDER_FILES = {
    "executable": BASE_REALPATH,
    **{member: BASE_PREFIX + "/lib/python3.12/" + member
       for member in ("venv/__init__.py", "venv/__main__.py", "site.py", "sysconfig.py")},
    **{"venv/scripts/" + member: BASE_PREFIX + "/lib/python3.12/venv/scripts/" + member
       for member in TEMPLATES.values()},
}
ALIAS_TARGETS = {"bin/python": PROVIDER, "bin/python3": "python", "bin/python3.12": "python", "lib64": "lib"}
ERRORS = frozenset(("path", "owner", "metadata", "acl", "filesystem", "collision", "identity", "deadline",
                    "factory", "alias", "configuration", "wheel", "download", "inventory", "provenance",
                    "record", "entrypoint", "bound", "proof", "cwd", "system_target"))


class PrefixError(RuntimeError):
    """A closed value-free failure suitable for the existing service gate."""

    def __init__(self, code):
        self.code = code if code in ERRORS else "metadata"
        super().__init__("private Python " + self.code + " rejected")


def _need(condition, code):
    if not condition:
        raise PrefixError(code)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def remaining(deadline_ns):
    _need(type(deadline_ns) is int and 0 < deadline_ns < 2**63, "deadline")
    now = time.monotonic_ns()
    _need(now < deadline_ns, "deadline")
    return (deadline_ns - now) / NS


def _retain(first, later):
    expected = (PrefixError, OSError, http.client.HTTPException)
    first_control = first is not None and (getattr(first, "_forge_control", False) or not isinstance(first, expected))
    later_control = getattr(later, "_forge_control", False) or not isinstance(later, expected)
    return later if first is None or (later_control and not first_control) else first


def _close_all(fds, failure=None):
    for fd in reversed(fds):
        try:
            os.close(fd)
        except BaseException as exc:  # noqa: BLE001 - preserve first control through independent closure
            failure = _retain(failure, exc)
    if failure is not None:
        raise failure


def _absolute(raw):
    _need(type(raw) is str and raw and len(raw.encode("utf-8")) <= 4096 and raw.isprintable() and ":" not in raw, "path")
    path = Path(raw)
    _need(path.is_absolute() and str(path) == raw and all(p not in {".", ".."} for p in path.parts), "path")
    return path


def prefix_root(environment):
    root = _absolute(environment.get("RUNNER_TEMP"))
    run = environment.get("GITHUB_RUN_ID")
    _need(type(run) is str and re.fullmatch(r"[1-9][0-9]{0,18}", run) is not None
          and int(run) < 2**63 and environment.get("GITHUB_RUN_ATTEMPT") == "1", "path")
    return _absolute(str(root / ("forge-b-python-" + run + "-1")))


def python_path(environment):
    return prefix_root(environment) / "bin/python"


def wheel_path(environment):
    return prefix_root(environment) / "bootstrap" / WHEEL_NAME


def site_path(environment):
    return prefix_root(environment) / "lib/python3.12/site-packages"


def system_target(environment):
    return _absolute(environment.get("HOME")) / ".local/lib/python3.12/site-packages"


def factory_argv(environment):
    return [PROVIDER, "-B", "-I", "-S", "-m", "venv", "--symlinks", "--without-pip", str(prefix_root(environment))]


def bootstrap_argv(environment):
    wheel = str(wheel_path(environment))
    return [str(python_path(environment)), "-B", "-I", wheel + "/pip", *PIP_FLAGS,
            "install", "--no-index", "--no-deps", "--no-compile", wheel]


def extras_argv(environment):
    return [str(python_path(environment)), "-B", "-I", "-m", "pip", *PIP_FLAGS,
            "install", "--index-url", "https://pypi.org/simple", "-e", ".[dev,mcp,semgrep,vertex]", "pytest==9.1.1"]


def target_argv(environment):
    return [str(python_path(environment)), "-B", "-I", "-m", "pip", *PIP_FLAGS,
            "install", "--index-url", "https://pypi.org/simple", "--target", str(system_target(environment)), "pytest==9.1.1"]


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _stable_identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)


def _ordinary():
    _need(os.getuid() == os.geteuid() > 0 and os.getgid() == os.getegid() > 0, "owner")


def _acl_absent(fd, deadline_ns):
    for name in ("system.posix_acl_access", "system.posix_acl_default"):
        remaining(deadline_ns)
        try:
            os.getxattr(fd, name)
        except OSError as exc:
            # ENOTSUP/EOPNOTSUPP and permission errors never prove absence.
            _need(exc.errno == errno.ENODATA, "acl")
        else:
            raise PrefixError("acl")
        remaining(deadline_ns)


def _directory_info(info, *, private=False, ancestor=False):
    _need(stat.S_ISDIR(info.st_mode) and info.st_uid in {0, os.getuid()} and info.st_gid in {0, os.getgid()}
          and (not info.st_mode & 0o022 or (ancestor and info.st_uid == 0 and info.st_mode & stat.S_ISVTX)), "metadata")
    if private:
        _need(info.st_uid == os.getuid() and info.st_gid == os.getgid() and stat.S_IMODE(info.st_mode) == 0o700, "owner")


def _open_directory(path, deadline_ns, *, private=False, acl=False):
    """Bounded no-follow ancestor walk, returning only the final descriptor."""
    path = _absolute(str(path))
    _need(len(path.parts) <= 256, "bound")
    fds, result, failure = [], None, None
    try:
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        fds.append(fd)
        _directory_info(os.fstat(fd), ancestor=True)
        for part in path.parts[1:]:
            remaining(deadline_ns)
            before = os.stat(part, dir_fd=fd, follow_symlinks=False)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            fds.append(child)
            _need(_stamp(before) == _stamp(os.fstat(child)), "identity")
            _directory_info(before, ancestor=True)
            fd = child
        _directory_info(os.fstat(fd), private=private)
        if acl:
            _acl_absent(fd, deadline_ns)
        _need(path.resolve(strict=True) == path and _stamp(path.lstat()) == _stamp(os.fstat(fd)), "identity")
        remaining(deadline_ns)
        result = fds.pop()
    except BaseException as exc:  # noqa: BLE001 - retain first control through all independent closes
        failure = exc
    try:
        _close_all(fds, failure)
    except BaseException as exc:
        if result is not None:
            _close_all([result], exc)
        raise
    return result


def _row(path, info):
    return {"path": str(path), "device": info.st_dev, "inode": info.st_ino, "uid": info.st_uid,
            "gid": info.st_gid, "mode": stat.S_IMODE(info.st_mode)}


def _read(path, limit, deadline_ns, *, content=False, owned=False, provider_member=None):
    path = _absolute(str(path))
    _need(provider_member is None or (type(provider_member) is str and provider_member in TRUSTED_PROVIDER_FILES
          and str(path) == TRUSTED_PROVIDER_FILES[provider_member] and owned is False), "metadata")
    remaining(deadline_ns)
    before = path.lstat()
    _need(path.resolve(strict=True) == path and stat.S_ISREG(before.st_mode) and before.st_nlink == 1
          and not before.st_mode & 0o022 and before.st_uid in {0, os.getuid()} and before.st_gid in {0, os.getgid()}
          and 0 <= before.st_size <= min(limit, FILE_LIMIT), "metadata")
    if owned:
        _need(before.st_uid == os.getuid() and before.st_gid == os.getgid(), "owner")
    fd, failure, raw = None, None, bytearray() if content else None
    checksum, total = hashlib.sha256(), 0
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        _need(_stamp(os.fstat(fd)) == _stamp(before), "identity")
        # Preserve the existing provider/stdlib file trust boundary. Only nine
        # exact, fixed source roles omit the new private-prefix ACL requirement;
        # owned=False alone is never an exemption. All old file predicates above
        # and stable no-follow identity checks remain required for these reads.
        if provider_member is None:
            _acl_absent(fd, deadline_ns)
        while True:
            remaining(deadline_ns)
            chunk = os.read(fd, min(65536, limit - total + 1))
            if not chunk:
                break
            total += len(chunk)
            _need(total <= limit and total <= FILE_LIMIT, "bound")
            checksum.update(chunk)
            if raw is not None:
                raw.extend(chunk)
        _need(total == before.st_size and _stamp(os.fstat(fd)) == _stamp(before)
              and _stamp(path.lstat()) == _stamp(before) and path.resolve(strict=True) == path, "identity")
        remaining(deadline_ns)
    except BaseException as exc:  # noqa: BLE001 - retain first control through all independent closes
        failure = exc
    _close_all([] if fd is None else [fd], failure)
    remaining(deadline_ns)
    result = {**_row(path, before), "bytes": total, "sha256": checksum.hexdigest()}
    return (result, bytes(raw)) if content else result


def _local_filesystem(fd):
    # Linux statfs starts with signed long f_type. The overallocated buffer
    # avoids architecture-dependent padding without interpreting other fields.
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, "fstatfs", None)
    _need(function is not None and sys.platform == "linux", "filesystem")
    function.argtypes = (ctypes.c_int, ctypes.c_void_p)
    function.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(256)
    _need(function(fd, buffer) == 0, "filesystem")
    value = ctypes.c_long.from_buffer(buffer).value & 0xFFFFFFFF
    _need(value == 0xEF53, "filesystem")
    return value


def _rename_noreplace(source_fd, source, destination_fd, destination):
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, "renameat2", None)
    _need(function is not None, "filesystem")
    function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    function.restype = ctypes.c_int
    if function(source_fd, os.fsencode(source), destination_fd, os.fsencode(destination), 1) != 0:
        code = ctypes.get_errno()
        raise PrefixError("collision" if code in {errno.EEXIST, errno.ENOTEMPTY} else "filesystem")


def _absent(fd, name):
    try:
        os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise PrefixError("collision")


def _empty_fd(fd, deadline_ns):
    remaining(deadline_ns)
    entries = failure = None
    try:
        entries = os.scandir(fd)
        _need(next(entries, None) is None, "inventory")
    except BaseException as exc:  # noqa: BLE001 - retain first control through iterator close
        failure = _retain(failure, exc)
    finally:
        if entries is not None:
            try:
                entries.close()
            except BaseException as exc:  # noqa: BLE001 - closure cannot replace the first control
                failure = _retain(failure, exc)
    if failure is not None:
        raise failure
    remaining(deadline_ns)


def root_identity(environment, deadline_ns):
    root = prefix_root(environment)
    fd = _open_directory(root, deadline_ns, private=True, acl=True)
    failure = result = None
    try:
        info = os.fstat(fd)
        result = {"role": "private_python", "name_sha256": hashlib.sha256(str(root).encode("utf-8")).hexdigest(),
                  "device": info.st_dev, "inode": info.st_ino, "uid": info.st_uid, "gid": info.st_gid, "mode": 0o700}
    except BaseException as exc:  # noqa: BLE001 - preserve control through descriptor close
        failure = exc
    _close_all([fd], failure)
    remaining(deadline_ns)
    return result


def create_empty_root(environment, deadline_ns):
    """Relocate one newly-created empty inode; retain any unadmitted failure."""
    _ordinary()
    root = prefix_root(environment)
    home = _absolute(environment.get("HOME"))
    source_parent = home / ".local"
    source_name = "." + root.name
    fds, failure, result = [], None, None
    try:
        remaining(deadline_ns)
        source_fd = _open_directory(source_parent, deadline_ns, private=True, acl=True)
        fds.append(source_fd)
        destination_fd = _open_directory(root.parent, deadline_ns)
        fds.append(destination_fd)
        _need(os.fstat(source_fd).st_dev == os.fstat(destination_fd).st_dev
              and _local_filesystem(source_fd) == _local_filesystem(destination_fd), "filesystem")
        _absent(source_fd, source_name)
        _absent(destination_fd, root.name)
        remaining(deadline_ns)
        os.mkdir(source_name, 0o700, dir_fd=source_fd)
        remaining(deadline_ns)
        moved_fd = os.open(source_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=source_fd)
        fds.append(moved_fd)
        initial = os.fstat(moved_fd)
        _directory_info(initial, private=True)
        _need(initial.st_dev == os.fstat(source_fd).st_dev, "identity")
        _acl_absent(moved_fd, deadline_ns)
        _empty_fd(moved_fd, deadline_ns)
        _need(_stamp(os.stat(source_name, dir_fd=source_fd, follow_symlinks=False)) == _stamp(initial), "identity")
        _absent(destination_fd, root.name)
        remaining(deadline_ns)
        _rename_noreplace(source_fd, source_name, destination_fd, root.name)
        remaining(deadline_ns)
        _absent(source_fd, source_name)
        final = os.stat(root.name, dir_fd=destination_fd, follow_symlinks=False)
        _need(_stable_identity(final) == _stable_identity(initial)
              and _stamp(final) == _stamp(os.fstat(moved_fd)), "identity")
        _directory_info(final, private=True)
        _acl_absent(moved_fd, deadline_ns)
        _empty_fd(moved_fd, deadline_ns)
        for fd in (moved_fd, source_fd, destination_fd):
            remaining(deadline_ns)
            os.fsync(fd)
            remaining(deadline_ns)
        _need(_stamp(os.stat(root.name, dir_fd=destination_fd, follow_symlinks=False)) == _stamp(os.fstat(moved_fd)), "identity")
        _need(root.resolve(strict=True) == root and _stamp(root.lstat()) == _stamp(final)
              and _stable_identity(source_parent.lstat()) == _stable_identity(os.fstat(source_fd))
              and _stable_identity(root.parent.lstat()) == _stable_identity(os.fstat(destination_fd)), "identity")
        result = {"role": "private_python", "name_sha256": hashlib.sha256(str(root).encode("utf-8")).hexdigest(),
                  "device": final.st_dev, "inode": final.st_ino, "uid": final.st_uid, "gid": final.st_gid, "mode": 0o700}
    except BaseException as exc:  # noqa: BLE001 - retain first control through all independent closes
        failure = exc
    _close_all(fds, failure)
    remaining(deadline_ns)
    return result


def check_empty_cwd(environment, deadline_ns):
    path = _absolute(environment.get("HOME")) / ".config"
    _need(environment.get("XDG_CONFIG_HOME") == str(path), "cwd")
    fd = _open_directory(path, deadline_ns, private=True, acl=True)
    failure = result = None
    try:
        before = os.fstat(fd)
        _empty_fd(fd, deadline_ns)
        _need(_stamp(before) == _stamp(os.fstat(fd)) == _stamp(path.lstat()), "identity")
        result = _row(path, before)
    except BaseException as exc:  # noqa: BLE001 - retain first control through all independent closes
        failure = exc
    _close_all([fd], failure)
    remaining(deadline_ns)
    return result


def expected_cfg(environment):
    return ("home = " + str(Path(PROVIDER).parent) + "\ninclude-system-site-packages = false\nversion = 3.12.14\n"
            "executable = " + BASE_REALPATH + "\ncommand = " + PROVIDER + " -m venv --without-pip "
            + str(prefix_root(environment)) + "\n").encode("utf-8")


def _template_bytes(raw, name, environment):
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeError:
        raise PrefixError("factory") from None
    root = prefix_root(environment)
    substitutions = {"__VENV_DIR__": str(root), "__VENV_NAME__": root.name,
                     "__VENV_PROMPT__": "(" + root.name + ") ", "__VENV_BIN_NAME__": "bin",
                     "__VENV_PYTHON__": str(python_path(environment))}
    for key, value in substitutions.items():
        quoted = "'" + value.replace("'", "''") + "'" if name.endswith(".ps1") else shlex.quote(value)
        text = text.replace(key, quoted)
    return text.encode("utf-8")


def _factory_inputs(environment, deadline_ns):
    base = _absolute(BASE_PREFIX)
    _need(Path(PROVIDER).resolve(strict=True) == Path(BASE_REALPATH)
          and Path(BASE_REALPATH).resolve(strict=True) == Path(BASE_REALPATH), "factory")
    executable, head = _read(BASE_REALPATH, FILE_LIMIT, deadline_ns, content=True, provider_member="executable")
    _need(head[:4] == b"\x7fELF" and executable["mode"] & 0o111, "factory")
    stdlib = base / "lib/python3.12"
    sources = {}
    for relative in ("venv/__init__.py", "venv/__main__.py", "site.py", "sysconfig.py"):
        sources[relative] = _read(stdlib / relative, 1024 * 1024, deadline_ns, provider_member=relative)
    templates, outputs = {}, {}
    for name, relative in TEMPLATES.items():
        item, raw = _read(stdlib / "venv/scripts" / relative, 64 * 1024, deadline_ns, content=True,
                          provider_member="venv/scripts/" + relative)
        templates[name] = item
        expected = _template_bytes(raw, name, environment)
        outputs["bin/" + name] = {"bytes": len(expected), "sha256": hashlib.sha256(expected).hexdigest(), "mode": item["mode"]}
    cfg = expected_cfg(environment)
    outputs["pyvenv.cfg"] = {"bytes": len(cfg), "sha256": hashlib.sha256(cfg).hexdigest()}
    return {"constructor": PROVIDER, "base_realpath": BASE_REALPATH, "version": list(VERSION), "executable": executable,
            "sources": sources, "templates": templates, "outputs": outputs}


def constructor_inputs(environment, deadline_ns):
    _need(tuple(sys.version_info[:3]) == VERSION and sys.executable == PROVIDER and sys._base_executable == PROVIDER
          and sys.prefix == sys.base_prefix == BASE_PREFIX and sys.exec_prefix == sys.base_exec_prefix == BASE_PREFIX
          and sys.flags.isolated == 1 and sys.flags.no_site == 1 and sys.dont_write_bytecode, "factory")
    check_empty_cwd(environment, deadline_ns)
    return _factory_inputs(environment, deadline_ns)


def _alias_rows(environment, deadline_ns):
    root, result = prefix_root(environment), {}
    for relative, target in ALIAS_TARGETS.items():
        path = root / relative
        remaining(deadline_ns)
        info = path.lstat()
        _need(stat.S_ISLNK(info.st_mode) and info.st_uid == os.getuid() and info.st_gid == os.getgid()
              and info.st_nlink == 1 and os.readlink(path) == target, "alias")
        expected = root / "lib" if relative == "lib64" else Path(BASE_REALPATH)
        _need(path.resolve(strict=True) == expected and expected.resolve(strict=True) == expected
              and _stamp(path.lstat()) == _stamp(info) and os.readlink(path) == target, "alias")
        result[relative] = {**_row(path, info), "target": target, "realpath": str(expected)}
    return result


def _walk(root, deadline_ns, *, aliases=None):
    root = _absolute(str(root))
    aliases = {} if aliases is None else aliases
    files, directories, saved, total = {}, {}, {}, 0
    stack = [root]
    while stack:
        remaining(deadline_ns)
        path = stack.pop()
        relative = path.relative_to(root).as_posix()
        relative = "" if relative == "." else relative
        _need(len(files) + len(directories) + len(aliases) < ENTRY_LIMIT, "bound")
        if relative in aliases:
            continue
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            fd = _open_directory(path, deadline_ns, private=(path == root), acl=True)
            failure = None
            try:
                _need(_stamp(info) == _stamp(os.fstat(fd)), "identity")
                directories[str(path)] = _row(path, info)
                saved[str(path)] = _stamp(info)
                names, entries, scan_failure = [], None, None
                try:
                    entries = os.scandir(fd)
                    for entry in entries:
                        remaining(deadline_ns)
                        _need(len(names) + len(files) + len(directories) + len(stack) < ENTRY_LIMIT, "bound")
                        _need(entry.name not in {".", ".."} and len(os.fsencode(entry.name)) <= 255, "path")
                        names.append(entry.name)
                except BaseException as exc:  # noqa: BLE001 - retain first control before independent closure
                    scan_failure = _retain(scan_failure, exc)
                finally:
                    if entries is not None:
                        try:
                            entries.close()
                        except BaseException as exc:  # noqa: BLE001 - do not replace an earlier control
                            scan_failure = _retain(scan_failure, exc)
                if scan_failure is not None:
                    raise scan_failure
                stack.extend(path / name for name in sorted(names, reverse=True))
            except BaseException as exc:  # noqa: BLE001 - retain first control through all independent closes
                failure = exc
            _close_all([fd], failure)
        else:
            item = _read(path, FILE_LIMIT, deadline_ns, owned=True)
            total += item["bytes"]
            _need(total <= TOTAL_LIMIT, "bound")
            files[str(path)] = item
    for name, stamp in saved.items():
        remaining(deadline_ns)
        path = Path(name)
        _need(path.resolve(strict=True) == path and _stamp(path.lstat()) == stamp, "identity")
    total += sum(len(item["target"].encode("utf-8")) for item in aliases.values())
    _need(len(files) + len(directories) + len(aliases) <= ENTRY_LIMIT and total <= TOTAL_LIMIT, "bound")
    return {"directories": [directories[name] for name in sorted(directories)], "files": [files[name] for name in sorted(files)]}


def _factory_outputs(environment, inventory, inputs):
    root = prefix_root(environment)
    files = {item["path"]: item for item in inventory["files"]}
    for relative, expected in inputs["outputs"].items():
        actual = files.get(str(root / relative))
        _need(actual is not None and all(actual[key] == value for key, value in expected.items()), "factory")
    for forbidden in ("bin/pyvenv.cfg", "bin/python._pth", "bin/python3._pth", "bin/python3.12._pth"):
        _need(str(root / forbidden) not in files, "configuration")


def validate_factory(environment, inputs, deadline_ns, *, wheel=False):
    _need(inputs == _factory_inputs(environment, deadline_ns), "factory")
    aliases = _alias_rows(environment, deadline_ns)
    inventory = _walk(prefix_root(environment), deadline_ns, aliases=aliases)
    root = prefix_root(environment)
    expected_directories = set(FACTORY_DIRS) | ({"bootstrap"} if wheel else set())
    expected_files = set(FACTORY_FILES) | ({"bootstrap/" + WHEEL_NAME} if wheel else set())
    _need({"" if item["path"] == str(root)
           else Path(item["path"]).relative_to(root).as_posix() for item in inventory["directories"]} == expected_directories
          and {Path(item["path"]).relative_to(root).as_posix() for item in inventory["files"]} == expected_files, "factory")
    _factory_outputs(environment, inventory, inputs)
    if wheel:
        verify_wheel(environment, deadline_ns)
    result = {"schema_version": 1, "root_identity": root_identity(environment, deadline_ns), **inventory,
              "aliases": aliases, "bin_origins": _factory_origins(environment)}
    _need(len(canonical(result)) <= INVENTORY_LIMIT, "bound")
    remaining(deadline_ns)
    return result


def _factory_origins(environment):
    root = prefix_root(environment)
    return {str(root / relative): {"kind": "factory", "member": relative}
            for relative in (*("bin/" + name for name in TEMPLATES), "bin/python", "bin/python3", "bin/python3.12")}


def verify_wheel(environment, deadline_ns):
    item = _read(wheel_path(environment), WHEEL_BYTES, deadline_ns, owned=True)
    _need(item["bytes"] == WHEEL_BYTES and item["sha256"] == WHEEL_SHA256 and item["mode"] == 0o600, "wheel")
    return item


def download_worker(environment, deadline_ns):
    """Only the fixed anonymous wheel GET; US supplies its original work cutoff."""
    remaining(deadline_ns)
    root = prefix_root(environment)
    _need(deadline_ns - time.monotonic_ns() <= 30 * NS, "deadline")
    root_identity(environment, deadline_ns)
    destination = root / "bootstrap"
    _need(not os.path.lexists(destination), "collision")
    connection = http.client.HTTPSConnection(WHEEL_HOST, timeout=min(10, remaining(deadline_ns)), context=ssl.create_default_context())
    fd = response = directory_fd = failure = result = None
    try:
        os.mkdir(destination, 0o700)
        directory_fd = _open_directory(destination, deadline_ns, private=True, acl=True)
        _empty_fd(directory_fd, deadline_ns)
        connection.connect()
        connection.sock.settimeout(min(10, remaining(deadline_ns)))
        connection.request("GET", WHEEL_URL_PATH, headers={"Accept-Encoding": "identity", "Connection": "close"})
        response = connection.getresponse()
        lengths = response.headers.get_all("Content-Length", [])
        encodings = response.headers.get_all("Content-Encoding", [])
        _need(response.status == 200 and lengths == [str(WHEEL_BYTES)] and encodings in ([], ["identity"])
              and response.headers.get_all("Transfer-Encoding", []) == [], "download")
        # Require actual EOF on the requested close-delimited connection;
        # the library otherwise hides surplus bytes after Content-Length.
        response.length = None
        fd = os.open(WHEEL_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=directory_fd)
        initial = os.fstat(fd)
        _need(stat.S_ISREG(initial.st_mode) and initial.st_nlink == 1 and initial.st_size == 0
              and initial.st_uid == os.getuid() and initial.st_gid == os.getgid()
              and stat.S_IMODE(initial.st_mode) == 0o600, "metadata")
        _acl_absent(fd, deadline_ns)
        checksum, total = hashlib.sha256(), 0
        while True:
            remaining(deadline_ns)
            if connection.sock is not None:
                connection.sock.settimeout(min(10, remaining(deadline_ns)))
            chunk = response.read1(65536)
            remaining(deadline_ns)
            if not chunk:
                break
            total += len(chunk)
            _need(total <= WHEEL_BYTES, "download")
            checksum.update(chunk)
            view = memoryview(chunk)
            while view:
                remaining(deadline_ns)
                count = os.write(fd, view)
                _need(count > 0, "download")
                view = view[count:]
        _need(total == WHEEL_BYTES and checksum.hexdigest() == WHEEL_SHA256, "wheel")
        os.fsync(fd)
        remaining(deadline_ns)
        after = os.fstat(fd)
        _need(_stable_identity(after) == _stable_identity(initial) and after.st_size == total and after.st_nlink == 1
              and _stamp(after) == _stamp(os.stat(WHEEL_NAME, dir_fd=directory_fd, follow_symlinks=False)), "identity")
        os.fsync(directory_fd)
        remaining(deadline_ns)
    except BaseException as exc:  # noqa: BLE001 - retain first control through all independent closes
        failure = _retain(failure, exc)
    finally:
        # Close the output, response, socket and directory independently. A
        # successful provisional hash does not mask failed/late persistence.
        for obj, kind in ((fd, "fd"), (response, "object"), (connection, "object"), (directory_fd, "fd")):
            if obj is not None:
                try:
                    if kind == "fd":
                        os.close(obj)
                    else:
                        obj.close()
                except BaseException as exc:  # noqa: BLE001 - retain first control through all independent closes
                    failure = _retain(failure, exc)
    if failure is not None:
        raise failure
    remaining(deadline_ns)
    result = verify_wheel(environment, deadline_ns)
    remaining(deadline_ns)
    return result


def _decode(raw, code):
    try:
        return raw.decode("utf-8", "strict")
    except UnicodeError:
        raise PrefixError(code) from None


def _record_rows(raw):
    """Bound each encoded physical row before CSV gets any attacker data."""
    _need(len(raw) <= RECORD_LIMIT and raw and raw.endswith(b"\n") and b"\x00" not in raw, "record")
    result = []
    for encoded in raw.splitlines(keepends=True):
        _need(len(encoded) <= RECORD_ROW_LIMIT and encoded.endswith(b"\n"), "bound")
        try:
            rows = list(csv.reader([_decode(encoded, "record")], strict=True))
        except (csv.Error, UnicodeError):
            raise PrefixError("record") from None
        _need(len(rows) == 1 and len(rows[0]) == 3, "record")
        result.append(rows[0])
        _need(len(result) <= ENTRY_LIMIT, "bound")
    return result


def _record_destination(root, site, raw):
    _need(type(raw) is str and raw and len(raw.encode("utf-8")) <= 4096 and raw.isprintable()
          and "\\" not in raw and not raw.startswith("/") and ":" not in raw, "record")
    parts = list(site.relative_to(root).parts)
    for part in raw.split("/"):
        _need(part not in {"", "."}, "record")
        if part == "..":
            _need(parts, "record")
            parts.pop()
        else:
            parts.append(part)
    _need(parts, "record")
    return root.joinpath(*parts)


def _distribution_name(raw):
    _need(type(raw) is str and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", raw) is not None, "provenance")
    return re.sub(r"[-_.]+", "-", raw).lower()


def _metadata(raw):
    # Header data only. Repeated unrelated standard headers (Requires-Dist,
    # Classifier, Project-URL) are legal; Name and Version must be unique.
    text = _decode(raw, "provenance")
    _need("\x00" not in text and "\r" not in text, "provenance")
    values = {"name": [], "version": []}
    previous_key = None
    for line in text.split("\n\n", 1)[0].splitlines():
        if line.startswith((" ", "\t")):
            _need(previous_key is not None and previous_key not in values, "provenance")
            continue
        _need(":" in line, "provenance")
        key, value = line.split(":", 1)
        _need(re.fullmatch(r"[A-Za-z0-9-]+", key) is not None, "provenance")
        previous_key = key.lower()
        if previous_key in values:
            values[previous_key].append(value.strip())
    _need(all(len(items) == 1 for items in values.values()), "provenance")
    name = _distribution_name(values["name"][0])
    version = values["version"][0]
    _need(version and len(version) <= 256 and version.isascii() and version.isprintable(), "provenance")
    return name, version


def _entry_points(raw):
    _need(len(raw) <= ENTRY_POINTS_LIMIT and b"\x00" not in raw, "bound")
    parser = configparser.ConfigParser(interpolation=None, strict=True, delimiters=("=",),
                                       empty_lines_in_values=False)
    parser.optionxform = str
    try:
        parser.read_string(_decode(raw, "entrypoint"))
    except configparser.Error:
        raise PrefixError("entrypoint") from None
    _need(not parser.defaults(), "entrypoint")
    output = {}
    for group in parser.sections():
        _need(re.fullmatch(r"[A-Za-z0-9_.-]+", group) is not None, "entrypoint")
        for name, value in parser.items(group, raw=True):
            _need(name and len(name.encode("utf-8")) <= 255 and "/" not in name and "\\" not in name
                  and name not in {".", ".."} and name.isprintable() and "\n" not in value, "entrypoint")
            if group in {"console_scripts", "gui_scripts"}:
                match = re.fullmatch(r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*):([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)(?:\s*\[[A-Za-z0-9_, .-]+\])?", value, re.ASCII)
                _need(match is not None and name not in output, "entrypoint")
                output[name] = match[1] + ":" + match[2]
    return output


def _script_bytes(interpreter, entry_point):
    module, function = entry_point.split(":")
    executable = interpreter.encode("utf-8")
    # Stock pip26 distlib uses the POSIX /bin/sh trampoline when a direct
    # shebang is too long or contains spaces. Both forms name exactly Q.
    if len(executable) + 3 <= 127 and b" " not in executable:
        shebang = b"#!" + executable + b"\n"
    else:
        if b" " in executable:
            executable = b'"' + executable + b'"'
        shebang = b"#!/bin/sh\n'''exec' " + executable + b' "$0" "$@"\n\' \'\'\'\n'
    body = ("import sys\nfrom " + module + " import " + function.split(".")[0]
            + "\nif __name__ == '__main__':\n    sys.argv[0] = sys.argv[0].removesuffix('.exe')\n"
            "    sys.exit(" + function + "())\n").encode("utf-8")
    return shebang + body


def _provenance(root, site, inventory, deadline_ns, *, interpreter, factory_origins=None, system_target_layout=False):
    files = {item["path"]: item for item in inventory["files"]}
    directories = {item["path"] for item in inventory["directories"]}
    distributions = [Path(name) for name in sorted(directories)
                     if Path(name).parent == site and name.endswith(".dist-info")]
    _need(len(distributions) <= DIST_LIMIT, "bound")
    owners, distributions_by_name, entries_by_name = {}, {}, {}
    row_count = record_bytes = entry_bytes = 0
    for dist in distributions:
        remaining(deadline_ns)
        record = dist / "RECORD"
        metadata = dist / "METADATA"
        _need(str(record) in files and str(metadata) in files, "provenance")
        meta_row, raw = _read(metadata, RECORD_LIMIT, deadline_ns, content=True, owned=True)
        _need(meta_row == files[str(metadata)], "identity")
        name, version = _metadata(raw)
        _need(name not in distributions_by_name, "provenance")
        # Dist-info spelling carries the same normalized project identity.
        _need(_distribution_name(dist.name[:-10].rsplit("-", 1)[0]) == name, "provenance")
        item, raw = _read(record, RECORD_LIMIT, deadline_ns, content=True, owned=True)
        _need(item == files[str(record)], "identity")
        record_bytes += len(raw)
        _need(record_bytes <= RECORD_TOTAL, "bound")
        rows = _record_rows(raw)
        row_count += len(rows)
        _need(row_count <= ENTRY_LIMIT, "bound")
        owned_paths = set()
        for spelling, hash_value, size in rows:
            if system_target_layout and spelling.startswith("../../bin/"):
                # pip's fixed --target flow installs into a temporary posix_home
                # scheme, then moves its bin next to its package payload without
                # rewriting RECORD. Only that exact stock relocation is mapped.
                tail = spelling.removeprefix("../../bin/")
                _need(tail and "/" not in tail and "\\" not in tail and tail not in {".", ".."}
                      and tail.isprintable() and ":" not in tail and len(tail.encode("utf-8")) <= 255, "record")
                destination = root / "bin" / tail
            else:
                destination = _record_destination(root, site, spelling)
            path = str(destination)
            _need(path in files and path not in owners, "provenance")
            actual = files[path]
            if hash_value:
                _need(re.fullmatch(r"sha256=[A-Za-z0-9_-]{43}", hash_value) is not None
                      and re.fullmatch(r"0|[1-9][0-9]{0,18}", size) is not None, "record")
                encoded = base64.urlsafe_b64encode(bytes.fromhex(actual["sha256"])).rstrip(b"=").decode("ascii")
                _need(hash_value == "sha256=" + encoded and int(size) == actual["bytes"], "record")
            else:
                _need(size == "" and (destination == record or destination.suffix == ".pyc"), "record")
            owners[path] = {"distribution": name, "record": str(record), "record_path": spelling}
            owned_paths.add(path)
        _need(str(record) in owned_paths and str(metadata) in owned_paths, "provenance")
        ep_path = str(dist / "entry_points.txt")
        entry_points = {}
        if ep_path in files:
            row, raw = _read(ep_path, ENTRY_POINTS_LIMIT, deadline_ns, content=True, owned=True)
            _need(row == files[ep_path] and ep_path in owned_paths, "identity")
            entry_bytes += len(raw)
            _need(entry_bytes <= ENTRY_POINTS_TOTAL, "bound")
            entry_points = _entry_points(raw)
        # pip's stock installer synthesizes the current minor-version alias.
        if name == "pip":
            _need(version == PIP_VERSION and entry_points.get("pip") == "pip._internal.cli.main:main"
                  and entry_points.get("pip3") == "pip._internal.cli.main:main", "provenance")
            entry_points["pip3.12"] = entry_points["pip"]
        for command, value in entry_points.items():
            _need(command not in entries_by_name, "provenance")
            entries_by_name[command] = (name, value)
        distributions_by_name[name] = {"version": version, "directory": str(dist), "record_sha256": files[str(record)]["sha256"]}
    bin_root = root / "bin"
    bin_members = {path for path in files if Path(path).parent == bin_root}
    origins = {} if factory_origins is None else dict(factory_origins)
    _need(len(bin_members | set(origins)) <= BIN_LIMIT, "bound")
    # No nested bin directories, hidden aliases or startup settings are admitted.
    _need(not any(Path(path).is_relative_to(bin_root) and Path(path) != bin_root for path in directories), "provenance")
    for path in sorted(bin_members):
        if path in origins:
            _need(path not in owners, "provenance")
            continue
        _need(path in owners, "provenance")
        origin = owners[path]
        command = Path(path).name
        entry = entries_by_name.get(command)
        _need(files[path]["mode"] & 0o111 and not files[path]["mode"] & 0o7000, "entrypoint")
        row, raw = _read(path, FILE_LIMIT, deadline_ns, content=True, owned=True)
        _need(row == files[path], "identity")
        if entry is not None:
            _need(entry[0] == origin["distribution"] and raw == _script_bytes(interpreter, entry[1]), "entrypoint")
        else:
            # A wheel may ship native ELF payloads or standard shell scripts.
            # Python script payloads must use this exact private interpreter.
            shebang = raw.split(b"\n", 1)[0]
            _need(raw.startswith(b"\x7fELF") or shebang in {
                ("#!" + interpreter).encode("utf-8"), b"#!/bin/sh", b"#!/bin/bash", b"#!/usr/bin/bash"}, "entrypoint")
        origins[path] = {"kind": "distribution", **origin, "entry_point": None if entry is None else entry[1],
                         "payload_sha256": row["sha256"]}
    # Every installed file, including startup hooks and generated pyc, has an
    # owner. Independent inventory hashes remain authoritative for empty hashes.
    for path in files:
        if Path(path).is_relative_to(site):
            _need(path in owners, "provenance")
    for command, (name, _) in entries_by_name.items():
        endpoint = str(bin_root / command)
        _need(endpoint in origins and origins[endpoint].get("distribution") == name, "provenance")
    remaining(deadline_ns)
    return {"bin_origins": {name: origins[name] for name in sorted(origins)}, "distributions": distributions_by_name,
            "owners": owners, "counts": {"distributions": len(distributions), "record_rows": row_count,
            "record_bytes": record_bytes, "entry_points_bytes": entry_bytes, "bin_members": len(bin_members | set(origins))}}


def prefix_inventory(environment, deadline_ns):
    _ordinary()
    root = prefix_root(environment)
    inputs = _factory_inputs(environment, deadline_ns)
    aliases = _alias_rows(environment, deadline_ns)
    inventory = _walk(root, deadline_ns, aliases=aliases)
    _factory_outputs(environment, inventory, inputs)
    verify_wheel(environment, deadline_ns)
    allowed_root = {"bin", "include", "lib", "bootstrap", "pyvenv.cfg", "lib64"}
    _need({Path(item["path"]).relative_to(root).parts[0] for item in [*inventory["directories"], *inventory["files"]]
           if item["path"] != str(root)} <= allowed_root, "inventory")
    # No include output, additional bootstrap files or alternative site roots.
    allowed_dirs = {str(root / value) for value in FACTORY_DIRS} | {str(root / "bootstrap")}
    site = site_path(environment)
    _need(all(item["path"] in allowed_dirs or Path(item["path"]).is_relative_to(site) for item in inventory["directories"]), "inventory")
    fixed = {str(root / value) for value in FACTORY_FILES} | {str(wheel_path(environment))}
    _need(all(item["path"] in fixed or Path(item["path"]).parent == root / "bin" or Path(item["path"]).is_relative_to(site)
              for item in inventory["files"]), "inventory")
    provenance = _provenance(root, site, inventory, deadline_ns, interpreter=str(python_path(environment)),
                             factory_origins=_factory_origins(environment))
    _need("pip" in provenance["distributions"], "provenance")
    result = {"schema_version": 1, "root_identity": root_identity(environment, deadline_ns), **inventory,
              "aliases": aliases, "bin_origins": provenance["bin_origins"]}
    _need(len(canonical(result)) <= INVENTORY_LIMIT, "bound")
    remaining(deadline_ns)
    return result


def _strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        _need(key not in result, "record")
        result[key] = value
    return result


def _json_data(raw):
    def nonfinite(_):
        raise PrefixError("record")
    try:
        return json.loads(_decode(raw, "record"), object_pairs_hook=_strict_pairs, parse_constant=nonfinite)
    except (ValueError, TypeError):
        raise PrefixError("record") from None


def validate_pip_stage(environment, factory, deadline_ns):
    _need(factory == _factory_inputs(environment, deadline_ns), "factory")
    inventory = prefix_inventory(environment, deadline_ns)
    root, site, wheel = prefix_root(environment), site_path(environment), wheel_path(environment)
    files = {item["path"]: item for item in inventory["files"]}
    provenance = _provenance(root, site, inventory, deadline_ns, interpreter=str(python_path(environment)),
                             factory_origins=_factory_origins(environment))
    _need(set(provenance["distributions"]) == {"pip"}, "provenance")
    wheel_row, raw_wheel = _read(wheel, WHEEL_BYTES, deadline_ns, content=True, owned=True)
    _need(wheel_row["sha256"] == WHEEL_SHA256, "wheel")
    expected, total = set(), 0
    buffer, archive, failure = io.BytesIO(raw_wheel), None, None
    try:
        archive = zipfile.ZipFile(buffer)
        members = archive.infolist()
        _need(len(members) <= ENTRY_LIMIT, "bound")
        for member in members:
            remaining(deadline_ns)
            name = member.filename
            _need(name and not member.is_dir() and not member.flag_bits & 1 and "\\" not in name
                  and str(PurePosixPath(name)) == name and all(p not in {".", ".."} for p in PurePosixPath(name).parts)
                  and not name.startswith("/") and name not in expected, "wheel")
            mode = member.external_attr >> 16
            _need(not stat.S_ISLNK(mode) and member.file_size <= FILE_LIMIT, "wheel")
            expected.add(name)
            total += member.file_size
            _need(total <= TOTAL_LIMIT and (name.startswith("pip/") or name.startswith("pip-26.2.1.dist-info/")), "wheel")
            destination = str(site / name)
            _need(destination in files, "provenance")
            if name != "pip-26.2.1.dist-info/RECORD":
                content = archive.read(member)
                _need(len(content) == member.file_size and files[destination]["bytes"] == member.file_size
                      and files[destination]["sha256"] == hashlib.sha256(content).hexdigest(), "wheel")
                if name == "pip/__main__.py":
                    _need(len(content) == WHEEL_ENTRY_BYTES and hashlib.sha256(content).hexdigest() == WHEEL_ENTRY_SHA256, "wheel")
    except BaseException as exc:  # noqa: BLE001 - preserve first control across archive and buffer close
        failure = _retain(failure, exc)
    finally:
        for resource in (archive, buffer):
            if resource is not None:
                try:
                    resource.close()
                except BaseException as exc:  # noqa: BLE001 - attempt both independent closes
                    failure = _retain(failure, exc)
    if failure is not None:
        raise failure
    remaining(deadline_ns)
    additional = {"pip-26.2.1.dist-info/INSTALLER", "pip-26.2.1.dist-info/REQUESTED", "pip-26.2.1.dist-info/direct_url.json"}
    actual = {str(Path(path).relative_to(site)) for path in files if Path(path).is_relative_to(site)}
    _need(actual == expected | additional, "provenance")
    for name, value in (("INSTALLER", b"pip\n"), ("REQUESTED", b"")):
        _, raw = _read(site / "pip-26.2.1.dist-info" / name, 64, deadline_ns, content=True, owned=True)
        _need(raw == value, "provenance")
    _, raw = _read(site / "pip-26.2.1.dist-info/direct_url.json", 16 * 1024, deadline_ns, content=True, owned=True)
    direct = _json_data(raw)
    _need(direct == {"url": wheel.as_uri(), "archive_info": {"hash": "sha256=" + WHEEL_SHA256,
                                                          "hashes": {"sha256": WHEEL_SHA256}}}, "provenance")
    bin_names = {Path(path).name for path in inventory["bin_origins"]}
    _need(bin_names == {*TEMPLATES, "python", "python3", "python3.12", "pip", "pip3", "pip3.12"}, "provenance")
    remaining(deadline_ns)
    return inventory


def validate_system_target(environment, deadline_ns, *, empty=False):
    target = system_target(environment)
    home = _absolute(environment.get("HOME"))
    _ordinary()
    # Validate each existing component without resolving through aliases. An
    # absent target is acceptable only while all existing ancestors stay safe.
    for path in (home, home / ".local", home / ".local/lib", home / ".local/lib/python3.12", target):
        remaining(deadline_ns)
        if not os.path.lexists(path):
            _need(empty, "system_target")
            return {"path": str(target), "absent": True, "inventory": None}
        fd = _open_directory(path, deadline_ns, private=True, acl=True)
        failure = None
        try:
            if path != target:
                for forbidden in ("sitecustomize.py", "usercustomize.py", "python._pth", "python3._pth", "python3.12._pth", "pyvenv.cfg"):
                    _need(not os.path.lexists(path / forbidden), "system_target")
            elif empty:
                _empty_fd(fd, deadline_ns)
        except BaseException as exc:  # noqa: BLE001 - retain first control through all independent closes
            failure = exc
        _close_all([fd], failure)
    if empty:
        remaining(deadline_ns)
        return {"path": str(target), "absent": False, "inventory": None}
    inventory = _walk(target, deadline_ns)
    provenance = _provenance(target, target, inventory, deadline_ns, interpreter=str(python_path(environment)), system_target_layout=True)
    _need(set(provenance["distributions"]) == {"pytest", "iniconfig", "packaging", "pluggy", "pygments"}
          and provenance["distributions"]["pytest"]["version"] == "9.1.1", "system_target")
    result = {"path": str(target), "absent": False,
              "inventory": {**inventory, "bin_origins": provenance["bin_origins"]}}
    _need(len(canonical(result)) <= INVENTORY_LIMIT, "bound")
    remaining(deadline_ns)
    return result


def _keys(value, expected):
    _need(type(value) is dict and value.keys() == set(expected), "proof")


def _hash(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def wheel_worker_argv(environment, deadline_ns, binding):
    helper = _absolute(environment.get("GITHUB_WORKSPACE")) / ".github/scripts/forge_ci/user_service.py"
    _need(type(deadline_ns) is int and 0 < deadline_ns < 2**63 and _hash(binding.get("helper_map_sha256")), "proof")
    return [PROVIDER, "-B", "-I", "-S", str(helper), "python-wheel-worker", "--deadline-ns", str(deadline_ns),
            "--helper-map-sha256", binding["helper_map_sha256"]]


def validate_provision(record, environment, binding):
    """Closed data validation; producer success and expected binding come from US."""
    _keys(record, ("schema_version", "kind", "profile", "binding", "root_identity", "factory", "pip", "stages"))
    _need(type(record["schema_version"]) is int and record["schema_version"] == 1
          and record["kind"] == "private-python-bootstrap" and record["profile"] == PROFILE, "proof")
    _keys(binding, ("run_id", "run_attempt", "candidate_sha", "workflow_sha", "workflow_job", "boot_id",
                    "launch_receipt_sha256", "helper_map_sha256"))
    _keys(record["binding"], binding.keys())
    _need(canonical(record["binding"]) == canonical(binding) and type(binding["run_id"]) is int and 0 < binding["run_id"] < 2**63
          and type(binding["run_attempt"]) is int and binding["run_attempt"] == 1
          and str(binding["run_id"]) == environment.get("GITHUB_RUN_ID")
          and environment.get("GITHUB_RUN_ATTEMPT") == "1"
          and binding["candidate_sha"] == binding["workflow_sha"] == environment.get("GITHUB_SHA") == environment.get("GITHUB_WORKFLOW_SHA")
          and type(binding["candidate_sha"]) is str and re.fullmatch(r"[0-9a-f]{40}", binding["candidate_sha"]) is not None
          and binding["candidate_sha"] != "0" * 40
          and binding["workflow_job"] == environment.get("GITHUB_JOB") == "linux-tests"
          and type(binding["boot_id"]) is str and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", binding["boot_id"]) is not None
          and all(_hash(binding[key]) for key in ("launch_receipt_sha256", "helper_map_sha256")), "proof")
    root = record["root_identity"]
    _keys(root, ("role", "name_sha256", "device", "inode", "uid", "gid", "mode"))
    _need(root["role"] == "private_python" and root["name_sha256"] == hashlib.sha256(str(prefix_root(environment)).encode("utf-8")).hexdigest()
          and type(root["mode"]) is int and root["mode"] == 0o700
          and all(type(root[key]) is int and 0 <= root[key] < 2**64 for key in ("device", "inode"))
          and all(type(root[key]) is int and 0 < root[key] < 2**32 for key in ("uid", "gid"))
          and root["uid"] == os.getuid() and root["gid"] == os.getgid(), "proof")
    factory = record["factory"]
    _keys(factory, ("constructor", "base_realpath", "version", "cfg_sha256", "templates_sha256", "manifest_sha256"))
    _keys(factory["templates_sha256"], TEMPLATES.keys())
    _need(factory["constructor"] == PROVIDER and factory["base_realpath"] == BASE_REALPATH
          and type(factory["version"]) is list and len(factory["version"]) == 3
          and all(type(number) is int for number in factory["version"]) and factory["version"] == list(VERSION)
          and factory["cfg_sha256"] == hashlib.sha256(expected_cfg(environment)).hexdigest()
          and _hash(factory["manifest_sha256"]) and all(_hash(value) for value in factory["templates_sha256"].values()), "proof")
    pip = record["pip"]
    _keys(pip, ("version", "wheel_bytes", "wheel_sha256", "entrypoint_sha256", "installed_manifest_sha256"))
    _need(pip["version"] == PIP_VERSION and type(pip["wheel_bytes"]) is int and pip["wheel_bytes"] == WHEEL_BYTES
          and pip["wheel_sha256"] == WHEEL_SHA256 and pip["entrypoint_sha256"] == WHEEL_ENTRY_SHA256
          and _hash(pip["installed_manifest_sha256"]), "proof")
    stages = record["stages"]
    _need(type(stages) is list and len(stages) == 4, "proof")
    previous = None
    for stage, name in zip(stages, ("root", "factory", "wheel", "bootstrap"), strict=True):
        _keys(stage, ("name", "started_ns", "finished_ns", "deadline_ns", "argv_sha256", "input_sha256", "output_sha256", "returncode", "direct_child_reaped"))
        _need(stage["name"] == name and all(type(stage[key]) is int and 0 < stage[key] < 2**63
                                            for key in ("started_ns", "finished_ns", "deadline_ns"))
              and _hash(stage["input_sha256"]) and _hash(stage["output_sha256"]), "proof")
        reserve = 5 * NS if name == "root" else 2 * NS
        _need(stage["started_ns"] < stage["finished_ns"] < stage["deadline_ns"] - reserve
              and stage["deadline_ns"] - stage["started_ns"] <= (180 * NS if name == "root" else 30 * NS), "proof")
        if name == "root":
            _need(stage["argv_sha256"] is None and stage["returncode"] is None and stage["direct_child_reaped"] is None, "proof")
        else:
            _need(stage["started_ns"] >= previous["finished_ns"] and stage["input_sha256"] == previous["output_sha256"]
                  and stage["deadline_ns"] <= stages[0]["deadline_ns"] - 5 * NS
                  and type(stage["returncode"]) is int and stage["returncode"] == 0
                  and stage["direct_child_reaped"] is True, "proof")
            argv = factory_argv(environment) if name == "factory" else (bootstrap_argv(environment) if name == "bootstrap"
                    else wheel_worker_argv(environment, stage["deadline_ns"] - 2 * NS, binding))
            _need(stage["argv_sha256"] == digest(argv), "proof")
        previous = stage
    _need(factory["manifest_sha256"] == stages[1]["output_sha256"]
          and pip["installed_manifest_sha256"] == stages[3]["output_sha256"]
          and len(canonical(record)) <= PROOF_LIMIT, "proof")
