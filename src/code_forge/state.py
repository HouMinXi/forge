# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""state.json schema + IO.

Schema owned by 02-01. Subsequent sub-plans add fields ADDITIVELY (no rename,
no remove). Bump SCHEMA_VERSION on breaking change.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from .disposition import DISPOSITION_PROTOCOL_VERSION, Disposition
from .errors import CorruptedStateError, SchemaVersionMismatchError

SCHEMA_VERSION: int = 1
MUTATION_SURVIVOR_COUNTER_VERSION: int = 1
EARNED_WINDOW_VERSION = 1
ROUND_PHASES = ("l0", "rulepack", "l1", "l2", "e2e", "coverage")
HOST_ROUND_FIELDS = frozenset(
    {
        "clean_credit_action",
        "phase_status",
        "acquisition_failures",
        "reset_observed",
        "source_hash",
        "reviewed_repositories",
    }
)

# Canonical pass names (shared by receipt.py, outlet_c.py, sarif.py).
_PASS_NAMES = ("qodo", "expert", "adversarial")


class PassOutcome(str, Enum):
    """Per-pass outcome derived from INFRA findings.

    Worst-outcome-wins ordering (most severe first):
      TIMEOUT > ERROR > SCHEMA_FAIL > INCOMPLETE > COMPLETED
    SKIPPED is reserved for future use (pass not attempted).
    """

    COMPLETED = "completed"
    TIMEOUT = "timeout"
    ERROR = "error"
    SCHEMA_FAIL = "schema_fail"
    INCOMPLETE = "incomplete"
    SKIPPED = "skipped"


_SEVERITY: dict[PassOutcome, int] = {
    PassOutcome.TIMEOUT: 0,
    PassOutcome.ERROR: 1,
    PassOutcome.SCHEMA_FAIL: 2,
    PassOutcome.INCOMPLETE: 3,
    PassOutcome.COMPLETED: 4,
}


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
    DELEGATED = "DELEGATED"
    UNRELIABLE = "UNRELIABLE"


class FindingDiagnosticKind(str, Enum):
    """Host-generated diagnostics that do not describe product defects."""

    PROVIDER_CAPACITY = "provider-capacity"
    CAPACITY_INCOMPLETE = "capacity-incomplete"


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
    source: Literal[
        "L0",
        "L1",
        "MUTANT",
        "E2E_CHECK",
        "COVERAGE",
        "INFRA",
        "FIXVAL",
        "EXEC",
        "RULEPACK",
        "UNTRUSTED",
    ]
    disposition: Disposition
    file: str
    line_range: list[int]
    description: str
    error: str | None = None
    anchor: dict | None = None
    evidence_files: list[str] | None = None
    is_timeout: bool = False
    # Backend that produced this finding. None for findings forge raises
    # itself (L0, MUTANT, INFRA); the ledger writer turns None into "".
    backend: str | None = None
    # Severity the reviewer assigned, "P0".."P3". None for findings forge
    # raises itself, which have no reviewer opinion to carry.
    #
    # Before this field existed, _severity_tier recovered severity by
    # looking for a "P1:" prefix on the description -- but an L1
    # description is "[qodo] <text>", so no L1 finding ever matched and
    # every one of them fell through to the source-based default. A P0
    # remote-execution finding and a P3 naming nit were indistinguishable
    # to the convergence gate, and the P3-only density path was
    # unreachable.
    severity: str | None = None
    # The finding's own quote. Envelope-level code_excerpts do not count.
    # A CONFIRMED finding with this empty is demoted to UNCERTAIN.
    excerpt: str | None = None
    # Why the falsifier reached its verdict. Empty when no falsifier ran.
    # A dismissed finding without this cannot be audited later.
    falsify_reasoning: str | None = None
    # Set at trusted acquisition or verified completeness boundaries only.
    diagnostic_kind: FindingDiagnosticKind | None = None
    provider_failure: dict | None = None


def is_receipt_audit(finding: StateFinding) -> bool:
    """Identify metadata-only diagnostics, not untrusted product candidates."""
    return (
        finding.id == "RECEIPT_UNTRUSTED"
        and finding.source == "UNTRUSTED"
        and finding.disposition == Disposition.UNCERTAIN
    )


