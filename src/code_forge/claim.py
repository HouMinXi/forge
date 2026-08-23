# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Mechanical claim_type derivation for ledger rows.

claim_type = WHAT is claimed (lint / review / mutation / e2e / coverage
/ infra / fixval).  Derived from StateFinding.source, never from
model self-report.  Carries a version_sensitive attribute that
Phase 52 reads to decide whether a finding needs env-manifest context.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClaimType:
    """A mechanically derived claim classification."""

    type: str
    version_sensitive: bool


_SOURCE_TO_CLAIM: dict[str, ClaimType] = {
    "L0": ClaimType(type="lint", version_sensitive=False),
    "L1": ClaimType(type="review", version_sensitive=True),
    "MUTANT": ClaimType(type="mutation", version_sensitive=True),
    "E2E_CHECK": ClaimType(type="e2e", version_sensitive=False),
    "COVERAGE": ClaimType(type="coverage", version_sensitive=False),
    "INFRA": ClaimType(type="infra", version_sensitive=False),
    "FIXVAL": ClaimType(type="fixval", version_sensitive=False),
    "LINT": ClaimType(type="lint", version_sensitive=False),
    "FORMAT": ClaimType(type="lint", version_sensitive=False),
    "SECURITY": ClaimType(type="lint", version_sensitive=False),
}


def derive_claim_type(source: str) -> ClaimType:
    """Derive claim_type from a StateFinding.source value.

    Raises ValueError for unknown source values to surface
    pipeline changes that add new source types without updating
    the claim_type table.
    """
    ct = _SOURCE_TO_CLAIM.get(source)
    if ct is None:
        raise ValueError(
            "unknown finding source %r; add to _SOURCE_TO_CLAIM" % source
        )
    return ct
