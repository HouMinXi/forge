# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for the install-hooks subcommand."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from code_forge.exit_codes import EXIT_FAIL, EXIT_PASS
from code_forge.install_hooks import (
    check_hooks_path_override,
    generate_commit_msg_hook_content,
    generate_hook_content,
    resolve_forge_path,
    resolve_hooks_dir,
    run_install_hooks,
)

# ---------------------------------------------------------------------------
# Shared test fixtures (module-level constants used by TestPresubmitRunner
# and TestBuiltinD12Check)
# ---------------------------------------------------------------------------

_ENTRY_DIFF = {
    "command": ["ruff", "check", "--select=E,W"],
    "applies_to": "*.py",
    "on": "diff",
    "applies_to_grep": "^.*\\.py$",
}
_ENTRY_PATCH = {
    "command": ["scripts/lint.sh"],
    "applies_to": "*.c",
    "on": "patch",
    "applies_to_grep": "^.*\\.c$",
}
_ENTRY_WHEN_EXISTS = {
    "command": ["scripts/checkpatch.pl", "--strict"],
    "applies_to": "*.c",
    "on": "diff",
    "applies_to_grep": "^.*\\.c$",
    "when_exists": "scripts/checkpatch.pl",
}


class TestInstallHookFresh:
    """Hook installation in a fresh repo (no existing hook)."""

    def test_creates_pre_commit_hook(self, tmp_path, monkeypatch):
        """Hook file exists after install."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
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

    def test_hook_is_executable(self, tmp_path, monkeypatch):
        """Hook file has executable bit set."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
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

    def test_hook_contains_gate_check(self, tmp_path, monkeypatch):
        """Hook content includes 'gate-check'."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
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

    def test_absolute_forge_path(self, tmp_path, monkeypatch):
        """Hook contains absolute path, not bare 'code-forge'."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
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

    def test_existing_hook_backed_up(self, tmp_path, monkeypatch):
        """Existing hook is backed up to pre-commit.code-forge-backup."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
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

        backup_path = hooks_dir / "pre-commit.code-forge-backup"
        assert backup_path.exists()
        assert "echo existing" in backup_path.read_text()

    def test_chain_calls_backup_first(self, tmp_path, monkeypatch):
        """New hook calls backup before forge gate-check."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
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
        backup_path = hooks_dir / "pre-commit.code-forge-backup"
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

    def test_backup_preserved_on_reinstall(self, tmp_path, monkeypatch):
        """Re-install does not overwrite existing backup."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
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

        backup_path = hooks_dir / "pre-commit.code-forge-backup"
        assert "echo original" in backup_path.read_text()

        # Second install (over forge hook)
        run_install_hooks(args=None, env={}, cwd=tmp_path)

        # Backup should still contain original content
        assert "echo original" in backup_path.read_text()


class TestNonForgeHookWithBackup:
    """Block when backup exists and hook_path is non-forge (ambiguous state)."""

    def test_non_forge_hook_with_backup_blocks(self, tmp_path, monkeypatch):
        """Returns FAIL when backup exists and hook is not forge-generated."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
        import io
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

        # Manually create both: an existing backup and a non-forge hook
        backup_path = hooks_dir / "pre-commit.code-forge-backup"
        backup_path.write_text("#!/bin/sh\necho old-backup\n")
        hook_path = hooks_dir / "pre-commit"
        hook_path.write_text("#!/bin/sh\necho manual-hook\n")

        stderr = io.StringIO()
        result = run_install_hooks(
            args=None, env={}, cwd=tmp_path,
            stdout=None, stderr=stderr,
        )
        assert result == EXIT_FAIL
        assert "error" in stderr.getvalue().lower()
        # Manual hook content is NOT lost (hook_path still has it)
        assert "manual-hook" in hook_path.read_text()
        # Backup is NOT overwritten
        assert "old-backup" in backup_path.read_text()


class TestHooksPathAbort:
    """Abort when core.hooksPath is set."""

    def test_hooks_path_set_aborts(self, tmp_path, monkeypatch):
        """Returns FAIL when core.hooksPath is set."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
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

    def test_hooks_path_unset_succeeds(self, tmp_path, monkeypatch):
        """Returns PASS when core.hooksPath is not set."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
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

    def test_resolves_via_git_rev_parse(self, tmp_path, monkeypatch):
        """Uses git rev-parse --git-path hooks."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
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

    def test_not_git_repo_fails(self, tmp_path, monkeypatch):
        """Raises RuntimeError outside git repo."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
        with pytest.raises(RuntimeError, match="Not in a git repository"):
            resolve_hooks_dir(tmp_path)


