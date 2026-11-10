# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Private, bounded v1 diagnostic records for the cross-repository host.

The qualified domain is Linux local overlay/tmpfs/ext4 with descriptor-relative
operations, private owned directories, and inspectable ACL namespaces. This is
not a security boundary against hostile processes with the same effective UID.
In particular, checking an owned temporary inode before unlink is not an atomic
conditional unlink. There is no power-loss durability or filesystem timing claim.
The host owns observation admission, attempt limits, and independent context.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from dataclasses import dataclass
from uuid import UUID, uuid4

MAX_BYTES = 4096
MAX_OBSERVATIONS = 64
_MAX_ROOT_COMPONENTS = 64
_MAX_EXCLUDED_ROOTS = 256
MESSAGE = "cross-repo-impact: no named changed symbols resolved; direct-caller impact not evaluated"
_AXIS = "CROSS-REPO-IMPACT"
_CODE = "no-named-changed-symbols"
_STATES = frozenset({"not_requested", "not_run", "no_named_targets", "targets_resolved", "unavailable"})
_REASONS = frozenset(
    {
        "invalid_schema",
        "invalid_context",
        "context_mismatch",
        "unsupported_filesystem",
        "unsafe_storage",
        "temporary_storage",
        "storage_unavailable",
        "identity_mismatch",
        "content_mismatch",
        "write_failed",
        "not_published",
        "invalid_locator",
        "invalid_publication",
        "read_failed",
        "name_conflict",
    }
)
_PUBLICATION_FAILURES = frozenset(
    {
        "publication_failed",
        "commit_uncertain",
        "name_conflict",
        "observation_limit",
        "capture_unavailable",
        "diagnostic_unavailable",
    }
)
_FIELDS = frozenset(
    {
        "schema_version",
        "execution_id",
        "repository_binding",
        "axis",
        "observation_sequence",
        "source_hash",
        "diff_sha256",
        "analysis_state",
        "diagnostics",
    }
)


class DiagnosticError(ValueError):
    """A bounded failure code, never a serialized operating-system exception."""


@dataclass(frozen=True, slots=True)
class ExpectedContext:
    execution_id: str
    repository_id: str
    source_hash: str
    diff_sha256: str
    observation_sequence: int


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class Observation:
    context: ExpectedContext
    analysis_state: str
    diagnostics: tuple[Diagnostic, ...]


@dataclass(frozen=True, slots=True)
class _Identity:
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class StorageBinding:
    source_root: Path
    identity: _Identity


@dataclass(frozen=True, slots=True)
class Locator:
    binding: StorageBinding
    directories: tuple[tuple[str, _Identity], ...]
    filename: str
    identity: _Identity
    byte_count: int
    digest: str


@dataclass(frozen=True, slots=True)
class PublicationResult:
    status: str
    locator: Locator | None = None
    failure: str | None = None
    cleanup: str = "not_needed"


def _string(value: object, *, nonempty: bool = False) -> str:
    if type(value) is not str or len(value) > 256:
        raise DiagnosticError("invalid_schema")
    try:
        size = len(value.encode("utf-8", errors="strict"))
    except UnicodeError:
        raise DiagnosticError("invalid_schema") from None
    if size > 256 or (nonempty and not value):
        raise DiagnosticError("invalid_schema")
    return value


def _reason(exc: Exception, fallback: str) -> str:
    if (
        type(exc) is DiagnosticError
        and len(exc.args) == 1
        and type(exc.args[0]) is str
        and exc.args[0] in _REASONS
    ):
        return exc.args[0]
    return fallback


def _uuid(value: object) -> str:
    value = _string(value, nonempty=True)
    try:
        parsed = UUID(value)
    except ValueError:
        raise DiagnosticError("invalid_context") from None
    if str(parsed) != value or parsed.version != 4:
        raise DiagnosticError("invalid_context")
    return value


def _validate_context(context: ExpectedContext) -> None:
    if type(context) is not ExpectedContext:
        raise DiagnosticError("invalid_context")
    _uuid(context.execution_id)
    _uuid(context.repository_id)
    _string(context.source_hash, nonempty=True)
    digest = _string(context.diff_sha256)
    if re.fullmatch("[0-9a-f]{64}", digest) is None:
        raise DiagnosticError("invalid_context")
    if (
        type(context.observation_sequence) is not int
        or not 1 <= context.observation_sequence <= MAX_OBSERVATIONS
    ):
        raise DiagnosticError("invalid_context")


def make_observation(context: ExpectedContext, state: str) -> Observation:
    _validate_context(context)
    if type(state) is not str or state not in _STATES:
        raise DiagnosticError("invalid_schema")
    diagnostics = (Diagnostic(_CODE, MESSAGE),) if state == "no_named_targets" else ()
    return Observation(context, state, diagnostics)


