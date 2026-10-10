"""Receipt-bound clean credit through the real offline review pipeline."""

import copy
import hashlib
import inspect
import json
import os
import stat
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from code_forge import llm_invoke, verify
from code_forge.autofix import NoChangeAutoFixer, StubAutoFixer
from code_forge.backend import BackendConfig
from code_forge.baseline import ResolvedReview
from code_forge.factories import build_grouped_l1_provider, build_l1_provider
from code_forge.falsify import StubFalsifier
from code_forge.falsify_real import RealFalsifier
from code_forge.errors import CorruptedStateError
from code_forge.machine import StateMachine, TimeoutBreaker
from code_forge.source import compute_source_hash
from code_forge.state import (
    ROUND_PHASES,
    Disposition,
    Mode,
    StateFinding,
    Verdict,
    load_state,
    save_state,
)

DIFF = (
    "diff --git a/control.ts b/control.ts\n--- a/control.ts\n+++ b/control.ts\n"
    "@@ -1,2 +1,3 @@\n const context = 1;\n+const value = 2;\n const end = 3;\n"
)
CONTENT = "const context = 1;\nconst value = 2;\nconst end = 3;\n"
SOURCE_HASH = compute_source_hash(git_diff=DIFF)
VALID = {
    "findings": [],
    "code_excerpts": [{"file": "control.ts", "start_line": 1, "end_line": 3, "content": CONTENT}],
}


@pytest.fixture
def review_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "control.ts").write_text(CONTENT)
    config = tmp_path / ".code-forge"
    config.mkdir()
    (config / "gate.yaml").write_text("verify:\n  required_cycles: 3\n")
    monkeypatch.setattr(
        "code_forge.manifest.extract_manifest",
        lambda root: type(
            "Manifest", (), {"to_dict": lambda self: {}, "to_prompt_block": lambda self: ""}
        )(),
    )
    return tmp_path


def _machine(root, monkeypatch, *, payload=None, rounds=3, threshold=3, hook=None, diff=DIFF):
    resolved = ResolvedReview([Path("control.ts")], None, diff, "git")
    backend = BackendConfig(
        name="owned-offline",
        type="api",
        format="openai",
        model="offline",
        base_url="http://127.0.0.1:1",
        timeout_s=5,
    )
    holder = {}

    def transport(prompt, *args, **kwargs):
        body = VALID if payload is None else payload(holder["machine"], prompt)
        if isinstance(body, Exception):
            raise body
        return llm_invoke.LLMResult(copy.deepcopy(body), llm_invoke.Usage(), 0.0)

    monkeypatch.setattr(llm_invoke, "_invoke_api", transport)
    provider = build_l1_provider("auto", resolved, backend=backend, max_attempts=1)
    machine = StateMachine(
        mode=Mode.LOCAL,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda finding: None,
        resolved_review=resolved,
        source_hash=compute_source_hash(git_diff=diff),
        baseline_spec_repr="owned offline review",
        cwd=root,
        registry={},
        l0_runner=lambda *args: ([], []),
        l1_provider=provider,
        l2_runner=lambda *args, **kwargs: ([], []),
        max_total_rounds=rounds,
        clean_round_threshold=threshold,
        post_round_hook=hook,
    )
    machine._state.env_manifest = {}
    holder["machine"] = machine
    return machine


def _verify(root, **kwargs):
    return verify.run_verify(root, SOURCE_HASH, verify.parse_diff_files(DIFF), diff_text=DIFF, **kwargs)


def _receipts(root):
    return sorted((root / ".code-forge" / "receipts").glob("receipt-*.json"))


def _falsifier_candidate(reviewer_name="qodo"):
    return StateFinding(
        id=f"l1-{reviewer_name}-new-falsifier-candidate",
        fingerprint="new-falsifier-candidate",
        source="L1",
        disposition=Disposition.CONFIRMED,
        file="control.ts",
        line_range=[2, 2],
        description="P1: new candidate",
        excerpt="const value = 2;",
    )


def _attach_falsifier_candidate(machine):
    producer = machine.l1_provider

    def provider():
        findings, excerpts, usage, duration = producer()
        return findings + [_falsifier_candidate()], excerpts, usage, duration

    machine.l1_provider = provider


def _proof(root, cycles):
    return {
        "version": 1,
        "source_hash": SOURCE_HASH,
        "reviewed_repositories": None,
        "cycles": [
            {
                "cycle": cycle,
                "receipt_sha256": {
                    str(index): hashlib.sha256(
                        (
                            root / ".code-forge" / "receipts" / f"receipt-c{cycle}p{index}.json"
                        ).read_bytes()
                    ).hexdigest()
                    for index in (1, 2, 3)
                },
            }
            for cycle in cycles
        ],
    }


def _run_valid_transition(machine):
    failure = None
    verdict = None
    try:
        verdict = machine.run()
    except CorruptedStateError as exc:
        failure = exc
    assert failure is None, f"product transition produced corrupt modern authority: {failure}"
    return verdict


def test_raw_receipt_public_boundary_reads_each_file_once(review_workspace, monkeypatch):
    root = review_workspace
    _machine(root, monkeypatch).run()
    paths = _receipts(root)
    real_read = Path.read_bytes
    reads = []

    def read(path):
        if path in paths:
            reads.append(path)
        return real_read(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    result = _verify(root)
    assert result.passed, result.reason
    assert sorted(reads) == paths


def test_state_window_round_trip_preserves_exact_authority(review_workspace, monkeypatch):
    root = review_workspace
    _machine(root, monkeypatch).run()
    path = root / ".code-forge" / "state.json"
    state = load_state(path)
    state.earned_clean_window = _proof(root, [1, 2, 3])
    save_state(state, path)
    assert json.loads(path.read_text()).get("earned_clean_window") == state.earned_clean_window
    assert load_state(path).earned_clean_window == state.earned_clean_window


def test_strict_earned_capture_requires_explicit_completed_status(review_workspace, monkeypatch):
    root = review_workspace
    _machine(root, monkeypatch).run()
    path = root / ".code-forge" / "receipts" / "receipt-c1p1.json"
    receipt = json.loads(path.read_text())
    del receipt["pass_status"]
    path.write_text(json.dumps(receipt))
    assert hasattr(verify, "_capture_earned_cycle"), "strict capture is missing"
    proof, result = verify._capture_earned_cycle(
        root,
        SOURCE_HASH,
        verify.parse_diff_files(DIFF),
        cycle=1,
        diff_text=DIFF,
    )
    assert proof is None
    assert not result.passed and "completed" in result.reason


def test_raw_receipt_supplied_empty_tuple_does_not_reload(review_workspace, monkeypatch):
    root = review_workspace
    _machine(root, monkeypatch).run()
    assert hasattr(verify, "_run_verify_impl"), "loaded-record implementation is missing"
    monkeypatch.setattr(verify, "_load_receipt_records", lambda path: pytest.fail("receipt reload"))
    result = verify._run_verify_impl(
        root,
        SOURCE_HASH,
        verify.parse_diff_files(DIFF),
        diff_text=DIFF,
        required_cycles=1,
        cycles=[1],
        respect_floor=False,
        receipt_records=(),
    )
    assert not result.passed and "missing receipts" in result.reason


def test_strict_earned_public_signature_stays_ten_arguments():
    assert list(inspect.signature(verify.run_verify).parameters) == [
        "cwd",
        "diff_sha256",
        "diff_files",
        "hardened",
        "diff_text",
        "required_cycles",
        "cycles",
        "respect_floor",
        "reviewed_repositories",
        "require_convergence",
    ]


def _modern_state(root, cycles=(1, 2, 3)):
    path = root / ".code-forge" / "state.json"
    state = load_state(path)
    for row in state.round_history:
        row.update(
            source_hash=SOURCE_HASH,
            reviewed_repositories=None,
            clean_credit_action="earned",
            acquisition_failures=[],
            reset_observed=False,
            phase_status={
                name: "returned" for name in ("l0", "rulepack", "l1", "l2", "e2e", "coverage")
            },
        )
    state.earned_clean_window = _proof(root, cycles)
    save_state(state, path)
    return state


@pytest.mark.parametrize("status", [None, "missing", "error", "incomplete"])
def test_strict_earned_status_does_not_borrow_legacy_compatibility(
    review_workspace, monkeypatch, status
):
    root = review_workspace
    _machine(root, monkeypatch).run()
    path = _receipts(root)[0]
    data = json.loads(path.read_text())
    if status == "missing":
        data.pop("pass_status")
    else:
        data["pass_status"] = status
    path.write_text(json.dumps(data))
    entry, result = verify._capture_earned_cycle(
        root,
        SOURCE_HASH,
        verify.parse_diff_files(DIFF),
        cycle=1,
        diff_text=DIFF,
    )
    assert entry is None and not result.passed
    assert "explicit completed" in result.reason
    if status in (None, "missing"):
        assert _verify(root, cycles=[1], required_cycles=1, respect_floor=False).passed


@pytest.mark.parametrize("damage", ["missing", "corrupt", "source", "scope", "extra", "status"])
def test_strict_earned_prior_proof_damage_refuses(review_workspace, monkeypatch, damage):
    root = review_workspace
    _machine(root, monkeypatch).run()
    _modern_state(root)
    path = _receipts(root)[0]
    data = json.loads(path.read_text())
    if damage == "missing":
        path.unlink()
    elif damage == "corrupt":
        path.write_bytes(b"{bad")
    else:
        if damage == "source":
            data["diff_sha256"] = "other-source"
        elif damage == "scope":
            data["reviewed_repositories"] = {"other": "f" * 64}
        elif damage == "status":
            data.pop("pass_status")
        else:
            data["pass"] = 4
            path = path.with_name("receipt-c1p4.json")
        path.write_text(json.dumps(data))
    result = _verify(root)
    assert not result.passed, damage
    assert "earned" in result.reason or "corrupt receipt" in result.reason


def test_raw_receipt_replacement_after_capture_refuses_original_digest(review_workspace, monkeypatch):
    root = review_workspace
    _machine(root, monkeypatch).run()
    _modern_state(root)
    path = _receipts(root)[0]
    original = path.read_bytes()
    real_load = verify._load_receipt_records

    def replace_after_load(directory):
        records = real_load(directory)
        path.write_bytes(original + b"\n")
        return records

    monkeypatch.setattr(verify, "_load_receipt_records", replace_after_load)
    entry, result = verify._capture_earned_cycle(
        root,
        SOURCE_HASH,
        verify.parse_diff_files(DIFF),
        cycle=1,
        diff_text=DIFF,
    )
    assert result.passed and entry["receipt_sha256"]["1"] == hashlib.sha256(original).hexdigest()
    monkeypatch.setattr(verify, "_load_receipt_records", real_load)
    result = _verify(root)
    assert not result.passed and "digest mismatch" in result.reason


@pytest.mark.parametrize("action", ["pending", "unavailable", "reset"])
@pytest.mark.parametrize("slot", ["absent", "null"])
def test_state_window_modern_presence_precedes_numeric_fallback(
    review_workspace, monkeypatch, action, slot
):
    root = review_workspace
    _machine(root, monkeypatch).run()
    state = _modern_state(root)
    latest = state.round_history[-1]
    latest["clean_credit_action"] = action
    if action == "pending":
        latest["phase_status"] = {name: "not_run" for name in latest["phase_status"]}
        latest.pop("dispositions")
    if action == "reset":
        latest.update(reset_observed=True, clean_rounds_after=0, fixpoint="RESET")
    path = root / ".code-forge" / "state.json"
    save_state(state, path)
    raw = json.loads(path.read_text())
    if slot == "absent":
        raw.pop("earned_clean_window")
    else:
        raw["earned_clean_window"] = None
    path.write_text(json.dumps(raw))
    result = _verify(root)
    assert not result.passed and ("earned" in result.reason or "host" in result.reason)
    assert _verify(root, cycles=[1, 2, 3]).passed


@pytest.mark.parametrize(
    "field,value",
    [
        ("clean_credit_action", None),
        ("phase_status", None),
        ("acquisition_failures", None),
        ("reset_observed", None),
        ("source_hash", None),
        ("reviewed_repositories", []),
    ],
)
def test_state_window_partial_modern_field_is_not_legacy(review_workspace, monkeypatch, field, value):
    root = review_workspace
    _machine(root, monkeypatch).run()
    path = root / ".code-forge" / "state.json"
    data = json.loads(path.read_text())
    data["round_history"][-1][field] = value
    path.write_text(json.dumps(data))
    result = _verify(root)
    assert not result.passed and "unavailable earned state" in result.reason


def _legacy_state(path):
    data = json.loads(path.read_text())
    data.pop("earned_clean_window", None)
    data.pop("clean_window_migration", None)
    for row in data["round_history"]:
        for key in (
            "source_hash",
            "reviewed_repositories",
            "clean_credit_action",
            "acquisition_failures",
            "reset_observed",
            "phase_status",
        ):
            row.pop(key, None)
    path.write_text(json.dumps(data))


def test_legacy_migration_unique_clean_history_binds_original_state(review_workspace, monkeypatch):
    root = review_workspace
    _machine(root, monkeypatch).run()
    path = root / ".code-forge" / "state.json"
    _legacy_state(path)
    original = path.read_bytes()
    state = load_state(path)
    result = verify._restore_earned_window(
        state,
        root,
        SOURCE_HASH,
        verify.parse_diff_files(DIFF),
        diff_text=DIFF,
        reviewed_repositories=None,
    )
    assert result.passed, result.reason
    assert state.clean_window_migration == {
        "original_state_sha256": hashlib.sha256(original).hexdigest(),
        "selected_cycles": [1, 2, 3],
    }
    save_state(state, path)
    assert _verify(root).passed


def test_legacy_clean_history_resumes_without_hold_reset(review_workspace, monkeypatch):
    root = review_workspace
    path = root / ".code-forge" / "state.json"
    assert _machine(root, monkeypatch, rounds=2).run() != Verdict.PASS
    _legacy_state(path)

    assert _machine(root, monkeypatch, rounds=1).run() == Verdict.PASS
    state = load_state(path)
    assert state.consecutive_clean_rounds == 3
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == [1, 2, 3]
    assert state.round_history[-1]["clean_credit_action"] == "earned"
    assert _verify(root).passed


@pytest.mark.parametrize(
    "damage", ["empty", "duplicate", "bool", "negative", "order", "ambiguous", "count", "status"]
)
def test_legacy_migration_unproved_credit_keeps_original_bytes(review_workspace, monkeypatch, damage):
    root = review_workspace
    _machine(root, monkeypatch).run()
    path = root / ".code-forge" / "state.json"
    _legacy_state(path)
    state = load_state(path)
    if damage == "empty":
        state.round_history = []
    elif damage == "duplicate":
        state.round_history[1]["round"] = 0
    elif damage == "bool":
        state.round_history[1]["round"] = True
    elif damage == "negative":
        state.round_history[0]["round"] = -1
    elif damage == "order":
        state.round_history.reverse()
    elif damage == "ambiguous":
        state.round_history[1].pop("fixpoint")
    elif damage == "count":
        state.consecutive_clean_rounds = 4
    else:
        receipt = _receipts(root)[0]
        data = json.loads(receipt.read_text())
        data["pass_status"] = None
        receipt.write_text(json.dumps(data))
    save_state(state, path)
    original = path.read_bytes()
    result = verify._restore_earned_window(
        state,
        root,
        SOURCE_HASH,
        verify.parse_diff_files(DIFF),
        diff_text=DIFF,
        reviewed_repositories=None,
    )
    assert not result.passed
    assert state.earned_clean_window is None
    assert path.read_bytes() == original


def test_state_window_explicit_nonadjacent_and_legacy_default_stay_distinct(
    review_workspace, monkeypatch
):
    root = review_workspace
    _machine(root, monkeypatch, rounds=4, threshold=4).run()
    state = _modern_state(root, (1, 2, 4))
    state.consecutive_clean_rounds = 3
    state.round_history[2].update(
        clean_credit_action="interrupted",
        clean_rounds_after=2,
        acquisition_failures=[
            {
                "id": "l1-adversarial-invoke-fail",
                "fingerprint": "invoke-fail-adversarial",
                "pass_name": "adversarial",
                "outcome": "error",
            }
        ],
    )
    state.round_history[3]["clean_rounds_after"] = 3
    save_state(state, root / ".code-forge" / "state.json")
    result = _verify(root)
    assert result.passed, result.reason
    result = _verify(root, cycles=[1, 2, 4])
    assert not result.passed and "not consecutive" in result.reason
    (root / ".code-forge" / "state.json").unlink()
    assert _verify(root).passed


@pytest.mark.parametrize("damage", ["version", "source", "scope", "count", "cycles"])
def test_state_window_present_invalid_proof_refuses(review_workspace, monkeypatch, damage):
    root = review_workspace
    _machine(root, monkeypatch).run()
    state = _modern_state(root)
    if damage == "version":
        state.earned_clean_window["version"] = 2
    elif damage == "source":
        state.earned_clean_window["source_hash"] = "wrong"
    elif damage == "scope":
        state.earned_clean_window["reviewed_repositories"] = {"other": "f" * 64}
    elif damage == "count":
        state.consecutive_clean_rounds = 4
    else:
        state.earned_clean_window["cycles"][1]["cycle"] = True
    save_state(state, root / ".code-forge" / "state.json")
    assert not _verify(root).passed


@pytest.mark.parametrize("kind", ["typed", "generic"])
def test_acquisition_interruption_preserves_only_proved_credit(review_workspace, monkeypatch, kind):
    root = review_workspace

    def payload(machine, prompt):
        if machine._state.round == 2 and "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return (
                llm_invoke.LLMInvokeError("owned acquisition failure")
                if kind == "typed"
                else RuntimeError("owned host failure")
            )
        return VALID

    machine = _machine(root, monkeypatch, payload=payload)
    assert machine.run() != Verdict.PASS
    state = load_state(root / ".code-forge" / "state.json")
    assert state.consecutive_clean_rounds == 2
    assert state.earned_clean_window is not None
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == [1, 2]
    assert state.round_history[-1]["clean_credit_action"] == "interrupted"
    resumed = _machine(root, monkeypatch, rounds=1)
    assert resumed.run() == Verdict.PASS
    state = load_state(root / ".code-forge" / "state.json")
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == [1, 2, 4]
    assert _verify(root).passed


@pytest.mark.parametrize(
    "reply,later_failure,gate_failure",
    [
        ("not JSON", False, False),
        ("not JSON", True, False),
        ("not JSON", False, True),
        (llm_invoke.InvalidJSONResponseError("invalid JSON", raw_response="not JSON"), False, False),
        ({"verdict": "UNCERTAIN", "reasoning": "need evidence"}, False, False),
        ({"verdict": "CONFIRMED", "reasoning": "new defect"}, False, False),
    ],
)
def test_falsifier_protocol_failure_preserves_only_prior_credit(
    review_workspace, monkeypatch, reply, later_failure, gate_failure
):
    root = review_workspace
    (root / ".code-forge" / "gate.yaml").write_text("verify:\n  required_cycles: 2\n")
    assert _machine(root, monkeypatch, rounds=1, threshold=2).run() != Verdict.PASS
    first = load_state(root / ".code-forge" / "state.json")
    assert first.consecutive_clean_rounds == 1
    assert [entry["cycle"] for entry in first.earned_clean_window["cycles"]] == [1]

    machine = _machine(root, monkeypatch, rounds=1, threshold=2)
    _attach_falsifier_candidate(machine)
    if gate_failure:
        producer = machine.l1_provider

        def bad_excerpts():
            findings, excerpts, usage, duration = producer()
            excerpts[0]["content"] = "const value = 200;\n"
            return findings, excerpts, usage, duration

        machine.l1_provider = bad_excerpts
    machine.falsifier = RealFalsifier(diff_text=DIFF)
    if later_failure:

        def failed_l2():
            raise RuntimeError("owned later phase failure")

        monkeypatch.setattr(machine, "_run_l2_phase", failed_l2)
    with monkeypatch.context() as patch:
        def answer(*args, **kwargs):
            if isinstance(reply, Exception):
                raise reply
            return llm_invoke.LLMResult(reply)

        patch.setattr(
            "code_forge.falsify_real.llm_invoke",
            answer,
        )
        if later_failure:
            with pytest.raises(RuntimeError, match="owned later phase failure"):
                machine.run()
        else:
            assert machine.run() != Verdict.PASS

    state = load_state(root / ".code-forge" / "state.json")
    row = state.round_history[-1]
    if not isinstance(reply, dict):
        assert state.consecutive_clean_rounds == 1
        assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == [1]
        assert row["clean_credit_action"] == "interrupted"
        assert row["fixpoint"] == "INCOMPLETE"
        assert row["falsify_protocol_failures"] == ["new-falsifier-candidate"]
        assert row["clean_rounds_after"] == 1
        assert row["phase_status"]["l2"] == ("failed" if later_failure else "returned")
        if gate_failure:
            assert state.verdict == Verdict.FAIL
        assert state.verdict != Verdict.PASS
        if not later_failure:
            receipt = json.loads(
                (root / ".code-forge" / "receipts" / "receipt-c2p1.json").read_text()
            )
            candidate = next(
                finding for finding in receipt["findings"]
                if finding["description"].startswith("P1: new candidate")
            )
            assert candidate["disposition"] == "UNCERTAIN"
            assert candidate["basis"]["authority"] == "infra-unavailable"
            assert candidate["basis"]["falsification_survived"] is False
        assert _machine(root, monkeypatch, rounds=1, threshold=2).run() == Verdict.PASS
        final = load_state(root / ".code-forge" / "state.json")
        assert [entry["cycle"] for entry in final.earned_clean_window["cycles"]] == [1, 3]
        assert _verify(root).passed
    else:
        assert row["fixpoint"] == "RESET"
        assert row["clean_credit_action"] == "reset"
        assert state.consecutive_clean_rounds == 0
        assert state.earned_clean_window["cycles"] == []


@pytest.mark.parametrize("decision", ["c", "d"])
def test_protocol_hold_confirmation_resets_inherited_credit(review_workspace, monkeypatch, decision):
    from code_forge.hold import run_hold_ui

    root = review_workspace
    path = root / ".code-forge" / "state.json"
    assert _machine(root, monkeypatch, rounds=2).run() != Verdict.PASS
    assert load_state(path).consecutive_clean_rounds == 2

    machine = _machine(root, monkeypatch, rounds=1)
    _attach_falsifier_candidate(machine)
    machine.falsifier = RealFalsifier(diff_text=DIFF)
    with monkeypatch.context() as patch:
        patch.setattr(
            "code_forge.falsify_real.llm_invoke",
            lambda *args, **kwargs: llm_invoke.LLMResult("not JSON"),
        )
        assert machine.run() == Verdict.PENDING

    interrupted = load_state(path)
    assert interrupted.consecutive_clean_rounds == 2
    assert [entry["cycle"] for entry in interrupted.earned_clean_window["cycles"]] == [1, 2]
    assert interrupted.round_history[-1]["clean_credit_action"] == "interrupted"
    run_hold_ui(interrupted, path, input_fn=lambda _: decision, output_fn=lambda _: None)
    decided = load_state(path)
    assert decided.findings[0].disposition == (
        Disposition.CONFIRMED if decision == "c" else Disposition.DISMISSED
    )

    resumed = _machine(root, monkeypatch, rounds=1).run()
    after = load_state(path)
    if decision == "c":
        assert resumed != Verdict.PASS
        assert after.round_history[-1]["clean_credit_action"] == "reset"
        assert after.consecutive_clean_rounds == 0
        assert after.earned_clean_window["cycles"] == []
        assert not _verify(root).passed
        assert _machine(root, monkeypatch, rounds=3).run() == Verdict.PASS
        recovered = load_state(path)
        assert [entry["cycle"] for entry in recovered.earned_clean_window["cycles"]] == [5, 6, 7]
        assert _verify(root).passed
    else:
        assert resumed == Verdict.PASS
        assert [entry["cycle"] for entry in after.earned_clean_window["cycles"]] == [1, 2, 4]
        assert _verify(root).passed


def test_confirmed_hold_reset_survives_unstarted_pending_attempt(review_workspace, monkeypatch):
    from code_forge.hold import run_hold_ui

    root = review_workspace
    path = root / ".code-forge" / "state.json"
    assert _machine(root, monkeypatch, rounds=2).run() != Verdict.PASS
    machine = _machine(root, monkeypatch, rounds=1)
    _attach_falsifier_candidate(machine)
    machine.falsifier = RealFalsifier(diff_text=DIFF)
    with monkeypatch.context() as patch:
        patch.setattr(
            "code_forge.falsify_real.llm_invoke",
            lambda *args, **kwargs: llm_invoke.LLMResult("not JSON"),
        )
        assert machine.run() == Verdict.PENDING
    run_hold_ui(load_state(path), path, input_fn=lambda _: "c", output_fn=lambda _: None)

    reserved = _machine(root, monkeypatch, rounds=1)

    def abort_before_execution():
        raise KeyboardInterrupt("owned interruption before host execution")

    monkeypatch.setattr(reserved, "_start_host_execution", abort_before_execution)
    with pytest.raises(KeyboardInterrupt, match="owned interruption"):
        reserved.run()
    pending = load_state(path)
    assert pending.round_history[-1]["clean_credit_action"] == "pending"
    assert pending.consecutive_clean_rounds == 2

    assert _machine(root, monkeypatch, rounds=1).run() != Verdict.PASS
    after = load_state(path)
    assert after.round_history[-1]["clean_credit_action"] == "reset"
    assert after.consecutive_clean_rounds == 0
    assert after.earned_clean_window["cycles"] == []
    assert not _verify(root).passed


def test_first_semantic_confirmation_after_protocol_failure_resets_credit(
    review_workspace, monkeypatch
):
    root = review_workspace
    added = ["const value = 2;"] + [f"const x{i} = {i};" for i in range(19)]
    lines = ["const context = 1;"] + added + ["const end = 3;"]
    (root / "control.ts").write_text("\n".join(lines) + "\n")
    diff = (
        "diff --git a/control.ts b/control.ts\n--- a/control.ts\n+++ b/control.ts\n"
        "@@ -1,2 +1,22 @@\n const context = 1;\n"
        + "".join("+" + line + "\n" for line in added)
        + " const end = 3;\n"
    )

    def payload(machine, prompt):
        start, end = (1, 14) if machine._state.round < 3 else (9, 22)
        return {
            "findings": [],
            "code_excerpts": [{
                "file": "control.ts",
                "start_line": start,
                "end_line": end,
                "content": "\n".join(lines[start - 1:end]) + "\n",
            }],
        }

    def machine_with_candidate(*, rounds=1, candidate=False):
        machine = _machine(root, monkeypatch, rounds=rounds, diff=diff, payload=payload)
        if candidate:
            producer = machine.l1_provider

            def provider():
                findings, excerpts, usage, duration = producer()
                finding = _falsifier_candidate()
                finding.severity = "P3"
                finding.description = "P3: new candidate"
                return findings + [finding], excerpts, usage, duration

            machine.l1_provider = provider
            machine.falsifier = RealFalsifier(diff_text=diff)
        return machine

    assert machine_with_candidate(rounds=2).run() != Verdict.PASS
    path = root / ".code-forge" / "state.json"
    assert load_state(path).consecutive_clean_rounds == 2
    with monkeypatch.context() as patch:
        patch.setattr(
            "code_forge.falsify_real.llm_invoke",
            lambda *args, **kwargs: llm_invoke.LLMResult("not JSON"),
        )
        assert machine_with_candidate(candidate=True).run() == Verdict.PENDING
    with monkeypatch.context() as patch:
        patch.setattr(
            "code_forge.falsify_real.llm_invoke",
            lambda *args, **kwargs: llm_invoke.LLMResult({
                "verdict": "CONFIRMED", "reasoning": "first semantic adjudication",
            }),
        )
        assert machine_with_candidate(candidate=True).run() != Verdict.PASS
    state = load_state(path)
    assert state.round_history[-1]["clean_credit_action"] == "reset"
    assert state.consecutive_clean_rounds == 0
    assert state.earned_clean_window["cycles"] == []
    result = verify.run_verify(
        root,
        compute_source_hash(git_diff=diff),
        verify.parse_diff_files(diff),
        diff_text=diff,
    )
    assert not result.passed


def test_sticky_dismissal_does_not_credit_failed_falsifier(review_workspace, monkeypatch):
    root = review_workspace
    (root / ".code-forge" / "gate.yaml").write_text("verify:\n  required_cycles: 2\n")
    first = _machine(root, monkeypatch, rounds=1, threshold=2)
    _attach_falsifier_candidate(first)
    first.falsifier = RealFalsifier(diff_text=DIFF)
    with monkeypatch.context() as patch:
        patch.setattr(
            "code_forge.falsify_real.llm_invoke",
            lambda *args, **kwargs: llm_invoke.LLMResult(
                {"verdict": "DISMISSED", "reasoning": "the diff is correct"}
            ),
        )
        assert first.run() != Verdict.PASS
    state = load_state(root / ".code-forge" / "state.json")
    assert state.consecutive_clean_rounds == 1
    assert state.round_history[-1]["dispositions"]["new-falsifier-candidate"] == "DISMISSED"

    second = _machine(root, monkeypatch, rounds=1, threshold=2)
    _attach_falsifier_candidate(second)
    second.falsifier = RealFalsifier(diff_text=DIFF)
    with monkeypatch.context() as patch:
        patch.setattr(
            "code_forge.falsify_real.llm_invoke",
            lambda *args, **kwargs: llm_invoke.LLMResult("not JSON"),
        )
        assert second.run() != Verdict.PASS
    state = load_state(root / ".code-forge" / "state.json")
    assert state.consecutive_clean_rounds == 1
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == [1]
    assert state.round_history[-1]["fixpoint"] == "INCOMPLETE"
    assert state.round_history[-1]["dispositions"]["new-falsifier-candidate"] == "DISMISSED"


def test_protocol_failure_does_not_hide_semantic_uncertain_reset(review_workspace, monkeypatch):
    root = review_workspace
    (root / ".code-forge" / "gate.yaml").write_text("verify:\n  required_cycles: 2\n")
    assert _machine(root, monkeypatch, rounds=1, threshold=2).run() != Verdict.PASS

    machine = _machine(root, monkeypatch, rounds=1, threshold=2)
    _attach_falsifier_candidate(machine)
    producer = machine.l1_provider

    def two_candidates():
        findings, excerpts, usage, duration = producer()
        other = _falsifier_candidate()
        other.id = "l1-expert-semantic-uncertain"
        other.fingerprint = "semantic-uncertain"
        return findings + [other], excerpts, usage, duration

    machine.l1_provider = two_candidates
    machine.falsifier = RealFalsifier(diff_text=DIFF)
    responses = iter(
        [
            llm_invoke.LLMResult("not JSON"),
            llm_invoke.LLMResult({"verdict": "UNCERTAIN", "reasoning": "need evidence"}),
        ]
    )
    with monkeypatch.context() as patch:
        patch.setattr("code_forge.falsify_real.llm_invoke", lambda *args, **kwargs: next(responses))
        assert machine.run() != Verdict.PASS

    state = load_state(root / ".code-forge" / "state.json")
    row = state.round_history[-1]
    assert len(row["falsify_protocol_failures"]) == 1
    assert row["fixpoint"] == "RESET"
    assert row["clean_credit_action"] == "reset"
    assert state.consecutive_clean_rounds == 0
    assert state.earned_clean_window["cycles"] == []


@pytest.mark.parametrize(
    "replies",
    [
        [
            {"verdict": "UNCERTAIN", "reasoning": "semantic ambiguity"},
            "not JSON",
        ],
        [
            "not JSON",
            {"verdict": "UNCERTAIN", "reasoning": "semantic ambiguity"},
        ],
        [
            {"verdict": "CONFIRMED", "reasoning": "new defect"},
            "not JSON",
        ],
        [{"verdict": "UNCERTAIN", "reasoning": "semantic ambiguity"}],
        [{"verdict": "CONFIRMED", "reasoning": "new defect"}],
    ],
    ids=[
        "uncertain-before-protocol",
        "protocol-before-uncertain",
        "confirmed-before-protocol",
        "uncertain-only",
        "confirmed-only",
    ],
)
def test_same_fingerprint_semantic_reset_survives_protocol_duplicate(
    review_workspace, monkeypatch, replies
):
    root = review_workspace
    (root / ".code-forge" / "gate.yaml").write_text("verify:\n  required_cycles: 2\n")
    assert _machine(root, monkeypatch, rounds=1, threshold=2).run() != Verdict.PASS
    before = load_state(root / ".code-forge" / "state.json")
    assert before.consecutive_clean_rounds == 1

    machine = _machine(root, monkeypatch, rounds=1, threshold=2)
    producer = machine.l1_provider

    def repeated_candidate():
        findings, excerpts, usage, duration = producer()
        names = ("qodo", "expert")[: len(replies)]
        return findings + [_falsifier_candidate(name) for name in names], excerpts, usage, duration

    machine.l1_provider = repeated_candidate
    machine.falsifier = RealFalsifier(diff_text=DIFF)
    responses = iter(llm_invoke.LLMResult(reply) for reply in replies)
    with monkeypatch.context() as patch:
        patch.setattr("code_forge.falsify_real.llm_invoke", lambda *args, **kwargs: next(responses))
        assert machine.run() != Verdict.PASS

    state = load_state(root / ".code-forge" / "state.json")
    row = state.round_history[-1]
    assert row["l1_fingerprints"] == ["new-falsifier-candidate"] * len(replies)
    assert row["falsify_protocol_failures"] == (["new-falsifier-candidate"] if len(replies) == 2 else [])
    assert row["fixpoint"] == "RESET"
    assert row["clean_credit_action"] == "reset"
    assert state.consecutive_clean_rounds == 0
    assert state.earned_clean_window["cycles"] == []

    assert _machine(root, monkeypatch, rounds=1, threshold=2).run() != Verdict.PASS
    later = load_state(root / ".code-forge" / "state.json")
    assert later.consecutive_clean_rounds == 1
    assert [entry["cycle"] for entry in later.earned_clean_window["cycles"]] == [3]


@pytest.mark.parametrize("semantic_pass", ["qodo", "expert"])
@pytest.mark.parametrize("verdict", ["UNCERTAIN", "CONFIRMED"])
def test_duplicate_receipts_keep_each_pass_falsifier_result(
    review_workspace, monkeypatch, semantic_pass, verdict
):
    root = review_workspace
    (root / ".code-forge" / "gate.yaml").write_text("verify:\n  required_cycles: 2\n")
    assert _machine(root, monkeypatch, rounds=1, threshold=2).run() != Verdict.PASS

    machine = _machine(root, monkeypatch, rounds=1, threshold=2)
    producer = machine.l1_provider

    def two_pass_candidates():
        findings, excerpts, usage, duration = producer()
        candidates = [_falsifier_candidate(name) for name in ("qodo", "expert")]
        return findings + candidates, excerpts, usage, duration

    machine.l1_provider = two_pass_candidates
    machine.falsifier = RealFalsifier(diff_text=DIFF)
    replies = iter(
        llm_invoke.LLMResult(
            {"verdict": verdict, "reasoning": "semantic result"}
            if name == semantic_pass else "not JSON"
        )
        for name in ("qodo", "expert")
    )
    with monkeypatch.context() as patch:
        patch.setattr("code_forge.falsify_real.llm_invoke", lambda *args, **kwargs: next(replies))
        assert machine.run() != Verdict.PASS

    state = load_state(root / ".code-forge" / "state.json")
    row = state.round_history[-1]
    assert row["fixpoint"] == "RESET"
    assert row["clean_credit_action"] == "reset"
    assert state.consecutive_clean_rounds == 0
    receipts = {
        name: json.loads((root / ".code-forge" / "receipts" / f"receipt-c2p{number}.json").read_text())
        for number, name in enumerate(("qodo", "expert"), 1)
    }
    assert all(receipt["findings_count"] == 1 for receipt in receipts.values())
    semantic = receipts[semantic_pass]["findings"][0]
    failure = receipts["expert" if semantic_pass == "qodo" else "qodo"]["findings"][0]
    assert semantic["disposition"] == verdict
    assert failure["disposition"] == "UNCERTAIN"
    assert failure["basis"]["authority"] == "infra-unavailable"
    assert failure["basis"]["falsification_survived"] is False
    assert "falsify() protocol violation" in failure["description"]

    assert _machine(root, monkeypatch, rounds=1, threshold=2).run() != Verdict.PASS
    assert _machine(root, monkeypatch, rounds=1, threshold=2).run() == Verdict.PASS
    final = load_state(root / ".code-forge" / "state.json")
    assert [entry["cycle"] for entry in final.earned_clean_window["cycles"]] == [3, 4]
    assert _verify(root).passed


@pytest.mark.parametrize(
    "reply, error_text",
    [
        ("not JSON", "protocol violation"),
        (llm_invoke.LLMInvokeError("owned outage"), "backend unavailable"),
        (RuntimeError("owned failure"), "raised"),
    ],
    ids=["protocol", "backend", "runtime"],
)
def test_ci_falsifier_failure_cannot_attest_clean_pass(
    review_workspace, monkeypatch, reply, error_text
):
    root = review_workspace
    machine = _machine(root, monkeypatch, rounds=1, threshold=1)
    machine.mode = Mode.CI
    machine.resolved_review = replace(
        machine.resolved_review, base_sha="a" * 40, head_sha="b" * 40
    )
    _attach_falsifier_candidate(machine)
    machine.falsifier = RealFalsifier(diff_text=DIFF)
    with monkeypatch.context() as patch:
        def answer(*args, **kwargs):
            if isinstance(reply, Exception):
                raise reply
            return llm_invoke.LLMResult(reply)

        patch.setattr(
            "code_forge.falsify_real.llm_invoke",
            answer,
        )
        verdict = machine.run()

    state = load_state(root / ".code-forge" / "state.json")
    receipt = json.loads(
        (root / ".code-forge" / "receipts" / "receipt-c1p1.json").read_text()
    )
    candidate = next(
        finding for finding in receipt["findings"]
        if finding["description"].startswith("P1: new candidate")
    )
    ledger = root / ".code-forge" / "ledger.jsonl"
    rows = [json.loads(line) for line in ledger.read_text().splitlines()] if ledger.exists() else []

    assert candidate["disposition"] == "UNCERTAIN"
    assert candidate["basis"]["falsification_survived"] is False
    if error_text == "protocol violation":
        assert candidate["basis"]["authority"] == "infra-unavailable"
    assert any(
        finding.fingerprint == "new-falsifier-candidate"
        and finding.disposition == Disposition.UNCERTAIN
        and error_text in finding.error
        for finding in state.findings
    )
    assert verdict == state.verdict == Verdict.FAIL
    assert state.converged is False
    assert state.rounds_with_falsify_infra == 1
    assert all(row["evidence_class"] != "clean_pass" for row in rows)


@pytest.mark.parametrize(
    "error",
    [llm_invoke.LLMInvokeError("owned outage"), RuntimeError("owned failure")],
    ids=["backend", "runtime"],
)
def test_local_falsifier_failure_receipt_grants_no_clean_credit(
    review_workspace, monkeypatch, error
):
    root = review_workspace
    (root / ".code-forge" / "gate.yaml").write_text("verify:\n  required_cycles: 2\n")
    assert _machine(root, monkeypatch, rounds=1, threshold=2).run() != Verdict.PASS
    first = load_state(root / ".code-forge" / "state.json")
    assert first.consecutive_clean_rounds == 1

    machine = _machine(root, monkeypatch, rounds=1, threshold=2)
    _attach_falsifier_candidate(machine)
    machine.falsifier = RealFalsifier(diff_text=DIFF)
    with monkeypatch.context() as patch:
        def answer(*args, **kwargs):
            raise error

        patch.setattr("code_forge.falsify_real.llm_invoke", answer)
        assert machine.run() != Verdict.PASS

    state = load_state(root / ".code-forge" / "state.json")
    receipt = json.loads(
        (root / ".code-forge" / "receipts" / "receipt-c2p1.json").read_text()
    )
    candidate = next(
        finding for finding in receipt["findings"]
        if finding["description"].startswith("P1: new candidate")
    )
    row = state.round_history[-1]
    assert row["fixpoint"] == "RESET"
    assert row["clean_credit_action"] == "reset"
    assert state.consecutive_clean_rounds == 0
    assert state.earned_clean_window["cycles"] == []
    assert candidate["disposition"] == "UNCERTAIN"
    assert candidate["basis"]["authority"] == "infra-unavailable"
    assert candidate["basis"]["falsification_survived"] is False


@pytest.mark.parametrize(
    "error",
    [llm_invoke.LLMInvokeError("owned outage"), RuntimeError("owned failure")],
    ids=["backend", "runtime"],
)
def test_ci_failed_duplicate_keeps_independent_confirmed_result(
    review_workspace, monkeypatch, error
):
    root = review_workspace
    monkeypatch.setenv("FORGE_FALSIFY_WORKERS", "1")
    machine = _machine(root, monkeypatch, rounds=1, threshold=1)
    machine.mode = Mode.CI
    machine.resolved_review = replace(
        machine.resolved_review, base_sha="a" * 40, head_sha="b" * 40
    )
    producer = machine.l1_provider

    def two_pass_candidates():
        findings, excerpts, usage, duration = producer()
        return findings + [_falsifier_candidate("qodo"), _falsifier_candidate("expert")], excerpts, usage, duration

    machine.l1_provider = two_pass_candidates
    machine.falsifier = RealFalsifier(diff_text=DIFF)
    replies = iter([{"verdict": "CONFIRMED", "reasoning": "semantic result"}, error])
    with monkeypatch.context() as patch:
        def answer(*args, **kwargs):
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return llm_invoke.LLMResult(reply)

        patch.setattr("code_forge.falsify_real.llm_invoke", answer)
        assert machine.run() == Verdict.FAIL

    state = load_state(root / ".code-forge" / "state.json")
    findings = [f for f in state.findings if f.fingerprint == "new-falsifier-candidate"]
    assert len(findings) == 1
    assert findings[0].disposition == Disposition.CONFIRMED
    receipts = [
        json.loads((root / ".code-forge" / "receipts" / f"receipt-c1p{number}.json").read_text())
        for number in (1, 2)
    ]
    assert receipts[0]["findings"][0]["disposition"] == "CONFIRMED"
    failure = receipts[1]["findings"][0]
    assert failure["disposition"] == "UNCERTAIN"
    assert failure["basis"]["authority"] == "infra-unavailable"
    assert failure["basis"]["falsification_survived"] is False
    ledger = [
        json.loads(line)
        for line in (root / ".code-forge" / "ledger.jsonl").read_text().splitlines()
    ]
    assert any(row["fingerprint"] == "new-falsifier-candidate" for row in ledger)
    assert all(row["evidence_class"] != "clean_pass" for row in ledger)


@pytest.mark.parametrize(
    "replies",
    [
        (
            {"verdict": "DISMISSED", "reasoning": "semantic dismissal"},
            llm_invoke.LLMInvokeError("owned outage"),
        ),
        (
            {"verdict": "DISMISSED", "reasoning": "semantic dismissal"},
            RuntimeError("owned failure"),
        ),
        (llm_invoke.LLMInvokeError("owned outage"), "not JSON"),
        ("not JSON", llm_invoke.LLMInvokeError("owned outage")),
    ],
    ids=["dismissed-backend", "dismissed-runtime", "backend-protocol", "protocol-backend"],
)
def test_local_mixed_falsifier_failure_resets_and_recovers(
    review_workspace, monkeypatch, replies
):
    root = review_workspace
    monkeypatch.setenv("FORGE_FALSIFY_WORKERS", "1")
    (root / ".code-forge" / "gate.yaml").write_text("verify:\n  required_cycles: 2\n")
    assert _machine(root, monkeypatch, rounds=1, threshold=2).run() != Verdict.PASS
    prior = load_state(root / ".code-forge" / "state.json")
    assert prior.consecutive_clean_rounds == 1

    machine = _machine(root, monkeypatch, rounds=1, threshold=2)
    producer = machine.l1_provider

    def two_pass_candidates():
        findings, excerpts, usage, duration = producer()
        return findings + [_falsifier_candidate("qodo"), _falsifier_candidate("expert")], excerpts, usage, duration

    machine.l1_provider = two_pass_candidates
    machine.falsifier = RealFalsifier(diff_text=DIFF)
    sequence = iter(replies)
    with monkeypatch.context() as patch:
        def answer(*args, **kwargs):
            reply = next(sequence)
            if isinstance(reply, Exception):
                raise reply
            return llm_invoke.LLMResult(reply)

        patch.setattr("code_forge.falsify_real.llm_invoke", answer)
        assert machine.run() != Verdict.PASS

    state = load_state(root / ".code-forge" / "state.json")
    row = state.round_history[-1]
    assert row["fixpoint"] == "RESET"
    assert row["clean_credit_action"] == "reset"
    assert state.consecutive_clean_rounds == 0
    assert state.earned_clean_window["cycles"] == []

    assert _machine(root, monkeypatch, rounds=1, threshold=2).run() != Verdict.PASS
    resumed = load_state(root / ".code-forge" / "state.json")
    assert resumed.consecutive_clean_rounds == 1
    assert [entry["cycle"] for entry in resumed.earned_clean_window["cycles"]] == [3]
    assert _machine(root, monkeypatch, rounds=1, threshold=2).run() == Verdict.PASS
    assert _verify(root).passed


@pytest.mark.parametrize(
    "error",
    [llm_invoke.LLMInvokeError("owned outage"), RuntimeError("owned failure")],
    ids=["backend", "runtime"],
)
def test_local_sticky_dismissal_cannot_hide_later_falsifier_failure(
    review_workspace, monkeypatch, error
):
    root = review_workspace
    (root / ".code-forge" / "gate.yaml").write_text("verify:\n  required_cycles: 2\n")

    first = _machine(root, monkeypatch, rounds=1, threshold=2)
    _attach_falsifier_candidate(first)
    first.falsifier = RealFalsifier(diff_text=DIFF)
    with monkeypatch.context() as patch:
        patch.setattr(
            "code_forge.falsify_real.llm_invoke",
            lambda *args, **kwargs: llm_invoke.LLMResult(
                {"verdict": "DISMISSED", "reasoning": "semantic dismissal"}
            ),
        )
        assert first.run() != Verdict.PASS
    prior = load_state(root / ".code-forge" / "state.json")
    assert prior.consecutive_clean_rounds == 1
    assert prior.round_history[-1]["clean_credit_action"] == "earned"

    second = _machine(root, monkeypatch, rounds=1, threshold=2)
    _attach_falsifier_candidate(second)
    second.falsifier = RealFalsifier(diff_text=DIFF)
    with monkeypatch.context() as patch:
        def outage(*args, **kwargs):
            raise error

        patch.setattr("code_forge.falsify_real.llm_invoke", outage)
        assert second.run() != Verdict.PASS
    failed = load_state(root / ".code-forge" / "state.json")
    assert failed.round_history[-1]["fixpoint"] == "RESET"
    assert failed.round_history[-1]["clean_credit_action"] == "reset"
    assert failed.consecutive_clean_rounds == 0
    assert failed.earned_clean_window["cycles"] == []

    assert _machine(root, monkeypatch, rounds=1, threshold=2).run() != Verdict.PASS
    recovered = load_state(root / ".code-forge" / "state.json")
    assert [entry["cycle"] for entry in recovered.earned_clean_window["cycles"]] == [3]
    assert _machine(root, monkeypatch, rounds=1, threshold=2).run() == Verdict.PASS
    assert _verify(root).passed


def test_same_fingerprint_dismissal_and_protocol_error_earns_no_new_credit(
    review_workspace, monkeypatch
):
    root = review_workspace
    (root / ".code-forge" / "gate.yaml").write_text("verify:\n  required_cycles: 2\n")
    assert _machine(root, monkeypatch, rounds=1, threshold=2).run() != Verdict.PASS

    machine = _machine(root, monkeypatch, rounds=1, threshold=2)
    producer = machine.l1_provider

    def repeated_candidate():
        findings, excerpts, usage, duration = producer()
        return findings + [_falsifier_candidate("qodo"), _falsifier_candidate("expert")], excerpts, usage, duration

    machine.l1_provider = repeated_candidate
    machine.falsifier = RealFalsifier(diff_text=DIFF)
    responses = iter(
        [
            llm_invoke.LLMResult({"verdict": "DISMISSED", "reasoning": "already safe"}),
            llm_invoke.LLMResult("not JSON"),
        ]
    )
    with monkeypatch.context() as patch:
        patch.setattr("code_forge.falsify_real.llm_invoke", lambda *args, **kwargs: next(responses))
        assert machine.run() != Verdict.PASS

    state = load_state(root / ".code-forge" / "state.json")
    row = state.round_history[-1]
    assert row["fixpoint"] == "INCOMPLETE"
    assert row["clean_credit_action"] == "interrupted"
    assert row["falsify_protocol_failures"] == ["new-falsifier-candidate"]
    assert state.consecutive_clean_rounds == 1
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == [1]
    assert _machine(root, monkeypatch, rounds=1, threshold=2).run() == Verdict.PASS
    assert _verify(root).passed


@pytest.mark.parametrize(
    "damage",
    [
        "empty", "duplicate", "wrong_fingerprint", "missing_disposition", "l1_not_returned",
        "clean_fixpoint", "earned", "reset_action",
    ],
)
def test_protocol_interruption_requires_host_observation(review_workspace, monkeypatch, damage):
    root = review_workspace
    (root / ".code-forge" / "gate.yaml").write_text("verify:\n  required_cycles: 2\n")
    assert _machine(root, monkeypatch, rounds=1, threshold=2).run() != Verdict.PASS
    machine = _machine(root, monkeypatch, rounds=1, threshold=2)
    _attach_falsifier_candidate(machine)
    machine.falsifier = RealFalsifier(diff_text=DIFF)
    with monkeypatch.context() as patch:
        patch.setattr(
            "code_forge.falsify_real.llm_invoke",
            lambda *args, **kwargs: llm_invoke.LLMResult("not JSON"),
        )
        assert machine.run() != Verdict.PASS
    path = root / ".code-forge" / "state.json"
    data = json.loads(path.read_text())
    row = data["round_history"][-1]
    assert row["clean_credit_action"] == "interrupted"
    if damage == "empty":
        row["falsify_protocol_failures"] = []
    elif damage == "duplicate":
        row["falsify_protocol_failures"] *= 2
    elif damage == "wrong_fingerprint":
        row["falsify_protocol_failures"] = ["not-an-l1-finding"]
    elif damage == "missing_disposition":
        del row["dispositions"]["new-falsifier-candidate"]
    elif damage == "l1_not_returned":
        row["phase_status"]["l1"] = "failed"
    elif damage == "clean_fixpoint":
        row["fixpoint"] = "CLEAN"
    elif damage == "earned":
        row["clean_credit_action"] = "earned"
    else:
        row["clean_credit_action"] = "reset"
    path.write_text(json.dumps(data))
    with pytest.raises(CorruptedStateError):
        load_state(path)


def test_attempt_history_pending_is_durable_before_real_dispatch(review_workspace, monkeypatch):
    root = review_workspace
    seen = []

    def payload(machine, prompt):
        if not seen:
            state = json.loads((root / ".code-forge" / "state.json").read_text())
            seen.append(state)
        return VALID

    _machine(root, monkeypatch, payload=payload).run()
    row = seen[0]["round_history"][-1] if seen[0]["round_history"] else None
    assert row is not None, "dispatch has no durable pending reservation"
    assert row["clean_credit_action"] == "unavailable"
    assert set(row["phase_status"].values()) == {"not_run"}
    assert seen[0]["earned_clean_window"]["cycles"] == []


@pytest.mark.parametrize("rounds", [2, 3])
def test_terminal_floor_honors_public_three_when_host_tier_is_two(review_workspace, monkeypatch, rounds):
    root = review_workspace
    machine = _machine(root, monkeypatch, rounds=rounds, threshold=2)
    verdict = machine.run()
    if rounds == 3:
        assert verdict == Verdict.PASS, "host stopped below the current public floor"
        assert machine._state.consecutive_clean_rounds == 3
        assert _verify(root).passed
    else:
        assert verdict != Verdict.PASS
        assert machine._state.consecutive_clean_rounds == 2
        assert not _verify(root).passed


def test_strict_earned_missing_prior_receipts_cannot_resume_false_pass(review_workspace, monkeypatch):
    root = review_workspace

    def payload(machine, prompt):
        if machine._state.round == 2 and "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return {"findings": [], "code_excerpts": []}
        return VALID

    first = _machine(root, monkeypatch, payload=payload)
    assert first.run() == Verdict.FAIL
    assert first._state.consecutive_clean_rounds == 2
    originals = [path for path in _receipts(root) if json.loads(path.read_text())["cycle"] in (1, 2)]
    assert len(originals) == 6
    for path in originals:
        path.unlink()
    resumed = _machine(root, monkeypatch, rounds=1)
    assert resumed.run() != Verdict.PASS, "missing inherited proof produced PASS"
    assert resumed._state.consecutive_clean_rounds == 2
    assert not _verify(root).passed


