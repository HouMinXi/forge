"""One fixed first-B observation; never a baseline guard or qualification.

Only the live controller creates a context after source and installer admission.
The service owns runtime admission; this module does not authorize discovered
packages. Finalization's alarm, original clocks and terminal receipt belong to
the controller. All operations here consume its absolute deadline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import time
from typing import Any

from . import launch, user_service

SPEC_SHA256 = "52f595d6c254b4e89c7e08329e3a9b16663c4e4a72565693d03ca72717663177"
PROFILE = "first-B-auth-v1"
ARGV = ("python3", "-m", "pytest", "-q", "--ignore=tests/test_cli_integration.py")
TIMEOUT_SECONDS, OWNER_ENVELOPE_SECONDS, FINAL_SECONDS = 2400, 30, 360
LATEST_START_SECONDS, SERVICE_GRACE_SECONDS = 4200, 6990
JOB_SECONDS, CLEANUP_SECONDS, ARTIFACT_SECONDS = 9000, 60, 300
NS = 1_000_000_000
OUTPUT_LIMIT = 1048576
FILE_LIMITS = {
    "stdout.bin": OUTPUT_LIMIT, "stderr.bin": OUTPUT_LIMIT,
    "owner.json": 262144, "result.json": 32768,
    "generated-before.json": 262144, "generated-after.json": 262144,
    "runtime.json": 65536, "environment.json": 32768, "manifest.json": 32768,
}
# stdout/stderr share ONE allocation. The other seven allocations are separate.
ALLOCATION_TOTAL = OUTPUT_LIMIT + sum(v for k, v in FILE_LIMITS.items() if not k.endswith(".bin"))
ADDITION_LIMIT = 2097152
MAX_ENTRIES, MAX_ADDITION_BYTES = 20000, 256 * 1024 * 1024
CACHE_LIMIT = 16 * 1024 * 1024
CACHE_DIRS = frozenset((".pytest_cache", ".pytest_cache/v", ".pytest_cache/v/cache"))
CACHE_SUPPORT = {
    ".pytest_cache/.gitignore": b"# Created by pytest automatically.\n*\n",
    ".pytest_cache/CACHEDIR.TAG": (
        b"Signature: 8a477f597d28d172789f06886806bc55\n"
        b"# This file is a cache directory tag created by pytest.\n"
        b"# For information about cache directory tags, see:\n"
        b"#\thttps://bford.info/cachedir/spec.html\n"
    ),
    ".pytest_cache/README.md": (
        b"# pytest cache directory #\n\n"
        b"This directory contains data from the pytest's cache plugin,\n"
        b"which provides the `--lf` and `--ff` options, as well as the `cache` fixture.\n\n"
        b"**Do not** commit this to version control.\n\n"
        b"See [the docs](https://docs.pytest.org/en/stable/how-to/cache.html) for more information.\n"
    ),
}
CACHE_JSON_LIMITS = {
    ".pytest_cache/v/cache/nodeids": 8388608,
    ".pytest_cache/v/cache/lastfailed": 8388608,
    ".pytest_cache/v/cache/stepwise": 8192,
}
INSTALL_DIRECTORY = "src/code_review_forge.egg-info"
INSTALL_FILES = frozenset(INSTALL_DIRECTORY + "/" + name for name in (
    "PKG-INFO", "SOURCES.txt", "dependency_links.txt", "entry_points.txt", "requires.txt", "top_level.txt"))
FINAL_CHECK_KEYS = frozenset(("phases_complete", "local_source_identity_sha256", "source_policy_sha256", "source_policy_passed"))
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN = object()


class MeasurementError(RuntimeError):
    """Fixed, value-free reason; never an exception or environment disclosure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _need(condition: bool, code: str) -> None:
    if not condition:
        raise MeasurementError(code)


def check_deadline(deadline_ns: int) -> None:
    _need(type(deadline_ns) is int and deadline_ns > 0, "INVALID_DEADLINE")
    _need(time.monotonic_ns() < deadline_ns, "FINALIZATION_TIMEOUT")


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_digest(value: Any) -> bool:
    return type(value) is str and _DIGEST.fullmatch(value) is not None


def _structure(value: Any, depth: int = 0, budget: list[int] | None = None) -> None:
    budget = [100000] if budget is None else budget
    budget[0] -= 1
    _need(depth <= 16 and budget[0] >= 0, "JSON_STRUCTURE_LIMIT")
    if type(value) is dict:
        _need(len(value) <= 50000 and all(type(k) is str for k in value), "JSON_OBJECT_LIMIT")
        for key, item in value.items():
            _need(len(key.encode("utf-8")) <= 4096, "JSON_KEY_LIMIT")
            _structure(item, depth + 1, budget)
    elif type(value) is list:
        _need(len(value) <= 50000, "JSON_ARRAY_LIMIT")
        for item in value:
            _structure(item, depth + 1, budget)
    elif type(value) is str:
        _need(len(value.encode("utf-8")) <= 65536, "JSON_STRING_LIMIT")
    elif type(value) is float:
        _need(math.isfinite(value), "JSON_NONFINITE")
    else:
        _need(value is None or type(value) in (int, bool), "JSON_TYPE")


def canonical(value: Any, limit: int, *, deadline_ns: int) -> bytes:
    check_deadline(deadline_ns)
    try:
        _structure(value)
        raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    except (ValueError, UnicodeError, RecursionError, TypeError) as exc:
        raise MeasurementError("JSON_ENCODING") from exc
    check_deadline(deadline_ns)
    _need(len(raw) <= limit, "ENCODED_FILE_LIMIT")
    return raw


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _need(key not in result, "JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _constant(_value):
    raise MeasurementError("JSON_NONFINITE")


def _json(raw: bytes, limit: int, *, deadline_ns: int) -> Any:
    check_deadline(deadline_ns)
    _need(type(raw) is bytes and len(raw) <= limit, "JSON_BYTE_LIMIT")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
        _structure(value)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise MeasurementError("JSON_ENCODING") from exc
    check_deadline(deadline_ns)
    return value


def _identity(info) -> tuple:
    return tuple(getattr(info, key) for key in (
        "st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns"))


def read_regular(path: Path, limit: int, *, deadline_ns: int) -> tuple[bytes, Any]:
    """Bounded no-follow read, rejecting replacement and change during hashing."""
    check_deadline(deadline_ns)
    _need(path.parent.resolve(strict=True) == path.parent, "PATH_ALIAS")
    before = path.lstat()
    _need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, "FILE_TYPE_OR_LINK")
    _need(before.st_size <= limit, "FILE_BYTE_LIMIT")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        _need(_identity(os.fstat(fd)) == _identity(before), "FILE_CHANGED")
        chunks, count = [], 0
        while True:
            check_deadline(deadline_ns)
            chunk = os.read(fd, min(65536, limit + 1 - count))
            if not chunk:
                break
            chunks.append(chunk)
            count += len(chunk)
            _need(count <= limit, "FILE_BYTE_LIMIT")
        _need(_identity(before) == _identity(os.fstat(fd)) == _identity(path.lstat()), "FILE_CHANGED")
        raw = b"".join(chunks)
        _need(len(raw) == before.st_size, "FILE_CHANGED")
        check_deadline(deadline_ns)
        return raw, before
    finally:
        os.close(fd)


