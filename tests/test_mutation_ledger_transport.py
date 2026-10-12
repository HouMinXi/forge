# SPDX-License-Identifier: Apache-2.0
"""The native producer carries exact identities; malformed bindings grant none.

Only the owned command seam is substituted for native measurements. Inventory
creation, result parsing, workspace cleanup, and generated payload code are real.
"""
import builtins
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from code_forge import mutation
from code_forge.baseline import GitRefBaseline, resolve_baseline
from code_forge.machine import mutation_result_verdict
from code_forge.mutation_findings import (
    decode_mutation_survivor_details, mutation_review_context,
)
from code_forge.state import Verdict
from tests.mutation_result_fixture import write_inventory
from tests.test_mutation_ledger_disposition import make_survivor as make_survivor


@pytest.fixture
def native_project(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    original = "def example(value):\n    return value + 1\n"
    (root / "calc.py").write_text(original)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "calc.py"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=Owned Test", "-c", "user.email=owned@example.invalid",
                    "commit", "-qm", "owned initial"], cwd=root, check=True)
    state = tmp_path / "state"
    subprocess.run(["git", "worktree", "add", "--detach", str(state), "HEAD"], cwd=root,
                   check=True, capture_output=True, text=True)
    (state / ".code-forge").mkdir(parents=True)
    (root / "calc.py").write_text("def example(value):\n    return value + 2\n")
    original = (root / "calc.py").read_text()
    baseline = [sys.executable, "-c", "pass"]
    (state / ".code-forge/gate.yaml").write_text(
        "test:\n  command: " + json.dumps(baseline) + "\n"
    )
    control = SimpleNamespace(
        root=root, state=state, baseline=baseline, calls=[],
        mutants={"x_example__mutmut_1": "survived"}, error=None, output="",
        original=original,
    )

    def execute(argv, **kwargs):
        control.calls.append((list(argv), dict(kwargs)))
        if "run" in argv:
            if control.error:
                return subprocess.CompletedProcess(argv, 2, "", control.error)
            control.output = write_inventory(Path(kwargs["cwd"]), "calc.py", control.mutants)
        if "results" in argv:
            return subprocess.CompletedProcess(argv, 0, control.output, "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(mutation, "run_owned_command", execute)
    return control


@pytest.fixture
def native_measurement(native_project):
    def measure():
        evidence = {}
        findings, infra = mutation.run_mutation(
            ["calc.py"], native_project.baseline, cwd=native_project.root, _evidence=evidence,
        )
        assert (native_project.root / "calc.py").read_text() == native_project.original
        assert not (native_project.root / "mutants").exists()
        assert not (native_project.root / "setup.cfg").exists()
        return findings, infra, evidence
    return measure


def working_review(root):
    return resolve_baseline(GitRefBaseline("HEAD"), GitRefBaseline("WORKING"),
                            [Path("calc.py")], root)


def bound_context(root):
    review = working_review(root)
    return mutation_review_context(source_root=root, base_sha=review.base_sha,
                                   head_sha=review.head_sha, git_diff=review.git_diff)


def bound_outcome(native_project, native_measurement):
    findings, infra, evidence = native_measurement()
    assert infra == [] and evidence["completed_measurement"] is True
    return mutation._mutation_outcome(
        findings, infra, evidence, review_context=bound_context(native_project.root),
    )


@pytest.mark.parametrize("fallback", [False, True], ids=["source-layout", "fallback-layout"])
def test_generated_native_producer_preserves_actual_identity_and_context(
    native_project, monkeypatch, run_detached_payload, fallback,
):
    context = bound_context(native_project.root)
    scripts = []

    def capture(argv, **kwargs):
        assert kwargs["cwd"] == native_project.root
        assert kwargs["close_fds"] is True and kwargs["start_new_session"] is True
        scripts.append(argv[2])
        return SimpleNamespace(pid=1234, wait=lambda timeout: 0)

    monkeypatch.setattr(mutation.subprocess, "Popen", capture)
    result = native_project.state / ".code-forge/mutation-result.json"
    assert mutation.launch_detached_mutation(
        ["calc.py"], native_project.baseline, native_project.root, result, review_context=context,
    )
    assert json.loads(result.read_text())["survivor_details_version"] == 1
    original_import = builtins.__import__
    refused = []

    def import_once(name, *args, **kwargs):
        if fallback and name == "code_forge.mutation" and not refused:
            refused.append(name)
            raise ImportError("owned first-layout refusal")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_once)
    monkeypatch.chdir(native_project.root)
    run_detached_payload(scripts[0])
    data = json.loads(result.read_text())
    assert len(refused) == int(fallback)
    assert data["status"] == "done" and data["baseline_passed"] is True
    assert data["completed_measurement"] is True and data["review_context"] == context
    assert data["survivors"] == ["mutant-calc.x_example__mutmut_1"]
    assert data["survivor_details"] == [{
        "id": "mutant-calc.x_example__mutmut_1", "fingerprint": "mutant:calc.x_example__mutmut_1",
        "source": "MUTANT", "disposition": "CONFIRMED", "file": "", "line_range": [0, 0],
        "description": "mutant survived: calc.x_example__mutmut_1",
    }]
    decoded = decode_mutation_survivor_details(data, review_context=context)
    assert decoded is not None and decoded[0].fingerprint == data["survivor_details"][0]["fingerprint"]
    assert (native_project.root / "calc.py").read_text() == native_project.original
    assert not (native_project.root / "mutants").exists()


