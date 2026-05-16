# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Parse checkpatch.pl emacs-format output into Finding objects."""

import re

from forge.parsers.base import Finding, ToolError

# checkpatch --emacs --show-types output format:
# file.c:42: WARNING:LONG_LINE: line length 82 exceeds 80 columns
_CHECKPATCH_RE = re.compile(
    r"^(.+):(\d+):\s+(WARNING|ERROR|CHECK):(\S+):\s+(.+)$"
)

_SUMMARY_RE = re.compile(r"^total:\s+", re.IGNORECASE)


def parse_checkpatch(
    output: str,
    tool_name: str = "checkpatch",
    exit_code: int = 0,
) -> list[Finding | ToolError]:
    """Parse checkpatch.pl --emacs output.

    Returns:
        [] on empty string (clean run).
        [Finding, ...] on valid output with violations.
        [ToolError] if non-empty input has no regex matches AND no
                    summary line (indicates corrupt output).
    """
    if not output.strip():
        return []

    findings = []
    has_summary = False

    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _SUMMARY_RE.match(stripped):
            has_summary = True
            continue
        m = _CHECKPATCH_RE.match(stripped)
        if m:
            findings.append(Finding(
                file=m.group(1),
                line=int(m.group(2)),
                end_line=int(m.group(2)),
                column=0,
                rule_id=m.group(4),
                level=m.group(3).lower(),
                message=m.group(5),
                tool_name=tool_name,
            ))

    # Non-empty input, zero matches, no summary -> corrupt
    if not findings and not has_summary:
        return [ToolError(
            tool_name=tool_name,
            exit_code=exit_code,
            stderr="",
            message=f"Failed to parse {tool_name} output: no matches",
        )]

    return findings
