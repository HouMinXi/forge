# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Host round-row shape: the authority fields and the product projection.

A leaf module: it imports nothing from this package. `state` re-exports every
name here, and `state_preserved` (which `state` imports at module level)
imports the projection from here, so both bind the same function object.
"""

from __future__ import annotations

HOST_ROUND_FIELDS = frozenset(
    {
        "clean_credit_action",
        "phase_status",
        "acquisition_failures",
        "reset_observed",
        "source_hash",
        "reviewed_repositories",
    }
)


def is_host_round(row: dict) -> bool:
    """Presence of any authority field prevents legacy interpretation."""
    return bool(HOST_ROUND_FIELDS.intersection(row)) or "evidence_rejections" in row


def _project_product_row(row: dict) -> dict | None:
    """One finalized product snapshot as the fixpoint sees it, or None."""
    if not is_host_round(row):
        return row
    if row["clean_credit_action"] == "pending" or "dispositions" not in row:
        return None
    row = dict(row)
    collisions = {
        fp for name in ("l0", "l2", "e2e", "rulepack") for fp in row.get(f"{name}_fingerprints", [])
    }
    synthetic = {f["fingerprint"] for f in row["acquisition_failures"]} - collisions
    row["dispositions"] = {fp: disp for fp, disp in row["dispositions"].items() if fp not in synthetic}
    return row
