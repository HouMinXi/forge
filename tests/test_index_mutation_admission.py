# SPDX-License-Identifier: Apache-2.0
"""Unsupported staged/committed native subjects stop before review work starts."""
from functools import partial
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

from code_forge import cli, factories, machine, mutation
from code_forge.autofix import StubAutoFixer
from code_forge.backend import BackendConfig
from code_forge.baseline import GitRefBaseline, ResolvedReview, resolve_baseline
from code_forge.falsify import StubFalsifier
from code_forge.state import Mode, State, Verdict, load_state, save_state


class ReachedWork(RuntimeError):
    pass


def git(root, *args):
    result = subprocess.run(["git", *args], cwd=root, check=True,
                            capture_output=True, text=True, timeout=10)
    return result.stdout.strip()


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    git(root, "init", "--initial-branch=fixture")
    git(root, "config", "user.name", "Owned fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    source = root / "calc.py"
    source.write_text("def value():\n    return 1\n", encoding="utf-8")
    git(root, "add", "--", "calc.py")
    git(root, "commit", "-m", "Create owned fixture")
    base = git(root, "rev-parse", "HEAD")
    source.write_text("def value():\n    return 2\n", encoding="utf-8")
    git(root, "add", "--", "calc.py")
    gate = {"test": {"command": [sys.executable, "-m", "pytest"]}}
    (root / ".code-forge").mkdir()
    (root / ".code-forge/gate.yaml").write_text(yaml.safe_dump(gate), encoding="utf-8")
    original_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name, *a, **kw:
                        "/owned/mutmut" if name == "mutmut" else original_which(name, *a, **kw))
    return SimpleNamespace(root=root, source=source, base=base, gate=gate)


def reviewed(project, kind="INDEX", paths=None):
    return resolve_baseline(GitRefBaseline(project.base), GitRefBaseline(kind),
                            paths or [Path("calc.py")], project.root)


def committed_review(project, paths=None):
    git(project.root, "commit", "-m", "Change owned fixture")
    review = reviewed(project, "HEAD", paths)
    assert review.target_kind == "COMMIT" and review.git_diff
    assert review.base_sha == project.base != review.head_sha
    assert git(project.root, "rev-parse", "HEAD~1") == project.base
    return review


def make_machine(project, tmp_path, *, mode=Mode.LOCAL, runner=None, review=None):
    state_root = tmp_path / "state"
    state_root.mkdir()
    (state_root / ".code-forge").mkdir()
    (state_root / ".code-forge/gate.yaml").write_text(yaml.safe_dump(project.gate), encoding="utf-8")
    calls = []

    def l0(*_args):
        calls.append("L0")
        return [], []

    def l1():
        calls.append("L1")
        raise ReachedWork("review work reached before unsupported-source rejection")

    review = review or reviewed(project)
    sm = machine.StateMachine(
        mode=mode, falsifier=StubFalsifier(), autofixer=StubAutoFixer(), revert_fn=lambda _f: None,
        resolved_review=review, source_hash="index-admission-fixture",
        baseline_spec_repr="HEAD~1->HEAD" if review.target_kind == "COMMIT" else "HEAD->INDEX",
        cwd=state_root, source_root=project.root,
        registry={}, l0_runner=l0, l1_provider=l1,
        l2_runner=mutation.run_mutation if runner is None else runner,
        advisory_runners=[], max_total_rounds=1, coverage_l1_active=False,
    )
    sm._state.env_manifest = {}
    return sm, calls


def prohibit_native(monkeypatch, calls):
    def forbidden(*_args, **_kwargs):
        calls.append("native")
        raise AssertionError("native/import probe must not run during admission")

    monkeypatch.setattr(mutation, "run_owned_command", forbidden)
    monkeypatch.setattr(machine, "launch_detached_mutation", forbidden)


