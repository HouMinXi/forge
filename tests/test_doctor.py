# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for code-forge doctor self-check command."""
from __future__ import annotations

import json
import os
import sys
import textwrap
from contextlib import ExitStack
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


class TestUserConfigLine:
    """Doctor reports the user-level config location every run.

    The line is informational only: never a FAIL row, never touches
    the exit code. Tests patch code_forge.user_config.user_config_path
    (NOT load_user_backends -- the conftest autouse fixture patches
    that, and patching it here would be silently neutered).
    """

    def _run(self, tmp_path, capsys):
        from code_forge.doctor import run_doctor
        rc = run_doctor(tmp_path, {})
        out = capsys.readouterr().out
        return rc, out

    def test_present_path_printed(self, tmp_path, capsys):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("backends: {}\n")
        with patch("code_forge.user_config.user_config_path",
                   return_value=cfg):
            rc, out = self._run(tmp_path, capsys)
        assert str(cfg) in out

    def test_absent_path_prints_would_be_location(self, tmp_path,
                                                  capsys):
        would_be = tmp_path / "xdg" / "code-forge" / "config.yaml"
        with patch("code_forge.user_config.user_config_path",
                   return_value=None), \
             patch("code_forge.user_config.user_config_dir",
                   return_value=would_be.parent):
            rc, out = self._run(tmp_path, capsys)
        assert str(would_be) in out
        assert "shared backends" in out

    def test_line_never_affects_exit_code(self, tmp_path, capsys):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("backends: {}\n")
        with patch("code_forge.user_config.user_config_path",
                   return_value=cfg):
            rc, _ = self._run(tmp_path, capsys)
        # An empty tmp workspace is a FAIL workspace; the user-config
        # line must not add to that. The assertion that matters: the
        # line is not itself a FAIL -- checked via the present-path
        # run's rc equaling the absent-path run's rc on the same ws.
        with patch("code_forge.user_config.user_config_path",
                   return_value=None), \
             patch("code_forge.user_config.user_config_dir",
                   return_value=tmp_path / "code-forge"):
            rc2, _ = self._run(tmp_path, capsys)
        assert rc == rc2

    def test_default_run_does_not_crash(self, tmp_path, capsys):
        # No patching of user_config at all: host may have a config or
        # not; either branch must render without raising.
        rc, out = self._run(tmp_path, capsys)
        assert "user config:" in out