def _nodeid(value: Any) -> bool:
    return type(value) is str and len(value.encode("utf-8")) <= 4096


def validate_cache_member(relative: str, raw: bytes, *, deadline_ns: int) -> None:
    if relative in CACHE_SUPPORT:
        _need(len(raw) <= 16384 and raw == CACHE_SUPPORT[relative], "CACHE_SUPPORT_BYTES")
        return
    _need(relative in CACHE_JSON_LIMITS, "CACHE_UNEXPECTED_MEMBER")
    value = _json(raw, CACHE_JSON_LIMITS[relative], deadline_ns=deadline_ns)
    if relative.endswith("/nodeids"):
        _need(type(value) is list and len(value) <= 50000 and all(_nodeid(x) for x in value)
              and len(set(value)) == len(value), "CACHE_NODEIDS")
    elif relative.endswith("/lastfailed"):
        _need(type(value) is dict and len(value) <= 50000 and all(_nodeid(k) and v is True for k, v in value.items()),
              "CACHE_LASTFAILED")
    else:
        _need(type(value) is dict and set(value) == {"last_failed", "last_test_count", "last_cache_date_str"}, "CACHE_STEPWISE")
        _need(value["last_failed"] is None or _nodeid(value["last_failed"]), "CACHE_STEPWISE")
        count = value["last_test_count"]
        date = value["last_cache_date_str"]
        _need(count is None or (type(count) is int and 0 <= count <= 50000), "CACHE_STEPWISE")
        _need(type(date) is str and 1 <= len(date) <= 128, "CACHE_STEPWISE")
        try:
            datetime.fromisoformat(date)
        except ValueError as exc:
            raise MeasurementError("CACHE_STEPWISE") from exc


def _entry(relative: str, info, kind: str, size: int, checksum: str | None) -> dict:
    return {"path": relative, "type": kind, "mode": stat.S_IMODE(info.st_mode),
            "uid": info.st_uid, "gid": info.st_gid, "size": size, "sha256": checksum}


def _install_expected(record: dict | None) -> dict:
    if record is None:
        return {}
    _need(type(record) is dict and set(record) == {"directory", "entries"}
          and record["directory"] == INSTALL_DIRECTORY and type(record["entries"]) is list
          and len(record["entries"]) == len(INSTALL_FILES), "INSTALL_METADATA_FIELDS")
    result = {}
    total = 0
    for row in record["entries"]:
        _need(type(row) is dict and set(row) == {"path", "mode", "uid", "gid", "size", "sha256"}, "INSTALL_METADATA_FIELDS")
        _need(row["path"] in INSTALL_FILES and row["path"] not in result
              and type(row["mode"]) is int and 0 <= row["mode"] <= 0o777 and not row["mode"] & 0o133
              and type(row["uid"]) is int and row["uid"] == os.getuid()
              and type(row["gid"]) is int and row["gid"] == os.getgid()
              and type(row["size"]) is int and 0 <= row["size"] <= 65536 and _is_digest(row["sha256"]),
              "INSTALL_METADATA_POLICY")
        result[row["path"]] = {**row, "type": "file"}
        total += row["size"]
    _need(set(result) == INSTALL_FILES and total <= 262144, "INSTALL_METADATA_LIMIT")
    return result


def inventory_generated(repo: Path, tracked: set[str], *, phase: str, deadline_ns: int,
                        install_metadata: dict | None = None) -> dict:
    """Complete candidate additions; tracked bytes are verified independently.

    A finite unexpected member is retained in the inventory and marks STOP.
    Overflow cannot be called a complete inventory and raises instead.
    """
    _need(phase in {"pre-phase", "pre-B", "post-B"}, "INVENTORY_PHASE")
    repo = Path(repo)
    _need(repo.is_absolute() and repo.resolve(strict=True) == repo, "PATH_ALIAS")
    installed = _install_expected(install_metadata)
    _need(not (set(installed) | ({INSTALL_DIRECTORY} if installed else set())) & tracked, "INSTALL_METADATA_TRACKED")
    ancestors = {str(parent) for path in tracked for parent in Path(path).parents if str(parent) != "."}
    rows, reasons, count, total = [], set(), 0, 0

    def visit(directory):
        nonlocal count, total
        check_deadline(deadline_ns)
        first = directory.lstat()
        _need(stat.S_ISDIR(first.st_mode), "DIRECTORY_TYPE")
        with os.scandir(directory) as iterator:
            names = []
            for item in iterator:
                check_deadline(deadline_ns)
                names.append(item.name)
                _need(len(names) <= MAX_ENTRIES, "INVENTORY_ENTRY_LIMIT")
        for name in sorted(names):
            check_deadline(deadline_ns)
            path = directory / name
            relative = path.relative_to(repo).as_posix()
            if relative == ".git":
                _need(not path.is_symlink(), "PATH_ALIAS")
                continue  # Git metadata is not a candidate addition or source.
            count += 1
            _need(count <= MAX_ENTRIES, "INVENTORY_ENTRY_LIMIT")
            info = path.lstat()
            if relative in tracked:
                continue  # Includes the two explicitly tracked, checked symlinks.
            if relative in ancestors:
                _need(stat.S_ISDIR(info.st_mode), "SOURCE_DIRECTORY_ALIAS")
                visit(path)
                continue
            owned = info.st_uid == os.getuid() and info.st_gid == os.getgid()
            if stat.S_ISDIR(info.st_mode):
                rows.append(_entry(relative, info, "directory", 0, None))
                if relative not in CACHE_DIRS and not (installed and relative == INSTALL_DIRECTORY):
                    reasons.add("CACHE_DIRECTORY_POLICY")
                if not owned or info.st_mode & 0o022 or stat.S_IMODE(info.st_mode) > 0o777:
                    reasons.add("CACHE_DIRECTORY_POLICY")
                visit(path)
            elif stat.S_ISREG(info.st_mode):
                total += info.st_size
                _need(total <= MAX_ADDITION_BYTES, "INVENTORY_BYTE_LIMIT")
                raw, verified = read_regular(path, MAX_ADDITION_BYTES, deadline_ns=deadline_ns)
                rows.append(_entry(relative, verified, "file", len(raw), _digest(raw)))
                if not owned or info.st_mode & 0o133 or stat.S_IMODE(info.st_mode) > 0o777 or info.st_nlink != 1:
                    reasons.add("CACHE_FILE_POLICY")
                if relative in installed:
                    if rows[-1] != installed[relative]:
                        reasons.add("INSTALL_METADATA_CHANGED")
                    continue
                try:
                    validate_cache_member(relative, raw, deadline_ns=deadline_ns)
                except MeasurementError as exc:
                    if exc.code == "FINALIZATION_TIMEOUT":
                        raise
                    reasons.add(exc.code)
            elif stat.S_ISLNK(info.st_mode):
                target = os.readlink(path).encode("utf-8")
                _need(_identity(info) == _identity(path.lstat()), "FILE_CHANGED")
                rows.append(_entry(relative, info, "symlink", len(target), _digest(target)))
                reasons.add("CACHE_FILE_POLICY")
            else:
                rows.append(_entry(relative, info, "special", 0, None))
                reasons.add("CACHE_FILE_POLICY")
        _need(_identity(first) == _identity(directory.lstat()), "DIRECTORY_CHANGED")
        check_deadline(deadline_ns)

    visit(repo)
    _need(len(rows) <= MAX_ENTRIES, "INVENTORY_ENTRY_LIMIT")
    cache_rows = [row for row in rows if row["path"] == ".pytest_cache" or row["path"].startswith(".pytest_cache/")]
    if (sum(row["size"] for row in cache_rows if row["type"] == "file") > CACHE_LIMIT
            or sum(r["type"] == "file" for r in cache_rows) > 6 or sum(r["type"] == "directory" for r in cache_rows) > 3):
        reasons.add("CACHE_AGGREGATE_LIMIT")
    found = {row["path"] for row in rows}
    if installed and not (set(installed) | {INSTALL_DIRECTORY}) <= found:
        reasons.add("INSTALL_METADATA_MISSING")
    if phase == "pre-phase" and found - set(installed) - ({INSTALL_DIRECTORY} if installed else set()):
        reasons.add("PREPHASE_GENERATED_PRESENT")
    result = {"schema_version": 1, "phase": phase, "complete": True,
              "status": "STOP" if reasons else "PASS", "reason_codes": sorted(reasons),
              "entries": rows, "regular_bytes": total}
    canonical(result, FILE_LIMITS["generated-before.json"], deadline_ns=deadline_ns)
    return result


