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
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
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
    _git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--quiet",
        "--allow-empty",
        "-m",
        "fixture",
    )
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
    repository,
    monkeypatch,
    capsys,
    suite_guard,
    change,
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
    suite_guard.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 1
    assert f"pre-commit ({change})" in session.config.stash[suite_guard._git_failure_key]


def _suite_checkout(repository, tmp_path, monkeypatch, suite_guard):
    """Run the guard from a linked worktree while other checkouts share refs."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(suite_guard.tempfile, "gettempdir", lambda: str(scratch))
    root = tmp_path / "suite"
    _git(repository, "worktree", "add", "--quiet", "--detach", str(root))
    (root / "tests").mkdir()
    monkeypatch.setattr(suite_guard, "__file__", str(root / "tests" / "conftest.py"))
    session = SimpleNamespace(config=SimpleNamespace(stash={}, option=SimpleNamespace(basetemp=None)))
    return scratch, session


def _commit(root):
    _git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--quiet",
        "--allow-empty",
        "-m",
        "other session",
    )


@pytest.mark.parametrize("nested", [False, True], ids=["sibling", "nested"])
@pytest.mark.parametrize("change", ["created", "moved", "deleted"])
def test_sessionfinish_ignores_branch_of_another_checkout(
    repository,
    tmp_path,
    monkeypatch,
    capsys,
    suite_guard,
    change,
    nested,
):
    _, session = _suite_checkout(repository, tmp_path, monkeypatch, suite_guard)
    parent = tmp_path / "suite" / ".worktrees" if nested else tmp_path / "sessions"
    other = parent / "other"
    base = "origin-main"
    _git(repository, "branch", "--quiet", base)
    _git(repository, "config", f"branch.{base}.remote", ".")
    _git(repository, "config", f"branch.{base}.merge", f"refs/heads/{base}")
    add = ("worktree", "add", "--quiet", "--track", "-b", "other-line", str(other), base)
    if change != "created":
        _git(repository, *add)
    suite_guard.pytest_sessionstart(session)
    if change == "created":
        _git(repository, *add)
    elif change == "moved":
        _commit(other)
    else:
        _git(repository, "worktree", "remove", str(other))
        _git(repository, "branch", "--quiet", "-D", "other-line")
    assert ("branch.other-line.merge" in _git(repository, "config", "--list", "--local")) == (
        change != "deleted"
    )
    suite_guard.pytest_sessionfinish(session, 0)
    err = capsys.readouterr().err
    assert "FATAL" not in err
    assert "refs/heads/other-line" in err


@pytest.mark.parametrize("change", ["branch", "scratch-worktree"])
def test_sessionfinish_rejects_branch_the_suite_could_have_made(
    repository,
    tmp_path,
    monkeypatch,
    capsys,
    suite_guard,
    change,
):
    scratch, session = _suite_checkout(repository, tmp_path, monkeypatch, suite_guard)
    suite_guard.pytest_sessionstart(session)
    if change == "branch":
        _git(repository, "branch", "stray")
    else:
        _git(repository, "worktree", "add", "--quiet", "-b", "stray", str(scratch / "wt"))
    suite_guard.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 1
    assert "+refs/heads/stray" in session.config.stash[suite_guard._git_failure_key]


_SUITE_ID = "forge-suite-under-test@suite.invalid"


@pytest.mark.parametrize("change", ["created", "moved"])
def test_sessionfinish_rejects_branch_carrying_the_session_identity(
    repository,
    tmp_path,
    monkeypatch,
    suite_guard,
    change,
):
    """#184: a ref update the reflog attributes to this session is fatal."""
    _, session = _suite_checkout(repository, tmp_path, monkeypatch, suite_guard)
    if change == "moved":
        _git(repository, "branch", "stray")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", _SUITE_ID)
    suite_guard.pytest_sessionstart(session)
    if change == "created":
        _git(repository, "branch", "stray")
    else:
        _git(
            repository,
            "update-ref",
            "refs/heads/stray",
            _git(
                repository,
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@example.invalid",
                "commit-tree",
                "-p",
                "HEAD",
                "-m",
                "x",
                "HEAD^{tree}",
            ),
        )
    suite_guard.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 1
    assert "refs/heads/stray" in session.config.stash[suite_guard._git_failure_key]


