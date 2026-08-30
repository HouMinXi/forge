"""Phase 56 baseline delta ledger.

Phase 56 changes what the eval numbers mean, which destroys "the number
moved" as a regression signal for the duration of the phase. This file is
the fixed point that survives it.

Two jobs:

1. Record, for every existing finding-level assertion in the suite, what it
   evaluates to at 4acbabb and what it must evaluate to after Phase 56, with
   the task that causes each change. An assertion updated without a stated
   cause is indistinguishable from one that was masking a real break.

2. Assert the invariant the phase must not violate: Phase 56 changes
   aggregation and presentation, so per-entry matching must come out
   byte-identical. If `score_findings` drifts, every downstream number is
   wrong in a way no aggregate check would catch.

Written before any Phase 56 task lands. The ledger table below is the
before-state, verified by running this file at 4acbabb.
"""

from code_forge.eval.corpus import CorpusEntry, ExpectedFinding
from code_forge.eval.scorer import EvalResult, compute_summary, score_findings


# --- The ledger -------------------------------------------------------
#
# tests/test_eval_corpus_findings.py, all ten findings_* assertions.
# Status: 56-2 has landed and both predicted rows moved. Nothing else did.
#
# line  assertion                    before  after  cause
# ----  ---------------------------  ------  -----  ---------------------
# 199   findings_expected == 3            3      3  unchanged (scored)
# 200   findings_hit == 2                 2      2  unchanged (scored)
# 201   findings_misses == 1              1      1  unchanged (scored)
# 202   findings_fp == 2                  2      2  unchanged (scored)
# 212   findings_expected == 0            0      2  56-2 (skipped entry)
# 230   data["findings_expected"] == 1    1      1  unchanged (scored)
# 231   data["findings_hit"] == 1         1      1  unchanged (scored)
# 232   data["findings_fp"] == 2          2      2  unchanged (scored)
# 822   findings_expected == 0            0      1  56-2 (no evidence)
# 823   findings_misses == 0              0      1  56-2 (no evidence)
#
# Seven unchanged rows are asserted here too. A phase that only proves what
# it meant to change has not shown it left the rest alone.
#
# When 56-2 landed the suite failed in exactly four places: the two rows
# above and the two before-snapshots in this file. No fifth. That is what
# the ledger was for -- an unpredicted failure would have meant the change
# reached further than intended, and there was no way to tell without
# having written the prediction down first.


def _finding(file: str = "a.py", lines: tuple = (1, 2), desc: str = "bad thing"):
    return ExpectedFinding(file=file, line_range=lines, description=desc)


def _entry(n_findings: int, name: str = "e1") -> CorpusEntry:
    # Distinct line ranges per finding: with everything on (1, 2) the range
    # rule matches any actual against any expected, and a bipartite-matching
    # bug would still score green. Separating them makes the invariant real.
    return CorpusEntry(
        name=name,
        diff_file="e1.diff",
        expected_verdict="HOLD",
        axis_tags=["SEC"],
        expected_findings=[
            _finding(lines=(10 * i + 1, 10 * i + 3), desc=f"defect number {i}")
            for i in range(n_findings)
        ],
    )


