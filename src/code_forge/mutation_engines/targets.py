# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Target-declaration loading, validation, discovery and selection accounting.

Implements the target-declaration contract from the admitted specification:
  - loading and validating the ``mutation.targets`` list
  - duplicate-id and oversized-declaration rejection
  - target discovery from a YAML configuration block
  - selection accounting: which targets are selected by changed paths
    (both old and new paths for rename/delete cases)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .schemas import (
    MAX_TARGETS,
    TargetDeclaration,
)


# ---------------------------------------------------------------------------
# Configuration loading
# ---------------------------------------------------------------------------

class DeclarationError(Exception):
    """Raised when target declarations are invalid."""


def load_targets(config: dict[str, Any]) -> list[TargetDeclaration]:
    """Parse and validate the ``mutation`` block from a gate configuration.

    *config* is the top-level parsed YAML mapping (e.g. from gate.yaml).
    Returns the list of validated ``TargetDeclaration`` objects.

    Raises ``DeclarationError`` on any validation failure including:
      - missing or non-dict ``mutation`` block
      - missing ``schema_version``
      - unknown or extra keys
      - duplicate target ids
      - oversized declarations (> MAX_TARGETS)
    """
    mutation = config.get("mutation")
    if mutation is None:
        raise DeclarationError("configuration has no 'mutation' block")
    if not isinstance(mutation, dict):
        raise DeclarationError(
            "'mutation' must be a mapping, got %s" % type(mutation).__name__
        )

    sv = mutation.get("schema_version")
    if sv != 1:
        raise DeclarationError(
            "mutation.schema_version must be 1, got %r" % sv
        )

    allowed_keys = {"schema_version", "targets"}
    extra = set(mutation.keys()) - allowed_keys
    if extra:
        raise DeclarationError(
            "mutation block has unknown keys: %s" % ", ".join(sorted(extra))
        )

    raw_targets = mutation.get("targets")
    if raw_targets is None:
        raise DeclarationError("mutation block has no 'targets' list")
    if not isinstance(raw_targets, list):
        raise DeclarationError(
            "'mutation.targets' must be a list, got %s"
            % type(raw_targets).__name__
        )

    if len(raw_targets) > MAX_TARGETS:
        raise DeclarationError(
            "too many targets: %d exceeds maximum %d"
            % (len(raw_targets), MAX_TARGETS)
        )

    targets: list[TargetDeclaration] = []
    seen_ids: set[str] = set()
    for i, raw in enumerate(raw_targets):
        try:
            td = TargetDeclaration.from_dict(raw)
        except (TypeError, ValueError) as exc:
            raise DeclarationError(
                "target[%d]: %s" % (i, exc)
            ) from exc

        if td.id in seen_ids:
            raise DeclarationError(
                "duplicate target id: %r" % td.id
            )
        seen_ids.add(td.id)
        targets.append(td)

    return targets


# ---------------------------------------------------------------------------
# Selection accounting
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ChangedPath:
    """A file path changed between two revisions.

    *old_path* is the path before the change (None for additions).
    *new_path* is the path after the change (None for deletions).
    """
    old_path: str | None
    new_path: str | None

    def __post_init__(self) -> None:
        if self.old_path is None and self.new_path is None:
            raise ValueError("ChangedPath must have at least one path")


@dataclass
class TargetSelection:
    """Records why a target was selected.

    Matches the specification's TargetSelection type.
    """
    target_id: str
    granularity: str  # "full" | "file" | "line"
    files: tuple[str, ...]
    line_ranges: dict[str, tuple[tuple[int, int], ...]]
    reasons: tuple[str, ...]


