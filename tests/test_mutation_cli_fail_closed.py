# SPDX-License-Identifier: Apache-2.0
"""Mutation-check reports success only after an applicable run finishes."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from code_forge.cli import _build_parser, _run_mutation_check
from code_forge.disposition import Disposition
from code_forge.exit_codes import EXIT_CLI_ERROR, EXIT_FAIL, EXIT_PASS
from code_forge.state import StateFinding


def _args(tmp_path, path="src/mod.py"):
    diff = tmp_path / "change.diff"
    diff.write_text(
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-a\n+b\n"
    )
    return _build_parser().parse_args(["mutation-check", "--diff", str(diff)])


def _skip(fingerprint):
    return StateFinding(
        id="MUTATION_SKIPPED", fingerprint=fingerprint, source="MUTANT",
        disposition=Disposition.DISMISSED, file="", line_range=[],
        description="mutation did not run",
    )


def _gate(tmp_path, test):
    gate = tmp_path / ".code-forge" / "gate.yaml"
    gate.parent.mkdir(exist_ok=True)
    gate.write_text(yaml.safe_dump({"test": test}))
    return gate


@pytest.mark.parametrize("with_finding", [False, True])
def test_infrastructure_failure_cannot_print_pass(tmp_path, capsys, with_finding):
    findings = [_skip("mutation-baseline-timeout")] if with_finding else []
    with patch("code_forge.mutation.run_mutation", return_value=(findings, ["baseline timed out"])):
        result = _run_mutation_check(_args(tmp_path), tmp_path)
    assert result == EXIT_CLI_ERROR
    output = capsys.readouterr()
    assert "baseline timed out" in output.err
    assert "PASS" not in output.err + output.out


@pytest.mark.parametrize("fingerprint", [
    "mutation-timeout", "mutation-results-timeout", "mutation-probe-timeout",
    "mutation-unavailable", "mutation-config-conflict", "mutation-future-error",
    "mutation-baseline-failed", "mutation-baseline-timeout",
])
def test_unfinished_mutation_is_not_pass(tmp_path, capsys, fingerprint):
    with patch("code_forge.mutation.run_mutation", return_value=([_skip(fingerprint)], [])):
        assert _run_mutation_check(_args(tmp_path), tmp_path) == EXIT_CLI_ERROR
    output = capsys.readouterr()
    assert "mutation did not run" in output.err
    assert "PASS" not in output.out + output.err


@pytest.mark.parametrize("path", ["tests/test_mod.py", "README.md"])
def test_real_inapplicable_diff_is_skip_not_pass(tmp_path, capsys, path):
    # Real runner exits before any subprocess when the diff has no production Python.
    assert _run_mutation_check(_args(tmp_path, path), tmp_path) == EXIT_PASS
    output = capsys.readouterr()
    assert "SKIP" in output.err
    assert "PASS" not in output.err + output.out


@pytest.mark.parametrize("fingerprint", ["mutation-tests-only", "mutation-no-python"])
def test_valid_skip_cannot_mask_unrelated_infrastructure_error(tmp_path, capsys, fingerprint):
    with patch(
        "code_forge.mutation.run_mutation",
        return_value=([_skip(fingerprint)], ["unexpected engine failure"]),
    ):
        assert _run_mutation_check(_args(tmp_path), tmp_path) == EXIT_CLI_ERROR
    output = capsys.readouterr()
    assert "unexpected engine failure" in output.err
    assert "PASS" not in output.err + output.out


def test_no_python_information_without_skip_is_an_error(tmp_path, capsys):
    with patch(
        "code_forge.mutation.run_mutation",
        return_value=([], ["no Python files in the diff"]),
    ):
        assert _run_mutation_check(_args(tmp_path), tmp_path) == EXIT_CLI_ERROR
    assert "PASS" not in capsys.readouterr().err


def test_survivor_with_valid_skip_still_fails(tmp_path, capsys):
    survivor = StateFinding(
        id="mutant-one", fingerprint="mutant:one", source="MUTANT",
        disposition=Disposition.CONFIRMED, file="src/mod.py", line_range=[1, 1],
        description="mutation survived",
    )
    with patch(
        "code_forge.mutation.run_mutation",
        return_value=([_skip("mutation-tests-only"), survivor], []),
    ):
        assert _run_mutation_check(_args(tmp_path), tmp_path) == EXIT_FAIL
    assert "PASS" not in capsys.readouterr().err


def test_completed_run_reports_pass_and_uses_defaults(tmp_path, capsys):
    with patch("code_forge.mutation.run_mutation", return_value=([], [])) as run:
        assert _run_mutation_check(_args(tmp_path), tmp_path) == EXIT_PASS
    assert "PASS" in capsys.readouterr().err
    kwargs = run.call_args.kwargs
    assert kwargs["baseline_cmd"] == ["pytest", "--tb=no", "-q"]
    assert kwargs["baseline_timeout"] == 120
    assert kwargs["also_copy"] is None
    assert kwargs["max_children"] is None
    assert kwargs["memory_limit_bytes"] is None
    assert kwargs["cwd"] == tmp_path


def test_gate_settings_reach_mutation_runner(tmp_path):
    command = [sys.executable, "-m", "pytest", "tests/test_mod.py", "-q"]
    _gate(tmp_path, {
        "command": command, "timeout_seconds": 901,
        "also_copy": ["scripts/"], "mutation_max_children": 2,
        "mutation_memory_limit_mb": 512,
    })
    args = _args(tmp_path)
    args.timeout = 47
    with patch("code_forge.mutation.run_mutation", return_value=([], [])) as run:
        assert _run_mutation_check(args, tmp_path) == EXIT_PASS
    kwargs = run.call_args.kwargs
    assert kwargs["baseline_cmd"] == command
    assert kwargs["baseline_timeout"] == 901
    assert kwargs["timeout"] == 47
    assert kwargs["also_copy"] == ["scripts/"]
    assert kwargs["max_children"] == 2
    assert kwargs["memory_limit_bytes"] == 512 * 1024**2
    assert kwargs["cwd"] == tmp_path


@pytest.mark.parametrize("content", ["test: []", "test: {command: []}", "test: ["])
def test_invalid_gate_fails_before_running_mutation(tmp_path, capsys, content):
    gate = _gate(tmp_path, {})
    gate.write_text(content)
    with patch("code_forge.mutation.run_mutation") as run:
        assert _run_mutation_check(_args(tmp_path), tmp_path) == EXIT_CLI_ERROR
    run.assert_not_called()
    assert "PASS" not in capsys.readouterr().err


def test_unreadable_gate_fails_before_running_mutation(tmp_path, capsys):
    gate = tmp_path / ".code-forge" / "gate.yaml"
    gate.mkdir(parents=True)
    with patch("code_forge.mutation.run_mutation") as run:
        assert _run_mutation_check(_args(tmp_path), tmp_path) == EXIT_CLI_ERROR
    run.assert_not_called()
    assert "PASS" not in capsys.readouterr().err


@pytest.mark.integration
@pytest.mark.parametrize("path", ["tests/test_mod.py", "README.md"])
def test_real_cli_inapplicable_diff_is_successful_skip(tmp_path, path):
    args = _args(tmp_path, path)
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "code_forge", "mutation-check", "--diff", args.diff],
        cwd=tmp_path, env=dict(os.environ, PYTHONPATH=str(root / "src")),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == EXIT_PASS, result.stdout + result.stderr
    assert "SKIP" in result.stderr
    assert "PASS" not in result.stdout + result.stderr


@pytest.mark.integration
def test_real_cli_baseline_failure_is_not_success(tmp_path):
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_failure.py").write_text("def test_failure():\n    assert False\n")
    _gate(tmp_path, {
        "command": [sys.executable, "-m", "pytest", "tests/", "-q"],
        "timeout_seconds": 30,
    })
    args = _args(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "code_forge", "mutation-check", "--diff", args.diff],
        cwd=tmp_path, env=dict(os.environ, PYTHONPATH=str(root / "src")),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == EXIT_CLI_ERROR, result.stdout + result.stderr
    assert "baseline failed" in result.stderr
    assert "PASS" not in result.stdout + result.stderr
