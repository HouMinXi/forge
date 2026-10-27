# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""gate-check must see staged files under their literal names.

`git diff --cached --name-only` prints a non-ASCII name, or one with a
control character, as a quoted, escaped string even with
core.quotePath=false. The quoted string no longer matches a source
pattern like "*.py", so gate-check reported "no source files staged" and
skipped the tests. Names must also keep their leading and trailing spaces.
"""

import subprocess
from io import StringIO

import pytest
import yaml

from code_forge.exit_codes import EXIT_FAIL
from code_forge.gate_check import run_gate_check


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _gate(tmp_path, monkeypatch, name, patterns):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent.resolve()))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    _git(tmp_path, "init", "-q")
    (tmp_path / name).write_text("x = 1\n")
    _git(tmp_path, "add", "--", name)
    forge_dir = tmp_path / ".code-forge"
    forge_dir.mkdir()
    config = {
        "test": {
            "command": ["python3", "-c", "raise SystemExit(1)"],
            "source_patterns": patterns,
        }
    }
    (forge_dir / "gate.yaml").write_text(yaml.dump(config))
    stderr = StringIO()
    result = run_gate_check(args=None, env={}, cwd=tmp_path, stdout=StringIO(), stderr=stderr)
    return result, stderr.getvalue()


@pytest.mark.parametrize(
    "name, patterns",
    [
        ("caf\u00e9.py", ["*.py"]),
        ("tab\there.py", ["*.py"]),
        ("new\nline.py", ["*.py"]),
        # Patterns that name the space: a stripped name would miss them.
        (" lead.py", [" lead.py"]),
        ("trail.py ", ["trail.py "]),
    ],
)
def test_staged_source_file_with_unusual_name_runs_tests(tmp_path, monkeypatch, name, patterns):
    result, stderr = _gate(tmp_path, monkeypatch, name, patterns)
    assert "no source files staged" not in stderr
    assert result == EXIT_FAIL


def test_staged_name_is_matched_exactly_not_stripped(tmp_path, monkeypatch):
    """`trail.py ` does not match `*.py`, so a commit of only that file skips the tests."""
    result, stderr = _gate(tmp_path, monkeypatch, "trail.py ", ["*.py"])
    assert "no source files staged" in stderr
    assert result != EXIT_FAIL
