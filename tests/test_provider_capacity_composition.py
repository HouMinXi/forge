"""Additive ddce98/c592 composition regressions.

Real offline provider/receipt fixtures exercise composition boundaries. Injected
FIXVAL results below prove refusal only, never owned execution acceptance.
"""

import copy
import json
from pathlib import Path

import pytest

from code_forge import fixval, llm_invoke, verify
from code_forge.autofix import NoChangeAutoFixer
from code_forge.errors import CorruptedStateError
from code_forge.fixval_evidence import new_stage, validate_stage
from code_forge.state import (
    Disposition,
    FindingDiagnosticKind,
    Mode,
    StateFinding,
    Verdict,
    load_state,
    save_state,
)
from tests.test_provider_capacity_reporting import _capacity, _reported
from tests.test_resume_receipt_provenance import (
    VALID,
    _machine,
    review_workspace as review_workspace,
)


def _truncated(machine, prompt):
    if "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
        return llm_invoke.LLMInvokeError("composition capacity", kind="truncated", retryable=False)
    return VALID


def _finding(name, source="L0", disposition=Disposition.CONFIRMED):
    return StateFinding(name, name, source, disposition, "control.ts", [2, 2], name)


@pytest.mark.parametrize("attested", [True, False])
@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("clean", Verdict.PASS),
        ("falsifier", Verdict.UNRELIABLE),
        ("capacity", Verdict.FAIL),
        ("capacity_falsifier", Verdict.FAIL),
        ("product_falsifier", Verdict.FAIL),
        ("coverage_falsifier", Verdict.FAIL),
    ],
)
def test_composed_ci_verdict_precedence(review_workspace, monkeypatch, attested, scenario, expected):
    def payload(machine, prompt):
        if "capacity" in scenario:
            result = _truncated(machine, prompt)
            if isinstance(result, Exception):
                return result
        body = copy.deepcopy(VALID)
        if "falsifier" in scenario and "structural code reviewer" in prompt:
            body["findings"] = [
                {
                    "id": "needs-falsifier",
                    "file": "control.ts",
                    "line": 2,
                    "severity": "P1",
                    "description": "needs adjudication",
                    "excerpt": "const value = 2;",
                }
            ]
        return body

    machine = _machine(review_workspace, monkeypatch, rounds=1, payload=payload)
    machine.mode = Mode.CI
    if not attested:
        machine.coverage_l1_active = False
        machine.coverage_exempt_patterns = ["control.ts"]
    if "falsifier" in scenario:

        def unavailable(finding):
            raise RuntimeError("composition falsifier unavailable")

        monkeypatch.setattr(machine.falsifier, "falsify", unavailable)
    if scenario == "product_falsifier":
        machine.l0_runner = lambda *args: ([_finding("independent-product")], [])
    elif scenario == "coverage_falsifier":
        machine.l0_runner = lambda *args: (
            [_finding("independent-coverage", "COVERAGE", Disposition.UNCERTAIN)],
            [],
        )
    assert machine.run() is expected
    persisted = load_state(review_workspace / ".code-forge/state.json")
    assert persisted.verdict is expected
    assert persisted.converged is (expected is Verdict.PASS)
    assert persisted.rounds_with_falsify_infra == int("falsifier" in scenario)
    if "capacity" in scenario:
        assert machine._acquisition_markers
        assert any(f.diagnostic_kind == "provider-capacity" for f in persisted.findings)


@pytest.mark.parametrize("attested", [True, False])
def test_terminal_live_capacity_refuses_before_fixval_and_persists(
    review_workspace, monkeypatch, attested
):
    machine = _machine(review_workspace, monkeypatch, rounds=1, payload=_truncated)
    if not attested:
        machine.coverage_l1_active = False
        machine.coverage_exempt_patterns = ["control.ts"]
    assert machine.run() is not Verdict.PASS
    assert machine._acquisition_markers
    before_credit = machine._state.consecutive_clean_rounds
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("live acquisition must refuse before FIXVAL")

    monkeypatch.setattr(fixval, "run_fixval", forbidden)
    machine._state.converged = True
    machine._state.verdict = Verdict.PASS
    machine._finalize_local_terminal()
    persisted = load_state(review_workspace / ".code-forge/state.json")
    assert not calls
    assert persisted.verdict is Verdict.FAIL and not persisted.converged
    assert persisted.consecutive_clean_rounds == before_credit == 0
    assert persisted.fixval_stage is None or persisted.fixval_stage["outcome"] == "pending"


