# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Cross-repo merge review orchestration.

Provides diff acquisition, joint context assembly, and per-repo
isolation utilities.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml

from .diff import get_changed_files
from .git import git_diff, resolve_git_ref


def get_sibling_diff(repo_path: Path, ref_spec: str) -> str:
    """Acquire unified diff for a sibling repo.

    Args:
        repo_path: resolved absolute path to sibling repo dir.
        ref_spec: "baseline..head" format (e.g. "main..feature-x").

    Returns:
        Unified diff string (empty string if no changes).

    Raises:
        ValueError: if ref_spec format is wrong.
        BaselineResolutionError: if a ref does not exist (from git.py).
            Never caught here -- caller sees it directly (fail-closed).
    """
    if ".." not in ref_spec or "..." in ref_spec:
        raise ValueError(
            "ref_spec must be 'baseline..head', got: %r" % ref_spec
        )
    baseline_ref, head_ref = ref_spec.split("..", 1)
    if not baseline_ref or not head_ref:
        raise ValueError(
            "ref_spec must be 'baseline..head', got: %r" % ref_spec
        )
    from .gate_check import _validate_ref_part

    _validate_ref_part("baseline", baseline_ref, "ref_spec")
    _validate_ref_part("head", head_ref, "ref_spec")
    resolve_git_ref(baseline_ref, repo_path)
    resolve_git_ref(head_ref, repo_path)
    return git_diff(baseline_ref, head_ref, [], repo_path)


def build_cross_repo_context(
    repos: list[dict],
) -> str:
    """Assemble joint review context string from per-repo diffs.

    Args:
        repos: list of {"label": str, "ref": str, "diff": str} dicts.

    Returns:
        A string with summary header, per-repo stats, and labeled diff
        blocks.  Empty string if repos list is empty.
    """
    if not repos:
        return ""

    # Summary header
    header = "Cross-repo review: " + " + ".join(
        "%s (%s)" % (r["label"], r["ref"]) for r in repos
    )

    # Per-repo stats and blocks
    stats_lines = []
    blocks = []
    for r in repos:
        label = r["label"]
        ref = r["ref"]
        diff = r["diff"]

        if diff:
            lines = diff.splitlines()
            files_changed = sum(
                1 for line in lines if line.startswith("diff --git ")
            )
            added = sum(
                1 for line in lines
                if line.startswith("+") and not line.startswith("+++")
            )
            removed = sum(
                1 for line in lines
                if line.startswith("-") and not line.startswith("---")
            )
            stats_lines.append(
                "%s: %d file%s changed, +%d/-%d"
                % (
                    label,
                    files_changed,
                    "s" if files_changed != 1 else "",
                    added,
                    removed,
                )
            )
            blocks.append(
                "## Repo: [%s] (%s)\n%s\n" % (label, ref, diff)
            )
        else:
            stats_lines.append("%s: no changes" % label)
            blocks.append(
                "## Repo: [%s] (%s)\n(no changes)\n" % (label, ref)
            )

    return (
        header + "\n"
        + "\n".join(stats_lines) + "\n"
        + "\n".join(blocks)
    )


def make_per_repo_cwd(
    label: str,
    gate_config: dict | None = None,
) -> Path:
    """Create isolated .code-forge/ work dir for a single StateMachine thread.

    Args:
        label: identifier used in the temp dir prefix.
        gate_config: if provided, written as gate.yaml into the
            .code-forge/ subdir (eliminates TOCTOU on disk re-read).

    Returns:
        Path to the temp dir.  Caller must clean up via
        shutil.rmtree or tempfile.TemporaryDirectory.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="forge-cross-%s-" % label))
    code_forge_dir = tmp_dir / ".code-forge"
    code_forge_dir.mkdir()
    if gate_config is not None:
        (code_forge_dir / "gate.yaml").write_text(
            yaml.safe_dump(gate_config, default_flow_style=False)
        )
    return tmp_dir


def derive_source_files(
    repo_path: Path,
    per_repo_diff: str,
) -> list[Path]:
    """Derive changed-file list from a per-repo diff as absolute paths.

    Args:
        repo_path: absolute path to the repo root.
        per_repo_diff: unified diff text for this repo.

    Returns:
        List of absolute Path objects for each changed file.
        Empty list when per_repo_diff is empty (no changes).
    """
    if not per_repo_diff:
        return []
    rel_files = get_changed_files(per_repo_diff)
    return [Path(repo_path / f).resolve() for f in rel_files]
