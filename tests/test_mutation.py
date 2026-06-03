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

from code_forge.disposition import Disposition
from code_forge.mutation import parse_mutmut_results, run_mutation


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
            "    code_forge.mutation.x_run__mutmut_1: survived\n"
            "    code_forge.mutation.x_run__mutmut_2: killed\n"
            "    code_forge.mutation.x_run__mutmut_3: survived\n"
        )
        survivors, warnings = parse_mutmut_results(stdout)
        assert len(survivors) == 2
        assert survivors[0].mutant_name == "code_forge.mutation.x_run__mutmut_1"
        assert survivors[1].mutant_name == "code_forge.mutation.x_run__mutmut_3"

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

    @patch("code_forge.mutation.subprocess.run")
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

    @patch("code_forge.mutation.subprocess.run")
    @patch("code_forge.mutation.shutil.which", return_value=None)
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

    @patch("code_forge.mutation.subprocess.run")
    @patch("code_forge.mutation.shutil.which", return_value="/usr/bin/mutmut")
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

    @patch("code_forge.mutation.subprocess.run")
    @patch("code_forge.mutation.shutil.which", return_value="/usr/bin/mutmut")
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

    @patch("code_forge.mutation.subprocess.run")
    @patch("code_forge.mutation.shutil.which", return_value="/usr/bin/mutmut")
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

    @patch("code_forge.mutation.subprocess.run")
    @patch("code_forge.mutation.shutil.which", return_value="/usr/bin/mutmut")
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


