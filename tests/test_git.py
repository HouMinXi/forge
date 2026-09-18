# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for forge.git -- git subprocess wrapper with diff-spec validation."""

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from code_forge.git import (
    _BLOB_TEXT_ENCODING,
    _UNKNOWN_BLAME,
    git_blame,
    read_diff_blob,
    run_git_diff,
    validate_diff_spec,
)


class TestValidateDiffSpec:
    """Tests for validate_diff_spec()."""

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError, match=r"^diff_spec must not be empty$"):
            validate_diff_spec("")

    def test_rejects_leading_dash_flag_injection(self):
        with pytest.raises(ValueError) as caught:
            validate_diff_spec("--evil-flag")
        assert str(caught.value) == (
            "Invalid diff_spec: '--evil-flag' looks like a flag"
        )

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
        with pytest.raises(ValueError) as caught:
            validate_diff_spec("HEAD@{u}")
        assert str(caught.value) == (
            "Invalid diff_spec: 'HEAD@{u}' contains disallowed characters"
        )

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
        result = run_git_diff()
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
        with pytest.raises(RuntimeError, match=r"^git not found$"):
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
    """Tests for git_blame() porcelain parser (: git.py is single owner)."""

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
                "date": "2023-11-14",
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
        assert result[1]["date"] == "2023-11-14"
        assert result[2]["date"] == "2023-11-14"

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
    def test_git_blame_invokes_porcelain(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=self.SIMPLE_PORCELAIN,
        )
        git_blame("src/foo.py", Path("/repo"))
        mock_which.assert_called_with("git")
        mock_run.assert_called_once_with(
            ["git", "blame", "--porcelain", "--", "src/foo.py"],
            cwd=Path("/repo"),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=60,
        )

    @patch("code_forge.git.shutil.which", return_value=None)
    def test_git_blame_returns_empty_when_git_missing(self, mock_which):
        assert git_blame("src/foo.py", Path("/repo")) == {}
        mock_which.assert_called_with("git")

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_unknown_author_when_header_omits_it(
        self, mock_which, mock_run
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 1 1\n"
                "filename src/foo.py\n"
                "\tprint('hello')\n"
            ),
        )
        result = git_blame("src/foo.py", Path("/repo"))
        assert result[1] == {
            "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "author": "unknown",
            "subject": "",
            "date": "",
        }

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_unknown_fallback_is_not_shared(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 1 1\n"
                "filename src/foo.py\n"
                "\tprint('hello')\n"
            ),
        )
        first = git_blame("src/foo.py", Path("/repo"))
        first[1]["author"] = "mutated"
        assert _UNKNOWN_BLAME["author"] == "unknown"
        second = git_blame("src/foo.py", Path("/repo"))
        assert second[1]["author"] == "unknown"
        with pytest.raises(TypeError):
            _UNKNOWN_BLAME["author"] = "x"  # type: ignore[index]
        assert _UNKNOWN_BLAME["author"] == "unknown"

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_skips_non_numeric_final_line(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 not-a-line 1\n"
                "author Alice\n"
                "filename src/foo.py\n"
                "\tprint('hello')\n"
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 1 1\n"
                "author Alice\n"
                "filename src/foo.py\n"
                "\tprint('hello')\n"
            ),
        )
        result = git_blame("src/foo.py", Path("/repo"))
        assert result[1]["author"] == "Alice"
        assert list(result) == [1]
        assert 0 not in result

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_skipped_block_does_not_leak_into_the_next_one(
        self, mock_which, mock_run
    ):
        """A skipped block must not donate its line number or metadata.

        The next valid header resets skip_block and rebinds the line
        number, so a block that omits author/filename falls back to the
        unknown entry under its OWN final-line -- it does not inherit
        the broken block's author or index.
        """
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 not-a-line 1\n"
                "author Broken Author\n"
                "summary broken subject\n"
                "filename src/foo.py\n"
                "\tbad content\n"
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb 2 7 1\n"
                "\tgood content\n"
            ),
        )
        result = git_blame("src/foo.py", Path("/repo"))
        assert list(result) == [7]
        entry = result[7]
        assert entry["sha"].startswith("bbbb")
        assert entry["author"] == "unknown"
        assert entry["subject"] == ""

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_malformed_block_does_not_pollute_cache(
        self, mock_which, mock_run
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 1 1\n"
                "author Alice\n"
                "summary first\n"
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb 2 not-a-line 1\n"
                "author Eve\n"
                "summary spoof\n"
                "filename src/foo.py\n"
                "\tprint('dropped')\n"
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 3 3 1\n"
                "\tprint('two')\n"
            ),
        )
        result = git_blame("src/foo.py", Path("/repo"))
        assert 1 not in result
        assert 2 not in result
        assert result[3]["author"] == "unknown"
        assert result[3]["subject"] == ""

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_parses_second_block_after_content(
        self, mock_which, mock_run
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 1 1\n"
                "author Alice\n"
                "author-mail <alice@example.com>\n"
                "author-time 1700000000\n"
                "author-tz +0000\n"
                "committer Alice\n"
                "committer-mail <alice@example.com>\n"
                "committer-time 1700000000\n"
                "committer-tz +0000\n"
                "summary first\n"
                "filename src/foo.py\n"
                "\tprint('one')\n"
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb 2 2 1\n"
                "author Bob\n"
                "author-mail <bob@example.com>\n"
                "author-time 1700000000\n"
                "author-tz +0000\n"
                "committer Bob\n"
                "committer-mail <bob@example.com>\n"
                "committer-time 1700000000\n"
                "committer-tz +0000\n"
                "summary second\n"
                "filename src/foo.py\n"
                "\tprint('two')\n"
            ),
        )
        result = git_blame("src/foo.py", Path("/repo"))
        assert result[1]["author"] == "Alice"
        assert result[1]["subject"] == "first"
        assert result[2]["author"] == "Bob"
        assert result[2]["subject"] == "second"
        assert result[2]["sha"] == "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_tab_before_header_uses_line_zero(
        self, mock_which, mock_run
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="\tprint('orphan')\n",
        )
        result = git_blame("src/foo.py", Path("/repo"))
        assert result[0]["sha"] == ""
        assert result[0]["author"] == "unknown"
        assert 1 not in result
        assert list(result) == [0]

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_double_space_committer_time(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 1 1\n"
                "author Alice\n"
                "committer-time  1700000000\n"
                "summary first\n"
                "filename src/foo.py\n"
                "\tprint('hello')\n"
            ),
        )
        result = git_blame("src/foo.py", Path("/repo"))
        assert result[1]["date"] == "2023-11-14"

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_rejects_non_forty_hex_header(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 1 1\n"
                "filename src/foo.py\n"
                "\tprint('hello')\n"
            ),
        )
        result = git_blame("src/foo.py", Path("/repo"))
        assert result[0]["sha"] == ""
        assert 1 not in result

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_header_orig_line_need_not_be_hex(
        self, mock_which, mock_run
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa z 7 1\n"
                "author Alice\n"
                "summary first\n"
                "filename src/foo.py\n"
                "\tprint('hello')\n"
            ),
        )
        result = git_blame("src/foo.py", Path("/repo"))
        assert result[7]["author"] == "Alice"
        assert result[7]["sha"] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_empty_stdout_is_empty_map(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        assert git_blame("src/foo.py", Path("/repo")) == {}

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_timeout_returns_empty(self, mock_which, mock_run):
        import subprocess as sp

        mock_run.side_effect = sp.TimeoutExpired(cmd="git", timeout=60)
        assert git_blame("src/foo.py", Path("/repo")) == {}

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_oserror_returns_empty(self, mock_which, mock_run):
        mock_run.side_effect = OSError("boom")
        assert git_blame("src/foo.py", Path("/repo")) == {}

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_skips_blank_lines_before_content(
        self, mock_which, mock_run
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 1 1\n"
                "author Alice\n"
                "summary first\n"
                "filename src/foo.py\n"
                "\n"
                "\tprint('hello')\n"
            ),
        )
        result = git_blame("src/foo.py", Path("/repo"))
        assert result[1]["author"] == "Alice"

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_does_not_cache_until_filename(
        self, mock_which, mock_run
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 1 1\n"
                "author Alice\n"
                "summary first\n"
                "\tprint('hello')\n"
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 2 2 1\n"
                "filename src/foo.py\n"
                "\tprint('again')\n"
            ),
        )
        result = git_blame("src/foo.py", Path("/repo"))
        # A later SHA header resets the block, so Alice is never cached.
        assert result[1]["author"] == "unknown"
        assert result[2]["author"] == "unknown"

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_or_would_overwrite_cache(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 1 1\n"
                "author Alice\n"
                "summary first\n"
                "filename src/foo.py\n"
                "\tprint('one')\n"
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 2 2 1\n"
                "author Eve\n"
                "summary spoof\n"
                "filename src/foo.py\n"
                "\tprint('two')\n"
            ),
        )
        result = git_blame("src/foo.py", Path("/repo"))
        assert result[1]["author"] == "Alice"
        assert result[2]["author"] == "Alice"
        assert result[2]["subject"] == "first"

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_filename_without_sha_stays_unknown(
        self, mock_which, mock_run
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="filename src/foo.py\n\tprint('hello')\n",
        )
        result = git_blame("src/foo.py", Path("/repo"))
        assert result[0] == {
            "sha": "",
            "author": "unknown",
            "subject": "",
            "date": "",
        }

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_metadata_before_header_uses_empty_defaults(
        self, mock_which, mock_run
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "author Alice\n"
                "filename src/foo.py\n"
                "\tprint('hello')\n"
            ),
        )
        result = git_blame("src/foo.py", Path("/repo"))
        assert result[0] == {
            "sha": "",
            "author": "Alice",
            "subject": "",
            "date": "",
        }

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_header_without_author_stays_unknown(
        self, mock_which, mock_run
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 1 1\n"
                "filename src/foo.py\n"
                "\tprint('hello')\n"
            ),
        )
        result = git_blame("src/foo.py", Path("/repo"))
        assert result[1] == {
            "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "author": "unknown",
            "subject": "",
            "date": "",
        }

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_split_once_keeps_spaces_in_time(
        self, mock_which, mock_run
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 1 1\n"
                "author Alice\n"
                "committer-time 1700000000 extra\n"
                "summary first\n"
                "filename src/foo.py\n"
                "\tprint('hello')\n"
            ),
        )
        result = git_blame("src/foo.py", Path("/repo"))
        assert result[1]["date"] == ""

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_rsplit_would_drop_prefix_spaces(
        self, mock_which, mock_run
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 1 1\n"
                "author Alice\n"
                "committer-time 1700000000\n"
                "summary first\n"
                "filename src/foo.py\n"
                "\tprint('hello')\n"
            ),
        )
        result = git_blame("src/foo.py", Path("/repo"))
        assert result[1]["date"] == "2023-11-14"

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_split_none_collapses_spaces(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 1 1\n"
                "author Alice\n"
                "committer-time  1700000000 extra\n"
                "summary first\n"
                "filename src/foo.py\n"
                "\tprint('hello')\n"
            ),
        )
        result = git_blame("src/foo.py", Path("/repo"))
        assert result[1]["date"] == ""

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_split_two_keeps_suffix(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 1 1\n"
                "author Alice\n"
                "committer-time 1700000000 extra\n"
                "summary first\n"
                "filename src/foo.py\n"
                "\tprint('hello')\n"
            ),
        )
        result = git_blame("src/foo.py", Path("/repo"))
        assert result[1]["date"] == ""

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_break_on_content_drops_later_lines(
        self, mock_which, mock_run
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=self.DEDUP_PORCELAIN,
        )
        result = git_blame("src/bar.py", Path("/repo"))
        assert 1 in result
        assert 2 in result

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_default_line_is_zero(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="\tprint('orphan')\n",
        )
        result = git_blame("src/foo.py", Path("/repo"))
        assert list(result) == [0]

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_missing_cache_fields_use_defaults(
        self, mock_which, mock_run
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 1 1\n"
                "author Alice\n"
                "filename src/foo.py\n"
                "\tprint('hello')\n"
            ),
        )
        result = git_blame("src/foo.py", Path("/repo"))
        assert result[1]["subject"] == ""
        assert result[1]["date"] == ""
        assert result[1]["author"] == "Alice"

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_content_before_cache_uses_unknown(
        self, mock_which, mock_run
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 1 1\n"
                "\tprint('hello')\n"
            ),
        )
        result = git_blame("src/foo.py", Path("/repo"))
        assert result[1] == {
            "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "author": "unknown",
            "subject": "",
            "date": "",
        }

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_does_not_treat_g_as_hex(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "gaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 1 1\n"
                "filename src/foo.py\n"
                "\tprint('hello')\n"
            ),
        )
        result = git_blame("src/foo.py", Path("/repo"))
        assert result[0]["sha"] == ""

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_needs_three_header_fields(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1\n"
                "filename src/foo.py\n"
                "\tprint('hello')\n"
            ),
        )
        result = git_blame("src/foo.py", Path("/repo"))
        assert result[0]["sha"] == ""

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_author_prefix_is_seven_chars(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 1 1\n"
                "author Alice\n"
                "summary first\n"
                "filename src/foo.py\n"
                "\tprint('hello')\n"
            ),
        )
        result = git_blame("src/foo.py", Path("/repo"))
        assert result[1]["author"] == "Alice"

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_summary_prefix_is_eight_chars(
        self, mock_which, mock_run
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 1 1\n"
                "author Alice\n"
                "summary first line\n"
                "filename src/foo.py\n"
                "\tprint('hello')\n"
            ),
        )
        result = git_blame("src/foo.py", Path("/repo"))
        assert result[1]["subject"] == "first line"

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_filename_prefix_required(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 1 1\n"
                "author Alice\n"
                "summary first\n"
                "\tprint('hello')\n"
            ),
        )
        result = git_blame("src/foo.py", Path("/repo"))
        assert result[1]["author"] == "unknown"

    @patch("code_forge.git.subprocess.run")
    @patch("code_forge.git.shutil.which", return_value="/usr/bin/git")
    def test_git_blame_author_not_committed_yet(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=self.STAGED_PORCELAIN,
        )
        result = git_blame("src/baz.py", Path("/repo"))
        assert result[1]["author"] == "Not Committed Yet"
        assert result[1]["date"] == "2023-11-14"

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
        """git_blame is a callable in git.py (: single git owner)."""
        assert callable(git_blame)


