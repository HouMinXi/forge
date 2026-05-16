# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Shared SARIF 2.1.0 parser for ruff and semgrep (DRY)."""

import json

from forge.parsers.base import Finding, ToolError


def _parse_sarif(
    output: str,
    tool_name: str,
    exit_code: int = 0,
) -> list[Finding | ToolError]:
    """Parse SARIF 2.1.0 JSON into Finding objects.

    Shared by ruff and semgrep -- tool_name distinguishes them.

    Returns:
        [] on empty string (clean run).
        [Finding, ...] on valid SARIF with results.
        [ToolError] on malformed/unparseable output.
    """
    if not output.strip():
        return []
    try:
        sarif = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return [ToolError(
            tool_name=tool_name,
            exit_code=exit_code,
            stderr="",
            message=f"Failed to parse {tool_name} SARIF output",
        )]

    findings = []
    try:
        for run in sarif.get("runs", []):
            for result in run.get("results", []):
                for location in result.get("locations", []):
                    phys = location.get("physicalLocation", {})
                    artifact = phys.get("artifactLocation", {})
                    region = phys.get("region", {})
                    uri = artifact.get("uri", "")
                    # Strip file:// prefix
                    if uri.startswith("file:///"):
                        uri = uri[len("file:///"):]
                    elif uri.startswith("file://"):
                        uri = uri[len("file://"):]
                    start_line = region.get("startLine", 0)
                    end_line_raw = region.get("endLine")
                    findings.append(Finding(
                        file=uri,
                        line=start_line,
                        end_line=(
                            end_line_raw if end_line_raw is not None
                            else start_line
                        ),
                        column=(region.get("startColumn") or 0),
                        rule_id=result.get("ruleId", "unknown"),
                        level=result.get("level", "warning"),
                        message=(
                            result.get("message", {}).get("text", "")
                        ),
                        tool_name=tool_name,
                    ))
    except (KeyError, TypeError, AttributeError):
        return [ToolError(
            tool_name=tool_name,
            exit_code=exit_code,
            stderr="",
            message=(
                f"Failed to parse {tool_name} SARIF output: "
                "unexpected structure"
            ),
        )]
    return findings
