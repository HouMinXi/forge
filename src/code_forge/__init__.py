# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Forge -- 3-state quality gate for code review."""

import os

__version__ = "2.9.0"

# Exit code constants re-exported from exit_codes module (H5 + R3-L3).
from .exit_codes import (
    EXIT_BUSY,
    EXIT_CLI_ERROR,
    EXIT_DELEGATED,
    EXIT_ESCALATED,
    EXIT_FAIL,
    EXIT_PASS,
    EXIT_TIMEOUT,
    EXIT_UNRELIABLE,
)

# Parent pid as seen before any heavy import. The MCP server's parent-death
# check compares against this: sampling it later, after mcp/pydantic have
# loaded, can happen after the parent already died and the process was
# reparented, and then the check compares the new parent with itself.
# The pid is kept alongside so a forked child does not inherit a parent pid
# that was never its own.
STARTUP_PID = os.getpid()
STARTUP_PPID = os.getppid()

# Declares the re-export as deliberate. Without it these read as unused
# imports (ruff F401), which is noise every future lint run has to be told
# to ignore -- and noise that trains a reader to skip lint output is worse
# than no lint at all.
#
# EXIT_UNRELIABLE was absent from the import block while every sibling
# constant was present, so `from code_forge import EXIT_UNRELIABLE` raised
# ImportError for no reason a caller could have predicted. A partial
# re-export is worse than none: it looks complete.
__all__ = [
    "EXIT_BUSY",
    "EXIT_CLI_ERROR",
    "EXIT_DELEGATED",
    "EXIT_ESCALATED",
    "EXIT_FAIL",
    "EXIT_PASS",
    "EXIT_TIMEOUT",
    "EXIT_UNRELIABLE",
    "__version__",
]