def provider_failure_snapshot(metadata: dict | None) -> dict | None:
    """Copy only acquired fields as plain JSON, excluding nested observations."""
    if type(metadata) is not dict:
        return None

    def scalar(value):
        if value is None or type(value) in (str, int, bool):
            return value
        if type(value) is float and math.isfinite(value):
            return value
        return None

    usage = metadata.get("usage")
    return {
        key: (
            {name: scalar(usage.get(name)) for name in (
                "input_tokens", "output_tokens", "cached_input_tokens"
            )} if type(usage) is dict else None
        ) if key == "usage" else scalar(metadata.get(key))
        for key in ("kind", "duration_s", "exit_code", "stderr", "usage")
    }


def record_provider_failure(finding: StateFinding, error: Exception) -> None:
    """Retain actual invocation metadata; only typed truncation is capacity."""
    from .llm_invoke import LLMInvokeError, Usage

    if not isinstance(error, LLMInvokeError):
        return
    finding.is_timeout = error.is_timeout
    finding.provider_failure = provider_failure_snapshot({
        "kind": error.kind,
        "duration_s": error.duration_s,
        "exit_code": error.exit_code,
        "stderr": error.stderr,
        "usage": None if type(error.usage) is not Usage else {
            "input_tokens": error.usage.input_tokens,
            "output_tokens": error.usage.output_tokens,
            "cached_input_tokens": error.usage.cached_input_tokens,
        },
    })
    if error.kind == "truncated":
        finding.diagnostic_kind = FindingDiagnosticKind.PROVIDER_CAPACITY


def _host_diagnostic_shape(finding: StateFinding) -> bool:
    return (
        all(type(value) is str for value in (finding.id, finding.fingerprint, finding.source, finding.file))
        and finding.source == "INFRA"
        and finding.disposition is Disposition.CONFIRMED
        and type(finding.line_range) is list
        and finding.line_range == [0, 0]
        and all(type(line) is int for line in finding.line_range)
    )


def is_provider_capacity(finding: StateFinding) -> bool:
    """Only a typed diagnostic with the host producer structure qualifies."""
    if (
        type(finding.diagnostic_kind) is not FindingDiagnosticKind
        or finding.diagnostic_kind is not FindingDiagnosticKind.PROVIDER_CAPACITY
        or not _host_diagnostic_shape(finding)
        or type(finding.provider_failure) is not dict
        or type(finding.provider_failure.get("kind")) is not str
        or finding.provider_failure.get("kind") != "truncated"
    ):
        return False
    return any(
        finding.id == f"l1-{name}-{kind}-fail"
        and finding.fingerprint == f"{kind}-fail-{name}"
        and finding.file == file
        for name in _PASS_NAMES
        for kind, file in (("invoke", "<llm-invoke>"), ("spawn", "<spawn>"))
    )


def is_provider_diagnostic(finding: StateFinding) -> bool:
    """Capacity acquisition and its independently validated incomplete proof."""
    return is_provider_capacity(finding) or (
        type(finding.diagnostic_kind) is FindingDiagnosticKind
        and finding.diagnostic_kind is FindingDiagnosticKind.CAPACITY_INCOMPLETE
        and finding.id == "RECEIPT_INVALID"
        and _host_diagnostic_shape(finding)
        and finding.file == "<receipt-evidence>"
        and re.fullmatch(r"receipt-[0-9a-f]{12}", finding.fingerprint) is not None
    )


def reporting_product_findings(findings: list[StateFinding]) -> list[StateFinding]:
    """Shared product projection; raw findings remain available for diagnosis."""
    return [f for f in findings if not is_receipt_audit(f) and not is_provider_diagnostic(f)]


