# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for the gate-check subcommand."""
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, mock_open, patch

import pytest
import yaml

from forge.exit_codes import EXIT_FAIL, EXIT_PASS
from forge.gate_check import (
    compute_baseline_delta,
    is_ci_mode,
    load_gate_config,
    match_source_patterns,
    run_gate_check,
    translate_exit_code,
    validate_command_safety,
)


# --- Parse + Translate + FAIL-OPEN ---

class TestLoadGateConfig:
    def test_valid_config(self):
        """Loads gate.yaml and returns dict."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
  env:
    PYTHONPATH: "src"
  timeout_seconds: 120
  cwd: "."
  source_patterns: ["*.py"]
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert config["test"]["command"] == ["python3", "-m", "pytest"]
        assert config["test"]["env"]["PYTHONPATH"] == "src"

    def test_missing_file_raises(self):
        """FileNotFoundError when file absent."""
        def raise_fnf(*args, **kwargs):
            raise FileNotFoundError("gate.yaml not found")

        with pytest.raises(FileNotFoundError):
            load_gate_config("gate.yaml", fs_open=raise_fnf)

    def test_invalid_yaml_raises(self):
        """ValueError on malformed YAML."""
        m = mock_open(read_data="{ invalid yaml")
        with pytest.raises(ValueError, match="Invalid YAML"):
            load_gate_config("gate.yaml", fs_open=m)

    def test_missing_command_raises(self):
        """ValueError when test.command missing."""
        yaml_content = "test:\n  env: {}\n"
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError, match="command.*required"):
            load_gate_config("gate.yaml", fs_open=m)


class TestValidateCommandSafety:
    def test_known_runner_accepted(self):
        """Known runners (python3, cargo, go) accepted."""
        validate_command_safety(["python3", "-m", "pytest"])
        validate_command_safety(["cargo", "test"])
        validate_command_safety(["go", "test", "./..."])
        # Should not raise

    def test_unknown_runner_rejected(self):
        """Unknown runners (rm, bash) rejected."""
        with pytest.raises(ValueError, match="Unknown test runner"):
            validate_command_safety(["rm", "-rf", "/"])
        with pytest.raises(ValueError, match="Unknown test runner"):
            validate_command_safety(["bash", "-c", "echo"])

    def test_metachar_rejected(self):
        """Shell metacharacters (|, ;, &) rejected."""
        with pytest.raises(ValueError, match="metacharacter"):
            validate_command_safety(["python3", "-c", "import os; os.system('ls')"])
        with pytest.raises(ValueError, match="metacharacter"):
            validate_command_safety(["pytest", "tests/", "|", "grep", "PASS"])


class TestTranslateExitCode:
    def test_exit_0_allow(self):
        """Exit 0 -> 0 (allow)."""
        assert translate_exit_code(0) == 0

    def test_exit_1_block(self):
        """Exit 1 -> 1 (BLOCK - real failure)."""
        assert translate_exit_code(1) == 1

    def test_exit_2_warn(self):
        """Exit 2 -> 0 (warn, keyboard interrupt)."""
        assert translate_exit_code(2) == 0

    def test_exit_3_warn(self):
        """Exit 3 -> 0 (warn, internal error)."""
        assert translate_exit_code(3) == 0

    def test_exit_4_block(self):
        """Exit 4 -> 1 (BLOCK - usage error)."""
        assert translate_exit_code(4) == 1

    def test_exit_5_block(self):
        """Exit 5 -> 1 (BLOCK - no tests collected)."""
        assert translate_exit_code(5) == 1

    def test_exit_99_block(self):
        """Exit 99 (unknown) -> 1 (BLOCK)."""
        assert translate_exit_code(99) == 1

    def test_timeout_block(self):
        """Timeout (represented as high exit code) -> 1 (BLOCK)."""
        assert translate_exit_code(124) == 1  # typical timeout exit


class TestFailOpenGuard:
    """FAIL-OPEN guard: config errors -> BLOCK (exit 1), never allow."""

    def test_missing_gate_yaml_blocks(self):
        """run_gate_check returns 1 (BLOCK) when gate.yaml missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            (cwd / ".forge").mkdir()
            # gate.yaml absent

            from io import StringIO
            stderr = StringIO()
            result = run_gate_check(
                args=None, env={}, cwd=cwd,
                stdout=StringIO(), stderr=stderr
            )
            assert result == EXIT_FAIL
            assert "error" in stderr.getvalue().lower()

    def test_invalid_yaml_blocks(self):
        """run_gate_check returns 1 (BLOCK) on invalid YAML."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".forge"
            forge_dir.mkdir()
            (forge_dir / "gate.yaml").write_text("{ invalid")

            from io import StringIO
            stderr = StringIO()
            result = run_gate_check(
                args=None, env={}, cwd=cwd,
                stdout=StringIO(), stderr=stderr
            )
            assert result == EXIT_FAIL

    def test_unsafe_command_blocks(self):
        """run_gate_check returns 1 (BLOCK) on unsafe command."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".forge"
            forge_dir.mkdir()

            config = {
                "test": {
                    "command": ["rm", "-rf", "/"],
                }
            }
            (forge_dir / "gate.yaml").write_text(yaml.dump(config))

            from io import StringIO
            stderr = StringIO()
            result = run_gate_check(
                args=None, env={}, cwd=cwd,
                stdout=StringIO(), stderr=stderr
            )
            assert result == EXIT_FAIL

    def test_never_returns_exit_2(self):
        """Assert return != 2 for all error paths."""
        # This is a meta-test: run_gate_check MUST never return 2
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            (cwd / ".forge").mkdir()

            from io import StringIO
            result = run_gate_check(
                args=None, env={}, cwd=cwd,
                stdout=StringIO(), stderr=StringIO()
            )
            assert result != 2  # EXIT_CLI_ERROR
            assert result in (0, 1)  # Only PASS or FAIL


