# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""The falsifier's except clause must name where its raise comes from."""

import subprocess
from pathlib import Path

FILE = Path(__file__).resolve().parent.parent / "src" / "code_forge" / "falsify_real.py"


def test_falsify_except_raise_names_its_cause():
    result = subprocess.run(
        ["ruff", "check", str(FILE), "--select", "B904"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout
