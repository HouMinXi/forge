"""Phase 57-3b: an entry can assert that NO finding belongs to it.

An all-HOLD corpus cannot measure precision. A reviewer that flags every
diff scores 1.0 on all three ratios, which makes the headline number this
milestone exists to produce unfalsifiable. Negative controls fix that --
but only if a finding raised against a clean entry actually reaches
findings_fp, and today it does not.

Three sites drop it. score_findings returns (0, 0, 0) on an empty
expected_findings; compute_summary skips the entry; and runner.py never
reads the findings in the first place. Fix any two and the third keeps the
number at zero while every test agrees.

The obstacle is that an empty list already means something: corpus.py
states an explicit empty expected_findings key means no answer key, and
the Phase 56 ledger pins it. Redefining [] would flip a live contract and
break that pin -- and a phase that edits a ledger assertion to make its own
behaviour pass is doing the thing the ledger exists to catch.

So the assertion gets its own signal, and absent keeps the empty list.
"""

import pytest

from code_forge.eval.corpus import CorpusEntry, ExpectedFinding, load_corpus
from code_forge.eval.scorer import EvalResult, compute_summary, score_findings

_A_FINDING = {"file": "a.py", "line_range": (1, 2), "message": "something"}


def _clean(name="clean"):
    """An entry annotated to say no finding belongs here."""
    return CorpusEntry(
        name=name,
        diff_file="%s.diff" % name,
        expected_verdict="PASS",
        axis_tags=["RUNTIME"],
        expected_findings=[],
        asserts_no_findings=True,
    )


def _absent(name="absent"):
    """An entry that was simply never annotated."""
    return CorpusEntry(
        name=name,
        diff_file="%s.diff" % name,
        expected_verdict="PASS",
        axis_tags=["RUNTIME"],
        expected_findings=[],
    )


def _keyed(name="keyed"):
    return CorpusEntry(
        name=name,
        diff_file="%s.diff" % name,
        expected_verdict="HOLD",
        axis_tags=["SEC"],
        expected_findings=[
            ExpectedFinding(file="a.py", description="defect here", line_range=(1, 5))
        ],
    )


def _result(entry, finding_hits=0.0, finding_misses=0.0, finding_fps=0.0):
    return EvalResult(
        entry=entry,
        actual_verdict=entry.expected_verdict,
        runs=1,
        caught_count=0,
        skipped_reason="",
        finding_hits=finding_hits,
        finding_misses=finding_misses,
        finding_fps=finding_fps,
    )


class TestScoreFindings:
    def test_clean_entry_counts_actuals_as_false_positives(self):
        assert score_findings(_clean(), [_A_FINDING]) == (0, 0, 1)

    def test_clean_entry_with_no_findings_is_all_zeros(self):
        # A true negative: nothing expected, nothing raised.
        assert score_findings(_clean(), []) == (0, 0, 0)

    def test_clean_entry_counts_every_actual(self):
        three = [dict(_A_FINDING, message="n%d" % i) for i in range(3)]
        assert score_findings(_clean(), three) == (0, 0, 3)

    def test_absent_key_still_scores_zero(self):
        # The Phase 56 contract, unchanged. This is the assertion the
        # ledger pins, and it must keep passing untouched.
        assert score_findings(_absent(), [_A_FINDING]) == (0, 0, 0)

    def test_keyed_entry_is_unaffected(self):
        hits, misses, fps = score_findings(
            _keyed(), [{"file": "a.py", "line_range": (1, 5), "message": "defect here"}]
        )
        assert (hits, misses, fps) == (1, 0, 0)


