# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for code-forge doctor self-check command."""
from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from code_forge.doctor import (
    _check_backends,
    _check_gate_yaml,
    _check_handshake,
    _check_outlet,
    _check_registries,
    _check_trust,
    _check_workspace,
    run_doctor,
)


# -- _check_workspace --


def test_workspace_pass(tmp_path):
    gate_dir = tmp_path / ".code-forge"
    gate_dir.mkdir()
    (gate_dir / "gate.yaml").write_text("test: {}")
    ok, msg, ws = _check_workspace(tmp_path, {})
    assert ok
    assert ws == tmp_path


def test_workspace_env_override(tmp_path):
    ok, msg, ws = _check_workspace(
        Path("/nonexistent"), {"FORGE_PROJECT_DIR": str(tmp_path)})
    assert ok
    assert ws == tmp_path.resolve()


# -- _check_gate_yaml --


def test_gate_yaml_pass(tmp_path):
    gate_dir = tmp_path / ".code-forge"
    gate_dir.mkdir()
    (gate_dir / "gate.yaml").write_text(
        "backends:\n  demo:\n    type: api\n")
    ok, msg, data = _check_gate_yaml(tmp_path)
    assert ok
    assert "demo" in data["backends"]


def test_gate_yaml_not_found(tmp_path):
    ok, msg, data = _check_gate_yaml(tmp_path)
    assert not ok
    assert "not found" in msg
    assert data is None


def test_gate_yaml_empty_file(tmp_path):
    gate_dir = tmp_path / ".code-forge"
    gate_dir.mkdir()
    (gate_dir / "gate.yaml").write_text("")
    ok, msg, data = _check_gate_yaml(tmp_path)
    assert ok
    assert data == {}


def test_gate_yaml_non_dict(tmp_path):
    gate_dir = tmp_path / ".code-forge"
    gate_dir.mkdir()
    (gate_dir / "gate.yaml").write_text("- item1\n- item2\n")
    ok, msg, data = _check_gate_yaml(tmp_path)
    assert not ok
    assert "mapping" in msg
    assert data is None


def test_gate_yaml_unicode_error(tmp_path):
    gate_dir = tmp_path / ".code-forge"
    gate_dir.mkdir()
    (gate_dir / "gate.yaml").write_bytes(b"\x80\x81\x82")
    ok, msg, data = _check_gate_yaml(tmp_path)
    assert not ok
    assert data is None


# -- _check_trust --


def test_trust_granted(tmp_path):
    gate_path = tmp_path / "gate.yaml"
    mock_status = MagicMock()
    mock_status.trusted = True
    with patch("code_forge.trust.trust_status",
               return_value=mock_status):
        ok, msg = _check_trust(gate_path, {})
    assert ok
    assert msg == "granted"


def test_trust_not_granted(tmp_path):
    gate_path = tmp_path / "gate.yaml"
    mock_status = MagicMock()
    mock_status.trusted = False
    with patch("code_forge.trust.trust_status",
               return_value=mock_status):
        ok, msg = _check_trust(gate_path, {})
    assert not ok
    assert "not granted" in msg
    assert "code-forge trust" in msg


# -- _check_backends --


def test_backends_user_provenance():
    """T2a: user-only backend shows provenance=user."""
    gate_data = {}
    mock_cfg = MagicMock()
    mock_cfg.name = "my-backend"
    mock_probe = MagicMock()
    mock_probe.ok = True
    with patch("code_forge.user_config.load_user_backends",
               return_value={"my-backend": {"type": "api"}}), \
         patch("code_forge.user_config.merge_backends",
               return_value={"my-backend": {"type": "api"}}), \
         patch("code_forge.backend.load_backend_configs",
               return_value=[mock_cfg]), \
         patch("code_forge.backend.probe_backend",
               return_value=mock_probe):
        diag, configs = _check_backends(Path("/ws"), gate_data, {})
    assert len(diag) == 1
    assert diag[0][0] is True
    assert "(user)" in diag[0][1]


def test_backends_no_backends():
    gate_data = {}
    with patch("code_forge.user_config.load_user_backends",
               return_value={}), \
         patch("code_forge.user_config.merge_backends",
               return_value={}), \
         patch("code_forge.backend.load_backend_configs",
               return_value=[]):
        diag, configs = _check_backends(Path("/ws"), gate_data, {})
    assert diag[0][0] is False
    assert "no backends" in diag[0][1]


def test_backends_shadowed():
    """Shadowed backend prints SHADOWED AND probes the project version."""
    gate_data = {"backends": {"shared": {"type": "api"}}}
    mock_cfg = MagicMock()
    mock_cfg.name = "shared"
    mock_probe = MagicMock()
    mock_probe.ok = True
    with patch("code_forge.user_config.load_user_backends",
               return_value={"shared": {"type": "api"}}), \
         patch("code_forge.user_config.merge_backends",
               return_value={"shared": {"type": "api"}}), \
         patch("code_forge.backend.load_backend_configs",
               return_value=[mock_cfg]), \
         patch("code_forge.backend.probe_backend",
               return_value=mock_probe) as mock_pb:
        diag, configs = _check_backends(
            Path("/ws"), gate_data, {})
    assert "SHADOWED" in diag[0][1]
    assert diag[0][0] is True
    # Project version is still probed (not skipped)
    mock_pb.assert_called_once()
    assert len(diag) == 2  # SHADOWED + probe result
    assert diag[1][0] is True
    assert "(project)" in diag[1][1]


def test_backends_config_error():
    gate_data = {}
    with patch("code_forge.user_config.load_user_backends",
               side_effect=Exception("broken")):
        diag, configs = _check_backends(Path("/ws"), gate_data, {})
    assert diag[0][0] is False
    assert "backend config error" in diag[0][1]


