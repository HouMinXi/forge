# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Unit tests for mutation.py module.

Covers parse_mutmut_results and run_mutation functions with all scenarios:
empty input, single survivor, ranges, comma-separated, multi-file, malformed
(parser tests); empty files, non-Python, flaky guard, mutmut missing, timeout,
success with/without survivors (run_mutation tests).
"""

import subprocess
from unittest.mock import patch

from forge.disposition import Disposition
from forge.mutation import parse_mutmut_results, run_mutation


class TestParseMutmutResults:
    """Test parse_mutmut_results with various mutmut output formats."""

    def test_empty_string_returns_empty_list(self):
        """Test 1: empty string returns []"""
        survivors, warnings = parse_mutmut_results("")
        assert survivors == []

    def test_single_survivor_single_id(self):
        """Test 2: single survivor single ID"""
        stdout = """Survived (1)
---- ./src/foo.py (1) ----
5
"""
        survivors, warnings = parse_mutmut_results(stdout)
        assert len(survivors) == 1
        assert survivors[0].file == "./src/foo.py"
        assert survivors[0].mutant_id == 5

    def test_range_produces_multiple_ids(self):
        """Test 3: range "1-3" produces IDs 1, 2, 3"""
        stdout = """Survived (3)
---- ./src/bar.py (3) ----
1-3
"""
        survivors, warnings = parse_mutmut_results(stdout)
        assert len(survivors) == 3
        assert survivors[0].mutant_id == 1
        assert survivors[1].mutant_id == 2
        assert survivors[2].mutant_id == 3
        assert all(s.file == "./src/bar.py" for s in survivors)

    def test_comma_separated_produces_correct_ids(self):
        """Test 4: comma-separated "1, 5, 7" produces 3 survivors"""
        stdout = """Survived (3)
---- ./src/baz.py (3) ----
1, 5, 7
"""
        survivors, warnings = parse_mutmut_results(stdout)
        assert len(survivors) == 3
        assert survivors[0].mutant_id == 1
        assert survivors[1].mutant_id == 5
        assert survivors[2].mutant_id == 7

    def test_mixed_range_and_comma(self):
        """Test 5: mixed range+comma "1-3, 7" produces 4 survivors"""
        stdout = """Survived (4)
---- ./src/mixed.py (4) ----
1-3, 7
"""
        survivors, warnings = parse_mutmut_results(stdout)
        assert len(survivors) == 4
        assert survivors[0].mutant_id == 1
        assert survivors[1].mutant_id == 2
        assert survivors[2].mutant_id == 3
        assert survivors[3].mutant_id == 7

    def test_multiple_files_produce_correct_attribution(self):
        """Test 6: multiple files produce survivors with correct file attribution"""
        stdout = """Survived (4)
---- ./src/file1.py (2) ----
1, 2
---- ./src/file2.py (2) ----
3, 4
"""
        survivors, warnings = parse_mutmut_results(stdout)
        assert len(survivors) == 4
        assert survivors[0].file == "./src/file1.py"
        assert survivors[0].mutant_id == 1
        assert survivors[1].file == "./src/file1.py"
        assert survivors[1].mutant_id == 2
        assert survivors[2].file == "./src/file2.py"
        assert survivors[2].mutant_id == 3
        assert survivors[3].file == "./src/file2.py"
        assert survivors[3].mutant_id == 4

    def test_malformed_input_returns_empty_list(self):
        """Test 7: malformed input (no "----" lines) returns []"""
        stdout = """Survived (2)
some random text
1, 2
"""
        survivors, warnings = parse_mutmut_results(stdout)
        assert survivors == []


class TestRunMutation:
    """Test run_mutation with various scenarios."""

    def test_empty_diff_files_returns_empty_results(self):
        """Test 8: empty diff_files returns ([], [])"""
        findings, infra = run_mutation([], ["pytest"])
        assert findings == []
        assert infra == []

    def test_non_python_files_only_returns_mutation_skipped(self):
        """Test 9: non-.py files only returns MUTATION_SKIPPED"""
        findings, infra = run_mutation(["foo.js", "bar.c"], ["pytest"])
        assert len(findings) == 1
        assert findings[0].id == "MUTATION_SKIPPED"
        assert findings[0].source == "MUTANT"
        assert findings[0].disposition == Disposition.DISMISSED
        assert "unsupported" in findings[0].description or "Python-only" in findings[0].description
        assert len(infra) == 1
        assert "no Python files" in infra[0]

    @patch("forge.mutation.subprocess.run")
    def test_flaky_guard_baseline_fails_on_run_2(self, mock_run):
        """Test 10: flaky guard -- baseline fails on run 2 of 3"""
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="FAIL"),
        ]
        findings, infra = run_mutation(["test.py"], ["pytest"])
        assert len(findings) == 1
        assert findings[0].id == "MUTATION_SKIPPED"
        assert findings[0].source == "MUTANT"
        assert findings[0].disposition == Disposition.DISMISSED
        assert "flaky" in findings[0].description
        assert len(infra) == 1
        assert "flaky guard" in infra[0]

    @patch("forge.mutation.subprocess.run")
    @patch("forge.mutation.shutil.which", return_value=None)
    def test_mutmut_not_installed(self, mock_which, mock_run):
        """Test 11: mutmut not installed returns MUTATION_SKIPPED"""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        findings, infra = run_mutation(["test.py"], ["pytest"])
        assert len(findings) == 1
        assert findings[0].id == "MUTATION_SKIPPED"
        assert findings[0].source == "MUTANT"
        assert findings[0].disposition == Disposition.DISMISSED
        assert "not installed" in findings[0].description
        assert len(infra) == 0

    @patch("forge.mutation.subprocess.run")
    @patch("forge.mutation.shutil.which", return_value="/usr/bin/mutmut")
    def test_mutmut_timeout_returns_mutation_skipped(self, mock_which, mock_run):
        """Test 12: mutmut timeout returns MUTATION_SKIPPED"""
        def side_effect(*args, **kwargs):
            if "mutmut" in args[0] and "run" in args[0]:
                raise subprocess.TimeoutExpired(cmd=args[0], timeout=600)
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        findings, infra = run_mutation(["test.py"], ["pytest"], timeout=600)
        assert len(findings) == 1
        assert findings[0].id == "MUTATION_SKIPPED"
        assert findings[0].source == "MUTANT"
        assert findings[0].disposition == Disposition.DISMISSED
        assert "timed out" in findings[0].description

    @patch("forge.mutation.subprocess.run")
    @patch("forge.mutation.shutil.which", return_value="/usr/bin/mutmut")
    def test_successful_run_with_survivors(self, mock_which, mock_run):
        """Test 13: successful run with survivors returns CONFIRMED findings"""
        mutmut_results_stdout = """Survived (2)
