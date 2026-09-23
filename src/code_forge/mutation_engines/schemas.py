# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Mutation engine schema definitions.

Immutable value types for target declarations, worker files, run
envelopes, target results and the nine-member normalized status
enumeration.  Field sets are verbatim from the admitted specification
(2026-09-22-mutation-interface-spec.md) and plan (revision four).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


# -- Identifier validation ---------------------------------------------------

_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

# Maximum byte size for a single target-declaration YAML document
# (including all fields). [INFERRED] The spec says "bounded" but gives
# no number; 256 KiB is generous enough for any real declaration while
# rejecting multi-megabyte abuse.
MAX_DECLARATION_BYTES = 256 * 1024

# Maximum number of targets in a single configuration block.
# [INFERRED] spec says declarations reject "oversized"; 256 targets is
# the practical ceiling.
MAX_TARGETS = 256


def valid_identifier(value: str) -> bool:
    """Return True when *value* matches ``[a-z][a-z0-9_-]{0,63}``."""
    return bool(_IDENTIFIER_RE.match(value))


# -- Normalized status -------------------------------------------------------

class NormalizedStatus(str, Enum):
    """Nine-member enumeration (spec internal-contracts section)."""
    KILLED = "killed"
    SURVIVED = "survived"
    NO_COVERAGE = "no_coverage"
    NONVIABLE = "nonviable"
    TIMED_OUT = "timed_out"
    RUNTIME_ERROR = "runtime_error"
    IGNORED = "ignored"
    PENDING = "pending"
    UNKNOWN = "unknown"


# -- Run state / aggregate decision / baseline state -------------------------

class RunState(str, Enum):
    """Per-target run state (spec result-envelope section)."""
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    UNAVAILABLE = "unavailable"
    CANCELLED = "cancelled"
    ERROR = "error"
    INAPPLICABLE = "inapplicable"


class AggregateDecision(str, Enum):
    """Aggregate mutation decision (spec result-envelope section)."""
    PASS = "pass"
    FAIL = "fail"
    HOLD = "hold"
    NOT_APPLICABLE = "not_applicable"


class BaselineState(str, Enum):
    """Baseline state (spec result-envelope section)."""
    PASSED = "passed"
    FAILED = "failed"
    EMPTY = "empty"
    UNSTABLE = "unstable"
    UNKNOWN = "unknown"


class CleanupState(str, Enum):
    """Cleanup state (spec result-envelope section)."""
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    PENDING = "pending"


# -- Budget ------------------------------------------------------------------

_BUDGET_KEYS = frozenset({
    "total_seconds",
    "baseline_seconds",
    "mutant_seconds",
    "concurrency",
    "memory_mb",
    "processes",
    "workspace_mb",
    "evidence_mb",
})


@dataclass(frozen=True)
class Budget:
    """Execution budget for a target.  Every member is a required positive integer."""
    total_seconds: int
    baseline_seconds: int
    mutant_seconds: int
    concurrency: int
    memory_mb: int
    processes: int
    workspace_mb: int
    evidence_mb: int

    def __post_init__(self) -> None:
        for name in _BUDGET_KEYS:
            val = getattr(self, name)
            if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
                raise ValueError(
                    "budget.%s must be a positive integer, got %r" % (name, val)
                )
        if self.baseline_seconds > self.total_seconds:
            raise ValueError(
                "budget.baseline_seconds (%d) exceeds total_seconds (%d)"
                % (self.baseline_seconds, self.total_seconds)
            )
        if self.mutant_seconds > self.total_seconds:
            raise ValueError(
                "budget.mutant_seconds (%d) exceeds total_seconds (%d)"
                % (self.mutant_seconds, self.total_seconds)
            )

    def to_dict(self) -> dict[str, int]:
        return {k: getattr(self, k) for k in _BUDGET_KEYS}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Budget:
        if not isinstance(data, dict):
            raise TypeError("budget must be a mapping, got %s" % type(data).__name__)
        missing = _BUDGET_KEYS - data.keys()
        if missing:
            raise ValueError("budget missing required keys: %s" % ", ".join(sorted(missing)))
        extra = set(data.keys()) - _BUDGET_KEYS
        if extra:
            raise ValueError("budget has unknown keys: %s" % ", ".join(sorted(extra)))
        return cls(**{k: data[k] for k in _BUDGET_KEYS})


# -- Target declaration ------------------------------------------------------

_TARGET_REQUIRED_KEYS = frozenset({
    "id", "adapter", "root", "sources", "tests", "inputs",
    "oracle", "command", "execution_profile", "environment", "budget",
})

