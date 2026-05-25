# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for the install-hooks subcommand."""

import os
import subprocess
from pathlib import Path

import pytest

from forge.exit_codes import EXIT_FAIL, EXIT_PASS
from forge.install_hooks import (
    check_hooks_path_override,
    generate_hook_content,
    resolve_forge_path,
    resolve_hooks_dir,
    run_install_hooks,
)


class TestInstallHookFresh:
    """Hook installation in a fresh repo (no existing hook)."""

    def test_creates_pre_commit_hook(self, tmp_path):
        """Hook file exists after install."""
        # Setup a git repo
        subprocess.run(
            ["git", "init"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )

        # Run install-hooks
        result = run_install_hooks(
            args=None,
            env={},
            cwd=tmp_path,
            stdout=None,
            stderr=None,
        )

        assert result == EXIT_PASS
        # Resolve hooks dir to check
        hooks_result = subprocess.run(
            ["git", "rev-parse", "--git-path", "hooks"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        hooks_dir = tmp_path / hooks_result.stdout.strip()
        hook_path = hooks_dir / "pre-commit"
        assert hook_path.exists()

    def test_hook_is_executable(self, tmp_path):
        """Hook file has executable bit set."""
        subprocess.run(
            ["git", "init"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )

        run_install_hooks(args=None, env={}, cwd=tmp_path)

        hooks_result = subprocess.run(
            ["git", "rev-parse", "--git-path", "hooks"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        hooks_dir = tmp_path / hooks_result.stdout.strip()
        hook_path = hooks_dir / "pre-commit"

        # Check file mode includes execute bit
        mode = os.stat(hook_path).st_mode
        assert mode & 0o111  # Any execute bit set

    def test_hook_contains_gate_check(self, tmp_path):
        """Hook content includes 'gate-check'."""
        subprocess.run(
            ["git", "init"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )

        run_install_hooks(args=None, env={}, cwd=tmp_path)

        hooks_result = subprocess.run(
            ["git", "rev-parse", "--git-path", "hooks"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        hooks_dir = tmp_path / hooks_result.stdout.strip()
        hook_path = hooks_dir / "pre-commit"

        content = hook_path.read_text()
        assert "gate-check" in content

    def test_absolute_forge_path(self, tmp_path):
        """Hook contains absolute path, not bare 'forge'."""
        subprocess.run(
            ["git", "init"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )

        run_install_hooks(args=None, env={}, cwd=tmp_path)

        hooks_result = subprocess.run(
            ["git", "rev-parse", "--git-path", "hooks"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        hooks_dir = tmp_path / hooks_result.stdout.strip()
        hook_path = hooks_dir / "pre-commit"

        content = hook_path.read_text()
        # Absolute path contains "/" (POSIX) or has sys.executable
        # We check that the line with gate-check has a path separator
        for line in content.split("\n"):
            if "gate-check" in line and not line.strip().startswith("#"):
                assert "/" in line  # Absolute path contains /
                break


class TestInstallHookChain:
    """Backup + chain when existing hook present."""

    def test_existing_hook_backed_up(self, tmp_path):
        """Existing hook is backed up to pre-commit.forge-backup."""
        subprocess.run(
            ["git", "init"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )

        # Create existing hook
        hooks_result = subprocess.run(
            ["git", "rev-parse", "--git-path", "hooks"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        hooks_dir = tmp_path / hooks_result.stdout.strip()
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hook_path = hooks_dir / "pre-commit"
        hook_path.write_text("#!/bin/sh\necho existing\n")

        # Install forge hook
        run_install_hooks(args=None, env={}, cwd=tmp_path)

        backup_path = hooks_dir / "pre-commit.forge-backup"
        assert backup_path.exists()
        assert "echo existing" in backup_path.read_text()

    def test_chain_calls_backup_first(self, tmp_path):
        """New hook calls backup before forge gate-check."""
        subprocess.run(
            ["git", "init"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )

        # Create existing hook
        hooks_result = subprocess.run(
            ["git", "rev-parse", "--git-path", "hooks"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        hooks_dir = tmp_path / hooks_result.stdout.strip()
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hook_path = hooks_dir / "pre-commit"
        hook_path.write_text("#!/bin/sh\necho existing\n")

        # Install forge hook
        run_install_hooks(args=None, env={}, cwd=tmp_path)

        content = hook_path.read_text()
        backup_path = hooks_dir / "pre-commit.forge-backup"
        assert str(backup_path) in content
        # Backup call should appear before gate-check
        backup_line = -1
        gate_line = -1
        for i, line in enumerate(content.split("\n")):
            if str(backup_path) in line and not line.strip().startswith("#"):
                backup_line = i
            if "gate-check" in line and not line.strip().startswith("#"):
                gate_line = i
        assert backup_line < gate_line

    def test_backup_preserved_on_reinstall(self, tmp_path):
        """Re-install does not overwrite existing backup."""
        subprocess.run(
            ["git", "init"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )

        hooks_result = subprocess.run(
            ["git", "rev-parse", "--git-path", "hooks"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        hooks_dir = tmp_path / hooks_result.stdout.strip()
        hooks_dir.mkdir(parents=True, exist_ok=True)

        # First install with existing hook
        hook_path = hooks_dir / "pre-commit"
        hook_path.write_text("#!/bin/sh\necho original\n")
        run_install_hooks(args=None, env={}, cwd=tmp_path)

        backup_path = hooks_dir / "pre-commit.forge-backup"
        assert "echo original" in backup_path.read_text()

        # Second install (over forge hook)
        run_install_hooks(args=None, env={}, cwd=tmp_path)

        # Backup should still contain original content
        assert "echo original" in backup_path.read_text()


class TestHooksPathAbort:
    """Abort when core.hooksPath is set."""

    def test_hooks_path_set_aborts(self, tmp_path):
        """Returns FAIL when core.hooksPath is set."""
        subprocess.run(
            ["git", "init"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "core.hooksPath", "/custom/hooks"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )

        result = run_install_hooks(args=None, env={}, cwd=tmp_path)

        assert result == EXIT_FAIL

    def test_hooks_path_unset_succeeds(self, tmp_path):
        """Returns PASS when core.hooksPath is not set."""
        subprocess.run(
            ["git", "init"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )

        result = run_install_hooks(args=None, env={}, cwd=tmp_path)

        assert result == EXIT_PASS


class TestHooksDirResolution:
    """Hooks directory resolution via git rev-parse."""

    def test_resolves_via_git_rev_parse(self, tmp_path):
        """Uses git rev-parse --git-path hooks."""
        subprocess.run(
            ["git", "init"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )

        # Mock run_cmd to verify it's called correctly
        real_run = subprocess.run
        calls = []

        def mock_run(*args, **kwargs):
            calls.append((args, kwargs))
            return real_run(*args, **kwargs)

        hooks_dir = resolve_hooks_dir(tmp_path, run_cmd=mock_run)

        # Check that git rev-parse --git-path hooks was called
        assert any(
            "git" in str(call[0]) and "--git-path" in str(call[0])
            for call in calls
        )
        assert hooks_dir.exists()

    def test_not_git_repo_fails(self, tmp_path):
        """Raises RuntimeError outside git repo."""
        with pytest.raises(RuntimeError, match="Not in a git repository"):
            resolve_hooks_dir(tmp_path)


class TestInstallHookIntegration:
    """Full install cycle integration test."""

    def test_full_install_cycle(self, tmp_path):
        """Init repo, install, verify hook works."""
        subprocess.run(
            ["git", "init"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )

        # Install hook
        result = run_install_hooks(args=None, env={}, cwd=tmp_path)
        assert result == EXIT_PASS

        # Verify hook file
        hooks_result = subprocess.run(
            ["git", "rev-parse", "--git-path", "hooks"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        hooks_dir = tmp_path / hooks_result.stdout.strip()
        hook_path = hooks_dir / "pre-commit"

        assert hook_path.exists()
        content = hook_path.read_text()
        assert "#!/bin/sh" in content
        assert "gate-check" in content
        assert os.access(hook_path, os.X_OK)


class TestHelperFunctions:
    """Unit tests for individual helper functions."""

    def test_check_hooks_path_override_set(self, tmp_path):
        """Returns value when core.hooksPath is set."""
        subprocess.run(
            ["git", "init"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "core.hooksPath", "/custom/hooks"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )

        result = check_hooks_path_override(tmp_path)
        assert result == "/custom/hooks"

    def test_check_hooks_path_override_unset(self, tmp_path):
        """Returns None when core.hooksPath is not set."""
        subprocess.run(
            ["git", "init"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )

        result = check_hooks_path_override(tmp_path)
        assert result is None

    def test_resolve_forge_path_returns_absolute(self):
        """resolve_forge_path returns absolute path."""
        path = resolve_forge_path()
        assert "/" in path  # Contains path separator
        assert "gate-check" in path

    def test_generate_hook_content_no_chain(self):
        """Hook content without chain."""
        content = generate_hook_content("/usr/bin/forge gate-check", None)
        assert "#!/bin/sh" in content
        assert "/usr/bin/forge gate-check" in content
        assert "Chained" not in content

    def test_generate_hook_content_with_chain(self):
        """Hook content with chain."""
        chain = Path("/hooks/pre-commit.forge-backup")
        content = generate_hook_content("/usr/bin/forge gate-check", chain)
        assert "#!/bin/sh" in content
        assert "/usr/bin/forge gate-check" in content
        assert str(chain) in content
        assert "Chained" in content


class TestIdempotency:
    """Idempotent re-install over forge hook."""

    def test_reinstall_over_forge_hook_skips_backup(self, tmp_path):
        """Re-installing over forge hook does not create backup."""
        subprocess.run(
            ["git", "init"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )

        # First install
        run_install_hooks(args=None, env={}, cwd=tmp_path)

        hooks_result = subprocess.run(
            ["git", "rev-parse", "--git-path", "hooks"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        hooks_dir = tmp_path / hooks_result.stdout.strip()
        backup_path = hooks_dir / "pre-commit.forge-backup"

        # Backup should NOT exist (no existing hook before first install)
        assert not backup_path.exists()

        # Second install (over forge hook)
        run_install_hooks(args=None, env={}, cwd=tmp_path)

        # Backup should STILL not exist (idempotent)
        assert not backup_path.exists()
