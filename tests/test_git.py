# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for forge.git -- git subprocess wrapper with diff-spec validation."""

from unittest.mock import patch, MagicMock

import pytest

from pathlib import Path

from code_forge.git import validate_diff_spec, run_git_diff, git_blame


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

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
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
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        assert result == "diff --git a/f.py b/f.py\n"

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_returns_diff_text_on_success(self, mock_which, mock_run):
        """Returns diff text string on exit 1 (differences found)."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="diff output here",
            stderr="",
        )
        assert run_git_diff("HEAD") == "diff output here"

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_returns_empty_on_no_diff(self, mock_which, mock_run):
        """Returns empty string on exit 0 (no differences)."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="",
        )
        assert run_git_diff("HEAD") == ""

    @patch("code_forge.git.shutil.which", return_value=None)
    def test_raises_when_git_unavailable(self, mock_which):
        """Raises RuntimeError when git is not found."""
        with pytest.raises(RuntimeError, match="git not found"):
            run_git_diff("HEAD")

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_raises_on_fatal_error(self, mock_which, mock_run):
        """Raises RuntimeError on exit 128+ (fatal git error)."""
        mock_run.return_value = MagicMock(
            returncode=128,
            stdout="",
            stderr="fatal: not a git repository",
        )
        with pytest.raises(RuntimeError, match="not a git repository"):
            run_git_diff("HEAD")

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
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
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
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
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_unexpected_exit_code_raises(self, mock_which, mock_run):
        """Exit codes other than 0 or 1 raise RuntimeError."""
        mock_run.return_value = MagicMock(
            returncode=2,
            stdout="partial output",
            stderr="error details",
        )
        with pytest.raises(RuntimeError, match="error details"):
            run_git_diff("HEAD")

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_negative_exit_code_raises(self, mock_which, mock_run):
        """Negative exit codes (signal kill) raise RuntimeError."""
        mock_run.return_value = MagicMock(
            returncode=-9,
            stdout="",
            stderr="",
        )
        with pytest.raises(RuntimeError, match="git diff failed"):
            run_git_diff("HEAD")


class TestRunGitDiffUndecodableBytes:
    """Decode-class regression (real git, no mocks).

    Child output that is not valid UTF-8 must come back with
    replacement characters, never raise.  Without an explicit
    encoding, subprocess decodes with the locale codec and a strict
    error handler -- the crash class this suite pins shut.
    """

    def test_diff_survives_non_utf8_bytes(self, tmp_path, monkeypatch):
        import subprocess as sp

        sp.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        target = tmp_path / "gbk.txt"
        # CJK content encoded as GBK: high-bit byte sequences that
        # are NOT valid UTF-8 (\u escapes only, no raw non-ASCII).
        target.write_bytes("\u4e2d\u6587\u6ce8\u91ca".encode("gbk"))
        sp.run(["git", "add", "."], cwd=tmp_path, check=True)
        sp.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
             "commit", "-q", "-m", "seed"],
            cwd=tmp_path, check=True,
        )
        target.write_bytes("\u66f4\u591a\u4e2d\u6587".encode("gbk"))
        monkeypatch.chdir(tmp_path)
        out = run_git_diff("HEAD")
        assert "gbk.txt" in out
        # errors="replace" turns the undecodable bytes into U+FFFD;
        # their presence proves the tolerant decode path ran.
        assert "\ufffd" in out


# ---- git_blame tests (Phase 21-01) ----