def test_actual_custom_fingerprint_is_not_derived_from_survivor_id(native_project, native_measurement):
    findings, infra, evidence = native_measurement()
    item = findings[0]
    actual_id = item.id
    item.fingerprint = "example"
    context = bound_context(native_project.root)
    data = mutation._mutation_outcome(
        findings, infra, evidence, review_context=context,
    )
    decoded = decode_mutation_survivor_details(data, review_context=context)
    assert decoded is not None and decoded[0].id == actual_id
    assert decoded[0].fingerprint == "example" and decoded[0].file == ""
    assert decoded[0].line_range == [0, 0]


@pytest.mark.parametrize("status", [None, "killed", "timeout", "pending", "SURVIVED", "survived "])
def test_claim_requires_its_actual_survived_inventory_member(native_project, native_measurement, status):
    data = bound_outcome(native_project, native_measurement)
    identity = data["survivors"][0]
    key = identity[len("mutant-"):]
    if status is None:
        data["survivors"][0] = "mutant-never-measured"
        data["survivor_details"][0]["id"] = "mutant-never-measured"
    else:
        data["inventory"][key] = status
    data["survivor_details"][0]["fingerprint"] = "unrelated-actual-fingerprint"
    data["inventory"]["unrelated-actual-fingerprint"] = "survived"
    assert decode_mutation_survivor_details(data, review_context=bound_context(native_project.root)) is None


def test_actual_ids_preserve_producer_order_with_unrelated_inventory_entries(native_project, native_measurement):
    native_project.mutants["x_example__mutmut_2"] = "survived"
    data = bound_outcome(native_project, native_measurement)
    original_ids = list(data["survivors"])
    original_details = deepcopy(data["survivor_details"])
    data["inventory"] = dict(reversed(list(data["inventory"].items())))
    data["inventory"].update({"type.checked.member": "caught by type check", "unrelated.member": "killed"})
    decoded = decode_mutation_survivor_details(data, review_context=bound_context(native_project.root))
    assert decoded is not None and [item.id for item in decoded] == original_ids
    assert [item.fingerprint for item in decoded] == [item["fingerprint"] for item in original_details]
    data["inventory"][original_ids[1][len("mutant-"):]] = "killed"
    assert decode_mutation_survivor_details(data, review_context=bound_context(native_project.root)) is None


@pytest.mark.parametrize("context", [None, {}, False, "bad", 0, []])
def test_producer_version_gate_is_explicit_none(native_project, native_measurement, context):
    findings, infra, evidence = native_measurement()
    data = mutation._mutation_outcome(findings, infra, evidence, review_context=context)
    assert ("survivor_details_version" in data) is (context is not None)
    if context is not None:
        assert data["survivor_details_version"] == 1
        assert decode_mutation_survivor_details(data, review_context=bound_context(native_project.root)) is None