# --- CI Detection ---

class TestCIDetection:
    def test_forge_mode_ci(self):
        """FORGE_MODE=ci -> is_ci_mode True."""
        assert is_ci_mode({"FORGE_MODE": "ci"}) is True

    def test_forge_mode_ci_case_insensitive(self):
        """FORGE_MODE=CI -> True (case-insensitive)."""
        assert is_ci_mode({"FORGE_MODE": "CI"}) is True
        assert is_ci_mode({"FORGE_MODE": "Ci"}) is True

    def test_github_actions(self):
        """GITHUB_ACTIONS=true -> True."""
        assert is_ci_mode({"GITHUB_ACTIONS": "true"}) is True

    def test_gitlab_ci(self):
        """GITLAB_CI=true -> True."""
        assert is_ci_mode({"GITLAB_CI": "true"}) is True

    def test_jenkins_url(self):
        """JENKINS_URL=http://... -> True."""
        assert is_ci_mode({"JENKINS_URL": "http://jenkins"}) is True

    def test_build_url(self):
        """BUILD_URL=http://... -> True."""
        assert is_ci_mode({"BUILD_URL": "http://build"}) is True

    def test_ci_var(self):
        """CI=1 -> True."""
        assert is_ci_mode({"CI": "1"}) is True

    def test_no_ci_vars(self):
        """Empty env -> False."""
        assert is_ci_mode({}) is False

    def test_skip_tests_ignored_in_ci(self):
        """FORGE_SKIP_TESTS=1 + CI=1 -> tests still run."""
        # This is tested in run_gate_check integration tests
        # We verify is_ci_mode returns True so the skip logic is bypassed
        assert is_ci_mode({"CI": "1", "FORGE_SKIP_TESTS": "1"}) is True


# --- Baseline Delta ---

