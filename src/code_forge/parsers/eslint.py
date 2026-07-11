# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Parse eslint --format json into Finding objects.

eslint --format json emits a JSON array with one object per file,
each containing a "messages" array. Each message has: ruleId, severity
(1=warning, 2=error), message, line, column, endLine, endColumn.

Verified against eslint 8.57.1 actual output.
"""

import json

from code_forge.parsers.base import Finding, ToolError

# eslint severity -> base.Finding.level
_SEVERITY_MAP: dict[int, str] = {
    1: "warning",
    2: "error",
}


def parse_eslint(
    output: str,
    tool_name: str = "eslint",
    exit_code: int = 0,
) -> list[Finding | ToolError]:
    """Parse eslint JSON output into Finding objects.

    Returns:
        [] on empty string (clean run).
        [Finding, ...] on valid JSON with messages.
        [ToolError] on malformed/unparseable output.
    """
    if not output.strip():
        return []
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return [ToolError(
            tool_name=tool_name,
            exit_code=exit_code,
            stderr="",
            message="Failed to parse %s JSON output" % tool_name,
        )]

    findings: list[Finding | ToolError] = []
    for entry in data:
        filepath = entry.get("filePath", "")
        for msg in entry.get("messages", []):
            rule_id = msg.get("ruleId", "unknown")
            severity = msg.get("severity", 1)
            level = _SEVERITY_MAP.get(severity, "warning")
            findings.append(Finding(
                file=filepath,
                line=msg.get("line", 0),
                end_line=msg.get("endLine", msg.get("line", 0)),
                column=msg.get("column", 0),
                rule_id=rule_id,
                level=level,
                message=msg.get("message", ""),
                tool_name=tool_name,
            ))
    return findings
