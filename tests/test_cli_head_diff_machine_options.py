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
from code_forge import git as cli_git
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


@pytest.fixture
def binary_noprefix_repo(noprefix_repo):
    """The noprefix repo plus a tracked 20 KB binary file with an uncommitted change."""
    blob = noprefix_repo / "blob.bin"
    blob.write_bytes(bytes(range(256)) * 80)
    _git(noprefix_repo, "add", "blob.bin")
    _git(noprefix_repo, "commit", "-qm", "add blob")
    blob.write_bytes(bytes(reversed(range(256))) * 80)
    return noprefix_repo


def _capture_canary_diff(repo, monkeypatch):
    import code_forge.backend as backend_mod
    import code_forge.canary_gen as canary_gen_mod

    seen = {}
    monkeypatch.setattr(backend_mod, "resolve_backend", lambda *a, **k: object())

    def fake_run(**kwargs):
        seen["diff_text"] = kwargs["diff_text"]
        return object(), []

    monkeypatch.setattr(canary_gen_mod, "run_inline_canary", fake_run)
    args = SimpleNamespace(canary=True, git_diff=None)
    cli._dispatch_inline_canary("inline", args, {}, {}, {}, repo)
    return seen["diff_text"]


def _capture_e2e_diff(repo, monkeypatch):
    import code_forge.e2e_check as e2e_mod

    seen = {}

    def fake_run(*, diff_text, repo_root):
        seen["diff_text"] = diff_text
        return [], []

    monkeypatch.setattr(e2e_mod, "run_e2e_check", fake_run)
    args = SimpleNamespace(repo_root=None, diff=None)
    assert cli._run_e2e_check_cmd(args, repo) == 0
    return seen["diff_text"]


@pytest.mark.parametrize("capture", [_capture_canary_diff, _capture_e2e_diff])
def test_prompt_diffs_use_fixed_prefixes_under_noprefix(capture, noprefix_repo, monkeypatch):
    """Canary and e2e-check parse `a/` `b/` paths, which diff.noprefix removes."""
    from code_forge.diff import get_changed_files
    from code_forge.verify import parse_diff_files

    diff_text = capture(noprefix_repo, monkeypatch)
    assert "--- a/f.py" in diff_text
    assert "+++ b/f.py" in diff_text
    assert get_changed_files(diff_text) == ["f.py"]
    assert parse_diff_files(diff_text) == {"f.py": [1]}


@pytest.mark.parametrize("capture", [_capture_canary_diff, _capture_e2e_diff])
def test_prompt_diffs_omit_binary_patches(capture, binary_noprefix_repo, monkeypatch):
    """A binary patch is base85 noise: it must not inflate the model prompt."""
    from code_forge.diff import get_changed_files
    from code_forge.verify import parse_diff_files

    diff_text = capture(binary_noprefix_repo, monkeypatch)
    assert "GIT binary patch" not in diff_text
    assert len(diff_text) < 2000
    # The binary file is still listed and the text file still parses.
    assert get_changed_files(diff_text) == ["blob.bin", "f.py"]
    assert parse_diff_files(diff_text) == {"f.py": [1]}


def _review_diff_with_binary(repo):
    return subprocess.run(
        ["git", "diff", *cli_git._MACHINE_DIFF_OPTIONS, "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_verify_diff_keeps_binary_patch_for_the_review_hash(binary_noprefix_repo, monkeypatch):
    """verify hashes the same text as the review path, which carries --binary."""
    review_diff = _review_diff_with_binary(binary_noprefix_repo)
    assert "GIT binary patch" in review_diff
    monkeypatch.setattr(sys, "argv", ["code-forge", "verify"])
    with patch("code_forge.verify.run_verify", return_value=VerifyResult(True, "ok", 1, 1)) as run_verify:
        assert cli.main() == 0
    assert run_verify.call_args.args[1] == compute_source_hash(git_diff=review_diff)


def test_smoke_receipt_keeps_binary_patch_in_its_hash(binary_noprefix_repo):
    review_diff = _review_diff_with_binary(binary_noprefix_repo)
    args = SimpleNamespace(
        command=[sys.executable, "-c", "print('smoke')"],
        surface="check",
        target="HEAD",
        timeout=5,
    )
    assert cli._handle_smoke_run(args, binary_noprefix_repo) == 0
    receipt = json.loads(next(binary_noprefix_repo.rglob("smoke-receipt-*.json")).read_text())
    assert receipt["diff_sha256"] == compute_source_hash(git_diff=review_diff)
