# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""End-to-end integration tests for baseline + snapshot + source_hash."""

import subprocess
from pathlib import Path

import pytest

from code_forge.baseline import (
    EmptyBaseline,
    GitRefBaseline,
    SnapshotBaseline,
    resolve_baseline,
)
from code_forge.snapshot import (
    Snapshot,
    SnapshotEntry,
    _hash_file,
    find_existing_snapshot,
    save_snapshot,
    snapshot_path_for,
    validate_snapshot,
)
from code_forge.source import compute_source_hash


@pytest.fixture
def git_repo(tmp_path):
    """Create a temporary git repo with tracked + untracked files."""
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
    # Tracked file
    tracked = tmp_path / "main.py"
    tracked.write_text("def main():\n    pass\n")
    subprocess.run(
        ["git", "add", "main.py"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    return tmp_path


class TestGitRepoWorkingTreeIntegration:
    """(a) git repo, --baseline HEAD --head WORKING with mixed files."""

    def test_tracked_and_untracked_in_diff(self, git_repo):
        # Modify tracked file
        (git_repo / "main.py").write_text("def main():\n    return 1\n")
        # Add untracked file
        (git_repo / "helper.py").write_text("def helper():\n    pass\n")

        result = resolve_baseline(
            GitRefBaseline(ref="HEAD"),
            GitRefBaseline(ref="WORKING"),
            [Path(".")],
            git_repo,
        )
        assert result.mode_hint == "git"
        assert result.git_diff is not None
        # Both tracked change and untracked file should appear
        assert "return 1" in result.git_diff
        assert "helper" in result.git_diff

    def test_source_hash_from_git_diff(self, git_repo):
        (git_repo / "main.py").write_text("def main():\n    return 1\n")
        result = resolve_baseline(
            GitRefBaseline(ref="HEAD"),
            GitRefBaseline(ref="WORKING"),
            [Path(".")],
            git_repo,
        )
        h = compute_source_hash(git_diff=result.git_diff)
        assert len(h) == 64


class TestNonGitFirstRun:
    """(b) non-git dir first run, PASS -> snapshot created."""

    def test_first_run_empty_then_save(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        (project / "a.py").write_text('print("a")\n')
        (project / "b.py").write_text('print("b")\n')

        # First run: EmptyBaseline
        result = resolve_baseline(
            EmptyBaseline(),
            None,
            [Path(".")],
            project,
        )
        assert result.mode_hint == "non-git"
        assert result.baseline_content is None

        # Simulate PASS: save snapshot
        files = [project / "a.py", project / "b.py"]
        source_h = compute_source_hash(files=files)
        snap = Snapshot(
            source_hash=source_h,
            files=[
                SnapshotEntry(
                    path=f.relative_to(project).as_posix(),
                    content_hash=_hash_file(f),
                )
                for f in files
            ],
        )
        path = snapshot_path_for(source_h, project)
        save_snapshot(snap, path)
        assert path.exists()


class TestNonGitSnapshotResume:
    """(c) non-git dir second run, snapshot loaded as baseline."""

    def test_second_run_loads_snapshot(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        (project / "a.py").write_text('print("a")\n')
        (project / "b.py").write_text('print("b")\n')

        files = [project / "a.py", project / "b.py"]
        source_h = compute_source_hash(files=files)
        snap = Snapshot(
            source_hash=source_h,
            files=[
                SnapshotEntry(
                    path=f.relative_to(project).as_posix(),
                    content_hash=_hash_file(f),
                )
                for f in files
            ],
            finding_dispositions={"fp1": "ACCEPT"},
        )
        path = snapshot_path_for(source_h, project)
        save_snapshot(snap, path)

        # Auto-detect snapshot
        found = find_existing_snapshot(source_h, project)
        assert found is not None

        # Resolve with snapshot
        result = resolve_baseline(
            SnapshotBaseline(path=found),
            None,
            [Path(".")],
            project,
        )
        assert result.mode_hint == "non-git"
        assert result.baseline_content is not None
        snap_data = result.baseline_content["snapshot"]
        assert snap_data["finding_dispositions"] == {"fp1": "ACCEPT"}


class TestNonGitPartialInvalidation:
    """(d) non-git dir with one file edited -> partial invalidation."""

    def test_one_file_edited(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        (project / "a.py").write_text('print("a")\n')
        (project / "b.py").write_text('print("b")\n')

        files = [project / "a.py", project / "b.py"]
        snap = Snapshot(
            source_hash="orig",
            files=[
                SnapshotEntry(
                    path=f.relative_to(project).as_posix(),
                    content_hash=_hash_file(f),
                )
                for f in files
            ],
        )

        # Edit one file
        (project / "b.py").write_text('print("b modified")\n')

        result = validate_snapshot(snap, files, project)
        assert result.unchanged == ["a.py"]
        assert result.changed == ["b.py"]
        assert result.missing == []
        assert result.added == []

    def test_source_hash_changes_on_edit(self, tmp_path):
        """SC-11: edit changes source_hash -> stale state detection."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "a.py").write_text('print("a")\n')
        files = [project / "a.py"]

        h1 = compute_source_hash(files=files)
        (project / "a.py").write_text('print("a modified")\n')
        h2 = compute_source_hash(files=files)
        assert h1 != h2
