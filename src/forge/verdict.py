# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Verdict determination -- PASS/FAIL from delta findings.

Pure function. Phase 1 implements PASS/FAIL only (GATE-01).
HOLD state is Phase 2+.

Addresses:
- Consensus #4: ToolError in results -> FAIL (not false PASS)
- Consensus #6: uses EXIT_PASS/EXIT_FAIL from forge.__init__
"""

from forge import EXIT_PASS, EXIT_FAIL
from forge.parsers.base import Finding, ToolError

# Lightweight type alias for readability.
# Phase 2 may replace with a proper enum or dataclass when HOLD is added.
Verdict = tuple[str, int]  # (verdict_string, exit_code)


def determine_verdict(
    delta_findings: list[Finding | ToolError],
) -> Verdict:
    """Determine verdict from delta findings.

    Rules:
    - Empty list: PASS (no new violations)
    - Any ToolError: FAIL (tool crash = cannot guarantee no violations)
    - Any Finding: FAIL (new violations found)

    Per GATE-01, Phase 1 implements PASS/FAIL only.
    Per GATE-04, all Layer 0 violations are gate-blocking.

    Args:
        delta_findings: filtered findings on changed lines

    Returns:
        (verdict_string, exit_code) tuple
    """
    if not delta_findings:
        return ("PASS", EXIT_PASS)

    return ("FAIL", EXIT_FAIL)
