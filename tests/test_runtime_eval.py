"""Tests for RUNTIME advisory eval axis.

Covers:
- CorpusEntry.expected_advisory field (extension + backward compat)
- load_corpus parses expected_advisory from YAML
- advisory_caught() keyword matching helper (scorer.py)
- EvalResult.advisory_caught_count field
- EvalSummary.advisory_caught / advisory_missed fields
- RuntimeAxisHook (registered, post_review is no-op)
- Runner per-run loop reads advisory-findings.json and increments advisory counter
- corpus.yaml E1-E6 entries have expected_advisory + corrected expected_verdict
- Eval concat excludes runtime-smoke-summary findings
- compute_summary uses advisory_caught_count for pure-RUNTIME entries
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from code_forge.eval.corpus import CorpusEntry, load_corpus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(
    name: str = "test",
    expected_verdict: str = "PASS",
    tags: list[str] | None = None,
    diff_file: str = "diffs/test.diff",
    expected_advisory: list[str] | None = None,
) -> CorpusEntry:
    kwargs: dict[str, Any] = {
        "name": name,
        "diff_file": diff_file,
        "expected_verdict": expected_verdict,
        "axis_tags": tags or ["RUNTIME"],
    }
    if expected_advisory is not None:
        kwargs["expected_advisory"] = expected_advisory
    return CorpusEntry(**kwargs)


# ===========================================================================
# CorpusEntry.expected_advisory field
# ===========================================================================


class TestCorpusEntryExpectedAdvisory:
    """CorpusEntry extended with optional expected_advisory field."""

    def test_default_is_empty_list(self) -> None:
        """CorpusEntry constructed without expected_advisory defaults to []."""
        entry = CorpusEntry(
            name="e",
            diff_file="d.diff",
            expected_verdict="PASS",
            axis_tags=["RUNTIME"],
        )
        assert entry.expected_advisory == []

    def test_accepts_keyword_list(self) -> None:
        """CorpusEntry accepts expected_advisory as keyword list."""
        entry = CorpusEntry(
            name="e",
            diff_file="d.diff",
            expected_verdict="PASS",
            axis_tags=["RUNTIME"],
            expected_advisory=["nftables", "stale"],
        )
        assert entry.expected_advisory == ["nftables", "stale"]

    def test_frozen_dataclass_still_works(self) -> None:
        """CorpusEntry remains frozen (immutable)."""
        entry = _entry(expected_advisory=["foo"])
        with pytest.raises((AttributeError, TypeError)):
            entry.expected_advisory = ["bar"]  # type: ignore[misc]

    def test_existing_entry_without_advisory_still_constructs(self) -> None:
        """Existing entries (no expected_advisory) are backward-compatible."""
        entry = CorpusEntry(
            name="gate-yaml-rce",
            diff_file="diffs/gate-yaml-rce.diff",
            expected_verdict="HOLD",
            axis_tags=["TRUST", "SEC"],
        )
        assert entry.expected_advisory == []


class TestLoadCorpusExpectedAdvisory:
    """load_corpus parses expected_advisory from YAML."""

    def test_parses_expected_advisory_when_present(self, tmp_path: Path) -> None:
        yaml_text = textwrap.dedent("""\
            entries:
              - name: E1-stale-nftables
                diff_file: diffs/E1-stale-nftables.diff
                expected_verdict: PASS
                axis_tags: [RUNTIME]
                expected_advisory: ["nftables", "stale", "reload"]
        """)
        manifest = tmp_path / "corpus.yaml"
        manifest.write_text(yaml_text, encoding="utf-8")
        entries = load_corpus(manifest)
        assert len(entries) == 1
        assert entries[0].expected_advisory == ["nftables", "stale", "reload"]

    def test_returns_empty_list_when_field_absent(self, tmp_path: Path) -> None:
        yaml_text = textwrap.dedent("""\
            entries:
              - name: gate-yaml-rce
                diff_file: diffs/gate-yaml-rce.diff
                expected_verdict: HOLD
                axis_tags: [TRUST, SEC]
        """)
        manifest = tmp_path / "corpus.yaml"
        manifest.write_text(yaml_text, encoding="utf-8")
        entries = load_corpus(manifest)
        assert entries[0].expected_advisory == []

    def test_mixed_entries_backward_compat(self, tmp_path: Path) -> None:
        """Mix of entries with and without expected_advisory all load correctly."""
        yaml_text = textwrap.dedent("""\
            entries:
              - name: with-advisory
                diff_file: diffs/a.diff
                expected_verdict: PASS
                axis_tags: [RUNTIME]
                expected_advisory: ["nftables"]
              - name: without-advisory
                diff_file: diffs/b.diff
                expected_verdict: HOLD
                axis_tags: [TRUST]
        """)
        manifest = tmp_path / "corpus.yaml"
        manifest.write_text(yaml_text, encoding="utf-8")
        entries = load_corpus(manifest)
        assert entries[0].expected_advisory == ["nftables"]
        assert entries[1].expected_advisory == []


# ===========================================================================
# advisory_caught() helper
# ===========================================================================


class TestAdvisoryCaught:
    """advisory_caught() performs case-insensitive keyword substring matching (D-12)."""

    def setup_method(self) -> None:
        from code_forge.eval.scorer import advisory_caught
        self.advisory_caught = advisory_caught

    def test_all_keywords_present_returns_true(self) -> None:
        assert self.advisory_caught(
            "nftables rules may be stale", ["nftables", "stale"]
        )

    def test_single_keyword_hit_returns_true(self) -> None:
        assert self.advisory_caught(
            "nftables rules may be stale", ["nftables"]
        )

    def test_no_keyword_match_returns_false(self) -> None:
        assert not self.advisory_caught("clean code review", ["nftables"])

    def test_case_insensitive_match(self) -> None:
        """Matching is case-insensitive (D-12)."""
        assert self.advisory_caught("NFTABLES reload needed", ["nftables"])

    def test_empty_text_returns_false(self) -> None:
        assert not self.advisory_caught("", ["nftables"])

    def test_empty_keywords_returns_false(self) -> None:
        assert not self.advisory_caught("some text about nftables", [])

    def test_substring_not_exact_word(self) -> None:
        """Substring containment, not exact word match."""
        assert self.advisory_caught("nftables-related stale rules", ["nftables"])

    def test_first_keyword_hit_is_sufficient(self) -> None:
        """Any keyword hit = True (not requiring ALL keywords)."""
        assert self.advisory_caught("only nftables here", ["nftables", "missing_kw"])

    def test_mixed_case_both_sides(self) -> None:
        assert self.advisory_caught("NFTABLES Rules MAY be Stale", ["Stale"])

    def test_no_text_with_empty_keywords(self) -> None:
        assert not self.advisory_caught("", [])


# ===========================================================================
# EvalResult.advisory_caught_count field
# ===========================================================================


class TestEvalResultAdvisoryCaughtCount:
    """EvalResult has advisory_caught_count: int = 0 (separate from caught_count)."""

    def test_default_advisory_caught_count_is_zero(self) -> None:
        from code_forge.eval.scorer import EvalResult
        entry = _entry()
        result = EvalResult(
            entry=entry,
            actual_verdict="PASS",
            runs=3,
            caught_count=0,
            skipped_reason="",
        )
        assert result.advisory_caught_count == 0

    def test_advisory_caught_count_settable(self) -> None:
        from code_forge.eval.scorer import EvalResult
        entry = _entry()
        result = EvalResult(
            entry=entry,
            actual_verdict="PASS",
            runs=3,
            caught_count=0,
            skipped_reason="",
            advisory_caught_count=2,
        )
        assert result.advisory_caught_count == 2

    def test_caught_count_independent_of_advisory_caught_count(self) -> None:
        """caught_count and advisory_caught_count are fully independent fields."""
        from code_forge.eval.scorer import EvalResult
        entry = _entry()
        result = EvalResult(
            entry=entry,
            actual_verdict="PASS",
            runs=3,
            caught_count=0,
            skipped_reason="",
            advisory_caught_count=3,
        )
        # verdict-match caught_count stayed 0 even though advisory hit 3/3
        assert result.caught_count == 0
        assert result.advisory_caught_count == 3


# ===========================================================================
# EvalSummary.advisory_caught / advisory_missed fields
# ===========================================================================


class TestEvalSummaryAdvisoryFields:
    """EvalSummary has advisory_caught and advisory_missed counters."""

    def test_advisory_caught_field_exists(self) -> None:
        import dataclasses

        from code_forge.eval.scorer import EvalSummary
        fields = {f.name for f in dataclasses.fields(EvalSummary)}
        assert "advisory_caught" in fields
        assert "advisory_missed" in fields


# ===========================================================================
# RuntimeAxisHook
# ===========================================================================


class TestRuntimeAxisHook:
    """RuntimeAxisHook: registered, no-op post_review."""

    def test_runtime_axis_hook_registered(self) -> None:
        """RuntimeAxisHook is registered in _AXIS_HOOKS."""
        import code_forge.eval.runner as runner_mod
        from code_forge.eval.runner import RuntimeAxisHook
        assert any(isinstance(h, RuntimeAxisHook) for h in runner_mod._AXIS_HOOKS)

    def test_post_review_is_noop(self) -> None:
        """RuntimeAxisHook.post_review does nothing (scoring is in runner loop)."""
        from code_forge.eval.runner import RuntimeAxisHook
        from code_forge.eval.scorer import EvalResult
        hook = RuntimeAxisHook()
        entry = _entry()
        result = EvalResult(
            entry=entry, actual_verdict="PASS", runs=3,
            caught_count=0, skipped_reason="", advisory_caught_count=0,
        )
        # Must not raise; must not call advisory_caught
        with patch("code_forge.eval.scorer.advisory_caught") as mock_ac:
            hook.post_review(entry, result)
            mock_ac.assert_not_called()

    def test_pre_review_is_noop(self) -> None:
        """RuntimeAxisHook.pre_review does nothing."""
        from code_forge.eval.runner import RuntimeAxisHook
        hook = RuntimeAxisHook()
        entry = _entry()
        hook.pre_review(entry)  # must not raise

    def test_runtime_not_in_deterministic_tags(self) -> None:
        """RUNTIME is an LLM axis (3-run majority), not deterministic."""
        from code_forge.eval.runner import DETERMINISTIC_TAGS
        assert "RUNTIME" not in DETERMINISTIC_TAGS


# ===========================================================================
# Runner per-run loop reads advisory-findings.json
# ===========================================================================


class TestRunnerAdvisoryScoring:
    """replay_entry reads advisory-findings.json and increments advisory counter."""

    def _make_advisory_findings(
        self, tmp_dir: str, findings: list[dict]
    ) -> None:
        """Write advisory-findings.json into the temp review dir."""
        path = Path(tmp_dir) / "advisory-findings.json"
        path.write_text(json.dumps(findings), encoding="utf-8")

    @patch("code_forge.eval.runner.subprocess.run")
    @patch("code_forge.eval.runner.record_trust")
    def test_advisory_caught_count_incremented_on_keyword_match(
        self, mock_trust: MagicMock, mock_run: MagicMock, tmp_path: Path,
    ) -> None:
        """advisory_caught_count >= 2 (majority) when advisory text matches keywords."""
        import code_forge.eval.runner as runner_mod

        advisory_findings = [
            {
                "id": "runtime-0",
                "axis": "RUNTIME",
                "description": "nftables rules may be stale after reload",
            },
        ]

        def side_effect(cmd, **kwargs):
            m = MagicMock()
            m.returncode = 0
            m.stderr = b""
            m.stdout = b""
            return m

        mock_run.side_effect = side_effect

        # Patch _run_single to also write advisory-findings.json in temp dir
        written_dirs: list[str] = []

        def patched_run_single(entry, diff_path, temp_dir, backend_name,
                               backend_config=None, corpus_dir=None):
            # Write advisory-findings.json before cleanup
            self._make_advisory_findings(temp_dir, advisory_findings)
            written_dirs.append(temp_dir)
            return False, ""  # not flagged, no skip

        diff_dir = tmp_path / "corpus"
        (diff_dir / "diffs").mkdir(parents=True)
        (diff_dir / "diffs" / "test.diff").write_text("--- a/f\n+++ b/f\n")

        entry = _entry(
            expected_verdict="PASS",
            tags=["RUNTIME"],
            expected_advisory=["nftables", "stale"],
        )

        with patch.object(runner_mod, "_run_single", side_effect=patched_run_single):
            result = runner_mod.replay_entry(entry, diff_dir, "test-backend", runs=3)

        # 3 runs all matched -> advisory_caught_count = 3 >= majority 2
        assert result.advisory_caught_count >= 2

    @patch("code_forge.eval.runner.subprocess.run")
    @patch("code_forge.eval.runner.record_trust")
    def test_advisory_no_match_keeps_zero(
        self, mock_trust: MagicMock, mock_run: MagicMock, tmp_path: Path,
    ) -> None:
        """advisory_caught_count stays 0 when text does not match keywords."""
        import code_forge.eval.runner as runner_mod

        advisory_findings = [
            {
                "id": "runtime-0",
                "axis": "RUNTIME",
                "description": "clean code path, no runtime issues",
            },
        ]

        def patched_run_single(entry, diff_path, temp_dir, backend_name,
                               backend_config=None, corpus_dir=None):
            self._make_advisory_findings(temp_dir, advisory_findings)
            return False, ""

        diff_dir = tmp_path / "corpus"
        (diff_dir / "diffs").mkdir(parents=True)
        (diff_dir / "diffs" / "test.diff").write_text("--- a/f\n+++ b/f\n")

        entry = _entry(
            expected_verdict="PASS",
            tags=["RUNTIME"],
            expected_advisory=["nftables", "stale"],
        )

        with patch.object(runner_mod, "_run_single", side_effect=patched_run_single):
            result = runner_mod.replay_entry(entry, diff_dir, "test-backend", runs=3)

        assert result.advisory_caught_count == 0

    @patch("code_forge.eval.runner.subprocess.run")
    @patch("code_forge.eval.runner.record_trust")
    def test_caught_count_not_contaminated_by_advisory(
        self, mock_trust: MagicMock, mock_run: MagicMock, tmp_path: Path,
    ) -> None:
        """caught_count (verdict-match) is NOT affected by advisory keyword match."""
        import code_forge.eval.runner as runner_mod

        advisory_findings = [
            {
                "id": "runtime-0",
                "axis": "RUNTIME",
                "description": "nftables rules may be stale",
            },
        ]

        def patched_run_single(entry, diff_path, temp_dir, backend_name,
                               backend_config=None, corpus_dir=None):
            self._make_advisory_findings(temp_dir, advisory_findings)
            return False, ""  # forge returned PASS (not flagged)

        diff_dir = tmp_path / "corpus"
        (diff_dir / "diffs").mkdir(parents=True)
        (diff_dir / "diffs" / "test.diff").write_text("--- a/f\n+++ b/f\n")

        entry = _entry(
            expected_verdict="PASS",
            tags=["RUNTIME"],
            expected_advisory=["nftables"],
        )

        with patch.object(runner_mod, "_run_single", side_effect=patched_run_single):
            result = runner_mod.replay_entry(entry, diff_dir, "test-backend", runs=3)

        # forge said PASS all 3 runs -> caught_count stays 0
        assert result.caught_count == 0
        # advisory matched -> advisory_caught_count >= 2
        assert result.advisory_caught_count >= 2

    @patch("code_forge.eval.runner.subprocess.run")
    @patch("code_forge.eval.runner.record_trust")
    def test_runtime_smoke_summary_excluded_from_concat(
        self, mock_trust: MagicMock, mock_run: MagicMock, tmp_path: Path,
    ) -> None:
        """runtime-smoke-summary finding is excluded from advisory keyword concat."""
        import code_forge.eval.runner as runner_mod

        # The summary finding contains surface names that would false-positive keywords.
        # Even if it contains "nftables", it must NOT count as a keyword hit.
        advisory_findings = [
            {
                "id": "runtime-smoke-summary",
                "axis": "RUNTIME",
                "description": "smoke: 0/1 surfaces verified; NOT VERIFIED: [nftables]",
            },
        ]

        def patched_run_single(entry, diff_path, temp_dir, backend_name,
                               backend_config=None, corpus_dir=None):
            self._make_advisory_findings(temp_dir, advisory_findings)
            return False, ""

        diff_dir = tmp_path / "corpus"
        (diff_dir / "diffs").mkdir(parents=True)
        (diff_dir / "diffs" / "test.diff").write_text("--- a/f\n+++ b/f\n")

        # keywords: "nftables" -- present in smoke-summary but must be excluded
        entry = _entry(
            expected_verdict="PASS",
            tags=["RUNTIME"],
            expected_advisory=["nftables"],
        )

        with patch.object(runner_mod, "_run_single", side_effect=patched_run_single):
            result = runner_mod.replay_entry(entry, diff_dir, "test-backend", runs=3)

        # smoke-summary excluded -> no keyword hit -> advisory_caught_count == 0
        assert result.advisory_caught_count == 0

    @patch("code_forge.eval.runner.subprocess.run")
    @patch("code_forge.eval.runner.record_trust")
    def test_no_expected_advisory_skips_advisory_scoring(
        self, mock_trust: MagicMock, mock_run: MagicMock, tmp_path: Path,
    ) -> None:
        """Entry with empty expected_advisory has advisory_caught_count=0 (no scoring)."""
        import code_forge.eval.runner as runner_mod

        advisory_findings = [
            {"id": "runtime-0", "axis": "RUNTIME", "description": "nftables stale"},
        ]

        def patched_run_single(entry, diff_path, temp_dir, backend_name,
                               backend_config=None, corpus_dir=None):
            self._make_advisory_findings(temp_dir, advisory_findings)
            return False, ""

        diff_dir = tmp_path / "corpus"
        (diff_dir / "diffs").mkdir(parents=True)
        (diff_dir / "diffs" / "test.diff").write_text("--- a/f\n+++ b/f\n")

        entry = _entry(
            expected_verdict="PASS",
            tags=["RUNTIME"],
            expected_advisory=[],  # no keywords -> no scoring
        )

        with patch.object(runner_mod, "_run_single", side_effect=patched_run_single):
            result = runner_mod.replay_entry(entry, diff_dir, "test-backend", runs=3)

        assert result.advisory_caught_count == 0


# ===========================================================================
# compute_summary uses advisory_caught_count for pure-RUNTIME entries
# ===========================================================================


class TestComputeSummaryAdvisoryScoring:
    """compute_summary advisory axis: pure-RUNTIME (expected_verdict=PASS) entries."""

    def _make_result(
        self,
        expected_verdict: str,
        actual_verdict: str,
        caught_count: int,
        advisory_caught_count: int,
        expected_advisory: list[str],
        tags: list[str] | None = None,
        runs: int = 3,
    ):
        from code_forge.eval.scorer import EvalResult
        entry = _entry(
            expected_verdict=expected_verdict,
            tags=tags or ["RUNTIME"],
            expected_advisory=expected_advisory,
        )
        return EvalResult(
            entry=entry,
            actual_verdict=actual_verdict,
            runs=runs,
            caught_count=caught_count,
            skipped_reason="",
            advisory_caught_count=advisory_caught_count,
        )

    def test_pure_runtime_advisory_hit_counted(self) -> None:
        """RUNTIME entry: advisory_caught_count>=majority -> advisory_caught incremented."""
        from code_forge.eval.scorer import compute_summary
        result = self._make_result(
            expected_verdict="PASS",
            actual_verdict="PASS",
            caught_count=0,
            advisory_caught_count=2,  # 2 of 3 runs matched
            expected_advisory=["nftables"],
        )
        summary = compute_summary([result])
        assert summary.advisory_caught == 1
        assert summary.advisory_missed == 0

    def test_pure_runtime_advisory_miss_counted(self) -> None:
        """RUNTIME entry: advisory_caught_count<majority -> advisory_missed incremented."""
        from code_forge.eval.scorer import compute_summary
        result = self._make_result(
            expected_verdict="PASS",
            actual_verdict="PASS",
            caught_count=0,
            advisory_caught_count=0,  # zero hits
            expected_advisory=["nftables"],
        )
        summary = compute_summary([result])
        assert summary.advisory_caught == 0
        assert summary.advisory_missed == 1

    def test_non_runtime_entry_unaffected(self) -> None:
        """TRUST entry (HOLD expected) uses verdict-match, not advisory scoring."""
        from code_forge.eval.scorer import EvalResult, compute_summary
        entry = CorpusEntry(
            name="gate-yaml-rce",
            diff_file="diffs/gate-yaml-rce.diff",
            expected_verdict="HOLD",
            axis_tags=["TRUST", "SEC"],
            expected_advisory=[],
        )
        result = EvalResult(
            entry=entry,
            actual_verdict="HOLD",
            runs=1,
            caught_count=1,
            skipped_reason="",
            advisory_caught_count=0,
        )
        summary = compute_summary([result])
        # verdict-match: caught=1, missed=0
        assert summary.caught == 1
        assert summary.missed == 0
        # advisory counts don't apply here (no expected_advisory)
        assert summary.advisory_caught == 0
        assert summary.advisory_missed == 0

    def test_dual_axis_entry_verdict_match_gates_caught(self) -> None:
        """ttl_class (RUNTIME+FIXVAL, HOLD): verdict-match determines caught, not advisory."""
        from code_forge.eval.scorer import EvalResult, compute_summary
        entry = CorpusEntry(
            name="ttl_class",
            diff_file="diffs/ttl_class.diff",
            expected_verdict="HOLD",
            axis_tags=["RUNTIME", "FIXVAL"],
            expected_advisory=["ttl", "class"],
        )
        result = EvalResult(
            entry=entry,
            actual_verdict="HOLD",
            runs=1,
            caught_count=1,
            skipped_reason="",
            advisory_caught_count=1,
        )
        summary = compute_summary([result])
        # FIXVAL blocks -> caught=1 (verdict-match is the gate)
        assert summary.caught == 1
        assert summary.missed == 0


# ===========================================================================
# corpus.yaml E1-E6 verification
# ===========================================================================


CORPUS_YAML = (
    Path(__file__).parent
    / "eval"
    / "corpus"
    / "corpus.yaml"
)


class TestCorpusYamlE1E6:
    """E1-E6 entries in corpus.yaml have expected_advisory and corrected expected_verdict."""

    def _load(self) -> list[CorpusEntry]:
        return load_corpus(CORPUS_YAML)

    def _by_name(self, name: str) -> CorpusEntry:
        entries = {e.name: e for e in self._load()}
        assert name in entries, f"Entry {name!r} not found in corpus.yaml"
        return entries[name]

    def test_e1_has_expected_advisory(self) -> None:
        e = self._by_name("E1-stale-nftables")
        assert len(e.expected_advisory) >= 2, "E1 must have at least 2 keywords"
        assert any("nftables" in kw.lower() for kw in e.expected_advisory)

    def test_e2_has_expected_advisory(self) -> None:
        e = self._by_name("E2-pcap-suffix")
        assert len(e.expected_advisory) >= 2
        assert any("pcap" in kw.lower() for kw in e.expected_advisory)

    def test_e3_has_expected_advisory(self) -> None:
        e = self._by_name("E3-transit-probe")
        assert len(e.expected_advisory) >= 2
        assert any("probe" in kw.lower() or "transit" in kw.lower() for kw in e.expected_advisory)

    def test_e4_has_expected_advisory(self) -> None:
        e = self._by_name("E4-curl-tproxy")
        assert len(e.expected_advisory) >= 2
        assert any("tproxy" in kw.lower() or "proxy" in kw.lower() for kw in e.expected_advisory)

    def test_e5_has_expected_advisory(self) -> None:
        e = self._by_name("E5-fast-502")
        assert len(e.expected_advisory) >= 2
        assert any("502" in kw or "timeout" in kw.lower() for kw in e.expected_advisory)

    def test_e6_has_expected_advisory(self) -> None:
        e = self._by_name("E6-reprobe-blackout")
        assert len(e.expected_advisory) >= 2
        assert any("reprobe" in kw.lower() or "probe" in kw.lower() for kw in e.expected_advisory)

    def test_e1_expected_verdict_is_pass(self) -> None:
        """E1-E6 expected_verdict corrected to PASS (D-06: RUNTIME is advisory, cannot block)."""
        e = self._by_name("E1-stale-nftables")
        assert e.expected_verdict == "PASS", (
            "D-06: RUNTIME axis cannot block; E1 expected_verdict must be PASS"
        )

    def test_e2_expected_verdict_is_pass(self) -> None:
        e = self._by_name("E2-pcap-suffix")
        assert e.expected_verdict == "PASS"

    def test_e3_expected_verdict_is_pass(self) -> None:
        e = self._by_name("E3-transit-probe")
        assert e.expected_verdict == "PASS"

    def test_e4_expected_verdict_is_pass(self) -> None:
        e = self._by_name("E4-curl-tproxy")
        assert e.expected_verdict == "PASS"

    def test_e5_expected_verdict_is_pass(self) -> None:
        e = self._by_name("E5-fast-502")
        assert e.expected_verdict == "PASS"

    def test_e6_expected_verdict_is_pass(self) -> None:
        e = self._by_name("E6-reprobe-blackout")
        assert e.expected_verdict == "PASS"

    def test_ttl_class_retains_hold_verdict(self) -> None:
        """ttl_class is FIXVAL+RUNTIME; FIXVAL can block -> expected_verdict stays HOLD."""
        e = self._by_name("ttl_class")
        assert e.expected_verdict == "HOLD"

    def test_ttl_class_has_expected_advisory(self) -> None:
        """ttl_class gains expected_advisory for RUNTIME scoring."""
        e = self._by_name("ttl_class")
        assert len(e.expected_advisory) >= 2
        assert any("ttl" in kw.lower() for kw in e.expected_advisory)

    def test_gate_yaml_rce_no_expected_advisory_needed(self) -> None:
        """gate-yaml-rce is TRUST/SEC -- no RUNTIME advisory needed."""
        e = self._by_name("gate-yaml-rce")
        # expected_advisory may be empty for non-RUNTIME entries
        assert isinstance(e.expected_advisory, list)

    def test_all_e1_e6_have_runtime_tag(self) -> None:
        """All E1-E6 entries have RUNTIME in axis_tags."""
        entries = {e.name: e for e in self._load()}
        for name in ["E1-stale-nftables", "E2-pcap-suffix", "E3-transit-probe",
                     "E4-curl-tproxy", "E5-fast-502", "E6-reprobe-blackout"]:
            assert "RUNTIME" in entries[name].axis_tags, f"{name} missing RUNTIME tag"


class TestMutantKillCoverage:
    """Kill tests for R2 surviving mutants (M8, M10)."""

    def test_read_advisory_findings_rejects_dict_json(self, tmp_path):
        """advisory-findings.json as dict returns [] not [dict] (M8 kill test)."""
        import json
        from code_forge.eval.runner import _read_advisory_findings
        (tmp_path / "advisory-findings.json").write_text(json.dumps({"error": "timeout"}))
        result = _read_advisory_findings(str(tmp_path))
        assert result == [], "dict-format advisory-findings.json must return [] not [dict]"

    def test_is_pure_runtime_empty_advisory_not_classified(self):
        """PASS+empty expected_advisory is NOT pure-RUNTIME (M10 kill test)."""
        from code_forge.eval.scorer import _is_pure_runtime_advisory, EvalResult
        from code_forge.eval.corpus import CorpusEntry
        entry = CorpusEntry("t", "f.diff", "PASS", ["RUNTIME"], [])
        result = EvalResult(entry, "PASS", 1, 0, None, advisory_caught_count=0)
        assert not _is_pure_runtime_advisory(result), (
            "PASS+empty advisory must NOT be pure-RUNTIME (no keywords to match)"
        )

    def test_compute_summary_skips_empty_advisory_entry(self):
        """PASS+empty advisory entry does not inflate advisory_missed (M10 kill)."""
        from code_forge.eval.scorer import compute_summary, EvalResult
        from code_forge.eval.corpus import CorpusEntry
        entry = CorpusEntry("no-advisory", "f.diff", "PASS", ["RUNTIME"], [])
        result = EvalResult(entry, "PASS", 1, 0, None, advisory_caught_count=0)
        summary = compute_summary([result])
        assert summary.advisory_missed == 0, "empty advisory entry must not count as missed"
        assert summary.advisory_caught == 0
        assert summary.correct_pass == 1
