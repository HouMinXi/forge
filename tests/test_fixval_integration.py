# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Integration tests for FIXVAL pipeline wiring.

Tests exercise the FIXVAL gate through StateMachine to verify:
- Hollow test blocks with FAIL verdict
- Non-hollow test passes with PASS verdict
- Skip (no test+code pairing) records FIXVAL_SKIPPED, never silent
- Waiver produces advisory
- Overfit guard emits advisory on PASS
- Non-converged machine never runs FIXVAL
- _get_commit_message works correctly
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from code_forge.advisory import AdvisoryFinding
from code_forge.autofix import FixOutcome, StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.fixval import (
    FixvalCandidate,
    FixvalResult,
    FixvalSkip,
    FixvalStatus,
)
from code_forge.machine import StateMachine
from code_forge.state import Mode, StateFinding, Verdict


def _make_resolved(
    source_files=None, git_diff="--- a/foo.py\n+++ b/foo.py\n",
):
    """Create a ResolvedReview for tests."""
    return ResolvedReview(
        source_files=source_files or [Path("src/foo.py"), Path("tests/test_foo.py")],
        baseline_content=None,
        git_diff=git_diff,
        mode_hint="git",
    )


def _make_machine(tmp_path, resolved=None, l0_runner=None):
    """Create a StateMachine that converges cleanly in 3 rounds."""
    if l0_runner is None:
        def l0_runner(registry, files):
            return ([], [])

    # Create gate.yaml so FIXVAL can read test.command
    gate_dir = tmp_path / ".code-forge"
    gate_dir.mkdir(parents=True, exist_ok=True)
    gate_yaml = gate_dir / "gate.yaml"
    gate_yaml.write_text(
        "test:\n  command:\n    - python\n    - -m\n    - pytest\n",
        encoding="utf-8",
    )

    return StateMachine(
        mode=Mode.LOCAL,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=resolved or _make_resolved(),
        source_hash="abc",
        baseline_spec_repr="empty",
        cwd=tmp_path,
        registry={},
        l0_runner=l0_runner,
    )


class TestFixvalBlocksHollowTest:
    """FIXVAL blocks when test passes on both fixed and reverted code."""

    def test_hollow_returns_fail(self, tmp_path):
        hollow_finding = StateFinding(
            id="FIXVAL_HOLLOW",
            fingerprint="fixval-hollow",
            source="FIXVAL",
            disposition=Disposition.DISMISSED,
            file="tests/test_foo.py",
            line_range=[],
            description="hollow test",
        )
        block_result = FixvalResult(
            status=FixvalStatus.BLOCK,
            findings=[hollow_finding],
            advisories=[],
            block_message="Test did not fail on revert",
        )

        machine = _make_machine(tmp_path)

        with (
            patch(
                "code_forge.fixval.classify_fixval_candidate",
                return_value=FixvalCandidate(
                    test_files=["tests/test_foo.py"],
                    non_test_files=["src/foo.py"],
                ),
            ),
            patch(
                "code_forge.fixval.run_fixval",
                return_value=block_result,
            ),
        ):
            verdict = machine.run()

        assert verdict == Verdict.FAIL
        assert machine._state.converged is False

        # FIXVAL_HOLLOW finding present with DISMISSED disposition
        fixval_findings = [
            f for f in machine._state.findings
            if f.source == "FIXVAL"
        ]
        assert len(fixval_findings) >= 1
        hollow = [f for f in fixval_findings if f.id == "FIXVAL_HOLLOW"]
        assert len(hollow) == 1
        assert hollow[0].disposition == Disposition.DISMISSED
        # block_message stored in error field
        assert hollow[0].error == "Test did not fail on revert"


class TestFixvalPassesNonhollowTest:
    """FIXVAL passes when test fails on reverted code (not hollow)."""

    def test_nonhollow_returns_pass(self, tmp_path):
        pass_result = FixvalResult(
            status=FixvalStatus.PASS,
            findings=[],
            advisories=[],
        )

        machine = _make_machine(tmp_path)

        with (
            patch(
                "code_forge.fixval.classify_fixval_candidate",
                return_value=FixvalCandidate(
                    test_files=["tests/test_foo.py"],
                    non_test_files=["src/foo.py"],
                ),
            ),
            patch(
                "code_forge.fixval.run_fixval",
                return_value=pass_result,
            ),
            patch(
                "code_forge.fixval.run_overfit_guard",
                return_value=[],
            ),
        ):
            verdict = machine.run()

        assert verdict == Verdict.PASS
        assert machine._state.converged is True

        # No FIXVAL_HOLLOW finding
        hollow = [
            f for f in machine._state.findings
            if f.id == "FIXVAL_HOLLOW"
        ]
        assert len(hollow) == 0


