# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""The CI recipes in docs/setup-ci.md must be runnable as written."""

from pathlib import Path

DOCS = Path(__file__).parents[1] / "docs"


def _ci_review_commands() -> list[str]:
    lines = (DOCS / "setup-ci.md").read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if "code-forge review --mode ci" in ln]


def test_ci_recipes_do_not_use_committed():
    # --committed maps to --head HEAD (a COMMIT source), which native mutation
    # review rejects with exit 2 for any Python source under mutation. It also
    # reviews only the last commit, not the whole branch.
    commands = _ci_review_commands()
    assert commands, "no CI review recipe found in setup-ci.md"
    for command in commands:
        assert "--committed" not in command, command
        assert "--head WORKING" in command, command


def test_manual_does_not_claim_committed_reviews_the_branch():
    manual = (DOCS / "manual.md").read_text(encoding="utf-8")
    line = next(ln for ln in manual.splitlines() if ln.startswith("code-forge review --committed"))
    assert "merge-base" not in line, line
