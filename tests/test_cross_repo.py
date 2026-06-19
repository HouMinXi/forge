# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for cross-repo diff acquisition and context assembly.

Uses real git repos with tmp_path + monkeypatch GIT_CEILING_DIRECTORIES
to prevent test repos from leaking into the parent repo's git state.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from code_forge.cross_repo import (
    build_cross_repo_context,
    derive_source_files,
    get_sibling_diff,
    make_per_repo_cwd,
)
from code_forge.errors import BaselineResolutionError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a real git repo with main and feature branches.

    main:    file.py = "x = 1\\n"
    feature: file.py = "x = 2\\n"
    HEAD left on main after setup.
    """
    monkeypatch.setenv(
        "GIT_CEILING_DIRECTORIES",
        str(tmp_path.parent),
        prepend=os.pathsep,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *cmd: subprocess.run(  # noqa: E731
        list(cmd), cwd=repo, check=True,
        capture_output=True, text=True,
    )
    run("git", "init", "-b", "main")
    run("git", "config", "user.email", "test@test.com")
    run("git", "config", "user.name", "Test")

    (repo / "file.py").write_text("x = 1\n")
    run("git", "add", "file.py")
    run("git", "commit", "-m", "init")

    run("git", "checkout", "-b", "feature")
    (repo / "file.py").write_text("x = 2\n")
    run("git", "add", "file.py")
    run("git", "commit", "-m", "change")

    run("git", "checkout", "main")
    return repo


# ---------------------------------------------------------------------------
# get_sibling_diff
# ---------------------------------------------------------------------------


def test_get_sibling_diff_happy(git_repo: Path) -> None:
    """Diff between main..feature returns non-empty diff string."""
    diff = get_sibling_diff(git_repo, "main..feature")
    assert diff
    assert "file.py" in diff


def test_get_sibling_diff_no_changes(git_repo: Path) -> None:
    """Same ref both sides produces empty diff."""
    diff = get_sibling_diff(git_repo, "main..main")
    assert diff == ""


def test_get_sibling_diff_invalid_ref(git_repo: Path) -> None:
    """Unknown branch raises BaselineResolutionError (fail-closed)."""
    with pytest.raises(BaselineResolutionError):
        get_sibling_diff(git_repo, "main..nonexistent-branch")


def test_get_sibling_diff_bad_ref_format(git_repo: Path) -> None:
    """Ref without '..' raises ValueError."""
    with pytest.raises(ValueError, match="baseline[.][.]head"):
        get_sibling_diff(git_repo, "main-feature")


def test_get_sibling_diff_empty_baseline(git_repo: Path) -> None:
    """Ref with empty baseline (..head) raises ValueError."""
    with pytest.raises(ValueError, match="baseline[.][.]head"):
        get_sibling_diff(git_repo, "..feature")


def test_get_sibling_diff_empty_head(git_repo: Path) -> None:
    """Ref with empty head (baseline..) raises ValueError."""
    with pytest.raises(ValueError, match="baseline[.][.]head"):
        get_sibling_diff(git_repo, "main..")


# ---------------------------------------------------------------------------
# build_cross_repo_context
# ---------------------------------------------------------------------------


def test_build_cross_repo_context_summary_header() -> None:
    """Output starts with 'Cross-repo review:' summary line."""
    repos = [
        {"label": "primary", "ref": "main..feat", "diff": "diff --git a/f b/f\n"},
    ]
    result = build_cross_repo_context(repos)
    assert result.startswith("Cross-repo review:")


def test_build_cross_repo_context_labeled_blocks() -> None:
    """Each repo section has '## Repo: [label]' heading."""
    repos = [
        {"label": "primary", "ref": "main..feat-a", "diff": "diff --git a/f b/f\n"},
        {"label": "sibling", "ref": "main..feat-b", "diff": "diff --git a/g b/g\n"},
    ]
    result = build_cross_repo_context(repos)
    assert "## Repo: [primary]" in result
    assert "## Repo: [sibling]" in result


def test_build_cross_repo_context_empty_diff() -> None:
    """Empty diff produces '(no changes)' in the block."""
    repos = [
        {"label": "empty-repo", "ref": "main..main", "diff": ""},
    ]
    result = build_cross_repo_context(repos)
    assert "(no changes)" in result


def test_build_cross_repo_context_empty_repos() -> None:
    """Empty repos list returns empty string."""
    assert build_cross_repo_context([]) == ""


# ---------------------------------------------------------------------------
# make_per_repo_cwd
# ---------------------------------------------------------------------------


def test_make_per_repo_cwd_creates_dirs() -> None:
    """Returned path exists with .code-forge/ subdir."""
    cwd = make_per_repo_cwd("test-label")
    try:
        assert cwd.is_dir()
        assert (cwd / ".code-forge").is_dir()
    finally:
        import shutil
        shutil.rmtree(cwd, ignore_errors=True)


def test_make_per_repo_cwd_unique() -> None:
    """Two calls with same label return different paths."""
    cwd1 = make_per_repo_cwd("same")
    cwd2 = make_per_repo_cwd("same")
    try:
        assert cwd1 != cwd2
    finally:
        import shutil
        shutil.rmtree(cwd1, ignore_errors=True)
        shutil.rmtree(cwd2, ignore_errors=True)


def test_make_per_repo_cwd_seeds_gate_config() -> None:
    """When gate_config is provided, gate.yaml is written into .code-forge/."""
    config = {"test": {"command": ["pytest", "-q"]}, "outlet": "subprocess"}
    cwd = make_per_repo_cwd("seeded", gate_config=config)
    try:
        gate_path = cwd / ".code-forge" / "gate.yaml"
        assert gate_path.exists()
        loaded = yaml.safe_load(gate_path.read_text())
        assert loaded["outlet"] == "subprocess"
    finally:
        import shutil
        shutil.rmtree(cwd, ignore_errors=True)


# ---------------------------------------------------------------------------
# derive_source_files
# ---------------------------------------------------------------------------


def test_derive_source_files_absolute(tmp_path: Path) -> None:
    """Returns absolute paths resolved against repo_path."""
    diff_text = (
        "diff --git a/src/main.py b/src/main.py\n"
        "--- a/src/main.py\n"
        "+++ b/src/main.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    files = derive_source_files(tmp_path, diff_text)
    assert len(files) == 1
    assert files[0].is_absolute()
    assert str(files[0]).startswith(str(tmp_path))


def test_derive_source_files_empty_diff() -> None:
    """Empty diff returns empty list (not error)."""
    files = derive_source_files(Path("/tmp"), "")
    assert files == []


# ---------------------------------------------------------------------------
# Cross-validator: gate_check and cross_repo reject the same bad refs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_ref", [
    "main-feature",       # no ".."
    "main...feature",     # three dots
    "..feature",          # empty baseline
    "main..",             # empty head
    "-dash..feature",     # baseline starts with dash (option injection)
    "main..-dash",        # head starts with dash
    ".dot..feature",      # baseline starts with dot
    "main; rm -rf..head", # shell metacharacters in baseline
])
def test_ref_validation_consistent(
    tmp_path: Path,
    git_repo: Path,
    bad_ref: str,
) -> None:
    """Both validate_siblings() and get_sibling_diff() reject the same refs."""
    from code_forge.gate_check import validate_siblings

    gate_yaml_dir = tmp_path / ".code-forge"
    gate_yaml_dir.mkdir(exist_ok=True)
    siblings = [{"repo": str(git_repo), "ref": bad_ref}]

    with pytest.raises(ValueError):
        validate_siblings(siblings, gate_yaml_dir=gate_yaml_dir)

    with pytest.raises(ValueError):
        get_sibling_diff(git_repo, bad_ref)


# ---------------------------------------------------------------------------
# Thread isolation and orchestrator tests
# ---------------------------------------------------------------------------


def test_thread_isolation() -> None:
    """make_per_repo_cwd() returns distinct paths; writes do not collide."""
    import shutil

    p1 = make_per_repo_cwd("repo")
    p2 = make_per_repo_cwd("repo")
    try:
        assert p1 != p2
        (p1 / ".code-forge" / "mutation-result.json").write_text("A")
        (p2 / ".code-forge" / "mutation-result.json").write_text("B")
        assert (p1 / ".code-forge" / "mutation-result.json").read_text() == "A"
        assert (p2 / ".code-forge" / "mutation-result.json").read_text() == "B"
    finally:
        shutil.rmtree(p1, ignore_errors=True)
        shutil.rmtree(p2, ignore_errors=True)


def test_same_stack_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """validate_siblings() rejects sibling with different language when primary_language set."""
    from code_forge.detect import DetectionResult
    from code_forge.gate_check import validate_siblings

    gate_yaml_dir = tmp_path / ".code-forge"
    gate_yaml_dir.mkdir()
    sibling_dir = tmp_path / "sibling"
    sibling_dir.mkdir()

    monkeypatch.setattr(
        "code_forge.detect.detect_toolchain",
        lambda path: DetectionResult(detected=[], missing=[], language="shell"),
    )
    siblings = [{"repo": str(sibling_dir), "ref": "main..feature"}]
    with pytest.raises(ValueError, match="same-stack"):
        validate_siblings(
            siblings, gate_yaml_dir=gate_yaml_dir, primary_language="python",
        )


def test_invalid_ref_fail_closed(
    tmp_path: Path,
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_cross_repo() propagates BaselineResolutionError on bad sibling ref."""
    from code_forge.cross_repo import run_cross_repo
    from code_forge.state import Mode

    monkeypatch.setenv(
        "GIT_CEILING_DIRECTORIES",
        str(tmp_path.parent),
        prepend=os.pathsep,
    )
    with pytest.raises(BaselineResolutionError):
        run_cross_repo(
            primary_path=git_repo,
            primary_ref="main..feature",
            primary_label="primary",
            siblings=[{
                "repo": str(git_repo),
                "ref": "main..nonexistent-xyz",
                "label": "bad-sib",
            }],
            gate_config={"test": {"command": ["pytest", "-q"]}},
            mode=Mode.LOCAL,
            engine_choice="stub",
            backend=None,
            max_rounds=1,
            max_fix_attempts=1,
            clean_round_threshold=1,
        )


