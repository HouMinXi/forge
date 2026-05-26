# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Parse ruff SARIF output into Finding objects."""

from forge.parsers.base import Finding, ToolError
from forge.parsers._sarif import _parse_sarif


def parse_ruff(
    output: str,
    tool_name: str = "ruff",
    exit_code: int = 0,
) -> list[Finding | ToolError]:
    """Parse ruff --output-format sarif output.

    Thin wrapper around shared SARIF parser with tool_name="ruff".
    """
    return _parse_sarif(output, tool_name=tool_name, exit_code=exit_code)
