# SPDX-License-Identifier: Apache-2.0
import difflib
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

_git_snapshot_key = pytest.StashKey[dict]()


@pytest.fixture(autouse=True)
def _skip_worktree_check(monkeypatch):
    """All tests bypass the linked-worktree enforcement gate."""
    monkeypatch.setenv("FORGE_SKIP_WORKTREE_CHECK", "1")


@pytest.fixture(autouse=True)
def _isolate_user_config(monkeypatch):
    """Prevent user-level backends from leaking into tests.

    Without this, ~/.config/code-forge/config.yaml backends get merged
    into every test's gate.yaml via _merge_user_into, and their
    api_key_env requirements cause preflight failures in CI and on
    machines where the keys are not exported.
    """
    monkeypatch.setattr(
        "code_forge.user_config.load_user_backends", lambda: {}
    )


@pytest.fixture(autouse=True, scope="session")
def _git_isolation():
    """Block git from discovering the real repo .git via directory walk-up."""
    repo_root = Path(__file__).resolve().parent.parent
    original = os.environ.get("GIT_CEILING_DIRECTORIES")
    os.environ["GIT_CEILING_DIRECTORIES"] = str(repo_root.parent)
    yield
    if original is None:
        os.environ.pop("GIT_CEILING_DIRECTORIES", None)
    else:
        os.environ["GIT_CEILING_DIRECTORIES"] = original


def _snapshot_git_state(repo_root):
    """Capture .git state for drift comparison."""
    snap = {}

    result = subprocess.run(
        ["git", "config", "--list", "--local"],
        cwd=str(repo_root), capture_output=True, text=True, timeout=10,
    )
    snap["config"] = result.stdout

    try:
        git_dir_result = subprocess.run(
            ["git", "rev-parse", "--absolute-git-dir"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=10,
        )
        git_dir = Path(git_dir_result.stdout.strip())
    except Exception:
        git_dir = repo_root / ".git"

    hooks_dir = git_dir / "hooks"
    hooks = {}
    if hooks_dir.is_dir():
        for f in sorted(hooks_dir.iterdir()):
            if f.is_file() and not f.name.endswith(".sample"):
                hooks[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()
    snap["hooks"] = hooks

    result = subprocess.run(
        ["git", "for-each-ref", "refs/heads/",
         "--format=%(refname) %(objectname)"],
        cwd=str(repo_root), capture_output=True, text=True, timeout=10,
    )
    snap["refs_heads"] = result.stdout

    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_root), capture_output=True, text=True, timeout=10,
    )
    snap["HEAD"] = result.stdout.strip()

    dotfiles = {}
    for item in sorted(repo_root.glob(".git*")):
        if item.is_file() and item.name != ".git":
            dotfiles[item.name] = hashlib.sha256(item.read_bytes()).hexdigest()
    snap["dotfiles"] = dotfiles

    return snap


def pytest_sessionstart(session):
    repo_root = Path(__file__).resolve().parent.parent
    session.config.stash[_git_snapshot_key] = _snapshot_git_state(repo_root)


def pytest_sessionfinish(session, exitstatus):
    repo_root = Path(__file__).resolve().parent.parent
    before = session.config.stash.get(_git_snapshot_key, None)
    if before is None:
        return

    after = _snapshot_git_state(repo_root)
    diffs = []

    for field in ("config", "refs_heads", "HEAD"):
        if before[field] != after[field]:
            diff = difflib.unified_diff(
                before[field].splitlines(keepends=True),
                after[field].splitlines(keepends=True),
                fromfile=f".git {field} BEFORE",
                tofile=f".git {field} AFTER",
            )
            diffs.append(f"Changed: .git/{field}\n" + "".join(diff))

    if before["hooks"] != after["hooks"]:
        added = set(after["hooks"]) - set(before["hooks"])
        removed = set(before["hooks"]) - set(after["hooks"])
        changed = {
            k for k in set(before["hooks"]) & set(after["hooks"])
            if before["hooks"][k] != after["hooks"][k]
        }
        parts = []
        for k in sorted(added):
            parts.append(f"  + {k} (new)")
        for k in sorted(removed):
            parts.append(f"  - {k} (removed)")
        for k in sorted(changed):
            parts.append(f"  ~ {k} (content changed)")
        diffs.append("Changed: .git/hooks/\n" + "\n".join(parts))

    if before["dotfiles"] != after["dotfiles"]:
        for k in sorted(set(before["dotfiles"]) | set(after["dotfiles"])):
            b = before["dotfiles"].get(k)
            a = after["dotfiles"].get(k)
            if b != a:
                if b is None:
                    diffs.append(f"Changed: {k} (new file)")
                elif a is None:
                    diffs.append(f"Changed: {k} (removed)")
                else:
                    diffs.append(f"Changed: {k} (content changed)")

    if diffs:
        msg = (
            "FATAL: Test suite modified real .git state!\n"
            + "\n".join(diffs)
            + "\n\nReal .git has been altered. Clean up before continuing.\n"
        )
        sys.stderr.write(msg)
        sys.exit(1)
