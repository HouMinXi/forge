# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <minxi@hou.email>
"""Epistemic basis disclosure module (Phase 51: BASIS-DISCLOSE).

Provides mechanical derivation of epistemic authority and falsification
survival status for findings emitted across deterministic and LLM passes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Optional

from .disposition import Disposition
from .manifest import ManifestTier
from .state import StateFinding

# Epistemic Authority constants
AUTHORITY_DETERMINISTIC_EXECUTED: Final[str] = "deterministic-executed"
AUTHORITY_LLM_TRAINED: Final[str] = "llm-trained"
AUTHORITY_LLM_DOCS_LATEST: Final[str] = "llm-docs-latest"
AUTHORITY_LLM_DOCS_PINNED: Final[str] = "llm-docs-pinned"

_DETERMINISTIC_SOURCES: Final[frozenset[str]] = frozenset(
    {
        "L0",
        "MUTANT",
        "E2E_CHECK",
        "COVERAGE",
        "EXEC",
        "FIXVAL",
        "LINT",
        "FORMAT",
        "SECURITY",
        "RULEPACK",
    }
)


@dataclass(frozen=True)
class EpistemicBasis:
    """Epistemic authority and falsification record for a finding."""

    authority: str
    falsification_survived: bool
    convergence_rounds: int
    not_verified_against_declared_env: bool = False
    exec_evidence: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert EpistemicBasis to JSON-serializable dictionary."""
        d: dict[str, Any] = {
            "authority": self.authority,
            "falsification_survived": self.falsification_survived,
            "convergence_rounds": self.convergence_rounds,
        }
        if self.not_verified_against_declared_env:
            d["not_verified_against_declared_env"] = True
        if self.exec_evidence is not None:
            d["exec_evidence"] = self.exec_evidence
        return d


def derive_basis(
    finding: StateFinding,
    convergence_rounds: int = 1,
    manifest_tier: ManifestTier = ManifestTier.DECLARED,
    exec_evidence: Optional[str] = None,
) -> EpistemicBasis:
    """Derive EpistemicBasis from a StateFinding mechanically.

    Deterministic sources are backed by executed verification tools and
    are invariant: manifest_tier NEVER modifies or caps their authority,
    even when the environment is ABSENT (their claims are grounded in
    executed tools, not in LLM recall of a declared environment).
    L1 sources reflect LLM generative reasoning against falsifier
    outcomes and are version-sensitive: the tier of the environment
    manifest selects their epistemic authority per the 3-tier matrix:
      - DECLARED:  llm-docs-pinned, not_verified_against_declared_env=False
      - OBSERVED:  llm-docs-latest, not_verified_against_declared_env=False
      - ABSENT:    llm-docs-latest, not_verified_against_declared_env=True
    Unknown sources raise ValueError to prevent unclassified authority leak.

    When exec_evidence == "fail_before" and finding is L1/CONFIRMED,
    the basis records exec_evidence="fail_before" (EXEC-03 strengthening).
    """
    # Resolve exec_evidence for L1 CONFIRMED findings only
    effective_exec = None
    if (
        exec_evidence == "fail_before"
        and finding.source == "L1"
        and finding.disposition == Disposition.CONFIRMED
    ):
        effective_exec = "fail_before"

    if finding.source == "INFRA":
        return EpistemicBasis(
            authority="infra-unavailable",
            falsification_survived=False,
            convergence_rounds=convergence_rounds,
        )

    if finding.source in _DETERMINISTIC_SOURCES:
        return EpistemicBasis(
            authority=AUTHORITY_DETERMINISTIC_EXECUTED,
            falsification_survived=True,
            convergence_rounds=convergence_rounds,
        )

    if finding.source == "L1":
        survived = finding.disposition != Disposition.DISMISSED
        if manifest_tier == ManifestTier.DECLARED:
            return EpistemicBasis(
                authority=AUTHORITY_LLM_DOCS_PINNED,
                falsification_survived=survived,
                convergence_rounds=convergence_rounds,
                exec_evidence=effective_exec,
            )
        if manifest_tier == ManifestTier.OBSERVED:
            return EpistemicBasis(
                authority=AUTHORITY_LLM_DOCS_LATEST,
                falsification_survived=survived,
                convergence_rounds=convergence_rounds,
                exec_evidence=effective_exec,
            )
        return EpistemicBasis(
            authority=AUTHORITY_LLM_DOCS_LATEST,
            falsification_survived=survived,
            convergence_rounds=convergence_rounds,
            not_verified_against_declared_env=True,
            exec_evidence=effective_exec,
        )

    raise ValueError(f"unknown finding source {finding.source!r}; add to basis derivation table")
