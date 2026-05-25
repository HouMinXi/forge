# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""install-hooks subcommand: write .git/hooks/pre-commit hook.

Resolves the hooks directory (worktree-safe), backs up and chains any
existing hook, embeds an absolute forge path, and aborts when
core.hooksPath is set. Idempotent on re-install.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import IO, Callable, Mapping, Optional

from .exit_codes import EXIT_FAIL, EXIT_PASS


def resolve_hooks_dir(
    cwd: Path,
    run_cmd: Callable = subprocess.run,
) -> Path:
    """Resolve git hooks directory via git rev-parse --git-path hooks.

    Args:
        cwd: working directory (repo root)
        run_cmd: subprocess.run callable (injected for testing)

    Returns:
        Absolute Path to hooks directory

    Raises:
        RuntimeError: if not in a git repo or git command fails
    """
    try:
        result = run_cmd(
            ["git", "rev-parse", "--git-path", "hooks"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
            cwd=str(cwd),
        )
        hooks_path = result.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "Not in a git repository or git command failed: %s" % e
        ) from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            "git rev-parse timed out: %s" % e
        ) from e

    # If relative, resolve against repo root
    hooks_path_obj = Path(hooks_path)
    if not hooks_path_obj.is_absolute():
        # Get repo root via git rev-parse --show-toplevel
        try:
            root_result = run_cmd(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
                cwd=str(cwd),
            )
            repo_root = Path(root_result.stdout.strip())
            hooks_path_obj = repo_root / hooks_path_obj
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                "git rev-parse --show-toplevel failed: %s" % e
            ) from e

    return hooks_path_obj.resolve()


def check_hooks_path_override(
    cwd: Path,
    run_cmd: Callable = subprocess.run,
) -> str | None:
    """Check if core.hooksPath is set.

    Args:
        cwd: working directory
        run_cmd: subprocess.run callable (injected for testing)

    Returns:
        The value of core.hooksPath if set and non-empty, None otherwise
    """
    try:
        result = run_cmd(
            ["git", "config", "--get", "core.hooksPath"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
            cwd=str(cwd),
        )
        # returncode 1 means "not set", which is normal
        if result.returncode == 0:
            value = result.stdout.strip()
            return value if value else None
        return None
    except subprocess.TimeoutExpired:
        return None


def resolve_forge_path() -> str:
    """Resolve absolute forge path for hook embedding.

    Returns:
        Absolute path string that will be embedded in hook.
        For single executable: "/usr/local/bin/forge gate-check"
        For python module: "/usr/bin/python3 -m forge gate-check"

    Raises:
        RuntimeError: if no valid forge executable path found
    """
    # Try shutil.which('forge') first
    forge_exe = shutil.which("forge")
    if forge_exe is not None and os.access(forge_exe, os.X_OK):
        return "%s gate-check" % forge_exe

    # Fallback to sys.executable + ' -m forge'
    if sys.executable and os.access(sys.executable, os.X_OK):
        return "%s -m forge gate-check" % sys.executable

    raise RuntimeError(
        "Cannot resolve forge path: 'forge' not on PATH and "
        "sys.executable is not valid"
    )


def generate_hook_content(
    forge_invocation: str,
    chain_path: Path | None,
) -> str:
    """Generate pre-commit hook shell script content.

    Args:
        forge_invocation: absolute forge path + args (e.g. "/usr/local/bin/forge gate-check")
        chain_path: Path to existing hook backup, or None

    Returns:
        Shell script content as string
    """
    if chain_path is not None:
        return f"""#!/bin/sh
# Forge pre-commit gate-check (installed by forge install-hooks)
# Chained existing hook: {chain_path}
"{chain_path}" "$@" || exit 1
exec {forge_invocation}
"""
    else:
        return f"""#!/bin/sh
# Forge pre-commit gate-check (installed by forge install-hooks)
exec {forge_invocation}
"""


def run_install_hooks(
    args=None,
    env: Optional[Mapping[str, str]] = None,
    cwd: Optional[Path] = None,
    stdout: Optional[IO] = None,
    stderr: Optional[IO] = None,
) -> int:
    """Main install-hooks entry point.

    Args:
        args: parsed argparse Namespace (unused for now, for future flags)
        env: environment variables (os.environ if None)
        cwd: working directory (Path.cwd() if None)
        stdout: output stream (sys.stdout if None)
        stderr: error stream (sys.stderr if None)

    Returns:
        EXIT_PASS (0) on success, EXIT_FAIL (1) on any error
    """
    if env is None:
        env = os.environ
    if cwd is None:
        cwd = Path.cwd()
    if stdout is None:
        stdout = sys.stdout
    if stderr is None:
        stderr = sys.stderr

    try:
        # Step a: check core.hooksPath override
        hooks_path_override = check_hooks_path_override(cwd)
        if hooks_path_override is not None:
            print(
                "forge: error: core.hooksPath is set to '%s'. "
                "forge install-hooks cannot install to a custom hooks path. "
                "Add 'exec /path/to/forge gate-check' to your pre-commit hook manually."
                % hooks_path_override,
                file=stderr,
            )
            return EXIT_FAIL

        # Step b: resolve hooks directory
        hooks_dir = resolve_hooks_dir(cwd)

        # Step c: check for .pre-commit-config.yaml
        pre_commit_config = cwd / ".pre-commit-config.yaml"
        if pre_commit_config.exists():
            print(
                "forge: warning: pre-commit framework detected. "
                "forge hook will chain after existing hooks.",
                file=stderr,
            )

        # Step d: resolve forge absolute path
        forge_invocation = resolve_forge_path()

        # Step e: check for existing pre-commit hook
        hook_path = hooks_dir / "pre-commit"
        backup_path = hooks_dir / "pre-commit.forge-backup"
        chain_path = None

        if hook_path.exists():
            # Read first 3 lines to check if it's a forge-generated hook
            try:
                with open(hook_path, "r", encoding="utf-8") as f:
                    first_lines = [f.readline() for _ in range(3)]
                hook_header = "".join(first_lines)
                is_forge_hook = "forge gate-check" in hook_header
            except (OSError, UnicodeDecodeError):
                is_forge_hook = False

            if is_forge_hook:
                # Idempotent re-install: skip backup, overwrite
                print(
                    "forge: re-installing hook (existing is forge-generated)",
                    file=stderr,
                )
            else:
                # Backup existing non-forge hook
                if backup_path.exists():
                    # Preserve original backup, don't overwrite
                    print(
                        "forge: warning: backup already exists at %s; "
                        "preserving original backup"
                        % backup_path,
                        file=stderr,
                    )
                else:
                    # Move existing hook to backup
                    shutil.move(str(hook_path), str(backup_path))
                    print(
                        "forge: existing hook backed up to %s"
                        % backup_path,
                        file=stderr,
                    )
                chain_path = backup_path

        # Step f: generate hook content
        hook_content = generate_hook_content(forge_invocation, chain_path)

        # Step g: write hook file
        hooks_dir.mkdir(parents=True, exist_ok=True)
        with open(hook_path, "w", encoding="utf-8") as f:
            f.write(hook_content)

        # Step h: chmod 0o755
        os.chmod(hook_path, 0o755)

        # Step i: success message
        print(
            "forge: pre-commit hook installed at %s" % hook_path,
            file=stderr,
        )

        return EXIT_PASS

    except Exception as e:
        print(
            "forge: error: install-hooks failed: %s" % e,
            file=stderr,
        )
        return EXIT_FAIL