@pytest.mark.parametrize("context", [None, {}, False])
def test_generated_producer_version_gate_with_empty_original(
    native_project, monkeypatch, run_detached_payload, context,
):
    native_project.mutants = {"x_example__mutmut_1": "killed"}
    review_context = bound_context(native_project.root)
    scripts = []

    def capture(argv, **kwargs):
        scripts.append(argv[2])
        return SimpleNamespace(pid=1234, wait=lambda timeout: 0)

    monkeypatch.setattr(mutation.subprocess, "Popen", capture)
    result = native_project.state / ".code-forge/mutation-result.json"
    assert mutation.launch_detached_mutation(
        ["calc.py"], native_project.baseline, native_project.root, result, review_context=context,
    )
    initial = json.loads(result.read_text())
    assert ("survivor_details_version" in initial) is (context is not None)
    run_detached_payload(scripts[0])
    data = json.loads(result.read_text())
    assert data["status"] == "done" and data["survivors"] == []
    assert ("survivor_details_version" in data) is (context is not None)
    assert decode_mutation_survivor_details(data, review_context=review_context) is None


@pytest.mark.parametrize("case,expected", [
    ("clean", None), ("survivor", Verdict.FAIL), ("error", None),
])
def test_old_no_context_outcomes_keep_legacy_decision(native_project, native_measurement, case, expected):
    if case == "clean":
        native_project.mutants = {"x_example__mutmut_1": "killed"}
    if case == "error":
        native_project.error = "OWNED_RUN_REFUSED"
    findings, infra, evidence = native_measurement()
    omitted = mutation._mutation_outcome(findings, infra, evidence)
    explicit_none = mutation._mutation_outcome(findings, infra, evidence, review_context=None)
    assert omitted == explicit_none and "survivor_details_version" not in omitted
    assert omitted["review_context"] is None
    assert mutation_result_verdict(omitted) is expected
    assert decode_mutation_survivor_details(omitted, review_context=bound_context(native_project.root)) is None


@pytest.mark.parametrize("context", [{}, False, "bad"])
def test_malformed_non_null_context_with_empty_original_is_versioned_and_invalid(
    native_project, native_measurement, context,
):
    native_project.mutants = {"x_example__mutmut_1": "killed"}
    findings, infra, evidence = native_measurement()
    data = mutation._mutation_outcome(findings, infra, evidence, review_context=context)
    assert data["survivors"] == [] and data["survivor_details_version"] == 1
    assert decode_mutation_survivor_details(data, review_context=bound_context(native_project.root)) is None


@pytest.mark.parametrize("field,value", [
    ("survivor_details_version", True), ("survivor_details_version", 2),
    ("survivor_details_version", None), ("survivor_details", []),
    ("survivors", ["mutant-other"]), ("survivors", [1]), ("survivors", "not-list"),
    ("inventory", {}), ("inventory", {"native": False}), ("baseline_passed", False),
    ("completed_measurement", 1), ("status", "error"), ("infra_errors", ["failed"]),
    ("message", "failed"), ("review_context", None), ("review_context", {}),
    ("publication_error", "failed"), ("cancellation_type", "KeyboardInterrupt"),
    ("cleanup_error", "failed"), ("process_failure", "failed"), ("cancelled", True),
    ("cleanup_complete", False),
])
def test_entire_carrier_must_be_valid(native_project, native_measurement, field, value):
    data = bound_outcome(native_project, native_measurement)
    data[field] = value
    frozen = deepcopy(data)
    assert decode_mutation_survivor_details(data, review_context=bound_context(native_project.root)) is None
    assert data == frozen


@pytest.mark.parametrize("field,value", [
    ("id", "mutant-"), ("fingerprint", ""), ("source", "L0"),
    ("disposition", "DISMISSED"), ("file", None), ("description", ""),
    ("line_range", [False, 0]), ("line_range", [0.0, 0]), ("line_range", [-1, 0]),
    ("line_range", [float("nan"), 0]),
    ("line_range", [2, 1]), ("line_range", [0]), ("line_range", None),
])
def test_each_claim_field_must_be_valid(native_project, native_measurement, field, value):
    data = bound_outcome(native_project, native_measurement)
    data["survivor_details"][0][field] = value
    assert decode_mutation_survivor_details(data, review_context=bound_context(native_project.root)) is None