class TestGitBlame:
    """Tests for git_blame() porcelain parser (D-06: git.py is single owner)."""

    # Fixture: single-commit porcelain block (one line, full metadata)
    SIMPLE_PORCELAIN = (
        "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee 1 1 1\n"
        "author Alice\n"
        "author-mail <alice@example.com>\n"
        "author-time 1700000000\n"
        "author-tz +0000\n"
        "committer Alice\n"
        "committer-mail <alice@example.com>\n"
        "committer-time 1700000000\n"
        "committer-tz +0000\n"
        "summary fix: null check\n"
        "filename src/foo.py\n"
        "\tprint('hello')\n"
    )

    # Fixture: two lines with same SHA -- second block has no author/summary
    DEDUP_PORCELAIN = (
        "5040f17eaabbccdd0011223344556677aabbccdd 1 1 2\n"
        "author Bob\n"
        "author-mail <bob@example.com>\n"
        "author-time 1700000000\n"
        "author-tz +0000\n"
        "committer Bob\n"
        "committer-mail <bob@example.com>\n"
        "committer-time 1700000000\n"
        "committer-tz +0000\n"
        "summary refactor: extract helper\n"
        "filename src/bar.py\n"
        "\tdef helper():\n"
        "5040f17eaabbccdd0011223344556677aabbccdd 2 2\n"
        "filename src/bar.py\n"
        "\t    return 42\n"
    )

    # Fixture: staged/uncommitted line (SHA = 40 zeros)
    # The "author Not Committed Yet" line is present (real git always emits it).
    # The "summary" line is absent (per plan spec: subject defaults to "").
    STAGED_PORCELAIN = (
        "0000000000000000000000000000000000000000 1 1 1\n"
        "author Not Committed Yet\n"
        "author-mail <not.committed.yet>\n"
        "author-time 1700000000\n"
        "author-tz +0000\n"
        "committer Not Committed Yet\n"
        "committer-mail <not.committed.yet>\n"
        "committer-time 1700000000\n"
        "committer-tz +0000\n"
        "filename src/baz.py\n"
        "\tnew_line = True\n"
    )

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_parses_simple(self, mock_which, mock_run):
        """Single commit block parsed correctly."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=self.SIMPLE_PORCELAIN,
        )
        result = git_blame("src/foo.py", Path("/repo"))
        assert result == {
            1: {
                "sha": "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee",
                "author": "Alice",
                "subject": "fix: null check",
            }
        }

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_dedup_sha(self, mock_which, mock_run):
        """Two lines with same SHA -- second block has no author/summary."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=self.DEDUP_PORCELAIN,
        )
        result = git_blame("src/bar.py", Path("/repo"))
        # Both lines must have correct author+subject from sha_cache
        assert result[1]["author"] == "Bob"
        assert result[1]["subject"] == "refactor: extract helper"
        assert result[2]["author"] == "Bob"
        assert result[2]["subject"] == "refactor: extract helper"
        assert result[1]["sha"] == "5040f17eaabbccdd0011223344556677aabbccdd"
        assert result[2]["sha"] == "5040f17eaabbccdd0011223344556677aabbccdd"

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_staged_line(self, mock_which, mock_run):
        """SHA = 0*40 -> staged entry with sentinel SHA and known author."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=self.STAGED_PORCELAIN,
        )
        result = git_blame("src/baz.py", Path("/repo"))
        assert result[1]["sha"] == "0" * 40
        assert result[1]["author"] == "Not Committed Yet"
        # summary line absent -> subject defaults to ""
        assert result[1]["subject"] == ""

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_returns_empty_on_nonzero(self, mock_which, mock_run):
        """Non-zero exit (e.g. binary file) -> returns {}."""
        mock_run.return_value = MagicMock(
            returncode=128,
            stdout="",
            stderr="fatal: no such path",
        )
        result = git_blame("binary.so", Path("/repo"))
        assert result == {}

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_returns_empty_for_missing_file(
        self, mock_which, mock_run
    ):
        """File path that does not exist -> returns {} (non-zero exit)."""
        mock_run.return_value = MagicMock(
            returncode=128,
            stdout="",
            stderr="fatal: no such path 'nonexistent.py'",
        )
        result = git_blame("nonexistent.py", Path("/repo"))
        assert result == {}

    def test_git_blame_exists(self):
        """git_blame is a callable in git.py (D-06: single git owner)."""
        assert callable(git_blame)
