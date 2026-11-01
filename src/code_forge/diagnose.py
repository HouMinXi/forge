# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""STATE-05 A/B/C/D non-convergence classifier.

Pure function over round_history + infra_errors. Called when state machine
exhausts MAX_TOTAL_ROUNDS without reaching fixpoint or HOLD.

Categories:
  A = FIXED -> CONFIRMED oscillation
      (same fingerprint toggles disposition across consecutive rounds)
  B = net CONFIRMED count not decreasing across rounds
      (R1 H4 fix: expanded to cover both genuinely new fingerprints each
      round AND stuck CONFIRMED count that auto-fix never reduces.
      Threshold: net CONFIRMED non-decreasing over last 3 rounds.)
  C = UNCERTAIN accumulation
      (falsifier indecisive; UNCERTAIN count grows monotonically >= 3 rounds)
  D = infrastructure failure
      (ANY infra_errors entry: binary trigger per R3 MED2)

Tie-breaker priority: D > A > B > C

`preserved_credit_note` is the report-only line that goes with an ESCALATED
review whose rounds since the last reset include a `preserved` round that held
clean rounds.
"""

from typing import Literal

# Longest list of preserved cycles the note spells out; the rest is counted.
_PRESERVED_NOTE_MAX_CYCLES = 5


def diagnose_non_convergence(
    round_history: list[dict],
    infra_errors: list[str],
) -> Literal["A", "B", "C", "D"]:
    """Classify why state machine failed to converge.

    round_history: list of per-round dicts with keys:
      - round: int
      - l0_fingerprints: list[str]
      - l1_fingerprints: list[str]
      - dispositions: dict[str, str]  (fingerprint -> Disposition value)
      - fixed_fingerprints: list[str] (this round)

    infra_errors: list of error message strings.

    Returns: "A" | "B" | "C" | "D"
    """
    # R3 MED2: ANY infra_errors entry -> D (binary trigger).
    # Append convergence detail so caller's ESCALATED message carries context.
    if infra_errors:
        secondary = []
        if _has_fixed_to_confirmed_toggle(round_history):
            secondary.append("oscillation (A)")
        if _has_monotonic_new_confirmed(round_history):
            secondary.append("non-decreasing confirmed (B)")
        if _has_uncertain_growth(round_history):
            secondary.append("uncertain accumulation (C)")
        if secondary:
            infra_errors.append("infrastructure errors also present: %s" % ", ".join(secondary))
        return "D"
    if _has_fixed_to_confirmed_toggle(round_history):
        return "A"
    if _has_monotonic_new_confirmed(round_history):
        return "B"
    if _has_uncertain_growth(round_history):
        return "C"
    # Default fallback: oscillation-most-likely if no other signal.
    return "A"


def _has_fixed_to_confirmed_toggle(history: list[dict]) -> bool:
    """Category A: same fingerprint FIXED in round N, CONFIRMED in N+1.

    Detects fix-loop instability where auto-fix appears to succeed but
    finding re-detects in next round.
    """
    for i in range(len(history) - 1):
        current_disps = history[i].get("dispositions", {})
        next_disps = history[i + 1].get("dispositions", {})
        for fp, disp in current_disps.items():
            if disp == "FIXED" and next_disps.get(fp) == "CONFIRMED":
                return True
    return False


def _has_monotonic_new_confirmed(history: list[dict]) -> bool:
    """Category B (R1 H4 expanded): net CONFIRMED count not decreasing.

    Returns True iff over the last 3 rounds, the count of CONFIRMED
    fingerprints did NOT strictly decrease across any consecutive pair.
    Requires >= 3 rounds of history; returns False otherwise.
    """
    if len(history) < 3:
        return False
    window = history[-3:]
    counts = []
    for entry in window:
        disps = entry.get("dispositions", {})
        n = sum(1 for v in disps.values() if v == "CONFIRMED")
        counts.append(n)
    # Must have at least one CONFIRMED in the window to qualify as B
    if max(counts) == 0:
        return False
    # Non-decreasing: every pair c[i] <= c[i+1]
    for i in range(len(counts) - 1):
        if counts[i] > counts[i + 1]:
            return False
    return True


def _has_uncertain_growth(history: list[dict]) -> bool:
    """Category C: UNCERTAIN count grows monotonically over >= 3 rounds."""
    if len(history) < 3:
        return False
    window = history[-3:]
    counts = []
    for entry in window:
        disps = entry.get("dispositions", {})
        n = sum(1 for v in disps.values() if v == "UNCERTAIN")
        counts.append(n)
    # Strictly increasing: every pair c[i] < c[i+1]
    for i in range(len(counts) - 1):
        if counts[i] >= counts[i + 1]:
            return False
    return True


def preserved_credit_note(round_history: list[dict], clean_rounds: int) -> str | None:
    """TD-P6-C3d: name the host rounds since the last reset that kept clean credit.

    A `preserved` round (schema/coverage failures only) keeps the earned
    window without earning, so an ESCALATED review that ran through such
    rounds can still hold credit. A preserved row that held no clean round
    (`clean_rounds_after` 0) kept nothing and is not named. Report-only: the
    line changes no verdict, category or credit. None when no clean round is
    held. Cycles are `round + 1`, as in the earned window and the receipt
    names.
    """
    cycles: list[int] = []
    for row in round_history:
        # A legacy row has no credit action and changes nothing here.
        action = row.get("clean_credit_action")
        if action == "reset":
            cycles = []
        elif action == "preserved" and row.get("clean_rounds_after", 0) > 0:
            cycles.append(row["round"] + 1)
    if not cycles or clean_rounds == 0:
        return None
    shown = ", ".join(str(cycle) for cycle in cycles[:_PRESERVED_NOTE_MAX_CYCLES])
    extra = len(cycles) - _PRESERVED_NOTE_MAX_CYCLES
    if extra > 0:
        shown += f" (+{extra} more)"
    # Not the tiered-reset log's "clean credit preserved" wording, which
    # names one cycle's decision.
    return (
        f"credit kept by preserved round(s) at cycle(s) {shown} "
        f"(schema/coverage failures only, not earned); {clean_rounds} clean round(s) held in total"
    )
