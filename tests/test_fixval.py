# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for FIXVAL core module (fix-validation gate).

TDD RED phase: tests written first, implementation follows.
Covers: classify_fixval_candidate, parse_fixval_waiver, run_fixval,
        FixvalResult findings, run_overfit_guard, end-to-end real git.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


from code_forge.disposition import Disposition
from code_forge.fixval import (
    FixvalCandidate,
    FixvalSkip,
    FixvalStatus,
    classify_fixval_candidate,
    parse_fixval_waiver,
    run_fixval,
    run_overfit_guard,
)
from code_forge.state import StateFinding


# ---- classify_fixval_candidate tests ----


class TestClassifyFixvalCandidate:
    """Structural trigger -- BOTH test and non-test files required."""

    def test_both_code_and_test_returns_candidate(self):
        result = classify_fixval_candidate(["src/foo.py", "tests/test_foo.py"])
        assert isinstance(result, FixvalCandidate)
        assert result.test_files == ["tests/test_foo.py"]
        assert result.non_test_files == ["src/foo.py"]

    def test_only_code_returns_skip(self):
        result = classify_fixval_candidate(["src/foo.py"])
        assert isinstance(result, FixvalSkip)
        assert "no test file" in result.reason.lower()

    def test_only_test_returns_skip(self):
        result = classify_fixval_candidate(["tests/test_foo.py"])
        assert isinstance(result, FixvalSkip)
        assert "no non-test file" in result.reason.lower()

    def test_empty_returns_skip(self):
        result = classify_fixval_candidate([])
        assert isinstance(result, FixvalSkip)

    def test_multi_lang_patterns(self):
        result = classify_fixval_candidate(["src/foo.py", "foo.test.ts", "bar_test.go"])
        assert isinstance(result, FixvalCandidate)
        assert sorted(result.test_files) == ["bar_test.go", "foo.test.ts"]
        assert result.non_test_files == ["src/foo.py"]

    def test_python_test_patterns(self):
        """All Python test patterns: tests/test_*.py, *_test.py, test_*.py"""
        result = classify_fixval_candidate(
            [
                "src/app.py",
                "tests/test_app.py",
                "utils_test.py",
                "test_helpers.py",
            ]
        )
        assert isinstance(result, FixvalCandidate)
        assert len(result.test_files) == 3
        assert result.non_test_files == ["src/app.py"]

    def test_ts_spec_pattern(self):
        """TypeScript .spec.ts recognized as test."""
        result = classify_fixval_candidate(["src/app.ts", "src/app.spec.ts"])
        assert isinstance(result, FixvalCandidate)
        assert result.test_files == ["src/app.spec.ts"]

    def test_projection_filters_tests_but_keeps_deleted_production_facts(self):
        result = classify_fixval_candidate(
            ["src/removed.py", "tests/test_removed.py", "tests/test_live.py"],
            executable_files=["tests/test_live.py"],
        )
        assert result == FixvalCandidate(["tests/test_live.py"], ["src/removed.py"])

    def test_no_executable_test_has_explicit_skip(self):
        result = classify_fixval_candidate(
            ["src/live.py", "tests/test_removed.py"],
            executable_files=["src/live.py"],
        )
        assert isinstance(result, FixvalSkip)
        assert result.reason == "no executable test file in diff"

    def test_projection_preserves_test_only_skip(self):
        result = classify_fixval_candidate(
            ["tests/test_live.py"],
            executable_files=["tests/test_live.py"],
        )
        assert isinstance(result, FixvalSkip)
        assert result.reason == "no non-test file in diff"


# ---- parse_fixval_waiver tests ----


class TestParseFixvalWaiver:
    """Dual-channel waiver (env var + trailer)."""

    def test_waiver_from_trailer(self):
        msg = "fix: foo\n\nFixval-Waiver: flaky network test"
        assert parse_fixval_waiver(msg) == "flaky network test"

    def test_waiver_from_env_var(self):
        env = {"FIXVAL_WAIVER": "flaky"}
        assert parse_fixval_waiver("fix: foo", env=env) == "flaky"

    def test_waiver_env_takes_precedence(self):
        env = {"FIXVAL_WAIVER": "env reason"}
        msg = "fix: foo\n\nFixval-Waiver: trailer reason"
        assert parse_fixval_waiver(msg, env=env) == "env reason"

    def test_waiver_absent(self):
        assert parse_fixval_waiver("fix: foo") is None

    def test_waiver_absent_with_empty_env(self):
        assert parse_fixval_waiver("fix: foo", env={}) is None

    def test_waiver_case_insensitive(self):
        msg = "fix: foo\n\nfixval-waiver: reason"
        assert parse_fixval_waiver(msg) == "reason"

    def test_waiver_empty_reason_trailer(self):
        msg = "fix: foo\n\nFixval-Waiver: "
        assert parse_fixval_waiver(msg) is None

    def test_waiver_empty_reason_env(self):
        env = {"FIXVAL_WAIVER": "  "}
        assert parse_fixval_waiver("fix: foo", env=env) is None

    def test_waiver_whitespace_only_trailer(self):
        msg = "fix: foo\n\nFixval-Waiver:   \t  "
        assert parse_fixval_waiver(msg) is None


# ---- run_fixval tests ----


def _make_candidate():
    return FixvalCandidate(
        test_files=["tests/test_foo.py"],
        non_test_files=["src/foo.py"],
    )


def _private_reverse_results(results):
    """Model Git's private output as well as its exit status in orchestration tests."""
    from pathlib import Path

    values = iter(results)

    def run(argv, **kwargs):
        result = next(values)
        if argv[:3] == ["git", "apply", "-R"] and result.returncode == 0:
            image = Path(kwargs["cwd"]).resolve()
            assert image.name.startswith(".fixval-image-")
            source = image / "src/foo.py"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"old\n")
        return result

    return run


class TestRunFixval:
    """run_fixval: revert-RED/restore-GREEN core logic."""

    def test_diff_text_none_skips(self, tmp_path):
        candidate = _make_candidate()
        result = run_fixval(
            candidate,
            test_cmd=["python", "-m", "pytest"],
            cwd=tmp_path,
            commit_message="fix: foo",
            diff_text=None,
        )
        assert result.status == FixvalStatus.SKIPPED
        assert "non-git review" in result.findings[0].description.lower()

    @patch("code_forge.fixval.parse_fixval_waiver")
    def test_waiver_bypasses_with_advisory(self, mock_waiver, tmp_path):
        mock_waiver.return_value = "flaky network test"
        candidate = _make_candidate()
        result = run_fixval(
            candidate,
            test_cmd=["python", "-m", "pytest"],
            cwd=tmp_path,
            commit_message="fix: foo\n\nFixval-Waiver: flaky network test",
            diff_text="some diff",
        )
        assert result.status == FixvalStatus.WAIVED
        assert len(result.advisories) >= 1
        assert result.advisories[0].axis == "FIXVAL"

    @patch("code_forge.fixval._run_baseline_guard")
    def test_baseline_failure_skips(self, mock_guard, tmp_path):
        mock_guard.return_value = (
            "skip",
            [
                StateFinding(
                    id="FIXVAL_SKIPPED",
                    fingerprint="fixval-baseline-fail",
                    source="FIXVAL",
                    disposition=Disposition.DISMISSED,
                    file="",
                    line_range=[],
                    description="baseline failed",
                )
            ],
            ["baseline failed"],
        )
        candidate = _make_candidate()
        result = run_fixval(
            candidate,
            test_cmd=["python", "-m", "pytest"],
            cwd=tmp_path,
            commit_message="fix: foo",
            diff_text="--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1 +1 @@\n-old\n+new\n",
        )
        assert result.status == FixvalStatus.SKIPPED

    @patch("code_forge.fixval._run_baseline_guard")
    @patch("subprocess.run")
    def test_revert_apply_failure_blocks(self, mock_run, mock_guard, tmp_path):
        mock_guard.return_value = ("passed", [], [])
        # git apply -R fails
        mock_run.return_value = MagicMock(returncode=1, stderr="error")
        candidate = _make_candidate()
        diff = "--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
        result = run_fixval(
            candidate,
            test_cmd=["python", "-m", "pytest"],
            cwd=tmp_path,
            commit_message="fix: foo",
            diff_text=diff,
        )
        assert result.status == FixvalStatus.BLOCK
        assert "revert" in result.findings[0].description.lower()

    @patch("code_forge.fixval._run_baseline_guard")
    @patch("subprocess.run")
    def test_test_fails_on_revert_passes(self, mock_run, mock_guard, tmp_path):
        mock_guard.return_value = ("passed", [], [])
        # First call: git apply -R (revert) succeeds
        # Second call: test run -> fails (RED) = PASS
        # Third call: git apply (restore) succeeds
        mock_run.side_effect = _private_reverse_results(
            [
                MagicMock(returncode=0),  # git apply -R
                MagicMock(returncode=1),  # test fails (RED) -> FIXVAL PASS
                MagicMock(returncode=0),  # git apply (restore)
            ]
        )
        candidate = _make_candidate()
        diff = "--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
        result = run_fixval(
            candidate,
            test_cmd=["python", "-m", "pytest"],
            cwd=tmp_path,
            commit_message="fix: foo",
            diff_text=diff,
        )
        assert result.status == FixvalStatus.PASS

    @patch("code_forge.fixval._run_baseline_guard")
    @patch("subprocess.run")
    def test_test_passes_on_revert_blocks(self, mock_run, mock_guard, tmp_path):
        mock_guard.return_value = ("passed", [], [])
        mock_run.side_effect = _private_reverse_results(
            [
                MagicMock(returncode=0),  # git apply -R (revert)
                MagicMock(returncode=0),  # test passes (GREEN) -> FIXVAL BLOCK
                MagicMock(returncode=0),  # git apply (restore)
            ]
        )
        candidate = _make_candidate()
        diff = "--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
        result = run_fixval(
            candidate,
            test_cmd=["python", "-m", "pytest"],
            cwd=tmp_path,
            commit_message="fix: foo",
            diff_text=diff,
        )
        assert result.status == FixvalStatus.BLOCK
        assert result.block_message  # non-empty

    @patch("code_forge.fixval._run_baseline_guard")
    @patch("subprocess.run")
    def test_restore_validates_apply_without_checkout(self, mock_run, mock_guard, tmp_path):
        mock_guard.return_value = ("passed", [], [])
        mock_run.side_effect = _private_reverse_results(
            [
                MagicMock(returncode=0),  # git apply -R
                MagicMock(returncode=1),  # test fails -> PASS
                MagicMock(returncode=0),  # git apply (forward restore)
            ]
        )
        candidate = _make_candidate()
        diff = "--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
        run_fixval(
            candidate,
            test_cmd=["python", "-m", "pytest"],
            cwd=tmp_path,
            commit_message="fix: foo",
            diff_text=diff,
        )
        # Verify Git validates forward applicability without publishing source.
        restore_call = mock_run.call_args_list[-1]
        cmd = restore_call[0][0] if restore_call[0] else restore_call[1].get("args", [])
        assert "git" in cmd[0] if isinstance(cmd, list) else True
        assert "--check" in cmd
        # Must NOT contain "checkout"
        for call in mock_run.call_args_list:
            args = call[0][0] if call[0] else call[1].get("args", [])
            if isinstance(args, list):
                assert "checkout" not in args

    @patch("code_forge.fixval._logger")
    @patch("code_forge.fixval._run_baseline_guard")
    @patch("subprocess.run")
    def test_restore_failure_logs_error(self, mock_run, mock_guard, mock_logger, tmp_path):
        """If restore patch fails, _logger.error is called with details."""
        mock_guard.return_value = ("passed", [], [])
        mock_run.side_effect = _private_reverse_results(
            [
                MagicMock(returncode=0),  # git apply -R
                MagicMock(returncode=0),  # test passes -> BLOCK
                MagicMock(returncode=1, stderr="conflict"),  # restore FAILS
            ]
        )
        candidate = _make_candidate()
        diff = "--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
        result = run_fixval(
            candidate,
            test_cmd=["python", "-m", "pytest"],
            cwd=tmp_path,
            commit_message="fix: foo",
            diff_text=diff,
        )
        assert result.status == FixvalStatus.BLOCK
        assert mock_logger.error.called

    @patch("code_forge.fixval._run_baseline_guard")
    @patch("subprocess.run")
    def test_scoped_test_cmd(self, mock_run, mock_guard, tmp_path):
        mock_guard.return_value = ("passed", [], [])
        mock_run.side_effect = _private_reverse_results(
            [
                MagicMock(returncode=0),  # git apply -R
                MagicMock(returncode=1),  # test (scoped)
                MagicMock(returncode=0),  # git apply restore
            ]
        )
        candidate = FixvalCandidate(
            test_files=["tests/test_a.py", "tests/test_b.py"],
            non_test_files=["src/foo.py"],
        )
        diff = "--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
        run_fixval(
            candidate,
            test_cmd=["python", "-m", "pytest"],
            cwd=tmp_path,
            commit_message="fix: foo",
            diff_text=diff,
        )
        # The test run call (second subprocess.run call) should include
        # test files appended to test_cmd
        test_call = mock_run.call_args_list[1]
        args = test_call[0][0] if test_call[0] else test_call[1].get("args", [])
        assert "tests/test_a.py" in args
        assert "tests/test_b.py" in args

    @patch("code_forge.fixval._run_baseline_guard")
    def test_baseline_needs_strip_retry(self, mock_guard, tmp_path):
        # First call returns needs_strip_retry, second returns passed,
        # then test passes on revert -> BLOCK
        mock_guard.side_effect = [
            ("needs_strip_retry", [], []),
            ("passed", [], []),
        ]
        candidate = _make_candidate()
        diff = "--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = _private_reverse_results(
                [
                    MagicMock(returncode=0),  # git apply -R
                    MagicMock(returncode=0),  # test passes -> BLOCK
                    MagicMock(returncode=0),  # git apply restore
                ]
            )
            result = run_fixval(
                candidate,
                test_cmd=["python", "-m", "pytest"],
                cwd=tmp_path,
                commit_message="fix: foo",
                diff_text=diff,
            )
        assert result.status == FixvalStatus.BLOCK
        assert mock_guard.call_count == 2

    @patch("code_forge.fixval._run_baseline_guard")
    @patch("subprocess.run")
    def test_revert_from_diff_text(self, mock_run, mock_guard, tmp_path):
        """Verify revert patch is derived from diff_text via unidiff,
        not from a separate git command."""
        mock_guard.return_value = ("passed", [], [])
        mock_run.side_effect = _private_reverse_results(
            [
                MagicMock(returncode=0),  # git apply -R
                MagicMock(returncode=1),  # test fails -> PASS
                MagicMock(returncode=0),  # git apply restore
            ]
        )
        candidate = _make_candidate()
        diff = (
            "--- a/src/foo.py\n+++ b/src/foo.py\n"
            "@@ -1 +1 @@\n-old\n+new\n"
            "--- a/tests/test_foo.py\n+++ b/tests/test_foo.py\n"
            "@@ -1 +1 @@\n-old_test\n+new_test\n"
        )
        run_fixval(
            candidate,
            test_cmd=["python", "-m", "pytest"],
            cwd=tmp_path,
            commit_message="fix: foo",
            diff_text=diff,
        )
        # The revert (git apply -R) should be called with a temp file
        # containing only the non-test diff (src/foo.py), not test_foo.py
        revert_call = mock_run.call_args_list[0]
        args = revert_call[0][0] if revert_call[0] else revert_call[1].get("args", [])
        assert args[0] == "git"
        assert args[1] == "apply"
        assert "-R" in args