class TestBaselineDelta:
    def test_no_baseline_allows(self):
        """None baseline -> (False, []) -- allow (bootstrap)."""
        test_output = "FAILED tests/test_foo.py::test_bar\n"
        should_block, failures = compute_baseline_delta(test_output, None)
        assert should_block is False
        assert failures == []

    def test_known_failure_not_new(self):
        """Failure in baseline -> not new -> allow."""
        baseline = {
            "test_results": {
                "tests/test_foo.py::test_bar": "failed"
            }
        }
        test_output = "FAILED tests/test_foo.py::test_bar\n"
        should_block, failures = compute_baseline_delta(test_output, baseline)
        assert should_block is False

    def test_new_failure_blocks(self):
        """Failure not in baseline -> NEW -> BLOCK."""
        baseline = {
            "test_results": {}
        }
        test_output = "FAILED tests/test_foo.py::test_new\n"
        should_block, failures = compute_baseline_delta(test_output, baseline)
        assert should_block is True
        assert "tests/test_foo.py::test_new" in failures

    def test_new_test_passes_ok(self):
        """Test not in baseline, passes -> not a failure -> allow."""
        baseline = {
            "test_results": {}
        }
        test_output = "PASSED tests/test_foo.py::test_new\n"
        should_block, failures = compute_baseline_delta(test_output, baseline)
        assert should_block is False

    def test_previously_passing_now_fails(self):
        """Regression: was passing, now fails -> NEW -> BLOCK."""
        baseline = {
            "test_results": {
                "tests/test_foo.py::test_bar": "passed"
            }
        }
        test_output = "FAILED tests/test_foo.py::test_bar\n"
        should_block, failures = compute_baseline_delta(test_output, baseline)
        assert should_block is True
        assert "tests/test_foo.py::test_bar" in failures


# --- Source Pattern Matching ---

class TestSourcePatterns:
    def test_py_file_matches(self):
        """"foo.py" matches ["*.py"]."""
        assert match_source_patterns(["foo.py"], ["*.py"]) is True

    def test_md_file_no_match(self):
        """"README.md" does not match ["*.py"]."""
        assert match_source_patterns(["README.md"], ["*.py"]) is False

    def test_empty_patterns_matches_all(self):
        """[] patterns -> True (always run tests)."""
        assert match_source_patterns(["foo.py"], []) is True
        assert match_source_patterns(["README.md"], []) is True

    def test_no_staged_files_skips(self):
        """Empty file list -> False (skip tests)."""
        assert match_source_patterns([], ["*.py"]) is False
        assert match_source_patterns([], []) is False


# Integration Tests

