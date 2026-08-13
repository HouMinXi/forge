# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Dogfood regression tests for ADOPT-05.

Verifies that forge's pre-commit hook blocks commits introducing new test
failures and allows commits once failures are reverted. The test creates a
fully isolated scratch repo in tmp_path and exercises the complete
install -> commit -> block -> revert -> allow cycle.
"""

import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from code_forge.install_hooks import (
    _build_planning_leak_guard,
    generate_hook_content,
    run_install_hooks,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path, **kwargs) -> subprocess.CompletedProcess:
    """Run a git command in the given directory."""
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        **kwargs,
    )


def _init_scratch_repo(root: Path) -> None:
    """Create a git repo with identity configured and hooksPath cleared.

    Explicitly unsets core.hooksPath so the scratch repo does not inherit
    a custom value from the user's global ~/.gitconfig (which would cause
    install-hooks to reject installation).  The unset is a no-op when
    the key does not exist (git config --unset exits 5, ignored by
    check=False).
    """
    _git(["init"], root)
    _git(["config", "user.name", "Test"], root)
    _git(["config", "user.email", "test@test.com"], root)
    _git(["config", "--local", "--unset", "core.hooksPath"], root)


def _write_file(path: Path, content: str) -> None:
    """Write content to a file, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDogfood:
    """ADOPT-05: forge gates its own commits via the real pipeline."""

    def test_injected_failure_blocks_commit(self, tmp_path, monkeypatch):
        """A staged file with a new test failure is blocked by the hook.

        Full 10-step cycle:
        1. git init with identity
        2. create minimal Python project with one passing test
        3. create gate.yaml with test command
        4. create test_baseline.json with known-good baseline
        5. generate and install the pre-commit hook
        6. initial commit (passing) -- should succeed
        7. add a failing test
        8. attempt commit -- should BLOCK (non-zero exit)
        9. revert the failing test
        10. commit the revert -- should succeed
        """
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )

        # Step 1: git init
        _init_scratch_repo(tmp_path)

        # Step 2: minimal Python project
        _write_file(
            tmp_path / "pyproject.toml",
            textwrap.dedent("""\
                [tool.pytest.ini_options]
                testpaths = ["tests"]
            """),
        )
        _write_file(
            tmp_path / "tests" / "__init__.py",
            "",
        )
        _write_file(
            tmp_path / "tests" / "test_sample.py",
            textwrap.dedent("""\
                def test_pass():
                    assert True
            """),
        )

        # Step 3: gate.yaml with test command as a list
        _write_file(
            tmp_path / ".code-forge" / "gate.yaml",
            json.dumps({
                "test": {
                    "command": ["python3", "-m", "pytest", "-q"],
                    "source_patterns": ["*.py"],
                },
            }),
        )

        # Step 4: test_baseline.json with the correct schema
        _write_file(
            tmp_path / ".code-forge" / "test_baseline.json",
            json.dumps({
                "schema_version": "1.0",
                "test_results": {
                    "tests/test_sample.py::test_pass": "passed",
                },
            }),
        )

        # Step 5: write a pre-commit hook that runs gate-check directly.
        # We bypass generate_hook_content here because it includes the
        # attestation block (code-forge verify), which requires a trusted
        # receipt that doesn't exist in a scratch repo. The test exercises
        # the gate-check path specifically (the end-to-end mechanism that
        # blocks new test failures).
        src_dir = Path(__file__).resolve().parent.parent / "src"
        hook_script = textwrap.dedent("""\
            #!/bin/sh
            # dogfood test hook: gate-check only
            PYTHONPATH=%s exec %s -m code_forge gate-check
        """) % (src_dir, sys.executable)

        # Step 6: write hook and make executable
        hook_path = tmp_path / ".git" / "hooks" / "pre-commit"
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_text(hook_script, encoding="utf-8")
        hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)

        # Initial commit with all files (should pass)
        _git(["add", "."], tmp_path)
        result = _git(["commit", "-m", "initial: passing tests"], tmp_path)
        assert result.returncode == 0, (
            "Initial commit should pass.\nstdout: %s\nstderr: %s"
            % (result.stdout, result.stderr)
        )

        # Step 7: inject a failing test
        _write_file(
            tmp_path / "tests" / "test_sample.py",
            textwrap.dedent("""\
                def test_pass():
                    assert True

                def test_fail():
                    assert False
            """),
        )

        # Step 8: attempt commit -- should BLOCK
        _git(["add", "tests/test_sample.py"], tmp_path)
        result = _git(["commit", "-m", "inject: add failing test"], tmp_path)
        assert result.returncode != 0, (
            "Commit with failing test should be blocked.\nstdout: %s\nstderr: %s"
            % (result.stdout, result.stderr)
        )

        # Step 9: revert the failing test, add a second passing test
        # to create a real diff (reverting to identical content produces
        # an empty staging area which git refuses to commit).
        _write_file(
            tmp_path / "tests" / "test_sample.py",
            textwrap.dedent("""\
                def test_pass():
                    assert True

                def test_also_passes():
                    assert 1 + 1 == 2
            """),
        )

        # Step 10: commit the fix -- should pass
        _git(["add", "tests/test_sample.py"], tmp_path)
        result = _git(["commit", "-m", "fix: replace failing test with passing one"], tmp_path)
        assert result.returncode == 0, (
            "Commit after reverting failure should pass.\nstdout: %s\nstderr: %s"
            % (result.stdout, result.stderr)
        )

    def test_planning_leak_guard_blocks_staging(
        self, tmp_path, monkeypatch
    ):
        """Planning-leak guard blocks commits that stage .planning/ or CLAUDE.md.

        Exercises the runtime blocking behavior of _build_planning_leak_guard()
        by installing a hook with the guard enabled, staging forbidden paths,
        and verifying the commit is rejected with the expected error message.
        """
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )

        _init_scratch_repo(tmp_path)
        _write_file(tmp_path / "README.md", "# test\n")
        _git(["add", "."], tmp_path)
        _git(["commit", "-m", "initial"], tmp_path)

        # Install a minimal hook with only the planning-leak guard.
        # Attestation and gate-check are omitted to isolate guard behavior.
        hook_script = (
            "#!/bin/sh\n"
            + _build_planning_leak_guard()
            + "exit 0\n"
        )
        hook_path = tmp_path / ".git" / "hooks" / "pre-commit"
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_text(hook_script, encoding="utf-8")
        hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)

        # Staging .planning/ should be blocked
        _write_file(tmp_path / ".planning" / "STATE.md", "leak test\n")
        _git(["add", "-f", ".planning/STATE.md"], tmp_path)
        result = _git(["commit", "-m", "leak: planning"], tmp_path)
        assert result.returncode != 0, (
            "Commit staging .planning/ should be blocked.\n"
            "stdout: %s\nstderr: %s" % (result.stdout, result.stderr)
        )
        assert "BLOCKED" in result.stderr, (
            "Error should mention BLOCKED.\nstderr: %s" % result.stderr
        )
        assert ".planning/STATE.md" in result.stderr, (
            "Error should list the offending path.\nstderr: %s" % result.stderr
        )
        _git(["reset", "HEAD", ".planning/STATE.md"], tmp_path)

        # Staging CLAUDE.md should also be blocked
        _write_file(tmp_path / "CLAUDE.md", "leak test\n")
        _git(["add", "-f", "CLAUDE.md"], tmp_path)
        result = _git(["commit", "-m", "leak: claude"], tmp_path)
        assert result.returncode != 0, (
            "Commit staging CLAUDE.md should be blocked.\n"
            "stdout: %s\nstderr: %s" % (result.stdout, result.stderr)
        )
        assert "CLAUDE.md" in result.stderr, (
            "Error should list CLAUDE.md.\nstderr: %s" % result.stderr
        )
        _git(["reset", "HEAD", "CLAUDE.md"], tmp_path)

        # A normal file should pass through the guard
        _write_file(tmp_path / "normal.txt", "safe\n")
        _git(["add", "normal.txt"], tmp_path)
        result = _git(["commit", "-m", "safe commit"], tmp_path)
        assert result.returncode == 0, (
            "Normal commit should pass.\n"
            "stdout: %s\nstderr: %s" % (result.stdout, result.stderr)
        )

    def test_forge_detection_enables_planning_leak_guard(
        self, tmp_path, monkeypatch
    ):
        """run_install_hooks auto-detects forge repos via marker file.

        Verifies the DETECTION logic: when src/code_forge/__init__.py
        exists relative to cwd, run_install_hooks passes
        planning_leak_guard=True and the generated hook contains the
        planning-leak guard block. This does NOT install a functional
        forge package -- it only creates the marker file to trigger
        detection.
        """
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )

        # Set up a git repo that looks like forge
        _init_scratch_repo(tmp_path)

        # The forge-detection marker
        _write_file(
            tmp_path / "src" / "code_forge" / "__init__.py",
            "",
        )

        # Need an initial commit so the hook can function
        _git(["add", "."], tmp_path)
        _git(["commit", "-m", "initial"], tmp_path)

        # Run install-hooks with cwd pointing at the scratch repo
        import io
        out = io.StringIO()
        err = io.StringIO()
        rc = run_install_hooks(
            cwd=tmp_path,
            stdout=out,
            stderr=err,
        )
        assert rc == 0, (
            "install-hooks should succeed.\nstdout: %s\nstderr: %s"
            % (out.getvalue(), err.getvalue())
        )

        # Read the installed hook and verify it contains the planning-leak guard
        hook_path = tmp_path / ".git" / "hooks" / "pre-commit"
        assert hook_path.exists(), "pre-commit hook should be installed"
        hook_text = hook_path.read_text(encoding="utf-8")
        assert "planning-leak guard" in hook_text, (
            "Hook should contain planning-leak guard for forge repos.\n"
            "Hook content:\n%s" % hook_text
        )
        assert "_LEAK=$(git diff --cached --name-only" in hook_text, (
            "Hook should contain the leak detection logic"
        )

    def test_no_planning_leak_guard_for_non_forge_repos(
        self, tmp_path, monkeypatch
    ):
        """run_install_hooks does NOT enable the leak guard for non-forge repos."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )

        # Set up a plain git repo (no src/code_forge/__init__.py)
        _init_scratch_repo(tmp_path)
        _write_file(tmp_path / "README.md", "# test repo\n")
        _git(["add", "."], tmp_path)
        _git(["commit", "-m", "initial"], tmp_path)

        import io
        out = io.StringIO()
        err = io.StringIO()
        rc = run_install_hooks(
            cwd=tmp_path,
            stdout=out,
            stderr=err,
        )
        assert rc == 0, (
            "install-hooks should succeed.\nstdout: %s\nstderr: %s"
            % (out.getvalue(), err.getvalue())
        )

        hook_path = tmp_path / ".git" / "hooks" / "pre-commit"
        assert hook_path.exists(), "pre-commit hook should be installed"
        hook_text = hook_path.read_text(encoding="utf-8")
        assert "planning-leak guard" not in hook_text, (
            "Hook should NOT contain planning-leak guard for non-forge repos.\n"
            "Hook content:\n%s" % hook_text
        )
