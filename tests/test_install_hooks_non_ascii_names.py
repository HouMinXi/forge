# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Pre-commit hook blocks that list staged names must see non-ASCII names.

git quotes such names by default, for example "caf\\303\\251.md" with the
surrounding double quotes. A pattern anchored on the first or last
character then no longer matches the name.
"""

import subprocess

from code_forge.install_hooks import _build_planning_leak_guard, generate_hook_content


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


def _run_hook_with_staged_file(tmp_path, monkeypatch, name):
    """Stage ``name`` and run the generated hook with a fake ``code-forge``.

    The fake binary prints a marker and fails, so a hook that reaches the
    verify step exits 1 with the marker on stderr.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "code-forge"
    fake.write_text("#!/bin/sh\necho FAKE-VERIFY-RAN >&2\nexit 1\n")
    fake.chmod(0o755)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.resolve()))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    _git(repo, "init", "-q")
    (repo / name).write_text("content\n")
    _git(repo, "add", ".")
    hook = generate_hook_content("code-forge gate-check", None)
    return subprocess.run(
        ["sh", "-c", hook],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def test_carveout_skips_verify_for_non_ascii_non_code_file(tmp_path, monkeypatch):
    result = _run_hook_with_staged_file(tmp_path, monkeypatch, "caf\u00e9.md")
    assert result.returncode == 0
    assert "skipping verify (non-code commit)" in result.stderr
    assert "FAKE-VERIFY-RAN" not in result.stderr


def test_carveout_still_verifies_non_ascii_code_file(tmp_path, monkeypatch):
    result = _run_hook_with_staged_file(tmp_path, monkeypatch, "caf\u00e9.py")
    assert result.returncode == 1
    assert "FAKE-VERIFY-RAN" in result.stderr
    assert "skipping verify (non-code commit)" not in result.stderr
