# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Parse flake8 default text output into Finding objects.

flake8 default output format is one finding per line:
  path:row:col: CODE message

This parser mirrors parsers/checkpatch.py in structure: compiled
module-level regex, empty -> [], match -> Finding, non-empty with
zero matches -> [ToolError] (corrupt output, Consensus #4).
"""

import re

from code_forge.parsers.base import Finding, ToolError

# flake8 output: "src/foo.py:10:1: E501 line too long"
# group(1) = file (non-greedy .+? up to first :row:col:)
# group(2) = row (int)
# group(3) = col (int)
# group(4) = code (e.g. E501, W605, F401, C901)
# group(5) = message (rest of line, may contain colons)
_FLAKE8_RE = re.compile(r"^(.+?):(\d+):(\d+):\s+([A-Z]+[0-9]+)\s+(.*)$")


def parse_flake8(
    output: str,
    tool_name: str = "flake8",
    exit_code: int = 0,
) -> list[Finding | ToolError]:
    """Parse flake8 default text output.

    Returns:
        [] on empty string (clean run).
        [Finding, ...] on valid output with violations.
        [ToolError] if non-empty input has no regex matches
                    (indicates corrupt output).
    """
    if not output.strip():
        return []

    findings = []

    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m = _FLAKE8_RE.match(stripped)
        if m:
            # flake8 emits no severity; map all to "warning"
            # (honest default within the "error"|"warning"|"note"
            # enum in base.Finding; flake8 lint findings are advisory)
            findings.append(Finding(
                file=m.group(1),
                line=int(m.group(2)),
                end_line=int(m.group(2)),
                column=int(m.group(3)),
                rule_id=m.group(4),
                level="warning",
                message=m.group(5).strip(),
                tool_name=tool_name,
            ))

    # Non-empty input, zero matches -> corrupt
    # (flake8 has no summary line, unlike checkpatch)
    if not findings:
        return [ToolError(
            tool_name=tool_name,
            exit_code=exit_code,
            stderr="",
            message="Failed to parse %s output: no matches" % tool_name,
        )]

    return findings
