# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Snapshot persistence per BASELINE-02 + invalidation per BASELINE-03.

SCHEMA_VERSION is independent of state.SCHEMA_VERSION (snapshot evolves
on different cadence from state.json).

B2 fix: NO Disposition import. finding_dispositions: dict[str, str] stores
disposition values as strings; state machine (02-02) converts to/from
Disposition at the read/write boundary.
"""
from __future__ import annotations

import hashlib
import json
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .errors import (
    BaselineResolutionError,
    CorruptedSnapshotError,
    SnapshotSchemaMismatchError,
)

SNAPSHOT_SCHEMA_VERSION: int = 1


@dataclass(frozen=True)
class SnapshotEntry:
    """One file's recorded state in the snapshot."""

    path: str  # path-as-posix relative to snapshot root (H3)
    content_hash: str  # SHA256 (text: normalized; binary: raw bytes)


@dataclass
class Snapshot:
    """A persisted baseline snapshot. BASELINE-02 + BASELINE-03."""

    schema_version: int = SNAPSHOT_SCHEMA_VERSION
    source_hash: str = ""
    files: list[SnapshotEntry] = field(default_factory=list)
    finding_dispositions: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class InvalidationResult:
    """BASELINE-03 partial-invalidation result.

    missing = files in snapshot but absent from current source.
    changed = files present in both but content hash differs.
    unchanged = files where snapshot hash matches current.
    added = files in current but not in snapshot.
    """

    missing: list[str]
    changed: list[str]
    unchanged: list[str]
    added: list[str]


def snapshot_path_for(source_hash: str, cwd: Path) -> Path:
    """Standard location: .code-forge/snapshots/<source-hash>.json under cwd."""
    return cwd / ".code-forge" / "snapshots" / ("%s.json" % source_hash)


def find_existing_snapshot(source_hash: str, cwd: Path) -> Optional[Path]:
    """H5 fix: snapshot auto-discovery helper.

    Returns the snapshot path if it exists, else None.
    """
    p = snapshot_path_for(source_hash, cwd)
    return p if p.exists() else None


SNAPSHOT_COUNT_WARN_THRESHOLD: int = 50


def save_snapshot(snapshot: Snapshot, path: Path) -> None:
    """Atomic write of snapshot file. Auto-creates parent dirs (D1).

    OQ2 fix: after write, count snapshot files in directory; if above
    threshold, warn user about manual cleanup.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(snapshot), indent=2))
    tmp.replace(path)

    snapshots = list(path.parent.glob("*.json"))
    if len(snapshots) > SNAPSHOT_COUNT_WARN_THRESHOLD:
        warnings.warn(
            "forge: %d snapshot files in %s; "
            "consider manual cleanup (no auto-GC in v2.0)"
            % (len(snapshots), path.parent),
            stacklevel=2,
        )


def load_snapshot(path: Path) -> Optional[Snapshot]:
    """Load snapshot. Returns None on missing file (BASELINE-03).

    Raises:
        CorruptedSnapshotError: JSON parse failure or missing/invalid
            fields in snapshot data.
        SnapshotSchemaMismatchError: schema_version mismatch.
    """
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise CorruptedSnapshotError(
            "cannot parse %s: %s" % (path, e)
        ) from e

    sv = data.get("schema_version")
    if sv != SNAPSHOT_SCHEMA_VERSION:
        raise SnapshotSchemaMismatchError(
            "snapshot schema_version=%s, forge expects %s; "
            "remove %s to start fresh" % (sv, SNAPSHOT_SCHEMA_VERSION, path)
        )

    try:
        return Snapshot(
            schema_version=data["schema_version"],
            source_hash=data["source_hash"],
            files=[SnapshotEntry(**e) for e in data.get("files", [])],
            finding_dispositions=dict(
                data.get("finding_dispositions", {})
            ),
        )
    except (KeyError, TypeError) as e:
        raise CorruptedSnapshotError(
            "invalid snapshot data in %s: %s" % (path, e)
        ) from e


def validate_snapshot(
    snapshot: Snapshot, current_files: list[Path], root: Path
) -> InvalidationResult:
    """BASELINE-03: classify files as unchanged/changed/added/missing.

    H6 fix: files outside root raise BaselineResolutionError with
    explicit file and root context.
    """
    snapshot_map = {e.path: e.content_hash for e in snapshot.files}
    current_map: dict[str, str] = {}
    for f in current_files:
        try:
            rel = f.relative_to(root).as_posix()
        except ValueError as e:
            raise BaselineResolutionError(
                "file %s is outside snapshot root %s "
                "-- cannot classify against snapshot" % (f, root)
            ) from e
        current_map[rel] = _hash_file(f)

    missing = sorted(p for p in snapshot_map if p not in current_map)
    added = sorted(p for p in current_map if p not in snapshot_map)
    changed = sorted(
        p
        for p in current_map
        if p in snapshot_map and current_map[p] != snapshot_map[p]
    )
    unchanged = sorted(
        p
        for p in current_map
        if p in snapshot_map and current_map[p] == snapshot_map[p]
    )

    return InvalidationResult(
        missing=missing,
        changed=changed,
        unchanged=unchanged,
        added=added,
    )


def _hash_file(path: Path) -> str:
    """SHA256 of file content.

    Text files use normalize_text (LF + trailing-ws strip).
    Binary files hash raw bytes (H1 fix).
    """
    from .source import normalize_text

    try:
        content = path.read_text(encoding="utf-8")
        return hashlib.sha256(
            normalize_text(content).encode("utf-8")
        ).hexdigest()
    except UnicodeDecodeError:
        return hashlib.sha256(path.read_bytes()).hexdigest()
