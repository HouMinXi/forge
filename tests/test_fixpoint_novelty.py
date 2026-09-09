# SPDX-License-Identifier: Apache-2.0
"""LOCAL fixpoint: a round is clean when it brings nothing new, not when
it brings nothing.

Measured 2026-09-06 on two LOCAL reviews of the same branch (state files
under tests/fixtures/fixpoint/). R3 ran six rounds, clean count never
above 0. Of 28 fingerprints that were ever CONFIRMED/UNCERTAIN, 24 were
seen live in exactly one round; only 3 changed disposition across
rounds. That is not a judge flipping on the same finding, it is three
L1 passes at non-zero temperature drawing a different sample of the
diff every round. Under clause (a) -- "any CONFIRMED fingerprint absent
from the prior round resets the counter" -- a review of a large diff
cannot terminate: each round's sample is new relative to the round
before, by construction.

The clause now asks whether the fingerprint is new relative to EVERY
prior round of this review, not just the last one. A finding that was
CONFIRMED in round 1, absent in round 2 and back in round 3 is not new;
it was already counted, and if it was not fixed the severity tiers
below still hold the review open. What no longer resets the counter is
the mere act of L1 re-sampling something it already reported.
"""
from __future__ import annotations

import json
from pathlib import Path

from code_forge.disposition import Disposition
from code_forge.llm_invoke import Usage
from code_forge.machine import _FixpointResult
from code_forge.state import StateFinding
from tests.test_runtime_machine import _make_sm

FIX = Path(__file__).parent / "fixtures" / "fixpoint"


def _F(fp: str, d: Disposition, sev: str = "P3") -> StateFinding:
    return StateFinding(id=fp, fingerprint=fp, source="L1", disposition=d,
                        file="a.py", line_range=[1, 1],
                        description="%s: %s" % (sev, fp))


class _Keep:
    def falsify(self, f):
        return f.disposition


_BIG_DIFF = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,60 +1,60 @@\n" \
    + "".join("-x%d = %d\n+x%d = %d\n" % (i, i, i, i + 1) for i in range(60))


def _sm_with_sequence(tmp_path, seq, threshold=3, cap=12):
    # A 120-line diff so that a handful of P3 findings sit under the
    # density threshold (0.15/line); _make_sm's one-line diff would turn
    # any two P3s into a CYCLE_RESTART and hide the clause under test.
    sm = _make_sm(tmp_path, git_diff=_BIG_DIFF)
    sm.falsifier = _Keep()
    i = {"n": 0}

    def l1():
        k = i["n"]; i["n"] += 1
        return (seq[k] if k < len(seq) else [], [], Usage(), 0.0)
    sm.l1_provider = l1
    sm.clean_round_threshold = threshold
    sm.max_total_rounds = cap
    return sm


def _replay_live_sets(state_path: Path) -> list[list[str]]:
    """The per-round set of fingerprints that were CONFIRMED or UNCERTAIN,
    straight from a real state.json round_history."""
    d = json.loads(state_path.read_text())
    return [[k for k, v in r["dispositions"].items() if v in ("CONFIRMED", "UNCERTAIN")]
            for r in d["round_history"]]


def test_resampled_finding_is_not_new():
    """Round 0 reports A, round 1 reports B (A absent), round 2 reports A
    again. Clause (a) under the old reading: round 2 has a CONFIRMED
    fingerprint not in round 1 -> RESET. A was already counted in round
    0; it is not new to this review."""
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    seq = [[_F("A", Disposition.CONFIRMED)],
           [_F("B", Disposition.CONFIRMED)],
           [_F("A", Disposition.CONFIRMED)]]
    sm = _sm_with_sequence(tmp, seq, cap=3)
    sm.run()
    hist = sm._state.round_history
    # round 2: A is recurring (seen in round 0), P3, below density ->
    # severity tier says CLEAN, not RESET-for-novelty
    assert hist[2]["fixpoint"] != "RESET", hist[2]


def test_genuinely_new_finding_still_resets():
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    seq = [[_F("A", Disposition.CONFIRMED)],
           [_F("A", Disposition.CONFIRMED)],
           [_F("A", Disposition.CONFIRMED), _F("Z", Disposition.CONFIRMED)]]
    sm = _sm_with_sequence(tmp, seq, cap=3)
    sm.run()
    assert sm._state.round_history[2]["fixpoint"] == "RESET"


def test_p1_recurring_still_holds_the_review_open():
    """Novelty is one clause; severity is another. A P1 that keeps
    coming back is RESET every time regardless."""
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    seq = [[_F("A", Disposition.CONFIRMED, "P1")]] * 4
    sm = _sm_with_sequence(tmp, seq, cap=4)
    sm.run()
    assert all(r["fixpoint"] == "RESET" for r in sm._state.round_history)
    assert sm._state.consecutive_clean_rounds == 0


def test_real_r3_sequence_is_also_genuinely_open():
    """Honest result: the six-round R3 run, replayed, still RESETs every
    round under the new clause. Each of its rounds brought fingerprints
    the review had never dispositioned (5/7/7/2/2/5). What the review
    was measuring there was real: L1 kept finding new things in 500
    fresh lines, and three of them were defects. The novelty clause
    does not make that go away, and must not."""
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    sets = _replay_live_sets(FIX / "a4-r3-oscillation.json")
    assert len(sets) == 6
    seq = [[_F(fp, Disposition.CONFIRMED) for fp in s] for s in sets]
    sm = _sm_with_sequence(tmp, seq, cap=6)
    sm.run()
    assert all(r["fixpoint"] == "RESET" for r in sm._state.round_history)


def test_resampling_alone_converges():
    """What the clause DOES fix: a diff with a fixed set of P3 findings
    {A B C D} that L1 samples 2-of-4 each round. Old clause: every
    round has a CONFIRMED absent from the previous round -> RESET
    forever. New clause: once all four have been seen, no round brings
    anything new, and the P3 tier lets the counter climb."""
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    A, B, C, D = (lambda: _F("A", Disposition.CONFIRMED),
                  lambda: _F("B", Disposition.CONFIRMED),
                  lambda: _F("C", Disposition.CONFIRMED),
                  lambda: _F("D", Disposition.CONFIRMED))
    seq = [[A(), B()], [C(), D()], [A(), C()], [B(), D()], [A(), D()], [B(), C()]]
    sm = _sm_with_sequence(tmp, seq, threshold=3, cap=6)
    sm.run()
    fx = [r["fixpoint"] for r in sm._state.round_history]
    assert fx[0] == "RESET" and fx[1] == "RESET"        # A B, then C D: new
    assert all(f != "RESET" for f in fx[2:]), fx          # nothing new after
    assert sm._state.consecutive_clean_rounds >= 3


def test_real_r2_sequence_is_genuinely_open():
    """R2 by contrast: round 2 brought 11 new live fingerprints out of
    12. Novelty is real there; the counter must not move."""
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    sets = _replay_live_sets(FIX / "a4-r2-oscillation.json")
    seq = [[_F(fp, Disposition.CONFIRMED) for fp in s] for s in sets]
    sm = _sm_with_sequence(tmp, seq, cap=3)
    sm.run()
    assert all(r["fixpoint"] == "RESET" for r in sm._state.round_history)
