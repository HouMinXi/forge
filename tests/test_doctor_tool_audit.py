# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for the doctor tool-audit check."""
from __future__ import annotations

import stat
import textwrap
from pathlib import Path
from subprocess import TimeoutExpired
from unittest.mock import MagicMock, patch

from code_forge.doctor import _audit_tools, run_doctor


# -- Fixture helpers -------------------------------------------------------

_FIXTURE_TEMPLATE = textwrap.dedent("""\
    tools:
      %s:
        command: %s
        output_format: grep_line
        file_patterns: ["*.py"]
""")


def _write_tools_yaml(
    ws: Path, name: str = "testtool",
    command: str = "python3",
) -> Path:
    """Write a minimal valid tools.yaml and return its path."""
    gate_dir = ws / ".code-forge"
    gate_dir.mkdir(parents=True, exist_ok=True)
    path = gate_dir / "tools.yaml"
    path.write_text(_FIXTURE_TEMPLATE % (name, command))
    return path


# -- Unit tests ------------------------------------------------------------

class TestAuditTools:
    """Unit tests for _audit_tools."""

    def test_pass_when_installed(self, tmp_path):
        """Test 1: capture_tool_version returns version -> PASS."""
        _write_tools_yaml(tmp_path, command="python3")
        results = _audit_tools(tmp_path)
        assert len(results) == 1
        ok, msg = results[0]
        assert ok is True
        assert "testtool" in msg

    def test_fail_when_not_installed(self, tmp_path):
        """Test 2: capture_tool_version returns not_installed -> FAIL."""
        _write_tools_yaml(
            tmp_path, name="missing",
            command="nonexistent-binary-xyz",
        )
        results = _audit_tools(tmp_path)
        assert len(results) == 1
        ok, msg = results[0]
        assert ok is False
        assert "not_installed" in msg
        assert "missing" in msg

    def test_pass_when_unknown(self, tmp_path):
        """Test 3: capture_tool_version returns unknown -> PASS."""
        _write_tools_yaml(tmp_path, command="python3")
        with patch(
            "code_forge.runner._resolve_command",
            return_value="/usr/bin/python3",
        ), patch(
            "code_forge.runner.subprocess.run",
            side_effect=TimeoutExpired(cmd="python3", timeout=5),
        ):
            results = _audit_tools(tmp_path)
        assert len(results) == 1
        ok, msg = results[0]
        assert ok is True
        assert "unknown" in msg

    def test_pass_relative_path_tool(self, tmp_path):
        """Test 4: relative-path tool resolves via chdir (no monkeypatch)."""
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        script = scripts_dir / "checkpatch.pl"
        script.write_text("#!/bin/sh\necho \"1.0.0\"\n")
        script.chmod(script.stat().st_mode
                      | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        _write_tools_yaml(
            tmp_path, name="checkpatch",
            command="scripts/checkpatch.pl",
        )
        # Drive from pytest's default CWD -- the audit's own chdir
        # must anchor resolution to tmp_path.  NO monkeypatch.chdir.
        results = _audit_tools(tmp_path)
        assert len(results) == 1
        ok, msg = results[0]
        assert ok is True
        assert "checkpatch" in msg

    def test_skip_when_no_tools_yaml(self, tmp_path):
        """Test 5: tools.yaml missing -> SKIP (ok=None)."""
        results = _audit_tools(tmp_path)
        assert len(results) == 1
        ok, msg = results[0]
        assert ok is None
        assert "no tools.yaml" in msg

    def test_fail_when_malformed_yaml(self, tmp_path):
        """Test 6: tools.yaml malformed -> FAIL with error message."""
        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir(parents=True)
        (gate_dir / "tools.yaml").write_text("{{invalid yaml\n")
        results = _audit_tools(tmp_path)
        assert len(results) == 1
        ok, msg = results[0]
        assert ok is False
        assert "tools.yaml error" in msg

    def test_skip_when_empty_registry(self, tmp_path):
        """Test 7: empty registry -> SKIP (ok=None)."""
        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir(parents=True)
        (gate_dir / "tools.yaml").write_text("tools: {}\n")
        results = _audit_tools(tmp_path)
        assert len(results) == 1
        ok, msg = results[0]
        assert ok is None
        assert "no tools configured" in msg

    def test_skip_cargo_root(self, tmp_path):
        """Test 8: tool with cargo_root -> SKIP with tool name."""
        _write_tools_yaml(tmp_path, name="clippy", command="cargo clippy")
        fake_tc = MagicMock()
        fake_tc.name = "clippy"
        fake_tc.command = "cargo clippy"
        fake_tc.working_dir = "cargo_root"
        with patch(
            "code_forge.registry.load_registry",
            return_value={"clippy": fake_tc},
        ):
            results = _audit_tools(tmp_path)
        assert len(results) == 1
        ok, msg = results[0]
        assert ok is None
        assert "clippy" in msg
        assert "cargo_root" in msg

    def test_bug_inject_resolve_command_returns_none(self, tmp_path):
        """Test 9: bug-inject -- _resolve_command returns None -> FAIL."""
        _write_tools_yaml(tmp_path, command="python3")
        # With mock: all commands fail to resolve
        with patch(
            "code_forge.runner._resolve_command",
            return_value=None,
        ):
            results = _audit_tools(tmp_path)
        assert len(results) == 1
        ok, msg = results[0]
        assert ok is False
        assert "not_installed" in msg

        # Without mock: resolves normally -> PASS
        results = _audit_tools(tmp_path)
        assert len(results) == 1
        ok, msg = results[0]
        assert ok is True

    def test_fail_whitespace_command(self, tmp_path):
        """Whitespace-only command reports not_installed, no crash.

        The message must be "not_installed" (resolver guard) --
        an "audit error" message would mean the guard is gone and
        the per-tool handler swallowed an IndexError instead.
        """
        _write_tools_yaml(tmp_path, name="blank", command='"  "')
        results = _audit_tools(tmp_path)
        assert len(results) == 1
        ok, msg = results[0]
        assert ok is False
        assert "not_installed" in msg


# -- Integration test ------------------------------------------------------

class TestDoctorIntegration:
    """Integration test: run_doctor catches missing tool."""

    def test_run_doctor_catches_missing_tool(self, tmp_path, capsys):
        """Audit FAIL is the sole exit-1 source."""
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
              bad-tool:
                command: nonexistent-binary-xyz
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

        assert rc == 1
        out = capsys.readouterr().out
        assert "bad-tool: not_installed" in out
        assert "FAIL" in out
