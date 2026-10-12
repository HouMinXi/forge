# SPDX-License-Identifier: Apache-2.0
"""Real LOCAL, CI, and CLI consumers require the same exact native ruling."""
from copy import deepcopy
from dataclasses import replace
from functools import partial
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from code_forge import mutation
from code_forge.autofix import StubAutoFixer
from code_forge.cli import _build_parser, _run_ledger, _run_mutation_check
from code_forge.disposition import Disposition
from code_forge.factories import build_l2_runner
from code_forge.falsify import StubFalsifier
from code_forge.git import _MACHINE_DIFF_OPTIONS
from code_forge.ledger import (
    TerminalState, append_row, finding_suppression_key, iter_rows, resolve_ledger_root,
)
from code_forge.machine import StateMachine
from code_forge.mutation_findings import is_mutation_survivor
from code_forge.state import Mode, Verdict, load_state
from tests.test_mutation_ledger_disposition import matching_row as _original_matching_row
from tests.test_mutation_ledger_transport import (
    bound_context, bound_outcome, native_measurement as native_measurement,
    native_project as native_project, working_review as source_review,
)


def matching_row(root, finding, state=TerminalState.DISPROVED):
    row = _original_matching_row(root, finding, state)
    review = source_review(root)
    key = finding_suppression_key(
        base_sha=review.base_sha, head_sha=review.head_sha, git_diff=review.git_diff,
        file=finding.file, line=finding.line_range[0], source=finding.source,
        description=finding.description,
    )
    return replace(row, base_sha=review.base_sha, head_sha=review.head_sha, suppression_key=key)


def make_machine(project, *, mode=Mode.LOCAL, runner=None):
    values = dict(
        mode=mode, falsifier=StubFalsifier(), autofixer=StubAutoFixer(), revert_fn=lambda _f: None,
        resolved_review=source_review(project.root),
        source_hash="bound-mutation-control", baseline_spec_repr="owned process substitute",
        cwd=project.state, source_root=project.root, registry={}, l0_runner=lambda *_a: ([], []),
        coverage_l1_active=False, coverage_exempt_patterns=["*.py"],
        clean_round_threshold=3, max_total_rounds=3,
    )
    if runner is not None:
        values["l2_runner"] = runner
    machine = StateMachine(**values)
    machine._state.env_manifest = {}
    return machine


def put_result(project, payload):
    path = project.state / ".code-forge/mutation-result.json"
    path.write_text(json.dumps(payload))
    return path


@pytest.mark.parametrize("factory", [False, True], ids=["direct-native", "exact-factory-partial"])
def test_local_exact_native_ruling_keeps_three_round_survivor_counter_zero(
    native_project, native_measurement, monkeypatch, factory,
):
    findings, infra, proof = native_measurement()
    assert infra == [] and proof["completed_measurement"] is True
    append_row(native_project.root, matching_row(native_project.root, findings[0]))
    monkeypatch.setattr(shutil, "which", lambda _name: "/owned/mutmut")
    runner = build_l2_runner(cwd=native_project.root) if factory else mutation.run_mutation
    machine = make_machine(native_project, runner=runner)
    assert machine.run() is Verdict.PASS
    assert machine._state.converged is True
    assert machine._state.consecutive_survivor_rounds == 0
    assert len(machine._state.round_history) == 3
    fingerprint = findings[0].fingerprint
    assert all(row["dispositions"][fingerprint] == "DISMISSED" for row in machine._state.round_history)
    saved = load_state(native_project.state / ".code-forge/state.json")
    assert saved.consecutive_survivor_rounds == 0
    assert not any("3 consecutive rounds" in message for message in saved.infra_errors)
    assert any(item.fingerprint == fingerprint and item.disposition is Disposition.DISMISSED
               for item in saved.findings)
    assert all(Path(kwargs["cwd"]) == native_project.root for _argv, kwargs in native_project.calls)


def test_local_mixed_known_unknown_keeps_unknown_active(native_project, native_measurement):
    native_project.mutants["x_example__mutmut_2"] = "survived"
    findings, _, _ = native_measurement()
    append_row(native_project.root, matching_row(native_project.root, findings[0]))
    machine = make_machine(native_project, runner=mutation.run_mutation)
    assert machine.run() is Verdict.FAIL
    assert machine._state.consecutive_survivor_rounds == 3
    active = [item for item in machine._state.findings if is_mutation_survivor(item)]
    assert [item.fingerprint for item in active] == [findings[1].fingerprint]
    assert any(item.fingerprint == findings[0].fingerprint and item.disposition is Disposition.DISMISSED
               for item in machine._state.findings)


