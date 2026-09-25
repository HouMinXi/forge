"""Computed-width oracle for style.css. Drives headless Chrome."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def test_panel_width():
    result = subprocess.run(
        ["/opt/node-0/node", str(ROOT / "probe.mjs")],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": "/usr/bin:/bin", "HOME": "/workspace", "FORGE_CSS_STRONG": os.environ.get("FORGE_CSS_STRONG", "1")},
    )
    assert result.returncode == 0, result.stderr
