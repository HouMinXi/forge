"""Default collection must stay in this checkout's intended test suite."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def _collect(repo, label, *paths):
    command = [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *paths]
    result = subprocess.run(
        command,
        cwd=repo,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    (repo / (label + ".stdout.log")).write_text(result.stdout, encoding="utf-8")
    (repo / (label + ".stderr.log")).write_text(result.stderr, encoding="utf-8")
    (repo / (label + ".command.json")).write_text(
        json.dumps({"argv": command, "cwd": str(repo), "exit": result.returncode}, indent=2),
        encoding="utf-8",
    )
    return result


@pytest.mark.parametrize("archive_root", [".planning", ".worktrees"])
def test_default_collection_matches_explicit_suite_with_archived_poison(tmp_path, archive_root):
    config = Path(__file__).resolve().parents[1] / "pyproject.toml"
    (tmp_path / "pyproject.toml").write_bytes(config.read_bytes())
    tests = tmp_path / "tests"
    nested = tests / "nested"
    nested.mkdir(parents=True)
    (tests / "test_legitimate.py").write_text("def test_first():\n    assert True\n", encoding="utf-8")
    (nested / "test_nested.py").write_text("def test_second():\n    assert True\n", encoding="utf-8")
    (tmp_path / "conftest.py").write_text(
        "from pathlib import Path\n"
        "def pytest_configure(config):\n"
        "    Path(__file__).with_name('root-conftest-loaded').write_text('loaded')\n",
        encoding="utf-8",
    )
    poison = tmp_path / archive_root / "archived" / "tests" / "test_poison.py"
    poison.parent.mkdir(parents=True)
    poison.write_text("raise RuntimeError('ARCHIVED_COLLECTION_SENTINEL')\n", encoding="utf-8")

    explicit = _collect(tmp_path, "explicit-legitimate", "tests")
    default = _collect(tmp_path, "default")
    expected = {"tests/test_legitimate.py::test_first", "tests/nested/test_nested.py::test_second"}
    for result in (explicit, default):
        assert result.returncode == 0, result.stdout + result.stderr
        nodes = {line for line in result.stdout.splitlines() if "::" in line}
        assert nodes == expected
        assert "ARCHIVED_COLLECTION_SENTINEL" not in result.stdout + result.stderr
    assert (tmp_path / "root-conftest-loaded").read_text(encoding="utf-8") == "loaded"

    external = _collect(tmp_path, "explicit-external", str(poison.relative_to(tmp_path)))
    assert external.returncode == 2
    assert "ARCHIVED_COLLECTION_SENTINEL" in external.stdout + external.stderr