@pytest.mark.parametrize("kind", ["custom", "extra-partial-keyword", "foreign-partial-root"])
def test_custom_and_non_factory_forms_gain_no_native_proof(
    native_project, native_measurement, kind,
):
    findings, _, _ = native_measurement()
    append_row(native_project.root, matching_row(native_project.root, findings[0]))
    received = []
    if kind == "custom":
        def runner(diff_files, baseline_cmd, *, baseline_timeout=120):
            received.append((diff_files, baseline_cmd, baseline_timeout))
            return deepcopy(findings), []
    elif kind == "extra-partial-keyword":
        runner = partial(mutation.run_mutation, cwd=native_project.root, timeout=123)
    else:
        foreign = native_project.root.parent / "foreign"
        foreign.mkdir()
        (foreign / "calc.py").write_text(native_project.original)
        runner = partial(mutation.run_mutation, cwd=foreign)
    machine = make_machine(native_project, runner=runner)
    assert machine.run() is Verdict.FAIL
    assert machine._state.consecutive_survivor_rounds == 3
    assert machine._mutation_measurement_eligible is False
    assert any(is_mutation_survivor(item) for item in machine._state.findings)
    if kind == "custom":
        assert len(received) == 3


@pytest.mark.parametrize("early_exit", ["no-files", "missing-gate", "custom", "native-error"])
def test_native_proof_is_reset_before_every_call_and_early_exit(
    native_project, early_exit,
):
    machine = make_machine(native_project, runner=mutation.run_mutation)
    assert machine._run_l2_phase() and machine._mutation_measurement_eligible is True
    if early_exit == "no-files":
        machine.resolved_review = replace(machine.resolved_review, source_files=[])
    elif early_exit == "missing-gate":
        (native_project.state / ".code-forge/gate.yaml").unlink()
    elif early_exit == "custom":
        machine.l2_runner = lambda *_a, **_k: ([], [])
    else:
        native_project.error = "OWNED_RUN_REFUSED"
    machine._run_l2_phase()
    assert machine._mutation_measurement_eligible is False


@pytest.mark.parametrize("later", ["changed-key", "foreign-root", "unkeyed", "escaped", "unadjudicated", "malformed"])
def test_local_later_row_revokes_prior_round_dismissal(native_project, native_measurement, later):
    findings, _, _ = native_measurement()
    row = matching_row(native_project.root, findings[0])
    append_row(native_project.root, row)
    changes = {
        "changed-key": {"suppression_key": "changed"}, "foreign-root": {"repo_root": "/other"},
        "unkeyed": {"suppression_key": ""}, "escaped": {"terminal_state": TerminalState.ESCAPED},
        "unadjudicated": {"terminal_state": TerminalState.UNADJUDICATED},
    }.get(later)
    machine = make_machine(native_project, runner=mutation.run_mutation)
    machine._execute_round(0)
    assert not any(is_mutation_survivor(item) for item in machine._state.findings)
    if later == "malformed":
        with (native_project.root / ".code-forge/ledger.jsonl").open("a") as stream:
            stream.write("malformed\n")
    else:
        append_row(native_project.root, replace(row, **changes))
    machine._execute_round(1)
    assert any(is_mutation_survivor(item) for item in machine._state.findings)
    assert machine._state.round_history[-1]["dispositions"][findings[0].fingerprint] == "CONFIRMED"


def test_local_native_dismissal_does_not_leak_into_later_custom_run(native_project, native_measurement):
    findings, _, _ = native_measurement()
    append_row(native_project.root, matching_row(native_project.root, findings[0]))
    machine = make_machine(native_project, runner=mutation.run_mutation)
    machine._execute_round(0)
    assert not any(is_mutation_survivor(item) for item in machine._state.findings)
    machine.l2_runner = lambda *_a, **_k: (deepcopy(findings), [])
    machine._execute_round(1)
    assert machine._mutation_measurement_eligible is False
    assert any(is_mutation_survivor(item) for item in machine._state.findings)


