# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for forge.registry -- YAML tool config loader and file matcher."""

import pytest

from code_forge.registry import ToolConfig, load_registry, match_tools


class TestToolConfigDefaults:
    """Verify ToolConfig field defaults."""

    def test_defaults(self):
        tc = ToolConfig(
            name="t",
            command="t",
            args=[],
            output_format="x",
            file_patterns=["*"],
        )
        assert tc.required is False
        assert tc.timeout == 30
        assert tc.exclude_patterns == []
        assert tc.enabled is True
        assert tc.working_dir is None


class TestLoadRegistry:
    """Tests for load_registry()."""

    def test_valid_yaml(self, tmp_path):
        """Valid YAML returns dict[str, ToolConfig] with correct fields."""
        yaml_file = tmp_path / "tools.yaml"
        yaml_file.write_text(
            "tools:\n"
            "  shellcheck:\n"
            "    command: shellcheck\n"
            "    args: ['-f', 'json1']\n"
            "    output_format: shellcheck_json\n"
            "    file_patterns: ['*.sh', '*.bash']\n"
            "    required: true\n"
            "    timeout: 60\n"
        )
        registry = load_registry(str(yaml_file))
        assert "shellcheck" in registry
        tc = registry["shellcheck"]
        assert tc.command == "shellcheck"
        assert tc.args == ["-f", "json1"]
        assert tc.output_format == "shellcheck_json"
        assert tc.file_patterns == ["*.sh", "*.bash"]
        assert tc.required is True
        assert tc.timeout == 60
        assert tc.enabled is True

    def test_missing_file_raises(self, tmp_path):
        """Missing file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_registry(str(tmp_path / "nonexistent.yaml"))

    def test_malformed_yaml_syntax_raises(self, tmp_path):
        """Invalid YAML syntax raises ValueError."""
        yaml_file = tmp_path / "tools.yaml"
        yaml_file.write_text("tools:\n  broken: [\n")
        with pytest.raises(ValueError, match="Invalid YAML"):
            load_registry(str(yaml_file))

    def test_tools_as_list_raises(self, tmp_path):
        """tools as list instead of mapping raises ValueError."""
        yaml_file = tmp_path / "tools.yaml"
        yaml_file.write_text("tools:\n  - shellcheck\n  - ruff\n")
        with pytest.raises(ValueError, match="must be a mapping"):
            load_registry(str(yaml_file))

    def test_file_patterns_null_raises(self, tmp_path):
        """file_patterns: null raises ValueError (required field)."""
        yaml_file = tmp_path / "tools.yaml"
        yaml_file.write_text(
            "tools:\n"
            "  bad:\n"
            "    command: x\n"
            "    args: []\n"
            "    output_format: x\n"
            "    file_patterns: null\n"
        )
        with pytest.raises(ValueError, match="cannot be null"):
            load_registry(str(yaml_file))

    def test_file_patterns_as_string_raises(self, tmp_path):
        """file_patterns as string instead of list raises ValueError."""
        yaml_file = tmp_path / "tools.yaml"
        yaml_file.write_text(
            "tools:\n"
            "  bad:\n"
            "    command: x\n"
            "    args: []\n"
            "    output_format: x\n"
            "    file_patterns: '*.sh'\n"
        )
        with pytest.raises(ValueError, match="must be a list"):
            load_registry(str(yaml_file))

    def test_malformed_yaml_missing_command(self, tmp_path):
        """Missing required 'command' key raises ValueError."""
        yaml_file = tmp_path / "tools.yaml"
        yaml_file.write_text(
            "tools:\n"
            "  broken:\n"
            "    args: []\n"
            "    output_format: x\n"
            "    file_patterns: ['*']\n"
        )
        with pytest.raises(ValueError, match="command"):
            load_registry(str(yaml_file))

    def test_malformed_yaml_missing_output_format(self, tmp_path):
        """Missing required 'output_format' key raises ValueError."""
        yaml_file = tmp_path / "tools.yaml"
        yaml_file.write_text(
            "tools:\n"
            "  broken:\n"
            "    command: x\n"
            "    args: []\n"
            "    file_patterns: ['*']\n"
        )
        with pytest.raises(ValueError, match="output_format"):
            load_registry(str(yaml_file))

    def test_empty_tools_returns_empty(self, tmp_path):
        """Empty tools dict returns empty dict."""
        yaml_file = tmp_path / "tools.yaml"
        yaml_file.write_text("tools: {}\n")
        registry = load_registry(str(yaml_file))
        assert registry == {}

    def test_filters_disabled_entries(self, tmp_path):
        """Entries with enabled=false are filtered out (Round 3 C-4)."""
        yaml_file = tmp_path / "tools.yaml"
        yaml_file.write_text(
            "tools:\n"
            "  active:\n"
            "    command: ruff\n"
            "    args: ['check']\n"
            "    output_format: ruff_json\n"
            "    file_patterns: ['*.py']\n"
            "  disabled:\n"
            "    command: checkpatch\n"
            "    args: []\n"
            "    output_format: checkpatch\n"
            "    file_patterns: ['*.c']\n"
            "    enabled: false\n"
        )
        registry = load_registry(str(yaml_file))
        assert "active" in registry
        assert "disabled" not in registry

    def test_warns_unknown_output_format(self, tmp_path, caplog):
        """Unknown output_format logs a warning but does not crash."""
        yaml_file = tmp_path / "tools.yaml"
        yaml_file.write_text(
            "tools:\n"
            "  weird:\n"
            "    command: weird\n"
            "    args: []\n"
            "    output_format: unknown_format_xyz\n"
            "    file_patterns: ['*']\n"
        )
        import logging
        with caplog.at_level(logging.WARNING):
            registry = load_registry(str(yaml_file))
        assert "weird" in registry
        assert "unknown_format_xyz" in caplog.text


class TestMatchTools:
    """Tests for match_tools()."""

    def test_matches_glob_patterns(self):
        """Returns correct tool-to-file mapping based on glob patterns."""
        registry = {
            "shellcheck": ToolConfig(
                name="shellcheck",
                command="shellcheck",
                args=[],
                output_format="shellcheck_json",
                file_patterns=["*.sh", "*.bash"],
            ),
            "ruff": ToolConfig(
                name="ruff",
                command="ruff",
                args=[],
                output_format="ruff_json",
                file_patterns=["*.py"],
            ),
        }
        files = ["main.py", "test.sh", "setup.py", "README.md"]
        result = match_tools(registry, files)
        assert set(result["ruff"]) == {"main.py", "setup.py"}
        assert result["shellcheck"] == ["test.sh"]

    def test_no_matching_files(self):
        """No matching files returns empty lists."""
        registry = {
            "ruff": ToolConfig(
                name="ruff",
                command="ruff",
                args=[],
                output_format="ruff_json",
                file_patterns=["*.py"],
            ),
        }
        files = ["README.md", "test.sh"]
        result = match_tools(registry, files)
        assert result["ruff"] == []

    def test_exclude_patterns(self):
        """Exclude patterns filter out matching files."""
        registry = {
            "ruff": ToolConfig(
                name="ruff",
                command="ruff",
                args=[],
                output_format="ruff_json",
                file_patterns=["*.py"],
                exclude_patterns=["test_*.py"],
            ),
        }
        files = ["main.py", "test_main.py", "setup.py"]
        result = match_tools(registry, files)
        assert set(result["ruff"]) == {"main.py", "setup.py"}
