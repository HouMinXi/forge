# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Parse semgrep SARIF output into Finding objects."""

from code_forge.parsers.base import Finding, ToolError
from code_forge.parsers._sarif import _parse_sarif


def parse_semgrep(
    output: str,
    tool_name: str = "semgrep",
    exit_code: int = 0,
) -> list[Finding | ToolError]:
    """Parse semgrep --sarif output.

    Thin wrapper around shared SARIF parser with tool_name="semgrep".
    """
    return _parse_sarif(output, tool_name=tool_name, exit_code=exit_code)
