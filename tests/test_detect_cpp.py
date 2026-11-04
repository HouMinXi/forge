# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for C++ language detection (cppcheck)."""

from pathlib import Path
import json
import subprocess
from unittest.mock import patch

import pytest

from code_forge.detect import (
    detect_and_init,
    detect_toolchain,
    generate_tools_yaml,
)
from code_forge.coverage import compute_uncovered_files
from code_forge.machine import _default_l0_runner
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
            tmp_path,
            which_fn=_make_which_fn("cppcheck"),
        )
        assert "cppcheck" in result.detected

    def test_cpp_files_detected(self, tmp_path):
        """*.cpp files trigger C/C++ detection."""
        (tmp_path / "main.cpp").write_text("#include <iostream>\n")
        result = detect_toolchain(
            tmp_path,
            which_fn=_make_which_fn("cppcheck"),
        )
        assert "cppcheck" in result.detected

    def test_cpp_missing_linter(self, tmp_path):
        """cppcheck not on PATH -> missing list."""
        (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.10)\n")
        result = detect_toolchain(
            tmp_path,
            which_fn=_make_which_fn(),  # nothing on PATH
        )
        assert "cppcheck" in result.missing
        assert result.language == "c_cpp"

    def test_cpp_tools_yaml_roundtrip(self, tmp_path):
        """cppcheck tools.yaml entry round-trips."""
        (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.10)\n")
        result = detect_toolchain(
            tmp_path,
            which_fn=_make_which_fn("cppcheck"),
        )
        assert "cppcheck" in result.detected

        yaml_path = tmp_path / "tools.yaml"
        generate_tools_yaml(result, yaml_path)

        registry = load_registry(str(yaml_path))
        assert "cppcheck" in registry
        tc = registry["cppcheck"]
        assert tc.output_format == "sarif"
        assert isinstance(tc.command, str)


class TestCcDetectionConsumer:
    """A .cc-only project must reach the actual L0 consumer after generation."""

    @pytest.mark.parametrize("filename", ["main.cc", "src/main.cc"])
    @pytest.mark.parametrize("with_cmake", [False, True])
    def test_detect_generate_load_and_review_cc(self, tmp_path, filename, with_cmake):
        source = tmp_path / filename
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            "int main() {\n    int *p = nullptr;\n    int value = 0;\n"
            "    value = *p;\n    return *p;\n}\n",
            encoding="utf-8",
        )
        if with_cmake:
            (tmp_path / "CMakeLists.txt").write_text("project(demo)\n", encoding="utf-8")

        result = detect_toolchain(tmp_path, which_fn=_make_which_fn("cppcheck"))
        assert result.detected == ["cppcheck"]
        assert result.missing == []
        assert result.language == "c_cpp"
        config_path = tmp_path / ".code-forge" / "tools.yaml"
        generate_tools_yaml(result, config_path)
        registry = load_registry(str(config_path))

        # Reuse a real producer fixture, changing only its file identity for this
        # synthetic source. No native cppcheck analysis is claimed or launched.
        sarif = json.loads((_FIXTURES_DIR / "cpp_real.sarif").read_text())
        for item in sarif["runs"][0]["results"]:
            for location in item["locations"]:
                location["physicalLocation"]["artifactLocation"]["uri"] = filename

        def completed(argv, **kwargs):
            if "--version" in argv:
                return subprocess.CompletedProcess(argv, 0, stdout="Cppcheck mock\n", stderr="")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr=json.dumps(sarif))

        with (
            patch("code_forge.runner._resolve_command", return_value="/mock/cppcheck"),
            patch("code_forge.runner.subprocess.run", side_effect=completed) as launch,
        ):
            findings, errors = _default_l0_runner(registry, [Path(filename)], cwd=tmp_path)

        assert errors == []
        assert len(findings) == 2
        assert {finding.file for finding in findings} == {filename}
        assert all("Null pointer dereference" in finding.description for finding in findings)
        assert [call.args[0] for call in launch.call_args_list] == [
            ["/mock/cppcheck", "--version"],
            ["/mock/cppcheck", "-q", "--output-format=sarif", filename],
        ]
        assert launch.call_args.kwargs["cwd"] == tmp_path
        files = [filename, "README.md", "header.h"]
        assert compute_uncovered_files(files, registry, False) == ["README.md", "header.h"]
        assert compute_uncovered_files(files, registry, True) == []

    def test_cc_missing_cppcheck_is_reported(self, tmp_path):
        (tmp_path / "main.cc").write_text("int main() {}\n", encoding="utf-8")
        result = detect_toolchain(tmp_path, which_fn=_make_which_fn())
        assert result.detected == []
        assert result.missing == ["cppcheck"]
        assert result.language == "c_cpp"

    def test_existing_user_patterns_remain_unchanged(self, tmp_path):
        (tmp_path / "main.cc").write_text("int main() {}\n", encoding="utf-8")
        config = tmp_path / ".code-forge" / "tools.yaml"
        config.parent.mkdir()
        original = (
            "# User-selected scope must not be silently widened.\n"
            "tools:\n  cppcheck:\n    command: cppcheck -q --output-format=sarif\n"
            "    output_format: sarif\n    file_patterns: ['*.cpp']\n"
            "    exclude_patterns: ['vendor/*']\n    output_stream: stderr\n"
        )
        config.write_text(original, encoding="utf-8")
        with patch("code_forge.detect.detect_toolchain", side_effect=AssertionError("no redetect")):
            result = detect_and_init(tmp_path, quiet=True)
        assert result.detected == ["cppcheck"]
        assert config.read_text(encoding="utf-8") == original
        registry = load_registry(str(config))
        assert compute_uncovered_files(["main.cc"], registry, False) == ["main.cc"]


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