# ---- FixvalResult findings tests ----


class TestFixvalResultFindings:
    """Verify correct StateFinding for each status."""

    def test_block_produces_dismissed(self, tmp_path):
        """BLOCK -> DISMISSED StateFinding (block via Verdict.FAIL,
        not CONFIRMED -- CONFIRMED blocks reconvergence)."""
        with (
            patch("code_forge.fixval._run_baseline_guard") as mock_guard,
            patch("subprocess.run") as mock_run,
        ):
            mock_guard.return_value = ("passed", [], [])
            mock_run.side_effect = _private_reverse_results(
                [
                    MagicMock(returncode=0),
                    MagicMock(returncode=0),  # test passes -> BLOCK
                    MagicMock(returncode=0),
                ]
            )
            candidate = _make_candidate()
            diff = "--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
            result = run_fixval(
                candidate,
                test_cmd=["python", "-m", "pytest"],
                cwd=tmp_path,
                commit_message="fix: foo",
                diff_text=diff,
            )
        assert result.status == FixvalStatus.BLOCK
        assert len(result.findings) == 1
        f = result.findings[0]
        assert f.disposition == Disposition.DISMISSED
        assert f.source == "FIXVAL"
        assert f.id == "FIXVAL_HOLLOW"
        assert f.fingerprint == "fixval-hollow"

    def test_pass_produces_empty(self, tmp_path):
        with (
            patch("code_forge.fixval._run_baseline_guard") as mock_guard,
            patch("subprocess.run") as mock_run,
        ):
            mock_guard.return_value = ("passed", [], [])
            mock_run.side_effect = _private_reverse_results(
                [
                    MagicMock(returncode=0),
                    MagicMock(returncode=1),  # test fails -> PASS
                    MagicMock(returncode=0),
                ]
            )
            candidate = _make_candidate()
            diff = "--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
            result = run_fixval(
                candidate,
                test_cmd=["python", "-m", "pytest"],
                cwd=tmp_path,
                commit_message="fix: foo",
                diff_text=diff,
            )
        assert result.status == FixvalStatus.PASS
        assert result.findings == []

    def test_skipped_produces_dismissed(self, tmp_path):
        candidate = _make_candidate()
        result = run_fixval(
            candidate,
            test_cmd=["python", "-m", "pytest"],
            cwd=tmp_path,
            commit_message="fix: foo",
            diff_text=None,
        )
        assert result.status == FixvalStatus.SKIPPED
        assert len(result.findings) == 1
        f = result.findings[0]
        assert f.disposition == Disposition.DISMISSED
        assert f.source == "FIXVAL"
        assert f.id == "FIXVAL_SKIPPED"

    @patch("code_forge.fixval.parse_fixval_waiver")
    def test_waived_produces_dismissed_plus_advisory(self, mock_waiver, tmp_path):
        mock_waiver.return_value = "flaky"
        candidate = _make_candidate()
        result = run_fixval(
            candidate,
            test_cmd=["python", "-m", "pytest"],
            cwd=tmp_path,
            commit_message="fix: foo",
            diff_text="some diff",
        )
        assert result.status == FixvalStatus.WAIVED
        assert len(result.findings) == 1
        f = result.findings[0]
        assert f.disposition == Disposition.DISMISSED
        assert f.source == "FIXVAL"
        assert len(result.advisories) >= 1


# ---- Integration test (real git) ----


