# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""STATE-06 mode resolution.

Pure precedence function: CLI flag > FORGE_MODE env > TTY default.
"""
from __future__ import annotations

from typing import Mapping, Optional

from .state import Mode


VALID_MODE_STRINGS = {"local": Mode.LOCAL, "ci": Mode.CI}


def resolve_mode(
    cli_arg: Optional[str],
    env: Mapping[str, str],
    stdout_isatty: bool,
) -> Mode:
    """Resolve effective Mode given inputs.

    Precedence (highest first):
      1. cli_arg if not None (CLI --mode flag value)
      2. env["FORGE_MODE"] if present and non-empty
      3. default: Mode.LOCAL if stdout_isatty else Mode.CI

    String values are case-insensitive ("local", "Local", "LOCAL" all OK).
    Invalid string (anywhere) raises ValueError with the offending value.
    Empty FORGE_MODE ("") falls through to TTY default (M3 fix).
    Whitespace-only FORGE_MODE ("  ") raises ValueError (L1 fix).

    Args:
      cli_arg: value from argparse --mode (None if not provided)
      env: os.environ or test-injected mapping
      stdout_isatty: result of sys.stdout.isatty() at process start

    Returns:
      Mode.LOCAL or Mode.CI

    Raises:
      ValueError: cli_arg or env value not in {"local", "ci"}
    """
    if cli_arg is not None:
        return _parse_mode_string(cli_arg, source="--mode")
    env_value = env.get("FORGE_MODE")
    if env_value is not None and env_value != "":
        return _parse_mode_string(env_value, source="FORGE_MODE env")
    return Mode.LOCAL if stdout_isatty else Mode.CI


def _parse_mode_string(value: str, source: str) -> Mode:
    """Lower + lookup; raise with source attribution on miss."""
    key = value.strip().lower()
    if key not in VALID_MODE_STRINGS:
        raise ValueError(
            "invalid mode %r from %s (expected: local|ci)" % (value, source)
        )
    return VALID_MODE_STRINGS[key]