class TestDoctorLive:
    """--live: opt-in real completions, additive rows, exit pipeline.

    The live helper is patched at code_forge.backend (doctor imports
    it function-locally, so the source module is the patch target).
    """

    def _workspace(self, tmp_path, backend_type="api"):
        ws = tmp_path / "project"
        gate_dir = ws / ".code-forge"
        gate_dir.mkdir(parents=True)
        if backend_type == "api":
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
        else:
            (gate_dir / "gate.yaml").write_text(textwrap.dedent("""\
                backends:
                  local:
                    type: cli
                    model: claude-sonnet-4-6
                outlet: subprocess
            """))
        (gate_dir / "tools.yaml").write_text(textwrap.dedent("""\
            tools:
              pyver:
                command: python3
                output_format: grep_line
                file_patterns: ["*.py"]
        """))
        return ws

    def _green_ctx(self):
        return [
            patch("code_forge.doctor._check_handshake",
                  return_value=(True, "code-forge-mcp")),
            patch("code_forge.doctor._check_registries",
                  return_value=[("Claude Code", "PRESENT")]),
            patch("code_forge.trust.trust_status",
                  return_value=MagicMock(trusted=True)),
        ]

    def test_default_path_never_calls_live_helper(self, tmp_path,
                                                  capsys):
        ws = self._workspace(tmp_path)
        with ExitStack() as stack, \
             patch("code_forge.backend.probe_backend_live") as live:
            for ctx in self._green_ctx():
                stack.enter_context(ctx)
            rc = run_doctor(cwd=ws, env={"DEMO_API_KEY": "k"})
        live.assert_not_called()
        assert rc == 0
        assert "live:" not in capsys.readouterr().out

    def test_live_on_success_row(self, tmp_path, capsys):
        from code_forge.backend import LiveProbeResult
        ws = self._workspace(tmp_path)
        with ExitStack() as stack, \
             patch("code_forge.backend.probe_backend_live",
                   return_value=LiveProbeResult(ok=True)) as live:
            for ctx in self._green_ctx():
                stack.enter_context(ctx)
            rc = run_doctor(cwd=ws, env={"DEMO_API_KEY": "k"},
                            live=True)
        live.assert_called_once()
        assert rc == 0
        out = capsys.readouterr().out
        assert "live: ok" in out
        # offline row still present alongside
        assert "demo (project)" in out

    def test_live_on_failure_fails_exit(self, tmp_path, capsys):
        from code_forge.backend import LiveProbeResult
        ws = self._workspace(tmp_path)
        fail = LiveProbeResult(
            ok=False, error_class="http-error",
            detail="code-forge: demo backend: HTTP error (404)",
            suggestion="Inspect the body excerpt",
        )
        with ExitStack() as stack, \
             patch("code_forge.backend.probe_backend_live",
                   return_value=fail):
            for ctx in self._green_ctx():
                stack.enter_context(ctx)
            rc = run_doctor(cwd=ws, env={"DEMO_API_KEY": "k"},
                            live=True)
        assert rc == 1
        out = capsys.readouterr().out
        assert "http-error" in out
        assert "FAIL" in out

    @pytest.mark.parametrize("error_class", [
        "timeout", "credential-rejected", "connection-refused",
        "SSE-mixed", "JSON-malformed", "truncated-output",
        "http-error", "unclassified",
    ])
    def test_all_eight_classes_render(self, tmp_path, capsys,
                                      error_class):
        from code_forge.backend import LiveProbeResult
        from code_forge.doctor import _check_backends
        gate_data = {"backends": {"demo": {
            "type": "api", "model": "m", "format": "openai",
            "base_url": "https://api.example.com/v1",
            "api_key_env": "DEMO_API_KEY",
        }}}
        result = LiveProbeResult(
            ok=False, error_class=error_class,
            detail="something failed", suggestion="do the thing",
        )
        with patch("code_forge.backend.probe_backend",
                   return_value=MagicMock(ok=True)), \
             patch("code_forge.backend.probe_backend_live",
                   return_value=result):
            diag, _ = _check_backends(
                Path("/ws"), gate_data, {}, live=True)
        live_rows = [m for ok, m in diag if "live:" in m]
        assert len(live_rows) == 1
        assert error_class in live_rows[0]
        assert "do the thing" in live_rows[0]
        assert any(ok is False for ok, _ in diag)

    def test_cli_backend_skips_live_helper(self, tmp_path, capsys):
        ws = self._workspace(tmp_path, backend_type="cli")
        with ExitStack() as stack, \
             patch("code_forge.backend.probe_backend_live") as live:
            for ctx in self._green_ctx():
                stack.enter_context(ctx)
            _rc = run_doctor(cwd=ws, env={}, live=True)
        live.assert_not_called()
        out = capsys.readouterr().out
        assert "skipped" in out
        assert "no live probe applies" in out


class TestDoctorLiveCliDispatch:
    """main()-level: the --live flag must reach run_doctor."""

    def test_doctor_live_flag_reaches_run_doctor(self, monkeypatch):
        from code_forge import cli as cli_mod

        calls = {}

        def fake_run_doctor(cwd, env, live=False):
            calls["live"] = live
            return 0

        monkeypatch.setattr("code_forge.doctor.run_doctor",
                            fake_run_doctor)
        monkeypatch.setattr(sys, "argv", [
            "code-forge", "doctor", "--live"])
        cli_mod.main()

        assert calls.get("live") is True
