"""Tests for eval corpus loader (corpus.py)."""
from __future__ import annotations

from pathlib import Path

import pytest

from code_forge.eval.corpus import CorpusEntry, load_corpus


class TestCorpusEntry:
    """CorpusEntry frozen dataclass tests."""

    def test_construction(self) -> None:
        entry = CorpusEntry(
            name="gate-yaml-rce",
            diff_file="diffs/gate-yaml-rce.diff",
            expected_verdict="HOLD",
            axis_tags=["TRUST", "SEC"],
        )
        assert entry.name == "gate-yaml-rce"
        assert entry.diff_file == "diffs/gate-yaml-rce.diff"
        assert entry.expected_verdict == "HOLD"
        assert entry.axis_tags == ["TRUST", "SEC"]

    def test_frozen(self) -> None:
        entry = CorpusEntry(
            name="x", diff_file="x.diff",
            expected_verdict="HOLD", axis_tags=[],
        )
        with pytest.raises(AttributeError):
            entry.name = "changed"  # type: ignore[misc]


class TestLoadCorpus:
    """load_corpus function tests."""

    def test_load_basic(self, tmp_path: Path) -> None:
        diff_dir = tmp_path / "diffs"
        diff_dir.mkdir()
        (diff_dir / "rce.diff").write_text("--- a/f\n+++ b/f\n")

        manifest = tmp_path / "corpus.yaml"
        manifest.write_text(
            "entries:\n"
            "  - name: rce\n"
            "    diff_file: diffs/rce.diff\n"
            "    expected_verdict: HOLD\n"
            "    axis_tags: [TRUST]\n"
        )
        entries = load_corpus(manifest)
        assert len(entries) == 1
        assert entries[0].name == "rce"
        assert entries[0].expected_verdict == "HOLD"
        assert entries[0].axis_tags == ["TRUST"]

    def test_load_multiple_entries(self, tmp_path: Path) -> None:
        manifest = tmp_path / "corpus.yaml"
        manifest.write_text(
            "entries:\n"
            "  - name: a\n"
            "    diff_file: a.diff\n"
            "    expected_verdict: HOLD\n"
            "    axis_tags: [SEC]\n"
            "  - name: b\n"
            "    diff_file: b.diff\n"
            "    expected_verdict: PASS\n"
            "    axis_tags: [RUNTIME]\n"
        )
        entries = load_corpus(manifest)
        assert len(entries) == 2
        assert entries[0].name == "a"
        assert entries[1].name == "b"
        assert entries[1].expected_verdict == "PASS"

    def test_empty_manifest(self, tmp_path: Path) -> None:
        manifest = tmp_path / "corpus.yaml"
        manifest.write_text("")
        entries = load_corpus(manifest)
        assert entries == []

    def test_no_entries_key(self, tmp_path: Path) -> None:
        manifest = tmp_path / "corpus.yaml"
        manifest.write_text("some_other_key: 42\n")
        entries = load_corpus(manifest)
        assert entries == []

    def test_missing_diff_file_still_creates_entry(self, tmp_path: Path) -> None:
        """Missing diff file at load time: CorpusEntry still created (D-12)."""
        manifest = tmp_path / "corpus.yaml"
        manifest.write_text(
            "entries:\n"
            "  - name: missing\n"
            "    diff_file: diffs/nonexistent.diff\n"
            "    expected_verdict: HOLD\n"
            "    axis_tags: [TRUST]\n"
        )
        entries = load_corpus(manifest)
        assert len(entries) == 1
        assert entries[0].name == "missing"
        assert entries[0].diff_file == "diffs/nonexistent.diff"

    def test_malformed_yaml(self, tmp_path: Path) -> None:
        manifest = tmp_path / "corpus.yaml"
        manifest.write_text("{{{{not valid yaml")
        with pytest.raises(ValueError, match="Failed to parse"):
            load_corpus(manifest)

    def test_diff_file_resolved_relative(self, tmp_path: Path) -> None:
        """diff_file is stored as-is (relative to manifest parent)."""
        manifest = tmp_path / "corpus.yaml"
        manifest.write_text(
            "entries:\n"
            "  - name: test\n"
            "    diff_file: sub/dir/test.diff\n"
            "    expected_verdict: PASS\n"
            "    axis_tags: []\n"
        )
        entries = load_corpus(manifest)
        assert entries[0].diff_file == "sub/dir/test.diff"