class TestFixvalSkipsNoTestFile:
    """FIXVAL records SKIPPED when no test+code pairing exists."""

    def test_skip_records_finding(self, tmp_path):
        machine = _make_machine(tmp_path)

        with patch(
            "code_forge.fixval.classify_fixval_candidate",
            return_value=FixvalSkip(reason="no test file in diff"),
        ):
            verdict = machine.run()

        assert verdict == Verdict.PASS

        skip_findings = [
            f for f in machine._state.findings
            if f.id == "FIXVAL_SKIPPED"
        ]
        assert len(skip_findings) == 1
        assert skip_findings[0].disposition == Disposition.DISMISSED
        assert "no test file" in skip_findings[0].description


class TestFixvalWaiverProducesAdvisory:
    """Waiver results in PASS verdict with advisory recorded."""

    def test_waiver_advisory_emitted(self, tmp_path):
        waiver_advisory = AdvisoryFinding(
            id="FIXVAL_WAIVER_RECORD",
            axis="FIXVAL",
            file="",
            line_range=[],
            description="FIXVAL waived via FIXVAL_WAIVER env var: flaky",
            attribution="fixval-waiver",
        )
        waived_result = FixvalResult(
            status=FixvalStatus.WAIVED,
            findings=[
                StateFinding(
                    id="FIXVAL_WAIVED",
                    fingerprint="fixval-waived",
                    source="FIXVAL",
                    disposition=Disposition.DISMISSED,
                    file="",
                    line_range=[],
                    description="FIXVAL waived: flaky",
                ),
            ],
            advisories=[waiver_advisory],
        )

        machine = _make_machine(tmp_path)

        with (
            patch(
                "code_forge.fixval.classify_fixval_candidate",
                return_value=FixvalCandidate(
                    test_files=["tests/test_foo.py"],
                    non_test_files=["src/foo.py"],
                ),
            ),
            patch(
                "code_forge.fixval.run_fixval",
                return_value=waived_result,
            ),
        ):
            verdict = machine.run()

        assert verdict == Verdict.PASS

        # Advisory present
        assert any(
            a.id == "FIXVAL_WAIVER_RECORD"
            for a in machine._advisories
        )

        # Advisory serialized to file
        advisory_path = tmp_path / ".code-forge" / "advisory-findings.json"
        assert advisory_path.exists()
        import json
        data = json.loads(advisory_path.read_text(encoding="utf-8"))
        waiver_entries = [
            e for e in data if e.get("id") == "FIXVAL_WAIVER_RECORD"
        ]
        assert len(waiver_entries) == 1


class TestFixvalOverfitAdvisoryEmitted:
    """Overfit guard advisory emitted when FIXVAL passes."""

    def test_overfit_advisory_in_advisories(self, tmp_path):
        overfit_advisory = AdvisoryFinding(
            id="FIXVAL_OVERFIT",
            axis="FIXVAL",
            file="src/foo.py",
            line_range=[],
            description="test may be overfitting to variable names",
            attribution="fixval-overfit-guard",
        )

        machine = _make_machine(tmp_path)

        with (
            patch(
                "code_forge.fixval.classify_fixval_candidate",
                return_value=FixvalCandidate(
                    test_files=["tests/test_foo.py"],
                    non_test_files=["src/foo.py"],
                ),
            ),
            patch(
                "code_forge.fixval.run_fixval",
                return_value=FixvalResult(
                    status=FixvalStatus.PASS,
                    findings=[],
                    advisories=[],
                ),
            ),
            patch(
                "code_forge.fixval.run_overfit_guard",
                return_value=[overfit_advisory],
            ),
        ):
            verdict = machine.run()

        assert verdict == Verdict.PASS
        assert any(
            a.id == "FIXVAL_OVERFIT" for a in machine._advisories
        )


class TestFixvalNotRunOnNonConverged:
    """If the machine does not converge, FIXVAL never runs."""

    def test_no_fixval_findings_on_fail(self, tmp_path):
        """Machine with persistent CONFIRMED findings never converges,
        so _finalize_local_terminal is never called.

        Uses a custom AutoFixer that always returns NO_CHANGE so the
        CONFIRMED finding persists across rounds until ESCALATED.
        """
        def persistent_finding_l0(registry, files):
            return (
                [
                    StateFinding(
                        id="persistent",
                        fingerprint="persistent-fp",
                        source="L0",
                        disposition=Disposition.CONFIRMED,
                        file="test.py",
                        line_range=[1, 1],
                        description="persistent",
                    ),
                ],
                [],
            )

        class NoChangeAutoFixer:
            """AutoFixer that never fixes anything."""
            def fix(self, finding, mode_hint=None):
                return FixOutcome.NO_CHANGE

        machine = _make_machine(
            tmp_path,
            l0_runner=persistent_finding_l0,
        )
        machine.autofixer = NoChangeAutoFixer()
        # Low max to avoid long test
        machine.max_total_rounds = 4

        verdict = machine.run()

        # Machine did NOT converge (PENDING or ESCALATED, not PASS)
        assert verdict != Verdict.PASS
        # No FIXVAL findings at all -- _finalize_local_terminal never ran
        fixval_findings = [
            f for f in machine._state.findings
            if f.source == "FIXVAL"
        ]
        assert len(fixval_findings) == 0


class TestGetCommitMessage:
    """_get_commit_message reads COMMIT_EDITMSG or falls back."""

    def test_reads_commit_editmsg(self, tmp_path):
        machine = _make_machine(tmp_path)

        # Simulate git rev-parse returning a path, and that path existing
        commit_msg_path = tmp_path / "COMMIT_EDITMSG"
        commit_msg_path.write_text(
            "fix: something\n\nSigned-off-by: Test\n",
            encoding="utf-8",
        )

        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = str(commit_msg_path) + "\n"
            mock_run.return_value = mock_result

            result = machine._get_commit_message()

        assert "fix: something" in result

    def test_falls_back_to_git_log(self, tmp_path):
        machine = _make_machine(tmp_path)

        call_count = {"n": 0}

        def mock_run_side_effect(*args, **kwargs):
            call_count["n"] += 1
            result = MagicMock()
            if call_count["n"] == 1:
                # First call: rev-parse fails
                result.returncode = 1
                result.stdout = ""
            else:
                # Second call: git log succeeds
                result.returncode = 0
                result.stdout = "chore: fallback message\n"
            return result

        with patch("subprocess.run", side_effect=mock_run_side_effect):
            result = machine._get_commit_message()

        assert "chore: fallback message" in result

    def test_returns_empty_on_failure(self, tmp_path):
        machine = _make_machine(tmp_path)

        with patch("subprocess.run", side_effect=Exception("no git")):
            result = machine._get_commit_message()

        assert result == ""


class TestEvalFixvalHookRegistered:
    """FixvalAxisHook is registered in _AXIS_HOOKS at module load."""

    def test_eval_fixval_hook_registered(self):
        # Force fresh import from worktree
        import importlib
        import sys

        mod_name = "code_forge.eval.runner"
        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])

        from code_forge.eval.runner import _AXIS_HOOKS

        hook_types = [h.__class__.__name__ for h in _AXIS_HOOKS]
        assert "FixvalAxisHook" in hook_types


class TestEvalFixvalScoresBugP1201:
    """FixvalAxisHook post_review processes FIXVAL-tagged entries."""

    def test_eval_fixval_scores_bug_p12_01(self):
        from code_forge.eval.corpus import CorpusEntry
        from code_forge.eval.runner import FixvalAxisHook
        from code_forge.eval.scorer import EvalResult

        hook = FixvalAxisHook()

        entry = CorpusEntry(
            name="BUG-P12-01",
            diff_file="diffs/bug-p12-01.diff",
            expected_verdict="HOLD",
            axis_tags=["FIXVAL"],
        )

        # HOLD verdict -> caught (no exception = hook ran successfully)
        result_hold = EvalResult(
            entry=entry,
            actual_verdict="HOLD",
            runs=1,
            caught_count=1,
            skipped_reason="",
        )
        hook.post_review(entry, result_hold)

        # PASS verdict -> missed (no exception = hook ran successfully)
        result_pass = EvalResult(
            entry=entry,
            actual_verdict="PASS",
            runs=1,
            caught_count=0,
            skipped_reason="",
        )
        hook.post_review(entry, result_pass)

    def test_eval_fixval_ignores_non_fixval_entry(self):
        from code_forge.eval.corpus import CorpusEntry
        from code_forge.eval.runner import FixvalAxisHook
        from code_forge.eval.scorer import EvalResult

        hook = FixvalAxisHook()

        entry = CorpusEntry(
            name="SEC-01",
            diff_file="diffs/sec-01.diff",
            expected_verdict="HOLD",
            axis_tags=["SEC"],
        )

        result = EvalResult(
            entry=entry,
            actual_verdict="PASS",
            runs=1,
            caught_count=0,
            skipped_reason="",
        )
        # Should not raise or process (no FIXVAL tag)
        hook.post_review(entry, result)
