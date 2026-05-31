# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Pluggable review-backend abstraction.

Provides:
  - BackendConfig: frozen dataclass for a backend entry
  - ProbeResult: frozen dataclass for reachability result
  - load_backend_configs(): parse config entries into BackendConfig list
  - resolve_backend(): FORGE_BACKEND > config default > session default
  - resolve_auth_timeout(): FORGE_AUTH_TIMEOUT resolution
  - probe_backend(): backend-agnostic reachability probe
  - invalidate_probe_cache(): remove cached probe result
  - DEFAULT_BACKEND: session-model cli backend with no model pin

Locks abstraction + config schema + resolution + probe ONLY.
HTTP clients and cli review wrappers land later.

NON-GOAL: resolve_backend NEVER inspects diff, complexity,
or change size.  The user configures the model; forge follows the session
model by default.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Mapping, Optional

from .errors import CliError

# -- Constants --------------------------------------------------------

VALID_BACKEND_TYPES = {"api", "cli"}
VALID_API_FORMATS = {"openai", "anthropic"}

DEFAULT_AUTH_TIMEOUT = 20          # generous cap
MAX_REASONABLE_AUTH_TIMEOUT = 120  # sanity bound

CACHE_TTL_SECONDS = 300            # 5-minute TTL
CACHE_FILENAME = "backend_probe_cache.json"


# -- Data classes -----------------------------------------------------


@dataclass(frozen=True)
class ProbeResult:
    """Reachability probe result (generalizes old AuthResult)."""

    ok: bool
    error: Optional[str] = None


@dataclass(frozen=True)
class BackendConfig:
    """A configured review backend entry.

    api_key_env holds the NAME of an env var, NEVER a raw secret.
    The secret value is looked up at probe time, never stored.
    """

    name: str
    type: str               # "api" | "cli"
    model: str              # may be "" for the session-default cli
    format: Optional[str] = None       # "openai" | "anthropic"
    base_url: Optional[str] = None     # only for api
    api_key_env: Optional[str] = None  # env var NAME only
    default: bool = False              # config default marker


# -- DEFAULT_BACKEND -------------------------------------------------

DEFAULT_BACKEND = BackendConfig(
    name="session-default",
    type="cli",
    model="",
    format=None,
    base_url=None,
    api_key_env=None,
)


# -- Config parsing ---------------------------------------------------


def _parse_backend_entry(entry: dict) -> BackendConfig:
    """Parse and validate a single backend config entry."""
    name = entry.get("name", "<unnamed>")

    # Reject inline secrets (never allow raw keys in config)
    if "api_key" in entry:
        raise CliError(
            "backend %r: store the env-var NAME in api_key_env, "
            "never an inline api_key" % name
        )

    btype = entry.get("type", "")
    if btype not in VALID_BACKEND_TYPES:
        raise CliError(
            "backend %r: invalid type %r (expected: %s)"
            % (name, btype, "|".join(sorted(VALID_BACKEND_TYPES)))
        )

    model = entry.get("model", "")
    is_default = entry.get("default", False)

    if btype == "api":
        fmt = entry.get("format")
        if not fmt:
            raise CliError(
                "backend %r (api): missing required field 'format' "
                "(expected: %s)"
                % (name, "|".join(sorted(VALID_API_FORMATS)))
            )
        if fmt not in VALID_API_FORMATS:
            raise CliError(
                "backend %r (api): invalid format %r (expected: %s)"
                % (name, fmt, "|".join(sorted(VALID_API_FORMATS)))
            )
        base_url = entry.get("base_url")
        if not base_url:
            raise CliError(
                "backend %r (api): missing required field 'base_url'"
                % name
            )
        api_key_env = entry.get("api_key_env")
        if not api_key_env:
            raise CliError(
                "backend %r (api): missing required field 'api_key_env'"
                % name
            )
        return BackendConfig(
            name=name,
            type=btype,
            model=model,
            format=fmt,
            base_url=base_url,
            api_key_env=api_key_env,
            default=is_default,
        )

    # type == "cli"
    return BackendConfig(
        name=name,
        type=btype,
        model=model,
        format=None,
        base_url=None,
        api_key_env=None,
        default=is_default,
    )


