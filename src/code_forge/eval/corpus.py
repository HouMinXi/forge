"""Eval corpus loader: YAML manifest + CorpusEntry dataclass.

Loads a YAML manifest of self-contained diff files with expected verdicts
and axis tags. Each entry becomes a CorpusEntry that the runner replays
through the complete forge pipeline.

Missing diff files at load time do NOT raise -- the CorpusEntry is still
created. SKIPPED handling happens at run time
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


def valid_line_range(raw_range) -> bool:
    """A semantically valid forge line range: two non-bool ints,
    1-based, ordered. Shared by the manifest parser and
    finding_hit so the two cannot drift."""
    return (
        isinstance(raw_range, (list, tuple))
        and len(raw_range) == 2
        and all(
            isinstance(v, int) and not isinstance(v, bool)
            for v in raw_range
        )
        and raw_range[0] >= 1
        and raw_range[1] >= raw_range[0]
    )


@dataclass(frozen=True)
class ExpectedFinding:
    """One findings-level answer in the corpus.

    A run "hits" this finding when one of its CONFIRMED findings
    matches: same file AND (overlapping line range, or shared
    description terms when either side has no range).

    Fields:
        file: path of the file the finding lives in.
        line_range: optional (start, end) 1-based inclusive range.
        description: one-sentence statement of the defect.
    """

    file: str
    description: str
    line_range: tuple[int, int] | None = None


@dataclass(frozen=True)
class CorpusEntry:
    """A single entry in the eval corpus manifest.

    Fields:
        name: human-readable identifier for this entry.
        diff_file: path to .diff file, relative to manifest parent.
        expected_verdict: "HOLD" or "PASS" -- what forge should produce.
        axis_tags: which review axes this entry exercises (e.g. TRUST, SEC).
        expected_advisory: keyword strings for advisory axis scoring.
            RUNTIME entries list keywords that must appear in advisory text for
            the entry to be counted as "caught" by the RUNTIME axis. Empty list
            (default) means no advisory scoring for this entry.
        expected_findings: findings-level answer key. A non-empty list
            adds findings scoring on top of the verdict quadrant: each
            expected finding must appear in the run's CONFIRMED findings.
            Empty (default) keeps the verdict-only scoring unchanged.
    """

    name: str
    diff_file: str
    expected_verdict: str
    axis_tags: list[str]
    expected_advisory: list[str] = field(default_factory=list)
    expected_findings: list[ExpectedFinding] = field(default_factory=list)


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
        verdict = raw.get("expected_verdict")
        if verdict not in ("HOLD", "PASS"):
            raise ValueError(
                "invalid expected_verdict %r in corpus entry %s"
                % (verdict, raw.get("name", "?"))
            )
        entries.append(
            CorpusEntry(
                name=raw["name"],
                diff_file=raw["diff_file"],
                expected_verdict=verdict,
                axis_tags=list(raw.get("axis_tags", [])),
                expected_advisory=list(raw.get("expected_advisory", [])),
                expected_findings=_parse_expected_findings(
                    raw.get("expected_findings", []),
                    raw["name"],
                ),
            )
        )
    return entries


def _parse_expected_findings(
    raw: list[dict], entry_name: str
) -> list[ExpectedFinding]:
    """Validate and build ExpectedFinding list from manifest YAML.

    Strict on shape (a malformed answer key would silently poison the
    findings scoring): file must be a non-empty string, description a
    non-empty string, line_range absent or a two-int list.
    """
    findings: list[ExpectedFinding] = []
    if raw is None:
        # An explicit empty expected_findings key means no answer key,
        # same as the absent key.
        raw = []
    if not isinstance(raw, list):
        raise ValueError(
            "expected_findings in %r must be a list" % entry_name
        )
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(
                "expected_findings entry in %r is not a mapping"
                % entry_name
            )
        file = item.get("file")
        description = item.get("description")
        if not isinstance(file, str) or not file.strip():
            raise ValueError(
                "expected_findings entry in %r missing file" % entry_name
            )
        if not isinstance(description, str) or not description.strip():
            raise ValueError(
                "expected_findings entry in %r missing description"
                % entry_name
            )
        if not re.search(r"[a-zA-Z0-9_]", description):
            raise ValueError(
                "expected_findings entry in %r has a description with "
                "no alphanumeric token -- it could never match"
                % entry_name
            )
        # Store the stripped values: validation and matching must
        # agree, or a whitespace-padded value validates and then can
        # never match.
        file = file.strip()
        description = description.strip()

        line_range = None
        raw_range = item.get("line_range")
        if raw_range is not None:
            if not valid_line_range(raw_range):
                raise ValueError(
                    "expected_findings entry in %r has invalid "
                    "line_range %r" % (entry_name, raw_range)
                )
            line_range = (raw_range[0], raw_range[1])
        findings.append(
            ExpectedFinding(
                file=file,
                description=description,
                line_range=line_range,
            )
        )
    return findings
