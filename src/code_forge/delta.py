# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Delta computation -- filter findings to changed lines only.

This is the core of baseline mode (LAYER0-02).  The design doc says
"Layer 0 flags only NEW violations introduced by the diff."  The
delta filter determines "NEW" by checking if the finding's line
range intersects lines that were added or modified in the diff.

This module is a pure function with no I/O -- same inputs always
produce same outputs, satisfying GATE-02 determinism.
"""

from code_forge.parsers.base import Finding, ToolError


def lines_intersect(
    line_range: list[int],
    changed_lines: set[int],
) -> bool:
    """Check if any line in a finding's range intersects changed lines.

    Shared by filter_delta (Finding objects with line/end_line) and
    the L0 phase delta filter (StateFinding objects with line_range).

    Args:
        line_range: [start, end] inclusive line range
        changed_lines: set of line numbers that changed in the diff

    Returns:
        True if any line in the range is in changed_lines
    """
    start, end = line_range[0], line_range[-1]
    return any(ln in changed_lines for ln in range(start, end + 1))


def filter_delta(
    findings: list[Finding | ToolError],
    changed_lines: dict[str, set[int]],
) -> tuple[list[Finding | ToolError], list[Finding | ToolError]]:
    """Filter findings to only those on changed lines.

    Returns a 2-tuple:
        delta_findings: findings whose line range intersects changed
            lines, plus all ToolError items (not filtered by line).
        all_findings: a copy of the input list (preserved for the
            reporter to show "N pre-existing violation(s) in
            unchanged code").

    ToolError items are always included in delta_findings because
    tool errors represent tool-level failures that must be reported
    regardless of which lines changed.

    For each Finding, a multi-line finding (line != end_line) is
    kept if ANY line in range(finding.line, finding.end_line + 1)
    intersects the changed lines set (RESEARCH.md Pitfall 2).

    Args:
        findings: list of Finding and/or ToolError items
        changed_lines: {file_path: set_of_changed_line_numbers}
            from extract_changed_lines()

    Returns:
        (delta_findings, all_findings)
    """
    all_findings = list(findings)
    delta_findings: list[Finding | ToolError] = []

    for item in findings:
        # ToolError items always pass through
        if isinstance(item, ToolError):
            delta_findings.append(item)
            continue

        # Finding: check if file is in changed_lines
        file_lines = changed_lines.get(item.file)
        if file_lines is None:
            continue

        if lines_intersect([item.line, item.end_line], file_lines):
            delta_findings.append(item)

    return (delta_findings, all_findings)
