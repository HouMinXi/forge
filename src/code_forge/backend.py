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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Mapping, Optional, Tuple

from .errors import CliError

# -- Constants --------------------------------------------------------

VALID_BACKEND_TYPES = {"api", "cli"}
VALID_API_FORMATS = {"openai", "anthropic", "vertex"}
VALID_THINKING_TYPES = {"enabled", "adaptive", "disabled"}
VALID_OUTCAP_KEYS = {"max_tokens", "max_completion_tokens"}

# Keys managed by typed BackendConfig fields or protocol structure.
# Users must not override these through the generic params dict.
PROTECTED_PARAM_KEYS = frozenset({
    "model", "messages", "stream", "anthropic_version",
    "temperature", "thinking", "reasoning_effort",
    "max_completion_tokens", "max_tokens", "output_ceiling",
})

# ADR-0005 fields that only make sense on api backends
_API_ONLY_FIELDS = (
    "temperature", "max_completion_tokens", "thinking_type",
    "thinking_budget", "reasoning_effort", "stream",
    "outcap_key", "output_ceiling", "params",
)

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
    command: str = ""                  # cli binary name or path
    default: bool = False              # config default marker
    max_tokens: int = 16384            # output token cap for api calls
    output_ceiling: int = 0            # 0 = use max_tokens; >0 = override cap
    project_id: Optional[str] = None  # vertex: GCP project ID
    region: Optional[str] = None      # vertex: GCP region (default: global)
    credentials_path: Optional[str] = None  # vertex: service account JSON path

    # Per-provider sampling and reasoning parameters
    temperature: float = -1.0              # -1 = omit; >=0 = send
    max_completion_tokens: int = 0         # 0 = fallback to existing max_tokens
    thinking_type: str = ""                # "enabled"|"adaptive"|"disabled"; ""=omit
    thinking_budget: int = 0              # >0 = add thinking.budget_tokens
    reasoning_effort: str = ""             # ""=omit; non-empty = send
    stream: bool = False                   # true = SSE, reassembled to one response
    timeout_s: int = 0                     # 0 = use default timeout chain
    outcap_key: str = ""                   # "" = format default cap key name
    params: Optional[dict] = field(default=None, compare=False)
    # compare=False keeps BackendConfig hashable despite the dict

    # CLI backend child-process env overrides
    env_unset: Tuple[str, ...] = ()        # var names to remove from child env
    env_set: Tuple[Tuple[str, str], ...] = ()  # (name, value) pairs to set


# -- DEFAULT_BACKEND -------------------------------------------------

DEFAULT_BACKEND = BackendConfig(
    name="session-default",
    type="cli",
    model="",
    format=None,
    base_url=None,
    api_key_env=None,
    command="",
    max_tokens=16384,
)


# -- Config parsing ---------------------------------------------------


def _parse_provider_fields(entry: dict, name: str) -> dict:
    """Extract and validate provider-aware fields from an api entry.

    Returns kwargs dict for BackendConfig construction.
    """
    kw: dict = {}

    # Typed sampling/reasoning fields
    kw["temperature"] = entry.get("temperature", -1.0)
    kw["max_completion_tokens"] = entry.get("max_completion_tokens", 0)
    kw["thinking_budget"] = entry.get("thinking_budget", 0)
    kw["reasoning_effort"] = entry.get("reasoning_effort", "")
    kw["stream"] = bool(entry.get("stream", False))
    kw["timeout_s"] = entry.get("timeout_s", 0)

    # thinking_type enum
    tt = entry.get("thinking_type", "")
    if tt and tt not in VALID_THINKING_TYPES:
        raise CliError(
            "backend %r: invalid thinking_type %r (expected: %s)"
            % (name, tt, "|".join(sorted(VALID_THINKING_TYPES)))
        )
    kw["thinking_type"] = tt

    # outcap_key enum
    ock = entry.get("outcap_key") or ""
    if ock and ock not in VALID_OUTCAP_KEYS:
        raise CliError(
            "backend %r: invalid outcap_key %r (expected: %s or empty)"
            % (name, ock, "|".join(sorted(VALID_OUTCAP_KEYS)))
        )
    kw["outcap_key"] = ock

    # Cap validation: at least one positive
    max_tokens = entry.get("max_tokens", 16384)
    mct = kw["max_completion_tokens"]
    if mct <= 0 and max_tokens <= 0:
        raise CliError(
            "backend %r: output token cap must be positive "
            "(max_completion_tokens and max_tokens are both zero)"
            % name
        )

    # params: reject protected keys
    params = entry.get("params")
    if params is not None:
        for pk in PROTECTED_PARAM_KEYS:
            if pk in params:
                raise CliError(
                    "backend %r: params must not contain protected "
                    "key %r (use the dedicated config field instead)"
                    % (name, pk)
                )
    kw["params"] = params

    # Reject cli-only env fields on api/vertex
    if "env" in entry:
        raise CliError(
            "backend %r: 'env' field is only valid on cli backends"
            % name
        )
    if "env_unset" in entry:
        raise CliError(
            "backend %r: 'env_unset' is an internal field name, "
            "not a config key (use env: {unset: [...]})" % name
        )
    if "env_set" in entry:
        raise CliError(
            "backend %r: 'env_set' is an internal field name, "
            "not a config key (use env: {set: {...}})" % name
        )

    return kw


