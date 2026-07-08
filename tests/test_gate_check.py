# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for the gate-check subcommand."""
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, mock_open, patch

import pytest
import yaml

from code_forge.exit_codes import EXIT_FAIL, EXIT_PASS
import subprocess

from code_forge.gate_check import (
    compute_baseline_delta,
    fnmatch_to_grep,
    is_ci_mode,
    load_gate_config,
    match_source_patterns,
    run_gate_check,
    translate_exit_code,
    validate_command_safety,
    validate_presubmit_command,
    validate_retry_config,
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


    def test_missing_test_section_error_contains_snippet(self):
        """Error message includes a pasteable YAML snippet."""
        yaml_content = "backends:\n  x:\n    type: cli\n"
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError, match="Add:") as exc_info:
            load_gate_config("gate.yaml", fs_open=m)
        msg = str(exc_info.value)
        assert "command:" in msg
        assert "timeout_seconds:" in msg

    def test_error_snippet_satisfies_load_gate_config(self):
        """The YAML snippet in the error actually parses and loads."""
        no_test = "backends:\n  x:\n    type: cli\n"
        m = mock_open(read_data=no_test)
        try:
            load_gate_config("gate.yaml", fs_open=m)
        except ValueError as e:
            msg = str(e)
            snippet = msg.split("Add:\n", 1)[1]
        full_yaml = no_test + snippet + "\n"
        m2 = mock_open(read_data=full_yaml)
        config = load_gate_config("gate.yaml", fs_open=m2)
        assert "test" in config
        assert config["test"]["command"] == ["pytest", "-q"]


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
            (cwd / ".code-forge").mkdir()
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
            forge_dir = cwd / ".code-forge"
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
            forge_dir = cwd / ".code-forge"
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
            (cwd / ".code-forge").mkdir()

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
            forge_dir = cwd / ".code-forge"
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

    def test_quiet_flag_suppresses_warnings(self):
        """args.quiet=True suppresses warning messages."""
        import types
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".code-forge"
            forge_dir.mkdir()

            config = {
                "test": {
                    "command": ["python3", "-m", "pytest"],
                }
            }
            (forge_dir / "gate.yaml").write_text(yaml.dump(config))

            args = types.SimpleNamespace(quiet=True)
            from io import StringIO
            stderr = StringIO()
            result = run_gate_check(
                args=args,
                env={"FORGE_SKIP_TESTS": "1"},  # No CI vars
                cwd=cwd,
                stdout=StringIO(),
                stderr=stderr,
            )
            assert result == EXIT_PASS
            # With quiet=True, the FORGE_SKIP_TESTS warning is suppressed
            assert stderr.getvalue() == ""

    @patch("code_forge.gate_check.subprocess.run")
    def test_skip_tests_ignored_in_ci_mode(self, mock_run):
        """FORGE_SKIP_TESTS=1 + CI=1 -> gate still runs (not skipped)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".code-forge"
            forge_dir.mkdir()

            config = {
                "test": {
                    "command": ["python3", "-m", "pytest"],
                }
            }
            (forge_dir / "gate.yaml").write_text(yaml.dump(config))

            def side_effect(*args, **kwargs):
                if args[0][0] == "git":
                    return Mock(returncode=0, stdout="foo.py\n", stderr="")
                return Mock(returncode=0, stdout="", stderr="")

            mock_run.side_effect = side_effect

            from io import StringIO
            stderr = StringIO()
            result = run_gate_check(
                args=None,
                env={"FORGE_SKIP_TESTS": "1", "CI": "1"},
                cwd=cwd,
                stdout=StringIO(),
                stderr=stderr,
            )
            # In CI mode, FORGE_SKIP_TESTS is ignored; test ran and passed
            assert result == EXIT_PASS
            assert "CI mode" in stderr.getvalue()

    @patch("code_forge.gate_check.subprocess.run")
    def test_test_pass_returns_pass(self, mock_run):
        """Test exit 0 -> gate-check returns PASS."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".code-forge"
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

    @patch("code_forge.gate_check.subprocess.run")
    def test_test_fail_new_failure_returns_fail(self, mock_run):
        """Test exit 1 + NEW failure -> gate-check returns FAIL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".code-forge"
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

    @patch("code_forge.gate_check.subprocess.run")
    def test_exit_1_no_baseline_blocks_by_default(self, mock_run):
        """Test exit 1 + no baseline -> gate BLOCKS (fail-closed)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".code-forge"
            forge_dir.mkdir()

            config = {
                "test": {
                    "command": ["python3", "-m", "pytest"],
                }
            }
            (forge_dir / "gate.yaml").write_text(yaml.dump(config))

            def side_effect(*args, **kwargs):
                if args[0][0] == "git":
                    return Mock(returncode=0, stdout="foo.py\n", stderr="")
                return Mock(
                    returncode=1,
                    stdout="FAILED tests/test_foo.py::test_bar\n",
                    stderr="",
                )

            mock_run.side_effect = side_effect

            from io import StringIO
            stderr = StringIO()
            result = run_gate_check(
                args=None, env={}, cwd=cwd,
                stdout=StringIO(), stderr=stderr,
            )
            assert result == EXIT_FAIL
            assert "no baseline established" in stderr.getvalue()

    @patch("code_forge.gate_check.subprocess.run")
    def test_exit_1_no_baseline_allows_with_opt_in(self, mock_run):
        """FORGE_ALLOW_NO_BASELINE=1 restores the old allow behavior."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".code-forge"
            forge_dir.mkdir()

            config = {
                "test": {
                    "command": ["python3", "-m", "pytest"],
                }
            }
            (forge_dir / "gate.yaml").write_text(yaml.dump(config))

            def side_effect(*args, **kwargs):
                if args[0][0] == "git":
                    return Mock(returncode=0, stdout="foo.py\n", stderr="")
                return Mock(
                    returncode=1,
                    stdout="FAILED tests/test_foo.py::test_bar\n",
                    stderr="",
                )

            mock_run.side_effect = side_effect

            from io import StringIO
            stderr = StringIO()
            result = run_gate_check(
                args=None,
                env={"FORGE_ALLOW_NO_BASELINE": "1"},
                cwd=cwd,
                stdout=StringIO(), stderr=stderr,
            )
            assert result == EXIT_PASS
            assert "no baseline" in stderr.getvalue()

    @patch("code_forge.gate_check.subprocess.run")
    def test_exit_4_blocks_regardless_of_baseline(self, mock_run):
        """Exit 4 (usage error) BLOCKs even with permissive baseline."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".code-forge"
            forge_dir.mkdir()

            config = {
                "test": {
                    "command": ["python3", "-m", "pytest"],
                }
            }
            (forge_dir / "gate.yaml").write_text(yaml.dump(config))

            # Permissive baseline: known failure listed as failed
            baseline = {
                "schema_version": "1.0",
                "test_results": {
                    "tests/test_foo.py::test_bar": "failed"
                }
            }
            (forge_dir / "test_baseline.json").write_text(
                json.dumps(baseline)
            )

            def side_effect(*args, **kwargs):
                if args[0][0] == "git":
                    return Mock(returncode=0, stdout="foo.py\n", stderr="")
                # Test runner exits with 4 (usage error)
                return Mock(returncode=4, stdout="", stderr="")

            mock_run.side_effect = side_effect

            from io import StringIO
            result = run_gate_check(
                args=None, env={}, cwd=cwd,
                stdout=StringIO(), stderr=StringIO()
            )
            assert result == EXIT_FAIL, (
                "exit 4 must BLOCK (1) regardless of baseline, got %d"
                % result
            )

    @patch("code_forge.gate_check.subprocess.run")
    def test_exit_5_blocks_regardless_of_baseline(self, mock_run):
        """Exit 5 (no tests collected) BLOCKs even with empty baseline."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".code-forge"
            forge_dir.mkdir()

            config = {
                "test": {
                    "command": ["python3", "-m", "pytest"],
                }
            }
            (forge_dir / "gate.yaml").write_text(yaml.dump(config))

            # Empty baseline: no known failures, so vacuous delta would PASS
            baseline = {
                "schema_version": "1.0",
                "test_results": {}
            }
            (forge_dir / "test_baseline.json").write_text(
                json.dumps(baseline)
            )

            def side_effect(*args, **kwargs):
                if args[0][0] == "git":
                    return Mock(returncode=0, stdout="foo.py\n", stderr="")
                # Test runner exits with 5 (no tests collected)
                return Mock(returncode=5, stdout="", stderr="")

            mock_run.side_effect = side_effect

            from io import StringIO
            result = run_gate_check(
                args=None, env={}, cwd=cwd,
                stdout=StringIO(), stderr=StringIO()
            )
            assert result == EXIT_FAIL, (
                "exit 5 must BLOCK (1) regardless of baseline, got %d"
                % result
            )


    @patch("code_forge.gate_check.subprocess.run")
    def test_git_not_found_blocks(self, mock_run):
        """git not on PATH -> gate BLOCKs with clean error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".code-forge"
            forge_dir.mkdir()

            config = {
                "test": {
                    "command": ["python3", "-m", "pytest"],
                }
            }
            (forge_dir / "gate.yaml").write_text(yaml.dump(config))

            def side_effect(*args, **kwargs):
                if args[0][0] == "git":
                    raise FileNotFoundError("git not found")
                return Mock(returncode=0, stdout="", stderr="")

            mock_run.side_effect = side_effect

            from io import StringIO
            stderr = StringIO()
            result = run_gate_check(
                args=None, env={}, cwd=cwd,
                stdout=StringIO(), stderr=stderr,
            )
            assert result == EXIT_FAIL
            assert "error" in stderr.getvalue().lower()

    @patch("code_forge.gate_check.subprocess.run")
    def test_runner_not_found_blocks(self, mock_run):
        """Test runner not on PATH -> gate BLOCKs with clean error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            forge_dir = cwd / ".code-forge"
            forge_dir.mkdir()

            config = {
                "test": {
                    "command": ["python3", "-m", "pytest"],
                }
            }
            (forge_dir / "gate.yaml").write_text(yaml.dump(config))

            def side_effect(*args, **kwargs):
                if args[0][0] == "git":
                    return Mock(returncode=0, stdout="foo.py\n", stderr="")
                # Test runner not found
                raise FileNotFoundError("python3 not found")

            mock_run.side_effect = side_effect

            from io import StringIO
            stderr = StringIO()
            result = run_gate_check(
                args=None, env={}, cwd=cwd,
                stdout=StringIO(), stderr=stderr,
            )
            assert result == EXIT_FAIL
            assert "error" in stderr.getvalue().lower()


# --- Bug-inject tests ---

class TestBugInjectExitTranslation:
    """Break exit-code translation, verify tests catch it."""

    def test_all_block_codes_actually_block(self):
        """Every exit code that should BLOCK returns 1."""
        from code_forge.gate_check import translate_exit_code

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
            (cwd / ".code-forge").mkdir()
            (cwd / ".code-forge" / "gate.yaml").write_text("{{invalid yaml")

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
            (cwd / ".code-forge").mkdir()
            (cwd / ".code-forge" / "gate.yaml").write_text(
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


# --- Presubmit Schema Validation ---


class TestPresubmitValidation:
    """Tests for presubmit section validation in load_gate_config
    and helper functions validate_presubmit_entry, validate_presubmit_command,
    and fnmatch_to_grep.
    """

    # -- load_gate_config: valid presubmit section --

    def test_valid_presubmit_list_loads_ok(self):
        """gate.yaml with valid presubmit list loads without error."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - command: ["go", "vet", "./..."]
    applies_to: "*.go"
    on: "diff"
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert "presubmit" in config
        assert len(config["presubmit"]) == 1

    def test_valid_presubmit_with_when_exists_loads_ok(self):
        """gate.yaml with presubmit entry with valid when_exists string loads ok."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - command: ["scripts/checkpatch.pl", "--strict"]
    applies_to: "*.c"
    on: "patch"
    when_exists: "scripts/checkpatch.pl"
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert config["presubmit"][0]["when_exists"] == "scripts/checkpatch.pl"

    def test_no_presubmit_section_loads_ok(self):
        """gate.yaml with no presubmit section loads ok (section is optional)."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert "presubmit" not in config

    def test_on_diff_loads_ok(self):
        """gate.yaml with presubmit entry with on='diff' loads without error."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - command: ["go", "vet", "./..."]
    applies_to: "*.go"
    on: "diff"
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert config["presubmit"][0]["on"] == "diff"

    def test_on_patch_loads_ok(self):
        """gate.yaml with presubmit entry with on='patch' loads without error."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - command: ["scripts/checkpatch.pl"]
    applies_to: "*.c"
    on: "patch"
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert config["presubmit"][0]["on"] == "patch"

    # -- load_gate_config: non_ascii field --

    def test_non_ascii_ai_smell_loads_ok(self):
        """gate.yaml with non_ascii: 'ai-smell' loads ok."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
non_ascii: "ai-smell"
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert config["non_ascii"] == "ai-smell"

    def test_non_ascii_strict_loads_ok(self):
        """gate.yaml with non_ascii: 'strict' loads ok."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
