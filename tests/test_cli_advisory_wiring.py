# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for advisory-findings.json -> SARIF/summary wiring."""
from __future__ import annotations

import json
from pathlib import Path

from code_forge.cli import _load_advisories
from code_forge.sarif import build_sarif_log, format_summary
from code_forge.state import State, Verdict


def _write_advisories(directory: Path, advisories: list[dict]) -> Path:
    path = directory / "advisory-findings.json"
    path.write_text(json.dumps(advisories), encoding="utf-8")
    return path


def _sample_advisory() -> dict:
    return {
        "id": "adv-1",
        "axis": "RUNTIME",
        "file": "src/test.py",
        "line_range": [10, 15],
        "description": "test advisory finding",
        "attribution": "runtime-axis/llm",
    }


class TestLoadAdvisories:
    def test_loads_valid_advisory_file(self, tmp_path):
        _write_advisories(tmp_path, [_sample_advisory()])
        result = _load_advisories(tmp_path / "advisory-findings.json")
        assert len(result) == 1
        assert result[0].id == "adv-1"
        assert result[0].axis == "RUNTIME"

    def test_returns_empty_when_file_absent(self, tmp_path):
        result = _load_advisories(tmp_path / "advisory-findings.json")
        assert result == []

    def test_returns_empty_on_malformed_json(self, tmp_path):
        path = tmp_path / "advisory-findings.json"
        path.write_text("{bad json", encoding="utf-8")
        result = _load_advisories(path)
        assert result == []

    def test_returns_empty_on_schema_mismatch(self, tmp_path):
        _write_advisories(tmp_path, [{"wrong": "fields"}])
        result = _load_advisories(tmp_path / "advisory-findings.json")
        assert result == []


class TestAdvisorySarifWiring:
    def test_advisories_appear_in_sarif_properties(self, tmp_path):
        _write_advisories(tmp_path, [_sample_advisory(), _sample_advisory()])
        advisories = _load_advisories(tmp_path / "advisory-findings.json")
        state = State(verdict=Verdict.PASS)
        log = build_sarif_log(state, {}, "2.7.0", advisories=advisories)
        props = log["runs"][0].get("properties", {})
        assert "advisories" in props
        assert len(props["advisories"]) == 2
        assert props["advisories"][0]["id"] == "adv-1"

    def test_advisories_absent_from_sarif_results(self, tmp_path):
        _write_advisories(tmp_path, [_sample_advisory()])
        advisories = _load_advisories(tmp_path / "advisory-findings.json")
        state = State(verdict=Verdict.PASS)
        log = build_sarif_log(state, {}, "2.7.0", advisories=advisories)
        assert log["runs"][0]["results"] == []

    def test_advisory_count_in_summary(self):
        state = State(verdict=Verdict.PASS)
        summary = format_summary(state, advisory_count=3)
        assert "advisory=3" in summary

    def test_advisory_count_absent_when_zero(self):
        state = State(verdict=Verdict.PASS)
        summary = format_summary(state, advisory_count=0)
        assert "advisory=" not in summary

    def test_no_advisories_no_sarif_property(self):
        state = State(verdict=Verdict.PASS)
        log = build_sarif_log(state, {}, "2.7.0", advisories=None)
        props = log["runs"][0].get("properties", {})
        assert "advisories" not in props
