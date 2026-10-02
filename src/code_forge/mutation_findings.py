# SPDX-License-Identifier: Apache-2.0
"""Distinguish reported surviving mutants from measurement diagnostics."""

from .disposition import Disposition
from .state import StateFinding


def is_mutation_survivor(finding: StateFinding) -> bool:
    """The mutant- prefix is reserved for identified surviving mutants."""
    return (
        finding.source == "MUTANT"
        and finding.disposition == Disposition.CONFIRMED
        and isinstance(finding.id, str)
        and finding.id.startswith("mutant-")
        and len(finding.id) > len("mutant-")
    )


def is_mutation_diagnostic(finding: StateFinding) -> bool:
    """An unresolved mutation finding without a surviving-mutant identity."""
    return (
        finding.source == "MUTANT"
        and finding.disposition in (Disposition.CONFIRMED, Disposition.UNCERTAIN)
        and not is_mutation_survivor(finding)
    )