non_ascii: "strict"
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert config["non_ascii"] == "strict"

    def test_non_ascii_absent_defaults_to_ai_smell(self):
        """gate.yaml with no non_ascii field defaults to 'ai-smell'."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert config.get("non_ascii", "ai-smell") == "ai-smell"

    def test_non_ascii_unknown_raises(self):
        """gate.yaml with non_ascii: 'unknown-value' raises ValueError (fail-closed)."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
non_ascii: "unknown-value"
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError, match="non_ascii"):
            load_gate_config("gate.yaml", fs_open=m)

    # -- load_gate_config: presubmit error cases --

    def test_presubmit_non_list_raises(self):
        """gate.yaml with presubmit as non-list raises ValueError."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  command: ["go", "vet", "./..."]
  applies_to: "*.go"
  on: "diff"
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError, match="presubmit"):
            load_gate_config("gate.yaml", fs_open=m)

    def test_missing_command_raises(self):
        """gate.yaml with presubmit entry missing 'command' raises ValueError."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - applies_to: "*.go"
    on: "diff"
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError, match="presubmit.*command"):
            load_gate_config("gate.yaml", fs_open=m)

    def test_metachar_in_command_raises(self):
        """gate.yaml with presubmit entry command containing '|' raises ValueError."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - command: ["go", "vet", "./...", "|", "grep", "error"]
    applies_to: "*.go"
    on: "diff"
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError):
            load_gate_config("gate.yaml", fs_open=m)

    def test_command_not_list_raises(self):
        """gate.yaml with presubmit entry command that is not a list raises ValueError."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - command: "go vet ./..."
    applies_to: "*.go"
    on: "diff"
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError):
            load_gate_config("gate.yaml", fs_open=m)

    def test_on_message_raises(self):
        """gate.yaml with presubmit entry with on='message' raises ValueError."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - command: ["go", "vet", "./..."]
    applies_to: "*.go"
    on: "message"
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError, match="message"):
            load_gate_config("gate.yaml", fs_open=m)

    def test_on_invalid_value_raises(self):
        """gate.yaml with presubmit entry with invalid 'on' value raises ValueError."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - command: ["go", "vet", "./..."]
    applies_to: "*.go"
    on: "staged"
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError):
            load_gate_config("gate.yaml", fs_open=m)

    def test_applies_to_non_string_raises(self):
        """gate.yaml with presubmit entry applies_to as non-string raises ValueError."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - command: ["go", "vet", "./..."]
    applies_to:
      - "*.go"
      - "*.rs"
    on: "diff"
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError):
            load_gate_config("gate.yaml", fs_open=m)

    def test_applies_to_single_quote_raises(self):
        """gate.yaml with applies_to containing single-quote raises ValueError."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - command: ["go", "vet", "./..."]
    applies_to: "*.go'"
    on: "diff"
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError):
            load_gate_config("gate.yaml", fs_open=m)

    def test_applies_to_double_quote_raises(self):
        """gate.yaml with applies_to containing double-quote raises ValueError."""
        yaml_content = r"""
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - command: ["go", "vet", "./..."]
    applies_to: '*.go"'
    on: "diff"
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError):
            load_gate_config("gate.yaml", fs_open=m)

    def test_when_exists_single_quote_raises(self):
        """gate.yaml with when_exists containing single-quote raises ValueError."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - command: ["go", "vet", "./..."]
    applies_to: "*.go"
    on: "diff"
    when_exists: "scripts/check'.pl"
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError):
            load_gate_config("gate.yaml", fs_open=m)

    def test_when_exists_double_quote_raises(self):
        """gate.yaml with when_exists containing double-quote raises ValueError."""
        yaml_content = r"""
test:
  command: ["python3", "-m", "pytest"]
presubmit:
  - command: ["go", "vet", "./..."]
    applies_to: "*.go"
    on: "diff"
    when_exists: 'scripts/check".pl'
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError):
            load_gate_config("gate.yaml", fs_open=m)

    # -- validate_presubmit_command --

    def test_validate_presubmit_command_rejects_metachar_in_each_element(self):
        """validate_presubmit_command rejects metacharacters in each element."""
        for meta in list("|;&$><`"):
            bad_command = ["go", "vet", "./..." + meta]
            with pytest.raises(ValueError):
                validate_presubmit_command(bad_command)

    def test_validate_presubmit_command_rejects_percent_in_command(self):
        """validate_presubmit_command rejects % in command elements.

        A % in a command arg causes TypeError at hook-generation time because
        _build_presubmit_block uses Python % string formatting with the command.
        """
        with pytest.raises(ValueError, match="Percent sign"):
            validate_presubmit_command(["checkpatch%", "--strict"])
        with pytest.raises(ValueError, match="Percent sign"):
            validate_presubmit_command(["lint", "--flag=%s"])

    # -- fnmatch_to_grep via real grep -E --

    def _grep_matches(self, pattern: str, text: str) -> bool:
        """Run real grep -E to test the pattern against text."""
        result = subprocess.run(
            ["grep", "-E", pattern],
            input=text,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def test_fnmatch_star_matches_zero_chars(self):
        """fnmatch_to_grep('test_*.py') matches 'test_.py' (star=zero chars OK)."""
        pattern = fnmatch_to_grep("test_*.py")
        assert self._grep_matches(pattern, "test_.py")

    def test_fnmatch_star_matches_nonempty(self):
        """fnmatch_to_grep('test_*.py') matches 'test_foo.py'."""
        pattern = fnmatch_to_grep("test_*.py")
        assert self._grep_matches(pattern, "test_foo.py")

    def test_fnmatch_star_no_match_wrong_ext(self):
        """fnmatch_to_grep('*.py') does NOT match 'foo.js' via real grep -E."""
        pattern = fnmatch_to_grep("*.py")
        assert not self._grep_matches(pattern, "foo.js")

    def test_fnmatch_star_matches_foo_py(self):
        """fnmatch_to_grep('*.py') matches 'foo.py' via real grep -E."""
        pattern = fnmatch_to_grep("*.py")
        assert self._grep_matches(pattern, "foo.py")

    def test_fnmatch_anchor_rejects_path_prefix(self):
        """fnmatch_to_grep('test_*.py') must NOT match 'src/test_foo.py'.

        Without the leading ^ anchor, grep -E 'test_.*\\.py$' matches the
        substring 'test_foo.py' inside 'src/test_foo.py'. The anchor ensures
        the pattern is applied to the full filename only.
        """
        pattern = fnmatch_to_grep("test_*.py")
        assert not self._grep_matches(pattern, "src/test_foo.py")


# --- graph_triage section validation ---


class TestGraphTriageValidation:
    """Tests for graph_triage section validation in load_gate_config."""

    def test_graph_triage_valid(self):
        """gate.yaml with valid graph_triage section passes validation."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
