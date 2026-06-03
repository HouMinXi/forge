# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Parse pylint --output-format=json into Finding objects.

pylint --output-format=json emits a SINGLE JSON ARRAY for the whole
run (unlike clippy which emits one JSON object per line). Corrupt-input
handling mirrors parsers/_sarif.py (one json.loads of the whole blob),
NOT clippy's per-line failure counter. Field mapping mirrors clippy.py
style.

Verified against pylint 4.0.5 actual output: each array element has
keys: type, module, obj, line, column, endLine (int or null),
endColumn (int or null), path, symbol, message, message-id (hyphenated).
"""

import json

from code_forge.parsers.base import Finding, ToolError

# Map pylint "type" field to base.Finding.level.
# base.Finding.level allows ONLY "error"|"warning"|"note" (base.py:26).
# pylint's 6 types are collapsed:
#   fatal/error -> "error" (real failures)
#   warning -> "warning"
#   convention/refactor/information -> "note" (advisory style/quality)
_PYLINT_LEVEL_MAP: dict[str, str] = {
    "fatal": "error",
    "error": "error",
    "warning": "warning",
    "convention": "note",
    "refactor": "note",
    "information": "note",
}


def parse_pylint(
    output: str,
    tool_name: str = "pylint",
    exit_code: int = 0,
) -> list[Finding | ToolError]:
    """Parse pylint --output-format=json output.

    Returns:
        [] on empty string or empty array "[]" (clean run).
        [Finding, ...] on valid JSON array with diagnostics.
        [ToolError] on malformed/unparseable output (mirrors _sarif.py).
    """
    if not output.strip():
        return []

    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return [ToolError(
            tool_name=tool_name,
            exit_code=exit_code,
            stderr="",
            message="Failed to parse %s JSON output" % tool_name,
        )]

    # Unexpected top-level structure (not a list)
    if not isinstance(data, list):
        return [ToolError(
            tool_name=tool_name,
            exit_code=exit_code,
            stderr="",
            message="Failed to parse %s JSON output: "
                    "unexpected structure" % tool_name,
        )]

    findings = []
    for obj in data:
        # Defensive: skip non-dict entries
        if not isinstance(obj, dict):
            continue

        line_val = int(obj.get("line", 0) or 0)

        # endLine can be null -> fall back to line (mirrors clippy
        # line_end-or-line_start and _sarif endLine handling)
        el = obj.get("endLine")
        end_line = int(el) if el is not None else line_val

        # Prefer message-id (e.g. "C0114") as rule_id: stable across
        # pylint versions, human-greppable. Fall back to symbol then
        # "unknown".
        rule_id = (
            obj.get("message-id")
            or obj.get("symbol")
            or "unknown"
        )

        level = _PYLINT_LEVEL_MAP.get(
            obj.get("type", ""), "note",
        )

        findings.append(Finding(
            file=obj.get("path", ""),
            line=line_val,
            end_line=end_line,
            column=int(obj.get("column", 0) or 0),
            rule_id=rule_id,
            level=level,
            message=obj.get("message", ""),
            tool_name=tool_name,
        ))

    return findings
