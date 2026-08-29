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
import re
from pathlib import Path

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


def extract_changed_lines(
    diff_text: str,
    repo_root: "Path | None" = None,
) -> dict[str, set[int]]:
    """Parse unified diff, return {file: set_of_changed_line_numbers}.

    Only added/modified lines. Deleted files excluded.

    Line-number drift is N/A because both tools and diff reference
    the working tree file state. Tools run on the actual files
    (post-patch), and git diff -U0 HEAD reports target-side line
    numbers for those same files. Addresses LAYER0-03 and review
    Consensus #2.

    Args:
        diff_text: raw unified diff text (from git diff output)
        repo_root: if provided, also register absolute path keys
            (str(repo_root / rel_path)) so lookups succeed when
            findings carry absolute paths.

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
            if repo_root is not None and not Path(filepath).is_absolute():
                abs_key = str(Path(repo_root) / filepath)
                result[abs_key] = changed_lines

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


def split_diff_for_files(diff_text: str, members: list[str]) -> str:
    """Return the sections of a unified diff belonging to `members`.

    Splits on `diff --git` headers and keeps each section whose path is in
    `members`, in the diff's original order. The path is read from the
    `+++ b/<path>` line, falling back to `--- a/<path>` for deletions, so
    paths containing spaces survive (the `diff --git` line itself is
    ambiguous for those).

    A member with no section (binary or pure-rename entries carry no hunks,
    and a path listing tool may still report the file) is skipped rather
    than treated as an error: nothing about such a file can be reviewed as
    text anyway.
    """
    if not diff_text:
        return ""
    wanted = set(members)
    sections: list[tuple[str | None, str]] = []
    current: list[str] = []
    for line in diff_text.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if current:
                sections.append(_section_entry(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        sections.append(_section_entry(current))
    return "".join(text for path, text in sections if path in wanted)


def _section_entry(lines: list[str]) -> tuple[str | None, str]:
    """(post-change path, verbatim section text) for one diff section."""
    old_path: str | None = None
    for line in lines:
        if line.startswith("+++ b/"):
            return line[len("+++ b/"):].rstrip("\n"), "".join(lines)
        if line.startswith("--- a/"):
            old_path = line[len("--- a/"):].rstrip("\n")
    return old_path, "".join(lines)


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


def describe_fabricated_lines(
    file_lines: dict[int, str],
    start_line: int,
    end_line: int,
    cap: int = 100,
) -> str:
    """Name the lines in start_line..end_line the post-image does not have.

    ``file_lines`` is one file's entry from :func:`_extract_post_image_lines`.
    A line the diff never produced cannot have been read from it, so an
    excerpt covering one is fabricated.  Returns them comma-separated, or
    "" when every line in the range is present.

    Two shapes land here: a line falling in the gap between two hunks, and
    a line past the last one the diff produced.  Both are simply absent
    from ``file_lines``.  At most ``cap`` are named, with a trailing "..."
    when more were left out -- ``end_line`` comes from the reviewer and is
    bounded by nothing on disk.

    Callers that also hold the diff's exempt files must skip those first:
    binary, rename, and mode-change entries have no post-image at all, and
    every line of an excerpt against one would read as fabricated here.
    """
    fabricated: list[int] = []
    truncated = False

    for ln in range(start_line, end_line + 1):
        if ln in file_lines:
            continue
        if len(fabricated) >= cap:
            truncated = True
            break
        fabricated.append(ln)

    if not fabricated:
        return ""
    named = ", ".join(str(ln) for ln in fabricated)
    if truncated:
        named += ", ..."
    return named


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


_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def annotate_diff_lines(diff_text: str) -> str:
    """Annotate a unified diff with post-image line numbers.

    Prepends a bracket tag to each hunk content line so the reviewer can
    read the line number instead of counting from the hunk header.  Every
    other byte of the diff -- file headers, ``index``, ``old mode``/``new
    mode``, ``Binary files ... differ``, ``similarity index``, ``rename
    from``/``rename to`` -- passes through untouched.

    This annotates the text in place rather than rebuilding it from a
    parsed model, because a rebuild can only emit the header kinds it was
    taught, and the ones it was not taught vanish silently.  A binary file
    or a chmod then reaches the reviewer looking untouched, which is worse
    than an unannotated diff: it is a diff that lies about its own
    contents.

    Format:
        [   79]  context line
        [+  82] +added line
        [----] -removed line (no post-image number -- deleted lines
            have no target-side line number in unified diff)
        [    ] \\ No newline at end of file

    Returns *diff_text* unchanged on empty, None, or unparseable input.
    """
    result = _annotation_walk(diff_text)
    if result is None:
        return diff_text or ""
    text, _wrote_bracket = result
    # "Parseable" does not imply "wrote a bracket line": an empty hunk
    # (@@ -1,0 +1,0 @@), a pure rename, a chmod, a binary, a bare
    # "diff --git" header all walk to zero bracket emissions. Hand those
    # back as supplied, because the input text is what every other reader
    # of this function's contract (verify, tests, logs) already speaks.
    if not _wrote_bracket:
        return diff_text
    return text


def _annotation_walk(diff_text: str) -> tuple[str, bool] | None:
    """Walk diff_text once; return (annotated_output, wrote_bracket).

    Returns None when annotation would hand its input back unannotated
    (empty, unparseable, or a parseable diff with zero hunks). On None,
    callers should use the input text directly -- annotate_diff_lines's
    documented contract is "return the input unchanged" in those cases.

    The two are read off the same walk, so they cannot disagree with each
    other: every "did annotation produce a bracket" probe that asked the
    OUTPUT has been wrong in a new way each time (strip, byte equality,
    startswith). Ask the walk that produced the output.
    """
    if not diff_text or not diff_text.strip():
        return None
    try:
        if not list(unidiff.PatchSet(diff_text)):
            return None
    except unidiff.errors.UnidiffParseError:
        return None

    out: list[str] = []
    line_no = None
    src_left = 0
    tgt_left = 0
    wrote_bracket = False
    for line in diff_text.splitlines():
        m = _HUNK_HEADER.match(line)
        if line_no is not None and src_left <= 0 and tgt_left <= 0 \
                and not line.startswith("\\"):
            line_no = None
        if m:
            src_left = int(m.group(2)) if m.group(2) is not None else 1
            line_no = int(m.group(3))
            tgt_left = int(m.group(4)) if m.group(4) is not None else 1
            out.append(line)
        elif line_no is None:
            out.append(line)
        elif line.startswith("+"):
            out.append("[+%4d] %s" % (line_no, line))
            wrote_bracket = True
            line_no += 1
            tgt_left -= 1
        elif line.startswith("-"):
            out.append("[----] %s" % line)
            wrote_bracket = True
            src_left -= 1
        elif line.startswith("\\"):
            out.append("[    ] %s" % line)
            wrote_bracket = True
        elif line.startswith(" ") or line == "":
            out.append("[ %4d]%s" % (line_no, line))
            wrote_bracket = True
            line_no += 1
            src_left -= 1
            tgt_left -= 1
        else:
            # Unreachable on any diff the unidiff gate admits: every shape
            # that would land here (a hunk body line that is neither added
            # nor removed nor context nor "\ No newline") is rejected before
            # the walk begins. Kept as the defensive fall-through, not as a
            # documented degradation path -- the walker's actual degradation
            # is the `line_no = None` state used elsewhere.
            line_no = None
            out.append(line)
    return ("\n".join(out) + "\n", wrote_bracket)


def annotated_diff_prompt_block(diff_text: str) -> str:
    """The diff as it appears in a review prompt: legend, then annotation.

    The bracket column is only useful to a reviewer that knows what it
    means. Every caller pairs the two, so they are built together here --
    a prompt that shows ``[+  82]`` without saying what 82 counts invites
    the reviewer to read it as part of the source line.

    Used by the L1 review prompts, the canary review in ``canary_gen``,
    and the runtime lifecycle question in ``runtime``. The daemon-state
    questions never cite lines, so the column would be noise there.
    """
    result = _annotation_walk(diff_text)
    if result is None:
        return "\nDiff:\n" + (diff_text or "")
    annotated, wrote_bracket = result
    # The legend must appear exactly when the walker wrote a bracket line.
    # Three output-side proxies preceded this (strip, byte equality,
    # startswith) and each was wrong in a new way: an unannotated diff can
    # carry a "[" of its own; annotation always adds a trailing newline;
    # a parseable diff can contain zero hunks. Ask the walk that produced
    # the output rather than the output it produced.
    if not wrote_bracket:
        return "\nDiff:\n" + diff_text
    return (
        "\nDiff:\n"
        "Each line is prefixed with its line number in the file AFTER "
        "this change is applied. Use those numbers directly in "
        "start_line/end_line -- do not count lines yourself.\n"
        "  [  79]  unchanged context line, at line 79\n"
        "  [+  82] +added line, at line 82\n"
        "  [----] -removed line: gone after the change, so it has no "
        "line number and cannot be cited\n"
        "The bracket is not part of the source. Excerpt content must "
        "reproduce the code to the right of it, without the bracket and "
        "without the leading +/- marker.\n\n"
        + annotated
    )
