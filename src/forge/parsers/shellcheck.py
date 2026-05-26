# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Parse shellcheck JSON output into Finding objects."""

import json

from forge.parsers.base import Finding, ToolError


def parse_shellcheck(
    output: str,
    tool_name: str = "shellcheck",
    exit_code: int = 0,
) -> list[Finding | ToolError]:
    """Parse shellcheck -f json output.

    Returns:
        [] on empty string (clean run).
        [Finding, ...] on valid output with findings.
        [ToolError] on malformed/unparseable output.
    """
    if not output.strip():
        return []
    try:
        raw = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return [ToolError(
            tool_name=tool_name,
            exit_code=exit_code,
            stderr="",
            message=f"Failed to parse {tool_name} JSON output",
        )]

    findings = []
    try:
        for item in raw:
            findings.append(Finding(
                file=item["file"],
                line=item["line"],
                # Round 3 H-1: use `or` to handle JSON null values
                end_line=(item.get("endLine") or item["line"]),
                column=(item.get("column") or 0),
                rule_id=f"SC{item['code']}",
                level=item.get("level", "warning"),
                message=item["message"],
                tool_name=tool_name,
                fix=None,
            ))
    except (KeyError, TypeError, AttributeError):
        return [ToolError(
            tool_name=tool_name,
            exit_code=exit_code,
            stderr="",
            message=f"Failed to parse {tool_name} output: missing fields",
        )]
    return findings
