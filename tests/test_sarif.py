# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for forge.sarif module (LAYER0-07 SARIF 2.1.0 emission).

22 test cases per 02-06-PLAN:
  (a) build_sarif_log basic shape
  (b) zero findings -> runs[0].results == []
  (c) CONFIRMED finding -> level=error, no suppressions
  (d) UNCERTAIN finding -> level=warning, no suppressions
  (e) DISMISSED finding -> level=note, suppressions=[{kind:external}]
  (f) FIXED finding -> level=note, suppressions=[{kind:inSource}]
  (g1) typical 2-element line_range
  (g2) empty line_range -> startLine=endLine=1
  (g3) single-element line_range -> endLine mirrors startLine
  (g4) line_range >2 elements -> first two used
  (h) anchor present -> properties.anchor
  (i) anchor None -> no anchor key
  (j) evidence_files present -> properties.evidence_files
  (j2) error present -> properties.error
  (k) ruleId == fingerprint
  (l) message.text == description
  (m) format_summary regex match
  (n) format_summary counts correctness
  (o) PENDING verdict -> build_sarif_log raises ValueError
  (o2) PENDING verdict -> format_summary raises ValueError
  (p) mixed findings correctness
  (q) _suppressions_for unknown Disposition raises ValueError
"""
import re
from unittest.mock import Mock

import pytest

from code_forge.disposition import Disposition
from code_forge.sarif import (
    DISPOSITION_TO_LEVEL,
    SARIF_SCHEMA_URI,
    SARIF_VERSION,
    _build_location,
    _build_properties,
    _build_semantic_version,
    _suppressions_for,
    build_sarif_log,
    format_summary,
)
from code_forge.state import State, StateFinding, Verdict


def _make_finding(
    disposition: Disposition,
    fingerprint: str = "fp-test",
    file: str = "src/test.py",
    line_range: list = None,
    description: str = "test finding",
    source: str = "L0",
    anchor: dict = None,
    evidence_files: list = None,
    error: str = None,
) -> StateFinding:
    """Helper to create StateFinding with defaults."""
    return StateFinding(
        id="f-test",
        fingerprint=fingerprint,
        source=source,
        disposition=disposition,
        file=file,
        line_range=line_range if line_range is not None else [10, 12],
        description=description,
        anchor=anchor,
        evidence_files=evidence_files,
        error=error,
    )


def _make_state(
    verdict: Verdict = Verdict.PASS,
    findings: list = None,
) -> State:
    """Helper to create State with defaults."""
    return State(
        verdict=verdict,
        findings=findings if findings is not None else [],
    )


class TestBuildSarifLogBasicShape:
    """(a) build_sarif_log basic shape."""

    def test_top_level_structure(self):
        state = _make_state(Verdict.PASS, [])
        tool_versions = {"shellcheck": "0.10.0"}
        result = build_sarif_log(state, tool_versions, "2.0.0a1")

        assert result["$schema"] == SARIF_SCHEMA_URI
        assert result["version"] == SARIF_VERSION
        assert isinstance(result["runs"], list)
        assert len(result["runs"]) == 1

    def test_tool_driver_name(self):
        state = _make_state(Verdict.PASS, [])
        result = build_sarif_log(state, {}, "2.0.0a1")

        driver = result["runs"][0]["tool"]["driver"]
        assert driver["name"] == "code-forge"

    def test_semantic_version_format(self):
        state = _make_state(Verdict.PASS, [])
        tool_versions = {"ruff": "0.4.2", "shellcheck": "0.10.0"}
        result = build_sarif_log(state, tool_versions, "2.0.0a1")

        sem_ver = result["runs"][0]["tool"]["driver"]["semanticVersion"]
        # Sorted order: ruff before shellcheck
        assert sem_ver == "code-forge 2.0.0a1 [ruff=0.4.2 shellcheck=0.10.0]"

    def test_semantic_version_empty_tools(self):
        state = _make_state(Verdict.PASS, [])
        result = build_sarif_log(state, {}, "2.0.0a1")

        sem_ver = result["runs"][0]["tool"]["driver"]["semanticVersion"]
        assert sem_ver == "code-forge 2.0.0a1 []"


class TestZeroFindings:
    """(b) zero findings."""

    def test_empty_results(self):
        state = _make_state(Verdict.PASS, [])
        result = build_sarif_log(state, {}, "2.0.0a1")

        assert result["runs"][0]["results"] == []


class TestConfirmedFinding:
    """(c) CONFIRMED finding."""

    def test_level_error_no_suppressions(self):
        finding = _make_finding(Disposition.CONFIRMED)
        state = _make_state(Verdict.FAIL, [finding])
        result = build_sarif_log(state, {}, "2.0.0a1")

        sarif_result = result["runs"][0]["results"][0]
        assert sarif_result["level"] == "error"
        assert "suppressions" not in sarif_result


class TestUncertainFinding:
    """(d) UNCERTAIN finding."""

    def test_level_warning_no_suppressions(self):
        finding = _make_finding(Disposition.UNCERTAIN)
        state = _make_state(Verdict.PASS, [finding])
        result = build_sarif_log(state, {}, "2.0.0a1")

        sarif_result = result["runs"][0]["results"][0]
        assert sarif_result["level"] == "warning"
        assert "suppressions" not in sarif_result


class TestDismissedFinding:
    """(e) DISMISSED finding."""

    def test_level_note_with_external_suppression(self):
        finding = _make_finding(Disposition.DISMISSED)
        state = _make_state(Verdict.PASS, [finding])
        result = build_sarif_log(state, {}, "2.0.0a1")

        sarif_result = result["runs"][0]["results"][0]
        assert sarif_result["level"] == "note"
        assert sarif_result["suppressions"] == [{"kind": "external"}]


class TestFixedFinding:
    """(f) FIXED finding."""

    def test_level_note_with_insource_suppression(self):
        finding = _make_finding(Disposition.FIXED)
        state = _make_state(Verdict.PASS, [finding])
        result = build_sarif_log(state, {}, "2.0.0a1")

        sarif_result = result["runs"][0]["results"][0]
        assert sarif_result["level"] == "note"
        assert sarif_result["suppressions"] == [{
            "kind": "inSource",
            "properties": {"fix_commit": None},
        }]


class TestLineRangeHandling:
    """(g1-g4) line_range edge cases."""

    def test_typical_two_element(self):
        """(g1) typical 2-element line_range."""
        finding = _make_finding(Disposition.CONFIRMED, line_range=[10, 15])
        location = _build_location(finding)

        region = location["physicalLocation"]["region"]
        assert region["startLine"] == 10
        assert region["endLine"] == 15

    def test_empty_line_range(self):
        """(g2) empty line_range -> startLine=endLine=1."""
        finding = _make_finding(Disposition.CONFIRMED, line_range=[])
        location = _build_location(finding)

        region = location["physicalLocation"]["region"]
        assert region["startLine"] == 1
        assert region["endLine"] == 1

    def test_single_element_line_range(self):
        """(g3) single-element line_range -> endLine mirrors startLine."""
        finding = _make_finding(Disposition.CONFIRMED, line_range=[42])
        location = _build_location(finding)

        region = location["physicalLocation"]["region"]
        assert region["startLine"] == 42
        assert region["endLine"] == 42

    def test_line_range_more_than_two(self):
        """(g4) line_range >2 elements -> first two used."""
        finding = _make_finding(
            Disposition.CONFIRMED, line_range=[10, 20, 30, 40]
        )
        location = _build_location(finding)

        region = location["physicalLocation"]["region"]
        assert region["startLine"] == 10
        assert region["endLine"] == 20


class TestAnchorHandling:
    """(h, i) anchor field handling."""

    def test_anchor_present(self):
        """(h) anchor present -> properties.anchor."""
        anchor_data = {"commit": "abc123", "context": "test"}
        finding = _make_finding(Disposition.CONFIRMED, anchor=anchor_data)
        props = _build_properties(finding)

        assert props["anchor"] == anchor_data

    def test_anchor_absent(self):
        """(i) anchor None -> no anchor key."""
        finding = _make_finding(Disposition.CONFIRMED, anchor=None)
        props = _build_properties(finding)

        assert "anchor" not in props


class TestEvidenceFilesHandling:
    """(j) evidence_files field handling."""

    def test_evidence_files_present(self):
        """(j) evidence_files present -> properties.evidence_files."""
        files = ["log1.txt", "log2.txt"]
        finding = _make_finding(Disposition.CONFIRMED, evidence_files=files)
        props = _build_properties(finding)

        assert props["evidence_files"] == files

    def test_evidence_files_absent(self):
        """evidence_files None -> no key."""
        finding = _make_finding(Disposition.CONFIRMED, evidence_files=None)
        props = _build_properties(finding)

        assert "evidence_files" not in props


class TestErrorFieldHandling:
    """(j2) error field handling."""

    def test_error_present(self):
        """(j2) error present -> properties.error."""
        finding = _make_finding(
            Disposition.CONFIRMED, error="parse error: unexpected EOF"
        )
        props = _build_properties(finding)

        assert props["error"] == "parse error: unexpected EOF"

    def test_error_absent(self):
        """error None -> no error key."""
        finding = _make_finding(Disposition.CONFIRMED, error=None)
        props = _build_properties(finding)

        assert "error" not in props


class TestRuleIdMapping:
    """(k) ruleId == fingerprint."""

    def test_rule_id_equals_fingerprint(self):
        finding = _make_finding(
            Disposition.CONFIRMED, fingerprint="fp-unique-123"
        )
        state = _make_state(Verdict.FAIL, [finding])
        result = build_sarif_log(state, {}, "2.0.0a1")

        sarif_result = result["runs"][0]["results"][0]
        assert sarif_result["ruleId"] == "fp-unique-123"


class TestMessageMapping:
    """(l) message.text == description."""

    def test_message_text_equals_description(self):
        finding = _make_finding(
            Disposition.CONFIRMED, description="detailed error message here"
        )
        state = _make_state(Verdict.FAIL, [finding])
        result = build_sarif_log(state, {}, "2.0.0a1")

        sarif_result = result["runs"][0]["results"][0]
        assert sarif_result["message"]["text"] == "detailed error message here"


class TestFormatSummaryRegex:
    """(m) format_summary regex match."""

    SUMMARY_REGEX = (
        r"^code-forge: (PASS|FAIL|ESCALATED) findings=\d+ confirmed=\d+ "
        r"uncertain=\d+ dismissed=\d+ fixed=\d+$"
    )

    def test_pass_matches_regex(self):
        state = _make_state(Verdict.PASS, [])
        summary = format_summary(state)
        assert re.match(self.SUMMARY_REGEX, summary)

    def test_fail_matches_regex(self):
        finding = _make_finding(Disposition.CONFIRMED)
        state = _make_state(Verdict.FAIL, [finding])
        summary = format_summary(state)
        assert re.match(self.SUMMARY_REGEX, summary)

    def test_escalated_matches_regex(self):
        state = _make_state(Verdict.ESCALATED, [])
        summary = format_summary(state)
        assert re.match(self.SUMMARY_REGEX, summary)


class TestFormatSummaryCounts:
    """(n) format_summary counts correctness."""

    def test_counts_match_findings(self):
        findings = [
            _make_finding(Disposition.CONFIRMED),
            _make_finding(Disposition.CONFIRMED),
            _make_finding(Disposition.UNCERTAIN),
            _make_finding(Disposition.DISMISSED),
        ]
        state = _make_state(Verdict.FAIL, findings)
        summary = format_summary(state)

        assert "findings=4" in summary
        assert "confirmed=2" in summary
        assert "uncertain=1" in summary
        assert "dismissed=1" in summary
        assert "fixed=0" in summary

    def test_total_equals_sum(self):
        findings = [
            _make_finding(Disposition.CONFIRMED),
            _make_finding(Disposition.UNCERTAIN),
            _make_finding(Disposition.DISMISSED),
            _make_finding(Disposition.FIXED),
        ]
        state = _make_state(Verdict.FAIL, findings)
        summary = format_summary(state)

        # Total = 4 = 1 + 1 + 1 + 1
        assert "findings=4" in summary
        assert "confirmed=1" in summary
        assert "uncertain=1" in summary
        assert "dismissed=1" in summary
        assert "fixed=1" in summary


class TestPendingVerdictRaises:
    """(o, o2) PENDING verdict raises ValueError."""

    def test_build_sarif_log_pending_raises(self):
        """(o) build_sarif_log raises on PENDING."""
        state = _make_state(Verdict.PENDING, [])
        with pytest.raises(ValueError) as exc_info:
            build_sarif_log(state, {}, "2.0.0a1")

        assert "GATE-01b" in str(exc_info.value)

    def test_format_summary_pending_raises(self):
        """(o2) format_summary raises on PENDING."""
        state = _make_state(Verdict.PENDING, [])
        with pytest.raises(ValueError) as exc_info:
            format_summary(state)

        assert "GATE-01b" in str(exc_info.value)


class TestMixedFindings:
    """(p) mixed findings correctness."""

    def test_mixed_dispositions_all_correct(self):
        findings = [
            _make_finding(Disposition.CONFIRMED, fingerprint="fp-1"),
            _make_finding(Disposition.UNCERTAIN, fingerprint="fp-2"),
            _make_finding(Disposition.DISMISSED, fingerprint="fp-3"),
            _make_finding(Disposition.FIXED, fingerprint="fp-4"),
        ]
        state = _make_state(Verdict.FAIL, findings)
        result = build_sarif_log(state, {}, "2.0.0a1")

        results = result["runs"][0]["results"]
        assert len(results) == 4

        # CONFIRMED
        assert results[0]["level"] == "error"
        assert "suppressions" not in results[0]

        # UNCERTAIN
        assert results[1]["level"] == "warning"
        assert "suppressions" not in results[1]

        # DISMISSED
        assert results[2]["level"] == "note"
        assert results[2]["suppressions"] == [{"kind": "external"}]

        # FIXED
        assert results[3]["level"] == "note"
        assert results[3]["suppressions"] == [{
            "kind": "inSource",
            "properties": {"fix_commit": None},
        }]


class TestUnknownDispositionRaises:
    """(q) _suppressions_for unknown Disposition raises ValueError."""

    def test_unknown_disposition_raises(self):
        """Mock a non-Disposition value to trigger the else branch."""
        sentinel = Mock(spec=Disposition)
        sentinel.name = "UNKNOWN_FUTURE"
        # Ensure it doesn't match any known disposition
        with pytest.raises(ValueError) as exc_info:
            _suppressions_for(sentinel)

        assert "Disposition" in str(exc_info.value)
        assert "sarif.py mapping table needs update" in str(exc_info.value)


class TestSourceAlwaysPresent:
    """Verify source field is always in properties."""

    def test_source_l0(self):
        finding = _make_finding(Disposition.CONFIRMED, source="L0")
        props = _build_properties(finding)
        assert props["source"] == "L0"

    def test_source_l1(self):
        finding = _make_finding(Disposition.UNCERTAIN, source="L1")
        props = _build_properties(finding)
        assert props["source"] == "L1"


class TestDispositionToLevelMapping:
    """Verify DISPOSITION_TO_LEVEL table coverage."""

    def test_all_dispositions_mapped(self):
        for disp in Disposition:
            assert disp in DISPOSITION_TO_LEVEL


class TestBuildSemanticVersion:
    """Additional _build_semantic_version tests."""

    def test_sorted_tool_order(self):
        tools = {"z_tool": "1.0", "a_tool": "2.0", "m_tool": "3.0"}
        result = _build_semantic_version("2.0.0a1", tools)
        assert result == "code-forge 2.0.0a1 [a_tool=2.0 m_tool=3.0 z_tool=1.0]"


class TestTokenCost:
    """tokenCost property bag in runs[0].properties."""

    def _make_cost_state(
        self,
        cost_total_input=100,
        cost_total_output=50,
        cost_passes=3,
        cost_total_duration=10.5,
        findings=None,
    ):
        st = _make_state(Verdict.PASS, findings or [])
        st.cost_total_input = cost_total_input
        st.cost_total_output = cost_total_output
        st.cost_passes = cost_passes
        st.cost_total_duration = cost_total_duration
        return st

    def test_sarif_log_with_token_cost(self):
        """tokenCost emitted when backend_name provided and passes > 0."""
        state = self._make_cost_state()
        result = build_sarif_log(
            state, {}, "2.0.0a1",
            backend_name="mimo", backend_model="mimo-v2.5-pro",
        )
        tc = result["runs"][0]["properties"]["tokenCost"]
        assert tc["inputTokens"] == 100
        assert tc["outputTokens"] == 50
        assert tc["totalTokens"] == 150
        assert tc["backend"] == "mimo"
        assert tc["model"] == "mimo-v2.5-pro"
        assert tc["passes"] == 3
        assert tc["durationSeconds"] == 10.5

    def test_sarif_log_without_backend_name(self):
        """No tokenCost when backend_name is None (cli backend)."""
        state = self._make_cost_state()
        result = build_sarif_log(state, {}, "2.0.0a1", backend_name=None)
        run = result["runs"][0]
        assert "properties" not in run or "tokenCost" not in run.get(
            "properties", {}
        )

    def test_sarif_log_zero_passes(self):
        """No tokenCost when cost_passes is 0 (no review ran)."""
        state = self._make_cost_state(cost_passes=0)
        result = build_sarif_log(
            state, {}, "2.0.0a1", backend_name="mimo",
        )
        run = result["runs"][0]
        assert "properties" not in run or "tokenCost" not in run.get(
            "properties", {}
        )

    def test_sarif_log_preserves_results(self):
        """tokenCost does not alter findings in results."""
        findings = [
            _make_finding(Disposition.CONFIRMED, fingerprint="fp-a"),
            _make_finding(Disposition.UNCERTAIN, fingerprint="fp-b"),
        ]
        state = self._make_cost_state(findings=findings)
        result_with = build_sarif_log(
            state, {}, "2.0.0a1",
            backend_name="mimo", backend_model="mimo-v2.5-pro",
        )
        result_without = build_sarif_log(state, {}, "2.0.0a1")
        assert (
            result_with["runs"][0]["results"]
            == result_without["runs"][0]["results"]
        )
        assert len(result_with["runs"][0]["results"]) == 2
