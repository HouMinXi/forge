# SPDX-License-Identifier: Apache-2.0
"""Regression checks for tests that run inside a mutation mirror."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _pytest_from(root, *args):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    return subprocess.run(
        [sys.executable, "-m", "pytest", *args, "-q", "-p", "no:randomly"],
        cwd=root, env=env, capture_output=True, text=True, timeout=60,
    )


@pytest.mark.integration
def test_mutation_unit_tests_ignore_invoking_repository_config(tmp_path):
    config = tmp_path / ".code-forge" / "gate.yaml"
    config.parent.mkdir()
    config.write_text("test:\n  mutation_skip_globs: ['**']\n", encoding="utf-8")
    result = _pytest_from(
        tmp_path,
        str(ROOT / "tests/test_mutation.py") + "::TestRunMutation",
        str(ROOT / "tests/test_mutation.py") + "::TestVenvFallback",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "passed" in result.stdout
    assert config.read_text(encoding="utf-8") == (
        "test:\n  mutation_skip_globs: ['**']\n"
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("file", "checkout_class", "behavior_test"),
    [
        (
            "test_mutation_resource_guard.py",
            "TestRepoGateConfigIsTracked",
            "test_build_mutmut_config_excludes_integration",
        ),
        (
            "test_taint_integration.py",
            "TestProvenanceQuestion",
            "test_source_files_injected_before_run",
        ),
    ],
)
def test_checkout_scans_are_excluded_only_in_mutation_stage(
    tmp_path, file, checkout_class, behavior_test,
):
    test_path = str(ROOT / "tests" / file)
    normal = _pytest_from(tmp_path, test_path, "--collect-only")
    assert normal.returncode == 0, normal.stdout + normal.stderr
    assert checkout_class in normal.stdout
    assert behavior_test in normal.stdout

    mutation = _pytest_from(
        tmp_path, test_path, "--collect-only", "-m",
        "not integration and not source_scan",
    )
    assert mutation.returncode == 0, mutation.stdout + mutation.stderr
    assert checkout_class not in mutation.stdout
    assert behavior_test in mutation.stdout
