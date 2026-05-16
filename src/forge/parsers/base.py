# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Base types for the forge parser subsystem.

Finding: frozen dataclass representing a single tool finding.
ToolError: sentinel type representing a tool execution failure.
"""

from dataclasses import dataclass, asdict
from typing import Optional


@dataclass(frozen=True)
class Finding:
    """A single finding from a deterministic tool.

    All fields are immutable (frozen=True). The `fix` field is optional
    and defaults to None when no suggested fix is available.
    """

    file: str           # relative path from repo root
    line: int           # 1-based start line
    end_line: int       # 1-based end line (same as line if single-line)
    column: int         # 1-based start column (0 if unknown)
    rule_id: str        # tool-specific rule identifier
    level: str          # "error" | "warning" | "note"
    message: str        # human-readable description
    tool_name: str      # which tool produced this
    fix: Optional[str] = None  # suggested fix text

    def to_dict(self) -> dict:
        """Serialize to plain dict for JSON state persistence."""
        return asdict(self)


@dataclass(frozen=True)
class ToolError:
    """Sentinel returned by parsers when tool execution failed.

    Distinguishes 'no findings' (empty list) from 'tool crashed'
    (ToolError in list). Addresses review Consensus #4: tool crash
    must not produce false PASS.

    Downstream code checks ``isinstance(item, ToolError)`` to
    distinguish tool failure from a clean run.
    """

    tool_name: str   # which tool failed
    exit_code: int   # tool's exit code
    stderr: str      # stderr output (for diagnostics)
    message: str     # human-readable error description

    def to_dict(self) -> dict:
        """Serialize to plain dict for JSON state persistence.

        Addresses Round 3 C-3: state.py needs to serialize ToolError
        to state.json via this method. Without it, json.dumps crashes
        on ToolError objects.
        """
        return {
            "tool_name": self.tool_name,
            "exit_code": self.exit_code,
            "stderr": self.stderr,
            "message": self.message,
        }
