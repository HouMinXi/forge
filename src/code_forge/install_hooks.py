# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""install-hooks subcommand: write .git/hooks/pre-commit hook.

Resolves the hooks directory (worktree-safe), backs up and chains any
existing hook, embeds an absolute code-forge path, and aborts when
core.hooksPath is set. Idempotent on re-install.
"""
from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import IO, Callable, Mapping, Optional

from .exit_codes import EXIT_FAIL, EXIT_PASS
from .gate_check import load_gate_config


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
    """Resolve absolute code-forge path for hook embedding.

    Returns:
        Absolute path string that will be embedded in hook.
        For single executable: "/usr/local/bin/code-forge gate-check"
        For python module: "/usr/bin/python3 -m code_forge gate-check"

    Raises:
        RuntimeError: if no valid code-forge executable path found
    """
    logger = logging.getLogger("code_forge")

    # Try shutil.which('code-forge') first
    forge_exe = shutil.which("code-forge")
    if forge_exe is not None and os.access(forge_exe, os.X_OK):
        # Run liveness check
        try:
            result = subprocess.run(
                [forge_exe, "--version"],
                capture_output=True,
                text=True,
                timeout=1,
                check=False,
            )
            if (
                result.returncode == 0
                and result.stdout.strip().startswith("code-forge ")
            ):
                return "%s gate-check" % shlex.quote(forge_exe)
            else:
                logger.warning(
                    "code-forge at %s failed --version check; "
                    "falling back to sys.executable",
                    forge_exe,
                )
        except subprocess.TimeoutExpired:
            logger.warning(
                "code-forge at %s --version timed out; "
                "falling back to sys.executable",
                forge_exe,
            )
        except Exception as e:
            logger.warning(
                "code-forge at %s --version raised %s; "
                "falling back to sys.executable",
                forge_exe,
                e,
            )

    # Fallback to sys.executable + ' -m code_forge'
    if sys.executable and os.access(sys.executable, os.X_OK):
        return "%s -m code_forge gate-check" % shlex.quote(sys.executable)

    raise RuntimeError(
        "Cannot resolve code-forge path: 'code-forge' not on PATH and "
        "sys.executable is not valid"
    )


# Non-ASCII grep patterns for two-mode check (ai-smell and strict).
# ai-smell: only confusable typographic chars by Unicode codepoint (PCRE \x{HHHH}).
# Codepoints covered:
#   U+2014 em dash, U+2013 en dash,
#   U+2018/U+2019 smart single quotes, U+201C/U+201D smart double quotes,
#   U+2026 ellipsis, U+2192 right arrow, U+00A0 non-breaking space.
_NON_ASCII_PATTERN_AI_SMELL = (
    r"[\x{2014}\x{2013}\x{2018}\x{2019}\x{201C}\x{201D}\x{2026}\x{2192}\x{00A0}]"
)
# strict: all non-ASCII characters.
_NON_ASCII_PATTERN_STRICT = r"[^\x00-\x7F]"

# AI-vocab check: 6-word high-signal subset from the full 19-word SKILL.md list.
# This narrower set is intentional -- the full 19-word list produces false
# positives in technical code (e.g. "robust", "ensure", "leverage" appear in
# legitimate API docs and release notes). The 6-word subset catches the
# clearest AI-smell markers with very low false-positive rate.
_AI_VOCAB_PATTERN = (
    "delve|tapestry|testament|moreover|furthermore|it is worth noting"
)


def _build_non_ascii_pattern(non_ascii_mode: str) -> str:
    """Return grep -P pattern string for the given non_ascii_mode."""
    if non_ascii_mode == "strict":
        return _NON_ASCII_PATTERN_STRICT
    return _NON_ASCII_PATTERN_AI_SMELL


def _build_d12_precommit_block(non_ascii_mode: str) -> str:
    """Build the staged-diff non-ASCII + AI-vocab check block.

    Placed after carveout and attestation, before presubmit runner.
    Runs only for code commits (non-code exits at carveout).
    """
    pattern = _build_non_ascii_pattern(non_ascii_mode)
    return (
        "# built-in: non-ASCII check on staged diff\n"
        "# ai-smell mode blocks confusable typographic chars;"
        " strict mode blocks all non-ASCII\n"
        "_NON_ASCII=$(git diff --cached -U0 | grep '^+' | grep -v '^+++' | \\\n"
        "    grep -P '%s' | head -5)\n" % pattern
        + "if [ -n \"$_NON_ASCII\" ]; then\n"
        "    echo \"code-forge: non-ASCII characters in staged diff:\" >&2\n"
        "    printf '%%s\\n' \"$_NON_ASCII\" >&2\n"
        "    exit 1\n"
        "fi\n"
        "# built-in: AI-vocab check on staged diff\n"
        "# 6-word high-signal subset (narrower than SKILL.md's 19-word list"
        " to reduce false positives)\n"
        "_AI_VOCAB=$(git diff --cached -U0 | grep '^+' | grep -v '^+++' | grep -iE \\\n"
        "    '%s' | head -5)\n" % _AI_VOCAB_PATTERN
        + "if [ -n \"$_AI_VOCAB\" ]; then\n"
        "    echo \"code-forge: AI vocabulary detected in staged diff:\" >&2\n"
        "    printf '%%s\\n' \"$_AI_VOCAB\" >&2\n"
        "    exit 1\n"
        "fi\n"
        "\n"
    )


def _build_presubmit_block(entries: list[dict]) -> str:
    """Build the presubmit runner block for each configured linter entry.

    Each entry dict must have:
        command: list[str]       -- linter command (already validated)
        applies_to: str          -- glob (for display only)
        on: str                  -- "diff" or "patch"
        applies_to_grep: str     -- POSIX ERE for grep -E (precomputed by schema validator)
        when_exists: str         -- optional activation path

    Args:
        entries: list of validated presubmit entry dicts

    Returns:
        Shell script fragment (empty string if entries is empty)

    Raises:
        ValueError: if an entry has an unexpected 'on' value (defensive;
                    schema validation rejects on=message before this point)
    """
    if not entries:
        return ""

    lines = []
    for entry in entries:
        on_value = entry.get("on", "")
        if on_value not in ("diff", "patch"):
            raise ValueError(
                "presubmit entry 'on' must be 'diff' or 'patch', got: %r"
                % on_value
            )

        cmd = entry["command"]
        # shlex.quote each element for safe shell embedding (F3 constraint)
        cmd_str = " ".join(shlex.quote(c) for c in cmd)
        cmd0_quoted = shlex.quote(cmd[0])
        grep_pattern = entry["applies_to_grep"]
        when_exists = entry.get("when_exists")

        indent = ""
        if when_exists is not None:
            lines.append('if [ -e "%s" ]; then' % when_exists)
            indent = "    "

        # Binary existence check: handles PATH executables (command -v)
        # AND relative/absolute paths like scripts/my-linter.sh ([ -x ]).
        lines.append(
            "%scommand -v %s >/dev/null 2>&1 || [ -x %s ] || {"
            % (indent, cmd0_quoted, cmd0_quoted)
        )
        lines.append(
            '%s    echo "code-forge: presubmit FAILED: %s not found" >&2; exit 1; }'
            % (indent, cmd[0])
        )

        # Filter staged files matching applies_to_grep
        lines.append(
            "%s_MATCH=$(printf '%%s\\n' \"$STAGED\" | grep -E '%s')"
            % (indent, grep_pattern)
        )
        lines.append("%sif [ -n \"$_MATCH\" ]; then" % indent)

        # Run command: pipe git diff --cached -- $_MATCH to the linter
        lines.append(
            "%s    git diff --cached -- $_MATCH | %s || {"
            % (indent, cmd_str)
        )
        lines.append(
            '%s        echo "code-forge: presubmit FAILED: %s returned non-zero" >&2;'
            " exit 1; }" % (indent, cmd[0])
        )
        lines.append("%sfi" % indent)

        if when_exists is not None:
            lines.append("fi")

        lines.append("")

    return "\n".join(lines)


def generate_hook_content(
    forge_invocation: str,
    chain_path: Path | None,
    presubmit_entries: list[dict] | None = None,
    non_ascii_mode: str = "ai-smell",
) -> str:
    """Generate pre-commit hook shell script content.

    Hook execution order:
      1. carveout block  -- non-code commits exit 0 here
      2. attestation     -- code-forge verify --quiet
      3. built-in staged-diff check -- non-ASCII + AI-vocab on staged diff
      4. presubmit runner -- user-configured linters (fail-closed)
      5. chain call      -- existing hook (if chaining)
      6. exec gate-check -- R1 test gate

    Args:
        forge_invocation: absolute code-forge path + args
        chain_path: Path to existing hook backup, or None
        presubmit_entries: validated presubmit entry dicts from gate.yaml, or None
        non_ascii_mode: "ai-smell" (default) or "strict"

    Returns:
        Shell script content as string

    Raises:
        ValueError: if any presubmit entry has an unexpected 'on' value
    """
    carveout_block = (
        '# non-code carve-out: skip verify+gate-check for non-code commits\n'
        "NON_CODE="
        r"'\.md$|\.txt$|\.yaml$|\.yml$|\.json$|\.toml$|\.cfg$|\.ini$"
        r"|\.conf$|(^|/)\.gitignore$|(^|/)\.editorconfig$|(^|/)\.env\.example$"
        r"|(^|/)LICENSE$|(^|/)README$|(^|/)CHANGELOG$"
        r"|(^|/)Makefile$|(^|/)Dockerfile$|(^|/)\.dockerignore$'"
        "\n"
        'STAGED=$(git diff --cached --name-only)\n'
        'if [ -z "$STAGED" ]; then exit 0; fi\n'
        'NON_MATCH=$(printf \'%s\\n\' "$STAGED" | grep -vE "$NON_CODE")\n'
        'if [ -z "$NON_MATCH" ]; then\n'
        '    echo "code-forge: skipping verify (non-code commit)" >&2\n'
        '    exit 0\n'
        'fi\n'
        '\n'
    )
    attestation_block = (
        "# code-forge receipt attestation check\n"
        "code-forge verify --quiet 2>/dev/null || {\n"
        '    echo "code-forge: receipt verification failed.'
        ' Run: code-forge verify" >&2\n'
        "    exit 1\n"
        "}\n"
        "\n"
    )
    d12_block = _build_d12_precommit_block(non_ascii_mode)

    effective_entries = presubmit_entries if presubmit_entries else []
    presubmit_block = _build_presubmit_block(effective_entries)

    if chain_path is not None:
        return (
            "#!/bin/sh\n"
            "# code-forge pre-commit gate-check"
            " (installed by code-forge install-hooks)\n"
            "# Chained existing hook: %s\n" % chain_path
            + carveout_block
            + attestation_block
            + d12_block
            + presubmit_block
            + '"%s" "$@" || exit 1\n' % chain_path
            + "exec %s\n" % forge_invocation
        )
    else:
        return (
            "#!/bin/sh\n"
            "# code-forge pre-commit gate-check"
            " (installed by code-forge install-hooks)\n"
            + carveout_block
            + attestation_block
            + d12_block
            + presubmit_block
            + "exec %s\n" % forge_invocation
        )


def generate_commit_msg_hook_content(
    chain_path: Path | None,
    non_ascii_mode: str = "ai-smell",
) -> str:
    """Generate commit-msg hook shell script content.

    Checks commit message for non-ASCII characters and AI vocabulary.
    Runs for ALL commits (no non-code carveout: commit messages must
    always be clean).

    The message file path is received as $1 (git commit-msg hook contract).

    Args:
        chain_path: Path to existing commit-msg backup hook, or None
        non_ascii_mode: "ai-smell" (default) or "strict"

    Returns:
        Shell script content as string
    """
    pattern = _build_non_ascii_pattern(non_ascii_mode)
    chain_call = ""
    if chain_path is not None:
        chain_call = '"%s" "$1" || exit 1\n' % chain_path

    return (
        "#!/bin/sh\n"
        "# code-forge commit-msg non-ASCII + AI-vocab check (installed by code-forge install-hooks)\n"
        "_MSG_FILE=\"$1\"\n"
        "_NON_ASCII=$(grep -P '%s' \"$_MSG_FILE\" | head -5)\n" % pattern
        + "if [ -n \"$_NON_ASCII\" ]; then\n"
        "    echo \"code-forge: non-ASCII in commit message:\" >&2\n"
        "    printf '%%s\\n' \"$_NON_ASCII\" >&2\n"
        "    exit 1\n"
        "fi\n"
        "_AI_VOCAB=$(grep -iE '%s' \\\n" % _AI_VOCAB_PATTERN
        + "    \"$_MSG_FILE\" | head -5)\n"
        "if [ -n \"$_AI_VOCAB\" ]; then\n"
        "    echo \"code-forge: AI vocabulary in commit message:\" >&2\n"
        "    printf '%%s\\n' \"$_AI_VOCAB\" >&2\n"
        "    exit 1\n"
        "fi\n"
        + chain_call
        + "exit 0\n"
    )


def run_install_hooks(
    args=None,
    env: Optional[Mapping[str, str]] = None,
    cwd: Optional[Path] = None,
    stdout: Optional[IO] = None,
    stderr: Optional[IO] = None,
) -> int:
    """Main install-hooks entry point.

    Installs BOTH pre-commit and commit-msg hooks.

    Args:
        args: parsed argparse Namespace; reads args.quiet if present
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

    quiet = getattr(args, "quiet", False)

    def info(msg):
        if not quiet:
            print(msg, file=stderr)

    try:
        # Step a: check core.hooksPath override
        hooks_path_override = check_hooks_path_override(cwd)
        if hooks_path_override is not None:
            print(
                "code-forge: error: core.hooksPath is set to '%s'. "
                "code-forge install-hooks cannot install to a custom hooks path. "
                "Add 'exec /path/to/code-forge gate-check' to your pre-commit hook manually."
                % hooks_path_override,
                file=stderr,
            )
            return EXIT_FAIL

        # Step b: resolve hooks directory
        hooks_dir = resolve_hooks_dir(cwd)

        # Step c: check for .pre-commit-config.yaml
        pre_commit_config = cwd / ".pre-commit-config.yaml"
        if pre_commit_config.exists():
            info(
                "code-forge: warning: pre-commit framework detected. "
                "code-forge hook will chain after existing hooks."
            )

        # Step d: resolve code-forge absolute path
        forge_invocation = resolve_forge_path()

        # Step d.5: load gate.yaml for presubmit config and non_ascii mode
        presubmit_entries = None
        non_ascii_mode = "ai-smell"
        gate_yaml_path = cwd / ".code-forge" / "gate.yaml"
        try:
            gate_config = load_gate_config(gate_yaml_path)
            non_ascii_mode = gate_config.get("non_ascii", "ai-smell")
            if "presubmit" in gate_config:
                presubmit_entries = gate_config["presubmit"]
        except FileNotFoundError:
            # No gate.yaml is valid: no presubmit entries, default non_ascii mode
            pass
        except ValueError as e:
            # Malformed presubmit config: fail-closed.
            # Silently proceeding would generate a hook that omits configured
            # linters, which is worse than blocking install.
            print(
                "code-forge: error: gate.yaml presubmit config invalid: %s" % e,
                file=stderr,
            )
            return EXIT_FAIL

        # Step e: check for existing pre-commit hook
        hook_path = hooks_dir / "pre-commit"
        backup_path = hooks_dir / "pre-commit.code-forge-backup"
        chain_path = None

        if hook_path.exists():
            # Read first 3 lines to check if it's a code-forge-generated hook
            try:
                with open(hook_path, "r", encoding="utf-8") as f:
                    first_lines = [f.readline() for _ in range(3)]
                hook_header = "".join(first_lines)
                is_forge_hook = (
                    "code-forge gate-check" in hook_header
                    or "installed by code-forge install-hooks" in hook_header
                )
            except (OSError, UnicodeDecodeError):
                is_forge_hook = False

            if is_forge_hook:
                # Idempotent re-install: skip backup, overwrite
                info(
                    "code-forge: re-installing hook (existing is code-forge-generated)"
                )
            else:
                # Backup existing non-code-forge hook
                if backup_path.exists():
                    print(
                        "code-forge: error: pre-commit.code-forge-backup already "
                        "exists at %s and a non-code-forge hook is at %s. "
                        "Remove one of them manually, then re-run "
                        "code-forge install-hooks."
                        % (backup_path, hook_path),
                        file=stderr,
                    )
                    return EXIT_FAIL
                else:
                    # Move existing hook to backup
                    shutil.move(str(hook_path), str(backup_path))
                    info(
                        "code-forge: existing hook backed up to %s"
                        % backup_path
                    )
                chain_path = backup_path

        # Step f: generate pre-commit hook content
        hook_content = generate_hook_content(
            forge_invocation, chain_path,
            presubmit_entries=presubmit_entries,
            non_ascii_mode=non_ascii_mode,
        )

        # Step g: write pre-commit hook file
        hooks_dir.mkdir(parents=True, exist_ok=True)
        with open(hook_path, "w", encoding="utf-8") as f:
            f.write(hook_content)

        # Step h: chmod 0o755
        os.chmod(hook_path, 0o755)

        # Step i: success message for pre-commit
        info("code-forge: pre-commit hook installed at %s" % hook_path)

        # Step g.5: install commit-msg hook
        commit_msg_path = hooks_dir / "commit-msg"
        commit_msg_backup = hooks_dir / "commit-msg.code-forge-backup"
        commit_msg_chain = None

        if commit_msg_path.exists():
            try:
                with open(commit_msg_path, "r", encoding="utf-8") as f:
                    first_lines = [f.readline() for _ in range(3)]
                cm_header = "".join(first_lines)
                is_forge_cm = "code-forge commit-msg" in cm_header
            except (OSError, UnicodeDecodeError):
                is_forge_cm = False

            if is_forge_cm:
                info(
                    "code-forge: re-installing commit-msg hook"
                    " (existing is code-forge-generated)"
                )
            else:
                if commit_msg_backup.exists():
                    print(
                        "code-forge: error: commit-msg.code-forge-backup already "
                        "exists at %s and a non-forge commit-msg hook is at %s. "
                        "Remove one manually, then re-run code-forge install-hooks."
                        % (commit_msg_backup, commit_msg_path),
                        file=stderr,
                    )
                    return EXIT_FAIL
                shutil.move(str(commit_msg_path), str(commit_msg_backup))
                info(
                    "code-forge: existing commit-msg hook backed up to %s"
                    % commit_msg_backup
                )
                commit_msg_chain = commit_msg_backup

        commit_msg_content = generate_commit_msg_hook_content(
            commit_msg_chain, non_ascii_mode=non_ascii_mode
        )
        with open(commit_msg_path, "w", encoding="utf-8") as f:
            f.write(commit_msg_content)
        os.chmod(commit_msg_path, 0o755)
        info("code-forge: commit-msg hook installed at %s" % commit_msg_path)

        return EXIT_PASS

    except Exception as e:
        print(
            "code-forge: error: install-hooks failed: %s" % e,
            file=stderr,
        )
        return EXIT_FAIL
