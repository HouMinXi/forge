"""Phase 56-1: precision, recall, F1 derived from finding-level counts.

These are properties, not stored fields, so they cannot drift from the
counts they derive from. A stored ratio that disagrees with its own
numerator is a class of bug this phase should not be able to have.

Zero-denominator returns None, never 0.0. An F1 of 0.0 is a measurement
("we scored, and scored terribly"); None is an abstention ("there was
nothing to score"). Collapsing them would let an empty run read as a
catastrophic one, or worse, the reverse.
"""

import math

import pytest

from code_forge.eval.corpus import CorpusEntry, ExpectedFinding
from code_forge.eval.scorer import EvalResult, compute_summary, format_table


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


def _summary(expected: int, hits: int, fps: int, name: str = "e"):
    """One scored entry with the given finding-level counts."""
    result = EvalResult(
        entry=_entry(expected, name=name),
        actual_verdict="HOLD",
        runs=1,
        caught_count=1,
        skipped_reason="",
        finding_hits=hits,
        finding_misses=expected - hits,
        finding_fps=fps,
    )
    return compute_summary([result])


class TestHandWorkedExample:
    """The charter requires a hand-computed case verified against the code.

    hits=6, fps=4, expected=10:
        precision = 6 / (6 + 4) = 0.60
        recall    = 6 / 10      = 0.60
        f1        = 2*.6*.6/(.6+.6) = 0.60
    """

    def test_precision(self):
        assert _summary(expected=10, hits=6, fps=4).precision == pytest.approx(0.60)

    def test_recall(self):
        assert _summary(expected=10, hits=6, fps=4).recall == pytest.approx(0.60)

    def test_f1(self):
        assert _summary(expected=10, hits=6, fps=4).f1 == pytest.approx(0.60)

    def test_f1_is_harmonic_not_arithmetic(self):
        # P and R deliberately far apart: harmonic mean 0.30, arithmetic 0.55.
        # An implementation using the arithmetic mean passes every equal-P-R
        # case above and fails only here.
        s = _summary(expected=10, hits=9, fps=41)  # P = 9/50 = 0.18, R = 0.90
        assert s.precision == pytest.approx(0.18)
        assert s.recall == pytest.approx(0.90)
        assert s.f1 == pytest.approx(2 * 0.18 * 0.90 / (0.18 + 0.90))
        assert s.f1 != pytest.approx((0.18 + 0.90) / 2)


class TestAbstentionNotZero:
    """Zero denominators return None. Each path gets its own assertion."""

    def test_precision_none_when_no_findings_emitted(self):
        # Nothing reported: no claim was made, so no claim can be wrong.
        assert _summary(expected=3, hits=0, fps=0).precision is None

    def test_recall_none_when_answer_key_empty(self):
        entry = CorpusEntry(
            name="no-key",
            diff_file="no-key.diff",
            expected_verdict="PASS",
            axis_tags=["SEC"],
            expected_findings=[],
        )
        result = EvalResult(
            entry=entry, actual_verdict="PASS", runs=1, caught_count=0,
            skipped_reason="",
        )
        assert compute_summary([result]).recall is None

    def test_f1_none_when_either_component_is_none(self):
        assert _summary(expected=3, hits=0, fps=0).f1 is None

    def test_f1_none_when_both_are_zero(self):
        # P and R both defined but zero: harmonic mean is 0/0. Still an
        # abstention rather than a score -- a division that cannot be
        # performed is not the same as a score of nothing.
        s = _summary(expected=5, hits=0, fps=7)
        assert s.precision == 0.0
        assert s.recall == 0.0
        assert s.f1 is None

    def test_zero_is_reported_when_it_is_measured(self):
        # The counterpart to the above: a real zero must not become None.
        # Findings were emitted and all were wrong.
        s = _summary(expected=5, hits=0, fps=7)
        assert s.precision == 0.0
        assert s.precision is not None


class TestSignalToNoise:
    """56-4: hits over false positives, None when there is no noise."""

    def test_ratio(self):
        assert _summary(expected=10, hits=6, fps=3).signal_to_noise == pytest.approx(2.0)

    def test_none_when_no_false_positives(self):
        # Infinitely good is not a number. Say so rather than printing inf.
        s = _summary(expected=10, hits=6, fps=0)
        assert s.signal_to_noise is None
        assert not (
            isinstance(s.signal_to_noise, float) and math.isinf(s.signal_to_noise)
        )


