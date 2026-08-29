# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for forge.diff -- git diff parser with changed-line extraction."""

import pytest

from code_forge.diff import (
    annotate_diff_lines,
    count_diff_lines,
    extract_changed_lines,
    get_changed_files,
    tier_threshold,
)


# -- Test fixtures: actual unified diff format --

SINGLE_FILE_ADD = """\
diff --git a/hello.py b/hello.py
new file mode 100644
index 0000000..e69de29
--- /dev/null
+++ b/hello.py
@@ -0,0 +1,3 @@
+#!/usr/bin/env python3
+print("hello")
+print("world")
"""

MULTI_FILE_DIFF = """\
diff --git a/main.py b/main.py
index 1234567..abcdef0 100644
--- a/main.py
+++ b/main.py
@@ -1,2 +1,3 @@
 import os
+import sys
 print("hi")
diff --git a/util.py b/util.py
index 1234567..abcdef0 100644
--- a/util.py
+++ b/util.py
@@ -5,0 +6,2 @@
+def helper():
+    pass
"""

RENAME_DIFF = """\
diff --git a/old_name.py b/new_name.py
similarity index 90%
rename from old_name.py
rename to new_name.py
index 1234567..abcdef0 100644
--- a/old_name.py
+++ b/new_name.py
@@ -1,2 +1,3 @@
 import os
+import sys
 print("hi")
"""

DELETE_DIFF = """\
diff --git a/removed.py b/removed.py
deleted file mode 100644
index 1234567..0000000
--- a/removed.py
+++ /dev/null
@@ -1,5 +0,0 @@
-import os
-import sys
-
-def main():
-    pass
"""

PURE_DELETION_HUNK = """\
diff --git a/shrink.py b/shrink.py
index 1234567..abcdef0 100644
--- a/shrink.py
+++ b/shrink.py
@@ -3,2 +3,0 @@
-def old_func():
-    pass
"""

MULTIPLE_HUNKS = """\
diff --git a/big.py b/big.py
index 1234567..abcdef0 100644
--- a/big.py
+++ b/big.py
@@ -1,0 +2 @@
+import sys
@@ -10,0 +12,2 @@
+def new_func():
+    return 42
"""

BINARY_DIFF = """\
diff --git a/image.png b/image.png
new file mode 100644
index 0000000..1234567
Binary files /dev/null and b/image.png differ
"""

EMPTY_DIFF = ""


class TestExtractChangedLines:
    """Tests for extract_changed_lines()."""

    def test_single_file_add(self):
        """Added lines return correct line numbers."""
        result = extract_changed_lines(SINGLE_FILE_ADD)
        assert "hello.py" in result
        assert result["hello.py"] == {1, 2, 3}

    def test_multi_file(self):
        """Multiple files parsed correctly."""
        result = extract_changed_lines(MULTI_FILE_DIFF)
        assert "main.py" in result
        assert 2 in result["main.py"]
        assert "util.py" in result
        assert result["util.py"] == {6, 7}

    def test_deleted_files_excluded(self):
        """Deleted files do not appear in output."""
        result = extract_changed_lines(DELETE_DIFF)
        assert "removed.py" not in result

    def test_renamed_file_uses_target_path(self):
        """Renamed files use target path, not source path."""
        result = extract_changed_lines(RENAME_DIFF)
        assert "new_name.py" in result
        assert "old_name.py" not in result
        assert 2 in result["new_name.py"]

    def test_pure_deletion_hunk_empty_set(self):
        """Pure deletion hunks produce empty set for that file."""
        result = extract_changed_lines(PURE_DELETION_HUNK)
        # File exists in diff but has no added lines
        assert result.get("shrink.py", set()) == set()

    def test_multiple_hunks_merged(self):
        """Multiple hunks in same file are merged into one set."""
        result = extract_changed_lines(MULTIPLE_HUNKS)
        assert "big.py" in result
        assert result["big.py"] == {2, 12, 13}

    def test_empty_diff(self):
        """Empty diff returns empty dict."""
        result = extract_changed_lines(EMPTY_DIFF)
        assert result == {}

    def test_binary_file_no_crash(self):
        """Binary file markers handled gracefully (no crash)."""
        result = extract_changed_lines(BINARY_DIFF)
        assert result == {}


