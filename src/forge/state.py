# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""State persistence to .forge/state.json.

Atomic write via tempfile + os.replace in the same directory
(Round 3 H-2: avoids cross-filesystem os.replace failure).

Addresses:
- Mimo: .forge/ directory auto-creation before write
- Consensus #3: tool_versions in state structure
- Round 3 C-3: ToolError.to_dict() for serialization
"""

import json
import logging
import os
import tempfile

logger = logging.getLogger(__name__)


def _ensure_dir(path: str) -> None:
    """Create parent directory of path if it does not exist."""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)


def write_state(state_path: str, data: dict) -> None:
    """Write state dict to JSON file atomically.

    Creates parent directory if needed. Uses tempfile in the same
    directory as state_path to ensure os.replace() is an atomic
    rename (same filesystem).

    Args:
        state_path: path to state.json (e.g. ".forge/state.json")
        data: state dict to serialize
    """
    _ensure_dir(state_path)

    dest_dir = os.path.dirname(os.path.abspath(state_path))
    tmp_path = None
    try:
        fd = tempfile.NamedTemporaryFile(
            mode="w",
            dir=dest_dir,
            suffix=".tmp",
            delete=False,
        )
        tmp_path = fd.name
        with fd:
            json.dump(data, fd, indent=2)
        os.replace(tmp_path, os.path.abspath(state_path))
    except Exception:
        if tmp_path is not None and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def read_state(state_path: str) -> dict | None:
    """Read state from JSON file.

    Args:
        state_path: path to state.json

    Returns:
        Parsed dict, or None if file missing or corrupt.
    """
    if not os.path.isfile(state_path):
        return None

    try:
        with open(state_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read state from %s: %s", state_path, exc)
        return None
