# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""TDD tests for backend config + resolution + probe.

Tests cover:
  - BackendConfig parse + schema validation
  - Active-backend resolution (precedence + session default)
  - Timeout resolution
  - Backend-agnostic reachability probe
  - Probe caching with TTL
  - Real-API opt-in
"""
from __future__ import annotations

import inspect
import subprocess

import pytest

from code_forge.backend import (
    DEFAULT_BACKEND,
    BackendConfig,
    ProbeResult,
    invalidate_probe_cache,
    load_backend_configs,
    probe_backend,
    resolve_auth_timeout,
    resolve_backend,
)
from code_forge.errors import CliError


# -- Helpers ----------------------------------------------------------


def _api_entry(**overrides):
    """Build a minimal valid api backend entry (D-11 dict-schema).

    Returns an entry dict without the 'name' key; the dict key in
    the backends mapping provides the name (injected by load_backend_configs).
    Default backend name when used in _as_backends_dict is 'deepseek'.
    """
    base = {
        "type": "api",
        "format": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "model": "deepseek-chat",
    }
    base.update(overrides)
    return base


def _cli_entry(**overrides):
    """Build a minimal valid cli backend entry (D-11 dict-schema).

    Returns an entry dict without the 'name' key; the dict key in
    the backends mapping provides the name (injected by load_backend_configs).
    Default backend name when used in _as_backends_dict is 'claude-sub'.
    """
    base = {
        "type": "cli",
        "model": "sonnet",
    }
    base.update(overrides)
    return base


def _as_api_backends(entry=None, name="deepseek"):
    """Wrap an api entry in the D-11 dict-schema backends mapping."""
    if entry is None:
        entry = _api_entry()
    return {"backends": {name: entry}}


def _as_cli_backends(entry=None, name="claude-sub"):
    """Wrap a cli entry in the D-11 dict-schema backends mapping."""
    if entry is None:
        entry = _cli_entry()
    return {"backends": {name: entry}}


def _noop_which(name):
    return "/usr/bin/" + name


def _noop_run(*_a, **_kw):
    raise AssertionError("run_cmd should not be called")


# =====================================================================
# Config schema + parse
# =====================================================================


class TestBackendConfigParse:
    """BackendConfig parses entries into frozen dataclass."""

    def test_backendconfig_parses_api_openai(self):
        cfgs = load_backend_configs(_as_api_backends())
        assert len(cfgs) == 1
        cfg = cfgs[0]
        assert cfg.name == "deepseek"
        assert cfg.type == "api"
        assert cfg.format == "openai"
        assert cfg.base_url == "https://api.deepseek.com/v1"
        assert cfg.api_key_env == "DEEPSEEK_API_KEY"
        assert cfg.model == "deepseek-chat"

    def test_backendconfig_parses_api_anthropic(self):
        entry = _api_entry(
            format="anthropic",
            base_url="https://api.anthropic.com",
            api_key_env="ANTHROPIC_API_KEY",
            model="claude-sonnet-4-20250514",
        )
        cfgs = load_backend_configs(
            {"backends": {"claude-api": entry}}
        )
        cfg = cfgs[0]
        assert cfg.type == "api"
        assert cfg.format == "anthropic"
        assert cfg.api_key_env == "ANTHROPIC_API_KEY"

    def test_backendconfig_parses_cli(self):
        cfgs = load_backend_configs(_as_cli_backends())
        cfg = cfgs[0]
        assert cfg.type == "cli"
        assert cfg.name == "claude-sub"
        assert cfg.model == "sonnet"

    def test_parse_cli_backend_with_command(self):
        """cli backend with explicit command field."""
        entry = _cli_entry(command="aicc")
        cfgs = load_backend_configs({"backends": {"custom": entry}})
        cfg = cfgs[0]
        assert cfg.command == "aicc"

    def test_parse_cli_backend_default_command(self):
        """cli backend without command key defaults to empty string."""
        cfgs = load_backend_configs({"backends": {"x": _cli_entry()}})
        cfg = cfgs[0]
        assert cfg.command == ""

    def test_parse_api_backend_ignores_command(self):
        """api backend always has empty command."""
        entry = _api_entry(command="should-be-ignored")
        cfgs = load_backend_configs(_as_api_backends(entry))
        cfg = cfgs[0]
        assert cfg.command == ""

    def test_default_backend_has_empty_command(self):
        """DEFAULT_BACKEND.command is empty string."""
        assert DEFAULT_BACKEND.command == ""

    def test_backendconfig_api_missing_format_raises(self):
        entry = _api_entry()
        del entry["format"]
        with pytest.raises(CliError, match="format"):
            load_backend_configs(_as_api_backends(entry))

    def test_backendconfig_api_missing_api_key_env_raises(self):
        entry = _api_entry()
        del entry["api_key_env"]
        with pytest.raises(CliError, match="api_key_env"):
            load_backend_configs(_as_api_backends(entry))

    def test_backendconfig_unknown_type_raises(self):
        entry = _api_entry(type="grpc")
        with pytest.raises(CliError, match="grpc"):
            load_backend_configs(_as_api_backends(entry))

    def test_backendconfig_inline_secret_rejected(self):
        """api_key (raw key) instead of api_key_env -> CliError."""
        entry = _api_entry(api_key="sk-secret-raw-key-value")
        with pytest.raises(CliError, match="api_key_env"):
            load_backend_configs(_as_api_backends(entry))

    def test_load_backend_configs_empty_returns_empty_list(self):
        assert load_backend_configs(None) == []
        assert load_backend_configs({}) == []
        assert load_backend_configs({"backends": {}}) == []

    def test_load_backend_configs_list_raises_cli_error(self):
        """backends as list -> CliError (D-11: dict required)."""
        with pytest.raises(CliError, match="backends must be a dict"):
            load_backend_configs({"backends": [_api_entry()]})

    def test_load_backend_configs_non_dict_entry_raises(self):
        """backends dict value that is not a dict -> CliError."""
        with pytest.raises(CliError):
            load_backend_configs({"backends": {"bad": "not-a-dict"}})

    def test_load_backend_configs_multiple_defaults_raises(self):
        """Two backends with default: true -> CliError (D-03)."""
        entry1 = _api_entry(default=True)
        entry2 = _api_entry(
            format="anthropic",
            base_url="https://api.anthropic.com",
            api_key_env="ANTHROPIC_API_KEY",
            model="claude-sonnet",
            default=True,
        )
        data = {"backends": {"deepseek": entry1, "claude-api": entry2}}
        with pytest.raises(CliError, match="multiple default backends"):
            load_backend_configs(data)

    def test_load_backend_configs_name_injected_from_key(self):
        """Backend name comes from YAML dict key, not from entry."""
        entry = _api_entry()
        cfgs = load_backend_configs({"backends": {"my-backend": entry}})
        assert cfgs[0].name == "my-backend"

    def test_load_backend_configs_max_tokens_from_entry(self):
        """max_tokens from entry overrides default 16384."""
        entry = _api_entry(max_tokens=8192)
        cfgs = load_backend_configs(_as_api_backends(entry))
        assert cfgs[0].max_tokens == 8192

    def test_load_backend_configs_max_tokens_default(self):
        """max_tokens defaults to 16384 when not in entry."""
        cfgs = load_backend_configs(_as_api_backends())
        assert cfgs[0].max_tokens == 16384

    def test_load_backend_configs_max_tokens_override(self):
        """max_tokens=8192 in entry is honoured (D-05)."""
        entry = _api_entry(max_tokens=8192)
        cfgs = load_backend_configs(_as_api_backends(entry))
        assert cfgs[0].max_tokens == 8192

    def test_load_backend_configs_dict_schema(self):
        """dict-based backends block parses correctly; name injected from key (D-11)."""
        data = {
            "backends": {
                "mimo": {
                    "type": "api",
                    "format": "anthropic",
                    "base_url": "https://api.mimo.com",
                    "api_key_env": "MIMO_API_KEY",
                    "model": "MiMo-V2.5-Pro",
                },
            }
        }
        cfgs = load_backend_configs(data)
        assert len(cfgs) == 1
        cfg = cfgs[0]
        assert cfg.name == "mimo"
        assert cfg.format == "anthropic"
        assert cfg.model == "MiMo-V2.5-Pro"
        assert cfg.api_key_env == "MIMO_API_KEY"

    def test_load_backend_configs_non_dict_raises(self):
        """Non-dict backends value (list) raises CliError (D-11)."""
        with pytest.raises(CliError, match="backends must be a dict"):
            load_backend_configs({"backends": [_api_entry()]})

    def test_load_backend_configs_entry_not_dict_raises(self):
        """Dict entry whose value is not a dict raises CliError."""
        with pytest.raises(CliError):
            load_backend_configs({"backends": {"bad": "not-a-dict"}})

    def test_multiple_defaults_raises(self):
        """Two entries with default=True raises CliError naming both backends (D-03)."""
        entry1 = _api_entry(default=True)
        entry2 = _api_entry(
            format="anthropic",
            base_url="https://api.anthropic.com",
            api_key_env="ANTHROPIC_API_KEY",
            model="claude-sonnet",
            default=True,
        )
        data = {"backends": {"deepseek": entry1, "claude-api": entry2}}
        with pytest.raises(CliError, match="multiple default backends"):
            load_backend_configs(data)

    def test_single_default_accepted(self):
        """Exactly one default=True entry is accepted without error (D-03)."""
        entry = _api_entry(default=True)
        cfgs = load_backend_configs(_as_api_backends(entry))
        assert len(cfgs) == 1
        assert cfgs[0].default is True

    def test_no_default_returns_first(self):
        """No default=True entry -> resolve_backend returns configs[0] (D-03)."""
        entry1 = _api_entry()
        entry2 = _api_entry(
            format="anthropic",
            base_url="https://api.anthropic.com",
            api_key_env="ANTHROPIC_API_KEY",
            model="claude-sonnet",
        )
        data = {"backends": {"deepseek": entry1, "claude-api": entry2}}
        cfgs = load_backend_configs(data)
        # Both entries have default=False; resolve_backend with no env -> first
        result = resolve_backend(env={}, configs=cfgs)
        assert result.name == "deepseek"


# =====================================================================
# Active-backend resolution (precedence + session default)
# =====================================================================


class TestResolveBackend:
    """resolve_backend honors FORGE_BACKEND > config > session default."""

    def test_resolve_backend_env_override_wins(self):
        cfgs = load_backend_configs(_as_api_backends())
        result = resolve_backend(
            env={"FORGE_BACKEND": "deepseek"}, configs=cfgs,
        )
        assert result.name == "deepseek"

    def test_resolve_backend_env_override_unknown_name_raises(self):
        cfgs = load_backend_configs(_as_api_backends())
        with pytest.raises(CliError, match="ghost"):
            resolve_backend(
                env={"FORGE_BACKEND": "ghost"}, configs=cfgs,
            )

    def test_resolve_backend_config_default_when_no_env(self):
        cfgs = load_backend_configs(_as_api_backends())
        result = resolve_backend(env={}, configs=cfgs)
        assert result.name == "deepseek"

    def test_resolve_backend_session_default_when_no_config_no_env(self):
        """No env + no configs -> DEFAULT_BACKEND (session model)."""
        result = resolve_backend(env={}, configs=[])
        assert result is DEFAULT_BACKEND
        assert result.type == "cli"
        assert result.model == ""

    def test_resolve_backend_has_no_diff_parameter(self):
        """NON-GOAL guard: signature has no diff/complexity/size."""
        sig = inspect.signature(resolve_backend)
        param_names = set(sig.parameters.keys())
        forbidden = {
            "diff", "complexity", "size", "change_size",
            "code", "lines", "changesize",
        }
        overlap = param_names & forbidden
        assert overlap == set(), (
            "resolve_backend MUST NOT take diff-related params: %s"
            % overlap
        )

    def test_resolve_backend_env_empty_falls_through(self):
        """FORGE_BACKEND="" -> falls through to config/session default."""
        cfgs = load_backend_configs(_as_api_backends())
        result = resolve_backend(
            env={"FORGE_BACKEND": ""}, configs=cfgs,
        )
        assert result.name == "deepseek"

    def test_resolve_backend_env_whitespace_raises(self):
        """FORGE_BACKEND="  " -> CliError with source attribution."""
        with pytest.raises((CliError, ValueError)):
            resolve_backend(env={"FORGE_BACKEND": "  "}, configs=[])

    def test_resolve_backend_cli_override_wins(self):
        """cli_value takes top priority over env and configs."""
        cfgs = load_backend_configs(_as_api_backends())
        result = resolve_backend(
            env={"FORGE_BACKEND": "deepseek"},
            configs=cfgs,
            cli_value="deepseek",
        )
        assert result.name == "deepseek"


# =====================================================================
# Timeout resolution
# =====================================================================


class TestTimeoutResolution:
    """resolve_auth_timeout follows env_resolver.py precedence."""

    def test_resolve_timeout_default(self):
        assert resolve_auth_timeout(cli_value=None, env={}) == 20

    def test_resolve_timeout_env_override(self):
        assert resolve_auth_timeout(
            cli_value=None, env={"FORGE_AUTH_TIMEOUT": "30"},
        ) == 30

    def test_resolve_timeout_invalid_string(self):
        with pytest.raises(CliError, match="FORGE_AUTH_TIMEOUT"):
            resolve_auth_timeout(
                cli_value=None, env={"FORGE_AUTH_TIMEOUT": "abc"},
            )

    def test_resolve_timeout_too_low(self):
        with pytest.raises(CliError):
            resolve_auth_timeout(
                cli_value=None, env={"FORGE_AUTH_TIMEOUT": "0"},
            )


# =====================================================================
# Backend-agnostic reachability probe -- CLI path
# =====================================================================


class TestProbeCli:
    """probe_backend for cli/claude backend."""

    def test_probe_cli_claude_logged_in(self, tmp_path):
        """Logged-in -> ok=True; command is auth status, NOT inference."""
        called_with = {}

        def mock_run(cmd, **kw):
            called_with["cmd"] = cmd
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout='{"loggedIn": true, "authMethod": "subscription"}',
                stderr="",
            )

        result = probe_backend(
            DEFAULT_BACKEND,
            which_fn=_noop_which,
            run_cmd=mock_run,
            env={},
            cache_dir=tmp_path,
            time_fn=lambda: 1000.0,
        )
        assert result.ok is True
        assert called_with["cmd"] == ["claude", "auth", "status", "--json"]

    def test_probe_cli_claude_not_logged_in(self, tmp_path):
        def mock_run(cmd, **kw):
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout='{"loggedIn": false}',
                stderr="",
            )

        result = probe_backend(
            DEFAULT_BACKEND,
            which_fn=_noop_which,
            run_cmd=mock_run,
            env={},
            cache_dir=tmp_path,
            time_fn=lambda: 1000.0,
        )
        assert result.ok is False
        assert "not logged in" in result.error.lower() or \
            "login" in result.error.lower()

    def test_probe_cli_binary_not_found(self, tmp_path):
        result = probe_backend(
            DEFAULT_BACKEND,
            which_fn=lambda _name: None,
            run_cmd=_noop_run,
            env={},
            cache_dir=tmp_path,
            time_fn=lambda: 1000.0,
        )
        assert result.ok is False
        assert "not found" in result.error.lower()

    def test_probe_cli_nonzero_exit(self, tmp_path):
        """Non-zero exit with stderr=None must not crash (None-safe)."""
        def mock_run(cmd, **kw):
            return subprocess.CompletedProcess(
                args=cmd, returncode=1,
                stdout="",
                stderr=None,
            )

        result = probe_backend(
            DEFAULT_BACKEND,
            which_fn=_noop_which,
            run_cmd=mock_run,
            env={},
            cache_dir=tmp_path,
            time_fn=lambda: 1000.0,
        )
        assert result.ok is False
        assert result.error is not None

    def test_probe_cli_malformed_json(self, tmp_path):
        """returncode=0 but non-JSON stdout -> ok=False."""
        def mock_run(cmd, **kw):
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout="not json at all",
                stderr="",
            )

        result = probe_backend(
            DEFAULT_BACKEND,
            which_fn=_noop_which,
            run_cmd=mock_run,
            env={},
            cache_dir=tmp_path,
            time_fn=lambda: 1000.0,
        )
        assert result.ok is False
        assert "parse" in result.error.lower()

    def test_probe_cli_timeout(self, tmp_path):
        def mock_run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=20)

        result = probe_backend(
            DEFAULT_BACKEND,
            which_fn=_noop_which,
            run_cmd=mock_run,
            env={},
            cache_dir=tmp_path,
            time_fn=lambda: 1000.0,
        )
        assert result.ok is False
        assert "timed out" in result.error.lower()

    def test_probe_cli_oserror_returns_fail(self, tmp_path):
        """OSError from run_cmd -> ProbeResult(ok=False), not crash."""
        def mock_run(cmd, **kw):
            raise PermissionError("mocked permission denied")

        result = probe_backend(
            DEFAULT_BACKEND,
            which_fn=_noop_which,
            run_cmd=mock_run,
            env={},
            cache_dir=tmp_path,
            time_fn=lambda: 1000.0,
        )
        assert result.ok is False
        assert "permission denied" in result.error.lower()

    def test_probe_cli_filenotfounderror_returns_fail(self, tmp_path):
        """FileNotFoundError (TOCTOU after which()) -> ok=False."""
        def mock_run(cmd, **kw):
            raise FileNotFoundError("No such file: 'claude'")

        result = probe_backend(
            DEFAULT_BACKEND,
            which_fn=_noop_which,
            run_cmd=mock_run,
            env={},
            cache_dir=tmp_path,
            time_fn=lambda: 1000.0,
        )
        assert result.ok is False
        assert result.error is not None


# =====================================================================
# Probe OSError -> outlet FAIL CLOSED
# =====================================================================


class TestProbeOsErrorFailClosed:
    """OSError in probe must trigger CliError (FAIL CLOSED) in outlet."""

    def test_oserror_probe_triggers_cli_error_in_resolve_outlet(self):
        """resolve_outlet raises CliError when probe hits OSError."""
        from code_forge.outlet_resolver import resolve_outlet

        def oserror_probe() -> ProbeResult:
            return ProbeResult(
                ok=False,
                error="claude reachability probe failed to start: "
                "[Errno 13] Permission denied",
            )

        with pytest.raises(CliError, match="Configure a review backend"):
            resolve_outlet(
                env={},
                gate_yaml_path=None,
                reachability_fn=oserror_probe,
            )


# =====================================================================
# Backend-agnostic reachability probe -- API path
# =====================================================================


class TestProbeApi:
    """probe_backend for api backend: no subprocess, no network."""

    def test_probe_api_key_present(self, tmp_path):
        """Key env var set -> ok=True, run_cmd NOT called."""
        cfg = load_backend_configs(_as_api_backends())[0]
        result = probe_backend(
            cfg,
            which_fn=_noop_which,
            run_cmd=_noop_run,
            env={"DEEPSEEK_API_KEY": "sk-test-key"},
            cache_dir=tmp_path,
            time_fn=lambda: 1000.0,
        )
        assert result.ok is True

    def test_probe_api_key_missing(self, tmp_path):
        """Key env var missing -> ok=False, error names the env var."""
        cfg = load_backend_configs(_as_api_backends())[0]
        result = probe_backend(
            cfg,
            which_fn=_noop_which,
            run_cmd=_noop_run,
            env={},
            cache_dir=tmp_path,
            time_fn=lambda: 1000.0,
        )
        assert result.ok is False
        assert "DEEPSEEK_API_KEY" in result.error


# =====================================================================
# Probe caching
# =====================================================================


class TestProbeCache:
    """File-based reachability cache with 5-min TTL."""

    def test_probe_cache_hit_within_ttl(self, tmp_path):
        """Second call within TTL does not invoke run_cmd."""
        call_count = [0]

        def mock_run(cmd, **kw):
            call_count[0] += 1
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout='{"loggedIn": true}',
                stderr="",
            )

        # Call 1: _read_cache (file missing -> exception, no time_fn),
        #         _write_cache (time_fn -> 1000.0)
        # Call 2: _read_cache (time_fn -> 1060.0, 1060-1000=60<300 HIT)
        times = iter([1000.0, 1060.0])

        for _ in range(2):
            probe_backend(
                DEFAULT_BACKEND,
                which_fn=_noop_which,
                run_cmd=mock_run,
                env={},
                cache_dir=tmp_path,
                time_fn=lambda: next(times),
            )
        assert call_count[0] == 1

    def test_probe_cache_miss_after_ttl(self, tmp_path):
        """After 301s, cache is stale -> re-probe."""
        call_count = [0]

        def mock_run(cmd, **kw):
            call_count[0] += 1
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout='{"loggedIn": true}',
                stderr="",
            )

        # Call 1: _write_cache (time_fn -> 1000.0)
        # Call 2: _read_cache (time_fn -> 1301.0, 1301-1000=301>=300 MISS),
        #         _write_cache (time_fn -> 1301.0)
        times = iter([1000.0, 1301.0, 1301.0])

        for _ in range(2):
            probe_backend(
                DEFAULT_BACKEND,
                which_fn=_noop_which,
                run_cmd=mock_run,
                env={},
                cache_dir=tmp_path,
                time_fn=lambda: next(times),
            )
        assert call_count[0] == 2

    def test_probe_failure_not_cached(self, tmp_path):
        """A failed probe is not cached; next call re-probes."""
        call_count = [0]

        def mock_run(cmd, **kw):
            call_count[0] += 1
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout='{"loggedIn": false}',
                stderr="",
            )

        for _ in range(2):
            probe_backend(
                DEFAULT_BACKEND,
                which_fn=_noop_which,
                run_cmd=mock_run,
                env={},
                cache_dir=tmp_path,
                time_fn=lambda: 1000.0,
            )
        assert call_count[0] == 2

    def test_probe_corrupted_cache_treated_as_miss(self, tmp_path):
        """Invalid JSON in cache file -> cache miss, re-probes."""
        cache_file = tmp_path / "backend_probe_cache.json"
        cache_file.write_text("{broken json!!")

        call_count = [0]

        def mock_run(cmd, **kw):
            call_count[0] += 1
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout='{"loggedIn": true}',
                stderr="",
            )

        probe_backend(
            DEFAULT_BACKEND,
            which_fn=_noop_which,
            run_cmd=mock_run,
            env={},
            cache_dir=tmp_path,
            time_fn=lambda: 1000.0,
        )
        assert call_count[0] == 1

    def test_probe_cache_missing_key_treated_as_miss(self, tmp_path):
        """Cache JSON missing 'timestamp' key -> miss, re-probes."""
        cache_file = tmp_path / "backend_probe_cache.json"
        cache_file.write_text('{"ok": true}')

        call_count = [0]

        def mock_run(cmd, **kw):
            call_count[0] += 1
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout='{"loggedIn": true}',
                stderr="",
            )

        probe_backend(
            DEFAULT_BACKEND,
            which_fn=_noop_which,
            run_cmd=mock_run,
            env={},
            cache_dir=tmp_path,
            time_fn=lambda: 1000.0,
        )
        assert call_count[0] == 1

    def test_probe_cache_bad_timestamp_treated_as_miss(self, tmp_path):
        """timestamp is non-numeric -> miss, re-probes."""
        cache_file = tmp_path / "backend_probe_cache.json"
        cache_file.write_text('{"ok": true, "timestamp": "not-a-number"}')

        call_count = [0]

        def mock_run(cmd, **kw):
            call_count[0] += 1
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout='{"loggedIn": true}',
                stderr="",
            )

        probe_backend(
            DEFAULT_BACKEND,
            which_fn=_noop_which,
            run_cmd=mock_run,
            env={},
            cache_dir=tmp_path,
            time_fn=lambda: 1000.0,
        )
        assert call_count[0] == 1

    def test_probe_cache_non_dict_treated_as_miss(self, tmp_path):
        """Cache contains JSON array -> miss, re-probes."""
        cache_file = tmp_path / "backend_probe_cache.json"
        cache_file.write_text("[1, 2, 3]")

        call_count = [0]

        def mock_run(cmd, **kw):
            call_count[0] += 1
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout='{"loggedIn": true}',
                stderr="",
            )

        probe_backend(
            DEFAULT_BACKEND,
            which_fn=_noop_which,
            run_cmd=mock_run,
            env={},
            cache_dir=tmp_path,
            time_fn=lambda: 1000.0,
        )
        assert call_count[0] == 1

    def test_probe_cache_cross_backend_miss(self, tmp_path):
        """Cache from backend A must not satisfy a probe for backend B."""
        call_count = {"a": 0, "b": 0}

        def mock_run_a(cmd, **kw):
            call_count["a"] += 1
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout='{"loggedIn": true}',
                stderr="",
            )

        def mock_run_b(cmd, **kw):
            call_count["b"] += 1
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout='{"loggedIn": true}',
                stderr="",
            )

        backend_a = BackendConfig(
            name="backend-a", type="cli", model="",
        )
        backend_b = BackendConfig(
            name="backend-b", type="cli", model="",
        )

        # Probe backend A -- writes cache with backend="backend-a"
        probe_backend(
            backend_a,
            which_fn=_noop_which,
            run_cmd=mock_run_a,
            env={},
            cache_dir=tmp_path,
            time_fn=lambda: 1000.0,
        )
        assert call_count["a"] == 1

        # Probe backend B within TTL -- must NOT reuse A's cache
        probe_backend(
            backend_b,
            which_fn=_noop_which,
            run_cmd=mock_run_b,
            env={},
            cache_dir=tmp_path,
            time_fn=lambda: 1060.0,
        )
        assert call_count["b"] == 1, (
            "backend B should re-probe, not use backend A's cached result"
        )

    def test_invalidate_probe_cache(self, tmp_path):
        """invalidate_probe_cache removes the cache file."""
        cache_file = tmp_path / "backend_probe_cache.json"
        cache_file.write_text('{"ok": true, "timestamp": 1000}')
        assert cache_file.exists()

        invalidate_probe_cache(cache_dir=tmp_path)
        assert not cache_file.exists()


# =====================================================================
# Real-API opt-in
# =====================================================================


class TestRealApi:
    """Real-API smoke test (skipped unless opted in)."""

    @pytest.mark.real_api
    def test_real_probe(self):
        """Calls probe_backend(DEFAULT_BACKEND) with real defaults."""
        result = probe_backend(DEFAULT_BACKEND)
        assert isinstance(result, ProbeResult)
