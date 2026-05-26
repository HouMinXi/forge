# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for forge.diff -- git diff parser with changed-line extraction."""

import pytest

from forge.diff import extract_changed_lines, get_changed_files


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