class TestGateCheckIntegration:
    """End-to-end tests for run_gate_check."""

    def test_skip_tests_in_local_mode(self):
        """FORGE_SKIP_TESTS=1 in local mode -> allow + warning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".forge"
            forge_dir.mkdir()

            config = {
                "test": {
                    "command": ["python3", "-m", "pytest"],
                }
            }
            (forge_dir / "gate.yaml").write_text(yaml.dump(config))

            from io import StringIO
            stderr = StringIO()
            result = run_gate_check(
                args=None,
                env={"FORGE_SKIP_TESTS": "1"},  # No CI vars
                cwd=cwd,
                stdout=StringIO(),
                stderr=stderr
            )
            assert result == EXIT_PASS
            assert "FORGE_SKIP_TESTS" in stderr.getvalue()

    @patch("forge.gate_check.subprocess.run")
    def test_test_pass_returns_pass(self, mock_run):
        """Test exit 0 -> gate-check returns PASS."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".forge"
            forge_dir.mkdir()

            config = {
                "test": {
                    "command": ["python3", "-m", "pytest"],
                }
            }
            (forge_dir / "gate.yaml").write_text(yaml.dump(config))

            # Mock git diff --cached
            def side_effect(*args, **kwargs):
                if args[0][0] == "git":
                    return Mock(returncode=0, stdout="foo.py\n", stderr="")
                # Test command
                return Mock(returncode=0, stdout="", stderr="")

            mock_run.side_effect = side_effect

            from io import StringIO
            result = run_gate_check(
                args=None, env={}, cwd=cwd,
                stdout=StringIO(), stderr=StringIO()
            )
            assert result == EXIT_PASS

    @patch("forge.gate_check.subprocess.run")
    def test_test_fail_new_failure_returns_fail(self, mock_run):
        """Test exit 1 + NEW failure -> gate-check returns FAIL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".forge"
            forge_dir.mkdir()

            config = {
                "test": {
                    "command": ["python3", "-m", "pytest"],
                }
            }
            (forge_dir / "gate.yaml").write_text(yaml.dump(config))

            # Empty baseline (all failures are new)
            baseline = {
                "schema_version": "1.0",
                "test_results": {}
            }
            (forge_dir / "test_baseline.json").write_text(json.dumps(baseline))

            # Mock subprocess
            def side_effect(*args, **kwargs):
                if args[0][0] == "git":
                    return Mock(returncode=0, stdout="foo.py\n", stderr="")
                # Test command fails
                return Mock(
                    returncode=1,
                    stdout="FAILED tests/test_foo.py::test_bar\n",
                    stderr=""
                )

            mock_run.side_effect = side_effect

            from io import StringIO
            stderr = StringIO()
            result = run_gate_check(
                args=None, env={}, cwd=cwd,
                stdout=StringIO(), stderr=stderr
            )
            assert result == EXIT_FAIL
            assert "NEW test failures" in stderr.getvalue()


# --- Bug-inject tests ---

class TestBugInjectExitTranslation:
    """Break exit-code translation, verify tests catch it."""

    def test_all_block_codes_actually_block(self):
        """Every exit code that should BLOCK returns 1."""
        from forge.gate_check import translate_exit_code

        for code in [1, 4, 5, 99]:
            assert translate_exit_code(code) == 1, (
                "exit %d should BLOCK (1)" % code
            )


class TestBugInjectFailOpen:
    """Break FAIL-OPEN guard, verify tests catch it."""

    def test_config_error_must_block(self):
        """If config error returns 0, the gate fails open."""
        from io import StringIO

        with tempfile.TemporaryDirectory() as cwd:
            cwd = Path(cwd)
            (cwd / ".forge").mkdir()
            (cwd / ".forge" / "gate.yaml").write_text("{{invalid yaml")

            stderr = StringIO()
            result = run_gate_check(
                args=None, env={}, cwd=cwd,
                stdout=StringIO(), stderr=stderr
            )
            assert result == EXIT_FAIL, (
                "config parse error must BLOCK (1), got %d" % result
            )

    def test_missing_config_must_block(self):
        """If gate.yaml is missing, the gate must block."""
        from io import StringIO

        with tempfile.TemporaryDirectory() as cwd:
            cwd = Path(cwd)

            stderr = StringIO()
            result = run_gate_check(
                args=None, env={}, cwd=cwd,
                stdout=StringIO(), stderr=stderr
            )
            assert result == EXIT_FAIL, (
                "missing gate.yaml must BLOCK (1), got %d" % result
            )

    def test_unsafe_command_must_block(self):
        """If test.command has shell metacharacters, gate blocks."""
        from io import StringIO

        with tempfile.TemporaryDirectory() as cwd:
            cwd = Path(cwd)
            (cwd / ".forge").mkdir()
            (cwd / ".forge" / "gate.yaml").write_text(
                "---\ntest:\n"
                "  command: ['sh', '-c', 'rm -rf /']\n"
                "  timeout_seconds: 10\n"
                "  cwd: '.'\n"
            )

            stderr = StringIO()
            result = run_gate_check(
                args=None, env={}, cwd=cwd,
                stdout=StringIO(), stderr=stderr
            )
            assert result == EXIT_FAIL, (
                "unsafe command must BLOCK (1), got %d" % result
            )
