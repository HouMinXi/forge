# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Outlet resolution.

Pure precedence function: FORGE_OUTLET env > gate.yaml outlet > backend
reachability probe.

Resolves which review outlet to use:
  - "subprocess" -> Outlet A (fresh subprocess per pass)
  - "inline"     -> Outlet B (inline merged skill, in-process)
  - "subagent"   -> Outlet C (fresh Agent per pass, no CLI overhead)
  - "sampling"   -> Outlet D (MCP client's own model, no API key)

Deprecated: "cli" is accepted as an alias for "subprocess" with a
  stderr DeprecationWarning. Will be removed in a future release.

Key invariants:
  - Outlet B (inline), Outlet C (subagent), and Outlet D (sampling)
    NEVER trigger the reachability probe.
  - Backend unreachable with no explicit override raises CliError
    (FAIL CLOSED) -- never silently degrades to inline.
  - No implicit claude -p fallthrough: when no backend is explicitly
    configured and no FORGE_OUTLET is set, CLI refuses to probe and
    raises CliError with configuration guidance.
  - No model-capability auto-detection anywhere.
  - gate.yaml is read via a lightweight reader that does NOT
    require a "test:" section.
"""
from __future__ import annotations

import sys
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

VALID_OUTLET_STRINGS = {
    "subprocess": "subprocess",
    "inline": "inline",
    "subagent": "subagent",
    "sampling": "sampling",
}

# Deprecated aliases: old_value -> (canonical_value, deprecation_message)
_DEPRECATED_OUTLET_ALIASES = {
    "cli": ("subprocess", "outlet 'cli' renamed to 'subprocess'"),
}

# Shared remediation text for every no-backend refusal.
_NO_BACKEND_GUIDANCE = (
    "  1. add a 'backends:' entry to .code-forge/gate.yaml"
    " (create the file with 'code-forge init' if missing)\n"
    "  2. --backend-url/--backend-format/--backend-key-env/"
    "--backend-model (one-off)\n"
    "  3. FORGE_OUTLET=inline"
    " (review in THIS session; uses main quota)\n"
    "Implicit `claude -p` is disabled:"
    " it nests a subprocess and bills the main account."
)


def _require_backend_for_subprocess(
    outlet: str,
    configs: list,
    has_explicit_backend: bool,
    source: str,
) -> str:
    """Explicit 'subprocess' still requires a configured backend.

    inline and subagent run in-session and sampling is MCP-only, so
    only subprocess depends on a backend. Without this check an
    explicit outlet value short-circuits past the zero-config guard
    and the review falls through to the implicit `claude -p` path
    the guard exists to block (init templates used to ship an active
    'outlet: subprocess' key, making this the default failure mode).
    """
    if outlet == "subprocess" and not configs and not has_explicit_backend:
        raise CliError(
            "outlet 'subprocess' is set (%s) but no review backend is"
            " configured. Choose one:\n%s" % (source, _NO_BACKEND_GUIDANCE)
        )
    return outlet


# -- Parsing ---------------------------------------------------------------


def _parse_outlet_string(value: str, source: str) -> str:
    """Strip, lowercase, look up in allow-list.

    Structurally identical to mode_resolver._parse_mode_string:
    whitespace-only strips to "" which is not in the allow-list
    and raises with source attribution.

    Deprecated aliases (e.g. "cli") are accepted with a stderr warning.
    """
    key = value.strip().lower()
    if key in _DEPRECATED_OUTLET_ALIASES:
        canonical, msg = _DEPRECATED_OUTLET_ALIASES[key]
        print(
            "code-forge: DeprecationWarning: %s. Update your config." % msg,
            file=sys.stderr,
        )
        return canonical
    if key not in VALID_OUTLET_STRINGS:
        raise CliError(
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
    Reachable -> "subprocess" (fail-safe Outlet A).
    Unreachable -> CliError (FAIL CLOSED).

    An explicit "inline", "subagent", or "sampling" (from cli_value, env,
    or gate.yaml) short-circuits BEFORE any reachability probe -- Outlets
    B, C, and D NEVER probe.

    Nowhere in this function is model capability
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
        "subprocess", "inline", "subagent", or "sampling"

    Raises:
        ValueError: invalid outlet string from cli_value, env, or gate.yaml
        CliError: zero-config (no backend configured) or backend unreachable
    """
    if configs is None:
        configs = []

    # Check cli_value first (highest precedence)
    if cli_value is not None and cli_value != "":
        return _require_backend_for_subprocess(
            _parse_outlet_string(cli_value, "--outlet flag"),
            configs, has_explicit_backend, "--outlet flag",
        )

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
        return _require_backend_for_subprocess(
            _parse_outlet_string(env_value, "FORGE_OUTLET env"),
            configs, has_explicit_backend, "FORGE_OUTLET env",
        )

    # Step 2: gate.yaml outlet field
    if gate_yaml_path is not None:
        gate_value = load_outlet_from_gate(gate_yaml_path)
        if gate_value is not None:
            return _require_backend_for_subprocess(
                _parse_outlet_string(str(gate_value), "gate.yaml outlet"),
                configs, has_explicit_backend, "gate.yaml outlet",
            )

    # Step 3: zero-config guard -- refuse to probe the implicit subprocess
    # when no backend is explicitly configured. This prevents a 120s timeout
    # and billing the main session account.
    if not configs and not has_explicit_backend:
        raise CliError(
            "No review backend configured. Choose one:\n%s"
            % _NO_BACKEND_GUIDANCE
        )

    # Step 4: backend reachability
    result = reachability_fn()
    if result.ok:
        return "subprocess"

    raise CliError(
        "Configure a review backend or set FORGE_OUTLET=inline. "
        "Reachability: %s" % result.error
    )
