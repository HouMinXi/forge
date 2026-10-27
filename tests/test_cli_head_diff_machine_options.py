# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""CLI paths that run `git diff HEAD` must not depend on the user's diff config.

The review path builds its diff with the machine options in
code_forge.git (fixed a/ b/ prefixes, no color, no textconv or external
diff). The verify, smoke-run, inline canary and e2e-check paths ran a bare
`git diff HEAD`, so a common setting such as diff.noprefix=true gave them a
diff the parsers cannot read and a hash that never matches the review's.
"""

import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from code_forge import cli
from code_forge.source import compute_source_hash
from code_forge.verify import VerifyResult

_PREFIX_CONFIGS = ("diff.noprefix", "diff.mnemonicPrefix")


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def noprefix_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent.resolve()))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    _git(tmp_path, "config", "user.name", "T")
    (tmp_path / "f.py").write_text("a = 1\n")
    _git(tmp_path, "add", "f.py")
    _git(tmp_path, "commit", "-qm", "init")
    (tmp_path / "f.py").write_text("a = 2\n")
    _git(tmp_path, "config", "diff.noprefix", "true")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _reference_hash(repo):
    out = subprocess.run(
        ["git", "-c", "diff.noprefix=false", "diff", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "--- a/f.py" in out
    return compute_source_hash(git_diff=out)


def test_verify_reads_changed_files_under_noprefix(noprefix_repo, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["code-forge", "verify"])
    with patch("code_forge.verify.run_verify", return_value=VerifyResult(True, "ok", 1, 1)) as run_verify:
        assert cli.main() == 0
    args, _ = run_verify.call_args
    diff_sha, diff_files = args[1], args[2]
    assert diff_files == {"f.py": [1]}
    assert diff_sha == _reference_hash(noprefix_repo)


def test_smoke_receipt_hash_ignores_noprefix(noprefix_repo):
    args = SimpleNamespace(
        command=[sys.executable, "-c", "print('smoke')"],
        surface="check",
        target="HEAD",
        timeout=5,
    )
    assert cli._handle_smoke_run(args, noprefix_repo) == 0
    receipt = json.loads(next(noprefix_repo.rglob("smoke-receipt-*.json")).read_text())
    assert receipt["diff_sha256"] == _reference_hash(noprefix_repo)