@pytest.mark.parametrize("target_kind", ["INDEX", "COMMIT"])
@pytest.mark.parametrize("mode,runner_kind", [
    (Mode.LOCAL, "direct"), (Mode.LOCAL, "factory"),
    (Mode.LOCAL, "direct-without-PATH"), (Mode.LOCAL, "partial-without-PATH"),
    (Mode.CI, "direct"), (Mode.CI, "detached-custom"),
])
def test_native_index_is_rejected_before_any_round(project, tmp_path, monkeypatch, mode, runner_kind, target_kind):
    review = committed_review(project) if target_kind == "COMMIT" else reviewed(project)
    head_before = git(project.root, "rev-parse", "HEAD")
    staged_before = git(project.root, "show", ":calc.py")
    if runner_kind == "factory":
        runner = factories.build_l2_runner(cwd=project.root)
        assert type(runner) is partial and runner.func is mutation.run_mutation
    elif runner_kind == "detached-custom":
        def runner(*_a, **_kw):
            return [], []
    elif runner_kind == "partial-without-PATH":
        runner = partial(mutation.run_mutation, cwd=project.root)
    else:
        runner = mutation.run_mutation
    if "without-PATH" in runner_kind:
        monkeypatch.setattr(shutil, "which", lambda *_a, **_kw: None)
    sm, calls = make_machine(project, tmp_path, mode=mode, runner=runner, review=review)
    prohibit_native(monkeypatch, calls)
    prior = State(source_hash=sm.source_hash, verdict=Verdict.PASS, converged=True)
    prior.round_history = [{"round": 7, "retained_prior_audit": "original"}]
    path = sm.cwd / ".code-forge/state.json"
    save_state(prior, path)
    before = path.read_bytes()
    (sm.cwd / "prior-state.json").write_bytes(before)
    try:
        verdict = sm.run()
    except ReachedWork:
        verdict = None
    assert verdict is Verdict.FAIL, "native snapshot must fail admission before reaching L0/L1"
    assert calls == [], "unsupported snapshot must not execute review/native work"
    saved = load_state(path)
    assert saved.verdict is Verdict.FAIL and saved.converged is False
    assert any(target_kind in error and "WORKING" in error for error in saved.infra_errors)
    if target_kind == "COMMIT":
        assert "--baseline HEAD~1 --head WORKING" in saved.infra_errors[0]
    assert saved.source_hash == sm.source_hash and saved.baseline_spec_repr == sm.baseline_spec_repr
    assert saved.round_history == (prior.round_history if mode is Mode.LOCAL else [])
    assert saved.consecutive_clean_rounds == 0
    assert (sm.cwd / "prior-state.json").read_bytes() == before
    assert not (sm.cwd / ".code-forge/receipts").exists()
    assert not (sm.cwd / ".code-forge/mutation-result.json").exists()
    assert sm.resolved_review.target_kind == target_kind
    assert "+    return 2" in sm.resolved_review.git_diff
    assert project.source.read_text() == "def value():\n    return 2\n"
    assert git(project.root, "rev-parse", "HEAD") == head_before
    assert git(project.root, "show", ":calc.py") == staged_before


@pytest.mark.parametrize("target_kind", ["INDEX", "COMMIT"])
@pytest.mark.parametrize("variant", ["unstaged", "index-moved", "assume-unchanged", "skip-worktree", "include-override"])
def test_index_refusal_does_not_depend_on_accidental_byte_equality(project, tmp_path, monkeypatch, variant, target_kind):
    review = committed_review(project) if target_kind == "COMMIT" else reviewed(project)
    if variant == "index-moved":
        project.source.write_text("def value():\n    return 3\n", encoding="utf-8")
        git(project.root, "add", "--", "calc.py")
    elif variant == "include-override":
        project.gate["test"].update(mutation_skip_globs=["*.py"], mutation_include_globs=["calc.py"])
        (project.root / ".code-forge/gate.yaml").write_text(yaml.safe_dump(project.gate), encoding="utf-8")
    else:
        if variant in {"assume-unchanged", "skip-worktree"}:
            git(project.root, "update-index", "--" + variant, "calc.py")
        project.source.write_text("def value():\n    return 3\n", encoding="utf-8")
    staged_before = git(project.root, "show", ":calc.py")
    physical_before = project.source.read_bytes()
    head_before = git(project.root, "rev-parse", "HEAD")
    sm, calls = make_machine(project, tmp_path, review=review)
    prohibit_native(monkeypatch, calls)
    try:
        verdict = sm.run()
    except ReachedWork:
        verdict = None
    assert verdict is Verdict.FAIL, "snapshot contract must reject hidden/moved physical subjects"
    assert calls == []
    assert git(project.root, "show", ":calc.py") == staged_before
    assert project.source.read_bytes() == physical_before
    assert git(project.root, "rev-parse", "HEAD") == head_before
    assert sm.resolved_review is review and review.target_kind == target_kind


