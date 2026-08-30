"""Phase 56-3: mean across runs, not the best one.

replay_entry aggregated a multi-run replay with pick_best_findings, which
returns the single run with the most hits and fewest false positives. Every
metric built on that inherits an optimistic bias -- it is the same
methodology that makes vendor self-benchmarks unquotable, and forge's whole
reason for measuring itself is to produce a number that is not that.

Mean, not majority vote. Two reasons, both found in plan review:

  score_findings returns scalar counts, so two runs each reporting (1,1,0)
  cannot be told apart -- "both matched expected #1" and "one matched #1,
  the other #2" are the same tuple. Voting needs an identity the counts
  have already discarded.

  Majority-of-3 maps per-run detection probability p to 3p^2-2p^3. It lifts
  p=0.8 to 0.896 and pushes p=0.3 down to 0.216: it flatters easy defects
  and penalises hard ones by about a quarter. Hard defects are where a
  review tool earns its keep, so that is the wrong bias to swap an
  optimistic one for.

Mean also matches how forge runs in production, which is once.
"""

import math

import pytest

from code_forge.eval.scorer import mean_findings


class TestMeanAggregation:
    def test_single_run_is_that_run(self):
        assert mean_findings([(3, 1, 2)])[:3] == (3.0, 1.0, 2.0)

    def test_mean_of_three(self):
        # hits 2,1,1 -> 1.333..., not 2. best-of-N would report 2.
        hits, misses, fps, _, _, n = mean_findings([(2, 0, 1), (1, 1, 0), (1, 1, 2)])
        assert hits == pytest.approx(4 / 3)
        assert misses == pytest.approx(2 / 3)
        assert fps == pytest.approx(1.0)
        assert n == 3

    def test_empty_is_zero_with_no_runs(self):
        hits, misses, fps, hit_se, fp_se, n = mean_findings([])
        assert (hits, misses, fps) == (0.0, 0.0, 0.0)
        assert n == 0
        assert hit_se == 0.0 and fp_se == 0.0


class TestStandardError:
    """The variance best-of-N was hiding.

    SE = sample stdev / sqrt(n), using the n-1 denominator: these runs are
    a sample of the tool's behaviour, not the entire population of runs it
    could ever produce.
    """

    def test_zero_when_runs_agree(self):
        _, _, _, hit_se, fp_se, _ = mean_findings([(2, 0, 1)] * 3)
        assert hit_se == 0.0
        assert fp_se == 0.0

    def test_zero_for_a_single_run(self):
        # One observation has no spread to report. Not an error, and not
        # a claim of perfect consistency either -- callers see n=1 beside
        # it and can judge.
        _, _, _, hit_se, fp_se, n = mean_findings([(5, 0, 0)])
        assert (hit_se, fp_se, n) == (0.0, 0.0, 1)

    def test_matches_hand_computation(self):
        # hits 1, 2, 3: mean 2, sample stdev 1, SE = 1/sqrt(3).
        _, _, _, hit_se, _, _ = mean_findings([(1, 0, 0), (2, 0, 0), (3, 0, 0)])
        assert hit_se == pytest.approx(1 / math.sqrt(3))

    def test_grows_with_spread(self):
        tight = mean_findings([(2, 0, 0), (2, 0, 0), (2, 0, 0)])[3]
        loose = mean_findings([(0, 0, 0), (2, 0, 0), (4, 0, 0)])[3]
        assert loose > tight


class TestNotBestOfN:
    """The regression this task exists to prevent.

    Every assertion here fails if pick_best_findings comes back.
    """

    def test_mean_is_below_the_best_run(self):
        runs = [(5, 0, 0), (1, 4, 0), (1, 4, 0)]
        hits = mean_findings(runs)[0]
        assert hits == pytest.approx(7 / 3)
        assert hits < 5  # best-of-N would say 5

    def test_false_positives_are_not_minimised_away(self):
        # best-of-N breaks hit-ties toward fewest false positives, so a
        # single clean run could hide two noisy ones.
        runs = [(2, 0, 0), (2, 0, 6), (2, 0, 6)]
        fps = mean_findings(runs)[2]
        assert fps == pytest.approx(4.0)
        assert fps > 0  # best-of-N would say 0


class TestReplayEntryUsesMean:
    """The call site, not just the helper.

    A correct helper wired to nothing is the failure mode that makes unit
    tests pass while the reported numbers stay optimistic, so this drives
    the real replay_entry and reads the EvalResult it builds. The review
    itself is stubbed; the aggregation under test is not.
    """

    def test_replay_entry_reports_the_mean_across_runs(self, tmp_path, monkeypatch):
        from code_forge.eval import runner
        from code_forge.eval.corpus import CorpusEntry, ExpectedFinding

        entry = CorpusEntry(
            name="mean-entry",
            diff_file="d.diff",
            expected_verdict="HOLD",
            axis_tags=["SEC"],
            expected_findings=[
                ExpectedFinding(
                    file="a.py", line_range=(1, 3), description="first defect here"
                ),
                ExpectedFinding(
                    file="a.py", line_range=(20, 22), description="second defect here"
                ),
            ],
        )
        diff = tmp_path / "d.diff"
        diff.write_text("diff --git a/a.py b/a.py\n")

        # Run 1 finds both, runs 2 and 3 find one each. best-of-N reports
        # 2 hits; the mean is 4/3.
        per_run = [
            [
                {"file": "a.py", "line_range": (1, 3), "message": "first defect here"},
                {
                    "file": "a.py",
                    "line_range": (20, 22),
                    "message": "second defect here",
                },
            ],
            [{"file": "a.py", "line_range": (1, 3), "message": "first defect here"}],
            [{"file": "a.py", "line_range": (1, 3), "message": "first defect here"}],
        ]
        calls = {"n": 0}

        def fake_confirmed(_temp_dir):
            findings = per_run[calls["n"] % len(per_run)]
            calls["n"] += 1
            return findings

        monkeypatch.setattr(runner, "_read_confirmed_findings", fake_confirmed)
        # _run_single is the layer replay_entry actually calls; it returns
        # (flagged, skip_reason). Stubbing _run_review instead leaves the
        # real _run_single in place, which bails with a skip_reason before
        # scoring ever runs -- the entry then reports 0.0 hits and the test
        # fails for the wrong reason.
        monkeypatch.setattr(runner, "_run_single", lambda *a, **k: (True, ""))

        result = runner.replay_entry(entry, diff.parent, "stub-backend", runs=3)

        assert result.finding_hits == pytest.approx(4 / 3)
        assert result.finding_hits != 2  # best-of-N would report 2

    def test_pick_best_findings_still_exists_for_manual_use(self):
        from code_forge.eval.scorer import pick_best_findings

        assert pick_best_findings([(1, 0, 0), (2, 0, 0)]) == (2, 0, 0)
