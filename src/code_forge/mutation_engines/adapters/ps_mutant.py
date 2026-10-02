"""PSMutant adapter.

PSMutant (Fortigi, PowerShell Gallery 0.5.0) mutates PowerShell with
the language AST and scores the result with Pester 5.2 or newer. Its
report schema v2 gives each mutant exactly two statuses: Killed or
Survived. A timeout is counted as Killed by the tool, so a third
status here would disagree with the report.
"""

from __future__ import annotations

from code_forge.mutation_engines.adapters.base import (
    CapabilityReport,
    CapabilityState,
    ExecutionContext,
    InputSnapshot,
)
from code_forge.mutation_engines.schemas import (
    InfrastructureError,
    NormalizedStatus,
    TargetDeclaration,
    TargetResult,
)
from code_forge.mutation_engines.targets import TargetSelection

ADAPTER_ID = "ps-mutant"
ADAPTER_VERSION = "1"
SUPPORTED_PSMUTANT = "0.5.0"

_STATUS = {
    "Killed": NormalizedStatus.KILLED,
    "Survived": NormalizedStatus.SURVIVED,
}


def map_psmutant_status(native: str) -> NormalizedStatus:
    """Map one report mutant Status onto the forge enumeration."""
    return _STATUS.get(native, NormalizedStatus.UNKNOWN)


class PSMutantAdapter:
    """Registered PowerShell adapter. Report mapping is the landed slice.

    probe checks pwsh is on PATH. run stays unwired: a score needs a
    Pester suite, which the review gate does not have.
    """

    id = ADAPTER_ID

    def probe(self, target: TargetDeclaration, context: ExecutionContext) -> CapabilityReport:
        del context
        import shutil

        if shutil.which("pwsh") is None:
            return CapabilityReport(
                state=CapabilityState.MISSING_DEPENDENCY,
                resolved_tool_version=None,
                evidence=(),
                errors=(
                    InfrastructureError(
                        code="missing-pwsh",
                        phase="probe",
                        target_id=target.id,
                        message="pwsh is not on PATH",
                        retryable=False,
                        evidence_refs=(),
                    ),
                ),
            )
        return CapabilityReport(
            state=CapabilityState.AVAILABLE,
            resolved_tool_version=SUPPORTED_PSMUTANT,
            evidence=(),
            errors=(),
        )

    def run(
        self,
        target: TargetDeclaration,
        selection: TargetSelection,
        snapshot: InputSnapshot,
        context: ExecutionContext,
    ) -> TargetResult:
        del target, selection, snapshot, context
        raise NotImplementedError("ps-mutant run is not wired; map the report first")

    def invoke(self, root):
        """Run pwsh against a tree. No Pester file is an empty result, not a score."""
        from pathlib import Path

        from code_forge.mutation_dispatch import invoke_tool

        root = Path(root)
        tests = list(root.rglob("*.Tests.ps1"))
        return invoke_tool(
            ["pwsh", "-NoProfile", "-Command", "Get-Command Invoke-PSMutation"],
            "no pester" if not tests else "ran",
            cwd=root,
        )
