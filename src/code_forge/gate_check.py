# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""gate-check subcommand: test-based commit gate.

Parses .code-forge/gate.yaml, runs the configured test command, translates
exit codes, and blocks on new failures vs a baseline.

run_gate_check returns ONLY 0 or 1, NEVER 2 (EXIT_CLI_ERROR).
If it returned 2, the pre-commit hook's exit-code translation would
treat 2 as "allow+warn", causing FAIL-OPEN on config errors.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
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

    # Validate optional non_ascii field (top-level)
    if "non_ascii" in data:
        if data["non_ascii"] not in ("ai-smell", "strict"):
            raise ValueError(
                "non_ascii must be 'ai-smell' or 'strict', got: %r"
                % data["non_ascii"]
            )

    # Validate optional presubmit section
    if "presubmit" in data:
        if not isinstance(data["presubmit"], list):
            raise ValueError(
                "gate.yaml 'presubmit' must be a list, got: %s"
                % type(data["presubmit"]).__name__
            )
        for idx, entry in enumerate(data["presubmit"]):
            try:
                validate_presubmit_entry(entry)
            except ValueError as e:
                raise ValueError(
                    "presubmit[%d]: %s" % (idx, e)
                ) from e

    # Validate optional graph_triage section
    if "graph_triage" in data:
        validate_graph_triage(data["graph_triage"])

    # Validate optional daemon_state section
    if "daemon_state" in data:
        validate_daemon_state(data["daemon_state"])

    # Validate optional siblings section (cross-repo review)
    if "siblings" in data:
        validate_siblings(
            data["siblings"],
            gate_yaml_dir=Path(str(config_path)).parent,
        )

    # Validate optional canary section (reviewer laziness check)
    if "canary" in data:
        validate_canary_config(data["canary"])

    return data


def validate_graph_triage(section: dict) -> None:
    """Validate the graph_triage section of gate.yaml.

    Schema:
        enabled:  bool   -- OPTIONAL. Explicit enable/disable.
        db_path:  str    -- OPTIONAL. Path to graph.db override.
        Unknown keys are allowed (forward-compatible).

    Args:
        section: dict from gate.yaml graph_triage key.

    Raises:
        ValueError: if known fields have wrong types.
    """
    if not isinstance(section, dict):
        raise ValueError(
            "gate.yaml 'graph_triage' must be a mapping, got: %s"
            % type(section).__name__
        )
    if "enabled" in section:
        if not isinstance(section["enabled"], bool):
            raise ValueError(
                "gate.yaml 'graph_triage.enabled' must be a bool, got: %r"
                % section["enabled"]
            )
    if "db_path" in section:
        if not isinstance(section["db_path"], str):
            raise ValueError(
                "gate.yaml 'graph_triage.db_path' must be a string, got: %r"
                % section["db_path"]
            )


