# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Empty-diff early gate: review must say 'no changes' not silent PASS."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def scratch_repo(tmp_path):
    """Git repo with init commit + empty commit + trusted gate.yaml."""
    subprocess.run(
        ["git", "init"], cwd=tmp_path,
        capture_output=True, check=True,
    )
    for k, v in [("user.email", "t@t"), ("user.name", "t")]:
        subprocess.run(
            ["git", "config", k, v], cwd=tmp_path,
            capture_output=True, check=True,
        )
    (tmp_path / "a.py").write_text("x = 1\n")
    subprocess.run(
        ["git", "add", "a.py"], cwd=tmp_path,
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=tmp_path,
        capture_output=True, check=True,
    )
    # Set up a gate.yaml with a dummy backend so outlet=subprocess
    # resolves (the empty-diff guard fires before the backend is used)
    code_forge_dir = tmp_path / ".code-forge"
    code_forge_dir.mkdir()
    (code_forge_dir / "gate.yaml").write_text(
        "backends:\n"
        "  dummy:\n"
        "    type: api\n"
        "    format: openai\n"
        "    base_url: http://localhost:1/v1\n"
        "    model: x\n"
        "    api_key_env: DUMMY_KEY\n"
    )
    # Trust it (HOME must match the review env so trusted.json is found)
    src_dir = str(Path(__file__).resolve().parents[1] / "src")
    subprocess.run(
        [sys.executable, "-m", "code_forge.cli", "trust"],
        cwd=tmp_path, capture_output=True, check=True,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
             "HOME": str(tmp_path), "PYTHONPATH": src_dir},
    )
    # Empty commit
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "empty"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    return tmp_path


def _run_review(cwd, extra_args=None, extra_env=None):
    src_dir = str(Path(__file__).resolve().parents[1] / "src")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(cwd),
        "PYTHONPATH": src_dir,
        "DUMMY_KEY": "sk-fake",
    }
    if extra_env:
        env.update(extra_env)
    cmd = [
        sys.executable, "-m", "code_forge.cli",
        "review", "--allow-main",
        "--baseline", "HEAD~1", "--head", "HEAD",
        "--quiet",
    ]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(
        cmd, cwd=cwd, env=env,
        capture_output=True, text=True, timeout=10,
    )


class TestEmptyDiffGate:
    def test_empty_diff_prints_no_changes(self, scratch_repo):
        """Empty diff -> 'no changes to review' + exit 0."""
        result = _run_review(scratch_repo)
        assert result.returncode == 0, result.stderr
        assert "no changes to review" in result.stderr

    def test_nonempty_diff_does_not_trigger_guard(self, scratch_repo):
        """Real changes must NOT hit the empty-diff guard."""
        (scratch_repo / "a.py").write_text("x = 2\n")
        subprocess.run(
            ["git", "add", "a.py"], cwd=scratch_repo,
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "change"], cwd=scratch_repo,
            capture_output=True, check=True,
        )
        # Kill quickly -- we only need to prove the guard didn't fire,
        # not complete a full review against a dead backend.
        try:
            result = _run_review(
                scratch_repo,
                extra_args=["--max-total-rounds", "1"],
            )
            stderr_text = result.stderr
        except subprocess.TimeoutExpired as e:
            stderr_text = (e.stderr or b"").decode("utf-8", errors="replace")
        assert "no changes to review" not in stderr_text