def load_backend_configs(
    data: Optional[dict],
) -> List[BackendConfig]:
    """Parse already-loaded config mapping into BackendConfig list.

    Returns [] for None / empty / missing backends key.
    """
    if data is None:
        return []
    backends = data.get("backends")
    if not backends:
        return []
    return [_parse_backend_entry(e) for e in backends]


# -- Timeout resolution -----------------------------------------------


def resolve_auth_timeout(
    cli_value: Optional[int],
    env: Mapping[str, str],
) -> int:
    """Resolve auth probe timeout: cli > env > default (20s).

    Validates >= 1 and <= MAX_REASONABLE_AUTH_TIMEOUT.
    """
    if cli_value is not None:
        return _validate_timeout(cli_value, "--auth-timeout")
    raw = env.get("FORGE_AUTH_TIMEOUT")
    if raw is None or raw == "":
        return DEFAULT_AUTH_TIMEOUT
    return _parse_env_timeout(raw)


def _parse_env_timeout(raw: str) -> int:
    """Parse FORGE_AUTH_TIMEOUT string to validated int."""
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise CliError(
            "invalid FORGE_AUTH_TIMEOUT: %r (expected int)" % raw
        ) from exc
    return _validate_timeout(value, "FORGE_AUTH_TIMEOUT")


def _validate_timeout(value: int, name: str) -> int:
    """Validate timeout >= 1 and <= cap."""
    if value < 1:
        raise CliError(
            "invalid %s: %d (must be >= 1)" % (name, value)
        )
    if value > MAX_REASONABLE_AUTH_TIMEOUT:
        raise CliError(
            "invalid %s: %d (exceeds sanity cap %d)"
            % (name, value, MAX_REASONABLE_AUTH_TIMEOUT)
        )
    return value


# -- Backend resolution ------------------------------------------------


def resolve_backend(
    env: Mapping[str, str],
    configs: List[BackendConfig],
    cli_value: Optional[str] = None,
) -> BackendConfig:
    """Resolve the active backend.

    Precedence: cli_value > FORGE_BACKEND env > config default > session
    default (DEFAULT_BACKEND).

    CRITICAL (NON-GOAL): This function MUST NOT accept a diff,
    complexity, or change-size parameter.  forge NEVER analyzes the diff
    to auto-select a backend or model.
    """
    # Step 1: determine the override key (cli > env)
    override = cli_value
    if override is None:
        override = env.get("FORGE_BACKEND")

    if override is not None and override != "":
        key = override.strip()
        if key == "":
            raise CliError(
                "invalid FORGE_BACKEND: whitespace-only value %r"
                % override
            )
        for cfg in configs:
            if cfg.name == key:
                return cfg
        configured = ", ".join(c.name for c in configs) or "none"
        raise CliError(
            "unknown backend %r (configured: %s)" % (key, configured)
        )

    # Step 2: empty-string override falls through
    # Step 3: config default
    if configs:
        for cfg in configs:
            if cfg.default:
                return cfg
        return configs[0]

    # Step 4: session-model default
    return DEFAULT_BACKEND


# -- Probe cache ------------------------------------------------------


def _default_cache_dir() -> Path:
    """Return the default cache directory for forge."""
    base = os.environ.get(
        "XDG_CACHE_HOME", str(Path.home() / ".cache")
    )
    return Path(base) / "code-forge"


def _read_cache(
    cache_dir: Path,
    backend_name: str,
    time_fn: Callable[[], float],
) -> Optional[ProbeResult]:
    """Read cached probe result if valid and within TTL.

    Returns None on ANY read failure: cache is convenience-only,
    so corrupted JSON, missing keys, wrong types, binary garbage,
    backend name mismatch, or permission errors all trigger a safe
    cache miss that re-probes.
    """
    try:
        cache_path = cache_dir / CACHE_FILENAME
        raw = cache_path.read_text()
        data = json.loads(raw)
        if data.get("backend") != backend_name:
            return None
        if data["ok"] and (time_fn() - data["timestamp"]) < CACHE_TTL_SECONDS:
            return ProbeResult(ok=True)
    except Exception:
        return None
    return None


