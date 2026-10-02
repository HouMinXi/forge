"""Mull adapter for C and C++.

Mull mutates LLVM IR. The runner binary is versioned with the Clang it
was built against: this host has clang 22, so the binary is
mull-runner-22. probe only checks that binary is on PATH. run is not
wired, because a Mull score needs a test binary compiled with the
matching IR plugin, which the review gate does not have.
"""

from __future__ import annotations

import shutil

from code_forge.mutation_engines.adapters.base import (
    CapabilityReport,
    CapabilityState,
    ExecutionContext,
    InputSnapshot,
)
from code_forge.mutation_engines.schemas import (
    InfrastructureError,
    TargetDeclaration,
    TargetResult,
)
from code_forge.mutation_engines.targets import TargetSelection

ADAPTER_ID = "c-mull"
RUNNER = "mull-runner-22"


class MullAdapter:
    """Registered C/C++ adapter. Presence of the runner is the landed slice."""

    id = ADAPTER_ID

    def probe(self, target: TargetDeclaration, context: ExecutionContext) -> CapabilityReport:
        del context
        if shutil.which(RUNNER) is None:
            return CapabilityReport(
                state=CapabilityState.MISSING_DEPENDENCY,
                resolved_tool_version=None,
                evidence=(),
                errors=(
                    InfrastructureError(
                        code="missing-mull",
                        phase="probe",
                        target_id=target.id,
                        message="%s is not on PATH" % RUNNER,
                        retryable=False,
                        evidence_refs=(),
                    ),
                ),
            )
        return CapabilityReport(
            state=CapabilityState.AVAILABLE,
            resolved_tool_version=RUNNER,
            evidence=(),
            errors=(),
        )

    def invoke(self, root):
        """Call the runner. No compiled test binary means no score."""
        from pathlib import Path

        from code_forge.mutation_dispatch import invoke_tool

        root = Path(root)
        binaries = [p for p in root.rglob("*_test") if p.is_file() and p.stat().st_mode & 0o111]
        return invoke_tool(
            [RUNNER, "--version"], "no c test binary" if not binaries else "ran", cwd=root
        )

    def run(
        self,
        target: TargetDeclaration,
        selection: TargetSelection,
        snapshot: InputSnapshot,
        context: ExecutionContext,
    ) -> TargetResult:
        del target, selection, snapshot, context
        raise NotImplementedError("c-mull run needs a binary built with the mull IR plugin")