class TestInstallHookIntegration:
    """Full install cycle integration test."""

    def test_full_install_cycle(self, tmp_path, monkeypatch):
        """Init repo, install, verify hook works."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
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

    def test_check_hooks_path_override_set(self, tmp_path, monkeypatch):
        """Returns value when core.hooksPath is set."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
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

    def test_check_hooks_path_override_unset(self, tmp_path, monkeypatch):
        """Returns None when core.hooksPath is not set."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
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
        content = generate_hook_content("/usr/bin/code-forge gate-check", None)
        assert "#!/bin/sh" in content
        assert "/usr/bin/code-forge gate-check" in content
        assert "Chained" not in content

    def test_generate_hook_content_with_chain(self):
        """Hook content with chain."""
        chain = Path("/hooks/pre-commit.code-forge-backup")
        content = generate_hook_content("/usr/bin/code-forge gate-check", chain)
        assert "#!/bin/sh" in content
        assert "/usr/bin/code-forge gate-check" in content
        assert str(chain) in content
        assert "Chained" in content


class TestIdempotency:
    """Idempotent re-install over code-forge hook."""

    def test_reinstall_over_forge_hook_skips_backup(self, tmp_path, monkeypatch):
        """Re-installing over forge hook does not create backup."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
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
        backup_path = hooks_dir / "pre-commit.code-forge-backup"

        # Backup should NOT exist (no existing hook before first install)
        assert not backup_path.exists()

        # Second install (over forge hook)
        run_install_hooks(args=None, env={}, cwd=tmp_path)

        # Backup should STILL not exist (idempotent)
        assert not backup_path.exists()