class TestGetChangedFiles:
    """Tests for get_changed_files()."""

    def test_returns_sorted_list(self):
        """Returns sorted list of files with additions."""
        result = get_changed_files(MULTI_FILE_DIFF)
        assert result == ["main.py", "util.py"]

    def test_excludes_deleted(self):
        """Deleted files not in output."""
        result = get_changed_files(DELETE_DIFF)
        assert result == []

    def test_empty_diff_returns_empty(self):
        """Empty diff returns empty list."""
        result = get_changed_files(EMPTY_DIFF)
        assert result == []

    def test_single_file(self):
        """Single file add returns that file."""
        result = get_changed_files(SINGLE_FILE_ADD)
        assert result == ["hello.py"]


# -- Fixtures for count_diff_lines tests --

ADDED_ONLY_3 = """\
diff --git a/foo.py b/foo.py
new file mode 100644
index 0000000..e69de29
--- /dev/null
+++ b/foo.py
@@ -0,0 +1,3 @@
+line1
+line2
+line3
"""

REMOVED_ONLY_2 = """\
diff --git a/bar.py b/bar.py
index 1234567..abcdef0 100644
--- a/bar.py
+++ b/bar.py
@@ -1,2 +1,0 @@
-old1
-old2
"""

MIXED_3ADD_2DEL = """\
diff --git a/mix.py b/mix.py
index 1234567..abcdef0 100644
--- a/mix.py
+++ b/mix.py
@@ -1,4 +1,5 @@
 context
-old1
-old2
+new1
+new2
+new3
 more context
"""

RENAME_ONLY_DIFF = """\
diff --git a/old.py b/new.py
similarity index 100%
rename from old.py
rename to new.py
"""

MODE_ONLY_DIFF = """\
diff --git a/script.sh b/script.sh
old mode 100644
new mode 100755
"""

MALFORMED_DIFF = "this is not a valid diff at all"


class TestCountDiffLines:
    """Tests for count_diff_lines()."""

    def test_count_diff_lines_empty(self):
        """Empty string and None both return 0."""
        assert count_diff_lines("") == 0
        assert count_diff_lines(None) == 0

    def test_count_diff_lines_added_only(self):
        """3-line addition diff returns 3."""
        assert count_diff_lines(ADDED_ONLY_3) == 3

    def test_count_diff_lines_removed_only(self):
        """2-line deletion diff returns 2."""
        assert count_diff_lines(REMOVED_ONLY_2) == 2

    def test_count_diff_lines_mixed(self):
        """3 additions + 2 deletions returns 5."""
        assert count_diff_lines(MIXED_3ADD_2DEL) == 5

    def test_count_diff_lines_parse_error(self):
        """Malformed text returns 0."""
        assert count_diff_lines(MALFORMED_DIFF) == 0

    def test_count_diff_lines_binary(self):
        """Binary file diff returns 0."""
        assert count_diff_lines(BINARY_DIFF) == 0

    def test_count_diff_lines_rename_only(self):
        """Rename-only diff returns 0."""
        assert count_diff_lines(RENAME_ONLY_DIFF) == 0

    def test_count_diff_lines_mode_only(self):
        """Mode-only change (chmod) returns 0."""
        assert count_diff_lines(MODE_ONLY_DIFF) == 0


