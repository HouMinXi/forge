# SPDX-License-Identifier: Apache-2.0
"""Exercise the snapshot guard against disposable Git repositories."""

import hashlib
import importlib.util
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def suite_guard():
    path = Path(__file__).with_name("conftest.py")
    spec = importlib.util.spec_from_file_location("snapshot_guard_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path, monkeypatch):
    # Use isolated config; never invoke developer hooks or signing tools.
    for name in tuple(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    config = tmp_path / "empty.gitconfig"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "--quiet")
    _git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "--quiet", "--allow-empty", "-m", "fixture")
    return root


@pytest.mark.parametrize("layout", ["normal", "worktree", "absolute-hooks", "relative-hooks"])
def test_snapshot_tracks_the_hooks_git_uses(repository, layout, suite_guard):
    root = repository
    if layout == "worktree":
        root = repository.parent / "linked"
        _git(repository, "worktree", "add", "--quiet", "--detach", str(root))
    elif layout.endswith("hooks"):
        hooks = repository.parent / "configured hooks"
        hooks.mkdir()
        configured = str(hooks) if layout == "absolute-hooks" else "../configured hooks"
        _git(root, "config", "core.hooksPath", configured)
    hooks = Path(_git(root, "rev-parse", "--path-format=absolute", "--git-path", "hooks"))
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    before = suite_guard._snapshot_git_state(root)
    assert before["hooks"]["pre-commit"] == hashlib.sha256(hook.read_bytes()).hexdigest()
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    after = suite_guard._snapshot_git_state(root)
    assert after["hooks"]["pre-commit"] == hashlib.sha256(hook.read_bytes()).hexdigest()
    assert before["hooks"] != after["hooks"]


@pytest.mark.parametrize("query", ["config", "hooks", "refs"])
def test_snapshot_propagates_query_failure(repository, monkeypatch, suite_guard, query):
    run = subprocess.run

    def fail_query(args, **kwargs):
        matches = (
            (query == "config" and args[1] == "config")
            or (query == "hooks" and args[1] == "rev-parse" and "HEAD" not in args)
            or (query == "refs" and args[1] == "for-each-ref")
        )
        if matches:
            result = subprocess.CompletedProcess(args, 128, "", "injected Git failure")
            if kwargs.get("check"):
                result.check_returncode()
            return result
        return run(args, **kwargs)

    monkeypatch.setattr(suite_guard.subprocess, "run", fail_query)
    with pytest.raises(subprocess.CalledProcessError) as error:
        suite_guard._snapshot_git_state(repository)
    assert error.value.returncode == 128
    assert error.value.stderr == "injected Git failure"


def test_snapshot_does_not_accept_failed_git_query(repository, monkeypatch, suite_guard):
    not_a_repo = repository / "not-a-repo"
    not_a_repo.mkdir()
    # Deliberately nest under a real repo, then stop Git before it reaches it.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(repository))
    with pytest.raises(subprocess.CalledProcessError) as error:
        suite_guard._snapshot_git_state(not_a_repo)
    assert error.value.returncode == 128


def test_sessionstart_skips_source_copy_without_git(tmp_path, monkeypatch, suite_guard):
    root = tmp_path / "source-copy"
    (root / "tests").mkdir(parents=True)
    monkeypatch.setattr(suite_guard, "__file__", str(root / "tests" / "conftest.py"))
    session = SimpleNamespace(config=SimpleNamespace(stash={}))
    suite_guard.pytest_sessionstart(session)
    assert suite_guard._git_snapshot_key not in session.config.stash
    suite_guard.pytest_sessionfinish(session, 0)


def test_snapshot_accepts_unborn_branch(repository, suite_guard):
    root = repository.parent / "unborn"
    root.mkdir()
    _git(root, "init", "--quiet")
    first = suite_guard._snapshot_git_state(root)
    assert first["HEAD"] == "ref: " + _git(root, "symbolic-ref", "HEAD")
    assert first["refs_heads"] == ""


def test_snapshot_tracks_detached_head(repository, suite_guard):
    _git(repository, "checkout", "--quiet", "--detach")
    assert suite_guard._snapshot_git_state(repository)["HEAD"] == _git(repository, "rev-parse", "HEAD")


def test_sessionstart_rejects_broken_git_marker(tmp_path, monkeypatch, suite_guard):
    root = tmp_path / "broken"
    (root / "tests").mkdir(parents=True)
    (root / ".git").write_text("gitdir: missing\n", encoding="utf-8")
    monkeypatch.setattr(suite_guard, "__file__", str(root / "tests" / "conftest.py"))
    session = SimpleNamespace(config=SimpleNamespace(stash={}))
    with pytest.raises(subprocess.CalledProcessError):
        suite_guard.pytest_sessionstart(session)
    assert suite_guard._git_snapshot_key not in session.config.stash


@pytest.mark.parametrize("change", ["new", "removed", "content changed"])
def test_sessionfinish_rejects_shared_hook_drift(
    repository, monkeypatch, capsys, suite_guard, change,
):
    root = repository.parent / "linked"
    _git(repository, "worktree", "add", "--quiet", "--detach", str(root))
    (root / "tests").mkdir()
    monkeypatch.setattr(suite_guard, "__file__", str(root / "tests" / "conftest.py"))
    hook = repository / ".git" / "hooks" / "pre-commit"
    if change != "new":
        hook.write_text("original\n", encoding="utf-8")
    session = SimpleNamespace(config=SimpleNamespace(stash={}))
    suite_guard.pytest_sessionstart(session)
    if change == "removed":
        hook.unlink()
    else:
        hook.write_text("changed\n", encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        suite_guard.pytest_sessionfinish(session, 0)
    assert error.value.code == 1
    assert f"pre-commit ({change})" in capsys.readouterr().err
