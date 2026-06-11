# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for FIXVAL core module (fix-validation gate).

TDD RED phase: tests written first, implementation follows.
Covers: classify_fixval_candidate, parse_fixval_waiver, run_fixval,
        FixvalResult findings, run_overfit_guard, end-to-end real git.
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch


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
    """D-01: structural trigger -- BOTH test and non-test files required."""

    def test_both_code_and_test_returns_candidate(self):
        result = classify_fixval_candidate(
            ["src/foo.py", "tests/test_foo.py"]
        )
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
        result = classify_fixval_candidate(
            ["src/foo.py", "foo.test.ts", "bar_test.go"]
        )
        assert isinstance(result, FixvalCandidate)
        assert sorted(result.test_files) == ["bar_test.go", "foo.test.ts"]
        assert result.non_test_files == ["src/foo.py"]

    def test_python_test_patterns(self):
        """All Python test patterns: tests/test_*.py, *_test.py, test_*.py"""
        result = classify_fixval_candidate([
            "src/app.py",
            "tests/test_app.py",
            "utils_test.py",
            "test_helpers.py",
        ])
        assert isinstance(result, FixvalCandidate)
        assert len(result.test_files) == 3
        assert result.non_test_files == ["src/app.py"]

    def test_ts_spec_pattern(self):
        """TypeScript .spec.ts recognized as test."""
        result = classify_fixval_candidate(
            ["src/app.ts", "src/app.spec.ts"]
        )
        assert isinstance(result, FixvalCandidate)
        assert result.test_files == ["src/app.spec.ts"]


# ---- parse_fixval_waiver tests ----