class TestTierThreshold:
    """Tests for tier_threshold()."""

    def test_tier_threshold_env_override(self):
        """env_override=5 returns 5 regardless of line_count."""
        assert tier_threshold(10, env_override=5) == 5
        assert tier_threshold(500, env_override=5) == 5

    def test_tier_threshold_env_override_clamp(self):
        """env_override=0 is clamped to 1 (floor)."""
        assert tier_threshold(10, env_override=0) == 1
        assert tier_threshold(10, env_override=-3) == 1

    def test_tier_threshold_whole_file(self):
        """whole_file=True forces 3 cycles regardless of line_count."""
        assert tier_threshold(500, whole_file=True) == 3
        assert tier_threshold(10, whole_file=True) == 3

    def test_tier_threshold_small(self):
        """line_count=10 (small diff) returns 2."""
        assert tier_threshold(10) == 2

    def test_tier_threshold_medium(self):
        """line_count=100 (medium diff) returns 3."""
        assert tier_threshold(100) == 3

    def test_tier_threshold_large(self):
        """line_count=200 (large diff) returns 4."""
        assert tier_threshold(200) == 4

    def test_tier_threshold_zero(self):
        """line_count=0 (empty/parse-error) returns 3 (safe default)."""
        assert tier_threshold(0) == 3

    def test_tier_threshold_one(self):
        """line_count=1 (smallest real diff) returns 2."""
        assert tier_threshold(1) == 2

    @pytest.mark.parametrize(
        "line_count, expected",
        [
            (49, 2),   # just under boundary
            (50, 3),   # at boundary
            (199, 3),  # just under boundary
            (200, 4),  # at boundary
        ],
    )
    def test_tier_threshold_boundaries(self, line_count, expected):
        """Boundary values at tier transitions."""
        assert tier_threshold(line_count) == expected


# -- annotate_diff_lines --

ANNOTATE_INPUT = """\
diff --git a/test.py b/test.py
--- a/test.py
+++ b/test.py
@@ -10,5 +10,5 @@ def foo():
     context1
     context2
+    added1
     context3
-    removed1
     context4
"""