class TestEndToEndRealGit:
    """Real git operations, no mocking."""

    def test_end_to_end_real_git(self, tmp_path):
        """Create a tmp_path git repo, add code+test, run run_fixval
        with real git apply -R / git apply (forward restore)."""
        # Init git repo
        subprocess.run(
            ["git", "init"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

        # Create initial code file
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        code_file = src_dir / "calc.py"
        code_file.write_text("def add(a, b):\n    return 0\n")

        # Create test file
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        test_file = tests_dir / "test_calc.py"
        test_file.write_text("from src.calc import add\ndef test_add():\n    assert add(1, 2) == 0\n")

        # Initial commit
        subprocess.run(
            ["git", "add", "."],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

        # Fix the code
        code_file.write_text("def add(a, b):\n    return a + b\n")
        # Fix the test
        test_file.write_text("from src.calc import add\ndef test_add():\n    assert add(1, 2) == 3\n")

        # Generate diff
        diff_result = subprocess.run(
            ["git", "diff"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        diff_text = diff_result.stdout

        candidate = FixvalCandidate(
            test_files=["tests/test_calc.py"],
            non_test_files=["src/calc.py"],
        )

        # run_fixval with real git. The test should go RED on revert
        # because the reverted code returns 0, but the test expects 3.
        with patch("code_forge.fixval._run_baseline_guard") as mock_guard:
            mock_guard.return_value = ("passed", [], [])
            result = run_fixval(
                candidate,
                test_cmd=[
                    "python3",
                    "-c",
                    "import sys; sys.path.insert(0, 'src'); "
                    "sys.path.insert(0, '.'); "
                    "from src.calc import add; "
                    "assert add(1, 2) == 3, 'expected 3'",
                ],
                cwd=tmp_path,
                commit_message="fix: correct add function",
                diff_text=diff_text,
            )

        assert result.status == FixvalStatus.PASS
        # Verify code was restored
        assert "return a + b" in code_file.read_text()


# ---- _VariableRenamer tests ----


class TestVariableRenamer:
    """_VariableRenamer must produce valid code (target + uses both renamed)."""

    def test_renames_target_and_uses_consistently(self):
        import ast as _ast
        from code_forge.fixval import _VariableRenamer

        code = "def f():\n    x = 1\n    return x\n"
        tree = _ast.parse(code)
        renamer = _VariableRenamer()
        transformed = renamer.visit(tree)
        _ast.fix_missing_locations(transformed)
        out = _ast.unparse(transformed)
        assert "x_renamed = 1" in out
        assert "return x_renamed" in out

    def test_renamed_code_executes_without_name_error(self):
        import ast as _ast
        from code_forge.fixval import _VariableRenamer

        code = "def compute():\n    result = 42\n    return result\n"
        tree = _ast.parse(code)
        renamer = _VariableRenamer()
        transformed = renamer.visit(tree)
        _ast.fix_missing_locations(transformed)
        out = _ast.unparse(transformed)
        ns: dict = {}
        exec(compile(out, "<string>", "exec"), ns)
        assert ns["compute"]() == 42


# ---- run_overfit_guard tests ----


class TestRunOverfitGuard:
    """Overfit guard is ADVISORY, never blocking."""

    def test_rename_breaks_test_emits_advisory(self, tmp_path):
        # Create a .py file with a local variable
        src_file = tmp_path / "src" / "module.py"
        src_file.parent.mkdir(parents=True, exist_ok=True)
        src_file.write_text("def compute():\n    result = 42\n    return result\n")
        candidate = FixvalCandidate(
            test_files=["tests/test_module.py"],
            non_test_files=[str(src_file)],
        )
        # Mock subprocess: test fails after rename -> overfitting
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            advisories = run_overfit_guard(
                candidate,
                test_cmd=["python", "-m", "pytest"],
                cwd=tmp_path,
            )
        assert len(advisories) == 1
        assert advisories[0].axis == "FIXVAL"
        assert "overfit" in advisories[0].description.lower()

    def test_rename_keeps_test_passing_no_advisory(self, tmp_path):
        src_file = tmp_path / "src" / "module.py"
        src_file.parent.mkdir(parents=True, exist_ok=True)
        src_file.write_text("def compute():\n    result = 42\n    return result\n")
        candidate = FixvalCandidate(
            test_files=["tests/test_module.py"],
            non_test_files=[str(src_file)],
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            advisories = run_overfit_guard(
                candidate,
                test_cmd=["python", "-m", "pytest"],
                cwd=tmp_path,
            )
        assert advisories == []

    def test_overfit_restores_original_bytes(self, tmp_path):
        """File content must be byte-identical after overfit guard,
        preserving comments, formatting, and whitespace."""
        src_file = tmp_path / "src" / "module.py"
        src_file.parent.mkdir(parents=True, exist_ok=True)
        original = (
            "# Important comment\n"
            "def compute():  # inline comment\n"
            "    result = 42  # magic number\n"
            "    return result\n"
        )
        src_file.write_text(original)
        candidate = FixvalCandidate(
            test_files=["tests/test_module.py"],
            non_test_files=[str(src_file)],
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            run_overfit_guard(
                candidate,
                test_cmd=["python", "-m", "pytest"],
                cwd=tmp_path,
            )
        assert src_file.read_text() == original

    def test_non_python_files_skipped(self, tmp_path):
        """Non-.py files produce empty advisory list."""
        candidate = FixvalCandidate(
            test_files=["tests/test_foo.ts"],
            non_test_files=["src/foo.ts"],
        )
        advisories = run_overfit_guard(
            candidate,
            test_cmd=["npx", "jest"],
            cwd=tmp_path,
        )
        assert advisories == []


# Preservation is tested with actual filesystem objects and injected process
# boundaries. No mutation backend or provider is involved.


def _recovery_paths(root, pattern):
    import hashlib
    import os

    namespace = hashlib.sha256(os.fsencode(root.absolute())).hexdigest()[:12]
    pattern = pattern.replace(".fixval-recovery-", ".fixval-recovery-%s-" % namespace, 1)
    pattern = pattern.replace(".fixval-retired-", ".fixval-retired-%s-" % namespace, 1)
    return root.parent.glob(pattern)


def _preservation_fixture(tmp_path, retained=False):
    from code_forge._fixval_transaction import FixvalTransaction

    source = tmp_path / "src"
    source.mkdir()
    live = source / "model.py"
    live.write_bytes(b"new\n")
    live.chmod(0o751)
    patch_text = "--- a/src/model.py\n+++ b/src/model.py\n@@ -1 +1 @@\n-old\n+new\n"
    if retained:
        gone = source / "gone.py"
        gone.write_bytes(b"RETAINED\n")
        if retained == "symlink":
            gone.unlink()
            gone.symlink_to("model.py")
        else:
            gone.chmod(0o713)
        patch_text = "--- a/src/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-tracked\n" + patch_text
    return FixvalTransaction(tmp_path, patch_text), live


def test_transaction_close_before_prepare_releases_descriptors(tmp_path):
    import os

    transaction, live = _preservation_fixture(tmp_path)
    descriptors = list(transaction.parents.values())
    transaction.close()
    for fd in descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)
    assert live.read_bytes() == b"new\n"


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_transaction_refuses_unsupported_platform_before_mutation(tmp_path, monkeypatch, platform):
    import sys

    from code_forge._fixval_transaction import FixvalTransaction, TransactionError

    source = tmp_path / "file.py"
    source.write_bytes(b"new\n")
    monkeypatch.setattr(sys, "platform", platform)
    with pytest.raises(TransactionError, match="Linux"):
        FixvalTransaction(tmp_path, "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new\n")
    assert source.read_bytes() == b"new\n"
    assert not list(_recovery_paths(tmp_path, ".fixval-recovery-*"))


@pytest.mark.parametrize("capability", ["renameat2", "dir_fd"])
def test_transaction_refuses_missing_capabilities_before_mutation(tmp_path, monkeypatch, capability):
    import ctypes
    import os

    from code_forge._fixval_transaction import FixvalTransaction, TransactionError

    if capability == "renameat2":
        monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: object())
    else:
        monkeypatch.setattr(os, "supports_dir_fd", set())
    with pytest.raises(TransactionError, match="renameat2|safely bind"):
        FixvalTransaction(tmp_path, "")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("enforcement", ["unavailable", "ignored"])
def test_transaction_kernel_preflight_changes_no_source(tmp_path, monkeypatch, enforcement):
    import errno

    from code_forge._fixval_transaction import TransactionError

    transaction, live = _preservation_fixture(tmp_path, retained=True)
    original = {
        p.name: (p.read_bytes(), p.stat().st_ino, p.stat().st_mode) for p in live.parent.iterdir()
    }

    def unavailable(*args):
        if enforcement == "unavailable":
            raise OSError(errno.ENOSYS, "injected missing syscall")

    monkeypatch.setattr(transaction, "_rename_between", unavailable)
    with pytest.raises(TransactionError, match="kernel"):
        transaction.prepare()
    assert transaction.restore(False) == []
    transaction.recovery_needed = True
    transaction.close()
    assert {
        p.name: (p.read_bytes(), p.stat().st_ino, p.stat().st_mode) for p in live.parent.iterdir()
    } == original
    assert (transaction.directory / ".rename-probe").exists()


@pytest.mark.parametrize("name", ["../outside.py", ".git/config", "/outside.py"])
def test_transaction_rejects_unsafe_patch_before_allocating(tmp_path, name):
    from code_forge._fixval_transaction import FixvalTransaction, TransactionError

    patch_text = "--- a/%s\n+++ b/%s\n@@ -1 +1 @@\n-old\n+new\n" % (name, name)
    with pytest.raises(TransactionError, match="unsafe"):
        FixvalTransaction(tmp_path, patch_text)
    assert not list(tmp_path.iterdir())


def test_transaction_constructor_closes_on_unsupported_node(tmp_path):
    import os

    from code_forge._fixval_transaction import FixvalTransaction, TransactionError

    (tmp_path / "folder").mkdir()
    descriptors_before = set(os.listdir("/proc/self/fd"))
    with pytest.raises(TransactionError, match="neither a regular"):
        FixvalTransaction(tmp_path, "--- a/folder\n+++ b/folder\n@@ -1 +1 @@\n-old\n+new\n")
    assert set(os.listdir("/proc/self/fd")) == descriptors_before
    assert list(tmp_path.iterdir()) == [tmp_path / "folder"]


@pytest.mark.parametrize("race", ["opening", "reading"])
def test_transaction_identity_rejects_changed_entry(tmp_path, monkeypatch, race):
    import hashlib
    import os

    from code_forge._fixval_transaction import TransactionError, _identity

    source = tmp_path / "file"
    source.write_bytes(b"ORIGINAL\n")
    other = tmp_path / "foreign"
    other.write_bytes(b"FOREIGN\n")
    parent = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        if race == "opening":
            real_open = os.open
            monkeypatch.setattr(os, "open", lambda name, flags, **kwargs: real_open(other, flags))
        else:
            real_digest = hashlib.file_digest

            def changing_digest(*args):
                result = real_digest(*args)
                source.write_bytes(b"CHANGED_BY_WRITER\n")
                return result

            monkeypatch.setattr(hashlib, "file_digest", changing_digest)
        with pytest.raises(TransactionError, match="changed while"):
            _identity(parent, "file")
        assert other.read_bytes() == b"FOREIGN\n"
    finally:
        os.close(parent)


@pytest.mark.parametrize("race", ["existing_backup", "new_backup", "source_swap", "source_reoccupied"])
def test_transaction_preservation_never_overwrites_backup_or_replacement(tmp_path, monkeypatch, race):
    from code_forge._fixval_transaction import TransactionError

    transaction, live = _preservation_fixture(tmp_path, retained=True)
    gone = live.parent / "gone.py"
    retained_inode = gone.stat().st_ino
    renamed = None
    foreign = None
    original_rename = transaction._rename_between

    def racing_rename(source_fd, source, target_fd, target):
        nonlocal renamed, foreign
        if source == "gone.py":
            if race == "new_backup":
                path = transaction.directory / target
                path.write_bytes(b"FOREIGN_BACKUP\n")
                foreign = path, path.stat().st_ino
            elif race in {"source_swap", "source_reoccupied"}:
                renamed = gone.with_name("original-retained")
                gone.rename(renamed)
                gone.write_bytes(b"FOREIGN_REPLACEMENT\n")
                foreign = gone.stat().st_ino
        original_rename(source_fd, source, target_fd, target)
        if source == "gone.py" and race == "source_reoccupied":
            gone.write_bytes(b"THIRD_FOREIGN_ENTRY\n")
            foreign = (transaction.directory / target).stat().st_ino, gone.stat().st_ino

    monkeypatch.setattr(transaction, "_rename_between", racing_rename)
    if race == "existing_backup":
        original_check = transaction._check_no_replace

        def occupied_backup():
            nonlocal foreign
            original_check()
            path = transaction.directory / "0"
            path.write_bytes(b"FOREIGN_BACKUP\n")
            foreign = path, path.stat().st_ino

        monkeypatch.setattr(transaction, "_check_no_replace", occupied_backup)
    with pytest.raises((OSError, TransactionError)):
        transaction.prepare()
    errors = transaction.restore(False)
    transaction.recovery_needed = True
    transaction.close()
    assert live.read_bytes() == b"new\n"
    if race in {"existing_backup", "new_backup"}:
        assert gone.stat().st_ino == retained_inode
        assert gone.read_bytes() == b"RETAINED\n"
        assert foreign[0].stat().st_ino == foreign[1]
        assert foreign[0].read_bytes() == b"FOREIGN_BACKUP\n"
    else:
        assert renamed.stat().st_ino == retained_inode
        assert renamed.read_bytes() == b"RETAINED\n"
        assert errors
        if race == "source_swap":
            assert gone.stat().st_ino == foreign
            assert gone.read_bytes() == b"FOREIGN_REPLACEMENT\n"
        else:
            assert (transaction.directory / "0").stat().st_ino == foreign[0]
            assert (transaction.directory / "0").read_bytes() == b"FOREIGN_REPLACEMENT\n"
            assert gone.stat().st_ino == foreign[1]
            assert gone.read_bytes() == b"THIRD_FOREIGN_ENTRY\n"


@pytest.mark.parametrize("failure", ["copy_interrupted", "snapshot_changed", "retained_changed"])
def test_transaction_snapshot_failures_preserve_foreign_source(tmp_path, monkeypatch, failure):
    import shutil

    from code_forge._fixval_transaction import TransactionError

    transaction, live = _preservation_fixture(tmp_path, retained=True)
    real_copy = shutil.copyfileobj
    changed = live if failure != "retained_changed" else live.with_name("gone.py")

    def changing_copy(*args):
        real_copy(*args)
        changed.write_bytes(b"FOREIGN_WRITER\n")
        if failure == "copy_interrupted":
            raise OSError("injected snapshot error")

    monkeypatch.setattr(shutil, "copyfileobj", changing_copy)
    with pytest.raises((OSError, TransactionError)):
        transaction.prepare()
    identity = changed.stat().st_ino, changed.read_bytes(), changed.stat().st_mode
    assert transaction.restore(False)
    transaction.close()
    assert (changed.stat().st_ino, changed.read_bytes(), changed.stat().st_mode) == identity


def test_transaction_saved_payload_replacement_prevents_source_removal(tmp_path):
    transaction, live = _preservation_fixture(tmp_path)
    transaction.prepare()
    assert transaction.reverse().returncode == 0
    saved = transaction.directory / "0"
    saved.rename(transaction.directory / "saved-original")
    saved.write_bytes(b"FOREIGN_BACKUP\n")
    transaction.mark_reverted()
    inode = live.stat().st_ino
    errors = transaction.restore(False)
    transaction.close()
    assert any("saved entry changed" in error for error in errors)
    assert live.stat().st_ino == inode
    assert live.read_bytes() == b"old\n"
    assert saved.read_bytes() == b"FOREIGN_BACKUP\n"
    assert (transaction.directory / "saved-original").read_bytes() == b"new\n"


def test_transaction_mode_restore_rejects_foreign_open_descriptor(tmp_path, monkeypatch):
    import os

    transaction, live = _preservation_fixture(tmp_path)
    transaction.prepare()
    assert transaction.reverse().returncode == 0
    transaction.mark_reverted()

    other = tmp_path / "foreign"
    other.write_bytes(b"FOREIGN\n")
    other.chmod(0o711)
    real_open = os.open
    count = 0

    def swapped_open(name, flags, **kwargs):
        nonlocal count
        if name == "0":
            count += 1
            if count == 2:
                return real_open(other, flags)
        return real_open(name, flags, **kwargs)

    monkeypatch.setattr(os, "open", swapped_open)
    assert any("source entry changed while opening" in error for error in transaction.restore(False))
    transaction.close()
    assert other.read_bytes() == b"FOREIGN\n"
    assert other.stat().st_mode & 0o777 == 0o711


@pytest.mark.parametrize("retained", [False, True])
def test_transaction_detects_writer_after_restore_link(tmp_path, monkeypatch, retained):
    import os

    transaction, live = _preservation_fixture(tmp_path, retained=retained)
    transaction.prepare()
    assert transaction.reverse().returncode == 0
    transaction.mark_reverted()
    real_link = os.link

    def changed_after_link(source, target, **kwargs):
        real_link(source, target, **kwargs)
        if (target == "gone.py") if retained else (target == "model.py"):
            path = live.with_name(target)
            if retained:
                path.chmod(0o700)
            else:
                path.write_bytes(b"FOREIGN_AFTER_LINK\n")

    monkeypatch.setattr(os, "link", changed_after_link)
    errors = transaction.restore(False)
    transaction.close()
    assert any("was not restored" in error for error in errors)
    if retained:
        assert live.with_name("gone.py").stat().st_mode & 0o777 == 0o700
    else:
        assert live.read_bytes() == b"FOREIGN_AFTER_LINK\n"


def test_transaction_cleanup_does_not_delete_changed_backup(tmp_path):
    from code_forge._fixval_transaction import TransactionError

    transaction, live = _preservation_fixture(tmp_path)
    transaction.prepare()
    assert transaction.restore(False) == []
    saved = transaction.directory / "0"
    saved.write_bytes(b"FOREIGN_BACKUP_CONTENT\n")
    with pytest.raises(TransactionError, match="changed before cleanup"):
        transaction.close()
    assert saved.read_bytes() == b"FOREIGN_BACKUP_CONTENT\n"
    assert live.read_bytes() == b"new\n"


def test_transaction_recovery_locator_survives_proc_restriction(tmp_path, monkeypatch):
    import os

    transaction, live = _preservation_fixture(tmp_path, retained="symlink")
    transaction.prepare()
    original_readlink = os.readlink

    def restricted_readlink(path, *args, **kwargs):
        if str(path).startswith("/proc/self/fd/"):
            raise OSError("injected proc restriction")
        return original_readlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "readlink", restricted_readlink)
    assert transaction.restore(False) == []
    transaction.recovery_needed = True
    transaction.close()
    assert "recovery inode" in transaction.recovery_location
    assert transaction.directory.exists()
    assert live.read_bytes() == b"new\n"


def test_transaction_removed_original_parent_keeps_recovery_payload(tmp_path):
    transaction, live = _preservation_fixture(tmp_path)
    transaction.prepare()
    live.unlink()
    live.parent.rmdir()
    assert any("parent with an original source" in error for error in transaction.restore(False))
    transaction.close()
    assert (transaction.directory / "0").read_bytes() == b"new\n"


def test_transaction_kernel_probe_replacement_is_preserved(tmp_path, monkeypatch):
    import errno

    from code_forge._fixval_transaction import TransactionError

    transaction, live = _preservation_fixture(tmp_path)

    def replace_probe(*args):
        probe = transaction.directory / ".rename-probe"
        probe.rename(transaction.directory / "original-probe")
        probe.write_bytes(b"FOREIGN_PROBE\n")
        raise OSError(errno.EEXIST, "injected destination collision")

    monkeypatch.setattr(transaction, "_rename_between", replace_probe)
    with pytest.raises(TransactionError, match="probe was replaced"):
        transaction.prepare()
    assert transaction.restore(False) == []
    transaction.recovery_needed = True
    transaction.close()
    assert (transaction.directory / ".rename-probe").read_bytes() == b"FOREIGN_PROBE\n"
    assert live.read_bytes() == b"new\n"


@pytest.mark.parametrize("failure", ["invalid_patch", "patch_allocation", "nul_path", "test_only"])
def test_fixval_preflight_errors_do_not_start_git(tmp_path, monkeypatch, failure):
    import tempfile

    transaction, live = _preservation_fixture(tmp_path)
    transaction.close()
    monkeypatch.setattr("code_forge.fixval._run_baseline_guard", lambda *a, **k: ("passed", [], []))
    monkeypatch.delenv("FIXVAL_WAIVER", raising=False)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("preflight must not invoke Git"))
    patch_text = "--- a/src/model.py\n+++ b/src/model.py\n@@ -1 +1 @@\n-old\n+new\n"
    if failure == "invalid_patch":
        patch_text = patch_text.replace("@@ -1 +1 @@", "@@ -1,3 +1 @@")
    elif failure == "patch_allocation":

        def allocation_error(*args, **kwargs):
            raise OSError("injected patch allocation failure")

        monkeypatch.setattr(tempfile, "mkstemp", allocation_error)
    elif failure == "nul_path":
        patch_text = patch_text.replace("model.py", "model.py\x00bad")
    else:
        patch_text = patch_text.replace("src/model.py", "tests/test_model.py")
    result = run_fixval(
        FixvalCandidate(["tests/test_live.py"], ["src/model.py"]),
        ["python", "-m", "pytest"],
        tmp_path,
        "fix",
        patch_text,
    )
    assert result.status == (FixvalStatus.SKIPPED if failure == "test_only" else FixvalStatus.BLOCK)
    assert live.read_bytes() == b"new\n"
    assert live.stat().st_mode & 0o777 == 0o751


@pytest.mark.parametrize(
    "failure", ["restore_cancel", "restore_repeat_cancel", "close_cancel", "patch_unlink"]
)
def test_fixval_cleanup_interruption_restores_known_source_and_truthful_recovery(
    tmp_path, monkeypatch, failure
):
    import os
    from pathlib import Path

    from code_forge._fixval_transaction import FixvalTransaction

    transaction, live = _preservation_fixture(tmp_path)
    transaction.close()
    monkeypatch.setattr("code_forge.fixval._run_baseline_guard", lambda *a, **k: ("passed", [], []))
    monkeypatch.delenv("FIXVAL_WAIVER", raising=False)
    real_run = subprocess.run

    def process(argv, **kwargs):
        if argv[0] == "git":
            git_calls.append(list(argv))
            return real_run(argv, **kwargs)
        return subprocess.CompletedProcess(argv, 1, "real process boundary control", "")

    monkeypatch.setattr(subprocess, "run", process)
    git_calls = []
    if failure in {"restore_cancel", "restore_repeat_cancel"}:
        original_restore = FixvalTransaction.restore
        calls = 0

        def restore_with_cancel(self, forward):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise KeyboardInterrupt("injected restore cancellation")
            result = original_restore(self, forward)
            if failure == "restore_repeat_cancel":
                raise KeyboardInterrupt("injected repeated cancellation")
            return result

        monkeypatch.setattr(FixvalTransaction, "restore", restore_with_cancel)
    elif failure == "close_cancel":
        original_close = FixvalTransaction.close

        def close_with_cancel(self):
            original_close(self)
            raise KeyboardInterrupt("injected cleanup cancellation")

        monkeypatch.setattr(FixvalTransaction, "close", close_with_cancel)
    else:
        original_unlink = os.unlink

        def unlink_with_error(path, *args, **kwargs):
            if Path(path).name.startswith(".fixval-revert-"):
                raise OSError("injected patch cleanup failure")
            return original_unlink(path, *args, **kwargs)

        monkeypatch.setattr(os, "unlink", unlink_with_error)
        monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {unlink_with_error})
    patch_text = "--- a/src/model.py\n+++ b/src/model.py\n@@ -1 +1 @@\n-old\n+new\n"
    if failure == "patch_unlink":
        result = run_fixval(
            FixvalCandidate(["tests/test_live.py"], ["src/model.py"]),
            ["python", "-m", "pytest"],
            tmp_path,
            "fix",
            patch_text,
        )
        assert result.status == FixvalStatus.BLOCK
        recovery = result.block_message.rsplit("Recovery: ", 1)[1]
        assert Path(recovery).is_file()
    else:
        with pytest.raises(KeyboardInterrupt, match="injected") as captured:
            run_fixval(
                FixvalCandidate(["tests/test_live.py"], ["src/model.py"]),
                ["python", "-m", "pytest"],
                tmp_path,
                "fix",
                patch_text,
            )
        assert captured.value.__notes__
        if failure == "close_cancel":
            recovery = captured.value.__notes__[0].split("FIXVAL recovery: ", 1)[1].split(" (", 1)[0]
            assert Path(recovery).is_file()
        else:
            assert calls == 2
            assert list(_recovery_paths(tmp_path, ".fixval-recovery-*"))
    assert live.read_bytes() == b"new\n"
    assert live.stat().st_mode & 0o777 == 0o751
    assert len(git_calls) == 2


def _structural_git_fixture(tmp_path, direction, nested=False):
    """Build Git's actual patch for a tracked file/directory replacement."""
    import os

    root = tmp_path / "repo"
    root.mkdir()

    def git(*args, input=None):
        return subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "user.name=Structural Test",
                "-c",
                "user.email=structural@example.invalid",
                *args,
            ],
            cwd=root,
            input=input,
            text=True,
            capture_output=True,
            check=True,
            timeout=10,
        )

    git("init", "-q")
    path = root / "pkg"
    if direction == "directory_to_file":
        path.mkdir()
        child = path / "sub" if nested else path
        if nested:
            child.mkdir()
        (child / "old.py").write_bytes(b"old child\n")
    else:
        path.write_bytes(b"old leaf\n")
    git("add", "--", "pkg")
    git("commit", "-qm", "initial fixture")
    if path.is_dir():
        (child / "old.py").unlink()
        if nested:
            child.rmdir()
        path.rmdir()
        path.write_bytes(b"new leaf\n")
        path.chmod(0o751)
        live = path
    else:
        path.unlink()
        path.mkdir()
        path.chmod(0o711)
        child = path / "sub" if nested else path
        if nested:
            child.mkdir()
            child.chmod(0o710)
        live = child / "new.py"
        live.write_bytes(b"new child\n")
        live.chmod(0o751)
    git("add", "--", "pkg")
    patch_text = git("diff", "--cached", "--binary", "--no-ext-diff").stdout
    git("apply", "-R", "--check", input=patch_text)
    before = (live.read_bytes(), os.lstat(live).st_mode)
    directory_mode = os.lstat(path).st_mode if path.is_dir() else None
    return root, path, live, patch_text, git, before, directory_mode


def _retained_deletion_git_fixture(tmp_path, nested):
    import os

    root = tmp_path / "repo"
    root.mkdir()

    def git(*args, input=None):
        return subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "gc.auto=0",
                "-c",
                "maintenance.auto=false",
                "-c",
                "user.name=Retained Test",
                "-c",
                "user.email=retained@example.invalid",
                *args,
            ],
            cwd=root,
            input=input,
            text=True,
            capture_output=True,
            check=True,
            timeout=2,
        )

    git("init", "-q")
    source = root / "src"
    source.mkdir()
    parent = source / "nested" if nested else source
    if nested:
        parent.mkdir()
    retained = parent / "gone.py"
    retained.write_bytes(b"value = 1\n")
    tests = root / "tests"
    tests.mkdir()
    test_file = tests / "test_case.py"
    test_file.write_bytes(b"def test_case():\n    assert 1 == 1\n")
    git("add", "--", "src", "tests")
    git("commit", "-qm", "initial fixture")
    git("rm", "--cached", "--", retained.relative_to(root).as_posix())
    retained.write_bytes(b"retained local value\n")
    retained.chmod(0o640)
    source.chmod(0o711)
    if nested:
        parent.chmod(0o750)
    test_file.write_bytes(b"def test_case():\n    assert 2 == 2\n")
    test_file.chmod(0o751)
    git("add", "--", "tests/test_case.py")
    patch_text = git("diff", "--cached", "--binary", "--no-ext-diff").stdout
    info = os.lstat(retained)
    original = retained.read_bytes(), info.st_dev, info.st_ino, info.st_mode
    modes = {path.relative_to(root).as_posix(): os.lstat(path).st_mode for path in {source, parent}}
    return root, retained, test_file, patch_text, git, original, modes


