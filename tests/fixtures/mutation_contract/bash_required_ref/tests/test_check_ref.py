"""Behavioral oracle for scripts/check-ref.sh. Calls real bash."""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check-ref.sh"


def test_rejects_empty_ref():
    result = subprocess.run(
        ["bash", str(SCRIPT), ""],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "empty ref" in result.stderr


def test_accepts_ref():
    result = subprocess.run(
        ["bash", str(SCRIPT), "main"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "ok main"