class TestAnnotateDiffLines:
    """annotate_diff_lines adds post-image line numbers to diff content."""

    def test_added_lines_get_plus_marker(self):
        result = annotate_diff_lines(ANNOTATE_INPUT)
        lines = result.splitlines()
        added = [x for x in lines if x.startswith("[+")]
        assert len(added) == 1
        assert added[0] == "[+  12] +    added1"

    def test_context_lines_get_space_marker(self):
        result = annotate_diff_lines(ANNOTATE_INPUT)
        lines = result.splitlines()
        ctx = [x for x in lines if x.startswith("[ ") and "]" in x]
        assert len(ctx) == 4
        assert ctx[0] == "[   10]     context1"
        assert ctx[1] == "[   11]     context2"

    def test_removed_lines_get_dash_marker(self):
        result = annotate_diff_lines(ANNOTATE_INPUT)
        lines = result.splitlines()
        removed = [x for x in lines if x.startswith("[----]")]
        assert len(removed) == 1
        assert removed[0] == "[----] -    removed1"

    def test_hunk_headers_unchanged(self):
        result = annotate_diff_lines(ANNOTATE_INPUT)
        assert "@@ -10,5 +10,5 @@" in result

    def test_file_headers_unchanged(self):
        result = annotate_diff_lines(ANNOTATE_INPUT)
        assert "--- a/test.py" in result
        assert "+++ b/test.py" in result

    def test_empty_diff_returns_empty(self):
        assert annotate_diff_lines("") == ""
        assert annotate_diff_lines(None) == ""

    def test_parse_failure_returns_original(self):
        garbage = "not a diff at all"
        assert annotate_diff_lines(garbage) == garbage

    def test_multiple_hunks(self):
        multi = """\
diff --git a/m.py b/m.py
--- a/m.py
+++ b/m.py
@@ -1,3 +1,4 @@
 a
+X
 b
 c
@@ -10,3 +11,4 @@
 d
+Y
 e
 f
"""
        result = annotate_diff_lines(multi)
        assert "[+   2] +X" in result
        assert "[+  12] +Y" in result

    def test_enclosing_function_survives_on_the_hunk_header(self):
        """git names the enclosing function on @@; rebuilding must keep it.

        Two hunks in different functions: without the section header a
        reviewer sees two anonymous line ranges and cannot tell which
        function either edit lands in.
        """
        diff = (
            "diff --git a/m.py b/m.py\n"
            "--- a/m.py\n"
            "+++ b/m.py\n"
            "@@ -1,3 +1,4 @@ def first():\n"
            " a\n"
            "+X\n"
            " b\n"
            " c\n"
            "@@ -10,3 +11,4 @@ def second():\n"
            " d\n"
            "+Y\n"
            " e\n"
            " f\n"
        )
        result = annotate_diff_lines(diff)
        assert "@@ -1,3 +1,4 @@ def first():" in result, result
        assert "@@ -10,3 +11,4 @@ def second():" in result, result

    def test_hunk_header_has_no_trailing_space_without_context(self):
        """A hunk with no enclosing context keeps a bare @@ line."""
        diff = (
            "diff --git a/m.py b/m.py\n"
            "--- a/m.py\n"
            "+++ b/m.py\n"
            "@@ -1,2 +1,3 @@\n"
            " a\n"
            "+X\n"
            " b\n"
        )
        result = annotate_diff_lines(diff)
        assert "@@ -1,2 +1,3 @@\n" in result, result

    def test_a_binary_file_still_says_it_changed(self):
        """The only line carrying the change must not be dropped.

        Annotation used to rebuild the diff from a parsed model, which
        could emit only the header kinds it was taught; a binary file came
        out as a bare ---/+++ pair and read as untouched.
        """
        diff = (
            "diff --git a/img.png b/img.png\n"
            "index 1234567..89abcde 100644\n"
            "Binary files a/img.png and b/img.png differ\n"
        )
        result = annotate_diff_lines(diff)
        assert "Binary files a/img.png and b/img.png differ" in result, result
        assert "index 1234567..89abcde 100644" in result, result

    def test_a_mode_change_still_says_it_changed(self):
        """chmod-only diffs carry their whole meaning in two header lines."""
        diff = (
            "diff --git a/s.sh b/s.sh\n"
            "old mode 100644\n"
            "new mode 100755\n"
        )
        result = annotate_diff_lines(diff)
        assert "old mode 100644" in result, result
        assert "new mode 100755" in result, result

    def test_deletion_only_hunk_marks_its_removed_lines(self):
        """A hunk owing zero post-image lines still has lines to mark."""
        diff = (
            "diff --git a/gone.py b/gone.py\n"
            "deleted file mode 100644\n"
            "--- a/gone.py\n"
            "+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n"
            "-a\n"
            "-b\n"
        )
        result = annotate_diff_lines(diff)
        assert "[----] -a" in result, result
        assert "[----] -b" in result, result

    def test_no_newline_marker_keeps_its_backslash(self):
        """The marker is a literal diff line, not decoration."""
        diff = (
            "diff --git a/t.py b/t.py\n"
            "--- a/t.py\n"
            "+++ b/t.py\n"
            "@@ -1,2 +1,2 @@\n"
            " a\n"
            "-b\n"
            "\\ No newline at end of file\n"
            "+c\n"
        )
        result = annotate_diff_lines(diff)
        assert "[    ] \\ No newline at end of file" in result, result

    def test_no_newline_marker_is_marked_at_the_end_of_a_hunk(self):
        """Deleting a file that lacked a final newline is a real git shape.

        The marker consumes neither side's line budget, so a hunk whose last
        real line spends both would close one line too early and leave the
        marker unmarked.
        """
        for diff in (
            # deletion-only hunk
            "diff --git a/g.py b/g.py\n"
            "deleted file mode 100644\n"
            "--- a/g.py\n"
            "+++ /dev/null\n"
            "@@ -1,1 +0,0 @@\n"
            "-a\n"
            "\\ No newline at end of file\n",
            # addition-only hunk
            "diff --git a/n.py b/n.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/n.py\n"
            "@@ -0,0 +1,1 @@\n"
            "+a\n"
            "\\ No newline at end of file\n",
        ):
            result = annotate_diff_lines(diff)
            assert "[    ] \\ No newline at end of file" in result, result

    def test_single_line_hunk_range_is_left_as_written(self):
        """git writes '@@ -1 +1 @@'; rewriting it to '-1,1 +1,1' is noise."""
        diff = (
            "diff --git a/o.py b/o.py\n"
            "--- a/o.py\n"
            "+++ b/o.py\n"
            "@@ -1 +1 @@\n"
            "-a\n"
            "+b\n"
        )
        result = annotate_diff_lines(diff)
        assert "@@ -1 +1 @@" in result, result
        assert "[+   1] +b" in result, result

    def test_headers_after_a_hunk_are_not_numbered(self):
        """The next file's ---/+++ start with -/+ but are not hunk content."""
        diff = (
            "diff --git a/x.py b/x.py\n"
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@ -1,1 +1,2 @@\n"
            " a\n"
            "+b\n"
            "diff --git a/y.py b/y.py\n"
            "--- a/y.py\n"
            "+++ b/y.py\n"
            "@@ -1,1 +1,2 @@\n"
            " c\n"
            "+d\n"
        )
        result = annotate_diff_lines(diff)
        assert "\n--- a/y.py\n" in result, result
        assert "\n+++ b/y.py\n" in result, result
        assert "[+   2] +b" in result and "[+   2] +d" in result, result

    def test_prompt_block_omits_the_legend_for_a_binary_only_diff(self):
        """No bracket column is produced, so no legend should promise one."""
        from code_forge.diff import annotated_diff_prompt_block

        diff = (
            "diff --git a/img.png b/img.png\n"
            "Binary files a/img.png and b/img.png differ\n"
        )
        block = annotated_diff_prompt_block(diff)
        assert "AFTER" not in block, block
        assert "Binary files" in block, block

    def test_a_missing_trailing_newline_does_not_conjure_a_legend(self):
        """The legend follows the bracket column, not a text comparison.

        Annotation always ends its output with a newline, so a diff that
        arrives without one is never byte-equal to its own annotation --
        which is why "did the text change" was the wrong question to ask.
        """
        from code_forge.diff import annotated_diff_prompt_block

        for diff in (
            "diff --git a/s.sh b/s.sh\nold mode 100644\nnew mode 100755",
            "diff --git a/i.png b/i.png\nBinary files a/i.png and b/i.png differ",
            "diff --git a/o.py b/n.py\nsimilarity index 100%\n"
            "rename from o.py\nrename to n.py",
        ):
            block = annotated_diff_prompt_block(diff)
            assert "AFTER" not in block, block

    def test_an_unannotated_diff_that_looks_like_it_has_brackets(self):
        """A line starting with '[' in the INPUT is not evidence of annotation.

        "[PATCH] diff --git ..." starts with [, yet annotation never touched
        it. A guard that reads the output for "[" lines is the third proxy
        this test exists to say no to.
        """
        from code_forge.diff import annotated_diff_prompt_block

        for diff in (
            "[PATCH] diff --git a/b b/b\nBinary files differ",
            "[INFO] hello\n[WARN] world",
        ):
            block = annotated_diff_prompt_block(diff)
            assert "AFTER" not in block, block

    def test_a_hunk_with_zero_lines_gets_no_legend(self):
        """A parseable @@ -1,0 +1,0 @@ carries no body for brackets to tag.

        Header presence is yet another proxy -- the walker must see at
        least one bracket line leave its loop.
        """
        from code_forge.diff import annotated_diff_prompt_block

        diff = (
            "diff --git a/x.py b/x.py\n"
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@ -1,0 +1,0 @@\n"
        )
        block = annotated_diff_prompt_block(diff)
        assert "AFTER" not in block, block

    def test_a_hunk_without_a_trailing_newline_still_gets_the_legend(self):
        """The counterpart: brackets present, so the legend must appear."""
        from code_forge.diff import annotated_diff_prompt_block

        diff = (
            "diff --git a/x.py b/x.py\n"
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@ -1,1 +1,2 @@\n"
            " a\n"
            "+b"
        )
        block = annotated_diff_prompt_block(diff)
        assert "AFTER" in block, block
        assert "[+   2] +b" in block, block

    def test_headers_of_a_plain_diff_u_are_not_numbered(self):
        """Without 'diff --git', the next file's ---/+++ follow hunk content.

        Those lines start with - and + but are not hunk content; only the
        hunk's own line counts can tell them apart.
        """
        diff = (
            "--- a/x.py\t2026-01-01\n"
            "+++ b/x.py\t2026-01-02\n"
            "@@ -1,1 +1,2 @@\n"
            " a\n"
            "+b\n"
            "--- a/y.py\t2026-01-01\n"
            "+++ b/y.py\t2026-01-02\n"
            "@@ -1,1 +1,2 @@\n"
            " c\n"
            "+d\n"
        )
        result = annotate_diff_lines(diff)
        assert "\n--- a/y.py\t2026-01-01\n" in result, result
        assert "\n+++ b/y.py\t2026-01-02\n" in result, result
        assert "[+   2] +b" in result and "[+   2] +d" in result, result

    def test_in_hunk_content_that_looks_like_a_header_is_still_content(self):
        """A removed line reading '--- a/z.py' is a deletion, not a header."""
        diff = (
            "diff --git a/x.py b/x.py\n"
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@ -1,4 +1,2 @@\n"
            " keep\n"
            "--- a/z.py\n"
            "-+++ b/z.py\n"
            " tail\n"
        )
        result = annotate_diff_lines(diff)
        assert "[----] --- a/z.py" in result, result
        assert "[----] -+++ b/z.py" in result, result
        assert "[    2] tail" in result, result

    def test_prompt_block_explains_the_bracket_column(self):
        """The reviewer is told what the numbers mean, before the diff."""
        from code_forge.diff import annotated_diff_prompt_block

        block = annotated_diff_prompt_block(ANNOTATE_INPUT)
        assert "AFTER" in block, block
        assert "start_line/end_line" in block, block
        # The legend has to arrive before the lines it describes.
        assert block.index("[+  82] +added line") < block.index(
            "[+  12] +    added1"), block

    def test_prompt_block_omits_the_legend_when_nothing_was_annotated(self):
        """Unparseable input keeps its raw text and gets no bracket legend."""
        from code_forge.diff import annotated_diff_prompt_block

        garbage = "not a diff at all\n"
        block = annotated_diff_prompt_block(garbage)
        assert block == "\nDiff:\n" + garbage, block
        assert annotated_diff_prompt_block("") == "\nDiff:\n"

    def test_no_newline_marker_shows_placeholder(self):
        """'\\ No newline at end of file' has no target_line_no."""
        diff = (
            "diff --git a/t.py b/t.py\n"
            "--- a/t.py\n"
            "+++ b/t.py\n"
            "@@ -1,2 +1,2 @@\n"
            " a\n"
            "-b\n"
            "\\ No newline at end of file\n"
            "+c\n"
        )
        result = annotate_diff_lines(diff)
        assert "[    ]" in result
        assert "No newline" in result

    def test_rename_headers_preserved(self):
        diff = (
            "diff --git a/old.py b/new.py\n"
            "similarity index 100%\n"
            "rename from old.py\n"
            "rename to new.py\n"
        )
        result = annotate_diff_lines(diff)
        assert "rename from old.py" in result
        assert "rename to new.py" in result

    def test_added_file_headers(self):
        diff = (
            "diff --git a/new.py b/new.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/new.py\n"
            "@@ -0,0 +1,3 @@\n"
            "+a\n"
            "+b\n"
            "+c\n"
        )
        result = annotate_diff_lines(diff)
        assert "new file mode 100644" in result
        assert "--- /dev/null" in result
        assert "+++ b/new.py" in result
        assert "[+   1] +a" in result

    def test_deleted_file_headers(self):
        diff = (
            "diff --git a/old.py b/old.py\n"
            "deleted file mode 100644\n"
            "--- a/old.py\n"
            "+++ /dev/null\n"
            "@@ -1,3 +0,0 @@\n"
            "-a\n"
            "-b\n"
            "-c\n"
        )
        result = annotate_diff_lines(diff)
        assert "deleted file mode 100644" in result
        assert "--- a/old.py" in result
        assert "+++ /dev/null" in result
        assert "[----] -a" in result