@pytest.mark.parametrize("nested", [False, True], ids=["flat", "nested"])
def test_transaction_retained_cached_deletion_restores_parent_modes(tmp_path, nested):
    import errno
    import os
    from code_forge._fixval_transaction import FixvalTransaction

    root, retained, test_file, patch_text, git, original, modes = _retained_deletion_git_fixture(
        tmp_path, nested
    )
    index = (root / ".git/index").read_bytes()
    head = git("rev-parse", "HEAD").stdout
    test_original = test_file.read_bytes(), os.lstat(test_file).st_mode
    transaction = FixvalTransaction(root, patch_text)
    try:
        transaction.prepare()
        git("apply", "-R", "--check", input=patch_text)
        assert transaction.reverse().returncode == 0
        transaction.mark_reverted()
        assert transaction.can_apply_forward()
        git("apply", "--check", input=patch_text)
        assert (root / "src").exists()
        assert transaction.restore(False) == []
        info = os.lstat(retained)
        assert (retained.read_bytes(), info.st_dev, info.st_ino, info.st_mode) == original
        assert {name: os.lstat(root / name).st_mode for name in modes} == modes
        assert (test_file.read_bytes(), os.lstat(test_file).st_mode) == test_original
        assert (root / ".git/index").read_bytes() == index
        assert git("rev-parse", "HEAD").stdout == head
    finally:
        descriptors = set(transaction.parents.values())
        if transaction.saved_fd is not None:
            descriptors.add(transaction.saved_fd)
        transaction.close()
        for descriptor in descriptors:
            with pytest.raises(OSError) as closed:
                os.fstat(descriptor)
            assert closed.value.errno == errno.EBADF
    assert not list(_recovery_paths(root, ".fixval-recovery-*"))
    assert not list(_recovery_paths(root, ".fixval-retired-*"))


@pytest.mark.parametrize("direction", ["directory_to_file", "file_to_directory"])
@pytest.mark.parametrize("forward", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_transaction_structural_git_round_trip(tmp_path, direction, forward, nested):
    import os
    from code_forge._fixval_transaction import FixvalTransaction

    root, path, live, patch_text, git, before, directory_mode = _structural_git_fixture(
        tmp_path, direction, nested
    )
    transaction = FixvalTransaction(root, patch_text)
    try:
        transaction.prepare()
        assert transaction.reverse().returncode == 0
        transaction.mark_reverted()
        assert transaction.can_apply_forward()
        if forward:
            git("apply", "--check", input=patch_text)
        assert transaction.restore(False) == []
        assert (live.read_bytes(), os.lstat(live).st_mode) == before
        if directory_mode is not None:
            assert os.lstat(path).st_mode == directory_mode
            if nested:
                assert os.lstat(path / "sub").st_mode & 0o777 == 0o710
        else:
            assert path.is_file()
    finally:
        transaction.close()
    assert not list(_recovery_paths(root, ".fixval-recovery-*"))


@pytest.mark.parametrize("direction", ["directory_to_file", "file_to_directory"])
@pytest.mark.parametrize("foreign", ["file", "symlink", "directory"])
def test_transaction_structural_foreign_leaf_is_preserved(tmp_path, direction, foreign):
    import os
    from code_forge._fixval_transaction import FixvalTransaction, TransactionError

    root, path, live, patch_text, git, before, _ = _structural_git_fixture(tmp_path, direction)
    transaction = FixvalTransaction(root, patch_text)
    transaction.prepare()
    assert transaction.reverse().returncode == 0
    transaction.mark_reverted()
    victim = path / "old.py" if direction == "directory_to_file" else path
    victim.unlink()
    if foreign == "directory":
        victim.mkdir()
        (victim / "untracked").write_bytes(b"foreign\n")
    elif foreign == "symlink":
        victim.symlink_to("outside")
    else:
        victim.write_bytes(b"foreign\n")
    identity = os.lstat(victim)
    try:
        try:
            assert not transaction.can_apply_forward()
        except (OSError, TransactionError):
            pass
        assert transaction.restore(False)
        after = os.lstat(victim)
        assert (after.st_dev, after.st_ino, after.st_mode) == (
            identity.st_dev,
            identity.st_ino,
            identity.st_mode,
        )
        if foreign == "file":
            assert victim.read_bytes() == b"foreign\n"
        elif foreign == "symlink":
            assert os.readlink(victim) == "outside"
        else:
            assert (victim / "untracked").read_bytes() == b"foreign\n"
        assert transaction.recovery_needed
    finally:
        transaction.close()
    assert list(_recovery_paths(root, ".fixval-recovery-*"))


def test_transaction_structural_replaced_root_is_foreign(tmp_path):
    import os
    from code_forge._fixval_transaction import FixvalTransaction, TransactionError

    root, path, live, patch_text, git, _, _ = _structural_git_fixture(tmp_path, "directory_to_file")
    transaction = FixvalTransaction(root, patch_text)
    transaction.prepare()
    assert transaction.reverse().returncode == 0
    transaction.mark_reverted()
    moved = tmp_path / "writer-moved-root"
    root.rename(moved)
    root.mkdir()
    (root / "pkg").symlink_to(moved / "pkg")
    writer = root / "writer.py"
    writer.write_bytes(b"foreign root\n")
    inode = os.lstat(writer).st_ino
    transaction.recovery_needed = True
    try:
        try:
            allowed = transaction.can_apply_forward()
        except (OSError, TransactionError):
            allowed = False
        assert not allowed
        assert transaction.restore(False)
        assert os.lstat(writer).st_ino == inode
        assert writer.read_bytes() == b"foreign root\n"
        assert (moved / "pkg" / "old.py").read_bytes() == b"old child\n"
    finally:
        transaction.close()


def test_transaction_structural_missing_postimage_stays_absent(tmp_path):
    from code_forge._fixval_transaction import FixvalTransaction

    root, path, live, patch_text, git, _, _ = _structural_git_fixture(tmp_path, "file_to_directory")
    live.unlink()
    path.rmdir()
    transaction = FixvalTransaction(root, patch_text)
    try:
        assert all(entry.original is None for entry in transaction.entries)
        transaction.prepare()
        assert not path.exists()
        assert transaction.restore(False) == []
        assert not path.exists()
    finally:
        transaction.close()
    assert not list(_recovery_paths(root, ".fixval-recovery-*"))


def test_transaction_structural_untracked_child_survives_partial_retry(tmp_path):
    import os
    from code_forge._fixval_transaction import FixvalTransaction

    root, path, live, patch_text, git, _, _ = _structural_git_fixture(
        tmp_path, "directory_to_file", nested=True
    )
    transaction = FixvalTransaction(root, patch_text)
    transaction.prepare()
    assert transaction.reverse().returncode == 0
    transaction.mark_reverted()
    writer = path / "untracked.py"
    writer.write_bytes(b"writer data\n")
    inode = os.lstat(writer).st_ino
    try:
        assert transaction.restore(False)
        assert transaction.restore(False)
        assert writer.read_bytes() == b"writer data\n"
        assert os.lstat(writer).st_ino == inode
        assert not (path / "sub").exists()
        assert transaction.recovery_needed
    finally:
        transaction.close()


@pytest.mark.parametrize("replacement", ["file", "directory"])
def test_transaction_structural_unlinked_parent_rejects_foreign_role(tmp_path, replacement):
    import os
    import unidiff
    from code_forge._fixval_transaction import FixvalTransaction, TransactionError

    root, path, live, patch_text, git, _, _ = _structural_git_fixture(tmp_path, "file_to_directory")
    if replacement == "file":
        # Reverse only Git's addition so the preimage has no leaf at pkg.
        patch_text = "".join(str(item) for item in unidiff.PatchSet(patch_text) if item.is_added_file)
    transaction = FixvalTransaction(root, patch_text)
    transaction.prepare()
    assert transaction.reverse().returncode == 0
    if path.exists():
        path.unlink()
    if replacement == "file":
        path.write_bytes(b"foreign role\n")
    else:
        path.mkdir()
    identity = os.lstat(path)
    try:
        with pytest.raises(TransactionError, match="foreign entry replaced removed directory"):
            transaction.mark_reverted()
        assert transaction.restore(False)
        assert os.lstat(path).st_ino == identity.st_ino
        if replacement == "file":
            assert path.read_bytes() == b"foreign role\n"
        else:
            assert path.is_dir()
            assert list(path.iterdir()) == []
    finally:
        transaction.close()


def test_transaction_structural_symlink_parent_is_not_followed(tmp_path):
    import os
    from code_forge._fixval_transaction import FixvalTransaction

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "new.py").write_bytes(b"foreign outside\n")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pkg").symlink_to(outside)
    identity = os.lstat(root / "pkg")
    patch_text = "--- /dev/null\n+++ b/pkg/new.py\n@@ -0,0 +1 @@\n+new child\n"
    with pytest.raises(NotADirectoryError):
        FixvalTransaction(root, patch_text)
    assert os.lstat(root / "pkg").st_ino == identity.st_ino
    assert (outside / "new.py").read_bytes() == b"foreign outside\n"
    assert not list(_recovery_paths(root, ".fixval-recovery-*"))


def test_transaction_structural_forward_deleted_directory_is_recovered(tmp_path):
    import os
    from code_forge._fixval_transaction import FixvalTransaction

    root, path, live, patch_text, git, before, directory_mode = _structural_git_fixture(
        tmp_path, "file_to_directory"
    )
    transaction = FixvalTransaction(root, patch_text)
    transaction.prepare()
    assert transaction.reverse().returncode == 0
    transaction.mark_reverted()
    assert transaction.can_apply_forward()
    git("apply", input=patch_text)
    live.unlink()
    path.rmdir()
    try:
        assert transaction.restore(False) == []
        assert (live.read_bytes(), os.lstat(live).st_mode) == before
        assert os.lstat(path).st_mode == directory_mode
    finally:
        transaction.close()


def test_transaction_structural_forward_directory_swap_preserves_writer(tmp_path, monkeypatch):
    import os
    from code_forge._fixval_transaction import FixvalTransaction

    root, path, live, patch_text, git, _, _ = _structural_git_fixture(tmp_path, "file_to_directory")
    transaction = FixvalTransaction(root, patch_text)
    transaction.prepare()
    assert transaction.reverse().returncode == 0
    transaction.mark_reverted()
    assert transaction.can_apply_forward()
    git("apply", "--check", input=patch_text)
    bind = transaction._restore_removed_parents
    swapped = []

    def swap_after_bind():
        bind()
        path.rename(root / "writer-moved-directory")
        path.mkdir()
        (path / "writer.py").write_bytes(b"writer namespace\n")
        swapped.append(os.lstat(path))

    monkeypatch.setattr(transaction, "_restore_removed_parents", swap_after_bind)
    try:
        assert transaction.restore(False)
        assert swapped
        assert os.lstat(path).st_ino == swapped[0].st_ino
        assert (path / "writer.py").read_bytes() == b"writer namespace\n"
        assert transaction.recovery_needed
    finally:
        transaction.close()


