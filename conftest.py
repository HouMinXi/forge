# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""pytest configuration for worktree test execution.

Inserts the worktree src/ directory at the front of sys.path so that
the worktree's code_forge package takes precedence over any globally
installed version (e.g. main-branch editable install at /code/forge/src).

This conftest.py is worktree-specific and is not committed to main.
"""
import sys
from pathlib import Path

# Insert worktree src at front of sys.path (not append -- must win over
# the main repo's editable-install path that is already on sys.path).
_worktree_src = str(Path(__file__).parent / "src")
if _worktree_src not in sys.path:
    sys.path.insert(0, _worktree_src)