@pytest.mark.parametrize("authority", ["known", "revoked", "unproved"])
def test_current_l2_survivor_does_not_inherit_prior_promotion(
    native_project, native_measurement, authority,
):
    findings, _, _ = native_measurement()
    row = matching_row(native_project.root, findings[0])
    append_row(native_project.root, row)
    machine = make_machine(native_project, runner=mutation.run_mutation)
    machine._execute_round(0)
    fingerprint = findings[0].fingerprint
    for item in machine._state.findings:
        if item.fingerprint == fingerprint:
            item.disposition = Disposition.UNCERTAIN
    machine._state.round_history[-1]["dispositions"][fingerprint] = "UNCERTAIN"
    machine._state.fix_attempts[fingerprint] = machine.max_fix_attempts
    if authority == "revoked":
        append_row(native_project.root, replace(row, terminal_state=TerminalState.UNADJUDICATED))
    elif authority == "unproved":
        machine.l2_runner = lambda *_a, **_k: (deepcopy(findings), [])
    machine._execute_round(1)
    item = next(item for item in machine._state.findings if item.fingerprint == fingerprint)
    assert item.disposition is (Disposition.DISMISSED if authority == "known" else Disposition.CONFIRMED)


def test_non_mutant_custom_claim_retains_ordinary_carry_forward(native_project, native_measurement):
    findings, _, _ = native_measurement()
    append_row(native_project.root, matching_row(native_project.root, findings[0]))
    machine = make_machine(native_project, runner=mutation.run_mutation)
    machine._execute_round(0)
    ordinary = deepcopy(findings[0])
    ordinary.source = "L0"
    machine.l2_runner = lambda *_a, **_k: ([ordinary], [])
    machine._execute_round(1)
    assert machine._mutation_measurement_eligible is False
    assert ordinary.disposition is Disposition.DISMISSED


def test_ci_bound_native_result_is_consumed_once_with_original_audit_count(
    native_project, native_measurement, monkeypatch,
):
    payload = bound_outcome(native_project, native_measurement)
    findings, _, _ = native_measurement()
    append_row(native_project.root, matching_row(native_project.root, findings[0]))
    frozen = deepcopy(payload)
    result = put_result(native_project, payload)
    messages = []
    monkeypatch.setattr("code_forge.machine.progress.emit", messages.append)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    machine = make_machine(native_project, mode=Mode.CI)
    assert machine.run() is Verdict.PASS
    assert payload == frozen and not result.exists()
    assert "mutation: 0 unresolved of 1 original survivors" in messages
    assert any(findings[0].fingerprint in message and "dismissed" in message for message in messages)
    messages.clear()
    assert machine.run() is Verdict.PASS
    assert not any("original survivors" in message for message in messages)


@pytest.mark.parametrize("case", ["valid-custom", "valid-extra", "absent-member", "killed-member"])
def test_ci_actual_inventory_binding_precedes_matching_custom_fingerprint_ruling(
    native_project, native_measurement, monkeypatch, case,
):
    findings, infra, evidence = native_measurement()
    assert infra == [] and evidence["completed_measurement"] is True
    item = findings[0]
    item.fingerprint = "example"
    payload = mutation._mutation_outcome(
        findings, infra, evidence, review_context=bound_context(native_project.root),
    )
    actual_id = item.id
    if case == "valid-extra":
        payload["inventory"]["calc.x_terminal_extra__mutmut_9"] = "killed"
    elif case == "absent-member":
        payload["survivors"][0] = "mutant-never-measured"
        payload["survivor_details"][0]["id"] = "mutant-never-measured"
    elif case == "killed-member":
        payload["inventory"][actual_id[len("mutant-"):]] = "killed"
    append_row(native_project.root, matching_row(native_project.root, item))
    result = put_result(native_project, payload)
    frozen = deepcopy(payload)
    messages = []
    monkeypatch.setattr("code_forge.machine.progress.emit", messages.append)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    machine = make_machine(native_project, mode=Mode.CI)
    expected = Verdict.PASS if case.startswith("valid-") else Verdict.FAIL
    assert machine.run() is expected
    assert not result.exists() and payload == frozen
    if expected is Verdict.PASS:
        assert "mutation: 0 unresolved of 1 original survivors" in messages
        assert any("example dismissed" in message for message in messages)
    else:
        assert "CI: invalid bound mutation survivor details" in machine._state.infra_errors
        assert not any("example dismissed" in message for message in messages)