def test_transaction_structural_child_created_during_retirement_is_preserved(tmp_path, monkeypatch):
    import os
    from code_forge._fixval_transaction import FixvalTransaction

    root, path, live, patch_text, git, _, _ = _structural_git_fixture(tmp_path, "directory_to_file")
    transaction = FixvalTransaction(root, patch_text)
    transaction.prepare()
    assert transaction.reverse().returncode == 0
    transaction.mark_reverted()
    identity = os.lstat(path)
    rename = transaction._rename_between
    raced = []

    def add_before_rename(source_fd, source, target_fd, target):
        if source == "pkg" and target.startswith(".retired-parent-") and not raced:
            (path / "writer.py").write_bytes(b"foreign child\n")
            raced.append(True)
        return rename(source_fd, source, target_fd, target)

    monkeypatch.setattr(transaction, "_rename_between", add_before_rename)
    try:
        assert transaction.restore(False)
        assert raced
        assert os.lstat(path).st_ino == identity.st_ino
        assert (path / "writer.py").read_bytes() == b"foreign child\n"
        assert transaction.recovery_needed
    finally:
        transaction.close()


def test_transaction_structural_renamed_parent_is_foreign(tmp_path):
    import os
    from code_forge._fixval_transaction import FixvalTransaction, TransactionError

    root, path, live, patch_text, git, _, _ = _structural_git_fixture(tmp_path, "directory_to_file")
    transaction = FixvalTransaction(root, patch_text)
    transaction.prepare()
    assert transaction.reverse().returncode == 0
    transaction.mark_reverted()
    moved = root / "writer-directory"
    path.rename(moved)
    path.mkdir()
    (path / "writer.py").write_bytes(b"foreign namespace\n")
    identity = os.lstat(path)
    try:
        with pytest.raises(TransactionError):
            transaction.can_apply_forward()
        assert transaction.restore(False)
        assert os.lstat(path).st_ino == identity.st_ino
        assert (path / "writer.py").read_bytes() == b"foreign namespace\n"
        assert (moved / "old.py").read_bytes() == b"old child\n"
    finally:
        transaction.close()


def test_transaction_structural_empty_parent_rename_race_preserves_writer(tmp_path, monkeypatch):
    import os
    from code_forge._fixval_transaction import FixvalTransaction

    root, path, live, patch_text, git, _, _ = _structural_git_fixture(tmp_path, "directory_to_file")
    transaction = FixvalTransaction(root, patch_text)
    transaction.prepare()
    assert transaction.reverse().returncode == 0
    transaction.mark_reverted()
    rename = transaction._rename_between
    raced = []

    def replace_before_rename(source_fd, source, target_fd, target):
        if source == "pkg" and target.startswith(".retired-parent-") and not raced:
            path.rename(root / "original-preimage-directory")
            path.mkdir()
            raced.append(os.lstat(path))
        return rename(source_fd, source, target_fd, target)

    monkeypatch.setattr(transaction, "_rename_between", replace_before_rename)
    try:
        assert transaction.restore(False)
        assert raced
        assert os.lstat(path).st_ino == raced[0].st_ino
        assert path.is_dir()
        assert list(path.iterdir()) == []
        assert transaction.recovery_needed
    finally:
        transaction.close()


def _cleanup_entry_state(path):
    import os
    import stat

    observed = path.lstat()
    payload = os.readlink(path) if stat.S_ISLNK(observed.st_mode) else path.read_bytes()
    return observed.st_ino, observed.st_mode, payload


@pytest.mark.parametrize("kind", ["file", "symlink"])
@pytest.mark.parametrize("reoccupied", [False, True])
def test_cleanup_retires_and_preserves_late_replacement(tmp_path, monkeypatch, kind, reoccupied):
    import os
    from pathlib import Path

    import code_forge._fixval_transaction as module

    transaction, live = _preservation_fixture(tmp_path)
    transaction.prepare()
    assert transaction.restore(False) == []
    original_directory = transaction.directory
    saved = original_directory / "0"
    displaced = tmp_path / "writer-held-owned"
    expected = _cleanup_entry_state(saved)
    identity = module._identity
    replacement = None
    occupied = None

    def replace_after_check(parent, leaf):
        nonlocal replacement
        value = identity(parent, leaf)
        if parent == transaction.saved_fd and leaf == "0" and replacement is None:
            saved.rename(displaced)
            if kind == "file":
                saved.write_bytes(b"FOREIGN_REPLACEMENT\n")
            else:
                saved.symlink_to("foreign-literal-target")
            replacement = _cleanup_entry_state(saved)
        return value

    rename = transaction._rename_between

    def occupy_original_after_retirement(source_fd, source, target_fd, target):
        nonlocal occupied
        rename(source_fd, source, target_fd, target)
        if reoccupied and source_fd == transaction.saved_fd and source == "0":
            fd = os.open("0", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=source_fd)
            with os.fdopen(fd, "wb") as writer:
                writer.write(b"THIRD_WRITER\n")
            bound_directory = Path(os.readlink("/proc/self/fd/%s" % source_fd))
            occupied = _cleanup_entry_state(bound_directory / "0")

    monkeypatch.setattr(module, "_identity", replace_after_check)
    monkeypatch.setattr(transaction, "_rename_between", occupy_original_after_retirement)
    with pytest.raises(module.TransactionError, match="entry changed during cleanup"):
        transaction.close()
    assert _cleanup_entry_state(displaced) == expected
    assert live.read_bytes() == b"new\n"
    assert transaction.recovery_needed
    assert original_directory.is_dir()
    if reoccupied:
        assert _cleanup_entry_state(saved) == occupied
        retired = list(_recovery_paths(tmp_path, ".fixval-retired-*/payload-*"))
        assert len(retired) == 1 and _cleanup_entry_state(retired[0]) == replacement
        assert str(retired[0].parent) in transaction.recovery_location
    else:
        assert _cleanup_entry_state(saved) == replacement
        assert not list(_recovery_paths(tmp_path, ".fixval-retired-*"))
        assert transaction.recovery_location == str(original_directory)


@pytest.mark.parametrize("reoccupied", [False, True])
def test_cleanup_retires_directory_before_deleting_its_name(tmp_path, monkeypatch, reoccupied):
    import code_forge._fixval_transaction as module

    transaction, live = _preservation_fixture(tmp_path)
    transaction.prepare()
    assert transaction.restore(False) == []
    original = transaction.directory
    displaced = tmp_path / "writer-displaced-recovery"
    payload = _cleanup_entry_state(original / "0")
    check = transaction._check_directory
    foreign_inode = None
    third_inode = None

    def replace_after_directory_check():
        nonlocal foreign_inode
        check()
        original.rename(displaced)
        original.mkdir()
        foreign_inode = original.stat().st_ino

    rename = transaction._rename_between

    def occupy_original(source_fd, source, target_fd, target):
        nonlocal third_inode
        rename(source_fd, source, target_fd, target)
        if reoccupied and source_fd == transaction.recovery_parent_fd and source == original.name:
            original.mkdir()
            (original / "third").write_bytes(b"THIRD_DIRECTORY_WRITER\n")
            third_inode = original.stat().st_ino

    monkeypatch.setattr(transaction, "_check_directory", replace_after_directory_check)
    monkeypatch.setattr(transaction, "_rename_between", occupy_original)
    with pytest.raises(module.TransactionError, match="directory changed during cleanup"):
        transaction.close()
    assert _cleanup_entry_state(displaced / "0") == payload
    assert live.read_bytes() == b"new\n"
    assert transaction.recovery_needed
    assert str(displaced) in transaction.recovery_location
    if reoccupied:
        assert original.stat().st_ino == third_inode
        assert (original / "third").read_bytes() == b"THIRD_DIRECTORY_WRITER\n"
        retired = list(_recovery_paths(tmp_path, ".fixval-retired-*/recovery"))
        assert len(retired) == 1 and retired[0].stat().st_ino == foreign_inode
        assert str(retired[0].parent) in transaction.recovery_location
    else:
        assert original.stat().st_ino == foreign_inode
        assert not list(_recovery_paths(tmp_path, ".fixval-retired-*"))


@pytest.mark.parametrize("kind", ["directory", "leaf"])
def test_cleanup_never_overwrites_retirement_destination(tmp_path, monkeypatch, kind):
    import errno

    transaction, live = _preservation_fixture(tmp_path)
    transaction.prepare()
    assert transaction.restore(False) == []
    original = transaction.directory
    payload = _cleanup_entry_state(original / "0")
    rename = transaction._rename_between
    foreign = None

    def occupy_destination(source_fd, source, target_fd, target):
        nonlocal foreign
        if foreign is None and target == ("recovery" if kind == "directory" else "payload-0"):
            holder = next(_recovery_paths(tmp_path, ".fixval-retired-*"))
            occupied = holder / target
            occupied.write_bytes(b"FOREIGN_RETIREMENT_DESTINATION\n")
            foreign = occupied, _cleanup_entry_state(occupied)
        rename(source_fd, source, target_fd, target)

    monkeypatch.setattr(transaction, "_rename_between", occupy_destination)
    with pytest.raises(OSError) as captured:
        transaction.close()
    assert captured.value.errno == errno.EEXIST
    assert _cleanup_entry_state(foreign[0]) == foreign[1]
    assert _cleanup_entry_state(original / "0") == payload
    assert live.read_bytes() == b"new\n"
    assert transaction.recovery_needed
    assert str(foreign[0].parent) in transaction.recovery_location


@pytest.mark.parametrize("boundary", ["directory", "leaf"])
def test_cleanup_cancellation_retains_reachable_payloads(tmp_path, monkeypatch, boundary):
    transaction, live = _preservation_fixture(tmp_path)
    transaction.prepare()
    assert transaction.restore(False) == []
    original = transaction.directory
    payload = _cleanup_entry_state(original / "0")
    rename = transaction._rename_between
    interrupted = False

    def cancel_before_retirement(source_fd, source, target_fd, target):
        nonlocal interrupted
        if not interrupted and target == ("recovery" if boundary == "directory" else "payload-0"):
            interrupted = True
            raise KeyboardInterrupt("owned cleanup interruption")
        rename(source_fd, source, target_fd, target)

    monkeypatch.setattr(transaction, "_rename_between", cancel_before_retirement)
    with pytest.raises(KeyboardInterrupt, match="owned cleanup interruption"):
        transaction.close()
    assert interrupted
    assert _cleanup_entry_state(original / "0") == payload
    assert live.read_bytes() == b"new\n"
    assert transaction.recovery_needed
    assert transaction.recovery_location == str(original)
    assert not list(_recovery_paths(tmp_path, ".fixval-retired-*"))


@pytest.mark.parametrize("new_name", ["directory", "leaf"])
def test_cleanup_preserves_reoccupied_original_names(tmp_path, monkeypatch, new_name):
    transaction, live = _preservation_fixture(tmp_path)
    transaction.prepare()
    assert transaction.restore(False) == []
    original = transaction.directory
    rename = transaction._rename_between
    foreign = None

    def create_original_name(source_fd, source, target_fd, target):
        nonlocal foreign
        rename(source_fd, source, target_fd, target)
        if (
            new_name == "directory"
            and source_fd == transaction.recovery_parent_fd
            and source == original.name
        ):
            original.mkdir()
            foreign = original, original.stat().st_ino
        elif new_name == "leaf" and source_fd == transaction.saved_fd and source == "0":
            retired_directory = next(_recovery_paths(tmp_path, ".fixval-retired-*/recovery"))
            path = retired_directory / "0"
            path.write_bytes(b"NEW_ORIGINAL_LEAF\n")
            foreign = path, _cleanup_entry_state(path)

    monkeypatch.setattr(transaction, "_rename_between", create_original_name)
    if new_name == "directory":
        transaction.close()
        assert original.stat().st_ino == foreign[1]
        assert not list(_recovery_paths(tmp_path, ".fixval-retired-*"))
    else:
        with pytest.raises(OSError):
            transaction.close()
        assert _cleanup_entry_state(original / "0") == foreign[1]
        assert transaction.recovery_needed
        assert transaction.recovery_location == str(original)
    assert live.read_bytes() == b"new\n"