@pytest.mark.parametrize("change", ["created", "moved"])
def test_sessionfinish_notes_branch_made_by_another_identity(
    repository,
    tmp_path,
    monkeypatch,
    capsys,
    suite_guard,
    change,
):
    """#184: another session's branch that is not checked out anywhere (a
    created branch plus its tracking config) is a note, not a failure."""
    _, session = _suite_checkout(repository, tmp_path, monkeypatch, suite_guard)
    if change == "moved":
        _git(repository, "branch", "other")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", _SUITE_ID)
    suite_guard.pytest_sessionstart(session)
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "other-session@example.invalid")
    if change == "created":
        _git(repository, "branch", "other")
    else:
        _git(
            repository,
            "update-ref",
            "refs/heads/other",
            _git(
                repository,
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@example.invalid",
                "commit-tree",
                "-p",
                "HEAD",
                "-m",
                "x",
                "HEAD^{tree}",
            ),
        )
    _git(repository, "config", "branch.other.remote", "origin")
    suite_guard.pytest_sessionfinish(session, 0)
    assert getattr(session, "exitstatus", 0) == 0
    assert suite_guard._git_failure_key not in session.config.stash
    assert "refs/heads/other changed in another session" in capsys.readouterr().err


def test_guard_failure_is_printed_in_the_terminal_summary(suite_guard):
    """#184: the failure is reported after pytest's own summary, not by sys.exit."""
    written = []
    reporter = SimpleNamespace(write_sep=lambda *a, **k: written.append(a), write=written.append)
    config = SimpleNamespace(
        stash={suite_guard._git_failure_key: "FATAL: Test suite modified real .git state!\n"}
    )
    suite_guard.pytest_terminal_summary(reporter, 1, config)
    assert any("FATAL: Test suite modified real .git state!" in str(w) for w in written)


def test_sessionfinish_notes_branch_deleted_by_another_session(
    repository, tmp_path, monkeypatch, capsys, suite_guard
):
    """#196: a branch another session last wrote and then deletes mid-run
    (no reflog left) is a note, not a failure."""
    _, session = _suite_checkout(repository, tmp_path, monkeypatch, suite_guard)
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "other-session@example.invalid")
    _git(repository, "branch", "victim")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", _SUITE_ID)
    suite_guard.pytest_sessionstart(session)
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "other-session@example.invalid")
    _git(repository, "branch", "-D", "victim")
    suite_guard.pytest_sessionfinish(session, 0)
    assert getattr(session, "exitstatus", 0) == 0
    assert "refs/heads/victim changed in another session" in capsys.readouterr().err


def test_sessionfinish_rejects_deleting_a_branch_this_session_wrote(
    repository, tmp_path, monkeypatch, suite_guard
):
    _, session = _suite_checkout(repository, tmp_path, monkeypatch, suite_guard)
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", _SUITE_ID)
    _git(repository, "branch", "mine")
    suite_guard.pytest_sessionstart(session)
    _git(repository, "branch", "-D", "mine")
    suite_guard.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 1


def test_sessionfinish_rejects_test_overriding_only_the_committer_email(
    repository, tmp_path, monkeypatch, suite_guard
):
    """#196 P3: the suite's committer name still marks the write as ours."""
    _, session = _suite_checkout(repository, tmp_path, monkeypatch, suite_guard)
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", _SUITE_ID)
    monkeypatch.setenv("GIT_COMMITTER_NAME", suite_guard._SUITE_COMMITTER_NAME)
    suite_guard.pytest_sessionstart(session)
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "a-test-of-its-own@example.invalid")
    _git(repository, "branch", "stray")
    suite_guard.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 1
    assert "+refs/heads/stray" in session.config.stash[suite_guard._git_failure_key]


def test_reflog_query_timeout_degrades_to_the_suites_change(
    repository, tmp_path, monkeypatch, suite_guard
):
    """A hung reflog query neither crashes the session nor hides a change:
    the writer is unknown, so the change stays fatal."""
    _, session = _suite_checkout(repository, tmp_path, monkeypatch, suite_guard)
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", _SUITE_ID)
    suite_guard.pytest_sessionstart(session)
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "other-session@example.invalid")
    _git(repository, "branch", "other")
    real = suite_guard._git_output

    def slow(root, *args):
        if args[:2] == ("log", "-g"):
            raise subprocess.TimeoutExpired(["git", *args], 10)
        return real(root, *args)

    monkeypatch.setattr(suite_guard, "_git_output", slow)
    assert suite_guard._last_writer(repository, "refs/heads/other") is None
    suite_guard.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 1


def test_lookup_timeout_on_existing_ref_ignores_saved_foreign_writer(
    repository, tmp_path, monkeypatch, suite_guard
):
    """#43: the sessionstart writer stands in only for a ref that is gone.
    An existing ref another session wrote at start, then moved by the suite,
    whose reflog lookup times out at finish, stays the suite's (fatal)."""
    _, session = _suite_checkout(repository, tmp_path, monkeypatch, suite_guard)
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "other-session@example.invalid")
    _git(repository, "branch", "shared")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", _SUITE_ID)
    suite_guard.pytest_sessionstart(session)
    assert (
        session.config.stash[suite_guard._git_snapshot_key]["writers"]["refs/heads/shared"][1]
        == "other-session@example.invalid"
    )
    _git(
        repository,
        "update-ref",
        "refs/heads/shared",
        _git(
            repository,
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@example.invalid",
            "commit-tree",
            "-p",
            "HEAD",
            "-m",
            "x",
            "HEAD^{tree}",
        ),
    )
    real = suite_guard._git_output

    def slow(root, *args):
        if args[:2] == ("log", "-g"):
            raise subprocess.TimeoutExpired(["git", *args], 10)
        return real(root, *args)

    monkeypatch.setattr(suite_guard, "_git_output", slow)
    suite_guard.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 1


