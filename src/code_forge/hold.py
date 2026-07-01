# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""HOLD UX + ESCALATED-frozen predicate.

run_hold_ui prompts human for UNCERTAIN dispositions. check_escalated_frozen
implements DISPO-05(c) deferred from 02-02.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from .disposition import Disposition, MAX_FIX_ATTEMPTS_PER_FINGERPRINT
from .state import State, StateFinding, save_state


VALID_INPUTS = {"c": Disposition.CONFIRMED, "d": Disposition.DISMISSED}
QUIT_INPUTS = {"q"}


class HoldAborted(Exception):
    """Raised when human aborts HOLD UX (Ctrl+D / EOF / "q" input).

    Message is generic ("HOLD UX aborted by user"), not stdin-specific.
    """


def run_hold_ui(
    state: State,
    state_path: Path,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> None:
    """Prompt human for each UNCERTAIN finding.

    For each finding with disposition == UNCERTAIN:
      - Print summary (id, file:line, description).
      - Prompt: "[c]onfirm / [d]ismiss / [s]kip / [q]uit: "
      - "c" -> set disposition CONFIRMED
      - "d" -> set disposition DISMISSED
      - "s" -> leave UNCERTAIN, move on
      - "q" -> raise HoldAborted
      - invalid -> reprompt
      - EOF -> raise HoldAborted

    After loop: clear hold_reason, rebuild dispositions cache, persist.

    Idempotent: if zero UNCERTAIN findings, returns immediately with
    no I/O (caller may invoke unconditionally after PENDING return).
    """
    uncertain = [
        f for f in state.findings if f.disposition == Disposition.UNCERTAIN
    ]
    if not uncertain:
        state.hold_reason = None
        save_state(state, state_path)
        return

    output_fn(
        "HOLD: %d UNCERTAIN finding(s) need human disposition."
        % len(uncertain)
    )
    for finding in uncertain:
        _prompt_one(finding, input_fn, output_fn)

    state.hold_reason = None
    state.dispositions = {f.id: f.disposition for f in state.findings}
    save_state(state, state_path)


def _prompt_one(
    finding: StateFinding,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> None:
    """Inner per-finding prompt loop (reprompts on invalid input)."""
    lr = finding.line_range
    start = lr[0] if len(lr) >= 1 else 0
    end = lr[1] if len(lr) >= 2 else start
    output_fn(
        "  [%s] %s:%d-%d  %s"
        % (
            finding.id,
            finding.file,
            start,
            end,
            finding.description,
        )
    )
    while True:
        try:
            choice = input_fn(
                "    [c]onfirm / [d]ismiss / [s]kip / [q]uit: "
            ).strip().lower()
        except EOFError:
            raise HoldAborted("HOLD UX aborted by user")
        if choice in QUIT_INPUTS:
            raise HoldAborted("HOLD UX aborted by user")
        if choice == "s":
            return
        if choice in VALID_INPUTS:
            finding.disposition = VALID_INPUTS[choice]
            return
        output_fn("    (invalid input %r; expected c/d/s/q)" % choice)


def check_escalated_frozen(state: State) -> bool:
    """DISPO-05(c) predicate: re-CONFIRM of promoted finding -> ESCALATED.

    Returns True iff ALL of:
      - state.hold_reason is None (not currently in HOLD entry)
      - state.promoted_fingerprints is non-empty
      - at least one finding has: disposition == CONFIRMED AND
        fingerprint in promoted_fingerprints AND
        fix_attempts[fp] >= MAX_FIX_ATTEMPTS_PER_FINGERPRINT
    """
    if state.hold_reason is not None:
        return False
    if not state.promoted_fingerprints:
        return False
    for finding in state.findings:
        if (
            finding.disposition == Disposition.CONFIRMED
            and finding.fingerprint in state.promoted_fingerprints
            and state.fix_attempts.get(finding.fingerprint, 0)
            >= MAX_FIX_ATTEMPTS_PER_FINGERPRINT
        ):
            return True
    return False
