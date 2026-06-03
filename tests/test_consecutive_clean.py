import os
from pathlib import Path
from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.machine import StateMachine
from code_forge.state import Mode, StateFinding, Verdict, load_state


def _resolved():
    return ResolvedReview(
        source_files=[Path("test.py")], baseline_content=None,
        git_diff=None, mode_hint="git",
    )


def _finding(fp="fp-1"):
    return StateFinding(
        id=fp, fingerprint=fp, source="L1",
        disposition=Disposition.CONFIRMED,
        file="test.py", line_range=[1, 1], description="test",
    )


class TestConsecutiveClean:
    def test_needs_3_clean_rounds_not_1(self, tmp_path):
        sm = StateMachine(
            mode=Mode.LOCAL, falsifier=StubFalsifier(),
            autofixer=StubAutoFixer(), revert_fn=lambda f: None,
            resolved_review=_resolved(), source_hash="a",
            baseline_spec_repr="HEAD", cwd=tmp_path, registry={},
            l1_provider=lambda: [], max_total_rounds=10,
        )
        assert sm.run() == Verdict.PASS
        state = load_state(tmp_path / ".code-forge" / "state.json")
        assert state.consecutive_clean_rounds >= 3
        assert state.round >= 2

    def test_resets_counter_on_confirmed_finding(self, tmp_path):
        calls = {"n": 0}
        def _prov():
            calls["n"] += 1
            if calls["n"] <= 2:
                return [_finding("fp-%d" % calls["n"])]
            return []

        sm = StateMachine(
            mode=Mode.LOCAL, falsifier=StubFalsifier(),
            autofixer=StubAutoFixer(), revert_fn=lambda f: None,
            resolved_review=_resolved(), source_hash="a",
            baseline_spec_repr="HEAD", cwd=tmp_path, registry={},
            l1_provider=_prov, max_total_rounds=20,
        )
        sm.run()
        state = load_state(tmp_path / ".code-forge" / "state.json")
        assert state.round >= 4

    def test_receipts_written_during_run(self, tmp_path):
        """Integration: StateMachine.run() writes receipt files."""
        sm = StateMachine(
            mode=Mode.LOCAL, falsifier=StubFalsifier(),
            autofixer=StubAutoFixer(), revert_fn=lambda f: None,
            resolved_review=_resolved(), source_hash="a",
            baseline_spec_repr="HEAD", cwd=tmp_path, registry={},
            l1_provider=lambda: [], max_total_rounds=10,
        )
        sm.run()
        receipt_dir = tmp_path / ".code-forge" / "receipts"
        assert receipt_dir.exists()
        receipts = list(receipt_dir.glob("*.json"))
        assert len(receipts) >= 3

    def test_threshold_1_recovers_single_fixpoint(self, tmp_path):
        os.environ["FORGE_CLEAN_ROUND_THRESHOLD"] = "1"
        try:
            sm = StateMachine(
                mode=Mode.LOCAL, falsifier=StubFalsifier(),
                autofixer=StubAutoFixer(), revert_fn=lambda f: None,
                resolved_review=_resolved(), source_hash="a",
                baseline_spec_repr="HEAD", cwd=tmp_path, registry={},
                l1_provider=lambda: [], max_total_rounds=10,
            )
            assert sm.run() == Verdict.PASS
            state = load_state(tmp_path / ".code-forge" / "state.json")
            assert state.round == 0
        finally:
            del os.environ["FORGE_CLEAN_ROUND_THRESHOLD"]

    def test_all_clean_run_passes_verify(self, tmp_path, monkeypatch):
        """Regression: all-clean convergence with a real diff produces
        receipts that pass verify.

        The removed check #8 (progressive obligation) required coverage
        Jaccard distance >= 0.2 between all-clean cycles. Clean cycles
        cover the full diff every round, so the distance is always 0.0 --
        check #8 permanently blocked this legitimate path. This test locks
        the post-fix guarantee and the machine.py diff_files threading that
        gives clean-cycle receipts their real (>= 60%) coverage.
        """
        import datetime
        from unittest.mock import patch
        from code_forge.verify import parse_diff_files, run_verify

        monkeypatch.delenv("FORGE_CLEAN_ROUND_THRESHOLD", raising=False)
        diff_text = (
            "diff --git a/test.py b/test.py\n"
            "--- a/test.py\n"
            "+++ b/test.py\n"
            "@@ -1,3 +1,5 @@\n"
            " def f():\n"
            "+    x = 1\n"
            "+    y = 2\n"
            "     return 1\n"
        )
        resolved = ResolvedReview(
            source_files=[Path("test.py")], baseline_content=None,
            git_diff=diff_text, mode_hint="git",
        )
        sm = StateMachine(
            mode=Mode.LOCAL, falsifier=StubFalsifier(),
            autofixer=StubAutoFixer(), revert_fn=lambda f: None,
            resolved_review=resolved, source_hash="a",
            baseline_spec_repr="HEAD", cwd=tmp_path, registry={},
            l1_provider=lambda: [], max_total_rounds=10,
        )

        base = datetime.datetime(
            2026, 5, 28, 10, 0, 0, tzinfo=datetime.timezone.utc
        )
        counter = {"n": 0}

        def _monotonic_now(*args, **kwargs):
            ts = base + datetime.timedelta(minutes=5 * counter["n"])
            counter["n"] += 1
            return ts

        with patch("code_forge.receipt.datetime") as mock_dt:
            mock_dt.datetime.now.side_effect = _monotonic_now
            mock_dt.timedelta = datetime.timedelta
            mock_dt.timezone = datetime.timezone
            assert sm.run() == Verdict.PASS

        diff_files = parse_diff_files(diff_text)
        result = run_verify(tmp_path, "a", diff_files)
        assert result.passed, result.reason