class TestParseFixvalWaiver:
    """D-04/D-05: dual-channel waiver (env var + trailer)."""

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
            [StateFinding(
                id="FIXVAL_SKIPPED",
                fingerprint="fixval-baseline-fail",
                source="FIXVAL",
                disposition=Disposition.DISMISSED,
                file="",
                line_range=[],
                description="baseline failed",
            )],
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
    def test_revert_apply_failure_skips(
        self, mock_run, mock_guard, tmp_path
    ):
        mock_guard.return_value = ("passed", [], [])
        # git apply -R fails
        mock_run.return_value = MagicMock(returncode=1, stderr="error")
        candidate = _make_candidate()
        diff = (
            "--- a/src/foo.py\n+++ b/src/foo.py\n"
            "@@ -1 +1 @@\n-old\n+new\n"
        )
        result = run_fixval(
            candidate,
            test_cmd=["python", "-m", "pytest"],
            cwd=tmp_path,
            commit_message="fix: foo",
            diff_text=diff,
        )
        assert result.status == FixvalStatus.SKIPPED
        assert "revert" in result.findings[0].description.lower()

    @patch("code_forge.fixval._run_baseline_guard")
    @patch("subprocess.run")
    def test_test_fails_on_revert_passes(
        self, mock_run, mock_guard, tmp_path
    ):
        mock_guard.return_value = ("passed", [], [])
        # First call: git apply -R (revert) succeeds
        # Second call: test run -> fails (RED) = PASS
        # Third call: git apply (restore) succeeds
        mock_run.side_effect = [
            MagicMock(returncode=0),  # git apply -R
            MagicMock(returncode=1),  # test fails (RED) -> FIXVAL PASS
            MagicMock(returncode=0),  # git apply (restore)
        ]
        candidate = _make_candidate()
        diff = (
            "--- a/src/foo.py\n+++ b/src/foo.py\n"
            "@@ -1 +1 @@\n-old\n+new\n"
        )
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
    def test_test_passes_on_revert_blocks(
        self, mock_run, mock_guard, tmp_path
    ):
        mock_guard.return_value = ("passed", [], [])
        mock_run.side_effect = [
            MagicMock(returncode=0),  # git apply -R (revert)
            MagicMock(returncode=0),  # test passes (GREEN) -> FIXVAL BLOCK
            MagicMock(returncode=0),  # git apply (restore)
        ]
        candidate = _make_candidate()
        diff = (
            "--- a/src/foo.py\n+++ b/src/foo.py\n"
            "@@ -1 +1 @@\n-old\n+new\n"
        )
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
    def test_restore_via_apply_not_checkout(
        self, mock_run, mock_guard, tmp_path
    ):
        mock_guard.return_value = ("passed", [], [])
        mock_run.side_effect = [
            MagicMock(returncode=0),  # git apply -R
            MagicMock(returncode=1),  # test fails -> PASS
            MagicMock(returncode=0),  # git apply (forward restore)
        ]
        candidate = _make_candidate()
        diff = (
            "--- a/src/foo.py\n+++ b/src/foo.py\n"
            "@@ -1 +1 @@\n-old\n+new\n"
        )
        run_fixval(
            candidate,
            test_cmd=["python", "-m", "pytest"],
            cwd=tmp_path,
            commit_message="fix: foo",
            diff_text=diff,
        )
        # Verify git apply (forward, no -R) was called for restore
        restore_call = mock_run.call_args_list[-1]
        cmd = restore_call[0][0] if restore_call[0] else restore_call[1].get("args", [])
        assert "git" in cmd[0] if isinstance(cmd, list) else True
        # Must NOT contain "checkout"
        for call in mock_run.call_args_list:
            args = call[0][0] if call[0] else call[1].get("args", [])
            if isinstance(args, list):
                assert "checkout" not in args

    @patch("code_forge.fixval._run_baseline_guard")
    @patch("subprocess.run")
    def test_scoped_test_cmd(self, mock_run, mock_guard, tmp_path):
        mock_guard.return_value = ("passed", [], [])
        mock_run.side_effect = [
            MagicMock(returncode=0),  # git apply -R
            MagicMock(returncode=1),  # test (scoped)
            MagicMock(returncode=0),  # git apply restore
        ]
        candidate = FixvalCandidate(
            test_files=["tests/test_a.py", "tests/test_b.py"],
            non_test_files=["src/foo.py"],
        )
        diff = (
            "--- a/src/foo.py\n+++ b/src/foo.py\n"
            "@@ -1 +1 @@\n-old\n+new\n"
        )
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
        diff = (
            "--- a/src/foo.py\n+++ b/src/foo.py\n"
            "@@ -1 +1 @@\n-old\n+new\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),  # git apply -R
                MagicMock(returncode=0),  # test passes -> BLOCK
                MagicMock(returncode=0),  # git apply restore
            ]
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
        mock_run.side_effect = [
            MagicMock(returncode=0),  # git apply -R
            MagicMock(returncode=1),  # test fails -> PASS
            MagicMock(returncode=0),  # git apply restore
        ]
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
        with patch("code_forge.fixval._run_baseline_guard") as mock_guard, \
             patch("subprocess.run") as mock_run:
            mock_guard.return_value = ("passed", [], [])
            mock_run.side_effect = [
                MagicMock(returncode=0),
                MagicMock(returncode=0),  # test passes -> BLOCK
                MagicMock(returncode=0),
            ]
            candidate = _make_candidate()
            diff = (
                "--- a/src/foo.py\n+++ b/src/foo.py\n"
                "@@ -1 +1 @@\n-old\n+new\n"
            )
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
        with patch("code_forge.fixval._run_baseline_guard") as mock_guard, \
             patch("subprocess.run") as mock_run:
            mock_guard.return_value = ("passed", [], [])
            mock_run.side_effect = [
                MagicMock(returncode=0),
                MagicMock(returncode=1),  # test fails -> PASS
                MagicMock(returncode=0),
            ]
            candidate = _make_candidate()
            diff = (
                "--- a/src/foo.py\n+++ b/src/foo.py\n"
                "@@ -1 +1 @@\n-old\n+new\n"
            )
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
    def test_waived_produces_dismissed_plus_advisory(
        self, mock_waiver, tmp_path
    ):
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
            ["git", "init"], cwd=tmp_path, check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=tmp_path, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=tmp_path, check=True, capture_output=True,
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
        test_file.write_text(
            "from src.calc import add\n"
            "def test_add():\n"
            "    assert add(1, 2) == 0\n"
        )

        # Initial commit
        subprocess.run(
            ["git", "add", "."], cwd=tmp_path, check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=tmp_path, check=True, capture_output=True,
        )

        # Fix the code
        code_file.write_text("def add(a, b):\n    return a + b\n")
        # Fix the test
        test_file.write_text(
            "from src.calc import add\n"
            "def test_add():\n"
            "    assert add(1, 2) == 3\n"
        )

        # Generate diff
        diff_result = subprocess.run(
            ["git", "diff"], cwd=tmp_path, capture_output=True, text=True,
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
                test_cmd=["python3", "-c",
                           "import sys; sys.path.insert(0, 'src'); "
                           "sys.path.insert(0, '.'); "
                           "from src.calc import add; "
                           "assert add(1, 2) == 3, 'expected 3'"],
                cwd=tmp_path,
                commit_message="fix: correct add function",
                diff_text=diff_text,
            )

        assert result.status == FixvalStatus.PASS
        # Verify code was restored
        assert "return a + b" in code_file.read_text()


# ---- run_overfit_guard tests ----


class TestRunOverfitGuard:
    """D-03: overfit guard is ADVISORY, never blocking."""

    def test_rename_breaks_test_emits_advisory(self, tmp_path):
        # Create a .py file with a local variable
        src_file = tmp_path / "src" / "module.py"
        src_file.parent.mkdir(parents=True, exist_ok=True)
        src_file.write_text(
            "def compute():\n"
            "    result = 42\n"
            "    return result\n"
        )
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
        src_file.write_text(
            "def compute():\n"
            "    result = 42\n"
            "    return result\n"
        )
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
