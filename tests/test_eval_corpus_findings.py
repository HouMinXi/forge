# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Findings-level answers in the eval corpus.

The verdict-level corpus (HOLD/PASS) measures false green at the gate
level only; a pipeline that holds for the wrong reason still misses
the findings the bank knows about. expected_findings adds a
findings-level answer key: each expected finding must appear in the
run's CONFIRMED findings for the entry to count as caught.
"""

from pathlib import Path

import pytest

from code_forge.eval.corpus import CorpusEntry, ExpectedFinding, load_corpus
from code_forge.eval.scorer import finding_hit


class TestExpectedFinding:
    def test_construction(self) -> None:
        f = ExpectedFinding(
            file="src/a.py", line_range=(3, 5), description="bad thing",
        )
        assert f.file == "src/a.py"
        assert f.line_range == (3, 5)
        assert f.description == "bad thing"

    def test_line_range_optional(self) -> None:
        f = ExpectedFinding(file="src/a.py", description="bad thing")
        assert f.line_range is None


class TestCorpusEntryExpectedFindings:
    def test_default_empty(self) -> None:
        entry = CorpusEntry(
            name="x", diff_file="d/x.diff",
            expected_verdict="HOLD", axis_tags=[],
        )
        assert entry.expected_findings == []

    def test_with_findings(self) -> None:
        entry = CorpusEntry(
            name="x", diff_file="d/x.diff",
            expected_verdict="HOLD", axis_tags=[],
            expected_findings=[
                ExpectedFinding(
                    file="src/a.py", line_range=(3, 5),
                    description="bad thing",
                ),
            ],
        )
        assert len(entry.expected_findings) == 1
        assert entry.expected_findings[0].line_range == (3, 5)


class TestLoadCorpusExpectedFindings:
    def _write(self, tmp_path: Path, body: str) -> Path:
        manifest = tmp_path / "corpus.yaml"
        manifest.write_text(body)
        return manifest

    def test_load_findings(self, tmp_path: Path) -> None:
        manifest = self._write(
            tmp_path,
            "entries:\n"
            "  - name: rce\n"
            "    diff_file: diffs/rce.diff\n"
            "    expected_verdict: HOLD\n"
            "    axis_tags: [TRUST]\n"
            "    expected_findings:\n"
            "      - file: src/a.py\n"
            "        line_range: [3, 5]\n"
            "        description: bad thing\n",
        )
        entries = load_corpus(manifest)
        assert len(entries[0].expected_findings) == 1
        f = entries[0].expected_findings[0]
        assert f.file == "src/a.py"
        assert f.line_range == (3, 5)
        assert f.description == "bad thing"

    def test_load_findings_without_range(self, tmp_path: Path) -> None:
        manifest = self._write(
            tmp_path,
            "entries:\n"
            "  - name: rce\n"
            "    diff_file: diffs/rce.diff\n"
            "    expected_verdict: HOLD\n"
            "    axis_tags: [TRUST]\n"
            "    expected_findings:\n"
            "      - file: src/a.py\n"
            "        description: bad thing\n",
        )
        entries = load_corpus(manifest)
        assert entries[0].expected_findings[0].line_range is None

    def test_load_missing_description_rejected(self, tmp_path: Path) -> None:
        manifest = self._write(
            tmp_path,
            "entries:\n"
            "  - name: rce\n"
            "    diff_file: diffs/rce.diff\n"
            "    expected_verdict: HOLD\n"
            "    axis_tags: [TRUST]\n"
            "    expected_findings:\n"
            "      - file: src/a.py\n"
            "        line_range: [3, 5]\n",
        )
        with pytest.raises(ValueError):
            load_corpus(manifest)

    def test_load_bad_range_rejected(self, tmp_path: Path) -> None:
        manifest = self._write(
            tmp_path,
            "entries:\n"
            "  - name: rce\n"
            "    diff_file: diffs/rce.diff\n"
            "    expected_verdict: HOLD\n"
            "    axis_tags: [TRUST]\n"
            "    expected_findings:\n"
            "      - file: src/a.py\n"
            "        line_range: [3]\n"
            "        description: bad thing\n",
        )
        with pytest.raises(ValueError):
            load_corpus(manifest)


class TestFindingHit:
    """Matching rules for one actual finding against one expected."""

    def _actual(self, file, line_range=None, description="found it"):
        return {"file": file, "line_range": line_range, "description": description}

    def test_file_and_line_overlap(self) -> None:
        expected = ExpectedFinding(
            file="src/a.py", line_range=(3, 5), description="bad thing",
        )
        assert finding_hit(self._actual("src/a.py", [4, 6]), expected)

    def test_file_mismatch(self) -> None:
        expected = ExpectedFinding(
            file="src/a.py", line_range=(3, 5), description="bad thing",
        )
        assert not finding_hit(self._actual("src/b.py", [4, 6]), expected)

    def test_no_line_overlap(self) -> None:
        expected = ExpectedFinding(
            file="src/a.py", line_range=(3, 5), description="bad thing",
        )
        assert not finding_hit(self._actual("src/a.py", [10, 12]), expected)

    def test_description_tokens_when_no_ranges(self) -> None:
        expected = ExpectedFinding(
            file="src/a.py", description="cache trade date none branch",
        )
        assert finding_hit(
            self._actual("src/a.py", None, "the cache trade date None branch"),
            expected,
        )

    def test_description_no_shared_tokens(self) -> None:
        expected = ExpectedFinding(
            file="src/a.py", description="cache trade date none branch",
        )
        assert not finding_hit(
            self._actual("src/a.py", None, "unrelated wording here"),
            expected,
        )


class TestFindingsAggregation:
    def _entry(self, n_findings=2):
        return CorpusEntry(
            name="x", diff_file="d/x.diff",
            expected_verdict="HOLD", axis_tags=[],
            expected_findings=[
                ExpectedFinding(file="src/a.py", description="bad thing %d" % i)
                for i in range(n_findings)
            ],
        )

    def _result(self, entry, hits=0, misses=0, fps=0):
        from code_forge.eval.scorer import EvalResult
        return EvalResult(
            entry=entry, actual_verdict="HOLD", runs=1, caught_count=1,
            skipped_reason="", finding_hits=hits,
            finding_misses=misses, finding_fps=fps,
        )

    def test_summary_aggregates_findings(self):
        from code_forge.eval.scorer import compute_summary
        e1 = self._entry(2)
        e2 = self._entry(1)
        r1 = self._result(e1, hits=1, misses=1, fps=2)
        r2 = self._result(e2, hits=1, misses=0, fps=0)
        s = compute_summary([r1, r2])
        assert s.findings_expected == 3
        assert s.findings_hit == 2
        assert s.findings_misses == 1
        assert s.findings_fp == 2

    def test_summary_charges_recall_for_skipped_entries(self):
        from code_forge.eval.scorer import EvalResult, compute_summary
        e = self._entry(2)
        r = EvalResult(
            entry=e, actual_verdict="SKIPPED", runs=0, caught_count=0,
            skipped_reason="nope",
        )
        s = compute_summary([r])
        # Phase 56-2: was 0. A skipped entry used to leave both numerator
        # and denominator, so the defect it carried cost nothing -- and the
        # entries that get skipped under a budget are the expensive ones,
        # which are the hard defects. Recall now pays for it; precision
        # still does not, since nothing was emitted to be wrong.
        assert s.findings_expected == 2
        assert s.findings_hit == 0
        assert s.findings_skipped_entries == 1

    def test_table_has_findings_line(self):
        from code_forge.eval.scorer import compute_summary, format_table
        e = self._entry(1)
        s = compute_summary([self._result(e, hits=1, fps=1)])
        text = format_table(s)
        # Phase 56-3: was "hit 1/1" / "false positives 1". The counts are
        # means across runs now, so the table renders them with %.2f --
        # %d floored 1.33 to 1 and printed "hit 1/2 (missed 0)", a line
        # that contradicts itself. Whole numbers gain trailing zeros;
        # the value asserted here is unchanged.
        assert "Findings-level: hit 1.00/1" in text
        assert "false positives 1.00" in text

    def test_json_report_carries_findings(self, tmp_path):
        from code_forge.eval.scorer import compute_summary, write_json_report
        e = self._entry(1)
        s = compute_summary([self._result(e, hits=1, misses=0, fps=2)])
        out = tmp_path / "r.json"
        write_json_report(s, out)
        import json
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["findings_expected"] == 1
        assert data["findings_hit"] == 1
        assert data["findings_fp"] == 2
        assert data["results"][0]["finding_hits"] == 1


class TestRunnerFindingsHelpers:
    def test_read_confirmed_findings_filters_disposition(
        self, tmp_path,
    ):
        from code_forge.eval.runner import _read_confirmed_findings
        forge_dir = tmp_path / ".code-forge"
        forge_dir.mkdir()
        (forge_dir / "state.json").write_text(
            '{"findings": ['
            ' {"disposition": "CONFIRMED", "file": "src/a.py", '
            '  "line_range": [3, 5], "description": "bad"},'
            ' {"disposition": "DISMISSED", "file": "src/b.py", '
            '  "description": "noise"},'
            ' {"disposition": "UNCERTAIN", "file": "src/c.py", '
            '  "description": "maybe"}'
            ']}'
        )
        out = _read_confirmed_findings(str(tmp_path))
        assert len(out) == 1
        assert out[0]["file"] == "src/a.py"
        assert out[0]["line_range"] == [3, 5]

    def test_read_confirmed_findings_missing_state(self, tmp_path):
        from code_forge.eval.runner import _read_confirmed_findings
        assert _read_confirmed_findings(str(tmp_path)) is None

    def test_read_confirmed_findings_malformed(self, tmp_path):
        from code_forge.eval.runner import _read_confirmed_findings
        forge_dir = tmp_path / ".code-forge"
        forge_dir.mkdir()
        (forge_dir / "state.json").write_text("{not json")
        assert _read_confirmed_findings(str(tmp_path)) is None

    def test_score_findings_hits_misses_fps(self):
        from code_forge.eval.scorer import score_findings
        entry = CorpusEntry(
            name="x", diff_file="d/x.diff",
            expected_verdict="HOLD", axis_tags=[],
            expected_findings=[
                ExpectedFinding(
                    file="src/a.py", line_range=(3, 5),
                    description="bad thing",
                ),
                ExpectedFinding(
                    file="src/b.py", description="other defect",
                ),
            ],
        )
        confirmed = [
            {"file": "src/a.py", "line_range": [4, 6], "description": "bad thing"},
            {"file": "src/c.py", "description": "noise finding"},
        ]
        hits, misses, fps = score_findings(entry, confirmed)
        assert hits == 1
        assert misses == 1
        assert fps == 1

    def test_score_findings_empty_key(self):
        from code_forge.eval.scorer import score_findings
        entry = CorpusEntry(
            name="x", diff_file="d/x.diff",
            expected_verdict="HOLD", axis_tags=[],
        )
        assert score_findings(entry, []) == (0, 0, 0)


class TestPickBestFindings:
    def test_empty(self):
        from code_forge.eval.scorer import pick_best_findings
        assert pick_best_findings([]) == (0, 0, 0)

    def test_most_hits_wins(self):
        from code_forge.eval.scorer import pick_best_findings
        per_run = [(1, 1, 2), (2, 0, 3), (0, 2, 0)]
        assert pick_best_findings(per_run) == (2, 0, 3)

    def test_tie_prefers_fewer_fps(self):
        from code_forge.eval.scorer import pick_best_findings
        per_run = [(2, 0, 3), (2, 0, 1)]
        assert pick_best_findings(per_run) == (2, 0, 1)


class TestReplayEntryWiring:
    """replay_entry must actually read state.json and score findings."""

    def test_replay_scores_findings_from_state(
        self, tmp_path: Path,
    ) -> None:
        import json
        from unittest.mock import MagicMock, patch

        from code_forge.eval.runner import replay_entry

        def fake_review(cmd, cwd, env, timeout_s):
            forge_dir = Path(cwd) / ".code-forge"
            forge_dir.mkdir(parents=True, exist_ok=True)
            (forge_dir / "state.json").write_text(json.dumps({
                "findings": [
                    {"disposition": "CONFIRMED", "file": "src/a.py",
                     "line_range": [4, 6], "description": "bad thing"},
                ],
            }))
            return (1, "")

        with patch(
            "code_forge.eval.runner._run_review",
            side_effect=fake_review,
        ), patch(
            "code_forge.eval.runner.subprocess.run",
            return_value=MagicMock(returncode=0, stderr=b"", stdout=b""),
        ), patch(
            "code_forge.eval.runner.record_trust",
        ):
            corpus = tmp_path / "corpus"
            diffs = corpus / "diffs"
            diffs.mkdir(parents=True)
            (diffs / "test.diff").write_text("--- a/f\n+++ b/f\n")

            entry = CorpusEntry(
                name="t", diff_file="diffs/test.diff",
                expected_verdict="HOLD", axis_tags=["TRUST"],
                expected_findings=[
                    ExpectedFinding(
                        file="src/a.py", line_range=(3, 5),
                        description="bad thing",
                    ),
                ],
            )
            result = replay_entry(entry, corpus, "test-backend")
        assert result.finding_hits == 1
        assert result.finding_misses == 0
        assert result.finding_fps == 0


class TestMalformedInputHardening:
    def _write(self, tmp_path: Path, body: str) -> Path:
        manifest = tmp_path / "corpus.yaml"
        manifest.write_text(body)
        return manifest

    def test_expected_findings_null_coerced_empty(self, tmp_path: Path) -> None:
        manifest = self._write(
            tmp_path,
            "entries:\n"
            "  - name: rce\n"
            "    diff_file: d.diff\n"
            "    expected_verdict: HOLD\n"
            "    axis_tags: []\n"
            "    expected_findings: null\n",
        )
        entries = load_corpus(manifest)
        assert entries[0].expected_findings == []

    def test_expected_findings_scalar_entries_rejected(
        self, tmp_path: Path,
    ) -> None:
        manifest = self._write(
            tmp_path,
            "entries:\n"
            "  - name: rce\n"
            "    diff_file: d.diff\n"
            "    expected_verdict: HOLD\n"
            "    axis_tags: []\n"
            "    expected_findings: ['foo']\n",
        )
        with pytest.raises(ValueError):
            load_corpus(manifest)

    def test_bool_range_rejected(self, tmp_path: Path) -> None:
        manifest = self._write(
            tmp_path,
            "entries:\n"
            "  - name: rce\n"
            "    diff_file: d.diff\n"
            "    expected_verdict: HOLD\n"
            "    axis_tags: []\n"
            "    expected_findings:\n"
            "      - file: a.py\n"
            "        description: bad\n"
            "        line_range: [true, false]\n",
        )
        with pytest.raises(ValueError):
            load_corpus(manifest)

    def test_inverted_range_rejected(self, tmp_path: Path) -> None:
        manifest = self._write(
            tmp_path,
            "entries:\n"
            "  - name: rce\n"
            "    diff_file: d.diff\n"
            "    expected_verdict: HOLD\n"
            "    axis_tags: []\n"
            "    expected_findings:\n"
            "      - file: a.py\n"
            "        description: bad\n"
            "        line_range: [5, 2]\n",
        )
        with pytest.raises(ValueError):
            load_corpus(manifest)


class TestConfirmedFindingsHardening:
    def _write_state(self, tmp_path: Path, content: str) -> None:
        forge_dir = tmp_path / ".code-forge"
        forge_dir.mkdir()
        (forge_dir / "state.json").write_text(content)

    def test_null_state_returns_none(self, tmp_path: Path) -> None:
        from code_forge.eval.runner import _read_confirmed_findings
        self._write_state(tmp_path, "null")
        assert _read_confirmed_findings(str(tmp_path)) is None

    def test_list_state_returns_none(self, tmp_path: Path) -> None:
        from code_forge.eval.runner import _read_confirmed_findings
        self._write_state(tmp_path, "[]")
        assert _read_confirmed_findings(str(tmp_path)) is None

    def test_findings_null_returns_none(self, tmp_path: Path) -> None:
        from code_forge.eval.runner import _read_confirmed_findings
        self._write_state(tmp_path, '{"findings": null}')
        assert _read_confirmed_findings(str(tmp_path)) is None

    def test_missing_state_returns_none(self, tmp_path: Path) -> None:
        from code_forge.eval.runner import _read_confirmed_findings
        assert _read_confirmed_findings(str(tmp_path)) is None

    def test_empty_file_finding_skipped(self, tmp_path: Path) -> None:
        from code_forge.eval.runner import _read_confirmed_findings
        self._write_state(
            tmp_path,
            '{"findings": [{"disposition": "CONFIRMED", '
            '"file": "", "description": "bad thing"}]}',
        )
        assert _read_confirmed_findings(str(tmp_path)) == []

    def test_unicode_error_returns_none(self, tmp_path: Path) -> None:
        from code_forge.eval.runner import _read_confirmed_findings
        forge_dir = tmp_path / ".code-forge"
        forge_dir.mkdir()
        (forge_dir / "state.json").write_bytes(b"\xff\xfe\x00")
        assert _read_confirmed_findings(str(tmp_path)) is None

    def test_non_dict_entries_skipped(self, tmp_path: Path) -> None:
        from code_forge.eval.runner import _read_confirmed_findings
        self._write_state(tmp_path, '{"findings": ["junk"]}')
        assert _read_confirmed_findings(str(tmp_path)) == []

    def test_malformed_range_becomes_none(self, tmp_path: Path) -> None:
        from code_forge.eval.runner import _read_confirmed_findings
        self._write_state(
            tmp_path,
            '{"findings": [{"disposition": "CONFIRMED", '
            '"file": "a.py", "line_range": "12", '
            '"description": "bad"}]}',
        )
        out = _read_confirmed_findings(str(tmp_path))
        assert len(out) == 1
        assert out[0]["line_range"] is None


class TestFindingHitHardening:
    def test_string_range_falls_to_description(self) -> None:
        expected = ExpectedFinding(
            file="a.py", description="cache trade date none branch",
        )
        actual = {
            "file": "a.py", "line_range": "12",
            "description": "the cache trade date none branch",
        }
        assert finding_hit(actual, expected)

    def test_short_range_list_falls_to_description(self) -> None:
        expected = ExpectedFinding(
            file="a.py", description="cache trade date none branch",
        )
        actual = {
            "file": "a.py", "line_range": [4],
            "description": "the cache trade date none branch",
        }
        assert finding_hit(actual, expected)

    def test_non_string_description_no_crash(self) -> None:
        expected = ExpectedFinding(
            file="a.py", description="cache trade date none branch",
        )
        actual = {"file": "a.py", "line_range": None, "description": 42}
        assert not finding_hit(actual, expected)


class TestJsonReportExpectedFindings:
    def test_entry_carries_answer_key(self, tmp_path: Path) -> None:
        import json
        from code_forge.eval.scorer import (
            EvalResult, compute_summary, write_json_report,
        )
        entry = CorpusEntry(
            name="x", diff_file="d/x.diff",
            expected_verdict="HOLD", axis_tags=[],
            expected_findings=[
                ExpectedFinding(
                    file="a.py", line_range=(3, 5),
                    description="bad thing",
                ),
            ],
        )
        result = EvalResult(
            entry=entry, actual_verdict="HOLD", runs=1, caught_count=1,
            skipped_reason="",
        )
        out = tmp_path / "r.json"
        write_json_report(compute_summary([result]), out)
        data = json.loads(out.read_text(encoding="utf-8"))
        ef = data["results"][0]["entry"]["expected_findings"]
        assert ef == [
            {"file": "a.py", "description": "bad thing",
             "line_range": [3, 5]},
        ]


class TestFindingHitHardeningWithRanges:
    def test_malformed_actual_range_with_expected_range(self) -> None:
        """expected carries a range; malformed actual must fall through
        to the description rule instead of crashing the scorer."""
        expected = ExpectedFinding(
            file="a.py", line_range=(3, 5),
            description="cache trade date none branch",
        )
        actual = {
            "file": "a.py", "line_range": "12",
            "description": "the cache trade date none branch",
        }
        assert finding_hit(actual, expected)

    def test_malformed_actual_range_no_desc_overlap(self) -> None:
        expected = ExpectedFinding(
            file="a.py", line_range=(3, 5),
            description="cache trade date none branch",
        )
        actual = {
            "file": "a.py", "line_range": [4],
            "description": "unrelated wording here",
        }
        assert not finding_hit(actual, expected)


class TestKuhnMatching:
    def test_greedy_counterexample_exact_match(self) -> None:
        """Greedy first-match under-counts here: E1 (broad) matches
        A1 and A2, E2 (narrow) matches A1 only. Greedy takes E1->A1
        and leaves E2 unmatched (1 hit); maximum matching assigns
        E1->A2, E2->A1 (2 hits)."""
        from code_forge.eval.scorer import score_findings
        entry = CorpusEntry(
            name="x", diff_file="d/x.diff",
            expected_verdict="HOLD", axis_tags=[],
            expected_findings=[
                ExpectedFinding(
                    file="a.py", line_range=(1, 9),
                    description="first broad defect",
                ),
                ExpectedFinding(
                    file="a.py", line_range=(1, 3),
                    description="second narrow defect",
                ),
            ],
        )
        confirmed = [
            {"file": "a.py", "line_range": [1, 3],
             "description": "narrow defect one"},
            {"file": "a.py", "line_range": [7, 9],
             "description": "broad defect two"},
        ]
        hits, misses, fps = score_findings(entry, confirmed)
        assert hits == 2
        assert misses == 0
        assert fps == 0


class TestR2Fixes:
    def test_greedy_dedup_no_inflation(self) -> None:
        """One actual finding must not hit two expected entries."""
        from code_forge.eval.scorer import score_findings
        entry = CorpusEntry(
            name="x", diff_file="d/x.diff",
            expected_verdict="HOLD", axis_tags=[],
            expected_findings=[
                ExpectedFinding(
                    file="a.py", line_range=(3, 5),
                    description="first defect thing",
                ),
                ExpectedFinding(
                    file="a.py", line_range=(4, 6),
                    description="second defect thing",
                ),
            ],
        )
        confirmed = [
            {"file": "a.py", "line_range": [4, 5],
             "description": "overlapping defect thing"},
        ]
        hits, misses, fps = score_findings(entry, confirmed)
        assert hits == 1
        assert misses == 1
        assert fps == 0

    def test_inverted_actual_range_treated_absent(self) -> None:
        expected = ExpectedFinding(
            file="a.py", line_range=(3, 5),
            description="cache trade date none branch",
        )
        actual = {
            "file": "a.py", "line_range": [5, 1],
            "description": "the cache trade date none branch",
        }
        assert finding_hit(actual, expected)

    def test_zero_based_actual_range_treated_absent(self) -> None:
        expected = ExpectedFinding(
            file="a.py", line_range=(3, 5),
            description="cache trade date none branch",
        )
        actual = {
            "file": "a.py", "line_range": [0, 5],
            "description": "the cache trade date none branch",
        }
        assert finding_hit(actual, expected)

    def test_short_expected_description_single_token_match(
        self,
    ) -> None:
        expected = ExpectedFinding(file="a.py", description="bad thing")
        assert finding_hit(
            {"file": "a.py", "description": "this is a bad thing here"},
            expected,
        )
        assert not finding_hit(
            {"file": "a.py", "description": "unrelated wording"},
            expected,
        )

    def test_whitespace_file_rejected(self, tmp_path: Path) -> None:
        manifest = tmp_path / "corpus.yaml"
        manifest.write_text(
            "entries:\n"
            "  - name: rce\n"
            "    diff_file: d.diff\n"
            "    expected_verdict: HOLD\n"
            "    axis_tags: []\n"
            "    expected_findings:\n"
            "      - file: '   '\n"
            "        description: bad thing\n",
        )
        with pytest.raises(ValueError):
            load_corpus(manifest)

    def test_valid_line_range_semantics(self) -> None:
        from code_forge.eval.scorer import valid_line_range
        assert valid_line_range([3, 5])
        assert valid_line_range((3, 3))
        assert not valid_line_range([0, 5])
        assert not valid_line_range([5, 1])
        assert not valid_line_range([True, 5])
        assert not valid_line_range("12")
        assert not valid_line_range([3])
        assert not valid_line_range(None)


class TestZeroTokenDescription:
    def test_zero_sig_token_expected_matches_any_token(self) -> None:
        expected = ExpectedFinding(file="a.py", description="RCE bug")
        assert finding_hit(
            {"file": "a.py", "description": "an RCE bug here"},
            expected,
        )
        assert not finding_hit(
            {"file": "a.py", "description": "clean code only"},
            expected,
        )


class TestPaddedValues:
    def test_padded_file_stored_stripped(self, tmp_path: Path) -> None:
        """A whitespace-padded but valid file must be stored stripped
        so exact matching in finding_hit can succeed."""
        manifest = tmp_path / "corpus.yaml"
        manifest.write_text(
            "entries:\n"
            "  - name: rce\n"
            "    diff_file: d.diff\n"
            "    expected_verdict: HOLD\n"
            "    axis_tags: []\n"
            "    expected_findings:\n"
            "      - file: '  src/a.py  '\n"
            "        description: bad thing\n",
        )
        entries = load_corpus(manifest)
        assert entries[0].expected_findings[0].file == "src/a.py"


class TestReplayEntryMissingState:
    def test_run_without_state_does_not_crash(self, tmp_path: Path) -> None:
        """A run that produces no state.json must not crash the
        scoring (and must not participate in findings scoring)."""
        from unittest.mock import MagicMock, patch

        from code_forge.eval.runner import replay_entry

        with patch(
            "code_forge.eval.runner._run_review",
            return_value=(1, ""),
        ), patch(
            "code_forge.eval.runner.subprocess.run",
            return_value=MagicMock(returncode=0, stderr=b"", stdout=b""),
        ), patch(
            "code_forge.eval.runner.record_trust",
        ):
            corpus = tmp_path / "corpus"
            diffs = corpus / "diffs"
            diffs.mkdir(parents=True)
            (diffs / "test.diff").write_text("--- a/f\n+++ b/f\n")

            entry = CorpusEntry(
                name="t", diff_file="diffs/test.diff",
                expected_verdict="HOLD", axis_tags=["TRUST"],
                expected_findings=[
                    ExpectedFinding(
                        file="src/a.py", line_range=(3, 5),
                        description="bad thing",
                    ),
                ],
            )
            result = replay_entry(entry, corpus, "test-backend")
        assert result.finding_hits == 0
        assert result.finding_misses == 0


class TestR4Fixes:
    def test_invalid_expected_range_falls_to_description(self) -> None:
        """An ExpectedFinding constructed programmatically with an
        invalid range must not be permanently un-hittable: with a
        valid actual range present, the overlap branch must be
        skipped in favour of the description rule."""
        expected = ExpectedFinding(
            file="a.py", line_range=(5, 1),
            description="cache trade date none branch",
        )
        assert finding_hit(
            {"file": "a.py", "line_range": [3, 4],
             "description": "the cache trade date none branch"},
            expected,
        )

    def test_punctuation_only_description_rejected(
        self, tmp_path: Path,
    ) -> None:
        manifest = tmp_path / "corpus.yaml"
        manifest.write_text(
            "entries:\n"
            "  - name: rce\n"
            "    diff_file: d.diff\n"
            "    expected_verdict: HOLD\n"
            "    axis_tags: []\n"
            "    expected_findings:\n"
            "      - file: a.py\n"
            "        description: '!!!'\n",
        )
        with pytest.raises(ValueError):
            load_corpus(manifest)

    def test_evidenceless_entry_excluded_from_summary(self) -> None:
        from code_forge.eval.scorer import EvalResult, compute_summary
        entry = CorpusEntry(
            name="x", diff_file="d/x.diff",
            expected_verdict="HOLD", axis_tags=[],
            expected_findings=[
                ExpectedFinding(
                    file="a.py", line_range=(3, 5),
                    description="bad thing",
                ),
            ],
        )
        r = EvalResult(
            entry=entry, actual_verdict="HOLD", runs=1, caught_count=1,
            skipped_reason="", findings_evidence=False,
        )
        s = compute_summary([r])
        # Phase 56-2: was 0/0. An entry that ran but produced no state
        # evidence is a second exclusion path beside skipped_reason, on the
        # findings_evidence half of the same condition. Same treatment for
        # the same reason: the defect was there, it was not reported, and
        # how the run failed to produce evidence is not the corpus's
        # problem.
        assert s.findings_expected == 1
        assert s.findings_misses == 1
        assert s.findings_skipped_entries == 1
