# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Drift test: RUNTIME_LIFECYCLE_QUESTION constant vs SKILL.md mirror.

anti-drift: asserts that the verbatim lifecycle question text in
runtime.py matches the copy embedded in code-forge/SKILL.md. Any divergence
between the two copies is caught immediately -- preventing inline-outlet
users from asking a different question than the CLI outlet uses.

Lesson from 19.1 dual-copy divergence: two copies of the same text drift
silently without an automated check.
"""
from __future__ import annotations

from pathlib import Path

from code_forge.runtime import RUNTIME_LIFECYCLE_QUESTION


def _skill_md_path() -> Path:
    """Locate code-forge/SKILL.md relative to the package root."""
    pkg_root = Path(__file__).parent.parent / "src" / "code_forge"
    return pkg_root / "skills" / "code-forge" / "SKILL.md"


def test_runtime_lifecycle_question_in_skill_md() -> None:
    """RUNTIME_LIFECYCLE_QUESTION appears verbatim in code-forge/SKILL.md.

    Catches any drift between runtime.py constant and the SKILL.md mirror.
    : both copies must be identical.
    """
    skill_md = _skill_md_path()
    assert skill_md.exists(), (
        "code-forge/SKILL.md not found at %s -- "
        "cannot verify anti-drift invariant" % skill_md
    )

    content = skill_md.read_text(encoding="utf-8")

    # The full RUNTIME_LIFECYCLE_QUESTION constant -- including its literal
    # {diff_text} placeholder -- is embedded verbatim in a SKILL.md code
    # block, so a plain substring check matches without any stripping.
    assert RUNTIME_LIFECYCLE_QUESTION in content, (
        "RUNTIME_LIFECYCLE_QUESTION not found verbatim in %s\n"
        "drift detected: runtime.py constant and SKILL.md mirror have diverged.\n"
        "Fix: update the SKILL.md RUNTIME Axis code block to match "
        "RUNTIME_LIFECYCLE_QUESTION exactly." % skill_md
    )