def test_mixed_reset_genuine_product_clears_window_even_with_acquisition_failure(
    review_workspace, monkeypatch
):
    root = review_workspace

    def payload(machine, prompt):
        if machine._state.round == 2 and "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("owned acquisition failure")
        return VALID

    machine = _machine(root, monkeypatch, payload=payload)
    machine.autofixer = NoChangeAutoFixer()
    machine.l0_runner = lambda *args: (
        [
            StateFinding(
                id="actual-product",
                fingerprint="actual-product",
                source="L0",
                disposition=Disposition.CONFIRMED,
                file="control.ts",
                line_range=[2, 2],
                description="actual product reset",
            )
        ]
        if machine._state.round == 2
        else [],
        [],
    )
    assert _run_valid_transition(machine) != Verdict.PASS
    assert machine._state.consecutive_clean_rounds == 0
    assert machine._state.earned_clean_window is not None
    assert machine._state.earned_clean_window["cycles"] == []
    assert machine._state.round_history[-1]["clean_credit_action"] == "reset"


@pytest.mark.parametrize("outcome", ["rejected", "unavailable", "mimic"])
def test_acquisition_audit_and_model_marker_cannot_gain_interruption_credit(
    review_workspace, monkeypatch, outcome
):
    def payload(machine, prompt):
        if machine._state.round != 2 or "adversarial" not in prompt.rsplit("You are a ", 1)[-1]:
            return VALID
        if outcome == "unavailable":
            return {"findings": [], "code_excerpts": []}
        if outcome == "rejected":
            return {"findings": [], "code_excerpts": [{"file": "control.ts"}]}
        body = copy.deepcopy(VALID)
        body["findings"] = [
            {
                "id": "l1-adversarial-invoke-fail",
                "fingerprint": "invoke-fail-adversarial",
                "source": "INFRA",
                "file": "control.ts",
                "line": 2,
                "severity": "P1",
                "description": "model marker mimic",
            }
        ]
        return body

    machine = _machine(review_workspace, monkeypatch, payload=payload)
    assert machine.run() != Verdict.PASS
    latest = machine._state.round_history[-1]
    assert latest["acquisition_failures"] == []
    assert latest["clean_credit_action"] != "interrupted", "audit/model payload borrowed host authority"
    assert not _verify(review_workspace).passed


def test_acquisition_real_spawn_marker_preserves_proof(review_workspace, monkeypatch):
    from code_forge.factories import _L1Call
    from code_forge.outlet_c import _run_chunk

    machine = _machine(review_workspace, monkeypatch)

    def spawn(name, diff):
        if machine._state.round == 2 and name == "adversarial":
            raise RuntimeError("owned spawn error")
        return copy.deepcopy(VALID)

    machine.l1_provider = _L1Call(
        lambda call: _run_chunk(
            DIFF,
            spawn,
            ("qodo", "expert", "adversarial"),
            attempted=call.attempted_excerpts,
            rejection_state=call,
        )
    )
    assert machine.run() != Verdict.PASS
    assert machine._state.consecutive_clean_rounds == 2
    assert machine._state.round_history[-1]["acquisition_failures"] == [
        {
            "id": "l1-adversarial-spawn-fail",
            "fingerprint": "spawn-fail-adversarial",
            "pass_name": "adversarial",
            "outcome": "timeout",
        }
    ]


def test_acquisition_grouped_first_surviving_host_object_is_authority(review_workspace, monkeypatch):
    machine = _machine(
        review_workspace,
        monkeypatch,
        payload=lambda machine, prompt: (
            llm_invoke.LLMInvokeError("owned grouped error") if machine._state.round == 2 else VALID
        ),
    )
    machine.l1_provider = build_grouped_l1_provider(
        "auto",
        [{"name": name, "resolved": machine.resolved_review} for name in ("first", "second")],
        backend=BackendConfig(
            name="owned-offline",
            type="api",
            format="openai",
            model="offline",
            base_url="http://127.0.0.1:1",
        ),
        max_attempts=1,
    )
    assert machine.run() != Verdict.PASS
    assert machine._state.consecutive_clean_rounds == 2
    failures = machine._state.round_history[-1]["acquisition_failures"]
    assert len(failures) == 3
    assert len({failure["fingerprint"] for failure in failures}) == 3


@pytest.mark.parametrize("collision", [False, True])
def test_real_product_confirmed_reset_clears_empty_window(review_workspace, monkeypatch, collision):
    machine = _machine(
        review_workspace,
        monkeypatch,
        payload=lambda machine, prompt: (
            llm_invoke.LLMInvokeError("owned failure")
            if collision and machine._state.round == 2
            else VALID
        ),
    )
    machine.autofixer = NoChangeAutoFixer()
    machine.l0_runner = lambda *args: (
        [
            StateFinding(
                id="actual-product",
                fingerprint="invoke-fail-qodo" if collision else "actual-product",
                source="L0",
                disposition=Disposition.CONFIRMED,
                file="control.ts",
                line_range=[2, 2],
                description="P1: actual product",
            )
        ]
        if machine._state.round == 2
        else [],
        [],
    )
    assert machine.run() != Verdict.PASS
    assert machine._state.consecutive_clean_rounds == 0
    assert machine._state.earned_clean_window["cycles"] == []
    assert machine._state.round_history[-1]["reset_observed"] is True
    from code_forge.state import product_round_history

    product = product_round_history(machine._state.round_history)[-1]["dispositions"]
    assert product["invoke-fail-qodo" if collision else "actual-product"] == "CONFIRMED"


@pytest.mark.parametrize("kind", ["zero", "partial", "phase"])
def test_early_failure_finalizes_observed_phases_without_fabrication(
    review_workspace, monkeypatch, kind
):
    machine = _machine(review_workspace, monkeypatch, rounds=1)
    if kind == "phase":
        machine.l1_provider = lambda: (_ for _ in ()).throw(TimeoutBreaker("owned primary"))
    else:
        import code_forge.receipt as receipt_module

        real_writer = receipt_module.write_receipts

        def writer(**kwargs):
            if kind == "partial":
                real_writer(**kwargs)
                (review_workspace / ".code-forge" / "receipts" / "receipt-c1p2.json").unlink()
            raise OSError("owned publication error")

        monkeypatch.setattr(receipt_module, "write_receipts", writer)
        machine._check_l1_can_still_converge = lambda findings: (_ for _ in ()).throw(
            TimeoutBreaker("owned primary")
        )
    with pytest.raises(TimeoutBreaker, match="owned primary"):
        machine.run()
    state = load_state(review_workspace / ".code-forge" / "state.json")
    assert state.verdict != Verdict.PASS
    assert len(state.round_history) == 1
    row = state.round_history[0]
    assert row["clean_credit_action"] == "unavailable"
    assert row["phase_status"]["l1"] == ("failed" if kind == "phase" else "returned")
    assert row["phase_status"]["l2"] == row["phase_status"]["e2e"] == "not_run"
    assert state.earned_clean_window["cycles"] == []
    resumed = _machine(review_workspace, monkeypatch, rounds=1)
    resumed._maybe_load_prior_state()
    assert resumed._continuation_round_index() == 1


@pytest.mark.parametrize("when", ["begin", "final"])
def test_state_persistence_failure_never_claims_pass(review_workspace, monkeypatch, when):
    import code_forge.machine as machine_module

    real_save = machine_module.save_state
    dispatches = []
    machine = _machine(
        review_workspace, monkeypatch, payload=lambda machine, prompt: dispatches.append(prompt) or VALID
    )

    def save(state, path):
        if (when == "begin" and state.round_history[-1]["clean_credit_action"] == "pending") or (
            when == "final" and state.verdict == Verdict.PASS
        ):
            raise OSError("owned save error")
        return real_save(state, path)

    monkeypatch.setattr(machine_module, "save_state", save)
    with pytest.raises(OSError, match="owned save error"):
        machine.run()
    assert machine._state.verdict != Verdict.PASS
    assert machine._state.converged is False
    if when == "begin":
        assert dispatches == []


def test_current_pending_no_files_denies_ready_window_and_reserves_highwater(
    review_workspace, monkeypatch
):
    root = review_workspace
    machine = _machine(root, monkeypatch)
    assert machine.run() == Verdict.PASS
    machine._begin_host_attempt(3)
    assert not _verify(root).passed
    reopened = _machine(root, monkeypatch, rounds=1)
    reopened._maybe_load_prior_state()
    assert reopened._continuation_round_index() == 4
    assert len(_receipts(root)) == 9


def test_receipt_global_other_source_highwater_still_consumes_id(review_workspace, monkeypatch):
    root = review_workspace
    machine = _machine(root, monkeypatch)
    assert machine.run() == Verdict.PASS
    path = _receipts(root)[0]
    data = json.loads(path.read_text())
    data.update(cycle=50, diff_sha256="another-source")
    (path.parent / "receipt-c50p1.json").write_text(json.dumps(data))
    assert machine._continuation_round_index() == 50
    assert not _verify(root).passed


def test_healthy_resume_revalidates_existing_ready_credit(review_workspace, monkeypatch):
    root = review_workspace
    assert _machine(root, monkeypatch).run() == Verdict.PASS
    resumed = _machine(root, monkeypatch, rounds=1)
    assert resumed.run() == Verdict.PASS
    assert resumed._state.consecutive_clean_rounds == 4
    assert [entry["cycle"] for entry in resumed._state.earned_clean_window["cycles"]] == [1, 2, 3, 4]
    assert _verify(root).passed


def test_terminal_floor_raised_after_capture_denies_short_window(review_workspace, monkeypatch):
    root = review_workspace

    def raise_floor(round_index):
        if round_index == 2:
            (root / ".code-forge" / "gate.yaml").write_text("verify:\n  required_cycles: 5\n")

    machine = _machine(root, monkeypatch, hook=raise_floor)
    assert machine.run() != Verdict.PASS
    assert machine._state.consecutive_clean_rounds == 3
    assert len(machine._state.earned_clean_window["cycles"]) == 3
    assert not _verify(root).passed


def _owned_git_diff(root):
    def git(*args):
        return subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout

    git("init", "-q")
    (root / "control.ts").write_text("const context = 1;\nconst end = 3;\n")
    git("add", "control.ts")
    git(
        "-c",
        "user.name=Owned Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "owned fixture baseline",
    )
    (root / "control.ts").write_text(CONTENT)
    git("add", "control.ts")
    return git("diff", "HEAD", "--unified=3")


def _cli_verify_and_code_hook(root):
    from code_forge.install_hooks import generate_hook_content

    source = Path(verify.__file__).resolve().parents[1]
    env = dict(
        os.environ,
        PYTHONPATH=str(source),
        PYTHONDONTWRITEBYTECODE="1",
        FORGE_COMMIT_CLASS="",
        FORGE_TEST_FIXTURE="1",
    )
    command = [sys.executable, "-B", "-m", "code_forge", "verify"]
    cli_result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=10)
    bin_dir = root / "owned-bin"
    bin_dir.mkdir(exist_ok=True)
    wrapper = bin_dir / "code-forge"
    # The generated hook's attestation executes the real CLI. Its later,
    # independent review/gate stages are explicitly outside this control.
    wrapper.write_text(
        '#!/bin/sh\ncase "$1" in\n'
        'verify) exec "$FORGE_FIXTURE_PYTHON" -B -m code_forge "$@" ;;\n'
        "review|gate-check) exit 0 ;;\n*) exit 97 ;;\nesac\n"
    )
    wrapper.chmod(0o700)
    hook = root / "owned-code-hook"
    hook.write_text(generate_hook_content("code-forge gate-check", None))
    env.update(PATH=str(bin_dir) + os.pathsep + env["PATH"], FORGE_FIXTURE_PYTHON=sys.executable)
    hook_result = subprocess.run(
        ["/bin/sh", str(hook)], cwd=root, env=env, capture_output=True, text=True, timeout=10
    )
    return cli_result, hook_result


@pytest.mark.parametrize("missing", [False, True])
def test_default_machine_cli_generated_hook_share_nonadjacent_earned_proof(
    review_workspace, monkeypatch, missing
):
    root = review_workspace
    diff = _owned_git_diff(root)
    machine = _machine(
        root,
        monkeypatch,
        diff=diff,
        payload=lambda machine, prompt: (
            llm_invoke.LLMInvokeError("owned interruption")
            if machine._state.round == 2 and "adversarial" in prompt.rsplit("You are a ", 1)[-1]
            else VALID
        ),
    )
    assert machine.run() != Verdict.PASS
    if missing:
        for path in _receipts(root):
            if json.loads(path.read_text())["cycle"] in (1, 2):
                path.unlink()
    resumed = _machine(root, monkeypatch, diff=diff, rounds=1)
    verdict = resumed.run()
    cli_result, hook_result = _cli_verify_and_code_hook(root)
    if missing:
        assert verdict != Verdict.PASS
        assert cli_result.returncode != 0 and hook_result.returncode != 0
    else:
        assert verdict == Verdict.PASS
        assert cli_result.returncode == 0, cli_result.stdout + cli_result.stderr
        assert hook_result.returncode == 0, hook_result.stdout + hook_result.stderr
        assert [entry["cycle"] for entry in resumed._state.earned_clean_window["cycles"]] == [1, 2, 4]


def _archive_inventory(path):
    if not path.exists() and not path.is_symlink():
        return {"absent": True}
    entries = {}
    for item in [path] if not path.is_dir() else [path, *sorted(path.rglob("*"))]:
        info = item.lstat()
        key = "." if item == path else str(item.relative_to(path))
        entry = {"mode": stat.S_IMODE(info.st_mode)}
        if item.is_symlink():
            entry["symlink"] = os.readlink(item)
        elif item.is_file():
            entry["sha256"] = hashlib.sha256(item.read_bytes()).hexdigest()
        else:
            entry["directory"] = True
        entries[key] = entry
    return entries


def _explicit_archive(root, *, fail_second=False):
    from code_forge.lock import ForgeLock

    active = root / ".code-forge"
    recovery = active / "recovery"
    paths = [active / "state.json", active / "receipts"]
    assert all(not path.is_symlink() for path in [*paths, recovery]), "symlinked archive root"
    archive = recovery / "owned-explicit-choice"
    assert not archive.exists()
    protected = [
        active / "gate.yaml",
        active / "tools.yaml",
        root / ".git" / "index",
        root / ".git" / "config",
    ]
    guards = {str(path): _archive_inventory(path) for path in protected}
    with ForgeLock(active / "code-forge.lock"):
        before = {path.name: _archive_inventory(path) for path in paths}
        archive.mkdir(parents=True)
        decision = {
            "decision": "explicit_fresh_review",
            "ready": False,
            "source_hash": json.loads(paths[0].read_text())["source_hash"],
            "original_count_diagnostic": json.loads(paths[0].read_text())["consecutive_clean_rounds"],
            "preimage": before,
            "protected": guards,
        }
        record = archive / "inventory.json"
        record.write_text(json.dumps(decision, indent=2))
        try:
            for index, path in enumerate(paths):
                if fail_second and index == 1:
                    raise OSError("owned second rename failure")
                if path.exists():
                    path.rename(archive / path.name)
            assert all(not path.exists() for path in paths)
            assert {path.name: _archive_inventory(archive / path.name) for path in paths} == before
            assert {str(path): _archive_inventory(path) for path in protected} == guards
            decision["ready"] = True
        except OSError as exc:
            decision["partial_error"] = str(exc)
            decision["locations"] = {
                path.name: {"active": path.exists(), "archive": (archive / path.name).exists()}
                for path in paths
            }
            record.write_text(json.dumps(decision, indent=2))
            raise
        record.write_text(json.dumps(decision, indent=2))
    return archive, before, guards


def _normal_cli_review(root, monkeypatch):
    from code_forge import cli
    from code_forge.context_sources import GatherResult

    # Owned transport and unrelated advisory/native runner seams; parsing,
    # baseline resolution, real L1 producer, machine, lock, writer and verify run.
    monkeypatch.setattr(
        llm_invoke,
        "_invoke_api",
        lambda *args, **kwargs: llm_invoke.LLMResult(copy.deepcopy(VALID), llm_invoke.Usage(), 0.0),
    )
    monkeypatch.setattr(cli, "_merge_user_into", lambda cfgs, gate: cfgs)
    monkeypatch.setattr(cli, "build_falsifier", lambda *args, **kwargs: StubFalsifier())
    monkeypatch.setattr(cli, "_run_test_assertion_review", lambda *args, **kwargs: [])
    monkeypatch.setattr("code_forge.context_sources.gather", lambda *args, **kwargs: GatherResult())
    monkeypatch.setattr(StateMachine, "_run_advisory_axes", lambda self: None)
    bound = []

    def l2(*, cwd):
        bound.append(cwd)
        return lambda *args, **kwargs: ([], [])

    monkeypatch.setattr(cli, "build_l2_runner", l2)
    monkeypatch.setattr(cli, "build_e2e_checker", lambda: lambda *args, **kwargs: [])
    registry = root / ".code-forge" / "owned-tools.yaml"
    registry.write_text("tools: {}\n")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "code-forge",
            "review",
            "--mode",
            "local",
            "--baseline",
            "HEAD",
            "--head",
            "INDEX",
            "--max-total-rounds",
            "3",
            "--allow-main",
            "--registry",
            str(registry),
            "--outlet",
            "subprocess",
            "--backend-url",
            "http://127.0.0.1:1",
            "--backend-format",
            "openai",
            "--backend-key-env",
            "FORGE_OWNED_OFFLINE_KEY",
            "--backend-model",
            "offline",
        ],
    )
    monkeypatch.setenv("FORGE_OWNED_OFFLINE_KEY", "owned-offline")
    result = cli.main()
    assert bound == [root], "normal caller L2 seam was not bound to actual fixture cwd"
    return result


