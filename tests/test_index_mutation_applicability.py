# SPDX-License-Identifier: Apache-2.0
"""Boundary controls for native intent, empty scopes and existing source policy."""
from functools import partial
import os
from pathlib import Path

import pytest

from code_forge import mutation
from code_forge.state import Mode, State, Verdict, load_state, save_state
from tests.test_index_mutation_admission import (
    ReachedWork, git, make_machine, prohibit_native, project as project, reviewed,
)


@pytest.mark.parametrize("case", ["invalid-partial-root", "empty-INDEX"])
def test_non_native_or_empty_index_keeps_the_original_work_boundary(project, tmp_path, monkeypatch, case):
    runner = partial(mutation.run_mutation, cwd=None)
    if case == "empty-INDEX":
        git(project.root, "reset", "--", "calc.py")
        runner = mutation.run_mutation
    sm, calls = make_machine(project, tmp_path, runner=runner, review=reviewed(project))
    prohibit_native(monkeypatch, calls)
    with pytest.raises(ReachedWork, match="review work reached"):
        sm.run()
    assert calls == ["L0", "L1"]


@pytest.mark.parametrize("include", [False, True])
def test_ci_detached_selection_honors_its_actual_copy_configuration(project, tmp_path, monkeypatch, include):
    project.gate["test"]["mutation_skip_globs"] = ["*.py"]
    if include:
        project.gate["test"]["mutation_include_globs"] = ["calc.py"]
    sm, calls = make_machine(project, tmp_path, mode=Mode.CI, runner=lambda *_a, **_kw: ([], []))
    prohibit_native(monkeypatch, calls)
    if include:
        assert sm.run() is Verdict.FAIL
        assert calls == []
    else:
        with pytest.raises(ReachedWork, match="review work reached"):
            sm.run()
        assert calls == ["L0", "L1"]


def test_repeated_refusal_preserves_prior_diagnostic_without_a_new_round(project, tmp_path, monkeypatch):
    sm, calls = make_machine(project, tmp_path)
    prohibit_native(monkeypatch, calls)
    assert sm.run() is Verdict.FAIL
    before = load_state(sm.cwd / ".code-forge/state.json")
    assert sm.run() is Verdict.FAIL
    after = load_state(sm.cwd / ".code-forge/state.json")
    assert after.infra_errors == before.infra_errors and len(after.infra_errors) == 1
    assert after.round_history == before.round_history == []
    assert calls == []


def test_missing_source_policy_uses_existing_defaults(project, tmp_path, monkeypatch):
    (project.root / ".code-forge/gate.yaml").unlink()
    sm, calls = make_machine(project, tmp_path)
    prohibit_native(monkeypatch, calls)
    assert sm.run() is Verdict.FAIL
    assert calls == []


def test_refusal_preserves_historical_counter_and_existing_audit_bytes(project, tmp_path, monkeypatch):
    sm, calls = make_machine(project, tmp_path)
    prohibit_native(monkeypatch, calls)
    prior = State(source_hash=sm.source_hash, verdict=Verdict.PASS, converged=True,
                  consecutive_clean_rounds=2)
    prior.round_history = [{"round": 4, "retained_prior_audit": "original"}]
    state_path = sm.cwd / ".code-forge/state.json"
    save_state(prior, state_path)
    original_state = state_path.read_bytes()
    (sm.cwd / "prior-state.json").write_bytes(original_state)
    receipts = sm.cwd / ".code-forge/receipts"
    receipts.mkdir()
    audit = receipts / "prior-audit.txt"
    audit.write_bytes(b"prior audit bytes; no new round or authority\n")
    assert sm.run() is Verdict.FAIL
    saved = load_state(state_path)
    assert saved.verdict is Verdict.FAIL and saved.converged is False
    assert saved.consecutive_clean_rounds == 2 and saved.round_history == prior.round_history
    assert saved.earned_clean_window is None
    assert audit.read_bytes() == b"prior audit bytes; no new round or authority\n"
    assert (sm.cwd / "prior-state.json").read_bytes() == original_state
    assert calls == []


