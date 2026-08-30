# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for JS/TS language detection (ESLint)."""
from pathlib import Path


from code_forge.detect import (
    detect_toolchain,
    generate_tools_yaml,
)
from code_forge.parsers.eslint import parse_eslint
from code_forge.parsers.base import Finding
from code_forge.registry import load_registry


def _make_which_fn(*known_binaries):
    """Return a which_fn that knows only the given binaries."""
    known = set(known_binaries)

    def _which(name):
        return f"/usr/bin/{name}" if name in known else None

    return _which


_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "sarif"


class TestJSDetection:
    """JS/TS project detection via package.json and *.js files."""

    def test_package_json_detected(self, tmp_path):
        """package.json triggers JS detection."""
        (tmp_path / "package.json").write_text('{"name": "test"}\n')
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn("eslint"),
        )
        assert "eslint" in result.detected

    def test_js_files_detected(self, tmp_path):
        """*.js files trigger JS detection."""
        (tmp_path / "index.js").write_text("console.log('hello');\n")
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn("eslint"),
        )
        assert "eslint" in result.detected

    def test_ts_files_detected(self, tmp_path):
        """*.ts files trigger JS detection."""
        (tmp_path / "index.ts").write_text("console.log('hello');\n")
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn("eslint"),
        )
        assert "eslint" in result.detected

    def test_js_missing_linter(self, tmp_path):
        """eslint not on PATH -> missing list."""
        (tmp_path / "package.json").write_text('{"name": "test"}\n')
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn(),  # nothing on PATH
        )
        assert "eslint" in result.missing
        assert result.language == "js"

    def test_js_tools_yaml_roundtrip(self, tmp_path):
        """eslint tools.yaml entry round-trips."""
        (tmp_path / "package.json").write_text('{"name": "test"}\n')
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn("eslint"),
        )
        assert "eslint" in result.detected

        yaml_path = tmp_path / "tools.yaml"
        generate_tools_yaml(result, yaml_path)

        registry = load_registry(str(yaml_path))
        assert "eslint" in registry
        tc = registry["eslint"]
        assert tc.output_format == "eslint_json"
        assert isinstance(tc.command, str)


class TestJSSarifFixture:
    """Real ESLint SARIF fixture for JS."""

    def test_real_fixture_parses_nounusedvars_finding(self):
        """The spike fixture (js_real.sarif) must parse to Finding."""
        fixture = (_FIXTURES_DIR / "js_real.sarif").read_text()
        results = parse_eslint(fixture, tool_name="eslint")
        assert len(results) >= 1
        f = results[0]
        assert isinstance(f, Finding)
        assert "no-unused-vars" in f.rule_id
