"""Tests for evidence re-verify: catch findings whose cite does not exist.

A fresh-context reviewer can fabricate a plausible-looking finding that cites
a file or line not actually in the reviewed tree. reverify_finding_cites
re-checks each finding's (file, line) against the real source and splits
findings into those whose cite holds up and those that do not.
"""
from __future__ import annotations

import os

from code_forge.evidence import reverify_finding_cites


def _finding(file: str, line: int) -> dict:
    return {"file": file, "line": line, "severity": "high", "description": "d"}


def _lookup_from(files: dict[str, list[str]]):
    def lookup(path: str):
        return files.get(os.path.normpath(path))

    return lookup


def test_finding_with_real_file_and_line_verifies() -> None:
    findings = [_finding("src/a.py", 3)]
    lookup = _lookup_from({"src/a.py": ["l1", "l2", "l3", "l4"]})

    result = reverify_finding_cites(findings, lookup)

    assert result.verified == (findings[0],)
    assert result.unverified == ()


def test_finding_citing_missing_file_is_unverified() -> None:
    findings = [_finding("src/ghost.py", 3)]
    lookup = _lookup_from({"src/a.py": ["l1"]})

    result = reverify_finding_cites(findings, lookup)

    assert result.unverified == (findings[0],)
    assert result.verified == ()


def test_finding_citing_line_past_eof_is_unverified() -> None:
    findings = [_finding("src/a.py", 99)]
    lookup = _lookup_from({"src/a.py": ["l1", "l2"]})

    result = reverify_finding_cites(findings, lookup)

    assert result.unverified == (findings[0],)


def test_finding_at_last_line_verifies() -> None:
    findings = [_finding("src/a.py", 2)]
    lookup = _lookup_from({"src/a.py": ["l1", "l2"]})

    result = reverify_finding_cites(findings, lookup)

    assert result.verified == (findings[0],)


def test_file_level_finding_line_zero_verifies_when_file_exists() -> None:
    # A finding with no specific line makes no checkable line claim; if the
    # file is real, there is nothing to falsify.
    findings = [_finding("src/a.py", 0)]
    lookup = _lookup_from({"src/a.py": ["l1"]})

    result = reverify_finding_cites(findings, lookup)

    assert result.verified == (findings[0],)


def test_mixed_findings_split_in_order() -> None:
    good = _finding("src/a.py", 1)
    bad_file = _finding("src/ghost.py", 1)
    bad_line = _finding("src/a.py", 50)
    lookup = _lookup_from({"src/a.py": ["only one line"]})

    result = reverify_finding_cites([good, bad_file, bad_line], lookup)

    assert result.verified == (good,)
    assert result.unverified == (bad_file, bad_line)


def test_empty_findings_yields_empty() -> None:
    result = reverify_finding_cites([], _lookup_from({}))

    assert result.verified == ()
    assert result.unverified == ()