@pytest.mark.parametrize("target_kind", ["INDEX", "COMMIT"])
@pytest.mark.parametrize("case", [
    "WORKING", "COMMIT", "unknown", "custom", "extra-partial", "foreign-partial", "positional-partial",
    "factory-unavailable", "CI-no-native", "no-python", "other-adapter", "tests-only", "deleted-only", "configured-skip", "invalid-config",
])
def test_non_native_or_unselected_subject_keeps_its_normal_boundary(project, tmp_path, monkeypatch, case, target_kind):
    runner = mutation.run_mutation
    mode = Mode.LOCAL
    review = reviewed(project)
    if case == "WORKING":
        review = reviewed(project, "WORKING")
    elif case == "COMMIT":
        review = reviewed(project, project.base)
    elif case == "unknown":
        review = ResolvedReview(review.source_files, None, None, "non-git")
    elif case in {"custom", "CI-no-native"}:
        def runner(*_a, **_kw):
            return [], []
        if case == "CI-no-native":
            mode = Mode.CI
            monkeypatch.setattr(shutil, "which", lambda *_a, **_kw: None)
    elif case == "extra-partial":
        runner = partial(mutation.run_mutation, cwd=project.root, timeout=2)
    elif case == "foreign-partial":
        runner = partial(mutation.run_mutation, cwd=tmp_path)
    elif case == "positional-partial":
        runner = partial(mutation.run_mutation, [])
    elif case == "factory-unavailable":
        monkeypatch.setattr(shutil, "which", lambda *_a, **_kw: None)
        runner = factories.build_l2_runner(cwd=project.root)
    elif case in {"no-python", "other-adapter", "tests-only"}:
        path = {"no-python": "note.txt", "other-adapter": "script.ts", "tests-only": "tests/test_calc.py"}[case]
        (project.root / path).parent.mkdir(parents=True, exist_ok=True)
        (project.root / path).write_text("value = 2\n", encoding="utf-8")
        git(project.root, "add", "--", path)
        review = reviewed(project, paths=[Path(path)])
    elif case == "deleted-only":
        git(project.root, "rm", "-f", "--", "calc.py")
        review = reviewed(project)
    elif case == "configured-skip":
        project.gate["test"]["mutation_skip_globs"] = ["*.py"]
        (project.root / ".code-forge/gate.yaml").write_text(yaml.safe_dump(project.gate), encoding="utf-8")
    elif case == "invalid-config":
        project.gate = {"test": {"command": []}}
    if target_kind == "COMMIT" and case not in {"WORKING", "COMMIT", "unknown"}:
        review = committed_review(project, paths=review.source_files)
    sm, calls = make_machine(project, tmp_path, mode=mode, runner=runner, review=review)
    prohibit_native(monkeypatch, calls)
    with pytest.raises(ReachedWork, match="review work reached"):
        sm.run()
    assert calls == ["L0", "L1"]


def configure_cli(project, monkeypatch, *, outlet="subprocess", committed=False, mode="ci"):
    from code_forge import outlet_resolver
    monkeypatch.chdir(project.root)
    monkeypatch.setattr(outlet_resolver, "resolve_outlet", lambda *_a, **_kw: outlet)
    monkeypatch.setattr(cli, "_load_gate_backends", lambda _path: ([], {}))
    monkeypatch.setattr(cli, "_merge_user_into", lambda cfgs, _data: cfgs)
    monkeypatch.setattr("code_forge.user_config.load_user_retry", lambda: {})
    backend = BackendConfig(name="owned", type="api", format="openai", base_url="https://unused.invalid/v1", model="owned")
    monkeypatch.setattr("code_forge.backend.resolve_backend", lambda *_a, **_kw: backend)
    monkeypatch.setattr(cli, "_check_backend_credentials", lambda *_a, **_kw: None)
    tools = project.root / "owned-tools.yaml"
    tools.write_text("tools: {}\n", encoding="utf-8")
    selection = ["--committed"] if committed else ["--baseline", project.base, "--head", "INDEX"]
    if mode == "ci":
        # #140: CI never trusts an untracked in-tree gate.yaml; supply the
        # fixture gate from a config root outside the checkout.
        outside = project.root.parent / (project.root.name + "-ci-config")
        (outside / ".code-forge").mkdir(parents=True, exist_ok=True)
        (outside / ".code-forge/gate.yaml").write_bytes((project.root / ".code-forge/gate.yaml").read_bytes())
        monkeypatch.setenv("FORGE_TEST_CONFIG_ROOT", str(outside))
    return ["forge", "review", "--allow-main", "--backend", "owned", "--registry", str(tools),
            "--mode", mode, *selection, "calc.py"]