def test_scrubbed_env_write_is_a_documented_note(repository, tmp_path, monkeypatch, capsys, suite_guard):
    """#201 pin: a branch written with every GIT_* variable dropped carries no
    session marker, so it is attributed to another session (documented gap in
    _ref_made_by). If this ever becomes fatal, update the docstring."""
    _, session = _suite_checkout(repository, tmp_path, monkeypatch, suite_guard)
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", _SUITE_ID)
    monkeypatch.setenv("GIT_COMMITTER_NAME", suite_guard._SUITE_COMMITTER_NAME)
    suite_guard.pytest_sessionstart(session)
    scrubbed = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    scrubbed.update(HOME=str(tmp_path), GIT_CONFIG_NOSYSTEM="1")
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=box",
            "-c",
            "user.email=box@example.invalid",
            "branch",
            "scrubbed",
        ],
        env=scrubbed,
        check=True,
        capture_output=True,
    )
    suite_guard.pytest_sessionfinish(session, 0)
    assert getattr(session, "exitstatus", 0) == 0
    assert "refs/heads/scrubbed changed in another session" in capsys.readouterr().err
    assert "#201" in suite_guard._ref_made_by.__doc__


def test_scrubbed_env_bare_git_from_repo_cwd_is_a_documented_note(
    repository, tmp_path, monkeypatch, capsys, suite_guard
):
    """#201 addendum: scrubbing GIT_* drops GIT_CEILING_DIRECTORIES too, so a
    bare ``git branch`` (no -C) from the repo-root cwd reaches the real
    repository. It is still only a note (documented in _ref_made_by)."""
    _, session = _suite_checkout(repository, tmp_path, monkeypatch, suite_guard)
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", _SUITE_ID)
    monkeypatch.setenv("GIT_COMMITTER_NAME", suite_guard._SUITE_COMMITTER_NAME)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(repository.parent))
    suite_guard.pytest_sessionstart(session)
    scrubbed = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    assert "GIT_CEILING_DIRECTORIES" not in scrubbed
    scrubbed.update(HOME=str(tmp_path), GIT_CONFIG_NOSYSTEM="1")
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=box",
            "-c",
            "user.email=box@example.invalid",
            "branch",
            "bare-scrubbed",
        ],
        env=scrubbed,
        cwd=str(repository),
        check=True,
        capture_output=True,
    )
    suite_guard.pytest_sessionfinish(session, 0)
    assert getattr(session, "exitstatus", 0) == 0
    assert "refs/heads/bare-scrubbed changed in another session" in capsys.readouterr().err
    doc = suite_guard._ref_made_by.__doc__
    assert "bare ``git branch x``" in doc and "names the real repository explicitly" not in doc


def test_guard_failure_follows_pytests_summary_in_a_real_session(tmp_path):
    """#184/#201: in a real pytest run the guard reports inside pytest's
    terminal summary (after the test results, before the final stats line)
    and the process exits 1."""
    import shutil
    import sys

    root = tmp_path / "proj"
    (root / "tests").mkdir(parents=True)
    shutil.copy(Path(__file__).with_name("conftest.py"), root / "tests" / "conftest.py")
    (root / "tests" / "test_stray.py").write_text(
        "import subprocess, pathlib\n"
        "def test_writes_real_repo():\n"
        "    root = pathlib.Path(__file__).resolve().parents[1]\n"
        "    subprocess.run(['git', '-C', str(root), 'branch', 'stray'], check=True)\n",
        encoding="utf-8",
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(HOME=str(tmp_path), GIT_CONFIG_NOSYSTEM="1")
    for args in (
        ["init", "-q"],
        [
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@e.invalid",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "base",
        ],
    ):
        subprocess.run(["git", "-C", str(root), *args], env=env, check=True, capture_output=True)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist", "tests"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    text = out.stdout + out.stderr
    assert out.returncode == 1, text
    assert "1 passed" in out.stdout, text
    assert "+refs/heads/stray" in out.stdout, text
    # Hook order: the test outcome is reported first, the guard's section is
    # printed from pytest_terminal_summary (no sys.exit cut-off), and pytest's
    # final stats line still follows it.
    section = out.stdout.index("real .git state changed")
    assert out.stdout.index("[100%]") < section < out.stdout.rindex("1 passed"), text
