# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for BOTH-04 outlet resolution.

Mirrors test_mode_resolver.py structure:
  - TestEnvOverride: FORGE_OUTLET env var wins over gate.yaml/reachability
  - TestGateYamlOutlet: gate.yaml outlet field
  - TestBackendReachabilityDefault: no override -> reachability probe
  - TestEdgeCases: whitespace, invalid, case-insensitive, inline-never-probes
  - TestLoadOutletFromGate: standalone gate.yaml reader tests
"""
from __future__ import annotations

import pytest

from code_forge.backend import BackendConfig, ProbeResult
from code_forge.errors import CliError
from code_forge.outlet_resolver import load_outlet_from_gate, resolve_outlet



_DUMMY_CFG = BackendConfig(
    name="test-stub", type="cli", model="",
    format="", base_url="", api_key_env="",
    command="", default=False, max_tokens=0,
)

# -- Helpers ---------------------------------------------------------------


def _ok_probe() -> ProbeResult:
    """Reachability probe that returns ok=True."""
    return ProbeResult(ok=True)


def _fail_probe() -> ProbeResult:
    """Reachability probe that returns ok=False with error."""
    return ProbeResult(ok=False, error="mock unreachable")


def _bomb_probe() -> ProbeResult:
    """Reachability probe that explodes if called."""
    raise AssertionError("reachability_fn should NOT have been called")


# -- TestEnvOverride -------------------------------------------------------


class TestEnvOverride:
    """FORGE_OUTLET env var has highest precedence."""

    def test_env_cli_overrides_all(self):
        result = resolve_outlet(
            env={"FORGE_OUTLET": "cli"},
            gate_yaml_path=None,
            reachability_fn=_bomb_probe,
        )
        assert result == "subprocess"

    def test_env_inline_overrides_all(self):
        result = resolve_outlet(
            env={"FORGE_OUTLET": "inline"},
            gate_yaml_path=None,
            reachability_fn=_bomb_probe,
        )
        assert result == "inline"

    def test_env_empty_falls_through(self):
        """FORGE_OUTLET='' falls through to gate.yaml / reachability.

        has_explicit_backend=True simulates a configured backend so the
        zero-config guard does not fire before the reachability probe.
        """
        result = resolve_outlet(
            env={"FORGE_OUTLET": ""},
            gate_yaml_path=None,
            has_explicit_backend=True,
            reachability_fn=_ok_probe,
        )
        assert result == "subprocess"

    def test_env_whitespace_raises(self):
        """FORGE_OUTLET='  ' raises ValueError with source attribution."""
        with pytest.raises(ValueError, match="invalid outlet"):
            resolve_outlet(
                env={"FORGE_OUTLET": "  "},
                gate_yaml_path=None,
                reachability_fn=_bomb_probe,
            )

    def test_env_invalid_raises(self):
        """FORGE_OUTLET='both' raises ValueError."""
        with pytest.raises(ValueError, match="invalid outlet"):
            resolve_outlet(
                env={"FORGE_OUTLET": "both"},
                gate_yaml_path=None,
                reachability_fn=_bomb_probe,
            )

    def test_env_case_insensitive(self):
        """FORGE_OUTLET=CLI returns 'subprocess' via deprecated alias (case-insensitive)."""
        result = resolve_outlet(
            env={"FORGE_OUTLET": "CLI"},
            gate_yaml_path=None,
            reachability_fn=_bomb_probe,
        )
        assert result == "subprocess"

    def test_env_case_insensitive_inline(self):
        """FORGE_OUTLET=INLINE returns 'inline' (case-insensitive)."""
        result = resolve_outlet(
            env={"FORGE_OUTLET": "INLINE"},
            gate_yaml_path=None,
            reachability_fn=_bomb_probe,
        )
        assert result == "inline"


# -- TestGateYamlOutlet ----------------------------------------------------


class TestGateYamlOutlet:
    """gate.yaml outlet field (separate lightweight reader)."""

    def test_gate_yaml_outlet_cli(self, tmp_path):
        """gate.yaml with outlet: cli (deprecated alias) -> returns 'subprocess'."""
        gate = tmp_path / "gate.yaml"
        gate.write_text("outlet: cli\n")
        result = resolve_outlet(
            env={},
            gate_yaml_path=gate,
            reachability_fn=_bomb_probe,
        )
        assert result == "subprocess"

    def test_gate_yaml_outlet_inline(self, tmp_path):
        """gate.yaml with outlet: inline -> returns 'inline' (no probe)."""
        gate = tmp_path / "gate.yaml"
        gate.write_text("outlet: inline\n")
        result = resolve_outlet(
            env={},
            gate_yaml_path=gate,
            reachability_fn=_bomb_probe,
        )
        assert result == "inline"

    def test_gate_yaml_no_outlet_key(self, tmp_path):
        """gate.yaml without outlet key -> falls through to probe, returns 'subprocess'.

        has_explicit_backend=True simulates a configured backend so the
        zero-config guard does not fire before the reachability probe.
        """
        gate = tmp_path / "gate.yaml"
        gate.write_text("test:\n  command: [pytest]\n")
        result = resolve_outlet(
            env={},
            gate_yaml_path=gate,
            has_explicit_backend=True,
            reachability_fn=_ok_probe,
        )
        assert result == "subprocess"


# -- TestBackendReachabilityDefault ----------------------------------------


class TestBackendReachabilityDefault:
    """No override -> reachability probe."""

    def test_no_override_backend_reachable(self):
        """Backend reachable -> returns 'subprocess' (fail-safe Outlet A).

        has_explicit_backend=True simulates a configured backend so the
        zero-config guard does not fire before the reachability probe.
        """
        result = resolve_outlet(
            env={},
            gate_yaml_path=None,
            has_explicit_backend=True,
            reachability_fn=_ok_probe,
        )
        assert result == "subprocess"

    def test_no_override_backend_unreachable(self):
        """Backend unreachable -> raises CliError (FAIL CLOSED).

        has_explicit_backend=True simulates a configured backend so the
        zero-config guard does not fire; the reachability probe runs and
        raises the unreachable CliError.
        """
        with pytest.raises(CliError) as exc_info:
            resolve_outlet(
                env={},
                gate_yaml_path=None,
                has_explicit_backend=True,
                reachability_fn=_fail_probe,
            )
        msg = str(exc_info.value)
        assert "Configure a review backend" in msg
        assert "FORGE_OUTLET=inline" in msg


# -- TestZeroConfigGuard ---------------------------------------------------


class TestZeroConfigGuard:
    """Zero-config guard: no backend configured -> CliError."""

    def test_no_backend_no_env_raises(self):
        """No configs, no has_explicit_backend, no env -> CliError."""
        with pytest.raises(CliError) as exc_info:
            resolve_outlet(
                env={},
                gate_yaml_path=None,
                configs=[],
                has_explicit_backend=False,
                reachability_fn=_bomb_probe,
            )
        msg = str(exc_info.value)
        assert "No review backend configured" in msg
        assert "gate.yaml" in msg
        assert "FORGE_OUTLET=inline" in msg

    def test_configs_nonempty_bypasses_guard(self):
        """Non-empty configs list means user configured a backend -> no guard."""
        result = resolve_outlet(
            env={},
            gate_yaml_path=None,
            configs=["any_truthy_value"],
            has_explicit_backend=False,
            reachability_fn=_ok_probe,
        )
        assert result == "subprocess"

    def test_has_explicit_backend_bypasses_guard(self):
        """has_explicit_backend=True bypasses guard even with empty configs."""
        result = resolve_outlet(
            env={},
            gate_yaml_path=None,
            configs=[],
            has_explicit_backend=True,
            reachability_fn=_ok_probe,
        )
        assert result == "subprocess"

    def test_gate_yaml_outlet_bypasses_guard(self, tmp_path):
        """gate.yaml outlet=inline short-circuits before guard fires."""
        gate = tmp_path / "gate.yaml"
        gate.write_text("outlet: inline\n")
        result = resolve_outlet(
            env={},
            gate_yaml_path=gate,
            configs=[],
            has_explicit_backend=False,
            reachability_fn=_bomb_probe,
        )
        assert result == "inline"

    def test_forge_outlet_env_bypasses_guard(self):
        """FORGE_OUTLET=inline short-circuits before guard fires."""
        result = resolve_outlet(
            env={"FORGE_OUTLET": "inline"},
            gate_yaml_path=None,
            configs=[],
            has_explicit_backend=False,
            reachability_fn=_bomb_probe,
        )
        assert result == "inline"


# -- TestEdgeCases ---------------------------------------------------------


class TestEdgeCases:
    """Inline-never-probes, source attribution, mixed scenarios."""

    def test_inline_override_skips_reachability_probe(self):
        """FORGE_OUTLET=inline with a bomb probe -> returns 'inline'.

        Proves Outlet B NEVER probes.
        """
        result = resolve_outlet(
            env={"FORGE_OUTLET": "inline"},
            gate_yaml_path=None,
            reachability_fn=_bomb_probe,
        )
        assert result == "inline"

    def test_gate_inline_skips_probe(self, tmp_path):
        """gate.yaml outlet: inline with a bomb probe -> returns 'inline'."""
        gate = tmp_path / "gate.yaml"
        gate.write_text("outlet: inline\n")
        result = resolve_outlet(
            env={},
            gate_yaml_path=gate,
            reachability_fn=_bomb_probe,
        )
        assert result == "inline"

    def test_env_overrides_gate(self, tmp_path):
        """FORGE_OUTLET=inline wins over gate.yaml outlet: cli."""
        gate = tmp_path / "gate.yaml"
        gate.write_text("outlet: cli\n")
        result = resolve_outlet(
            env={"FORGE_OUTLET": "inline"},
            gate_yaml_path=gate,
            reachability_fn=_bomb_probe,
        )
        assert result == "inline"


# -- TestLoadOutletFromGate ------------------------------------------------


class TestLoadOutletFromGate:
    """Standalone gate.yaml reader."""

    def test_reads_only_outlet(self, tmp_path):
        """gate.yaml with outlet: inline but NO test: section -> ok."""
        gate = tmp_path / "gate.yaml"
        gate.write_text("outlet: inline\n")
        result = load_outlet_from_gate(gate)
        assert result == "inline"

    def test_missing_file(self, tmp_path):
        """gate.yaml does not exist -> returns None."""
        gate = tmp_path / "nonexistent.yaml"
        result = load_outlet_from_gate(gate)
        assert result is None

    def test_corrupted_yaml(self, tmp_path):
        """Invalid YAML -> raises ValueError with 'gate.yaml read failed'."""
        gate = tmp_path / "gate.yaml"
        gate.write_text("{unclosed: [bracket")
        with pytest.raises(ValueError, match="gate.yaml read failed"):
            load_outlet_from_gate(gate)

    def test_permission_denied(self, tmp_path):
        """Unreadable gate.yaml -> ValueError with 'permission denied'."""
        gate = tmp_path / "gate.yaml"
        gate.write_text("outlet: cli\n")

        def _raise_perm(*args, **kwargs):
            raise PermissionError("mocked permission denied")

        with pytest.raises(ValueError, match="gate.yaml read failed") as exc_info:
            load_outlet_from_gate(gate, fs_open=_raise_perm)
        assert "permission denied" in str(exc_info.value).lower()

    def test_no_outlet_key(self, tmp_path):
        """gate.yaml with no outlet key -> returns None."""
        gate = tmp_path / "gate.yaml"
        gate.write_text("test:\n  command: [pytest]\n")
        result = load_outlet_from_gate(gate)
        assert result is None


# -- TestCliValuePrecedence ------------------------------------------------


class TestCliValuePrecedence:
    """--outlet flag (cli_value) has highest precedence."""

    def test_cli_value_wins_over_env(self):
        """cli_value='inline' overrides FORGE_OUTLET='cli'."""
        result = resolve_outlet(
            env={"FORGE_OUTLET": "cli"},
            gate_yaml_path=None,
            cli_value="inline",
            reachability_fn=_bomb_probe,
        )
        assert result == "inline"

    def test_cli_value_wins_over_gate_yaml(self, tmp_path):
        """cli_value='cli' overrides gate.yaml outlet=inline."""
        gate = tmp_path / "gate.yaml"
        gate.write_text("outlet: inline\n")
        result = resolve_outlet(
            env={},
            gate_yaml_path=gate,
            cli_value="cli",
            reachability_fn=_bomb_probe,
        )
        assert result == "subprocess"

    def test_cli_value_empty_falls_through(self):
        """cli_value='' falls through to env."""
        result = resolve_outlet(
            env={"FORGE_OUTLET": "inline"},
            gate_yaml_path=None,
            cli_value="",
            reachability_fn=_bomb_probe,
        )
        assert result == "inline"

    def test_cli_value_none_falls_through(self):
        """cli_value=None falls through to env."""
        result = resolve_outlet(
            env={"FORGE_OUTLET": "cli"},
            gate_yaml_path=None,
            cli_value=None,
            reachability_fn=_ok_probe,
        )
        assert result == "subprocess"

    def test_cli_value_invalid_raises(self):
        """cli_value='invalid' raises ValueError with source attribution."""
        with pytest.raises(ValueError, match="--outlet flag"):
            resolve_outlet(
                env={},
                gate_yaml_path=None,
                cli_value="invalid",
            )


# -- TestSubagentOutlet ----------------------------------------------------


class TestSubagentOutlet:
    """Outlet C (subagent) acceptance tests."""

    def test_env_subagent_accepted(self):
        """FORGE_OUTLET=subagent -> returns 'subagent'."""
        result = resolve_outlet(
            env={"FORGE_OUTLET": "subagent"},
            gate_yaml_path=None,
            reachability_fn=_bomb_probe,
        )
        assert result == "subagent"

    def test_env_subagent_case_insensitive(self):
        """FORGE_OUTLET=SUBAGENT -> returns 'subagent'."""
        result = resolve_outlet(
            env={"FORGE_OUTLET": "SUBAGENT"},
            gate_yaml_path=None,
            reachability_fn=_bomb_probe,
        )
        assert result == "subagent"

    def test_gate_yaml_subagent(self, tmp_path):
        """gate.yaml with outlet: subagent -> returns 'subagent'."""
        gate = tmp_path / "gate.yaml"
        gate.write_text("outlet: subagent\n")
        result = resolve_outlet(
            env={},
            gate_yaml_path=gate,
            reachability_fn=_bomb_probe,
        )
        assert result == "subagent"

    def test_cli_value_subagent(self):
        """cli_value='subagent' -> returns 'subagent'."""
        result = resolve_outlet(
            env={},
            gate_yaml_path=None,
            cli_value="subagent",
            reachability_fn=_bomb_probe,
        )
        assert result == "subagent"

    def test_subagent_skips_reachability_probe(self):
        """FORGE_OUTLET=subagent with bomb probe -> no explosion.

        Proves Outlet C NEVER probes (same as Outlet B).
        """
        result = resolve_outlet(
            env={"FORGE_OUTLET": "subagent"},
            gate_yaml_path=None,
            reachability_fn=_bomb_probe,
        )
        assert result == "subagent"


# -- FORGE_BACKEND + default reachability_fn --------------------------------


class TestForgeBackendDefaultFn:
    """Default reachability_fn must not crash when FORGE_BACKEND is set.

    resolve_outlet's built-in default uses configs=[], so FORGE_BACKEND
    with no injected fn raises CliError "unknown backend (configured: none)".
    """

    def test_default_fn_with_forge_backend_crashes_on_empty_configs(self):
        """Zero-config guard fires before FORGE_BACKEND is checked."""
        with pytest.raises(CliError, match="No review backend configured"):
            resolve_outlet(
                env={"FORGE_BACKEND": "deepseek"},
                gate_yaml_path=None,
            )

    def test_injected_fn_with_forge_backend_succeeds(self, tmp_path):
        """Injected fn with real gate.yaml resolves FORGE_BACKEND correctly."""
        from code_forge.backend import load_backend_configs
        # Create a gate.yaml with backends config
        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir()
        gate_yaml = gate_dir / "gate.yaml"
        gate_yaml.write_text("""
