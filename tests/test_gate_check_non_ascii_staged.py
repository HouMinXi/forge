# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""gate-check must see staged files whose names are not ASCII.

With git's default core.quotePath=true, `git diff --cached --name-only`
prints such a name as a quoted, octal-escaped string. The quoted string
no longer matches a source pattern like "*.py", so gate-check reported
"no source files staged" and skipped the tests.
"""

import subprocess
from io import StringIO

import yaml

from code_forge.exit_codes import EXIT_FAIL
from code_forge.gate_check import run_gate_check


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_staged_non_ascii_source_file_runs_tests(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent.resolve()))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    _git(tmp_path, "init", "-q")
    (tmp_path / "caf\u00e9.py").write_text("x = 1\n")
    _git(tmp_path, "add", ".")
    forge_dir = tmp_path / ".code-forge"
    forge_dir.mkdir()
    config = {
        "test": {
            "command": ["python3", "-c", "raise SystemExit(1)"],
            "source_patterns": ["*.py"],
        }
    }
    (forge_dir / "gate.yaml").write_text(yaml.dump(config))
    stderr = StringIO()
    result = run_gate_check(args=None, env={}, cwd=tmp_path, stdout=StringIO(), stderr=stderr)
    assert "no source files staged" not in stderr.getvalue()
    assert result == EXIT_FAIL
