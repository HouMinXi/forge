# SPDX-License-Identifier: Apache-2.0
"""A supported measurement survives unrelated adapter applicability notes.

The executor and generated inventory are deliberately substituted fixtures.
These tests exercise real ownership, result packaging and CI consumption,
without running a mutation engine.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from code_forge import mutation
from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.falsify import StubFalsifier
from code_forge.machine import StateMachine
from code_forge.disposition import Disposition
from code_forge.state import Mode, StateFinding, Verdict


@pytest.mark.parametrize("sibling", [False, True], ids=["python", "mixed"])
@pytest.mark.parametrize(
    "case",
    ["survivor", "killed", "baseline-failed", "run-refused", "invalid", "timeout", "release-failed"],
)
def test_completed_python_measurement_retains_its_ci_verdict(
    tmp_path, monkeypatch, run_detached_payload, sibling, case
):
    source = tmp_path / "src"
    source.mkdir()
    (source / "add.py").write_text("def add(a, b):\n    return a + b\n")
    (source / "view.ts").write_text("export const value = 1;\n")
    original_sources = {path.name: path.read_bytes() for path in source.iterdir()}
    selected = ["src/add.py"] + (["src/view.ts"] if sibling else [])
    calls = []

    def execute(argv, **kwargs):
        assert kwargs["cwd"] == str(tmp_path)
        calls.append(argv)
        if "run" in argv:
            assert "only_mutate=src/add.py\n" in (tmp_path / "setup.cfg").read_text()
            assert "src/view.ts" not in (tmp_path / "setup.cfg").read_text()
            if case == "run-refused":
                return subprocess.CompletedProcess(argv, 2, "", "OWNED_REFUSAL")
            if case == "timeout":
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
            mirror = tmp_path / "mutants/src/add.py"
            mirror.parent.mkdir(parents=True, exist_ok=True)
            mirror.write_text("def x_add__mutmut_1(a, b):\n    return a - b\n")
            mirror.with_name("add.py.meta").write_text(
                json.dumps({"exit_code_by_key": {"add.x_add__mutmut_1": 0 if case == "survivor" else 1}})
            )
        if "results" in argv:
            output = (
                "BROKEN_TRANSPORT"
                if case == "invalid"
                else (
                    "add.x_add__mutmut_1: survived"
                    if case == "survivor"
                    else "add.x_add__mutmut_1: killed"
                )
            )
            return subprocess.CompletedProcess(argv, 0, output, "")
        if case == "baseline-failed":
            return subprocess.CompletedProcess(argv, 1, "", "OWNED_TEST_FAILURE")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(mutation, "run_owned_command", execute)
    monkeypatch.setattr(mutation, "_resolve_mutmut_invocation", lambda *_a, **_k: ["mutmut"])
    monkeypatch.setattr(
        "code_forge.mutation_dispatch.other_adapter_note", lambda *_a, **_k: "UNMEASURED_TYPESCRIPT"
    )
    if case == "release-failed":
        original_release = mutation.MutationWorkspace.release

        def release(workspace):
            original_release(workspace)
            raise mutation.MutationWorkspaceError("OWNED_RELEASE_REFUSAL")

        monkeypatch.setattr(mutation.MutationWorkspace, "release", release)

    evidence = {}
    direct, infra = mutation.run_mutation(
        selected, [sys.executable, "-m", "pytest"], cwd=tmp_path, _evidence=evidence
    )
    assert {path.name: path.read_bytes() for path in source.iterdir()} == original_sources
    assert not (tmp_path / "setup.cfg").exists()
    assert not (tmp_path / "mutants").exists()

    scripts = []

    def capture(argv, **kwargs):
        assert kwargs["cwd"] == tmp_path
        scripts.append(argv[2])
        return type("OwnedSubstitute", (), {"pid": 123, "wait": lambda self, timeout: 0})()

    monkeypatch.setattr(mutation.subprocess, "Popen", capture)
    result = tmp_path / ".code-forge/mutation-result.json"
    assert mutation.launch_detached_mutation(
        selected, [sys.executable, "-m", "pytest"], tmp_path, result
    )
    run_detached_payload(scripts[0])
    payload = json.loads(result.read_text())
    machine = StateMachine(
        mode=Mode.CI,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda _f: None,
        resolved_review=ResolvedReview([Path(value) for value in selected], None, None, "git"),
        source_hash="owned-adapter-control",
        baseline_spec_repr="substituted executor",
        cwd=tmp_path,
        registry={},
        l0_runner=lambda *_a: ([], []),
    )
    monkeypatch.setattr(machine, "_execute_round", lambda **_k: None)
    monkeypatch.setattr(machine, "_receipt_gate_round_errors", lambda: [])
    monkeypatch.setattr(machine, "_receipt_gate_terminal_errors", lambda: [])
    monkeypatch.setattr(machine, "_persist_state", lambda: None)
    monkeypatch.setattr(machine, "_write_ci_ledger_rows", lambda: None)
    monkeypatch.setattr("shutil.which", lambda _command: None)
    verdict = machine._run_ci()
    if case in ("survivor", "killed"):
        assert verdict is (Verdict.FAIL if case == "survivor" else Verdict.PASS)
        assert payload["status"] == "done" and payload["baseline_passed"] is True
        assert payload["survivors"] == (["mutant-add.x_add__mutmut_1"] if case == "survivor" else [])
        assert evidence["completed_measurement"] is True
        assert evidence["baseline_passed"] is (not sibling)
        assert not infra
    else:
        assert payload["status"] == "error" and payload["baseline_passed"] is False
        assert verdict is Verdict.PASS
        assert not evidence["completed_measurement"]
        assert not evidence["baseline_passed"]
        assert not payload["survivors"]
    assert payload["skipped"].count("UNMEASURED_TYPESCRIPT") == int(sibling)
    assert not result.exists()
    assert calls
    assert {path.name: path.read_bytes() for path in source.iterdir()} == original_sources


@pytest.mark.parametrize(
    "evidence,infra,diagnostic,expected",
    [
        ({}, [], False, "error"),
        ({"baseline_passed": False}, [], False, "error"),
        ({"baseline_passed": True}, [], False, "done"),
        ({"completed_measurement": False, "baseline_passed": True}, [], False, "error"),
        ({"completed_measurement": True}, ["OWNED_INFRA"], False, "error"),
        ({"completed_measurement": True}, [], True, "error"),
    ],
)
def test_terminal_carrier_retains_false_proof_and_independent_errors(
    evidence, infra, diagnostic, expected
):
    # A legacy baseline-only dictionary is an explicit substituted producer
    # contract, not native authenticity or an inferred survivor proof.
    findings = (
        [StateFinding("MUTATION_ERROR", "refusal", "MUTANT", Disposition.CONFIRMED, "", [], "REFUSED")]
        if diagnostic
        else []
    )
    result = mutation._mutation_outcome(findings, infra, evidence)
    assert result["status"] == expected
    assert result["baseline_passed"] is (expected == "done")
    assert result["survivors"] == []
    assert result["infra_errors"] == infra
    assert bool(result["message"]) is (expected == "error")
