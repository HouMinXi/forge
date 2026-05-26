# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for baseline resolution dispatch."""

import subprocess
from pathlib import Path

import pytest

from forge.baseline import (
    EmptyBaseline,
    GitRefBaseline,
    SnapshotBaseline,
    resolve_baseline,
    serialize_baseline_spec,
)
from forge.errors import BaselineResolutionError
from forge.snapshot import Snapshot, save_snapshot, snapshot_path_for


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


class TestBaselineSpecFrozen:
    """SC-1: 3 frozen dataclasses + isinstance dispatch."""

    def test_git_ref_frozen(self):
        spec = GitRefBaseline(ref="HEAD")
        with pytest.raises(AttributeError):
            spec.ref = "main"  # type: ignore[misc]

    def test_snapshot_frozen(self, tmp_path):
        spec = SnapshotBaseline(path=tmp_path / "snap.json")
        with pytest.raises(AttributeError):
            spec.path = tmp_path  # type: ignore[misc]

    def test_empty_frozen(self):
        spec = EmptyBaseline()
        with pytest.raises(AttributeError):
            spec.x = 1  # type: ignore[attr-defined]

    def test_isinstance_dispatch(self):
        specs = [
            GitRefBaseline(ref="HEAD"),
            SnapshotBaseline(path=Path("/tmp/x.json")),
            EmptyBaseline(),
        ]
        assert isinstance(specs[0], GitRefBaseline)
        assert isinstance(specs[1], SnapshotBaseline)
        assert isinstance(specs[2], EmptyBaseline)


class TestResolveBaseline:
    """SC-2: resolver dispatch per spec type."""

    def test_git_ref_baseline_in_repo(self, git_repo):
        """SC-2 + SC-3: GitRefBaseline dispatches to git diff."""
        result = resolve_baseline(
            GitRefBaseline(ref="HEAD"),
            None,
            [Path(".")],
            git_repo,
        )
        assert result.mode_hint == "git"
        assert result.git_diff is not None

    def test_empty_baseline_non_git(self, tmp_path):
        """SC-6: EmptyBaseline in non-git dir."""
        non_repo = tmp_path / "nongit"
        non_repo.mkdir()
        result = resolve_baseline(
            EmptyBaseline(),
            None,
            [Path(".")],
            non_repo,
        )
        assert result.mode_hint == "non-git"
        assert result.baseline_content is None
        assert result.git_diff is None

    def test_empty_baseline_in_git(self, git_repo):
        """SC-6: EmptyBaseline in git repo without head."""
        result = resolve_baseline(
            EmptyBaseline(),
            None,
            [Path(".")],
            git_repo,
        )
        assert result.mode_hint == "git"
        assert result.baseline_content is None
        assert result.git_diff is None

    def test_empty_baseline_with_head_in_git(self, git_repo):
        """B3: --baseline empty --head WORKING uses empty-tree sha."""
        result = resolve_baseline(
            EmptyBaseline(),
            GitRefBaseline(ref="WORKING"),
            [Path(".")],
            git_repo,
        )
        assert result.mode_hint == "git"
        assert result.git_diff is not None
        # Should contain the tracked file content as add
        assert "hello" in result.git_diff

    def test_snapshot_baseline_valid(self, tmp_path):
        """SC-7: SnapshotBaseline loads stored snapshot."""
        non_repo = tmp_path / "nongit"
        non_repo.mkdir()
        snap = Snapshot(source_hash="test123")
        path = snapshot_path_for("test123", non_repo)
        save_snapshot(snap, path)
        result = resolve_baseline(
            SnapshotBaseline(path=path),
            None,
            [Path(".")],
            non_repo,
        )
        assert result.mode_hint == "non-git"
        assert result.baseline_content is not None
        assert "snapshot" in result.baseline_content

    def test_snapshot_baseline_missing_falls_back(self, tmp_path):
        """SnapshotBaseline with missing file falls back to empty."""
        non_repo = tmp_path / "nongit"
        non_repo.mkdir()
        result = resolve_baseline(
            SnapshotBaseline(path=tmp_path / "nonexistent.json"),
            None,
            [Path(".")],
            non_repo,
        )
        assert result.mode_hint == "non-git"
        assert result.baseline_content is None

    def test_snapshot_with_head_raises(self, tmp_path):
        """M1: SnapshotBaseline + head_spec -> BaselineResolutionError."""
        with pytest.raises(
            BaselineResolutionError, match="does not accept head_spec"
        ):
            resolve_baseline(
                SnapshotBaseline(path=tmp_path / "s.json"),
                GitRefBaseline(ref="HEAD"),
                [Path(".")],
                tmp_path,
            )

    def test_git_ref_outside_repo_raises(self, tmp_path):
        """GitRefBaseline outside git repo -> error."""
        non_repo = tmp_path / "nongit"
        non_repo.mkdir()
        with pytest.raises(
            BaselineResolutionError, match="outside git repo"
        ):
            resolve_baseline(
                GitRefBaseline(ref="HEAD"),
                None,
                [Path(".")],
                non_repo,
            )

    def test_pseudo_ref_as_baseline_raises(self, git_repo):
        """Pseudo-ref as baseline -> BaselineResolutionError."""
        with pytest.raises(
            BaselineResolutionError, match="pseudo-ref"
        ):
            resolve_baseline(
                GitRefBaseline(ref="WORKING"),
                None,
                [Path(".")],
                git_repo,
            )

    def test_empty_with_head_non_git_raises(self, tmp_path):
        """EmptyBaseline + head_spec outside git -> error."""
        non_repo = tmp_path / "nongit"
        non_repo.mkdir()
        with pytest.raises(
            BaselineResolutionError, match="only valid in a git repo"
        ):
            resolve_baseline(
                EmptyBaseline(),
                GitRefBaseline(ref="HEAD"),
                [Path(".")],
                non_repo,
            )


class TestSerializeBaselineSpec:
    """OQ1: serialize_baseline_spec helper."""

    def test_git_ref(self):
        assert serialize_baseline_spec(
            GitRefBaseline(ref="HEAD")
        ) == "git:HEAD"

    def test_snapshot(self, tmp_path):
        p = tmp_path / "snap.json"
        assert serialize_baseline_spec(
            SnapshotBaseline(path=p)
        ).startswith("snapshot:")

    def test_empty(self):
        assert serialize_baseline_spec(EmptyBaseline()) == "empty"