@pytest.mark.parametrize("case", ["unknown", "later-row", "legacy-integers", "location-only"])
def test_ci_unresolved_or_unbound_survivors_still_fail(
    native_project, native_measurement, monkeypatch, case,
):
    payload = bound_outcome(native_project, native_measurement)
    findings, _, _ = native_measurement()
    row = matching_row(native_project.root, findings[0])
    if case != "unknown":
        append_row(native_project.root, row)
    if case == "later-row":
        append_row(native_project.root, replace(row, terminal_state=TerminalState.UNADJUDICATED))
    elif case == "legacy-integers":
        payload = {"status": "done", "baseline_passed": True, "survivors": [1, 2]}
    elif case == "location-only":
        payload["survivor_details"] = [{"file": "", "line_range": [0, 0]}]
    result = put_result(native_project, payload)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    machine = make_machine(native_project, mode=Mode.CI)
    assert machine.run() is Verdict.FAIL
    assert machine._state.converged is False and not result.exists()


@pytest.mark.parametrize("invalid", [{}, False, "bad", "unknown-version", "bool-version", "null-version"])
def test_ci_bound_invalid_empty_original_fails_before_legacy_clean_verdict(
    native_project, native_measurement, monkeypatch, invalid,
):
    native_project.mutants = {"x_example__mutmut_1": "killed"}
    findings, infra, evidence = native_measurement()
    context = bound_context(native_project.root) if isinstance(invalid, str) and "version" in invalid else invalid
    payload = mutation._mutation_outcome(findings, infra, evidence, review_context=context)
    if invalid == "unknown-version":
        payload["survivor_details_version"] = 2
    elif invalid == "bool-version":
        payload["survivor_details_version"] = True
    elif invalid == "null-version":
        payload["survivor_details_version"] = None
    assert payload["survivors"] == [] and "survivor_details_version" in payload
    result = put_result(native_project, payload)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    machine = make_machine(native_project, mode=Mode.CI)
    assert machine.run() is Verdict.FAIL
    assert "CI: invalid bound mutation survivor details" in machine._state.infra_errors
    assert not result.exists() and not any(is_mutation_survivor(item) for item in machine._state.findings)


@pytest.mark.parametrize("case,expected", [
    ("clean", Verdict.PASS), ("survivor", Verdict.FAIL), ("error", Verdict.PASS),
])
def test_ci_old_external_no_context_outcomes_keep_legacy_behavior(
    native_project, native_measurement, monkeypatch, case, expected,
):
    if case == "clean":
        native_project.mutants = {"x_example__mutmut_1": "killed"}
    if case == "error":
        native_project.error = "OWNED_RUN_REFUSED"
    findings, infra, evidence = native_measurement()
    if findings and case == "survivor":
        append_row(native_project.root, matching_row(native_project.root, findings[0]))
    payload = mutation._mutation_outcome(findings, infra, evidence)
    assert "survivor_details_version" not in payload
    result = put_result(native_project, payload)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    machine = make_machine(native_project, mode=Mode.CI)
    assert machine.run() is expected and not result.exists()
    if case == "error":
        assert any("CI: mutation error:" in message for message in machine._state.infra_errors)


def test_ci_receipt_refusal_precedes_result_consumption(native_project, native_measurement, monkeypatch):
    payload = bound_outcome(native_project, native_measurement)
    result = put_result(native_project, payload)
    original = result.read_bytes()
    machine = make_machine(native_project, mode=Mode.CI)
    monkeypatch.setattr(machine, "_receipt_gate_round_errors", lambda: ["owned receipt refusal"])
    assert machine.run() is Verdict.FAIL
    assert result.read_bytes() == original


def test_ci_launch_forwards_real_bound_context_to_generated_native_payload(
    native_project, monkeypatch, run_detached_payload,
):
    scripts = []
    original_popen = mutation.subprocess.Popen

    def capture(argv, **kwargs):
        if (len(argv) != 3 or list(argv[:2]) != [sys.executable, "-c"]
                or kwargs.get("start_new_session") is not True
                or kwargs.get("close_fds") is not True
                or kwargs.get("cwd") != native_project.root
                or any(kwargs.get(name) != subprocess.DEVNULL
                       for name in ("stdin", "stdout", "stderr"))):
            return original_popen(argv, **kwargs)
        scripts.append(argv[2])
        return SimpleNamespace(pid=1234, wait=lambda timeout: 0)

    monkeypatch.setattr(shutil, "which", lambda _name: "/owned/mutmut")
    monkeypatch.setattr(mutation.subprocess, "Popen", capture)
    machine = make_machine(native_project, mode=Mode.CI)
    assert machine.run() is Verdict.PENDING and len(scripts) == 1
    run_detached_payload(scripts[0])
    data = json.loads((native_project.state / ".code-forge/mutation-result.json").read_text())
    assert data["review_context"] == bound_context(native_project.root)
    assert data["survivor_details_version"] == 1
    assert data["survivor_details"][0]["fingerprint"] == "mutant:calc.x_example__mutmut_1"