graph_triage:
  enabled: true
  db_path: "/path/graph.db"
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert config["graph_triage"]["enabled"] is True
        assert config["graph_triage"]["db_path"] == "/path/graph.db"

    def test_graph_triage_invalid_enabled(self):
        """graph_triage.enabled not bool raises ValueError."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
graph_triage:
  enabled: "yes"
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError, match="graph_triage"):
            load_gate_config("gate.yaml", fs_open=m)

    def test_graph_triage_invalid_db_path(self):
        """graph_triage.db_path not string raises ValueError."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
graph_triage:
  db_path: 123
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError, match="graph_triage"):
            load_gate_config("gate.yaml", fs_open=m)

    def test_graph_triage_absent_ok(self):
        """gate.yaml without graph_triage section passes validation."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert "graph_triage" not in config

    def test_graph_triage_extra_keys_ok(self):
        """graph_triage section with unknown keys does not raise."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
graph_triage:
  enabled: true
  future_key: "some_value"
  another_key: 42
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert config["graph_triage"]["enabled"] is True
        assert config["graph_triage"]["future_key"] == "some_value"


# --- daemon_state validation (STATE-01g) ---


class TestDaemonStateValidation:
    """gate.yaml daemon_state section validation."""

    def test_daemon_state_valid(self):
        """Valid daemon_state section passes validation."""
        from code_forge.gate_check import validate_daemon_state

        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