def derive_pass_outcomes(
    l1_findings: list[StateFinding],
) -> dict[str, PassOutcome]:
    """Derive per-pass outcomes from INFRA findings.

    Scans for INFRA findings with predictable IDs. Consults
    StateFinding.is_timeout to distinguish TIMEOUT from ERROR
    for invoke-fail findings (factories.py sets is_timeout on
    each finding; the discriminant is already there).

    Empty findings list: returns all COMPLETED. This is correct
    because if _run_l1_phase produced zero findings, all passes
    succeeded (no INFRA markers). If _run_l1_phase crashed,
    machine.py catches the exception and sets verdict to
    ESCALATED before format_summary is ever called.

    Worst-outcome-wins: if multiple INFRA findings exist for
    the same pass (e.g. across chunks), the most severe wins.
    """
    outcomes: dict[str, PassOutcome] = {}
    for f in l1_findings:
        if f.source != "INFRA":
            continue
        for pass_name in _PASS_NAMES:
            candidate: PassOutcome | None = None
            if f.id == f"l1-{pass_name}-spawn-fail":
                candidate = (
                    PassOutcome.TIMEOUT
                    if type(f.provider_failure) is not dict or type(f.provider_failure.get("kind")) is not str or f.is_timeout
                    else PassOutcome.ERROR
                )
            elif f.id == f"l1-{pass_name}-invoke-fail":
                candidate = PassOutcome.TIMEOUT if getattr(f, "is_timeout", False) else PassOutcome.ERROR
            elif f.id == f"l1-{pass_name}-schema-fail":
                candidate = PassOutcome.SCHEMA_FAIL
            elif f.id == f"l1-{pass_name}-incomplete-coverage":
                candidate = PassOutcome.INCOMPLETE
            if candidate is not None:
                existing = outcomes.get(pass_name)
                if existing is None or (_SEVERITY[candidate] < _SEVERITY[existing]):
                    outcomes[pass_name] = candidate
    for pass_name in _PASS_NAMES:
        if pass_name not in outcomes:
            outcomes[pass_name] = PassOutcome.COMPLETED
    return outcomes


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
    source_hash: str | None = None
    findings: list[StateFinding] = field(default_factory=list)
    # Derived lookup cache (NOT source of truth; SOT = StateFinding.disposition).
    # save_state rebuilds from findings; load_state verifies cache matches.
    dispositions: dict[str, Disposition] = field(default_factory=dict)
    fix_attempts: dict[str, int] = field(default_factory=dict)
    verdict: Verdict = Verdict.PENDING
    converged: bool = False
    # 02-02 additions:
    baseline_spec_repr: str | None = None
    round_history: list[dict] = field(default_factory=list)
    infra_errors: list[str] = field(default_factory=list)
    # 02-04 additions:
    hold_reason: str | None = None
    promoted_fingerprints: set[str] = field(default_factory=set)
    # Mutation survivor round counter (LOCAL mode):
    consecutive_survivor_rounds: int = 0  # LOCAL mode only
    mutation_survivor_counter_version: int = MUTATION_SURVIVOR_COUNTER_VERSION
    survivor_counter_migration: dict[str, Any] | None = None
    _legacy_survivor_state: bytes | None = field(default=None, repr=False, compare=False)
    consecutive_clean_rounds: int = 0  # LOCAL mode only
    earned_clean_window: dict | None = None
    clean_window_migration: dict | None = None
    _earned_window_present: bool = field(default=False, repr=False, compare=False)
    _clean_state_sha256: str | None = field(default=None, repr=False, compare=False)
    # Rounds ending with a pass that did not complete. Persisted for the
    # same reason the two above are: a run that stops before its third
    # round (FAIL on round 1 is the common shape) would otherwise restart
    # this at zero on the next invocation, and the >=3 breaker in
    # _check_l1_can_still_converge could never fire against a backend that
    # fails identically every time.
    rounds_with_failed_pass: int = 0  # LOCAL mode only
    # Rounds where the falsifier could not reach its backend. Persisted
    # for the same reason as the field above.
    rounds_with_falsify_infra: int = 0  # LOCAL mode only
    # 08-02 additions: cost tracking fields (CLI-08)
    cost_total_input: int = 0
    cost_total_output: int = 0
    # Tokens served from the provider prompt cache across all rounds.
    cost_total_cached: int = 0
    cost_total_duration: float = 0.0
    cost_passes: int = 0
    cost_per_pass: list[dict] = field(default_factory=list)
    # Phase 52 addition: env_manifest snapshot
    env_manifest: dict[str, Any] | None = None
    # Phase 53a addition: exec_evidence snapshot
    exec_evidence: dict[str, Any] | None = None
    fixval_stage: dict[str, Any] | None = None


def _valid_repository_manifest(value) -> bool:
    return value is None or (
        isinstance(value, dict)
        and bool(value)
        and all(
            isinstance(k, str) and bool(k) and isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v)
            for k, v in value.items()
        )
    )


