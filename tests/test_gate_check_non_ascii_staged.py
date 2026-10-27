# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""gate-check must see staged files under their literal names.

Without -z, `git diff --cached --name-only` prints a non-ASCII name as a
quoted, escaped string under the default core.quotePath=true, and prints a
name with a control character (tab, newline) that way even with
core.quotePath=false. The quoted string no longer matches a source pattern
like "*.py", so gate-check reported "no source files staged" and skipped the
tests. Names must also keep their leading and trailing whitespace and any
trailing "\\r" or "\\n", which a text-mode read or a strip would change.
"""

import os
import subprocess
from io import StringIO

import pytest
import yaml

from code_forge.exit_codes import EXIT_FAIL
from code_forge.gate_check import run_gate_check


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _stage(tmp_path, monkeypatch, name, patterns):
    """Init a repo, stage `name` (or nothing) and write a gate.yaml that fails its tests."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent.resolve()))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    _git(tmp_path, "init", "-q")
    if name is not None:
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


def _run(tmp_path):
    stderr = StringIO()
    result = run_gate_check(args=None, env={}, cwd=tmp_path, stdout=StringIO(), stderr=stderr)
    return result, stderr.getvalue()


def _gate(tmp_path, monkeypatch, name, patterns):
    _stage(tmp_path, monkeypatch, name, patterns)
    return _run(tmp_path)


@pytest.mark.parametrize(
    "name, patterns",
    [
        ("caf\u00e9.py", ["*.py"]),
        ("tab\there.py", ["*.py"]),
        ("new\nline.py", ["*.py"]),
        # Patterns that name the space: a stripped name would miss them.
        (" lead.py", [" lead.py"]),
        ("trail.py ", ["trail.py "]),
        ("trail.py\n", ["trail.py\n"]),
        ("cr.py\r", ["cr.py\r"]),
        # A name that is not valid UTF-8 must still be listed, not crash the gate.
        (os.fsdecode(b"bad\xff.py"), ["*.py"]),
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


def test_staged_name_with_trailing_newline_is_not_trimmed(tmp_path, monkeypatch):
    """`a.py\\n` does not match `*.py`, so a commit of only that file skips the tests."""
    result, stderr = _gate(tmp_path, monkeypatch, "a.py\n", ["*.py"])
    assert "no source files staged" in stderr
    assert result != EXIT_FAIL


def test_empty_index_skips_tests_even_with_a_match_all_pattern(tmp_path, monkeypatch):
    result, stderr = _gate(tmp_path, monkeypatch, None, ["*"])
    assert "no source files staged" in stderr
    assert result != EXIT_FAIL


def test_failing_git_diff_blocks_instead_of_skipping(tmp_path, monkeypatch):
    _stage(tmp_path, monkeypatch, "a.py", ["*.py"])
    (tmp_path / ".git" / "index").write_bytes(b"not an index")
    result, stderr = _run(tmp_path)
    assert result == EXIT_FAIL
    assert "git diff --cached failed" in stderr
