# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""STATE-11 lock integration tests."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from forge import EXIT_BUSY, EXIT_PASS
from forge.cli import main
from forge.lock import ForgeLockBusy


class TestLockBusy:
    """SC-11: ForgeLockBusy -> exit 3."""

    def test_lock_busy_returns_exit_busy(
        self, tmp_path, monkeypatch, capsys
    ):
        """ForgeLockBusy -> stderr message + exit 3."""
        monkeypatch.setattr(
            sys, "argv", ["forge", "--mode", "ci", "a.py"],
        )
        monkeypatch.chdir(str(tmp_path))

        # Mock _run to raise ForgeLockBusy (simulates lock conflict
        # at any point inside the pipeline).
        from forge.cli import _run as real_run
        with patch(
            "forge.cli._run",
            side_effect=ForgeLockBusy(
                12345, tmp_path / ".forge" / "forge.lock"
            ),
        ):
            exit_code = main()

        assert exit_code == EXIT_BUSY
        captured = capsys.readouterr()
        assert "12345" in captured.err


class TestLockReleasedOnExit:
    """SC-10(c): lock released on terminal exit."""

    def test_lock_released_after_run(
        self, tmp_path, monkeypatch
    ):
        """Lock file absent after successful run."""
        import subprocess
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "init"], cwd=str(repo),
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "test"],
            cwd=str(repo), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(repo), capture_output=True, check=True,
        )
        forge_dir = repo / ".forge"
        forge_dir.mkdir(parents=True, exist_ok=True)
        (forge_dir / "tools.yaml").write_text("tools: {}\n")
        (repo / "a.py").write_text("# initial\n")
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(repo), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(repo), capture_output=True, check=True,
        )
        (repo / "a.py").write_text("# modified\n")

        monkeypatch.setattr(
            sys, "argv",
            ["forge", "--mode", "ci", "a.py"],
        )
        monkeypatch.chdir(str(repo))
        exit_code = main()
        assert exit_code == EXIT_PASS

        state_path = forge_dir / "state.json"
        lock_path = forge_dir / "forge.lock"
        assert state_path.exists()
        assert not lock_path.exists()


class TestLockReleasedOnException:
    """SC-10(d): lock released on exception."""

    def test_lock_released_on_exception(
        self, tmp_path, monkeypatch
    ):
        """Exception mid-run -> lock released by __exit__."""
        import subprocess
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "init"], cwd=str(repo),
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "test"],
            cwd=str(repo), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(repo), capture_output=True, check=True,
        )
        forge_dir = repo / ".forge"
        forge_dir.mkdir(parents=True, exist_ok=True)
        (forge_dir / "tools.yaml").write_text("tools: {}\n")
        (repo / "a.py").write_text("# initial\n")
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(repo), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(repo), capture_output=True, check=True,
        )
        (repo / "a.py").write_text("# modified\n")

        monkeypatch.setattr(
            sys, "argv",
            ["forge", "--mode", "ci", "a.py"],
        )
        monkeypatch.chdir(str(repo))

        # Inject crash inside the lock scope.
        with patch(
            "forge.cli._run_hold_loop",
            side_effect=RuntimeError("test crash"),
        ):
            exit_code = main()

        # Exception caught by top-level handler -> EXIT_FAIL.
        from forge import EXIT_FAIL
        assert exit_code == EXIT_FAIL

        lock_path = forge_dir / "forge.lock"
        assert not lock_path.exists()