def inventory_runtime_roots(environment: dict, *, deadline_ns: int) -> list[dict]:
    """Bound complete external HOME/TMPDIR traversal and retain aggregate hashes."""
    result = []
    for name in ("HOME", "TMPDIR"):
        root = Path(environment[name])
        _need(root.is_absolute() and root.resolve(strict=True) == root, "RUNTIME_ROOT_ALIAS")
        count, total, digest = 0, 0, hashlib.sha256()

        def visit(directory, root=root, digest=digest):
            nonlocal count, total
            check_deadline(deadline_ns)
            first = directory.lstat()
            _need(stat.S_ISDIR(first.st_mode), "RUNTIME_ROOT_TYPE")
            with os.scandir(directory) as iterator:
                names = []
                for item in iterator:
                    names.append(item.name)
                    _need(len(names) <= MAX_ENTRIES, "RUNTIME_ENTRY_LIMIT")
                    check_deadline(deadline_ns)
            for entry in sorted(names):
                check_deadline(deadline_ns)
                path = directory / entry
                info = path.lstat()
                count += 1
                _need(count <= MAX_ENTRIES, "RUNTIME_ENTRY_LIMIT")
                relative = path.relative_to(root).as_posix()
                if stat.S_ISREG(info.st_mode):
                    total += info.st_size
                    _need(total <= MAX_ADDITION_BYTES, "RUNTIME_BYTE_LIMIT")
                    raw, info = read_regular(path, MAX_ADDITION_BYTES, deadline_ns=deadline_ns)
                    row = _entry(relative, info, "file", len(raw), _digest(raw))
                elif stat.S_ISDIR(info.st_mode):
                    row = _entry(relative, info, "directory", 0, None)
                elif stat.S_ISLNK(info.st_mode):
                    target = os.readlink(path).encode("utf-8")
                    _need(_identity(info) == _identity(path.lstat()), "FILE_CHANGED")
                    row = _entry(relative, info, "symlink", len(target), _digest(target))
                else:
                    raise MeasurementError("RUNTIME_SPECIAL_FILE")
                digest.update(canonical(row, 16384, deadline_ns=deadline_ns) + b"\n")
                if row["type"] == "directory":
                    visit(path)
            _need(_identity(first) == _identity(directory.lstat()), "DIRECTORY_CHANGED")

        visit(root)
        result.append({"name": name, "entries": count, "regular_bytes": total,
                       "inventory_sha256": digest.hexdigest(), "complete": True})
    check_deadline(deadline_ns)
    return result


@dataclass(slots=True)
class _Context:
    token: object
    repo: Path
    environment: dict
    binding: dict
    source: dict
    runtime: dict
    tracked: set[str]
    before: dict
    source_binding_sha256: str
    environment_sha256: str
    phase_inventory: dict | None = None
    pre_b: dict | None = None
    attempted: bool = False
    outcome: Any = None
    persisted: bool = False


@dataclass(slots=True)
class _Outcome:
    context: _Context
    status: str
    nonce: str | None = None
    stdout: bytes = b""
    stderr: bytes = b""
    owner: dict = field(default_factory=dict)
    started_ns: int | None = None
    ended_ns: int | None = None
    service_started_ns: int | None = None
    service_deadline_ns: int | None = None
    job_deadline_ns: int | None = None
    returncode: int | None = None
    reason: str | None = None
    interrupted: BaseException | None = None


def _context(context) -> _Context:
    _need(type(context) is _Context and context.token is _TOKEN, "CONTEXT_REQUIRED")
    return context


def baseline_environment(environment: dict, repo: Path) -> dict:
    """Production first-baseline derivation; the fixed runner has no path prefix."""
    result = dict(environment)
    result["PYTHONPATH"] = str(repo / "src")
    runner = ARGV[0]
    if os.path.dirname(runner):
        project_bin = os.path.dirname(os.path.abspath(runner))
        result["PATH"] = project_bin + os.pathsep + result.get("PATH", "")
    return result


def _source_check(context: _Context, deadline_ns: int) -> None:
    check_deadline(deadline_ns)
    current = launch.inspect_checkout(context.repo, context.source["candidate_sha"], deadline=deadline_ns / NS)
    _need(current == context.source, "SOURCE_CHANGED")
    check_deadline(deadline_ns)