@pytest.mark.parametrize("target_kind", ["INDEX", "COMMIT"])
@pytest.mark.parametrize("mode", ["local", "ci"])
def test_cli_index_refuses_before_enrichment_and_mcp_preserves_error(project, monkeypatch, capsys, target_kind, mode):
    from code_forge.mcp_server import _make_result
    if target_kind == "COMMIT":
        committed_review(project)
    args = configure_cli(project, monkeypatch, committed=target_kind == "COMMIT", mode=mode)
    calls = []
    prohibit_native(monkeypatch, calls)

    def enrichment(*_a, **_kw):
        calls.append("enrichment")
        raise ReachedWork("enrichment reached before snapshot admission")

    monkeypatch.setattr(cli, "_assemble_post_image", enrichment)
    monkeypatch.setattr(sys, "argv", args)
    exit_code = cli.main()
    output = capsys.readouterr()
    assert exit_code == 2, "native snapshot must return actionable CLI_ERROR before model enrichment"
    assert calls == [] and output.out == ""
    assert target_kind in output.err and "WORKING" in output.err
    if target_kind == "COMMIT":
        assert "--baseline HEAD~1 --head WORKING" in output.err
    assert "unsupported" in output.err.lower()
    projected = _make_result(output.out, exit_code, 0.0, output.err)
    assert projected.structuredContent["exit_code"] == 2
    assert output.err in projected.structuredContent["output"]
    assert not (project.root / ".code-forge/receipts").exists()


@pytest.mark.parametrize("target_kind", ["INDEX", "COMMIT"])
@pytest.mark.parametrize("outlet", ["subagent", "inline"])
def test_cli_static_outlets_keep_index_admitted(project, monkeypatch, outlet, target_kind):
    if target_kind == "COMMIT":
        committed_review(project)
    args = configure_cli(project, monkeypatch, outlet=outlet, committed=target_kind == "COMMIT")
    observed = []
    if outlet == "inline":
        def inline(*_a, **_kw):
            observed.append("inline")
            return Verdict.DELEGATED
        monkeypatch.setattr(cli, "_dispatch_inline_canary", inline)
    else:
        def subagent(_outlet, _warn, _contract, _backend, resolved, *_a, **_kw):
            observed.append(resolved.target_kind)
            return Verdict.DELEGATED
        monkeypatch.setattr(cli, "_dispatch_subagent", subagent)
    monkeypatch.setattr(sys, "argv", args)
    assert cli.main() == 5
    assert observed == (["inline"] if outlet == "inline" else [target_kind])


@pytest.mark.parametrize("target_kind", ["INDEX", "COMMIT"])
def test_actual_outlet_c_retains_its_no_native_index_seam(project, target_kind):
    from code_forge.outlet_c import run_outlet_c
    observed = []

    class StaticBoundary(BaseException):
        pass

    def spawn(*_a, **_kw):
        observed.append("reviewer-boundary")
        raise StaticBoundary("static reviewer boundary")

    review = committed_review(project) if target_kind == "COMMIT" else reviewed(project)
    with pytest.raises(StaticBoundary, match="static reviewer boundary"):
        run_outlet_c(review, "static-snapshot", project.root, spawn,
                     falsifier=StubFalsifier(), max_total_rounds=1)
    assert observed == ["reviewer-boundary"]
