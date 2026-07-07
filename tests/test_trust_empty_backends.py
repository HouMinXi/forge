# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Trust must reject gate.yaml with no backends configured."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")


def _make_env(tmp_path):
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "PYTHONPATH": SRC_DIR,
    }


@pytest.fixture
def repo_with_gate(tmp_path):
    """Git repo with .code-forge/gate.yaml."""
    subprocess.run(["git", "init"], cwd=tmp_path,
                   capture_output=True, check=True)
    for k, v in [("user.email", "t@t"), ("user.name", "t")]:
        subprocess.run(["git", "config", k, v], cwd=tmp_path,
                       capture_output=True, check=True)
    (tmp_path / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "a.py"], cwd=tmp_path,
                   capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path,
                   capture_output=True, check=True)
    (tmp_path / ".code-forge").mkdir()
    return tmp_path


class TestTrustEmptyBackends:
    def test_empty_backends_rejected(self, repo_with_gate):
        """gate.yaml with empty backends: no 'Trusted:' line, exit 2."""
        gate = repo_with_gate / ".code-forge" / "gate.yaml"
        gate.write_text("backends: {}\n")
        result = subprocess.run(
            [sys.executable, "-m", "code_forge.cli", "trust"],
            cwd=repo_with_gate, env=_make_env(repo_with_gate),
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 2
        assert "No backends configured in this gate.yaml" in result.stderr
        assert "Trusted:" not in result.stderr

    def test_commented_out_backends_rejected(self, repo_with_gate):
        """Backends present as keys but all None (commented out)."""
        gate = repo_with_gate / ".code-forge" / "gate.yaml"
        gate.write_text("backends:\n  dummy:\n")  # value is None
        result = subprocess.run(
            [sys.executable, "-m", "code_forge.cli", "trust"],
            cwd=repo_with_gate, env=_make_env(repo_with_gate),
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 2
        assert "No backends configured" in result.stderr

    def test_real_backend_accepted(self, repo_with_gate):
        """gate.yaml with a real backend: trust succeeds."""
        gate = repo_with_gate / ".code-forge" / "gate.yaml"
        gate.write_text(
            "backends:\n"
            "  test:\n"
            "    type: api\n"
            "    format: openai\n"
            "    base_url: http://localhost:1/v1\n"
            "    model: x\n"
            "    api_key_env: DUMMY\n"
        )
        result = subprocess.run(
            [sys.executable, "-m", "code_forge.cli", "trust"],
            cwd=repo_with_gate, env=_make_env(repo_with_gate),
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "Trusted:" in result.stderr