class TestVenvFallback:
    """Tests for conditional VIRTUAL_ENV fallback in run_mutation.

    When the test runner is unavailable in the inherited env (e.g.
    "No module named pytest" under a uv-managed venv), run_mutation
    should strip VIRTUAL_ENV and retry. But when the inherited env
    works, or when the failure is a genuine test failure (not a
    runner-missing error), no stripping should occur.
    """

    @patch("code_forge.mutation.subprocess.run")
    @patch.dict("os.environ", {
        "VIRTUAL_ENV": "/fake/venv",
        "PATH": "/fake/venv/bin:/usr/bin:/bin",
    }, clear=True)
    def test_inherited_baseline_passes_no_strip(self, mock_run):
        """T-A: When inherited baseline passes, VIRTUAL_ENV stays intact.

        Regression guard: a normal user whose venv has the runner and
        all deps must not have VIRTUAL_ENV stripped.
        """
        envs_seen = []

        def side_effect(*args, **kwargs):
            env = kwargs.get("env", {})
            envs_seen.append(dict(env))
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )

        mock_run.side_effect = side_effect
        run_mutation(["test.py"], ["python3", "-m", "pytest"])

        # All 3 baseline calls must have VIRTUAL_ENV present
        baseline_envs = envs_seen[:3]
        assert len(baseline_envs) == 3
        for i, env in enumerate(baseline_envs):
            assert "VIRTUAL_ENV" in env, (
                "baseline run %d lost VIRTUAL_ENV" % (i + 1)
            )
            assert env["VIRTUAL_ENV"] == "/fake/venv"

    @patch("code_forge.mutation.shutil.which", return_value="/usr/bin/mutmut")
    @patch("code_forge.mutation.subprocess.run")
    @patch.dict("os.environ", {
        "VIRTUAL_ENV": "/fake/venv",
        "PATH": "/fake/venv/bin:/usr/bin:/bin",
    }, clear=True)
    def test_runner_missing_triggers_strip_retry(self, mock_run, mock_which):
        """T-B: Runner-missing error triggers strip-retry that succeeds.

        First baseline attempt fails with "No module named 'pytest'"
        (the runner module is missing in the inherited env). The
        fallback strips VIRTUAL_ENV and retries; the retry succeeds.
        Mutation should proceed (not MUTATION_SKIPPED).
        """
        call_count = {"n": 0}
        envs_seen = []

        def side_effect(*args, **kwargs):
            call_count["n"] += 1
            env = kwargs.get("env", {})
            envs_seen.append(dict(env))
            cmd = args[0]

            # First baseline call: runner missing in inherited env
            if call_count["n"] == 1:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=1,
                    stdout="",
                    stderr="No module named 'pytest'",
                )
            # After strip: all calls succeed (baseline retries + mutmut)
            if isinstance(cmd, list) and "mutmut" in cmd:
                if "results" in cmd:
                    return subprocess.CompletedProcess(
                        args=cmd, returncode=0,
                        stdout="    mod.fn__mutmut_1: survived\n",
                        stderr="",
                    )
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout="", stderr=""
                )
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )

        mock_run.side_effect = side_effect
        findings, infra = run_mutation(["test.py"], ["python3", "-m", "pytest"])

        # The first call had VIRTUAL_ENV; after strip, it should be gone
        assert "VIRTUAL_ENV" in envs_seen[0]
        # Find the retry calls (after the strip)
        retry_envs = [e for e in envs_seen[1:] if "VIRTUAL_ENV" not in e]
        assert len(retry_envs) > 0, (
            "no retry with VIRTUAL_ENV stripped was attempted"
        )

        # Must NOT be MUTATION_SKIPPED -- mutation should have proceeded
        skipped = [f for f in findings if f.id == "MUTATION_SKIPPED"]
        assert skipped == [], (
            "mutation was skipped despite successful retry: %s" % skipped
        )

    @patch("code_forge.mutation.subprocess.run")
    @patch.dict("os.environ", {
        "VIRTUAL_ENV": "/fake/venv",
        "PATH": "/fake/venv/bin:/usr/bin:/bin",
    }, clear=True)
    def test_genuine_failure_no_strip_retry(self, mock_run):
        """T-C: Genuine test failure does NOT trigger strip-retry.

        Baseline fails with returncode=1 but the output does NOT
        contain a runner-missing signature. This is a real test
        failure, so no fallback should occur and MUTATION_SKIPPED
        should be returned.
        """
        envs_seen = []

        def side_effect(*args, **kwargs):
            env = kwargs.get("env", {})
            envs_seen.append(dict(env))
            return subprocess.CompletedProcess(
                args=[], returncode=1,
                stdout="FAILED tests/test_foo.py::test_bar - AssertionError",
                stderr="",
            )

        mock_run.side_effect = side_effect
        findings, infra = run_mutation(["test.py"], ["python3", "-m", "pytest"])

        # Only 1 call should have been made (no retry)
        assert len(envs_seen) == 1, (
            "expected 1 subprocess call (no retry), got %d" % len(envs_seen)
        )
        # All calls should have VIRTUAL_ENV (no stripping)
        for env in envs_seen:
            assert "VIRTUAL_ENV" in env, "VIRTUAL_ENV was stripped on genuine failure"

        # Must return MUTATION_SKIPPED
        skipped = [f for f in findings if f.id == "MUTATION_SKIPPED"]
        assert len(skipped) == 1
        assert "flaky" in skipped[0].description or "baseline" in infra[0]

    @patch("code_forge.mutation.subprocess.run")
    @patch.dict("os.environ", {
        "VIRTUAL_ENV": "/fake/venv",
        "PATH": "/fake/venv/bin:/usr/bin:/bin",
    }, clear=True)
    def test_project_dep_missing_no_strip_retry(self, mock_run):
        """T-C2: Missing PROJECT dep (not runner) does NOT trigger strip.

        Baseline fails with "No module named 'venvonly_marker'" -- this
        is a project dependency, not the runner. No strip-retry should
        occur, because stripping the venv would make it worse.
        """
        envs_seen = []

        def side_effect(*args, **kwargs):
            env = kwargs.get("env", {})
            envs_seen.append(dict(env))
            return subprocess.CompletedProcess(
                args=[], returncode=1,
                stdout="",
                stderr="No module named 'venvonly_marker'",
            )

        mock_run.side_effect = side_effect
        findings, infra = run_mutation(
            ["test.py"], ["python3", "-m", "pytest"]
        )

        # Only 1 call -- no retry
        assert len(envs_seen) == 1, (
            "strip-retry triggered on project dep missing (should not)"
        )
        assert "VIRTUAL_ENV" in envs_seen[0]

        skipped = [f for f in findings if f.id == "MUTATION_SKIPPED"]
        assert len(skipped) == 1

    @patch("code_forge.mutation.subprocess.run")
    @patch.dict("os.environ", {
        "VIRTUAL_ENV": "/fake/venv",
        "PATH": "/fake/venv/bin:/usr/bin:/bin",
    }, clear=True)
    def test_bare_binary_not_found_triggers_strip(self, mock_run):
        """T-B2: Bare binary not on PATH triggers strip-retry.

        When baseline_cmd is ["pytest", ...] (bare binary, not
        python3 -m), and it raises FileNotFoundError, strip-retry
        should activate.
        """
        call_count = {"n": 0}
        envs_seen = []

        def side_effect(*args, **kwargs):
            call_count["n"] += 1
            env = kwargs.get("env", {})
            envs_seen.append(dict(env))

            if call_count["n"] == 1:
                raise FileNotFoundError("pytest")
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )

        mock_run.side_effect = side_effect
        # Use bare binary form -- mutmut not installed so it will skip
        # after the guard passes, which is fine for this test
        findings, infra = run_mutation(["test.py"], ["pytest"])

        # First call had VIRTUAL_ENV; retry should not
        assert "VIRTUAL_ENV" in envs_seen[0]
        retry_envs = [e for e in envs_seen[1:] if "VIRTUAL_ENV" not in e]
        assert len(retry_envs) > 0, "no strip-retry on FileNotFoundError"

    @patch("code_forge.mutation.shutil.which", return_value="/usr/bin/mutmut")
    @patch("code_forge.mutation.subprocess.run")
    @patch.dict("os.environ", {
        "VIRTUAL_ENV": "/fake/venv",
        "PATH": "/fake/venv/bin:/usr/bin:/bin",
    }, clear=True)
    def test_python_flags_before_m_still_detected(self, mock_run, _which):
        """Flags between python and -m (e.g. -W ignore) do not break
        runner-missing detection."""
        call_count = {"n": 0}

        def side_effect(*args, **kwargs):
            call_count["n"] += 1
            cmd = args[0]
            if call_count["n"] == 1:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=1,
                    stdout="",
                    stderr="No module named 'pytest'",
                )
            if isinstance(cmd, list) and "mutmut" in cmd:
                if "results" in cmd:
                    return subprocess.CompletedProcess(
                        args=cmd, returncode=0,
                        stdout="    mod.fn__mutmut_1: survived\n",
                        stderr="",
                    )
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout="", stderr="",
                )
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr="",
            )

        mock_run.side_effect = side_effect
        findings, _ = run_mutation(
            ["test.py"], ["python3", "-W", "ignore", "-m", "pytest"],
        )
        skipped = [f for f in findings
                    if f.id == "MUTATION_SKIPPED"
                    and f.fingerprint == "mutation-flaky"]
        assert skipped == [], (
            "mutation skipped despite successful retry with flags: %s"
            % skipped
        )

    @patch("code_forge.mutation.subprocess.run")
    @patch.dict("os.environ", {
        "VIRTUAL_ENV": "/fake/venv",
        "PATH": "/fake/venv/bin:/usr/bin:/bin",
    }, clear=True)
    def test_pytest_marker_flag_not_confused_with_python_m(self, mock_run):
        """pytest -m slow must NOT trigger runner-missing detection."""
        envs_seen = []

        def side_effect(*args, **kwargs):
            envs_seen.append(dict(kwargs.get("env", {})))
            return subprocess.CompletedProcess(
                args=[], returncode=1,
                stdout="FAILED test_foo.py -m slow",
                stderr="",
            )

        mock_run.side_effect = side_effect
        findings, _ = run_mutation(["test.py"], ["pytest", "-m", "slow"])
        assert len(envs_seen) == 1, (
            "strip-retry triggered for pytest -m flag (should not)"
        )
        assert "VIRTUAL_ENV" in envs_seen[0]
        skipped = [f for f in findings if f.id == "MUTATION_SKIPPED"]
        assert len(skipped) == 1


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