@pytest.fixture
def git_project(native_project):
    root = native_project.root
    (root / ".code-forge").mkdir(exist_ok=True)
    shutil.copyfile(native_project.state / ".code-forge/gate.yaml", root / ".code-forge/gate.yaml")
    native_project.head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True,
    ).stdout.strip()
    native_project.diff = subprocess.run(
        ["git", "diff", *_MACHINE_DIFF_OPTIONS, "HEAD"], cwd=root, check=True, capture_output=True, text=True,
    ).stdout
    return native_project


def cli_row(project, finding, state=TerminalState.DISPROVED):
    key = finding_suppression_key(
        base_sha=project.head, head_sha=project.head, git_diff=project.diff, file=finding.file,
        line=finding.line_range[0], source=finding.source, description=finding.description,
    )
    return replace(matching_row(project.root, finding, state), base_sha=project.head,
                   head_sha=project.head, suppression_key=key)


@pytest.mark.parametrize("state", [TerminalState.FIXED, TerminalState.DISPROVED, TerminalState.DUPLICATE])
def test_cli_native_exact_ruling_reports_pass(git_project, native_measurement, capsys, state):
    findings, infra, proof = native_measurement()
    assert not infra and proof["completed_measurement"] is True
    append_row(git_project.root, cli_row(git_project, findings[0], state))
    args = _build_parser().parse_args(["mutation-check"])
    assert _run_mutation_check(args, git_project.root) == 0
    output = capsys.readouterr()
    assert output.out == "" and "mutation-check: PASS" in output.err
    assert findings[0].fingerprint in output.err and "dismissed" in output.err


@pytest.mark.parametrize("case", ["unknown", "revoked", "unkeyed", "unbound-diff", "head-drift", "mixed"])
def test_cli_unknown_revoked_and_unbound_measurements_still_fail(
    git_project, native_measurement, monkeypatch, capsys, case,
):
    if case == "mixed":
        git_project.mutants["x_example__mutmut_2"] = "survived"
    findings, _, _ = native_measurement()
    row = cli_row(git_project, findings[0])
    if case != "unknown":
        append_row(git_project.root, row)
    if case in {"revoked", "unkeyed"}:
        append_row(git_project.root, replace(row, suppression_key="" if case == "unkeyed" else "changed"))
    argv = ["mutation-check"]
    if case == "unbound-diff":
        patch = git_project.root.parent / "unbound.patch"
        patch.write_text(git_project.diff)
        argv += ["--diff", str(patch)]
    if case == "head-drift":
        original_run = subprocess.run
        captured = []

        def drift(argv, **kwargs):
            if argv == ["git", "rev-parse", "HEAD"]:
                captured.append(argv)
                if len(captured) == 2:
                    return subprocess.CompletedProcess(argv, 0, "c" * 40 + "\n", "")
            return original_run(argv, **kwargs)

        monkeypatch.setattr(subprocess, "run", drift)
    assert _run_mutation_check(_build_parser().parse_args(argv), git_project.root) == 1
    assert "survivor(s) found" in capsys.readouterr().err


def test_cli_native_run_error_is_not_erased_by_ruling(git_project, native_measurement, capsys):
    findings, _, _ = native_measurement()
    append_row(git_project.root, cli_row(git_project, findings[0]))
    git_project.error = "OWNED_RUN_REFUSED"
    assert _run_mutation_check(_build_parser().parse_args(["mutation-check"]), git_project.root) != 0
    assert "OWNED_RUN_REFUSED" in capsys.readouterr().err


