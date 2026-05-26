# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""YAML tool registry loader and file matcher.

Reads .forge/tools.yaml and returns structured ToolConfig objects.
Validates required fields and filters disabled entries (Round 3 C-4).
"""

import logging
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# Known parser keys -- warn on unknown but do not reject
_KNOWN_FORMATS = frozenset({
    "shellcheck_json",
    "ruff_json",
    "semgrep_json",
    "checkpatch",
    "pylint_json",
    "clippy_json",
    "golangci_json",
})

# Fields that must be present in each tool entry
_REQUIRED_FIELDS = ("command", "output_format", "file_patterns")


@dataclass
class ToolConfig:
    """Configuration for a single tool in the registry."""

    name: str
    command: str
    args: list[str]
    output_format: str          # parser dispatch key
    file_patterns: list[str]    # glob patterns (e.g. ["*.sh", "*.bash"])
    required: bool = False
    timeout: int = 30
    exclude_patterns: list[str] = field(default_factory=list)
    working_dir: Optional[str] = None  # e.g. "cargo_root"
    enabled: bool = True        # Round 3 C-4: allows disabling tools


def load_registry(yaml_path: str) -> dict[str, ToolConfig]:
    """Load .forge/tools.yaml, validate, return {name: ToolConfig}.

    Filters out entries where enabled=False (Round 3 C-4).

    Raises:
        FileNotFoundError: if yaml_path does not exist
        ValueError: if a tool entry is missing required fields
    """
    with open(yaml_path, "r", encoding="utf-8") as f:
        try:
            data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in {yaml_path}: {e}") from e

    if data is None or "tools" not in data:
        return {}

    tools = data["tools"]
    if not tools:
        return {}

    if not isinstance(tools, dict):
        raise ValueError(
            f"{yaml_path}: 'tools' must be a mapping, got {type(tools).__name__}"
        )

    registry = {}
    for name, entry in tools.items():
        if not isinstance(entry, dict):
            raise ValueError(
                "Tool '%s': entry must be a mapping, got %s"
                % (name, type(entry).__name__)
            )

        # Validate required fields
        for req in _REQUIRED_FIELDS:
            if req not in entry:
                raise ValueError(
                    "Tool '%s': missing required field '%s'" % (name, req)
                )

        for list_field in ("args", "file_patterns", "exclude_patterns"):
            val = entry.get(list_field)
            if val is None and list_field in _REQUIRED_FIELDS:
                raise ValueError(
                    "Tool '%s': required field '%s' cannot be null"
                    % (name, list_field)
                )
            if val is not None and not isinstance(val, list):
                raise ValueError(
                    "Tool '%s': '%s' must be a list, got %s"
                    % (name, list_field, type(val).__name__)
                )

        fmt = entry["output_format"]
        if fmt not in _KNOWN_FORMATS:
            logger.warning(
                "Tool '%s': unknown output_format '%s'", name, fmt
            )

        tc = ToolConfig(
            name=name,
            command=entry["command"],
            args=entry.get("args", []),
            output_format=fmt,
            file_patterns=entry["file_patterns"],
            required=entry.get("required", False),
            timeout=entry.get("timeout", 30),
            exclude_patterns=entry.get("exclude_patterns", []),
            working_dir=entry.get("working_dir"),
            enabled=entry.get("enabled", True),
        )

        # Filter disabled entries (Round 3 C-4)
        if not tc.enabled:
            continue

        registry[name] = tc

    return registry


def match_tools(
    registry: dict[str, ToolConfig],
    files: list[str],
) -> dict[str, list[str]]:
    """Return {tool_name: [matching_files]} for given file list.

    Only considers enabled tools (registry already filtered by
    load_registry). Files matching exclude_patterns are removed.
    """
    result = {}
    for name, tc in registry.items():
        matched = []
        for filepath in files:
            # Check if file matches any include pattern
            if not any(fnmatch(filepath, p) for p in tc.file_patterns):
                continue
            # Check if file matches any exclude pattern
            if any(fnmatch(filepath, p) for p in tc.exclude_patterns):
                continue
            matched.append(filepath)
        result[name] = matched
    return result