def _write_cache(
    cache_dir: Path,
    backend_name: str,
    time_fn: Callable[[], float],
) -> None:
    """Write a successful probe result to cache."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / CACHE_FILENAME
    cache_path.write_text(json.dumps({
        "ok": True,
        "backend": backend_name,
        "timestamp": time_fn(),
    }))


def invalidate_probe_cache(
    cache_dir: Optional[Path] = None,
) -> None:
    """Remove the probe cache file."""
    if cache_dir is None:
        cache_dir = _default_cache_dir()
    cache_path = cache_dir / CACHE_FILENAME
    cache_path.unlink(missing_ok=True)


# -- Backend-agnostic reachability probe ------------------------------


def probe_backend(
    backend: BackendConfig,
    which_fn: Callable = shutil.which,
    run_cmd: Callable = subprocess.run,
    env: Optional[Mapping[str, str]] = None,
    cache_dir: Optional[Path] = None,
    time_fn: Callable[[], float] = time.time,
    timeout: int = DEFAULT_AUTH_TIMEOUT,
) -> ProbeResult:
    """Backend-agnostic reachability probe.

    For cli/claude: runs ``claude auth status --json`` (NOT an
    inference call).  For api: checks api_key_env presence in env
    (no subprocess, no network).

    Successful results are cached with 5-min TTL.
    Failures are NOT cached.
    """
    if env is None:
        env = os.environ
    if cache_dir is None:
        cache_dir = _default_cache_dir()

    # Check cache first
    cached = _read_cache(cache_dir, backend.name, time_fn)
    if cached is not None:
        return cached

    # Dispatch on backend type
    if backend.type == "api":
        result = _probe_api(backend, env)
    else:
        result = _probe_cli(backend, which_fn, run_cmd, timeout)

    # Cache successful results only
    if result.ok:
        _write_cache(cache_dir, backend.name, time_fn)

    return result


def _probe_api(
    backend: BackendConfig,
    env: Mapping[str, str],
) -> ProbeResult:
    """Check that the configured api_key_env is present.

    No subprocess, no network call.
    """
    key_name = backend.api_key_env
    if not key_name:
        return ProbeResult(
            ok=False,
            error="backend %r: no api_key_env configured" % backend.name,
        )
    if env.get(key_name):
        return ProbeResult(ok=True)
    return ProbeResult(
        ok=False,
        error="%s not set. Export the API key for backend %r."
        % (key_name, backend.name),
    )


def _probe_cli(
    backend: BackendConfig,
    which_fn: Callable,
    run_cmd: Callable,
    timeout: int,
) -> ProbeResult:
    """Probe a cli backend via ``claude auth status --json``.

    This is NOT an inference call -- zero token cost.
    """
    if which_fn("claude") is None:
        return ProbeResult(
            ok=False,
            error=(
                "claude binary not found in PATH. "
                "Install Claude CLI: "
                "https://docs.anthropic.com/en/docs/claude-code"
            ),
        )

    try:
        result = run_cmd(
            ["claude", "auth", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ProbeResult(
            ok=False,
            error=(
                "reachability probe timed out after %ds. "
                "Increase FORGE_AUTH_TIMEOUT or check the CLI."
                % timeout
            ),
        )

    if result.returncode != 0:
        stderr_text = (result.stderr or "")[:100]
        return ProbeResult(
            ok=False,
            error=(
                "claude reachability check failed (exit %d): %s"
                % (result.returncode, stderr_text)
            ),
        )

    # Parse the JSON output
    try:
        parsed = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return ProbeResult(
            ok=False,
            error="could not parse `claude auth status --json` output",
        )

    if parsed.get("loggedIn") is True:
        return ProbeResult(ok=True)

    return ProbeResult(
        ok=False,
        error=(
            "claude not logged in. "
            "Run `claude auth login` or set ANTHROPIC_API_KEY."
        ),
    )