@pytest.mark.parametrize(
    "damage",
    [
        "hash",
        "unreadable",
        "scope",
        "missing_status",
        "invalid_status",
        "unrelated_unresolved",
        "unwitnessed",
    ],
)
def test_terminal_capacity_keeps_authoritative_and_forensic_refusals(
    review_workspace, monkeypatch, damage
):
    def hook(round_index):
        if round_index != 2:
            return
        path = review_workspace / ".code-forge/receipts/receipt-c3p1.json"
        if damage == "unreadable":
            path.write_text("{")
            return
        receipt = json.loads(path.read_text())
        if damage == "hash":
            receipt["diff_sha256"] = "wrong"
        elif damage == "scope":
            receipt["reviewed_repositories"] = {"foreign": "scope"}
        elif damage == "missing_status":
            receipt.pop("pass_status")
        elif damage == "invalid_status":
            receipt["pass_status"] = "invented"
        elif damage == "unrelated_unresolved":
            receipt["findings"] = [
                {
                    "file": "control.ts",
                    "line": 2,
                    "description": "independent defect",
                    "disposition": "CONFIRMED",
                    "basis": {
                        "authority": "infra-unavailable",
                        "falsification_survived": False,
                        "convergence_rounds": 3,
                    },
                }
            ]
            receipt["findings_count"] = 1
        else:
            receipt["anchors"] = []
            receipt["code_excerpts"] = []
            receipt["covered_line_ranges"] = []
            sibling = path.with_name("receipt-c3p2.json")
            sibling_receipt = json.loads(sibling.read_text())
            sibling_receipt.update(anchors=[], code_excerpts=[], covered_line_ranges=[])
            sibling.write_text(json.dumps(sibling_receipt))
        path.write_text(json.dumps(receipt))

    machine = _machine(review_workspace, monkeypatch, payload=_capacity, hook=hook)
    assert machine.run() is Verdict.FAIL
    errors = machine._receipt_gate_terminal_errors()
    assert errors and any(str(error).startswith("receipt acceptance:") for error in errors)
    if damage != "unreadable":
        assert any(str(error).startswith("receipt attempt:") for error in errors)
    else:
        assert all(not machine._capacity_incomplete(error.verification) for error in errors)
    summary, report = _reported(review_workspace)
    assert "capacity_incomplete=" not in summary
    assert any(f.id == "RECEIPT_INVALID" for f in machine.active_findings)
    assert report["results"]
    assert not machine._state.converged


@pytest.mark.parametrize("product", [False, True])
def test_recovery_keeps_only_earned_credit_and_new_invocation(review_workspace, monkeypatch, product):
    machine = _machine(review_workspace, monkeypatch, payload=_capacity)
    if product:
        machine.autofixer = NoChangeAutoFixer()
        machine.l0_runner = lambda *args: (
            [_finding("product")] if machine._state.round == 2 else [],
            [],
        )
    assert machine.run() is not Verdict.PASS
    before = load_state(review_workspace / ".code-forge/state.json")
    assert before.consecutive_clean_rounds == (0 if product else 2)
    assert before.round_history[-1]["clean_credit_action"] == ("reset" if product else "interrupted")
    previous_invocation = machine._fixval_invocation_id
    # Resume reporting is not fresh acquisition authority.
    resumed = _machine(review_workspace, monkeypatch, rounds=3 if product else 1)
    assert resumed._acquisition_authority is None
    assert resumed._acquisition_markers == []
    assert resumed.run() is Verdict.PASS
    state = load_state(review_workspace / ".code-forge/state.json")
    assert state.fixval_stage["invocation_id"] != previous_invocation
    assert state.fixval_stage["outcome"] == "SKIPPED"
    validate_stage(state.fixval_stage)
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == (
        [4, 5, 6] if product else [1, 2, 4]
    )


