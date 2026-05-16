# SPDX-License-Identifier: AGPL-3.0-or-later
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
