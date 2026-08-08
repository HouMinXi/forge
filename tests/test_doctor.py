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


def test_backends_cli_error_returns_none_sentinel():
    """CliError (e.g. multiple defaults) returns configs=None, not [].

    configs=None signals "backends exist but are invalid" so run_doctor
    skips the outlet check. configs=[] means "no backends at all" and
    still triggers the outlet check. Confusing the two reintroduces the
    contradictory-diagnosis bug where doctor says both "multiple default
    backends" and "no backend configured" in the same run.
    """
    from code_forge.errors import CliError

    gate_data = {"backends": {"a": {}, "b": {}}}
    with patch("code_forge.backend.load_backend_configs",
               side_effect=CliError("multiple default backends: a, b")):
        with patch("code_forge.user_config.load_user_backends",
                   return_value={}):
            with patch("code_forge.user_config.merge_backends",
                       return_value={"a": {}, "b": {}}):
                diag, configs = _check_backends(
                    Path("/ws"), gate_data, {})

    assert configs is None, (
        "CliError must return configs=None (not []) to skip outlet check"
    )
    assert diag[0][0] is False
    assert "multiple default" in diag[0][1]


def test_backends_non_cli_error_returns_empty_list():
    """Non-CliError exceptions return configs=[] (genuinely no backends)."""
    gate_data = {"backends": {"a": {}}}
    with patch("code_forge.backend.load_backend_configs",
               side_effect=RuntimeError("yaml parse failed")):
        with patch("code_forge.user_config.load_user_backends",
                   return_value={}):
            with patch("code_forge.user_config.merge_backends",
                       return_value={"a": {}}):
                diag, configs = _check_backends(
                    Path("/ws"), gate_data, {})

    assert configs == [], (
        "Non-CliError must return configs=[] (not None)"
    )


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
    (gate_dir / "tools.yaml").write_text(textwrap.dedent("""\
        tools:
          pyver:
            command: python3
            output_format: grep_line
            file_patterns: ["*.py"]
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
    assert "tool-audit:" in out


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


# -- _check_hook_drift --


def _init_repo(path: Path) -> None:
    import subprocess
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    gate_dir = path / ".code-forge"
    gate_dir.mkdir(exist_ok=True)
    (gate_dir / "gate.yaml").write_text(textwrap.dedent("""\
        test:
          command: [pytest, -q]
          timeout_seconds: 900
    """))


def _install(path: Path) -> Path:
    import io

    from code_forge.install_hooks import run_install_hooks
    rc = run_install_hooks(
        args=None, env={}, cwd=path,
        stdout=io.StringIO(), stderr=io.StringIO())
    assert rc == 0
    return path / ".git" / "hooks"


def _by_hook(results):
    return dict((msg.split(":")[0], ok) for ok, msg in results)


def test_hook_drift_current_is_silent(tmp_path):
    from code_forge.doctor import _check_hook_drift
    _init_repo(tmp_path)
    _install(tmp_path)
    results = _check_hook_drift(tmp_path)
    assert [ok for ok, _ in results] == [True, True]
    assert all("current" in msg for _, msg in results)


def test_hook_drift_stale_pre_commit_fails(tmp_path):
    from code_forge.doctor import _check_hook_drift
    _init_repo(tmp_path)
    hooks = _install(tmp_path)
    hook = hooks / "pre-commit"
    # The e605b26 regression verbatim: verify's real reason swallowed and
    # replaced by a generic line.
    text = hook.read_text()
    assert 'VERIFY_OUT=$(code-forge verify 2>&1)' in text
    hook.write_text(text.replace(
        '    VERIFY_OUT=$(code-forge verify 2>&1) || {\n'
        '        echo "$VERIFY_OUT" >&2\n',
        '    code-forge verify --quiet 2>/dev/null || {\n'
        '        echo "code-forge: receipt verification failed." >&2\n'))

    results = _by_hook(_check_hook_drift(tmp_path))
    assert results["pre-commit"] is False
    # Only the changed hook is reported: a check that flags everything has
    # no negatives, so it can never report a false one.
    assert results["commit-msg"] is True


def test_hook_drift_stale_commit_msg_fails(tmp_path):
    from code_forge.doctor import _check_hook_drift
    _init_repo(tmp_path)
    hooks = _install(tmp_path)
    hook = hooks / "commit-msg"
    hook.write_text(hook.read_text().replace("head -5", "head -3"))

    results = _by_hook(_check_hook_drift(tmp_path))
    assert results["commit-msg"] is False
    assert results["pre-commit"] is True


def test_hook_drift_missing_hook_skips(tmp_path):
    from code_forge.doctor import _check_hook_drift
    _init_repo(tmp_path)
    hooks = _install(tmp_path)
    (hooks / "pre-commit").unlink()
    results = _by_hook(_check_hook_drift(tmp_path))
    assert results["pre-commit"] is None
    assert results["commit-msg"] is True


def test_hook_drift_foreign_hook_skips(tmp_path):
    from code_forge.doctor import _check_hook_drift
    _init_repo(tmp_path)
    hooks = _install(tmp_path)
    (hooks / "pre-commit").write_text("#!/bin/sh\n# a hook forge did not write\n")
    results = _by_hook(_check_hook_drift(tmp_path))
    assert results["pre-commit"] is None


def test_hook_drift_chained_hook_is_current(tmp_path):
    """A hook chaining a backup is current, not drift."""
    from code_forge.doctor import _check_hook_drift
    _init_repo(tmp_path)
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / "pre-commit").write_text("#!/bin/sh\necho existing\n")
    (hooks_dir / "commit-msg").write_text("#!/bin/sh\necho existing\n")
    _install(tmp_path)
    assert (hooks_dir / "pre-commit.code-forge-backup").exists()
    results = _check_hook_drift(tmp_path)
    assert [ok for ok, _ in results] == [True, True]


def test_hook_drift_non_git_is_silent(tmp_path):
    from code_forge.doctor import _check_hook_drift
    gate_dir = tmp_path / ".code-forge"
    gate_dir.mkdir()
    (gate_dir / "gate.yaml").write_text("test:\n  section: true\n")
    assert _check_hook_drift(tmp_path) == []


def test_hook_drift_ungeneratable_gate_fails(tmp_path):
    """A forge hook is installed but gate.yaml can no longer produce one."""
    from code_forge.doctor import _check_hook_drift
    _init_repo(tmp_path)
    _install(tmp_path)
    (tmp_path / ".code-forge" / "gate.yaml").write_text("backends: {}\n")
    results = _check_hook_drift(tmp_path)
    assert any(ok is False and "cannot regenerate" in msg
               for ok, msg in results)
    # The multi-line gate.yaml error must not shred the diagnostic table.
    assert all("\n" not in msg for _, msg in results)


def test_doctor_reports_hook_drift(tmp_path, capsys):
    """run_doctor surfaces a stale hook as a FAIL row and exit 1."""
    _init_repo(tmp_path)
    hooks = _install(tmp_path)
    hook = hooks / "pre-commit"
    hook.write_text(hook.read_text().replace(
        "exec ", "# drifted\nexec ", 1))
    (tmp_path / ".code-forge" / "gate.yaml").write_text(textwrap.dedent("""\
        test:
          command: [pytest, -q]
          timeout_seconds: 900
        backends:
          demo:
            type: api
            model: test-model
            format: openai
            base_url: https://api.example.com/v1
            api_key_env: DEMO_API_KEY
        outlet: subprocess
    """))
    with patch("code_forge.doctor._check_handshake",
               return_value=(True, "code-forge-mcp")), \
         patch("code_forge.doctor._check_registries",
               return_value=[]), \
         patch("code_forge.trust.trust_status") as mt:
        mt.return_value = MagicMock(trusted=True)
        rc = run_doctor(cwd=tmp_path, env={"DEMO_API_KEY": "k"})
    out = capsys.readouterr().out
    assert "hooks:" in out
    assert "run code-forge install-hooks" in out
    assert rc == 1
