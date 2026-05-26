# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for source_hash computation per STATE-07."""

import pytest
from pathlib import Path

from forge.source import compute_source_hash, normalize_text


class TestNormalizeText:
    """Whitespace normalization."""

    def test_trailing_whitespace_stripped(self):
        assert normalize_text("hello   \nworld  ") == "hello\nworld"

    def test_crlf_converted_to_lf(self):
        assert normalize_text("a\r\nb\r\n") == "a\nb"

    def test_mixed_endings(self):
        assert normalize_text("a\r\nb\nc\r\n") == "a\nb\nc"

    def test_empty_string(self):
        assert normalize_text("") == ""

    def test_tabs_stripped_from_trailing(self):
        assert normalize_text("x\t\ny") == "x\ny"


class TestComputeSourceHash:
    """SC-8, SC-9, SC-10, SC-11."""

    def test_git_diff_deterministic(self):
        """SC-8: same diff -> same hash across runs."""
        diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
        h1 = compute_source_hash(git_diff=diff)
        h2 = compute_source_hash(git_diff=diff)
        assert h1 == h2
        assert len(h1) == 64  # SHA256 hex

    def test_whitespace_only_diff_same_hash(self):
        """SC-8: trailing-ws and CRLF/LF normalize to same hash."""
        diff_lf = "--- a/f.py\n+++ b/f.py\n"
        diff_crlf = "--- a/f.py\r\n+++ b/f.py\r\n"
        diff_trailing = "--- a/f.py  \n+++ b/f.py  \n"
        assert compute_source_hash(git_diff=diff_lf) == \
            compute_source_hash(git_diff=diff_crlf)
        assert compute_source_hash(git_diff=diff_lf) == \
            compute_source_hash(git_diff=diff_trailing)

    def test_mode_isolation(self):
        """SC-9: git-mode and non-git mode produce different hashes."""
        content = "hello world"
        tmp = Path("/tmp/test_mode_isolation.txt")
        tmp.write_text(content)
        try:
            git_h = compute_source_hash(git_diff=content)
            nongt_h = compute_source_hash(files=[tmp])
            assert git_h != nongt_h
        finally:
            tmp.unlink(missing_ok=True)

    def test_non_git_sort_deterministic(self, tmp_path):
        """SC-8 + H3: file order stable via as_posix() sort."""
        f_b = tmp_path / "b.py"
        f_a = tmp_path / "a.py"
        f_b.write_text("b")
        f_a.write_text("a")
        h1 = compute_source_hash(files=[f_b, f_a])
        h2 = compute_source_hash(files=[f_a, f_b])
        assert h1 == h2

    def test_binary_file_hashed(self, tmp_path):
        """SC-10 / H1: binary file hashed as raw bytes."""
        f_bin = tmp_path / "data.bin"
        f_bin.write_bytes(b"\x00\x01\x02\x03")
        f_txt = tmp_path / "text.py"
        f_txt.write_text("hello")
        h = compute_source_hash(files=[f_txt, f_bin])
        assert len(h) == 64

    def test_binary_mutation_changes_hash(self, tmp_path):
        """SC-10: mutating binary content changes hash."""
        f = tmp_path / "data.bin"
        f.write_bytes(b"\x00\x01\x02\x03")
        h1 = compute_source_hash(files=[f])
        f.write_bytes(b"\x00\x01\x02\x04")
        h2 = compute_source_hash(files=[f])
        assert h1 != h2

    def test_binary_deterministic_across_runs(self, tmp_path):
        """SC-10: same binary across runs -> same hash."""
        f = tmp_path / "data.bin"
        f.write_bytes(b"\x00\x01\x02\x03")
        h1 = compute_source_hash(files=[f])
        h2 = compute_source_hash(files=[f])
        assert h1 == h2

    def test_file_edit_changes_hash(self, tmp_path):
        """SC-11: edits during HOLD change source_hash."""
        f = tmp_path / "code.py"
        f.write_text("original")
        h1 = compute_source_hash(files=[f])
        f.write_text("modified")
        h2 = compute_source_hash(files=[f])
        assert h1 != h2

    def test_both_args_raises(self, tmp_path):
        """Both git_diff and files raises ValueError."""
        f = tmp_path / "a.py"
        f.write_text("x")
        with pytest.raises(ValueError, match="exactly one"):
            compute_source_hash(git_diff="diff", files=[f])

    def test_neither_arg_raises(self):
        """Neither git_diff nor files raises ValueError."""
        with pytest.raises(ValueError, match="exactly one"):
            compute_source_hash()

    def test_posix_sort_consistency(self, tmp_path):
        """H3: paths with mixed separators sort consistently."""
        d = tmp_path / "sub"
        d.mkdir()
        f1 = d / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("a")
        f2.write_text("b")
        h1 = compute_source_hash(files=[f1, f2])
        h2 = compute_source_hash(files=[f2, f1])
        assert h1 == h2