daemon_state:
  enabled: true
  subsystems: ["nftables", "routing"]
  patterns: ["flock", "pidfile"]
  conflicts:
    - subsystem: "killswitch"
      mutates: "nft mark"
      interferes_with: "health check probes"
  conflicts_file: "conflicts.yaml"
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert "daemon_state" in config

    def test_daemon_state_invalid_enabled(self):
        """daemon_state.enabled not bool raises ValueError."""
        from code_forge.gate_check import validate_daemon_state

        with pytest.raises(ValueError, match="bool"):
            validate_daemon_state({"enabled": "yes"})

    def test_daemon_state_invalid_subsystems(self):
        """daemon_state.subsystems not list raises ValueError."""
        from code_forge.gate_check import validate_daemon_state

        with pytest.raises(ValueError, match="list"):
            validate_daemon_state({"subsystems": "nftables"})

    def test_daemon_state_missing_triplet_field(self):
        """Conflict triplet missing 'mutates' raises ValueError with field name."""
        from code_forge.gate_check import validate_daemon_state

        with pytest.raises(ValueError, match="mutates"):
            validate_daemon_state({
                "conflicts": [
                    {
                        "subsystem": "killswitch",
                        "interferes_with": "health check",
                        # missing "mutates"
                    },
                ],
            })

    def test_daemon_state_conflicts_file_string(self):
        """conflicts_file not string raises ValueError."""
        from code_forge.gate_check import validate_daemon_state

        with pytest.raises(ValueError, match="string"):
            validate_daemon_state({"conflicts_file": 42})

    def test_daemon_state_absent_ok(self):
        """gate.yaml without daemon_state section passes validation."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert "daemon_state" not in config


class TestRetryConfig:
    """Tests for validate_retry_config and load_gate_config retry wiring."""

    def test_valid_full_config(self):
        """Both fields present and valid passes."""
        validate_retry_config({"max_attempts": 5, "initial_delay_s": 2})

    def test_valid_min_max_attempts(self):
        """max_attempts=1 (minimum) passes."""
        validate_retry_config({"max_attempts": 1})

    def test_valid_max_max_attempts(self):
        """max_attempts=10 (maximum) passes."""
        validate_retry_config({"max_attempts": 10})

    def test_valid_min_initial_delay(self):
        """initial_delay_s=0.1 (minimum) passes."""
        validate_retry_config({"initial_delay_s": 0.1})

    def test_valid_max_initial_delay(self):
        """initial_delay_s=30 (maximum) passes."""
        validate_retry_config({"initial_delay_s": 30})

    def test_valid_empty_dict(self):
        """Empty dict passes (all fields optional)."""
        validate_retry_config({})

    def test_max_attempts_below_min(self):
        """max_attempts=0 raises ValueError."""
        with pytest.raises(ValueError, match="max_attempts"):
            validate_retry_config({"max_attempts": 0})

    def test_max_attempts_above_max(self):
        """max_attempts=11 raises ValueError."""
        with pytest.raises(ValueError, match="max_attempts"):
            validate_retry_config({"max_attempts": 11})

    def test_max_attempts_wrong_type(self):
        """max_attempts='five' raises ValueError."""
        with pytest.raises(ValueError, match="max_attempts"):
            validate_retry_config({"max_attempts": "five"})

    def test_max_attempts_bool_rejected(self):
        """Bool is not int for max_attempts."""
        with pytest.raises(ValueError, match="max_attempts"):
            validate_retry_config({"max_attempts": True})

    def test_initial_delay_below_min(self):
        """initial_delay_s=0 raises ValueError."""
        with pytest.raises(ValueError, match="initial_delay_s"):
            validate_retry_config({"initial_delay_s": 0})

    def test_initial_delay_above_max(self):
        """initial_delay_s=31 raises ValueError."""
        with pytest.raises(ValueError, match="initial_delay_s"):
            validate_retry_config({"initial_delay_s": 31})

    def test_initial_delay_wrong_type(self):
        """initial_delay_s='two' raises ValueError."""
        with pytest.raises(ValueError, match="initial_delay_s"):
            validate_retry_config({"initial_delay_s": "two"})

    def test_not_a_dict(self):
        """Non-dict raises ValueError."""
        with pytest.raises(ValueError, match="mapping"):
            validate_retry_config("not a dict")

    def test_load_gate_config_with_retry(self):
        """load_gate_config calls validate_retry_config when retry present."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
retry:
  max_attempts: 3
  initial_delay_s: 1.5
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert config["retry"]["max_attempts"] == 3

    def test_load_gate_config_without_retry(self):
        """load_gate_config without retry does not raise."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
"""
        m = mock_open(read_data=yaml_content)
        config = load_gate_config("gate.yaml", fs_open=m)
        assert "retry" not in config

    def test_load_gate_config_invalid_retry_rejected(self):
        """load_gate_config with invalid retry raises ValueError."""
        yaml_content = """
test:
  command: ["python3", "-m", "pytest"]
retry:
  max_attempts: 0
"""
        m = mock_open(read_data=yaml_content)
        with pytest.raises(ValueError, match="max_attempts"):
            load_gate_config("gate.yaml", fs_open=m)

    def test_initial_delay_bool_rejected(self):
        """Bool is not a number for initial_delay_s."""
        with pytest.raises(ValueError, match="initial_delay_s"):
            validate_retry_config({"initial_delay_s": True})

    def test_initial_delay_int_accepted(self):
        """Integer value for initial_delay_s is valid (int is a number)."""
        validate_retry_config({"initial_delay_s": 5})