def validate_daemon_state(section: object) -> None:
    """Validate the daemon_state section of gate.yaml.

    Schema:
        enabled:         bool         -- OPTIONAL. Explicit enable/disable.
        subsystems:      list[str]    -- OPTIONAL. State domains to focus on.
        patterns:        list[str]    -- OPTIONAL. Extra keyword patterns.
        conflicts:       list[dict]   -- OPTIONAL. Static conflict triplets.
            Each triplet requires: subsystem (str), mutates (str),
            interferes_with (str).
        conflicts_file:  str          -- OPTIONAL. Path to external conflicts YAML.

    Args:
        section: value from gate.yaml daemon_state key.

    Raises:
        ValueError: if known fields have wrong types or required sub-fields missing.
    """
    if not isinstance(section, dict):
        raise ValueError(
            "gate.yaml 'daemon_state' must be a mapping, got: %s"
            % type(section).__name__
        )
    if "enabled" in section:
        if not isinstance(section["enabled"], bool):
            raise ValueError(
                "gate.yaml 'daemon_state.enabled' must be a bool, got: %r"
                % section["enabled"]
            )
    if "subsystems" in section:
        if not isinstance(section["subsystems"], list):
            raise ValueError(
                "gate.yaml 'daemon_state.subsystems' must be a list, got: %s"
                % type(section["subsystems"]).__name__
            )
        for s in section["subsystems"]:
            if not isinstance(s, str):
                raise ValueError(
                    "'daemon_state.subsystems' elements must be strings"
                )
    if "patterns" in section:
        if not isinstance(section["patterns"], list):
            raise ValueError(
                "gate.yaml 'daemon_state.patterns' must be a list, got: %s"
                % type(section["patterns"]).__name__
            )
    if "conflicts" in section:
        if not isinstance(section["conflicts"], list):
            raise ValueError(
                "gate.yaml 'daemon_state.conflicts' must be a list, got: %s"
                % type(section["conflicts"]).__name__
            )
        for idx, c in enumerate(section["conflicts"]):
            if not isinstance(c, dict):
                raise ValueError(
                    "daemon_state.conflicts[%d] must be a mapping" % idx
                )
            for req_key in ("subsystem", "mutates", "interferes_with"):
                if req_key not in c:
                    raise ValueError(
                        "daemon_state.conflicts[%d] missing required '%s'"
                        % (idx, req_key)
                    )
                if not isinstance(c[req_key], str):
                    raise ValueError(
                        "daemon_state.conflicts[%d].%s must be a string"
                        % (idx, req_key)
                    )
    if "conflicts_file" in section:
        if not isinstance(section["conflicts_file"], str):
            raise ValueError(
                "gate.yaml 'daemon_state.conflicts_file' must be a string, "
                "got: %r" % section["conflicts_file"]
            )


def validate_canary_config(section: object) -> None:
    """Validate the canary section of gate.yaml.

    Schema:
        enabled:          bool          -- OPTIONAL. Activate the canary check.
        n:                int (3..5)    -- OPTIONAL. Number of canary mutations.
        threshold_ratio:  float >0..1.0 -- OPTIONAL. Minimum catch ratio.
        Unknown keys are allowed (forward-compatible).

    Args:
        section: value from gate.yaml canary key.

    Raises:
        ValueError: if known fields have wrong types or values out of range.
    """
    if not isinstance(section, dict):
        raise ValueError(
            "gate.yaml 'canary' must be a mapping, got: %s"
            % type(section).__name__
        )
    if "enabled" in section:
        if not isinstance(section["enabled"], bool):
            raise ValueError(
                "gate.yaml 'canary.enabled' must be a bool, got: %r"
                % section["enabled"]
            )
    if "n" in section:
        if not isinstance(section["n"], int) or isinstance(section["n"], bool):
            raise ValueError(
                "gate.yaml 'canary.n' must be an int, got: %r"
                % section["n"]
            )
        if section["n"] < 3 or section["n"] > 5:
            raise ValueError(
                "gate.yaml 'canary.n' must be in range 3..5, got: %d"
                % section["n"]
            )
    if "threshold_ratio" in section:
        val = section["threshold_ratio"]
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            raise ValueError(
                "gate.yaml 'canary.threshold_ratio' must be a number, "
                "got: %r" % val
            )
        if val <= 0.0 or val > 1.0:
            raise ValueError(
                "gate.yaml 'canary.threshold_ratio' must be > 0.0 and "
                "<= 1.0 (must be > 0.0..1.0), got: %s" % val
            )


_REF_COMPONENT_RE = re.compile(r"[A-Za-z0-9._/@-]+\Z")


def _validate_ref_part(part_name: str, part_val: str, context: str) -> None:
    """Validate a single git ref component for character safety.

    Rejects leading dash (option injection), leading dot (ambiguous ref),
    and any character outside the git-safe set [A-Za-z0-9._/@-].

    Args:
        part_name: "baseline" or "head" (for error messages).
        part_val: the ref string to validate.
        context: error message prefix (e.g. "siblings[0]:" or "ref_spec").

    Raises:
        ValueError: on invalid characters or leading dash/dot.
    """
    if part_val.startswith("-") or part_val.startswith("."):
        raise ValueError(
            "%s ref %s must not start with '-' or '.'" % (context, part_name)
        )
    if not _REF_COMPONENT_RE.match(part_val):
        raise ValueError(
            "%s ref %s contains invalid characters" % (context, part_name)
        )


