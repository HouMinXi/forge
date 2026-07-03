# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""User-level configuration shared by MCP server and CLI.

Provides a single-point loader for user-level backend defaults
(~/.config/code-forge/config.yaml or FORGE_CONFIG_DIR override).
Both mcp_server.py and cli.py import from here so the resolution
logic is never duplicated.

User-level backends are trusted implicitly (same trust level as
env vars) and do NOT pass through the project-level trust gate.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


def user_config_dir() -> Path:
    """Resolve user-level config directory (shared by reader and writer).

    Priority: FORGE_CONFIG_DIR > $XDG_CONFIG_HOME/code-forge >
    ~/.config/code-forge.  Returns the directory path (may not exist).
    """
    env = os.environ.get("FORGE_CONFIG_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    xdg_base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if not xdg_base:
        xdg_base = str(Path.home() / ".config")
    return Path(xdg_base).expanduser().resolve() / "code-forge"


def user_config_path() -> Path | None:
    """User-level config: explicit env, XDG path, or legacy fallback.

    Uses user_config_dir() for directory resolution, then checks for
    config.yaml there and at the legacy ~/.code-forge/gate.yaml path.

    Returns the path if found, else None.  The legacy path emits a
    deprecation warning on first load.
    """
    env = os.environ.get("FORGE_CONFIG_DIR", "").strip()
    if env:
        p = user_config_dir() / "config.yaml"
        if p.is_file():
            return p
        log.warning("FORGE_CONFIG_DIR=%s but %s not found", env, p)
        return None
    xdg = user_config_dir() / "config.yaml"
    if xdg.is_file():
        return xdg
    legacy = Path.home() / ".code-forge" / "gate.yaml"
    if legacy.is_file():
        log.warning(
            "Reading user-level backends from legacy path %s -- "
            "move to %s to silence this warning", legacy, xdg
        )
        return legacy
    return None


def load_user_backends() -> dict[str, dict]:
    """Load backends from user-level config (lenient, no 'test' required).

    Returns a dict of {backend_name: raw_config_dict}, empty on any
    error (warns, never crashes).
    """
    import yaml as _y

    path = user_config_path()
    if path is None:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = _y.safe_load(f)
    except Exception as exc:
        log.warning("Cannot read user config %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        log.warning("User config %s is not a YAML mapping, ignoring", path)
        return {}
    backends = data.get("backends")
    if backends is None:
        return {}
    if not isinstance(backends, dict):
        log.warning("User config %s 'backends' is not a mapping, ignoring", path)
        return {}
    return backends


def merge_backends(
    project: dict[str, dict],
    user: dict[str, dict],
) -> dict[str, dict]:
    """Merge project and user backends: project wins by name.

    Returns a new dict with project backends first (preserving
    insertion order) followed by user-only backends.  This ensures
    fallback[0] always picks a project backend that the CLI can
    resolve from gate.yaml.
    """
    merged: dict[str, dict] = {}
    merged.update(project)
    for k, v in user.items():
        if k not in merged:
            merged[k] = v
    return merged