def _applicable_machine(root, monkeypatch):
    machine = _machine(root, monkeypatch)
    # Real provider/receipts still cover the actual diff. Extra source membership
    # makes FIXVAL applicable; injected outcomes below are negative controls.
    machine.resolved_review.source_files.append(Path("tests/test_control.py"))
    (root / "tests").mkdir()
    (root / "tests/test_control.py").write_text("def test_control():\n    assert True\n")
    (root / ".code-forge/gate.yaml").write_text(
        "verify:\n  required_cycles: 3\ntest:\n  command: [python3, -m, pytest]\n"
    )
    monkeypatch.setattr(machine, "_get_commit_message", lambda: "")
    monkeypatch.delenv("FIXVAL_WAIVER", raising=False)
    return machine


@pytest.mark.parametrize(
    "damage",
    [
        "missing_config",
        "invalid_config",
        "config_changes_on_load",
        "config_changes_on_execution",
        "unknown_result",
        "unknown_status",
        "missing_proof",
        "stale_invocation",
        "stale_source",
        "invalid_terminal_proof",
    ],
)
def test_healthy_acquisition_cannot_bypass_fixval_chain(review_workspace, monkeypatch, damage):
    machine = _applicable_machine(review_workspace, monkeypatch)
    path = review_workspace / ".code-forge/gate.yaml"
    calls = []
    if damage == "missing_config":
        path.unlink()
    elif damage == "invalid_config":
        path.write_text("test: {}\n")
    elif damage == "config_changes_on_load":
        from code_forge import gate_check

        original = gate_check.load_gate_config

        def load(file):
            result = original(file)
            path.write_text(path.read_text() + "# changed\n")
            return result

        monkeypatch.setattr(gate_check, "load_gate_config", load)

    def execute(*args, **kwargs):
        calls.append(kwargs)
        if damage == "config_changes_on_execution":
            path.write_text(path.read_text() + "# changed\n")
        if damage == "unknown_result":
            return object()
        result = fixval.FixvalResult(fixval.FixvalStatus.PASS, [], [])
        if damage == "unknown_status":
            result.status = "PASS"
        elif damage in ("stale_invocation", "stale_source", "invalid_terminal_proof"):
            stage = new_stage(kwargs["stage_id"], kwargs["source_hash"])
            if damage == "stale_invocation":
                stage["invocation_id"] = "f" * 32
            elif damage == "stale_source":
                stage["source_hash"] = "f" * 64
            else:
                stage["outcome"] = "PASS"
            result.stage = stage
        return result

    monkeypatch.setattr(fixval, "run_fixval", execute)
    assert machine.run() is Verdict.FAIL
    state = load_state(review_workspace / ".code-forge/state.json")
    assert state.consecutive_clean_rounds == 3
    assert not state.converged and state.verdict is Verdict.FAIL
    assert state.fixval_stage["outcome"] == "ERROR"
    validate_stage(state.fixval_stage)
    assert any(f.id == "FIXVAL_ERROR" for f in state.findings)
    assert bool(calls) is (damage not in ("missing_config", "invalid_config", "config_changes_on_load"))


