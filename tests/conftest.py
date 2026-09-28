# SPDX-License-Identifier: Apache-2.0
import ast
import difflib
import hashlib
import inspect
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

import code_forge.mutation

_git_snapshot_key = pytest.StashKey[dict]()

# mutmut injects a trampoline import into every mutated file. Importing that
# module calls Config.get(), which walks cwd for setup.cfg / src / lib. A CLI
# subprocess started from a scratch git repo has none of those and dies on
# FileNotFoundError before the command runs. Plant a marked cfg + empty src/
# so the guess succeeds and the subprocess can start.
_MUTMUT_SCRATCH_CFG = "# managed-by-code-forge-mutation\n[mutmut]\nsource_paths=src\n"


def plant_mutmut_cfg(root: Path) -> None:
    """Stop a mutated import from guessing source_paths off an empty cwd."""
    (root / "src").mkdir(exist_ok=True)
    cfg = root / "setup.cfg"
    if not cfg.exists():
        cfg.write_text(_MUTMUT_SCRATCH_CFG, encoding="utf-8")


def _detach_prologue() -> str:
    """Recover the launcher's fork/_exit prologue from the product source.

    Hard-coding a copy here lets the two drift apart in silence: change the
    prologue's spacing in mutation.py and this file keeps matching nothing,
    the strip becomes a no-op, and every exec'd payload forks the pytest
    session again. Read the literal out of the module instead so a drift
    turns into a loud failure rather than a silent one.
    """
    source = inspect.getsource(code_forge.mutation.launch_detached_mutation)
    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "os.fork()" in node.value and "os._exit" in node.value:
                return node.value
    raise AssertionError(
        "launch_detached_mutation no longer carries a fork/_exit prologue "
        "literal; update _run_detached_payload to match the new shape"
    )


def _run_detached_payload(script: str, globals_dict: dict | None = None) -> None:
    """Run a launcher-generated script without forking the test session.

    ``launch_detached_mutation`` prepends a fork/_exit prologue so the real run
    can reparent to init. Executing that prologue inside pytest forks the
    session itself: the child carries on through the remaining tests, writing
    to the same stdout and the same .git as the parent, and the parent's own
    report never appears. Strip the prologue and run only the payload.
    """
    prologue = _detach_prologue()
    assert script.startswith(prologue), (
        "payload does not start with the prologue read from mutation.py; "
        "exec would fork the pytest session"
    )
    script = script[len(prologue) :]
    assert "os._exit" not in script, "detach prologue still present; exec would fork pytest"
    exec(compile(script, "<detached-mutation>", "exec"), globals_dict or {})  # noqa: S102


@pytest.fixture
def run_detached_payload():
    """Expose :func:`run_detached_payload` without a cross-file import."""
    return _run_detached_payload


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
    monkeypatch.setattr("code_forge.user_config.load_user_backends", lambda: {})


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


def _git_output(repo_root, *args):
    """Reject a failed Git query instead of snapshotting empty output."""
    return subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout


def _foreign_branches(repo_root):
    """Branches checked out where this suite never writes.

    The repository is shared by parallel sessions, each in its own worktree;
    their branches come and go while the suite runs. The suite only makes
    worktrees under the temp directory, so a branch checked out in any other
    worktree -- including ones nested inside this checkout -- was not made by
    it. The branch of this checkout itself is covered by the HEAD check.
    """
    own_root = repo_root.resolve()
    scratch = Path(tempfile.gettempdir()).resolve()
    foreign = set()
    path = None
    listing = _git_output(repo_root, "worktree", "list", "--porcelain")
    for line in listing.splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree ") :]).resolve()
        elif line.startswith("branch ") and path is not None:
            if path != own_root and scratch not in path.parents:
                foreign.add(line[len("branch ") :])
    return foreign


def _branch_changes(before, after):
    """Split refs/heads changes into this suite's and other sessions'."""

    def heads(snap):
        return dict(line.split(" ", 1) for line in snap["refs_heads"].splitlines())

    old, new = heads(before), heads(after)
    foreign = before["foreign_branches"] | after["foreign_branches"]
    changed = {ref for ref in old.keys() | new.keys() if old.get(ref) != new.get(ref)}
    return changed - foreign, changed & foreign


