# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Parse cargo clippy JSON diagnostic output into Finding objects."""

import json

from code_forge.parsers.base import Finding, ToolError


def parse_clippy(
    output: str,
    tool_name: str = "clippy",
    exit_code: int = 0,
) -> list[Finding | ToolError]:
    """Parse cargo clippy --message-format=json output.

    Cargo emits one JSON object per line.  Only lines with
    reason="compiler-message" and level in (warning, error) are
    relevant.  Non-diagnostic lines (build-script-executed, etc.)
    are silently skipped.

    Emit each primary location once per diagnostic, regardless of span order.
    Legacy output with no is_primary fields retains its first-span fallback.
    Partially marked, non-boolean, or all-secondary spans yield ToolError.

    Returns:
        [] on empty string (clean run).
        [Finding, ...] on valid output with diagnostics.
        [ToolError] if ALL lines fail JSON parse and input is non-empty.
    """
    if not output.strip():
        return []

    findings = []
    parse_failures = 0
    total_lines = 0

    for raw_line in output.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        total_lines += 1
        try:
            obj = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            parse_failures += 1
            continue

        if obj.get("reason") != "compiler-message":
            continue

        msg = obj.get("message", {})
        level = msg.get("level", "")
        if level not in ("warning", "error"):
            continue

        spans = msg.get("spans")
        if not spans:
            continue  # empty spans -- no file location

        if any("is_primary" in span for span in spans):
            if any(type(span.get("is_primary")) is not bool for span in spans):
                findings.append(
                    ToolError(tool_name, exit_code, "", "Invalid %s primary span markers" % tool_name)
                )
                continue
            selected_spans = [span for span in spans if span["is_primary"]]
            if not selected_spans:
                findings.append(
                    ToolError(tool_name, exit_code, "", "Missing %s primary span" % tool_name)
                )
                continue
        else:
            selected_spans = spans[:1]
        code_obj = msg.get("code")
        if code_obj is not None:
            rule_id = code_obj.get("code", "unknown")
        else:
            rule_id = "unknown"

        seen_locations = set()
        for span in selected_spans:
            line_start = span.get("line_start", 0)
            location = (
                span.get("file_name", ""),
                line_start,
                span.get("line_end") or line_start,
                span.get("column_start") or 0,
            )
            if location in seen_locations:
                continue
            seen_locations.add(location)
            findings.append(
                Finding(
                    file=location[0],
                    line=location[1],
                    end_line=location[2],
                    column=location[3],
                    rule_id=rule_id,
                    level=level,
                    message=msg.get("message", ""),
                    tool_name=tool_name,
                )
            )

    # If all lines failed JSON parse, output is corrupt
    if total_lines > 0 and parse_failures == total_lines:
        return [
            ToolError(
                tool_name=tool_name,
                exit_code=exit_code,
                stderr="",
                message=f"Failed to parse {tool_name} output: no valid JSON",
            )
        ]

    return findings