def validate_earned_clean_window(window: dict) -> None:
    """Validate additive proof shape without reading receipts or making policy."""
    if not isinstance(window, dict) or set(window) != {
        "version",
        "source_hash",
        "reviewed_repositories",
        "cycles",
    }:
        raise CorruptedStateError("invalid earned clean window object")
    if type(window["version"]) is not int or window["version"] != EARNED_WINDOW_VERSION:
        raise CorruptedStateError("unsupported earned clean window version")
    if not isinstance(window["source_hash"], str) or not window["source_hash"]:
        raise CorruptedStateError("invalid earned clean window source")
    if not _valid_repository_manifest(window["reviewed_repositories"]):
        raise CorruptedStateError("invalid earned clean window repository scope")
    if not isinstance(window["cycles"], list):
        raise CorruptedStateError("invalid earned clean window cycles")
    previous = 0
    for entry in window["cycles"]:
        if not isinstance(entry, dict) or set(entry) != {"cycle", "receipt_sha256"}:
            raise CorruptedStateError("invalid earned cycle entry")
        cycle = entry["cycle"]
        hashes = entry["receipt_sha256"]
        if type(cycle) is not int or cycle <= previous:
            raise CorruptedStateError("earned cycles must be positive, unique and increasing")
        if (
            not isinstance(hashes, dict)
            or set(hashes) != {"1", "2", "3"}
            or not all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v) for v in hashes.values())
        ):
            raise CorruptedStateError("invalid earned receipt digests")
        previous = cycle


def is_host_round(row: dict) -> bool:
    """Presence of any authority field prevents legacy interpretation."""
    return bool(HOST_ROUND_FIELDS.intersection(row))


def validate_round_history(history: list[dict], round_index: int | None = None) -> None:
    """Validate immutable attempt IDs and modern host observations."""
    if not isinstance(history, list):
        raise CorruptedStateError("invalid round history container")
    previous = -1
    for row in history:
        if not isinstance(row, dict):
            raise CorruptedStateError("invalid round history row")
        index = row.get("round")
        if type(index) is not int or index < 0 or index <= previous:
            raise CorruptedStateError("round history IDs must be nonnegative, unique and increasing")
        previous = index
        if not is_host_round(row):
            continue
        if not HOST_ROUND_FIELDS.issubset(row):
            raise CorruptedStateError("incomplete host round authority")
        action = row["clean_credit_action"]
        if action not in ("pending", "earned", "interrupted", "reset", "unavailable"):
            raise CorruptedStateError("invalid host clean credit action")
        phases = row["phase_status"]
        if (
            not isinstance(phases, dict)
            or set(phases) != set(ROUND_PHASES)
            or not all(value in ("not_run", "returned", "failed") for value in phases.values())
        ):
            raise CorruptedStateError("invalid host phase status")
        if (
            not isinstance(row["source_hash"], str)
            or not row["source_hash"]
            or not (_valid_repository_manifest(row["reviewed_repositories"]))
        ):
            raise CorruptedStateError("invalid host round source/scope")
        if type(row["reset_observed"]) is not bool or (
            type(row.get("clean_rounds_after")) is not int or row["clean_rounds_after"] < 0
        ):
            raise CorruptedStateError("invalid host reset/count")
        failures = row["acquisition_failures"]
        if not isinstance(failures, list):
            raise CorruptedStateError("invalid acquisition failure list")
        for failure in failures:
            if not isinstance(failure, dict) or set(failure) != {
                "id",
                "fingerprint",
                "pass_name",
                "outcome",
            }:
                raise CorruptedStateError("invalid acquisition failure identity")
            name = failure["pass_name"]
            if name not in _PASS_NAMES or failure["outcome"] not in ("error", "timeout"):
                raise CorruptedStateError("invalid acquisition failure outcome")
            kind = "spawn" if failure["id"] == f"l1-{name}-spawn-fail" else "invoke"
            if (
                failure["id"] != f"l1-{name}-{kind}-fail"
                or (failure["fingerprint"] != f"{kind}-fail-{name}")
            ):
                raise CorruptedStateError("invalid acquisition producer marker")
        observed = any(value == "returned" for value in phases.values())
        if action == "pending" and (
            any(value != "not_run" for value in phases.values())
            or failures
            or row["reset_observed"]
            or "fixpoint" in row
        ):
            raise CorruptedStateError("pending host round contains finalized observations")
        if "dispositions" in row:
            disps = row["dispositions"]
            if (
                not observed
                or action == "pending"
                or not isinstance(disps, dict)
                or not all(
                    isinstance(k, str) and isinstance(v, str) and v in {d.value for d in Disposition}
                    for k, v in disps.items()
                )
            ):
                raise CorruptedStateError("invalid observed product snapshot")
        if action == "earned" and (
            not all(value == "returned" for value in phases.values())
            or failures
            or row["reset_observed"]
            or row.get("fixpoint") != "CLEAN"
        ):
            raise CorruptedStateError("earned host round lacks CLEAN observation")
        if action == "interrupted" and (
            not failures
            or phases["l1"] != "returned"
            or row["reset_observed"]
            or row.get("fixpoint") != "CLEAN"
        ):
            raise CorruptedStateError("interrupted host round lacks nonreset acquisition observation")
        if action == "reset" and (
            not row["reset_observed"]
            or row["clean_rounds_after"] != 0
            or row.get("fixpoint") not in ("RESET", "CYCLE_RESTART")
        ):
            raise CorruptedStateError("reset host round lacks reset observation")
        if action in ("earned", "interrupted", "reset") and "dispositions" not in row:
            raise CorruptedStateError("finalized host round lacks product snapshot")
    if round_index is not None and (
        type(round_index) is not int
        or round_index < 0
        or (history and round_index != previous)
        or (not history and round_index != 0)
    ):
        raise CorruptedStateError("stored round disagrees with attempt history")


