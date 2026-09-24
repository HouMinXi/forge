"""Run state-root layout, worker reservation and ownership records.

Implements the specification's state obligations: a private operator
state root with one directory per run, an atomic worker reservation
(one active run per worker, concurrency is one), an owner record
pinning boot and process-start identity, atomic result publication,
and on-demand dead-run recovery.

Recovery never deletes what it cannot verify: an unreadable or
schema-invalid owner record yields a hold outcome with operator
guidance.  Reclaiming is allowed only after the owner is proven dead
via pid liveness, process start ticks and boot identifier, and only
the exact manifest-owned run directory is removed.  No polling
service is introduced; recovery runs when the run is opened.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from code_forge.mutation_engines.schemas import valid_identifier

MAX_PUBLISH_BYTES = 64 * 1024 * 1024

_OWNER_FIELDS = frozenset({
    "schema_version", "state_root", "run_id", "pid", "boot_id",
    "start_ticks", "cgroup_path", "supervisor_thread", "created_utc",
})


class StateError(Exception):
    """Raised for layout, validation and publication failures."""


class SecondHolderError(StateError):
    """Raised when a worker is already reserved by a live holder."""


@dataclass(frozen=True)
class OwnerRecord:
    """Ownership identity for a run (spec dead-run recovery rules)."""

    schema_version: int
    state_root: str
    run_id: str
    pid: int
    boot_id: str
    start_ticks: int
    cgroup_path: str
    supervisor_thread: str
    created_utc: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "state_root": self.state_root,
            "run_id": self.run_id,
            "pid": self.pid,
            "boot_id": self.boot_id,
            "start_ticks": self.start_ticks,
            "cgroup_path": self.cgroup_path,
            "supervisor_thread": self.supervisor_thread,
            "created_utc": self.created_utc,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OwnerRecord:
        if not isinstance(data, dict):
            raise StateError("owner record must be a mapping")
        keys = set(data.keys())
        if keys != _OWNER_FIELDS:
            raise StateError(
                "owner record fields must be exactly %s" % sorted(_OWNER_FIELDS)
            )
        if data["schema_version"] != 1:
            raise StateError("owner record schema_version must be 1")
        for field in ("pid", "start_ticks"):
            value = data[field]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise StateError("owner record %s must be a non-negative int" % field)
        for field in (
            "state_root", "run_id", "boot_id", "cgroup_path",
            "supervisor_thread", "created_utc",
        ):
            if not isinstance(data[field], str) or not data[field]:
                raise StateError("owner record %s must be a nonempty string" % field)
        return cls(**data)


@dataclass(frozen=True)
class RecoveryOutcome:
    """The result of an on-demand dead-run recovery attempt."""

    action: str  # "reclaimed" | "refused_live_owner" | "hold" | "no_run"
    detail: str


@dataclass(frozen=True)
class Reservation:
    """A held worker reservation; release() frees it."""

    worker_id: str
    lock_path: Path

    def release(self) -> None:
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            pass


def current_boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def _process_start_ticks(pid: int) -> int | None:
    """Start time (clock ticks) of *pid*, or None when not readable."""
    try:
        text = Path("/proc/%d/stat" % pid).read_text()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None
    # field 1 may contain spaces inside parentheses; split after ") "
    try:
        rest = text[text.rindex(")") + 2:]
        return int(rest.split()[19])  # field 22 overall
    except (ValueError, IndexError):
        return None


def run_dir(state_root: str | Path, run_id: str) -> Path:
    """Return (creating) the run directory for a validated run id."""
    if not run_id or not isinstance(run_id, str) or not valid_identifier(run_id):
        raise StateError("run id must be an identifier, got %r" % (run_id,))
    path = Path(state_root) / "runs" / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def make_owner_record(
    *,
    state_root: str,
    run_id: str,
    pid: int,
    cgroup_path: str,
    supervisor_thread: str,
) -> OwnerRecord:
    """Capture boot and process-start identity for *pid*."""
    ticks = _process_start_ticks(pid)
    if ticks is None:
        raise StateError("cannot read start identity for pid %d" % pid)
    return OwnerRecord(
        schema_version=1,
        state_root=state_root,
        run_id=run_id,
        pid=pid,
        boot_id=current_boot_id(),
        start_ticks=ticks,
        cgroup_path=cgroup_path,
        supervisor_thread=supervisor_thread,
        created_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )


def write_owner(directory: Path, owner: OwnerRecord) -> None:
    payload = json.dumps(owner.to_dict(), sort_keys=True).encode("utf-8")
    _atomic_write(directory / "owner.json", payload)


def read_owner(directory: Path) -> OwnerRecord:
    path = directory / "owner.json"
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise StateError("owner record missing in %s" % directory) from None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise StateError("owner record is not valid JSON: %s" % exc) from None
    return OwnerRecord.from_dict(parsed)


def is_owner_alive(owner: OwnerRecord) -> bool:
    """True only when pid, start ticks and boot id all still match."""
    if owner.boot_id != current_boot_id():
        return False
    ticks = _process_start_ticks(owner.pid)
    if ticks is None:
        return False
    return ticks == owner.start_ticks


def reserve_worker(
    state_root: str | Path, worker_id: str, owner: OwnerRecord
) -> Reservation:
    """Reserve *worker_id* atomically; reject a second live holder."""
    if not valid_identifier(worker_id):
        raise StateError("worker id must be an identifier, got %r" % (worker_id,))
    locks = Path(state_root) / "workers"
    locks.mkdir(parents=True, exist_ok=True)
    lock = locks / (worker_id + ".lock")
    payload = json.dumps(owner.to_dict(), sort_keys=True).encode("utf-8")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise SecondHolderError(
            "worker %r is already reserved" % worker_id
        ) from None
    with os.fdopen(fd, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return Reservation(worker_id=worker_id, lock_path=lock)


def publish(directory: Path, name: str, payload: bytes) -> None:
    """Atomically publish *payload* under results/ (tmp + fsync + rename)."""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise StateError("publication name must be a plain file name, got %r" % name)
    if len(payload) > MAX_PUBLISH_BYTES:
        raise StateError(
            "publication %r exceeds %d bytes" % (name, MAX_PUBLISH_BYTES)
        )
    results = directory / "results"
    results.mkdir(parents=True, exist_ok=True)
    _atomic_write(results / name, payload)


def read_published(directory: Path, name: str) -> bytes:
    try:
        return (directory / "results" / name).read_bytes()
    except FileNotFoundError:
        raise StateError("no published artifact %r" % name) from None


def _atomic_write(path: Path, payload: bytes) -> None:
    tmp = path.with_name(
        ".tmp-%d-%s-%s" % (os.getpid(), os.urandom(6).hex(), path.name)
    )
    try:
        with open(tmp, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.rename(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def recover_run(state_root: str | Path, run_id: str) -> RecoveryOutcome:
    """On-demand dead-run recovery (spec: never delete the unverifiable)."""
    directory = Path(state_root) / "runs" / run_id
    if not directory.is_dir():
        return RecoveryOutcome("no_run", "run directory does not exist")
    try:
        owner = read_owner(directory)
    except StateError as exc:
        return RecoveryOutcome(
            "hold",
            "owner record unverifiable (%s); reclaim manually after inspection"
            % exc,
        )
    if is_owner_alive(owner):
        return RecoveryOutcome(
            "refused_live_owner",
            "owner pid %d is still alive" % owner.pid,
        )
    if owner.state_root != str(state_root):
        return RecoveryOutcome(
            "hold",
            "owner record names a different state root; refusing to delete",
        )
    for child in sorted(directory.rglob("*"), reverse=True):
        if child.is_dir() and not child.is_symlink():
            child.rmdir()
        else:
            child.unlink()
    directory.rmdir()
    return RecoveryOutcome("reclaimed", "dead owner run reclaimed")