# Adapters that require a corpus field
_CORPUS_ADAPTERS = frozenset({"patch-corpus"})


def _validate_relative_path(path: str, field_name: str) -> None:
    """Reject absolute paths, traversal and paths resolving outside root."""
    if not path:
        raise ValueError("%s: empty path" % field_name)
    if path.startswith("/"):
        raise ValueError("%s: absolute path not allowed: %s" % (field_name, path))
    if "\\" in path:
        raise ValueError("%s: backslash not allowed: %s" % (field_name, path))
    # Check for traversal components
    for part in path.replace("\\", "/").split("/"):
        if part == "..":
            raise ValueError("%s: traversal (..) not allowed: %s" % (field_name, path))


def _validate_relative_pattern(pattern: str, field_name: str) -> None:
    """Validate a relative glob pattern (sources/tests)."""
    if not pattern:
        raise ValueError("%s: empty pattern" % field_name)
    if pattern.startswith("/"):
        raise ValueError("%s: absolute pattern not allowed: %s" % (field_name, pattern))
    for part in pattern.replace("\\", "/").split("/"):
        if part == "..":
            raise ValueError("%s: traversal (..) not allowed: %s" % (field_name, pattern))


@dataclass(frozen=True)
class TargetDeclaration:
    """A single mutation target declaration from the gate configuration."""
    id: str
    adapter: str
    root: str
    sources: tuple[str, ...]
    tests: tuple[str, ...]
    inputs: tuple[str, ...]
    oracle: str
    command: tuple[str, ...]
    execution_profile: str
    environment: str
    budget: Budget
    engine_config: str | None = None
    corpus: str | None = None

    def __post_init__(self) -> None:
        if not valid_identifier(self.id):
            raise ValueError(
                "target id must match [a-z][a-z0-9_-]{0,63}, got %r" % self.id
            )
        if not valid_identifier(self.adapter):
            raise ValueError(
                "target adapter must be a valid identifier, got %r" % self.adapter
            )
        if not self.sources:
            raise ValueError("target %r: sources must be nonempty" % self.id)
        if not self.tests:
            raise ValueError("target %r: tests must be nonempty" % self.id)
        if not self.command:
            raise ValueError("target %r: command must be nonempty" % self.id)
        if not isinstance(self.command, tuple) or not all(
            isinstance(a, str) for a in self.command
        ):
            raise ValueError("target %r: command must be a tuple of strings" % self.id)

        # Validate root
        _validate_relative_path(self.root, "target %r: root" % self.id)

        # Validate source / test patterns
        for pat in self.sources:
            _validate_relative_pattern(pat, "target %r: sources" % self.id)
        for pat in self.tests:
            _validate_relative_pattern(pat, "target %r: tests" % self.id)

        # Validate exact input paths
        for p in self.inputs:
            _validate_relative_path(p, "target %r: inputs" % self.id)

        # corpus / engine_config validation
        if self.adapter in _CORPUS_ADAPTERS:
            if self.corpus is None:
                raise ValueError(
                    "target %r: corpus is required for adapter %r"
                    % (self.id, self.adapter)
                )
            _validate_relative_path(self.corpus, "target %r: corpus" % self.id)
        else:
            if self.corpus is not None:
                raise ValueError(
                    "target %r: corpus is forbidden for non-corpus adapter %r"
                    % (self.id, self.adapter)
                )

        if self.engine_config is not None:
            _validate_relative_path(
                self.engine_config, "target %r: engine_config" % self.id
            )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "adapter": self.adapter,
            "root": self.root,
            "sources": list(self.sources),
            "tests": list(self.tests),
            "inputs": list(self.inputs),
            "oracle": self.oracle,
            "command": list(self.command),
            "execution_profile": self.execution_profile,
            "environment": self.environment,
            "budget": self.budget.to_dict(),
        }
        if self.engine_config is not None:
            d["engine_config"] = self.engine_config
        if self.corpus is not None:
            d["corpus"] = self.corpus
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TargetDeclaration:
        if not isinstance(data, dict):
            raise TypeError(
                "target declaration must be a mapping, got %s" % type(data).__name__
            )

        # Check required keys
        missing = _TARGET_REQUIRED_KEYS - data.keys()
        if missing:
            raise ValueError(
                "target declaration missing required keys: %s"
                % ", ".join(sorted(missing))
            )

        allowed = _TARGET_REQUIRED_KEYS | {"engine_config", "corpus"}
        extra = set(data.keys()) - allowed
        if extra:
            raise ValueError(
                "target declaration has unknown keys: %s"
                % ", ".join(sorted(extra))
            )

        # Coerce list fields to tuples
        sources = data["sources"]
        if not isinstance(sources, list):
            raise TypeError("sources must be a list, got %s" % type(sources).__name__)
        tests = data["tests"]
        if not isinstance(tests, list):
            raise TypeError("tests must be a list, got %s" % type(tests).__name__)
        inputs = data["inputs"]
        if not isinstance(inputs, list):
            raise TypeError("inputs must be a list, got %s" % type(inputs).__name__)
        command = data["command"]
        if not isinstance(command, list):
            raise TypeError("command must be a list, got %s" % type(command).__name__)

        for lst_name, lst_val in [("sources", sources), ("tests", tests),
                                  ("inputs", inputs), ("command", command)]:
            if not all(isinstance(x, str) for x in lst_val):
                raise TypeError("%s entries must be strings" % lst_name)

        budget = Budget.from_dict(data["budget"])

        return cls(
            id=data["id"],
            adapter=data["adapter"],
            root=data["root"],
            sources=tuple(sources),
            tests=tuple(tests),
            inputs=tuple(inputs),
            oracle=data["oracle"],
            command=tuple(command),
            execution_profile=data["execution_profile"],
            environment=data["environment"],
            budget=budget,
            engine_config=data.get("engine_config"),
            corpus=data.get("corpus"),
        )


