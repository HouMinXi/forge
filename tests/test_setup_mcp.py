# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for setup-mcp subcommand."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
import yaml

from code_forge.setup_mcp import (
    PRESETS,
    _detect_backends,
    _render_user_config,
    run_setup_mcp,
)


class TestPresets:
    """Preset table consistency."""

    def test_all_presets_have_required_fields(self):
        for name, p in PRESETS.items():
            assert p.name == name
            assert p.format in ("openai", "anthropic")
            assert p.base_url.startswith("https://")
            assert p.api_key_env
            assert p.model
            assert p.max_tokens > 0

    def test_no_vertex_preset(self):
        """Vertex is project-specific, not included in presets."""
        assert "vertex" not in PRESETS


class TestDetectBackends:
    """Auto-detection from environment."""

    def test_detects_set_keys(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}, clear=False):
            detected = _detect_backends()
        names = [p.name for p in detected]
        assert "deepseek" in names

    def test_empty_when_no_keys(self):
        env_clean = {k: "" for k in [p.api_key_env for p in PRESETS.values()]}
        with patch.dict(os.environ, env_clean, clear=False):
            assert _detect_backends() == []

    def test_multi_key_default_is_first_detected(self):
        """When multiple keys present, first in PRESETS insertion order wins."""
        keys = {p.api_key_env: "sk-test" for p in PRESETS.values()}
        with patch.dict(os.environ, keys, clear=False):
            detected = _detect_backends()
        assert len(detected) == len(PRESETS)
        content = _render_user_config(detected)
        data = yaml.safe_load(content)
        first_name = list(data["backends"].keys())[0]
        assert data["backends"][first_name].get("default") is True
        for name in list(data["backends"].keys())[1:]:
            assert "default" not in data["backends"][name]


class TestRenderUserConfig:
    """User config YAML rendering."""

    def test_first_preset_gets_default(self):
        presets = [PRESETS["deepseek"], PRESETS["glm"]]
        content = _render_user_config(presets)
        data = yaml.safe_load(content)
        assert data["backends"]["deepseek"]["default"] is True
        assert "default" not in data["backends"]["glm"]

    def test_renders_all_required_fields(self):
        content = _render_user_config([PRESETS["kimi"]])
        data = yaml.safe_load(content)
        b = data["backends"]["kimi"]
        assert b["type"] == "api"
        assert b["format"] == "openai"
        assert b["base_url"] == "https://api.kimi.com/coding/v1"
        assert b["api_key_env"] == "KIMI_API_KEY"
        assert b["model"] == "K2.7-Code"
        assert b["max_tokens"] == 32768


class TestRunSetupMcp:
    """End-to-end setup-mcp behavior."""

    def test_writes_user_config_and_gate_yaml(self, tmp_path):
        user_dir = tmp_path / "user-config"
        project = tmp_path / "project"
        project.mkdir()
        with patch.dict(os.environ, {"FORGE_CONFIG_DIR": str(user_dir)}, clear=False):
            rc = run_setup_mcp(project, ["deepseek"])
        assert rc == 0
        assert (user_dir / "config.yaml").exists()
        assert (project / ".code-forge" / "gate.yaml").exists()

        # User config has the backend
        data = yaml.safe_load((user_dir / "config.yaml").read_text())
        assert "deepseek" in data["backends"]

        # Project gate.yaml has no backends (C1)
        gate = yaml.safe_load((project / ".code-forge" / "gate.yaml").read_text())
        assert "backends" not in gate
        assert gate["outlet"] == "subprocess"

    def test_idempotent_no_overwrite(self, tmp_path):
        user_dir = tmp_path / "user-config"
        project = tmp_path / "project"
        project.mkdir()
        with patch.dict(os.environ, {"FORGE_CONFIG_DIR": str(user_dir)}, clear=False):
            run_setup_mcp(project, ["deepseek"])
            # Write a marker to detect overwrite
            marker = "# do not touch\n"
            cfg = user_dir / "config.yaml"
            cfg.write_text(marker)
            run_setup_mcp(project, ["deepseek"])
            assert cfg.read_text() == marker

    def test_force_overwrites(self, tmp_path):
        user_dir = tmp_path / "user-config"
        project = tmp_path / "project"
        project.mkdir()
        with patch.dict(os.environ, {"FORGE_CONFIG_DIR": str(user_dir)}, clear=False):
            run_setup_mcp(project, ["deepseek"])
            run_setup_mcp(project, ["glm"], force=True)
            data = yaml.safe_load((user_dir / "config.yaml").read_text())
            assert "glm" in data["backends"]
            assert "deepseek" not in data["backends"]  # --force replaces, not merges
            gate = yaml.safe_load(
                (project / ".code-forge" / "gate.yaml").read_text()
            )
            assert gate["outlet"] == "subprocess"  # gate.yaml also replaced

    def test_dry_run_writes_nothing(self, tmp_path):
        user_dir = tmp_path / "user-config"
        project = tmp_path / "project"
        project.mkdir()
        with patch.dict(os.environ, {"FORGE_CONFIG_DIR": str(user_dir)}, clear=False):
            rc = run_setup_mcp(project, ["deepseek"], dry_run=True)
        assert rc == 0
        assert not (user_dir / "config.yaml").exists()
        assert not (project / ".code-forge" / "gate.yaml").exists()

    def test_unknown_preset_returns_error(self, tmp_path):
        rc = run_setup_mcp(tmp_path, ["nonexistent"])
        assert rc == 1

    def test_auto_trust_on_new_gate_yaml(self, tmp_path):
        """Auto-trust only the gate.yaml we just wrote."""
        user_dir = tmp_path / "user-config"
        project = tmp_path / "project"
        project.mkdir()
        with patch.dict(os.environ, {"FORGE_CONFIG_DIR": str(user_dir)}, clear=False):
            run_setup_mcp(project, ["deepseek"])

        gate_path = project / ".code-forge" / "gate.yaml"
        gate_data = yaml.safe_load(gate_path.read_text())

        from code_forge.trust import is_trusted
        assert is_trusted(gate_path, gate_data)

    def test_no_auto_trust_on_existing_gate_yaml(self, tmp_path):
        """Existing gate.yaml is not auto-trusted by setup-mcp."""
        user_dir = tmp_path / "user-config"
        project = tmp_path / "project"
        gate_dir = project / ".code-forge"
        gate_dir.mkdir(parents=True)
        gate_path = gate_dir / "gate.yaml"
        gate_path.write_text("outlet: subprocess\n")

        with patch.dict(os.environ, {"FORGE_CONFIG_DIR": str(user_dir)}, clear=False):
            run_setup_mcp(project, ["deepseek"])

        gate_data = yaml.safe_load(gate_path.read_text())
        from code_forge.trust import is_trusted
        # Existing file was not touched, so not auto-trusted
        assert not is_trusted(gate_path, gate_data)


