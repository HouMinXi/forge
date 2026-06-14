# SPDX-License-Identifier: Apache-2.0
"""Tests for the eval CLI subcommand (17-04).

Verifies: parser registration, _run_eval dispatch, format_table stderr,
JSON output via --output, --runs validation, exit code on bad corpus path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Lazy-import test: ensure cli module can be loaded
from code_forge.cli import _build_parser, main
from code_forge.eval.corpus import CorpusEntry
from code_forge.eval.scorer import EvalResult, EvalSummary


# ---------------------------------------------------------------------------
# Parser registration tests
# ---------------------------------------------------------------------------


class TestEvalParser:
    """Verify eval subcommand is registered with correct args."""

    def test_eval_subcommand_recognized(self):
        """eval is a known subcommand in the parser."""
        parser = _build_parser()
        args = parser.parse_args(["eval", "--corpus", "c.yaml", "--backend", "b"])
        assert args.subcommand == "eval"

    def test_corpus_argument_required(self):
        """--corpus is required."""
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["eval", "--backend", "b"])

    def test_backend_argument_required(self):
        """--backend is required."""
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["eval", "--corpus", "c.yaml"])

    def test_runs_argument_optional_int(self):
        """--runs is optional and parsed as int."""
        parser = _build_parser()
        args = parser.parse_args([
            "eval", "--corpus", "c.yaml", "--backend", "b", "--runs", "5",
        ])
        assert args.runs == 5

    def test_output_argument_optional_path(self):
        """--output is optional and parsed as Path."""
        parser = _build_parser()
        args = parser.parse_args([
            "eval", "--corpus", "c.yaml", "--backend", "b",
            "--output", "/tmp/out.json",
        ])
        assert args.output == Path("/tmp/out.json")

    def test_runs_default_is_none(self):
        """--runs defaults to None (axis-dependent)."""
        parser = _build_parser()
        args = parser.parse_args([
            "eval", "--corpus", "c.yaml", "--backend", "b",
        ])
        assert args.runs is None

    def test_output_default_is_none(self):
        """--output defaults to None (no JSON output)."""
        parser = _build_parser()
        args = parser.parse_args([
            "eval", "--corpus", "c.yaml", "--backend", "b",
        ])
        assert args.output is None


# ---------------------------------------------------------------------------
# _run_eval dispatch tests (mocked -- no real LLM calls)
# ---------------------------------------------------------------------------


class TestRunEval:
    """Test _run_eval function with mocked replay_entry."""

    def _make_entry(self, name="test-bug", expected="HOLD"):
        return CorpusEntry(
            name=name,
            diff_file="diffs/%s.diff" % name,
            expected_verdict=expected,
            axis_tags=["RUNTIME"],
        )

    def _make_result(self, entry, actual="HOLD", runs=1, caught=1, skip=""):
        return EvalResult(
            entry=entry,
            actual_verdict=actual,
            runs=runs,
            caught_count=caught,
            skipped_reason=skip,
        )

    def test_run_eval_calls_pipeline(self, tmp_path, capsys):
        """_run_eval loads corpus, replays entries, prints table."""
        from code_forge.cli import _run_eval

        entry = self._make_entry()
        result = self._make_result(entry)

        mock_args = MagicMock()
        mock_args.corpus = tmp_path / "corpus.yaml"
        mock_args.backend = "test-backend"
        mock_args.runs = None
        mock_args.output = None

        with patch("code_forge.eval.corpus.load_corpus", return_value=[entry]), \
             patch("code_forge.eval.runner.replay_entry", return_value=result), \
             patch("code_forge.eval.scorer.compute_summary") as mock_summary, \
             patch("code_forge.eval.scorer.format_table", return_value="table-output"):

            summary = EvalSummary(
                total=1, caught=1, missed=0, correct_pass=0,
                false_positive=0, skipped=0, results=[result],
                advisory_caught=0, advisory_missed=0,
            )
            mock_summary.return_value = summary

            rc = _run_eval(mock_args)

        assert rc == 0
        captured = capsys.readouterr()
        assert "table-output" in captured.err

    def test_run_eval_writes_json_output(self, tmp_path):
        """--output causes JSON report to be written."""
        from code_forge.cli import _run_eval

        entry = self._make_entry()
        result = self._make_result(entry)
        out_path = tmp_path / "results.json"

        mock_args = MagicMock()
        mock_args.corpus = tmp_path / "corpus.yaml"
        mock_args.backend = "test-backend"
        mock_args.runs = None
        mock_args.output = out_path

        summary = EvalSummary(
            total=1, caught=1, missed=0, correct_pass=0,
            false_positive=0, skipped=0, results=[result],
            advisory_caught=0, advisory_missed=0,
        )

        with patch("code_forge.eval.corpus.load_corpus", return_value=[entry]), \
             patch("code_forge.eval.runner.replay_entry", return_value=result), \
             patch("code_forge.eval.scorer.compute_summary", return_value=summary), \
             patch("code_forge.eval.scorer.format_table", return_value="t"), \
             patch("code_forge.eval.scorer.write_json_report") as mock_write:

            rc = _run_eval(mock_args)

        assert rc == 0
        mock_write.assert_called_once_with(summary, out_path)

    def test_run_eval_passes_runs_override(self, tmp_path):
        """--runs N is forwarded to replay_entry."""
        from code_forge.cli import _run_eval

        entry = self._make_entry()
        result = self._make_result(entry, runs=5, caught=3)

        mock_args = MagicMock()
        mock_args.corpus = tmp_path / "corpus.yaml"
        mock_args.backend = "test-backend"
        mock_args.runs = 5
        mock_args.output = None

        summary = EvalSummary(
            total=1, caught=1, missed=0, correct_pass=0,
            false_positive=0, skipped=0, results=[result],
            advisory_caught=0, advisory_missed=0,
        )

        with patch("code_forge.eval.corpus.load_corpus", return_value=[entry]), \
             patch("code_forge.eval.runner.replay_entry", return_value=result) as mock_replay, \
             patch("code_forge.eval.scorer.compute_summary", return_value=summary), \
             patch("code_forge.eval.scorer.format_table", return_value="t"):

            rc = _run_eval(mock_args)

        assert rc == 0
        mock_replay.assert_called_once()
        call_kwargs = mock_replay.call_args
        assert call_kwargs[1].get("runs") == 5 or call_kwargs[0][3] == 5

    def test_run_eval_runs_validation_rejects_zero(self, capsys):
        """--runs 0 prints error and returns EXIT_CLI_ERROR."""
        from code_forge.cli import _run_eval
        from code_forge.exit_codes import EXIT_CLI_ERROR

        mock_args = MagicMock()
        mock_args.corpus = Path("/tmp/nonexistent.yaml")
        mock_args.backend = "test"
        mock_args.runs = 0
        mock_args.output = None

        rc = _run_eval(mock_args)
        assert rc == EXIT_CLI_ERROR
        captured = capsys.readouterr()
        assert "--runs must be >= 1" in captured.err

    def test_run_eval_runs_validation_rejects_negative(self, capsys):
        """--runs -1 prints error and returns EXIT_CLI_ERROR."""
        from code_forge.cli import _run_eval
        from code_forge.exit_codes import EXIT_CLI_ERROR

        mock_args = MagicMock()
        mock_args.corpus = Path("/tmp/nonexistent.yaml")
        mock_args.backend = "test"
        mock_args.runs = -1
        mock_args.output = None

        rc = _run_eval(mock_args)
        assert rc == EXIT_CLI_ERROR
        captured = capsys.readouterr()
        assert "--runs must be >= 1" in captured.err

    def test_run_eval_file_not_found(self, capsys, tmp_path):
        """Missing corpus file returns EXIT_CLI_ERROR."""
        from code_forge.cli import _run_eval
        from code_forge.exit_codes import EXIT_CLI_ERROR

        mock_args = MagicMock()
        mock_args.corpus = tmp_path / "no-such-file.yaml"
        mock_args.backend = "test"
        mock_args.runs = None
        mock_args.output = None

        rc = _run_eval(mock_args)
        assert rc == EXIT_CLI_ERROR

    def test_run_eval_malformed_yaml(self, capsys, tmp_path):
        """Malformed YAML corpus returns EXIT_CLI_ERROR."""
        from code_forge.cli import _run_eval
        from code_forge.exit_codes import EXIT_CLI_ERROR

        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text(": :\n  - [invalid", encoding="utf-8")

        mock_args = MagicMock()
        mock_args.corpus = bad_yaml
        mock_args.backend = "test"
        mock_args.runs = None
        mock_args.output = None

        rc = _run_eval(mock_args)
        assert rc == EXIT_CLI_ERROR


# ---------------------------------------------------------------------------
# known_subcommands includes eval
# ---------------------------------------------------------------------------


class TestEvalKnownSubcommand:
    """Verify eval is in the known_subcommands set."""

    def test_eval_in_known_subcommands(self):
        """eval must be in known_subcommands to avoid review fallback."""
        import code_forge.cli as cli_mod
        src = Path(cli_mod.__file__).read_text(encoding="utf-8")
        assert "'eval'" in src or '"eval"' in src


# ---------------------------------------------------------------------------
# Corpus manifest completeness test
# ---------------------------------------------------------------------------


class TestCorpusCompleteness:
    """Verify corpus.yaml contains all 11 required entries."""

    REQUIRED_NAMES = {
        "gate-yaml-rce",
        "E1-stale-nftables",
        "E2-pcap-suffix",
        "E3-transit-probe",
        "E4-curl-tproxy",
        "E5-fast-502",
        "E6-reprobe-blackout",
        "E7-killswitch-reprobe",
        "BUG-P12-01",
        "ttl_class",
        "E8-blast-radius-llm-invoke",
    }

    def test_corpus_has_all_entries(self):
        """corpus.yaml must contain all 11 EVAL-01 SC1 entries."""
        from code_forge.eval.corpus import load_corpus

        manifest = Path(__file__).parent / "eval" / "corpus" / "corpus.yaml"
        entries = load_corpus(manifest)
        names = {e.name for e in entries}
        assert names == self.REQUIRED_NAMES, (
            "Missing: %s, Extra: %s"
            % (self.REQUIRED_NAMES - names, names - self.REQUIRED_NAMES)
        )

    def test_all_diff_files_exist_and_nonempty(self):
        """Each entry's diff_file must exist and be non-empty."""
        from code_forge.eval.corpus import load_corpus

        manifest = Path(__file__).parent / "eval" / "corpus" / "corpus.yaml"
        entries = load_corpus(manifest)
        corpus_dir = manifest.parent
        for entry in entries:
            diff_path = corpus_dir / entry.diff_file
            assert diff_path.exists(), "diff file missing: %s" % entry.diff_file
            assert diff_path.stat().st_size > 0, (
                "diff file empty: %s" % entry.diff_file
            )
