# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Forge -- 3-state quality gate for code review."""

__version__ = "2.0.0a1"

# Exit code constants (Consensus #6 -- formally defined)
EXIT_PASS = 0   # No new violations
EXIT_FAIL = 1   # New violations found
# EXIT_ESCALATED = 2  # Phase 2+ (state machine non-convergence)
