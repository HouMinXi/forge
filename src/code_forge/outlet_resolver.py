# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Outlet resolution.

Pure precedence function: FORGE_OUTLET env > gate.yaml outlet > backend
reachability probe.

Resolves which review outlet to use:
  - "cli"      -> Outlet A (CLI dispatcher, fresh subprocess per pass)
  - "inline"   -> Outlet B (inline merged skill, in-process)
  - "subagent" -> Outlet C (fresh Agent per pass, no CLI overhead)

Key invariants:
  - Outlet B (inline) and Outlet C (subagent) NEVER trigger the
    reachability probe.
  - Backend unreachable with no explicit override raises CliError
    (FAIL CLOSED) -- never silently degrades to inline.
  - No implicit claude -p fallthrough: when no backend is explicitly
    configured and no FORGE_OUTLET is set, CLI refuses to probe and
    raises CliError with configuration guidance.
  - No model-capability auto-detection anywhere (LOCKED).
  - gate.yaml is read via a lightweight reader that does NOT
    require a "test:" section.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Mapping, Optional

import yaml

from .backend import (
    ProbeResult,
    probe_backend,
    resolve_backend,
)
from .errors import CliError


# -- Valid outlet values ---------------------------------------------------

VALID_OUTLET_STRINGS = {"cli": "cli", "inline": "inline", "subagent": "subagent"}


# -- Parsing ---------------------------------------------------------------


def _parse_outlet_string(value: str, source: str) -> str:
    """Strip, lowercase, look up in allow-list.

    Structurally identical to mode_resolver._parse_mode_string:
    whitespace-only strips to "" which is not in the allow-list
    and raises with source attribution.
    """
    key = value.strip().lower()
    if key not in VALID_OUTLET_STRINGS:
        raise ValueError(
            "invalid outlet %r from %s (expected: %s)"
            % (value, source, "|".join(sorted(VALID_OUTLET_STRINGS)))
        )
    return VALID_OUTLET_STRINGS[key]


# -- gate.yaml reader ---------------------------------------------------


def load_outlet_from_gate(
    gate_yaml_path: Path,
    fs_open: Callable = open,
) -> Optional[str]:
    """Read only the 'outlet' key from gate.yaml.

    Does NOT call load_gate_config (avoids the "test section
    required" constraint).

    Returns:
        The outlet string value if present, else None.

    Raises:
        ValueError: corrupted YAML or unreadable file.
    """
    try:
        with fs_open(gate_yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        return None
    except PermissionError as exc:
        raise ValueError(
            "gate.yaml read failed: permission denied"
        ) from exc
    except yaml.YAMLError as exc:
        raise ValueError(
            "gate.yaml read failed: %s" % exc
        ) from exc

    if isinstance(data, dict) and "outlet" in data:
        val = data["outlet"]
        if not isinstance(val, str):
            raise ValueError(
                "gate.yaml outlet must be a string, got %s"
                % type(val).__name__
            )
        return val

    return None


# -- Main resolver ---------------------------------------------------------


def resolve_outlet(
    env: Mapping[str, str],
    gate_yaml_path: Optional[Path] = None,
    *,
    cli_value: Optional[str] = None,
    configs: Optional[list] = None,
    has_explicit_backend: bool = False,
    reachability_fn: Optional[Callable[[], ProbeResult]] = None,
) -> str:
    """Resolve effective outlet given inputs.

    Precedence (highest first):
      1. cli_value from --outlet flag (if present and non-empty)
      2. FORGE_OUTLET env var (if present and non-empty)
      3. gate.yaml outlet field (if gate_yaml_path given and key present)
      4. Zero-config guard: raise CliError when no backend is configured
      5. Backend reachability probe

    The fifth signal uses the backend-agnostic probe from backend.py.
    Reachable -> "cli" (fail-safe Outlet A).
    Unreachable -> CliError (FAIL CLOSED).

    An explicit "inline" or "subagent" (from cli_value, env, or gate.yaml)
    short-circuits BEFORE any reachability probe -- Outlets B and C NEVER
    probe.

    LOCKED: nowhere in this function is model capability
    inspected.  The only signals are the explicit override and the
    objective reachability of the configured backend.

    Args:
        env: os.environ or test-injected mapping
        gate_yaml_path: path to gate.yaml (None to skip)
        cli_value: value from --outlet flag (highest precedence)
        configs: list of BackendConfig from gate.yaml ([] means no backend
            configured; non-empty means user deliberately configured one)
        has_explicit_backend: True when caller assembled an inline backend
            via --backend-url/format/key-env/model or --backend <name>
        reachability_fn: callable returning ProbeResult (injected
            for testability; production default resolves + probes
            the configured backend)

    Returns:
        "cli", "inline", or "subagent"

    Raises:
        ValueError: invalid outlet string from cli_value, env, or gate.yaml
        CliError: zero-config (no backend configured) or backend unreachable
    """
    if configs is None:
        configs = []

    # Check cli_value first (highest precedence)
    if cli_value is not None and cli_value != "":
        return _parse_outlet_string(cli_value, "--outlet flag")

    # Default probes the session-default CLI backend only.
    # Production callers should inject a reachability_fn that
    # uses the loaded backend config.
    if reachability_fn is None:
        def reachability_fn() -> ProbeResult:
            backend = resolve_backend(env, configs=configs, cli_value=None)
            return probe_backend(backend, env=env)

    # Step 1: FORGE_OUTLET env override
    env_value = env.get("FORGE_OUTLET")
    if env_value is not None and env_value != "":
        return _parse_outlet_string(env_value, "FORGE_OUTLET env")

    # Step 2: gate.yaml outlet field
    if gate_yaml_path is not None:
        gate_value = load_outlet_from_gate(gate_yaml_path)
        if gate_value is not None:
            return _parse_outlet_string(str(gate_value), "gate.yaml outlet")

    # Step 3: zero-config guard -- refuse to probe the implicit subprocess
    # when no backend is explicitly configured. This prevents a 120s timeout
    # and billing the main session account.
    if not configs and not has_explicit_backend:
        raise CliError(
            "No review backend configured. Choose one:\n"
            "  1. gate.yaml backends (persistent):"
            " create .code-forge/gate.yaml\n"
            "  2. --backend-url/--backend-format/--backend-key-env/"
            "--backend-model (one-off)\n"
            "  3. FORGE_OUTLET=inline"
            " (review in THIS session; uses main quota)\n"
            "Implicit `claude -p` is disabled:"
            " it nests a subprocess and bills the main account."
        )

    # Step 4: backend reachability
    result = reachability_fn()
    if result.ok:
        return "cli"

    raise CliError(
        "Configure a review backend or set FORGE_OUTLET=inline. "
        "Reachability: %s" % result.error
    )