def validate_siblings(
    siblings: object,
    gate_yaml_dir: Path,
    primary_language: str | None = None,
) -> None:
    """Validate the siblings section of gate.yaml.

    Checks structural integrity (types, required fields), ref format,
    remote URL rejection (v1 local-only), path traversal via symlink
    guard, label character constraints, reserved-label rejection, and
    label uniqueness after defaulting.

    Args:
        siblings: value from gate.yaml siblings key (expected list).
        gate_yaml_dir: directory containing gate.yaml (used to resolve
            relative repo paths; gate_root = gate_yaml_dir.parent).
        primary_language: if provided, reject siblings whose detected
            language differs (same-stack constraint). Pass None to skip.

    Raises:
        ValueError: on any validation failure.
    """
    if not isinstance(siblings, list):
        raise ValueError("siblings: must be a list")

    from .conventions_resolver import _symlink_guard_passes

    gate_root = gate_yaml_dir.parent
    seen_labels: set[str] = set()

    for idx, entry in enumerate(siblings):
        if not isinstance(entry, dict):
            raise ValueError("siblings[%d]: must be a mapping" % idx)

        # Required fields (must be non-empty strings)
        repo_val = entry.get("repo")
        if not isinstance(repo_val, str) or not repo_val:
            raise ValueError("siblings[%d]: 'repo' is required" % idx)
        ref_val = entry.get("ref")
        if not isinstance(ref_val, str) or not ref_val:
            raise ValueError("siblings[%d]: 'ref' is required" % idx)

        ref = ref_val
        # ref must contain exactly one ".." separator (not "...")
        if ".." not in ref or "..." in ref:
            raise ValueError(
                "siblings[%d]: ref must be 'baseline..head', got %r"
                % (idx, ref)
            )
        base_part, head_part = ref.split("..", 1)
        if not base_part or not head_part:
            raise ValueError(
                "siblings[%d]: ref must be 'baseline..head', got %r"
                % (idx, ref)
            )
        _validate_ref_part("baseline", base_part, "siblings[%d]:" % idx)
        _validate_ref_part("head", head_part, "siblings[%d]:" % idx)

        repo_str = repo_val

        # Reject remote URLs (v1 supports local paths only)
        if repo_str.startswith("https://") or repo_str.startswith("git@"):
            raise ValueError(
                "siblings[%d]: remote URLs not supported in v1; "
                "use a local path" % idx
            )

        # Symlink guard: resolve relative to project root
        raw_path = Path(repo_str)
        if raw_path.is_absolute():
            resolved = raw_path.resolve()
        else:
            resolved = (gate_root / repo_str).resolve()
        if not _symlink_guard_passes(resolved, gate_root):
            raise ValueError(
                "siblings[%d]: repo path traverses outside project" % idx
            )

        # Label: explicit or defaulted from repo basename
        label = entry.get("label") or os.path.basename(
            repo_str.rstrip("/")
        )

        # Label character validation
        if not re.fullmatch(r"[A-Za-z0-9_-]+", label):
            raise ValueError(
                "siblings[%d]: label must be alphanumeric/hyphen/"
                "underscore only" % idx
            )

        # Reserved label
        if label == "primary":
            raise ValueError(
                "siblings: label 'primary' is reserved"
            )

        # Uniqueness
        if label in seen_labels:
            raise ValueError(
                "siblings: duplicate label '%s'" % label
            )
        seen_labels.add(label)

        # Same-stack language check (skipped when primary_language is None)
        if primary_language is not None:
            from .detect import detect_toolchain

            result = detect_toolchain(resolved)
            if result.language != primary_language:
                raise ValueError(
                    "siblings[%d]: sibling detected as '%s' but "
                    "primary is '%s'; same-stack only for v1"
                    % (idx, result.language, primary_language)
                )