@pytest.mark.parametrize("exception", ["no_tests", "waiver"])
def test_healthy_acquisition_preserves_explicit_preflight_exceptions(
    review_workspace, monkeypatch, exception
):
    machine = (
        _machine(review_workspace, monkeypatch)
        if exception == "no_tests"
        else _applicable_machine(review_workspace, monkeypatch)
    )
    if exception == "waiver":
        monkeypatch.setenv("FIXVAL_WAIVER", "composition explicit waiver")
    else:
        monkeypatch.delenv("FIXVAL_WAIVER", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("preflight exception must not execute FIXVAL")

    monkeypatch.setattr(fixval, "run_fixval", forbidden)
    assert machine.run() is Verdict.PASS
    state = load_state(review_workspace / ".code-forge/state.json")
    assert state.fixval_stage["outcome"] == ("WAIVED" if exception == "waiver" else "SKIPPED")
    assert state.fixval_stage["invocation_id"] == machine._fixval_invocation_id
    validate_stage(state.fixval_stage)


@pytest.mark.parametrize("damage", [None, "version", "outcome", "invocation"])
def test_diagnostics_and_fixval_stage_validate_on_both_state_boundaries(
    review_workspace, monkeypatch, damage
):
    machine = _machine(review_workspace, monkeypatch, rounds=1, payload=_truncated)
    assert machine.run() is Verdict.FAIL
    path = review_workspace / ".code-forge/state.json"
    state = load_state(path)
    state.fixval_stage = new_stage(machine._fixval_invocation_id, machine.source_hash)
    save_state(state, path)
    valid = json.loads(path.read_text())
    restored = load_state(path)
    assert restored.fixval_stage == state.fixval_stage
    assert any(
        f.diagnostic_kind == "provider-capacity" and f.disposition is Disposition.CONFIRMED
        for f in restored.findings
    )
    assert _machine(review_workspace, monkeypatch)._acquisition_authority is None
    if damage is None:
        return
    key, value = {
        "version": ("version", 99),
        "outcome": ("outcome", "PASS"),
        "invocation": ("invocation_id", None),
    }[damage]
    state.fixval_stage[key] = value
    with pytest.raises(ValueError):
        save_state(state, path)
    assert json.loads(path.read_text()) == valid
    valid["fixval_stage"][key] = value
    path.write_text(json.dumps(valid))
    with pytest.raises(CorruptedStateError):
        load_state(path)


def test_reporting_projects_only_diagnostic_classes_with_stage_preserved(review_workspace, monkeypatch):
    machine = _machine(review_workspace, monkeypatch, rounds=1, payload=_truncated)
    machine.mode = Mode.CI
    machine.l0_runner = lambda *args: (
        [_finding("ordinary-infra", "INFRA"), _finding("ordinary-product")],
        [],
    )
    assert machine.run() is Verdict.FAIL
    state = load_state(review_workspace / ".code-forge/state.json")
    assert any(
        f.diagnostic_kind == "provider-capacity" and f.disposition is Disposition.CONFIRMED
        for f in state.findings
    )
    summary, report = _reported(review_workspace)
    descriptions = {entry["message"]["text"] for entry in report["results"]}
    assert {"ordinary-infra", "ordinary-product"} <= descriptions
    assert report["properties"]["providerDiagnostics"]
    assert "FAIL" in summary and not state.converged


@pytest.mark.parametrize("hard_position", ["earned", "attempted"])
def test_same_reason_distinct_terminal_results_never_share_capacity_authority(
    review_workspace, monkeypatch, hard_position
):
    machine = _machine(review_workspace, monkeypatch, rounds=1, payload=_truncated)
    assert machine.run() is Verdict.FAIL
    # Derive the capacity observation from the actual receipts and live producer.
    capacity = verify.run_verify(
        review_workspace,
        machine.source_hash,
        verify.parse_diff_files(machine._receipt_diff()),
        diff_text=machine._receipt_diff(),
        required_cycles=1,
        cycles=[1],
        respect_floor=False,
        require_convergence=False,
    )
    assert machine._capacity_incomplete(capacity)
    hard = copy.deepcopy(capacity)
    hard.checks_run = 3
    hard.checks_passed = 2
    # Same diagnostic text deliberately hides an independent earlier check.
    assert hard.reason == capacity.reason and hard != capacity
    assert not machine._capacity_incomplete(hard)
    primary, forensic = (hard, capacity) if hard_position == "earned" else (capacity, hard)
    calls = []

    def verify_windows(*args, **kwargs):
        calls.append(kwargs)
        return forensic if "cycles" in kwargs else primary

    monkeypatch.setattr(verify, "run_verify", verify_windows)
    errors = machine._receipt_gate_terminal_errors()
    assert len(errors) == 2
    assert errors[0].verification is primary
    assert errors[1].verification is forensic
    assert str(errors[0]) == "receipt acceptance: " + capacity.reason
    assert str(errors[1]) == "receipt attempt: " + capacity.reason
    assert calls[0]["required_cycles"] == machine.clean_round_threshold
    assert "cycles" not in calls[0]
    assert calls[1]["cycles"] == [machine._written_cycles[-1]]
    assert calls[1]["required_cycles"] == 1
    assert calls[1]["respect_floor"] is False
    assert calls[1]["require_convergence"] is False
    assert [machine._capacity_incomplete(error.verification) for error in errors] == (
        [False, True] if hard_position == "earned" else [True, False]
    )
    machine._finalize_local_terminal()
    state = load_state(review_workspace / ".code-forge/state.json")
    assert state.verdict is Verdict.FAIL and not state.converged
    assert state.consecutive_clean_rounds == 0
    assert any(f.id == "RECEIPT_INVALID" and f.diagnostic_kind is None for f in state.findings)


@pytest.mark.parametrize(
    "mutation", ["metadata", "producer", "original_membership", "emitted_membership"]
)
def test_post_acquisition_mutation_cannot_manufacture_capacity_authority(
    review_workspace, monkeypatch, mutation
):
    def payload(machine, prompt):
        if "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("generic original", kind="conn", retryable=False)
        return VALID

    machine = _machine(review_workspace, monkeypatch, rounds=1, payload=payload)
    assert machine.run() is Verdict.FAIL
    result = verify.run_verify(
        review_workspace,
        machine.source_hash,
        verify.parse_diff_files(machine._receipt_diff()),
        diff_text=machine._receipt_diff(),
        required_cycles=1,
        cycles=[1],
        respect_floor=False,
        require_convergence=False,
    )
    assert not machine._capacity_incomplete(result)
    if mutation == "metadata":
        original = machine.l1_provider.acquisition_failures[0]
        original.provider_failure["kind"] = "truncated"
        original.diagnostic_kind = FindingDiagnosticKind.PROVIDER_CAPACITY
    elif mutation == "producer":
        machine.l1_provider = copy.copy(machine.l1_provider)
    elif mutation == "original_membership":
        machine.l1_provider.acquisition_failures[:] = [
            copy.deepcopy(f) for f in machine.l1_provider.acquisition_failures
        ]
    else:
        machine._acquisition_markers[:] = [copy.deepcopy(f) for f in machine._acquisition_markers]
    assert not machine._capacity_incomplete(result)
    machine._finalize_local_terminal()
    state = load_state(review_workspace / ".code-forge/state.json")
    assert state.verdict is Verdict.FAIL and not state.converged
    assert state.consecutive_clean_rounds == 0


def test_repeated_machine_run_refreshes_acquisition_and_fixval_invocation(review_workspace, monkeypatch):
    failing = [True]
    starts = []

    def payload(machine, prompt):
        if failing[0]:
            return _truncated(machine, prompt)
        # Capture occurs after the entire producer returns, not during transport.
        starts.append(machine._acquisition_authority)
        return VALID

    machine = _machine(review_workspace, monkeypatch, rounds=1, payload=payload)
    assert machine.run() is Verdict.FAIL
    first_invocation = machine._fixval_invocation_id
    first_snapshot = machine._acquisition_authority
    assert first_snapshot is not None
    failing[0] = False
    machine.max_total_rounds = 3
    assert machine.run() is Verdict.PASS
    assert starts and all(snapshot is None for snapshot in starts)
    state = load_state(review_workspace / ".code-forge/state.json")
    assert machine._fixval_invocation_id != first_invocation
    assert state.fixval_stage["invocation_id"] == machine._fixval_invocation_id
    assert state.fixval_stage["outcome"] == "SKIPPED"
    assert state.consecutive_clean_rounds == 3
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == [2, 3, 4]
    assert machine._acquisition_markers == []
