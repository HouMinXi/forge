# SPDX-License-Identifier: AGPL-3.0-or-later
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
"""
from typing import Literal


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
    if infra_errors:
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
