# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""state.json schema + IO.

Schema owned by 02-01. Subsequent sub-plans add fields ADDITIVELY (no rename,
no remove). Bump SCHEMA_VERSION on breaking change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
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
    source: Literal["L0", "L1", "MUTANT", "E2E_CHECK", "COVERAGE"]
    disposition: Disposition
    file: str
    line_range: list[int]
    description: str
    error: Optional[str] = None
    anchor: Optional[dict] = None
    evidence_files: Optional[list[str]] = None


@dataclass
class State:
    """state.json schema. v1.

    02-02 additions (additive only, no schema_version bump per D2):
      - baseline_spec_repr: from 02-03 serialize_baseline_spec; recorded so
        HOLD resume can verify which baseline was used (OQ1 fix from 02-03)
      - round_history: per-round snapshots for STATE-05 diagnosis
      - infra_errors: error messages collected during L0/L1/falsify failures
        (drives STATE-05 Category D classification)

    02-04 additions (additive per D2):
      - hold_reason: Optional[str] -- set on HOLD entry; cleared on resume.
        Disambiguates "interrupted mid-run" from "HOLD pending human input".
      - promoted_fingerprints: set[str] -- fingerprints promoted CONFIRMED ->
        UNCERTAIN via DISPO-05. Used by ESCALATED-frozen predicate.
        Serialized as sorted list (JSON has no native set type).
    """
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
    # 02-02 additions:
    baseline_spec_repr: Optional[str] = None
    round_history: list[dict] = field(default_factory=list)
    infra_errors: list[str] = field(default_factory=list)
    # 02-04 additions:
    hold_reason: Optional[str] = None
    promoted_fingerprints: set[str] = field(default_factory=set)
    # Mutation survivor round counter (LOCAL mode):
    consecutive_survivor_rounds: int = 0  # LOCAL mode only
    consecutive_clean_rounds: int = 0  # LOCAL mode only
    # 08-02 additions: cost tracking fields (CLI-08)
    cost_total_input: int = 0
    cost_total_output: int = 0
    cost_total_duration: float = 0.0
    cost_passes: int = 0
    cost_per_pass: list[dict] = field(default_factory=list)


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
            "remove .code-forge/state.json to start fresh" % (sv, SCHEMA_VERSION)
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
        state = State(
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

    # 02-02 additions: backward-compat defaults for pre-02-02 state.json
    # (R1 B1 silent-loss guard). Pre-02-02 files lack these keys; the
    # loader returns a State with defaults rather than KeyError.
    state.baseline_spec_repr = data.get("baseline_spec_repr")
    state.round_history = data.get("round_history", [])
    state.infra_errors = data.get("infra_errors", [])

    # 02-04 additions: backward-compat defaults for pre-02-04 state.json.
    state.hold_reason = data.get("hold_reason")
    state.promoted_fingerprints = set(
        data.get("promoted_fingerprints", [])
    )

    # 02-02 additions: backward-compat defaults for pre-02-02 state.json.
    state.consecutive_survivor_rounds = data.get(
        "consecutive_survivor_rounds", 0
    )
    state.consecutive_clean_rounds = data.get(
        "consecutive_clean_rounds", 0
    )

    # 08-02 additions: backward-compat defaults for pre-08-02 state.json.
    cost_data = data.get("cost", {})
    state.cost_total_input = cost_data.get("total_input_tokens", 0)
    state.cost_total_output = cost_data.get("total_output_tokens", 0)
    state.cost_total_duration = cost_data.get("total_duration_s", 0.0)
    state.cost_passes = cost_data.get("passes", 0)
    state.cost_per_pass = cost_data.get("per_pass", [])

    return state


def _finding_to_dict(f: StateFinding) -> dict:
    """Serialize StateFinding to JSON-safe dict."""
    d = {
        "id": f.id,
        "fingerprint": f.fingerprint,
        "source": f.source,
        "disposition": f.disposition.value,
        "file": f.file,
        "line_range": list(f.line_range),
        "description": f.description,
        "error": f.error,
        "anchor": f.anchor,
        "evidence_files": f.evidence_files,
    }
    return d


def save_state(state: State, path: Path) -> None:
    """Atomic write of state.json. Rebuilds dispositions cache first.

    02-04 rewrite: no asdict on State. asdict cannot handle the set-typed
    promoted_fingerprints field. All fields serialized explicitly.
    """
    state.dispositions = {f.id: f.disposition for f in state.findings}
    data = {
        "schema_version": state.schema_version,
        "disposition_protocol_version": state.disposition_protocol_version,
        "round": state.round,
        "mode": state.mode.value,
        "source_hash": state.source_hash,
        "findings": [_finding_to_dict(f) for f in state.findings],
        "dispositions": {
            k: v.value for k, v in state.dispositions.items()
        },
        "fix_attempts": dict(state.fix_attempts),
        "verdict": state.verdict.value,
        "converged": state.converged,
        "baseline_spec_repr": state.baseline_spec_repr,
        "round_history": list(state.round_history),
        "infra_errors": list(state.infra_errors),
        "hold_reason": state.hold_reason,
        "promoted_fingerprints": sorted(state.promoted_fingerprints),
        "consecutive_survivor_rounds": state.consecutive_survivor_rounds,
        "consecutive_clean_rounds": state.consecutive_clean_rounds,
        "cost": {
            "total_input_tokens": state.cost_total_input,
            "total_output_tokens": state.cost_total_output,
            "total_duration_s": state.cost_total_duration,
            "passes": state.cost_passes,
            "per_pass": state.cost_per_pass,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)
