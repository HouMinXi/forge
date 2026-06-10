"""Eval corpus loader: YAML manifest + CorpusEntry dataclass (D-07).

Loads a YAML manifest of self-contained diff files with expected verdicts
and axis tags. Each entry becomes a CorpusEntry that the runner replays
through the complete forge pipeline.

Missing diff files at load time do NOT raise -- the CorpusEntry is still
created. SKIPPED handling happens at run time per D-12.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class CorpusEntry:
    """A single entry in the eval corpus manifest.

    Fields:
        name: human-readable identifier for this entry.
        diff_file: path to .diff file, relative to manifest parent.
        expected_verdict: "HOLD" or "PASS" -- what forge should produce.
        axis_tags: which review axes this entry exercises (e.g. TRUST, SEC).
    """

    name: str
    diff_file: str
    expected_verdict: str
    axis_tags: list[str]


def load_corpus(manifest_path: Path) -> list[CorpusEntry]:
    """Load a corpus manifest YAML file and return list of CorpusEntry.

    Args:
        manifest_path: path to the corpus.yaml manifest file.

    Returns:
        List of CorpusEntry. Empty list if manifest is empty or has no
        ``entries`` key.

    Raises:
        ValueError: if the YAML is malformed (parse error).
    """
    text = manifest_path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(
            f"Failed to parse corpus manifest {manifest_path}: {exc}"
        ) from exc

    if data is None:
        return []

    raw_entries = data.get("entries")
    if not raw_entries:
        return []

    entries: list[CorpusEntry] = []
    for raw in raw_entries:
        entries.append(
            CorpusEntry(
                name=raw["name"],
                diff_file=raw["diff_file"],
                expected_verdict=raw["expected_verdict"],
                axis_tags=list(raw.get("axis_tags", [])),
            )
        )
    return entries
