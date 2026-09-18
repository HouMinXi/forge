# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for pseudo-ref resolution, is_git_repo, resolve_git_ref."""

import subprocess
import warnings
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from code_forge.errors import BaselineResolutionError
from code_forge.git import (
    INDEX,
    WORKING,
    _is_likely_binary,
    cached_diff,
    git_diff,
    is_git_repo,
    is_pseudo_ref,
    resolve_git_ref,
    working_tree_diff,
)


@pytest.fixture
def git_repo(tmp_path):
    """Create a temporary git repo with one initial commit."""
    subprocess.run(
        ["git", "init"], cwd=tmp_path,
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    # Create initial commit
    tracked = tmp_path / "tracked.py"
    tracked.write_text("print('hello')\n")
    subprocess.run(
        ["git", "add", "tracked.py"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    return tmp_path


class TestIsPseudoRef:
    def test_working_is_pseudo(self):
        assert is_pseudo_ref(WORKING) is True

    def test_index_is_pseudo(self):
        assert is_pseudo_ref(INDEX) is True

    def test_head_is_not_pseudo(self):
        assert is_pseudo_ref("HEAD") is False

    def test_branch_is_not_pseudo(self):
        assert is_pseudo_ref("main") is False


class TestIsGitRepo:
    """SC-5."""

    def test_inside_repo(self, git_repo):
        assert is_git_repo(git_repo) is True

    def test_outside_repo(self, tmp_path):
        non_repo = tmp_path / "not_a_repo"
        non_repo.mkdir()
        assert is_git_repo(non_repo) is False

    @patch("code_forge.git.subprocess.run")
    def test_invokes_rev_parse_git_dir(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout=".git\n", stderr="")
        assert is_git_repo(tmp_path) is True
        mock_run.assert_called_once_with(
            ["git", "rev-parse", "--git-dir"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    @patch("code_forge.git.subprocess.run", side_effect=FileNotFoundError)
    def test_missing_git_is_not_a_repo(self, mock_run, tmp_path):
        assert is_git_repo(tmp_path) is False


class TestResolveGitRef:
    """SC-5."""

    def test_valid_ref_returns_sha(self, git_repo):
        sha = resolve_git_ref("HEAD", git_repo)
        assert len(sha) == 40
        assert all(c in "0123456789abcdef" for c in sha)

    def test_unknown_ref_raises(self, git_repo):
        with pytest.raises(BaselineResolutionError) as caught:
            resolve_git_ref("nonexistent-ref", git_repo)
        assert "git ref 'nonexistent-ref' does not resolve" in str(caught.value)
        assert str(git_repo) in str(caught.value)

    @patch("code_forge.git.subprocess.run")
    def test_invokes_rev_parse_verify_commit(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
            stderr="",
        )
        sha = resolve_git_ref("HEAD", tmp_path)
        assert sha == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        mock_run.assert_called_once_with(
            ["git", "rev-parse", "--verify", "HEAD^{commit}"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )


class TestIsLikelyBinary:
    def test_text_file(self, tmp_path):
        f = tmp_path / "text.py"
        f.write_text("hello world\n")
        assert _is_likely_binary(f) is False

    def test_binary_file(self, tmp_path):
        f = tmp_path / "data.bin"
        f.write_bytes(b"\x00\x01\x02\x03")
        assert _is_likely_binary(f) is True

    def test_unreadable_file(self, tmp_path):
        f = tmp_path / "nope"
        # Non-existent file
        assert _is_likely_binary(f) is False

    def test_null_after_8kib_is_still_text(self, tmp_path):
        f = tmp_path / "tail.bin"
        f.write_bytes(b"a" * 8192 + b"\x00")
        assert _is_likely_binary(f) is False


class TestWorkingTreeDiff:
    """SC-3: WORKING pseudo-ref includes tracked + untracked."""

    def test_tracked_changes_included(self, git_repo):
        tracked = git_repo / "tracked.py"
        tracked.write_text("print('modified')\n")
        diff = working_tree_diff("HEAD", [Path(".")], git_repo)
        assert "modified" in diff

    def test_untracked_text_included(self, git_repo):
        untracked = git_repo / "new_file.py"
        untracked.write_text("print('untracked')\n")
        diff = working_tree_diff("HEAD", [Path(".")], git_repo)
        assert "untracked" in diff

    def test_gitignored_excluded(self, git_repo):
        gitignore = git_repo / ".gitignore"
        gitignore.write_text("ignored.py\n")
        subprocess.run(
            ["git", "add", ".gitignore"],
            cwd=git_repo, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "add gitignore"],
            cwd=git_repo, capture_output=True, check=True,
        )
        ignored = git_repo / "ignored.py"
        ignored.write_text("should be ignored\n")
        diff = working_tree_diff("HEAD", [Path(".")], git_repo)
        assert "should be ignored" not in diff

    def test_binary_untracked_skipped_with_warning(self, git_repo):
        """H2: binary untracked files skipped with warning."""
        binary_f = git_repo / "image.bin"
        binary_f.write_bytes(b"\x89PNG\x00\x01\x02\x03")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            working_tree_diff("HEAD", [Path(".")], git_repo)
            binary_warns = [
                x for x in w
                if "binary untracked" in str(x.message)
            ]
            assert len(binary_warns) == 1
            assert str(binary_warns[0].message) == (
                "forge: skipped 1 binary untracked file(s) from "
                "working-tree diff: ['image.bin']"
            )

    @patch("code_forge.git.warnings.warn")
    def test_binary_skip_warns_at_stacklevel_two(self, mock_warn, git_repo):
        (git_repo / "image.bin").write_bytes(b"\x00data")
        working_tree_diff("HEAD", [Path(".")], git_repo)
        assert mock_warn.call_args.kwargs["stacklevel"] == 2

    def test_binary_skip_lists_three_then_ellipsis(self, git_repo):
        for name in ("a.bin", "b.bin", "c.bin", "d.bin"):
            (git_repo / name).write_bytes(b"\x00data")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            working_tree_diff("HEAD", [Path(".")], git_repo)
        messages = [
            str(item.message)
            for item in caught
            if "binary untracked" in str(item.message)
        ]
        assert messages == [
            (
                "forge: skipped 4 binary untracked file(s) from "
                "working-tree diff: ['a.bin', 'b.bin', 'c.bin']..."
            )
        ]

    def test_three_binaries_have_no_ellipsis(self, git_repo):
        for name in ("a.bin", "b.bin", "c.bin"):
            (git_repo / name).write_bytes(b"\x00data")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            working_tree_diff("HEAD", [Path(".")], git_repo)
        messages = [
            str(item.message)
            for item in caught
            if "binary untracked" in str(item.message)
        ]
        assert messages == [
            (
                "forge: skipped 3 binary untracked file(s) from "
                "working-tree diff: ['a.bin', 'b.bin', 'c.bin']"
            )
        ]

    def test_skips_every_binary_untracked_file(self, git_repo):
        (git_repo / "first.bin").write_bytes(b"\x00one")
        (git_repo / "second.bin").write_bytes(b"\x00two")
        (git_repo / "keep.py").write_text("print('keep')\n")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            diff = working_tree_diff("HEAD", [Path(".")], git_repo)
        assert "keep" in diff
        assert "print('keep')" in diff
        message = str(caught[0].message)
        assert "first.bin" in message
        assert "second.bin" in message

    def test_bogus_baseline_ref_raises(self, git_repo):
        """R3-1: bogus baseline ref -> exit 128 -> BaselineResolutionError."""
        with pytest.raises(BaselineResolutionError) as caught:
            working_tree_diff("bogus-ref-xyz", [Path(".")], git_repo)
        text = str(caught.value)
        assert "git diff bogus-ref-xyz (tracked, working_tree_diff) failed" in text
        assert "exit 128" in text

    @patch("code_forge.git.subprocess.run")
    def test_tracked_diff_uses_tolerant_decode(self, mock_run, tmp_path):
        mock_run.side_effect = [
            MagicMock(returncode=1, stdout="diff --git a/f b/f\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        out = working_tree_diff("HEAD", [Path(".")], tmp_path)
        assert out == "diff --git a/f b/f\n"
        assert mock_run.call_args_list[0] == call(
            ["git", "diff", "HEAD", "--", "."],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        assert mock_run.call_args_list[1] == call(
            ["git", "ls-files", "--others", "--exclude-standard", "--", "."],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )

    @patch("code_forge.git._is_likely_binary", return_value=False)
    @patch("code_forge.git.subprocess.run")
    def test_untracked_uses_no_index_and_newline_join(
        self, mock_run, _mock_binary, tmp_path
    ):
        first = tmp_path / "a.py"
        second = tmp_path / "b.py"
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="tracked\n", stderr=""),
            MagicMock(returncode=0, stdout="a.py\nb.py\n", stderr=""),
            MagicMock(returncode=1, stdout="U1", stderr=""),
            MagicMock(returncode=1, stdout="U2", stderr=""),
        ]
        out = working_tree_diff("HEAD", [Path(".")], tmp_path)
        assert out == "tracked\nU1\nU2"
        assert mock_run.call_args_list[2] == call(
            ["git", "diff", "--no-index", "/dev/null", str(first)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        assert mock_run.call_args_list[3] == call(
            ["git", "diff", "--no-index", "/dev/null", str(second)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    @patch("code_forge.git._is_likely_binary", return_value=False)
    @patch("code_forge.git.subprocess.run")
    def test_untracked_no_index_exit_zero_is_kept(
        self, mock_run, _mock_binary, tmp_path
    ):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="a.py\n", stderr=""),
            MagicMock(returncode=0, stdout="U0", stderr=""),
        ]
        out = working_tree_diff("HEAD", [Path(".")], tmp_path)
        assert out == "U0"

    @patch("code_forge.git.subprocess.run")
    def test_untracked_no_index_failure_raises(self, mock_run, tmp_path):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="keep.py\n", stderr=""),
            MagicMock(returncode=2, stdout="", stderr="fatal: no-index"),
        ]
        (tmp_path / "keep.py").write_text("x\n")
        with pytest.raises(BaselineResolutionError) as caught:
            working_tree_diff("HEAD", [Path(".")], tmp_path)
        text = str(caught.value)
        assert "git diff --no-index failed for untracked file keep.py" in text
        assert "exit 2" in text
        assert "fatal: no-index" in text

    def test_empty_paths_still_diff_dot(self, git_repo):
        tracked = git_repo / "tracked.py"
        tracked.write_text("print('modified')\n")
        diff = working_tree_diff("HEAD", [], git_repo)
        assert "modified" in diff


class TestCachedDiff:
    """SC-4: INDEX pseudo-ref = staged only."""

    def test_staged_changes(self, git_repo):
        f = git_repo / "tracked.py"
        f.write_text("print('staged')\n")
        subprocess.run(
            ["git", "add", "tracked.py"],
            cwd=git_repo, capture_output=True, check=True,
        )
        diff = cached_diff("HEAD", [Path(".")], git_repo)
        assert "staged" in diff

    def test_unstaged_not_included(self, git_repo):
        f = git_repo / "tracked.py"
        f.write_text("print('unstaged')\n")
        diff = cached_diff("HEAD", [Path(".")], git_repo)
        # No staged changes, diff should be empty
        assert diff.strip() == ""

    def test_bogus_baseline_raises(self, git_repo):
        """R3-1: exit 2+ raises BaselineResolutionError."""
        with pytest.raises(BaselineResolutionError) as caught:
            cached_diff("bogus-ref-xyz", [Path(".")], git_repo)
        text = str(caught.value)
        assert "git diff --cached bogus-ref-xyz failed" in text
        assert "exit 128" in text

    @patch("code_forge.git.subprocess.run")
    def test_invokes_cached_diff_argv(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        cached_diff("HEAD", [Path(".")], tmp_path)
        mock_run.assert_called_once_with(
            ["git", "diff", "--cached", "HEAD", "--", "."],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    @patch("code_forge.git.subprocess.run")
    def test_cached_diff_exit_one_is_a_diff(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="diff --git a/f b/f\n", stderr=""
        )
        assert cached_diff("HEAD", [Path(".")], tmp_path) == (
            "diff --git a/f b/f\n"
        )

    @patch("code_forge.git.subprocess.run")
    def test_cached_diff_exit_two_raises(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=2, stdout="", stderr="usage: git diff"
        )
        with pytest.raises(BaselineResolutionError) as caught:
            cached_diff("HEAD", [Path(".")], tmp_path)
        assert "exit 2" in str(caught.value)


class TestGitDiff:
    """SC-4: regular two-ref diff."""

    def test_two_ref_diff(self, git_repo):
        f = git_repo / "tracked.py"
        f.write_text("print('v2')\n")
        subprocess.run(
            ["git", "add", "tracked.py"],
            cwd=git_repo, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "v2"],
            cwd=git_repo, capture_output=True, check=True,
        )
        diff = git_diff("HEAD~1", "HEAD", [Path(".")], git_repo)
        assert "v2" in diff

    def test_no_diff_returns_empty(self, git_repo):
        diff = git_diff("HEAD", "HEAD", [Path(".")], git_repo)
        assert diff.strip() == ""

    def test_bogus_ref_raises(self, git_repo):
        with pytest.raises(BaselineResolutionError) as caught:
            git_diff("bogus-ref", "HEAD", [Path(".")], git_repo)
        text = str(caught.value)
        assert "git diff bogus-ref..HEAD failed" in text
        assert "exit 128" in text

    @patch("code_forge.git.subprocess.run")
    def test_invokes_two_ref_argv(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=1, stdout="d\n", stderr="")
        out = git_diff("HEAD~1", "HEAD", [Path(".")], tmp_path)
        assert out == "d\n"
        mock_run.assert_called_once_with(
            ["git", "diff", "HEAD~1", "HEAD", "--", "."],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    @patch("code_forge.git.subprocess.run")
    def test_exit_two_is_an_error(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=2, stdout="", stderr="usage: git diff"
        )
        with pytest.raises(BaselineResolutionError) as caught:
            git_diff("HEAD", "HEAD", [Path(".")], tmp_path)
        assert "exit 2" in str(caught.value)
