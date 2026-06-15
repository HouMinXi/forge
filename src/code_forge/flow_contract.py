# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Flow contract constants: single source of truth for P3 thresholds and cycle defaults.

These constants are referenced by:
  - src/code_forge/machine.py   (runtime state machine)
  - skills/code-forge/SKILL.md  (prose documentation of the same rules)

When any threshold value changes, update both this file and the prose in SKILL.md.
The drift guard test (tests/test_flow_contract_drift.py) enforces this by parsing
SKILL.md with regex and asserting the parsed values match these constants.

No imports. No runtime logic. Constants only.
"""

__all__ = [
    "P3_DISTINCT_PER_FILE_THRESHOLD",
    "P3_DISTINCT_PER_DIFF_THRESHOLD",
    "P3_DENSITY_THRESHOLD",
    "DEFAULT_CLEAN_ROUND_THRESHOLD",
]

# P3 density escalation thresholds (state machine Step C).
# A P3 finding batch that exceeds any one of these triggers a P2-equivalent
# cycle restart instead of simple accumulation.
P3_DISTINCT_PER_FILE_THRESHOLD: int = 5
P3_DISTINCT_PER_DIFF_THRESHOLD: int = 10
P3_DENSITY_THRESHOLD: float = 0.15

# Default number of consecutive clean cycles required before the state machine
# declares a PASS verdict. Overridden at runtime by FORGE_CLEAN_ROUND_THRESHOLD
# or the clean_round_threshold field on StateMachine.
DEFAULT_CLEAN_ROUND_THRESHOLD: int = 3
