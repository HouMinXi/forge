"""Explicit caller-trusted diagnostic tickets, not authenticated or current authority.

Tickets contain private absolute paths and execution/input identifiers. The caller
must protect and explicitly select them; same-UID tampering cannot be prevented.
No latest search, source rebinding, migration, freshness or recovery permission.
"""

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import stat
from uuid import uuid4
from . import _advisory_diagnostics as storage

MAX_TICKET_BYTES = 32768
_FORMAT = "forge-diagnostic-history-ticket-v1"


class TicketError(ValueError):
    """Bounded failure code. An export failure may leave a committed ticket."""


def _fail():
    raise TicketError("invalid_ticket")


def _integer(token):
    if len(token) > 20:
        _fail()
    return int(token)


def _object(value, keys):
    if type(value) is not dict or value.keys() != set(keys):
        _fail()
    return value


def _identity(value):
    item = _object(value, ("device", "inode"))
    result = storage._Identity(item["device"], item["inode"])
    storage._validate_identity(result)
    return result


def _decode(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_TICKET_BYTES:
        _fail()
    text = raw.decode("utf-8", errors="strict")
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
            if depth > 6:
                _fail()
        elif char in "}]":
            depth -= 1
            if depth < 0:
                _fail()
    data = json.loads(
        text,
        object_pairs_hook=storage._pairs,
        parse_int=_integer,
        parse_float=storage._reject_number,
        parse_constant=storage._reject_number,
    )
    _object(data, ("format", "expected", "locator"))
    if data["format"] != _FORMAT:
        _fail()
    context = _object(
        data["expected"],
        ("execution_id", "repository_id", "source_hash", "diff_sha256", "observation_sequence"),
    )
    expected = storage.ExpectedContext(**context)
    storage._validate_context(expected)
    loc = _object(
        data["locator"],
        ("root", "root_identity", "directories", "filename", "identity", "byte_count", "digest"),
    )
    if type(loc["root"]) is not str or len(loc["root"]) > 4095:
        _fail()
    root = Path(loc["root"])
    if str(root) != loc["root"]:
        _fail()
    binding = storage.StorageBinding(root, _identity(loc["root_identity"]))
    if type(loc["directories"]) is not list or len(loc["directories"]) != 5:
        _fail()
    directories = []
    for entry in loc["directories"]:
        if type(entry) is not list or len(entry) != 2 or type(entry[0]) is not str:
            _fail()
        directories.append((entry[0], _identity(entry[1])))
    locator = storage.Locator(
        binding,
        tuple(directories),
        loc["filename"],
        _identity(loc["identity"]),
        loc["byte_count"],
        loc["digest"],
    )
    publication = storage.PublicationResult("published", locator)
    storage.validate_publication(publication, expected)
    return publication, expected


def _directory(path, descriptors):
    storage._validate_root(path)
    if path != path.resolve(strict=True):
        _fail()
    fd = storage._open_source(path, descriptors)
    if stat.S_IMODE(os.fstat(fd).st_mode) != 0o700:
        _fail()
    return fd


def _read_leaf(parent, name, descriptors):
    fd = descriptors.add(
        os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
    )
    before = storage._private(fd, directory=False)
    if stat.S_IMODE(before.st_mode) != 0o600 or not 0 < before.st_size <= MAX_TICKET_BYTES:
        _fail()
    chunks = []
    remaining = MAX_TICKET_BYTES + 1
    while remaining:
        chunk = os.read(fd, remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    raw = b"".join(chunks)
    after = os.fstat(fd)
    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if (
        len(raw) != before.st_size
        or len(raw) > MAX_TICKET_BYTES
        or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        or storage._identity(current) != storage._identity(before)
        or not stat.S_ISREG(current.st_mode)
    ):
        _fail()
    return raw, storage._identity(before)


def export_ticket(
    directory: Path, publication: storage.PublicationResult, expected: storage.ExpectedContext
) -> Path:
    """Strict-read first, then exclusively save a caller-protected credential.

    Does not change permissions, create directories or overwrite a prior ticket.
    A failed/cancelled call may have committed its final name; never blindly retry.
    """
    temporary = owned = parent = None
    primary = None
    try:
        storage.read_observation(publication, expected)
        loc = publication.locator
        document = {
            "format": _FORMAT,
            "expected": asdict(expected),
            "locator": {
                "root": str(loc.binding.source_root),
                "root_identity": asdict(loc.binding.identity),
                "directories": [[name, asdict(identity)] for name, identity in loc.directories],
                "filename": loc.filename,
                "identity": asdict(loc.identity),
                "byte_count": loc.byte_count,
                "digest": loc.digest,
            },
        }
        raw = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
            "utf-8"
        )
        _decode(raw)
        filename = f"{uuid4()}.diagnostic-ticket.json"
        with storage._Descriptors() as descriptors:
            try:
                parent = _directory(directory, descriptors)
                directory_identity = storage._identity(os.fstat(parent))
                temporary = f".diagnostic-ticket-pending-{uuid4()}"
                fd = descriptors.add(
                    os.open(
                        temporary,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                        0o600,
                        dir_fd=parent,
                    )
                )
                owned = storage._identity(os.fstat(fd))
                storage._private(fd, directory=False)
                if stat.S_IMODE(os.fstat(fd).st_mode) != 0o600:
                    _fail()
                view = memoryview(raw)
                while view:
                    count = os.write(fd, view)
                    if count <= 0:
                        _fail()
                    view = view[count:]
                os.link(temporary, filename, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                saved, identity = _read_leaf(parent, filename, descriptors)
                if identity != owned or hashlib.sha256(saved).digest() != hashlib.sha256(raw).digest():
                    _fail()
                reopened = _directory(directory, descriptors)
                if storage._identity(os.fstat(reopened)) != directory_identity:
                    _fail()
            except BaseException as exc:
                primary = exc
                raise
            finally:
                if parent is not None and temporary is not None and owned is not None:
                    try:
                        if storage._cleanup_temp(parent, temporary, owned) != "complete":
                            _fail()
                    except BaseException:
                        if primary is None:
                            raise
        return directory / filename
    except Exception:  # noqa: BLE001 -- Never expose private paths or raw storage errors.
        raise TicketError("ticket_export_failed") from None


def read_ticket(path: Path) -> storage.Observation:
    """Read an explicitly caller-trusted ticket; mode checks are not authenticity.

    Returns historical v1 state and bounded diagnostics only. The caller, not this unsigned file, supplies
    the decision to trust this saved identity. Same-UID attackers can forge it.
    """
    try:
        storage._validate_root(path)
        with storage._Descriptors() as descriptors:
            parent = _directory(path.parent, descriptors)
            raw, _ = _read_leaf(parent, path.name, descriptors)
        publication, expected = _decode(raw)
        return storage.read_observation(publication, expected)
    except Exception:  # noqa: BLE001 -- Return only a bounded diagnostic category.
        raise TicketError("ticket_read_failed") from None


def validate_ticket_directory(directory: Path) -> None:
    """Validate the explicit existing private directory; do not create or chmod."""
    try:
        with storage._Descriptors() as descriptors:
            _directory(directory, descriptors)
    except Exception:  # noqa: BLE001 -- Bounded user-facing error, no private path echo.
        raise TicketError("invalid_ticket_directory") from None
