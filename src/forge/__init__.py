# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Forge -- 3-state quality gate for code review."""

__version__ = "2.0.0a1"

# Exit code constants re-exported from exit_codes module (H5 + R3-L3).
from .exit_codes import (
    EXIT_BUSY,
    EXIT_CLI_ERROR,
    EXIT_ESCALATED,
    EXIT_FAIL,
    EXIT_PASS,
)
