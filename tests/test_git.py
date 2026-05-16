# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for forge.git -- git subprocess wrapper with diff-spec validation."""

from unittest.mock import patch, MagicMock

import pytest

from forge.git import validate_diff_spec, run_git_diff


class TestValidateDiffSpec:
    """Tests for validate_diff_spec()."""

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError):
            validate_diff_spec("")

    def test_rejects_leading_dash_flag_injection(self):
        with pytest.raises(ValueError):
            validate_diff_spec("--evil-flag")

    def test_rejects_single_dash(self):
        with pytest.raises(ValueError):
            validate_diff_spec("-x")

    def test_accepts_staged(self):
        """--staged is a safe known flag."""
        assert validate_diff_spec("--staged") == "--staged"

    def test_accepts_cached(self):
        """--cached is a safe known flag."""
        assert validate_diff_spec("--cached") == "--cached"

    def test_accepts_head(self):
        assert validate_diff_spec("HEAD") == "HEAD"

    def test_accepts_head_tilde(self):
        assert validate_diff_spec("HEAD~1") == "HEAD~1"

    def test_accepts_head_caret(self):
        """Round 7 R7-L5: caret must be accepted."""
        assert validate_diff_spec("HEAD^") == "HEAD^"

    def test_accepts_commit_hash(self):
        assert validate_diff_spec("abc123") == "abc123"

    def test_accepts_commit_range(self):
        assert validate_diff_spec("abc123..def456") == "abc123..def456"

    def test_accepts_branch_name_with_slash(self):
        assert validate_diff_spec("feature/foo") == "feature/foo"

    def test_accepts_tag(self):
        assert validate_diff_spec("v1.2.3") == "v1.2.3"

    def test_accepts_remote_ref(self):
        assert validate_diff_spec("origin/main") == "origin/main"

    def test_accepts_at_sign(self):
        assert validate_diff_spec("HEAD@") == "HEAD@"

    def test_accepts_hyphen_in_branch(self):
        """Round 7 R7-L5: hyphen in branch name must be accepted."""
        assert validate_diff_spec("abc-def") == "abc-def"

    def test_rejects_curly_braces(self):
        """Curly-brace syntax not permitted."""
        with pytest.raises(ValueError):
            validate_diff_spec("HEAD@{u}")

    def test_rejects_backtick(self):
        with pytest.raises(ValueError):
            validate_diff_spec("HEAD`whoami`")

    def test_rejects_dollar(self):
        with pytest.raises(ValueError):
            validate_diff_spec("$HOME")

    def test_rejects_semicolon(self):
        with pytest.raises(ValueError):
            validate_diff_spec("HEAD;rm -rf /")

    def test_rejects_pipe(self):
        with pytest.raises(ValueError):
            validate_diff_spec("HEAD|cat")

    def test_rejects_ampersand(self):
        with pytest.raises(ValueError):
            validate_diff_spec("HEAD&")

    def test_rejects_space(self):
        with pytest.raises(ValueError):
            validate_diff_spec("HEAD --evil")


class TestRunGitDiff:
    """Tests for run_git_diff()."""

    @patch("forge.git.subprocess.run")
    @patch("forge.git.shutil.which", return_value="/usr/bin/git")
    def test_calls_subprocess_with_validated_spec(self, mock_which, mock_run):
        """Calls subprocess.run with validated diff_spec."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="diff --git a/f.py b/f.py\n",
            stderr="",
        )
        result = run_git_diff("HEAD")
        mock_run.assert_called_once_with(
            ["git", "diff", "-U0", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result == "diff --git a/f.py b/f.py\n"

    @patch("forge.git.subprocess.run")
    @patch("forge.git.shutil.which", return_value="/usr/bin/git")
    def test_returns_diff_text_on_success(self, mock_which, mock_run):
        """Returns diff text string on exit 1 (differences found)."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="diff output here",
            stderr="",
        )
        assert run_git_diff("HEAD") == "diff output here"

    @patch("forge.git.subprocess.run")
    @patch("forge.git.shutil.which", return_value="/usr/bin/git")
    def test_returns_empty_on_no_diff(self, mock_which, mock_run):
        """Returns empty string on exit 0 (no differences)."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="",
        )
        assert run_git_diff("HEAD") == ""

    @patch("forge.git.shutil.which", return_value=None)
    def test_raises_when_git_unavailable(self, mock_which):
        """Raises RuntimeError when git is not found."""
        with pytest.raises(RuntimeError, match="git not found"):
            run_git_diff("HEAD")

    @patch("forge.git.subprocess.run")
    @patch("forge.git.shutil.which", return_value="/usr/bin/git")
    def test_raises_on_fatal_error(self, mock_which, mock_run):
        """Raises RuntimeError on exit 128+ (fatal git error)."""
        mock_run.return_value = MagicMock(
            returncode=128,
            stdout="",
            stderr="fatal: not a git repository",
        )
        with pytest.raises(RuntimeError, match="not a git repository"):
            run_git_diff("HEAD")

    @patch("forge.git.subprocess.run")
    @patch("forge.git.shutil.which", return_value="/usr/bin/git")
    def test_staged_flag(self, mock_which, mock_run):
        """--staged flag works correctly."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="",
        )
        run_git_diff("--staged")
        mock_run.assert_called_once_with(
            ["git", "diff", "-U0", "--staged"],
            capture_output=True,
            text=True,
            check=False,
        )

    @patch("forge.git.subprocess.run")
    @patch("forge.git.shutil.which", return_value="/usr/bin/git")
    def test_extra_args(self, mock_which, mock_run):
        """extra_args are appended to command."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="",
        )
        run_git_diff("HEAD", extra_args=["--name-only"])
        mock_run.assert_called_once_with(
            ["git", "diff", "-U0", "HEAD", "--name-only"],
            capture_output=True,
            text=True,
            check=False,
        )