def product_round_history(history: list[dict], *, before_round: int | None = None) -> list[dict]:
    """Project finalized observed product snapshots without hiding host attempts."""
    if any(isinstance(row, dict) and is_host_round(row) for row in history):
        validate_round_history(history)
    projected = []
    for row in history:
        if before_round is not None and row.get("round", -1) >= before_round:
            continue
        if is_host_round(row):
            if row["clean_credit_action"] == "pending" or "dispositions" not in row:
                continue
            row = dict(row)
            collisions = {
                fp
                for name in ("l0", "l2", "e2e", "rulepack")
                for fp in row.get(f"{name}_fingerprints", [])
            }
            synthetic = {f["fingerprint"] for f in row["acquisition_failures"]} - collisions
            row["dispositions"] = {
                fp: disp for fp, disp in row["dispositions"].items() if fp not in synthetic
            }
        projected.append(row)
    return projected


def earned_history_cycles(state: State, source_hash: str, manifest: dict | None) -> list[int]:
    """Reconstruct CLEAN/reset transitions; unfinished reservations earn no credit."""
    validate_round_history(state.round_history, state.round)
    earned = []
    for row in state.round_history:
        if is_host_round(row):
            if row["source_hash"] != source_hash:
                continue
            if row["reviewed_repositories"] != manifest:
                raise CorruptedStateError("host round repository scope mismatch")
            action = row["clean_credit_action"]
            if action == "earned":
                earned.append(row["round"] + 1)
            elif action == "reset":
                earned.clear()
            elif action not in ("interrupted", "pending") and earned:
                if action == "unavailable" and all(
                    value == "not_run" for value in row["phase_status"].values()
                ):
                    raise CorruptedStateError(
                        "unfinished host execution cannot prove absence of reset observations"
                    )
                raise CorruptedStateError("unavailable host attempt cannot prove inherited credit")
        else:
            fixpoint = row.get("fixpoint")
            if fixpoint == "CLEAN":
                earned.append(row["round"] + 1)
            elif fixpoint in ("RESET", "CYCLE_RESTART"):
                earned.clear()
            else:
                raise CorruptedStateError("legacy round lacks an explicit CLEAN/reset transition")
        if (
            row.get("clean_rounds_after") != len(earned)
            or type(row.get("clean_rounds_after")) is not int
        ):
            raise CorruptedStateError("clean count disagrees with round history")
    if type(state.consecutive_clean_rounds) is not int or state.consecutive_clean_rounds != len(earned):
        raise CorruptedStateError("clean count lacks complete history provenance")
    return earned


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
        is_timeout=d.get("is_timeout", False),
        backend=d.get("backend"),
        # .get keeps state.json files written before this field existed
        # loadable; they simply carry no reviewer severity, which is the
        # same position they were in when they were written.
        severity=d.get("severity"),
        excerpt=d.get("excerpt"),
        falsify_reasoning=d.get("falsify_reasoning"),
        diagnostic_kind=(
            None if d.get("diagnostic_kind") is None
            else FindingDiagnosticKind(d["diagnostic_kind"])
        ),
        provider_failure=d.get("provider_failure"),
    )


