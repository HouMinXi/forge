"""Tests for eval scorer (scorer.py)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_forge.eval.corpus import CorpusEntry
from code_forge.eval.scorer import (
    EvalResult,
    compute_summary,
    format_table,
    write_json_report,
)


def _entry(
    name: str = "test",
    expected: str = "HOLD",
    tags: list[str] | None = None,
) -> CorpusEntry:
    return CorpusEntry(
        name=name,
        diff_file=f"diffs/{name}.diff",
        expected_verdict=expected,
        axis_tags=tags or [],
    )


def _result(
    name: str = "test",
    expected: str = "HOLD",
    actual: str = "HOLD",
    runs: int = 1,
    caught_count: int = 1,
    skipped_reason: str = "",
) -> EvalResult:
    return EvalResult(
        entry=_entry(name=name, expected=expected),
        actual_verdict=actual,
        runs=runs,
        caught_count=caught_count,
        skipped_reason=skipped_reason,
    )


class TestEvalResult:
    """EvalResult frozen dataclass tests."""

    def test_construction(self) -> None:
        r = _result()
        assert r.entry.name == "test"
        assert r.actual_verdict == "HOLD"
        assert r.runs == 1
        assert r.caught_count == 1
        assert r.skipped_reason == ""

    def test_frozen(self) -> None:
        r = _result()
        with pytest.raises(AttributeError):
            r.actual_verdict = "PASS"  # type: ignore[misc]


class TestComputeSummary:
    """compute_summary tests."""

    def test_all_caught(self) -> None:
        results = [
            _result(name="a", expected="HOLD", actual="HOLD"),
            _result(name="b", expected="HOLD", actual="HOLD"),
        ]
        s = compute_summary(results)
        assert s.total == 2
        assert s.caught == 2
        assert s.missed == 0
        assert s.skipped == 0

    def test_some_missed(self) -> None:
        results = [
            _result(name="a", expected="HOLD", actual="HOLD"),
            _result(name="b", expected="HOLD", actual="PASS", caught_count=0),
        ]
        s = compute_summary(results)
        assert s.caught == 1
        assert s.missed == 1

    def test_correct_pass(self) -> None:
        """Expected PASS, actual PASS = correct pass (not a false green)."""
        results = [
            _result(name="ok", expected="PASS", actual="PASS", caught_count=0),
        ]
        s = compute_summary(results)
        assert s.correct_pass == 1
        assert s.caught == 0
        assert s.missed == 0

    def test_false_positive(self) -> None:
        """Expected PASS, actual HOLD = false positive (over-block)."""
        results = [
            _result(name="fp", expected="PASS", actual="HOLD", caught_count=1),
        ]
        s = compute_summary(results)
        assert s.false_positive == 1
        assert s.caught == 0

    def test_skipped_excluded_from_denominator(self) -> None:
        """SKIPPED entries excluded from caught+missed denominator."""
        results = [
            _result(name="caught", expected="HOLD", actual="HOLD"),
            _result(
                name="skip", expected="HOLD", actual="SKIPPED",
                caught_count=0, skipped_reason="timeout",
            ),
        ]
        s = compute_summary(results)
        assert s.total == 2
        assert s.skipped == 1
        assert s.caught == 1
        assert s.missed == 0

    def test_empty_results(self) -> None:
        s = compute_summary([])
        assert s.total == 0
        assert s.caught == 0
        assert s.missed == 0
        assert s.skipped == 0

    def test_four_quadrant_mix(self) -> None:
        results = [
            _result(name="caught1", expected="HOLD", actual="HOLD"),
            _result(name="caught2", expected="HOLD", actual="HOLD"),
            _result(
                name="missed", expected="HOLD", actual="PASS",
                caught_count=0,
            ),
            _result(name="ok", expected="PASS", actual="PASS", caught_count=0),
            _result(
                name="overblock", expected="PASS", actual="HOLD",
                caught_count=1,
            ),
            _result(
                name="skip", expected="HOLD", actual="SKIPPED",
                caught_count=0, skipped_reason="apply failed",
            ),
        ]
        s = compute_summary(results)
        assert s.total == 6
        assert s.caught == 2
        assert s.missed == 1
        assert s.correct_pass == 1
        assert s.false_positive == 1
        assert s.skipped == 1


class TestFormatTable:
    """format_table output tests."""

    def test_raw_counts_not_percentages(self) -> None:
        """format_table uses raw counts 'Caught: 7/9' NOT percentages."""
        results = [
            _result(name=f"e{i}", expected="HOLD", actual="HOLD")
            for i in range(7)
        ] + [
            _result(
                name=f"m{i}", expected="HOLD", actual="PASS",
                caught_count=0,
            )
            for i in range(2)
        ]
        s = compute_summary(results)
        table = format_table(s)
        assert "7/9" in table
        # Must NOT contain percentages
        assert "%" not in table
        assert "77.8" not in table

    def test_skip_rate_shown(self) -> None:
        """Skip rate shown beside catch count."""
        results = [
            _result(name="caught", expected="HOLD", actual="HOLD"),
            _result(
                name="skip", expected="HOLD", actual="SKIPPED",
                caught_count=0, skipped_reason="timeout",
            ),
        ]
        s = compute_summary(results)
        table = format_table(s)
        assert "1 skipped" in table

    def test_table_contains_column_headers(self) -> None:
        results = [_result()]
        s = compute_summary(results)
        table = format_table(s)
        assert "Name" in table
        assert "Expected" in table
        assert "Actual" in table

    def test_table_contains_caught_line(self) -> None:
        results = [_result()]
        s = compute_summary(results)
        table = format_table(s)
        assert "Caught:" in table


class TestWriteJsonReport:
    """write_json_report tests."""

    def test_creates_valid_json(self, tmp_path: Path) -> None:
        results = [_result(name="a"), _result(name="b")]
        s = compute_summary(results)
        out = tmp_path / "report.json"
        write_json_report(s, out)
        data = json.loads(out.read_text())
        assert data["total"] == 2
        assert data["caught"] == 2

    def test_json_contains_results(self, tmp_path: Path) -> None:
        results = [_result(name="x")]
        s = compute_summary(results)
        out = tmp_path / "report.json"
        write_json_report(s, out)
        data = json.loads(out.read_text())
        assert "results" in data
        assert len(data["results"]) == 1
        assert data["results"][0]["entry"]["name"] == "x"
