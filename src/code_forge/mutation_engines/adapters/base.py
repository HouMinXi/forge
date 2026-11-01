"""Adapter contract: protocol plus the value types it exchanges.

Mirrors the specification's internal-contracts section.  Data types are
immutable values; adapters construct ``TargetResult`` (from schemas) and
never mutate shared state.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Protocol

from code_forge.mutation_engines import isolate, state
from code_forge.mutation_engines.schemas import (
    ArtifactReference,
    BaselineRecord,
    BaselineState,
    Cleanup,
    CleanupState,
    CommandReceipt,
    Generation,
    InfrastructureError,
    Inventory,
    RunIdentity,
    RunState,
    TargetDeclaration,
    TargetResult,
    valid_identifier,
)
from code_forge.mutation_engines.targets import TargetSelection

MAX_SNAPSHOT_FILES = 65536


class CapabilityState(str, Enum):
    """Probe outcome (spec internal-contracts section)."""

    AVAILABLE = "available"
    UNSUPPORTED_VERSION = "unsupported_version"
    MISSING_DEPENDENCY = "missing_dependency"
    UNAVAILABLE_ISOLATION = "unavailable_isolation"


@dataclass(frozen=True)
class CapabilityReport:
    """What an adapter learned during probe (spec field set verbatim)."""

    state: CapabilityState
    resolved_tool_version: str | None
    evidence: tuple[str, ...]
    errors: tuple[InfrastructureError, ...]

    def __post_init__(self) -> None:
        if self.state is CapabilityState.AVAILABLE and not self.resolved_tool_version:
            raise ValueError("available probe must record the resolved tool version")


@dataclass(frozen=True)
class InputEntry:
    """One frozen input file (spec internal-contracts section)."""

    path: str
    digest: str
    mode: int
    symlink_target: str | None

    def __post_init__(self) -> None:
        if not self.path or self.path.startswith("/") or ".." in self.path.split("/"):
            raise ValueError("input entry path must be relative and contained, got %r" % self.path)
        if not self.digest:
            raise ValueError("input entry digest must be nonempty")
        if self.mode < 0:
            raise ValueError("input entry mode must be nonnegative")


@dataclass(frozen=True)
class InputSnapshot:
    """Frozen, supervisor-owned read-only input tree (spec field set)."""

    reviewed_source_id: str
    manifest_digest: str
    selection_digest: str
    root: str
    files: tuple[InputEntry, ...]

    def __post_init__(self) -> None:
        if not self.reviewed_source_id:
            raise ValueError("snapshot reviewed_source_id must be nonempty")
        if not self.manifest_digest or not self.selection_digest:
            raise ValueError("snapshot digests must be nonempty")
        if not os.path.isdir(self.root):
            raise ValueError("snapshot root is not a directory: %r" % self.root)
        if len(self.files) > MAX_SNAPSHOT_FILES:
            raise ValueError("snapshot file count exceeds %d" % MAX_SNAPSHOT_FILES)


@dataclass(frozen=True)
class ExecutionContext:
    """Everything an adapter needs to execute one supervised target run."""

    run_id: str
    config_digest: str
    execution_policy_digest: str
    toolchain_fingerprint: str
    cgroup_root: str
    state_root: str
    approved_python: str
    memory_mb: int
    pids: int
    workspace_mb: int
    process_headroom_mb: int
    extra_python_paths: tuple[str, ...] = ()
    approved_node: str = ""
    extra_node_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not valid_identifier(self.run_id):
            raise ValueError("context run_id must be an identifier, got %r" % (self.run_id,))
        if not self.config_digest or not self.execution_policy_digest:
            raise ValueError("context digests must be nonempty")
        if not self.toolchain_fingerprint:
            raise ValueError("context toolchain_fingerprint must be nonempty")
        if not self.state_root:
            raise ValueError("context state_root must be nonempty")
        if not self.approved_python:
            raise ValueError("context approved_python must be nonempty")
        if self.memory_mb <= 0 or self.pids <= 0 or self.workspace_mb <= 0:
            raise ValueError("context limits must be positive")
        if self.process_headroom_mb < 0:
            raise ValueError("context process_headroom_mb must be nonnegative")
        if self.memory_mb < self.workspace_mb + self.process_headroom_mb:
            raise ValueError(
                "memory_mb %d below workspace_mb %d plus process_headroom_mb %d"
                % (self.memory_mb, self.workspace_mb, self.process_headroom_mb)
            )
        for path in self.extra_python_paths:
            if not path.startswith("/"):
                raise ValueError("extra python paths must be absolute, got %r" % (path,))
        for path in self.extra_node_paths:
            if not path.startswith("/"):
                raise ValueError("extra node paths must be absolute, got %r" % (path,))


class MutationAdapter(Protocol):
    """The specification's adapter protocol."""

    id: str

    def probe(self, target: TargetDeclaration, context: ExecutionContext) -> CapabilityReport: ...

    def run(
        self,
        target: TargetDeclaration,
        selection: TargetSelection,
        snapshot: InputSnapshot,
        context: ExecutionContext,
    ) -> TargetResult: ...


def isolation_probe_error(exc: isolate.IsolationUnavailable, target_id: str) -> InfrastructureError:
    """The probe error for an isolation check that failed.

    A probe cgroup left behind is a cleanup failure that a later run can
    clear, so it is retryable. Any other reason means the host lacks the
    isolation envelope.
    """
    if exc.reason == "probe_cleanup_failed":
        return InfrastructureError(
            code="probe-cleanup-failed",
            phase="cleanup",
            target_id=target_id,
            message=str(exc),
            retryable=True,
            evidence_refs=(),
        )
    return InfrastructureError(
        code="isolation-unavailable",
        phase="probe",
        target_id=target_id,
        message=str(exc),
        retryable=False,
        evidence_refs=(),
    )


