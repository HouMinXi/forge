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

    probe and run stay honest: this host has no pwsh sandbox wired, so
    probe reports the tool missing and run refuses instead of returning
    a fabricated score.
    """

    id = ADAPTER_ID

    def probe(self, target: TargetDeclaration, context: ExecutionContext) -> CapabilityReport:
        del context
        return CapabilityReport(
            state=CapabilityState.MISSING_DEPENDENCY,
            resolved_tool_version=None,
            evidence=(),
            errors=(
                InfrastructureError(
                    code="missing-pwsh",
                    phase="probe",
                    target_id=target.id,
                    message="pwsh and PSMutant are not wired on this host",
                    retryable=False,
                    evidence_refs=(),
                ),
            ),
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