def load_state(path: Path) -> State | None:
    """Load state.json. Returns None if file does not exist.

    Raises:
        CorruptedStateError: JSON parse failure, missing/invalid fields,
            invalid enum values, or cache mismatch.
        SchemaVersionMismatchError: schema_version != SCHEMA_VERSION.
    """
    if not path.exists():
        return None
    original = path.read_bytes()
    try:
        data = json.loads(original.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise CorruptedStateError(f"cannot parse {path}: {e}") from e

    sv = data.get("schema_version")
    if sv != SCHEMA_VERSION:
        raise SchemaVersionMismatchError(
            f"state.json schema_version={sv}, forge expects {SCHEMA_VERSION}; "
            "remove .code-forge/state.json to start fresh"
        )

    if data.get("source_hash") is not None and not isinstance(data["source_hash"], str):
        raise CorruptedStateError(f"invalid source identity in {path}")

    try:
        findings = [_finding_from_dict(f) for f in data.get("findings", [])]
        dispositions = {k: Disposition(v) for k, v in data.get("dispositions", {}).items()}
    except (KeyError, ValueError) as e:
        raise CorruptedStateError(f"invalid finding or disposition in {path}: {e}") from e

    expected = {f.id: f.disposition for f in findings}
    if dispositions != expected:
        raise CorruptedStateError(f"dispositions cache out of sync with findings (path={path})")

    try:
        state = State(
            schema_version=data["schema_version"],
            disposition_protocol_version=data["disposition_protocol_version"],
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
        raise CorruptedStateError(f"missing or invalid field in {path}: {e}") from e

    # 02-02 additions: backward-compat defaults for pre-02-02 state.json
    # (R1 B1 silent-loss guard). Pre-02-02 files lack these keys; the
    # loader returns a State with defaults rather than KeyError.
    state.baseline_spec_repr = data.get("baseline_spec_repr")
    state.round_history = data.get("round_history", [])
    if not isinstance(state.round_history, list) or not all(
        isinstance(row, dict) for row in state.round_history
    ):
        raise CorruptedStateError(f"invalid round history in {path}")
    state.infra_errors = data.get("infra_errors", [])

    # 02-04 additions: backward-compat defaults for pre-02-04 state.json.
    state.hold_reason = data.get("hold_reason")
    state.promoted_fingerprints = set(data.get("promoted_fingerprints", []))

    # 02-02 additions: backward-compat defaults for pre-02-02 state.json.
    counter = data.get("consecutive_survivor_rounds", 0)
    if type(counter) is not int or counter < 0:
        raise CorruptedStateError(f"invalid mutation survivor counter in {path}")
    version = data.get("mutation_survivor_counter_version")
    if "mutation_survivor_counter_version" in data and (
        type(version) is not int or version != MUTATION_SURVIVOR_COUNTER_VERSION
    ):
        raise CorruptedStateError(f"unsupported mutation survivor counter version in {path}")
    state.survivor_counter_migration = data.get("survivor_counter_migration")
    if version == MUTATION_SURVIVOR_COUNTER_VERSION:
        state.consecutive_survivor_rounds = counter
    elif counter:
        state.survivor_counter_migration = {
            "previous_count": counter,
            "reason": "legacy counter lacks surviving-mutant accounting provenance",
            "source_sha256": hashlib.sha256(original).hexdigest(),
        }
        state._legacy_survivor_state = original
    state.consecutive_clean_rounds = data.get("consecutive_clean_rounds", 0)
    state._earned_window_present = "earned_clean_window" in data
    state.earned_clean_window = data.get("earned_clean_window")
    state.clean_window_migration = data.get("clean_window_migration")
    state._clean_state_sha256 = hashlib.sha256(original).hexdigest()
    if state._earned_window_present:
        validate_earned_clean_window(state.earned_clean_window)
    if state._earned_window_present or any(
        isinstance(row, dict) and is_host_round(row) for row in state.round_history
    ):
        validate_round_history(state.round_history, state.round)
    state.rounds_with_failed_pass = data.get("rounds_with_failed_pass", 0)
    state.rounds_with_falsify_infra = data.get("rounds_with_falsify_infra", 0)

    # 08-02 additions: backward-compat defaults for pre-08-02 state.json.
    cost_data = data.get("cost", {})
    state.cost_total_input = cost_data.get("total_input_tokens", 0)
    state.cost_total_output = cost_data.get("total_output_tokens", 0)
    state.cost_total_cached = cost_data.get("total_cached_tokens", 0)
    state.cost_total_duration = cost_data.get("total_duration_s", 0.0)
    state.cost_passes = cost_data.get("passes", 0)
    state.cost_per_pass = cost_data.get("per_pass", [])

    # Phase 52 additions: env_manifest snapshot
    state.env_manifest = data.get("env_manifest")

    # Phase 53a additions: exec_evidence snapshot
    state.exec_evidence = data.get("exec_evidence")
    state.fixval_stage = data.get("fixval_stage")
    if state.fixval_stage is not None:
        from .fixval_evidence import validate_stage

        try:
            validate_stage(state.fixval_stage)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            raise CorruptedStateError(f"invalid FIXVAL terminal stage: {exc}") from exc

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
        "is_timeout": f.is_timeout,
        "backend": f.backend,
        "severity": f.severity,
        "excerpt": f.excerpt,
        "falsify_reasoning": f.falsify_reasoning,
        "diagnostic_kind": None if f.diagnostic_kind is None else f.diagnostic_kind.value,
        "provider_failure": f.provider_failure,
    }
    return d


def _archive_legacy_survivor_state(state: State, path: Path) -> None:
    """Preserve a reset legacy count before its state file is replaced."""
    original = state._legacy_survivor_state
    if original is None:
        return
    digest = hashlib.sha256(original).hexdigest()
    archive = path.with_name(f"{path.name}.legacy-survivors-{digest}.json")
    try:
        fd = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if not hasattr(os, "O_NOFOLLOW"):
            raise OSError(
                "cannot verify an existing legacy state archive without no-follow support"
            ) from None
        fd = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as preserved:
            if not stat.S_ISREG(os.fstat(preserved.fileno()).st_mode):
                raise CorruptedStateError(
                    f"legacy state archive is not a regular file: {archive}"
                ) from None
            if preserved.read() != original:
                raise CorruptedStateError(f"legacy state archive content differs: {archive}") from None
    else:
        with os.fdopen(fd, "wb") as preserved:
            preserved.write(original)
    state._legacy_survivor_state = None


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
        "dispositions": {k: v.value for k, v in state.dispositions.items()},
        "fix_attempts": dict(state.fix_attempts),
        "verdict": state.verdict.value,
        "converged": state.converged,
        "baseline_spec_repr": state.baseline_spec_repr,
        "round_history": list(state.round_history),
        "infra_errors": list(state.infra_errors),
        "hold_reason": state.hold_reason,
        "promoted_fingerprints": sorted(state.promoted_fingerprints),
        "consecutive_survivor_rounds": state.consecutive_survivor_rounds,
        "mutation_survivor_counter_version": state.mutation_survivor_counter_version,
        "survivor_counter_migration": state.survivor_counter_migration,
        "consecutive_clean_rounds": state.consecutive_clean_rounds,
        "rounds_with_failed_pass": state.rounds_with_failed_pass,
        "rounds_with_falsify_infra": state.rounds_with_falsify_infra,
        "cost": {
            "total_input_tokens": state.cost_total_input,
            "total_output_tokens": state.cost_total_output,
            "total_cached_tokens": state.cost_total_cached,
            "total_duration_s": state.cost_total_duration,
            "passes": state.cost_passes,
            "per_pass": state.cost_per_pass,
        },
        "env_manifest": state.env_manifest,
        "exec_evidence": state.exec_evidence,
        "fixval_stage": state.fixval_stage,
    }
    if state.fixval_stage is not None:
        from .fixval_evidence import validate_stage

        validate_stage(state.fixval_stage)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    if state.earned_clean_window is not None or state._earned_window_present:
        data["earned_clean_window"] = state.earned_clean_window
    if state.clean_window_migration is not None:
        data["clean_window_migration"] = state.clean_window_migration
    _archive_legacy_survivor_state(state, path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)
