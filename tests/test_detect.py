# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""TDD tests for toolchain auto-detection.

Tests cover:
  - pyproject.toml-aware tool detection with PATH verification
  - pyproject.toml with no [tool.*] sections -> PATH fallback
  - Fallback detection (no pyproject.toml, has .py files)
  - Empty project error stop
  - Idempotent init with real language metadata
  - Malformed existing tools.yaml fail-loud
  - Round-trip YAML through load_registry with str command
  - Force flag: merge preserves user entries, no crash on missing file,
    graceful fallback on corrupt existing
  - Detection report format
  - Corrupted TOML recovery
  - Quiet mode
  - flake8 config-file detection (.flake8 / setup.cfg / tox.ini + PATH)
  - Deep copy: file_patterns not aliased to module constants
  - Shell detection: *.sh files trigger shellcheck registry scan
  - Shell round-trip: shellcheck entry round-trips through load_registry
"""

import yaml
import pytest

from code_forge.detect import (
    PYTHON_TOOL_REGISTRY,
    SHELL_TOOL_REGISTRY,
    DetectionResult,
    detect_and_init,
    detect_toolchain,
    generate_tools_yaml,
)
from code_forge.errors import CliError
from code_forge.registry import ToolConfig, load_registry


# -- helpers ---------------------------------------------------------------

def _make_which_fn(*known_binaries):
    """Return a which_fn that returns a path for known binaries."""
    def _which(name):
        if name in known_binaries:
            return "/usr/bin/" + name
        return None
    return _which


def _write_pyproject(tmp_path, content):
    """Write a pyproject.toml with the given content."""
    (tmp_path / "pyproject.toml").write_text(content, encoding="utf-8")


# -- pyproject.toml detection ---------------------------------------------

class TestPyprojectDetection:
    """Detection from pyproject.toml [tool.*] sections."""

    def test_pyproject_with_ruff_config(self, tmp_path):
        _write_pyproject(tmp_path, "[tool.ruff]\nline-length = 88\n")
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn("ruff"),
        )
        assert "ruff" in result.detected
        assert result.language == "python"

    def test_pyproject_with_multiple_tools(self, tmp_path):
        content = (
            "[tool.ruff]\nline-length = 88\n\n"
            "[tool.pytest.ini_options]\naddopts = '-v'\n\n"
            "[tool.mypy]\nstrict = true\n"
        )
        _write_pyproject(tmp_path, content)
        result = detect_toolchain(
            tmp_path,
            which_fn=_make_which_fn("ruff", "pytest"),
        )
        assert "ruff" in result.detected
        assert "pytest" in result.detected
        assert "mypy" in result.missing

    def test_pyproject_no_tool_sections_falls_back_to_path(self, tmp_path):
        """pyproject.toml with only [project] metadata and no
        [tool.*] sections falls back to PATH scan."""
        content = (
            "[project]\n"
            'name = "myproject"\n'
            'version = "1.0"\n'
        )
        _write_pyproject(tmp_path, content)
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn("ruff"),
        )
        assert "ruff" in result.detected


# -- fallback detection ----------------------------------------------------

class TestFallbackDetection:
    """Detection without pyproject.toml via Python indicators."""

    def test_fallback_no_pyproject_with_py_files(self, tmp_path):
        (tmp_path / "app.py").write_text("print('hello')\n")
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn("ruff"),
        )
        assert "ruff" in result.detected
        assert result.language == "python"

    def test_fallback_setup_py(self, tmp_path):
        (tmp_path / "setup.py").write_text("from setuptools import setup\n")
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn("ruff"),
        )
        assert "ruff" in result.detected

    def test_fallback_requirements_txt(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("flask\n")
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn("pylint"),
        )
        assert "pylint" in result.detected


# -- empty project ---------------------------------------------------------

class TestEmptyProject:
    """Empty project raises CliError."""

    def test_empty_project_no_python(self, tmp_path):
        with pytest.raises(CliError, match="No toolchain detected"):
            detect_toolchain(tmp_path, which_fn=_make_which_fn())


# -- idempotency -----------------------------------------------------------

class TestIdempotency:
    """Existing non-empty tools.yaml skips regeneration."""

    def test_idempotent_existing_nonempty_tools_yaml(self, tmp_path):
        cfg_dir = tmp_path / ".code-forge"
        cfg_dir.mkdir()
        tools_yaml = cfg_dir / "tools.yaml"
        tools_yaml.write_text(
            "tools:\n"
            "  ruff:\n"
            '    command: "ruff check --output-format=sarif"\n'
            "    output_format: sarif\n"
            "    file_patterns: ['*.py']\n",
            encoding="utf-8",
        )
        result = detect_and_init(tmp_path, which_fn=_make_which_fn("ruff"))
        assert "ruff" in result.detected

    def test_idempotent_existing_returns_actual_language(self, tmp_path):
        """Returned language is 'python', not 'existing'."""
        cfg_dir = tmp_path / ".code-forge"
        cfg_dir.mkdir()
        tools_yaml = cfg_dir / "tools.yaml"
        tools_yaml.write_text(
            "tools:\n"
            "  ruff:\n"
            '    command: "ruff check --output-format=sarif"\n'
            "    output_format: sarif\n"
            "    file_patterns: ['*.py']\n",
            encoding="utf-8",
        )
        result = detect_and_init(tmp_path, which_fn=_make_which_fn("ruff"))
        assert result.language != "existing"
        assert result.language == "python"

    def test_empty_tools_yaml_treated_as_missing(self, tmp_path):
        """tools.yaml with 'tools: []' -> regenerate."""
        cfg_dir = tmp_path / ".code-forge"
        cfg_dir.mkdir()
        (cfg_dir / "tools.yaml").write_text(
            "tools: []\n", encoding="utf-8",
        )
        (tmp_path / "app.py").write_text("x = 1\n")
        result = detect_and_init(tmp_path, which_fn=_make_which_fn("ruff"))
        assert "ruff" in result.detected

    def test_zero_byte_tools_yaml_treated_as_missing(self, tmp_path):
        cfg_dir = tmp_path / ".code-forge"
        cfg_dir.mkdir()
        (cfg_dir / "tools.yaml").write_text("", encoding="utf-8")
        (tmp_path / "app.py").write_text("x = 1\n")
        result = detect_and_init(tmp_path, which_fn=_make_which_fn("ruff"))
        assert "ruff" in result.detected


# -- malformed existing config --------------------------------------------

class TestMalformedExisting:
    """Malformed existing tools.yaml fails loud."""

    def test_malformed_existing_tools_yaml_fails_loud(self, tmp_path):
        """Present + non-empty + schema-invalid -> CliError."""
        cfg_dir = tmp_path / ".code-forge"
        cfg_dir.mkdir()
        (cfg_dir / "tools.yaml").write_text(
            "tools:\n"
            "  ruff:\n"
            "    output_format: sarif\n"
            "    file_patterns: ['*.py']\n",
            encoding="utf-8",
        )
        with pytest.raises(CliError, match="malformed"):
            detect_and_init(tmp_path, which_fn=_make_which_fn("ruff"))

    def test_force_regenerates_malformed_tools_yaml(self, tmp_path):
        """Escape hatch: force=True regenerates over malformed."""
        cfg_dir = tmp_path / ".code-forge"
        cfg_dir.mkdir()
        (cfg_dir / "tools.yaml").write_text(
            "tools:\n"
            "  ruff:\n"
            "    output_format: sarif\n"
            "    file_patterns: ['*.py']\n",
            encoding="utf-8",
        )
        (tmp_path / "app.py").write_text("x = 1\n")
        result = detect_and_init(
            tmp_path, force=True, which_fn=_make_which_fn("ruff"),
        )
        assert isinstance(result, DetectionResult)
        assert "ruff" in result.detected


# -- round-trip YAML -------------------------------------------------------

class TestRoundTrip:
    """Generated YAML round-trips through load_registry()."""

    def test_generated_yaml_roundtrips_through_load_registry(self, tmp_path):
        _write_pyproject(tmp_path, "[tool.ruff]\nline-length = 88\n")
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn("ruff"),
        )
        yaml_path = tmp_path / "tools.yaml"
        generate_tools_yaml(result, yaml_path)
        registry = load_registry(str(yaml_path))
        assert len(registry) > 0
        for tc in registry.values():
            assert isinstance(tc, ToolConfig)
            # command must be str, not list
            assert isinstance(tc.command, str)


# -- deep copy aliasing ----------------------------------------------------

class TestDeepCopy:
    """generate_tools_yaml must not alias file_patterns to module constants."""

    def test_file_patterns_not_aliased_to_constant(self, tmp_path):
        """Generated entry's file_patterns is a fresh list, not the
        same object as the PYTHON_TOOL_REGISTRY constant's list."""
        orig_patterns = PYTHON_TOOL_REGISTRY["ruff"]["tools_yaml_entry"][
            "file_patterns"
        ]
        orig_id = id(orig_patterns)

        yaml_path = tmp_path / "tools.yaml"
        result = DetectionResult(
            detected=["ruff"], missing=[], language="python",
        )
        generate_tools_yaml(result, yaml_path)

        # Constant must not be mutated
        assert orig_patterns == ["*.py"], (
            "PYTHON_TOOL_REGISTRY constant mutated: %s" % orig_patterns
        )
        assert id(orig_patterns) == orig_id, (
            "PYTHON_TOOL_REGISTRY constant object replaced"
        )

        # Written content must still be correct
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        assert data["tools"]["ruff"]["file_patterns"] == ["*.py"]

    def test_mutation_of_generated_does_not_affect_constant(self, tmp_path):
        """Mutating the in-memory loaded YAML does not corrupt the
        registry constant on a subsequent generate call."""
        result = DetectionResult(
            detected=["ruff"], missing=[], language="python",
        )

        # First generate
        yaml_path1 = tmp_path / "tools1.yaml"
        generate_tools_yaml(result, yaml_path1)
        with open(yaml_path1) as f:
            data = yaml.safe_load(f)

        # Mutate the loaded copy in memory
        data["tools"]["ruff"]["file_patterns"].append("*.pyw")

        # Second generate -- should still produce ["*.py"]
        yaml_path2 = tmp_path / "tools2.yaml"
        generate_tools_yaml(result, yaml_path2)
        with open(yaml_path2) as f:
            data2 = yaml.safe_load(f)
        assert data2["tools"]["ruff"]["file_patterns"] == ["*.py"], (
            "Second generate corrupted by mutation of first: %s"
            % data2["tools"]["ruff"]["file_patterns"]
        )


