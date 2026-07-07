# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Pre-commit hook fail-closed: exit 2/5 must block by default.

Tests the GENERATED shell script, not the Python generator.
Golden Rule 3: prove the real hook works by executing it.
"""

import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

from code_forge.install_hooks import _build_review_block


@pytest.fixture
def hook_env(tmp_path):
    """Set up a temp dir with a stub code-forge binary and the hook."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    # Stub code-forge that exits with the code from $STUB_EXIT env var
    stub = bin_dir / "code-forge"
    stub.write_text(textwrap.dedent("""\
        #!/bin/sh
        exit ${STUB_EXIT:-0}
    """))
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    # Generate the hook block using the real _build_review_block
    abs_path = str(stub) + " gate-check"
    block = _build_review_block(abs_path)

    # Wrap in a script that sources the block
    hook_script = tmp_path / "hook.sh"
    hook_script.write_text("#!/bin/sh\nset -e\n" + block)
    hook_script.chmod(hook_script.stat().st_mode | stat.S_IEXEC)

    return tmp_path, hook_script, bin_dir


class TestHookFailClosed:
    def test_rc0_passes_through(self, hook_env):
        """Review exits 0 (success) -> hook exits 0."""
        tmp_path, hook, bin_dir = hook_env
        env = {"PATH": str(bin_dir), "STUB_EXIT": "0"}
        result = subprocess.run(
            [str(hook)], env=env,
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode == 0

    def test_rc2_blocks_by_default(self, hook_env):
        """Review exits 2 (no backend) -> hook exits 1 (blocks)."""
        tmp_path, hook, bin_dir = hook_env
        env = {"PATH": str(bin_dir), "STUB_EXIT": "2"}
        result = subprocess.run(
            [str(hook)], env=env,
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode == 1
        assert "review skipped" in result.stderr

    def test_rc2_allows_with_opt_in(self, hook_env):
        """FORGE_ALLOW_NO_BACKEND=1 + exit 2 -> hook exits 0."""
        tmp_path, hook, bin_dir = hook_env
        env = {
            "PATH": str(bin_dir), "STUB_EXIT": "2",
            "FORGE_ALLOW_NO_BACKEND": "1",
        }
        result = subprocess.run(
            [str(hook)], env=env,
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode == 0
        assert "review skipped" in result.stderr

    def test_rc5_blocks_by_default(self, hook_env):
        """Review exits 5 (delegated) -> hook exits 1 (blocks)."""
        tmp_path, hook, bin_dir = hook_env
        env = {"PATH": str(bin_dir), "STUB_EXIT": "5"}
        result = subprocess.run(
            [str(hook)], env=env,
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode == 1
        assert "review delegated" in result.stderr

    def test_rc5_allows_with_opt_in(self, hook_env):
        """FORGE_ALLOW_NO_BACKEND=1 + exit 5 -> hook exits 0."""
        tmp_path, hook, bin_dir = hook_env
        env = {
            "PATH": str(bin_dir), "STUB_EXIT": "5",
            "FORGE_ALLOW_NO_BACKEND": "1",
        }
        result = subprocess.run(
            [str(hook)], env=env,
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode == 0

    def test_other_nonzero_blocks(self, hook_env):
        """Review exits 1 (FAIL) -> hook exits 1 (unchanged)."""
        tmp_path, hook, bin_dir = hook_env
        env = {"PATH": str(bin_dir), "STUB_EXIT": "1"}
        result = subprocess.run(
            [str(hook)], env=env,
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode == 1
        assert "review FAILED" in result.stderr
