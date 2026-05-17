# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for pseudo-ref resolution, is_git_repo, resolve_git_ref."""

import subprocess
import warnings

import pytest
from pathlib import Path

from forge.errors import BaselineResolutionError
from forge.git import (
    WORKING,
    INDEX,
    is_pseudo_ref,
    is_git_repo,
    resolve_git_ref,
    working_tree_diff,
    cached_diff,
    git_diff,
    _is_likely_binary,
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


class TestResolveGitRef:
    """SC-5."""

    def test_valid_ref_returns_sha(self, git_repo):
        sha = resolve_git_ref("HEAD", git_repo)
        assert len(sha) == 40
        assert all(c in "0123456789abcdef" for c in sha)

    def test_unknown_ref_raises(self, git_repo):
        with pytest.raises(BaselineResolutionError, match="nonexistent"):
            resolve_git_ref("nonexistent-ref", git_repo)


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
            assert "image.bin" in str(binary_warns[0].message)

    def test_bogus_baseline_ref_raises(self, git_repo):
        """R3-1: bogus baseline ref -> exit 128 -> BaselineResolutionError."""
        with pytest.raises(BaselineResolutionError):
            working_tree_diff("bogus-ref-xyz", [Path(".")], git_repo)


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
        with pytest.raises(BaselineResolutionError):
            cached_diff("bogus-ref-xyz", [Path(".")], git_repo)


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
        with pytest.raises(BaselineResolutionError):
            git_diff("bogus-ref", "HEAD", [Path(".")], git_repo)
