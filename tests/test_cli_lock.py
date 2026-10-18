# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""STATE-11 lock integration tests."""

import sys
from unittest.mock import patch

from code_forge import EXIT_BUSY, EXIT_FAIL, EXIT_PASS
from code_forge.cli import main
from code_forge.lock import ForgeLockBusy


class TestLockBusy:
    """SC-11: ForgeLockBusy -> exit 3."""

    def test_lock_busy_returns_exit_busy(self, tmp_path, monkeypatch, capsys):
        """ForgeLockBusy -> stderr message + exit 3."""
        monkeypatch.setattr(
            sys,
            "argv",
            ["code-forge", "--mode", "ci", "a.py"],
        )
        monkeypatch.chdir(str(tmp_path))

        # Mock _run to raise ForgeLockBusy (simulates lock conflict
        # at any point inside the pipeline).
        with patch(
            "code_forge.cli._run",
            side_effect=ForgeLockBusy(12345, tmp_path / ".code-forge" / "code-forge.lock"),
        ):
            exit_code = main()

        assert exit_code == EXIT_BUSY
        captured = capsys.readouterr()
        assert "12345" in captured.err


class TestLockReleasedOnExit:
    """SC-10(c): lock released on terminal exit."""

    def test_lock_released_after_run(self, tmp_path, monkeypatch):
        """Lock file absent after successful run."""
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "init"],
            cwd=str(repo),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "test"],
            cwd=str(repo),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(repo),
            capture_output=True,
            check=True,
        )
        forge_dir = repo / ".code-forge"
        forge_dir.mkdir(parents=True, exist_ok=True)
        (forge_dir / "tools.yaml").write_text("tools: {}\n")
        # #104: the lock is under test, not the verdict. Exempt a.py from the
        # coverage rule (no linter is configured) so the run can PASS.
        (forge_dir / "coverage.yaml").write_text("version: 1\nexempt_patterns:\n  - a.py\n")
        (repo / "a.py").write_text("# initial\n")
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(repo),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(repo),
            capture_output=True,
            check=True,
        )
        (repo / "a.py").write_text("# modified\n")

        monkeypatch.setattr(
            sys,
            "argv",
            ["code-forge", "--mode", "ci", "--falsification-engine", "stub", "a.py"],
        )
        monkeypatch.setattr(
            "code_forge.outlet_resolver.resolve_outlet",
            lambda *a, **kw: "subprocess",
        )
        # #104: an empty tools.yaml triggers toolchain auto-detection, which
        # fails when no linter is on PATH. Keep the test hermetic.
        monkeypatch.setattr("code_forge.detect.detect_and_init", lambda *a, **kw: None)
        monkeypatch.chdir(str(repo))
        exit_code = main()
        # This is a lock test, not a verdict test. In CI the run may FAIL for
        # unrelated policy reasons (e.g. #146/#178: no gate.yaml on the
        # baseline means mutation is not measured); the lock must be released
        # either way.
        assert exit_code in (EXIT_PASS, EXIT_FAIL), exit_code

        state_path = forge_dir / "state.json"
        lock_path = forge_dir / "code-forge.lock"
        assert state_path.exists()
        assert not lock_path.exists()


class TestLockReleasedOnException:
    """SC-10(d): lock released on exception."""

    def test_lock_released_on_exception(self, tmp_path, monkeypatch):
        """Exception mid-run -> lock released by __exit__."""
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "init"],
            cwd=str(repo),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "test"],
            cwd=str(repo),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(repo),
            capture_output=True,
            check=True,
        )
        forge_dir = repo / ".code-forge"
        forge_dir.mkdir(parents=True, exist_ok=True)
        (forge_dir / "tools.yaml").write_text("tools: {}\n")
        # #104: the lock is under test, not the verdict. Exempt a.py from the
        # coverage rule (no linter is configured) so the run can PASS.
        (forge_dir / "coverage.yaml").write_text("version: 1\nexempt_patterns:\n  - a.py\n")
        (repo / "a.py").write_text("# initial\n")
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(repo),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(repo),
            capture_output=True,
            check=True,
        )
        (repo / "a.py").write_text("# modified\n")

        monkeypatch.setattr(
            sys,
            "argv",
            ["code-forge", "--mode", "ci", "--falsification-engine", "stub", "a.py"],
        )
        monkeypatch.setattr(
            "code_forge.outlet_resolver.resolve_outlet",
            lambda *a, **kw: "subprocess",
        )
        # #104: an empty tools.yaml triggers toolchain auto-detection, which
        # fails when no linter is on PATH. Keep the test hermetic.
        monkeypatch.setattr("code_forge.detect.detect_and_init", lambda *a, **kw: None)
        monkeypatch.chdir(str(repo))

        # Inject crash inside the lock scope.
        with patch(
            "code_forge.cli._run_hold_loop",
            side_effect=RuntimeError("test crash"),
        ):
            exit_code = main()

        # Exception caught by top-level handler -> EXIT_FAIL.
        from code_forge import EXIT_FAIL

        assert exit_code == EXIT_FAIL

        lock_path = forge_dir / "code-forge.lock"
        assert not lock_path.exists()
