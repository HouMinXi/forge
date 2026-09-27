"""Keep sandbox projects out of the host suite without disabling their tests."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = "tests/fixtures/mutation_contract/css_button_width/test_width.py"
pytestmark = pytest.mark.source_scan


def _collect(*paths: str) -> set[str]:
    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:randomly", *paths],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return {line.strip() for line in result.stdout.splitlines() if "::" in line}


def test_host_collection_keeps_drivers_but_not_sandbox_projects():
    nodes = _collect("tests")
    assert "tests/test_mutation_css_real.py::test_width_mutation_is_killed" in nodes
    assert "tests/test_mutation_css_real.py::test_weakened_width_assertion_lets_it_survive" in nodes
    assert not any(node.startswith("tests/fixtures/mutation_contract/") for node in nodes)


def test_explicit_sandbox_fixture_is_still_collectable():
    assert _collect(FIXTURE) == {f"{FIXTURE}::test_panel_width"}
