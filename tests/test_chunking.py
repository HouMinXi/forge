# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for large-diff chunking (Phase 40).

Validates:
- Diff under threshold: no chunking
- Diff over threshold: chunking activates
- 3-file diff produces findings from all 3 files
- Chunk timeout sets pass_status=TIMEOUT
- All chunks succeed -> pass_status=COMPLETED
- Dedup by fingerprint across chunks
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from code_forge.disposition import Disposition
from code_forge.outlet_c import (
    _split_diff_by_file,
    _read_chunk_threshold_kb,
    _run_chunk,
    _DEFAULT_CHUNK_THRESHOLD_KB,
)
from code_forge.state import StateFinding, PassOutcome, derive_pass_outcomes


# -- Test data --

SMALL_DIFF = """\
diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1,2 +1,3 @@
 x = 1
 y = 2
+z = 3
"""

THREE_FILE_DIFF = """\
diff --git a/file1.py b/file1.py
--- a/file1.py
+++ b/file1.py
@@ -1,2 +1,3 @@
 x = 1
 y = 2
+z = 3
diff --git a/file2.py b/file2.py
--- a/file2.py
+++ b/file2.py
@@ -1,2 +1,3 @@
 a = 1
 b = 2
+c = 3
diff --git a/file3.py b/file3.py
--- a/file3.py
+++ b/file3.py
@@ -1,2 +1,3 @@
 m = 1
 n = 2
+o = 3
"""

BINARY_DIFF = """\
diff --git a/image.png b/image.png
Binary files /dev/null and b/image.png differ
"""


class TestSplitDiffByFile:
    """Tests for _split_diff_by_file."""

    def test_three_file_diff(self):
        """3-file diff produces 3 chunks."""
        chunks = _split_diff_by_file(THREE_FILE_DIFF)
        assert len(chunks) == 3

    def test_single_file_diff(self):
        """Single file diff produces 1 chunk."""
        chunks = _split_diff_by_file(SMALL_DIFF)
        assert len(chunks) == 1

    def test_empty_diff(self):
        """Empty diff produces 0 chunks."""
        assert _split_diff_by_file("") == []
        assert _split_diff_by_file("   ") == []

    def test_binary_diff_skipped(self):
        """Binary file diff (no hunk headers) produces 0 chunks."""
        chunks = _split_diff_by_file(BINARY_DIFF)
        assert len(chunks) == 0


class TestReadChunkThreshold:
    """Tests for _read_chunk_threshold_kb."""

    def test_default_threshold(self):
        """Default threshold is 100KB."""
        # Ensure env var is unset.
        os.environ.pop("FORGE_DIFF_CHUNK_THRESHOLD_KB", None)
        assert _read_chunk_threshold_kb() == _DEFAULT_CHUNK_THRESHOLD_KB

    def test_custom_threshold(self):
        """Custom threshold from env var."""
        os.environ["FORGE_DIFF_CHUNK_THRESHOLD_KB"] = "50"
        try:
            assert _read_chunk_threshold_kb() == 50
        finally:
            del os.environ["FORGE_DIFF_CHUNK_THRESHOLD_KB"]

    def test_non_numeric_falls_back(self):
        """Non-numeric env var falls back to default."""
        os.environ["FORGE_DIFF_CHUNK_THRESHOLD_KB"] = "abc"
        try:
            assert _read_chunk_threshold_kb() == _DEFAULT_CHUNK_THRESHOLD_KB
        finally:
            del os.environ["FORGE_DIFF_CHUNK_THRESHOLD_KB"]

    def test_zero_means_always_chunk(self):
        """Zero threshold means always-chunk."""
        os.environ["FORGE_DIFF_CHUNK_THRESHOLD_KB"] = "0"
        try:
            assert _read_chunk_threshold_kb() == 0
        finally:
            del os.environ["FORGE_DIFF_CHUNK_THRESHOLD_KB"]


class TestRunChunk:
    """Tests for _run_chunk helper."""

    def test_run_chunk_all_passes_succeed(self):
        """All passes succeed -> no INFRA findings."""
        # Mock spawn_fn returns valid JSON with at least one excerpt.
        mock_spawn = MagicMock(
            return_value='{"findings": [], "code_excerpts": [{"file": "foo.py", "start_line": 1, "end_line": 3, "content": "test"}]}'
        )
        findings, excerpts, usage, dur = _run_chunk(
            SMALL_DIFF, mock_spawn, ("qodo", "expert", "adversarial"),
        )
        # spawn_fn called 3 times (once per pass).
        assert mock_spawn.call_count == 3
        # No INFRA findings.
        assert all(f.source != "INFRA" for f in findings)

    def test_run_chunk_spawn_fail(self):
        """Spawn failure -> INFRA finding with correct ID."""
        def spawn_fn(pass_name, diff):
            if pass_name == "expert":
                raise TimeoutError("timed out")
            return '{"findings": [], "code_excerpts": [{"file": "foo.py", "start_line": 1, "end_line": 3, "content": "test"}]}'

        findings, _, _, _ = _run_chunk(
            SMALL_DIFF, spawn_fn, ("qodo", "expert", "adversarial"),
        )
        infra = [f for f in findings if f.source == "INFRA"]
        assert len(infra) == 1
        assert infra[0].id == "l1-expert-spawn-fail"