def test_explicit_archive_normal_lowercase_caller_earns_fresh_proof(review_workspace, monkeypatch):
    root = review_workspace
    diff = _owned_git_diff(root)
    assert _machine(root, monkeypatch, diff=diff).run() == Verdict.PASS
    receipts = root / ".code-forge" / "receipts"
    (receipts / "owned-raw-audit.txt").write_bytes(b"retained corrupt/raw diagnostics\x00")
    (receipts / "owned-relative-link").symlink_to("owned-raw-audit.txt")
    (receipts / "receipt-c1p1.json").unlink()
    refused = _machine(root, monkeypatch, diff=diff, rounds=1)
    assert refused.run() != Verdict.PASS
    archive, before, guards = _explicit_archive(root)
    assert _normal_cli_review(root, monkeypatch) == 0
    state = load_state(root / ".code-forge" / "state.json")
    assert state.consecutive_clean_rounds == 3
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == [1, 2, 3]
    assert state.clean_window_migration is None
    assert {name: _archive_inventory(archive / name) for name in before} == before
    assert {path: _archive_inventory(Path(path)) for path in guards} == guards
    cli_result, hook_result = _cli_verify_and_code_hook(root)
    assert cli_result.returncode == hook_result.returncode == 0
    from code_forge import cli

    monkeypatch.setattr(sys, "argv", ["code-forge", "review", "--mode", "LOCAL"])
    assert cli.main() == 2


def test_explicit_archive_partial_rename_stops_before_normal_caller(review_workspace, monkeypatch):
    root = review_workspace
    diff = _owned_git_diff(root)
    _machine(root, monkeypatch, diff=diff).run()
    before = _archive_inventory(root / ".code-forge" / "receipts")
    with pytest.raises(OSError, match="owned second rename failure"):
        _explicit_archive(root, fail_second=True)
    archive = root / ".code-forge" / "recovery" / "owned-explicit-choice"
    decision = json.loads((archive / "inventory.json").read_text())
    assert decision["ready"] is False
    assert decision["locations"] == {
        "state.json": {"active": False, "archive": True},
        "receipts": {"active": True, "archive": False},
    }
    assert _archive_inventory(root / ".code-forge" / "receipts") == before
    assert not (root / ".code-forge" / "code-forge.lock").exists()


def _product_reader_machine(root, monkeypatch):
    machine = _machine(root, monkeypatch)
    machine.run()
    return machine


def test_product_promotion_reads_finalized_snapshot_before_pending_reservation(
    review_workspace, monkeypatch
):
    machine = _product_reader_machine(review_workspace, monkeypatch)
    prior = machine._state.round_history[-1]
    prior["dispositions"] = {"actual-product": "UNCERTAIN"}
    machine._state.fix_attempts["actual-product"] = machine.max_fix_attempts
    machine._begin_host_attempt(3)
    finding = StateFinding(
        "actual-product",
        "actual-product",
        "L0",
        Disposition.CONFIRMED,
        "control.ts",
        [2, 2],
        "actual redetect",
    )
    machine.l0_runner = lambda *args: ([finding], [])
    detected = machine._run_l0_phase()
    assert detected == [finding]
    assert machine._apply_promotion_stickiness(detected)[0].disposition == Disposition.UNCERTAIN
    prior["dispositions"]["actual-product"] = "CONFIRMED"
    finding.disposition = Disposition.CONFIRMED
    detected = machine._run_l0_phase()
    assert machine._apply_promotion_stickiness(detected)[0].disposition == Disposition.CONFIRMED


@pytest.mark.parametrize("reader", ["novelty", "reversion", "dismissal", "stall"])
def test_product_history_readers_use_observed_product_projection(review_workspace, monkeypatch, reader):
    machine = _product_reader_machine(review_workspace, monkeypatch)
    rows = machine._state.round_history
    marker = {
        "id": "l1-qodo-invoke-fail",
        "fingerprint": "invoke-fail-qodo",
        "pass_name": "qodo",
        "outcome": "error",
    }
    fp = "invoke-fail-qodo" if reader in ("novelty", "dismissal") else "actual-product"
    for row in rows:
        row["dispositions"] = {fp: "FIXED" if reader == "reversion" else "DISMISSED"}
        row["l0_fingerprints"] = [fp]
    if reader == "stall":
        for row in rows:
            row["dispositions"] = {fp: "CONFIRMED", "held": "UNCERTAIN"}
        rows[-1]["dispositions"][fp] = "DISMISSED"
        machine._begin_host_attempt(3)
        assert not machine._stalled_on_identical_round()
        rows[2]["dispositions"][fp] = "CONFIRMED"
        assert machine._stalled_on_identical_round()
        return
    if reader == "novelty":
        for row in rows:
            row.update(clean_credit_action="interrupted", acquisition_failures=[marker])
            row["l0_fingerprints"] = []
            row["dispositions"] = {fp: "CONFIRMED"}
    elif reader == "dismissal":
        latest = rows[-1]
        latest.update(clean_credit_action="interrupted", acquisition_failures=[marker])
        latest["l0_fingerprints"] = []
        latest["dispositions"] = {fp: "CONFIRMED"}
    else:
        latest = rows[-1]
        latest.update(
            clean_credit_action="unavailable",
            phase_status={name: "not_run" for name in latest["phase_status"]},
        )
        latest.pop("dispositions")
    machine._begin_host_attempt(3)
    finding = StateFinding(
        "actual-product", fp, "L0", Disposition.CONFIRMED, "control.ts", [2, 2], "P3: actual product"
    )
    machine._state.findings = [finding]
    if reader == "dismissal":
        machine._state.findings = []
        assert machine._apply_dismissed_stickiness([finding])[0].disposition == Disposition.DISMISSED
        finding.disposition = Disposition.CONFIRMED
        rows[1]["dispositions"][fp] = "CONFIRMED"
        assert machine._apply_dismissed_stickiness([finding])[0].disposition == Disposition.CONFIRMED
        finding.disposition = Disposition.STYLE
        machine._state.findings = [finding]
        assert machine._apply_dismissed_stickiness([finding])[0].disposition == Disposition.STYLE
    else:
        assert machine._fixpoint_reached().name == "RESET"


def test_product_diagnosis_receives_projection_while_host_history_keeps_markers(
    review_workspace, monkeypatch
):
    import code_forge.machine as machine_module

    seen = []
    real = machine_module.diagnose_non_convergence

    def diagnose(history, errors):
        seen.append(copy.deepcopy(history))
        return real(history, errors)

    monkeypatch.setattr(machine_module, "diagnose_non_convergence", diagnose)
    machine = _machine(
        review_workspace,
        monkeypatch,
        rounds=2,
        threshold=4,
        payload=lambda machine, prompt: (
            llm_invoke.LLMInvokeError("owned unavailable transport")
            if machine._state.round == 1
            else VALID
        ),
    )
    assert machine.run() == Verdict.FAIL
    assert seen == []
    resumed = _machine(review_workspace, monkeypatch, rounds=1, threshold=4)
    assert resumed.run() == Verdict.ESCALATED
    assert seen and all(row["clean_credit_action"] != "pending" for row in seen[-1])
    assert any("invoke-fail-qodo" in row["dispositions"] for row in resumed._state.round_history)
    assert all("invoke-fail-qodo" not in row["dispositions"] for row in seen[-1])
    assert len(resumed._state.round_history) == 3


def test_product_cycle_restart_resets_window_and_count_at_actual_local_site(
    review_workspace, monkeypatch
):
    root = review_workspace
    machine = _machine(root, monkeypatch, rounds=4, threshold=4)
    machine.autofixer = NoChangeAutoFixer()
    machine.max_fix_attempts = 10

    def l0(*args):
        if machine._state.round == 0:
            return [
                StateFinding(
                    "product",
                    "product",
                    "L0",
                    Disposition.STYLE,
                    "control.ts",
                    [2, 2],
                    "P2: actual product",
                )
            ], []
        if machine._state.round == 2:
            return [
                StateFinding(
                    "product",
                    "product",
                    "L0",
                    Disposition.CONFIRMED,
                    "control.ts",
                    [2, 2],
                    "P2: actual product",
                )
            ], []
        return [], []

    machine.l0_runner = l0
    machine._apply_dismissed_stickiness = lambda findings: findings
    assert _run_valid_transition(machine) != Verdict.PASS
    row = machine._state.round_history[2]
    assert row["fixpoint"] == "CYCLE_RESTART"
    assert row["clean_rounds_after"] == 0
    assert [entry["cycle"] for entry in machine._state.earned_clean_window["cycles"]] == [4]


def test_ci_and_trusted_no_l1_do_not_restore_local_credit(review_workspace, monkeypatch):
    root = review_workspace
    machine = _machine(root, monkeypatch, rounds=1)
    machine.mode = Mode.CI
    monkeypatch.setattr(
        verify,
        "_restore_earned_window",
        lambda *args, **kwargs: pytest.fail("LOCAL restore in CI/trusted path"),
    )
    assert machine.run() == Verdict.PASS
    assert machine._state.earned_clean_window is None
    assert _verify(
        root, cycles=[1], required_cycles=1, respect_floor=False, require_convergence=False
    ).passed
    trusted = _machine(root, monkeypatch, rounds=3)
    trusted.coverage_l1_active = False
    trusted.coverage_exempt_patterns = ["control.ts"]
    trusted.l1_provider = lambda: ([], [], llm_invoke.Usage(), 0.0)
    assert trusted.run() == Verdict.PASS


@pytest.mark.parametrize(
    "field",
    [
        "clean_credit_action",
        "phase_status",
        "acquisition_failures",
        "reset_observed",
        "source_hash",
        "reviewed_repositories",
    ],
)
def test_modern_single_null_key_presence_precedes_legacy_numeric_fallback(
    review_workspace, monkeypatch, field
):
    root = review_workspace
    _machine(root, monkeypatch).run()
    path = root / ".code-forge" / "state.json"
    _legacy_state(path)
    data = json.loads(path.read_text())
    data["round_history"][-1][field] = None
    path.write_text(json.dumps(data))
    assert not _verify(root).passed, "present modern authority fell through to numeric receipts"


