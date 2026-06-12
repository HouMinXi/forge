"""Tests for RuntimeRunner advisory axis.

Covers:
- AxisRunner Protocol conformance (is_advisory=True, returns list[AdvisoryFinding])
- RUNTIME_LIFECYCLE_QUESTION constant properties
- run() with empty diff returns []
- run() calls llm_invoke with RUNTIME_LIFECYCLE_QUESTION.replace(), NOT .format()
- run() parses structured JSON response: "surfaces" and "findings" keys
- run() on LLMInvokeError returns SKIPPED AdvisoryFinding (D-04 never-silent-skip)
- run() on malformed JSON returns SKIPPED AdvisoryFinding (D-04 never-silent-skip)
- run() reads smoke receipts from repo_root/.code-forge/smoke-receipts/
- run() with no receipts returns all LLM-enumerated surfaces as UNVERIFIED (D-08)
- run() with receipt matching one surface marks it VERIFIED, rest UNVERIFIED
- run() with receipt whose diff_sha256 mismatches treats it as invalid (Pitfall 3)
- Per-surface NOT VERIFIED = (LLM-enumerated) minus (receipt-declared),
  case-insensitive substring containment (D-11)
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from code_forge.advisory import AdvisoryFinding

# ---------------------------------------------------------------------------
# RUNTIME_LIFECYCLE_QUESTION constant tests
# ---------------------------------------------------------------------------


class TestRuntimeLifecycleQuestion:
    """RUNTIME_LIFECYCLE_QUESTION constant must satisfy D-05/D-10."""

    def test_constant_is_exported(self):
        from code_forge.runtime import RUNTIME_LIFECYCLE_QUESTION
        assert isinstance(RUNTIME_LIFECYCLE_QUESTION, str)

    def test_constant_is_non_empty(self):
        from code_forge.runtime import RUNTIME_LIFECYCLE_QUESTION
        assert len(RUNTIME_LIFECYCLE_QUESTION.strip()) > 0

    def test_constant_contains_diff_text_placeholder(self):
        from code_forge.runtime import RUNTIME_LIFECYCLE_QUESTION
        assert "{diff_text}" in RUNTIME_LIFECYCLE_QUESTION

    def test_constant_asks_about_runtime_surfaces(self):
        from code_forge.runtime import RUNTIME_LIFECYCLE_QUESTION
        lower = RUNTIME_LIFECYCLE_QUESTION.lower()
        # Must ask about runtime surfaces (some keyword)
        assert any(kw in lower for kw in ["surface", "runtime", "lifecycle"])

    def test_constant_asks_about_lifecycle_or_side_effects(self):
        from code_forge.runtime import RUNTIME_LIFECYCLE_QUESTION
        lower = RUNTIME_LIFECYCLE_QUESTION.lower()
        assert any(kw in lower for kw in ["lifecycle", "side effect", "side-effect"])

    def test_constant_asks_about_smoke_test_needs(self):
        from code_forge.runtime import RUNTIME_LIFECYCLE_QUESTION
        lower = RUNTIME_LIFECYCLE_QUESTION.lower()
        assert any(kw in lower for kw in ["smoke", "test", "verify"])

    def test_constant_requests_json_response(self):
        from code_forge.runtime import RUNTIME_LIFECYCLE_QUESTION
        lower = RUNTIME_LIFECYCLE_QUESTION.lower()
        assert "json" in lower

    def test_constant_requests_surfaces_key(self):
        from code_forge.runtime import RUNTIME_LIFECYCLE_QUESTION
        assert "surfaces" in RUNTIME_LIFECYCLE_QUESTION

    def test_constant_requests_findings_key(self):
        from code_forge.runtime import RUNTIME_LIFECYCLE_QUESTION
        assert "findings" in RUNTIME_LIFECYCLE_QUESTION


# ---------------------------------------------------------------------------
# AxisRunner Protocol conformance
# ---------------------------------------------------------------------------


class TestRuntimeRunnerProtocol:
    """RuntimeRunner must satisfy the AxisRunner Protocol."""

    def test_is_advisory_true(self):
        from code_forge.runtime import RuntimeRunner
        runner = RuntimeRunner()
        assert runner.is_advisory is True

    def test_has_run_method(self):
        from code_forge.runtime import RuntimeRunner
        runner = RuntimeRunner()
        assert callable(runner.run)

    def test_infra_errors_initialized_empty(self):
        from code_forge.runtime import RuntimeRunner
        runner = RuntimeRunner()
        assert runner.infra_errors == []

    def test_source_files_initialized_none(self):
        from code_forge.runtime import RuntimeRunner
        runner = RuntimeRunner()
        assert runner.source_files is None


# ---------------------------------------------------------------------------
# run() with empty / None diff_text
# ---------------------------------------------------------------------------


class TestRuntimeRunnerEmptyDiff:
    """run() with empty or None diff_text returns [] without LLM call."""

    def test_empty_string_returns_empty(self, tmp_path):
        from code_forge.runtime import RuntimeRunner
        runner = RuntimeRunner()
        result = runner.run("", tmp_path)
        assert result == []

    def test_whitespace_only_returns_empty(self, tmp_path):
        from code_forge.runtime import RuntimeRunner
        runner = RuntimeRunner()
        result = runner.run("   \n  \t  ", tmp_path)
        assert result == []


# ---------------------------------------------------------------------------
# run() LLM call uses str.replace(), NOT str.format()
# ---------------------------------------------------------------------------


class TestRuntimeRunnerLLMCall:
    """run() must use RUNTIME_LIFECYCLE_QUESTION.replace('{diff_text}', ...) not .format()."""

    def test_llm_invoked_with_diff_text_substituted(self, tmp_path):
        from code_forge.runtime import RuntimeRunner
        diff = "diff --git a/rules.sh b/rules.sh\n+nft add table"
        valid_response = MagicMock()
        valid_response.content = {"surfaces": [], "findings": []}

        with patch("code_forge.runtime.llm_invoke", return_value=valid_response) as mock_invoke:
            runner = RuntimeRunner()
            runner.run(diff, tmp_path)
            assert mock_invoke.called
            prompt_arg = mock_invoke.call_args[0][0]
            # diff text must appear in the prompt
            assert diff in prompt_arg
            # placeholder must NOT remain
            assert "{diff_text}" not in prompt_arg

    def test_diff_with_braces_does_not_raise(self, tmp_path):
        """Diff containing literal { or } must not cause KeyError (str.format trap)."""
        from code_forge.runtime import RuntimeRunner
        diff = "diff --git a/t.py b/t.py\n+x = {'key': 'value'}"
        valid_response = MagicMock()
        valid_response.content = {"surfaces": [], "findings": []}

        with patch("code_forge.runtime.llm_invoke", return_value=valid_response):
            runner = RuntimeRunner()
            # Must not raise KeyError
            result = runner.run(diff, tmp_path)
            assert isinstance(result, list)


# ---------------------------------------------------------------------------
# run() JSON parsing and AdvisoryFinding construction
# ---------------------------------------------------------------------------


class TestRuntimeRunnerJSONParsing:
    """run() parses structured JSON response: surfaces + findings."""

    def _make_runner_with_response(self, content):
        from code_forge.runtime import RuntimeRunner
        response = MagicMock()
        response.content = content
        runner = RuntimeRunner()
        return runner, response

    def test_valid_json_with_surfaces_produces_findings(self, tmp_path):
        from code_forge.runtime import RuntimeRunner
        response = MagicMock()
        response.content = {
            "surfaces": ["nftables", "systemd"],
            "findings": [
                {
                    "file": "rules.sh",
                    "line": 10,
                    "surface": "nftables",
                    "description": "nftables rules need reload",
                }
            ],
        }
        with patch("code_forge.runtime.llm_invoke", return_value=response):
            runner = RuntimeRunner()
            result = runner.run("diff --git a/f b/f\n+change", tmp_path)
        assert isinstance(result, list)
        # At least the finding from LLM
        axes = {f.axis for f in result}
        assert "RUNTIME" in axes

    def test_findings_have_runtime_axis(self, tmp_path):
        from code_forge.runtime import RuntimeRunner
        response = MagicMock()
        response.content = {
            "surfaces": ["nftables"],
            "findings": [
                {
                    "file": "rules.sh",
                    "line": 5,
                    "surface": "nftables",
                    "description": "nftables reload required",
                }
            ],
        }
        with patch("code_forge.runtime.llm_invoke", return_value=response):
            runner = RuntimeRunner()
            result = runner.run("diff --git a/f b/f\n+change", tmp_path)
        for finding in result:
            assert finding.axis == "RUNTIME"

    def test_findings_are_advisory_finding_instances(self, tmp_path):
        from code_forge.runtime import RuntimeRunner
        response = MagicMock()
        response.content = {
            "surfaces": [],
            "findings": [],
        }
        with patch("code_forge.runtime.llm_invoke", return_value=response):
            runner = RuntimeRunner()
            result = runner.run("diff --git a/f b/f\n+change", tmp_path)
        for item in result:
            assert isinstance(item, AdvisoryFinding)


# ---------------------------------------------------------------------------
# run() on LLMInvokeError - SKIPPED (D-04 never-silent-skip)
# ---------------------------------------------------------------------------


class TestRuntimeRunnerLLMError:
    """run() on LLMInvokeError returns SKIPPED AdvisoryFinding (D-04)."""

    def test_llm_error_returns_skipped_finding(self, tmp_path):
        from code_forge.llm_invoke import LLMInvokeError
        from code_forge.runtime import RuntimeRunner

        with patch("code_forge.runtime.llm_invoke",
                   side_effect=LLMInvokeError("connection refused")):
            runner = RuntimeRunner()
            result = runner.run("diff --git a/f b/f\n+change", tmp_path)

        assert len(result) == 1
        finding = result[0]
        assert isinstance(finding, AdvisoryFinding)
        assert finding.id == "runtime-skipped"
        assert finding.axis == "RUNTIME"
        assert "connection refused" in finding.description.lower() or \
               "skipped" in finding.description.lower()

    def test_llm_error_records_to_infra_errors(self, tmp_path):
        from code_forge.llm_invoke import LLMInvokeError
        from code_forge.runtime import RuntimeRunner

        with patch("code_forge.runtime.llm_invoke",
                   side_effect=LLMInvokeError("timeout")):
            runner = RuntimeRunner()
            runner.run("diff --git a/f b/f\n+change", tmp_path)

        assert len(runner.infra_errors) > 0

    def test_llm_error_never_silent(self, tmp_path):
        """LLM failure must return a non-empty list (never silently return [])."""
        from code_forge.llm_invoke import LLMInvokeError
        from code_forge.runtime import RuntimeRunner

        with patch("code_forge.runtime.llm_invoke",
                   side_effect=LLMInvokeError("auth failed")):
            runner = RuntimeRunner()
            result = runner.run("diff --git a/f b/f\n+change", tmp_path)

        assert len(result) > 0


# ---------------------------------------------------------------------------
# run() on malformed LLM JSON - SKIPPED (D-04 never-silent-skip)
# ---------------------------------------------------------------------------


class TestRuntimeRunnerMalformedJSON:
    """run() on malformed LLM JSON returns SKIPPED AdvisoryFinding (D-04)."""

    def test_string_content_not_valid_json_returns_skipped(self, tmp_path):
        """LLM returns a string that is not valid JSON."""
        from code_forge.runtime import RuntimeRunner

        response = MagicMock()
        response.content = "this is not json at all"

        with patch("code_forge.runtime.llm_invoke", return_value=response):
            runner = RuntimeRunner()
            result = runner.run("diff --git a/f b/f\n+change", tmp_path)

        assert len(result) >= 1
        skipped = [f for f in result if f.id == "runtime-skipped"]
        assert len(skipped) == 1
        assert "RUNTIME" in skipped[0].axis

    def test_missing_surfaces_key_returns_skipped(self, tmp_path):
        """JSON response missing 'surfaces' key -> SKIPPED."""
        from code_forge.runtime import RuntimeRunner

        response = MagicMock()
        response.content = {"bad_key": "no surfaces here"}

        with patch("code_forge.runtime.llm_invoke", return_value=response):
            runner = RuntimeRunner()
            result = runner.run("diff --git a/f b/f\n+change", tmp_path)

        skipped = [f for f in result if f.id == "runtime-skipped"]
        assert len(skipped) == 1

    def test_none_content_returns_skipped(self, tmp_path):
        """LLM returns None content -> SKIPPED."""
        from code_forge.runtime import RuntimeRunner

        response = MagicMock()
        response.content = None

        with patch("code_forge.runtime.llm_invoke", return_value=response):
            runner = RuntimeRunner()
            result = runner.run("diff --git a/f b/f\n+change", tmp_path)

        skipped = [f for f in result if f.id == "runtime-skipped"]
        assert len(skipped) == 1

    def test_malformed_json_records_infra_error(self, tmp_path):
        from code_forge.runtime import RuntimeRunner

        response = MagicMock()
        response.content = "not json"

        with patch("code_forge.runtime.llm_invoke", return_value=response):
            runner = RuntimeRunner()
            runner.run("diff --git a/f b/f\n+change", tmp_path)

        assert len(runner.infra_errors) > 0

    def test_list_wrapped_single_dict_parsed(self, tmp_path):
        """mimo-pro returns [{...}] -- single-element list must be unwrapped."""
        from code_forge.runtime import RuntimeRunner

        response = MagicMock()
        response.content = [{"surfaces": ["nftables", "systemd"], "findings": []}]

        with patch("code_forge.runtime.llm_invoke", return_value=response):
            runner = RuntimeRunner()
            result = runner.run("diff --git a/f b/f\n+nft add rule", tmp_path)

        skipped = [f for f in result if f.id == "runtime-skipped"]
        assert len(skipped) == 0, "single-element list must not produce SKIPPED"
        summary = [f for f in result if f.id == "runtime-smoke-summary"]
        assert len(summary) == 1
        assert "nftables" in summary[0].description or "systemd" in summary[0].description

    def test_multi_element_list_returns_skipped(self, tmp_path):
        """Multi-element list is ambiguous -- must still produce SKIPPED."""
        from code_forge.runtime import RuntimeRunner

        response = MagicMock()
        response.content = [
            {"surfaces": ["nftables"], "findings": []},
            {"surfaces": ["systemd"], "findings": []},
        ]

        with patch("code_forge.runtime.llm_invoke", return_value=response):
            runner = RuntimeRunner()
            result = runner.run("diff --git a/f b/f\n+nft add rule", tmp_path)

        skipped = [f for f in result if f.id == "runtime-skipped"]
        assert len(skipped) == 1

    def test_empty_list_returns_skipped(self, tmp_path):
        """Empty list is malformed -- must produce SKIPPED."""
        from code_forge.runtime import RuntimeRunner

        response = MagicMock()
        response.content = []

        with patch("code_forge.runtime.llm_invoke", return_value=response):
            runner = RuntimeRunner()
            result = runner.run("diff --git a/f b/f\n+nft add rule", tmp_path)

        skipped = [f for f in result if f.id == "runtime-skipped"]
        assert len(skipped) == 1


# ---------------------------------------------------------------------------
# run() reads smoke receipts and computes UNVERIFIED (D-07/D-08/D-11)
# ---------------------------------------------------------------------------


class TestRuntimeRunnerSmokeReceipts:
    """run() reads receipts and computes VERIFIED vs UNVERIFIED."""

    def _mock_response(self, surfaces=None, findings=None):
        response = MagicMock()
        response.content = {
            "surfaces": surfaces or [],
            "findings": findings or [],
        }
        return response

    def _write_receipt(self, receipts_dir: Path, surface: str,
                       diff_sha256: str, status: str = "VERIFIED"):
        receipts_dir.mkdir(parents=True, exist_ok=True)
        receipt = {
            "diff_sha256": diff_sha256,
            "surface": surface,
            "command": "pytest tests/",
            "exit_code": 0 if status == "VERIFIED" else 1,
            "transcript_sha256": "abc",
            "timestamp": "2026-06-12T10:00:00Z",
            "status": status,
        }
        path = receipts_dir / ("smoke-receipt-%s.json" % surface)
        path.write_text(json.dumps(receipt))
        return path

    def test_no_receipts_all_surfaces_unverified(self, tmp_path):
        """No receipts present -> all LLM-enumerated surfaces UNVERIFIED (D-08)."""
        from code_forge.runtime import RuntimeRunner

        diff = "diff --git a/rules.sh b/rules.sh\n+change"
        response = self._mock_response(surfaces=["nftables", "systemd"])

        with patch("code_forge.runtime.llm_invoke", return_value=response):
            runner = RuntimeRunner()
            result = runner.run(diff, tmp_path)

        # Should have a summary finding indicating unverified surfaces
        summary_findings = [f for f in result if "smoke" in f.description.lower()
                            or "unverified" in f.description.lower()
                            or "not verified" in f.description.lower()]
        assert len(summary_findings) >= 1
        desc = summary_findings[0].description
        # 0 verified out of 2 surfaces
        assert "0/2" in desc or "NOT VERIFIED" in desc or "UNVERIFIED" in desc

    def test_receipt_matching_surface_marks_verified(self, tmp_path):
        """Receipt with matching diff_sha256 and surface -> VERIFIED."""
        from code_forge.runtime import RuntimeRunner
        from code_forge.source import compute_source_hash

        diff = "diff --git a/rules.sh b/rules.sh\n+change"
        diff_hash = compute_source_hash(git_diff=diff)

        receipts_dir = tmp_path / ".code-forge" / "smoke-receipts"
        self._write_receipt(receipts_dir, "nftables", diff_hash, "VERIFIED")

        response = self._mock_response(surfaces=["nftables", "systemd"], findings=[])

        with patch("code_forge.runtime.llm_invoke", return_value=response):
            runner = RuntimeRunner()
            result = runner.run(diff, tmp_path)

        # summary finding should show 1/2 verified
        summary_findings = [f for f in result
                            if f.id == "runtime-smoke-summary"]
        assert len(summary_findings) == 1
        desc = summary_findings[0].description
        assert "1/2" in desc

    def test_receipt_hash_mismatch_treated_as_invalid(self, tmp_path):
        """Receipt whose diff_sha256 doesn't match current hash -> discarded (Pitfall 3)."""
        from code_forge.runtime import RuntimeRunner

        diff = "diff --git a/rules.sh b/rules.sh\n+change"
        receipts_dir = tmp_path / ".code-forge" / "smoke-receipts"
        # Write receipt with WRONG hash
        self._write_receipt(receipts_dir, "nftables", "wrong_hash_abc123", "VERIFIED")

        response = self._mock_response(surfaces=["nftables"], findings=[])

        with patch("code_forge.runtime.llm_invoke", return_value=response):
            runner = RuntimeRunner()
            result = runner.run(diff, tmp_path)

        # Receipt is invalid -> surface is UNVERIFIED
        summary_findings = [f for f in result if f.id == "runtime-smoke-summary"]
        assert len(summary_findings) == 1
        desc = summary_findings[0].description
        assert "0/1" in desc or "NOT VERIFIED" in desc

    def test_all_surfaces_verified_when_all_receipts_present(self, tmp_path):
        """All surfaces have valid receipts -> summary says all verified."""
        from code_forge.runtime import RuntimeRunner
        from code_forge.source import compute_source_hash

        diff = "diff --git a/rules.sh b/rules.sh\n+change"
        diff_hash = compute_source_hash(git_diff=diff)

        receipts_dir = tmp_path / ".code-forge" / "smoke-receipts"
        self._write_receipt(receipts_dir, "nftables", diff_hash, "VERIFIED")

        response = self._mock_response(surfaces=["nftables"], findings=[])

        with patch("code_forge.runtime.llm_invoke", return_value=response):
            runner = RuntimeRunner()
            result = runner.run(diff, tmp_path)

        summary_findings = [f for f in result if f.id == "runtime-smoke-summary"]
        assert len(summary_findings) == 1
        desc = summary_findings[0].description
        assert "all" in desc.lower() or "1/1" in desc

    def test_no_surfaces_produces_no_summary(self, tmp_path):
        """LLM enumerates 0 surfaces -> no summary finding (GM-R5-L2 fix)."""
        from code_forge.runtime import RuntimeRunner

        diff = "diff --git a/f b/f\n+change"
        response = self._mock_response(surfaces=[], findings=[])

        with patch("code_forge.runtime.llm_invoke", return_value=response):
            runner = RuntimeRunner()
            result = runner.run(diff, tmp_path)

        summary_findings = [f for f in result if f.id == "runtime-smoke-summary"]
        assert len(summary_findings) == 0

    def test_case_insensitive_surface_matching(self, tmp_path):
        """Surface matching is case-insensitive substring containment (D-11)."""
        from code_forge.runtime import RuntimeRunner
        from code_forge.source import compute_source_hash

        diff = "diff --git a/f b/f\n+change"
        diff_hash = compute_source_hash(git_diff=diff)

        receipts_dir = tmp_path / ".code-forge" / "smoke-receipts"
        # Receipt says "NFTables" (uppercase), LLM said "nftables" (lowercase)
        self._write_receipt(receipts_dir, "NFTables", diff_hash, "VERIFIED")

        response = self._mock_response(surfaces=["nftables"], findings=[])

        with patch("code_forge.runtime.llm_invoke", return_value=response):
            runner = RuntimeRunner()
            result = runner.run(diff, tmp_path)

        summary_findings = [f for f in result if f.id == "runtime-smoke-summary"]
        assert len(summary_findings) == 1
        # Case-insensitive: should be VERIFIED
        desc = summary_findings[0].description
        assert "1/1" in desc or "all" in desc.lower()

    def test_summary_finding_has_runtime_axis(self, tmp_path):
        """Summary finding axis must be RUNTIME."""
        from code_forge.runtime import RuntimeRunner

        diff = "diff --git a/f b/f\n+change"
        response = self._mock_response(surfaces=["nftables"], findings=[])

        with patch("code_forge.runtime.llm_invoke", return_value=response):
            runner = RuntimeRunner()
            result = runner.run(diff, tmp_path)

        summary_findings = [f for f in result if f.id == "runtime-smoke-summary"]
        assert len(summary_findings) == 1
        assert summary_findings[0].axis == "RUNTIME"

    def test_infra_errors_cleared_on_each_run(self, tmp_path):
        """infra_errors is cleared at start of each run() call."""
        from code_forge.llm_invoke import LLMInvokeError
        from code_forge.runtime import RuntimeRunner

        runner = RuntimeRunner()
        # First run: LLM error
        with patch("code_forge.runtime.llm_invoke",
                   side_effect=LLMInvokeError("err1")):
            runner.run("diff --git a/f b/f\n+c", tmp_path)

        assert len(runner.infra_errors) > 0

        # Second run: success
        response = MagicMock()
        response.content = {"surfaces": [], "findings": []}
        with patch("code_forge.runtime.llm_invoke", return_value=response):
            runner.run("diff --git a/f b/f\n+c", tmp_path)

        # infra_errors should be cleared from previous run
        assert runner.infra_errors == []

    def test_surfaces_null_json_returns_no_summary_not_skipped(self, tmp_path):
        """LLM returns surfaces=null: coerced to [] not TypeError (M1 kill test)."""
        from unittest.mock import MagicMock, patch

        from code_forge.runtime import RuntimeRunner
        response = MagicMock()
        response.content = {"surfaces": None, "findings": []}
        with patch("code_forge.runtime.llm_invoke", return_value=response):
            runner = RuntimeRunner()
            result = runner.run("diff --git a/f b/f\n+c", tmp_path)
        ids = {f.id for f in result}
        assert "runtime-skipped" not in ids, "surfaces=null must not return SKIPPED"
        assert "runtime-smoke-summary" not in ids, "surfaces=null (->empty) must produce no summary"

    def test_one_directional_surface_match_is_verified(self, tmp_path):
        """Short receipt surface matches longer LLM surface (M2 kill: or not and, D-11)."""
        from unittest.mock import MagicMock, patch

        from code_forge.runtime import RuntimeRunner, write_smoke_receipt
        diff = "diff --git a/rules.nft b/rules.nft\n+add rule"
        # receipt surface "nft" is substring of LLM surface "nftables-filter"
        receipts_dir = tmp_path / ".code-forge" / "smoke-receipts"
        write_smoke_receipt(receipts_dir, diff, "nft", "nft list", 0, b"ok", "2026-06-12T00:00:00Z")
        response = MagicMock()
        response.content = {"surfaces": ["nftables-filter"], "findings": []}
        with patch("code_forge.runtime.llm_invoke", return_value=response):
            runner = RuntimeRunner()
            result = runner.run(diff, tmp_path)
        summary = next((f for f in result if f.id == "runtime-smoke-summary"), None)
        assert summary is not None
        assert "all 1 surfaces verified" in summary.description, (
            "D-11 either direction: short receipt surface 'nft' must match 'nftables-filter'"
        )

