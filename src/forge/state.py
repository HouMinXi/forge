# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""state.json schema + IO.

Schema owned by 02-01. Subsequent sub-plans add fields ADDITIVELY (no rename,
no remove). Bump SCHEMA_VERSION on breaking change.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal, Optional

from .disposition import Disposition, DISPOSITION_PROTOCOL_VERSION
from .errors import CorruptedStateError, SchemaVersionMismatchError

SCHEMA_VERSION: int = 1


class Mode(str, Enum):
    """Forge execution mode. Resolved by 02-05, consumed by 02-02."""
    LOCAL = "LOCAL"
    CI = "CI"


class Verdict(str, Enum):
    """Process verdict (terminal). Set by state machine on exit."""
    PASS = "PASS"
    FAIL = "FAIL"
    ESCALATED = "ESCALATED"
    PENDING = "PENDING"


@dataclass
class StateFinding:
    """A single finding entry in state.json findings[].

    Named StateFinding (not Finding) to avoid conflict with Phase 1
    forge.parsers.base.Finding (parser-emitted record, different shape).
    Conversion: state machine in 02-02 maps parsers.base.Finding ->
    StateFinding.
    """
    id: str
    fingerprint: str
    source: Literal["L0", "L1"]
    disposition: Disposition
    file: str
    line_range: list[int]
    description: str
    error: Optional[str] = None
    anchor: Optional[dict] = None
    evidence_files: Optional[list[str]] = None


@dataclass
class State:
    """state.json schema. v1."""
    schema_version: int = SCHEMA_VERSION
    disposition_protocol_version: int = DISPOSITION_PROTOCOL_VERSION
    round: int = 0
    mode: Mode = Mode.LOCAL
    source_hash: Optional[str] = None
    findings: list[StateFinding] = field(default_factory=list)
    # Derived lookup cache (NOT source of truth; SOT = StateFinding.disposition).
    # save_state rebuilds from findings; load_state verifies cache matches.
    dispositions: dict[str, Disposition] = field(default_factory=dict)
    fix_attempts: dict[str, int] = field(default_factory=dict)
    verdict: Verdict = Verdict.PENDING
    converged: bool = False


def _finding_from_dict(d: dict) -> StateFinding:
    """Reconstruct StateFinding from JSON dict with enum conversion."""
    return StateFinding(
        id=d["id"],
        fingerprint=d["fingerprint"],
        source=d["source"],
        disposition=Disposition(d["disposition"]),
        file=d["file"],
        line_range=list(d["line_range"]),
        description=d["description"],
        error=d.get("error"),
        anchor=d.get("anchor"),
        evidence_files=d.get("evidence_files"),
    )


def load_state(path: Path) -> Optional[State]:
    """Load state.json. Returns None if file does not exist.

    Raises:
        CorruptedStateError: JSON parse failure, missing/invalid fields,
            invalid enum values, or cache mismatch.
        SchemaVersionMismatchError: schema_version != SCHEMA_VERSION.
    """
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise CorruptedStateError(
            "cannot parse %s: %s" % (path, e)
        ) from e

    sv = data.get("schema_version")
    if sv != SCHEMA_VERSION:
        raise SchemaVersionMismatchError(
            "state.json schema_version=%s, forge expects %s; "
            "remove .forge/state.json to start fresh" % (sv, SCHEMA_VERSION)
        )

    try:
        findings = [
            _finding_from_dict(f) for f in data.get("findings", [])
        ]
        dispositions = {
            k: Disposition(v)
            for k, v in data.get("dispositions", {}).items()
        }
    except (KeyError, ValueError) as e:
        raise CorruptedStateError(
            "invalid finding or disposition in %s: %s" % (path, e)
        ) from e

    expected = {f.id: f.disposition for f in findings}
    if dispositions != expected:
        raise CorruptedStateError(
            "dispositions cache out of sync with findings (path=%s)" % path
        )

    try:
        return State(
            schema_version=data["schema_version"],
            disposition_protocol_version=data[
                "disposition_protocol_version"
            ],
            round=data["round"],
            mode=Mode(data["mode"]),
            source_hash=data.get("source_hash"),
            findings=findings,
            dispositions=dispositions,
            fix_attempts=dict(data.get("fix_attempts", {})),
            verdict=Verdict(data["verdict"]),
            converged=bool(data["converged"]),
        )
    except (KeyError, ValueError) as e:
        raise CorruptedStateError(
            "missing or invalid field in %s: %s" % (path, e)
        ) from e


def save_state(state: State, path: Path) -> None:
    """Atomic write of state.json. Rebuilds dispositions cache first."""
    state.dispositions = {f.id: f.disposition for f in state.findings}
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(state), default=str, indent=2))
    tmp.replace(path)