---- ./test.py (2) ----
1, 2
"""
        def side_effect(*args, **kwargs):
            if "results" in args[0]:
                return subprocess.CompletedProcess(
                    args=[], returncode=0, stdout=mutmut_results_stdout, stderr=""
                )
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        findings, infra = run_mutation(["test.py"], ["pytest"])
        assert len(findings) == 2
        assert all(f.source == "MUTANT" for f in findings)
        assert all(f.disposition == Disposition.CONFIRMED for f in findings)
        assert findings[0].file == "./test.py"
        assert findings[0].description == "mutant 1 survived in ./test.py"
        assert findings[1].description == "mutant 2 survived in ./test.py"

    @patch("forge.mutation.subprocess.run")
    @patch("forge.mutation.shutil.which", return_value="/usr/bin/mutmut")
    def test_successful_run_zero_survivors(self, mock_which, mock_run):
        """Test 14: successful run with zero survivors returns empty findings list"""
        def side_effect(*args, **kwargs):
            if "results" in args[0]:
                return subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="", stderr=""
                )
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        findings, infra = run_mutation(["test.py"], ["pytest"])
        assert len(findings) == 0

    def test_all_mutation_skipped_findings_have_correct_attributes(self):
        """Test 15: all MUTATION_SKIPPED findings have source=MUTANT and disposition=DISMISSED"""
        test_cases = [
            (["test.js"], ["pytest"], "non-Python"),
        ]

        for diff_files, baseline_cmd, scenario in test_cases:
            findings, _ = run_mutation(diff_files, baseline_cmd)
            skipped_findings = [f for f in findings if f.id == "MUTATION_SKIPPED"]
            for f in skipped_findings:
                assert f.source == "MUTANT", "scenario: %s" % scenario
                assert f.disposition == Disposition.DISMISSED, "scenario: %s" % scenario


class TestMutationRealCLI:
    """Real mutmut CLI smoke tests (skipped if mutmut not installed)."""

    def test_real_mutmut_runs_without_usage_error(self, tmp_path):
        """Test that real mutmut CLI accepts our invocation."""
        import shutil
        import pytest

        if shutil.which("mutmut") is None:
            pytest.skip("mutmut not installed")

        # Invoke run_mutation in isolated directory
        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            # Create src/ structure (PYTHONPATH=src requirement)
            src_dir = tmp_path / "src"
            src_dir.mkdir()

            target = src_dir / "target.py"
            target.write_text(
                "def add(a, b):\n"
                "    return a + b\n"
            )

            test_file = tmp_path / "test_target.py"
            test_file.write_text(
                "from target import add\n"
                "def test_add():\n"
                "    assert add(1, 2) == 3\n"
            )

            findings, infra_errors = run_mutation(
                diff_files=["src/target.py"],
                baseline_cmd=["python3", "-m", "pytest", "-x", "test_target.py"],
                timeout=60,
            )

            # The primary assertion: no MUTATION_ERROR finding (exit 2 usage error).
            # If mutmut's invocation is correct, it will either:
            # 1. Produce survivors (CONFIRMED findings)
            # 2. Skip due to pytest incompatibility (MUTATION_SKIPPED)
            # 3. Have no survivors (empty findings list, which is acceptable)
            #
            # The EC-7 bug was a usage error (exit 2) due to --paths-to-mutate not existing.
            # This test verifies that bug is fixed.
            error_findings = [f for f in findings if f.id == "MUTATION_ERROR"]
            assert error_findings == [], (
                "mutmut invocation failed with usage error (bug NOT fixed): %s"
                % error_findings
            )

        finally:
            os.chdir(original_cwd)