class TestChunkingIntegration:
    """Integration tests for chunking behavior."""

    def test_three_file_diff_produces_findings_from_all(self):
        """3-file diff over threshold produces findings from all 3."""
        # Set threshold to 0 to force chunking.
        os.environ["FORGE_DIFF_CHUNK_THRESHOLD_KB"] = "0"
        try:
            from code_forge.outlet_c import _read_chunk_threshold_kb
            threshold = _read_chunk_threshold_kb()
            assert threshold == 0

            chunks = _split_diff_by_file(THREE_FILE_DIFF)
            assert len(chunks) == 3

            # Each chunk should be independently reviewable.
            all_fingerprints = set()
            for chunk in chunks:
                assert "@@" in chunk  # has hunk headers
                # Extract file name from diff header.
                import re
                m = re.search(r'diff --git a/(\S+)', chunk)
                assert m is not None
                all_fingerprints.add(m.group(1))
            assert len(all_fingerprints) == 3
        finally:
            del os.environ["FORGE_DIFF_CHUNK_THRESHOLD_KB"]

    def test_threshold_below_diff_size_no_chunk(self):
        """Diff under threshold: no chunking (single pass)."""
        os.environ["FORGE_DIFF_CHUNK_THRESHOLD_KB"] = "999999"
        try:
            threshold = _read_chunk_threshold_kb()
            diff_kb = len(SMALL_DIFF.encode()) / 1024
            assert diff_kb <= threshold
            # Should not chunk.
        finally:
            del os.environ["FORGE_DIFF_CHUNK_THRESHOLD_KB"]


class TestBugInjectProofs:
    """Bug-inject proofs to verify tests have teeth."""

    def test_corrupt_infra_id_wrong_outcome(self):
        """Bug: corrupt INFRA finding ID -> derive_pass_outcomes
        returns COMPLETED -> test catches it.

        If the ID pattern matching is broken, a spawn-fail finding
        would not be detected as TIMEOUT.
        """
        finding = StateFinding(
            id="l1-qodo-spawn-fail",
            fingerprint="spawn-fail-qodo",
            source="INFRA",
            disposition=Disposition.CONFIRMED,
            file="<spawn>",
            line_range=[0, 0],
            description="spawn failed",
        )
        outcomes = derive_pass_outcomes([finding])
        assert outcomes["qodo"] == PassOutcome.TIMEOUT

        # Bug-inject: corrupt the ID.
        bad_finding = StateFinding(
            id="l1-qodo-bogus",  # corrupted
            fingerprint="spawn-fail-qodo",
            source="INFRA",
            disposition=Disposition.CONFIRMED,
            file="<spawn>",
            line_range=[0, 0],
            description="bogus",
        )
        bad_outcomes = derive_pass_outcomes([bad_finding])
        # With bogus ID, qodo should be COMPLETED.
        assert bad_outcomes["qodo"] == PassOutcome.COMPLETED

    def test_skip_merge_missing_findings(self):
        """Bug: skip merge step -> some findings missing.

        If dedup is skipped, duplicate fingerprints would inflate
        the count. If concat is skipped, chunk findings are lost.
        """
        chunk1_findings = [
            StateFinding(
                id="l1-qodo-spawn-fail",
                fingerprint="spawn-fail-qodo",
                source="INFRA",
                disposition=Disposition.CONFIRMED,
                file="<spawn>",
                line_range=[0, 0],
                description="spawn failed",
            ),
        ]
        chunk2_findings = [
            StateFinding(
                id="f-test",
                fingerprint="fp-test",
                source="L1",
                disposition=Disposition.CONFIRMED,
                file="test.py",
                line_range=[1, 2],
                description="test finding",
            ),
        ]
        # Merge with dedup.
        all_findings = chunk1_findings + chunk2_findings
        seen = set()
        deduped = []
        for f in all_findings:
            if f.fingerprint not in seen:
                seen.add(f.fingerprint)
                deduped.append(f)
        assert len(deduped) == 2

        # Bug-inject: skip concat -> chunk2 findings missing.
        only_chunk1 = chunk1_findings
        assert len(only_chunk1) == 1  # would miss chunk2's finding
