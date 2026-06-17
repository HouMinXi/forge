"""Tests for the shared finding helpers."""
from __future__ import annotations

from code_forge.findings import finding_line


def test_returns_int_line_when_present() -> None:
    assert finding_line({"file": "a.py", "line": 42}) == 42


def test_missing_line_returns_zero() -> None:
    assert finding_line({"file": "a.py"}) == 0


def test_none_line_returns_zero() -> None:
    assert finding_line({"file": "a.py", "line": None}) == 0


def test_string_digits_are_coerced() -> None:
    assert finding_line({"file": "a.py", "line": "17"}) == 17


def test_unparseable_line_returns_zero() -> None:
    assert finding_line({"file": "a.py", "line": "not-a-number"}) == 0


def test_zero_line_returns_zero() -> None:
    assert finding_line({"file": "a.py", "line": 0}) == 0