def isolation_probe_errors(
    exc: isolate.IsolationUnavailable, target_id: str
) -> tuple[InfrastructureError, ...]:
    """Keep the primary cause and any distinct probe-removal failure."""
    primary = isolation_probe_error(exc, target_id)
    cleanup = exc.probe_cleanup_failure
    if cleanup is None or exc.reason == "probe_cleanup_failed":
        return (primary,)
    return (
        primary,
        InfrastructureError(
            code="probe-cleanup-failed",
            phase="cleanup",
            target_id=target_id,
            message=str(cleanup),
            retryable=True,
            evidence_refs=(),
        ),
    )


def cleanup_failure_error(exc: isolate.CgroupCleanupError, target_id: str) -> InfrastructureError:
    """The infrastructure error for a run cgroup left behind."""
    return InfrastructureError(
        code="cgroup-cleanup-failed",
        phase="cleanup",
        target_id=target_id,
        message=str(exc),
        retryable=True,
        evidence_refs=(),
    )


@dataclass
class RunEvidence:
    """What a run has gathered so far.

    run() owns it and the run body adds each receipt, artifact and the
    baseline as it gets them, so a sandbox failure partway through still
    reports everything gathered before it.
    """

    receipts: list[CommandReceipt] = field(default_factory=list)
    artifacts: list[ArtifactReference] = field(default_factory=list)
    baseline: BaselineRecord | None = None


def sandbox_failure_result(
    exc: isolate.CgroupCleanupError | isolate.IsolationUnavailable,
    identity: RunIdentity,
    run_directory: Path,
    baseline: BaselineRecord | None = None,
    receipts: Sequence[CommandReceipt] = (),
    artifacts: Sequence[ArtifactReference] = (),
) -> TargetResult:
    """Report a run stopped by the sandbox as incomplete, keeping its evidence.

    A run cgroup left behind (CgroupCleanupError) means the command finished
    but the host is not clean, so the result fails closed and the cleanup
    record says the owned group is not empty; the command's receipt, carried
    on the error, is kept. Missing isolation (IsolationUnavailable) means a
    command could not start; a probe cgroup left behind, or a run cgroup that
    teardown could not remove, is also recorded as a cleanup error.
    """
    target_id = identity.target_id
    receipt = None
    if isinstance(exc, isolate.CgroupCleanupError):
        receipt = exc.receipt if isinstance(exc.receipt, CommandReceipt) else None
        error = cleanup_failure_error(exc, target_id)
        cleanup_errors = (error,)
        group_left = True
        name = "cleanup-failure.json"
        report: dict[str, object] = {
            "message": str(exc),
            "exit_code": exc.exit_code,
            "timed_out": exc.timed_out,
        }
    else:
        error, *probe_errors = isolation_probe_errors(exc, target_id)
        if error.phase == "probe":
            # Supervisor.start re-checks isolation, so here it failed on a
            # command of the run, not in the adapter's probe.
            error = replace(error, phase="baseline" if baseline is None else "mutation")
        cleanup_errors = ((error,) if error.phase == "cleanup" else ()) + tuple(probe_errors)
        group_left = exc.cleanup_failure is not None
        if group_left:
            cleanup_errors += (cleanup_failure_error(exc.cleanup_failure, target_id),)
        name = "isolation-failure.json"
        report = {
            "message": str(exc),
            "reason": exc.reason,
            "cleanup_failure": str(exc.cleanup_failure) if group_left else None,
        }
        if exc.probe_cleanup_failure is not None:
            report["probe_cleanup_failure"] = str(exc.probe_cleanup_failure)
    payload = json.dumps(report, sort_keys=True).encode("utf-8")
    state.publish(run_directory, name, payload)
    evidence = ArtifactReference(
        relative_run_path="results/" + name,
        digest=hashlib.sha256(payload).hexdigest(),
        bytes=len(payload),
    )
    kept_receipts = tuple(receipts) + ((receipt,) if receipt is not None else ())
    if baseline is None:
        baseline = BaselineRecord(
            state=BaselineState.UNKNOWN,
            test_count=0,
            command_receipt=receipt,
            native_evidence=(evidence,),
        )
    new_errors = (error,) + tuple(e for e in cleanup_errors if e is not error)
    return TargetResult(
        identity=identity,
        target_id=target_id,
        adapter_id=identity.adapter_id,
        adapter_version=identity.adapter_version,
        tool_version=identity.tool_version,
        run_state=RunState.INCOMPLETE,
        reason_code=error.code,
        baseline=baseline,
        generation=Generation(
            inventory_artifact=evidence,
            completion_evidence=evidence,
            extractor_version=identity.adapter_version,
        ),
        inventory=Inventory(generated=0, selected=0, excluded=0, completed=0, manifest=()),
        outcomes=(),
        native_artifacts=tuple(artifacts) + (evidence,),
        command_receipts=kept_receipts,
        infrastructure_errors=new_errors,
        cleanup=Cleanup(
            state=CleanupState.INCOMPLETE if cleanup_errors else CleanupState.COMPLETE,
            owned_group_empty=not group_left,
            owned_mounts_removed=True,
            errors=cleanup_errors,
        ),
    )