@pytest.mark.parametrize("field,value", [
    ("base_sha", "c" * 40), ("head_sha", "c" * 40), ("diff_sha256", "c" * 64),
    ("source_repo_root", "/other"), ("ledger_root", "/other"), ("base_sha", False),
    ("unknown", "extra"),
])
def test_each_review_context_binding_must_match(native_project, native_measurement, field, value):
    data = bound_outcome(native_project, native_measurement)
    data["review_context"][field] = value
    assert decode_mutation_survivor_details(data, review_context=bound_context(native_project.root)) is None


@pytest.mark.parametrize("duplicate", ["id", "fingerprint", "order"])
def test_conflicting_and_reordered_identity_carriers_are_refused(
    native_project, native_measurement, duplicate,
):
    native_project.mutants["x_example__mutmut_2"] = "survived"
    data = bound_outcome(native_project, native_measurement)
    if duplicate == "order":
        data["survivor_details"].reverse()
    else:
        data["survivor_details"][1][duplicate] = data["survivor_details"][0][duplicate]
        if duplicate == "id":
            data["survivors"][1] = data["survivors"][0]
    assert decode_mutation_survivor_details(data, review_context=bound_context(native_project.root)) is None


def test_bound_clean_measurement_decodes_without_inventing_survivor(native_project, native_measurement):
    native_project.mutants = {"x_example__mutmut_1": "killed"}
    data = bound_outcome(native_project, native_measurement)
    assert data["survivors"] == []
    assert decode_mutation_survivor_details(data, review_context=bound_context(native_project.root)) == []


def test_error_publication_clears_new_authority_fields(native_project):
    result = native_project.state / ".code-forge/mutation-result.json"
    data = {"survivor_details_version": 1, "survivors": ["mutant-old"],
            "survivor_details": [{"fingerprint": "old"}], "completed_measurement": True,
            "review_context": bound_context(native_project.root)}
    mutation._publish_mutation_error(result, data, RuntimeError("owned failure"), {})
    published = json.loads(result.read_text())
    assert published["status"] == "error" and published["completed_measurement"] is False
    assert published["survivors"] == published["survivor_details"] == []
    assert published["review_context"] is None
    assert decode_mutation_survivor_details(published, review_context=bound_context(native_project.root)) is None


@pytest.mark.parametrize("retained", [1, 0], ids=["missing-second", "missing-all"])
def test_bound_carrier_requires_every_survived_inventory_identity(
    native_project, native_measurement, retained,
):
    native_project.mutants["x_example__mutmut_2"] = "survived"
    data = bound_outcome(native_project, native_measurement)
    assert list(data["inventory"].values()) == ["survived", "survived"]
    data["survivors"] = data["survivors"][:retained]
    data["survivor_details"] = data["survivor_details"][:retained]
    assert decode_mutation_survivor_details(
        data, review_context=bound_context(native_project.root),
    ) is None


@pytest.mark.parametrize("status", ["pending", "unknown", "not checked", "skipped", "SURVIVED"])
def test_bound_carrier_refuses_every_unlisted_nonterminal_inventory_status(
    native_project, native_measurement, status,
):
    data = bound_outcome(native_project, native_measurement)
    data["inventory"]["calc.x_extra__mutmut_9"] = status
    assert decode_mutation_survivor_details(
        data, review_context=bound_context(native_project.root),
    ) is None


@pytest.mark.parametrize("status", ["killed", "caught by type check"])
def test_terminal_inventory_members_preserve_custom_fingerprints_and_producer_order(
    native_project, native_measurement, status,
):
    native_project.mutants["x_example__mutmut_2"] = "survived"
    findings, infra, evidence = native_measurement()
    findings[0].fingerprint = "actual-fingerprint-independent-of-native-id"
    context = bound_context(native_project.root)
    data = mutation._mutation_outcome(findings, infra, evidence, review_context=context)
    # This is a carrier-schema control; the added terminal entry is not
    # represented as a newly executed native measurement.
    data["inventory"]["calc.x_extra__mutmut_9"] = status
    data["inventory"] = dict(reversed(list(data["inventory"].items())))
    decoded = decode_mutation_survivor_details(data, review_context=context)
    assert decoded is not None
    assert [item.id for item in decoded] == [item.id for item in findings]
    assert [item.fingerprint for item in decoded] == [item.fingerprint for item in findings]
