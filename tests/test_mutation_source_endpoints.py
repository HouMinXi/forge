# SPDX-License-Identifier: Apache-2.0
"""Stage A endpoint refusal controls; immutable lifetime binding remains open."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from code_forge import mutation
from code_forge.autofix import FixOutcome
from code_forge.baseline import GitRefBaseline, ResolvedReview, resolve_baseline
from code_forge.cli import _build_parser, _run_mutation_check
from code_forge.disposition import Disposition
from code_forge.factories import build_l2_runner
from code_forge.ledger import append_row
from code_forge.mutation_findings import is_mutation_survivor
from code_forge.state import Mode, StateFinding, Verdict, load_state
from tests.test_mutation_ledger_consumers import cli_row, make_machine
from tests.test_mutation_ledger_consumers import git_project as git_project
from tests.test_mutation_ledger_transport import native_project as native_project
from tests.test_mutation_ledger_transport import native_measurement as native_measurement


def working_review(project):
    return resolve_baseline(GitRefBaseline(project.head), GitRefBaseline("WORKING"),
                            [Path("calc.py")], project.root)


def _append_r7_row(project, row):
    append_row(project.root, row)


@pytest.mark.parametrize("drift", [False, True], ids=["unchanged-source", "baseline-source-edit"])
def test_cli_measured_working_source_requires_the_reviewed_postimage(
    git_project, native_measurement, monkeypatch, capsys, drift,
):
    findings, infra, evidence = native_measurement()
    assert infra == [] and evidence["completed_measurement"] is True
    _append_r7_row(git_project, cli_row(git_project, findings[0]))
    source = git_project.root / "calc.py"
    before = source.read_bytes()
    original_command = mutation.run_owned_command
    observed = []

    def execute(argv, **kwargs):
        if list(argv) == git_project.baseline and drift:
            source.write_text("def example(value):\n    return value + 3\n")
        if "run" in argv:
            observed.append({"source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                             "argv": list(argv), "cwd": str(kwargs["cwd"])})
        return original_command(argv, **kwargs)

    monkeypatch.setattr(mutation, "run_owned_command", execute)
    args = _build_parser().parse_args(["mutation-check"])
    exit_code = _run_mutation_check(args, git_project.root)
    output = capsys.readouterr()
    after = source.read_bytes()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=git_project.root,
                          check=True, capture_output=True, text=True).stdout.strip()
    (git_project.state / ".code-forge/r7-observation.json").write_text(json.dumps({
        "before_sha256": hashlib.sha256(before).hexdigest(),
        "after_sha256": hashlib.sha256(after).hexdigest(), "head_before": git_project.head,
        "head_after": head, "review_diff": git_project.diff, "native_commands": observed,
        "exit_code": exit_code, "stdout": output.out, "stderr": output.err,
        "ruling_fingerprint": findings[0].fingerprint, "native_id": findings[0].id,
    }))
    assert head == git_project.head and len(observed) == 1
    assert observed[0]["source_sha256"] == hashlib.sha256(after).hexdigest()
    assert (before != after) is drift
    assert exit_code == (1 if drift else 0)
    assert output.out == ""
    if drift:
        assert "mutation-check: PASS" not in output.err


@pytest.mark.parametrize("drift", [False, True], ids=["unchanged-source", "autofix-source-edit"])
def test_local_native_ruling_requires_the_postimage_measured_after_autofix(
    git_project, native_measurement, drift,
):
    findings, infra, evidence = native_measurement()
    assert infra == [] and evidence["completed_measurement"] is True
    _append_r7_row(git_project, cli_row(git_project, findings[0]))
    source = git_project.root / "calc.py"
    before = source.read_bytes()
    machine = make_machine(git_project, runner=mutation.run_mutation)
    machine.resolved_review = working_review(git_project)
    fix_calls = []

    class WorkingFixer:
        def fix(self, finding, mode_hint):
            fix_calls.append((finding.id, mode_hint))
            if drift:
                source.write_text("def example(value):\n    return value + 3\n")
            return FixOutcome.SUCCESS

    machine.autofixer = WorkingFixer()
    machine.l0_runner = lambda *_args: ([StateFinding(
        id="ordinary-source-edit", fingerprint="ordinary-source-edit", source="AST",
        disposition=Disposition.CONFIRMED, file="calc.py", line_range=[2, 2],
        description="ordinary source finding",
    )], [])
    machine._execute_round(1)
    current = [item for item in machine._state.findings if item.id == findings[0].id]
    after = source.read_bytes()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=git_project.root,
                          check=True, capture_output=True, text=True).stdout.strip()
    (git_project.state / ".code-forge/r7-observation.json").write_text(json.dumps({
        "before_sha256": hashlib.sha256(before).hexdigest(),
        "after_sha256": hashlib.sha256(after).hexdigest(), "head_before": git_project.head,
        "head_after": head, "review_diff": git_project.diff, "fix_calls": fix_calls,
        "native_id": findings[0].id, "ruling_fingerprint": findings[0].fingerprint,
        "current_dispositions": [item.disposition.value for item in current],
        "native_eligible": machine._mutation_measurement_eligible,
    }))
    assert head == git_project.head and len(fix_calls) == 1
    assert (before != after) is drift and len(current) == 1
    assert current[0].disposition is (Disposition.CONFIRMED if drift else Disposition.DISMISSED)


@pytest.mark.parametrize("factory", [False, True], ids=["direct-native", "exact-factory-partial"])
def test_working_positive_retains_three_round_native_ruling(
    git_project, native_measurement, monkeypatch, factory,
):
    findings, infra, proof = native_measurement()
    assert infra == [] and proof["completed_measurement"] is True
    append_row(git_project.root, cli_row(git_project, findings[0]))
    monkeypatch.setattr(shutil, "which", lambda _name: "/owned/mutmut")
    runner = build_l2_runner(cwd=git_project.root) if factory else mutation.run_mutation
    machine = make_machine(git_project, runner=runner)
    machine.resolved_review = working_review(git_project)
    assert machine.run() is Verdict.PASS
    assert machine._state.converged is True
    assert machine._state.consecutive_survivor_rounds == 0
    assert len(machine._state.round_history) == 3
    fingerprint = findings[0].fingerprint
    assert all(row["dispositions"][fingerprint] == "DISMISSED" for row in machine._state.round_history)
    saved = load_state(git_project.state / ".code-forge/state.json")
    assert saved.consecutive_survivor_rounds == 0
    assert not any("3 consecutive rounds" in message for message in saved.infra_errors)
    assert any(item.fingerprint == fingerprint and item.disposition is Disposition.DISMISSED
               for item in saved.findings)
    assert machine._mutation_measurement_eligible is True


@pytest.mark.parametrize("case", ["review-mismatch", "read-failure", "post-native-edit"])
def test_local_working_refuses_unavailable_or_changed_endpoint(
    git_project, native_measurement, monkeypatch, case,
):
    findings, _, _ = native_measurement()
    append_row(git_project.root, cli_row(git_project, findings[0]))
    machine = make_machine(git_project, runner=mutation.run_mutation)
    machine.resolved_review = working_review(git_project)
    source = git_project.root / "calc.py"
    if case == "review-mismatch":
        source.write_text("def example(value):\n    return value + 3\n")
    elif case == "read-failure":
        original_read = mutation._read_inventory_file

        def fail_review_read(path):
            if path == source:
                raise OSError("registered source read refusal")
            return original_read(path)

        monkeypatch.setattr(mutation, "_read_inventory_file", fail_review_read)
    else:
        original_command = mutation.run_owned_command

        def post_native_edit(argv, **kwargs):
            result = original_command(argv, **kwargs)
            if "results" in argv:
                source.write_text("def example(value):\n    return value + 3\n")
            return result

        monkeypatch.setattr(mutation, "run_owned_command", post_native_edit)
    machine._execute_round(0)
    assert machine._mutation_measurement_eligible is False
    current = [item for item in machine._state.findings if item.id == findings[0].id]
    assert len(current) == 1 and current[0].disposition is Disposition.CONFIRMED


@pytest.mark.parametrize("target_kind", [None, "INDEX", "COMMIT"])
def test_local_unknown_or_nonworking_subject_has_no_new_eligibility(
    git_project, native_measurement, target_kind,
):
    findings, _, _ = native_measurement()
    append_row(git_project.root, cli_row(git_project, findings[0]))
    machine = make_machine(git_project, runner=mutation.run_mutation)
    if target_kind is None:
        review = working_review(git_project)
        machine.resolved_review = ResolvedReview(
            review.source_files, review.baseline_content, review.git_diff, review.mode_hint,
            base_sha=review.base_sha, head_sha=review.head_sha,
        )
    else:
        machine.resolved_review = resolve_baseline(
            GitRefBaseline(git_project.head),
            GitRefBaseline("INDEX" if target_kind == "INDEX" else git_project.head),
            [Path("calc.py")], git_project.root,
        )
    current = machine._run_l2_phase()
    assert len(current) == 1 and is_mutation_survivor(current[0])
    assert machine._mutation_measurement_eligible is False


@pytest.mark.parametrize("case", ["capture-read-failure", "post-native-edit", "capture-diff-mismatch"])
def test_cli_endpoint_refusal_keeps_actual_survivor_visible(
    git_project, native_measurement, monkeypatch, capsys, case,
):
    findings, _, _ = native_measurement()
    append_row(git_project.root, cli_row(git_project, findings[0]))
    source = git_project.root / "calc.py"
    if case == "capture-read-failure":
        original_read = mutation._read_inventory_file

        def fail_review_read(path):
            if path == source:
                raise OSError("registered source read refusal")
            return original_read(path)

        monkeypatch.setattr(mutation, "_read_inventory_file", fail_review_read)
    elif case == "post-native-edit":
        original_command = mutation.run_owned_command

        def post_native_edit(argv, **kwargs):
            result = original_command(argv, **kwargs)
            if "results" in argv:
                source.write_text("def example(value):\n    return value + 3\n")
            return result

        monkeypatch.setattr(mutation, "run_owned_command", post_native_edit)
    else:
        from code_forge.git import _MACHINE_DIFF_OPTIONS
        original_run = subprocess.run

        def change_after_review_diff(argv, **kwargs):
            result = original_run(argv, **kwargs)
            if list(argv) == ["git", "diff", *_MACHINE_DIFF_OPTIONS, "HEAD"]:
                source.write_text("def example(value):\n    return value + 3\n")
            return result

        monkeypatch.setattr(subprocess, "run", change_after_review_diff)
    args = _build_parser().parse_args(["mutation-check"])
    assert _run_mutation_check(args, git_project.root) == 1
    output = capsys.readouterr()
    assert "native ruling refused" in output.err and "survivor(s) found" in output.err
    assert "mutation-check: PASS" not in output.err and output.out == ""


def test_local_pre_l2_check_is_independent_of_restored_post_endpoint(
    git_project, native_measurement, monkeypatch,
):
    findings, _, _ = native_measurement()
    append_row(git_project.root, cli_row(git_project, findings[0]))
    machine = make_machine(git_project, runner=mutation.run_mutation)
    machine.resolved_review = working_review(git_project)
    machine._execute_round(0)
    assert machine._mutation_measurement_eligible is True
    source = git_project.root / "calc.py"
    reviewed = source.read_bytes()
    source.write_text("def example(value):\n    return value + 3\n")
    original_command = mutation.run_owned_command
    restored = []

    def restore_on_baseline(argv, **kwargs):
        if list(argv) == git_project.baseline:
            source.write_bytes(reviewed)
            restored.append(True)
        return original_command(argv, **kwargs)

    monkeypatch.setattr(mutation, "run_owned_command", restore_on_baseline)
    current = machine._run_l2_phase()
    assert restored and source.read_bytes() == reviewed
    assert len(current) == 1 and is_mutation_survivor(current[0])
    assert machine._mutation_measurement_eligible is False


def test_local_original_review_guard_survives_multiple_autofix_rounds(
    git_project, native_measurement,
):
    findings, _, _ = native_measurement()
    append_row(git_project.root, cli_row(git_project, findings[0]))
    machine = make_machine(git_project, runner=mutation.run_mutation)
    machine.resolved_review = working_review(git_project)
    source = git_project.root / "calc.py"
    fixes = []

    class PersistentFixer:
        def fix(self, finding, mode_hint):
            fixes.append((finding.id, mode_hint))
            source.write_text("def example(value):\n    return value + 3\n")
            return FixOutcome.SUCCESS

    machine.autofixer = PersistentFixer()
    machine.l0_runner = lambda *_args: ([StateFinding(
        id="ordinary-persistent-edit", fingerprint="ordinary-persistent-edit", source="AST",
        disposition=Disposition.CONFIRMED, file="calc.py", line_range=[2, 2],
        description="ordinary source finding",
    )], [])
    for index in range(3):
        machine._execute_round(index)
        assert machine._mutation_measurement_eligible is False
        current = [item for item in machine._state.findings if item.id == findings[0].id]
        assert len(current) == 1 and current[0].disposition is Disposition.CONFIRMED
    assert fixes and len(machine._state.round_history) == 3
    assert all(row["dispositions"][findings[0].fingerprint] == "CONFIRMED"
               for row in machine._state.round_history)


@pytest.mark.parametrize("kind", ["ledger", "pin", "pass-name", "keyword"])
@pytest.mark.parametrize("native", [True, False], ids=["native-refusal", "ordinary-positive"])
def test_direct_ci_generic_suppression_keeps_native_survivors_blocking(
    git_project, native_measurement, monkeypatch, kind, native,
):
    findings, _, _ = native_measurement()
    item = deepcopy(findings[0])
    item.file = "calc.py"
    if not native:
        item.id = "ordinary-ci-control"
        item.source = "L0"
        item.fingerprint = "ordinary-ci-control"
    config = (git_project.state / ".code-forge/gate.yaml").read_text()
    if kind == "ledger":
        append_row(git_project.root, cli_row(git_project, item))
    elif kind == "pin":
        config += "pinned_paths: [calc.py]\n"
    elif kind == "pass-name":
        config += "style_downgrade:\n  pass_names: [" + item.source + "]\n"
    else:
        config += "style_downgrade:\n  keywords: [survived]\n"
    (git_project.state / ".code-forge/gate.yaml").write_text(config)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    machine = make_machine(git_project, mode=Mode.CI, runner=lambda *_a, **_k: ([deepcopy(item)], []))
    machine.resolved_review = working_review(git_project)
    assert machine.run() is (Verdict.FAIL if native else Verdict.PASS)
    current = next(finding for finding in machine._state.findings if finding.id == item.id)
    expected = Disposition.CONFIRMED if native else (
        Disposition.DISMISSED if kind in ("ledger", "pin") else Disposition.STYLE
    )
    assert current.disposition is expected