def fnmatch_to_grep(glob: str) -> str:
    """Convert a shell glob pattern to a POSIX ERE pattern for grep -E.

    Uses a custom converter -- does NOT use fnmatch.translate() which
    produces Python-only constructs incompatible with grep -E.

    Conversion rules:
        *  -> .*
        ?  -> .
        .  -> \\.
        all other chars pass through unchanged

    The result is anchored: ^<converted>$

    Args:
        glob: shell glob pattern (e.g. "*.py", "test_*.py")

    Returns:
        POSIX ERE string suitable for grep -E (e.g. "^.*\\.py$")
    """
    result = []
    for ch in glob:
        if ch == "*":
            result.append(".*")
        elif ch == "?":
            result.append(".")
        elif ch == ".":
            result.append("\\.")
        else:
            result.append(ch)
    return "^" + "".join(result) + "$"


def validate_presubmit_command(command: list[str]) -> None:
    """Validate a presubmit linter command for shell injection safety.

    Checks each element for SHELL_METACHARACTERS. Does NOT check
    KNOWN_RUNNERS -- presubmit commands are user-supplied external
    tools and must not be restricted to the test-runner allowlist.

    Args:
        command: presubmit command list

    Raises:
        ValueError: if any element contains a shell metacharacter
    """
    if not isinstance(command, list):
        raise ValueError("presubmit command must be a list")
    if not command:
        raise ValueError("presubmit command cannot be empty")
    for arg in command:
        if not isinstance(arg, str):
            raise ValueError("presubmit command elements must be strings")
        for char in SHELL_METACHARACTERS:
            if char in arg:
                raise ValueError(
                    "Shell metacharacter %r not allowed in presubmit command"
                    % char
                )
        if "%" in arg:
            raise ValueError(
                "Percent sign not allowed in presubmit command elements"
                " (breaks hook-generation string formatting)"
            )


