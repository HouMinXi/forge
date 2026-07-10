# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for Java language detection (PMD)."""
from pathlib import Path

import pytest

from code_forge.detect import (
    JAVA_TOOL_REGISTRY,
    DetectionResult,
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


class TestJavaDetection:
    """Java project detection via pom.xml and *.java files."""

    def test_pom_xml_detected(self, tmp_path):
        """pom.xml triggers Java detection."""
        (tmp_path / "pom.xml").write_text("<project></project>\n")
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn("pmd"),
        )
        assert "pmd" in result.detected

    def test_build_gradle_detected(self, tmp_path):
        """build.gradle triggers Java detection."""
        (tmp_path / "build.gradle").write_text("apply plugin: 'java'\n")
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn("pmd"),
        )
        assert "pmd" in result.detected

    def test_java_files_detected(self, tmp_path):
        """*.java files trigger Java detection."""
        (tmp_path / "Main.java").write_text("public class Main {}\n")
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn("pmd"),
        )
        assert "pmd" in result.detected

    def test_java_missing_linter(self, tmp_path):
        """pmd not on PATH -> missing list."""
        (tmp_path / "pom.xml").write_text("<project></project>\n")
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn(),  # nothing on PATH
        )
        assert "pmd" in result.missing
        assert result.language == "java"

    def test_java_tools_yaml_roundtrip(self, tmp_path):
        """pmd tools.yaml entry round-trips."""
        (tmp_path / "pom.xml").write_text("<project></project>\n")
        result = detect_toolchain(
            tmp_path, which_fn=_make_which_fn("pmd"),
        )
        assert "pmd" in result.detected

        yaml_path = tmp_path / "tools.yaml"
        generate_tools_yaml(result, yaml_path)

        registry = load_registry(str(yaml_path))
        assert "pmd" in registry
        tc = registry["pmd"]
        assert tc.output_format == "sarif"
        assert isinstance(tc.command, str)


class TestJavaSarifFixture:
    """Real PMD SARIF fixture for Java."""

    def test_real_fixture_parses_systemprintln_finding(self):
        """The spike fixture (java_real.sarif) must parse to Finding."""
        fixture = (_FIXTURES_DIR / "java_real.sarif").read_text()
        results = _parse_sarif(fixture, tool_name="pmd")
        assert len(results) >= 1
        f = results[0]
        assert isinstance(f, Finding)
        assert "SystemPrintln" in f.rule_id
