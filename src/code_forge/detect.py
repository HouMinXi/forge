# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Toolchain auto-detection for Python projects.

Detects project toolchain from pyproject.toml [tool.*] sections,
flake8 config files (.flake8, setup.cfg, tox.ini), and PATH-based
fallback heuristics. Generates .code-forge/tools.yaml for L0 linting.
"""

from __future__ import annotations

import configparser
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
    language: detected project language (e.g. "python").
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
) -> None:
    """Scan PATH for all PYTHON_TOOL_REGISTRY entries.

    Appends to detected/missing lists, guarding against duplicates.
    """
    for name, meta in PYTHON_TOOL_REGISTRY.items():
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
      5. If nothing detected, raise CliError.

    Args:
        project_root: Path to project root directory.
        which_fn: Callable for PATH lookup (default: shutil.which).
            Injected for testability.

    Returns:
        DetectionResult with detected/missing tool lists.

    Raises:
        CliError: if no Python indicators found.
    """
    detected: list[str] = []
    missing: list[str] = []
    pyproject_path = project_root / "pyproject.toml"
    pyproject_parsed = False

    if pyproject_path.exists():
        try:
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
            pyproject_parsed = True

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
        has_python = False

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

    return DetectionResult(
        detected=detected,
        missing=missing,
        language="python",
    )


def generate_tools_yaml(
    result: DetectionResult,
    output_path: Path,
) -> None:
    """Generate tools.yaml from detection result.

    Only includes tools with a tools_yaml_entry in the registry
    (linters with parsers). Tools without entries (pytest, mypy)
    are excluded.

    Args:
        result: DetectionResult from detect_toolchain().
        output_path: Path to write tools.yaml.

    Raises:
        CliError: if no linter tools have tools_yaml_entry.
    """
    tools_dict: dict[str, dict] = {}
    for tool_name in result.detected:
        meta = PYTHON_TOOL_REGISTRY.get(tool_name)
        if meta and "tools_yaml_entry" in meta:
            tools_dict[tool_name] = dict(meta["tools_yaml_entry"])

    if not tools_dict:
        raise CliError(
            "No toolchain detected. L0 has no static analysis tools. "
            "Install tools or manually configure "
            "`.code-forge/tools.yaml`."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"tools": tools_dict}, f, default_flow_style=False)


def detect_and_init(
    project_root: Path,
    force: bool = False,
    quiet: bool = False,
    which_fn: Callable[[str], Optional[str]] = shutil.which,
) -> DetectionResult:
    """Detect toolchain and generate tools.yaml if missing.

    Idempotent: if tools.yaml exists and is non-empty,
    skip generation unless force=True.

    Args:
        project_root: Path to project root directory.
        force: Force regeneration even if tools.yaml exists.
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
            # Existing non-empty registry -> return without regeneration
            # Use "python" as language, not "existing" sentinel
            return DetectionResult(
                detected=list(registry.keys()),
                missing=[],
                language="python",
            )
        # load_registry returned {} -> empty/zero-byte/null tools
        # Fall through to regenerate

    result = detect_toolchain(project_root, which_fn=which_fn)
    generate_tools_yaml(result, tools_yaml_path)

    if not quiet:
        print(
            "Detected: %s / Missing: %s"
            % (", ".join(result.detected), ", ".join(result.missing))
        )

    return result