def test_thread_exception_propagates(
    tmp_path: Path,
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Thread-internal exception propagates to caller (not swallowed)."""
    from code_forge.cross_repo import run_cross_repo
    from code_forge.state import Mode

    monkeypatch.setenv(
        "GIT_CEILING_DIRECTORIES",
        str(tmp_path.parent),
        prepend=os.pathsep,
    )

    class _BoomMachine:
        def __init__(self, **kwargs):
            pass

        def run(self):
            raise RuntimeError("boom from thread")

    monkeypatch.setattr("code_forge.machine.StateMachine", _BoomMachine)

    with pytest.raises(RuntimeError, match="boom from thread"):
        run_cross_repo(
            primary_path=git_repo,
            primary_ref="main..feature",
            primary_label="primary",
            siblings=[],
            gate_config={"test": {"command": ["pytest", "-q"]}},
            mode=Mode.LOCAL,
            engine_choice="stub",
            backend=None,
            max_rounds=1,
            max_fix_attempts=1,
            clean_round_threshold=1,
        )


def test_pending_escalates_to_fail(
    tmp_path: Path,
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Primary PENDING is escalated to FAIL in cross-repo mode."""
    from code_forge.cross_repo import run_cross_repo
    from code_forge.state import Mode, Verdict

    monkeypatch.setenv(
        "GIT_CEILING_DIRECTORIES",
        str(tmp_path.parent),
        prepend=os.pathsep,
    )

    class _PendingMachine:
        def __init__(self, **kwargs):
            pass

        def run(self):
            return Verdict.PENDING

    monkeypatch.setattr("code_forge.machine.StateMachine", _PendingMachine)

    messages = []
    result = run_cross_repo(
        primary_path=git_repo,
        primary_ref="main..feature",
        primary_label="primary",
        siblings=[],
        gate_config={"test": {"command": ["pytest", "-q"]}},
        mode=Mode.LOCAL,
        engine_choice="stub",
        backend=None,
        max_rounds=1,
        max_fix_attempts=1,
        clean_round_threshold=1,
        output_fn=messages.append,
    )
    assert result == Verdict.FAIL
    assert any("PENDING" in m for m in messages)


def test_validate_base_matches_dispatch(tmp_path: Path) -> None:
    """run_cross_repo uses the same containment base as dispatch-level validation.

    An absolute path outside the project root must be rejected by
    run_cross_repo's own validate_siblings call (narrow base:
    gate_root = primary_path), proving self-authoritative path safety.
    """
    from code_forge.gate_check import validate_siblings

    project = tmp_path / "project"
    project.mkdir()
    code_forge_dir = project / ".code-forge"
    code_forge_dir.mkdir()

    out_of_bounds = "/etc/passwd"
    siblings_abs = [{"repo": out_of_bounds, "ref": "main..feature"}]
    # Narrow base: gate_yaml_dir = .code-forge/, gate_root = project
    with pytest.raises(ValueError, match="traverses outside"):
        validate_siblings(siblings_abs, gate_yaml_dir=code_forge_dir)


def test_run_cross_repo_uses_narrow_base(
    tmp_path: Path,
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_cross_repo passes gate_yaml_dir = primary_path / '.code-forge'
    (narrow base, gate_root = primary_path), not primary_path itself
    (wide base, gate_root = primary_path.parent)."""
    from code_forge.cross_repo import run_cross_repo
    from code_forge.state import Mode

    monkeypatch.setenv(
        "GIT_CEILING_DIRECTORIES",
        str(tmp_path.parent),
        prepend=os.pathsep,
    )

    captured = {}
    real_vs = __import__(
        "code_forge.gate_check", fromlist=["validate_siblings"]
    ).validate_siblings

    def _spy(siblings, gate_yaml_dir, primary_language=None):
        captured["gate_yaml_dir"] = gate_yaml_dir
        return real_vs(siblings, gate_yaml_dir, primary_language)

    monkeypatch.setattr("code_forge.gate_check.validate_siblings", _spy)

    class _PassMachine:
        def __init__(self, **kwargs):
            pass

        def run(self):
            from code_forge.state import Verdict
            return Verdict.PASS

    monkeypatch.setattr("code_forge.machine.StateMachine", _PassMachine)

    run_cross_repo(
        primary_path=git_repo,
        primary_ref="main..feature",
        primary_label="primary",
        siblings=[],
        gate_config={"test": {"command": ["pytest", "-q"]}},
        mode=Mode.LOCAL,
        engine_choice="stub",
        backend=None,
        max_rounds=1,
        max_fix_attempts=1,
        clean_round_threshold=1,
    )
    assert captured["gate_yaml_dir"] == git_repo / ".code-forge"


def test_sibling_crash_is_advisory(
    tmp_path: Path,
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sibling thread crash does not abort the joint review."""
    from code_forge.cross_repo import run_cross_repo
    from code_forge.state import Mode, Verdict

    monkeypatch.setenv(
        "GIT_CEILING_DIRECTORIES",
        str(tmp_path.parent),
        prepend=os.pathsep,
    )

    call_count = {"n": 0}

    class _CrashOnSecond:
        def __init__(self, **kwargs):
            pass

        def run(self):
            call_count["n"] += 1
            if call_count["n"] > 1:
                raise RuntimeError("sibling boom")
            return Verdict.PASS

    monkeypatch.setattr("code_forge.machine.StateMachine", _CrashOnSecond)

    sib = tmp_path / "sibling"
    sib.mkdir()
    run = lambda *cmd: subprocess.run(  # noqa: E731
        list(cmd), cwd=sib, check=True, capture_output=True, text=True,
    )
    run("git", "init", "-b", "main")
    run("git", "config", "user.email", "t@t.com")
    run("git", "config", "user.name", "T")
    (sib / "f.py").write_text("y = 1\n")
    run("git", "add", "f.py")
    run("git", "commit", "-m", "init")
    run("git", "checkout", "-b", "feature")
    (sib / "f.py").write_text("y = 2\n")
    run("git", "add", "f.py")
    run("git", "commit", "-m", "change")
    run("git", "checkout", "main")

    messages = []
    result = run_cross_repo(
        primary_path=git_repo,
        primary_ref="main..feature",
        primary_label="primary",
        siblings=[{
            "repo": str(sib),
            "ref": "main..feature",
            "label": "sib",
        }],
        gate_config={"test": {"command": ["pytest", "-q"]}},
        mode=Mode.LOCAL,
        engine_choice="stub",
        backend=None,
        max_rounds=1,
        max_fix_attempts=1,
        clean_round_threshold=1,
        output_fn=messages.append,
    )
    assert result == Verdict.PASS
    assert any("crashed" in m for m in messages)


# ---------------------------------------------------------------------------
# CLI dispatch tests
# ---------------------------------------------------------------------------


def test_single_repo_zero_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No-siblings gate.yaml returns falsy siblings from _load_gate_siblings.

    Part A: load_gate_config on a no-siblings config -> siblings absent.
    Part B: _load_gate_siblings returns falsy siblings.
    """
    from code_forge.cli import _load_gate_siblings
    from code_forge.gate_check import load_gate_config

    # Part A: load_gate_config on a no-siblings config -> siblings absent
    gate_dir = tmp_path / ".code-forge"
    gate_dir.mkdir(parents=True)
    gate_yaml = gate_dir / "gate.yaml"
    gate_yaml.write_text("test:\n  command: [pytest, -q]\n")
    cfg = load_gate_config(gate_yaml)
    assert cfg.get("siblings") is None

    # Part B: drive the REAL dispatch helper with a no-siblings gate.yaml
    # and verify it returns falsy siblings (-> _run falls through to
    # _run_hold_loop, never calls run_cross_repo).
    _gate_raw, _gate_siblings = _load_gate_siblings(gate_yaml)
    assert not _gate_siblings

    # Also verify with a gate.yaml that HAS siblings -> truthy
    gate_yaml.write_text(
        "test:\n  command: [pytest, -q]\n"
        "siblings:\n"
        "  - repo: ../sib\n"
        "    ref: main..feat\n"
    )
    _raw2, _sibs2 = _load_gate_siblings(gate_yaml)
    assert _sibs2  # non-empty -> dispatch would fire


def test_remote_url_rejected() -> None:
    """validate_siblings rejects remote https:// repo (v1 local-only)."""
    from code_forge.gate_check import validate_siblings

    siblings = [{"repo": "https://github.com/x/y", "ref": "main..feature"}]
    with pytest.raises(ValueError, match="remote"):
        validate_siblings(siblings, gate_yaml_dir=Path("/tmp"))


def test_malformed_gate_yaml_fails_closed(tmp_path: Path) -> None:
    """A malformed or non-mapping gate.yaml raises CliError instead of hiding siblings."""
    from code_forge.cli import _load_gate_siblings
    from code_forge.errors import CliError

    gate_yaml = tmp_path / "gate.yaml"
    # Malformed YAML (unterminated flow sequence that looks like it has siblings)
    gate_yaml.write_text("siblings: [\n")
    with pytest.raises(CliError, match="malformed gate.yaml"):
        _load_gate_siblings(gate_yaml)

    # Non-dict YAML (bare string)
    gate_yaml.write_text("just a bare string")
    with pytest.raises(CliError, match="gate.yaml must be a mapping"):
        _load_gate_siblings(gate_yaml)

    # Missing file -> ({}, None)
    gate_yaml.unlink()
    assert _load_gate_siblings(gate_yaml) == ({}, None)


def test_dispatch_verdict_helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_cross_repo_verdict_or_none handles both sibling and no-sibling paths."""
    from code_forge.cli import _cross_repo_verdict_or_none
    from code_forge.state import Verdict

    gate_dir = tmp_path / ".code-forge"
    gate_dir.mkdir(parents=True)
    gate_yaml = gate_dir / "gate.yaml"

    # Mock run_cross_repo
    def mock_run_cross_repo(*args, **kwargs):
        return Verdict.PASS

    monkeypatch.setattr("code_forge.cross_repo.run_cross_repo", mock_run_cross_repo)

    from code_forge.baseline import GitRefBaseline
    
    baseline_spec = GitRefBaseline("main")
    head_spec = GitRefBaseline("feature")

    def mock_warn(msg):
        pass

    # No siblings -> returns None
    gate_yaml.write_text("test:\n  command: [pytest, -q]\n")
    res = _cross_repo_verdict_or_none(
        gate_yaml_path=gate_yaml, cwd=tmp_path, baseline_spec=baseline_spec,
        head_spec=head_spec, mode=None, engine_choice=None, backend=None,
        max_rounds=1, max_fix=1, _clean_threshold=1, warn=mock_warn
    )
    assert res is None

    # With siblings -> calls run_cross_repo -> returns Verdict.PASS
    gate_yaml.write_text(
        "test:\n  command: [pytest, -q]\n"
        "siblings:\n"
        f"  - repo: {tmp_path}\n"
        "    ref: main..feature\n"
    )
    res = _cross_repo_verdict_or_none(
        gate_yaml_path=gate_yaml, cwd=tmp_path, baseline_spec=baseline_spec,
        head_spec=head_spec, mode=None, engine_choice=None, backend=None,
        max_rounds=1, max_fix=1, _clean_threshold=1, warn=mock_warn
    )
    assert res == Verdict.PASS