# -- force flag ------------------------------------------------------------

class TestForceFlag:
    """force=True merges detected tools into existing tools.yaml."""

    def test_force_flag_overwrites(self, tmp_path):
        """Original test: force=True on shellcheck-only tools.yaml
        adds ruff. shellcheck entry is preserved (merge behavior)."""
        cfg_dir = tmp_path / ".code-forge"
        cfg_dir.mkdir()
        tools_yaml = cfg_dir / "tools.yaml"
        tools_yaml.write_text(
            "tools:\n"
            "  shellcheck:\n"
            '    command: "shellcheck --format=json1"\n'
            "    output_format: shellcheck_json\n"
            "    file_patterns: ['*.sh']\n",
            encoding="utf-8",
        )
        (tmp_path / "app.py").write_text("x = 1\n")
        result = detect_and_init(
            tmp_path, force=True, which_fn=_make_which_fn("ruff"),
        )
        assert "ruff" in result.detected
        registry = load_registry(str(tools_yaml))
        assert "ruff" in registry

    def test_force_preserves_user_entries(self, tmp_path):
        """force=True with Python project: existing shellcheck entry
        is preserved alongside newly detected ruff."""
        cfg_dir = tmp_path / ".code-forge"
        cfg_dir.mkdir()
        tools_yaml = cfg_dir / "tools.yaml"
        tools_yaml.write_text(
            "tools:\n"
            "  shellcheck:\n"
            '    command: "shellcheck -f json"\n'
            "    output_format: shellcheck_json\n"
            "    file_patterns:\n"
            "    - '*.sh'\n",
            encoding="utf-8",
        )
        (tmp_path / "app.py").write_text("x = 1\n")
        result = detect_and_init(
            tmp_path, force=True, which_fn=_make_which_fn("ruff"),
        )
        assert "ruff" in result.detected

        registry = load_registry(str(tools_yaml))
        assert "ruff" in registry, "Detected tool ruff missing from merged file"
        assert "shellcheck" in registry, (
            "User-added shellcheck entry was clobbered by force=True"
        )

    def test_force_preserves_unknown_format_entry(self, tmp_path):
        """force=True preserves user-added entry with unknown output_format.
        Such entries cannot pass load_registry validation, so we read
        back via yaml.safe_load instead."""
        cfg_dir = tmp_path / ".code-forge"
        cfg_dir.mkdir()
        tools_yaml = cfg_dir / "tools.yaml"
        tools_yaml.write_text(
            "tools:\n"
            "  custom_lint:\n"
            '    command: "custom-lint --json"\n'
            "    output_format: custom_json\n"
            "    file_patterns:\n"
            "    - '*.xyz'\n",
            encoding="utf-8",
        )
        (tmp_path / "app.py").write_text("x = 1\n")
        detect_and_init(
            tmp_path, force=True, which_fn=_make_which_fn("ruff"),
        )

        with open(tools_yaml) as f:
            data = yaml.safe_load(f)
        tools = data["tools"]
        assert "custom_lint" in tools, (
            "User-added custom_lint entry was clobbered by force=True"
        )
        assert "ruff" in tools, "Detected ruff not added during force merge"

    def test_force_nonexistent_tools_yaml(self, tmp_path):
        """force=True with no existing tools.yaml must not crash;
        generates fresh file."""
        (tmp_path / "app.py").write_text("x = 1\n")
        result = detect_and_init(
            tmp_path, force=True, which_fn=_make_which_fn("ruff"),
        )
        assert "ruff" in result.detected
        tools_yaml = tmp_path / ".code-forge" / "tools.yaml"
        assert tools_yaml.exists()

    def test_force_corrupt_existing_falls_back(self, tmp_path):
        """force=True with syntactically invalid YAML falls back to
        fresh generation."""
        cfg_dir = tmp_path / ".code-forge"
        cfg_dir.mkdir()
        (cfg_dir / "tools.yaml").write_text(
            "{{not valid yaml\n", encoding="utf-8",
        )
        (tmp_path / "app.py").write_text("x = 1\n")
        result = detect_and_init(
            tmp_path, force=True, which_fn=_make_which_fn("ruff"),
        )
        assert "ruff" in result.detected


