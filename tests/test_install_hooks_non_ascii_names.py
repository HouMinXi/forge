# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Pre-commit hook blocks that list staged names must see non-ASCII names.

git quotes such names by default, for example "caf\\303\\251.md" with the
surrounding double quotes. A pattern anchored on the first or last
character then no longer matches the name.
"""

import subprocess

from code_forge.install_hooks import _build_planning_leak_guard


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_planning_leak_guard_blocks_non_ascii_planning_file(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent.resolve()))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    _git(tmp_path, "init", "-q")
    (tmp_path / ".planning").mkdir()
    (tmp_path / ".planning" / "caf\u00e9.md").write_text("notes\n")
    _git(tmp_path, "add", ".")
    result = subprocess.run(
        ["sh", "-c", _build_planning_leak_guard()],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "BLOCKED" in result.stderr
