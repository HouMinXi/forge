"""Adapter contract: protocol plus the value types it exchanges.

Mirrors the specification's internal-contracts section.  Data types are
immutable values; adapters construct ``TargetResult`` (from schemas) and
never mutate shared state.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from code_forge.mutation_engines.schemas import (
    InfrastructureError,
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
            raise ValueError(
                "snapshot file count exceeds %d" % MAX_SNAPSHOT_FILES
            )


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
                raise ValueError(
                    "extra python paths must be absolute, got %r" % (path,)
                )
        for path in self.extra_node_paths:
            if not path.startswith("/"):
                raise ValueError(
                    "extra node paths must be absolute, got %r" % (path,)
                )


class MutationAdapter(Protocol):
    """The specification's adapter protocol."""

    id: str

    def probe(
        self, target: TargetDeclaration, context: ExecutionContext
    ) -> CapabilityReport: ...

    def run(
        self,
        target: TargetDeclaration,
        selection: TargetSelection,
        snapshot: InputSnapshot,
        context: ExecutionContext,
    ) -> TargetResult: ...