class TestFormatTableSurvivesNone:
    """format_table renders through format specs; None there raises TypeError.

    A crash in the reporting path is a self-inflicted false negative: the
    run produced numbers and we failed to show them.
    """

    def test_all_none_summary_does_not_raise(self):
        s = _summary(expected=3, hits=0, fps=0)
        assert s.precision is None and s.f1 is None
        table = format_table(s)  # must not raise
        assert isinstance(table, str)

    def test_abstention_is_visible_not_hidden(self):
        table = format_table(_summary(expected=3, hits=0, fps=0))
        assert "n/a" in table.lower()


class TestRatioDisplayThreshold:
    """Both sides of the Phase 17 rule, pinned.

    Phase 17 banned percentages from this table because a ratio over nine
    entries is false precision, and tests/test_eval_scorer.py:160 guards
    that. Phase 57 makes the corpus large enough for ratios to mean
    something. Rather than relax the old guard, both branches get a test:
    delete either one and the false-precision hole silently re-opens.
    """

    def _many(self, n: int, hits_each: int = 1, fps_each: int = 1):
        results = [
            EvalResult(
                entry=_entry(2, name=f"e{i}"),
                actual_verdict="HOLD",
                runs=1,
                caught_count=1,
                skipped_reason="",
                finding_hits=hits_each,
                finding_misses=2 - hits_each,
                finding_fps=fps_each,
            )
            for i in range(n)
        ]
        return compute_summary(results)

    def test_small_corpus_shows_no_percentages(self):
        table = format_table(self._many(9))
        assert "%" not in table
        assert "n/a" in table.lower()

    def test_small_corpus_says_why_it_abstained(self):
        # Abstention must be legible as a decision, not read as breakage.
        table = format_table(self._many(9))
        assert "false precision" in table.lower()

    def test_large_corpus_shows_percentages(self):
        table = format_table(self._many(30))
        assert "%" in table
        assert "Precision" in table

    def test_counts_print_on_both_sides_of_the_threshold(self):
        # The threshold gates the ratios, never the counts they come from.
        for n in (9, 30):
            assert "Findings-level:" in format_table(self._many(n))


class TestJsonReportCarriesMetrics:
    """The report is the artifact a number gets quoted from.

    format_table abstains below thirty entries; the JSON does not, because
    it is read by tools rather than skimmed, and because a number that
    exists only on a terminal cannot be traced back to later.
    """

    def _write(self, tmp_path, summary):
        import json

        from code_forge.eval.scorer import write_json_report

        out = tmp_path / "report.json"
        write_json_report(summary, out)
        return json.loads(out.read_text())

    def test_metrics_are_present(self, tmp_path):
        data = self._write(tmp_path, _summary(expected=10, hits=6, fps=4))
        assert data["precision"] == pytest.approx(0.60)
        assert data["recall"] == pytest.approx(0.60)
        assert data["f1"] == pytest.approx(0.60)

    def test_abstention_serialises_as_null_not_zero(self, tmp_path):
        # The whole point of returning None: a consumer must be able to
        # tell "not measured" from "measured, zero".
        data = self._write(tmp_path, _summary(expected=3, hits=0, fps=0))
        assert data["precision"] is None
        assert data["f1"] is None
        assert data["precision"] != 0.0

    def test_measured_zero_serialises_as_zero(self, tmp_path):
        data = self._write(tmp_path, _summary(expected=5, hits=0, fps=7))
        assert data["precision"] == 0.0
        assert data["precision"] is not None

    def test_aggregation_rule_is_named(self, tmp_path):
        # A report that does not say how it combined its runs can be
        # misread as best-of-N by anyone who remembers the old behaviour.
        data = self._write(tmp_path, _summary(expected=10, hits=6, fps=4))
        assert data["aggregation"] == "mean-of-n"

    def test_skip_count_is_present(self, tmp_path):
        data = self._write(tmp_path, _summary(expected=10, hits=6, fps=4))
        assert data["findings_skipped_entries"] == 0

    def test_ratios_appear_below_the_table_threshold(self, tmp_path):
        # One entry: format_table would abstain, the JSON must not.
        data = self._write(tmp_path, _summary(expected=10, hits=6, fps=4))
        assert data["total"] == 1
        assert data["f1"] is not None
