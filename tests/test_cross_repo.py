# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for cross-repo diff acquisition and context assembly.

Uses real git repos with tmp_path + monkeypatch GIT_CEILING_DIRECTORIES
to prevent test repos from leaking into the parent repo's git state.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from code_forge.cross_repo import (
    build_cross_repo_context,
    derive_source_files,
    get_sibling_diff,
    make_per_repo_cwd,
)
from code_forge.errors import BaselineResolutionError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a real git repo with main and feature branches.

    main:    file.py = "x = 1\\n"
    feature: file.py = "x = 2\\n"
    HEAD left on main after setup.
    """
    monkeypatch.setenv(
        "GIT_CEILING_DIRECTORIES",
        str(tmp_path.parent),
        prepend=os.pathsep,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *cmd: subprocess.run(  # noqa: E731
        list(cmd), cwd=repo, check=True,
        capture_output=True, text=True,
    )
    run("git", "init", "-b", "main")
    run("git", "config", "user.email", "test@test.com")
    run("git", "config", "user.name", "Test")

    (repo / "file.py").write_text("x = 1\n")
    run("git", "add", "file.py")
    run("git", "commit", "-m", "init")

    run("git", "checkout", "-b", "feature")
    (repo / "file.py").write_text("x = 2\n")
    run("git", "add", "file.py")
    run("git", "commit", "-m", "change")

    run("git", "checkout", "main")
    return repo


# ---------------------------------------------------------------------------
# get_sibling_diff
# ---------------------------------------------------------------------------


def test_get_sibling_diff_happy(git_repo: Path) -> None:
    """Diff between main..feature returns non-empty diff string."""
    diff = get_sibling_diff(git_repo, "main..feature")
    assert diff
    assert "file.py" in diff


def test_get_sibling_diff_no_changes(git_repo: Path) -> None:
    """Same ref both sides produces empty diff."""
    diff = get_sibling_diff(git_repo, "main..main")
    assert diff == ""


def test_get_sibling_diff_invalid_ref(git_repo: Path) -> None:
    """Unknown branch raises BaselineResolutionError (fail-closed)."""
    with pytest.raises(BaselineResolutionError):
        get_sibling_diff(git_repo, "main..nonexistent-branch")


def test_get_sibling_diff_bad_ref_format(git_repo: Path) -> None:
    """Ref without '..' raises ValueError."""
    with pytest.raises(ValueError, match="baseline[.][.]head"):
        get_sibling_diff(git_repo, "main-feature")


def test_get_sibling_diff_empty_baseline(git_repo: Path) -> None:
    """Ref with empty baseline (..head) raises ValueError."""
    with pytest.raises(ValueError, match="baseline[.][.]head"):
        get_sibling_diff(git_repo, "..feature")


def test_get_sibling_diff_empty_head(git_repo: Path) -> None:
    """Ref with empty head (baseline..) raises ValueError."""
    with pytest.raises(ValueError, match="baseline[.][.]head"):
        get_sibling_diff(git_repo, "main..")


# ---------------------------------------------------------------------------
# build_cross_repo_context
# ---------------------------------------------------------------------------


def test_build_cross_repo_context_summary_header() -> None:
    """Output starts with 'Cross-repo review:' summary line."""
    repos = [
        {"label": "primary", "ref": "main..feat", "diff": "diff --git a/f b/f\n"},
    ]
    result = build_cross_repo_context(repos)
    assert result.startswith("Cross-repo review:")


def test_build_cross_repo_context_labeled_blocks() -> None:
    """Each repo section has '## Repo: [label]' heading."""
    repos = [
        {"label": "primary", "ref": "main..feat-a", "diff": "diff --git a/f b/f\n"},
        {"label": "sibling", "ref": "main..feat-b", "diff": "diff --git a/g b/g\n"},
    ]
    result = build_cross_repo_context(repos)
    assert "## Repo: [primary]" in result
    assert "## Repo: [sibling]" in result


def test_build_cross_repo_context_empty_diff() -> None:
    """Empty diff produces '(no changes)' in the block."""
    repos = [
        {"label": "empty-repo", "ref": "main..main", "diff": ""},
    ]
    result = build_cross_repo_context(repos)
    assert "(no changes)" in result


def test_build_cross_repo_context_empty_repos() -> None:
    """Empty repos list returns empty string."""
    assert build_cross_repo_context([]) == ""


# ---------------------------------------------------------------------------
# make_per_repo_cwd
# ---------------------------------------------------------------------------


def test_make_per_repo_cwd_creates_dirs() -> None:
    """Returned path exists with .code-forge/ subdir."""
    cwd = make_per_repo_cwd("test-label")
    try:
        assert cwd.is_dir()
        assert (cwd / ".code-forge").is_dir()
    finally:
        import shutil
        shutil.rmtree(cwd, ignore_errors=True)


def test_make_per_repo_cwd_unique() -> None:
    """Two calls with same label return different paths."""
    cwd1 = make_per_repo_cwd("same")
    cwd2 = make_per_repo_cwd("same")
    try:
        assert cwd1 != cwd2
    finally:
        import shutil
        shutil.rmtree(cwd1, ignore_errors=True)
        shutil.rmtree(cwd2, ignore_errors=True)


def test_make_per_repo_cwd_seeds_gate_config() -> None:
    """When gate_config is provided, gate.yaml is written into .code-forge/."""
    config = {"test": {"command": ["pytest", "-q"]}, "outlet": "subprocess"}
    cwd = make_per_repo_cwd("seeded", gate_config=config)
    try:
        gate_path = cwd / ".code-forge" / "gate.yaml"
        assert gate_path.exists()
        loaded = yaml.safe_load(gate_path.read_text())
        assert loaded["outlet"] == "subprocess"
    finally:
        import shutil
        shutil.rmtree(cwd, ignore_errors=True)


# ---------------------------------------------------------------------------
# derive_source_files
# ---------------------------------------------------------------------------


def test_derive_source_files_absolute(tmp_path: Path) -> None:
    """Returns absolute paths resolved against repo_path."""
    diff_text = (
        "diff --git a/src/main.py b/src/main.py\n"
        "--- a/src/main.py\n"
        "+++ b/src/main.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    files = derive_source_files(tmp_path, diff_text)
    assert len(files) == 1
    assert files[0].is_absolute()
    assert str(files[0]).startswith(str(tmp_path))


def test_derive_source_files_empty_diff() -> None:
    """Empty diff returns empty list (not error)."""
    files = derive_source_files(Path("/tmp"), "")
    assert files == []


# ---------------------------------------------------------------------------
# Cross-validator: gate_check and cross_repo reject the same bad refs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_ref", [
    "main-feature",       # no ".."
    "main...feature",     # three dots
    "..feature",          # empty baseline
    "main..",             # empty head
    "-dash..feature",     # baseline starts with dash (option injection)
    "main..-dash",        # head starts with dash
    ".dot..feature",      # baseline starts with dot
    "main; rm -rf..head", # shell metacharacters in baseline
])
def test_ref_validation_consistent(
    tmp_path: Path,
    git_repo: Path,
    bad_ref: str,
) -> None:
    """Both validate_siblings() and get_sibling_diff() reject the same refs."""
    from code_forge.gate_check import validate_siblings

    gate_yaml_dir = tmp_path / ".code-forge"
    gate_yaml_dir.mkdir(exist_ok=True)
    siblings = [{"repo": str(git_repo), "ref": bad_ref}]

    with pytest.raises(ValueError):
        validate_siblings(siblings, gate_yaml_dir=gate_yaml_dir)

    with pytest.raises(ValueError):
        get_sibling_diff(git_repo, bad_ref)