@pytest.mark.parametrize("root_spelling", ["dot", "relative-repo"])
@pytest.mark.parametrize("route", ["direct", "exact-partial", "CI-detached"])
@pytest.mark.parametrize("selection", ["production", "true-test", "configured-skip", "include-override"])
def test_relative_root_uses_native_repository_relative_selection(
    project, tmp_path, monkeypatch, root_spelling, route, selection,
):
    import yaml

    ancestor = tmp_path / "tests"
    ancestor.mkdir()
    destination = ancestor / "owned-source"
    project.root.rename(destination)
    project.root = destination
    project.source = destination / "calc.py"
    name = "calc.py"
    if selection in {"true-test", "include-override"}:
        name = "tests/test_calc.py"
        path = project.root / name
        path.parent.mkdir()
        path.write_text("value = 2\n", encoding="utf-8")
        git(project.root, "add", "--", name)
    if selection == "configured-skip":
        project.gate["test"]["mutation_skip_globs"] = ["calc.py"]
    elif selection == "include-override":
        project.gate["test"]["mutation_include_globs"] = ["tests/test_calc.py"]
    (project.root / ".code-forge/gate.yaml").write_text(yaml.safe_dump(project.gate), encoding="utf-8")
    source = project.root / name
    review = reviewed(project, paths=[source])
    assert source.is_absolute() and review.git_diff and review.target_kind == "INDEX"
    if root_spelling == "dot":
        monkeypatch.chdir(project.root)
        root = Path(".")
    else:
        monkeypatch.chdir(ancestor)
        root = Path("owned-source")
    canonical = mutation._lexical_source(str(source), Path(os.path.abspath(root))).as_posix()
    assert canonical == name
    skip, include = mutation._globs_from_gate_yaml(root)
    selected = selection in {"production", "include-override"}
    assert (not mutation._is_test_path(canonical, skip, include, root)) is selected
    if route == "exact-partial":
        runner = partial(mutation.run_mutation, cwd=root)
    elif route == "CI-detached":
        def runner(*_a, **_kw):
            return [], []
    else:
        runner = mutation.run_mutation
    sm, calls = make_machine(project, tmp_path, mode=Mode.CI if route == "CI-detached" else Mode.LOCAL,
                             runner=runner, review=review)
    sm.source_root = root
    prohibit_native(monkeypatch, calls)
    before = source.read_bytes()
    staged = git(project.root, "show", ":" + name)
    if selected:
        try:
            verdict = sm.run()
        except ReachedWork:
            verdict = None
        assert verdict is Verdict.FAIL, "relative source root must reject canonical native INDEX selection before L0/L1"
        assert calls == []
        saved = load_state(sm.cwd / ".code-forge/state.json")
        assert saved.verdict is Verdict.FAIL and saved.converged is False
        assert any("INDEX" in error for error in saved.infra_errors)
        assert not (sm.cwd / ".code-forge/receipts").exists()
    else:
        with pytest.raises(ReachedWork, match="review work reached"):
            sm.run()
        assert calls == ["L0", "L1"]
    assert source.read_bytes() == before and git(project.root, "show", ":" + name) == staged
    assert sm.resolved_review is review and review.target_kind == "INDEX"


@pytest.mark.parametrize("kind", ["outside-root", "parent-traversal"])
def test_invalid_lexical_input_keeps_the_existing_consumer_diagnostic(project, tmp_path, kind):
    source = tmp_path / "outside.py" if kind == "outside-root" else Path("../calc.py")
    with pytest.raises(ValueError, match="outside the project root"):
        mutation._lexical_source(str(source), project.root)
    review = reviewed(project)
    error = mutation.mutation_review_admission_error(
        target_kind="INDEX", git_diff=review.git_diff, source_files=[source],
        source_root=project.root, config_root=project.root, runner=mutation.run_mutation,
    )
    assert error is None, "invalid lexical input must keep its existing consumer diagnostic, not new admission authority"
