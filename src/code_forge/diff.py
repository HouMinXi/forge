# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Git diff parser with changed-line extraction.

Uses unidiff library (RESEARCH.md Pattern 3) to parse unified diff
text and extract changed line numbers per file.

Line-number drift between tool output and diff is N/A: both reference
the same working tree file state. Tools run on the actual files
(post-patch), and git diff reports target-side line numbers for those
same files. Addresses LAYER0-03 and review Consensus #2.
"""

import logging

import unidiff

logger = logging.getLogger(__name__)


def count_diff_lines(diff_text: str | None) -> int:
    """Count insertions + deletions across all hunks in a unified diff.

    Used by tier_threshold() to determine cycle count based on diff size.
    Returns 0 for empty, None, malformed, binary, rename-only, or
    mode-only diffs.

    Args:
        diff_text: raw unified diff text (from git diff output)

    Returns:
        Total number of added + removed lines. 0 on any parse failure.
    """
    if not diff_text or not diff_text.strip():
        return 0

    try:
        patchset = unidiff.PatchSet(diff_text)
    except unidiff.errors.UnidiffParseError:
        logger.warning("Failed to parse diff for line counting")
        return 0

    total = 0
    for patched_file in patchset:
        for hunk in patched_file:
            for line in hunk:
                if line.is_added or line.is_removed:
                    total += 1

    return total


def tier_threshold(
    line_count: int,
    whole_file: bool = False,
    env_override: int | None = None,
) -> int:
    """Map diff line count to review cycle threshold.

    Priority chain:
      1. env_override (from FORGE_CLEAN_ROUND_THRESHOLD) always wins
      2. whole_file flag forces default 3 cycles
      3. line_count <= 0 returns 3 (safe default for empty/parse-error)
      4. line_count < 50 returns 2 (small diff relief)
      5. line_count >= 200 returns 4 (large diff extra scrutiny)
      6. else returns 3 (default)

    Args:
        line_count: total insertions + deletions from count_diff_lines()
        whole_file: True when --whole-file flag is active
        env_override: explicit threshold from env var (None = not set)

    Returns:
        Number of consecutive clean cycles required (minimum 1).
    """
    if env_override is not None:
        return max(1, env_override)

    if whole_file:
        return 3

    if line_count <= 0:
        return 3

    if line_count < 50:
        return 2

    if line_count >= 200:
        return 4

    return 3


def extract_changed_lines(diff_text: str) -> dict[str, set[int]]:
    """Parse unified diff, return {file: set_of_changed_line_numbers}.

    Only added/modified lines. Deleted files excluded.

    Line-number drift is N/A because both tools and diff reference
    the working tree file state. Tools run on the actual files
    (post-patch), and git diff -U0 HEAD reports target-side line
    numbers for those same files. Addresses LAYER0-03 and review
    Consensus #2.

    Args:
        diff_text: raw unified diff text (from git diff output)

    Returns:
        Dict mapping file paths to sets of added line numbers.
        Empty dict for empty or unparseable diff.
    """
    if not diff_text or not diff_text.strip():
        return {}

    try:
        patchset = unidiff.PatchSet(diff_text)
    except unidiff.errors.UnidiffParseError:
        logger.warning("Failed to parse diff text, returning empty dict")
        return {}

    result = {}
    for patched_file in patchset:
        # Skip deleted files
        if patched_file.is_removed_file:
            continue

        # Use target path (handles renames correctly)
        filepath = patched_file.path

        changed_lines = set()
        for hunk in patched_file:
            for line in hunk:
                if line.is_added and line.target_line_no is not None:
                    changed_lines.add(line.target_line_no)

        if changed_lines:
            result[filepath] = changed_lines

    return result


def get_changed_files(diff_text: str) -> list[str]:
    """Return sorted list of files with additions/modifications.

    Uses extract_changed_lines internally. Only files with at least
    one added line are included.

    Args:
        diff_text: raw unified diff text

    Returns:
        Sorted list of file paths with additions.
    """
    changed = extract_changed_lines(diff_text)
    return sorted(changed.keys())


def parse_diff_hunks(
    diff_text: str,
) -> tuple[dict[str, list[dict]], list[str]]:
    """Parse diff into per-hunk structure with explicit exemption tracking.

    Binary, rename-only, and mode-change files (zero hunks) are returned
    in exempt_files. Verify check 5 treats these as explicitly exempt --
    not silent fall-through (explicit boundary-hunk decision).
    """
    if not diff_text or not diff_text.strip():
        return ({}, [])

    try:
        patchset = unidiff.PatchSet(diff_text)
    except unidiff.errors.UnidiffParseError:
        logger.warning("Failed to parse diff for hunk extraction")
        return ({}, [])

    hunk_map: dict[str, list[dict]] = {}
    exempt_files: list[str] = []

    for pf in patchset:
        if pf.is_removed_file:
            continue

        hunks = list(pf)

        if getattr(pf, "is_binary_file", False):
            exempt_files.append(pf.path)
            continue
        if pf.is_rename and len(hunks) == 0:
            exempt_files.append(pf.path)
            continue
        if not pf.is_rename and not getattr(pf, "is_binary_file", False) and len(hunks) == 0:
            exempt_files.append(pf.path)
            continue

        file_hunks = []
        for hunk in hunks:
            added_lines = [
                line.target_line_no for line in hunk if line.is_added
            ]
            start = hunk.target_start
            end = (
                hunk.target_start + hunk.target_length - 1
                if hunk.target_length > 0
                else hunk.target_start
            )
            file_hunks.append(
                {
                    "start": start,
                    "end": end,
                    "added_lines": added_lines,
                    "is_deletion_only": len(added_lines) == 0,
                }
            )

        if file_hunks:
            hunk_map[pf.path] = file_hunks

    return (hunk_map, exempt_files)


def _extract_post_image_lines(
    diff_text: str,
) -> dict[str, dict[int, str]]:
    """Extract post-image lines from unified diff.

    Returns {file_path: {line_no: line_content}}. Used by verify check 5
    to compare excerpt content against the diff snapshot (not mutable
    working tree).
    """
    if not diff_text or not diff_text.strip():
        return {}

    try:
        patchset = unidiff.PatchSet(diff_text)
    except unidiff.errors.UnidiffParseError:
        logger.warning("Failed to parse diff for post-image extraction")
        return {}

    post_image: dict[str, dict[int, str]] = {}

    for pf in patchset:
        if pf.is_removed_file:
            continue

        file_lines: dict[int, str] = {}
        for hunk in pf:
            for line in hunk:
                if (line.is_added or line.is_context) and line.target_line_no is not None:
                    file_lines[line.target_line_no] = line.value

        if file_lines:
            post_image[pf.path] = file_lines

    return post_image