def _payload(observation: Observation) -> bytes:
    if type(observation) is not Observation:
        raise DiagnosticError("invalid_schema")
    expected = make_observation(observation.context, observation.analysis_state)
    if (
        type(observation.diagnostics) is not tuple
        or len(observation.diagnostics) != len(expected.diagnostics)
        or any(type(item) is not Diagnostic for item in observation.diagnostics)
    ):
        raise DiagnosticError("invalid_schema")
    if observation != expected:
        raise DiagnosticError("invalid_schema")
    for item in observation.diagnostics:
        _string(item.code)
        _string(item.message)
    context = observation.context
    raw = json.dumps(
        {
            "schema_version": 1,
            "execution_id": context.execution_id,
            "repository_binding": {"kind": "invocation-repository", "id": context.repository_id},
            "axis": _AXIS,
            "observation_sequence": context.observation_sequence,
            "source_hash": context.source_hash,
            "diff_sha256": context.diff_sha256,
            "analysis_state": observation.analysis_state,
            "diagnostics": [
                {"code": item.code, "message": item.message} for item in observation.diagnostics
            ],
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8", errors="strict")
    if len(raw) > MAX_BYTES:
        raise DiagnosticError("invalid_schema")
    return raw


def _reject_number(_value: str):
    raise DiagnosticError("invalid_schema")


def _integer(value: str) -> int:
    # Neither schema integer can exceed two decimal digits. Bound conversion
    # independently of the interpreter's configurable integer-string limit.
    if len(value) > 2:
        raise DiagnosticError("invalid_schema")
    return int(value)


def _pairs(items: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in items:
        if key in result:
            raise DiagnosticError("invalid_schema")
        result[key] = value
    return result


def _scan_depth(text: str) -> None:
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "{[":
            depth += 1
            if depth > 3:
                raise DiagnosticError("invalid_schema")
        elif char in "}]":
            depth -= 1
            if depth < 0:
                raise DiagnosticError("invalid_schema")


def _validate_strings(value: object) -> None:
    if type(value) is str:
        _string(value)
    elif type(value) is dict:
        for key, item in value.items():
            _string(key)
            _validate_strings(item)
    elif type(value) is list:
        for item in value:
            _validate_strings(item)


def decode_observation(raw: bytes, expected: ExpectedContext) -> Observation:
    """Decode only bounded closed-schema bytes against separately held context."""
    _validate_context(expected)
    if type(raw) is not bytes or len(raw) > MAX_BYTES:
        raise DiagnosticError("invalid_schema")
    try:
        text = raw.decode("utf-8", errors="strict")
        _scan_depth(text)
        data = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_constant=_reject_number,
            parse_float=_reject_number,
            parse_int=_integer,
        )
    except (UnicodeError, ValueError, RecursionError):
        raise DiagnosticError("invalid_schema") from None
    _validate_strings(data)
    if type(data) is not dict or data.keys() != _FIELDS:
        raise DiagnosticError("invalid_schema")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1 or data["axis"] != _AXIS:
        raise DiagnosticError("invalid_schema")
    binding = data["repository_binding"]
    if (
        type(binding) is not dict
        or binding.keys() != {"kind", "id"}
        or binding["kind"] != "invocation-repository"
    ):
        raise DiagnosticError("invalid_schema")
    context = ExpectedContext(
        data["execution_id"],
        binding["id"],
        data["source_hash"],
        data["diff_sha256"],
        data["observation_sequence"],
    )
    _validate_context(context)
    if context != expected:
        raise DiagnosticError("context_mismatch")
    observation = make_observation(context, data["analysis_state"])
    wanted = [{"code": item.code, "message": item.message} for item in observation.diagnostics]
    if type(data["diagnostics"]) is not list or data["diagnostics"] != wanted:
        raise DiagnosticError("invalid_schema")
    return observation


def _identity(info: os.stat_result) -> _Identity:
    return _Identity(info.st_dev, info.st_ino)


def _bounded_proc(path: str, cap: int) -> bytes:
    with open(path, "rb") as stream:
        result = stream.read(cap + 1)
    if len(result) > cap:
        raise DiagnosticError("unsupported_filesystem")
    return result


def _filesystem(fd: int, info: os.stat_result) -> None:
    if sys.platform != "linux":
        raise DiagnosticError("unsupported_filesystem")
    fdinfo = _bounded_proc(f"/proc/self/fdinfo/{fd}", 4096)
    ids = [line.split()[1:] for line in fdinfo.splitlines() if line.startswith(b"mnt_id:")]
    if len(ids) != 1 or len(ids[0]) != 1 or not ids[0][0].isdigit():
        raise DiagnosticError("unsupported_filesystem")
    mountinfo = _bounded_proc("/proc/self/mountinfo", 1024 * 1024)
    matches = [line for line in mountinfo.splitlines() if line.split(maxsplit=1)[0] == ids[0][0]]
    if len(matches) != 1:
        raise DiagnosticError("unsupported_filesystem")
    before, separator, after = matches[0].partition(b" - ")
    columns, details = before.split(), after.split()
    device = f"{os.major(info.st_dev)}:{os.minor(info.st_dev)}".encode("ascii")
    if (
        not separator
        or len(columns) < 6
        or len(details) < 3
        or columns[2] != device
        or details[0] not in {b"overlay", b"tmpfs", b"ext4"}
    ):
        raise DiagnosticError("unsupported_filesystem")


def _private(fd: int, *, directory: bool) -> os.stat_result:
    info = os.fstat(fd)
    kind_ok = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not kind_ok or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise DiagnosticError("unsafe_storage")
    _filesystem(fd, info)
    for name in ("system.posix_acl_access", "system.posix_acl_default"):
        try:
            os.getxattr(fd, name)
        except OSError as exc:
            # On an already qualified local Linux filesystem, ENOTSUP means
            # this ACL namespace is unsupported, not an unreadable ACL value.
            if exc.errno not in {errno.ENODATA, errno.ENOTSUP, errno.EOPNOTSUPP}:
                raise DiagnosticError("unsafe_storage") from None
        else:
            raise DiagnosticError("unsafe_storage")
    return info


class _Descriptors:
    """Close every held descriptor without masking an existing exception."""

    def __init__(self) -> None:
        self.items: list[int] = []
        self.primary: BaseException | None = None

    def add(self, fd: int) -> int:
        self.items.append(fd)
        return fd

    def __enter__(self):
        return self

    def __exit__(self, _kind, primary, _tb):
        first = None
        for fd in reversed(self.items):
            try:
                os.close(fd)
            except BaseException as exc:  # noqa: BLE001 -- Close every fd while preserving a primary exception.
                if first is None or (isinstance(first, Exception) and not isinstance(exc, Exception)):
                    first = exc
        if primary is None and self.primary is None and first is not None:
            raise first
        return False


def _open_source(root: Path, descriptors: _Descriptors) -> int:
    _validate_root(root)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = descriptors.add(os.open("/", flags))
    for part in root.parts[1:]:
        fd = descriptors.add(os.open(part, flags, dir_fd=fd))
    _private(fd, directory=True)
    return fd


def bind_storage(source_root: Path, excluded_roots: tuple[Path, ...] = ()) -> StorageBinding:
    """Retain source identity before worker use, without open descriptors."""
    try:
        _validate_root(source_root)
        if type(excluded_roots) is not tuple or len(excluded_roots) > _MAX_EXCLUDED_ROOTS:
            raise DiagnosticError("unsafe_storage")
        for excluded in excluded_roots:
            _validate_root(excluded)
        if source_root != source_root.resolve(strict=True):
            raise DiagnosticError("unsafe_storage")
        durable = source_root / ".code-forge" / "advisory-diagnostics"
        for excluded in excluded_roots:
            for alias in (excluded.absolute(), excluded.resolve()):
                if durable.is_relative_to(alias) or alias.is_relative_to(durable):
                    raise DiagnosticError("temporary_storage")
        with _Descriptors() as descriptors:
            fd = _open_source(source_root, descriptors)
            return StorageBinding(source_root, _identity(os.fstat(fd)))
    except Exception as exc:  # noqa: BLE001 -- Boundary errors carry only a closed reason, never raw private paths.
        raise DiagnosticError(_reason(exc, "storage_unavailable")) from None


def _open_binding(binding: StorageBinding, descriptors: _Descriptors) -> int:
    if type(binding) is not StorageBinding or type(binding.identity) is not _Identity:
        raise DiagnosticError("unsafe_storage")
    fd = _open_source(binding.source_root, descriptors)
    if _identity(os.fstat(fd)) != binding.identity:
        raise DiagnosticError("identity_mismatch")
    return fd


def _directory(parent: int, name: str, descriptors: _Descriptors, *, create: bool) -> int:
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent)
        except FileExistsError:
            pass
    fd = descriptors.add(
        os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    )
    _private(fd, directory=True)
    return fd


def _read_leaf(
    parent: int, name: str, identity: _Identity, size: int, digest: str, descriptors: _Descriptors
) -> bytes:
    fd = descriptors.add(
        os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
    )
    before = _private(fd, directory=False)
    if _identity(before) != identity or before.st_size != size or size > MAX_BYTES:
        raise DiagnosticError("identity_mismatch")
    chunks = []
    remaining = MAX_BYTES + 1
    while remaining:
        chunk = os.read(fd, remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    raw = b"".join(chunks)
    after = os.fstat(fd)
    # inode/size plus mtime/ctime catch observed concurrent edits; digest binds
    # accepted content even when the same-sized byte sequence was overwritten.
    if (
        _identity(after) != identity
        or after.st_size != size
        or len(raw) != size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ctime_ns != after.st_ctime_ns
        or hashlib.sha256(raw).hexdigest() != digest
    ):
        raise DiagnosticError("content_mismatch")
    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if _identity(current) != identity or current.st_size != size or not stat.S_ISREG(current.st_mode):
        raise DiagnosticError("identity_mismatch")
    return raw


def _cleanup_temp(parent: int, name: str, identity: _Identity) -> str:
    try:
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return "complete"
    if _identity(current) != identity or not stat.S_ISREG(current.st_mode):
        return "failed"
    os.unlink(name, dir_fd=parent)
    return "complete"


def publish_observation(binding: StorageBinding, observation: Observation) -> PublicationResult:
    """Complete-file, exclusive publication; never adopt or replace a record."""
    status, failure, cleanup = "not_published", None, "not_needed"
    locator = None
    parent = None
    temporary = None
    owned = None
    attempted = False
    primary = None
    try:
        with _Descriptors() as descriptors:
            try:
                raw = _payload(observation)
                context = observation.context
                digest = hashlib.sha256(raw).hexdigest()
                parent = _open_binding(binding, descriptors)
                directories = []
                for name in (
                    ".code-forge",
                    "advisory-diagnostics",
                    context.execution_id,
                    context.repository_id,
                    _AXIS,
                ):
                    parent = _directory(parent, name, descriptors, create=True)
                    directories.append((name, _identity(os.fstat(parent))))
                filename = f"{context.observation_sequence}.json"
                try:
                    os.stat(filename, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    # Refuse a known existing name before the commit boundary.
                    # An exception from link itself never proves nonpublication.
                    raise DiagnosticError("name_conflict")
                temporary = f".pending-{uuid4()}"
                fd = descriptors.add(
                    os.open(
                        temporary,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                        0o600,
                        dir_fd=parent,
                    )
                )
                cleanup = "failed"
                owned = _identity(os.fstat(fd))
                _private(fd, directory=False)
                view = memoryview(raw)
                while view:
                    count = os.write(fd, view)
                    if count <= 0:
                        raise DiagnosticError("write_failed")
                    view = view[count:]
                # No fsync/durable journal claim: completion and visibility, not
                # recovery after power loss, are the supported contract.
                candidate = Locator(binding, tuple(directories), filename, owned, len(raw), digest)
                attempted = True
                try:
                    os.link(
                        temporary, filename, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False
                    )
                    _read_leaf(parent, filename, owned, len(raw), digest, descriptors)
                except Exception:  # noqa: BLE001 -- A commit exception is not proof that publication failed.
                    try:
                        _read_leaf(parent, filename, owned, len(raw), digest, descriptors)
                    except Exception:  # noqa: BLE001 -- Unverifiable commit remains unknown; no retry or old-record fallback.
                        status, failure = "unknown", "commit_uncertain"
                    else:
                        status, locator = "published", candidate
                else:
                    status, locator = "published", candidate
            except (
                BaseException
            ) as exc:  # Retain primary signals while separately cleaning an owned temporary name.
                primary = exc
                descriptors.primary = exc
                if not isinstance(exc, Exception):
                    raise
                status = "unknown" if attempted else "not_published"
                failure = "commit_uncertain" if attempted else "publication_failed"
                if not attempted and type(exc) is DiagnosticError and str(exc) == "name_conflict":
                    failure = "name_conflict"
            finally:
                if parent is not None and temporary is not None and owned is not None:
                    try:
                        cleanup = _cleanup_temp(parent, temporary, owned)
                    except (
                        BaseException
                    ) as exc:  # Cleanup cannot erase publication or replace a primary exception.
                        cleanup = "failed"
                        if primary is None and not isinstance(exc, Exception):
                            raise
    except Exception:  # noqa: BLE001 -- Descriptor-close errors are ordinary storage failures, not gate failures.
        if status != "published":
            status, failure = (
                ("unknown", "commit_uncertain") if attempted else ("not_published", "publication_failed")
            )
    return PublicationResult(status, locator, failure, cleanup)


def _validate_identity(identity: _Identity) -> None:
    if (
        type(identity) is not _Identity
        or type(identity.device) is not int
        or type(identity.inode) is not int
        or not 0 <= identity.device < 2**64
        or not 0 < identity.inode < 2**64
    ):
        raise DiagnosticError("invalid_locator")


def _validate_root(root: Path) -> None:
    """Bound path bytes and simultaneously held directory fds before any I/O."""
    if type(root) is not type(Path()):
        raise DiagnosticError("invalid_locator")
    if root.anchor != "/" or not root.is_absolute() or len(root.parts) > _MAX_ROOT_COMPONENTS:
        raise DiagnosticError("invalid_locator")
    size = 0
    for part in root.parts[1:]:
        if len(part) > 255 or part in {".", ".."} or "\0" in part:
            raise DiagnosticError("invalid_locator")
        try:
            part_size = len(part.encode("utf-8", errors="strict"))
        except UnicodeError:
            raise DiagnosticError("invalid_locator") from None
        if part_size > 255:
            raise DiagnosticError("invalid_locator")
        size += part_size + 1
        if size > 4095:
            raise DiagnosticError("invalid_locator")


def _validate_binding(binding: StorageBinding) -> None:
    # Shape only: neither resolve/stat nor a claim that this path is still owned.
    # The reader subsequently establishes that through held directory fds.
    if type(binding) is not StorageBinding:
        raise DiagnosticError("invalid_locator")
    _validate_root(binding.source_root)
    _validate_identity(binding.identity)


def validate_publication(publication: PublicationResult, expected: ExpectedContext) -> None:
    """Validate a bounded host-held result without I/O or trusting its claims.

    Call before retaining callback results. Successful shape validation is not
    proof of publication, filesystem qualification, or ownership; strict reading
    must still reopen and verify the retained identities and bytes.
    """
    _validate_context(expected)
    if (
        type(publication) is not PublicationResult
        or type(publication.status) is not str
        or len(publication.status) > 16
        or publication.status not in {"published", "not_published", "unknown"}
        or type(publication.cleanup) is not str
        or len(publication.cleanup) > 16
        or publication.cleanup not in {"not_needed", "complete", "failed"}
    ):
        raise DiagnosticError("invalid_publication")
    if publication.status != "published":
        if (
            publication.locator is not None
            or type(publication.failure) is not str
            or len(publication.failure) > 32
            or publication.failure not in _PUBLICATION_FAILURES
        ):
            raise DiagnosticError("invalid_publication")
        return
    if publication.failure is not None or type(publication.locator) is not Locator:
        raise DiagnosticError("invalid_publication")
    locator = publication.locator
    wanted = (
        ".code-forge",
        "advisory-diagnostics",
        expected.execution_id,
        expected.repository_id,
        _AXIS,
    )
    if (
        type(locator.directories) is not tuple
        or len(locator.directories) != len(wanted)
        or type(locator.filename) is not str
        or locator.filename != f"{expected.observation_sequence}.json"
        or type(locator.byte_count) is not int
        or not 0 < locator.byte_count <= MAX_BYTES
        or type(locator.digest) is not str
        or len(locator.digest) != 64
        or re.fullmatch("[0-9a-f]{64}", locator.digest) is None
    ):
        raise DiagnosticError("invalid_locator")
    for item, name in zip(locator.directories, wanted, strict=True):
        if type(item) is not tuple or len(item) != 2 or type(item[0]) is not str or item[0] != name:
            raise DiagnosticError("invalid_locator")
        _validate_identity(item[1])
    _validate_identity(locator.identity)
    _validate_binding(locator.binding)


def read_observation(publication: PublicationResult, expected: ExpectedContext) -> Observation:
    """Reopen every retained identity after publisher descriptors have closed."""
    try:
        validate_publication(publication, expected)
        if publication.status != "published":
            raise DiagnosticError("not_published")
        locator = publication.locator
        with _Descriptors() as descriptors:
            parent = _open_binding(locator.binding, descriptors)
            for name, identity in locator.directories:
                parent = _directory(parent, name, descriptors, create=False)
                if _identity(os.fstat(parent)) != identity:
                    raise DiagnosticError("identity_mismatch")
            raw = _read_leaf(
                parent,
                locator.filename,
                locator.identity,
                locator.byte_count,
                locator.digest,
                descriptors,
            )
        return decode_observation(raw, expected)
    except Exception as exc:  # noqa: BLE001 -- Strict reader failures expose only bounded classes to the host.
        raise DiagnosticError(_reason(exc, "read_failed")) from None
