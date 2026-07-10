# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Toolchain auto-detection for Python and shell projects.

Detects project toolchain from pyproject.toml [tool.*] sections,
flake8 config files (.flake8, setup.cfg, tox.ini), shell indicators
(*.sh files), and PATH-based fallback heuristics. Generates
.code-forge/tools.yaml for L0 linting.
"""

from __future__ import annotations

import configparser
import copy
import logging
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import yaml

from code_forge.errors import CliError
from code_forge.registry import load_registry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DetectionResult:
    """Result of toolchain detection.

    detected: tools declared in project config AND available on PATH.
    missing: tools declared in project config but NOT on PATH.
    language: detected project language (e.g. "python", "shell").
    """

    detected: list[str]
    missing: list[str]
    language: str


# Tool registry: tool_name -> detection metadata + optional tools.yaml entry.
# tools_yaml_entry is only present for linters with a parser in
# PARSER_DISPATCH. Tools without it (pytest, mypy) are detected but
# not written to tools.yaml.
#
# CRITICAL: command values are str, not list.
# ToolConfig.command is type str (registry.py:38).
#
# flake8 has toml_section=None because flake8 does not read
# pyproject.toml [tool.flake8] -- it uses its own config files.
# The [tool.*] walk SKIPS entries with toml_section=None.
PYTHON_TOOL_REGISTRY: dict[str, dict] = {
    "ruff": {
        "toml_section": "tool.ruff",
        "binary": "ruff",
        "tools_yaml_entry": {
            # output_format MUST be "sarif" (the real PARSER_DISPATCH
            # key via _parse_sarif). "ruff_json" is NOT a dispatch key.
            "command": "ruff check --output-format=sarif",
            "output_format": "sarif",
            "file_patterns": ["*.py"],
        },
    },
    "pylint": {
        "toml_section": "tool.pylint",
        "binary": "pylint",
        "tools_yaml_entry": {
            # "pylint_json" dispatch key (parse_pylint).
            "command": "pylint --output-format=json",
            "output_format": "pylint_json",
            "file_patterns": ["*.py"],
        },
    },
    "pytest": {
        "toml_section": "tool.pytest.ini_options",
        "binary": "pytest",
        # No tools_yaml_entry: gate runner only, not a linter.
    },
    "mypy": {
        "toml_section": "tool.mypy",
        "binary": "mypy",
        # No tools_yaml_entry: type checker, no parser in
        # PARSER_DISPATCH. See Known Limitations in plan objective.
    },
    "flake8": {
        "toml_section": None,  # flake8 has no pyproject.toml support
        "binary": "flake8",
        "config_files": [".flake8"],
        "tools_yaml_entry": {
            # flake8 default text output, parsed by parse_flake8.
            "command": "flake8",
            "output_format": "flake8",
            "file_patterns": ["*.py"],
        },
    },
}

# Shell tool registry: tool_name -> detection metadata + tools.yaml entry.
# Shell projects do not use pyproject.toml, so no toml_section field.
# Detection is driven by file extension presence (*.sh, *.bash).
# output_format "shellcheck_json" matches the PARSER_DISPATCH key.
SHELL_TOOL_REGISTRY: dict[str, dict] = {
    "shellcheck": {
        "binary": "shellcheck",
        "tools_yaml_entry": {
            "command": "shellcheck -f json",
            "output_format": "shellcheck_json",
            "file_patterns": ["*.sh", "*.bash"],
        },
    },
}

# Go tool registry: golangci-lint with SARIF output.
# Detection is driven by go.mod or *.go file presence.
GO_TOOL_REGISTRY: dict[str, dict] = {
    "golangci-lint": {
        "binary": "golangci-lint",
        "tools_yaml_entry": {
            "command": "golangci-lint run --output.sarif.path=stdout",
            "output_format": "sarif",
            "file_patterns": ["*.go"],
        },
    },
}


def _get_tool_meta(tool_name: str) -> Optional[dict]:
    """Look up tool metadata across all registries.

    Checks PYTHON_TOOL_REGISTRY first, then SHELL_TOOL_REGISTRY,
    then GO_TOOL_REGISTRY.

    Args:
        tool_name: Name of the tool to look up.

    Returns:
        Registry entry dict, or None if not found in any registry.
    """
    for registry in (PYTHON_TOOL_REGISTRY, SHELL_TOOL_REGISTRY,
                     GO_TOOL_REGISTRY):
        if tool_name in registry:
            return registry[tool_name]
    return None


def _has_flake8_config(project_root: Path) -> bool:
    """Check if flake8 config is declared in this project.

    Returns True if any of:
      - project_root/.flake8 exists
      - setup.cfg has a [flake8] section
      - tox.ini has a [flake8] section
    """
    if (project_root / ".flake8").exists():
        return True

    for cfg_file in ("setup.cfg", "tox.ini"):
        cfg_path = project_root / cfg_file
        if cfg_path.exists():
            try:
                parser = configparser.ConfigParser()
                parser.read(str(cfg_path), encoding="utf-8")
                if parser.has_section("flake8"):
                    return True
            except configparser.Error:
                pass

    return False


def _scan_path_for_tools(
    which_fn: Callable[[str], Optional[str]],
    detected: list[str],
    missing: list[str],
    registry: dict[str, dict] = None,
) -> None:
    """Scan PATH for all entries in the given registry.

    Appends to detected/missing lists, guarding against duplicates.

    Args:
        which_fn: Callable for PATH lookup.
        detected: List of detected tool names (mutated in place).
        missing: List of missing tool names (mutated in place).
        registry: Tool registry to scan (default: PYTHON_TOOL_REGISTRY).
    """
    if registry is None:
        registry = PYTHON_TOOL_REGISTRY
    for name, meta in registry.items():
        binary = meta["binary"]
        if name in detected or name in missing:
            continue
        if which_fn(binary):
            detected.append(name)
        else:
            missing.append(name)


def detect_toolchain(
    project_root: Path,
    which_fn: Callable[[str], Optional[str]] = shutil.which,
) -> DetectionResult:
    """Detect project toolchain from config files and PATH.

    Strategy:
      1. Try pyproject.toml: walk [tool.*] sections, verify via PATH.
      2. If pyproject.toml has no recognized [tool.*] sections,
         fall back to PATH scan for all known Python tools.
      3. If no pyproject.toml or TOML is corrupt, check for Python
         indicators (*.py, setup.py, setup.cfg, requirements.txt)
         and scan PATH.
      4. flake8 config-file detection runs ALWAYS (independent of
         the [tool.*] walk) because flake8 has no pyproject.toml
         support.
      5. Shell detection: check for *.sh files in root and one level
         deep; if found, scan SHELL_TOOL_REGISTRY.
      6. If nothing detected, raise CliError.

    Language priority: Python indicators take precedence over shell
    in mixed projects. "shell" is returned only when no Python
    indicators are present.

    Args:
        project_root: Path to project root directory.
        which_fn: Callable for PATH lookup (default: shutil.which).
            Injected for testability.

    Returns:
        DetectionResult with detected/missing tool lists.

    Raises:
        CliError: if no project indicators found.
    """
    detected: list[str] = []
    missing: list[str] = []
    pyproject_path = project_root / "pyproject.toml"
    pyproject_parsed = False
    has_python = False

    if pyproject_path.exists():
        try:
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
            pyproject_parsed = True
            has_python = True

            tool_section = data.get("tool", {})
            if isinstance(tool_section, dict):
                for name, meta in PYTHON_TOOL_REGISTRY.items():
                    toml_key = meta.get("toml_section")
                    # Skip entries with no pyproject.toml support
                    # (e.g. flake8 has toml_section=None)
                    if toml_key is None:
                        continue

                    # Navigate nested keys like "tool.ruff" or
                    # "tool.pytest.ini_options"
                    parts = toml_key.split(".")
                    # First part is always "tool", already resolved
                    subsection = tool_section
                    found = True
                    for part in parts[1:]:
                        if isinstance(subsection, dict) and part in subsection:
                            subsection = subsection[part]
                        else:
                            found = False
                            break

                    if found:
                        binary = meta["binary"]
                        if which_fn(binary):
                            detected.append(name)
                        else:
                            missing.append(name)

            # If pyproject.toml parsed OK but no recognized
            # [tool.*] sections produced any detected tools, fall back
            # to PATH scan for all known Python tools.
            if not detected:
                _scan_path_for_tools(which_fn, detected, missing)

        except tomllib.TOMLDecodeError:
            # Corrupted TOML -> log warning, fall through to
            # fallback detection instead of crashing
            logger.warning(
                "Failed to parse %s, falling back to PATH detection",
                pyproject_path,
            )
            pyproject_parsed = False

    # Fallback: no pyproject.toml or corrupted TOML
    if not pyproject_parsed and not detected:
        # Check for Python indicators
        py_files = list(project_root.glob("*.py"))
        if not py_files:
            # Check one level deep
            py_files = list(project_root.glob("*/*.py"))
        if py_files:
            has_python = True

        for indicator in ("setup.py", "setup.cfg", "requirements.txt"):
            if (project_root / indicator).exists():
                has_python = True
                break

        if has_python:
            _scan_path_for_tools(which_fn, detected, missing)

    # flake8 config-file detection: runs ALWAYS, independent of
    # the [tool.*] walk and the PATH-scan fallback.
    # flake8 has no pyproject.toml [tool.flake8] support.
    if _has_flake8_config(project_root):
        binary = PYTHON_TOOL_REGISTRY["flake8"]["binary"]
        if which_fn(binary):
            if "flake8" not in detected:
                detected.append("flake8")
        else:
            if "flake8" not in missing:
                missing.append("flake8")

    # Shell detection: check for *.sh / *.bash in root and one level deep.
    # Runs independently of Python detection to support mixed projects.
    has_shell = False
    sh_files = list(project_root.glob("*.sh")) + list(project_root.glob("*.bash"))
    if not sh_files:
        sh_files = (
            list(project_root.glob("*/*.sh"))
            + list(project_root.glob("*/*.bash"))
        )
    if sh_files:
        has_shell = True
        _scan_path_for_tools(
            which_fn, detected, missing, registry=SHELL_TOOL_REGISTRY,
        )

    # Go detection: check for go.mod or *.go files.
    has_go = False
    if (project_root / "go.mod").exists():
        has_go = True
    else:
        go_files = list(project_root.glob("*.go"))
        if not go_files:
            go_files = list(project_root.glob("*/*.go"))
        if go_files:
            has_go = True
    if has_go:
        _scan_path_for_tools(
            which_fn, detected, missing, registry=GO_TOOL_REGISTRY,
        )

    # De-duplicate: a tool must not be in both lists
    for name in list(detected):
        if name in missing:
            missing.remove(name)

    # No tools detected at all
    if not detected and not missing:
        raise CliError(
            "No toolchain detected. L0 has no static analysis tools. "
            "Install tools or manually configure "
            "`.code-forge/tools.yaml`."
        )

    # Language priority: Go > Python > Shell in mixed projects.
    if has_go:
        language = "go"
    elif has_python:
        language = "python"
    elif has_shell:
        language = "shell"
    else:
        language = "python"

    return DetectionResult(
        detected=detected,
        missing=missing,
        language=language,
    )


def generate_tools_yaml(
    result: DetectionResult,
    output_path: Path,
) -> None:
    """Generate tools.yaml from detection result.

    Only includes tools with a tools_yaml_entry in any registry
    (linters with parsers). Tools without entries (pytest, mypy)
    are excluded.

    Uses copy.deepcopy to ensure generated entries have independent
    file_patterns lists -- no shared mutable alias with registry
    module constants.

    Args:
        result: DetectionResult from detect_toolchain().
        output_path: Path to write tools.yaml.

    Raises:
        CliError: if no linter tools have tools_yaml_entry.
    """
    tools_dict: dict[str, dict] = {}
    for tool_name in result.detected:
        meta = _get_tool_meta(tool_name)
        if meta and "tools_yaml_entry" in meta:
            tools_dict[tool_name] = copy.deepcopy(meta["tools_yaml_entry"])

    if not tools_dict:
        raise CliError(
            "No toolchain detected. L0 has no static analysis tools. "
            "Install tools or manually configure "
            "`.code-forge/tools.yaml`."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"tools": tools_dict}, f, default_flow_style=False)


def _merge_and_write(result: DetectionResult, output_path: Path) -> None:
    """Merge detected tools into existing tools.yaml, preserving user entries.

    User-added entries (names not in any tool registry) are preserved.
    Detected entries update/overwrite stale entries of the same name.
    Falls back to fresh generation if existing file is empty or corrupt.

    Args:
        result: DetectionResult with detected tools to merge in.
        output_path: Path to existing tools.yaml (must exist).
    """
    try:
        with open(output_path, "r", encoding="utf-8") as f:
            existing_data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError, ValueError):
        existing_data = None

    existing_tools = None
    if isinstance(existing_data, dict):
        candidate = existing_data.get("tools")
        if isinstance(candidate, dict):
            existing_tools = candidate

    if existing_tools is None:
        generate_tools_yaml(result, output_path)
        return

    # Build detected tools dict (same logic as generate_tools_yaml)
    detected_tools: dict[str, dict] = {}
    for tool_name in result.detected:
        meta = _get_tool_meta(tool_name)
        if meta and "tools_yaml_entry" in meta:
            detected_tools[tool_name] = copy.deepcopy(meta["tools_yaml_entry"])

    # Merge: keep only user-added entries (not in any registry),
    # then layer detected entries on top.
    all_registry_names = (set(PYTHON_TOOL_REGISTRY)
                          | set(SHELL_TOOL_REGISTRY)
                          | set(GO_TOOL_REGISTRY))
    merged = {k: v for k, v in existing_tools.items()
              if k not in all_registry_names}
    merged.update(detected_tools)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"tools": merged}, f, default_flow_style=False)


def detect_and_init(
    project_root: Path,
    force: bool = False,
    quiet: bool = False,
    which_fn: Callable[[str], Optional[str]] = shutil.which,
) -> DetectionResult:
    """Detect toolchain and generate tools.yaml if missing.

    Idempotent: if tools.yaml exists and is non-empty,
    skip generation unless force=True.

    When force=True and an existing tools.yaml is present, merges
    detected tools into the existing file, preserving user-added
    entries not in any tool registry.

    Args:
        project_root: Path to project root directory.
        force: Force regeneration even if tools.yaml exists.
            Merges detected tools into existing file to preserve
            user-added entries.
        quiet: Suppress stdout detection report.
        which_fn: Callable for PATH lookup (dependency injection).

    Returns:
        DetectionResult with detected/missing tool lists.

    Raises:
        CliError: on empty project or malformed existing
            tools.yaml.
    """
    tools_yaml_path = project_root / ".code-forge" / "tools.yaml"

    # Idempotency check: skip if existing non-empty tools.yaml
    if tools_yaml_path.exists() and not force:
        try:
            registry = load_registry(str(tools_yaml_path))
        except (FileNotFoundError, ValueError) as exc:
            # Present but malformed -> fail loud, do not
            # silently overwrite a hand-edited file
            raise CliError(
                "Existing %s is malformed: %s. "
                "Fix it, delete it to regenerate, or "
                "rerun with force=True." % (tools_yaml_path, exc)
            ) from exc

        if registry:
            # Existing non-empty registry -> return without regeneration.
            # Infer language from tool names present in the registry.
            has_shell_tools = any(t in SHELL_TOOL_REGISTRY for t in registry)
            has_python_tools = any(t in PYTHON_TOOL_REGISTRY for t in registry)
            has_go_tools = any(t in GO_TOOL_REGISTRY for t in registry)
            if has_go_tools:
                lang = "go"
            elif has_python_tools or not has_shell_tools:
                lang = "python"
            else:
                lang = "shell"
            return DetectionResult(
                detected=list(registry.keys()),
                missing=[],
                language=lang,
            )
        # load_registry returned {} -> empty/zero-byte/null tools
        # Fall through to regenerate

    result = detect_toolchain(project_root, which_fn=which_fn)

    if force and tools_yaml_path.exists():
        _merge_and_write(result, tools_yaml_path)
    else:
        generate_tools_yaml(result, tools_yaml_path)

    if not quiet:
        print(
            "Detected: %s / Missing: %s"
            % (", ".join(result.detected), ", ".join(result.missing))
        )

    return result