def test_raw_receipt_capture_supplied_records_never_reloads_or_selects_automatically(
    review_workspace, monkeypatch
):
    root = review_workspace
    _machine(root, monkeypatch).run()
    records = verify._load_receipt_records(root / ".code-forge" / "receipts")
    reads = []
    real = Path.read_bytes
    paths = _receipts(root)

    def read(path):
        if path in paths:
            reads.append(path)
        return real(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(
        verify,
        "_select_default_earned_cycles",
        lambda *args, **kwargs: pytest.fail("explicit proof recursively selected default cycles"),
    )
    entry, result = verify._capture_earned_cycle(
        root,
        SOURCE_HASH,
        verify.parse_diff_files(DIFF),
        cycle=1,
        diff_text=DIFF,
        receipt_records=records,
    )
    assert result.passed and entry is not None
    assert reads == [], "capture reloaded a supplied raw-byte boundary"


@pytest.mark.parametrize("root_name", ["state.json", "receipts", "recovery"])
def test_explicit_archive_rejects_symlinked_roots(review_workspace, monkeypatch, root_name):
    root = review_workspace
    _owned_git_diff(root)
    _machine(root, monkeypatch).run()
    active = root / ".code-forge"
    path = active / root_name
    target = active / ("owned-original-" + root_name)
    if path.exists():
        path.rename(target)
    else:
        target.mkdir()
    path.symlink_to(target.name)
    with pytest.raises(AssertionError, match="symlinked archive root"):
        _explicit_archive(root)
    assert path.is_symlink()
    assert not (active / "code-forge.lock").exists()


def test_terminal_machine_rereads_original_digest_after_current_capture(review_workspace, monkeypatch):
    root = review_workspace

    def replace(round_index):
        if round_index == 2:
            path = root / ".code-forge" / "receipts" / "receipt-c1p1.json"
            path.write_bytes(path.read_bytes() + b"\n")

    machine = _machine(root, monkeypatch, hook=replace)
    assert machine.run() != Verdict.PASS, "terminal omitted original earned digest revalidation"
    assert not _verify(root).passed


def test_early_breaker_real_product_reset_wins_before_publication(review_workspace, monkeypatch):
    root = review_workspace
    first = _machine(root, monkeypatch, rounds=2)
    first.run()
    machine = _machine(root, monkeypatch, rounds=1)
    machine.autofixer = NoChangeAutoFixer()
    machine.l0_runner = lambda *args: (
        [
            StateFinding(
                "actual", "actual", "L0", Disposition.CONFIRMED, "control.ts", [2, 2], "P1: actual"
            )
        ],
        [],
    )
    machine.l1_provider = lambda: (_ for _ in ()).throw(TimeoutBreaker("owned primary"))
    with pytest.raises(TimeoutBreaker, match="owned primary"):
        machine.run()
    assert machine._state.consecutive_clean_rounds == 0
    assert machine._state.earned_clean_window["cycles"] == []
    state = load_state(root / ".code-forge" / "state.json")
    assert state.consecutive_clean_rounds == 0
    assert state.earned_clean_window["cycles"] == []
    assert state.round_history[-1]["clean_credit_action"] == "reset"


@pytest.mark.parametrize("identity", [[SOURCE_HASH], True, {"hash": SOURCE_HASH}])
@pytest.mark.parametrize("damage", ["pending", "replacement"])
def test_modern_nonstring_source_identity_cannot_fall_back_to_numeric_receipts(
    review_workspace, monkeypatch, identity, damage
):
    root = review_workspace
    machine = _machine(root, monkeypatch)
    assert machine.run() == Verdict.PASS
    if damage == "pending":
        machine._begin_host_attempt(3)
    else:
        receipt = _receipts(root)[0]
        receipt.write_bytes(receipt.read_bytes() + b"\n")
    path = root / ".code-forge" / "state.json"
    data = json.loads(path.read_text())
    data["source_hash"] = identity
    path.write_text(json.dumps(data))
    result = _verify(root)
    assert not result.passed, "nonstring modern source identity bypassed earned proof"
    assert _verify(root, cycles=[1, 2, 3]).passed


def test_valid_other_source_state_keeps_numeric_evidence_policy(review_workspace, monkeypatch):
    root = review_workspace
    _machine(root, monkeypatch).run()
    path = root / ".code-forge" / "state.json"
    data = json.loads(path.read_text())
    data["source_hash"] = "known-other-source"
    data["earned_clean_window"]["source_hash"] = "known-other-source"
    for row in data["round_history"]:
        row["source_hash"] = "known-other-source"
    path.write_text(json.dumps(data))
    assert _verify(root).passed


def test_pending_reservation_is_durable_before_frozen_gate(review_workspace, monkeypatch):
    import code_forge.machine as machine_module

    seen = []

    def gate(state):
        path = review_workspace / ".code-forge" / "state.json"
        assert path.exists(), "begin did not persist its pending reservation"
        disk = json.loads(path.read_text())
        assert disk["round_history"][-1]["clean_credit_action"] == "pending"
        assert disk["earned_clean_window"]["cycles"] == []
        seen.append(disk)
        return False

    monkeypatch.setattr(machine_module, "check_escalated_frozen", gate)
    machine = _machine(review_workspace, monkeypatch, rounds=1)
    assert machine.run() != Verdict.PASS
    assert len(seen) == 1


def test_current_interrupted_zero_files_denies_ready_window(review_workspace, monkeypatch):
    import code_forge.receipt as receipt_module

    root = review_workspace
    assert _machine(root, monkeypatch).run() == Verdict.PASS
    original = {path.name: path.read_bytes() for path in _receipts(root)}
    machine = _machine(
        root,
        monkeypatch,
        rounds=1,
        payload=lambda machine, prompt: llm_invoke.LLMInvokeError("owned invocation failure"),
    )
    primary = TimeoutBreaker("owned primary after observed acquisition")
    machine._check_l1_can_still_converge = lambda findings: (_ for _ in ()).throw(primary)

    def unavailable_writer(**kwargs):
        raise OSError("owned zero-file publication failure")

    monkeypatch.setattr(receipt_module, "write_receipts", unavailable_writer)
    with pytest.raises(TimeoutBreaker) as caught:
        machine.run()
    assert caught.value is primary
    state = load_state(root / ".code-forge" / "state.json")
    assert state.consecutive_clean_rounds == 3
    assert state.round_history[-1]["clean_credit_action"] == "interrupted"
    assert state.round_history[-1]["phase_status"]["l1"] == "returned"
    assert state.round_history[-1]["phase_status"]["l2"] == "not_run"
    assert {path.name: path.read_bytes() for path in _receipts(root)} == original
    result = _verify(root)
    assert not result.passed and "latest host attempt" in result.reason


def test_terminal_rereads_public_floor_after_candidate_persistence(review_workspace, monkeypatch):
    root = review_workspace
    machine = _machine(root, monkeypatch)
    persist = machine._persist_state
    changed = []

    def raise_after_candidate():
        persist()
        if (
            machine._state.consecutive_clean_rounds == 3
            and machine._state.round_history[-1]["clean_credit_action"] == "earned"
            and not changed
        ):
            (root / ".code-forge" / "gate.yaml").write_text("verify:\n  required_cycles: 5\n")
            changed.append(machine._state.round)

    monkeypatch.setattr(machine, "_persist_state", raise_after_candidate)
    verdict = machine.run()
    assert changed == [2]
    assert verdict != Verdict.PASS, "terminal verifier ignored the newly persisted public floor"
    assert machine._state.consecutive_clean_rounds == 3
    assert len(machine._state.earned_clean_window["cycles"]) == 3
    result = _verify(root)
    assert not result.passed and "floor demands 5" in result.reason


@pytest.mark.parametrize(
    "damage,reason",
    [
        ("window-source", "invalid earned clean window source"),
        ("window-scope", "invalid earned clean window repository scope"),
        ("window-cycles", "invalid earned clean window cycles"),
        ("window-entry", "invalid earned cycle entry"),
        ("window-digest", "invalid earned receipt digests"),
        ("history-container", "invalid round history"),
        ("failure-identity", "invalid acquisition failure identity"),
        ("failure-outcome", "invalid acquisition failure outcome"),
        ("failure-marker", "invalid acquisition producer marker"),
        ("pending-observed", "pending host round contains finalized observations"),
        ("snapshot-disposition", "invalid observed product snapshot"),
        ("earned-transition", "earned host round lacks CLEAN observation"),
        ("reset-transition", "reset host round lacks reset observation"),
        ("snapshot-absent", "finalized host round lacks product snapshot"),
        ("round-scope", "host round repository scope mismatch"),
        ("round-count", "clean count disagrees with round history"),
        ("window-history", "earned window disagrees with clean/reset history"),
        ("source-absent", "modern state lacks source identity"),
    ],
)
def test_modern_authority_reachable_corruption_guards(review_workspace, monkeypatch, damage, reason):
    root = review_workspace
    assert _machine(root, monkeypatch).run() == Verdict.PASS
    path = root / ".code-forge" / "state.json"
    data = json.loads(path.read_text())
    window = data["earned_clean_window"]
    row = data["round_history"][-1]
    marker = {
        "id": "l1-qodo-invoke-fail",
        "fingerprint": "invoke-fail-qodo",
        "pass_name": "qodo",
        "outcome": "error",
    }
    if damage == "window-source":
        window["source_hash"] = ""
    elif damage == "window-scope":
        window["reviewed_repositories"] = {}
    elif damage == "window-cycles":
        window["cycles"] = {}
    elif damage == "window-entry":
        window["cycles"][0] = {}
    elif damage == "window-digest":
        window["cycles"][0]["receipt_sha256"]["1"] = "invalid"
    elif damage == "history-container":
        data["round_history"] = {}
    elif damage == "failure-identity":
        row["acquisition_failures"] = [{}]
    elif damage == "failure-outcome":
        marker["outcome"] = "completed"
        row["acquisition_failures"] = [marker]
    elif damage == "failure-marker":
        marker["fingerprint"] = "parsed-model-mimic"
        row["acquisition_failures"] = [marker]
    elif damage == "pending-observed":
        row["clean_credit_action"] = "pending"
    elif damage == "snapshot-disposition":
        row["dispositions"] = {"product": "invented"}
    elif damage == "earned-transition":
        row["fixpoint"] = "RESET"
    elif damage == "reset-transition":
        row["clean_credit_action"] = "reset"
    elif damage == "snapshot-absent":
        row.pop("dispositions")
    elif damage == "round-scope":
        row["reviewed_repositories"] = {"other": "f" * 64}
    elif damage == "round-count":
        row["clean_rounds_after"] = 999
    elif damage == "window-history":
        window["cycles"] = window["cycles"][:-1]
    else:
        data["source_hash"] = None
    path.write_text(json.dumps(data))
    original = path.read_bytes()
    result = _verify(root)
    assert not result.passed and reason in result.reason
    assert path.read_bytes() == original


@pytest.mark.parametrize("history,reason", [(None, "container"), ([None], "row")])
def test_round_history_validator_rejects_untyped_input(history, reason):
    from code_forge.state import validate_round_history

    with pytest.raises(CorruptedStateError, match=reason):
        validate_round_history(history)


@pytest.mark.parametrize("cycle", [0, True])
def test_earned_capture_invalid_cycle_is_recoverably_refused(review_workspace, cycle):
    entry, result = verify._capture_earned_cycle(
        review_workspace, SOURCE_HASH, verify.parse_diff_files(DIFF), cycle=cycle, diff_text=DIFF
    )
    assert entry is None and not result.passed and "positive int" in result.reason
    assert _receipts(review_workspace) == []


def test_earned_capture_corrupt_raw_file_is_recoverably_refused(review_workspace, monkeypatch):
    root = review_workspace
    assert _machine(root, monkeypatch).run() == Verdict.PASS
    _receipts(root)[0].write_bytes(b"{corrupt")
    entry, result = verify._capture_earned_cycle(
        root, SOURCE_HASH, verify.parse_diff_files(DIFF), cycle=1, diff_text=DIFF
    )
    assert entry is None and not result.passed and "corrupt receipt" in result.reason


@pytest.mark.parametrize("source", [SOURCE_HASH, None])
def test_true_legacy_state_without_window_retains_numeric_default(review_workspace, monkeypatch, source):
    root = review_workspace
    assert _machine(root, monkeypatch).run() == Verdict.PASS
    path = root / ".code-forge" / "state.json"
    _legacy_state(path)
    data = json.loads(path.read_text())
    data["source_hash"] = source
    path.write_text(json.dumps(data))
    assert _verify(root).passed


def test_restore_modern_missing_window_refuses_before_new_dispatch(review_workspace, monkeypatch):
    root = review_workspace
    assert _machine(root, monkeypatch).run() == Verdict.PASS
    path = root / ".code-forge" / "state.json"
    data = json.loads(path.read_text())
    data.pop("earned_clean_window")
    path.write_text(json.dumps(data))
    original = path.read_bytes()
    machine = _machine(root, monkeypatch, payload=lambda *args: pytest.fail("unproved dispatch"))
    assert machine.run() == Verdict.FAIL
    assert any(
        "modern history requires an earned clean window" in e for e in machine._state.infra_errors
    )
    assert machine._state.consecutive_clean_rounds == 3
    assert path.read_bytes() == original
    assert machine._state.earned_clean_window is None
    assert machine._state.round_history == data["round_history"]


def test_other_source_history_preserves_current_scope_without_borrowed_credit(
    review_workspace, monkeypatch
):
    root = review_workspace
    assert _machine(root, monkeypatch).run() == Verdict.PASS
    path = root / ".code-forge" / "state.json"
    data = json.loads(path.read_text())
    data["round_history"][0]["source_hash"] = "known-earlier-source"
    data["round_history"][1]["clean_rounds_after"] = 1
    data["round_history"][2]["clean_rounds_after"] = 2
    data["consecutive_clean_rounds"] = 2
    data["earned_clean_window"]["cycles"] = data["earned_clean_window"]["cycles"][1:]
    path.write_text(json.dumps(data))
    assert _verify(root, required_cycles=2, respect_floor=False).passed
    assert not _verify(root).passed


def test_reserved_attempt_and_finalizer_reject_duplicate_authority(review_workspace, monkeypatch):
    root = review_workspace
    machine = _machine(root, monkeypatch)
    assert machine.run() == Verdict.PASS
    original = (root / ".code-forge" / "state.json").read_bytes()
    with pytest.raises(CorruptedStateError, match="already reserved"):
        machine._begin_host_attempt(2)
    history = copy.deepcopy(machine._state.round_history)
    machine._finish_host_attempt("earned", None)
    assert machine._state.round_history == history
    assert (root / ".code-forge" / "state.json").read_bytes() == original
    machine._begin_host_attempt(3)
    machine._state.round_history[-1]["clean_credit_action"] = "earned"
    with pytest.raises(CorruptedStateError, match="finalized twice"):
        machine._finish_host_attempt("earned", None)


def test_real_hold_confirmation_finalizes_unavailable_frozen_attempt(review_workspace, monkeypatch):
    from code_forge.hold import run_hold_ui

    root = review_workspace
    monkeypatch.delenv("FORGE_HOLD_NONINTERACTIVE", raising=False)

    def recurrent_l0(*args):
        return [
            StateFinding(
                "product",
                "actual-unfixed-product",
                "L0",
                Disposition.CONFIRMED,
                "control.ts",
                [2, 2],
                "P1: actual unfixed product",
            )
        ], []

    first = _machine(root, monkeypatch, rounds=4)
    first.autofixer = NoChangeAutoFixer()
    first.l0_runner = recurrent_l0
    assert first.run() == Verdict.PENDING
    assert first._state.fix_attempts["actual-unfixed-product"] == 3
    promoted = first
    assert promoted._state.hold_reason is not None
    path = root / ".code-forge" / "state.json"
    run_hold_ui(promoted._state, path, input_fn=lambda _: "c", output_fn=lambda _: None)
    held = load_state(path)
    prefix = copy.deepcopy(held.round_history)
    assert held.hold_reason is None
    assert "actual-unfixed-product" in held.promoted_fingerprints
    assert held.findings[0].disposition == Disposition.CONFIRMED
    frozen = _machine(root, monkeypatch, rounds=1, payload=lambda *args: pytest.fail("frozen dispatch"))
    assert frozen.run() == Verdict.ESCALATED
    state = load_state(path)
    assert state.round_history[:-1] == prefix
    row = state.round_history[-1]
    assert row["clean_credit_action"] == "unavailable"
    assert set(row["phase_status"].values()) == {"not_run"}
    assert "dispositions" not in row
    assert row["round"] == prefix[-1]["round"] + 1
    assert state.consecutive_clean_rounds == 0 and state.earned_clean_window["cycles"] == []


def test_real_local_survivor_streak_finalizes_reset_without_native_run(review_workspace, monkeypatch):
    root = review_workspace
    (root / ".code-forge" / "gate.yaml").write_text(
        "verify:\n  required_cycles: 3\ntest:\n  command: ['true']\n"
    )
    machine = _machine(root, monkeypatch, rounds=3)
    calls = []

    def survivor(diff_files, command, **kwargs):
        calls.append((copy.deepcopy(diff_files), command, kwargs))
        return [
            StateFinding(
                "mutant-owned",
                "owned-survivor",
                "MUTANT",
                Disposition.CONFIRMED,
                "control.ts",
                [2, 2],
                "P1: actual survivor",
            )
        ], []

    machine.l2_runner = survivor
    assert machine.run() == Verdict.FAIL
    assert len(calls) == 3 and all(command == ["true"] for _, command, _ in calls)
    state = load_state(root / ".code-forge" / "state.json")
    assert state.consecutive_survivor_rounds == 3
    assert len(state.round_history) == 3
    assert state.round_history[-1]["clean_credit_action"] == "reset"
    assert state.round_history[-1]["reset_observed"] is True
    assert state.consecutive_clean_rounds == 0 and state.earned_clean_window["cycles"] == []
    assert any("surviving mutants reported in 3 consecutive rounds" in e for e in state.infra_errors)


@pytest.mark.parametrize(
    "damage",
    [
        "empty_acquisition",
        "reset_flag",
        "reset_fixpoint",
        "reset_combined",
        "missing_fixpoint",
        "l1_failed",
        "l1_not_run",
    ],
)
def test_interrupted_history_requires_consistent_acquisition_observation(
    review_workspace, monkeypatch, damage
):
    root = review_workspace

    def payload(machine, prompt):
        if machine._state.round == 2 and "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("owned acquisition failure")
        return VALID

    assert _machine(root, monkeypatch, payload=payload).run() != Verdict.PASS
    assert _machine(root, monkeypatch, rounds=1).run() == Verdict.PASS
    assert _verify(root).passed
    receipts = {path: path.read_bytes() for path in _receipts(root)}
    path = root / ".code-forge" / "state.json"
    data = json.loads(path.read_text())
    row = data["round_history"][2]
    assert row["clean_credit_action"] == "interrupted"
    assert row["acquisition_failures"]
    if damage == "empty_acquisition":
        row["acquisition_failures"] = []
    elif damage == "reset_flag":
        row["reset_observed"] = True
    elif damage == "reset_fixpoint":
        row["fixpoint"] = "RESET"
    elif damage == "reset_combined":
        row.update(reset_observed=True, fixpoint="RESET")
    elif damage == "missing_fixpoint":
        row.pop("fixpoint")
    else:
        row["phase_status"]["l1"] = "failed" if damage == "l1_failed" else "not_run"
    path.write_text(json.dumps(data))
    result = _verify(root)
    assert not result.passed and "interrupted host round" in result.reason
    assert {path: path.read_bytes() for path in receipts} == receipts


def test_interrupted_l1_then_failed_later_phase_preserves_proved_credit(review_workspace, monkeypatch):
    root = review_workspace

    def payload(machine, prompt):
        if machine._state.round == 2 and "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("owned acquisition failure")
        return VALID

    machine = _machine(root, monkeypatch, payload=payload)
    real_phase = machine._run_l2_phase

    def later_phase():
        if machine._state.round == 2:
            raise RuntimeError("owned later phase failure")
        return real_phase()

    monkeypatch.setattr(machine, "_run_l2_phase", later_phase)
    with pytest.raises(RuntimeError, match="owned later phase failure"):
        machine.run()
    path = root / ".code-forge" / "state.json"
    state = load_state(path)
    row = state.round_history[-1]
    assert row["clean_credit_action"] == "interrupted"
    assert row["phase_status"]["l1"] == "returned"
    assert row["phase_status"]["l2"] == "failed"
    assert row["acquisition_failures"] and row["fixpoint"] == "CLEAN"
    assert row["reset_observed"] is False
    assert state.consecutive_clean_rounds == 2
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == [1, 2]
    assert _machine(root, monkeypatch, rounds=1).run() == Verdict.PASS
    assert _verify(root).passed
    state = load_state(path)
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == [1, 2, 4]


@pytest.mark.parametrize(
    "damage",
    [
        "array",
        "null",
        "scalar",
        "string",
        "cost_null",
        "cost_array",
        "findings_nested_array",
        "findings_null_item",
        "findings_null",
        "dispositions_array",
    ],
)
def test_public_verify_malformed_state_returns_unavailable(review_workspace, monkeypatch, damage):
    root = review_workspace
    assert _machine(root, monkeypatch).run() == Verdict.PASS
    assert _verify(root).passed
    path = root / ".code-forge" / "state.json"
    data = json.loads(path.read_text())
    if damage in ("array", "null", "scalar", "string"):
        data = {"array": [], "null": None, "scalar": 7, "string": "invalid"}[damage]
    elif damage == "cost_null":
        data["cost"] = None
    elif damage == "cost_array":
        data["cost"] = []
    elif damage == "findings_nested_array":
        data["findings"] = [[]]
    elif damage == "findings_null_item":
        data["findings"] = [None]
    elif damage == "findings_null":
        data["findings"] = None
    else:
        data["dispositions"] = []
    path.write_text(json.dumps(data))
    original = path.read_bytes()
    result = _verify(root)
    assert not result.passed and "unavailable earned state" in result.reason
    assert "state.json" in result.reason
    assert path.read_bytes() == original


@pytest.mark.parametrize("phase", ROUND_PHASES)
@pytest.mark.parametrize("status", ["failed", "not_run"])
def test_earned_history_requires_every_phase_returned(review_workspace, monkeypatch, phase, status):
    root = review_workspace
    assert _machine(root, monkeypatch).run() == Verdict.PASS
    assert _verify(root).passed
    path = root / ".code-forge" / "state.json"
    data = json.loads(path.read_text())
    row = data["round_history"][-1]
    assert row["clean_credit_action"] == "earned"
    assert set(row["phase_status"]) == set(ROUND_PHASES)
    assert set(row["phase_status"].values()) == {"returned"}
    assert row["acquisition_failures"] == []
    receipts = {receipt: receipt.read_bytes() for receipt in _receipts(root)}
    row["phase_status"][phase] = status
    path.write_text(json.dumps(data))
    original = path.read_bytes()
    result = _verify(root)
    assert not result.passed and "earned host round" in result.reason
    assert path.read_bytes() == original
    assert {receipt: receipt.read_bytes() for receipt in receipts} == receipts


def test_earned_history_requires_no_acquisition_failure(review_workspace, monkeypatch):
    root = review_workspace
    assert _machine(root, monkeypatch).run() == Verdict.PASS
    assert _verify(root).passed
    path = root / ".code-forge" / "state.json"
    data = json.loads(path.read_text())
    row = data["round_history"][-1]
    assert row["clean_credit_action"] == "earned"
    assert set(row["phase_status"].values()) == {"returned"}
    assert row["acquisition_failures"] == []
    receipts = {receipt: receipt.read_bytes() for receipt in _receipts(root)}
    row["acquisition_failures"] = [
        {
            "id": "l1-qodo-invoke-fail",
            "fingerprint": "invoke-fail-qodo",
            "pass_name": "qodo",
            "outcome": "error",
        }
    ]
    path.write_text(json.dumps(data))
    original = path.read_bytes()
    result = _verify(root)
    assert not result.passed and "earned host round" in result.reason
    assert path.read_bytes() == original
    assert {receipt: receipt.read_bytes() for receipt in receipts} == receipts


def _pending_after_sigint(root, monkeypatch, *, earned_rounds=3):
    import signal

    first = _machine(root, monkeypatch, rounds=earned_rounds)
    first.run()
    path = root / ".code-forge" / "state.json"
    prior = load_state(path)
    receipts = {p.name: p.read_bytes() for p in _receipts(root)}
    dispatches = []

    def payload(active, prompt):
        dispatches.append(active._state.round)
        return VALID

    cancelled = _machine(root, monkeypatch, rounds=1, payload=payload)

    def interrupt(*args):
        disk = json.loads(path.read_text())
        row = disk["round_history"][-1]
        assert row["clean_credit_action"] == "pending"
        assert set(row["phase_status"].values()) == {"not_run"}
        signal.raise_signal(signal.SIGINT)

    monkeypatch.setattr(cancelled, "_start_host_execution", interrupt)
    with pytest.raises(KeyboardInterrupt):
        cancelled.run()
    pending = load_state(path)
    assert not dispatches
    assert pending.consecutive_clean_rounds == prior.consecutive_clean_rounds == earned_rounds
    assert pending.earned_clean_window == prior.earned_clean_window
    assert {p.name: p.read_bytes() for p in _receipts(root)} == receipts
    assert not _verify(root).passed
    return pending, receipts


@pytest.mark.parametrize("earned_rounds", [1, 3])
def test_pending_resume_revalidates_credit_then_executes_fresh_round(
    review_workspace, monkeypatch, earned_rounds
):
    root = review_workspace
    pending, receipts = _pending_after_sigint(root, monkeypatch, earned_rounds=earned_rounds)
    pending_history = copy.deepcopy(pending.round_history)
    path = root / ".code-forge" / "state.json"
    raw = path.read_bytes()
    restored = verify._restore_earned_window(
        pending,
        root,
        SOURCE_HASH,
        verify.parse_diff_files(DIFF),
        diff_text=DIFF,
        reviewed_repositories=None,
    )
    assert restored.passed, restored.reason
    public_boundary = verify._validate_earned_state(
        pending,
        root,
        SOURCE_HASH,
        verify.parse_diff_files(DIFF),
        receipt_records=verify._load_receipt_records(root / ".code-forge" / "receipts"),
        hardened=True,
        diff_text=DIFF,
        reviewed_repositories=None,
    )
    assert not public_boundary.passed and "pending" in public_boundary.reason
    assert pending.consecutive_clean_rounds == earned_rounds
    assert pending.round_history == pending_history
    assert path.read_bytes() == raw
    assert not _verify(root).passed
    dispatches = []

    def payload(active, prompt):
        dispatches.append(active._state.round)
        return VALID

    resumed = _machine(root, monkeypatch, rounds=1, payload=payload)
    verdict = resumed.run()
    assert dispatches == [earned_rounds + 1] * 3, resumed._state.infra_errors
    after = load_state(path)
    assert after.round_history[:-1] == pending_history
    assert after.round_history[-1]["clean_credit_action"] == "earned"
    assert after.consecutive_clean_rounds == earned_rounds + 1
    assert [entry["cycle"] for entry in after.earned_clean_window["cycles"]] == [
        *range(1, earned_rounds + 1),
        earned_rounds + 2,
    ]
    assert all(
        (root / ".code-forge" / "receipts" / name).read_bytes() == raw for name, raw in receipts.items()
    )
    assert not list((root / ".code-forge" / "receipts").glob(f"receipt-c{earned_rounds + 1}p*.json"))
    assert (verdict == Verdict.PASS) is (earned_rounds == 3)
    assert _verify(root).passed is (earned_rounds == 3)


@pytest.mark.parametrize(
    "damage",
    [
        "prior-count",
        "prior-missing",
        "prior-phase",
        "receipt-bytes",
        "receipt-missing",
        "proof-digest",
        "proof-missing",
        "pending-failed",
        "pending-returned",
        "pending-reset",
        "pending-snapshot",
        "pending-fixpoint",
        "pending-count",
    ],
)
def test_pending_resume_rejects_damaged_prior_proof_or_pending_observation(
    review_workspace, monkeypatch, damage
):
    root = review_workspace
    _pending_after_sigint(root, monkeypatch)
    path = root / ".code-forge" / "state.json"
    raw = json.loads(path.read_text())
    row = raw["round_history"][-1]
    if damage == "prior-count":
        raw["round_history"][0]["clean_rounds_after"] = 2
    elif damage == "prior-missing":
        raw["round_history"].pop(0)
    elif damage == "prior-phase":
        raw["round_history"][0]["phase_status"]["l2"] = "failed"
    elif damage == "receipt-bytes":
        receipt = _receipts(root)[0]
        receipt.write_bytes(receipt.read_bytes() + b"\n")
    elif damage == "receipt-missing":
        _receipts(root)[0].unlink()
    elif damage == "proof-digest":
        raw["earned_clean_window"]["cycles"][0]["receipt_sha256"]["1"] = "0" * 64
    elif damage == "proof-missing":
        raw["earned_clean_window"]["cycles"].pop(0)
    elif damage == "pending-failed":
        row["phase_status"]["l0"] = "failed"
    elif damage == "pending-returned":
        row["phase_status"]["l0"] = "returned"
    elif damage == "pending-reset":
        row["reset_observed"] = True
    elif damage == "pending-snapshot":
        row["dispositions"] = {}
    elif damage == "pending-fixpoint":
        row["fixpoint"] = "RESET"
    else:
        row["clean_rounds_after"] = 4
    path.write_text(json.dumps(raw))
    state_bytes = path.read_bytes()
    receipt_bytes = {p.name: p.read_bytes() for p in _receipts(root)}
    dispatches = []

    def payload(active, prompt):
        dispatches.append(active._state.round)
        return VALID

    resumed = _machine(root, monkeypatch, rounds=1, payload=payload)
    try:
        verdict = resumed.run()
    except CorruptedStateError:
        verdict = Verdict.FAIL
    assert verdict != Verdict.PASS
    assert not dispatches
    assert not _verify(root).passed
    assert path.read_bytes() == state_bytes
    assert {p.name: p.read_bytes() for p in _receipts(root)} == receipt_bytes


def test_pending_resume_new_product_reset_discards_prior_credit(review_workspace, monkeypatch):
    root = review_workspace
    pending, receipts = _pending_after_sigint(root, monkeypatch)
    history = copy.deepcopy(pending.round_history)
    resumed = _machine(root, monkeypatch, rounds=1)
    resumed.autofixer = NoChangeAutoFixer()
    resumed.l0_runner = lambda *args: (
        [
            StateFinding(
                "new-product",
                "new-product",
                "L0",
                Disposition.CONFIRMED,
                "control.ts",
                [2, 2],
                "P1: actual product",
            )
        ],
        [],
    )
    assert resumed.run() != Verdict.PASS
    state = load_state(root / ".code-forge" / "state.json")
    assert state.round_history[:-1] == history
    assert state.round_history[-1]["clean_credit_action"] == "reset"
    assert state.round_history[-1]["reset_observed"] is True
    assert state.consecutive_clean_rounds == 0 and state.earned_clean_window["cycles"] == []
    assert not _verify(root).passed
    assert all(
        (root / ".code-forge" / "receipts" / name).read_bytes() == raw for name, raw in receipts.items()
    )


class _OwnedCancellation(BaseException):
    pass


def _cancel_local_at(machine, monkeypatch, stage, primary):
    fired = []

    def cancel():
        if not fired:
            fired.append(stage)
            raise primary

    phases = {
        "rulepack": "_run_rulepack_blocking_phase",
        "l1": "l1_provider",
        "l2": "_run_l2_phase",
        "e2e": "_run_e2e_phase",
        "coverage": "_run_coverage_phase",
    }
    if stage in phases:
        original = getattr(machine, phases[stage])

        def operation(*args, **kwargs):
            cancel()
            return original(*args, **kwargs)

        monkeypatch.setattr(machine, phases[stage], operation)
    elif stage == "l0-observed":
        original = machine._observe_phase

        def observe(name, operation):
            result = original(name, operation)
            if name == "l0":
                cancel()
            return result

        monkeypatch.setattr(machine, "_observe_phase", observe)
    elif stage in ("autofix", "snapshot", "publication", "round-gate", "hook"):
        method = {
            "autofix": "_apply_autofix_loop_to",
            "snapshot": "_append_round_snapshot",
            "publication": "_publish_l1_receipts",
            "round-gate": "_receipt_gate_round_errors",
        }.get(stage)
        if stage == "hook":
            machine.post_round_hook = lambda _: cancel()
        else:
            original = getattr(machine, method)

            def after(*args, **kwargs):
                result = original(*args, **kwargs)
                cancel()
                return result

            monkeypatch.setattr(machine, method, after)
    elif stage in ("finish-before", "finish-after"):
        original = machine._finish_host_attempt

        def finish(action, fixpoint):
            if stage == "finish-before":
                cancel()
            result = original(action, fixpoint)
            if stage == "finish-after":
                cancel()
            return result

        monkeypatch.setattr(machine, "_finish_host_attempt", finish)
    elif stage == "finalized-save":
        original = machine._persist_state

        def persist():
            if machine._state.round_history[-1]["clean_credit_action"] in ("earned", "reset"):
                cancel()
            return original()

        monkeypatch.setattr(machine, "_persist_state", persist)
    elif stage == "capture":
        original = verify._capture_earned_cycle

        def capture(*args, **kwargs):
            result = original(*args, **kwargs)
            if machine._host_attempt_round is not None:
                assert kwargs["cycle"] == machine._host_attempt_round + 1
                cancel()
            return result

        monkeypatch.setattr(verify, "_capture_earned_cycle", capture)
    elif stage in ("clean-append", "proof-append"):

        class InterruptingList(list):
            def append(self, value):
                super().append(value)
                cancel()

        original = machine._execute_round

        def execute(index):
            original(index)
            if stage == "clean-append":
                machine._clean_window_cycles = InterruptingList(machine._clean_window_cycles)
            else:
                machine._state.earned_clean_window["cycles"] = InterruptingList(
                    machine._state.earned_clean_window["cycles"]
                )

        monkeypatch.setattr(machine, "_execute_round", execute)
    else:
        original = machine._receipt_gate_terminal_errors

        def terminal():
            result = original()
            cancel()
            return result

        monkeypatch.setattr(machine, "_receipt_gate_terminal_errors", terminal)
    return fired


@pytest.mark.parametrize(
    "stage",
    [
        "l0-observed",
        "autofix",
        "rulepack",
        "l1",
        "l2",
        "e2e",
        "coverage",
        "snapshot",
        "publication",
        "hook",
        "round-gate",
        "finish-before",
        "finish-after",
        "finalized-save",
    ],
)
@pytest.mark.parametrize("kind", ["keyboard", "system-exit", "other-base"])
def test_cancellation_preserves_actual_reset_across_local_boundaries(
    review_workspace, monkeypatch, stage, kind
):
    root = review_workspace
    _machine(root, monkeypatch, rounds=2).run()
    before = {p.name: p.read_bytes() for p in _receipts(root)}
    machine = _machine(root, monkeypatch, rounds=1)
    machine.autofixer = NoChangeAutoFixer()
    machine.l0_runner = lambda *args: (
        [
            StateFinding(
                "actual-reset",
                "actual-reset",
                "L0",
                Disposition.CONFIRMED,
                "control.ts",
                [2, 2],
                "P1: actual product",
            )
        ],
        [],
    )
    primary = {
        "keyboard": KeyboardInterrupt("owned"),
        "system-exit": SystemExit(73),
        "other-base": _OwnedCancellation("owned"),
    }[kind]
    fired = _cancel_local_at(machine, monkeypatch, stage, primary)
    with pytest.raises(type(primary)) as caught:
        machine.run()
    assert caught.value is primary and fired == [stage]
    state = load_state(root / ".code-forge" / "state.json")
    row = state.round_history[-1]
    assert row["clean_credit_action"] == "reset"
    assert row["reset_observed"] and row["phase_status"]["l0"] == "returned"
    assert row["dispositions"]["actual-reset"] == "CONFIRMED"
    assert state.consecutive_clean_rounds == 0 and state.earned_clean_window["cycles"] == []
    assert all(
        (root / ".code-forge/receipts" / name).read_bytes() == raw for name, raw in before.items()
    )
    assert not _verify(root).passed
    resumed = _machine(root, monkeypatch, rounds=1)
    assert resumed.run() != Verdict.PASS
    assert resumed._state.consecutive_clean_rounds == 1
    assert not _verify(root).passed


@pytest.mark.parametrize(
    "stage",
    [
        "capture",
        "clean-append",
        "proof-append",
        "finish-before",
        "finish-after",
        "finalized-save",
        "terminal",
    ],
)
def test_cancellation_rolls_back_only_unfinished_credit(review_workspace, monkeypatch, stage):
    root = review_workspace
    _machine(root, monkeypatch, rounds=2).run()
    machine = _machine(root, monkeypatch, rounds=1)
    primary = _OwnedCancellation("owned")
    fired = _cancel_local_at(machine, monkeypatch, stage, primary)
    with pytest.raises(_OwnedCancellation) as caught:
        machine.run()
    assert caught.value is primary and fired == [stage]
    state = load_state(root / ".code-forge" / "state.json")
    completed = stage in ("finish-after", "finalized-save", "terminal")
    assert state.consecutive_clean_rounds == (3 if completed else 2)
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == (
        [1, 2, 3] if completed else [1, 2]
    )
    assert machine._clean_window_cycles == ([1, 2, 3] if completed else [1, 2])
    assert state.round_history[-1]["clean_credit_action"] == ("earned" if completed else "unavailable")
    assert _verify(root).passed is completed


@pytest.mark.parametrize("secondary", [SystemExit(74), _OwnedCancellation("secondary")])
def test_cancellation_cleanup_failure_preserves_primary_and_durable_refusal(
    review_workspace, monkeypatch, secondary
):
    root = review_workspace
    _machine(root, monkeypatch, rounds=2).run()
    machine = _machine(root, monkeypatch, rounds=1)
    machine.autofixer = NoChangeAutoFixer()
    machine.l0_runner = lambda *args: (
        [
            StateFinding(
                "actual-reset",
                "actual-reset",
                "L0",
                Disposition.CONFIRMED,
                "control.ts",
                [2, 2],
                "P1: actual product",
            )
        ],
        [],
    )
    primary = KeyboardInterrupt("primary")
    _cancel_local_at(machine, monkeypatch, "l1", primary)
    original = machine._persist_state

    def persist():
        if machine._state.round_history[-1]["clean_credit_action"] == "reset":
            raise secondary
        return original()

    monkeypatch.setattr(machine, "_persist_state", persist)
    with pytest.raises(KeyboardInterrupt) as caught:
        machine.run()
    assert caught.value is primary
    assert any(
        "host attempt save failed after KeyboardInterrupt" in e for e in machine._state.infra_errors
    )
    state = load_state(root / ".code-forge/state.json")
    assert state.round_history[-1]["clean_credit_action"] == "unavailable"
    assert not _verify(root).passed
    assert _machine(root, monkeypatch, rounds=1).run() == Verdict.FAIL


def test_execution_marker_is_durable_before_first_phase(review_workspace, monkeypatch):
    root = review_workspace
    _machine(root, monkeypatch, rounds=2).run()
    machine = _machine(root, monkeypatch, rounds=1)
    primary = _OwnedCancellation("before first result")
    observed = []

    def interrupt(*args):
        observed.append(json.loads((root / ".code-forge" / "state.json").read_text()))
        raise primary

    machine.l0_runner = interrupt
    with pytest.raises(_OwnedCancellation) as caught:
        machine.run()
    assert caught.value is primary
    assert observed[0]["round_history"][-1]["clean_credit_action"] == "unavailable"
    assert set(observed[0]["round_history"][-1]["phase_status"].values()) == {"not_run"}
    assert observed[0]["consecutive_clean_rounds"] == 2
    saved = load_state(root / ".code-forge" / "state.json")
    assert saved.round_history[-1]["clean_credit_action"] == "unavailable"
    assert saved.consecutive_clean_rounds == 2
    assert not _verify(root).passed


def test_unknown_execution_refuses_inherited_credit_with_reason(review_workspace, monkeypatch):
    root = review_workspace
    _machine(root, monkeypatch, rounds=2).run()
    machine = _machine(root, monkeypatch, rounds=1)
    machine._maybe_load_prior_state()
    machine._begin_host_attempt(2)
    machine._start_host_execution()
    path = root / ".code-forge" / "state.json"
    before = path.read_bytes()
    receipts = {p.name: p.read_bytes() for p in _receipts(root)}
    dispatch = []
    resumed = _machine(root, monkeypatch, rounds=1, payload=lambda *_: dispatch.append(True))
    assert resumed.run() == Verdict.FAIL
    assert not dispatch
    assert (
        "unfinished host execution cannot prove absence of reset observations"
        in resumed._state.infra_errors[-1]
    )
    assert path.read_bytes() == before
    assert {p.name: p.read_bytes() for p in _receipts(root)} == receipts
    assert not _verify(root).passed


@pytest.mark.parametrize(
    "reporting_error", [OSError("reporting"), SystemExit(73), _OwnedCancellation("reporting")]
)
def test_cancellation_reporting_failure_keeps_primary(review_workspace, monkeypatch, reporting_error):
    import logging

    class BrokenReporter(logging.Handler):
        def emit(self, record):
            if "host attempt save failed after" in record.getMessage():
                raise reporting_error

    logger = logging.getLogger("code_forge")
    handler = BrokenReporter()
    logger.addHandler(handler)
    try:
        test_cancellation_cleanup_failure_preserves_primary_and_durable_refusal(
            review_workspace, monkeypatch, SystemExit(74)
        )
    finally:
        logger.removeHandler(handler)


@pytest.mark.parametrize("acquisition", [False, True])
@pytest.mark.parametrize("cancellation", [False, True])
def test_cancellation_keeps_post_merge_execution_reset(
    review_workspace, monkeypatch, acquisition, cancellation
):
    import signal

    root = review_workspace
    seed = _machine(root, monkeypatch, rounds=2)
    seed._state.env_manifest = {"tier": "declared"}
    seed.run()
    before = {p.name: p.read_bytes() for p in _receipts(root)}

    def payload(active, prompt):
        if acquisition and "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("owned acquisition failure")
        return VALID

    observed = []

    def hook(index):
        saved = load_state(root / ".code-forge" / "state.json")
        observed.append(saved)
        assert saved.exec_evidence["status"] == "fail_before"
        assert saved.exec_evidence["exit_code"] == 9
        assert any(f.source == "EXEC" and f.disposition == Disposition.CONFIRMED for f in saved.findings)
        if cancellation:
            signal.raise_signal(signal.SIGINT)

    machine = _machine(root, monkeypatch, rounds=1, payload=payload, hook=hook)
    machine.exec_falsify = True
    machine.exec_falsify_command = ["/usr/bin/python3", "-B", "-c", "raise SystemExit(9)"]
    machine.exec_falsify_timeout = 5
    if cancellation:
        with pytest.raises(KeyboardInterrupt):
            machine.run()
    else:
        assert machine.run() != Verdict.PASS
    assert len(observed) == 1
    state = load_state(root / ".code-forge" / "state.json")
    assert state.consecutive_clean_rounds == 0
    assert state.earned_clean_window["cycles"] == []
    assert state.round_history[-1]["clean_credit_action"] == "reset"
    assert state.round_history[-1]["reset_observed"] is True
    assert state.round_history[-1]["dispositions"]["exec-falsify-fail_before"] == "CONFIRMED"
    assert any(f.source == "EXEC" and f.disposition == Disposition.CONFIRMED for f in state.findings)
    assert {n: (root / ".code-forge" / "receipts" / n).read_bytes() for n in before} == before
    resumed = _machine(root, monkeypatch, rounds=1)
    resumed.exec_falsify = True
    resumed.exec_falsify_command = ["/usr/bin/python3", "-B", "-c", "raise SystemExit(0)"]
    resumed.exec_falsify_timeout = 5
    assert resumed.run() != Verdict.PASS
    assert resumed._state.consecutive_clean_rounds == 1
    public = verify.run_verify(root, SOURCE_HASH, verify.parse_diff_files(DIFF), diff_text=DIFF)
    assert not public.passed


def test_partial_next_round_excludes_prior_aggregate_and_keeps_current_reset(
    review_workspace, monkeypatch
):
    root = review_workspace

    def payload(active, prompt):
        if active._state.round == 2 and "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("owned acquisition failure")
        return VALID

    machine = _machine(root, monkeypatch, rounds=3, payload=payload)

    def l0(*args):
        current = machine._state.round == 2
        if current:
            machine.autofixer = NoChangeAutoFixer()
        fingerprint = "current-reset" if current else "prior-fixed"
        return [
            StateFinding(
                fingerprint, fingerprint, "L0", Disposition.CONFIRMED, "control.ts", [2, 2], "P1: actual"
            )
        ], []

    primary = _OwnedCancellation("partial next round")

    original_l2 = machine._run_l2_phase

    def l2():
        result = original_l2()
        if machine._state.round == 2:
            assert machine._state.consecutive_clean_rounds == 2
            assert any(f.fingerprint == "prior-fixed" for f in machine._state.findings)
            raise primary
        return result

    machine.l0_runner = l0
    monkeypatch.setattr(machine, "_run_l2_phase", l2)
    with pytest.raises(_OwnedCancellation) as caught:
        machine.run()
    assert caught.value is primary
    state = load_state(root / ".code-forge" / "state.json")
    assert state.consecutive_clean_rounds == 0
    assert state.round_history[-1]["clean_credit_action"] == "reset"
    assert state.round_history[-1]["dispositions"]["current-reset"] == "CONFIRMED"
    assert "prior-fixed" not in state.round_history[-1]["dispositions"]
    assert all(f.fingerprint != "prior-fixed" for f in state.findings)
    assert state.round_history[-1]["phase_status"]["l2"] == "not_run"
    assert _machine(root, monkeypatch, rounds=1).run() != Verdict.PASS
    assert not _verify(root).passed


def test_cancellation_between_aggregate_adoption_and_state_publication_keeps_reset(
    review_workspace, monkeypatch
):
    root = review_workspace
    _machine(root, monkeypatch, rounds=2).run()
    machine = _machine(root, monkeypatch, rounds=1)
    machine.autofixer = NoChangeAutoFixer()
    machine.l0_runner = lambda *args: (
        [
            StateFinding(
                "current-reset",
                "current-reset",
                "L0",
                Disposition.CONFIRMED,
                "control.ts",
                [2, 2],
                "P1: actual",
            )
        ],
        [],
    )
    primary = _OwnedCancellation("aggregate publication")
    original = StateMachine.__setattr__
    fired = []

    def assign(active, name, value):
        original(active, name, value)
        if active is machine and name == "_attempt_findings" and value is not None and not fired:
            fired.append(True)
            assert not active._state.findings
            raise primary

    monkeypatch.setattr(StateMachine, "__setattr__", assign)
    with pytest.raises(_OwnedCancellation) as caught:
        machine.run()
    assert caught.value is primary and fired == [True]
    state = load_state(root / ".code-forge" / "state.json")
    assert state.consecutive_clean_rounds == 0
    assert state.round_history[-1]["reset_observed"] is True
    assert state.round_history[-1]["dispositions"]["current-reset"] == "CONFIRMED"
    assert not _verify(root).passed


@pytest.mark.parametrize("producer", ["fixval", "receipt-diagnostic"])
def test_cancellation_after_finalized_attempt_retains_terminal_additions(
    review_workspace, monkeypatch, producer
):
    root = review_workspace
    _machine(root, monkeypatch, rounds=2).run()
    machine = _machine(root, monkeypatch, rounds=1)
    primary = _OwnedCancellation("terminal addition")
    fired = []
    if producer == "fixval":
        original = machine._persist_state

        def persist():
            if any(f.source == "FIXVAL" for f in machine._state.findings) and not fired:
                fired.append(True)
                raise primary
            return original()

        monkeypatch.setattr(machine, "_persist_state", persist)
    else:

        def tamper(index):
            path = root / ".code-forge" / "receipts" / f"receipt-c{index + 1}p1.json"
            raw = json.loads(path.read_text())
            raw["diff_sha256"] = "f" * 64
            path.write_text(json.dumps(raw))

        machine.post_round_hook = tamper
        original = machine._record_receipt_gate_failure

        def diagnostic(error):
            original(error)
            fired.append(True)
            raise primary

        monkeypatch.setattr(machine, "_record_receipt_gate_failure", diagnostic)
    with pytest.raises(_OwnedCancellation) as caught:
        machine.run()
    assert caught.value is primary and fired == [True]
    state = load_state(root / ".code-forge" / "state.json")
    assert state.consecutive_clean_rounds == (3 if producer == "fixval" else 2)
    assert state.round_history[-1]["clean_credit_action"] == (
        "earned" if producer == "fixval" else "unavailable"
    )
    assert any(
        f.id == ("FIXVAL_SKIPPED" if producer == "fixval" else "RECEIPT_INVALID") for f in state.findings
    )
    if producer == "receipt-diagnostic":
        assert not _verify(root).passed


def _interrupt_cached_phase(machine, phase, primary=None, *, marker_capture=False):
    import signal

    fired = []
    previous = sys.gettrace()

    def trace(frame, event, arg):
        if event != "line" or fired or frame.f_locals.get("self") is not machine:
            return trace
        if marker_capture:
            ready = (
                frame.f_code.co_name == "_capture_acquisition_markers"
                and bool(machine._acquisition_markers)
                and machine._phase_status[phase] == "not_run"
            )
        else:
            ready = (
                frame.f_code.co_name == "_observe_phase"
                and frame.f_locals.get("name") == phase
                and phase in machine._phase_findings
                and machine._phase_status[phase] == "not_run"
            )
        if ready:
            fired.append((frame.f_lineno, list(machine._phase_findings[phase])))
            if primary is not None:
                raise primary
            signal.raise_signal(signal.SIGINT)
        return trace

    class Tracing:
        def __enter__(self):
            sys.settrace(trace)
            return fired

        def __exit__(self, *args):
            sys.settrace(previous)

    return Tracing()


@pytest.mark.parametrize("phase", ROUND_PHASES)
@pytest.mark.parametrize("product", [False, True])
@pytest.mark.parametrize("failure", ["sigint", "system-exit", "ordinary"])
def test_cached_phase_observation_survives_status_publication_failure(
    review_workspace, monkeypatch, phase, product, failure
):
    root = review_workspace
    _machine(root, monkeypatch, rounds=2).run()
    before = {p.name: p.read_bytes() for p in _receipts(root)}
    machine = _machine(root, monkeypatch, rounds=1)
    machine.autofixer = NoChangeAutoFixer()
    finding = StateFinding(
        "observed-product",
        "observed-product",
        phase.upper(),
        Disposition.CONFIRMED,
        "control.ts",
        [2, 2],
        "P1: actual product",
    )
    findings = [finding] if product else []
    if phase == "l1":
        original = machine.l1_provider

        def provider():
            acquired, excerpts, usage, duration = original()
            return acquired + findings, excerpts, usage, duration

        machine.l1_provider = provider
    else:
        method = {
            "l0": "_run_l0_phase",
            "rulepack": "_run_rulepack_blocking_phase",
            "l2": "_run_l2_phase",
            "e2e": "_run_e2e_phase",
            "coverage": "_run_coverage_phase",
        }[phase]
        monkeypatch.setattr(machine, method, lambda: findings)
    primary = {"sigint": None, "system-exit": SystemExit(73), "ordinary": ValueError("owned")}[failure]
    with _interrupt_cached_phase(machine, phase, primary) as fired:
        with pytest.raises(KeyboardInterrupt if primary is None else type(primary)) as caught:
            machine.run()
    assert len(fired) == 1 and fired[0][1] == findings
    if primary is not None:
        assert caught.value is primary
    state = load_state(root / ".code-forge" / "state.json")
    row = state.round_history[-1]
    assert row["phase_status"][phase] == "returned"
    assert row["clean_credit_action"] == ("reset" if product else "unavailable")
    assert row["reset_observed"] is product
    assert state.consecutive_clean_rounds == (0 if product else 2)
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == (
        [] if product else [1, 2]
    )
    assert row.get("dispositions", {}).get("observed-product") == ("CONFIRMED" if product else None)
    assert {n: (root / ".code-forge" / "receipts" / n).read_bytes() for n in before} == before
    assert not _verify(root).passed
    resumed = _machine(root, monkeypatch, rounds=1)
    assert resumed.run() != Verdict.PASS
    if product:
        assert resumed._state.consecutive_clean_rounds == 1
    assert not _verify(root).passed


@pytest.mark.parametrize("marker_capture", [False, True])
@pytest.mark.parametrize("product", [False, True])
@pytest.mark.parametrize("kind", ["invoke", "invoke-timeout", "spawn"])
def test_cached_l1_acquisition_markers_survive_partial_capture(
    review_workspace, monkeypatch, marker_capture, product, kind
):
    root = review_workspace
    _machine(root, monkeypatch, rounds=2).run()

    def payload(active, prompt):
        if "adversarial" not in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError(
                "owned acquisition failure", is_timeout=kind == "invoke-timeout"
            )
        return VALID

    machine = _machine(root, monkeypatch, rounds=1, payload=payload)
    machine.autofixer = NoChangeAutoFixer()
    if product:
        machine.l0_runner = lambda *args: (
            [
                StateFinding(
                    "actual-reset",
                    "actual-reset",
                    "L0",
                    Disposition.CONFIRMED,
                    "control.ts",
                    [2, 2],
                    "P1: actual product",
                )
            ],
            [],
        )
    if kind == "spawn":
        from code_forge.outlet_c import _run_chunk

        def spawn(pass_name, diff):
            if pass_name != "adversarial":
                raise OSError("owned spawn failure")
            return json.dumps(VALID)

        def provider():
            return _run_chunk(DIFF, spawn, ("qodo", "expert", "adversarial"))

        machine.l1_provider = provider
    with _interrupt_cached_phase(machine, "l1", marker_capture=marker_capture) as fired:
        with pytest.raises(KeyboardInterrupt):
            machine.run()
    assert len(fired) == 1
    state = load_state(root / ".code-forge" / "state.json")
    row = state.round_history[-1]
    assert row["phase_status"]["l1"] == "returned"
    assert len(row["acquisition_failures"]) == 2
    assert {item["pass_name"] for item in row["acquisition_failures"]} == {"qodo", "expert"}
    assert {item["outcome"] for item in row["acquisition_failures"]} == {
        "error" if kind == "invoke" else "timeout"
    }
    assert all(
        any(marker is finding for finding in fired[0][1]) for marker in machine._acquisition_markers
    )
    assert row["clean_credit_action"] == ("reset" if product else "interrupted")
    assert row["reset_observed"] is product
    assert state.consecutive_clean_rounds == (0 if product else 2)
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == (
        [] if product else [1, 2]
    )
    assert not _verify(root).passed
    resumed = _machine(root, monkeypatch, rounds=1)
    assert (resumed.run() == Verdict.PASS) is (not product)
    assert resumed._state.consecutive_clean_rounds == (1 if product else 3)
    assert _verify(root).passed is (not product)


def test_new_reservation_clears_cached_observations_before_producer_failure(
    review_workspace, monkeypatch
):
    root = review_workspace
    _machine(root, monkeypatch, rounds=2).run()
    machine = _machine(root, monkeypatch, rounds=1)
    stale = StateFinding(
        "stale-product",
        "stale-product",
        "L0",
        Disposition.CONFIRMED,
        "control.ts",
        [2, 2],
        "P1: stale product",
    )
    machine._phase_findings = {phase: [stale] if phase == "l0" else [] for phase in ROUND_PHASES}
    primary = ValueError("producer did not return")

    def operation():
        assert machine._phase_findings == {}
        raise primary

    monkeypatch.setattr(machine, "_run_l0_phase", operation)
    with pytest.raises(ValueError) as caught:
        machine.run()
    assert caught.value is primary
    state = load_state(root / ".code-forge" / "state.json")
    row = state.round_history[-1]
    assert machine._phase_findings == {}
    assert row["phase_status"]["l0"] == "failed"
    assert row["clean_credit_action"] == "unavailable" and not row["reset_observed"]
    assert state.consecutive_clean_rounds == 2
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == [1, 2]
    assert all(f.fingerprint != "stale-product" for f in state.findings)
    assert not _verify(root).passed


def test_repeated_l1_marker_capture_failure_keeps_primary_and_durable_refusal(
    review_workspace, monkeypatch
):
    root = review_workspace
    _machine(root, monkeypatch, rounds=2).run()
    machine = _machine(root, monkeypatch, rounds=1)
    primary = ValueError("first capture")
    secondary = SystemExit(74)
    calls = []

    def capture(findings):
        calls.append(findings)
        raise primary if len(calls) == 1 else secondary

    monkeypatch.setattr(machine, "_capture_acquisition_markers", capture)
    with pytest.raises(ValueError) as caught:
        machine.run()
    assert caught.value is primary and len(calls) == 2
    assert calls[0] is calls[1] is machine._phase_findings["l1"]
    state = load_state(root / ".code-forge" / "state.json")
    assert state.round_history[-1]["clean_credit_action"] == "unavailable"
    assert not state.round_history[-1]["reset_observed"]
    assert not _verify(root).passed
    assert _machine(root, monkeypatch, rounds=1).run() == Verdict.FAIL


@pytest.mark.parametrize(
    "boundary",
    ["before-marker", "after-marker", "before-operation", "returned-local", "producer-raises"],
)
def test_execution_start_authority_survives_unknown_phase_cancellation(
    review_workspace, monkeypatch, boundary
):
    import signal

    root = review_workspace
    _machine(root, monkeypatch, rounds=2).run()
    before = {p.name: p.read_bytes() for p in _receipts(root)}
    path = root / ".code-forge" / "state.json"
    machine = _machine(root, monkeypatch, rounds=1)
    primary = ValueError("producer did not return") if boundary == "producer-raises" else None
    calls = []
    fired = []
    product = StateFinding(
        "returned-local",
        "returned-local",
        "L0",
        Disposition.CONFIRMED,
        "control.ts",
        [2, 2],
        "P1: actual product",
    )

    def operation():
        calls.append(True)
        if primary is not None:
            raise primary
        return [product]

    monkeypatch.setattr(machine, "_run_l0_phase", operation)
    original = machine._start_host_execution
    if boundary in ("before-marker", "after-marker"):

        def start():
            if boundary == "after-marker":
                original()
            disk = load_state(path)
            assert disk.round_history[-1]["clean_credit_action"] == (
                "pending" if boundary == "before-marker" else "unavailable"
            )
            fired.append(True)
            signal.raise_signal(signal.SIGINT)

        monkeypatch.setattr(machine, "_start_host_execution", start)
    source, first_line = inspect.getsourcelines(machine._observe_phase)
    needle = "result = operation()" if boundary == "before-operation" else "if self._host_attempt_round"
    line = next(first_line + i for i, text in enumerate(source) if needle in text)

    def trace(frame, event, arg):
        if (
            not fired
            and event == "line"
            and frame.f_code.co_name == "_observe_phase"
            and frame.f_locals.get("self") is machine
            and frame.f_locals.get("name") == "l0"
            and frame.f_lineno == line
        ):
            assert machine._phase_findings == {}
            if boundary == "returned-local":
                assert frame.f_locals["result"] == [product] and calls == [True]
            else:
                assert calls == []
            fired.append(True)
            signal.raise_signal(signal.SIGINT)
        return trace

    previous = sys.gettrace()
    if boundary in ("before-operation", "returned-local"):
        sys.settrace(trace)
    try:
        with pytest.raises(KeyboardInterrupt if primary is None else ValueError) as caught:
            machine.run()
    finally:
        sys.settrace(previous)
    if primary is not None:
        assert caught.value is primary and calls == [True]
    else:
        assert fired == [True]
    state = load_state(path)
    row = state.round_history[-1]
    pending = boundary == "before-marker"
    assert row["clean_credit_action"] == ("pending" if pending else "unavailable")
    assert row["phase_status"]["l0"] == ("failed" if primary is not None else "not_run")
    assert row["reset_observed"] is False
    assert state.consecutive_clean_rounds == 2
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == [1, 2]
    assert {p.name: p.read_bytes() for p in _receipts(root)} == before
    assert not _verify(root).passed
    saved = path.read_bytes()
    resumed = _machine(root, monkeypatch, rounds=1)
    assert (resumed.run() == Verdict.PASS) is pending
    assert _verify(root).passed is pending
    if not pending:
        assert path.read_bytes() == saved
        assert {p.name: p.read_bytes() for p in _receipts(root)} == before
    if boundary == "returned-local":
        archive, inventory, guards = _explicit_archive(root)
        assert (archive / "state.json").read_bytes() == saved
        assert _machine(root, monkeypatch, rounds=3).run() == Verdict.PASS
        assert _verify(root).passed
        fresh = load_state(path)
        assert [entry["cycle"] for entry in fresh.earned_clean_window["cycles"]] == [1, 2, 3]
        assert {name: _archive_inventory(archive / name) for name in inventory} == inventory
        assert {name: _archive_inventory(Path(name)) for name in guards} == guards


@pytest.mark.parametrize("damage", ["mismatched-round", "already-started"])
def test_host_execution_requires_the_current_pending_reservation(review_workspace, monkeypatch, damage):
    root = review_workspace
    _machine(root, monkeypatch, rounds=2).run()
    machine = _machine(root, monkeypatch, rounds=1)
    machine._maybe_load_prior_state()
    machine._begin_host_attempt(machine._continuation_round_index())
    if damage == "mismatched-round":
        machine._host_attempt_round += 1
    else:
        machine._start_host_execution()
    path = root / ".code-forge" / "state.json"
    before = path.read_bytes()
    with pytest.raises(CorruptedStateError, match="host execution lacks a pending reservation"):
        machine._start_host_execution()
    assert path.read_bytes() == before
    assert machine._phase_findings == {}
    assert machine._state.consecutive_clean_rounds == 2