def prepare(repo: Path, environment: dict, *, binding: dict, source: dict,
            trusted_runtime: dict, deadline_ns: int) -> _Context:
    """Called before every phase, with runtime admitted before service startup."""
    check_deadline(deadline_ns)
    repo = Path(repo)
    _need(repo.is_absolute() and repo.resolve(strict=True) == repo, "PATH_ALIAS")
    launch.validate_source(source)
    user_service.validate_binding(binding)
    user_service.validate_environment(environment)
    _need(binding["candidate_sha"] == source["candidate_sha"], "SOURCE_BINDING")
    _need(launch.inspect_checkout(repo, source["candidate_sha"], deadline=deadline_ns / NS) == source, "SOURCE_CHANGED")
    user_service.revalidate_runtime_admission(trusted_runtime, environment, source, deadline_ns=deadline_ns)
    supports = {Path(name).name: _digest(raw) for name, raw in CACHE_SUPPORT.items()}
    for interpreter in ("provider", "system"):
        _need(trusted_runtime["profile_metadata"][interpreter]["cache_support"] == supports, "PYTEST_CACHE_IMPLEMENTATION")
    for value in (environment, binding, source, trusted_runtime):
        canonical(value, MAX_ADDITION_BYTES, deadline_ns=deadline_ns)
    env = baseline_environment(environment, repo)
    ctx = _Context(_TOKEN, repo, dict(environment), json.loads(json.dumps(binding)), json.loads(json.dumps(source)),
                   json.loads(json.dumps(trusted_runtime)), set(), {},
                   _digest(canonical(source, FILE_LIMITS["result.json"], deadline_ns=deadline_ns)),
                   _digest(canonical(env, FILE_LIMITS["environment.json"], deadline_ns=deadline_ns)))
    _source_check(ctx, deadline_ns)
    ctx.tracked = set(launch._tree(repo, source["candidate_sha"], deadline=deadline_ns / NS))
    before = inventory_generated(repo, ctx.tracked, phase="pre-phase", deadline_ns=deadline_ns,
                                 install_metadata=ctx.runtime["generated_install_metadata"])
    before["runtime_roots"] = inventory_runtime_roots(environment, deadline_ns=deadline_ns)
    _need(before["status"] == "PASS", "PREPHASE_GENERATED_PRESENT")
    ctx.before = before
    check_deadline(deadline_ns)
    return ctx


def admit_phase_inventory(context: _Context, *, deadline_ns: int) -> dict:
    """Capture only after controller validates the preceding fixed phases."""
    ctx = _context(context)
    _need(not ctx.attempted and ctx.phase_inventory is None, "PHASE_INVENTORY_STATE")
    record = inventory_generated(ctx.repo, ctx.tracked, phase="pre-B", deadline_ns=deadline_ns,
                                 install_metadata=ctx.runtime.get("generated_install_metadata"))
    _need(record["status"] == "PASS", "PRE_B_GENERATED_POLICY")
    ctx.phase_inventory = record
    return json.loads(json.dumps(record))


def capture_pre_b(context: _Context, *, admitted_generated: dict, deadline_ns: int) -> dict:
    ctx = _context(context)
    _need(not ctx.attempted and ctx.phase_inventory == admitted_generated, "PHASE_INVENTORY_BINDING")
    _source_check(ctx, deadline_ns)
    current = inventory_generated(ctx.repo, ctx.tracked, phase="pre-B", deadline_ns=deadline_ns,
                                  install_metadata=ctx.runtime.get("generated_install_metadata"))
    _need(current == ctx.phase_inventory and current["status"] == "PASS", "PRE_B_GENERATED_CHANGED")
    user_service.revalidate_runtime_admission(ctx.runtime, ctx.environment, ctx.source, deadline_ns=deadline_ns)
    current["runtime_roots"] = inventory_runtime_roots(ctx.environment, deadline_ns=deadline_ns)
    ctx.pre_b = current
    check_deadline(deadline_ns)
    return json.loads(json.dumps(current))