# -- Worker file -------------------------------------------------------------

@dataclass(frozen=True)
class WorkerConfig:
    """Operator worker configuration (spec host-files section)."""
    schema_version: int
    id: str
    concurrency: int
    swap_mb: int
    memory_mb: int
    pids: int
    supervisor_memory_mb: int
    supervisor_pids: int
    delegated_cgroup_root: str
    state_root: str

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError(
                "worker schema_version must be 1, got %r" % self.schema_version
            )
        if not valid_identifier(self.id):
            raise ValueError(
                "worker id must be a valid identifier, got %r" % self.id
            )
        if self.concurrency != 1:
            raise ValueError(
                "worker concurrency must be 1, got %r" % self.concurrency
            )
        if self.swap_mb != 0:
            raise ValueError(
                "worker swap_mb must be 0, got %r" % self.swap_mb
            )
        for name in ("memory_mb", "pids", "supervisor_memory_mb", "supervisor_pids"):
            val = getattr(self, name)
            if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
                raise ValueError(
                    "worker.%s must be a positive integer, got %r" % (name, val)
                )
        if not self.delegated_cgroup_root.startswith("/"):
            raise ValueError(
                "worker.delegated_cgroup_root must be an absolute path, got %r"
                % self.delegated_cgroup_root
            )
        if not self.state_root.startswith("/"):
            raise ValueError(
                "worker.state_root must be an absolute path, got %r"
                % self.state_root
            )

    def check_admission(self, targets: list[TargetDeclaration]) -> None:
        """Raise ValueError when targets exceed the worker budget.

        Admission formula (plan lines 96-97):
          max(target.budget.memory_mb) + supervisor_memory_mb <= worker.memory_mb
          max(target.budget.processes) + supervisor_pids     <= worker.pids
        """
        if not targets:
            return
        max_memory = max(t.budget.memory_mb for t in targets)
        max_procs = max(t.budget.processes for t in targets)
        if max_memory + self.supervisor_memory_mb > self.memory_mb:
            raise ValueError(
                "admission: max target memory_mb (%d) + supervisor_memory_mb (%d) "
                "> worker.memory_mb (%d)"
                % (max_memory, self.supervisor_memory_mb, self.memory_mb)
            )
        if max_procs + self.supervisor_pids > self.pids:
            raise ValueError(
                "admission: max target processes (%d) + supervisor_pids (%d) "
                "> worker.pids (%d)"
                % (max_procs, self.supervisor_pids, self.pids)
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "concurrency": self.concurrency,
            "swap_mb": self.swap_mb,
            "memory_mb": self.memory_mb,
            "pids": self.pids,
            "supervisor_memory_mb": self.supervisor_memory_mb,
            "supervisor_pids": self.supervisor_pids,
            "delegated_cgroup_root": self.delegated_cgroup_root,
            "state_root": self.state_root,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkerConfig:
        if not isinstance(data, dict):
            raise TypeError(
                "worker config must be a mapping, got %s" % type(data).__name__
            )
        required = {
            "schema_version", "id", "concurrency", "swap_mb",
            "memory_mb", "pids", "supervisor_memory_mb", "supervisor_pids",
            "delegated_cgroup_root", "state_root",
        }
        missing = required - data.keys()
        if missing:
            raise ValueError(
                "worker config missing required keys: %s"
                % ", ".join(sorted(missing))
            )
        extra = set(data.keys()) - required
        if extra:
            raise ValueError(
                "worker config has unknown keys: %s" % ", ".join(sorted(extra))
            )
        return cls(**data)


# -- Infrastructure error / artifact reference --------------------------------

@dataclass(frozen=True)
class ArtifactReference:
    """A pointer to a run-owned evidence file."""
    relative_run_path: str
    digest: str
    bytes: int

    def __post_init__(self) -> None:
        if self.bytes < 0:
            raise ValueError("artifact bytes must be nonnegative, got %d" % self.bytes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_run_path": self.relative_run_path,
            "digest": self.digest,
            "bytes": self.bytes,
        }


@dataclass(frozen=True)
class InfrastructureError:
    """An infrastructure error record (spec result-envelope section)."""
    code: str
    phase: str
    target_id: str | None
    message: str
    retryable: bool
    evidence_refs: tuple[ArtifactReference, ...]

    _VALID_PHASES = frozenset({
        "resolve", "snapshot", "probe", "baseline",
        "mutation", "parse", "cleanup",
    })

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("infrastructure error code must be nonempty")
        if self.phase not in self._VALID_PHASES:
            raise ValueError(
                "infrastructure error phase must be one of %s, got %r"
                % (sorted(self._VALID_PHASES), self.phase)
            )
        if len(self.message) > 4096:
            raise ValueError(
                "infrastructure error message exceeds 4096 chars"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "phase": self.phase,
            "target_id": self.target_id,
            "message": self.message,
            "retryable": self.retryable,
            "evidence_refs": [r.to_dict() for r in self.evidence_refs],
        }


# -- Run envelope / target result (structural only) --------------------------

@dataclass(frozen=True)
class RunIdentity:
    """Identity fields shared by a run envelope and each target result."""
    run_id: str
    reviewed_source_id: str
    input_manifest_digest: str
    selection_digest: str
    config_digest: str
    execution_policy_digest: str
    toolchain_fingerprint: str
    target_id: str
    adapter_id: str
    adapter_version: str
    tool_version: str

    def to_dict(self) -> dict[str, str]:
        return {
            "run_id": self.run_id,
            "reviewed_source_id": self.reviewed_source_id,
            "input_manifest_digest": self.input_manifest_digest,
            "selection_digest": self.selection_digest,
            "config_digest": self.config_digest,
            "execution_policy_digest": self.execution_policy_digest,
            "toolchain_fingerprint": self.toolchain_fingerprint,
            "target_id": self.target_id,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "tool_version": self.tool_version,
        }


@dataclass(frozen=True)
class CommandReceipt:
    """Record of a single supervised command execution."""
    id: str
    run_id: str
    target_id: str
    executable_digest: str
    argv: tuple[str, ...]
    started_at: str
    finished_at: str
    exit_code: int | None
    signal: int | None
    timeout: bool
    applied_limits: dict[str, int]
    resource_events: dict[str, int | None]
    evidence_refs: tuple[ArtifactReference, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "target_id": self.target_id,
            "executable_digest": self.executable_digest,
            "argv": list(self.argv),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
            "signal": self.signal,
            "timeout": self.timeout,
            "applied_limits": dict(self.applied_limits),
            "resource_events": dict(self.resource_events),
            "evidence_refs": [r.to_dict() for r in self.evidence_refs],
        }


@dataclass(frozen=True)
class Cleanup:
    """Cleanup record (spec result-envelope section)."""
    state: CleanupState
    owned_group_empty: bool
    owned_mounts_removed: bool
    errors: tuple[InfrastructureError, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "owned_group_empty": self.owned_group_empty,
            "owned_mounts_removed": self.owned_mounts_removed,
            "errors": [e.to_dict() for e in self.errors],
        }


@dataclass(frozen=True)
class BaselineRecord:
    """Baseline evidence record (spec result-envelope section)."""
    state: BaselineState
    test_count: int
    command_receipt: CommandReceipt | None
    native_evidence: tuple[ArtifactReference, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "test_count": self.test_count,
            "command_receipt": (
                self.command_receipt.to_dict() if self.command_receipt else None
            ),
            "native_evidence": [r.to_dict() for r in self.native_evidence],
        }


@dataclass(frozen=True)
class Outcome:
    """A single mutant outcome (spec result-envelope section)."""
    mutant_id: str
    source_path: str
    source_digest: str
    location: str
    operator: str
    native_status: str
    normalized_status: NormalizedStatus
    test_evidence: tuple[ArtifactReference, ...]
    native_evidence: tuple[ArtifactReference, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "mutant_id": self.mutant_id,
            "source_path": self.source_path,
            "source_digest": self.source_digest,
            "location": self.location,
            "operator": self.operator,
            "native_status": self.native_status,
            "normalized_status": self.normalized_status.value,
            "test_evidence": [r.to_dict() for r in self.test_evidence],
            "native_evidence": [r.to_dict() for r in self.native_evidence],
        }


@dataclass(frozen=True)
class InventoryManifestEntry:
    """One entry in the generation manifest."""
    mutant_id: str
    source_path: str
    source_digest: str
    operator: str
    selected: bool
    exclusion: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mutant_id": self.mutant_id,
            "source_path": self.source_path,
            "source_digest": self.source_digest,
            "operator": self.operator,
            "selected": self.selected,
            "exclusion": self.exclusion,
        }


