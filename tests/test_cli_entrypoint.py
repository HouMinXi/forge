# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for cli.py __main__ guard -- subprocess only."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import code_forge


# Derive the worktree src path from the imported package so the
# subprocess runs THIS code, not the editable-install main tree.
_SRC = str(Path(code_forge.__file__).parents[1])


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    existing = os.environ.get("PYTHONPATH", "")
    env = {**os.environ, "PYTHONPATH": _SRC + os.pathsep + existing if existing else _SRC}
    return subprocess.run(
        [sys.executable, "-m", "code_forge.cli", *args],
        capture_output=True, text=True, env=env, timeout=15,
    )


def test_version_output():
    """T1: -m code_forge.cli --version prints version and exits 0."""
    r = _run_cli("--version")
    assert r.returncode == 0
    assert code_forge.__version__ in r.stdout, (
        "expected version %s in stdout, got: %r"
        % (code_forge.__version__, r.stdout)
    )


def test_exit_code_forwarded():
    """T2: main() return code is forwarded via sys.exit, not swallowed."""
    r = _run_cli("e2e-check", "--diff", "/nonexistent/x.diff")
    assert r.returncode == 2, (
        "expected EXIT_CLI_ERROR=2 for missing diff file, got %d"
        % r.returncode
    )
