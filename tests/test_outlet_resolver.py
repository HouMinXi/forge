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

from code_forge.backend import ProbeResult
from code_forge.errors import CliError
from code_forge.outlet_resolver import load_outlet_from_gate, resolve_outlet


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
        assert result == "cli"

    def test_env_inline_overrides_all(self):
        result = resolve_outlet(
            env={"FORGE_OUTLET": "inline"},
            gate_yaml_path=None,
            reachability_fn=_bomb_probe,
        )
        assert result == "inline"

    def test_env_empty_falls_through(self):
        """FORGE_OUTLET='' falls through to gate.yaml / reachability."""
        result = resolve_outlet(
            env={"FORGE_OUTLET": ""},
            gate_yaml_path=None,
            reachability_fn=_ok_probe,
        )
        assert result == "cli"

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
        """FORGE_OUTLET=CLI returns 'cli' (case-insensitive)."""
        result = resolve_outlet(
            env={"FORGE_OUTLET": "CLI"},
            gate_yaml_path=None,
            reachability_fn=_bomb_probe,
        )
        assert result == "cli"

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
        """gate.yaml with outlet: cli, no env override -> returns 'cli'."""
        gate = tmp_path / "gate.yaml"
        gate.write_text("outlet: cli\n")
        result = resolve_outlet(
            env={},
            gate_yaml_path=gate,
            reachability_fn=_bomb_probe,
        )
        assert result == "cli"

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
        """gate.yaml without outlet key -> falls through to probe."""
        gate = tmp_path / "gate.yaml"
        gate.write_text("test:\n  command: [pytest]\n")
        result = resolve_outlet(
            env={},
            gate_yaml_path=gate,
            reachability_fn=_ok_probe,
        )
        assert result == "cli"


# -- TestBackendReachabilityDefault ----------------------------------------


class TestBackendReachabilityDefault:
    """No override -> reachability probe."""

    def test_no_override_backend_reachable(self):
        """Backend reachable -> returns 'cli' (fail-safe Outlet A)."""
        result = resolve_outlet(
            env={},
            gate_yaml_path=None,
            reachability_fn=_ok_probe,
        )
        assert result == "cli"

    def test_no_override_backend_unreachable(self):
        """Backend unreachable -> raises CliError (FAIL CLOSED)."""
        with pytest.raises(CliError) as exc_info:
            resolve_outlet(
                env={},
                gate_yaml_path=None,
                reachability_fn=_fail_probe,
            )
        msg = str(exc_info.value)
        assert "Configure a review backend" in msg
        assert "FORGE_OUTLET=inline" in msg


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
        assert result == "cli"

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
        assert result == "cli"

    def test_cli_value_invalid_raises(self):
        """cli_value='invalid' raises ValueError with source attribution."""
        with pytest.raises(ValueError, match="--outlet flag"):
            resolve_outlet(
                env={},
                gate_yaml_path=None,
                cli_value="invalid",
            )
