# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for forge.verdict -- PASS/FAIL determination."""

from code_forge import EXIT_PASS, EXIT_FAIL
from code_forge.parsers.base import Finding, ToolError
from code_forge.verdict import Verdict, determine_verdict


def _make_finding(**kwargs):
    """Helper to create a Finding with defaults."""
    defaults = {
        "file": "test.py",
        "line": 1,
        "end_line": 1,
        "column": 0,
        "rule_id": "E001",
        "level": "error",
        "message": "test error",
        "tool_name": "test_tool",
    }
    defaults.update(kwargs)
    return Finding(**defaults)


def _make_tool_error(**kwargs):
    """Helper to create a ToolError with defaults."""
    defaults = {
        "tool_name": "test_tool",
        "exit_code": 2,
        "stderr": "segfault",
        "message": "tool crashed",
    }
    defaults.update(kwargs)
    return ToolError(**defaults)


class TestDetermineVerdict:
    """Tests for determine_verdict function."""

    def test_empty_findings_returns_pass(self):
        """Empty findings list -> PASS."""
        verdict, code = determine_verdict([])
        assert verdict == "PASS"
        assert code == EXIT_PASS
        assert code == 0

    def test_findings_returns_fail(self):
        """Non-empty Finding list -> FAIL."""
        findings = [_make_finding()]
        verdict, code = determine_verdict(findings)
        assert verdict == "FAIL"
        assert code == EXIT_FAIL
        assert code == 1

    def test_tool_error_returns_fail(self):
        """ToolError in list -> FAIL (Consensus #4)."""
        errors = [_make_tool_error()]
        verdict, code = determine_verdict(errors)
        assert verdict == "FAIL"
        assert code == EXIT_FAIL

    def test_mixed_finding_and_tool_error_returns_fail(self):
        """Mixed Finding + ToolError -> FAIL."""
        items = [_make_finding(), _make_tool_error()]
        verdict, code = determine_verdict(items)
        assert verdict == "FAIL"
        assert code == EXIT_FAIL

    def test_multiple_findings_returns_fail(self):
        """Multiple findings -> FAIL."""
        findings = [
            _make_finding(rule_id="E001"),
            _make_finding(rule_id="E002"),
            _make_finding(rule_id="E003"),
        ]
        verdict, code = determine_verdict(findings)
        assert verdict == "FAIL"
        assert code == EXIT_FAIL

    def test_deterministic_same_input_same_output(self):
        """Same findings always produce same verdict (GATE-02)."""
        findings = [
            _make_finding(rule_id="E001"),
            _make_tool_error(tool_name="ruff"),
        ]
        results = [determine_verdict(findings) for _ in range(10)]
        assert all(r == ("FAIL", EXIT_FAIL) for r in results)

    def test_uses_exit_constants(self):
        """Verify EXIT_PASS and EXIT_FAIL are the formal constants."""
        assert EXIT_PASS == 0
        assert EXIT_FAIL == 1
        _, pass_code = determine_verdict([])
        _, fail_code = determine_verdict([_make_finding()])
        assert pass_code is EXIT_PASS or pass_code == EXIT_PASS
        assert fail_code is EXIT_FAIL or fail_code == EXIT_FAIL

    def test_verdict_type_alias(self):
        """Verdict is a tuple[str, int] type alias."""
        result = determine_verdict([])
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], int)
