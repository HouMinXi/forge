# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Shared SARIF 2.1.0 parser for ruff and semgrep (DRY)."""

import json
import logging

from code_forge.parsers.base import Finding, ToolError


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

    findings: list[Finding | ToolError] = []
    bad_items = 0
    for run in sarif.get("runs", []):
        for result in run.get("results", []):
            for location in result.get("locations", []):
                try:
                    phys = location.get("physicalLocation", {})
                    artifact = phys.get("artifactLocation", {})
                    region = phys.get("region", {})
                    uri = artifact.get("uri", "")
                    # Strip file:// prefix, preserving absolute path.
                    # file:///tmp/foo -> /tmp/foo (not tmp/foo).
                    if uri.startswith("file:///"):
                        uri = uri[len("file://"):]
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
                except (KeyError, TypeError, AttributeError) as exc:
                    bad_items += 1
                    logging.warning(
                        "%s SARIF: skipping malformed item: %s", tool_name, exc
                    )
    if not findings and bad_items > 0:
        return [ToolError(
            tool_name=tool_name,
            exit_code=exit_code,
            stderr="",
            message=(
                f"Failed to parse {tool_name} SARIF output: "
                f"{bad_items} malformed item(s), no valid findings"
            ),
        )]
    return findings
