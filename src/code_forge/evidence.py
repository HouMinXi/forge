"""Evidence re-verify: detect findings whose cite does not hold up.

A fresh-context reviewer can return a plausible finding that cites a file or
line not present in the reviewed tree (the most common code-review
hallucination is inventing a plausible-but-nonexistent location). Before the
verdict trusts a finding, reverify_finding_cites re-checks each finding's
(file, line) against the real source via a caller-supplied lookup and splits
findings into verified and unverified.

A finding with no specific line (line <= 0) is a file-level claim: if the file
exists it verifies, since there is no line assertion to falsify. The laziness
signal lives in the canary gate (canary.py); this module only catches
fabricated or stale citations.

Public types: CiteVerification, reverify_finding_cites
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from .findings import finding_line


@dataclass(frozen=True)
class CiteVerification:
    """Findings split by whether their (file, line) cite re-verifies."""

    verified: tuple[Mapping[str, object], ...]
    unverified: tuple[Mapping[str, object], ...]


def _cite_verifies(
    finding: Mapping[str, object],
    source_lookup: Callable[[str], Sequence[str] | None],
) -> bool:
    lines = source_lookup(str(finding.get("file") or ""))
    if lines is None:
        return False  # cited file is absent from the reviewed tree
    line = finding_line(finding)
    if line <= 0:
        return True  # file-level claim; the file is real, no line to falsify
    return line <= len(lines)


def reverify_finding_cites(
    findings: Sequence[Mapping[str, object]],
    source_lookup: Callable[[str], Sequence[str] | None],
) -> CiteVerification:
    """Split findings by whether their cite re-verifies against real source.

    source_lookup maps a finding's file path to that file's lines, or None
    when the file does not exist in the reviewed tree. A finding verifies when
    its file exists and its line (if any) is within the file; otherwise it is
    unverified and should be treated as a possible fabrication. Order is
    preserved within each group.
    """
    verified: list[Mapping[str, object]] = []
    unverified: list[Mapping[str, object]] = []
    for finding in findings:
        if _cite_verifies(finding, source_lookup):
            verified.append(finding)
        else:
            unverified.append(finding)
    return CiteVerification(
        verified=tuple(verified), unverified=tuple(unverified)
    )
