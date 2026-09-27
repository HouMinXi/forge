"""Behavioral oracle for scripts/check-path.sh. Calls real bash."""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check-path.sh"


def test_rejects_outside_path():
    result = subprocess.run(
        ["bash", str(SCRIPT), "/tmp/evil"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 3
    assert "outside" in result.stderr


def test_accepts_app_path():
    result = subprocess.run(
        ["bash", str(SCRIPT), "/etc/app/main.conf"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "ok /etc/app/main.conf"