@pytest.mark.parametrize("empty", [False, True])
def test_cleanup_healthy_retirement_has_no_leftovers(tmp_path, empty):
    from code_forge._fixval_transaction import FixvalTransaction

    if empty:
        transaction = FixvalTransaction(tmp_path, "")
    else:
        transaction, live = _preservation_fixture(tmp_path)
    transaction.prepare()
    assert transaction.restore(False) == []
    transaction.close()
    assert not transaction.recovery_needed
    assert not list(_recovery_paths(tmp_path, ".fixval-recovery-*"))
    assert not list(_recovery_paths(tmp_path, ".fixval-retired-*"))
    if not empty:
        assert live.read_bytes() == b"new\n"


@pytest.mark.parametrize("interleaving", ["none", "content", "replacement", "mode", "parent"])
def test_transaction_forward_mode_restoration_rechecks_physical_source(
    tmp_path, monkeypatch, interleaving
):
    """A writer at the actual publication boundary must retain its data and recovery."""
    import os
    import stat

    from code_forge._fixval_transaction import FixvalTransaction

    root = tmp_path / "repo"
    root.mkdir()

    def _git(root, *args, input=None):
        return subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "user.name=Race Test",
                "-c",
                "user.email=race@example.invalid",
                *args,
            ],
            cwd=root,
            input=input,
            text=True,
            capture_output=True,
            check=True,
            timeout=5,
        ).stdout

    _git(root, "init", "-q")
    (root / "pkg").mkdir()
    (root / "pkg/value.py").write_bytes(b"value = 1\n")
    _git(root, "add", "pkg/value.py")
    _git(root, "commit", "-qm", "initial fixture")
    source = root / "pkg/value.py"
    source.write_bytes(b"value = 2\n")
    source.chmod(0o751)
    _git(root, "add", "pkg/value.py")
    packet = _git(root, "diff", "--cached", "--binary")
    index_before = (root / ".git/index").read_bytes()
    transaction = FixvalTransaction(root, packet)
    transaction.prepare()
    recovery = transaction.directory
    entry = next(entry for entry in transaction.entries if entry.path == "pkg/value.py")
    saved = recovery / entry.saved
    assert transaction.reverse().returncode == 0
    transaction.mark_reverted()
    _git(root, "apply", "--check", input=packet)
    real_link = os.link
    observed = []
    writer_path = source

    def writer(src, dst, **kwargs):
        nonlocal writer_path
        real_link(src, dst, **kwargs)
        if dst != "value.py" or kwargs.get("dst_dir_fd") != next(
            entry.parent for entry in transaction.entries if entry.path == "pkg/value.py"
        ):
            return
        observed.append(source.stat().st_ino)
        if interleaving == "content":
            source.write_bytes(b"writer data\n")
        elif interleaving == "replacement":
            replacement = root / "replacement"
            replacement.write_bytes(b"writer replacement\n")
            replacement.chmod(0o751)
            replacement.replace(source)
        elif interleaving == "mode":
            source.chmod(0o700)
        elif interleaving == "parent":
            source.parent.rename(root / "writer-parent")
            writer_path = root / "writer-parent/value.py"
            writer_path.write_bytes(b"writer parent data\n")

    monkeypatch.setattr("code_forge._fixval_transaction.os.link", writer)
    try:
        errors = transaction.restore(False)
        assert len(observed) == 1
        if interleaving == "none":
            assert errors == []
            assert source.read_bytes() == b"value = 2\n"
            assert stat.S_IMODE(source.stat().st_mode) == 0o751
        else:
            assert errors, "restoration must reject a writer at the publication boundary"
            assert transaction.recovery_needed
            assert saved.read_bytes() == b"value = 2\n"
            expected = {
                "content": b"writer data\n",
                "replacement": b"writer replacement\n",
                "mode": b"value = 2\n",
                "parent": b"writer parent data\n",
            }
            assert writer_path.read_bytes() == expected[interleaving]
            if interleaving == "mode":
                assert stat.S_IMODE(source.stat().st_mode) == 0o700
    finally:
        transaction.close()
    assert (root / ".git/index").read_bytes() == index_before
    assert recovery.exists() == (interleaving != "none")
    if interleaving != "none":
        assert saved.read_bytes() == b"value = 2\n"


def test_transaction_saved_link_restoration_rechecks_physical_source(tmp_path, monkeypatch):
    """A same-byte, same-mode writer replacement must not consume recovery."""
    import os

    from code_forge._fixval_transaction import FixvalTransaction

    root, path, live, packet, git, before, _ = _structural_git_fixture(tmp_path, "directory_to_file")
    index_before = (root / ".git/index").read_bytes()
    transaction = FixvalTransaction(root, packet)
    transaction.prepare()
    recovery = transaction.directory
    entry = next(entry for entry in transaction.entries if entry.path == "pkg")
    saved = recovery / entry.saved
    assert transaction.reverse().returncode == 0
    transaction.mark_reverted()
    real_link = os.link
    writer_inode = []

    def replace_after_link(src, dst, **kwargs):
        real_link(src, dst, **kwargs)
        if dst != entry.leaf or kwargs.get("dst_dir_fd") != entry.parent:
            return
        replacement = root / "writer-replacement"
        replacement.write_bytes(before[0])
        replacement.chmod(before[1] & 0o777)
        writer_inode.append(replacement.stat().st_ino)
        replacement.replace(path)

    monkeypatch.setattr("code_forge._fixval_transaction.os.link", replace_after_link)
    try:
        errors = transaction.restore(False)
        assert errors, "same-byte replacement must fail physical restoration validation"
        assert transaction.recovery_needed
        assert path.stat().st_ino == writer_inode[0]
        assert (live.read_bytes(), os.lstat(live).st_mode) == before
        assert saved.read_bytes() == before[0]
    finally:
        transaction.close()
    assert recovery.exists() and saved.read_bytes() == before[0]
    assert (root / ".git/index").read_bytes() == index_before


@pytest.mark.parametrize("retained", [False, True])
@pytest.mark.parametrize("writer", ["content", "mode"])
def test_transaction_live_publication_keeps_independent_original(
    tmp_path, monkeypatch, retained, writer
):
    """A real published inode may change, but original recovery must stay independent."""
    import os

    from code_forge._fixval_transaction import FixvalTransaction

    if retained:
        root, live, _, packet, git, original, _ = _retained_deletion_git_fixture(tmp_path, False)
        before = (original[0], original[3])
    else:
        root, _, live, packet, git, before, _ = _structural_git_fixture(tmp_path, "directory_to_file")
    index_before = (root / ".git/index").read_bytes()
    transaction = FixvalTransaction(root, packet)
    transaction.prepare()
    entry = next(
        entry for entry in transaction.entries if entry.path == live.relative_to(root).as_posix()
    )
    recovery = transaction.directory
    assert transaction.reverse().returncode == 0
    transaction.mark_reverted()
    real_link = os.link
    writes = []

    def writer_after_publish(src, dst, **kwargs):
        real_link(src, dst, **kwargs)
        if dst == entry.leaf and kwargs.get("dst_dir_fd") == entry.parent:
            writes.append(live.stat().st_ino)
            if writer == "content":
                live.write_bytes(b"concurrent writer data\n")
            else:
                live.chmod(0o700)

    monkeypatch.setattr("code_forge._fixval_transaction.os.link", writer_after_publish)
    try:
        assert transaction.restore(False)
        assert len(writes) == 1 and live.stat().st_ino == writes[0]
        assert transaction.recovery_needed
    finally:
        transaction.close()
    assert recovery.exists()
    if writer == "content":
        assert live.read_bytes() == b"concurrent writer data\n"
    else:
        assert live.read_bytes() == before[0] and live.stat().st_mode & 0o777 == 0o700
    originals = [
        payload
        for payload in recovery.iterdir()
        if payload.is_file()
        and payload.read_bytes() == before[0]
        and payload.stat().st_mode == before[1]
        and payload.stat().st_ino != live.stat().st_ino
    ]
    assert originals, "recovery must retain independent original bytes and mode"
    assert (root / ".git/index").read_bytes() == index_before


def test_transaction_unowned_forward_inode_keeps_writer_mode(tmp_path):
    """Same bytes do not authorize changing a foreign post-forward inode."""
    from code_forge._fixval_transaction import FixvalTransaction

    root, path, live, packet, git, before, _ = _structural_git_fixture(tmp_path, "directory_to_file")
    transaction = FixvalTransaction(root, packet)
    transaction.prepare()
    recovery = transaction.directory
    assert transaction.reverse().returncode == 0
    transaction.mark_reverted()
    git("apply", input=packet)
    foreign = root / "foreign-writer"
    foreign.write_bytes(before[0])
    foreign.chmod(0o700)
    foreign.replace(live)
    expected = live.stat().st_ino, live.read_bytes(), live.stat().st_mode
    try:
        assert transaction.restore(True), "unowned forward inode must not be adopted"
    finally:
        transaction.close()
    assert (live.stat().st_ino, live.read_bytes(), live.stat().st_mode) == expected
    assert recovery.exists()


@pytest.mark.parametrize("retained", [False, True])
@pytest.mark.parametrize("writer", ["content", "mode"])
def test_transaction_cleanup_keeps_snapshot_after_published_inode_changes(
    tmp_path, monkeypatch, retained, writer
):
    """Validate live source again before retiring the independent original snapshot."""
    import os
    from code_forge._fixval_transaction import FixvalTransaction, TransactionError

    if retained:
        root, live, _, packet, git, original, _ = _retained_deletion_git_fixture(tmp_path, False)
        before = (original[0], original[3])
    else:
        root, _, live, packet, git, before, _ = _structural_git_fixture(tmp_path, "directory_to_file")
    transaction = FixvalTransaction(root, packet)
    transaction.prepare()
    entry = next(
        entry for entry in transaction.entries if entry.path == live.relative_to(root).as_posix()
    )
    recovery = transaction.directory
    assert transaction.reverse().returncode == 0
    transaction.mark_reverted()
    assert transaction.restore(False) == []
    snapshots = {entry.snapshot for entry in transaction.entries if entry.snapshot is not None}
    ordered = sorted(transaction.saved_entries, key=lambda name: name in snapshots)
    mutable = entry.saved if retained else "published-" + entry.saved
    boundary = "payload-%s" % ordered.index(mutable)
    real_unlink = os.unlink
    writes = []

    def writer_after_retiring_live_link(name, **kwargs):
        real_unlink(name, **kwargs)
        if name == boundary:
            writes.append(live.stat().st_ino)
            if writer == "content":
                live.write_bytes(b"late writer data\n")
            else:
                live.chmod(0o700)

    monkeypatch.setattr(os, "unlink", writer_after_retiring_live_link)
    with pytest.raises(TransactionError, match="restored source changed before cleanup"):
        transaction.close()
    assert len(writes) == 1 and live.stat().st_ino == writes[0]
    assert transaction.recovery_needed and recovery.exists()
    snapshot = recovery / entry.snapshot
    assert (snapshot.read_bytes(), snapshot.stat().st_mode) == before
    assert snapshot.stat().st_ino != live.stat().st_ino
    if writer == "content":
        assert live.read_bytes() == b"late writer data\n"
    else:
        assert live.read_bytes() == before[0] and live.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("phase", ["before_link", "after_link", "before_mark"])