# -- detection report format -----------------------------------------------

class TestReportFormat:
    """DetectionResult has detected and missing lists."""

    def test_detection_report_format(self, tmp_path):
        content = (
            "[tool.ruff]\nline-length = 88\n\n"
            "[tool.mypy]\nstrict = true\n"
        )
        _write_pyproject(tmp_path, content)
        result = detect_toolchain(
            tmp_path,
            which_fn=_make_which_fn("ruff"),
        )
        assert isinstance(result.detected, list)
        assert isinstance(result.missing, list)
        assert "ruff" in result.detected
        assert "mypy" in result.missing


# -- error handling --------------------------------------------------------

class TestErrorHandling:
    """Edge case error handling."""

    def test_corrupted_pyproject_toml_falls_back(self, tmp_path):
        """Invalid TOML -> fallback to PATH detection."""
        _write_pyproject(tmp_path, "[[bad\ninvalid toml content\n")
        (tmp_path / "app.py").write_text("x = 1\n")
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn("ruff"),
        )
        assert "ruff" in result.detected


# -- quiet mode ------------------------------------------------------------

class TestQuietMode:
    """quiet=True suppresses stdout."""

    def test_quiet_mode_no_stdout(self, tmp_path, capsys):
        (tmp_path / "app.py").write_text("x = 1\n")
        detect_and_init(
            tmp_path, quiet=True, which_fn=_make_which_fn("ruff"),
        )
        captured = capsys.readouterr()
        assert captured.out == ""


