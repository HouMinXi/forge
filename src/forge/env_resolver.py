# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""CLI-03 env override resolution (cli_value > env > default).

FORGE_MODE is intentionally NOT here -- 02-04 resolve_mode owns it.
"""
from __future__ import annotations

from typing import Mapping, Optional

from .disposition import MAX_FIX_ATTEMPTS_PER_FINGERPRINT
from .errors import CliError


DEFAULT_MAX_TOTAL_ROUNDS = 20
MAX_REASONABLE_FIX_ATTEMPTS = 100   # sanity bound
MAX_REASONABLE_TOTAL_ROUNDS = 1000  # sanity bound


def resolve_max_total_rounds(
    cli_value: Optional[int], env: Mapping[str, str]
) -> int:
    """Resolve max total rounds: cli > env > default (20)."""
    if cli_value is not None:
        return _validate_int(
            cli_value, "--max-total-rounds", MAX_REASONABLE_TOTAL_ROUNDS
        )
    raw = env.get("FORGE_MAX_TOTAL_ROUNDS")
    if raw is None or raw == "":
        return DEFAULT_MAX_TOTAL_ROUNDS
    return _parse_env_int(
        raw, "FORGE_MAX_TOTAL_ROUNDS", MAX_REASONABLE_TOTAL_ROUNDS
    )


def resolve_max_fix_attempts(
    cli_value: Optional[int], env: Mapping[str, str]
) -> int:
    """Resolve max fix attempts: cli > env > default (3)."""
    if cli_value is not None:
        return _validate_int(
            cli_value, "--max-fix-attempts", MAX_REASONABLE_FIX_ATTEMPTS
        )
    raw = env.get("FORGE_MAX_FIX_ATTEMPTS_PER_FINGERPRINT")
    if raw is None or raw == "":
        return MAX_FIX_ATTEMPTS_PER_FINGERPRINT
    return _parse_env_int(
        raw, "FORGE_MAX_FIX_ATTEMPTS_PER_FINGERPRINT",
        MAX_REASONABLE_FIX_ATTEMPTS,
    )


def resolve_falsification_engine(
    cli_value: Optional[str], env: Mapping[str, str]
) -> str:
    """Resolve falsification engine: cli > env > default (auto)."""
    if cli_value is not None:
        return cli_value
    raw = env.get("FORGE_FALSIFICATION_ENGINE")
    if raw is None or raw == "":
        return "auto"
    key = raw.strip().lower()
    if key not in {"auto", "stub", "real"}:
        raise CliError(
            "invalid FORGE_FALSIFICATION_ENGINE: %r "
            "(expected auto|stub|real)" % raw
        )
    return key


def _parse_env_int(raw: str, name: str, sanity_cap: int) -> int:
    """Parse string env value to int with validation."""
    try:
        value = int(raw.strip())
    except ValueError:
        raise CliError("invalid %s: %r (expected int)" % (name, raw))
    return _validate_int(value, name, sanity_cap)


def _validate_int(value: int, name: str, sanity_cap: int) -> int:
    """Validate int >= 1 and <= sanity_cap."""
    if value < 1:
        raise CliError(
            "invalid %s: %d (must be >= 1)" % (name, value)
        )
    if value > sanity_cap:
        raise CliError(
            "invalid %s: %d (exceeds sanity cap %d)"
            % (name, value, sanity_cap)
        )
    return value
