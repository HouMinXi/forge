"""Shared helpers for the reviewer-JSON finding shape.

A finding is a mapping with at least "file", "line", "severity", and
"description" (validated by reviewer_json.py). The canary gate (canary.py) and
evidence re-verify (evidence.py) both need to read a finding's line robustly,
so the parsing lives here rather than being duplicated.

Public functions: finding_line
"""
from __future__ import annotations

from collections.abc import Mapping


def finding_line(finding: Mapping[str, object]) -> int:
    """Return a finding's 1-based line, or 0 when absent or unparseable.

    A 0 result means the finding makes no specific line claim (a file-level
    finding); callers treat that as "no locatable assertion to check".
    """
    try:
        return int(finding.get("line") or 0)
    except (TypeError, ValueError):
        return 0
