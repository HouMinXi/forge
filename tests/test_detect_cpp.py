# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for C++ language detection (cppcheck)."""
from pathlib import Path


from code_forge.detect import (
    detect_toolchain,
    generate_tools_yaml,
)
from code_forge.parsers._sarif import _parse_sarif
from code_forge.parsers.base import Finding
from code_forge.registry import load_registry


def _make_which_fn(*known_binaries):
    """Return a which_fn that knows only the given binaries."""
    known = set(known_binaries)

    def _which(name):
        return f"/usr/bin/{name}" if name in known else None

    return _which


_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "sarif"


class TestCppDetection:
    """C++ project detection via CMakeLists.txt and *.cpp files."""

    def test_cmake_detected(self, tmp_path):
        """CMakeLists.txt triggers C/C++ detection."""
        (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.10)\n")
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn("cppcheck"),
        )
        assert "cppcheck" in result.detected

    def test_cpp_files_detected(self, tmp_path):
        """*.cpp files trigger C/C++ detection."""
        (tmp_path / "main.cpp").write_text("#include <iostream>\n")
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn("cppcheck"),
        )
        assert "cppcheck" in result.detected

    def test_cpp_missing_linter(self, tmp_path):
        """cppcheck not on PATH -> missing list."""
        (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.10)\n")
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn(),  # nothing on PATH
        )
        assert "cppcheck" in result.missing
        assert result.language == "c_cpp"

    def test_cpp_tools_yaml_roundtrip(self, tmp_path):
        """cppcheck tools.yaml entry round-trips."""
        (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.10)\n")
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn("cppcheck"),
        )
        assert "cppcheck" in result.detected

        yaml_path = tmp_path / "tools.yaml"
        generate_tools_yaml(result, yaml_path)

        registry = load_registry(str(yaml_path))
        assert "cppcheck" in registry
        tc = registry["cppcheck"]
        assert tc.output_format == "sarif"
        assert isinstance(tc.command, str)


class TestCppSarifFixture:
    """Real cppcheck SARIF fixture for C++."""

    def test_real_fixture_parses_nullpointer_finding(self):
        """The spike fixture (cpp_real.sarif) must parse to Finding."""
        fixture = (_FIXTURES_DIR / "cpp_real.sarif").read_text()
        results = _parse_sarif(fixture, tool_name="cppcheck")
        assert len(results) >= 1
        f = results[0]
        assert isinstance(f, Finding)
        assert "nullPointer" in f.rule_id