@pytest.mark.parametrize("mode", [Mode.LOCAL, Mode.CI])
def test_key_writers_bind_source_root_and_append_changed_claim(native_project, native_measurement, mode):
    findings, _, _ = native_measurement()
    machine = make_machine(native_project, mode=mode)
    item = findings[0]
    if mode is Mode.LOCAL:
        item.disposition = Disposition.DISMISSED
    machine._state.findings = [item]
    writer = machine._write_ledger_rows if mode is Mode.LOCAL else machine._write_ci_ledger_rows
    assert writer() == 1
    assert writer() == 0
    old = list(iter_rows(native_project.root, strict=True))[-1]
    assert old.repo_root == str(resolve_ledger_root(native_project.root).resolve())
    assert old.suppression_key == machine._finding_suppression_key(item) != ""
    assert not (native_project.state / ".code-forge/ledger.jsonl").exists()
    item.description += " changed claim"
    assert writer() == 1
    newest = list(iter_rows(native_project.root, strict=True))[-1]
    assert newest.fingerprint == old.fingerprint and newest.suppression_key != old.suppression_key


@pytest.mark.parametrize("changed_shas", [False, True])
def test_adjudication_retains_key_only_for_same_review_shas(
    native_project, native_measurement, changed_shas,
):
    findings, _, _ = native_measurement()
    append_row(native_project.root, matching_row(native_project.root, findings[0], TerminalState.UNADJUDICATED))
    argv = ["ledger", "adjudicate", findings[0].fingerprint, "DISPROVED", "--evidence", "owned ruling"]
    if changed_shas:
        argv += ["--base-sha", "c" * 40, "--head-sha", "d" * 40]
    assert _run_ledger(_build_parser().parse_args(argv), native_project.root) == 0
    rows = list(iter_rows(native_project.root, strict=True))
    assert rows[-1].terminal_state is TerminalState.DISPROVED
    assert rows[-1].suppression_key == ("" if changed_shas else rows[0].suppression_key)
    assert rows[-1].file == rows[0].file and rows[-1].line == rows[0].line





def _append_owned_row(project, row):
    root = project.root.absolute()
    assert root.resolve() == root
    assert resolve_ledger_root(root).resolve() == root
    append_row(root, row)


def _record_inventory_observation(project, payload, verdict, errors):
    (project.state / ".code-forge/inventory-carrier-observation.json").write_text(json.dumps({
        "inventory": payload["inventory"], "survivors": payload["survivors"],
        "details": payload["survivor_details"], "verdict": verdict.value,
        "infra_errors": errors,
    }))


@pytest.mark.parametrize("retained", [1, 0], ids=["missing-second", "missing-all"])
def test_ci_missing_survived_inventory_identity_cannot_be_erased_by_matching_ruling(
    native_project, native_measurement, monkeypatch, retained,
):
    native_project.mutants["x_example__mutmut_2"] = "survived"
    findings, infra, evidence = native_measurement()
    assert infra == [] and evidence["completed_measurement"] is True
    payload = mutation._mutation_outcome(
        findings, infra, evidence, review_context=bound_context(native_project.root),
    )
    payload["survivors"] = payload["survivors"][:retained]
    payload["survivor_details"] = payload["survivor_details"][:retained]
    _append_owned_row(native_project, matching_row(native_project.root, findings[0]))
    put_result(native_project, payload)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    machine = make_machine(native_project, mode=Mode.CI)
    verdict = machine.run()
    _record_inventory_observation(native_project, payload, verdict, machine._state.infra_errors)
    assert verdict is Verdict.FAIL
    assert "CI: invalid bound mutation survivor details" in machine._state.infra_errors


@pytest.mark.parametrize("status", ["pending", "unknown", "not checked", "skipped", "SURVIVED"])
def test_ci_nonterminal_inventory_cannot_be_erased_by_matching_ruling(
    native_project, native_measurement, monkeypatch, status,
):
    findings, infra, evidence = native_measurement()
    assert infra == [] and evidence["completed_measurement"] is True
    payload = mutation._mutation_outcome(
        findings, infra, evidence, review_context=bound_context(native_project.root),
    )
    payload["inventory"]["calc.x_extra__mutmut_9"] = status
    _append_owned_row(native_project, matching_row(native_project.root, findings[0]))
    put_result(native_project, payload)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    machine = make_machine(native_project, mode=Mode.CI)
    verdict = machine.run()
    _record_inventory_observation(native_project, payload, verdict, machine._state.infra_errors)
    assert verdict is Verdict.FAIL
    assert "CI: invalid bound mutation survivor details" in machine._state.infra_errors
