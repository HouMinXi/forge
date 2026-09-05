# SPDX-License-Identifier: Apache-2.0
"""The fixpoint outcome of a round is written to disk with that round.

Measured 2026-09-05 on a killed LOCAL review: _execute_round persists
state.json before the fixpoint is computed, so the file always carries
the PREVIOUS round's consecutive_clean_rounds and no record of why the
counter moved. Reading the file mid-run (or after a kill) showed
clean=0 across five rounds that had in fact gone CLEAN/RESET/CLEAN/
CLEAN/RESET; the counter was right in memory and wrong on disk.

After this change every round_history entry carries `fixpoint`
(CLEAN / RESET / CYCLE_RESTART) and `clean_rounds_after`, and the
state file is persisted again once the fixpoint has been applied.
"""
from __future__ import annotations

import json

from code_forge.disposition import Disposition
from code_forge.llm_invoke import Usage
from code_forge.state import StateFinding
from tests.test_runtime_machine import _make_sm


def _F(fp: str, d: Disposition) -> StateFinding:
    return StateFinding(id=fp, fingerprint=fp, source="L1", disposition=d,
                        file="a.py", line_range=[1, 1], description=fp)


class _Keep:
    def falsify(self, f):
        return f.disposition


def _sm_with_sequence(tmp_path, seq, threshold=3, cap=8):
    sm = _make_sm(tmp_path)
    sm.falsifier = _Keep()
    i = {"n": 0}

    def l1():
        k = i["n"]; i["n"] += 1
        return (seq[k] if k < len(seq) else [], [], Usage(), 0.0)
    sm.l1_provider = l1
    sm.clean_round_threshold = threshold
    sm.max_total_rounds = cap
    return sm


def test_round_history_records_fixpoint_and_counter(tmp_path):
    seq = [[], [_F("A", Disposition.CONFIRMED)], [], [], []]
    sm = _sm_with_sequence(tmp_path, seq)
    sm.run()
    hist = sm._state.round_history
    got = [(r["round"], r["fixpoint"], r["clean_rounds_after"]) for r in hist]
    assert got == [
        (0, "CLEAN", 1),
        (1, "RESET", 0),
        (2, "CLEAN", 1),
        (3, "CLEAN", 2),
        (4, "CLEAN", 3),
    ]


def test_state_file_carries_current_round_counter(tmp_path):
    """After a round completes, state.json must show that round's
    counter, not the previous round's."""
    seq = [[], [_F("A", Disposition.CONFIRMED)], [], [], []]
    sm = _sm_with_sequence(tmp_path, seq)
    seen: list[tuple[int, int]] = []
    orig_hook = sm.post_round_hook

    def hook(round_index):
        d = json.loads((tmp_path / ".code-forge" / "state.json").read_text())
        seen.append((round_index, d["consecutive_clean_rounds"]))
        if orig_hook:
            orig_hook(round_index)
    sm.post_round_hook = hook
    sm.run()
    # post_round_hook fires from _execute_round, i.e. before the
    # fixpoint of THAT round; so the on-disk counter here is the value
    # after the previous round's fixpoint. The last persisted state
    # (after run() returns) must be the final counter.
    d = json.loads((tmp_path / ".code-forge" / "state.json").read_text())
    assert d["consecutive_clean_rounds"] == 3
    assert d["round_history"][-1]["fixpoint"] == "CLEAN"
    assert d["round_history"][-1]["clean_rounds_after"] == 3


def test_persist_happens_right_after_fixpoint(tmp_path):
    """Kill-mid-run scenario. Wrap _persist_state and record, at each
    call, whether the latest round_history entry already carries its
    fixpoint. There must be a persist whose latest entry HAS a fixpoint
    for every round, i.e. the write happens after the fixpoint of that
    round and not only at the start of the next one."""
    seq = [[], [_F("A", Disposition.CONFIRMED)], [], []]
    sm = _sm_with_sequence(tmp_path, seq, threshold=5, cap=4)
    log = []
    orig = sm._persist_state

    def spy():
        h = sm._state.round_history
        log.append((h[-1]["round"], "fixpoint" in h[-1]) if h else (None, False))
        orig()
    sm._persist_state = spy
    sm.run()
    with_fp = {r for r, has in log if has}
    assert with_fp == {0, 1, 2, 3}, log
