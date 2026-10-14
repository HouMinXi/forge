# SPDX-License-Identifier: Apache-2.0
"""Real-Git controls for resolved COMMIT diff operands.

Source-only candidate: execution belongs to the admitted qualification owner.
The three moving-ref cases keep the diff helper real; only the capture boundary
is interposed. These controls do not establish immutable measurement authority.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from code_forge import baseline
from code_forge.baseline import EmptyBaseline, GitRefBaseline, SnapshotBaseline
from code_forge.errors import BaselineResolutionError


_BASE_REF = "refs/heads/review-base"
_HEAD_REF = "refs/heads/review-head"
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_PATHS = [Path("calc.py")]


def _git(root, *args, input=None):
    """Run only against a disposable fixture; keep raw diff output intact."""
    return subprocess.run(
        ["git", *args],
        cwd=root,
        input=input,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=10,
    ).stdout


def _oid(root, ref):
    return _git(root, "rev-parse", "--verify", ref).strip()


def _expected_diff(root, base, head, paths=_PATHS, *, replacements=True):
    """Independent Git oracle: no production resolver or diff helper."""
    prefix = () if replacements else ("--no-replace-objects",)
    return _git(
        root, *prefix, "diff", "--no-ext-diff", "--no-textconv", "--no-color",
        "--src-prefix=a/", "--dst-prefix=b/", "--binary", base, head, "--",
        *(str(path) for path in paths),
    )


@pytest.fixture
def repo(tmp_path, monkeypatch):
    # Keep inherited Git settings out of the disposable repositories while
    # retaining the suite's discovery ceiling. Production semantics are not
    # changed; the replacement control uses real refs/replace below.
    for name in tuple(os.environ):
        if name.startswith("GIT_") and name != "GIT_CEILING_DIRECTORIES":
            monkeypatch.delenv(name, raising=False)
    config = tmp_path / "empty.gitconfig"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    template = tmp_path / "template"
    template.mkdir()
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "--quiet", "--template=" + str(template))
    _git(root, "config", "user.name", "Baseline Fixture")
    _git(root, "config", "user.email", "baseline@example.invalid")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "config", "tag.gpgsign", "false")
    _git(root, "config", "core.hooksPath", str(hooks))
    commits = {}
    for label in ("base", "reviewed", "moved"):
        (root / "calc.py").write_text(
            "value = '%s'\n" % label, encoding="utf-8",
        )
        (root / "other.py").write_text(
            "other = '%s'\n" % label, encoding="utf-8",
        )
        _git(root, "add", "calc.py", "other.py")
        _git(root, "commit", "--quiet", "-m", label)
        commits[label] = _oid(root, "HEAD")
    # Neither review ref is the checked-out branch. Moving one cannot
    # implicitly move the other operand through HEAD or an ancestry suffix.
    _git(root, "update-ref", _BASE_REF, commits["base"])
    _git(root, "update-ref", _HEAD_REF, commits["reviewed"])
    return SimpleNamespace(root=root, **commits)


def _move_after_capture(monkeypatch, repo, moving_ref):
    real_resolve = baseline.resolve_git_ref
    events = []

    def capture(ref, cwd):
        resolved = real_resolve(ref, cwd)
        if ref == moving_ref:
            assert not events, "the selected symbolic ref was resolved twice"
            before = (_oid(repo.root, _BASE_REF), _oid(repo.root, _HEAD_REF))
            _git(repo.root, "update-ref", moving_ref, repo.moved, resolved)
            after = (_oid(repo.root, _BASE_REF), _oid(repo.root, _HEAD_REF))
            events.append((resolved, before, after))
        return resolved

    monkeypatch.setattr(baseline, "resolve_git_ref", capture)
    return events


def test_commit_diff_keeps_captured_base_after_ref_move(repo, monkeypatch):
    expected = _expected_diff(repo.root, repo.base, repo.reviewed)
    moved = _expected_diff(repo.root, repo.moved, repo.reviewed)
    assert expected and moved and expected != moved
    assert "-value = 'base'" in expected
    assert "+value = 'reviewed'" in expected
    events = _move_after_capture(monkeypatch, repo, _BASE_REF)

    result = baseline.resolve_baseline(
        GitRefBaseline(_BASE_REF), GitRefBaseline(_HEAD_REF), _PATHS, repo.root,
    )

    assert events == [(
        repo.base, (repo.base, repo.reviewed), (repo.moved, repo.reviewed),
    )]
    assert _oid(repo.root, "HEAD") == repo.moved
    assert (result.base_sha, result.head_sha) == (repo.base, repo.reviewed)
    assert _expected_diff(repo.root, _BASE_REF, _HEAD_REF) == moved
    assert result.git_diff == expected, "resolved base operand was looked up again"
    assert result.target_kind == "COMMIT"
    assert result.source_files == _PATHS and result.diff_paths == ("calc.py",)


def test_commit_diff_keeps_captured_head_after_ref_move(repo, monkeypatch):
    expected = _expected_diff(repo.root, repo.base, repo.reviewed)
    moved = _expected_diff(repo.root, repo.base, repo.moved)
    assert expected and moved and expected != moved
    assert "+value = 'reviewed'" in expected
    assert "+value = 'moved'" in moved
    events = _move_after_capture(monkeypatch, repo, _HEAD_REF)

    result = baseline.resolve_baseline(
        GitRefBaseline(_BASE_REF), GitRefBaseline(_HEAD_REF), _PATHS, repo.root,
    )

    assert events == [(
        repo.reviewed, (repo.base, repo.reviewed), (repo.base, repo.moved),
    )]
    assert _oid(repo.root, "HEAD") == repo.moved
    assert (result.base_sha, result.head_sha) == (repo.base, repo.reviewed)
    assert _expected_diff(repo.root, _BASE_REF, _HEAD_REF) == moved
    assert result.git_diff == expected, "resolved head operand was looked up again"
    assert result.target_kind == "COMMIT"


def test_empty_commit_diff_keeps_captured_head_after_ref_move(repo, monkeypatch):
    expected = _expected_diff(repo.root, _EMPTY_TREE, repo.reviewed)
    moved = _expected_diff(repo.root, _EMPTY_TREE, repo.moved)
    assert expected and moved and expected != moved
    assert "+value = 'reviewed'" in expected
    assert "+value = 'moved'" in moved
    events = _move_after_capture(monkeypatch, repo, _HEAD_REF)

    result = baseline.resolve_baseline(
        EmptyBaseline(), GitRefBaseline(_HEAD_REF), _PATHS, repo.root,
    )

    assert events == [(
        repo.reviewed, (repo.base, repo.reviewed), (repo.base, repo.moved),
    )]
    assert (result.base_sha, result.head_sha) == (_EMPTY_TREE, repo.reviewed)
    assert _git(repo.root, "cat-file", "-t", result.base_sha).strip() == "tree"
    assert _expected_diff(repo.root, _EMPTY_TREE, _HEAD_REF) == moved
    assert result.git_diff == expected, "EMPTY resolved head was looked up again"
    assert result.target_kind == "COMMIT"


@pytest.mark.parametrize("form", [
    "branch", "head-range", "lightweight-tag", "annotated-tag",
    "full-commit", "abbreviated-commit", "annotated-tag-object",
])
def test_commit_ref_forms_preserve_existing_resolution(repo, form):
    if form == "branch":
        base_ref, head_ref = _BASE_REF, _HEAD_REF
    elif form == "head-range":
        base_ref, head_ref = "HEAD~2", "HEAD~1"
    elif form in ("lightweight-tag", "annotated-tag", "annotated-tag-object"):
        for name, oid in (("base-tag", repo.base), ("head-tag", repo.reviewed)):
            if form == "lightweight-tag":
                _git(repo.root, "tag", name, oid)
            else:
                _git(repo.root, "tag", "-a", name, oid, "-m", name)
        base_ref, head_ref = "base-tag", "head-tag"
        if form == "annotated-tag-object":
            base_ref, head_ref = _oid(repo.root, base_ref), _oid(repo.root, head_ref)
            assert _git(repo.root, "cat-file", "-t", head_ref).strip() == "tag"
    elif form == "full-commit":
        base_ref, head_ref = repo.base, repo.reviewed
    else:
        base_ref = _git(repo.root, "rev-parse", "--short=12", repo.base).strip()
        head_ref = _git(repo.root, "rev-parse", "--short=12", repo.reviewed).strip()

    expected = _expected_diff(repo.root, base_ref, head_ref)
    result = baseline.resolve_baseline(
        GitRefBaseline(base_ref), GitRefBaseline(head_ref), _PATHS, repo.root,
    )
    assert expected and result.git_diff == expected
    assert (result.base_sha, result.head_sha) == (repo.base, repo.reviewed)
    assert result.target_kind == "COMMIT"
    assert "other.py" not in result.git_diff
    assert result.source_files == _PATHS and result.diff_paths == ("calc.py",)


@pytest.mark.parametrize("empty", [False, True])
def test_stable_replacement_mapping_remains_honored(repo, empty):
    _git(repo.root, "replace", repo.reviewed, repo.moved)
    base_ref = _EMPTY_TREE if empty else _BASE_REF
    expected = _expected_diff(repo.root, base_ref, _HEAD_REF)
    without_replacement = _expected_diff(
        repo.root, base_ref, _HEAD_REF, replacements=False,
    )
    assert expected and without_replacement and expected != without_replacement
    assert "+value = 'moved'" in expected
    assert "+value = 'reviewed'" in without_replacement
    captured_head = _oid(repo.root, _HEAD_REF + "^{commit}")

    result = baseline.resolve_baseline(
        EmptyBaseline() if empty else GitRefBaseline(_BASE_REF),
        GitRefBaseline(_HEAD_REF), _PATHS, repo.root,
    )

    assert result.git_diff == expected
    assert result.head_sha == captured_head
    assert result.base_sha == (_EMPTY_TREE if empty else repo.base)
    assert result.target_kind == "COMMIT"


def test_unchanged_equal_refs_keep_empty_diff(repo):
    result = baseline.resolve_baseline(
        GitRefBaseline(_HEAD_REF), GitRefBaseline(_HEAD_REF), _PATHS, repo.root,
    )
    assert result.git_diff == ""
    assert (result.base_sha, result.head_sha) == (repo.reviewed, repo.reviewed)
    assert result.target_kind == "COMMIT"


@pytest.mark.parametrize("side", ["base", "head"])
@pytest.mark.parametrize("kind", ["missing", "tree", "blob"])
def test_invalid_commit_refs_keep_resolution_failure(repo, side, kind):
    if kind == "missing":
        invalid = "refs/heads/does-not-exist"
    elif kind == "tree":
        invalid = _oid(repo.root, repo.base + "^{tree}")
    else:
        invalid = _oid(repo.root, repo.base + ":calc.py")
    with pytest.raises(BaselineResolutionError, match="does not resolve"):
        baseline.resolve_baseline(
            GitRefBaseline(invalid if side == "base" else _BASE_REF),
            GitRefBaseline(invalid if side == "head" else _HEAD_REF),
            _PATHS, repo.root,
        )


@pytest.mark.parametrize("empty", [False, True])
def test_diff_failure_propagates_without_ref_retry(repo, monkeypatch, empty):
    # Deliberate failure injection tests propagation only, not native failure.
    real_resolve = baseline.resolve_git_ref
    resolutions = []
    diff_calls = []
    failure = BaselineResolutionError("injected post-resolution diff failure")

    def observe_resolution(ref, cwd):
        resolutions.append(ref)
        return real_resolve(ref, cwd)

    def fail_diff(base, head, paths, cwd):
        diff_calls.append((base, head, tuple(paths), cwd))
        raise failure

    monkeypatch.setattr(baseline, "resolve_git_ref", observe_resolution)
    monkeypatch.setattr(baseline, "git_diff", fail_diff)
    with pytest.raises(BaselineResolutionError) as caught:
        baseline.resolve_baseline(
            EmptyBaseline() if empty else GitRefBaseline(_BASE_REF),
            GitRefBaseline(_HEAD_REF), _PATHS, repo.root,
        )
    assert caught.value is failure
    assert resolutions == ([_HEAD_REF] if empty else [_BASE_REF, _HEAD_REF])
    assert diff_calls == [(
        _EMPTY_TREE if empty else repo.base, repo.reviewed, tuple(_PATHS), repo.root,
    )]


@pytest.mark.parametrize("empty", [False, True])
def test_working_preserves_tracked_and_untracked_content(repo, empty):
    (repo.root / "calc.py").write_text("value = 'working'\n", encoding="utf-8")
    (repo.root / "untracked.py").write_text("untracked = True\n", encoding="utf-8")
    result = baseline.resolve_baseline(
        EmptyBaseline() if empty else GitRefBaseline(_BASE_REF),
        GitRefBaseline("WORKING"), [Path(".")], repo.root,
    )
    assert result.target_kind == "WORKING"
    assert (result.base_sha, result.head_sha) == (
        _EMPTY_TREE if empty else repo.base, repo.moved,
    )
    assert "+value = 'working'" in result.git_diff
    assert "+untracked = True" in result.git_diff


@pytest.mark.parametrize("empty", [False, True])
def test_index_preserves_staged_unstaged_separation(repo, empty):
    source = repo.root / "calc.py"
    source.write_text("value = 'staged'\n", encoding="utf-8")
    _git(repo.root, "add", "calc.py")
    source.write_text("value = 'unstaged'\n", encoding="utf-8")
    result = baseline.resolve_baseline(
        EmptyBaseline() if empty else GitRefBaseline(_BASE_REF),
        GitRefBaseline("INDEX"), _PATHS, repo.root,
    )
    assert result.target_kind == "INDEX"
    assert (result.base_sha, result.head_sha) == (
        _EMPTY_TREE if empty else repo.base, repo.moved,
    )
    assert "+value = 'staged'" in result.git_diff
    assert "unstaged" not in result.git_diff


def test_empty_without_head_retains_no_diff(repo):
    result = baseline.resolve_baseline(EmptyBaseline(), None, _PATHS, repo.root)
    assert result.mode_hint == "git" and result.git_diff is None
    assert result.base_sha is None and result.head_sha is None
    assert result.target_kind is None


def test_non_git_empty_and_missing_snapshot_keep_fallback(tmp_path):
    root = tmp_path / "not-a-repository"
    root.mkdir()
    for spec in (EmptyBaseline(), SnapshotBaseline(root / "missing.json")):
        result = baseline.resolve_baseline(spec, None, _PATHS, root)
        assert result.mode_hint == "non-git" and result.git_diff is None
        assert result.baseline_content is None
        assert result.base_sha is None and result.head_sha is None
    with pytest.raises(BaselineResolutionError, match="only valid in a git repo"):
        baseline.resolve_baseline(
            EmptyBaseline(), GitRefBaseline("HEAD"), _PATHS, root,
        )


def test_committed_resolution_uses_linked_worktree_cwd(repo, tmp_path):
    linked = tmp_path / "linked"
    _git(repo.root, "worktree", "add", "--quiet", "--detach", str(linked), repo.reviewed)
    try:
        expected = _expected_diff(linked, repo.base, repo.reviewed)
        result = baseline.resolve_baseline(
            GitRefBaseline(_BASE_REF), GitRefBaseline("HEAD"), _PATHS, linked,
        )
        assert expected and result.git_diff == expected
        assert (result.base_sha, result.head_sha) == (repo.base, repo.reviewed)
        assert _oid(repo.root, "HEAD") == repo.moved
        assert _oid(linked, "HEAD") == repo.reviewed
    finally:
        _git(repo.root, "worktree", "remove", "--force", str(linked))
