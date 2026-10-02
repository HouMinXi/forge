# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tool execution engine with subprocess orchestration.

Resolves tool binaries (PATH and relative paths), captures tool
versions for GATE-02 reproducibility, runs tools with timeout, and
handles missing/failed tools gracefully.

Security: subprocess.run is ALWAYS called with a list argument,
never a string.  shell=True is never used.  See T-01-07.

Phase 1 scope note (Kimi H2): checkpatch.pl requires stdin input,
not file arguments.  The current runner only supports file-argument
tools.  stdin-input mode is deferred to Phase 2.

Phase 1 scope note (DeepSeek H-2): cargo_root detection (walking
parent directories to find Cargo.toml) is deferred to Phase 2.
When working_dir="cargo_root", the runner skips appending files to
the command but does NOT change the working directory.
"""

import logging
import os
import re
import shutil
import subprocess

from code_forge.registry import ToolConfig, match_tools

logger = logging.getLogger(__name__)


def _resolve_command(command: str) -> str | None:
    """Resolve a tool command to an executable path.

    First tries shutil.which on the binary name (first word of command).
    If that fails and the command contains os.sep (e.g.
    "scripts/checkpatch.pl"), checks whether the path exists and is
    executable.

    This addresses DeepSeek's finding: checkpatch.pl is a relative
    path, not on PATH.  shutil.which alone misses it.

    Args:
        command: tool command string from ToolConfig.command

    Returns:
        Resolved path string, or None if not found.
    """
    # Guard against whitespace-only commands (e.g. "  " from
    # tools.yaml passes load_registry validation but .split()
    # returns []).
    if not command or not command.strip():
        return None

    # Extract binary name (first word) for PATH resolution.
    # "cppcheck -q --output-format=sarif" -> "cppcheck"
    binary = command.split()[0]
    resolved = shutil.which(binary)
    if resolved is not None:
        return resolved

    # Try relative path resolution (e.g. scripts/checkpatch.pl)
    if os.sep in binary:
        if os.path.isfile(binary) and os.access(binary, os.X_OK):
            return binary

    return None


def capture_tool_version(command: str, args: list[str] | None = None) -> str:
    """Capture a tool's version string for GATE-02 reproducibility.

    Runs "<resolved_cmd> --version" and returns the first line of
    stdout.  Called once per tool at pipeline startup, NOT per file.

    Args:
        command: tool command string (will be resolved via PATH)
        args: configured arguments that may complete a module invocation

    Returns:
        Version string (first line of stdout), "not_installed" if
        the command cannot be found, or "unknown" on any error.
    """
    resolved = _resolve_command(command)
    if resolved is None:
        return "not_installed"

    try:
        parts = [resolved] + command.split()[1:] + list(args or [])
        prefix = _ruff_prefix_length(parts)
        version_command = parts[:prefix] + ["--version"] if prefix else [resolved, "--version"]
        result = subprocess.run(
            version_command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
        first_line = result.stdout.strip().split("\n")[0]
        return first_line if first_line else "unknown"
    except (subprocess.TimeoutExpired, OSError):
        return "unknown"


def _ruff_prefix_length(parts: list[str]) -> int:
    """Length of the known executable/module invocation, excluding subcommands."""
    binary = os.path.basename(os.path.realpath(parts[0]))
    if binary in ("ruff", "ruff.exe") or os.path.basename(parts[0]) in ("ruff", "ruff.exe"):
        return 1
    if not re.fullmatch(r"python(?:\d+(?:\.\d+)*)?(?:\.exe)?", binary):
        return 0
    index = 1
    while index < len(parts):
        token = parts[index]
        if token == "--check-hash-based-pycs":
            if parts[index + 1 : index + 2] not in (["always"], ["default"], ["never"]):
                return 0
            index += 2
            continue
        if not token.startswith("-") or token in ("-", "--"):
            return 0
        switches = token[1:]
        for offset, option in enumerate(switches):
            if option in "bBdEIOPqRsStuvx":
                continue
            if option not in "mWX":
                return 0
            # Argument-bearing switches consume the rest of this token or the
            # next argument. Their values are never more interpreter switches.
            value = switches[offset + 1 :]
            if not value:
                index += 1
                if index >= len(parts):
                    return 0
                value = parts[index]
            if option == "m":
                return index + 1 if value in ("ruff", "ruff.__main__") else 0
            break
        index += 1
    return 0


def _ruff_subcommand(parts: list[str], prefix: int) -> str | None:
    """Read the first subcommand without treating global option values as commands."""
    index = prefix
    while index < len(parts):
        token = parts[index]
        if token in ("--config", "--color"):
            index += 2
            continue
        if token.split("=", 1)[0] in ("--config", "--color") or token in (
            "--isolated",
            "--verbose",
            "--quiet",
            "--silent",
            "--help",
            "--version",
            "-v",
            "-q",
            "-s",
            "-h",
            "-V",
        ):
            index += 1
            continue
        return token
    return None


def sarif_producer_profile(command: str, args: list[str] | None = None) -> str | None:
    """Identify known Ruff check invocations independently of registry names.

    Opaque wrappers are not identifiable from their declared command alone.
    A symlink to the Ruff executable and Python's module invocation are known.
    """
    parts = command.split() + list(args or [])
    if not parts:
        return None
    parts[0] = _resolve_command(command) or parts[0]
    prefix = _ruff_prefix_length(parts)
    if not prefix:
        return None
    # File arguments are appended later and cannot choose the producer policy.
    return "ruff" if _ruff_subcommand(parts, prefix) == "check" else None


def run_tool(
    tool_config: ToolConfig,
    files: list[str],
) -> tuple[str, int, str] | None:
    """Execute a single tool via subprocess.

    Returns (stdout, returncode, stderr) 3-tuple on success, or
    None if the tool is missing (optional), timed out, or hit an
    OS error.

    The stderr field is captured and propagated so that downstream
    code can populate ToolError.stderr with the tool's actual error
    output (Round 5 Kimi R5-M3).

    Args:
        tool_config: tool configuration from registry
        files: list of file paths to lint

    Returns:
        (stdout, returncode, stderr) or None

    Raises:
        RuntimeError: if tool is required but not found
    """
    resolved = _resolve_command(tool_config.command)

    if resolved is None:
        if tool_config.required:
            raise RuntimeError("Required tool not found: %s" % tool_config.command)
        logger.info("Optional tool '%s' not found, skipping", tool_config.name)
        return None

    # Build command: [resolved_cmd] + flags + args + files
    # The command string may contain flags (e.g. "cppcheck -q --output-format=sarif").
    # _resolve_command returns the binary path; we need to extract flags from
    # the original command string.
    cmd_parts = tool_config.command.split()
    cmd = [resolved] + cmd_parts[1:] + tool_config.args
    if sarif_producer_profile(tool_config.command, tool_config.args) == "ruff":
        mutating = {"--fix", "--fix-only", "--unsafe-fixes", "--diff", "--add-noqa", "--add-ignore"}
        prefix = _ruff_prefix_length(cmd)
        ruff_args = cmd[prefix:]
        separator = prefix + (ruff_args.index("--") if "--" in ruff_args else len(ruff_args))
        before_separator = cmd[prefix:separator]
        if any(arg.split("=", 1)[0] in mutating for arg in before_separator):
            return ("", 2, "Forge detection refuses Ruff mutating flags")
        for flag in ("--no-fix", "--no-fix-only"):
            if flag not in before_separator:
                cmd.insert(separator, flag)
                separator += 1
    if tool_config.working_dir != "cargo_root":
        cmd = cmd + files

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=tool_config.timeout,
            check=False,
        )
        # Some tools (e.g. cppcheck) emit SARIF on stderr.
        # output_stream="stderr" swaps the streams so the parser
        # receives the SARIF content in the stdout position.
        if tool_config.output_stream == "stderr":
            return (result.stderr, result.returncode, result.stdout)
        return (result.stdout, result.returncode, result.stderr)
    except subprocess.TimeoutExpired:
        logger.warning(
            "Tool '%s' timed out after %ds",
            tool_config.name,
            tool_config.timeout,
        )
        return None
    except OSError as exc:
        logger.warning(
            "Tool '%s' failed with OS error: %s",
            tool_config.name,
            exc,
        )
        return None


def run_tools(
    registry: dict[str, ToolConfig],
    files: list[str],
) -> tuple[dict[str, tuple[str, int, str]], dict[str, str], list[str], list[str]]:
    """Execute all matching tools from the registry.

    Returns a 4-tuple:
        tool_results: {tool_name: (stdout, returncode, stderr)}
        tool_versions: {tool_name: version_string}
        tools_skipped: [tool_name, ...]
        infra_errors: [str, ...] -- timeout/OS errors surfaced for diagnostics

    Iterates sorted(registry.keys()) for GATE-02 determinism
    (Round 3 item 11).  Calls match_tools once before the per-tool
    loop (Mimo F-04).

    Args:
        registry: {name: ToolConfig} from load_registry
        files: list of changed file paths

    Returns:
        (tool_results, tool_versions, tools_skipped, infra_errors)
    """
    tool_results: dict[str, tuple[str, int, str]] = {}
    tool_versions: dict[str, str] = {}
    tools_skipped: list[str] = []
    infra_errors: list[str] = []

    # Call match_tools once (Mimo F-04)
    matched = match_tools(registry, files)

    for tool_name in sorted(registry.keys()):
        tool_config = registry[tool_name]

        # Capture version (Consensus #3)
        tool_versions[tool_name] = capture_tool_version(tool_config.command, tool_config.args)

        # Check for matching files
        matching_files = matched.get(tool_name, [])
        if not matching_files:
            tools_skipped.append(tool_name)
            continue

        result = run_tool(tool_config, matching_files)
        if result is None:
            tools_skipped.append(tool_name)
            infra_errors.append("tool %s: timed out or OS error (see log)" % tool_name)
        else:
            tool_results[tool_name] = result

    return (tool_results, tool_versions, tools_skipped, infra_errors)
