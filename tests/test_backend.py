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
from pathlib import Path
from unittest.mock import patch

import pytest

from code_forge.backend import (
    DEFAULT_BACKEND,
    PROTECTED_HEADER_KEYS,
    BackendConfig,
    ProbeResult,
    _parse_backend_entry,
    invalidate_probe_cache,
    load_backend_configs,
    probe_backend,
    resolve_auth_timeout,
    resolve_backend,
)
from code_forge.errors import CliError


# -- Helpers ----------------------------------------------------------


def _api_entry(**overrides):
    """Build a minimal valid api backend entry (dict-schema).

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
    """Build a minimal valid cli backend entry (dict-schema).

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


def _vertex_entry(**overrides):
    """Build a minimal valid vertex (api/OAuth) backend entry.

    Vertex backends carry no api_key_env; auth is OAuth2/ADC.
    """
    base = {
        "type": "api",
        "format": "vertex",
        "project_id": "my-gcp-project",
        "model": "claude-sonnet-4-6",
    }
    base.update(overrides)
    return base


def _as_api_backends(entry=None, name="deepseek"):
    """Wrap an api entry in the dict-schema backends mapping."""
    if entry is None:
        entry = _api_entry()
    return {"backends": {name: entry}}


def _as_cli_backends(entry=None, name="claude-sub"):
    """Wrap a cli entry in the dict-schema backends mapping."""
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

    def test_backendconfig_api_missing_credential_raises(self):
        entry = _api_entry()
        del entry["api_key_env"]
        with pytest.raises(CliError, match="missing credential field"):
            load_backend_configs(_as_api_backends(entry))

    def test_backendconfig_api_key_file_accepted(self, tmp_path):
        """api_key_file instead of api_key_env parses successfully."""
        kf = tmp_path / "key.txt"
        kf.write_text("sk-test\n")
        entry = _api_entry()
        del entry["api_key_env"]
        entry["api_key_file"] = str(kf)
        cfgs = load_backend_configs(_as_api_backends(entry))
        assert cfgs[0].api_key_file == str(kf)
        assert cfgs[0].api_key_env is None

    def test_backendconfig_api_both_key_fields_rejected(self, tmp_path):
        """api_key_env + api_key_file together -> CliError."""
        kf = tmp_path / "key.txt"
        kf.write_text("sk-test\n")
        entry = _api_entry(api_key_file=str(kf))
        with pytest.raises(CliError, match="not both"):
            load_backend_configs(_as_api_backends(entry))

    def test_backendconfig_expanduser_api_key_file(self):
        """~/key.txt is expanded at parse time."""
        entry = _api_entry()
        del entry["api_key_env"]
        entry["api_key_file"] = "~/key.txt"
        cfgs = load_backend_configs(_as_api_backends(entry))
        assert "~" not in cfgs[0].api_key_file

    def test_backendconfig_expanduser_credentials_path(self):
        """~/creds.json is expanded at parse time."""
        entry = _vertex_entry(credentials_path="~/creds.json")
        cfgs = load_backend_configs(
            {"backends": {"vtx": entry}}
        )
        assert "~" not in cfgs[0].credentials_path

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
        """backends as list -> CliError (: dict required)."""
        with pytest.raises(CliError, match="backends must be a dict"):
            load_backend_configs({"backends": [_api_entry()]})

    def test_load_backend_configs_non_dict_entry_raises(self):
        """backends dict value that is not a dict -> CliError."""
        with pytest.raises(CliError):
            load_backend_configs({"backends": {"bad": "not-a-dict"}})

    def test_load_backend_configs_multiple_defaults_raises(self):
        """Two backends with default: true -> CliError."""
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
        """max_tokens=8192 in entry is honoured."""
        entry = _api_entry(max_tokens=8192)
        cfgs = load_backend_configs(_as_api_backends(entry))
        assert cfgs[0].max_tokens == 8192

    def test_load_backend_configs_dict_schema(self):
        """dict-based backends block parses correctly; name injected from key."""
        data = {
            "backends": {
                "mimo": {
                    "type": "api",
                    "format": "anthropic",
                    "base_url": "https://api.mimo.com",
                    "api_key_env": "MIMO_API_KEY",
                    "model": "mimo-v2.5-pro",
                },
            }
        }
        cfgs = load_backend_configs(data)
        assert len(cfgs) == 1
        cfg = cfgs[0]
        assert cfg.name == "mimo"
        assert cfg.format == "anthropic"
        assert cfg.model == "mimo-v2.5-pro"
        assert cfg.api_key_env == "MIMO_API_KEY"

    def test_load_backend_configs_non_dict_raises(self):
        """Non-dict backends value (list) raises CliError."""
        with pytest.raises(CliError, match="backends must be a dict"):
            load_backend_configs({"backends": [_api_entry()]})

    def test_load_backend_configs_entry_not_dict_raises(self):
        """Dict entry whose value is not a dict raises CliError."""
        with pytest.raises(CliError):
            load_backend_configs({"backends": {"bad": "not-a-dict"}})

    def test_multiple_defaults_raises(self):
        """Two entries with default=True raises CliError naming both backends."""
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
        """Exactly one default=True entry is accepted without error."""
        entry = _api_entry(default=True)
        cfgs = load_backend_configs(_as_api_backends(entry))
        assert len(cfgs) == 1
        assert cfgs[0].default is True

    def test_no_default_returns_first(self):
        """No default=True entry -> resolve_backend returns configs[0]."""
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

        dummy_cfg = BackendConfig(
            name="test-cli", type="cli", model="",
            format="", base_url="", api_key_env="",
            command="", default=False, max_tokens=0,
        )
        with pytest.raises(CliError, match="Configure a review backend"):
            resolve_outlet(
                env={},
                gate_yaml_path=None,
                configs=[dummy_cfg],
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


class TestProbeApiKeyFile:
    """probe_backend for api backend with api_key_file credential."""

    def _make_cfg(self, tmp_path, mode=0o600):
        kf = tmp_path / "key.txt"
        kf.write_text("sk-test\n")
        kf.chmod(mode)
        entry = _api_entry()
        del entry["api_key_env"]
        entry["api_key_file"] = str(kf)
        return load_backend_configs(_as_api_backends(entry))[0]

    def test_probe_file_key_ok(self, tmp_path):
        """T2a: key file exists, 0600 -> ok=True."""
        cfg = self._make_cfg(tmp_path)
        result = probe_backend(
            cfg,
            which_fn=_noop_which,
            run_cmd=_noop_run,
            env={},
            cache_dir=tmp_path / "cache",
            time_fn=lambda: 1000.0,
        )
        assert result.ok is True

    def test_probe_file_key_missing(self, tmp_path):
        """Key file does not exist -> ok=False."""
        entry = _api_entry()
        del entry["api_key_env"]
        entry["api_key_file"] = str(tmp_path / "gone.txt")
        cfg = load_backend_configs(_as_api_backends(entry))[0]
        result = probe_backend(
            cfg,
            which_fn=_noop_which,
            run_cmd=_noop_run,
            env={},
            cache_dir=tmp_path / "cache",
            time_fn=lambda: 1000.0,
        )
        assert result.ok is False
        assert "not found" in result.error

    def test_probe_file_key_world_readable(self, tmp_path):
        """T2d: key file 0644 -> refused with mode in error."""
        cfg = self._make_cfg(tmp_path, mode=0o644)
        result = probe_backend(
            cfg,
            which_fn=_noop_which,
            run_cmd=_noop_run,
            env={},
            cache_dir=tmp_path / "cache",
            time_fn=lambda: 1000.0,
        )
        assert result.ok is False
        assert "readable" in result.error
        assert "644" in result.error


class TestProbeVertex:
    """probe_backend for vertex (api/OAuth) backend: no api_key_env, no network.

    Mirrors _invoke_vertex credential resolution -- explicit service-account
    file, GOOGLE_APPLICATION_CREDENTIALS, or a gcloud application-default file.
    """

    def test_probe_vertex_credentials_path_present(self, tmp_path):
        """credentials_path points at an existing file -> ok=True."""
        sa = tmp_path / "sa.json"
        sa.write_text("{}")
        cfg = load_backend_configs(
            _as_api_backends(
                _vertex_entry(credentials_path=str(sa)), name="vertex-claude"
            )
        )[0]
        result = probe_backend(
            cfg, which_fn=_noop_which, run_cmd=_noop_run,
            env={}, cache_dir=tmp_path, time_fn=lambda: 1000.0,
        )
        assert result.ok is True

    def test_probe_vertex_credentials_path_missing(self, tmp_path):
        """credentials_path set but file absent -> ok=False, error names path."""
        missing = tmp_path / "nope.json"
        cfg = load_backend_configs(
            _as_api_backends(
                _vertex_entry(credentials_path=str(missing)),
                name="vertex-claude",
            )
        )[0]
        result = probe_backend(
            cfg, which_fn=_noop_which, run_cmd=_noop_run,
            env={}, cache_dir=tmp_path, time_fn=lambda: 1000.0,
        )
        assert result.ok is False
        assert "nope.json" in result.error

    def test_probe_vertex_adc_env(self, tmp_path):
        """No credentials_path; GOOGLE_APPLICATION_CREDENTIALS set -> ok=True."""
        cfg = load_backend_configs(
            _as_api_backends(_vertex_entry(), name="vertex-claude")
        )[0]
        result = probe_backend(
            cfg, which_fn=_noop_which, run_cmd=_noop_run,
            env={"GOOGLE_APPLICATION_CREDENTIALS": "/tmp/adc.json"},
            cache_dir=tmp_path, time_fn=lambda: 1000.0,
        )
        assert result.ok is True

    def test_probe_vertex_gcloud_adc_file(self, tmp_path, monkeypatch):
        """No path/env, gcloud application-default file present -> ok=True."""
        fake_home = tmp_path / "home"
        gcloud_dir = fake_home / ".config" / "gcloud"
        gcloud_dir.mkdir(parents=True)
        (gcloud_dir / "application_default_credentials.json").write_text("{}")
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        cfg = load_backend_configs(
            _as_api_backends(_vertex_entry(), name="vertex-claude")
        )[0]
        result = probe_backend(
            cfg, which_fn=_noop_which, run_cmd=_noop_run,
            env={}, cache_dir=tmp_path, time_fn=lambda: 1000.0,
        )
        assert result.ok is True

    def test_probe_vertex_no_credentials(self, tmp_path, monkeypatch):
        """No path, no env, no gcloud file -> ok=False, error names GCP creds."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "empty-home")
        cfg = load_backend_configs(
            _as_api_backends(_vertex_entry(), name="vertex-claude")
        )[0]
        result = probe_backend(
            cfg, which_fn=_noop_which, run_cmd=_noop_run,
            env={}, cache_dir=tmp_path, time_fn=lambda: 1000.0,
        )
        assert result.ok is False
        assert "GCP credentials" in result.error

    def test_probe_vertex_result_cached(self, tmp_path):
        """Successful vertex probe is cached; 2nd call within TTL reuses it."""
        sa = tmp_path / "sa.json"
        sa.write_text("{}")
        cfg = load_backend_configs(
            _as_api_backends(
                _vertex_entry(credentials_path=str(sa)), name="vertex-claude"
            )
        )[0]
        first = probe_backend(
            cfg, which_fn=_noop_which, run_cmd=_noop_run,
            env={}, cache_dir=tmp_path, time_fn=lambda: 1000.0,
        )
        sa.unlink()  # an uncached re-probe would now return ok=False
        second = probe_backend(
            cfg, which_fn=_noop_which, run_cmd=_noop_run,
            env={}, cache_dir=tmp_path, time_fn=lambda: 1100.0,
        )
        assert first.ok is True
        assert second.ok is True  # proves the cached ok result was reused

    def test_probe_vertex_empty_credentials_path_falls_through(self, tmp_path):
        """credentials_path='' is falsy -> falls through to ADC, matching invoke."""
        cfg = load_backend_configs(
            _as_api_backends(
                _vertex_entry(credentials_path=""), name="vertex-claude"
            )
        )[0]
        result = probe_backend(
            cfg, which_fn=_noop_which, run_cmd=_noop_run,
            env={"GOOGLE_APPLICATION_CREDENTIALS": "/tmp/adc.json"},
            cache_dir=tmp_path, time_fn=lambda: 1000.0,
        )
        assert result.ok is True


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
        """Cache in one dir must not satisfy a probe using a different dir.

        Explicit-named cli backends bypass the probe entirely, so
        they cannot be used to test cache isolation.  Instead, use
        DEFAULT_BACKEND (name="session-default") with two separate cache
        directories to verify cache entries are dir-scoped, not shared.
        """
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

        cache_dir_a = tmp_path / "cache_a"
        cache_dir_b = tmp_path / "cache_b"

        # Probe with cache dir A -- writes cache there
        probe_backend(
            DEFAULT_BACKEND,
            which_fn=_noop_which,
            run_cmd=mock_run_a,
            env={},
            cache_dir=cache_dir_a,
            time_fn=lambda: 1000.0,
        )
        assert call_count["a"] == 1

        # Probe with cache dir B within TTL -- must NOT reuse dir A's cache
        probe_backend(
            DEFAULT_BACKEND,
            which_fn=_noop_which,
            run_cmd=mock_run_b,
            env={},
            cache_dir=cache_dir_b,
            time_fn=lambda: 1060.0,
        )
        assert call_count["b"] == 1, (
            "probe with separate cache dir must re-probe, not reuse other dir's cache"
        )

    def test_invalidate_probe_cache(self, tmp_path):
        """invalidate_probe_cache removes the cache file."""
        cache_file = tmp_path / "backend_probe_cache.json"
        cache_file.write_text('{"ok": true, "timestamp": 1000}')
        assert cache_file.exists()

        invalidate_probe_cache(cache_dir=tmp_path)
        assert not cache_file.exists()


# =====================================================================
# Probe bypass for explicitly-configured cli backends
# =====================================================================


class TestProbeBypass:
    """probe_backend bypasses reachability probe for named cli backends."""

    def test_explicit_cli_backend_bypasses_probe(self, tmp_path):
        """Named cli backend: which_fn and run_cmd must never be called."""
        backend = BackendConfig(
            name="vertex-claude",
            type="cli",
            model="claude-sonnet-4-6",
        )

        def should_not_probe(name):
            raise AssertionError("which_fn should not be called for explicit cli backend")

        def should_not_run(*_a, **_kw):
            raise AssertionError("run_cmd should not be called for explicit cli backend")

        result = probe_backend(
            backend,
            which_fn=should_not_probe,
            run_cmd=should_not_run,
            env={},
            cache_dir=tmp_path,
            time_fn=lambda: 1000.0,
        )
        assert result.ok is True

    def test_default_backend_still_probes(self, tmp_path):
        """DEFAULT_BACKEND (name="session-default") still calls _probe_cli."""
        called = [False]

        def mock_run(cmd, **kw):
            called[0] = True
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout='{"loggedIn": true}',
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
        assert called[0] is True, "run_cmd must be called for DEFAULT_BACKEND"

    def test_api_backend_not_affected_by_bypass(self, tmp_path):
        """API backends are unaffected: probe checks api_key_env presence."""
        backend = BackendConfig(
            name="mimo",
            type="api",
            format="openai",
            base_url="https://api.mimo.com",
            api_key_env="MIMO_KEY",
            model="mimo-v1",
        )

        result = probe_backend(
            backend,
            which_fn=_noop_which,
            run_cmd=_noop_run,
            env={"MIMO_KEY": "test-key"},
            cache_dir=tmp_path,
            time_fn=lambda: 1000.0,
        )
        assert result.ok is True


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


# =====================================================================
# Fixture-based backend config parsing
# =====================================================================


class TestFixtureBackends:
    """Validate 5-backend gate fixture loads and parses correctly."""

    @pytest.fixture
    def configs(self):
        import yaml

        fixture = Path(__file__).parent / "fixtures" / "gate_5backends.yaml"
        with open(fixture) as f:
            data = yaml.safe_load(f)
        return load_backend_configs(data)

    def test_load_5_backends_from_fixture(self, configs):
        """Fixture file loads exactly 5 backend configs."""
        assert len(configs) == 5

    def test_fixture_backend_names(self, configs):
        """All 5 expected backend names are present."""
        names = {c.name for c in configs}
        assert names == {"mimo", "deepseek", "kimi", "glm", "minimax"}

    def test_fixture_backend_formats(self, configs):
        """Each backend has the correct format (anthropic or openai)."""
        fmt = {c.name: c.format for c in configs}
        assert fmt["mimo"] == "anthropic"
        assert fmt["deepseek"] == "openai"
        assert fmt["kimi"] == "anthropic"
        assert fmt["glm"] == "openai"
        assert fmt["minimax"] == "anthropic"

    def test_fixture_default_is_mimo(self, configs):
        """Exactly one default backend, and it is mimo."""
        defaults = [c for c in configs if c.default]
        assert len(defaults) == 1
        assert defaults[0].name == "mimo"


# -- TestVertexBackendParsing ----------------------------------------------


class TestVertexBackendParsing:
    """Vertex format (api/vertex) parsing and validation."""

    def _entry(self, **kwargs):
        base = {
            "name": "vtx", "type": "api", "format": "vertex",
            "model": "claude-sonnet-4-6", "project_id": "my-project",
        }
        base.update(kwargs)
        return base

    def test_vertex_parses_with_project_id(self):
        cfg = _parse_backend_entry(self._entry())
        assert cfg.format == "vertex"
        assert cfg.project_id == "my-project"
        assert cfg.region == "global"
        assert cfg.base_url is None
        assert cfg.api_key_env is None

    def test_vertex_with_explicit_region(self):
        cfg = _parse_backend_entry(self._entry(region="us-east5"))
        assert cfg.region == "us-east5"

    def test_vertex_with_credentials_path(self):
        cfg = _parse_backend_entry(self._entry(credentials_path="/path/to/sa.json"))
        assert cfg.credentials_path == "/path/to/sa.json"

    def test_vertex_missing_project_id_raises(self):
        entry = {
            "name": "vtx", "type": "api", "format": "vertex",
            "model": "claude-sonnet-4-6",
        }
        with pytest.raises(CliError, match="missing required field 'project_id'"):
            _parse_backend_entry(entry)

    def test_vertex_no_base_url_needed(self):
        """vertex format works without base_url."""
        cfg = _parse_backend_entry(self._entry())
        assert cfg.base_url is None

    def test_vertex_no_api_key_env_needed(self):
        """vertex format works without api_key_env."""
        cfg = _parse_backend_entry(self._entry())
        assert cfg.api_key_env is None

    def test_openai_still_requires_base_url(self):
        """Regression: openai format still requires base_url after vertex branch."""
        entry = {
            "name": "bad", "type": "api", "format": "openai",
            "model": "gpt-4", "api_key_env": "OPENAI_KEY",
        }
        with pytest.raises(CliError, match="missing required field 'base_url'"):
            _parse_backend_entry(entry)


# -- Wave 1: provider-aware fields + cli env fields -------------------


class TestBackendConfigProviderFields:
    """Verify new BackendConfig fields have correct sentinel defaults."""

    def _minimal_api(self, **kw):
        return BackendConfig(
            name="test", type="api", model="m",
            format="openai", base_url="http://x", api_key_env="K",
            **kw,
        )

    def test_temperature_default_sentinel(self):
        cfg = self._minimal_api()
        assert cfg.temperature == -1.0

    def test_max_completion_tokens_default_zero(self):
        cfg = self._minimal_api()
        assert cfg.max_completion_tokens == 0

    def test_thinking_type_default_empty(self):
        cfg = self._minimal_api()
        assert cfg.thinking_type == ""

    def test_thinking_budget_default_zero(self):
        cfg = self._minimal_api()
        assert cfg.thinking_budget == 0

    def test_reasoning_effort_default_empty(self):
        cfg = self._minimal_api()
        assert cfg.reasoning_effort == ""

    def test_stream_default_false(self):
        cfg = self._minimal_api()
        assert cfg.stream is False

    def test_timeout_s_default_zero(self):
        cfg = self._minimal_api()
        assert cfg.timeout_s == 0

    def test_outcap_key_default_empty(self):
        cfg = self._minimal_api()
        assert cfg.outcap_key == ""

    def test_params_default_none(self):
        cfg = self._minimal_api()
        assert cfg.params is None

    def test_env_unset_default_empty_tuple(self):
        cfg = self._minimal_api()
        assert cfg.env_unset == ()

    def test_env_set_default_empty_tuple(self):
        cfg = self._minimal_api()
        assert cfg.env_set == ()

    def test_hashable_with_params_dict(self):
        """compare=False on params keeps frozen dataclass hashable."""
        cfg = self._minimal_api(params={"top_p": 0.9})
        h = hash(cfg)
        assert isinstance(h, int)

    def test_hashable_two_configs_differ_only_by_params(self):
        """Configs differing only by params have equal hash (compare=False)."""
        a = self._minimal_api(params={"a": 1})
        b = self._minimal_api(params={"b": 2})
        assert a == b
        assert hash(a) == hash(b)

    def test_default_backend_unchanged(self):
        """DEFAULT_BACKEND constructs without error with new fields at defaults."""
        assert DEFAULT_BACKEND.temperature == -1.0
        assert DEFAULT_BACKEND.max_completion_tokens == 0
        assert DEFAULT_BACKEND.params is None
        assert DEFAULT_BACKEND.env_unset == ()


# -- Wave 2: parse validation for new fields --------------------------

VALID_THINKING_TYPES = {"enabled", "adaptive", "disabled"}

PROTECTED_KEYS = {
    "model", "messages", "stream", "anthropic_version",
    "temperature", "thinking", "reasoning_effort",
    "max_completion_tokens", "max_tokens",
}


class TestParseProviderFields:
    """Parse validation for ADR-0005 typed fields on api backends."""

    def _api_entry(self, **kw):
        base = {
            "name": "ds", "type": "api", "format": "openai",
            "model": "deepseek-r1", "base_url": "http://x",
            "api_key_env": "K",
        }
        base.update(kw)
        return base

    def _vertex_entry(self, **kw):
        base = {
            "name": "v", "type": "api", "format": "vertex",
            "model": "claude-4", "project_id": "proj-1",
        }
        base.update(kw)
        return base

    # -- typed fields parsed correctly --

    def test_temperature_stored(self):
        cfg = _parse_backend_entry(self._api_entry(temperature=0.7))
        assert cfg.temperature == 0.7

    def test_temperature_absent_sentinel(self):
        cfg = _parse_backend_entry(self._api_entry())
        assert cfg.temperature == -1.0

    def test_max_completion_tokens_stored(self):
        cfg = _parse_backend_entry(
            self._api_entry(max_completion_tokens=32768)
        )
        assert cfg.max_completion_tokens == 32768

    def test_thinking_type_stored(self):
        cfg = _parse_backend_entry(
            self._api_entry(thinking_type="enabled")
        )
        assert cfg.thinking_type == "enabled"

    def test_thinking_type_invalid_rejected(self):
        with pytest.raises(CliError, match="thinking_type"):
            _parse_backend_entry(
                self._api_entry(thinking_type="bogus")
            )

    def test_thinking_budget_stored(self):
        cfg = _parse_backend_entry(
            self._api_entry(thinking_budget=16000)
        )
        assert cfg.thinking_budget == 16000

    def test_reasoning_effort_stored(self):
        cfg = _parse_backend_entry(
            self._api_entry(reasoning_effort="high")
        )
        assert cfg.reasoning_effort == "high"

    def test_stream_stored(self):
        cfg = _parse_backend_entry(self._api_entry(stream=True))
        assert cfg.stream is True

    def test_timeout_s_stored(self):
        cfg = _parse_backend_entry(self._api_entry(timeout_s=1800))
        assert cfg.timeout_s == 1800

    # -- outcap_key validation --

    def test_outcap_key_max_tokens(self):
        cfg = _parse_backend_entry(
            self._api_entry(outcap_key="max_tokens")
        )
        assert cfg.outcap_key == "max_tokens"

    def test_outcap_key_max_completion_tokens(self):
        cfg = _parse_backend_entry(
            self._api_entry(outcap_key="max_completion_tokens")
        )
        assert cfg.outcap_key == "max_completion_tokens"

    def test_outcap_key_empty_uses_default(self):
        cfg = _parse_backend_entry(self._api_entry())
        assert cfg.outcap_key == ""

    def test_outcap_key_null_treated_as_empty(self):
        cfg = _parse_backend_entry(self._api_entry(outcap_key=None))
        assert cfg.outcap_key == ""

    @pytest.mark.parametrize("field", [
        "temperature", "max_completion_tokens", "thinking_type",
        "thinking_budget", "reasoning_effort", "stream",
        "outcap_key", "output_ceiling", "params", "headers",
    ])
    def test_null_means_absent_for_every_api_only_field(self, field):
        """One rule for null across _API_ONLY_FIELDS: a null-valued
        field parses exactly like an absent one. Nothing stores None
        into a typed attribute and nothing crashes."""
        absent = _parse_backend_entry(self._api_entry())
        nulled = _parse_backend_entry(self._api_entry(**{field: None}))
        assert getattr(absent, field) == getattr(nulled, field), (
            "field %s: absent=%r null=%r" % (
                field, getattr(absent, field), getattr(nulled, field),
            )
        )

    def test_null_cli_side_is_not_a_rejection(self):
        """A null-valued api-only key on a cli backend parses as absent
        instead of tripping the key-presence rejection."""
        from code_forge.backend import load_backend_configs
        entry = _cli_entry()
        entry["headers"] = None
        cfgs = load_backend_configs({"backends": {"c": entry}})
        assert len(cfgs) == 1

    def test_outcap_key_invalid_rejected(self):
        with pytest.raises(CliError, match="outcap_key"):
            _parse_backend_entry(
                self._api_entry(outcap_key="bad_key")
            )

    # -- cap validation --

    def test_both_caps_zero_rejected(self):
        with pytest.raises(CliError, match="output token cap"):
            _parse_backend_entry(
                self._api_entry(
                    max_completion_tokens=0, max_tokens=0,
                )
            )

    def test_cap_one_positive_accepted(self):
        cfg = _parse_backend_entry(
            self._api_entry(max_completion_tokens=0, max_tokens=8192)
        )
        assert cfg.max_tokens == 8192

    # -- params: protected key rejection --

    def test_params_benign_key_stored(self):
        cfg = _parse_backend_entry(
            self._api_entry(params={"top_p": 0.9})
        )
        assert cfg.params == {"top_p": 0.9}

    def test_params_nested_stored_verbatim(self):
        rf = {"type": "json_object"}
        cfg = _parse_backend_entry(
            self._api_entry(params={"response_format": rf})
        )
        assert cfg.params["response_format"] == rf

    def test_params_protected_key_rejected(self):
        for key in PROTECTED_KEYS:
            with pytest.raises(CliError, match=key):
                _parse_backend_entry(
                    self._api_entry(params={key: "x"})
                )

    def test_two_protected_params_always_name_the_same_one(self):
        """The message must not depend on the interpreter's hash seed.

        Measured over twelve fresh interpreters: iterating the protected
        frozenset named 'max_tokens', 'model' or 'stream' for one config,
        so a user who fixed the key the error named could be shown a
        different one on the next run and read it as the fix failing.
        Hash randomisation is per-process, so this is checked with a
        subprocess rather than a loop -- an in-process loop reports one
        stable answer and proves nothing.
        """
        import code_forge
        import os
        import pathlib
        import subprocess
        import sys

        prog = (
            "from code_forge.backend import check_params\n"
            "from code_forge.errors import CliError\n"
            "try:\n"
            "    check_params(\n"
            "        {'model': 'a', 'stream': True, 'max_tokens': 1},\n"
            "        'b', CliError)\n"
            "except CliError as exc:\n"
            "    print(exc)\n"
        )
        # Derived from the imported package, not from cwd or a hardcoded
        # path: an editable install can resolve code_forge to a different
        # checkout than the one under test, and a subprocess that picked
        # its own would be measuring the wrong tree while looking fine.
        env = dict(os.environ)
        env["PYTHONPATH"] = str(
            pathlib.Path(code_forge.__file__).parent.parent
        )
        seen = set()
        for _ in range(8):
            done = subprocess.run(
                [sys.executable, "-c", prog],
                capture_output=True, text=True, check=True, env=env,
            )
            seen.add(done.stdout.strip())

        assert len(seen) == 1, (
            "the reported key varies between runs: %s" % sorted(seen)
        )
        assert "max_tokens" in seen.pop(), (
            "expected the alphabetically first protected key present"
        )

    # -- headers: grammar --

    def test_headers_benign_name_stored(self):
        cfg = _parse_backend_entry(
            self._api_entry(headers={"x-omniroute-compression": "off"})
        )
        assert cfg.headers == {"x-omniroute-compression": "off"}

    def test_headers_value_may_hold_inner_blanks(self):
        """A field value is words separated by blanks, not one word.

        The grammar refuses blanks only at the ends, and a rule written
        as "no whitespace" would take a perfectly ordinary value with it.
        """
        cfg = _parse_backend_entry(
            self._api_entry(headers={"x-note": "two words"})
        )
        assert cfg.headers == {"x-note": "two words"}

    def test_headers_malformed_name_rejected(self):
        """One rule for two different failures, because urllib has a gap.

        Measured 2026-08-07 against a socket that recorded the bytes,
        CPython 3.14. Six of these nine do get stopped, but only on the
        call: five as a bare ValueError naming neither the backend nor
        the header, and the non-ASCII one as a UnicodeEncodeError from
        the ascii codec -- all of them halfway through a review, after
        the diff is built and the credential is loaded. Names are
        encoded ascii while values are encoded latin-1, which is why
        the same 0xE9 that rides through a value dies in a name.

        The other three are sent: 'x-foo ' leaves as 'X-Foo : v',
        'x foo' as 'X Foo: v', and 'x\\tfoo' as 'X\\tFoo: v' with the tab
        raw on the wire. All three are malformed under RFC 7230, which
        allows no blank of any kind inside a field-name or before the
        colon. What a far side does with a header it cannot parse is
        its own business, and two hops need not agree. For those three
        this check is not an earlier error, it is the only one.
        """
        for spelling in ("", "   ", " x-foo", "x-foo ", "x foo", "x\tfoo",
                         "x-foo\r\nX-Evil", "x:foo", "x-foo\xe9"):
            with pytest.raises(CliError, match="not a valid HTTP field name"):
                _parse_backend_entry(
                    self._api_entry(headers={spelling: "v"})
                )

    def test_headers_malformed_value_rejected(self):
        """CR and LF need no rule of their own; the grammar has them.

        Same measurement, value side. urllib does refuse CR and LF, so
        no configured value splits a request today -- but it refuses on
        the call, with a ValueError naming neither backend nor header.

        The other four it sends verbatim, recorded off the socket:
        'X-Opt: nul\\x00byte' with the NUL intact, the obs-text byte as
        raw 0xE9, and leading and trailing blanks preserved. Whether a
        given far side then strips, keeps, or rejects those was not
        measured and is not forge's to assume, which is the argument
        for not sending them.
        """
        # The last is obs-text (0xE9), which RFC 7230 permits in a field
        # value and this deliberately does not -- these carry gateway
        # options, which are ASCII. Spelled as an escape so the source
        # stays ASCII while the value under test does not.
        for value in ("a\r\nX-Evil: 1", "a\nb", "a\r", " leading",
                      "trailing ", "nul\x00byte", "caf\xe9"):
            with pytest.raises(CliError,
                               match="not a valid HTTP field value"):
                _parse_backend_entry(
                    self._api_entry(headers={"x-foo": value})
                )

    def test_headers_non_string_value_rejected(self):
        """yaml types a bare number, and urllib cannot send one.

        Left to the wire this fails on the call, mid-review, with an
        error naming neither the backend nor the header.
        """
        with pytest.raises(CliError, match="string"):
            _parse_backend_entry(
                self._api_entry(headers={"x-retries": 3})
            )

    def test_headers_non_string_name_rejected(self):
        """The key side of the same yaml problem, and its own branch.

        'timeout: 30' under headers gives a str key, but '30: x' gives an
        int one -- yaml types bare scalars on both sides of the colon.
        An int key reaches .lower() and raises AttributeError rather than
        anything an operator can act on.
        """
        with pytest.raises(CliError, match="string"):
            _parse_backend_entry(
                self._api_entry(headers={42: "v"})
            )

    def test_headers_empty_is_not_an_error(self):
        """Absent and empty are different, and neither is a failure.

        A block someone commented the contents out of should behave as
        no headers at all, not as a config error.
        """
        cfg = _parse_backend_entry(self._api_entry(headers={}))
        assert cfg.headers == {}

    def test_headers_empty_value_is_allowed(self):
        """RFC 7230 permits an empty field-value, and so does this.

        The trailing '?' on _HEADER_VALUE_RE is what allows it. Some
        gateways read the presence of a header as the signal and ignore
        what it says, so refusing this would refuse a real usage for no
        stated reason -- and it is the one empty string the grammar
        deliberately accepts, next to a name where it deliberately does
        not.
        """
        cfg = _parse_backend_entry(
            self._api_entry(headers={"x-debug": ""})
        )
        assert cfg.headers == {"x-debug": ""}

    def test_headers_not_a_mapping_rejected(self):
        with pytest.raises(CliError, match="mapping"):
            _parse_backend_entry(
                self._api_entry(headers=["x-foo: bar"])
            )

    # -- headers: permission --

    def test_headers_protected_name_rejected(self):
        for name in PROTECTED_HEADER_KEYS:
            with pytest.raises(CliError, match="forge controls"):
                _parse_backend_entry(
                    self._api_entry(headers={name: "x"})
                )

    def test_headers_protected_name_rejected_whatever_the_case(self):
        """HTTP does not distinguish these spellings and a dict does.

        A check that only knows the canonical form lets the others past,
        and each one then sits in the outgoing dict NEXT TO the real
        header rather than replacing it -- two Authorization lines on the
        wire, with the far side choosing which to honour.
        """
        for spelling in ("Authorization", "AUTHORIZATION", "AuThOrIzAtIoN",
                         "X-API-Key", "Content-Type", "Host",
                         "Content-Length", "Transfer-Encoding"):
            with pytest.raises(CliError, match="forge controls"):
                _parse_backend_entry(
                    self._api_entry(headers={spelling: "x"})
                )

    def test_the_forbidden_list_did_not_quietly_shrink(self):
        """The check above iterates this set, so it cannot see a deletion.

        Remove a name from PROTECTED_HEADER_KEYS and that loop simply
        stops asking about it, the same blind spot the cli-field loop
        has. Measured: of the names in the set, only seven appear as a
        literal anywhere in this suite, so the rest are pinned by
        nothing. Spelled out here so a deletion has somewhere to fail.

        Only the WHATWG Fetch forbidden request-header names are
        listed. The ones forge adds on its own account -- the
        credential and the per-format framing -- are pinned by the
        case-spelling and smuggling tests, which assert something those
        names MEAN rather than only that they are present.

        The send-time check is no backstop for these: it compares
        against the headers forge itself sends, and forge sends none of
        them. This set is the only thing between a config and an Origin
        or a Cookie on forge's own outbound requests.
        """
        whatwg = {
            "accept-charset", "accept-encoding",
            "access-control-request-headers",
            "access-control-request-method", "connection", "content-length",
            "cookie", "cookie2", "date", "dnt", "expect", "host",
            "keep-alive", "origin", "referer", "set-cookie", "te",
            "trailer", "transfer-encoding", "upgrade", "via",
            "x-http-method", "x-http-method-override", "x-method-override",
        }
        missing = sorted(whatwg - set(PROTECTED_HEADER_KEYS))
        assert not missing, (
            "no longer refused at config load: %s" % ", ".join(missing)
        )

    def test_headers_protected_prefixes_rejected(self):
        """Proxy- carries a credential and Sec- is reserved for new ones."""
        for name in ("proxy-authorization", "Proxy-Connection",
                     "sec-fetch-mode", "Sec-Anything-Minted-Later"):
            with pytest.raises(CliError, match="forge controls"):
                _parse_backend_entry(
                    self._api_entry(headers={name: "x"})
                )

    def test_headers_framing_names_are_a_smuggling_primitive(self):
        """Why the framing names are on the list, stated as a test.

        Measured 2026-08-07 against a socket that recorded the bytes: a
        configured 'Content-Length: 3' REPLACED urllib's own
        'Content-Length: 13'. The server reads three bytes of a
        thirteen-byte body and the remaining ten stay in the connection
        to be parsed as the start of the next request.
        'Transfer-Encoding: chunked' dropped the Content-Length line
        entirely while the body was still sent unchunked, and a
        configured Host replaced the real one outright.
        """
        for name in ("content-length", "transfer-encoding", "host"):
            with pytest.raises(CliError, match="forge controls"):
                _parse_backend_entry(
                    self._api_entry(headers={name: "3"})
                )

    def test_a_protected_name_wearing_a_blank_is_still_refused(self):
        """The permission check does no stripping, and need not.

        'authorization ' is protected in intent and malformed in fact.
        It is refused as malformed, which is the message an operator
        sees, and that is fine -- what matters is that it is refused.

        The dependency here is on the grammar check EXISTING, not on it
        running first. Folded but unstripped, this name does not match
        anything in the protected set, so with the grammar check gone it
        would sail through as some header of its own and reach the wire
        as 'authorization : x'. Deleting that check is what breaks this
        test; reordering the two is not, which was measured rather than
        assumed.
        """
        with pytest.raises(CliError, match="not a valid HTTP field name"):
            _parse_backend_entry(
                self._api_entry(headers={"authorization ": "x"})
            )

    def test_two_spellings_of_one_name_are_refused(self):
        """Both pass every other check, and collide on the wire anyway.

        Neither name is protected and both are well-formed, so grammar
        and permission let them through individually. HTTP does not
        distinguish them. Measured off a socket, through the same
        Request(headers=...) form the call sites use: urllib folds the
        pair into one line, keeps the value written last, and titlecases
        the name -- 'x-note: one' then 'X-Note: two' sends
        'X-Note: two', and reversing the two yaml lines sends
        'X-Note: one'. A config that says two things and sends one of
        them, chosen by line order, is worth a parse error.
        """
        for pair in (("x-note", "X-Note"), ("X-Note", "x-note"),
                     ("x-trace-id", "X-Trace-Id")):
            with pytest.raises(CliError, match="HTTP treats as one header"):
                _parse_backend_entry(
                    self._api_entry(headers={pair[0]: "one", pair[1]: "two"})
                )

    def test_one_spelling_used_twice_is_not_a_collision(self):
        """yaml gives us a dict, so the duplicate is already gone.

        Guards the check against being written as a count of the
        original lines rather than of the folded names: two identical
        keys never reach it, and one key must not report itself.
        """
        cfg = _parse_backend_entry(self._api_entry(headers={"x-note": "one"}))
        assert cfg.headers == {"x-note": "one"}

    # -- env rejection on api/vertex --

    def test_env_on_api_rejected(self):
        with pytest.raises(CliError, match="env"):
            _parse_backend_entry(
                self._api_entry(env={"unset": ["FOO"]})
            )

    def test_env_on_vertex_rejected(self):
        with pytest.raises(CliError, match="env"):
            _parse_backend_entry(
                self._vertex_entry(env={"unset": ["FOO"]})
            )

    def test_env_unset_toplevel_on_api_rejected(self):
        with pytest.raises(CliError, match="env_unset"):
            _parse_backend_entry(
                self._api_entry(env_unset=["FOO"])
            )

    def test_env_set_toplevel_on_vertex_rejected(self):
        with pytest.raises(CliError, match="env_set"):
            _parse_backend_entry(
                self._vertex_entry(env_set={"X": "1"})
            )

    # -- vertex also gets typed fields --

    def test_vertex_typed_fields(self):
        cfg = _parse_backend_entry(
            self._vertex_entry(
                temperature=0.5, thinking_type="adaptive",
                reasoning_effort="high", timeout_s=600,
            )
        )
        assert cfg.temperature == 0.5
        assert cfg.thinking_type == "adaptive"
        assert cfg.reasoning_effort == "high"
        assert cfg.timeout_s == 600


class TestParseCliEnvFields:
    """Parse validation for ADR-0004 env fields on cli backends."""

    def _cli_entry(self, **kw):
        base = {
            "name": "local", "type": "cli", "model": "m",
            "command": "claude",
        }
        base.update(kw)
        return base

    # -- 0005 fields rejected on cli --

    def test_0005_fields_on_cli_rejected(self):
        """Driven from the tuple, so a new field cannot be added untested.

        This used to carry its own copy of the list, and the copy went
        stale the moment 'headers' was added to _API_ONLY_FIELDS -- a new
        field arrived with no coverage and nothing said so. Reading the
        real tuple means the loop below grows on its own; the sample
        values are the only thing left to maintain, and a missing one
        fails loudly rather than skipping the field.
        """
        from code_forge.backend import _API_ONLY_FIELDS

        sample = {
            "temperature": 0.5, "max_completion_tokens": 1000,
            "thinking_type": "enabled", "thinking_budget": 1000,
            "reasoning_effort": "high", "stream": True,
            "outcap_key": "max_tokens", "output_ceiling": 65536,
            "params": {"a": 1}, "headers": {"x-note": "v"},
        }
        missing = [f for f in _API_ONLY_FIELDS if f not in sample]
        assert not missing, (
            "_API_ONLY_FIELDS gained %r with no sample value here, so it "
            "would have gone untested" % (missing,)
        )

        for field_name in _API_ONLY_FIELDS:
            with pytest.raises(CliError, match=field_name):
                _parse_backend_entry(
                    self._cli_entry(**{field_name: sample[field_name]})
                )

    def test_headers_on_a_cli_backend_rejected(self):
        """Named on purpose, because the loop above cannot cover this.

        That loop reads _API_ONLY_FIELDS, so it tests whatever the tuple
        says and cannot notice a field being REMOVED from it -- measured:
        deleting 'headers' there leaves it green. A cli backend spawns a
        subprocess and sends no HTTP request, so headers configured on
        one would be silently ignored, which is the config-lies-about-
        the-wire failure this whole field exists to avoid.
        """
        with pytest.raises(CliError, match="headers"):
            _parse_backend_entry(
                self._cli_entry(headers={"x-note": "v"})
            )

    # -- env parsing --

    def test_env_absent_defaults(self):
        cfg = _parse_backend_entry(self._cli_entry())
        assert cfg.env_unset == ()
        assert cfg.env_set == ()

    def test_env_unset_parsed(self):
        cfg = _parse_backend_entry(
            self._cli_entry(env={"unset": ["A", "B"]})
        )
        assert cfg.env_unset == ("A", "B")

    def test_env_set_parsed(self):
        cfg = _parse_backend_entry(
            self._cli_entry(env={"set": {"X": "1"}})
        )
        assert cfg.env_set == (("X", "1"),)

    def test_env_set_value_coerced_to_str(self):
        cfg = _parse_backend_entry(
            self._cli_entry(env={"set": {"PORT": 8080}})
        )
        assert cfg.env_set == (("PORT", "8080"),)

    def test_env_both_populated(self):
        cfg = _parse_backend_entry(
            self._cli_entry(
                env={"unset": ["OLD"], "set": {"NEW": "val"}}
            )
        )
        assert cfg.env_unset == ("OLD",)
        assert cfg.env_set == (("NEW", "val"),)

    def test_env_not_dict_rejected(self):
        with pytest.raises(CliError, match="env"):
            _parse_backend_entry(self._cli_entry(env="bad"))

    def test_env_unknown_key_rejected(self):
        with pytest.raises(CliError, match="unknown"):
            _parse_backend_entry(
                self._cli_entry(env={"unset": [], "bogus": 1})
            )


class TestProbeBackendLive:
    """Live probe: one real completion, 60s cap, one attempt, no cache.

    llm_invoke is patched at its SOURCE module (backend.py imports it
    function-locally, so the call-time import sees the patch).
    """

    def _cfg(self, **kw):
        from code_forge.backend import BackendConfig
        base = dict(
            name="live-test", type="api", model="m",
            format="openai", base_url="https://example.com",
            api_key_env="LIVE_TEST_KEY", timeout_s=1800,
            max_tokens=8192,
        )
        base.update(kw)
        return BackendConfig(**base)

    def _invoke(self, error=None, result=None):
        """Capture (prompt, kwargs) at the llm_invoke call site."""
        from unittest.mock import MagicMock
        m = MagicMock()
        if error is not None:
            m.side_effect = error
        elif result is not None:
            m.return_value = result
        return m

    def test_success_returns_ok(self):
        from code_forge.backend import probe_backend_live
        m = self._invoke(result="unused")
        with patch("code_forge.llm_invoke.llm_invoke", m):
            r = probe_backend_live(self._cfg())
        assert r.ok is True

    def test_probe_config_overrides(self):
        from code_forge.backend import probe_backend_live
        m = self._invoke(result="unused")
        with patch("code_forge.llm_invoke.llm_invoke", m):
            probe_backend_live(self._cfg())
        cfg = m.call_args[1]["backend"]
        assert cfg.timeout_s == 60
        assert cfg.max_tokens == 32
        assert cfg.max_completion_tokens == 0
        assert cfg.output_ceiling == 0
        assert cfg.thinking_type == ""
        assert cfg.thinking_budget == 0
        assert cfg.reasoning_effort == ""
        # identity preserved: the source config object is untouched
        src = self._cfg()
        with patch("code_forge.llm_invoke.llm_invoke",
                   self._invoke(result="unused")):
            probe_backend_live(src)
        assert src.timeout_s == 1800

    def test_one_attempt_and_json_demand(self):
        from code_forge.backend import probe_backend_live
        m = self._invoke(result="unused")
        with patch("code_forge.llm_invoke.llm_invoke", m):
            probe_backend_live(self._cfg())
        kw = m.call_args[1]
        assert kw["max_attempts"] == 1
        assert "ok" in kw["expected_keys"]
        assert kw["continuation_breaker"] is not None

    def _classify(self, error):
        from code_forge.backend import probe_backend_live
        with patch("code_forge.llm_invoke.llm_invoke",
                   self._invoke(error=error)):
            return probe_backend_live(self._cfg())

    def test_timeout_class(self):
        from code_forge.llm_invoke import LLMInvokeError
        r = self._classify(LLMInvokeError("timed out", is_timeout=True))
        assert r.error_class == "timeout"
        assert r.suggestion

    def test_credential_class_via_kind(self):
        from code_forge.llm_invoke import LLMInvokeError
        r = self._classify(LLMInvokeError("env not set",
                                          kind="credentials"))
        assert r.error_class == "credential-rejected"

    def test_credential_class_via_http_code(self):
        from code_forge.llm_invoke import LLMInvokeError
        r = self._classify(LLMInvokeError("401", exit_code=401))
        assert r.error_class == "credential-rejected"

    def test_connect_timeout_classifies_as_timeout(self):
        from code_forge.llm_invoke import LLMInvokeError
        r = self._classify(LLMInvokeError(
            "URLError from live-test backend: timed out",
            is_timeout=True, kind="conn"))
        assert r.error_class == "timeout"

    def test_conn_class(self):
        from code_forge.llm_invoke import LLMInvokeError
        r = self._classify(LLMInvokeError("refused", kind="conn"))
        assert r.error_class == "connection-refused"

    def test_sse_class(self):
        from code_forge.llm_invoke import LLMInvokeError
        r = self._classify(LLMInvokeError("SSE body", kind="sse_body"))
        assert r.error_class == "SSE-mixed"

    def test_bad_body_class(self):
        from code_forge.llm_invoke import LLMInvokeError
        r = self._classify(LLMInvokeError("non-JSON", kind="bad_body"))
        assert r.error_class == "JSON-malformed"

    def test_truncated_class(self):
        from code_forge.llm_invoke import LLMInvokeError
        r = self._classify(LLMInvokeError("truncation breaker",
                                          kind="truncated"))
        assert r.error_class == "truncated-output"

    def test_http_error_class_no_kind(self):
        from code_forge.llm_invoke import LLMInvokeError
        r = self._classify(LLMInvokeError("404", exit_code=404))
        assert r.error_class == "http-error"

    def test_unclassified_fallback(self):
        from code_forge.llm_invoke import LLMInvokeError
        r = self._classify(LLMInvokeError("something novel",
                                          kind="no_json"))
        assert r.error_class == "unclassified"

    def test_detail_is_single_line(self):
        from code_forge.llm_invoke import LLMInvokeError
        r = self._classify(LLMInvokeError(
            "body:\nline two\nline three", kind="bad_body"))
        assert "\n" not in r.detail
