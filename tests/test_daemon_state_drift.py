# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Drift test: DAEMON_STATE_Q1 and DAEMON_STATE_Q2Q3 constants vs SKILL.md mirror.

Anti-drift: asserts that the verbatim question constants in daemon_state.py
match the copies embedded in code-forge/SKILL.md. Any divergence between
the two copies is caught immediately -- preventing inline-outlet users from
asking a different question than the CLI outlet uses.
"""
from __future__ import annotations

from pathlib import Path

from code_forge.daemon_state import DAEMON_STATE_Q1, DAEMON_STATE_Q2Q3


def _skill_md_path() -> Path:
    """Locate code-forge/SKILL.md relative to the package root."""
    pkg_root = Path(__file__).parent.parent / "src" / "code_forge"
    return pkg_root / "skills" / "code-forge" / "SKILL.md"


def test_daemon_state_q1_in_skill_md() -> None:
    """DAEMON_STATE_Q1 appears verbatim in code-forge/SKILL.md."""
    skill_md = _skill_md_path()
    assert skill_md.exists(), (
        "code-forge/SKILL.md not found at %s" % skill_md
    )
    content = skill_md.read_text(encoding="utf-8")
    assert DAEMON_STATE_Q1 in content, (
        "DAEMON_STATE_Q1 not found verbatim in %s\n"
        "Drift detected: daemon_state.py constant and SKILL.md mirror "
        "have diverged.\n"
        "Fix: update the SKILL.md Daemon State Axis code block to match "
        "DAEMON_STATE_Q1 exactly." % skill_md
    )


def test_daemon_state_q2q3_in_skill_md() -> None:
    """DAEMON_STATE_Q2Q3 appears verbatim in code-forge/SKILL.md."""
    skill_md = _skill_md_path()
    assert skill_md.exists(), (
        "code-forge/SKILL.md not found at %s" % skill_md
    )
    content = skill_md.read_text(encoding="utf-8")
    assert DAEMON_STATE_Q2Q3 in content, (
        "DAEMON_STATE_Q2Q3 not found verbatim in %s\n"
        "Drift detected: daemon_state.py constant and SKILL.md mirror "
        "have diverged.\n"
        "Fix: update the SKILL.md Daemon State Axis code block to match "
        "DAEMON_STATE_Q2Q3 exactly." % skill_md
    )