# -- _check_outlet --


def test_outlet_subprocess(tmp_path):
    gate_dir = tmp_path / ".code-forge"
    gate_dir.mkdir()
    (gate_dir / "gate.yaml").write_text("outlet: subprocess\n")
    with patch("code_forge.outlet_resolver.resolve_outlet",
               return_value="subprocess"):
        ok, msg = _check_outlet(
            tmp_path, {"outlet": "subprocess"}, {}, [])
    assert ok
    assert msg == "subprocess"


def test_outlet_sampling_fails():
    with patch("code_forge.outlet_resolver.resolve_outlet",
               return_value="sampling"):
        ok, msg = _check_outlet(Path("/ws"), {}, {}, [])
    assert not ok
    assert "sampling" in msg
    assert "Switch outlet" in msg


# -- _check_handshake --


def test_handshake_import_error():
    """T2b: mcp not installed."""
    with patch("code_forge.doctor.asyncio.run",
               side_effect=ImportError("no mcp")):
        ok, msg = _check_handshake()
    assert not ok
    assert "mcp not installed" in msg


# -- _check_registries --


def test_registries_found_and_absent(tmp_path):
    """T2c: forge in one registry, absent in another."""
    claude_json = tmp_path / ".claude.json"
    claude_json.write_text(json.dumps({
        "mcpServers": {
            "forge": {"command": "code-forge-mcp"}}
    }))
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(json.dumps({
        "mcpServers": {"other-tool": {"command": "other"}}
    }))

    from code_forge.doctor import RegistryEntry

    test_map = [
        RegistryEntry(
            name="Claude Code",
            paths=[str(claude_json)],
            key="mcpServers",
        ),
        RegistryEntry(
            name="Cursor",
            paths=[str(cursor_dir / "mcp.json")],
            key="mcpServers",
        ),
    ]
    with patch("code_forge.doctor.REGISTRY_MAP", test_map):
        results = _check_registries(tmp_path)
    result_dict = dict(results)
    assert result_dict["Claude Code"] == "PRESENT"
    assert result_dict["Cursor"] == "ABSENT"


def test_registries_command_field_match(tmp_path):
    """Registry detects forge via command field, not just name."""
    config = tmp_path / "settings.json"
    config.write_text(json.dumps({
        "mcpServers": {
            "my-review": {"command": "code-forge-mcp"}}
    }))
    from code_forge.doctor import RegistryEntry

    test_map = [
        RegistryEntry(name="Test", paths=[str(config)],
                      key="mcpServers"),
    ]
    with patch("code_forge.doctor.REGISTRY_MAP", test_map):
        results = _check_registries(tmp_path)
    assert results[0][1] == "PRESENT"


# -- T2d: no key material in output --


def test_no_key_leakage(tmp_path, capsys):
    """T2d: dummy key in api_key_file -> grep output -> zero hits."""
    secret = "sk-SUPERSECRET-1234567890"
    ws = tmp_path / "project"
    gate_dir = ws / ".code-forge"
    gate_dir.mkdir(parents=True)
    key_file = tmp_path / "key.txt"
    key_file.write_text(secret)
    os.chmod(key_file, 0o600)
    (gate_dir / "gate.yaml").write_text(textwrap.dedent("""\
        backends:
          test-backend:
            type: api
            model: test
            format: openai
            base_url: https://api.example.com/v1
            api_key_file: %s
    """ % key_file))

    with patch("code_forge.doctor._check_handshake",
               return_value=(True, "code-forge-mcp")), \
         patch("code_forge.doctor._check_registries",
               return_value=[]), \
         patch("code_forge.trust.trust_status") as mt:
        mt.return_value = MagicMock(trusted=True)
        run_doctor(cwd=ws, env={})

    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err


# -- T2e: real-path smoke tests --


def test_smoke_all_green(tmp_path, capsys):
    """T2e(a): trusted + subprocess + one api backend -> exit 0."""
    ws = tmp_path / "project"
    gate_dir = ws / ".code-forge"
    gate_dir.mkdir(parents=True)
    (gate_dir / "gate.yaml").write_text(textwrap.dedent("""\
        backends:
          demo:
            type: api
            model: test-model
            format: openai
            base_url: https://api.example.com/v1
            api_key_env: DEMO_API_KEY
        outlet: subprocess
    """))
    env = {"DEMO_API_KEY": "dummy-key-for-probe"}

    with patch("code_forge.doctor._check_handshake",
               return_value=(True, "code-forge-mcp")), \
         patch("code_forge.doctor._check_registries",
               return_value=[("Claude Code", "PRESENT")]), \
         patch("code_forge.trust.trust_status") as mt:
        mt.return_value = MagicMock(trusted=True)
        rc = run_doctor(cwd=ws, env=env)

    assert rc == 0
    out = capsys.readouterr().out
    assert "FAIL" not in out
    assert "SKIP" not in out


def test_smoke_no_backends(tmp_path, capsys):
    """T2e(b): no backends -> exit 1, backends FAIL."""
    ws = tmp_path / "project"
    gate_dir = ws / ".code-forge"
    gate_dir.mkdir(parents=True)
    (gate_dir / "gate.yaml").write_text("test:\n  section: true\n")

    with patch("code_forge.doctor._check_handshake",
               return_value=(True, "code-forge-mcp")), \
         patch("code_forge.doctor._check_registries",
               return_value=[]), \
         patch("code_forge.trust.trust_status") as mt:
        mt.return_value = MagicMock(trusted=True)
        rc = run_doctor(cwd=ws, env={})

    assert rc == 1
    out = capsys.readouterr().out
    assert "no backends configured" in out
    assert "FAIL" in out
