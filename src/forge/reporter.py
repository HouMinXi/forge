# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Terminal output formatting, cargo-check style.

Plain text output per D-05. Shows delta findings prominently,
pre-existing violation count for context, tool versions for
reproducibility.

Addresses:
- Mimo: all_findings preserved for pre-existing count
- Round 3 C-1: tools_failed parameter for silent-miss visibility
- DeepSeek F-4: optional tool failure warning
"""

from forge.parsers.base import Finding, ToolError


def format_report(
    delta_findings: list[Finding | ToolError],
    all_findings: list[Finding | ToolError],
    tool_versions: dict[str, str],
    tools_skipped: list[str],
    tools_failed: list[str],
) -> str:
    """Format findings into terminal-friendly report.

    Args:
        delta_findings: findings on changed lines (drive verdict)
        all_findings: all findings including pre-existing
        tool_versions: {tool_name: version_string} for reproducibility
        tools_skipped: tools not installed or no matching files
        tools_failed: tools that ran but returned ToolError (separate
            from tools_skipped -- different messaging)

    Returns:
        Formatted report string
    """
    lines: list[str] = []

    # Separate ToolErrors from Findings in delta
    delta_errors = [f for f in delta_findings if isinstance(f, ToolError)]
    delta_violations = [
        f for f in delta_findings if isinstance(f, Finding)
    ]

    has_errors = len(delta_errors) > 0
    has_violations = len(delta_violations) > 0

    if has_violations or has_errors:
        # FAIL output
        if has_violations and has_errors:
            lines.append(
                "forge: FAIL -- %d new violation(s) and tool error(s)"
                % len(delta_violations)
            )
        elif has_violations:
            lines.append(
                "forge: FAIL -- %d new violation(s)"
                % len(delta_violations)
            )
        else:
            lines.append("forge: FAIL -- tool error(s)")

        lines.append("")

        # Show violations
        for f in delta_violations:
            lines.append(
                "  %s:%d: [%s/%s] %s: %s"
                % (f.file, f.line, f.tool_name, f.rule_id, f.level, f.message)
            )

        # Show tool errors
        for e in delta_errors:
            lines.append(
                "  [%s] ERROR: %s" % (e.tool_name, e.message)
            )

        lines.append("")

        if has_violations and has_errors:
            lines.append(
                "forge: fix %d violation(s) and resolve tool errors before commit"
                % len(delta_violations)
            )
        elif has_violations:
            lines.append(
                "forge: fix %d violation(s) before commit"
                % len(delta_violations)
            )
        else:
            lines.append("forge: resolve tool errors before commit")
    else:
        # PASS output
        # Count pre-existing violations (all minus delta, Findings only)
        all_finding_count = sum(
            1 for f in all_findings if isinstance(f, Finding)
        )
        delta_finding_count = sum(
            1 for f in delta_findings if isinstance(f, Finding)
        )
        pre_existing = all_finding_count - delta_finding_count

        lines.append("forge: PASS -- no new violations")
        if pre_existing > 0:
            lines.append(
                "  (%d pre-existing violation(s) in unchanged code,"
                " not blocking)"
                % pre_existing
            )

    # Tools failed warning (always shown, even on PASS -- Round 3 C-1)
    if tools_failed:
        lines.append(
            "  WARNING: %d optional tool(s) failed: %s"
            " -- results may be incomplete"
            % (len(tools_failed), ", ".join(tools_failed))
        )

    # Tools skipped
    if tools_skipped:
        lines.append(
            "  (tools skipped: %s)" % ", ".join(tools_skipped)
        )

    # Tool versions for reproducibility
    if tool_versions:
        lines.append("")
        lines.append("tool versions:")
        for name in sorted(tool_versions.keys()):
            lines.append("  %s: %s" % (name, tool_versions[name]))

    return "\n".join(lines)