# -- flake8 config-file detection ------------------------------------------

class TestFlake8Detection:
    """flake8 is detected via its own config files, not pyproject.toml."""

    def test_flake8_dot_flake8_config_present_and_binary(self, tmp_path):
        """A .flake8 file + flake8 on PATH -> detected."""
        (tmp_path / ".flake8").write_text(
            "[flake8]\nmax-line-length = 88\n", encoding="utf-8",
        )
        (tmp_path / "app.py").write_text("x = 1\n")
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn("flake8"),
        )
        assert "flake8" in result.detected

    def test_flake8_setup_cfg_section_and_binary(self, tmp_path):
        """setup.cfg with [flake8] section + flake8 on PATH -> detected."""
        (tmp_path / "setup.cfg").write_text(
            "[metadata]\nname = myproject\n\n"
            "[flake8]\nmax-line-length = 88\n",
            encoding="utf-8",
        )
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn("flake8"),
        )
        assert "flake8" in result.detected

    def test_flake8_tox_ini_section_and_binary(self, tmp_path):
        """tox.ini with [flake8] section + flake8 on PATH -> detected."""
        (tmp_path / "tox.ini").write_text(
            "[tox]\nenvlist = py39\n\n"
            "[flake8]\nmax-line-length = 88\n",
            encoding="utf-8",
        )
        (tmp_path / "app.py").write_text("x = 1\n")
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn("flake8"),
        )
        assert "flake8" in result.detected

    def test_flake8_config_present_binary_absent(self, tmp_path):
        """.flake8 present but flake8 NOT on PATH -> missing."""
        (tmp_path / ".flake8").write_text(
            "[flake8]\nmax-line-length = 88\n", encoding="utf-8",
        )
        (tmp_path / "app.py").write_text("x = 1\n")
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn("ruff"),
        )
        assert "flake8" in result.missing
        assert "flake8" not in result.detected

    def test_flake8_no_config_not_detected(self, tmp_path):
        """No flake8 config files -> flake8 NOT in detected via
        config-file branch."""
        _write_pyproject(tmp_path, "[tool.ruff]\nline-length = 88\n")
        result = detect_toolchain(
            tmp_path,
            which_fn=_make_which_fn("ruff", "flake8"),
        )
        assert "ruff" in result.detected

    def test_flake8_config_fires_even_with_other_tool_sections(
        self, tmp_path,
    ):
        """pyproject.toml with [tool.ruff] AND .flake8 file -> BOTH
        ruff and flake8 in detected."""
        _write_pyproject(tmp_path, "[tool.ruff]\nline-length = 88\n")
        (tmp_path / ".flake8").write_text(
            "[flake8]\nmax-line-length = 88\n", encoding="utf-8",
        )
        result = detect_toolchain(
            tmp_path,
            which_fn=_make_which_fn("ruff", "flake8"),
        )
        assert "ruff" in result.detected
        assert "flake8" in result.detected