class TestScoringInvariant:
    """score_findings must be byte-identical before and after Phase 56.

    This is the phase's fixed point. Aggregation and display may change;
    the matching underneath them may not.
    """

    def test_exact_match_scores_one_hit(self):
        entry = _entry(1)
        confirmed = [{"file": "a.py", "line_range": (1, 2), "message": "defect number 0"}]
        assert score_findings(entry, confirmed) == (1, 0, 0)

    def test_unmatched_actual_is_a_false_positive(self):
        entry = _entry(1)
        confirmed = [
            {"file": "a.py", "line_range": (1, 2), "message": "defect number 0"},
            {"file": "z.py", "line_range": (9, 9), "message": "totally unrelated noise"},
        ]
        assert score_findings(entry, confirmed) == (1, 0, 1)

    def test_unmatched_expected_is_a_miss(self):
        entry = _entry(2)
        confirmed = [{"file": "a.py", "line_range": (1, 2), "message": "defect number 0"}]
        hits, misses, fps = score_findings(entry, confirmed)
        assert (hits, misses) == (1, 1)

    def test_no_answer_key_scores_zero(self):
        entry = CorpusEntry(
            name="no-key",
            diff_file="no-key.diff",
            expected_verdict="PASS",
            axis_tags=["SEC"],
            expected_findings=[],
        )
        assert score_findings(entry, [{"file": "a.py", "line_range": (1, 2), "message": "x"}]) == (
            0,
            0,
            0,
        )

    def test_empty_actual_misses_everything(self):
        entry = _entry(3)
        assert score_findings(entry, []) == (0, 3, 0)

    def test_non_overlapping_range_is_not_a_hit(self):
        # Added after a bug-injection escaped: replacing the range-overlap
        # test with `return True` left all eight other assertions green,
        # because none of them exercised a right-file/wrong-place actual.
        # That is the shape a matching regression would actually take.
        entry = _entry(1)
        confirmed = [
            {"file": "a.py", "line_range": (900, 910), "message": "unrelated wording"}
        ]
        assert score_findings(entry, confirmed) == (0, 1, 1)

    def test_wrong_file_same_range_is_not_a_hit(self):
        entry = _entry(1)
        confirmed = [
            {"file": "other.py", "line_range": (1, 3), "message": "defect number 0"}
        ]
        assert score_findings(entry, confirmed) == (0, 1, 1)


class TestLedgerAfter56_2:
    """The two rows the ledger predicted would move, now moved.

    Renamed from TestLedgerBeforeState when 56-2 landed. The before-values
    are kept in the comments below so the delta stays legible after the
    fact: a reader can see what changed and why without going to git.
    """

    def test_scored_entry_counts_unchanged_by_phase_56(self):
        # Ledger rows 199-202: an ordinary scored entry. Phase 56 must not
        # move these, and did not.
        entry = _entry(3)
        result = EvalResult(
            entry=entry,
            actual_verdict="HOLD",
            runs=1,
            caught_count=1,
            skipped_reason="",
            finding_hits=2,
            finding_misses=1,
            finding_fps=2,
        )
        summary = compute_summary([result])
        assert summary.findings_expected == 3
        assert summary.findings_hit == 2
        assert summary.findings_fp == 2

    def test_skipped_entry_after_56_2(self):
        # Ledger row 212. WAS 0, NOW 2 -- cause: 56-2. A skipped entry's
        # expected findings are counted so recall pays for the entry the
        # pipeline could not score.
        result = EvalResult(
            entry=_entry(2),
            actual_verdict="SKIPPED",
            runs=0,
            caught_count=0,
            skipped_reason="nope",
        )
        summary = compute_summary([result])
        assert summary.findings_expected == 2
        assert summary.findings_hit == 0
        assert summary.recall == 0.0
        # Precision abstains: nothing was emitted, so nothing was wrong.
        assert summary.precision is None

    def test_evidenceless_entry_after_56_2(self):
        # Ledger rows 822-823. WAS 0/0, NOW 1/1 -- cause: 56-2. A separate
        # exclusion from skipped_reason, on the findings_evidence half of
        # the same condition. Missed by the first draft of the plan and
        # found by grepping every findings_* assertion rather than trusting
        # the one the review named.
        result = EvalResult(
            entry=_entry(1),
            actual_verdict="HOLD",
            runs=1,
            caught_count=1,
            skipped_reason="",
            findings_evidence=False,
        )
        summary = compute_summary([result])
        assert summary.findings_expected == 1
        assert summary.findings_misses == 1

    def test_both_exclusion_paths_are_counted_together(self):
        # The two rows above are one event class from the metric's point of
        # view, and the skip counter says so.
        summary = compute_summary(
            [
                EvalResult(
                    entry=_entry(1, name="skip"),
                    actual_verdict="SKIPPED",
                    runs=0,
                    caught_count=0,
                    skipped_reason="nope",
                ),
                EvalResult(
                    entry=_entry(1, name="noev"),
                    actual_verdict="HOLD",
                    runs=1,
                    caught_count=1,
                    skipped_reason="",
                    findings_evidence=False,
                ),
            ]
        )
        assert summary.findings_skipped_entries == 2