@dataclass
class SelectionResult:
    """The outcome of selection accounting.

    Matches the specification's Selection type.
    """
    targets: tuple[TargetSelection, ...]
    unmatched_paths: tuple[str, ...]


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile a glob pattern into a regex.

    ``**`` matches across directories; ``*`` stays within one segment.
    """
    i = 0
    n = len(pattern)
    parts: list[str] = ["^"]
    while i < n:
        char = pattern[i]
        if char == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                i += 2
                if i < n and pattern[i] == "/":
                    i += 1
                    parts.append("(?:.*/)?")
                else:
                    parts.append(".*")
                continue
            parts.append("[^/]*")
            i += 1
            continue
        if char == "?":
            parts.append("[^/]")
            i += 1
            continue
        if char == "[":
            j = i + 1
            if j < n and pattern[j] == "!":
                j += 1
            if j < n and pattern[j] == "]":
                # A ']' immediately after '[' or '[!' is a literal member.
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j < n and j > i + 1 and pattern[i + 1 : j]:
                content = pattern[i + 1 : j]
                content = content.replace("\\", "\\\\")
                if content.startswith("!"):
                    content = "^" + content[1:]
                elif content.startswith("^"):
                    # Glob has no caret negation; keep it literal. Prepend
                    # the escape only after doubling backslashes, or the
                    # escape itself gets doubled and the class over-matches.
                    content = "\\^" + content[1:]
                parts.append("[" + content + "]")
                i = j + 1
            else:
                parts.append(re.escape(char))
                i += 1
            continue
        parts.append(re.escape(char))
        i += 1
    parts.append("$")
    return re.compile("".join(parts))


def _path_matches_patterns(path: str, patterns: tuple[str, ...]) -> bool:
    """Return True when *path* matches any of the glob *patterns*."""
    for pattern in patterns:
        if _glob_to_regex(pattern).match(path):
            return True
    return False


def _path_matches_exact(path: str, exact_paths: tuple[str, ...]) -> bool:
    """Return True when *path* equals any of the exact *exact_paths*."""
    return path in exact_paths


def select_targets(
    targets: list[TargetDeclaration],
    changes: list[ChangedPath],
    *,
    declaration_changed: bool = False,
    policy_changed: bool = False,
    before_targets: list[TargetDeclaration] | None = None,
) -> SelectionResult:
    """Determine which targets are selected by *changes*.

    Selection rules (spec line 123):
      - A target is selected if a changed old or new path matches its
        sources, tests, inputs, corpus or engine configuration.
      - A changed declaration or mutation-wide policy selects every
        affected target.
      - Selection checks BOTH before and after target maps (rename and
        delete cases must be accounted).

    *before_targets* is the target list from the pre-change revision
    (required for rename/delete accounting).  When None, only post-change
    targets are checked.

    *declaration_changed* selects every target in both before and after maps.
    *policy_changed* selects every target in both before and after maps.
    """
    # Collect all target ids from before and after
    after_by_id = {t.id: t for t in targets}
    before_by_id = {t.id: t for t in (before_targets or [])}
    all_ids = set(after_by_id.keys()) | set(before_by_id.keys())

    selected: dict[str, list[str]] = {}  # target_id -> reasons

    # Declaration or policy change selects every target
    if declaration_changed or policy_changed:
        for tid in all_ids:
            if declaration_changed:
                selected.setdefault(tid, []).append("declaration changed")
            if policy_changed:
                selected.setdefault(tid, []).append("policy changed")

    # Per-change selection
    matched_paths: set[str] = set()

    for change in changes:
        paths_to_check = []
        if change.old_path is not None:
            paths_to_check.append(("old", change.old_path))
        if change.new_path is not None:
            paths_to_check.append(("new", change.new_path))

        for side, path in paths_to_check:
            # Check against the appropriate target map
            target_maps = []
            if side == "old":
                target_maps.append(("before", before_by_id))
                target_maps.append(("after", after_by_id))
            else:
                target_maps.append(("after", after_by_id))
                target_maps.append(("before", before_by_id))

            for map_label, tmap in target_maps:
                for tid, tgt in tmap.items():
                    if _path_matches_patterns(path, tgt.sources):
                        reason = "%s path %s matches sources (%s)" % (
                            side, path, map_label
                        )
                        selected.setdefault(tid, []).append(reason)
                        matched_paths.add(path)
                    if _path_matches_patterns(path, tgt.tests):
                        reason = "%s path %s matches tests (%s)" % (
                            side, path, map_label
                        )
                        selected.setdefault(tid, []).append(reason)
                        matched_paths.add(path)
                    if _path_matches_exact(path, tgt.inputs):
                        reason = "%s path %s matches inputs (%s)" % (
                            side, path, map_label
                        )
                        selected.setdefault(tid, []).append(reason)
                        matched_paths.add(path)
                    if tgt.corpus is not None and path == tgt.corpus:
                        reason = "%s path %s matches corpus (%s)" % (
                            side, path, map_label
                        )
                        selected.setdefault(tid, []).append(reason)
                        matched_paths.add(path)
                    if tgt.engine_config is not None and path == tgt.engine_config:
                        reason = "%s path %s matches engine_config (%s)" % (
                            side, path, map_label
                        )
                        selected.setdefault(tid, []).append(reason)
                        matched_paths.add(path)

    # Build selection results
    target_selections: list[TargetSelection] = []
    for tid, reasons in sorted(selected.items()):
        # Determine matched files for this target. Both the before and the
        # after declaration contribute patterns: a rename can select via
        # the before map while the after map carries different patterns.
        candidates: list[TargetDeclaration] = []
        for decl in (after_by_id.get(tid), before_by_id.get(tid)):
            if decl is not None and decl not in candidates:
                candidates.append(decl)
        target_files: list[str] = []
        for change in changes:
            for p in (change.old_path, change.new_path):
                if p is None:
                    continue
                if any(
                    _path_matches_patterns(p, tgt.sources)
                    or _path_matches_patterns(p, tgt.tests)
                    or _path_matches_exact(p, tgt.inputs)
                    or (tgt.corpus is not None and p == tgt.corpus)
                    or (tgt.engine_config is not None and p == tgt.engine_config)
                    for tgt in candidates
                ):
                    if p not in target_files:
                        target_files.append(p)

        # Determine granularity
        has_source = any(
            "matches sources" in r for r in reasons
        )
        has_other = any(
            "matches tests" in r
            or "matches inputs" in r
            or "matches corpus" in r
            or "matches engine_config" in r
            or "declaration changed" in r
            or "policy changed" in r
            for r in reasons
        )
        if has_other and not has_source:
            granularity = "full"
        elif has_source and not has_other:
            granularity = "file"
        else:
            granularity = "full"

        # Deduplicate reasons
        unique_reasons = tuple(dict.fromkeys(reasons))
        target_selections.append(TargetSelection(
            target_id=tid,
            granularity=granularity,
            files=tuple(target_files),
            line_ranges={},
            reasons=unique_reasons,
        ))

    # Determine unmatched paths
    all_changed_paths: set[str] = set()
    for change in changes:
        if change.old_path is not None:
            all_changed_paths.add(change.old_path)
        if change.new_path is not None:
            all_changed_paths.add(change.new_path)
    unmatched = sorted(all_changed_paths - matched_paths)

    return SelectionResult(
        targets=tuple(target_selections),
        unmatched_paths=tuple(unmatched),
    )