class TestQuietFlag:
    """quiet flag suppresses informational output."""

    def test_quiet_suppresses_info(self, tmp_path, monkeypatch):
        """args.quiet=True suppresses installed-at message."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
        import types
        subprocess.run(
            ["git", "init"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )

        import io
        stderr = io.StringIO()
        args = types.SimpleNamespace(quiet=True)
        result = run_install_hooks(
            args=args, env={}, cwd=tmp_path,
            stdout=None, stderr=stderr,
        )
        assert result == EXIT_PASS
        # With quiet=True, no informational output
        assert stderr.getvalue() == ""

    def test_no_quiet_shows_info(self, tmp_path, monkeypatch):
        """Without quiet, installed-at message appears."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
        subprocess.run(
            ["git", "init"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )

        import io
        stderr = io.StringIO()
        result = run_install_hooks(
            args=None, env={}, cwd=tmp_path,
            stdout=None, stderr=stderr,
        )
        assert result == EXIT_PASS
        assert "pre-commit hook installed" in stderr.getvalue()


class TestHookAttestation:
    """Hook content includes attestation check."""

    def test_includes_attestation_check(self):
        """Hook content includes code-forge verify call."""
        content = generate_hook_content("/usr/bin/code-forge gate-check", None)
        assert "code-forge verify" in content

    def test_attestation_before_gate_check(self):
        """Attestation check appears before gate-check."""
        content = generate_hook_content("/usr/bin/code-forge gate-check", None)
        lines = content.split("\n")
        verify_line = -1
        gate_line = -1
        for i, line in enumerate(lines):
            if "code-forge verify" in line and not line.strip().startswith("#"):
                verify_line = i
            if "gate-check" in line and not line.strip().startswith("#"):
                gate_line = i
        assert verify_line != -1, "verify not found"
        assert gate_line != -1, "gate-check not found"
        assert verify_line < gate_line

    def test_attestation_with_chain(self):
        """Attestation check also present when chaining."""
        chain = Path("/hooks/pre-commit.code-forge-backup")
        content = generate_hook_content("/usr/bin/code-forge gate-check", chain)
        assert "code-forge verify" in content


class TestResolveForgeLiveness:
    """Test 11-14: resolve_forge_path liveness check."""

    def test_forge_passes_version_check(self):
        """Test 11: code-forge binary passes --version -> uses the binary path."""
        from unittest.mock import MagicMock, patch

        mock_which = MagicMock(return_value="/usr/bin/code-forge")
        mock_run = MagicMock(
            return_value=subprocess.CompletedProcess(
                args=["/usr/bin/code-forge", "--version"],
                returncode=0,
                stdout="code-forge 2.0.0a1\n",
                stderr="",
            )
        )

        with patch("code_forge.install_hooks.shutil.which", mock_which):
            with patch("code_forge.install_hooks.subprocess.run", mock_run):
                with patch("code_forge.install_hooks.os.access", return_value=True):
                    path = resolve_forge_path()
                assert "/usr/bin/code-forge" in path
                assert "gate-check" in path

    def test_forge_fails_version_check_fallback(self):
        """Test 12: code-forge binary fails --version -> falls back to sys.executable."""
        from unittest.mock import MagicMock, patch

        mock_which = MagicMock(return_value="/usr/bin/code-forge")
        mock_run = MagicMock(
            return_value=subprocess.CompletedProcess(
                args=["/usr/bin/code-forge", "--version"],
                returncode=1,
                stdout="",
                stderr="error",
            )
        )

        with patch("code_forge.install_hooks.shutil.which", mock_which):
            with patch("code_forge.install_hooks.subprocess.run", mock_run):
                path = resolve_forge_path()
                assert sys.executable in path or "-m code_forge" in path

    def test_forge_version_times_out_fallback(self):
        """Test 13: code-forge --version times out -> falls back to sys.executable."""
        from unittest.mock import MagicMock, patch

        mock_which = MagicMock(return_value="/usr/bin/code-forge")

        def side_effect(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=1)

        mock_run = MagicMock(side_effect=side_effect)

        with patch("code_forge.install_hooks.shutil.which", mock_which):
            with patch("code_forge.install_hooks.subprocess.run", mock_run):
                path = resolve_forge_path()
                assert sys.executable in path or "-m code_forge" in path

    def test_forge_version_invalid_output_fallback(self):
        """Test 14: code-forge --version stdout does not start with "code-forge " -> fallback."""
        from unittest.mock import MagicMock, patch

        mock_which = MagicMock(return_value="/usr/bin/code-forge")
        mock_run = MagicMock(
            return_value=subprocess.CompletedProcess(
                args=["/usr/bin/code-forge", "--version"],
                returncode=0,
                stdout="invalid output\n",
                stderr="",
            )
        )

        with patch("code_forge.install_hooks.shutil.which", mock_which):
            with patch("code_forge.install_hooks.subprocess.run", mock_run):
                path = resolve_forge_path()
                assert sys.executable in path or "-m code_forge" in path



class TestPresubmitRunner:
    """Task 1: presubmit runner block in generate_hook_content."""

    def test_no_entries_no_presubmit_block(self):
        """generate_hook_content with presubmit_entries=[] produces no presubmit block."""
        content = generate_hook_content(
            "/usr/bin/code-forge gate-check", None, presubmit_entries=[]
        )
        assert "presubmit" not in content.lower()

    def test_none_entries_no_presubmit_block(self):
        """generate_hook_content with presubmit_entries=None produces no presubmit block."""
        content = generate_hook_content(
            "/usr/bin/code-forge gate-check", None, presubmit_entries=None
        )
        assert "presubmit" not in content.lower()

    def test_on_diff_pipes_git_diff_cached(self):
        """on=diff entry pipes git diff --cached to command."""
        content = generate_hook_content(
            "/usr/bin/code-forge gate-check", None,
            presubmit_entries=[_ENTRY_DIFF],
        )
        assert "git diff --cached" in content
        assert "ruff" in content

    def test_on_patch_pipes_git_diff_cached(self):
        """on=patch entry also pipes git diff --cached to command."""
        content = generate_hook_content(
            "/usr/bin/code-forge gate-check", None,
            presubmit_entries=[_ENTRY_PATCH],
        )
        assert "git diff --cached" in content
        assert "scripts/lint.sh" in content

    def test_unexpected_on_raises_value_error(self):
        """generate_hook_content raises ValueError for unexpected on value."""
        bad_entry = {
            "command": ["foo"],
            "applies_to": "*.py",
            "on": "message",
            "applies_to_grep": "^.*\\.py$",
        }
        with pytest.raises(ValueError, match="on"):
            generate_hook_content(
                "/usr/bin/code-forge gate-check", None,
                presubmit_entries=[bad_entry],
            )

    def test_presubmit_block_after_carveout(self):
        """Presubmit block appears after carveout block (non-code commits exit first)."""
        content = generate_hook_content(
            "/usr/bin/code-forge gate-check", None,
            presubmit_entries=[_ENTRY_DIFF],
        )
        lines = content.split("\n")
        carveout_line = -1
        presubmit_line = -1
        for i, line in enumerate(lines):
            if "skipping verify (non-code commit)" in line:
                carveout_line = i
            if "code-forge: presubmit" in line and presubmit_line == -1:
                presubmit_line = i
        assert carveout_line != -1, "carveout block not found"
        assert presubmit_line != -1, "presubmit block not found"
        assert carveout_line < presubmit_line

    def test_presubmit_block_before_gate_check(self):
        """Presubmit block appears before exec gate-check."""
        content = generate_hook_content(
            "/usr/bin/code-forge gate-check", None,
            presubmit_entries=[_ENTRY_DIFF],
        )
        lines = content.split("\n")
        presubmit_line = -1
        gate_line = -1
        for i, line in enumerate(lines):
            if "code-forge: presubmit" in line and presubmit_line == -1:
                presubmit_line = i
            if "gate-check" in line and not line.strip().startswith("#") and gate_line == -1:
                gate_line = i
        assert presubmit_line != -1, "presubmit block not found"
        assert gate_line != -1, "gate-check not found"
        assert presubmit_line < gate_line

    def test_no_match_files_literal_in_hook(self):
        """Generated hook never contains the literal '$_MATCH_FILES' substring."""
        content = generate_hook_content(
            "/usr/bin/code-forge gate-check", None,
            presubmit_entries=[_ENTRY_DIFF],
        )
        assert "$_MATCH_FILES" not in content

    def test_command_existence_check_both_forms(self):
        """Generated hook checks command existence with both command -v AND [ -x ]."""
        content = generate_hook_content(
            "/usr/bin/code-forge gate-check", None,
            presubmit_entries=[_ENTRY_DIFF],
        )
        assert "command -v" in content
        assert "[ -x" in content

    def test_when_exists_wraps_in_guard(self):
        """Presubmit entry with when_exists wraps block in if-exists guard."""
        content = generate_hook_content(
            "/usr/bin/code-forge gate-check", None,
            presubmit_entries=[_ENTRY_WHEN_EXISTS],
        )
        assert "scripts/checkpatch.pl" in content
        # when_exists guard: [ -e "path" ]
        assert '[ -e "scripts/checkpatch.pl"' in content

    def test_applies_to_filters_staged_files(self):
        """Presubmit entry with applies_to filters staged files via grep."""
        content = generate_hook_content(
            "/usr/bin/code-forge gate-check", None,
            presubmit_entries=[_ENTRY_DIFF],
        )
        # applies_to_grep value must appear in generated shell
        assert "^.*\\.py$" in content
        # _MATCH variable used to filter
        assert "_MATCH" in content

    def test_run_install_hooks_reads_presubmit_from_gate_yaml(self, tmp_path, monkeypatch):
        """run_install_hooks reads presubmit from gate.yaml and passes to generate_hook_content."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES", str(tmp_path.parent), prepend=os.pathsep
        )
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)

        forge_dir = tmp_path / ".code-forge"
        forge_dir.mkdir()
        gate_yaml = forge_dir / "gate.yaml"
        gate_yaml.write_text(
            "test:\n"
            "  command: [pytest, -q]\n"
            "presubmit:\n"
            "  - command: [ruff, check]\n"
            "    applies_to: '*.py'\n"
            "    'on': diff\n"
        )

        result = run_install_hooks(args=None, env={}, cwd=tmp_path)
        assert result == EXIT_PASS

        hooks_result = subprocess.run(
            ["git", "rev-parse", "--git-path", "hooks"],
            cwd=tmp_path, capture_output=True, text=True, check=True,
        )
        hooks_dir = tmp_path / hooks_result.stdout.strip()
        content = (hooks_dir / "pre-commit").read_text()
        assert "ruff" in content

    def test_run_install_hooks_no_gate_yaml_no_presubmit(self, tmp_path, monkeypatch):
        """run_install_hooks with no gate.yaml generates hook with no presubmit block."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES", str(tmp_path.parent), prepend=os.pathsep
        )
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)

        result = run_install_hooks(args=None, env={}, cwd=tmp_path)
        assert result == EXIT_PASS

        hooks_result = subprocess.run(
            ["git", "rev-parse", "--git-path", "hooks"],
            cwd=tmp_path, capture_output=True, text=True, check=True,
        )
        hooks_dir = tmp_path / hooks_result.stdout.strip()
        content = (hooks_dir / "pre-commit").read_text()
        assert "presubmit FAILED" not in content

    def test_run_install_hooks_malformed_presubmit_fails(self, tmp_path, monkeypatch):
        """run_install_hooks with malformed presubmit returns EXIT_FAIL (fail-fast)."""
        import io as _io
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES", str(tmp_path.parent), prepend=os.pathsep
        )
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)

        forge_dir = tmp_path / ".code-forge"
        forge_dir.mkdir()
        gate_yaml = forge_dir / "gate.yaml"
        # Missing 'on' field -- should fail validation
        gate_yaml.write_text(
            "test:\n"
            "  command: [pytest, -q]\n"
            "presubmit:\n"
            "  - command: [ruff, check]\n"
            "    applies_to: '*.py'\n"
        )

        stderr_buf = _io.StringIO()
        result = run_install_hooks(args=None, env={}, cwd=tmp_path, stderr=stderr_buf)
        assert result == EXIT_FAIL