def validate_presubmit_entry(entry: dict) -> None:
    """Validate a single presubmit entry from gate.yaml.

    Schema:
        command:      list[str] -- REQUIRED. Linter command.
                      Each element checked against SHELL_METACHARACTERS.
        applies_to:   str       -- REQUIRED. Glob pattern.
                      Must NOT contain single-quote or double-quote.
        on:           str       -- REQUIRED. One of "diff" or "patch".
                      "message" is rejected (not yet implemented).
        when_exists:  str       -- OPTIONAL. Activation path.
                      Must NOT contain single-quote or double-quote.

    Stores "applies_to_grep" in entry (POSIX ERE form of applies_to)
    for direct shell use.

    Args:
        entry: dict representing one presubmit list element

    Raises:
        ValueError: if required fields are missing or values are invalid
    """
    if not isinstance(entry, dict):
        raise ValueError("each presubmit entry must be a mapping")

    # command: required, list, no shell metacharacters
    if "command" not in entry:
        raise ValueError(
            "presubmit entry missing required field 'command'"
        )
    if not isinstance(entry["command"], list):
        raise ValueError(
            "presubmit entry 'command' must be a list, got: %s"
            % type(entry["command"]).__name__
        )
    validate_presubmit_command(entry["command"])

    # applies_to: required, string, no quotes (breaks generated shell quoting)
    if "applies_to" not in entry:
        raise ValueError(
            "presubmit entry missing required field 'applies_to'"
        )
    if not isinstance(entry["applies_to"], str):
        raise ValueError(
            "presubmit entry 'applies_to' must be a string"
        )
    if "'" in entry["applies_to"]:
        raise ValueError(
            "presubmit entry 'applies_to' must not contain single-quote"
        )
    if '"' in entry["applies_to"]:
        raise ValueError(
            "presubmit entry 'applies_to' must not contain double-quote"
        )
    if "%" in entry["applies_to"]:
        raise ValueError(
            "presubmit entry 'applies_to' must not contain percent sign"
            " (breaks hook-generation string formatting)"
        )
    # Store the grep-compatible ERE for direct shell use
    entry["applies_to_grep"] = fnmatch_to_grep(entry["applies_to"])

    # on: required, "diff" or "patch" only.
    # YAML 1.1 coerces bare `on:` to boolean True; normalize it to the string
    # "on" so users can write `on: diff` without quoting the key.
    on_key = "on" if "on" in entry else (True if True in entry else None)
    if on_key is None:
        raise ValueError(
            "presubmit entry missing required field 'on'"
        )
    on_value = entry[on_key]
    # Normalize: store as string key for callers
    if on_key is True:
        entry["on"] = on_value
        del entry[True]
    if on_value == "message":
        raise ValueError(
            "presubmit entry 'on: message' not yet supported -- would "
            "silently never execute; use diff or patch"
        )
    if on_value not in ("diff", "patch"):
        raise ValueError(
            "presubmit entry 'on' must be 'diff' or 'patch', got: %r"
            % on_value
        )

    # when_exists: optional, string, no quotes
    if "when_exists" in entry:
        we = entry["when_exists"]
        if not isinstance(we, str):
            raise ValueError(
                "presubmit entry 'when_exists' must be a string"
            )
        if "'" in we:
            raise ValueError(
                "presubmit entry 'when_exists' must not contain single-quote"
            )
        if '"' in we:
            raise ValueError(
                "presubmit entry 'when_exists' must not contain double-quote"
            )
        if "%" in we:
            raise ValueError(
                "presubmit entry 'when_exists' must not contain percent sign"
                " (breaks hook-generation string formatting)"
            )


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

    Mapping:
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
        args: parsed argparse Namespace; reads args.quiet if present
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

    quiet = getattr(args, "quiet", False)

    def warn(msg):
        if not quiet:
            print(msg, file=stderr)

    # FAIL-OPEN guard: catch config/parse errors -> BLOCK (exit 1)
    try:
        config_path = cwd / ".code-forge" / "gate.yaml"
        config = load_gate_config(config_path)
        test_config = config["test"]

        # Validate command safety
        validate_command_safety(test_config["command"])

        # Load baseline (OK if None)
        baseline_path = cwd / ".code-forge" / "test_baseline.json"
        baseline = load_test_baseline(baseline_path)

    except (FileNotFoundError, ValueError) as e:
        print("forge: error: %s" % e, file=stderr)
        return EXIT_FAIL  # BLOCK on config error (FAIL-OPEN guard)

    # Check FORGE_SKIP_TESTS (only in local mode, ignored in CI)
    if env.get("FORGE_SKIP_TESTS") == "1":
        if is_ci_mode(env):
            warn("forge: CI mode: FORGE_SKIP_TESTS ignored")
        else:
            warn("forge: FORGE_SKIP_TESTS=1, skipping tests")
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
    except FileNotFoundError:
        print("forge: error: git not found on PATH", file=stderr)
        return EXIT_FAIL

    # Filter on source_patterns
    source_patterns = test_config.get("source_patterns", [])
    if not match_source_patterns(staged_files, source_patterns):
        warn("forge: no source files staged, skipping tests")
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
    except FileNotFoundError:
        print(
            "forge: error: test runner not found: %s" % command[0],
            file=stderr
        )
        return EXIT_FAIL

    # Translate exit code
    translated = translate_exit_code(test_returncode)

    # Special handling for exit 2-3 (warn but allow)
    if test_returncode == 2:
        warn(
            "forge: warning: tests exited with code 2 "
            "(keyboard interrupt); allowing commit"
        )
    elif test_returncode == 3:
        warn(
            "forge: warning: tests exited with code 3 "
            "(internal error); allowing commit"
        )

    # Baseline delta applies ONLY to real test failures (exit 1).
    # Exit 4 (usage error), exit 5 (no tests collected), and timeout BLOCK
    # directly -- vacuous delta would otherwise downgrade them to PASS.
    if translated == EXIT_FAIL and test_returncode == 1:
        # Real test failure -> check baseline delta
        should_block, new_failures = compute_baseline_delta(
            test_stdout, baseline
        )
        if not should_block:
            if baseline is None:
                warn(
                    "forge: warning: no baseline; tests failed but allowing commit"
                )
            else:
                warn(
                    "forge: all failures are known (in baseline); "
                    "allowing commit"
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
