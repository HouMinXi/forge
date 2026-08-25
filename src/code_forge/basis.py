# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <[EMAIL_REDACTED]>
"""Epistemic basis disclosure module (Phase 51: BASIS-DISCLOSE).

Provides mechanical derivation of epistemic authority and falsification
survival status for findings emitted across deterministic and LLM passes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from .disposition import Disposition
from .state import StateFinding

# Epistemic Authority constants
AUTHORITY_DETERMINISTIC_EXECUTED: Final[str] = "deterministic-executed"
AUTHORITY_LLM_TRAINED: Final[str] = "llm-trained"
AUTHORITY_LLM_DOCS_LATEST: Final[str] = "llm-docs-latest"
AUTHORITY_LLM_DOCS_PINNED: Final[str] = "llm-docs-pinned"

_DETERMINISTIC_SOURCES: Final[frozenset[str]] = frozenset({
    "L0",
    "MUTANT",
    "E2E_CHECK",
    "COVERAGE",
    "INFRA",
    "FIXVAL",
    "LINT",
    "FORMAT",
    "SECURITY",
})


@dataclass(frozen=True)
class EpistemicBasis:
    """Epistemic authority and falsification record for a finding."""

    authority: str
    falsification_survived: bool
    convergence_rounds: int

    def to_dict(self) -> dict[str, Any]:
        """Convert EpistemicBasis to JSON-serializable dictionary."""
        return {
            "authority": self.authority,
            "falsification_survived": self.falsification_survived,
            "convergence_rounds": self.convergence_rounds,
        }


def derive_basis(
    finding: StateFinding,
    convergence_rounds: int = 1,
) -> EpistemicBasis:
    """Derive EpistemicBasis from a StateFinding mechanically.

    Deterministic sources are backed by executed verification tools.
    L1 sources reflect LLM generative reasoning against falsifier outcomes.
    Unknown sources raise ValueError to prevent unclassified authority leak.
    """
    if finding.source in _DETERMINISTIC_SOURCES:
        return EpistemicBasis(
            authority=AUTHORITY_DETERMINISTIC_EXECUTED,
            falsification_survived=True,
            convergence_rounds=convergence_rounds,
        )

    if finding.source == "L1":
        survived = finding.disposition != Disposition.DISMISSED
        return EpistemicBasis(
            authority=AUTHORITY_LLM_TRAINED,
            falsification_survived=survived,
            convergence_rounds=convergence_rounds,
        )

    raise ValueError(
        f"unknown finding source {finding.source!r}; add to basis derivation table"
    )