def _without_branch_config(config, branches):
    """Drop branch.<name>.* entries that belong to the given refs/heads refs."""
    prefixes = tuple(f"branch.{ref[len('refs/heads/') :]}." for ref in branches)
    return "".join(line for line in config.splitlines(keepends=True) if not line.startswith(prefixes))


def _snapshot_git_state(repo_root):
    """Capture repository state using Git's worktree-aware paths."""
    snap: dict = {"config": _git_output(repo_root, "config", "--list", "--local")}

    hooks_dir = Path(
        _git_output(
            repo_root,
            "rev-parse",
            "--path-format=absolute",
            "--git-path",
            "hooks",
        ).strip()
    )
    hooks = {}
    if hooks_dir.is_dir():
        for f in sorted(hooks_dir.iterdir()):
            if f.is_file() and not f.name.endswith(".sample"):
                hooks[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()
    snap["hooks"] = hooks

    snap["refs_heads"] = _git_output(
        repo_root,
        "for-each-ref",
        "refs/heads/",
        "--format=%(refname) %(objectname)",
    )
    snap["foreign_branches"] = _foreign_branches(repo_root)
    try:
        snap["HEAD"] = _git_output(repo_root, "rev-parse", "--verify", "HEAD").strip()
    except subprocess.CalledProcessError:
        # An initialized repository may have no commits; retain its branch.
        snap["HEAD"] = "ref: " + _git_output(repo_root, "symbolic-ref", "HEAD").strip()

    dotfiles = {}
    for item in sorted(repo_root.glob(".git*")):
        if item.is_file() and item.name != ".git":
            dotfiles[item.name] = hashlib.sha256(item.read_bytes()).hexdigest()
    snap["dotfiles"] = dotfiles

    return snap


def pytest_configure(config):
    """Clear inherited Git settings; tests can set their own after configure."""
    isolation = pytest.MonkeyPatch()
    config.add_cleanup(isolation.undo)
    for name in tuple(os.environ):
        if name.startswith("GIT_"):
            isolation.delenv(name, raising=False)


def pytest_sessionstart(session):
    repo_root = Path(__file__).resolve().parent.parent
    # Mutation mirrors and source archives deliberately omit repository metadata.
    # A broken .git marker still reaches Git and must fail rather than be ignored.
    if not os.path.lexists(repo_root / ".git"):
        return
    session.config.stash[_git_snapshot_key] = _snapshot_git_state(repo_root)


def pytest_sessionfinish(session, exitstatus):
    repo_root = Path(__file__).resolve().parent.parent
    before = session.config.stash.get(_git_snapshot_key, None)
    if before is None:
        return

    after = _snapshot_git_state(repo_root)
    diffs = []

    own, foreign = _branch_changes(before, after)
    for ref in sorted(foreign):
        sys.stderr.write(f"note: {ref} changed in another session's worktree\n")

    compared = {
        "config": (
            _without_branch_config(before["config"], foreign),
            _without_branch_config(after["config"], foreign),
        ),
        "refs_heads": (before["refs_heads"], after["refs_heads"] if own else before["refs_heads"]),
        "HEAD": (before["HEAD"], after["HEAD"]),
    }
    for field, (old_text, new_text) in compared.items():
        if old_text != new_text:
            diff = difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=f".git {field} BEFORE",
                tofile=f".git {field} AFTER",
            )
            diffs.append(f"Changed: .git/{field}\n" + "".join(diff))

    if before["hooks"] != after["hooks"]:
        added = set(after["hooks"]) - set(before["hooks"])
        removed = set(before["hooks"]) - set(after["hooks"])
        changed = {
            k
            for k in set(before["hooks"]) & set(after["hooks"])
            if before["hooks"][k] != after["hooks"][k]
        }
        parts = [f"  + {k} (new)" for k in sorted(added)]
        parts.extend(f"  - {k} (removed)" for k in sorted(removed))
        parts.extend(f"  ~ {k} (content changed)" for k in sorted(changed))
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
