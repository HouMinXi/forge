# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Preserved host-round validation, split out of state.py (user decision D-v11-1).

Moved verbatim; state.py imports and re-exports every name here. This
module must not import `state` at module level (state imports it), so
the one state helper it needs, `is_host_round`, is looked up lazily.
"""

from __future__ import annotations

import re

from .disposition import Disposition
from .errors import CorruptedStateError


def is_host_round(row: dict) -> bool:
    """Late-bound `state.is_host_round` (no module-level import cycle)."""
    from .state import is_host_round as state_is_host_round

    return state_is_host_round(row)


_PRESERVED_III_FP = re.compile(r"^(?:schema-fail|incomplete-coverage)-(?:qodo|expert|adversarial)$")
_RECEIPT_AUDIT_FP = re.compile(r"^receipt-[0-9a-f]{12}$")
_MUTATION_INFRA_FP = "mutation-evidence-error"


def _validate_preserved_dispositions(row: dict, prior: list[dict]) -> None:
    """Q2=(iii) cause: a preserved row must name a schema/coverage cause.

    The snapshot must name at least one CONFIRMED schema-fail/incomplete-coverage
    fingerprint that L1 observed this round. Every other open disposition must
    be credit-neutral: an UNCERTAIN receipt audit row, the mutation infra
    diagnostic, or (M4) a recurring CONFIRMED fingerprint that the fixpoint's
    own clauses would not reset on: seen in an earlier product snapshot, not a
    FIXED->CONFIRMED reversion against the previous one, and not a first
    confirmation after a falsifier protocol observation. Severity is not
    persisted, so P0-P2 refusal is the machine's counterfactual fixpoint
    (`_schema_coverage_credit_preserve`); this loader refuses every new
    CONFIRMED and every other UNCERTAIN disposition.
    """
    disps = row["dispositions"]
    l1 = row.get("l1_fingerprints")
    if not isinstance(l1, list):
        l1 = []
    causes = [
        fp
        for fp, disp in disps.items()
        if _PRESERVED_III_FP.match(fp) and disp == Disposition.CONFIRMED.value and fp in l1
    ]
    if not causes:
        raise CorruptedStateError("preserved host round lacks Q2=(iii) schema/coverage disposition")
    history = [projected for past in prior if (projected := _project_product_row(past)) is not None]
    previous = history[-1].get("dispositions", {}) if history else {}
    open_values = {Disposition.CONFIRMED.value, Disposition.UNCERTAIN.value}
    for fp, disp in disps.items():
        if disp not in open_values or fp in causes:
            continue
        if fp == _MUTATION_INFRA_FP:
            continue
        if _RECEIPT_AUDIT_FP.match(fp) and disp == Disposition.UNCERTAIN.value:
            continue
        if disp == Disposition.CONFIRMED.value and _recurring_confirmed(fp, history, previous):
            continue
        raise CorruptedStateError("preserved host round lacks (iii)-only product dispositions")


def _recurring_confirmed(fp: str, history: list[dict], previous: dict) -> bool:
    """Mirror fixpoint clauses (a), (b) and the protocol rule for one fingerprint."""
    if not any(fp in past.get("dispositions", {}) for past in history):
        return False
    if previous.get(fp) == Disposition.FIXED.value:
        return False
    confirmed_before = any(
        past.get("dispositions", {}).get(fp) == Disposition.CONFIRMED.value for past in history
    )
    protocol_before = any(fp in past.get("falsify_protocol_failures", []) for past in history)
    return confirmed_before or not protocol_before


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