def _owner_digest(value: Any) -> str:
    return _digest(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8"))


_OWNER_REQUIRED = frozenset((
    "capture_version", "cleanup_complete", "timed_out", "cancelled", "owned", "invocation_nonce",
    "caller_pid", "caller_start_ticks", "cwd", "argv_sha256", "env_sha256", "owner_pid", "owner_start_ticks",
))
_OWNER_SUCCESS = frozenset((
    "resolved_executable", "driver_pid", "driver_start_ticks", "returncode", "duration_seconds",
    "retained_bytes", "diagnostic_truncated", "streams",
))
_OWNER_ERROR = frozenset(("error", "error_kind", "report_overflow"))
_EXECUTABLE_KEYS = frozenset(("path", "realpath", "dev", "ino", "size", "mtime_ns", "ctime_ns", "sha256"))
_ERROR_KINDS = frozenset(("RuntimeError", "ValueError", "OSError", "FileNotFoundError", "PermissionError",
                          "TimeoutExpired", "KeyboardInterrupt", "SystemExit", "MutationProcessError"))


def project_owner(report: Any, *, nonce: str, repo: Path, environment: dict,
                  stdout: bytes, stderr: bytes, caller_pid: int, deadline_ns: int) -> dict:
    """Validate all known owner fields, then replace error text with fixed codes.

    Error reports may lack capture/driver fields. They never establish complete
    output, even if cleanup metadata is available. Unknown fields are rejected.
    """
    check_deadline(deadline_ns)
    _need(type(stdout) is bytes and type(stderr) is bytes and len(stdout) + len(stderr) <= OUTPUT_LIMIT, "RAW_OUTPUT_LIMIT")
    _need(type(report) is dict and report.keys() <= _OWNER_REQUIRED | _OWNER_SUCCESS | _OWNER_ERROR, "OWNER_FIELDS")
    if not report:
        return {"schema_version": 1, "state": "UNAVAILABLE", "reason_code": "OWNER_REPORT_UNAVAILABLE", "cleanup_complete": False}
    if "report_overflow" in report:
        _need(set(report) == {"cleanup_complete", "error", "error_kind", "capture_version", "report_overflow"}
              and report["report_overflow"] is True and type(report["cleanup_complete"]) is bool
              and type(report["capture_version"]) is int and report["capture_version"] == 1
              and type(report["error"]) is str and report["error_kind"] == "ValueError", "OWNER_OVERFLOW_FIELDS")
        return {"schema_version": 1, "state": "ERROR", "reason_code": "OWNER_REPORT_OVERFLOW",
                "cleanup_complete": report["cleanup_complete"]}
    _need(_OWNER_REQUIRED <= report.keys(), "OWNER_MISSING_FIELDS")
    expected = {"capture_version": 1, "invocation_nonce": nonce, "caller_pid": caller_pid,
                "cwd": str(repo), "argv_sha256": _owner_digest(list(ARGV)), "env_sha256": _owner_digest(environment)}
    for key, value in expected.items():
        _need(type(report[key]) is type(value) and report[key] == value, "OWNER_INVOCATION_BINDING")
    for key in ("caller_pid", "caller_start_ticks", "owner_pid", "owner_start_ticks"):
        _need(type(report[key]) is int and report[key] > 0, "OWNER_IDENTITY")
    for key in ("cleanup_complete", "timed_out", "cancelled"):
        _need(type(report[key]) is bool, "OWNER_OUTCOME_TYPE")
    has_error = "error" in report or "error_kind" in report
    if has_error:
        _need("error" in report and "error_kind" in report and type(report["error"]) is str
              and len(report["error"].encode("utf-8")) <= 32768 and type(report["error_kind"]) is str, "OWNER_ERROR_FIELDS")
    else:
        _need(set(report) == _OWNER_REQUIRED | _OWNER_SUCCESS, "OWNER_MISSING_FIELDS")
    for key in ("driver_pid", "driver_start_ticks"):
        if key in report:
            _need(type(report[key]) is int and report[key] > 0, "OWNER_IDENTITY")
    _need(("driver_pid" in report) == ("driver_start_ticks" in report), "OWNER_IDENTITY")
    identities = [report["caller_pid"], report["owner_pid"]]
    if "driver_pid" in report:
        identities.append(report["driver_pid"])
    _need(len(set(identities)) == len(identities), "OWNER_IDENTITY")
    if "returncode" in report:
        _need(type(report["returncode"]) is int or (has_error and report["returncode"] is None), "OWNER_RETURN_CODE")
    if "duration_seconds" in report:
        duration = report["duration_seconds"]
        _need(type(duration) in (int, float) and math.isfinite(duration) and 0 <= duration <= TIMEOUT_SECONDS + OWNER_ENVELOPE_SECONDS,
              "OWNER_DURATION")
    owned = report["owned"]
    _need(type(owned) is list and len(owned) <= 4096, "OWNER_INVENTORY_LIMIT")
    seen = set()
    for row in owned:
        _need(type(row) is dict and set(row) == {"pid", "start_ticks", "observed_parent", "remaining", "reaped_status"}, "OWNER_INVENTORY_FIELDS")
        _need(all(type(row[k]) is int and row[k] > 0 for k in ("pid", "start_ticks", "observed_parent"))
              and type(row["remaining"]) is bool and (row["reaped_status"] is None or type(row["reaped_status"]) is int)
              and row["pid"] not in seen, "OWNER_INVENTORY_TYPE")
        _need(not report["cleanup_complete"] or not row["remaining"], "OWNER_CLEANUP_CONTRADICTION")
        seen.add(row["pid"])
    if "driver_pid" in report:
        matched = [row for row in owned if row["pid"] == report["driver_pid"]]
        _need(len(matched) == 1 and matched[0]["start_ticks"] == report["driver_start_ticks"]
              and matched[0]["observed_parent"] == report["owner_pid"], "OWNER_DRIVER_BINDING")
    if "resolved_executable" in report:
        executable = report["resolved_executable"]
        _need(type(executable) is dict and set(executable) == _EXECUTABLE_KEYS, "OWNER_EXECUTABLE_FIELDS")
        _need(all(type(executable[k]) is str and os.path.isabs(executable[k]) and len(executable[k]) <= 4096
                  for k in ("path", "realpath")) and _is_digest(executable["sha256"])
              and all(type(executable[k]) is int and executable[k] >= 0 for k in ("dev", "ino", "size", "mtime_ns", "ctime_ns")),
              "OWNER_EXECUTABLE_TYPE")
        # The selected executable bytes must still be those of the admitted
        # runtime. Revalidation also binds all package/loader bytes separately.
        selected = next((Path(component) / "python3" for component in environment["PATH"].split(os.pathsep)
                         if (Path(component) / "python3").is_file() and os.access(Path(component) / "python3", os.X_OK)), None)
        _need(selected is not None and str(selected) == executable["path"]
              and str(selected.resolve(strict=True)) == executable["realpath"], "OWNER_EXECUTABLE_BINDING")
        raw, details = read_regular(selected.resolve(strict=True), MAX_ADDITION_BYTES, deadline_ns=deadline_ns)
        _need(_digest(raw) == executable["sha256"] and all(getattr(details, "st_" + key) == executable[key]
                  for key in ("dev", "ino", "size", "mtime_ns", "ctime_ns")), "OWNER_EXECUTABLE_CHANGED")
    stream_keys = {"streams", "retained_bytes", "diagnostic_truncated"}
    _need(not (stream_keys & report.keys()) or stream_keys <= report.keys(), "OWNER_STREAM_FIELDS")
    if "streams" in report:
        _need(type(report["streams"]) is dict and set(report["streams"]) == {"stdout", "stderr"}, "OWNER_STREAM_FIELDS")
        retained = total = 0
        for name, raw in (("stdout", stdout), ("stderr", stderr)):
            stream = report["streams"][name]
            _need(type(stream) is dict and set(stream) == {"bytes", "sha256", "retained_bytes", "eof"}, "OWNER_STREAM_FIELDS")
            _need(type(stream["bytes"]) is int and type(stream["retained_bytes"]) is int
                  and 0 <= stream["retained_bytes"] <= stream["bytes"] and type(stream["eof"]) is bool
                  and _is_digest(stream["sha256"]), "OWNER_STREAM_TYPE")
            # Owner error paths may not expose the retained prefix transport.
            _need(has_error or stream["retained_bytes"] == len(raw), "OWNER_PREFIX_BINDING")
            if len(raw) == stream["bytes"]:
                _need(_digest(raw) == stream["sha256"], "OWNER_STREAM_HASH")
            retained += stream["retained_bytes"]
            total += stream["bytes"]
        _need(type(report["retained_bytes"]) is int and report["retained_bytes"] == retained <= OUTPUT_LIMIT
              and type(report["diagnostic_truncated"]) is bool and report["diagnostic_truncated"] == (total > retained),
              "OWNER_TRUNCATION_BINDING")
    else:
        _need(not stdout and not stderr, "OWNER_MISSING_STREAMS")
    result = {key: value for key, value in report.items() if key not in _OWNER_ERROR}
    result.update(schema_version=1, state="ERROR" if has_error else "VALIDATED",
                  reason_code="OWNER_ERROR" if has_error else None)
    if has_error:
        result["error_type_code"] = report["error_kind"] if report["error_kind"] in _ERROR_KINDS else "OTHER"
    canonical(result, FILE_LIMITS["owner.json"], deadline_ns=deadline_ns)
    check_deadline(deadline_ns)
    return result


def budget_available(*, now_ns: int, utc_ns: int, service_started_monotonic_ns: int,
                     service_grace_deadline_ns: int, job_artifact_deadline_ns: int,
                     job_started_utc_ns: int) -> bool:
    values = (now_ns, utc_ns, service_started_monotonic_ns, service_grace_deadline_ns,
              job_artifact_deadline_ns, job_started_utc_ns)
    _need(all(type(value) is int and value > 0 for value in values), "BUDGET_CLOCK_TYPE")
    _need(service_grace_deadline_ns == service_started_monotonic_ns + SERVICE_GRACE_SECONDS * NS,
          "SERVICE_CLOCK_BINDING")
    required = (TIMEOUT_SECONDS + OWNER_ENVELOPE_SECONDS + FINAL_SECONDS) * NS
    # Validate genuine A against J+8700, then derive C once on both clocks.
    job_artifact_deadline_utc_ns = job_started_utc_ns + (JOB_SECONDS - ARTIFACT_SECONDS) * NS
    job_work_deadline_ns = job_artifact_deadline_ns - CLEANUP_SECONDS * NS
    job_work_deadline_utc_ns = job_artifact_deadline_utc_ns - CLEANUP_SECONDS * NS
    return (0 <= now_ns - service_started_monotonic_ns <= LATEST_START_SECONDS * NS
            and now_ns + required <= min(service_grace_deadline_ns, job_work_deadline_ns)
            and utc_ns + required <= job_work_deadline_utc_ns
            and abs((job_artifact_deadline_ns - now_ns) - (job_artifact_deadline_utc_ns - utc_ns)) <= 5 * NS)


def _boot(context: _Context, deadline_ns: int) -> None:
    check_deadline(deadline_ns)
    with open("/proc/sys/kernel/random/boot_id", "rb") as handle:
        raw = handle.read(65)
    _need(raw.decode("ascii").strip() == context.binding["boot_id"], "BOOT_CHANGED")
    check_deadline(deadline_ns)


def run_once(context: _Context, *, service_started_monotonic_ns: int,
             service_grace_deadline_ns: int, job_artifact_deadline_ns: int) -> _Outcome:
    """Exactly one fixed owned call, admitted against the original two clocks.

    Returns the original control exception in outcome.interrupted after owned
    cleanup; the controller must retain that first cancellation through its
    best-effort finalization. This function never starts a second invocation.
    """
    ctx = _context(context)
    _need(not ctx.attempted and ctx.pre_b is not None, "MEASUREMENT_STATE")
    ctx.attempted = True
    out = _Outcome(ctx, "B_NOT_RUN_BUDGET", service_started_ns=service_started_monotonic_ns,
                   service_deadline_ns=service_grace_deadline_ns, job_deadline_ns=job_artifact_deadline_ns)
    ctx.outcome = out

    def admitted():
        return budget_available(now_ns=time.monotonic_ns(), utc_ns=time.time_ns(),
            service_started_monotonic_ns=service_started_monotonic_ns,
            service_grace_deadline_ns=service_grace_deadline_ns, job_artifact_deadline_ns=job_artifact_deadline_ns,
            job_started_utc_ns=ctx.binding["job_started_ns"])

    if not admitted():
        out.reason = "INSUFFICIENT_RESIDUAL_BUDGET"
        out.ended_ns = time.monotonic_ns()
        return out
    job_work_deadline_ns = job_artifact_deadline_ns - CLEANUP_SECONDS * NS
    preflight_deadline = min(service_grace_deadline_ns, job_work_deadline_ns) - (TIMEOUT_SECONDS + OWNER_ENVELOPE_SECONDS + FINAL_SECONDS) * NS
    try:
        _boot(ctx, preflight_deadline)
        _source_check(ctx, preflight_deadline)
        current = inventory_generated(ctx.repo, ctx.tracked, phase="pre-B", deadline_ns=preflight_deadline,
                                      install_metadata=ctx.runtime.get("generated_install_metadata"))
        _need(current == ctx.phase_inventory, "PRE_B_GENERATED_CHANGED")
        user_service.revalidate_runtime_admission(ctx.runtime, ctx.environment, ctx.source, deadline_ns=preflight_deadline)
        # Import only the immutable candidate's admitted owner after all checks.
        from code_forge import _mutation_process
        _need(Path(_mutation_process.__file__).resolve(strict=True) == ctx.repo / "src/code_forge/_mutation_process.py", "OWNER_IMPORT_SOURCE")
        environment = baseline_environment(ctx.environment, ctx.repo)
        _need(_owner_digest(environment) == ctx.environment_sha256, "ENVIRONMENT_CHANGED")
        if not admitted():
            out.reason = "INSUFFICIENT_RESIDUAL_BUDGET"
            out.ended_ns = time.monotonic_ns()
            return out
        out.nonce = secrets.token_hex(16)
        out.started_ns = time.monotonic_ns()
        _need(admitted(), "INSUFFICIENT_RESIDUAL_BUDGET")
        out.status = "B_FAILED"
        result = _mutation_process.run_owned_command(list(ARGV), cwd=str(ctx.repo), env=environment,
            timeout=TIMEOUT_SECONDS, capture_output=True, check=False, memory_limit_bytes=None,
            text=False, invocation_nonce=out.nonce, output_limit_bytes=OUTPUT_LIMIT)
        out.ended_ns = time.monotonic_ns()
        out.stdout, out.stderr = result.stdout, result.stderr
        out.owner = getattr(result, "ownership", {})
        _need(result.args == list(ARGV) and type(result.returncode) is int
              and type(out.owner) is dict and out.owner.get("returncode") == result.returncode,
              "OWNER_COMPLETED_PROCESS_BINDING")
        out.returncode = result.returncode
        out.status = "B_RETURNED"
    except BaseException as exc:  # noqa: BLE001 - preserve first interruption and owner cleanup metadata
        out.ended_ns = time.monotonic_ns()
        out.reason = "B_PREFLIGHT_FAILED" if out.started_ns is None else "B_COMMAND_ERROR"
        out.status = "B_NOT_RUN_PREFLIGHT" if out.started_ns is None else "B_FAILED"
        if isinstance(exc, subprocess.TimeoutExpired):
            out.reason = "B_TIMEOUT"
            out.stdout = exc.output if type(exc.output) is bytes else b""
            out.stderr = exc.stderr if type(exc.stderr) is bytes else b""
        elif not isinstance(exc, Exception) or getattr(exc, "_forge_control", False):
            out.reason = "B_CANCELLED"
            out.interrupted = exc
        out.owner = getattr(exc, "ownership", getattr(exc, "report", {}))
        if isinstance(exc, MeasurementError) and exc.code == "INSUFFICIENT_RESIDUAL_BUDGET":
            out.status, out.reason = "B_NOT_RUN_BUDGET", exc.code
    return out


def outcome_for(context: _Context) -> _Outcome | None:
    return _context(context).outcome


def _failed_inventory(phase: str, code: str) -> dict:
    return {"schema_version": 1, "phase": phase, "complete": False, "status": "STOP",
            "reason_codes": [code], "entries": [], "regular_bytes": None, "runtime_roots": []}


def _validate_final_checks(checks: dict, directory: Path, deadline_ns: int) -> bool:
    _need(type(checks) is dict and set(checks) == FINAL_CHECK_KEYS, "FINAL_CHECK_FIELDS")
    phase = checks["phases_complete"]
    _need(type(phase) is dict and set(phase) == {"bytes", "sha256"}
          and type(phase["bytes"]) is int and 0 < phase["bytes"] <= 65536 and _is_digest(phase["sha256"]), "PHASE_LINK_FIELDS")
    raw, _ = read_regular(directory.parent / "phases-complete.json", 65536, deadline_ns=deadline_ns)
    _need(len(raw) == phase["bytes"] and _digest(raw) == phase["sha256"], "PHASE_LINK_CHANGED")
    _need(type(checks["source_policy_passed"]) is bool, "FINAL_CHECK_TYPE")
    for key in ("local_source_identity_sha256", "source_policy_sha256"):
        _need(checks[key] is None or _is_digest(checks[key]), "FINAL_CHECK_DIGEST")
    return checks["source_policy_passed"] and all(checks[key] is not None for key in (
        "local_source_identity_sha256", "source_policy_sha256"))


class _Store:
    """Exclusive bounded files; success also requires final directory fsync."""

    def __init__(self, directory: Path, deadline_ns: int):
        check_deadline(deadline_ns)
        _need(directory.is_absolute() and directory.parent.resolve(strict=True) == directory.parent, "EVIDENCE_PATH_ALIAS")
        directory.mkdir(mode=0o700, exist_ok=False)
        self.directory, self.deadline_ns = directory, deadline_ns
        self.fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.initial = os.fstat(self.fd)
        self.files = {}
        self.check()

    def check(self):
        check_deadline(self.deadline_ns)
        current, named = os.fstat(self.fd), self.directory.lstat()
        _need((current.st_dev, current.st_ino) == (self.initial.st_dev, self.initial.st_ino)
              == (named.st_dev, named.st_ino) and stat.S_ISDIR(named.st_mode)
              and current.st_uid == os.getuid() and current.st_gid == os.getgid()
              and stat.S_IMODE(current.st_mode) == 0o700, "EVIDENCE_DIRECTORY_CHANGED")

    def write(self, name: str, raw: bytes):
        self.check()
        _need(name in FILE_LIMITS and name not in self.files and type(raw) is bytes and len(raw) <= FILE_LIMITS[name], "EVIDENCE_FILE_LIMIT")
        if name.endswith(".bin"):
            prior = sum(row["bytes"] for key, row in self.files.items() if key.endswith(".bin"))
            _need(prior + len(raw) <= OUTPUT_LIMIT, "RAW_OUTPUT_LIMIT")
        _need(sum(row["bytes"] for row in self.files.values()) + len(raw) <= ALLOCATION_TOTAL, "EVIDENCE_AGGREGATE_LIMIT")
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        try:
            view = memoryview(raw)
            while view:
                self.check()
                written = os.write(fd, view[:65536])
                _need(written > 0, "EVIDENCE_WRITE_FAILED")
                view = view[written:]
                self.check()
            self.check()
            os.fsync(fd)
            self.check()
            info = os.fstat(fd)
            _need(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1
                  and info.st_uid == os.getuid() and info.st_gid == os.getgid() and info.st_size == len(raw), "EVIDENCE_FILE_CHANGED")
        finally:
            os.close(fd)
        actual, _ = read_regular(self.directory / name, FILE_LIMITS[name], deadline_ns=self.deadline_ns)
        _need(actual == raw, "EVIDENCE_FILE_CHANGED")
        self.files[name] = {"bytes": len(actual), "sha256": _digest(actual)}

    def finish(self):
        self.check()
        _need(set(os.listdir(self.fd)) == set(FILE_LIMITS) == set(self.files), "EVIDENCE_MEMBER_SET")
        size = 0
        for name in FILE_LIMITS:
            self.check()
            raw, info = read_regular(self.directory / name, FILE_LIMITS[name], deadline_ns=self.deadline_ns)
            _need(stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid == os.getuid() and info.st_gid == os.getgid()
                  and self.files[name] == {"bytes": len(raw), "sha256": _digest(raw)}, "EVIDENCE_FILE_CHANGED")
            size += len(raw)
        _need(size <= ALLOCATION_TOTAL <= ADDITION_LIMIT, "EVIDENCE_AGGREGATE_LIMIT")
        self.check()
        os.fsync(self.fd)
        self.check()
        return size

    def close(self):
        os.close(self.fd)


def persist(context: _Context, outcome: _Outcome, directory: Path, *, final_deadline_ns: int,
            final_checks: dict) -> dict:
    """Finalize bounded evidence under the controller's single enforced deadline.

    A partial/late set is always STOP. Files are never replaced to convert late
    evidence into success. The returned persistence timestamp must be bound in
    the controller's terminal receipt and independently reconciled by root.
    """
    ctx = _context(context)
    _need(type(outcome) is _Outcome and outcome is ctx.outcome and outcome.context is ctx
          and not ctx.persisted, "PERSISTENCE_STATE")
    check_deadline(final_deadline_ns)
    _need(type(outcome.ended_ns) is int and outcome.ended_ns <= time.monotonic_ns()
          and final_deadline_ns <= min(outcome.ended_ns + FINAL_SECONDS * NS,
                                      outcome.service_deadline_ns,
                                      outcome.job_deadline_ns - CLEANUP_SECONDS * NS), "FINAL_DEADLINE_BINDING")
    ctx.persisted = True
    directory = Path(directory)
    reasons = set()
    checks_pass = _validate_final_checks(final_checks, directory, final_deadline_ns)
    if not checks_pass:
        reasons.add("FINAL_SOURCE_POLICY_FAILED")
    _need(type(outcome.stdout) is bytes and type(outcome.stderr) is bytes
          and len(outcome.stdout) + len(outcome.stderr) <= OUTPUT_LIMIT, "RAW_OUTPUT_LIMIT")
    before = {"schema_version": 1, "pre_phase": ctx.before, "pre_B": ctx.pre_b}
    try:
        after = inventory_generated(ctx.repo, ctx.tracked, phase="post-B", deadline_ns=final_deadline_ns,
                                    install_metadata=ctx.runtime.get("generated_install_metadata"))
        after["runtime_roots"] = inventory_runtime_roots(ctx.environment, deadline_ns=final_deadline_ns)
        if after["status"] != "PASS":
            reasons.add("POST_B_GENERATED_POLICY")
        _source_check(ctx, final_deadline_ns)
        user_service.revalidate_runtime_admission(ctx.runtime, ctx.environment, ctx.source, deadline_ns=final_deadline_ns)
        _boot(ctx, final_deadline_ns)
    except Exception as exc:  # noqa: BLE001 - every failed recheck prevents success
        if getattr(exc, "_forge_control", False):
            raise
        check_deadline(final_deadline_ns)
        code = exc.code if isinstance(exc, MeasurementError) else "POST_B_PREFLIGHT_FAILED"
        reasons.add(code)
        if "after" not in locals():
            after = _failed_inventory("post-B", code)
    environment = baseline_environment(ctx.environment, ctx.repo)
    try:
        owner = project_owner(outcome.owner, nonce=outcome.nonce, repo=ctx.repo, environment=environment,
                              stdout=outcome.stdout, stderr=outcome.stderr, caller_pid=os.getpid(), deadline_ns=final_deadline_ns)
    except MeasurementError as exc:
        check_deadline(final_deadline_ns)
        reasons.add(exc.code)
        owner = {"schema_version": 1, "state": "INVALID", "reason_code": exc.code, "cleanup_complete": False}
    complete_capture = (owner.get("state") == "VALIDATED" and owner.get("diagnostic_truncated") is False
                        and all(owner.get("streams", {}).get(name, {}).get("eof") is True for name in ("stdout", "stderr")))
    # This is owned B-process cleanup only; root checks later credential cleanup.
    cleanup = owner.get("cleanup_complete") is True
    if not complete_capture:
        reasons.add("INCOMPLETE_CAPTURE")
    if not cleanup:
        reasons.add("UNKNOWN_CLEANUP")
    returned = owner.get("returncode")
    if outcome.status != "B_RETURNED" or returned != 0 or type(returned) is not int or outcome.returncode != returned:
        reasons.add(outcome.reason or "B_NOT_SUCCESSFUL")
    if owner.get("timed_out") is not False or owner.get("cancelled") is not False or outcome.interrupted is not None:
        reasons.add("B_INTERRUPTED")
    if outcome.started_ns is not None:
        if not (outcome.started_ns <= outcome.ended_ns <= outcome.started_ns + (TIMEOUT_SECONDS + OWNER_ENVELOPE_SECONDS) * NS):
            reasons.add("OWNER_ENVELOPE_OVERRUN")
    runtime = user_service.runtime_summary(ctx.runtime)
    environment_record = {"schema_version": 1, "profile": PROFILE, "spec_sha256": SPEC_SHA256,
                          "allowed_names": sorted(environment), "profile_sha256": ctx.environment_sha256,
                          "production_derivation": "first-baseline-candidate-src-fixed-runner",
                          "private_home": True, "binary_owner_capture": True}
    status = "STOP" if reasons else "MEASUREMENT_COMPLETE"
    result = {"schema_version": 1, "profile": PROFILE, "spec_sha256": SPEC_SHA256, "status": status,
              "qualified": False, "baseline_passed": False, "whole_job_passed": False,
              "source_sha256": ctx.source_binding_sha256, "runtime_sha256": _owner_digest(ctx.runtime),
              "environment_sha256": ctx.environment_sha256,
              "configuration_sha256": _digest(canonical({"argv": list(ARGV), "timeout_seconds": TIMEOUT_SECONDS,
                  "output_limit_bytes": OUTPUT_LIMIT, "text": False, "spec_sha256": SPEC_SHA256}, 32768, deadline_ns=final_deadline_ns)),
              "binding_sha256": _owner_digest(ctx.binding), "argv": list(ARGV), "invocation_nonce": outcome.nonce,
              "timeout_seconds": TIMEOUT_SECONDS, "owner_envelope_seconds": OWNER_ENVELOPE_SECONDS,
              "started_monotonic_ns": outcome.started_ns, "ended_monotonic_ns": outcome.ended_ns,
              "service_started_monotonic_ns": outcome.service_started_ns,
              "service_grace_deadline_ns": outcome.service_deadline_ns, "job_artifact_deadline_ns": outcome.job_deadline_ns,
              "final_deadline_ns": final_deadline_ns, "checks_completed_monotonic_ns": time.monotonic_ns(),
              "returncode": returned if type(returned) is int else None,
              "signal": -returned if type(returned) is int and returned < 0 else None,
              "capture_complete": complete_capture, "cleanup_complete": cleanup,
              "reason_codes": sorted(reasons), "final_checks": final_checks,
              "terminal_reconciliation_required": True}
    check_deadline(final_deadline_ns)
    files = {"stdout.bin": outcome.stdout, "stderr.bin": outcome.stderr,
             "owner.json": canonical(owner, FILE_LIMITS["owner.json"], deadline_ns=final_deadline_ns),
             "generated-before.json": canonical(before, FILE_LIMITS["generated-before.json"], deadline_ns=final_deadline_ns),
             "generated-after.json": canonical(after, FILE_LIMITS["generated-after.json"], deadline_ns=final_deadline_ns),
             "runtime.json": canonical(runtime, FILE_LIMITS["runtime.json"], deadline_ns=final_deadline_ns),
             "environment.json": canonical(environment_record, FILE_LIMITS["environment.json"], deadline_ns=final_deadline_ns),
             "result.json": canonical(result, FILE_LIMITS["result.json"], deadline_ns=final_deadline_ns)}
    store = _Store(directory, final_deadline_ns)
    try:
        for name, raw in files.items():
            store.write(name, raw)
        manifest = {"schema_version": 1, "status": status, "files": dict(store.files),
                    "self_exclusion": "manifest.json", "content_sha256": _digest(canonical(store.files, 32768, deadline_ns=final_deadline_ns)),
                    "allocation_bytes": ALLOCATION_TOTAL, "raw_addition_limit": ADDITION_LIMIT,
                    "final_deadline_ns": final_deadline_ns, "completed_monotonic_ns": time.monotonic_ns(),
                    "qualified": False, "baseline_passed": False, "terminal_reconciliation_required": True}
        store.write("manifest.json", canonical(manifest, FILE_LIMITS["manifest.json"], deadline_ns=final_deadline_ns))
        actual_size = store.finish()
        _need(actual_size + final_checks["phases_complete"]["bytes"] <= ADDITION_LIMIT, "EVIDENCE_AGGREGATE_LIMIT")
        check_deadline(final_deadline_ns)
        persisted_ns = time.monotonic_ns()
        _need(persisted_ns < final_deadline_ns, "FINALIZATION_TIMEOUT")
        return {"schema_version": 1, "status": status, "qualified": False, "baseline_passed": False,
                "result_sha256": store.files["result.json"]["sha256"], "manifest_sha256": store.files["manifest.json"]["sha256"],
                "bytes": actual_size, "final_deadline_ns": final_deadline_ns, "persisted_monotonic_ns": persisted_ns,
                "reason_codes": sorted(reasons)}
    finally:
        store.close()
