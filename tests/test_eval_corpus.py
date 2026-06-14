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


class TestCorpusEntriesApply:
    """Guard: every corpus entry's diff must apply against its base_files seed."""

    CORPUS_DIR = Path(__file__).parent / "eval" / "corpus"

    @pytest.mark.parametrize("entry_name", [
        "gate-yaml-rce",
        "E1-stale-nftables",
        "E2-pcap-suffix",
        "E3-transit-probe",
        "E4-curl-tproxy",
        "E5-fast-502",
        "E6-reprobe-blackout",
        "BUG-P12-01",
        "ttl_class",
        "E8-blast-radius-llm-invoke",
    ])
    def test_corpus_entry_applies(self, entry_name: str, tmp_path: Path) -> None:
        """Each corpus diff must git-apply against its base_files seed."""
        import shutil
        import subprocess

        diff_path = self.CORPUS_DIR / "diffs" / f"{entry_name}.diff"
        assert diff_path.exists(), f"diff not found: {diff_path}"

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=str(repo), capture_output=True, check=True,
        )

        base_dir = self.CORPUS_DIR / "base_files" / entry_name
        if base_dir.is_dir():
            shutil.copytree(str(base_dir), str(repo), dirs_exist_ok=True)

        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(repo), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "-c", "user.name=test", "-c", "user.email=t@t",
             "commit", "--allow-empty", "-m", "init"],
            cwd=str(repo), capture_output=True, check=True,
        )

        result = subprocess.run(
            ["git", "apply", "--check", str(diff_path.resolve())],
            cwd=str(repo), capture_output=True, check=False,
        )
        stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
        assert result.returncode == 0, (
            f"{entry_name}: git apply --check failed: {stderr}"
        )
