# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""gate-check subcommand: test-based commit gate.

Phase 1 Plan 02: implements R1 commit gate logic - parse gate.yaml,
run tests, translate exit codes, FAIL-OPEN guard, CI detection,
baseline delta, source file filtering.

CRITICAL: run_gate_check returns ONLY 0 or 1, NEVER 2 (EXIT_CLI_ERROR).
If it returned 2, the pre-commit hook's exit-code translation would
treat 2 as "allow+warn", causing FAIL-OPEN on config errors.
"""
from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import IO, Mapping, Optional

import yaml

from .exit_codes import EXIT_FAIL, EXIT_PASS


# Known test runners for command safety validation
KNOWN_RUNNERS = {
    "python3", "python", "pytest",
    "cargo", "go", "make",
    "npm", "npx", "node",
}

# Shell metacharacters that must not appear in command args
SHELL_METACHARACTERS = set("|;&$><`")


def load_gate_config(
    config_path: str | Path,
    fs_open=open,
) -> dict:
    """Load and validate gate.yaml config.

    Args:
        config_path: path to gate.yaml
        fs_open: file open callable (injected for testing)

    Returns:
        dict with validated test config

    Raises:
        FileNotFoundError: if config_path does not exist
        ValueError: if YAML is malformed or required fields missing
    """
    try:
        with fs_open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        raise
    except yaml.YAMLError as e:
        raise ValueError("Invalid YAML in gate.yaml: %s" % e) from e

    if not isinstance(data, dict) or "test" not in data:
        raise ValueError("gate.yaml must have a 'test' section")

    test = data["test"]
    if not isinstance(test, dict):
        raise ValueError("'test' section must be a mapping")

    # Validate required fields
    if "command" not in test:
        raise ValueError("'test.command' is required")
    if not isinstance(test["command"], list):
        raise ValueError("'test.command' must be a list")
    if not test["command"]:
        raise ValueError("'test.command' cannot be empty")

    # Optional fields with defaults
    if "env" in test and not isinstance(test.get("env"), dict):
        raise ValueError("'test.env' must be a mapping if present")

    if "timeout_seconds" in test:
        if not isinstance(test["timeout_seconds"], int):
            raise ValueError("'test.timeout_seconds' must be an integer")
        if test["timeout_seconds"] <= 0:
            raise ValueError("'test.timeout_seconds' must be positive")

    if "cwd" in test and not isinstance(test["cwd"], str):
        raise ValueError("'test.cwd' must be a string if present")

    if "source_patterns" in test:
        if not isinstance(test["source_patterns"], list):
            raise ValueError("'test.source_patterns' must be a list if present")

    return data


def validate_command_safety(command: list[str]) -> None:
    """Validate test command for safety.

    Args:
        command: test command list

    Raises:
        ValueError: if command is unsafe
    """
    if not command:
        raise ValueError("command cannot be empty")
    if not isinstance(command, list):
        raise ValueError("command must be a list")

    # First element must be a known runner
    if command[0] not in KNOWN_RUNNERS:
        raise ValueError(
            "Unknown test runner: %s (expected one of: %s)"
            % (command[0], ", ".join(sorted(KNOWN_RUNNERS)))
        )

    # No element may contain shell metacharacters
    for arg in command:
        if not isinstance(arg, str):
            raise ValueError("command elements must be strings")
        for char in SHELL_METACHARACTERS:
            if char in arg:
                raise ValueError(
                    "Shell metacharacter %r not allowed in command args"
                    % char
                )


def is_ci_mode(env: Mapping[str, str]) -> bool:
    """Detect if running in CI mode.

    Args:
        env: environment variables (os.environ or test fixture)

    Returns:
        True if in CI mode, False otherwise
    """
    # FORGE_MODE=ci (case-insensitive)
    forge_mode = env.get("FORGE_MODE", "").strip().lower()
    if forge_mode == "ci":
        return True

    # Platform CI vars (any non-empty value means CI)
    ci_vars = ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_URL", "BUILD_URL"]
    for var in ci_vars:
        if env.get(var, "").strip():
            return True

    return False


def match_source_patterns(
    staged_files: list[str],
    patterns: list[str],
) -> bool:
    """Check if any staged file matches any pattern.

    Args:
        staged_files: list of file paths from git diff --cached
        patterns: list of glob patterns (e.g. ["*.py", "*.sh"])

    Returns:
        True if any file matches any pattern (run tests),
        False if no matches (skip tests).

    Special cases:
        - Empty staged_files -> False (no source changes, skip tests)
        - Empty patterns list + non-empty files -> True (always run tests)
    """
    if not staged_files:
        return False  # No files staged, skip tests
    if not patterns:
        return True  # No filter, always run

    for file_path in staged_files:
        for pattern in patterns:
            if fnmatch.fnmatch(file_path, pattern):
                return True
    return False


def load_test_baseline(
    baseline_path: str | Path,
    fs_open=open,
) -> dict | None:
    """Load test baseline from JSON.

    Args:
        baseline_path: path to test_baseline.json
        fs_open: file open callable (injected for testing)

    Returns:
        dict with baseline data, or None if file does not exist

    Raises:
        ValueError: if JSON is malformed or missing schema_version
    """
    try:
        with fs_open(baseline_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        raise ValueError("Invalid JSON in baseline: %s" % e) from e

    if not isinstance(data, dict):
        raise ValueError("Baseline must be a JSON object")
    if "schema_version" not in data:
        raise ValueError("Baseline missing 'schema_version' field")

    return data


def compute_baseline_delta(
    test_output: str,
    baseline: dict | None,
) -> tuple[bool, list[str]]:
    """Compute NEW failures vs baseline.

    Args:
        test_output: stdout from pytest -q (or other test runner)
        baseline: loaded baseline dict, or None

    Returns:
        (should_block: bool, new_failure_names: list[str])

    Logic:
        - No baseline -> (False, []) -- bootstrap, allow
        - Test not in baseline + fails -> NEW failure -> BLOCK
        - Test in baseline as "passed" + now fails -> NEW failure -> BLOCK
        - Test in baseline as "failed" + still fails -> known, not new
        - Test not in baseline + passes -> not a failure, allow
    """
    if baseline is None:
        return (False, [])  # No baseline, allow (bootstrap)

    # Parse pytest -q output (simplified: look for FAILED lines)
    # Real implementation would parse pytest's output format
    # For now, stub: extract test names from "FAILED test_name" lines
    new_failures = []
    baseline_results = baseline.get("test_results", {})

    # Simple parser: lines like "FAILED tests/test_foo.py::test_bar"
    for line in test_output.split("\n"):
        if line.startswith("FAILED "):
            test_name = line.split()[1] if len(line.split()) > 1 else ""
            if not test_name:
                continue

            # Check against baseline
            if test_name not in baseline_results:
                # New test that fails -> BLOCK
                new_failures.append(test_name)
            elif baseline_results[test_name] == "passed":
                # Was passing, now fails -> regression -> BLOCK
                new_failures.append(test_name)
            # else: was already failing in baseline -> known, not new

    should_block = len(new_failures) > 0
    return (should_block, new_failures)


def translate_exit_code(test_returncode: int) -> int:
    """Translate test exit code to hook exit code.

    Args:
        test_returncode: exit code from test subprocess

    Returns:
        0 (allow) or 1 (BLOCK) for the pre-commit hook

    Mapping (from SPEC v3.2):
        0 -> 0 (allow)
        1 -> 1 (BLOCK - real test failure)
        2, 3 -> 0 (allow - pytest interrupt/internal error)
        4 -> 1 (BLOCK - usage error, misconfigured command)
        5 -> 1 (BLOCK - no tests collected, toothless gate)
        timeout or >5 -> 1 (BLOCK)
    """
    if test_returncode == 0:
        return 0  # Pass
    if test_returncode == 1:
        return 1  # Real failure
    if test_returncode in (2, 3):
        return 0  # Interrupt/internal error, warn but allow
    if test_returncode in (4, 5):
        return 1  # Usage error / no tests collected
    # timeout or unknown (>5)
    return 1  # Block


def run_gate_check(
    args=None,
    env=None,
    cwd=None,
    stdout: Optional[IO] = None,
    stderr: Optional[IO] = None,
) -> int:
    """Main gate-check entry point.

    Args:
        args: parsed argparse Namespace (unused in Plan 02, for future flags)
        env: environment variables (os.environ if None)
        cwd: working directory (Path.cwd() if None)
        stdout: output stream (sys.stdout if None)
        stderr: error stream (sys.stderr if None)

    Returns:
        EXIT_PASS (0) or EXIT_FAIL (1)

    CRITICAL: NEVER returns EXIT_CLI_ERROR (2). Config/parse errors
    return EXIT_FAIL (1) to enforce FAIL-OPEN guard.
    """
    if env is None:
        env = os.environ
    if cwd is None:
        cwd = Path.cwd()
    if stdout is None:
        stdout = sys.stdout
    if stderr is None:
        stderr = sys.stderr

    # FAIL-OPEN guard: catch config/parse errors -> BLOCK (exit 1)
    try:
        config_path = cwd / ".forge" / "gate.yaml"
        config = load_gate_config(config_path)
        test_config = config["test"]

        # Validate command safety
        validate_command_safety(test_config["command"])

        # Load baseline (OK if None)
        baseline_path = cwd / ".forge" / "test_baseline.json"
        baseline = load_test_baseline(baseline_path)

    except (FileNotFoundError, ValueError) as e:
        print("forge: error: %s" % e, file=stderr)
        return EXIT_FAIL  # BLOCK on config error (FAIL-OPEN guard)

    # Check FORGE_SKIP_TESTS (only in local mode, ignored in CI)
    if env.get("FORGE_SKIP_TESTS") == "1":
        if is_ci_mode(env):
            print(
                "forge: CI mode: FORGE_SKIP_TESTS ignored",
                file=stderr
            )
        else:
            print(
                "forge: FORGE_SKIP_TESTS=1, skipping tests",
                file=stderr
            )
            return EXIT_PASS  # Allow

    # Get staged files via git diff --cached --name-only
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if result.returncode != 0:
            print(
                "forge: error: git diff --cached failed: %s"
                % result.stderr.strip(),
                file=stderr
            )
            return EXIT_FAIL  # BLOCK on git error

        staged_files = [
            line.strip()
            for line in result.stdout.strip().split("\n")
            if line.strip()
        ]
    except subprocess.TimeoutExpired:
        print("forge: error: git diff --cached timed out", file=stderr)
        return EXIT_FAIL

    # Filter on source_patterns
    source_patterns = test_config.get("source_patterns", [])
    if not match_source_patterns(staged_files, source_patterns):
        print(
            "forge: no source files staged, skipping tests",
            file=stderr
        )
        return EXIT_PASS  # Allow

    # Run test command
    command = test_config["command"]
    test_env = {**env, **test_config.get("env", {})}
    timeout = test_config.get("timeout_seconds", 120)
    test_cwd = cwd / test_config.get("cwd", ".")

    try:
        test_result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            env=test_env,
            cwd=str(test_cwd),
            timeout=timeout,
            check=False,
        )
        test_returncode = test_result.returncode
        test_stdout = test_result.stdout
    except subprocess.TimeoutExpired:
        print(
            "forge: error: tests timed out after %d seconds" % timeout,
            file=stderr
        )
        return EXIT_FAIL  # BLOCK on timeout

    # Translate exit code
    translated = translate_exit_code(test_returncode)

    # Special handling for exit 2-3 (warn but allow)
    if test_returncode == 2:
        print(
            "forge: warning: tests exited with code 2 "
            "(keyboard interrupt); allowing commit",
            file=stderr
        )
    elif test_returncode == 3:
        print(
            "forge: warning: tests exited with code 3 "
            "(internal error); allowing commit",
            file=stderr
        )

    # NF-1 fix: baseline delta applies ONLY to test exit 1 (real failure)
    # Exit 4/5/timeout/>5 BLOCK immediately WITHOUT baseline delta check
    if translated == EXIT_FAIL and test_returncode == 1:
        # Real test failure -> check baseline delta
        should_block, new_failures = compute_baseline_delta(
            test_stdout, baseline
        )
        if not should_block:
            if baseline is None:
                print(
                    "forge: warning: no baseline; tests failed but allowing "
                    "commit (run forge gate-check --record-baseline)",
                    file=stderr
                )
            else:
                print(
                    "forge: all failures are known (in baseline); "
                    "allowing commit",
                    file=stderr
                )
            return EXIT_PASS  # Downgrade to allow
        else:
            # NEW failures detected
            print(
                "forge: NEW test failures detected (not in baseline):",
                file=stderr
            )
            for test_name in new_failures:
                print("  - %s" % test_name, file=stderr)
            return EXIT_FAIL  # BLOCK

    return translated
