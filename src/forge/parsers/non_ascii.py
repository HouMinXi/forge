# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Parse grep -Pn non-ASCII output into Finding objects."""

import re

from forge.parsers.base import Finding, ToolError

# grep -Pn output: filename:lineno:content
_GREP_LINE_RE = re.compile(r"^(.+?):(\d+):(.+)$")


def parse_non_ascii(
    output: str,
    tool_name: str = "non_ascii",
    exit_code: int = 0,
) -> list[Finding | ToolError]:
    """Parse grep -Pn '[^\\x00-\\x7F]' output.

    Returns:
        [] on empty string (clean run).
        [Finding, ...] on valid grep output with matches.
        [] on non-matching lines (grep found nothing parseable).
    """
    if not output.strip():
        return []

    findings = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m = _GREP_LINE_RE.match(stripped)
        if m:
            content = m.group(3).strip()
            findings.append(Finding(
                file=m.group(1),
                line=int(m.group(2)),
                end_line=int(m.group(2)),
                column=0,
                rule_id="NON_ASCII",
                level="error",
                message=f"non-ASCII character found: {content}",
                tool_name=tool_name,
            ))

    return findings