@pytest.mark.parametrize("same_bytes", [False, True])
def test_transaction_reverse_publication_preserves_foreign_inode(
    tmp_path, monkeypatch, phase, same_bytes
):
    import os

    from code_forge._fixval_transaction import TransactionError

    transaction, live = _preservation_fixture(tmp_path)
    transaction.prepare()
    original = live.read_bytes(), live.stat().st_mode
    real_link = os.link
    foreign = []

    def replace_live():
        replacement = tmp_path / "writer"
        replacement.write_bytes(b"old\n" if same_bytes else b"writer data\n")
        replacement.chmod(0o751)
        replacement.replace(live)
        info = live.stat()
        foreign.append((info.st_dev, info.st_ino, info.st_mode, live.read_bytes()))

    def race(source, target, **kwargs):
        if source == "reverse-0" and phase == "before_link":
            replace_live()
        real_link(source, target, **kwargs)
        if source == "reverse-0" and phase == "after_link":
            replace_live()

    monkeypatch.setattr(os, "link", race)
    if phase == "before_mark":
        assert transaction.reverse().returncode == 0
        replace_live()
        with pytest.raises(TransactionError, match="reverse entry changed"):
            transaction.mark_reverted()
    else:
        with pytest.raises((TransactionError, FileExistsError)):
            transaction.reverse()
    assert transaction.restore(False)
    transaction.close()
    info = live.stat()
    assert (info.st_dev, info.st_ino, info.st_mode, live.read_bytes()) == foreign[0]
    snapshot = transaction.directory / "0"
    assert (snapshot.read_bytes(), snapshot.stat().st_mode) == original
    assert snapshot.stat().st_ino != live.stat().st_ino
    assert transaction.recovery_needed


def _private_image_fixture(tmp_path):
    from code_forge._fixval_transaction import _PrivateImage

    transaction, live = _preservation_fixture(tmp_path)
    transaction.prepare()
    image = _PrivateImage(transaction)
    root = Path(image.cwd).resolve()
    (root / "src").mkdir()
    (root / "src/model.py").write_bytes(b"private source\n")
    (root / ".reverse.patch").write_bytes(b"private patch\n")
    return transaction, image, root, live


@pytest.mark.parametrize("container", [False, True])
def test_private_image_allocation_and_seal_bind_opened_directory(tmp_path, monkeypatch, container):
    import os
    from code_forge._fixval_transaction import _PrivateImage, TransactionError

    transaction, live = _preservation_fixture(tmp_path)
    transaction.prepare()
    image = None
    if container:
        image = _PrivateImage(transaction)
        image_root = Path(image.cwd).resolve()
        (image_root / "src").mkdir()
        leaf = "src"
        parent = image.fd
    else:
        leaf = None
        parent = transaction.saved_fd
    real_open = os.open
    moved = tmp_path / "moved-owned-container"
    writer = []
    opened = []

    def replace_before_open(name, flags, *args, **kwargs):
        if kwargs.get("dir_fd") == parent and (name == leaf or leaf is None):
            current = Path("/proc/self/fd/%s" % parent).resolve() / name
            current.rename(moved)
            current.mkdir()
            (current / "writer").write_bytes(b"unrelated writer\n")
            writer.append((current, current.stat().st_ino))
        fd = real_open(name, flags, *args, **kwargs)
        if kwargs.get("dir_fd") == parent and (name == leaf or leaf is None):
            opened.append((fd, os.fstat(fd)))
        return fd

    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "open", replace_before_open)
            with pytest.raises(TransactionError, match="changed"):
                if container:
                    image.seal()
                else:
                    _PrivateImage(transaction)
        if not container:
            with pytest.raises(OSError):
                os.fstat(opened[0][0])
        assert len(writer) == 1
        current, inode = writer[0]
        assert current.stat().st_ino == inode
        assert (current / "writer").read_bytes() == b"unrelated writer\n"
        assert moved.is_dir()
        assert live.read_bytes() == b"new\n"
    finally:
        if image is not None:
            image.close()
        if not container:
            for fd, identity in opened:
                try:
                    observed = os.fstat(fd)
                except OSError:
                    continue
                if (observed.st_dev, observed.st_ino) == (identity.st_dev, identity.st_ino):
                    os.close(fd)
        transaction.recovery_needed = True
        transaction.close()


@pytest.mark.parametrize("change", ["container", "unexpected", "payload"])
def test_private_image_rejects_changed_namespace_without_deleting_writer(tmp_path, change):
    from code_forge._fixval_transaction import TransactionError

    transaction, image, root, live = _private_image_fixture(tmp_path)
    try:
        if change == "unexpected":
            writer = root / "writer"
            writer.write_bytes(b"unexpected writer\n")
            with pytest.raises(TransactionError, match="unexpected entry"):
                image.seal()
        else:
            image.seal()
            if change == "container":
                (root / "src").rename(tmp_path / "original-private-src")
                (root / "src").mkdir()
                writer = root / "src/writer"
                writer.write_bytes(b"container writer\n")
                with pytest.raises(TransactionError, match="container changed"):
                    image.check()
            else:
                writer = root / "src/model.py"
                writer.write_bytes(b"changed private payload\n")
                with pytest.raises(TransactionError, match="payload changed"):
                    image.cleanup()
                writer = Path(image.cwd).resolve() / "src/model.py"
        assert writer.read_bytes() in {
            b"unexpected writer\n",
            b"container writer\n",
            b"changed private payload\n",
        }
        assert live.read_bytes() == b"new\n"
    finally:
        image.close()
        transaction.recovery_needed = True
        transaction.close()


@pytest.mark.parametrize("root_retirement", [False, True])
@pytest.mark.parametrize("occupied", [False, True])
def test_private_image_retirement_preserves_replacement_and_rolls_back(
    tmp_path, monkeypatch, root_retirement, occupied
):
    from code_forge._fixval_transaction import TransactionError

    transaction, image, root, live = _private_image_fixture(tmp_path)
    image.seal()
    rename = transaction._rename_between
    replacements = []
    original = tmp_path / "original-private-directory"
    current = root if root_retirement else root / "src"
    before = current.stat().st_ino
    expected_leaf = image.name if root_retirement else "src"
    expected_parent = transaction.saved_fd if root_retirement else image.fd

    def replace_at_retirement(source_parent, source, target_parent, target):
        if source_parent == expected_parent and source == expected_leaf:
            current.rename(original)
            current.mkdir()
            (current / "writer").write_bytes(b"foreign retirement writer\n")
            replacements.append(current.stat().st_ino)
        rename(source_parent, source, target_parent, target)
        if source_parent == expected_parent and source == expected_leaf and occupied:
            current.mkdir()
            (current / "second-writer").write_bytes(b"occupied writer\n")

    try:
        monkeypatch.setattr(transaction, "_rename_between", replace_at_retirement)
        with pytest.raises(TransactionError, match="changed"):
            if root_retirement:
                image.cleanup()
            else:
                image._retire_directory(image.fd, "src", image.directories[0][3])
        assert original.stat().st_ino == before
        assert (
            original / ("src/model.py" if root_retirement else "model.py")
        ).read_bytes() == b"private source\n"
        if occupied:
            assert (current / "second-writer").read_bytes() == b"occupied writer\n"
            foreign = next(
                p for p in transaction.directory.iterdir() if p.stat().st_ino == replacements[0]
            )
        else:
            foreign = current
        assert foreign.stat().st_ino == replacements[0]
        assert (foreign / "writer").read_bytes() == b"foreign retirement writer\n"
        assert live.read_bytes() == b"new\n"
    finally:
        image.close()
        transaction.recovery_needed = True
        transaction.close()


@pytest.mark.parametrize("active", [False, True])
def test_private_image_cleanup_failure_is_visible_and_closes_owned_descriptors(tmp_path, active):
    import os
    from code_forge._fixval_transaction import TransactionError

    transaction, live = _preservation_fixture(tmp_path)
    transaction.prepare()
    descriptor = None
    try:
        exception = KeyboardInterrupt if active else TransactionError
        message = "owned active cancellation" if active else "private image cleanup failed"
        with pytest.raises(exception, match=message) as caught:
            with transaction._private_image() as image:
                descriptor = image.fd
                writer = Path(image.cwd).resolve() / "writer"
                writer.write_bytes(b"unexpected private writer\n")
                if active:
                    raise KeyboardInterrupt("owned active cancellation")
        if active:
            assert "private image cleanup failed" in caught.value.__notes__[0]
        with pytest.raises(OSError):
            os.fstat(descriptor)
        assert writer.read_bytes() == b"unexpected private writer\n"
        assert transaction.recovery_needed
        assert transaction.image_errors
        assert live.read_bytes() == b"new\n"
    finally:
        if descriptor is not None and image.fd is not None:
            image.close()
        transaction.recovery_needed = True
        transaction.close()


@pytest.mark.parametrize("failure", ["restore_error", "retry_error", "escaped_error"])
def test_fixval_restoration_errors_close_descriptors_and_retain_original(
    tmp_path, monkeypatch, failure, request
):
    import os
    from code_forge._fixval_transaction import FixvalTransaction

    transaction, live = _preservation_fixture(tmp_path)
    transaction.close()
    monkeypatch.setattr(
        "code_forge.fixval._run_baseline_guard", lambda *args, **kwargs: ("passed", [], [])
    )
    monkeypatch.delenv("FIXVAL_WAIVER", raising=False)
    original_run = subprocess.run
    captured = []
    calls = []
    identities = {}

    def close_owned():
        for owned in captured:
            try:
                observed = os.fstat(owned.root_fd)
            except OSError:
                continue
            identity = identities[id(owned)]
            if (observed.st_dev, observed.st_ino) == identity:
                owned.recovery_needed = True
                owned.close()

    request.addfinalizer(close_owned)

    def process(argv, **kwargs):
        if argv[0] == "git":
            return original_run(argv, **kwargs)
        return subprocess.CompletedProcess(argv, 1, "owned process control", "")

    def failed_restore(self, forward):
        captured.append(self)
        bound = os.fstat(self.root_fd)
        identities[id(self)] = (bound.st_dev, bound.st_ino)
        calls.append(forward)
        if failure == "retry_error" and len(calls) == 1:
            raise KeyboardInterrupt("owned restoration cancellation")
        if failure == "escaped_error":
            raise RuntimeError("owned unexpected restoration")
        raise OSError("owned restoration refusal")

    monkeypatch.setattr(subprocess, "run", process)
    monkeypatch.setattr(FixvalTransaction, "restore", failed_restore)
    patch_text = "--- a/src/model.py\n+++ b/src/model.py\n@@ -1 +1 @@\n-old\n+new\n"
    expected_exception = {"retry_error": KeyboardInterrupt, "escaped_error": RuntimeError}.get(failure)
    if expected_exception:
        with pytest.raises(expected_exception, match="owned") as caught:
            run_fixval(
                FixvalCandidate(["tests/test_live.py"], ["src/model.py"]),
                ["python", "-m", "pytest"],
                tmp_path,
                "fix",
                patch_text,
            )
    else:
        result = run_fixval(
            FixvalCandidate(["tests/test_live.py"], ["src/model.py"]),
            ["python", "-m", "pytest"],
            tmp_path,
            "fix",
            patch_text,
        )
        assert result.status == FixvalStatus.BLOCK
        assert "entry restoration failed" in result.block_message
    if failure == "retry_error":
        assert "entry restoration retry failed: owned restoration refusal" in caught.value.__notes__[0]
    assert len(calls) == (2 if failure == "retry_error" else 1)
    assert live.read_bytes() == b"old\n"
    assert live.is_file()
    for owned in captured:
        with pytest.raises(OSError):
            os.fstat(owned.root_fd)
        assert owned.directory.is_dir()
        assert owned.recovery_needed
        entry = next(e for e in owned.entries if e.path == "src/model.py")
        assert (owned.directory / entry.snapshot).read_bytes() == b"new\n"
