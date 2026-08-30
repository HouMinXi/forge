# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for L0 crash guard: nonzero exit + empty stdout = ToolError."""
from pathlib import Path
from unittest.mock import MagicMock, patch


from code_forge.machine import _default_l0_runner
from code_forge.parsers.base import Finding


def _make_registry(tool_name="test-tool", output_format="sarif"):
    """Create a minimal registry entry."""
    tc = MagicMock()
    tc.output_format = output_format
    tc.command = "test-tool"
    return {tool_name: tc}


class TestL0CrashGuard:
    """Nonzero exit + empty stdout must produce ToolError, not []."""

    @patch("code_forge.runner.run_tools")
    @patch("code_forge.parsers.parse_output", return_value=[])
    def test_crash_produces_toolerror(self, mock_parse, mock_run):
        """Nonzero exit + empty stdout -> ToolError."""
        mock_run.return_value = (
            {"test-tool": ("", 2, "some stderr")},
            {},
            [],
            [],
        )
        findings, infra = _default_l0_runner(
            _make_registry(), [Path("test.c")]
        )
        assert len(findings) == 0
        assert len(infra) == 1
        assert "exited 2" in infra[0]
        assert "some stderr" in infra[0]

    @patch("code_forge.runner.run_tools")
    @patch("code_forge.parsers.parse_output", return_value=[])
    def test_clean_run_no_toolerror(self, mock_parse, mock_run):
        """Zero exit + empty stdout -> no ToolError (clean run)."""
        mock_run.return_value = (
            {"test-tool": ("", 0, "")},
            {},
            [],
            [],
        )
        findings, infra = _default_l0_runner(
            _make_registry(), [Path("test.c")]
        )
        assert len(findings) == 0
        assert len(infra) == 0

    @patch("code_forge.runner.run_tools")
    @patch("code_forge.parsers.parse_output")
    def test_nonempty_stdout_no_guard(self, mock_parse, mock_run):
        """Nonzero exit + non-empty stdout -> parser handles it, not guard."""
        mock_parse.return_value = [
            Finding(file="test.c", line=1, end_line=1, column=0,
                    rule_id="test", level="error", message="test",
                    tool_name="test-tool"),
        ]
        mock_run.return_value = (
            {"test-tool": ("sarif output here", 1, "")},
            {},
            [],
            [],
        )
        findings, infra = _default_l0_runner(
            _make_registry(), [Path("test.c")]
        )
        assert len(findings) == 1
        assert len(infra) == 0

    @patch("code_forge.runner.run_tools")
    @patch("code_forge.parsers.parse_output", return_value=[])
    def test_guard_disabled_makes_crash_silent(self, mock_parse, mock_run):
        """Removing the guard makes crash produce silent []."""
        mock_run.return_value = (
            {"test-tool": ("", 2, "crash stderr")},
            {},
            [],
            [],
        )
        # With guard active: infra has 1 error
        findings, infra = _default_l0_runner(
            _make_registry(), [Path("test.c")]
        )
        assert len(infra) == 1, "guard must catch crash"
