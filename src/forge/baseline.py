# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""BaselineSpec discriminated union + resolver.

Owned by 02-03. 02-05 CLI parser emits BaselineSpec from string args;
this module dispatches to the right resolution path.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional, Union

from .errors import BaselineResolutionError
from .git import (
    INDEX,
    WORKING,
    cached_diff,
    git_diff,
    is_git_repo,
    is_pseudo_ref,
    resolve_git_ref,
    working_tree_diff,
)
from .snapshot import load_snapshot


@dataclass(frozen=True)
class GitRefBaseline:
    """Baseline = git ref (HEAD, branch, commit sha, WORKING, INDEX)."""

    ref: str


@dataclass(frozen=True)
class SnapshotBaseline:
    """Baseline = stored .forge/snapshots/<source-hash>.json."""

    path: Path


@dataclass(frozen=True)
class EmptyBaseline:
    """Baseline = nothing. All current source content is new."""

    pass


BaselineSpec = Union[GitRefBaseline, SnapshotBaseline, EmptyBaseline]


@dataclass(frozen=True)
class ResolvedReview:
    """Concrete review subject + baseline content after resolution.

    Consumed by state machine (02-02) and source_hash (STATE-07).
    """

    source_files: list[Path]
    baseline_content: Optional[dict]
    git_diff: Optional[str]
    mode_hint: str  # "git" | "non-git"


def resolve_baseline(
    baseline_spec: BaselineSpec,
    head_spec: Optional["GitRefBaseline"],
    paths: list[Path],
    cwd: Path,
) -> ResolvedReview:
    """Resolve (baseline, head, paths) to concrete review subject.

    Raises:
        BaselineResolutionError: invalid combination.
    """
    if isinstance(baseline_spec, GitRefBaseline):
        return _resolve_git(baseline_spec, head_spec, paths, cwd)
    if isinstance(baseline_spec, SnapshotBaseline):
        if head_spec is not None:
            raise BaselineResolutionError(
                "SnapshotBaseline does not accept head_spec "
                "(got: %r); snapshot is its own implicit head"
                % (head_spec,)
            )
        return _resolve_snapshot(baseline_spec, paths, cwd)
    if isinstance(baseline_spec, EmptyBaseline):
        if head_spec is not None and not is_git_repo(cwd):
            raise BaselineResolutionError(
                "EmptyBaseline + head_spec is only valid in a git repo; "
                "cwd=%s" % cwd
            )
        return _resolve_empty(head_spec, paths, cwd)
    raise BaselineResolutionError(
        "unknown baseline spec type: %s" % type(baseline_spec)
    )


def _resolve_git(
    baseline_spec: GitRefBaseline,
    head_spec: Optional[GitRefBaseline],
    paths: list[Path],
    cwd: Path,
) -> ResolvedReview:
    """B3 resolution -- git mode.

    1. Reject if cwd is not in a git repo.
    2. Reject pseudo-ref as baseline (WORKING/INDEX are head-only).
    3. Validate baseline ref via resolve_git_ref.
    4. Default head = GitRefBaseline(WORKING) if head_spec is None.
    5. Dispatch by head ref kind.
    6. Return ResolvedReview with diff, mode_hint="git".
    """
    if not is_git_repo(cwd):
        raise BaselineResolutionError(
            "GitRefBaseline used outside git repo (cwd=%s)" % cwd
        )
    if is_pseudo_ref(baseline_spec.ref):
        raise BaselineResolutionError(
            "baseline cannot be a pseudo-ref (%s); "
            "pseudo-refs are head-only" % baseline_spec.ref
        )
    resolve_git_ref(baseline_spec.ref, cwd)  # raises if ref unknown

    head = head_spec if head_spec is not None else GitRefBaseline(WORKING)
    if head.ref == WORKING:
        diff = working_tree_diff(baseline_spec.ref, paths, cwd)
    elif head.ref == INDEX:
        diff = cached_diff(baseline_spec.ref, paths, cwd)
    else:
        resolve_git_ref(head.ref, cwd)
        diff = git_diff(baseline_spec.ref, head.ref, paths, cwd)

    return ResolvedReview(
        source_files=paths,
        baseline_content=None,
        git_diff=diff,
        mode_hint="git",
    )


def _resolve_snapshot(
    baseline_spec: SnapshotBaseline,
    paths: list[Path],
    cwd: Path,
) -> ResolvedReview:
    """B3 resolution -- stored snapshot.

    1. load_snapshot(path); returns None on missing file (BASELINE-03).
    2. If None: fall back to EmptyBaseline resolution silently.
    3. Else: pack snapshot dict into baseline_content.
    """
    snap = load_snapshot(baseline_spec.path)
    if snap is None:
        return _resolve_empty(None, paths, cwd)
    return ResolvedReview(
        source_files=paths,
        baseline_content={"snapshot": asdict(snap)},
        git_diff=None,
        mode_hint="non-git",
    )


def _resolve_empty(
    head_spec: Optional[GitRefBaseline],
    paths: list[Path],
    cwd: Path,
) -> ResolvedReview:
    """B3 resolution -- empty baseline (all source is new).

    1. mode_hint = "git" if cwd is in a git repo else "non-git".
    2. If git repo AND head_spec provided: diff from empty tree to head
       (git empty tree sha 4b825dc642cb6eb9a060e54bf8d69288fbee4904).
    3. Otherwise: no diff.
    """
    if is_git_repo(cwd) and head_spec is not None:
        # Git's universal empty-tree sha (constant, not computed)
        empty_tree_sha = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
        if head_spec.ref == WORKING:
            diff = working_tree_diff(empty_tree_sha, paths, cwd)
        elif head_spec.ref == INDEX:
            diff = cached_diff(empty_tree_sha, paths, cwd)
        else:
            resolve_git_ref(head_spec.ref, cwd)
            diff = git_diff(empty_tree_sha, head_spec.ref, paths, cwd)
        return ResolvedReview(
            source_files=paths,
            baseline_content=None,
            git_diff=diff,
            mode_hint="git",
        )
    mode = "git" if is_git_repo(cwd) else "non-git"
    return ResolvedReview(
        source_files=paths,
        baseline_content=None,
        git_diff=None,
        mode_hint=mode,
    )


def serialize_baseline_spec(spec: BaselineSpec) -> str:
    """OQ1 fix: produce single-line repr for state.json.

    Formats:
      GitRefBaseline(ref="HEAD")        -> "git:HEAD"
      SnapshotBaseline(path=...)        -> "snapshot:<posix-path>"
      EmptyBaseline()                   -> "empty"
    """
    if isinstance(spec, GitRefBaseline):
        return "git:%s" % spec.ref
    if isinstance(spec, SnapshotBaseline):
        return "snapshot:%s" % spec.path.as_posix()
    if isinstance(spec, EmptyBaseline):
        return "empty"
    raise BaselineResolutionError(
        "cannot serialize unknown spec type: %s" % type(spec)
    )