class TestCheckBackendMergedView:
    """Precheck reads user-level backends via merge."""

    def test_zero_project_backends_user_backend_passes(self, tmp_path):
        """Project has gate.yaml with no backends; user config has one.
        _check_backend should pass (not raise ToolError)."""
        import code_forge.mcp_server as mod

        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir()
        gate_path = gate_dir / "gate.yaml"
        gate_path.write_text("outlet: subprocess\n")

        # Trust the gate.yaml so _load_gate_backends returns gate_data
        gate_data = yaml.safe_load(gate_path.read_text())
        from code_forge.trust import record_trust
        record_trust(gate_path, gate_data)

        user_raw = {"smoke-ds": {
            "type": "api", "format": "openai",
            "base_url": "http://localhost:9999",
            "api_key_env": "TEST_KEY",
            "model": "test",
        }}

        with (
            patch("code_forge.user_config.load_user_backends",
                  return_value=user_raw),
            patch.dict(os.environ, {"TEST_KEY": "fake-key"}),
        ):
            # Should NOT raise
            mod._check_backend(tmp_path)

    def test_zero_project_zero_user_raises(self, tmp_path):
        """No backends anywhere -> _check_backend raises ToolError."""
        import code_forge.mcp_server as mod
        from mcp.server.fastmcp.exceptions import ToolError

        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir()
        gate_path = gate_dir / "gate.yaml"
        gate_path.write_text("outlet: subprocess\n")

        gate_data = yaml.safe_load(gate_path.read_text())
        from code_forge.trust import record_trust
        record_trust(gate_path, gate_data)

        with patch("code_forge.user_config.load_user_backends",
                   return_value={}):
            with pytest.raises(ToolError, match="No review backends"):
                mod._check_backend(tmp_path)

    def test_project_and_user_backends_both_visible(self, tmp_path):
        """Project has one backend, user has another. Merged set includes both."""
        import code_forge.mcp_server as mod

        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir()
        gate_path = gate_dir / "gate.yaml"
        gate_path.write_text(
            "outlet: subprocess\n"
            "backends:\n"
            "  proj-ds:\n"
            "    type: api\n"
            "    format: openai\n"
            "    base_url: https://api.deepseek.com/v1\n"
            "    api_key_env: TEST_PROJ_KEY\n"
            "    model: test\n"
        )
        gate_data = yaml.safe_load(gate_path.read_text())
        from code_forge.trust import record_trust
        record_trust(gate_path, gate_data)

        user_raw = {"user-mimo": {
            "type": "api", "format": "anthropic",
            "base_url": "http://localhost:9999",
            "api_key_env": "TEST_USER_KEY",
            "model": "test",
        }}

        with (
            patch("code_forge.user_config.load_user_backends",
                  return_value=user_raw),
            patch.dict(os.environ, {
                "TEST_PROJ_KEY": "k1", "TEST_USER_KEY": "k2",
            }),
        ):
            mod._check_backend(tmp_path)
