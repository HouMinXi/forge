"""Phase 56-2: an entry that could not be scored is a miss, not a hole.

compute_summary excluded two kinds of entry from the finding-level counts:
those with a skipped_reason, and those whose run produced no state evidence
(findings_evidence=False). Both dropped out of the numerator AND the
denominator, so an entry the pipeline failed on cost nothing.

Under a review budget the entries most likely to fail are the expensive
ones, which are the hard defects. Dropping them raises precision and recall
for a reason that has nothing to do with review quality -- the same shape as
the best-of-N problem in 56-3, arriving by a different door.

Recall now pays for them: expected counts, hit does not. Precision does not,
because a tool that emitted nothing has made no claim that can be wrong.
"""

import pytest

from code_forge.eval.corpus import CorpusEntry, ExpectedFinding
from code_forge.eval.scorer import EvalResult, compute_summary


def _entry(n: int, name: str = "e") -> CorpusEntry:
    return CorpusEntry(
        name=name,
        diff_file=f"{name}.diff",
        expected_verdict="HOLD",
        axis_tags=["SEC"],
        expected_findings=[
            ExpectedFinding(
                file="a.py",
                line_range=(10 * i + 1, 10 * i + 3),
                description=f"defect number {i}",
            )
            for i in range(n)
        ],
    )


def _scored(n_expected: int, hits: int, fps: int = 0, name: str = "ok"):
    return EvalResult(
        entry=_entry(n_expected, name=name),
        actual_verdict="HOLD",
        runs=1,
        caught_count=1,
        skipped_reason="",
        finding_hits=hits,
        finding_misses=n_expected - hits,
        finding_fps=fps,
    )


def _skipped(n_expected: int, name: str = "skip"):
    return EvalResult(
        entry=_entry(n_expected, name=name),
        actual_verdict="SKIPPED",
        runs=0,
        caught_count=0,
        skipped_reason="timed out",
    )


def _evidenceless(n_expected: int, name: str = "noev"):
    """Ran to completion but produced no state to score against.

    A separate path from skipped_reason, on the findings_evidence half of
    the same condition. The first draft of this task missed it; found by
    grepping every findings_* assertion rather than trusting the one the
    review named.
    """
    return EvalResult(
        entry=_entry(n_expected, name=name),
        actual_verdict="HOLD",
        runs=1,
        caught_count=1,
        skipped_reason="",
        findings_evidence=False,
    )


class TestSkippedCountsAsMiss:
    def test_expected_is_counted(self):
        s = compute_summary([_skipped(2)])
        assert s.findings_expected == 2

    def test_hit_is_not_counted(self):
        s = compute_summary([_skipped(2)])
        assert s.findings_hit == 0

    def test_recall_is_charged(self):
        s = compute_summary([_skipped(2)])
        assert s.recall == 0.0

    def test_precision_is_not_charged(self):
        # Nothing was emitted, so nothing emitted was wrong.
        s = compute_summary([_skipped(2)])
        assert s.precision is None

    def test_precision_unaffected_by_a_skip_beside_a_scored_entry(self):
        scored_only = compute_summary([_scored(2, hits=1, fps=1)])
        with_skip = compute_summary([_scored(2, hits=1, fps=1), _skipped(2)])
        assert with_skip.precision == scored_only.precision
        # Both recalls are real numbers here (the scored entry supplies an
        # answer key), so the comparison is meaningful rather than a None
        # ordering accident.
        assert scored_only.recall is not None and with_skip.recall is not None
        assert with_skip.recall < scored_only.recall


class TestEvidencelessCountsAsMiss:
    """Same treatment, different exclusion path."""

    def test_expected_is_counted(self):
        assert compute_summary([_evidenceless(1)]).findings_expected == 1

    def test_misses_are_counted(self):
        assert compute_summary([_evidenceless(1)]).findings_misses == 1

    def test_recall_is_charged(self):
        assert compute_summary([_evidenceless(3)]).recall == 0.0

    def test_precision_is_not_charged(self):
        assert compute_summary([_evidenceless(3)]).precision is None


class TestHalfSkippedCorpus:
    """The plan's stated done-condition for this task."""

    def test_recall_halves_when_half_the_corpus_skips(self):
        all_scored = compute_summary(
            [_scored(2, hits=2, name=f"s{i}") for i in range(4)]
        )
        half_skipped = compute_summary(
            [_scored(2, hits=2, name=f"s{i}") for i in range(2)]
            + [_skipped(2, name=f"k{i}") for i in range(2)]
        )
        assert all_scored.recall == pytest.approx(1.0)
        assert half_skipped.recall == pytest.approx(0.5)


class TestSkipCountIsVisible:
    """A number nobody can size is a number nobody can trust.

    Publishing recall without publishing how much of the corpus actually
    ran lets a 40%-skipped run read exactly like a complete one.
    """

    def test_counts_both_exclusion_paths(self):
        s = compute_summary(
            [_scored(1, hits=1), _skipped(1), _evidenceless(1)]
        )
        assert s.findings_skipped_entries == 2

    def test_zero_when_everything_scored(self):
        assert compute_summary([_scored(1, hits=1)]).findings_skipped_entries == 0

    def test_verdict_level_skipped_field_is_untouched(self):
        # This task changes finding-level counting only. An evidenceless
        # entry is not a skipped verdict and must not become one.
        s = compute_summary([_evidenceless(1)])
        assert s.skipped == 0
        assert s.findings_skipped_entries == 1