class TestSplitDiffForFiles:
    """Split a unified diff into the sections belonging to given files.

    Grouped review needs each group's share of the diff as standalone text.
    A member with no section (a binary or pure rename entry has no hunks and
    sem can still report it) is skipped, not an error.
    """

    DIFF = (
        "diff --git a/src/a.py b/src/a.py\n"
        "--- a/src/a.py\n"
        "+++ b/src/a.py\n"
        "@@ -1,2 +1,3 @@\n"
        " one\n"
        "+two\n"
        " three\n"
        "diff --git a/src/b.py b/src/b.py\n"
        "--- a/src/b.py\n"
        "+++ b/src/b.py\n"
        "@@ -1 +1,2 @@\n"
        " keep\n"
        "+new\n"
        "diff --git a/README.md b/README.md\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )

    def test_single_member_returns_only_its_section(self):
        from code_forge.diff import split_diff_for_files
        out = split_diff_for_files(self.DIFF, ["src/b.py"])
        assert "src/b.py" in out
        assert "+new" in out
        assert "src/a.py" not in out
        assert "README.md" not in out

    def test_multiple_members_preserve_diff_order(self):
        from code_forge.diff import split_diff_for_files
        out = split_diff_for_files(self.DIFF, ["README.md", "src/a.py"])
        # asked in reverse order, emitted in the diff's own order
        assert out.index("src/a.py") < out.index("README.md")
        assert "src/b.py" not in out

    def test_all_members_round_trip(self):
        from code_forge.diff import split_diff_for_files
        members = ["src/a.py", "src/b.py", "README.md"]
        out = split_diff_for_files(self.DIFF, members)
        assert out == self.DIFF

    def test_absent_member_is_skipped(self):
        from code_forge.diff import split_diff_for_files
        out = split_diff_for_files(self.DIFF, ["src/a.py", "src/ghost.py"])
        assert "src/a.py" in out
        assert "ghost" not in out

    def test_empty_diff(self):
        from code_forge.diff import split_diff_for_files
        assert split_diff_for_files("", ["a.py"]) == ""

    def test_deleted_file_section_kept(self):
        from code_forge.diff import split_diff_for_files
        diff = (
            "diff --git a/src/old.py b/src/old.py\n"
            "deleted file mode 100644\n"
            "--- a/src/old.py\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            "-gone\n"
            "diff --git a/src/b.py b/src/b.py\n"
            "--- a/src/b.py\n"
            "+++ b/src/b.py\n"
            "@@ -1 +1,2 @@\n"
            " keep\n"
            "+new\n"
        )
        out = split_diff_for_files(diff, ["src/old.py"])
        assert "-gone" in out
        assert "src/b.py" not in out

    def test_new_file_section_kept(self):
        from code_forge.diff import split_diff_for_files
        diff = (
            "diff --git a/src/new.py b/src/new.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/src/new.py\n"
            "@@ -0,0 +1 @@\n"
            "+fresh\n"
        )
        assert split_diff_for_files(diff, ["src/new.py"]) == diff


class TestDescribeFabricatedLines:
    """Name the claimed lines a diff's post-image does not contain."""

    def test_returns_empty_when_every_line_is_present(self):
        from code_forge.diff import describe_fabricated_lines
        got = describe_fabricated_lines({1: "a", 2: "b", 3: "c"}, 1, 3)
        assert got == ""

    def test_names_a_line_past_the_end_of_the_post_image(self):
        from code_forge.diff import describe_fabricated_lines
        got = describe_fabricated_lines({1: "a", 2: "b"}, 1, 4)
        assert got == "3, 4"

    def test_names_a_line_in_the_gap_between_two_hunks(self):
        """Unchanged regions between hunks are absent, not empty strings."""
        from code_forge.diff import describe_fabricated_lines
        got = describe_fabricated_lines({1: "a", 2: "b", 9: "i"}, 1, 9)
        assert got == "3, 4, 5, 6, 7, 8"

    def test_the_cap_bounds_a_reviewer_supplied_end_line(self):
        """end_line comes from the reviewer and nothing on disk limits it.

        Without the cap a claim of end_line=1000000 would build a
        million-element list and a log line to match.
        """
        from code_forge.diff import describe_fabricated_lines
        got = describe_fabricated_lines({}, 1, 500, cap=3)
        assert got == "1, 2, 3, ..."

    def test_no_ellipsis_when_the_count_lands_exactly_on_the_cap(self):
        from code_forge.diff import describe_fabricated_lines
        got = describe_fabricated_lines({}, 1, 3, cap=3)
        assert got == "1, 2, 3"

    def test_a_single_line_range_is_handled(self):
        from code_forge.diff import describe_fabricated_lines
        assert describe_fabricated_lines({}, 7, 7) == "7"
        assert describe_fabricated_lines({7: "x"}, 7, 7) == ""
