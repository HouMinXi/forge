# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Unit tests for mutation.py module.

Covers parse_mutmut_results and run_mutation functions with all scenarios:
empty input, single survivor, no survivors, multi-mutant, non-survivor
status lines (parser tests); empty files, non-Python, flaky guard,
mutmut missing, timeout, success with/without survivors (run_mutation
tests); real CLI smoke tests using cwd parameter (no os.chdir).
"""

import shutil
import subprocess
from unittest.mock import patch

import pytest

from forge.disposition import Disposition
from forge.mutation import parse_mutmut_results, run_mutation


class TestParseMutmutResults:
    """Test parse_mutmut_results with mutmut 3.x output format.

    Format: "    module.fn__mutmut_N: status"
    Only "survived" lines produce Survivor objects.
    """

    def test_empty_string_returns_empty_list(self):
        """Test 1: empty string returns []"""
        survivors, warnings = parse_mutmut_results("")
        assert survivors == []
        assert warnings == []

    def test_single_survivor_returned(self):
        """Test 2: single survived line produces one Survivor"""
        stdout = "    add.x_add__mutmut_1: survived\n"
        survivors, warnings = parse_mutmut_results(stdout)
        assert len(survivors) == 1
        assert survivors[0].mutant_name == "add.x_add__mutmut_1"

    def test_killed_line_not_returned(self):
        """Test 3: killed status produces no Survivor"""
        stdout = "    add.x_add__mutmut_1: killed\n"
        survivors, warnings = parse_mutmut_results(stdout)
        assert survivors == []

    def test_multiple_survived_returned(self):
        """Test 4: multiple survived lines each produce a Survivor"""
        stdout = (
            "    forge.mutation.x_run__mutmut_1: survived\n"
            "    forge.mutation.x_run__mutmut_2: killed\n"
            "    forge.mutation.x_run__mutmut_3: survived\n"
        )
        survivors, warnings = parse_mutmut_results(stdout)
        assert len(survivors) == 2
        assert survivors[0].mutant_name == "forge.mutation.x_run__mutmut_1"
        assert survivors[1].mutant_name == "forge.mutation.x_run__mutmut_3"

    def test_non_survived_statuses_skipped(self):
        """Test 5: all non-survived statuses produce no Survivor"""
        stdout = (
            "    m.f__mutmut_1: killed\n"
            "    m.f__mutmut_2: no tests\n"
            "    m.f__mutmut_3: not checked\n"
            "    m.f__mutmut_4: timeout\n"
            "    m.f__mutmut_5: suspicious\n"
            "    m.f__mutmut_6: check was interrupted by user\n"
        )
        survivors, warnings = parse_mutmut_results(stdout)
        assert survivors == []

    def test_blank_lines_ignored(self):
        """Test 6: blank lines are skipped without error"""
        stdout = "\n    add.x_add__mutmut_1: survived\n\n"
        survivors, warnings = parse_mutmut_results(stdout)
        assert len(survivors) == 1

    def test_lines_without_colon_space_ignored(self):
        """Test 7: lines lacking ': ' separator are ignored"""
        stdout = "Mutant results\nadd.x_add__mutmut_1 survived\n"
        survivors, warnings = parse_mutmut_results(stdout)
        assert survivors == []

    def test_survivor_file_field_is_empty(self):
        """Test 8: mutmut 3.x results omit file paths; file is empty str"""
        stdout = "    mod.fn__mutmut_1: survived\n"
        survivors, warnings = parse_mutmut_results(stdout)
        assert survivors[0].file == ""


class TestRunMutation:
    """Test run_mutation with various scenarios."""

    def test_empty_diff_files_returns_empty_results(self):
        """Test 9: empty diff_files returns ([], [])"""
        findings, infra = run_mutation([], ["pytest"])
        assert findings == []
        assert infra == []

    def test_non_python_files_only_returns_mutation_skipped(self):
        """Test 10: non-.py files only returns MUTATION_SKIPPED"""
        findings, infra = run_mutation(["foo.js", "bar.c"], ["pytest"])
        assert len(findings) == 1
        assert findings[0].id == "MUTATION_SKIPPED"
        assert findings[0].source == "MUTANT"
        assert findings[0].disposition == Disposition.DISMISSED
        assert "Python-only" in findings[0].description
        assert len(infra) == 1
        assert "no Python files" in infra[0]

    @patch("forge.mutation.subprocess.run")
    def test_flaky_guard_baseline_fails_on_run_2(self, mock_run):
        """Test 11: flaky guard -- baseline fails on run 2 of 3"""
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
        """Test 12: mutmut not installed returns MUTATION_SKIPPED"""
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
        """Test 13: mutmut timeout returns MUTATION_SKIPPED"""
        call_count = {"n": 0}

        def side_effect(*args, **kwargs):
            call_count["n"] += 1
            cmd = args[0]
            # baseline runs (calls 1-3) succeed; mutmut run times out
            if isinstance(cmd, list) and "mutmut" in cmd and "run" in cmd:
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=600)
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
        """Test 14: successful run with survivors returns CONFIRMED findings.

        Uses mutmut 3.x results format: module.fn__mutmut_N: status
        """
        # mutmut 3.x results: module.fn__mutmut_N: survived
        mutmut_results_stdout = (
            "    test.x_foo__mutmut_1: survived\n"
            "    test.x_foo__mutmut_2: survived\n"
        )

        def side_effect(*args, **kwargs):
            cmd = args[0]
            if isinstance(cmd, list) and "mutmut" in cmd and "results" in cmd:
                return subprocess.CompletedProcess(
                    args=[], returncode=0, stdout=mutmut_results_stdout, stderr=""
                )
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        findings, infra = run_mutation(["test.py"], ["pytest"])
        assert len(findings) == 2
        assert all(f.source == "MUTANT" for f in findings)
        assert all(f.disposition == Disposition.CONFIRMED for f in findings)
        assert findings[0].id == "mutant-test.x_foo__mutmut_1"
        assert findings[1].id == "mutant-test.x_foo__mutmut_2"
        assert "test.x_foo__mutmut_1" in findings[0].description
        assert "test.x_foo__mutmut_2" in findings[1].description

    @patch("forge.mutation.subprocess.run")
    @patch("forge.mutation.shutil.which", return_value="/usr/bin/mutmut")
    def test_successful_run_zero_survivors(self, mock_which, mock_run):
        """Test 15: successful run with zero survivors returns empty findings"""
        def side_effect(*args, **kwargs):
            cmd = args[0]
            if isinstance(cmd, list) and "mutmut" in cmd and "results" in cmd:
                return subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="", stderr=""
                )
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        findings, infra = run_mutation(["test.py"], ["pytest"])
        assert len(findings) == 0

    @patch("forge.mutation.subprocess.run")
    @patch("forge.mutation.shutil.which", return_value="/usr/bin/mutmut")
    def test_mutmut_non_zero_exit_returns_mutation_error(self, mock_which, mock_run):
        """Test 16: any non-zero exit from mutmut run produces MUTATION_ERROR.

        Regression: attempt 2 only caught exit==2. Any non-zero is an error.
        """
        def side_effect(*args, **kwargs):
            cmd = args[0]
            if isinstance(cmd, list) and "mutmut" in cmd and "run" in cmd:
                return subprocess.CompletedProcess(
                    args=[], returncode=1, stdout="", stderr="stats collection failed"
                )
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        findings, infra = run_mutation(["test.py"], ["pytest"])
        assert len(findings) == 1
        assert findings[0].id == "MUTATION_ERROR"
        assert findings[0].source == "MUTANT"
        assert findings[0].disposition == Disposition.CONFIRMED
        assert "exit 1" in findings[0].description
        assert len(infra) == 1
        assert "exit 1" in infra[0]

    def test_all_mutation_skipped_findings_have_correct_attributes(self):
        """Test 17: all MUTATION_SKIPPED findings have source=MUTANT and disposition=DISMISSED"""
        findings, _ = run_mutation(["test.js"], ["pytest"])
        skipped_findings = [f for f in findings if f.id == "MUTATION_SKIPPED"]
        for f in skipped_findings:
            assert f.source == "MUTANT"
            assert f.disposition == Disposition.DISMISSED


class TestMutationRealCLI:
    """Real mutmut CLI smoke tests (skipped if mutmut not installed).

    Uses cwd parameter to run_mutation -- no os.chdir().
    """

    @pytest.mark.skipif(
        shutil.which("mutmut") is None, reason="mutmut not installed"
    )
    def test_real_mutmut_produces_findings(self, tmp_path):
        """Test 18: real mutmut produces MUTANT survivors on a toothless test.

        Creates a minimal project under tmp_path with a weak test that
        does not check the return value. Expects at least one surviving
        mutant (positive assertion, not just "no error").

        This is the regression test for the silent false-pass from
        attempts 1 and 2 where mutmut silently ran against empty dirs.
        """
        # Build minimal project: src/add.py + weak test
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "add.py").write_text(
            "def add(a, b):\n"
            "    return a + b\n"
        )
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_add.py").write_text(
            "from add import add\n"
            "def test_add():\n"
            "    # Weak test: no return value assertion\n"
            "    result = add(1, 2)\n"
            "    assert isinstance(result, int)\n"
        )

        findings, infra_errors = run_mutation(
            diff_files=["src/add.py"],
            baseline_cmd=["python3", "-m", "pytest", "tests/"],
            cwd=tmp_path,
        )

        # Positive assertion: the weak test must NOT kill the mutant
        mutant_findings = [
            f for f in findings
            if f.source == "MUTANT" and f.disposition == Disposition.CONFIRMED
            and f.id != "MUTATION_ERROR"
        ]
        error_findings = [f for f in findings if f.id == "MUTATION_ERROR"]

        assert error_findings == [], (
            "mutmut invocation failed: %s\ninfra_errors: %s"
            % (error_findings, infra_errors)
        )
        assert len(mutant_findings) > 0, (
            "no real mutants survived -- "
            "either mutmut did not run or all were killed\n"
            "findings=%s\ninfra_errors=%s" % (findings, infra_errors)
        )

    @pytest.mark.skipif(
        shutil.which("mutmut") is None, reason="mutmut not installed"
    )
    def test_real_mutmut_runs_without_usage_error(self, tmp_path):
        """Test 19: real mutmut CLI accepts our invocation on a good test.

        Uses a strong test (checks return value). All mutants should be
        killed; zero MUTATION_ERROR findings.
        """
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "add.py").write_text(
            "def add(a, b):\n"
            "    return a + b\n"
        )
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_add.py").write_text(
            "from add import add\n"
            "def test_add():\n"
            "    assert add(1, 2) == 3\n"
        )

        findings, infra_errors = run_mutation(
            diff_files=["src/add.py"],
            baseline_cmd=["python3", "-m", "pytest", "tests/"],
            cwd=tmp_path,
        )

        error_findings = [f for f in findings if f.id == "MUTATION_ERROR"]
        assert error_findings == [], (
            "mutmut invocation failed with error (bug NOT fixed): %s\n"
            "infra_errors: %s" % (error_findings, infra_errors)
        )

    @pytest.mark.skipif(
        shutil.which("mutmut") is None, reason="mutmut not installed"
    )
    def test_cleanup_after_run(self, tmp_path):
        """Test 20: mutants/ dir and setup.cfg are cleaned up after run."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "add.py").write_text(
            "def add(a, b):\n"
            "    return a + b\n"
        )
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_add.py").write_text(
            "from add import add\n"
            "def test_add():\n"
            "    assert add(1, 2) == 3\n"
        )

        run_mutation(
            diff_files=["src/add.py"],
            baseline_cmd=["python3", "-m", "pytest", "tests/"],
            cwd=tmp_path,
        )

        # mutants/ and setup.cfg must be removed after run
        mutants_dir = tmp_path / "mutants"
        setup_cfg = tmp_path / "setup.cfg"

        assert not mutants_dir.exists(), (
            "mutants/ dir not cleaned up after run_mutation"
        )
        assert not setup_cfg.exists(), (
            "setup.cfg not cleaned up after run_mutation"
        )