backends:
  deepseek:
    type: api
    format: openai
    base_url: https://api.deepseek.com/v1
    api_key_env: DEEPSEEK_API_KEY
    model: deepseek-chat
""")

        # Mimic the CLI closure that loads gate.yaml
        def _reachability():
            from code_forge.backend import (
                load_backend_configs, resolve_backend,
            )
            import yaml as _y
            cfgs = []
            try:
                with open(gate_yaml, "r", encoding="utf-8") as _f:
                    gd = _y.safe_load(_f)
                if isinstance(gd, dict):
                    cfgs = load_backend_configs(gd)
            except (FileNotFoundError, _y.YAMLError):
                pass
            # This should now succeed because deepseek is in cfgs
            _ = resolve_backend(
                {"FORGE_BACKEND": "deepseek"}, configs=cfgs, cli_value=None
            )
            # Stub the probe (we're testing resolution, not reachability)
            return ProbeResult(ok=True)

        # Load configs outside the closure so the zero-config guard is bypassed
        import yaml as _y
        with open(gate_yaml, "r", encoding="utf-8") as _f:
            _gd = _y.safe_load(_f)
        _cfgs = load_backend_configs(_gd) if isinstance(_gd, dict) else []

        result = resolve_outlet(
            env={"FORGE_BACKEND": "deepseek"},
            gate_yaml_path=gate_yaml,
            configs=_cfgs,
            reachability_fn=_reachability,
        )
        assert result == "subprocess"

    def test_injected_fn_unknown_backend_still_errors(self):
        """Typo in FORGE_BACKEND must still fail with a clear error."""
        from code_forge.backend import resolve_backend
        def _reachability():
            resolve_backend(
                {"FORGE_BACKEND": "typo-backend"}, configs=[], cli_value=None,
            )
            return ProbeResult(ok=True)
        with pytest.raises(CliError, match="unknown backend.*typo-backend"):
            resolve_outlet(
                env={"FORGE_BACKEND": "typo-backend"},
                gate_yaml_path=None,
                configs=[_DUMMY_CFG],
                reachability_fn=_reachability,
            )


# -- FORGE_BACKEND via real CLI entry point ---------------------------------


class TestForgeBackendRealEntry:
    """Drives cli._run_resolve_outlet with a real gate.yaml.

    Exercises the actual CLI function to verify FORGE_BACKEND routing
    works end-to-end when gate.yaml has matching backends configured.
    """

    _GATE = (
        "backends:\n"
        "  deepseek:\n"
        "    type: api\n"
        "    format: openai\n"
        "    base_url: https://api.deepseek.com/v1\n"
        "    api_key_env: DEEPSEEK_API_KEY\n"
        "    model: deepseek-chat\n"
    )

    def test_forge_backend_routes_via_real_entry(self, tmp_path, monkeypatch):
        """FORGE_BACKEND=deepseek resolves to 'subprocess' via _run_resolve_outlet."""
        import io
        import sys
        import yaml
        from code_forge.cli import _run_resolve_outlet
        from code_forge.exit_codes import EXIT_PASS
        from code_forge.trust import record_trust

        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir()
        gate_yaml_path = gate_dir / "gate.yaml"
        gate_yaml_path.write_text(self._GATE)

        # Trust the gate.yaml so _load_gate_backends returns configs.
        config_home = tmp_path / "xdg-config"
        config_home.mkdir()
        monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
        gate_data = yaml.safe_load(gate_yaml_path.read_text())
        record_trust(gate_yaml_path, gate_data)

        env = {"FORGE_BACKEND": "deepseek", "DEEPSEEK_API_KEY": "dummy"}
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
        try:
            rc = _run_resolve_outlet(env, tmp_path)
        finally:
            stdout_val = sys.stdout.getvalue()
            sys.stdout, sys.stderr = old_out, old_err

        assert rc == EXIT_PASS, "expected EXIT_PASS, got %d" % rc
        assert "subprocess" in stdout_val


# -- TestDeprecatedOutletAlias ---------------------------------------------


class TestDeprecatedOutletAlias:
    """'cli' is accepted as a deprecated alias for 'subprocess'."""

    def test_cli_alias_returns_subprocess(self):
        """FORGE_OUTLET=cli returns 'subprocess' via deprecated alias."""
        result = resolve_outlet(
            env={"FORGE_OUTLET": "cli"},
            gate_yaml_path=None,
            reachability_fn=_bomb_probe,
        )
        assert result == "subprocess"

    def test_cli_alias_emits_warning(self, capsys):
        """_parse_outlet_string('cli', ...) emits DeprecationWarning to stderr."""
        from code_forge.outlet_resolver import _parse_outlet_string
        result = _parse_outlet_string("cli", "test")
        assert result == "subprocess"
        captured = capsys.readouterr()
        assert "DeprecationWarning" in captured.err
        assert "renamed to 'subprocess'" in captured.err

    def test_subprocess_value_no_warning(self, capsys):
        """'subprocess' is the canonical value -- no DeprecationWarning emitted."""
        from code_forge.outlet_resolver import _parse_outlet_string
        result = _parse_outlet_string("subprocess", "test")
        assert result == "subprocess"
        captured = capsys.readouterr()
        assert "DeprecationWarning" not in captured.err

    def test_cli_alias_from_cli_flag(self):
        """--outlet cli returns 'subprocess' via deprecated alias."""
        result = resolve_outlet(
            env={},
            gate_yaml_path=None,
            cli_value="cli",
            reachability_fn=_bomb_probe,
        )
        assert result == "subprocess"

    def test_cli_alias_from_gate_yaml(self, tmp_path):
        """gate.yaml outlet: cli returns 'subprocess' via deprecated alias."""
        gate = tmp_path / "gate.yaml"
        gate.write_text("outlet: cli\n")
        result = resolve_outlet(
            env={},
            gate_yaml_path=gate,
            reachability_fn=_bomb_probe,
        )
        assert result == "subprocess"