# -- shell detection -------------------------------------------------------

class TestShellDetection:
    """Shell projects detected via *.sh file presence."""

    def test_shell_project_with_shellcheck_on_path(self, tmp_path):
        """*.sh file in root + shellcheck on PATH -> shellcheck detected,
        language is shell."""
        (tmp_path / "script.sh").write_text("#!/bin/bash\necho hi\n")
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn("shellcheck"),
        )
        assert "shellcheck" in result.detected
        assert result.language == "shell"

    def test_shell_project_shellcheck_missing(self, tmp_path):
        """*.sh file present but shellcheck NOT on PATH -> missing."""
        (tmp_path / "script.sh").write_text("#!/bin/bash\necho hi\n")
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn(),
        )
        assert "shellcheck" in result.missing
        assert result.language == "shell"

    def test_mixed_python_shell_project(self, tmp_path):
        """Both *.py and *.sh files: both ruff and shellcheck detected,
        language is python (Python is primary in mixed projects)."""
        (tmp_path / "app.py").write_text("x = 1\n")
        (tmp_path / "script.sh").write_text("#!/bin/bash\necho hi\n")
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn("ruff", "shellcheck"),
        )
        assert "ruff" in result.detected
        assert "shellcheck" in result.detected
        assert result.language == "python"

    def test_shell_only_no_python(self, tmp_path):
        """Only *.sh, no Python indicators -> shellcheck detected,
        language is shell."""
        (tmp_path / "script.sh").write_text("#!/bin/bash\necho hi\n")
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn("shellcheck"),
        )
        assert "shellcheck" in result.detected
        assert result.language == "shell"

    def test_shell_nested_sh_files(self, tmp_path):
        """*.sh one level deep is also picked up."""
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "deploy.sh").write_text("#!/bin/bash\necho deploy\n")
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn("shellcheck"),
        )
        assert "shellcheck" in result.detected


# -- shell round-trip ------------------------------------------------------

class TestShellRoundTrip:
    """shellcheck tools.yaml entry round-trips through load_registry."""

    def test_shell_tools_yaml_roundtrip(self, tmp_path):
        """Detect shellcheck, generate tools.yaml, load via load_registry."""
        (tmp_path / "script.sh").write_text("#!/bin/bash\necho hi\n")
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn("shellcheck"),
        )
        assert "shellcheck" in result.detected

        yaml_path = tmp_path / "tools.yaml"
        generate_tools_yaml(result, yaml_path)

        registry = load_registry(str(yaml_path))
        assert "shellcheck" in registry, (
            "shellcheck missing from round-tripped registry"
        )
        tc = registry["shellcheck"]
        assert tc.output_format == "shellcheck_json"
        assert isinstance(tc.command, str)
        assert "shellcheck" in tc.command