def _parse_cli_env(entry: dict, name: str) -> dict:
    """Extract and validate env fields from a cli entry.

    Returns kwargs dict with env_unset and env_set.
    """
    env = entry.get("env")
    if env is None:
        return {"env_unset": (), "env_set": ()}

    if not isinstance(env, dict):
        raise CliError(
            "backend %r: 'env' must be a dict, got %s"
            % (name, type(env).__name__)
        )

    allowed = {"unset", "set"}
    unknown = set(env.keys()) - allowed
    if unknown:
        raise CliError(
            "backend %r: unknown key(s) in env: %s"
            % (name, ", ".join(sorted(unknown)))
        )

    unset_list = env.get("unset") or []
    set_dict = env.get("set") or {}

    return {
        "env_unset": tuple(unset_list),
        "env_set": tuple(sorted(
            (k, str(v)) for k, v in set_dict.items()
        )),
    }


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
    max_tokens = entry.get("max_tokens", 16384)
    output_ceiling = entry.get("output_ceiling", 0)

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

        # Provider-aware fields (shared by all api formats)
        pf = _parse_provider_fields(entry, name)

        if fmt == "vertex":
            project_id = entry.get("project_id")
            if not project_id:
                raise CliError(
                    "backend %r (api/vertex): missing required field "
                    "'project_id'" % name
                )
            region = entry.get("region", "global")
            credentials_path = entry.get("credentials_path")
            return BackendConfig(
                name=name, type=btype, model=model, format=fmt,
                base_url=None, api_key_env=None, command="",
                default=is_default, max_tokens=max_tokens,
                output_ceiling=output_ceiling,
                project_id=project_id, region=region,
                credentials_path=credentials_path,
                **pf,
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
            name=name, type=btype, model=model, format=fmt,
            base_url=base_url, api_key_env=api_key_env, command="",
            default=is_default, max_tokens=max_tokens,
            output_ceiling=output_ceiling,
            **pf,
        )

    # type == "cli"
    # Reject api-only fields
    for af in _API_ONLY_FIELDS:
        if af in entry:
            raise CliError(
                "backend %r (cli): field %r is only valid on "
                "api backends" % (name, af)
            )

    env_kw = _parse_cli_env(entry, name)

    command = entry.get("command", "")
    return BackendConfig(
        name=name, type=btype, model=model,
        format=None, base_url=None, api_key_env=None,
        command=command, default=is_default, max_tokens=max_tokens,
        **env_kw,
    )


def load_backend_configs(
    data: Optional[dict],
) -> List[BackendConfig]:
    """Parse already-loaded config mapping into BackendConfig list.

    Expects backends as a dict with backend names as keys:
      backends:
        mimo:
          type: api
          ...

    Returns [] for None / empty / missing backends key.
    Raises CliError on invalid schema or multiple default: true entries.
    """
    if data is None:
        return []
    backends = data.get("backends")
    if not backends:
        return []
    if not isinstance(backends, dict):
        raise CliError(
            "backends must be a dict with backend names as keys"
        )
    configs = []
    for name, entry in backends.items():
        if not isinstance(entry, dict):
            raise CliError(
                "backend %r: entry must be a dict, got %s"
                % (name, type(entry).__name__)
            )
        entry["name"] = name
        configs.append(_parse_backend_entry(entry))
    # multiple default: true entries raise CliError
    defaults = [c for c in configs if c.default]
    if len(defaults) > 1:
        names = ", ".join(c.name for c in defaults)
        raise CliError("multiple default backends: %s" % names)
    return configs


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

    Explicitly configured cli backends (name != "session-default") bypass
    the probe entirely and return ProbeResult(ok=True) immediately.
    DEFAULT_BACKEND (name="session-default") still probes via _probe_cli.

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

    # Explicitly configured cli backends bypass probe (trust configured).
    # DEFAULT_BACKEND has name="session-default"; any gate.yaml cli backend
    # has a user-chosen name and is assumed reachable as configured.
    if backend.type == "cli" and backend.name != "session-default":
        return ProbeResult(ok=True)

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
    """Check that the configured backend credential is resolvable.

    No subprocess, no network call. openai/anthropic backends check
    api_key_env presence; vertex backends authenticate via OAuth2/ADC (no
    api_key_env), so they probe for resolvable GCP credentials instead,
    mirroring _invoke_vertex's resolution order.
    """
    if backend.format == "vertex":
        if backend.credentials_path:
            if Path(backend.credentials_path).is_file():
                return ProbeResult(ok=True)
            return ProbeResult(
                ok=False,
                error="backend %r (vertex): credentials_path %r not found"
                % (backend.name, backend.credentials_path),
            )
        # Presence-only, like the api_key_env check: validity is invoke's
        # job and the probe must stay no-network. credentials_path gets the
        # stronger is_file() check because it is forge-owned config.
        if env.get("GOOGLE_APPLICATION_CREDENTIALS"):
            return ProbeResult(ok=True)
        adc = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
        if adc.is_file():
            return ProbeResult(ok=True)
        return ProbeResult(
            ok=False,
            error="backend %r (vertex): no GCP credentials. Set "
            "credentials_path, GOOGLE_APPLICATION_CREDENTIALS, or run "
            "'gcloud auth application-default login'." % backend.name,
        )

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
    except OSError as exc:
        return ProbeResult(
            ok=False,
            error=(
                "claude reachability probe failed to start: %s"
                % exc
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