def _ok_run(stdout="", returncode=0, stderr=""):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


class TestReadDiffBlob:
    """Pin blob-id syntax, git argv, size bound, and decode rules."""

    def test_blob_text_encoding_is_utf8_lowercase(self):
        assert _BLOB_TEXT_ENCODING == "utf-8"
        assert _BLOB_TEXT_ENCODING.islower()

    def test_rejects_non_string_and_zero_oid(self):
        root = Path("/repo")
        assert read_diff_blob(None, root) is None
        assert read_diff_blob(123, root) is None
        assert read_diff_blob("HEAD:CHANGELOG.md", root) is None
        assert read_diff_blob("--help", root) is None
        assert read_diff_blob("ABCDEF0", root) is None

    @patch("code_forge.git.subprocess.run")
    def test_all_zero_oid_does_not_call_git(self, mock_run):
        assert read_diff_blob("0" * 40, Path("/repo")) is None
        mock_run.assert_not_called()

    @patch("code_forge.git.subprocess.run")
    def test_uppercase_hex_is_rejected_by_the_charset_not_the_length(
        self, mock_run
    ):
        """Abbreviated OIDs are allowed; uppercase ones are not.

        The selector is a lowercase-only charset, so raising the length
        floor to 40 would not change any of these answers. A 7-char
        lowercase abbreviation is a legitimate git OID and stays valid.
        """
        root = Path("/repo")
        assert read_diff_blob("ABCDEF0", root) is None
        assert read_diff_blob("ABCDEF0" * 4, root) is None
        mock_run.assert_not_called()

    @patch("code_forge.git.subprocess.run")
    def test_zero_oid_rejected_at_every_accepted_length(self, mock_run):
        """The zero guard is length-relative, not hardcoded to 40."""
        for size in (7, 8, 40, 64):
            assert read_diff_blob("0" * size, Path("/repo")) is None
        mock_run.assert_not_called()

    @patch("code_forge.git.subprocess.run")
    def test_unparsable_size_returns_none_without_escaping(self, mock_run):
        """int() on junk size output must not escape as a ValueError.

        `git cat-file -s` can return empty or non-numeric text. The
        int() call sits inside the try, so each case degrades to None
        rather than propagating to the caller.
        """
        for junk in ("", "not-a-number\n", "   \n", "12 extra\n"):
            mock_run.reset_mock()
            mock_run.return_value = MagicMock(returncode=0, stdout=junk)
            assert read_diff_blob("abc1234", Path("/repo")) is None

    @patch("code_forge.git.subprocess.run")
    def test_reads_bounded_text_blob(self, mock_run):
        mock_run.side_effect = [
            _ok_run("12\n"),
            MagicMock(returncode=0, stdout=b"hello source\n"),
        ]
        root = Path("/repo")
        oid = "abc1234"
        assert read_diff_blob(oid, root) == "hello source\n"
        assert mock_run.call_args_list == [
            call(
                ["git", "--no-replace-objects", "cat-file", "-s", oid],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=5,
            ),
            call(
                ["git", "--no-replace-objects", "cat-file", "blob", oid],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=5,
            ),
        ]

    @patch("code_forge.git.subprocess.run")
    def test_rejects_oversized_and_binary(self, mock_run):
        root = Path("/repo")
        oid = "abc1234"
        mock_run.return_value = _ok_run("2000001\n")
        assert read_diff_blob(oid, root) is None
        mock_run.side_effect = [
            _ok_run("2000000\n"),
            MagicMock(returncode=0, stdout=b"x" * 2_000_000),
        ]
        assert read_diff_blob(oid, root) == "x" * 2_000_000
        mock_run.side_effect = None
        mock_run.side_effect = [
            _ok_run("4\n"),
            MagicMock(returncode=0, stdout=b"ab\x00c"),
        ]
        assert read_diff_blob(oid, root) is None

    @patch("code_forge.git.subprocess.run")
    def test_rejects_size_probe_failure(self, mock_run):
        mock_run.return_value = _ok_run("", returncode=128)
        assert read_diff_blob("abc1234", Path("/repo")) is None

    @patch("code_forge.git.subprocess.run")
    def test_empty_blob_is_allowed(self, mock_run):
        mock_run.side_effect = [
            _ok_run("0\n"),
            MagicMock(returncode=0, stdout=b""),
        ]
        assert read_diff_blob("abc1234", Path("/repo")) == ""

    @patch("code_forge.git.subprocess.run")
    def test_timeout_and_missing_git_return_none(self, mock_run):
        import subprocess as sp

        mock_run.side_effect = sp.TimeoutExpired(cmd="git", timeout=5)
        assert read_diff_blob("abc1234", Path("/repo")) is None
        mock_run.side_effect = FileNotFoundError
        assert read_diff_blob("abc1234", Path("/repo")) is None

    @patch("code_forge.git.subprocess.run")
    def test_rejects_non_integer_size(self, mock_run):
        mock_run.return_value = _ok_run("not-a-size\n")
        assert read_diff_blob("abc1234", Path("/repo")) is None

    @patch("code_forge.git.subprocess.run")
    def test_rejects_decode_error(self, mock_run):
        mock_run.side_effect = [
            _ok_run("3\n"),
            MagicMock(returncode=0, stdout=b"\xff\xfe\xfd"),
        ]
        assert read_diff_blob("abc1234", Path("/repo")) is None

    @patch("code_forge.git.subprocess.run")
    def test_rejects_blob_exit_nonzero(self, mock_run):
        mock_run.side_effect = [
            _ok_run("3\n"),
            MagicMock(returncode=128, stdout=b""),
        ]
        assert read_diff_blob("abc1234", Path("/repo")) is None

    @patch("code_forge.git.subprocess.run")
    def test_rejects_hex_with_dots_before_running_git(self, mock_run):
        assert read_diff_blob("abc.def", Path("/repo")) is None
        mock_run.assert_not_called()