class TestCompoundSummary:
    def test_clean_entries_lower_precision(self):
        """The whole point: a false positive on a clean entry must land.

        Before this change the same corpus reported precision 1.0, which
        is what made an all-HOLD benchmark look rigorous.
        """
        results = [_result(_keyed("k%d" % i), finding_hits=1.0) for i in range(4)]
        results += [_result(_clean("c%d" % i), finding_fps=1.0) for i in range(4)]
        s = compute_summary(results)
        assert s.findings_fp == 4.0
        assert s.precision == pytest.approx(0.5)

    def test_clean_entries_do_not_touch_recall(self):
        # Nothing was expected of them, so they cannot be missed.
        results = [_result(_keyed("k%d" % i), finding_hits=1.0) for i in range(4)]
        with_clean = results + [_result(_clean("c"), finding_fps=3.0)]
        assert compute_summary(results).recall == compute_summary(with_clean).recall

    def test_clean_entry_is_not_counted_as_skipped(self):
        # It ran and produced a scoreable answer. Landing in the skipped
        # bucket would charge it to recall, which is the opposite of what
        # a true negative deserves.
        s = compute_summary([_result(_keyed(), finding_hits=1.0), _result(_clean())])
        assert s.findings_skipped_entries == 0

    def test_absent_key_entries_still_contribute_nothing(self):
        results = [_result(_keyed("k%d" % i), finding_hits=1.0) for i in range(4)]
        with_absent = results + [_result(_absent("a"), finding_fps=9.0)]
        a, b = compute_summary(results), compute_summary(with_absent)
        assert (a.findings_fp, a.precision) == (b.findings_fp, b.precision)

    def test_a_flag_everything_reviewer_no_longer_scores_perfectly(self):
        """The degenerate reviewer this task exists to catch.

        Ten defects found, ten clean diffs also flagged. Perfect recall,
        and precision that finally says something.
        """
        results = [_result(_keyed("k%d" % i), finding_hits=1.0) for i in range(10)]
        results += [_result(_clean("c%d" % i), finding_fps=1.0) for i in range(10)]
        s = compute_summary(results)
        assert s.recall == pytest.approx(1.0)
        assert s.precision == pytest.approx(0.5)
        assert s.f1 is not None and s.f1 < 1.0


class TestManifestRoundTrip:
    def test_flag_survives_the_loader(self, tmp_path):
        import yaml

        (tmp_path / "c.diff").write_text("diff --git a/m.py b/m.py\n")
        (tmp_path / "corpus.yaml").write_text(
            yaml.safe_dump(
                {
                    "entries": [
                        {
                            "name": "c",
                            "diff_file": "c.diff",
                            "expected_verdict": "PASS",
                            "axis_tags": ["RUNTIME"],
                            "asserts_no_findings": True,
                        }
                    ]
                }
            )
        )
        entry = load_corpus(tmp_path / "corpus.yaml")[0]
        assert entry.asserts_no_findings is True
        assert entry.expected_findings == []

    def test_absence_of_the_key_means_false(self, tmp_path):
        """The nine hand-written entries say nothing about this field.

        They must load exactly as before, which is what keeps this change
        from reaching into a corpus it has no business touching.
        """
        import yaml

        (tmp_path / "e.diff").write_text("diff --git a/m.py b/m.py\n")
        (tmp_path / "corpus.yaml").write_text(
            yaml.safe_dump(
                {
                    "entries": [
                        {
                            "name": "e",
                            "diff_file": "e.diff",
                            "expected_verdict": "HOLD",
                            "axis_tags": ["SEC"],
                        }
                    ]
                }
            )
        )
        entry = load_corpus(tmp_path / "corpus.yaml")[0]
        assert entry.asserts_no_findings is False


class TestReplayReadsFindingsForCleanEntries:
    """The third site, and the one a two-site fix leaves behind.

    runner.py gates _read_confirmed_findings on the same truthy test. Fix
    the scorer alone and findings_fp stays zero for want of input -- every
    scorer test passes while the corpus measures nothing. Injecting the old
    guard here is what proves the gate is wired, and it was green against
    the scorer tests alone.
    """

    def test_a_clean_entry_gets_its_findings_read(self, tmp_path, monkeypatch):
        from code_forge.eval import runner

        entry = _clean("replayed")
        (tmp_path / "replayed.diff").write_text("diff --git a/m.py b/m.py\n")

        def fake_confirmed(_temp_dir):
            return [dict(_A_FINDING, message="noise from the reviewer")]

        monkeypatch.setattr(runner, "_read_confirmed_findings", fake_confirmed)
        monkeypatch.setattr(runner, "_run_single", lambda *a, **k: (True, ""))

        result = runner.replay_entry(entry, tmp_path, "stub-backend", runs=1)

        assert result.finding_fps == 1.0
        assert result.findings_evidence is True
