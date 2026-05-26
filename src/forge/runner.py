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
import shutil
import subprocess

from forge.registry import ToolConfig, match_tools

logger = logging.getLogger(__name__)


def _resolve_command(command: str) -> str | None:
    """Resolve a tool command to an executable path.

    First tries shutil.which (PATH-based resolution).  If that fails
    and the command contains os.sep (e.g. "scripts/checkpatch.pl"),
    checks whether the path exists and is executable.

    This addresses DeepSeek's finding: checkpatch.pl is a relative
    path, not on PATH.  shutil.which alone misses it.

    Args:
        command: tool command string from ToolConfig.command

    Returns:
        Resolved path string, or None if not found.
    """
    resolved = shutil.which(command)
    if resolved is not None:
        return resolved

    # Try relative path resolution (e.g. scripts/checkpatch.pl)
    if os.sep in command:
        if os.path.isfile(command) and os.access(command, os.X_OK):
            return command

    return None


def capture_tool_version(command: str) -> str:
    """Capture a tool's version string for GATE-02 reproducibility.

    Runs "<resolved_cmd> --version" and returns the first line of
    stdout.  Called once per tool at pipeline startup, NOT per file.

    Args:
        command: tool command string (will be resolved via PATH)

    Returns:
        Version string (first line of stdout), "not_installed" if
        the command cannot be found, or "unknown" on any error.
    """
    resolved = _resolve_command(command)
    if resolved is None:
        return "not_installed"

    try:
        result = subprocess.run(
            [resolved, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        first_line = result.stdout.strip().split("\n")[0]
        return first_line if first_line else "unknown"
    except (subprocess.TimeoutExpired, OSError):
        return "unknown"


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
            raise RuntimeError(
                "Required tool not found: %s" % tool_config.command
            )
        logger.info(
            "Optional tool '%s' not found, skipping", tool_config.name
        )
        return None

    # Build command: [resolved_cmd] + args + files
    # Exception: cargo_root mode skips file args (clippy operates on crate)
    cmd = [resolved] + tool_config.args
    if tool_config.working_dir != "cargo_root":
        cmd = cmd + files

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=tool_config.timeout,
            check=False,
        )
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
) -> tuple[dict[str, tuple[str, int, str]], dict[str, str], list[str]]:
    """Execute all matching tools from the registry.

    Returns a 3-tuple:
        tool_results: {tool_name: (stdout, returncode, stderr)}
        tool_versions: {tool_name: version_string}
        tools_skipped: [tool_name, ...]

    Iterates sorted(registry.keys()) for GATE-02 determinism
    (Round 3 item 11).  Calls match_tools once before the per-tool
    loop (Mimo F-04).

    Args:
        registry: {name: ToolConfig} from load_registry
        files: list of changed file paths

    Returns:
        (tool_results, tool_versions, tools_skipped)
    """
    tool_results: dict[str, tuple[str, int, str]] = {}
    tool_versions: dict[str, str] = {}
    tools_skipped: list[str] = []

    # Call match_tools once (Mimo F-04)
    matched = match_tools(registry, files)

    for tool_name in sorted(registry.keys()):
        tool_config = registry[tool_name]

        # Capture version (Consensus #3)
        tool_versions[tool_name] = capture_tool_version(tool_config.command)

        # Check for matching files
        matching_files = matched.get(tool_name, [])
        if not matching_files:
            tools_skipped.append(tool_name)
            continue

        result = run_tool(tool_config, matching_files)
        if result is None:
            tools_skipped.append(tool_name)
        else:
            tool_results[tool_name] = result

    return (tool_results, tool_versions, tools_skipped)