@dataclass(frozen=True)
class Generation:
    """Generation evidence (spec result-envelope section)."""
    inventory_artifact: ArtifactReference
    completion_evidence: ArtifactReference
    extractor_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "inventory_artifact": self.inventory_artifact.to_dict(),
            "completion_evidence": self.completion_evidence.to_dict(),
            "extractor_version": self.extractor_version,
        }


@dataclass(frozen=True)
class Inventory:
    """Inventory counts and manifest (spec result-envelope section)."""
    generated: int
    selected: int
    excluded: int
    completed: int
    manifest: tuple[InventoryManifestEntry, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated": self.generated,
            "selected": self.selected,
            "excluded": self.excluded,
            "completed": self.completed,
            "manifest": [m.to_dict() for m in self.manifest],
        }


@dataclass(frozen=True)
class TargetResult:
    """Complete result for one selected target (spec result-envelope section)."""
    identity: RunIdentity
    target_id: str
    adapter_id: str
    adapter_version: str
    tool_version: str
    run_state: RunState
    reason_code: str
    baseline: BaselineRecord
    generation: Generation
    inventory: Inventory
    outcomes: tuple[Outcome, ...]
    native_artifacts: tuple[ArtifactReference, ...]
    command_receipts: tuple[CommandReceipt, ...]
    infrastructure_errors: tuple[InfrastructureError, ...]
    cleanup: Cleanup

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity.to_dict(),
            "target_id": self.target_id,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "tool_version": self.tool_version,
            "run_state": self.run_state.value,
            "reason_code": self.reason_code,
            "baseline": self.baseline.to_dict(),
            "generation": self.generation.to_dict(),
            "inventory": self.inventory.to_dict(),
            "outcomes": [o.to_dict() for o in self.outcomes],
            "native_artifacts": [a.to_dict() for a in self.native_artifacts],
            "command_receipts": [c.to_dict() for c in self.command_receipts],
            "infrastructure_errors": [e.to_dict() for e in self.infrastructure_errors],
            "cleanup": self.cleanup.to_dict(),
        }


@dataclass(frozen=True)
class RunEnvelope:
    """Top-level run envelope (spec result-envelope section)."""
    schema_version: int
    run_id: str
    reviewed_source_id: str
    input_manifest_digest: str
    selection_digest: str
    config_digest: str
    execution_policy_digest: str
    adapter_versions: dict[str, str]
    toolchain_fingerprint: str
    started_at: str
    completed_at: str
    targets: tuple[TargetResult, ...]
    aggregate_decision: AggregateDecision
    infrastructure_errors: tuple[InfrastructureError, ...]
    cleanup: Cleanup

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "reviewed_source_id": self.reviewed_source_id,
            "input_manifest_digest": self.input_manifest_digest,
            "selection_digest": self.selection_digest,
            "config_digest": self.config_digest,
            "execution_policy_digest": self.execution_policy_digest,
            "adapter_versions": dict(self.adapter_versions),
            "toolchain_fingerprint": self.toolchain_fingerprint,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "targets": [t.to_dict() for t in self.targets],
            "aggregate_decision": self.aggregate_decision.value,
            "infrastructure_errors": [e.to_dict() for e in self.infrastructure_errors],
            "cleanup": self.cleanup.to_dict(),
        }
