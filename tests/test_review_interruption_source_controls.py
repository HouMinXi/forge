"""Offline controls for review interruptions and rejected evidence.

Source base: 295885b3b484e15082140e65b1afaa5959bfd2be. This module is
portable control SOURCE, not evidence that it has been collected or executed.
Execution and native qualification require their separate runtime reports.

The reviewer transport sequence is constructed at llm_invoke._invoke_api;
exception fields match current emitting sites, but their transport constructors
are NOT exercised. L0/L2/E2E command runners are explicit empty-result seams.
Ledger-root discovery is an explicit fixture seam; fixture ledger consumption is
real. No provider, subprocess, native mutation, or full Forge acceptance is claimed.
Factory, exact _L1Call identity, machine accounting, receipt writing/verification,
state serialization and cli._emit_ci_output are real. Receipt proof is produced
and checked by those consumers; no receipt or verifier success is fabricated.

Current outcomes deliberately differ: invoke failures preserve prior credit but
remain product-visible; schema/coverage preserve prior earned (preserved). Pure typed excerpt rejection
preserves an individually reverified prefix while earning no new cycle; literal
damage and unknown execution still refuse automatic inherited credit.
"""

import contextlib
import copy
import dataclasses
import datetime
import enum
import hashlib
import io
import json
import os
import sys
import threading
import types
from collections import Counter
from pathlib import Path

import pytest

import code_forge.machine as machine_mod
from code_forge import cli, llm_invoke, verify
from code_forge.autofix import NoChangeAutoFixer
from code_forge.backend import BackendConfig
from code_forge.baseline import ResolvedReview
from code_forge.errors import CorruptedStateError
from code_forge.factories import build_l1_provider
from code_forge.falsify import StubFalsifier
from code_forge.falsify_real import RealFalsifier
from code_forge.machine import StateMachine, TimeoutBreaker
from code_forge.source import compute_source_hash
from code_forge.state import Mode, Verdict, load_state, save_state


SOURCE_PIN = "295885b3b484e15082140e65b1afaa5959bfd2be"
ROLES = ("qodo", "expert", "adversarial")
CONTENT = {
    "control.ts": "const context = 1;\nconst value = 2;\nconst end = 3;\n",
    "other.ts": "const context = 4;\nconst value = 5;\nconst end = 6;\n",
}
DIFF = (
    "diff --git a/control.ts b/control.ts\n--- a/control.ts\n+++ b/control.ts\n"
    "@@ -1,2 +1,3 @@\n const context = 1;\n+const value = 2;\n const end = 3;\n"
    "diff --git a/other.ts b/other.ts\n--- a/other.ts\n+++ b/other.ts\n"
    "@@ -1,2 +1,3 @@\n const context = 4;\n+const value = 5;\n const end = 6;\n"
)
CHANGED_DIFF = DIFF.replace("const value = 2;", "const value = 20;")
# C3 v3 T3: the same review plus an excerpt-exempt pure-deletion section.
DELETED_CONTENT = {"legacy.ts": "const keep = 7;\n"}
DELETION_DIFF = DIFF + (
    "diff --git a/legacy.ts b/legacy.ts\n--- a/legacy.ts\n+++ b/legacy.ts\n"
    "@@ -1,3 +1,1 @@\n const keep = 7;\n-const drop = 8;\n-const gone = 9;\n"
)

# Acquisition: case, observed pass status, current kind/timeout/exit.
ACQUISITION_CASES = [
    ("no-json", "error", "no_json", False, 0),
    ("timeout-empty-kind", "timeout", "", True, -1),
    ("connect-timeout", "timeout", "conn", True, -1),
    ("credentials", "error", "credentials", False, -1),
    ("http-401", "error", "", False, 401),
    ("http-429", "error", "", False, 429),
    ("http-503", "error", "", False, 503),
    ("capacity", "error", "truncated", False, -1),
    ("generic-host-error", "error", None, False, None),
]
# Evidence: case, pass status, action, numeric count, product count, verdict, marker.
EVIDENCE_CASES = [
    ("schema", "schema_fail", "preserved", 2, 2, "FAIL", "schema-fail"),
    ("coverage", "incomplete", "preserved", 2, 1, "ESCALATED", "incomplete-coverage"),
    ("missing-excerpts", "incomplete", "evidence_rejected", 2, 1, "FAIL", None),
    ("excerpt-line-count", "incomplete", "evidence_rejected", 2, 1, "FAIL", None),
    ("literal", "schema_fail", "unavailable", 2, 1, "FAIL", None),
]


class _ForbiddenExternalOperation(BaseException):
    """Production catches Exception; a forbidden operation must escape that fold."""


@pytest.fixture
def offline_workspace(tmp_path, monkeypatch):
    """No command is allowed; the request-count and forbidden-call logs are oracles."""
    active = [True]
    forbidden = []

    def audit(event, args):
        if active[0] and (
            event.startswith("socket.")
            or event.startswith(
                ("subprocess.Popen", "os.system", "os.fork", "os.posix_spawn", "os.exec")
            )
        ):
            forbidden.append(event)
            raise _ForbiddenExternalOperation("unexpected external operation: " + event)

    sys.addaudithook(audit)
    try:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("FORGE_FALSIFY_WORKERS", "1")

        def fixture_ledger_root(cwd):
            assert Path(cwd) == tmp_path
            return tmp_path

        monkeypatch.setattr("code_forge.machine.resolve_ledger_root", fixture_ledger_root)
        monkeypatch.setattr("code_forge.ledger.resolve_ledger_root", fixture_ledger_root)
        for name, content in CONTENT.items():
            (tmp_path / name).write_text(content, encoding="utf-8")
        config = tmp_path / ".code-forge"
        config.mkdir()
        # The declared command is never executed: l2_runner below is a seam,
        # and these source filenames are not FIXVAL test-file candidates.
        (config / "gate.yaml").write_text(
            "verify:\n  required_cycles: 3\n"
            "test:\n  command: [python, -m, pytest, -q]\n  timeout_seconds: 1\n",
            encoding="utf-8",
        )
        yield tmp_path
    finally:
        active[0] = False
        assert forbidden == [], "external operation attempted, even if caught: " + repr(forbidden)


def _healthy(diff=DIFF):
    content = dict(CONTENT)
    if diff == CHANGED_DIFF:
        content["control.ts"] = content["control.ts"].replace("const value = 2;", "const value = 20;")
    elif diff == DELETION_DIFF:
        content.update(DELETED_CONTENT)
    else:
        assert diff == DIFF, "fixture supports only its explicit source versions"
    return {
        "findings": [],
        "code_excerpts": [
            {"file": name, "start_line": 1, "end_line": text.count("\n"), "content": text}
            for name, text in content.items()
        ],
    }


def _reviewer_role(prompt):
    # This fixture selects constructed replies; this is NOT production trust
    # derived from prompt text. The production factory owns role attribution.
    role_sentence = prompt.rsplit("\nYou are a ", 1)[-1]
    prefixes = ("structural code reviewer:", "senior engineer:", "adversarial QE:")
    matches = [
        role for role, prefix in zip(ROLES, prefixes, strict=True) if role_sentence.startswith(prefix)
    ]
    assert len(matches) == 1, "unexpected reviewer prompt at the explicit transport seam"
    return matches[0]


def _acquisition_error(case):
    if case == "generic-host-error":
        return RuntimeError("constructed unexpected host failure")
    if case == "no-json":
        return llm_invoke.InvalidJSONResponseError(
            "constructed final JSON rejection",
            raw_response="constructed non-JSON response",
            kind="no_json",
            exit_code=0,
            retryable=False,
        )
    if case == "timeout-empty-kind":
        return llm_invoke.LLMInvokeError("constructed body timeout", is_timeout=True, retryable=False)
    if case == "connect-timeout":
        return llm_invoke.LLMInvokeError("constructed connect timeout", kind="conn", is_timeout=True)
    if case == "credentials":
        return llm_invoke.LLMInvokeError(
            "constructed missing credentials", kind="credentials", retryable=False
        )
    if case.startswith("http-"):
        code = int(case.split("-", 1)[1])
        return llm_invoke.LLMInvokeError(
            "constructed HTTP failure", exit_code=code, retryable=code in (429, 503)
        )
    if case == "capacity":
        return llm_invoke.LLMInvokeError(
            "constructed capacity failure", kind="truncated", retryable=False
        )
    raise AssertionError("unknown constructed acquisition case: " + case)


def _candidate_payload(payload, severity="P1"):
    payload["findings"] = [
        {
            "file": "control.ts",
            "line": 2,
            "severity": severity,
            "description": "constructed product defect",
            "excerpt": "const value = 2;",
        }
    ]
    return payload


def _apply_reviewer_fault(body, fault):
    """Mutate one constructed reviewer reply into the named receipt fault."""
    if fault == "schema":
        body["findings"] = "not an array"
    elif fault == "coverage":
        body["code_excerpts"] = body["code_excerpts"][:1]
    elif fault == "missing-excerpts":
        body["code_excerpts"] = []
    elif fault == "excerpt-line-count":
        body["code_excerpts"][0]["content"] = "const value = 2;\n"
    elif fault == "schema-findings":
        # C3: schema-invalid (severity) but the findings list and the excerpts
        # are intact, so the factory yields schema-fail + one UNTRUSTED companion.
        body["findings"] = [
            {
                "file": "control.ts",
                "line": 2,
                "severity": "P9",
                "description": "constructed schema-invalid candidate",
                "excerpt": "const value = 2;",
            }
        ]
    elif fault == "schema-findings-two":
        # C3 T6: one schema-invalid response, two candidates -> two companions.
        body = _apply_reviewer_fault(body, "schema-findings")
        body["findings"].append(
            {
                "file": "other.ts",
                "line": 2,
                "severity": "P9",
                "description": "second schema-invalid candidate",
                "excerpt": "const value = 5;",
            }
        )
    elif fault == "literal":
        body["code_excerpts"][0]["content"] = CONTENT["control.ts"].replace("value = 2", "value = 200")
    elif fault == "inadmissible-dict":
        # FTD-05 (L-A real path): a schema-invalid dict the audit writer cannot
        # encode (a tuple is not built-in JSON data), so the factory records
        # schema-fail and marks the pass "audit payload unavailable".
        body["findings"] = "not an array"
        body["constructed_unencodable"] = ("tuple",)
    else:
        raise AssertionError("unknown constructed reviewer fault: " + str(fault))
    return body


def _new_machine(
    root,
    monkeypatch,
    *,
    fault=None,
    rounds=1,
    mode=Mode.LOCAL,
    diff=DIFF,
    mixed_product=False,
    falsifier_reply=None,
    threshold=3,
    qodo_fault=None,
    product_severity="P1",
    fault_rounds=None,
):
    """Keep production _L1Call intact; inject candidates through reviewer JSON."""
    calls = []
    lock = threading.Lock()
    holder = {}

    def transport(prompt, *args, **kwargs):
        role = _reviewer_role(prompt)
        phase = (
            "excerpt-repair"
            if prompt.startswith(
                "The previous JSON is a review response without required code_excerpts."
            )
            else "review"
        )
        with lock:
            calls.append((holder["machine"]._state.round + 1, role, phase))
        body = _healthy(diff)
        if role == "qodo" and qodo_fault is not None:
            return llm_invoke.LLMResult(_apply_reviewer_fault(body, qodo_fault))
        if role == "qodo" and (mixed_product or falsifier_reply is not None):
            return llm_invoke.LLMResult(_candidate_payload(body, product_severity))
        cycle = holder["machine"]._state.round + 1
        if (
            role != "adversarial"
            or fault is None
            or (fault_rounds is not None and cycle not in fault_rounds)
        ):
            return llm_invoke.LLMResult(body)
        if fault in {row[0] for row in ACQUISITION_CASES}:
            raise _acquisition_error(fault)
        return llm_invoke.LLMResult(_apply_reviewer_fault(body, fault))

    monkeypatch.setattr(llm_invoke, "_invoke_api", transport)
    backend = BackendConfig(
        name="constructed-offline",
        type="api",
        format="openai",
        model="offline",
        base_url="http://127.0.0.1:1",
        timeout_s=1,
    )
    # Supplied diff identity is sufficient for these receipt controls. Leave
    # base/head/target absent: this is not #67 native WORKING-source authority.
    # Its real capture guard returns None before any Git command in this seam.
    resolved = ResolvedReview([Path(name) for name in CONTENT], None, diff, "git")
    provider = build_l1_provider("auto", resolved, backend=backend, max_attempts=1)
    falsifier = StubFalsifier()
    if mixed_product or falsifier_reply is not None:
        falsifier = RealFalsifier(backend=backend, diff_text=diff)

        def falsify_transport(*args, **kwargs):
            reply = falsifier_reply
            if mixed_product:
                reply = {"verdict": "CONFIRMED", "reasoning": "constructed diff-local defect"}
            if isinstance(reply, Exception):
                raise reply
            return llm_invoke.LLMResult(copy.deepcopy(reply))

        monkeypatch.setattr("code_forge.falsify_real.llm_invoke", falsify_transport)
    machine = StateMachine(
        mode=mode,
        falsifier=falsifier,
        autofixer=NoChangeAutoFixer(),
        revert_fn=lambda finding: None,
        resolved_review=resolved,
        source_hash=compute_source_hash(git_diff=diff),
        baseline_spec_repr="issue70 constructed offline diff",
        cwd=root,
        registry={},
        l0_runner=lambda *args: ([], []),
        l1_provider=provider,
        l2_runner=lambda *args, **kwargs: ([], []),
        e2e_runner=lambda *args: ([], []),
        max_total_rounds=rounds,
        clean_round_threshold=threshold,
    )
    machine._state.env_manifest = {}  # explicit fixture manifest, no environment discovery
    holder["machine"] = machine
    return machine, calls


def _state(root):
    path = root / ".code-forge" / "state.json"
    loaded = load_state(path)
    assert loaded is not None
    raw = json.loads(path.read_text(encoding="utf-8"))
    # Exercise a genuine second serialization/load, without overwriting source
    # state or using that copy as authority for the machine under test.
    roundtrip = root / ".code-forge" / "issue70-roundtrip.json"
    save_state(loaded, roundtrip)
    assert json.loads(roundtrip.read_text(encoding="utf-8")) == raw
    again = load_state(roundtrip)
    assert again.consecutive_clean_rounds == loaded.consecutive_clean_rounds
    assert again.earned_clean_window == loaded.earned_clean_window
    assert again.round_history == loaded.round_history
    return loaded, raw


def _report(root):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        cli._emit_ci_output(root / ".code-forge" / "state.json", {})
    log = json.loads(stdout.getvalue())
    assert log["version"] == "2.1.0" and len(log["runs"]) == 1
    return stderr.getvalue(), log["runs"][0]


def _verify(root, diff=DIFF):
    return verify.run_verify(
        root, compute_source_hash(git_diff=diff), verify.parse_diff_files(diff), diff_text=diff
    )


def _cycle_receipts(root, cycle):
    return [
        json.loads((root / ".code-forge" / "receipts" / f"receipt-c{cycle}p{number}.json").read_text())
        for number in (1, 2, 3)
    ]


def _assert_healthy_siblings(receipts, diff=DIFF, *, product_in_qodo=False):
    expected_excerpts = [
        dict(excerpt, rationale="reviewer-provided") for excerpt in _healthy(diff)["code_excerpts"]
    ]
    for number, receipt in enumerate(receipts[:2], 1):
        assert receipt["pass"] == number and receipt["pass_status"] == "completed"
        assert receipt["diff_sha256"] == compute_source_hash(git_diff=diff)
        assert receipt["code_excerpts"] == expected_excerpts
        if number == 1 and product_in_qodo:
            assert receipt["findings_count"] == len(receipt["findings"]) == 1
            assert receipt["findings"][0]["description"] == "[qodo] constructed product defect"
        else:
            assert receipt["findings_count"] == 0 and receipt["findings"] == []


def _assert_calls(calls, cycles, *, missing_excerpt_repair=False):
    expected = Counter((cycle, role, "review") for cycle in cycles for role in ROLES)
    if missing_excerpt_repair:
        # Real llm_invoke asks once outside max_attempts. The constructed
        # follow-up stays empty; do not skip or fabricate repair success.
        expected[(cycles[-1], "adversarial", "excerpt-repair")] += 1
    assert Counter(calls) == expected


def _earned_prefix(root, monkeypatch, diff=DIFF):
    machine, calls = _new_machine(root, monkeypatch, rounds=2, diff=diff)
    assert machine.run() is Verdict.ESCALATED  # two is below the unchanged floor
    _assert_calls(calls, [1, 2])
    state, _ = _state(root)
    assert state.consecutive_clean_rounds == 2
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == [1, 2]
    assert [row["clean_credit_action"] for row in state.round_history] == ["earned", "earned"]
    original = {}
    for cycle, stored in zip((1, 2), state.earned_clean_window["cycles"], strict=True):
        entry, result = verify._capture_earned_cycle(
            root,
            compute_source_hash(git_diff=diff),
            verify.parse_diff_files(diff),
            cycle=cycle,
            diff_text=diff,
        )
        assert result.passed and result.checks_run == result.checks_passed == 8, result.reason
        assert entry == stored
        assert [receipt["pass_status"] for receipt in _cycle_receipts(root, cycle)] == ["completed"] * 3
        for number in (1, 2, 3):
            path = root / ".code-forge" / "receipts" / f"receipt-c{cycle}p{number}.json"
            original[path] = path.read_bytes()
            assert hashlib.sha256(original[path]).hexdigest() == stored["receipt_sha256"][str(number)]
    assert not _verify(root, diff).passed
    return copy.deepcopy(state.earned_clean_window), original


def _assert_original_receipts(original):
    assert {path: path.read_bytes() for path in original} == original


def _restore_result(root, diff=DIFF):
    # Restoration is the real machine's independently verified proof operation,
    # distinct from public terminal verification and from the stored number.
    loaded = load_state(root / ".code-forge" / "state.json")
    return verify._restore_earned_window(
        loaded,
        root,
        compute_source_hash(git_diff=diff),
        verify.parse_diff_files(diff),
        diff_text=diff,
        reviewed_repositories=None,
    )


def _recover(root, monkeypatch, original, expected_cycles, *, rounds=1, diff=DIFF):
    machine, calls = _new_machine(root, monkeypatch, rounds=rounds, diff=diff)
    assert machine.run() is Verdict.PASS
    _assert_calls(calls, expected_cycles[-rounds:])
    state, _ = _state(root)
    assert state.converged is True
    assert state.consecutive_clean_rounds == 3
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == expected_cycles
    assert _verify(root, diff).passed
    summary, _ = _report(root)
    assert "code-forge: PASS " in summary
    _assert_original_receipts(original)


@pytest.mark.parametrize(
    "case,status,kind,is_timeout,exit_code", ACQUISITION_CASES, ids=[row[0] for row in ACQUISITION_CASES]
)
def test_current_acquisition_preserves_proof_without_earning_or_hiding_generic_failure(
    offline_workspace, monkeypatch, case, status, kind, is_timeout, exit_code
):
    root = offline_workspace
    proof, original = _earned_prefix(root, monkeypatch)
    machine, calls = _new_machine(root, monkeypatch, fault=case)
    assert machine.run() is Verdict.FAIL
    _assert_calls(calls, [3])
    state, raw = _state(root)
    assert state.converged is False and state.consecutive_clean_rounds == 2
    assert state.earned_clean_window == proof
    latest = state.round_history[-1]
    assert latest["clean_credit_action"] == "interrupted" and latest["fixpoint"] == "CLEAN"
    assert latest["reset_observed"] is False and latest["clean_rounds_after"] == 2
    assert latest["acquisition_failures"] == [
        {
            "id": "l1-adversarial-invoke-fail",
            "fingerprint": "invoke-fail-adversarial",
            "pass_name": "adversarial",
            "outcome": status,
        }
    ]
    failure = next(f for f in raw["findings"] if f["id"] == "l1-adversarial-invoke-fail")
    if case == "generic-host-error":
        assert failure["provider_failure"] is None
    else:
        assert failure["provider_failure"]["kind"] == kind
        assert failure["provider_failure"]["exit_code"] == exit_code
    assert failure["is_timeout"] is is_timeout
    receipts = _cycle_receipts(root, 3)
    assert [r["pass_status"] for r in receipts] == ["completed", "completed", status]
    _assert_healthy_siblings(receipts)
    assert receipts[2]["findings_count"] == len(receipts[2]["findings"]) == 1
    assert receipts[2]["findings"][0]["file"] == "<llm-invoke>"
    if case == "no-json":
        artifacts = list((root / ".code-forge" / "receipts" / "attempted").glob("attempted-c3p3-*.json"))
        assert len(artifacts) == 1
        audit = json.loads(artifacts[0].read_text())
        assert audit == {
            "attempted": True,
            "cycle": 3,
            "pass_name": "adversarial",
            "payload": {"raw_response": "constructed non-JSON response", "pass_name": "adversarial"},
        }
    assert _restore_result(root).passed
    assert not _verify(root).passed
    summary, report = _report(root)
    if case == "capacity":
        assert "FAIL findings=0 confirmed=0" in summary
        assert report["results"] == []
        assert {d["diagnostic_kind"] for d in report["properties"]["providerDiagnostics"]} == {
            "provider-capacity",
            "capacity-incomplete",
        }
    else:
        assert failure["diagnostic_kind"] is None
        assert "FAIL findings=2 confirmed=2" in summary
        assert len(report["results"]) == 2
        assert "providerDiagnostics" not in report.get("properties", {})
    assert "passes=2/3" in summary
    _recover(root, monkeypatch, original, [1, 2, 4])


@pytest.mark.parametrize(
    "case,status,action,count,products,verdict,marker",
    EVIDENCE_CASES,
    ids=[row[0] for row in EVIDENCE_CASES],
)
def test_current_schema_coverage_and_evidence_rejection_have_distinct_accounting(
    offline_workspace, monkeypatch, case, status, action, count, products, verdict, marker
):
    root = offline_workspace
    proof, original = _earned_prefix(root, monkeypatch)
    machine, calls = _new_machine(root, monkeypatch, fault=case)
    assert machine.run().value == verdict
    _assert_calls(calls, [3], missing_excerpt_repair=case == "missing-excerpts")
    state, raw = _state(root)
    assert not state.converged and state.consecutive_clean_rounds == count
    latest = state.round_history[-1]
    assert latest["clean_credit_action"] == action and latest["acquisition_failures"] == []
    assert latest["reset_observed"] is (action in ("reset", "preserved"))
    assert latest["clean_rounds_after"] == count
    assert state.earned_clean_window["cycles"] == ([] if action == "reset" else proof["cycles"])
    receipts = _cycle_receipts(root, 3)
    assert [r["pass_status"] for r in receipts] == ["completed", "completed", status]
    _assert_healthy_siblings(receipts)
    if marker is not None:
        assert any(f["id"] == f"l1-adversarial-{marker}" for f in raw["findings"])
    else:
        assert [f["id"] for f in raw["findings"]] == ["RECEIPT_INVALID"]
    summary, report = _report(root)
    assert f"{verdict} findings={products} confirmed={products}" in summary
    assert len(report["results"]) == products
    assert "providerDiagnostics" not in report.get("properties", {})
    assert not _verify(root).passed
    if action == "reset":
        assert _restore_result(root).passed  # valid empty proof, not old credit
        _recover(root, monkeypatch, original, [4, 5, 6], rounds=3)
    elif action in ("evidence_rejected", "preserved"):
        if action == "evidence_rejected":
            assert latest["evidence_rejections"]
        else:
            assert "evidence_rejections" not in latest
            assert latest["fixpoint"] in ("RESET", "CYCLE_RESTART")
            # M3: the (iii) key is checked against the real factory-built finding.
            produced = [f for f in machine._state.findings if f.id == f"l1-adversarial-{marker}"]
            assert len(produced) == 1
            assert machine._is_schema_or_incomplete_coverage_finding(produced[0]) is True
        assert _restore_result(root).passed
        _recover(root, monkeypatch, original, [1, 2, 4])
    else:
        result = _restore_result(root)
        assert not result.passed and "unavailable host attempt" in result.reason
        before = (root / ".code-forge" / "state.json").read_bytes()
        recovery, recovery_calls = _new_machine(root, monkeypatch)
        assert recovery.run() is Verdict.FAIL
        assert recovery_calls == []  # no blind healthy retry after unproved execution
        assert "unavailable host attempt" in recovery._state.infra_errors[-1]
        assert (root / ".code-forge" / "state.json").read_bytes() == before
        assert len(list((root / ".code-forge" / "receipts").glob("receipt-*.json"))) == 9
    _assert_original_receipts(original)


def test_current_mixed_product_and_acquisition_resets_prior_window(offline_workspace, monkeypatch):
    root = offline_workspace
    _, original = _earned_prefix(root, monkeypatch)
    machine, calls = _new_machine(root, monkeypatch, fault="no-json", mixed_product=True)
    assert machine.run() is Verdict.ESCALATED
    _assert_calls(calls, [3])
    state, raw = _state(root)
    assert state.consecutive_clean_rounds == 0 and state.earned_clean_window["cycles"] == []
    assert state.round_history[-1]["clean_credit_action"] == "reset"
    assert state.round_history[-1]["reset_observed"] is True
    assert any(f["source"] == "L1" and f["disposition"] == "CONFIRMED" for f in raw["findings"])
    assert any(f["id"] == "l1-adversarial-invoke-fail" for f in raw["findings"])
    receipts = _cycle_receipts(root, 3)
    assert [r["pass_status"] for r in receipts] == ["completed", "completed", "error"]
    _assert_healthy_siblings(receipts, product_in_qodo=True)
    assert receipts[0]["findings"][0]["disposition"] == "CONFIRMED"
    assert receipts[0]["findings"][0]["basis"]["authority"] != "infra-unavailable"
    assert receipts[2]["findings"][0]["file"] == "<llm-invoke>"
    assert receipts[2]["findings"][0]["basis"]["authority"] == "infra-unavailable"
    summary, report = _report(root)
    assert "ESCALATED findings=2 confirmed=2" in summary and len(report["results"]) == 2
    assert not _verify(root).passed
    _recover(root, monkeypatch, original, [4, 5, 6], rounds=3)


def _assert_preserved(root, proof, *, fixpoint, diff=DIFF):
    state, _ = _state(root)
    latest = state.round_history[-1]
    assert latest["clean_credit_action"] == "preserved" and latest["fixpoint"] == fixpoint
    assert latest["reset_observed"] is True and latest["clean_rounds_after"] == 2
    assert state.consecutive_clean_rounds == 2 and state.earned_clean_window == proof
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == [1, 2]
    assert _restore_result(root, diff).passed and not _verify(root, diff).passed
    return state


def _assert_wiped(root, *, phases=None, fixpoint="RESET"):
    state, _ = _state(root)
    latest = state.round_history[-1]
    assert latest["clean_credit_action"] == "reset" and latest["fixpoint"] == fixpoint
    assert latest["clean_rounds_after"] == 0
    assert state.consecutive_clean_rounds == 0 and state.earned_clean_window["cycles"] == []
    if phases is not None:
        assert latest["phase_status"] == phases
    # Restore sees a valid empty proof: no inherited credit survives.
    assert _restore_result(root).passed and not _verify(root).passed
    return state


def test_h1_schema_fault_then_l2_crash_never_preserves(offline_workspace, monkeypatch):
    """H1: a failed attempt with unreturned phases takes the M6 reset."""
    root = offline_workspace
    _, original = _earned_prefix(root, monkeypatch)
    machine, calls = _new_machine(root, monkeypatch, fault="schema")

    def crash(*_args, **_kwargs):
        raise RuntimeError("constructed L2 crash")

    monkeypatch.setattr(machine, "_run_l2_phase", crash)
    with pytest.raises(RuntimeError, match="constructed L2 crash"):
        machine.run()
    _assert_calls(calls, [3])
    _assert_wiped(
        root,
        phases={
            "l0": "returned",
            "rulepack": "returned",
            "l1": "returned",
            "l2": "failed",
            "e2e": "not_run",
            "coverage": "not_run",
        },
    )
    _recover(root, monkeypatch, original, [4, 5, 6], rounds=3)


@pytest.mark.parametrize(("fault", "verdict"), [("schema", "FAIL"), ("coverage", "ESCALATED")])
def test_recurring_schema_coverage_cycle_restart_keeps_window(
    offline_workspace, monkeypatch, fault, verdict
):
    """M2: a recurring (iii) finding reaches CYCLE_RESTART and the window survives (L2 message)."""
    root = offline_workspace
    proof, original = _earned_prefix(root, monkeypatch)
    for index, fixpoint in ((2, "RESET"), (3, "CYCLE_RESTART")):
        machine, calls = _new_machine(root, monkeypatch, fault=fault)
        assert machine.run().value == verdict
        _assert_calls(calls, [index + 1])
        state = _assert_preserved(root, proof, fixpoint=fixpoint)
    assert not any("restarting cycle" in err for err in state.infra_errors)
    assert (
        "tiered-reset: P2/P3-density observed at cycle 3 -- schema/coverage only, clean credit preserved"
    ) in state.infra_errors
    _recover(root, monkeypatch, original, [1, 2, 5])


def test_coverage_on_receipt_gate_path_preserves(offline_workspace, monkeypatch):
    """M2: coverage + schema in one round fails at the receipt gate and preserves."""
    root = offline_workspace
    proof, original = _earned_prefix(root, monkeypatch)
    machine, calls = _new_machine(root, monkeypatch, fault="schema", qodo_fault="coverage")
    assert machine.run() is Verdict.FAIL
    _assert_calls(calls, [3])
    state = _assert_preserved(root, proof, fixpoint="RESET")
    assert set(state.round_history[-1]["dispositions"]) == {
        "incomplete-coverage-qodo",
        "schema-fail-adversarial",
    }
    assert state.infra_errors[-1] == (
        "receipt: review evidence rejected in current round; see receipts/attempted"
    )
    _recover(root, monkeypatch, original, [1, 2, 4])


@pytest.mark.parametrize(("fault", "qodo_fault"), [("literal", "coverage"), ("coverage", "literal")])
def test_coverage_with_literal_damage_on_gate_path_resets(
    offline_workspace, monkeypatch, fault, qodo_fault
):
    """Gate path: literal damage beside a (iii) finding is not (iii)-only; M6 wipe."""
    root = offline_workspace
    _, original = _earned_prefix(root, monkeypatch)
    machine, calls = _new_machine(root, monkeypatch, fault=fault, qodo_fault=qodo_fault)
    assert machine.run() is Verdict.FAIL
    _assert_calls(calls, [3])
    state = _assert_wiped(root)
    assert any("excerpt content mismatch" in err for err in state.infra_errors)
    _recover(root, monkeypatch, original, [4, 5, 6], rounds=3)


_PRESERVED_LOG = (
    "tiered-reset: P2/P3-density observed at cycle %d -- schema/coverage only, clean credit preserved"
)
_RESTART_LOG = "tiered-reset: P2/P3-density -- restarting cycle %d"
_AUDIT_UNAVAILABLE = "review evidence rejected in current round; audit payload unavailable"


def _assert_log_matches_ledger(state, cycle):
    """L-B: the CYCLE_RESTART log line names the action the ledger recorded."""
    latest = state.round_history[-1]
    assert latest["fixpoint"] == "CYCLE_RESTART"
    preserved_line = _PRESERVED_LOG % cycle in state.infra_errors
    restart_line = _RESTART_LOG % cycle in state.infra_errors
    if latest["clean_credit_action"] == "preserved":
        assert preserved_line and not restart_line
    else:
        assert latest["clean_credit_action"] == "reset"
        assert restart_line and not preserved_line


def _inject_gate_errors(monkeypatch, machine, extra):
    """Append gate errors; record what the predicate and real gate saw at that moment."""
    original = machine._receipt_gate_round_errors
    seen = []

    def errors():
        observed = original()
        seen.append((machine._schema_coverage_credit_preserve(machine._fixpoint_reached()), observed))
        return observed + list(extra)

    monkeypatch.setattr(machine, "_receipt_gate_round_errors", errors)
    return seen


@pytest.mark.parametrize(
    "extra",
    [[_AUDIT_UNAVAILABLE], [_AUDIT_UNAVAILABLE, "receipt write failed: constructed I/O error"]],
    ids=["audit-unavailable", "audit-unavailable-plus-io"],
)
def test_la_audit_unavailable_beside_attempted_schema_wipes(offline_workspace, monkeypatch, extra):
    """L-A pin: attempted + audit-unavailable (+ I/O) gate errors wipe like M6 (deliberate narrowing)."""
    root = offline_workspace
    _, original = _earned_prefix(root, monkeypatch)
    machine, calls = _new_machine(root, monkeypatch, fault="schema")
    seen = _inject_gate_errors(monkeypatch, machine, extra)
    assert machine.run() is Verdict.FAIL
    _assert_calls(calls, [3])
    state = _assert_wiped(root)
    # Without the injected errors this round was (iii)-only and would preserve.
    assert seen == [(True, ["review evidence rejected in current round; see receipts/attempted"])]
    assert not any("clean credit preserved" in err for err in state.infra_errors)
    _recover(root, monkeypatch, original, [4, 5, 6], rounds=3)


@pytest.mark.parametrize("damage", ["preserve", "audit-unavailable", "literal"])
def test_lb_cycle_restart_log_matches_ledger(offline_workspace, monkeypatch, damage):
    """L-B: no 'credit preserved' line when the gate later takes the M6 wipe."""
    root = offline_workspace
    proof, original = _earned_prefix(root, monkeypatch)
    machine, _ = _new_machine(root, monkeypatch, fault="coverage")
    assert machine.run() is Verdict.ESCALATED
    _assert_preserved(root, proof, fixpoint="RESET")
    kwargs = {"fault": "coverage"}
    if damage == "literal":
        kwargs["qodo_fault"] = "literal"
    machine, calls = _new_machine(root, monkeypatch, **kwargs)
    seen = _inject_gate_errors(
        monkeypatch, machine, [_AUDIT_UNAVAILABLE] if damage == "audit-unavailable" else []
    )
    expected = Verdict.ESCALATED if damage == "preserve" else Verdict.FAIL
    assert machine.run() is expected
    # The predicate alone would preserve; only the gate evidence decides the wipe.
    assert [preserve for preserve, _ in seen] == [True]
    _assert_calls(calls, [4])
    state, _ = _state(root)
    _assert_log_matches_ledger(state, 3)
    if damage == "preserve":
        _assert_preserved(root, proof, fixpoint="CYCLE_RESTART")
        _recover(root, monkeypatch, original, [1, 2, 5])
    else:
        assert state.round_history[-1]["clean_credit_action"] == "reset"
        assert state.consecutive_clean_rounds == 0 and state.earned_clean_window["cycles"] == []
        _recover(root, monkeypatch, original, [5, 6, 7], rounds=3)


def _p3_prefix(root, monkeypatch, severity="P3"):
    """A recurring product finding: new in cycle 1 (reset), then cycles 2-3 earn."""
    # One P3 on this two-line fixture diff is above the production density
    # threshold; raise it so the recurring P3 is the sub-threshold CLEAN case.
    monkeypatch.setattr("code_forge.flow_contract.P3_DENSITY_THRESHOLD", 1.0)
    machine, calls = _new_machine(
        root,
        monkeypatch,
        rounds=3,
        mixed_product=True,
        product_severity=severity,
    )
    machine.run()
    _assert_calls(calls, [1, 2, 3])
    state, _ = _state(root)
    return state


def test_m4_recurring_p3_plus_coverage_preserves_on_real_run(offline_workspace, monkeypatch):
    """M4 run level: recurring P3 + new incomplete-coverage, then recurring coverage (CYCLE_RESTART), both keep [2, 3].

    The cause is incomplete-coverage, not schema-fail (name fixed in fix3-c3);
    the schema-fail companion run is test_c3_schema_invalid_findings_preserve_on_real_run.
    """
    root = offline_workspace
    state = _p3_prefix(root, monkeypatch)
    assert [row["clean_credit_action"] for row in state.round_history] == ["reset", "earned", "earned"]
    proof = copy.deepcopy(state.earned_clean_window)
    assert [entry["cycle"] for entry in proof["cycles"]] == [2, 3]
    product = [fp for fp, disp in state.round_history[-1]["dispositions"].items() if disp == "CONFIRMED"]
    assert len(product) == 1
    for cycle, fixpoint in ((4, "RESET"), (5, "CYCLE_RESTART")):
        machine, calls = _new_machine(
            root,
            monkeypatch,
            fault="coverage",
            mixed_product=True,
            product_severity="P3",
        )
        assert machine.run() is Verdict.ESCALATED
        _assert_calls(calls, [cycle])
        state, _ = _state(root)
        latest = state.round_history[-1]
        assert latest["clean_credit_action"] == "preserved" and latest["fixpoint"] == fixpoint
        assert latest["dispositions"][product[0]] == "CONFIRMED"
        assert latest["dispositions"]["incomplete-coverage-adversarial"] == "CONFIRMED"
        assert state.consecutive_clean_rounds == 2 and state.earned_clean_window == proof
        assert _restore_result(root).passed and not _verify(root).passed
    _assert_log_matches_ledger(state, 4)


@pytest.mark.parametrize("severity", ["P2", "P1", "P0"])
def test_m4_recurring_p0_to_p2_plus_schema_resets_on_real_run(offline_workspace, monkeypatch, severity):
    """M4 negative: a recurring P0-P2 beside schema/coverage keeps the M6 wipe."""
    root = offline_workspace
    state = _p3_prefix(root, monkeypatch, severity)
    expected_prefix = {"P2": "CYCLE_RESTART", "P1": "RESET", "P0": "RESET"}[severity]
    assert [row["fixpoint"] for row in state.round_history][1:] == [expected_prefix] * 2
    machine, calls = _new_machine(
        root,
        monkeypatch,
        fault="coverage",
        mixed_product=True,
        product_severity=severity,
    )
    machine.run()
    _assert_calls(calls, [4])
    state, _ = _state(root)
    latest = state.round_history[-1]
    assert latest["clean_credit_action"] == "reset"
    assert state.consecutive_clean_rounds == 0 and state.earned_clean_window["cycles"] == []


@pytest.mark.parametrize("severity", ["P2", "P1", "P0"])
def test_l3_recurring_finding_raised_to_p0_p2_beside_coverage_wipes_earned_window(
    offline_workspace, monkeypatch, severity
):
    """L-3: non-vacuous negative. The window [2, 3] was earned beside the
    recurring P3; the same fingerprint (location-stable) now at P0-P2 beside
    incomplete-coverage is a recurring finding, so only the tier decides,
    and the counterfactual is not CLEAN: the earned window is wiped."""
    root = offline_workspace
    state = _p3_prefix(root, monkeypatch)
    assert [row["clean_credit_action"] for row in state.round_history] == ["reset", "earned", "earned"]
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == [2, 3]
    product = [fp for fp, disp in state.round_history[-1]["dispositions"].items() if disp == "CONFIRMED"]
    assert len(product) == 1
    machine, calls = _new_machine(
        root,
        monkeypatch,
        fault="coverage",
        mixed_product=True,
        product_severity=severity,
    )
    machine.run()
    _assert_calls(calls, [4])
    state, _ = _state(root)
    latest = state.round_history[-1]
    assert latest["dispositions"][product[0]] == "CONFIRMED"
    assert latest["dispositions"]["incomplete-coverage-adversarial"] == "CONFIRMED"
    # The new incomplete-coverage cause is itself a first-seen CONFIRMED, so the
    # recorded fixpoint is RESET (clause a) at every tier; the tier is decided in
    # the counterfactual (P2 -> CYCLE_RESTART, P0/P1 -> RESET), neither CLEAN.
    assert latest["fixpoint"] == "RESET"
    assert latest["clean_credit_action"] == "reset"
    assert state.consecutive_clean_rounds == 0 and state.earned_clean_window["cycles"] == []


def test_m4_new_confirmed_product_plus_schema_resets_on_real_run(offline_workspace, monkeypatch):
    """M4 negative: a NEW CONFIRMED product finding (even P3) beside schema keeps the M6 wipe."""
    root = offline_workspace
    _, original = _earned_prefix(root, monkeypatch)
    monkeypatch.setattr("code_forge.flow_contract.P3_DENSITY_THRESHOLD", 1.0)
    machine, calls = _new_machine(
        root,
        monkeypatch,
        fault="coverage",
        mixed_product=True,
        product_severity="P3",
    )
    machine.run()
    _assert_calls(calls, [3])
    _assert_wiped(root)
    _recover(root, monkeypatch, original, [4, 5, 6], rounds=3)


def _c3_companion(machine):
    companions = [
        f
        for f in machine._state.findings
        if f.source == "UNTRUSTED" and f.id == f"l1-adversarial-untrusted-{f.fingerprint}"
    ]
    assert len(companions) == 1 and companions[0].disposition.value == "UNCERTAIN"
    return companions[0].fingerprint


def test_c3_schema_invalid_findings_preserve_on_real_run(offline_workspace, monkeypatch):
    """C3 §4.7: real factories path. One parsed schema-invalid response with valid
    excerpts yields schema-fail + an UNTRUSTED companion; the round preserves and
    the persisted row omits the companion fingerprint (mechanism C)."""
    root = offline_workspace
    proof, original = _earned_prefix(root, monkeypatch)
    machine, calls = _new_machine(root, monkeypatch, fault="schema-findings")
    seen = _inject_gate_errors(monkeypatch, machine, [])
    assert machine.run() is not Verdict.PASS
    _assert_calls(calls, [3])
    assert seen == [(True, ["review evidence rejected in current round; see receipts/attempted"])]
    fp = _c3_companion(machine)
    state = _assert_preserved(root, proof, fixpoint="RESET")
    latest = state.round_history[-1]
    assert fp not in latest["dispositions"] and fp in latest["l1_fingerprints"]
    assert latest["dispositions"]["schema-fail-adversarial"] == "CONFIRMED"
    # The companion stays visible as audit data in state findings.
    assert any(f.fingerprint == fp and f.source == "UNTRUSTED" for f in state.findings)
    _recover(root, monkeypatch, original, [1, 2, 4])


def test_c3v2_single_response_with_pure_deletion_section_preserves(offline_workspace, monkeypatch):
    """C3 v3 T3: one schema-invalid response per pass on a diff that also holds
    an excerpt-exempt pure-deletion section still preserves. Rule 2a counts the
    single response (1), so no receipt-diff belt is hidden in the decision."""
    from code_forge.reviewer_json import _requires_l1_excerpts

    root = offline_workspace
    for name, text in DELETED_CONTENT.items():
        (root / name).write_text(text, encoding="utf-8")
    deletion = DELETION_DIFF[len(DIFF) :]
    assert _requires_l1_excerpts(deletion) is False
    proof, _ = _earned_prefix(root, monkeypatch, diff=DELETION_DIFF)
    machine, calls = _new_machine(root, monkeypatch, fault="schema-findings", diff=DELETION_DIFF)
    seen = _inject_gate_errors(monkeypatch, machine, [])
    assert machine.run() is not Verdict.PASS
    _assert_calls(calls, [3])
    assert seen == [(True, ["review evidence rejected in current round; see receipts/attempted"])]
    fp = _c3_companion(machine)
    state = _assert_preserved(root, proof, fixpoint="RESET", diff=DELETION_DIFF)
    latest = state.round_history[-1]
    assert fp not in latest["dispositions"] and fp in latest["l1_fingerprints"]


def test_two_companions_from_one_schema_response_both_stripped_on_real_run(
    offline_workspace, monkeypatch
):
    """C3 v3 T6 (CR2 F-2): one parsed schema-invalid adversarial response with
    two candidates on a real, non-exempt diff -> two UNTRUSTED companions; the
    round preserves and the persisted row omits BOTH fingerprints."""
    root = offline_workspace
    proof, original = _earned_prefix(root, monkeypatch)
    machine, calls = _new_machine(root, monkeypatch, fault="schema-findings-two")
    seen = _inject_gate_errors(monkeypatch, machine, [])
    assert machine.run() is not Verdict.PASS
    _assert_calls(calls, [3])
    assert len(seen) == 1 and seen[0][0] is True
    comps = [
        f
        for f in machine._state.findings
        if f.source == "UNTRUSTED" and f.id == f"l1-adversarial-untrusted-{f.fingerprint}"
    ]
    assert len(comps) == 2 and all(c.disposition.value == "UNCERTAIN" for c in comps)
    state = _assert_preserved(root, proof, fixpoint="RESET")
    latest = state.round_history[-1]
    for c in comps:
        assert c.fingerprint in latest["l1_fingerprints"]
        assert c.fingerprint not in latest["dispositions"]
    ever_seen = {fp for row in state.round_history for fp in row.get("dispositions", {})}
    assert not ever_seen & {c.fingerprint for c in comps}
    _recover(root, monkeypatch, original, [1, 2, 4])


@pytest.mark.parametrize(
    ("extra", "qodo_fault"),
    [
        ([_AUDIT_UNAVAILABLE], None),
        ([_AUDIT_UNAVAILABLE, "receipt write failed: constructed I/O error"], None),
        ([], "literal"),
    ],
    ids=["audit-unavailable", "audit-unavailable-plus-io", "literal"],
)
def test_c3_companion_with_gate_damage_wipes_and_keeps_fingerprint(
    offline_workspace, monkeypatch, extra, qodo_fault
):
    """C3 §4.3: gate damage beside a companion takes the M6 wipe; F stays in the row."""
    root = offline_workspace
    _, original = _earned_prefix(root, monkeypatch)
    machine, calls = _new_machine(root, monkeypatch, fault="schema-findings", qodo_fault=qodo_fault)
    seen = _inject_gate_errors(monkeypatch, machine, extra)
    assert machine.run() is Verdict.FAIL
    _assert_calls(calls, [3])
    assert len(seen) == 1
    if qodo_fault is None:
        # Without the injected damage the predicate alone would preserve.
        assert seen[0][0] is True
    fp = _c3_companion(machine)
    state = _assert_wiped(root)
    assert state.round_history[-1]["dispositions"][fp] == "UNCERTAIN"
    assert not any("clean credit preserved" in err for err in state.infra_errors)
    _recover(root, monkeypatch, original, [4, 5, 6], rounds=3)


def _survivor(severity):
    from code_forge.state import StateFinding
    from code_forge.disposition import Disposition

    finding = StateFinding(
        id="mutant-control.x_mutmut_1",
        fingerprint="mutant:control.x_mutmut_1",
        source="MUTANT",
        disposition=Disposition.CONFIRMED,
        file="control.ts",
        line_range=[0, 0],
        description="mutant survived: control.x_mutmut_1",
    )
    if severity is not None:
        finding.severity = severity
    return finding


@pytest.mark.parametrize(
    ("severity", "action"),
    [("P3", "preserved"), (None, "reset")],
    ids=["p3-survivor-preserved", "default-survivor-reset"],
)
def test_lc_survivor_ladder_third_round_with_coverage(offline_workspace, monkeypatch, severity, action):
    """L-C: the 3-consecutive-survivor stop records preserved only for a (iii)-only counterfactual."""
    root = offline_workspace
    monkeypatch.setattr("code_forge.flow_contract.P3_DENSITY_THRESHOLD", 1.0)
    machine, calls = _new_machine(root, monkeypatch, fault="coverage", fault_rounds={3}, rounds=3)
    machine.l2_runner = lambda *args, **kwargs: ([_survivor(severity)], [])
    assert machine.run() is Verdict.FAIL
    _assert_calls(calls, [1, 2, 3])
    state, _ = _state(root)
    assert state.consecutive_survivor_rounds == 3
    assert "mutation: surviving mutants reported in 3 consecutive rounds" in state.infra_errors
    latest = state.round_history[-1]
    assert latest["clean_credit_action"] == action
    assert latest["dispositions"]["incomplete-coverage-adversarial"] == "CONFIRMED"
    if action == "preserved":
        assert [row["clean_credit_action"] for row in state.round_history] == [
            "reset",
            "earned",
            "preserved",
        ]
        assert latest["fixpoint"] == "RESET"
        assert state.consecutive_clean_rounds == 1
        assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == [2]
    else:
        assert state.consecutive_clean_rounds == 0 and state.earned_clean_window["cycles"] == []
    assert _restore_result(root).passed and not _verify(root).passed


@pytest.mark.parametrize("cause", ["mixed-product", "falsifier-transport", "acquisition"])
def test_schema_with_non_iii_cause_resets(offline_workspace, monkeypatch, cause):
    """M2: each non-(iii) companion cause keeps the M6 wipe on a real run."""
    root = offline_workspace
    _, original = _earned_prefix(root, monkeypatch)
    kwargs = {
        "mixed-product": {"fault": "schema", "mixed_product": True},
        "falsifier-transport": {
            "fault": "schema",
            "falsifier_reply": llm_invoke.LLMInvokeError(
                "constructed falsifier timeout",
                is_timeout=True,
                retryable=False,
            ),
        },
        "acquisition": {"fault": "no-json", "qodo_fault": "schema"},
    }[cause]
    machine, calls = _new_machine(root, monkeypatch, **kwargs)
    assert machine.run() is Verdict.FAIL
    _assert_calls(calls, [3])
    state = _assert_wiped(root)
    dispositions = state.round_history[-1]["dispositions"]
    assert any(fp.startswith("schema-fail-") for fp in dispositions)
    assert any(
        not fp.startswith("schema-fail-") and disp in ("CONFIRMED", "UNCERTAIN")
        for fp, disp in dispositions.items()
    )
    _recover(root, monkeypatch, original, [4, 5, 6], rounds=3)


@pytest.mark.parametrize("kind", ["protocol", "transport"])
def test_current_falsifier_protocol_and_transport_keep_candidate_but_differ_on_credit(
    offline_workspace, monkeypatch, kind
):
    root = offline_workspace
    proof, original = _earned_prefix(root, monkeypatch)
    reply = (
        "not JSON"
        if kind == "protocol"
        else llm_invoke.LLMInvokeError(
            "constructed falsifier timeout",
            is_timeout=True,
            retryable=False,
        )
    )
    machine, calls = _new_machine(root, monkeypatch, falsifier_reply=reply)
    assert machine.run() is Verdict.PENDING
    _assert_calls(calls, [3])
    state, raw = _state(root)
    assert not state.converged and state.rounds_with_falsify_infra == 1
    candidate = next(f for f in raw["findings"] if f["source"] == "L1")
    assert candidate["disposition"] == "UNCERTAIN" and candidate["diagnostic_kind"] is None
    row = state.round_history[-1]
    if kind == "protocol":
        assert state.consecutive_clean_rounds == 2 and state.earned_clean_window == proof
        assert row["clean_credit_action"] == "interrupted" and row["fixpoint"] == "INCOMPLETE"
        assert row["falsify_protocol_failures"] == [candidate["fingerprint"]]
    else:
        assert state.consecutive_clean_rounds == 0 and state.earned_clean_window["cycles"] == []
        assert row["clean_credit_action"] == "reset" and row["fixpoint"] == "RESET"
        assert row["falsify_protocol_failures"] == []
    receipts = _cycle_receipts(root, 3)
    assert [r["pass_status"] for r in receipts] == ["completed"] * 3
    failed = receipts[0]["findings"][0]
    assert failed["disposition"] == "UNCERTAIN"
    assert failed["basis"]["authority"] == "infra-unavailable"
    assert failed["basis"]["falsification_survived"] is False
    assert _restore_result(root).passed and not _verify(root).passed
    # The real reporting consumer deliberately rejects unresolved LOCAL HOLD.
    # Do not manufacture a CI state or a FAIL just to make it render.
    with pytest.raises(ValueError, match="PENDING verdict on a LOCAL"):
        _report(root)
    _recover(
        root,
        monkeypatch,
        original,
        [1, 2, 4] if kind == "protocol" else [4, 5, 6],
        rounds=1 if kind == "protocol" else 3,
    )


@pytest.mark.parametrize("fault", ["no-json", "timeout-empty-kind", "schema", "coverage", "literal"])
def test_current_ci_failure_does_not_import_local_credit(offline_workspace, monkeypatch, fault):
    root = offline_workspace
    _, original = _earned_prefix(root, monkeypatch)
    machine, calls = _new_machine(root, monkeypatch, mode=Mode.CI, fault=fault)
    assert machine.run() is Verdict.FAIL
    _assert_calls(calls, [3])
    state, raw = _state(root)
    assert state.mode is Mode.CI and not state.converged
    assert state.consecutive_clean_rounds == 0 and state.earned_clean_window is None
    assert "earned_clean_window" not in raw
    assert len(state.round_history) == 1 and state.round_history[0]["round"] == 2
    assert "clean_credit_action" not in state.round_history[0]
    summary, report = _report(root)
    assert "code-forge: FAIL " in summary and report["results"]
    assert not _verify(root).passed
    _assert_original_receipts(original)


@pytest.mark.parametrize("kind", ["protocol", "transport"])
def test_current_ci_falsifier_failure_reaches_real_reporting(offline_workspace, monkeypatch, kind):
    root = offline_workspace
    _, original = _earned_prefix(root, monkeypatch)
    reply = (
        "not JSON"
        if kind == "protocol"
        else llm_invoke.LLMInvokeError(
            "constructed falsifier timeout",
            is_timeout=True,
            retryable=False,
        )
    )
    machine, calls = _new_machine(root, monkeypatch, mode=Mode.CI, falsifier_reply=reply)
    assert machine.run() is Verdict.FAIL
    _assert_calls(calls, [3])
    state, raw = _state(root)
    assert state.consecutive_clean_rounds == 0 and state.earned_clean_window is None
    assert not state.converged and state.rounds_with_falsify_infra == 1
    assert any(f["source"] == "L1" and f["disposition"] == "UNCERTAIN" for f in raw["findings"])
    summary, report = _report(root)
    assert "FAIL findings=2 confirmed=0 uncertain=1 dismissed=1" in summary
    assert {f["id"] for f in raw["findings"]} == {
        candidate["id"] for candidate in raw["findings"] if candidate["source"] == "L1"
    } | {"MUTATION_SKIPPED"}
    skipped = next(f for f in raw["findings"] if f["id"] == "MUTATION_SKIPPED")
    assert skipped["source"] == "MUTANT" and skipped["disposition"] == "DISMISSED"
    assert len(report["results"]) == 2
    assert [r["pass_status"] for r in _cycle_receipts(root, 3)] == ["completed"] * 3
    assert not _verify(root).passed
    _assert_original_receipts(original)


@pytest.mark.parametrize("source_change", ["new-diff", "missing-state-source"])
def test_current_changed_or_missing_source_identity_cannot_inherit_credit(
    offline_workspace, monkeypatch, source_change
):
    root = offline_workspace
    _, original = _earned_prefix(root, monkeypatch)
    diff = DIFF
    if source_change == "new-diff":
        diff = CHANGED_DIFF
        (root / "control.ts").write_text(CONTENT["control.ts"].replace("value = 2", "value = 20"))
    else:
        state = load_state(root / ".code-forge" / "state.json")
        state.source_hash = None
        save_state(state, root / ".code-forge" / "state.json")
        result = _verify(root)
        assert not result.passed and "modern state lacks source identity" in result.reason
    machine, calls = _new_machine(root, monkeypatch, diff=diff, fault="no-json")
    assert machine.run() is Verdict.FAIL
    _assert_calls(calls, [3])
    state, _ = _state(root)
    assert state.source_hash == compute_source_hash(git_diff=diff)
    assert state.consecutive_clean_rounds == 0 and state.earned_clean_window["cycles"] == []
    assert state.round_history[-1]["clean_credit_action"] == "interrupted"
    summary, report = _report(root)
    assert "FAIL findings=2 confirmed=2" in summary and len(report["results"]) == 2
    assert not _verify(root, diff).passed
    _recover(root, monkeypatch, original, [4, 5, 6], rounds=3, diff=diff)


def test_current_unknown_window_source_keeps_number_but_refuses_before_dispatch(
    offline_workspace, monkeypatch
):
    root = offline_workspace
    _, original = _earned_prefix(root, monkeypatch)
    state = load_state(root / ".code-forge" / "state.json")
    state.earned_clean_window["source_hash"] = "unknown-source-proof"
    save_state(state, root / ".code-forge" / "state.json")
    before = (root / ".code-forge" / "state.json").read_bytes()
    machine, calls = _new_machine(root, monkeypatch)
    assert machine.run() is Verdict.FAIL and calls == []
    assert machine._state.consecutive_clean_rounds == 2
    assert "source/repository scope mismatch" in machine._state.infra_errors[-1]
    assert (root / ".code-forge" / "state.json").read_bytes() == before
    loaded, _ = _state(root)
    assert loaded.consecutive_clean_rounds == 2
    assert not _restore_result(root).passed and not _verify(root).passed
    # Current restore refusal returns before persisting its in-memory FAIL.
    # The canonical-state report therefore still shows the prefix ESCALATED.
    summary, report = _report(root)
    assert "ESCALATED findings=0 confirmed=0" in summary and report["results"] == []
    _assert_original_receipts(original)


def test_current_frozen_diff_identity_is_not_a_live_worktree_snapshot(offline_workspace, monkeypatch):
    root = offline_workspace
    proof, original = _earned_prefix(root, monkeypatch)
    path = root / "control.ts"
    changed = CONTENT["control.ts"].replace("value = 2", "value = 200")
    path.write_text(changed, encoding="utf-8")
    machine, calls = _new_machine(root, monkeypatch, fault="no-json")
    assert machine.run() is Verdict.FAIL
    _assert_calls(calls, [3])
    state, _ = _state(root)
    assert path.read_text(encoding="utf-8") == changed
    assert state.earned_clean_window == proof and state.consecutive_clean_rounds == 2
    assert state.round_history[-1]["clean_credit_action"] == "interrupted"
    assert _restore_result(root).passed  # verifies the supplied frozen diff only
    assert not _verify(root).passed
    summary, report = _report(root)
    assert "FAIL findings=2 confirmed=2" in summary and len(report["results"]) == 2
    # Restore actual fixture bytes before recovery. This test makes no claim
    # that endpoint equality proves no intermediate writes or native execution.
    path.write_text(CONTENT["control.ts"], encoding="utf-8")
    _recover(root, monkeypatch, original, [1, 2, 4])


def test_current_interruption_cannot_spend_an_already_large_enough_prior_window(
    offline_workspace, monkeypatch
):
    root = offline_workspace
    proof, original = _earned_prefix(root, monkeypatch)
    gate = root / ".code-forge" / "gate.yaml"
    gate.write_text(gate.read_text().replace("required_cycles: 3", "required_cycles: 2"))
    assert _verify(root).passed  # two actually verified cycles now meet policy
    machine, calls = _new_machine(root, monkeypatch, fault="no-json", threshold=2)
    assert machine.run() is Verdict.FAIL
    _assert_calls(calls, [3])
    state, _ = _state(root)
    assert state.earned_clean_window == proof and state.consecutive_clean_rounds == 2
    assert state.round_history[-1]["clean_credit_action"] == "interrupted"
    assert _restore_result(root).passed
    result = _verify(root)
    assert not result.passed and "latest host attempt did not earn clean proof" in result.reason
    summary, report = _report(root)
    assert "FAIL findings=2 confirmed=2" in summary and len(report["results"]) == 2
    _recover(root, monkeypatch, original, [1, 2, 4])


def test_current_prior_receipt_digest_damage_refuses_then_exact_restoration_recovers(
    offline_workspace, monkeypatch
):
    root = offline_workspace
    proof, original = _earned_prefix(root, monkeypatch)
    path = root / ".code-forge" / "receipts" / "receipt-c1p1.json"
    path.write_bytes(original[path] + b"\n")  # valid, semantically equal JSON; different proof bytes
    assert json.loads(path.read_bytes()) == json.loads(original[path])
    before = (root / ".code-forge" / "state.json").read_bytes()
    machine, calls = _new_machine(root, monkeypatch)
    assert machine.run() is Verdict.FAIL and calls == []
    assert machine._state.earned_clean_window == proof
    assert machine._state.consecutive_clean_rounds == 2
    assert (root / ".code-forge" / "state.json").read_bytes() == before
    result = _restore_result(root)
    assert not result.passed and result.reason == "earned receipt digest mismatch cycle 1"
    assert not _verify(root).passed
    state, _ = _state(root)
    assert state.consecutive_clean_rounds == 2 and state.earned_clean_window == proof
    summary, report = _report(root)
    assert "ESCALATED findings=0 confirmed=0" in summary and report["results"] == []
    path.write_bytes(original[path])
    assert _restore_result(root).passed
    _recover(root, monkeypatch, original, [1, 2, 3])


# fix6 (CR2G-M1, CoS 14:34 option A): a failed host attempt never preserves.
# Cases follow CR1's probes (code-review/pr319-cr2g-m1-cr1-probes/): every
# failure after all six phases returned takes the M6 reset, as on main
# 869873f6, and the original exception still propagates.

_ALL_RETURNED = dict.fromkeys(("l0", "rulepack", "l1", "l2", "e2e", "coverage"), "returned")


class _AfterRoundInterrupt(KeyboardInterrupt):
    """A KeyboardInterrupt subclass, so the test can tell it from a real Ctrl-C."""


def _fail_receipt_writes(mp):
    """A receipt-write I/O error in the round under test (recorded, not raised)."""

    def write_receipts(*_args, **_kwargs):
        raise OSError(28, "constructed receipt write I/O error")

    mp.setattr("code_forge.receipt.write_receipts", write_receipts)


def _raise_in_hook(machine, error):
    def hook(_index):
        raise error

    machine.post_round_hook = hook


def _fail_state_save_after_publish(mp, machine):
    """Fail the round-end save once this round's receipts were published (no test seam)."""
    publish = machine._publish_l1_receipts
    save = machine._persist_state
    armed = {"published": False, "raised": False}

    def published(*args, **kwargs):
        try:
            return publish(*args, **kwargs)
        finally:
            armed["published"] = True

    def failing_save():
        if armed["published"] and not armed["raised"]:
            armed["raised"] = True
            raise OSError(5, "constructed state save failure")
        return save()

    mp.setattr(machine, "_publish_l1_receipts", published)
    mp.setattr(machine, "_persist_state", failing_save)
    return armed


def _raise_in_gate(mp, machine):
    def gate():
        raise OSError(5, "constructed receipt gate read failure")

    mp.setattr(machine, "_receipt_gate_round_errors", gate)


def _crash_before_publish(mp, machine):
    def publish(*_args, **_kwargs):
        raise RuntimeError("constructed crash before receipt publication")

    mp.setattr(machine, "_publish_l1_receipts", publish)


def _workspace_blocker_after_round(mp, machine):
    """Production raise: the mutation-workspace blocker stops run() before the gate."""
    execute = machine._execute_round

    def blocked(round_index):
        execute(round_index)
        machine._mutation_workspace_blocker = "constructed workspace blocker"

    mp.setattr(machine, "_execute_round", blocked)


def _raise_after_gate(mp, machine):
    """The main-path gate already reset; recording its finding then raises."""

    def record(*_args, **_kwargs):
        raise RuntimeError("constructed failure after gate")

    mp.setattr(machine, "_record_receipt_gate_failure", record)


def _spy_actions(mp, machine):
    """Record each `_finish_host_attempt` action, so a case proves a preserve was settled."""
    finish = machine._finish_host_attempt
    armed = {"actions": [], "raised": False}

    def spy(action, *args, **kwargs):
        armed["actions"].append(action)
        return finish(action, *args, **kwargs)

    mp.setattr(machine, "_finish_host_attempt", spy)
    return armed


def _fail_first_save_after_preserve(mp, machine):
    """fix7 (CR3R-L1 a): the first state save after the main path settled `preserved` fails."""
    armed = _spy_actions(mp, machine)
    save = machine._persist_state

    def failing_save():
        if "preserved" in armed["actions"] and not armed["raised"]:
            armed["raised"] = True
            raise OSError(5, "constructed state save failure after preserve")
        return save()

    mp.setattr(machine, "_persist_state", failing_save)
    return armed


def _record_raises_after_preserve(mp, machine):
    """fix7 (CR3R-L1 b): `_record_receipt_gate_failure` raises after the gate settled `preserved`."""
    armed = _spy_actions(mp, machine)

    def record(*_args, **_kwargs):
        armed["raised"] = True
        raise RuntimeError("constructed gate record failure after preserve")

    mp.setattr(machine, "_record_receipt_gate_failure", record)
    return armed


_FIX6_CASES = {
    # case: (qodo_fault, receipt write fails, inject(mp, machine), raised type, match)
    "write-io-then-runtime-error": (
        None,
        True,
        lambda mp, m: _raise_in_hook(m, RuntimeError("constructed post-round failure")),
        RuntimeError,
        "constructed post-round failure",
    ),
    "write-io-then-ctrl-c": (
        None,
        True,
        lambda mp, m: _raise_in_hook(m, _AfterRoundInterrupt("constructed interrupt")),
        _AfterRoundInterrupt,
        "constructed interrupt",
    ),
    "write-io-then-state-save-oserror": (
        None,
        True,
        _fail_state_save_after_publish,
        OSError,
        "constructed state save failure",
    ),
    "write-io-then-workspace-blocker": (
        None,
        True,
        _workspace_blocker_after_round,
        TimeoutBreaker,
        "constructed workspace blocker",
    ),
    "write-io-then-failure-after-gate": (
        None,
        True,
        _raise_after_gate,
        RuntimeError,
        "constructed failure after gate",
    ),
    "gate-raises-oserror": (
        None,
        False,
        _raise_in_gate,
        OSError,
        "constructed receipt gate read failure",
    ),
    "literal-damage-then-runtime-error": (
        "literal",
        False,
        lambda mp, m: _raise_in_hook(m, RuntimeError("constructed post-round failure")),
        RuntimeError,
        "constructed post-round failure",
    ),
    "crash-before-receipts-published": (
        None,
        False,
        _crash_before_publish,
        RuntimeError,
        "constructed crash before receipt publication",
    ),
    "clean-gate-then-runtime-error": (
        None,
        False,
        lambda mp, m: _raise_in_hook(m, RuntimeError("constructed post-round failure")),
        RuntimeError,
        "constructed post-round failure",
    ),
    "clean-gate-then-ctrl-c": (
        None,
        False,
        lambda mp, m: _raise_in_hook(m, _AfterRoundInterrupt("constructed interrupt")),
        _AfterRoundInterrupt,
        "constructed interrupt",
    ),
    # fix7 (CR3R-L1): the main path already settled `preserved` before the failure.
    "preserved-then-state-save-oserror": (
        None,
        False,
        _fail_first_save_after_preserve,
        OSError,
        "constructed state save failure after preserve",
    ),
    "preserved-then-gate-record-error": (
        None,
        False,
        _record_raises_after_preserve,
        RuntimeError,
        "constructed gate record failure after preserve",
    ),
}


@pytest.mark.parametrize("case", list(_FIX6_CASES))
def test_fix6_failed_host_attempt_never_preserves(offline_workspace, monkeypatch, case):
    """CR2G-M1: a failure after every phase returned ends as reset and re-raises (P6-1, guard 3).

    fix7 (CR3R-L1 / J-L1): this holds even when the main path had already
    settled the round as `preserved` before the failure.
    """
    qodo_fault, write_io, inject, error, match = _FIX6_CASES[case]
    root = offline_workspace
    _, original = _earned_prefix(root, monkeypatch)
    machine, calls = _new_machine(root, monkeypatch, fault="schema", qodo_fault=qodo_fault)
    with monkeypatch.context() as mp:
        if write_io:
            _fail_receipt_writes(mp)
        armed = inject(mp, machine)
        with pytest.raises(error, match=match):
            machine.run()
    if isinstance(armed, dict):
        assert armed["raised"]
        if "actions" in armed:
            assert "preserved" in armed["actions"], armed["actions"]
    _assert_calls(calls, [3])
    state = _assert_wiped(root, phases=_ALL_RETURNED)
    # The receipt-write error is in the ledger, not only in memory.
    logged = [e for e in state.infra_errors if "receipt write failed" in e]
    assert bool(logged) is write_io, state.infra_errors
    _recover(root, monkeypatch, original, [4, 5, 6], rounds=3)


@pytest.mark.parametrize(
    "case",
    [
        "write-io-then-state-save-oserror",
        "write-io-then-workspace-blocker",
        "preserved-then-state-save-oserror",
        "preserved-then-gate-record-error",
    ],
)
def test_fix6_failed_attempt_then_one_healthy_round_is_not_pass(offline_workspace, monkeypatch, case):
    """CR2 impact probe: one healthy round after a failed attempt earns credit 1, window [4].

    No terminal PASS with window [1, 2, 4]. fix7 (CR3R-L1 / J-L1): this
    includes a failure after the round settled `preserved`; at b33b72c8 those
    cases kept credit 2 and gave PASS [1, 2, 4].
    """
    qodo_fault, write_io, inject, error, match = _FIX6_CASES[case]
    root = offline_workspace
    _earned_prefix(root, monkeypatch)
    machine, _ = _new_machine(root, monkeypatch, fault="schema", qodo_fault=qodo_fault)
    with monkeypatch.context() as mp:
        if write_io:
            _fail_receipt_writes(mp)
        inject(mp, machine)
        with pytest.raises(error, match=match):
            machine.run()
    follow, calls = _new_machine(root, monkeypatch, rounds=1)
    verdict = follow.run()
    _assert_calls(calls, [4])
    state, _ = _state(root)
    assert verdict is not Verdict.PASS and state.converged is False
    assert state.consecutive_clean_rounds == 1
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == [4]


@pytest.mark.parametrize(
    "inject",
    [_fail_first_save_after_preserve, _record_raises_after_preserve],
    ids=["state-save", "gate-record"],
)
def test_fix7_demoted_preserve_restores_c3_companion(offline_workspace, monkeypatch, inject):
    """CR3R-L1: the demoted row is the M6 reset row, so the stripped C3 companion is back in it."""
    root = offline_workspace
    _, original = _earned_prefix(root, monkeypatch)
    machine, calls = _new_machine(root, monkeypatch, fault="schema-findings")
    with monkeypatch.context() as mp:
        armed = inject(mp, machine)
        with pytest.raises((OSError, RuntimeError), match="after preserve"):
            machine.run()
    assert armed["raised"] and "preserved" in armed["actions"], armed
    _assert_calls(calls, [3])
    fp = _c3_companion(machine)
    state = _assert_wiped(root, phases=_ALL_RETURNED)
    assert state.round_history[-1]["dispositions"][fp] == "UNCERTAIN"
    _recover(root, monkeypatch, original, [4, 5, 6], rounds=3)


def _survivor_preserve_setup(root, monkeypatch):
    """L-C setup: the third consecutive survivor round settles `preserved` on the survivor stop."""
    monkeypatch.setattr("code_forge.flow_contract.P3_DENSITY_THRESHOLD", 1.0)
    machine, calls = _new_machine(root, monkeypatch, fault="coverage", fault_rounds={3}, rounds=3)
    machine.l2_runner = lambda *args, **kwargs: ([_survivor("P3")], [])
    return machine, calls, [1, 2, 3], [4]


def _earned_then(fault):
    def setup(root, monkeypatch):
        _earned_prefix(root, monkeypatch)
        machine, calls = _new_machine(root, monkeypatch, fault=fault)
        return machine, calls, [3], [4]

    return setup


def _cycle_restart_preserve_setup(root, monkeypatch):
    """A recurring coverage finding: the second attempt preserves on CYCLE_RESTART (L-B log line)."""
    _earned_prefix(root, monkeypatch)
    machine, _ = _new_machine(root, monkeypatch, fault="coverage")
    assert machine.run() is Verdict.ESCALATED
    machine, calls = _new_machine(root, monkeypatch, fault="coverage")
    return machine, calls, [4], [5]


_SURVIVOR_STOP = "mutation: surviving mutants reported in 3 consecutive rounds"

# J-L1 crash points: each state save after a `preserved` settle (b33b72c8 lines).
_FIX7_POST_SETTLE_SAVES = {
    # receipt gate: settle :2275, save :2289
    "gate-save": (
        _earned_then("schema"),
        lambda st: st.verdict is Verdict.FAIL and _SURVIVOR_STOP not in st.infra_errors,
    ),
    # 3-survivor stop: settle :2304, save :2314
    "survivor-stop-save": (_survivor_preserve_setup, lambda st: _SURVIVOR_STOP in st.infra_errors),
    # active fixpoint: settle :2350, save :2356
    "active-fixpoint-save": (_earned_then("coverage"), lambda st: st.verdict is not Verdict.FAIL),
    # active fixpoint on CYCLE_RESTART: the L-B "clean credit preserved" line was already written
    "active-cycle-restart-save": (
        _cycle_restart_preserve_setup,
        lambda st: _PRESERVED_LOG % 3 in st.infra_errors,
    ),
}


@pytest.mark.parametrize("site", list(_FIX7_POST_SETTLE_SAVES))
def test_fix7_crash_at_each_post_settle_save_resets(offline_workspace, monkeypatch, site):
    """J-L1: an OSError on any state save after a `preserved` settle ends as reset with credit 0."""
    setup, at_site = _FIX7_POST_SETTLE_SAVES[site]
    root = offline_workspace
    machine, calls, rounds, follow_round = setup(root, monkeypatch)
    seen = []
    with monkeypatch.context() as mp:
        armed = _fail_first_save_after_preserve(mp, machine)
        save = machine._persist_state

        def observed_save():
            if "preserved" in armed["actions"] and not armed["raised"]:
                seen.append(at_site(machine._state))
            return save()

        mp.setattr(machine, "_persist_state", observed_save)
        with pytest.raises(OSError, match="constructed state save failure after preserve"):
            machine.run()
    assert armed["raised"] and armed["actions"][-1] == "preserved", armed
    assert seen == [True], seen
    _assert_calls(calls, rounds)
    restart = site == "active-cycle-restart-save"
    state = _assert_wiped(root, fixpoint="CYCLE_RESTART" if restart else "RESET")
    assert state.consecutive_clean_rounds == 0
    if restart:
        # L-B: the log names the action the ledger now records (reset).
        _assert_log_matches_ledger(state, 3)
    follow, calls = _new_machine(root, monkeypatch, rounds=1)
    verdict = follow.run()
    _assert_calls(calls, follow_round)
    state, _ = _state(root)
    assert verdict is not Verdict.PASS and state.converged is False
    assert state.consecutive_clean_rounds == 1
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == follow_round


def test_fix7_failure_record_save_error_never_replaces_primary(offline_workspace, monkeypatch):
    """CR3R I-4: when saving the failure record fails, the original exception still propagates."""
    root = offline_workspace
    _earned_prefix(root, monkeypatch)
    machine, _ = _new_machine(root, monkeypatch, fault="schema")
    save = machine._persist_state
    fired = {"hook": False}

    def hook(_index):
        fired["hook"] = True
        raise RuntimeError("constructed primary failure")

    def failing_save():
        if fired["hook"]:
            raise OSError(5, "constructed failure-record save error")
        return save()

    machine.post_round_hook = hook
    monkeypatch.setattr(machine, "_persist_state", failing_save)
    with pytest.raises(RuntimeError, match="constructed primary failure"):
        machine.run()
    assert any(
        "host attempt save failed after RuntimeError" in error
        and "constructed failure-record save error" in error
        for error in machine._state.infra_errors
    ), machine._state.infra_errors


def _timeout_after_l1(mp, machine):
    """CR2R2-L1 P4: `_check_l1_can_still_converge` raises TimeoutBreaker."""

    def check(*_args, **_kwargs):
        raise TimeoutBreaker("constructed L1 convergence breaker")

    mp.setattr(machine, "_check_l1_can_still_converge", check)


def _raise_after_gate_recorded(mp, machine):
    """CR2R2-L1 P5: the gate records its finding, then the main path raises."""
    record = machine._record_receipt_gate_failure

    def recorded(*args, **kwargs):
        record(*args, **kwargs)
        raise RuntimeError("constructed failure after the gate finding")

    mp.setattr(machine, "_record_receipt_gate_failure", recorded)


def _fail_first_save_after_gate_record(mp, machine):
    """J-N1 (b): the gate records `receipt: <error>`, then the state save at b33b72c8 :2289 fails."""
    record = machine._record_receipt_gate_failure
    save = machine._persist_state
    armed = {"recorded": False, "raised": False}

    def recorded(*args, **kwargs):
        armed["recorded"] = True
        return record(*args, **kwargs)

    def failing_save():
        if armed["recorded"] and not armed["raised"]:
            armed["raised"] = True
            raise OSError(5, "constructed state save failure after the gate finding")
        return save()

    mp.setattr(machine, "_record_receipt_gate_failure", recorded)
    mp.setattr(machine, "_persist_state", failing_save)
    return armed


_FIX7_LOGGED_ONCE = {
    "l1-timeout-breaker": (_timeout_after_l1, TimeoutBreaker, "constructed L1 convergence breaker"),
    "gate-recorded-then-runtime-error": (
        _raise_after_gate_recorded,
        RuntimeError,
        "constructed failure after the gate finding",
    ),
    "gate-recorded-then-state-save-oserror": (
        _fail_first_save_after_gate_record,
        OSError,
        "constructed state save failure after the gate finding",
    ),
}


@pytest.mark.parametrize("case", list(_FIX7_LOGGED_ONCE))
def test_fix7_receipt_write_error_logged_once(offline_workspace, monkeypatch, case):
    """CR2R2-L1 / J-N1: a receipt-write error the main path logged is not logged again on failure.

    The gate's `receipt: <error>` entry and the failed path's unprefixed
    `<error>` are the same error: exactly one entry, one CLI line.
    """
    inject, error, match = _FIX7_LOGGED_ONCE[case]
    root = offline_workspace
    _earned_prefix(root, monkeypatch)
    machine, _ = _new_machine(root, monkeypatch, fault="schema")
    with monkeypatch.context() as mp:
        _fail_receipt_writes(mp)
        inject(mp, machine)
        with pytest.raises(error, match=match):
            machine.run()
    state, _ = _state(root)
    assert state.verdict is not Verdict.PASS
    logged = [e for e in state.infra_errors if "receipt write failed" in e]
    assert len(logged) == 1, state.infra_errors
    lines = [
        line for line in cli._format_infra_lines(state).splitlines() if "receipt write failed" in line
    ]
    assert len(lines) == 1, lines


# fix8 (CR3R2-L1): a preserve that an EARLIER attempt settled is not this
# attempt's to demote. Only the attempt that settled it may turn it into a reset.
def _ledger(state):
    return (
        copy.deepcopy(state.round_history),
        state.consecutive_clean_rounds,
        copy.deepcopy(state.earned_clean_window),
    )


def _raise_before_next_begin(error):
    """Ctrl-C (or any error) after round N settled, before round N+1 begins (loop top)."""

    def inject(machine, begin, _index):
        raise error

    return inject


def _reserved_id_in_next_begin(machine, begin, index):
    """The real `_begin_host_attempt` raises its own CorruptedStateError (reserved ID)."""
    return begin(index - 1)


_FIX8_BETWEEN_ROUNDS = {
    "ctrl-c-at-loop-top": (
        _raise_before_next_begin(_AfterRoundInterrupt("constructed interrupt between rounds")),
        _AfterRoundInterrupt,
        "constructed interrupt between rounds",
    ),
    "corrupted-state-in-begin": (
        _reserved_id_in_next_begin,
        CorruptedStateError,
        "host attempt ID is already reserved",
    ),
}


@pytest.mark.parametrize("case", list(_FIX8_BETWEEN_ROUNDS))
def test_fix8_failure_between_rounds_keeps_prior_preserve(offline_workspace, monkeypatch, case):
    """CR3R2-L1: a failure before the next round begins leaves the settled preserve as it was.

    Round 3 settles `preserved` (credit 2, window [1, 2]) and its save
    completes. The failure comes before round 4's attempt is reserved, so
    round 3's row, credit and window stay byte-for-byte what round 3 saved.
    At 311077fb the row became reset with credit 0 and window [].
    """
    inject, error, match = _FIX8_BETWEEN_ROUNDS[case]
    root = offline_workspace
    _earned_prefix(root, monkeypatch)
    machine, calls = _new_machine(root, monkeypatch, fault="coverage", rounds=2)
    begin = machine._begin_host_attempt
    armed = _spy_actions(monkeypatch, machine)
    seen = {}

    def next_begin(index):
        if armed["actions"] != ["preserved"]:
            return begin(index)
        seen["disk"] = _ledger(_state(root)[0])
        seen["memory"] = _ledger(machine._state)
        return inject(machine, begin, index)

    monkeypatch.setattr(machine, "_begin_host_attempt", next_begin)
    with pytest.raises(error, match=match):
        machine.run()
    assert armed["actions"] == ["preserved"], armed
    _assert_calls(calls, [3])
    state, _ = _state(root)
    rows, credit, window = seen["disk"]
    assert rows[-1]["round"] == 2 and rows[-1]["clean_credit_action"] == "preserved"
    assert credit == 2 and [entry["cycle"] for entry in window["cycles"]] == [1, 2]
    assert _ledger(state) == seen["disk"]
    assert _ledger(machine._state) == seen["memory"] == seen["disk"]
    assert state.verdict is Verdict.FAIL and state.converged is False


def _preserved_on_disk(root, monkeypatch):
    _earned_prefix(root, monkeypatch)
    machine, _ = _new_machine(root, monkeypatch, fault="coverage")
    with monkeypatch.context() as mp:
        armed = _spy_actions(mp, machine)
        machine.run()
    assert armed["actions"] == ["preserved"], armed
    state, _ = _state(root)
    assert state.round_history[-1]["clean_credit_action"] == "preserved"
    assert state.consecutive_clean_rounds == 2
    return root / ".code-forge" / "state.json"


def _corrupted_begin(mp, machine):
    def begin(_index):
        raise CorruptedStateError("constructed corrupted state at begin")

    mp.setattr(machine, "_begin_host_attempt", begin)


def _interrupt_receipt_diff(mp, machine):
    def diff(*_args, **_kwargs):
        raise _AfterRoundInterrupt("constructed interrupt in receipt diff")

    mp.setattr(machine, "_receipt_diff", diff)


def _interrupt_preflight(mp, machine):
    def preflight(*_args, **_kwargs):
        raise _AfterRoundInterrupt("constructed interrupt in mutation preflight")

    mp.setattr(machine, "_preflight_mutation_workspace", preflight)


_FIX8_RESUME = {
    # CR2G P12 / joint pre-attempt failure: Ctrl-C in the startup preflight.
    "ctrl-c-in-preflight": (
        _interrupt_preflight,
        _AfterRoundInterrupt,
        "constructed interrupt in mutation preflight",
    ),
    # CoS item (2): CorruptedStateError in `_begin_host_attempt` after resume.
    "corrupted-state-in-begin": (
        _corrupted_begin,
        CorruptedStateError,
        "constructed corrupted state at begin",
    ),
    # CR3 I-6: Ctrl-C in `_receipt_diff` while resuming.
    "ctrl-c-in-receipt-diff": (
        _interrupt_receipt_diff,
        _AfterRoundInterrupt,
        "constructed interrupt in receipt diff",
    ),
}


@pytest.mark.parametrize("case", list(_FIX8_RESUME))
def test_fix8_early_crash_after_resume_changes_nothing(offline_workspace, monkeypatch, case):
    """CR3R2-L1 / I-6: a crash right after resume leaves state.json and memory unchanged.

    At 311077fb state.json stayed the same but the in-memory row became
    reset with credit 0.
    """
    inject, error, match = _FIX8_RESUME[case]
    root = offline_workspace
    path = _preserved_on_disk(root, monkeypatch)
    before = path.read_bytes()
    expected = _ledger(_state(root)[0])
    machine, calls = _new_machine(root, monkeypatch, fault="coverage")
    with monkeypatch.context() as mp:
        inject(mp, machine)
        with pytest.raises(error, match=match):
            machine.run()
    assert calls == []
    assert path.read_bytes() == before
    assert _ledger(_state(root)[0]) == expected
    assert _ledger(machine._state) == expected


def _interrupt_continuation_index(mp, machine):
    def index(*_args, **_kwargs):
        raise _AfterRoundInterrupt("constructed interrupt in continuation index")

    mp.setattr(machine, "_continuation_round_index", index)


def _breaker_in_preflight(mp, machine):
    def preflight(*_args, **_kwargs):
        raise TimeoutBreaker("constructed orphan container")

    mp.setattr(machine, "_preflight_mutation_workspace", preflight)


# fix9 (CR3R3-L1 / joint J-L1): the same instance runs again and fails before its
# first attempt.
_FIX9_SECOND_RUN = {
    "ctrl-c-in-preflight": (_interrupt_preflight, "constructed interrupt in mutation preflight"),
    "ctrl-c-in-receipt-diff": (_interrupt_receipt_diff, "constructed interrupt in receipt diff"),
    "ctrl-c-in-continuation-index": (
        _interrupt_continuation_index,
        "constructed interrupt in continuation index",
    ),
    # joint J-L1 / CR2 E1: the #114 breaker raised from the startup preflight.
    "breaker-in-preflight": (_breaker_in_preflight, "constructed orphan container"),
}


@pytest.mark.parametrize("case", list(_FIX9_SECOND_RUN))
def test_fix9_second_run_early_crash_keeps_prior_preserve(offline_workspace, monkeypatch, case):
    """CR3R3-L1: a second run() on the same instance cannot demote the first run's preserve.

    The first run() settles round 2 `preserved` (credit 2, window [1, 2]) and
    returns. The second run() on the same instance is interrupted before its
    first attempt begins; it may record its FAIL verdict, but round 2's row,
    credit and window reload unchanged. At 072e3bb1 the guard was still 2 from the first
    run(), so the row became reset with credit 0 and window [] on disk.
    """
    inject, match = _FIX9_SECOND_RUN[case]
    root = offline_workspace
    _earned_prefix(root, monkeypatch)
    machine, calls = _new_machine(root, monkeypatch, fault="coverage")
    with monkeypatch.context() as mp:
        armed = _spy_actions(mp, machine)
        machine.run()
    assert armed["actions"] == ["preserved"], armed
    expected = _ledger(_state(root)[0])
    rows, credit, window = expected
    assert rows[-1]["round"] == 2 and rows[-1]["clean_credit_action"] == "preserved"
    assert credit == 2 and [entry["cycle"] for entry in window["cycles"]] == [1, 2]
    dispatched = len(calls)
    with monkeypatch.context() as mp:
        inject(mp, machine)
        with pytest.raises((_AfterRoundInterrupt, TimeoutBreaker), match=match):
            machine.run()
    assert len(calls) == dispatched
    state, _ = _state(root)
    assert _ledger(state) == expected
    assert _ledger(machine._state) == expected
    assert state.verdict is not Verdict.PASS


_P13 = {
    # CR2F8-L1 P13a: run 1 returns ESCALATED; run 2's preflight gets Ctrl-C.
    "p13a-escalated-then-ctrl-c": ("coverage", Verdict.ESCALATED, _AfterRoundInterrupt),
    # CR2F8-L1 P13b: run 1 returns FAIL at the gate; run 2's preflight raises.
    "p13b-fail-then-runtime-error": ("schema", Verdict.FAIL, RuntimeError),
}


@pytest.mark.parametrize("case", list(_P13))
def test_fix9_p13_second_run_preflight_failure_keeps_preserve(offline_workspace, monkeypatch, case):
    """CR2F8-L1 P13a/P13b: two run() calls on one instance; the second fails at preflight.

    Run 1 settles round 2 `preserved` (credit 2, window [1, 2]) and returns
    normally. Run 2 fails in `_preflight_mutation_workspace`, before its loop.
    Round 2 stays `preserved` with credit 2 and window [1, 2], on disk
    (reload) and in memory. At 072e3bb1 both became reset, credit 0, window [].
    """
    fault, verdict, error = _P13[case]
    root = offline_workspace
    _earned_prefix(root, monkeypatch)
    machine, calls = _new_machine(root, monkeypatch, fault=fault)
    with monkeypatch.context() as mp:
        armed = _spy_actions(mp, machine)
        assert machine.run() is verdict
    assert armed["actions"] == ["preserved"], armed
    expected = _ledger(_state(root)[0])
    rows, credit, window = expected
    assert rows[-1]["round"] == 2 and rows[-1]["clean_credit_action"] == "preserved"
    assert credit == 2 and [entry["cycle"] for entry in window["cycles"]] == [1, 2]
    assert _ledger(machine._state) == expected
    dispatched = len(calls)

    def preflight(*_args, **_kwargs):
        raise error("constructed preflight failure in the second run")

    with monkeypatch.context() as mp:
        mp.setattr(machine, "_preflight_mutation_workspace", preflight)
        with pytest.raises(error, match="constructed preflight failure"):
            machine.run()
    assert len(calls) == dispatched
    assert _ledger(_state(root)[0]) == expected
    assert _ledger(machine._state) == expected


def test_fix9_begin_entry_clears_a_stale_guard(offline_workspace, monkeypatch):
    """`_begin_host_attempt` itself drops a stale guard, whoever calls it.

    The guard is forced to the preserved round, then `_begin_host_attempt`
    is called directly for that round and raises its reserved-ID error
    before any attempt exists. The failed path that follows must not demote
    the row the earlier run settled, in memory or on disk.
    """
    root = offline_workspace
    _earned_prefix(root, monkeypatch)
    machine, _ = _new_machine(root, monkeypatch, fault="coverage")
    with monkeypatch.context() as mp:
        armed = _spy_actions(mp, machine)
        machine.run()
    assert armed["actions"] == ["preserved"], armed
    expected = _ledger(_state(root)[0])
    assert expected[0][-1]["round"] == machine._state.round == 2
    machine._settled_preserve_round = 2
    with pytest.raises(CorruptedStateError, match="host attempt ID is already reserved") as error:
        machine._begin_host_attempt(2)
    machine._finish_failed_host_attempt(error.value)
    assert machine._state.round_history[-1]["clean_credit_action"] == "preserved"
    assert _ledger(_state(root)[0]) == expected
    assert _ledger(machine._state) == expected


def test_fix9_failed_demotion_save_then_second_run_keeps_disk(offline_workspace, monkeypatch):
    """Joint J-N1 / CR2 E2: a demotion whose save failed is not redone by the next run().

    Run 1 settles round 2 `preserved` and its final save completes; then
    Ctrl-C, and the failure path's save of the demotion fails, so disk keeps
    `preserved` (credit 2) while memory holds the reset. The same instance
    runs again and is interrupted before its attempt: disk still reloads
    `preserved` with credit 2 and window [1, 2].
    """
    root = offline_workspace
    _earned_prefix(root, monkeypatch)
    machine, _ = _new_machine(root, monkeypatch, fault="coverage")
    save = machine._persist_state
    armed = {"on": False}

    def interrupt():
        armed["on"] = True
        raise _AfterRoundInterrupt("constructed interrupt after the final save")

    def failing_save():
        if armed["on"]:
            raise OSError(28, "constructed disk full during the demotion save")
        return save()

    with monkeypatch.context() as mp:
        actions = _spy_actions(mp, machine)
        mp.setattr(machine, "_check_mutation_infra_can_still_converge", interrupt)
        mp.setattr(machine, "_persist_state", failing_save)
        with pytest.raises(_AfterRoundInterrupt):
            machine.run()
    assert actions["actions"] == ["preserved"], actions
    assert machine._state.round_history[-1]["clean_credit_action"] == "reset"
    expected = _ledger(_state(root)[0])
    rows, credit, window = expected
    assert rows[-1]["round"] == 2 and rows[-1]["clean_credit_action"] == "preserved"
    assert credit == 2 and [entry["cycle"] for entry in window["cycles"]] == [1, 2]
    with monkeypatch.context() as mp:
        _interrupt_continuation_index(mp, machine)
        with pytest.raises(_AfterRoundInterrupt):
            machine.run()
    assert _ledger(_state(root)[0]) == expected


@pytest.mark.parametrize("other", ["earned", "preserved"])
def test_fix9_row_settled_by_another_instance_is_kept(offline_workspace, monkeypatch, other):
    """Joint J-L1 / J-N1, CR2 E3: A's next run() never touches a row B settled.

    A settles round 2 `preserved`. The ledger is rolled back to the earned
    prefix and B settles round 2 again (`earned`: credit 3, window [1, 2, 3];
    or `preserved`: credit 2, window [1, 2]). A's second run() is
    interrupted before its attempt; B's row, credit and window reload
    unchanged.
    """
    root = offline_workspace
    _earned_prefix(root, monkeypatch)
    forge = root / ".code-forge"
    prefix = (forge / "state.json").read_bytes()
    prefix_files = set(forge.rglob("*"))
    a, _ = _new_machine(root, monkeypatch, fault="coverage")
    with monkeypatch.context() as mp:
        armed = _spy_actions(mp, a)
        a.run()
    assert armed["actions"] == ["preserved"], armed
    (forge / "state.json").write_bytes(prefix)
    for path in sorted(forge.rglob("*"), reverse=True):
        if path not in prefix_files and path.is_file():
            path.unlink()
    b, _ = _new_machine(root, monkeypatch, fault="coverage" if other == "preserved" else None)
    with monkeypatch.context() as mp:
        armed = _spy_actions(mp, b)
        b.run()
    assert armed["actions"] == [other], armed
    expected = _ledger(_state(root)[0])
    rows, credit, window = expected
    assert rows[-1]["round"] == 2 and rows[-1]["clean_credit_action"] == other
    cycles = {"earned": [1, 2, 3], "preserved": [1, 2]}[other]
    assert credit == len(cycles) and [entry["cycle"] for entry in window["cycles"]] == cycles
    with monkeypatch.context() as mp:
        _interrupt_continuation_index(mp, a)
        with pytest.raises(_AfterRoundInterrupt):
            a.run()
    assert _ledger(_state(root)[0]) == expected


def _fail_save_when(mp, machine, predicate):
    """Fail the first state save, after a `preserved` settle, for which `predicate(state)` holds."""
    armed = _spy_actions(mp, machine)
    save = machine._persist_state

    def failing_save():
        if "preserved" in armed["actions"] and not armed["raised"] and predicate(machine._state):
            armed["raised"] = True
            armed["disk_before"] = _ledger(_state(machine.cwd)[0])
            raise OSError(5, "constructed state save failure after preserve")
        return save()

    mp.setattr(machine, "_persist_state", failing_save)
    return armed


def _real_mutation_infra_breaker(mp, machine):
    """CR2G P9 / joint :2733: the real #114 breaker fires after the round's final save."""
    armed = _spy_actions(mp, machine)
    check = machine._check_mutation_infra_can_still_converge

    def breaker():
        if "preserved" in armed["actions"] and not armed["raised"]:
            armed["raised"] = True
            armed["disk_before"] = _ledger(_state(machine.cwd)[0])
            machine._state.rounds_with_mutation_infra = 3
        return check()

    mp.setattr(machine, "_check_mutation_infra_can_still_converge", breaker)
    return armed


def _interrupt_in_hold_check(mp, machine):
    """CR2G P10 as probed (:2407 at 311077fb): Ctrl-C in `_should_enter_hold`, same attempt."""
    armed = _spy_actions(mp, machine)

    def hold():
        armed["raised"] = True
        armed["disk_before"] = _ledger(_state(machine.cwd)[0])
        raise _AfterRoundInterrupt("constructed interrupt in hold check")

    mp.setattr(machine, "_should_enter_hold", hold)
    return armed


def _stall_save_fails(mp, machine):
    """Joint J-N1 (:2426 at 311077fb): the stall ESCALATED save fails."""
    mp.setattr(machine, "_stalled_on_identical_round", lambda: True)
    return _fail_save_when(mp, machine, lambda st: st.verdict is Verdict.ESCALATED)


def _max_rounds_save_fails(mp, machine):
    """Joint J-N1 (:2434 at 311077fb): the MAX_TOTAL_ROUNDS ESCALATED save fails."""
    mp.setattr(machine, "_stalled_on_identical_round", lambda: False)
    return _fail_save_when(
        mp,
        machine,
        lambda st: (
            st.verdict is Verdict.ESCALATED and st.infra_errors[-1].startswith("ESCALATED category=")
        ),
    )


_FIX8_SAME_ATTEMPT = {
    "mutation-infra-breaker": (
        _real_mutation_infra_breaker,
        TimeoutBreaker,
        "mutation infrastructure error in 3 consecutive rounds",
    ),
    "ctrl-c-in-hold-check": (
        _interrupt_in_hold_check,
        _AfterRoundInterrupt,
        "constructed interrupt in hold check",
    ),
    "stall-escalated-save": (_stall_save_fails, OSError, "after preserve"),
    "max-rounds-escalated-save": (_max_rounds_save_fails, OSError, "after preserve"),
}


@pytest.mark.parametrize("case", list(_FIX8_SAME_ATTEMPT))
def test_fix8_same_attempt_failure_after_saved_preserve_resets(offline_workspace, monkeypatch, case):
    """Ruling A / CR2F7-L1 P9 / joint J-N1: the attempt that settled the preserve keeps no credit.

    The failure comes after the round's final save already put `preserved`
    (credit 2) on disk, but in the same attempt: state.json reloads with the
    reset row, credit 0 and window []. Green at 311077fb too (pins scope).
    """
    inject, error, match = _FIX8_SAME_ATTEMPT[case]
    root = offline_workspace
    _earned_prefix(root, monkeypatch)
    machine, calls = _new_machine(root, monkeypatch, fault="coverage")
    with monkeypatch.context() as mp:
        armed = inject(mp, machine)
        with pytest.raises(error, match=match):
            machine.run()
    assert armed["raised"] and armed["actions"] == ["preserved"], armed
    rows, credit, _ = armed["disk_before"]
    assert rows[-1]["clean_credit_action"] == "preserved" and credit == 2
    _assert_calls(calls, [3])
    state = _assert_wiped(root)
    assert state.verdict is not Verdict.PASS and state.converged is False


def test_fix8_earned_row_kept_after_post_settle_failure(offline_workspace, monkeypatch):
    """CR2F7-L2 P11: an earned row is unchanged by a failure after its settle, as on main.

    Only preserved rows are demoted; the earned row, credit 3 and window
    [1, 2, 3] reach disk with verdict FAIL.
    """
    root = offline_workspace
    _earned_prefix(root, monkeypatch)
    machine, calls = _new_machine(root, monkeypatch)
    with monkeypatch.context() as mp:
        armed = _spy_actions(mp, machine)
        save = machine._persist_state

        def failing_save():
            if "earned" in armed["actions"] and not armed["raised"]:
                armed["raised"] = True
                raise OSError(5, "constructed state save failure after earn")
            return save()

        mp.setattr(machine, "_persist_state", failing_save)
        with pytest.raises(OSError, match="after earn"):
            machine.run()
    assert armed["raised"] and armed["actions"] == ["earned"], armed
    _assert_calls(calls, [3])
    state, _ = _state(root)
    assert state.round_history[-1]["clean_credit_action"] == "earned"
    assert state.round_history[-1]["clean_rounds_after"] == 3
    assert state.consecutive_clean_rounds == 3
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == [1, 2, 3]
    assert state.verdict is Verdict.FAIL


def test_fix8_demotion_always_reaches_disk(offline_workspace, monkeypatch):
    """CR2G P12: a demotion is saved even when the failing attempt shows no observation.

    fix9 (joint J-N2): the failure is inside the attempt that settled the
    preserve, after the round's final save put `preserved` on disk. The
    attempt's observations are dropped first, so only the `not demoted` term
    keeps the failure path from returning before its save. The guard is the
    one this attempt set, not one left over from an earlier run().
    """
    root = offline_workspace
    _earned_prefix(root, monkeypatch)
    machine, _ = _new_machine(root, monkeypatch, fault="coverage")
    seen = {}

    def unobserved_failure():
        seen["disk"] = _ledger(_state(root)[0])
        seen["guard"] = machine._settled_preserve_round
        machine._phase_findings = {}
        machine._phase_status = {name: "not_run" for name in machine._phase_status}
        raise RuntimeError("constructed failure, nothing observed")

    with monkeypatch.context() as mp:
        armed = _spy_actions(mp, machine)
        mp.setattr(machine, "_check_mutation_infra_can_still_converge", unobserved_failure)
        with pytest.raises(RuntimeError, match="nothing observed"):
            machine.run()
    assert armed["actions"] == ["preserved"], armed
    rows, credit, _ = seen["disk"]
    assert rows[-1]["round"] == seen["guard"] == 2
    assert rows[-1]["clean_credit_action"] == "preserved" and credit == 2
    state = _assert_wiped(root)
    assert state.verdict is Verdict.FAIL


def test_fix8_second_run_records_identical_receipt_error(offline_workspace, monkeypatch):
    """CR2G I4: bookings are per attempt; a second run() logs its own identical error once.

    The first run books the error on the L1 breaker. The second run on the
    same instance hits the same write error and fails after the round; its
    error is still recorded, exactly once more.
    """
    root = offline_workspace
    _earned_prefix(root, monkeypatch)
    machine, _ = _new_machine(root, monkeypatch, fault="schema")
    with monkeypatch.context() as mp:
        _fail_receipt_writes(mp)
        _timeout_after_l1(mp, machine)
        with pytest.raises(TimeoutBreaker, match="constructed L1 convergence breaker"):
            machine.run()
    first = [e for e in _state(root)[0].infra_errors if "receipt write failed" in e]
    assert len(first) == 1, first
    with monkeypatch.context() as mp:
        _fail_receipt_writes(mp)
        _raise_in_hook(machine, RuntimeError("constructed post-round failure"))
        with pytest.raises(RuntimeError, match="constructed post-round failure"):
            machine.run()
    second = [e for e in _state(root)[0].infra_errors if "receipt write failed" in e]
    assert second[: len(first)] == first
    assert len(second) == len(first) + 1, second


def _secondary_interrupt_before_demotion(mp, machine):
    """Ctrl-C after the final save; a second Ctrl-C hits the failed path before its demotion."""
    real = machine_mod.is_host_round
    flag = {"armed": False, "fired": False}

    def interrupt():
        flag["armed"] = True
        raise _AfterRoundInterrupt("constructed primary Ctrl-C after the final save")

    def host_round(row):
        if flag["armed"] and not flag["fired"]:
            flag["fired"] = True
            raise KeyboardInterrupt("constructed second Ctrl-C inside the failed path")
        return real(row)

    mp.setattr(machine, "_check_mutation_infra_can_still_converge", interrupt)
    mp.setattr(machine_mod, "is_host_round", host_round)
    return flag


def test_f9_secondary_interrupt_before_demotion_then_rerun(offline_workspace, monkeypatch):
    """Joint J-N1: run 1's failed path never reaches its demotion, so its guard stays set.

    Run 1 settles round 2 `preserved`, then fails after the final save; a
    second Ctrl-C inside the failed path stops it before the demotion, so the
    failed attempt's `preserved` row (credit 2) stays on disk (pre-existing,
    documented). A second run() on the same instance then fails before its
    first attempt; only the run() entry reset keeps it from demoting that row.
    """
    root = offline_workspace
    _earned_prefix(root, monkeypatch)
    machine, _ = _new_machine(root, monkeypatch, fault="coverage")
    with monkeypatch.context() as mp:
        armed = _spy_actions(mp, machine)
        flag = _secondary_interrupt_before_demotion(mp, machine)
        with pytest.raises(_AfterRoundInterrupt):
            machine.run()
    assert armed["actions"] == ["preserved"], armed
    assert flag["fired"]
    expected = _ledger(_state(root)[0])
    rows, credit, _ = expected
    assert rows[-1]["clean_credit_action"] == "preserved" and credit == 2
    with monkeypatch.context() as mp:
        _interrupt_continuation_index(mp, machine)
        with pytest.raises(_AfterRoundInterrupt, match="continuation index"):
            machine.run()
    assert _ledger(_state(root)[0]) == expected
    assert _ledger(machine._state) == expected


def _run1_receipt_error_then_hook(mp, machine):
    _fail_receipt_writes(mp)
    _raise_in_hook(machine, RuntimeError("constructed post-round failure"))
    return RuntimeError


def _run1_preserved(mp, machine):
    return None


def _run1_secondary_interrupt(mp, machine):
    _secondary_interrupt_before_demotion(mp, machine)
    return _AfterRoundInterrupt


# fix10 (CR2F9-L1): what the first run() on an instance leaves in memory.
_FIX10_RUN1 = {
    # P21: a receipt-write error the failed path booked, then the run raised.
    "receipt-error-then-hook": ("schema", _run1_receipt_error_then_hook),
    # P13: round 2 settled `preserved`; run() returned ESCALATED.
    "preserved-returned": ("coverage", _run1_preserved),
    # J-N1: the failed path stopped before its demotion.
    "secondary-interrupt": ("coverage", _run1_secondary_interrupt),
}

_FIX10_RUN2 = {
    "preflight": _interrupt_preflight,
    "continuation-index": _interrupt_continuation_index,
}


def _fix10_run1(root, monkeypatch, case):
    fault, inject = _FIX10_RUN1[case]
    _earned_prefix(root, monkeypatch)
    machine, _ = _new_machine(root, monkeypatch, fault=fault)
    with monkeypatch.context() as mp:
        error = inject(mp, machine)
        if error is None:
            machine.run()
        else:
            with pytest.raises(error):
                machine.run()
    return machine


def _fix10_run2(mp, machine, second):
    _FIX10_RUN2[second](mp, machine)
    with pytest.raises(_AfterRoundInterrupt):
        machine.run()


@pytest.mark.parametrize("second", list(_FIX10_RUN2))
@pytest.mark.parametrize("case", ["receipt-error-then-hook", "preserved-returned"])
def test_fix10_second_run_early_failure_records_nothing_twice(
    offline_workspace, monkeypatch, case, second
):
    """CR2F9-L1 P21 / P13: a second run() failing before its first attempt adds nothing.

    Run 1 either logs one receipt-write error and raises (P21) or settles
    round 2 `preserved` and returns (P13). Run 2 on the same instance fails
    before its first attempt. Each receipt-write error is still on disk
    exactly once, and the ledger and verdict reload unchanged. At 23dfad66
    run 1's phase findings and receipt-write errors were still in memory, so
    run 2 logged the error a second time and saved a FAIL verdict.
    """
    root = offline_workspace
    machine = _fix10_run1(root, monkeypatch, case)
    before, _ = _state(root)
    logged = [e for e in before.infra_errors if "receipt write failed" in e]
    assert len(logged) == (1 if case == "receipt-error-then-hook" else 0), logged
    with monkeypatch.context() as mp:
        _fix10_run2(mp, machine, second)
    after, _ = _state(root)
    assert [e for e in after.infra_errors if "receipt write failed" in e] == logged
    assert after.infra_errors == before.infra_errors
    assert _ledger(after) == _ledger(before)
    assert after.verdict is before.verdict


@pytest.mark.parametrize("second", list(_FIX10_RUN2))
@pytest.mark.parametrize("case", list(_FIX10_RUN1))
def test_fix10_second_run_saves_what_a_new_instance_saves(offline_workspace, monkeypatch, case, second):
    """CR2F9-L1, generic: no per-run state of run 1 reaches run 2's saved state.

    After run 1, the same failure before the first attempt is run twice from
    the same disk: once by run 1's instance, once by a new instance. Both
    must leave the same state.json, byte for byte.
    """
    root = offline_workspace
    machine = _fix10_run1(root, monkeypatch, case)
    path = root / ".code-forge" / "state.json"
    after_run1 = path.read_bytes()
    with monkeypatch.context() as mp:
        _fix10_run2(mp, machine, second)
    same_instance = path.read_bytes()
    path.write_bytes(after_run1)
    fresh, _ = _new_machine(root, monkeypatch, fault=_FIX10_RUN1[case][0])
    with monkeypatch.context() as mp:
        _fix10_run2(mp, fresh, second)
    assert same_instance == path.read_bytes()


# fix11: run 1 stops after its round began; run 2 on the same instance then
# completes a round. Both runs see deterministic usage and wall time, so the
# saved state of run 2 can be compared with a new instance's, byte for byte.
_FIX11_USAGE = llm_invoke.Usage(11, 7, 3)


def _metered(mp, start):
    """Fixed reviewer usage, 1.5 s per L1 phase, and receipt stamps one second apart from `start`."""
    transport = llm_invoke._invoke_api

    def metered(*args, **kwargs):
        return dataclasses.replace(transport(*args, **kwargs), usage=_FIX11_USAGE)

    clock = {"now": 0.0, "stamps": 0}

    def monotonic():
        clock["now"] += 0.75
        return clock["now"]

    class Stamp(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            clock["stamps"] += 1
            return cls(2099, 1, 1, tzinfo=tz) + datetime.timedelta(seconds=start + clock["stamps"])

    mp.setattr(llm_invoke, "_invoke_api", metered)
    mp.setattr(
        "code_forge.factories.time", types.SimpleNamespace(monotonic=monotonic, sleep=lambda _s: None)
    )
    mp.setattr(
        "code_forge.receipt.datetime", types.SimpleNamespace(datetime=Stamp, timezone=datetime.timezone)
    )


def _fix11_l1_timeout(mp, machine):
    _timeout_after_l1(mp, machine)
    return TimeoutBreaker


def _fix11_hook(mp, machine):
    def hook(_index):
        raise RuntimeError("constructed post-round failure")

    mp.setattr(machine, "post_round_hook", hook)
    return RuntimeError


def _fix11_receipt_error_then_hook(mp, machine):
    _fail_receipt_writes(mp)
    return _fix11_hook(mp, machine)


_FIX11_RUN1 = {
    # Run 1 is stopped after L1 accumulated its usage, before the round folded it.
    "l1-timeout": ("schema", _fix11_l1_timeout),
    # Run 1 completes its round, then the post-round hook raises.
    "post-round-hook": ("schema", _fix11_hook),
    "receipt-error-then-hook": ("schema", _fix11_receipt_error_then_hook),
    "preserved-returned": ("coverage", _run1_preserved),
    # More run-1 stops, each after L1 ran in run 1's round.
    "workspace-blocker": (
        "schema",
        lambda mp, m: _fix11_raising(mp, m, _workspace_blocker_after_round, TimeoutBreaker),
    ),
    "gate-recorded-then-error": (
        "schema",
        lambda mp, m: _fix11_raising(mp, m, _raise_after_gate_recorded, RuntimeError, True),
    ),
    "state-save-after-publish": (
        "schema",
        lambda mp, m: _fix11_raising(mp, m, _fail_state_save_after_publish, OSError),
    ),
    "crash-before-publish": (
        "schema",
        lambda mp, m: _fix11_raising(mp, m, _crash_before_publish, RuntimeError),
    ),
    "gate-read-failure": ("schema", lambda mp, m: _fix11_raising(mp, m, _raise_in_gate, OSError)),
    "l1-timeout-coverage": ("coverage", _fix11_l1_timeout),
    "preserved-then-save-fails": (
        "coverage",
        lambda mp, m: _fix11_raising(mp, m, _fail_first_save_after_preserve, OSError),
    ),
    "preserved-then-hook": ("coverage", _fix11_hook),
    "hold-check-interrupt": (
        "coverage",
        lambda mp, m: _fix11_raising(mp, m, _interrupt_in_hold_check, _AfterRoundInterrupt),
    ),
    "secondary-interrupt": ("coverage", _run1_secondary_interrupt),
}


def _fix11_raising(mp, machine, inject, error, receipt_error=False):
    if receipt_error:
        _fail_receipt_writes(mp)
    inject(mp, machine)
    return error


def _fix11_round(
    root,
    monkeypatch,
    fault,
    inject=None,
    machine=None,
    rounds=4,
    start=10_000,
    mode=Mode.LOCAL,
    state=None,
):
    """One run() of at most `rounds` rounds.

    A given `machine` is reused, with a new instance's reviewer seam for `fault`.
    A new instance starts from a copy of `state` when one is given: the
    State, then the written and clean receipt cycle windows.
    """
    fresh, _ = _new_machine(root, monkeypatch, fault=fault, rounds=rounds, mode=mode)
    if state is not None:
        fresh._state = copy.deepcopy(state[0])
        fresh._written_cycles, fresh._clean_window_cycles = list(state[1]), list(state[2])
    machine = fresh if machine is None else machine
    machine.max_total_rounds = rounds
    with monkeypatch.context() as mp:
        _metered(mp, start)
        error = None if inject is None else inject(mp, machine)
        if error is None:
            machine.run()
        else:
            with pytest.raises(error):
                machine.run()
    return machine


def _files(root):
    """Every file the review wrote or read in the workspace, by relative path."""
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _restore_files(root, files):
    for path in [p for p in root.rglob("*") if p.is_file()]:
        if str(path.relative_to(root)) not in files:
            path.unlink()
    for name, data in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(data)


@pytest.mark.parametrize("case", list(_FIX11_RUN1))
def test_fix11_second_full_run_saves_what_a_new_instance_saves(offline_workspace, monkeypatch, case):
    """fix11: run 2 on run 1's instance completes its rounds and writes what a new instance writes.

    Run 1 stops after its round began (or returns). From the same files, run
    2 runs once on run 1's instance and once on a new instance, with the same
    reviewer replies, usage and wall time. Every file both leave in the
    workspace (state.json, receipts, ledger) must be byte-identical.
    """
    root = offline_workspace
    fault, inject = _FIX11_RUN1[case]
    _earned_prefix(root, monkeypatch)
    machine = _fix11_round(root, monkeypatch, fault, inject, rounds=1, start=0)
    after_run1 = _files(root)
    _fix11_round(root, monkeypatch, None, machine=machine)
    same_instance = _files(root)
    _restore_files(root, after_run1)
    assert _files(root) == after_run1
    _fix11_round(root, monkeypatch, None)
    fresh = _files(root)
    # Advisories are kept for the life of the instance (machine.py:842-847):
    # the reused instance's file lists run 1's advisories, then run 2's.
    advisories = ".code-forge/advisory-findings.json"
    reused, new = json.loads(same_instance.pop(advisories)), json.loads(fresh.pop(advisories))
    assert reused[len(reused) - len(new) :] == new
    assert {a["id"] for a in reused[: len(reused) - len(new)]} <= {a["id"] for a in new}
    assert sorted(same_instance) == sorted(fresh)
    state = ".code-forge/state.json"
    before, after = json.loads(after_run1[state]), json.loads(fresh[state])
    # Run 2 reviewed at least one more round and paid for it.
    assert after["round"] > before["round"], after["infra_errors"]
    assert after["cost"]["total_input_tokens"] > before["cost"]["total_input_tokens"]
    assert json.loads(same_instance[state]) == after
    assert same_instance == fresh


def test_fix11_second_run_pins_a_gate_edited_after_the_first(offline_workspace, monkeypatch):
    """fix11: each run() pins the untracked gate it starts with.

    Run 1 pins gate.yaml, then fails after its round. The gate is edited
    between runs. Run 2 on the same instance pins the edited gate, as a new
    instance does: no "changed during this review" refusal, same files.
    """
    root = offline_workspace
    _earned_prefix(root, monkeypatch)
    machine = _fix11_round(root, monkeypatch, "schema", _fix11_hook, rounds=1, start=0)
    gate = root / ".code-forge" / "gate.yaml"
    gate.write_text(gate.read_text().replace("timeout_seconds: 1", "timeout_seconds: 2"))
    record = Path(os.environ["XDG_CONFIG_HOME"]) / "code-forge" / "untracked-gates.json"
    after_run1, record_after_run1 = _files(root), record.read_bytes()
    _fix11_round(root, monkeypatch, None, machine=machine)
    same_instance = _files(root)
    _restore_files(root, after_run1)
    record.write_bytes(record_after_run1)
    _fix11_round(root, monkeypatch, None)
    fresh = _files(root)
    state = ".code-forge/state.json"
    reused = json.loads(same_instance[state])
    assert not [e for e in reused["infra_errors"] if "during this review" in e], reused["infra_errors"]
    advisories = ".code-forge/advisory-findings.json"
    new = json.loads(fresh.pop(advisories))
    assert "UNTRACKED_GATE_CHANGED" in {a["id"] for a in new}
    assert json.loads(same_instance.pop(advisories))[-len(new) :] == new
    assert json.loads(same_instance[state]) == json.loads(fresh[state])
    assert same_instance == fresh


_FIX11_CI_RUN1 = {
    "schema": ("schema", None),
    "coverage": ("coverage", None),
    "no-json": ("no-json", None),
    "receipt-error": ("schema", lambda mp, m: _fail_receipt_writes(mp)),
    "falsifier-timeout-then-hook": ("timeout-empty-kind", _fix11_hook),
}


@pytest.mark.parametrize("case", list(_FIX11_CI_RUN1))
def test_fix11_second_ci_run_saves_what_a_new_instance_saves(offline_workspace, monkeypatch, case):
    """fix11: the CI path of the differential above; run 2 has no injected fault.

    Run 2 still FAILs: this workspace is not a git checkout, so its in-tree
    gate.yaml is untracked and CI refuses to measure mutation with it (and the
    carried State keeps run 1's infra errors). The new instance FAILs the same
    way. test_fix12_ci_outside_root_runs_reach_the_measurement below covers a
    run 2 that does reach the measurement.

    CI never reloads state.json, and the in-memory State is not per-run state:
    a reused instance keeps it (main pins this in
    test_mutation_ledger_ci_controls.py). The receipt cycle windows are kept
    for the life of the instance by design (machine.__post_init__). So the
    new instance starts from copies of those three, and everything else must
    match.
    """
    root = offline_workspace
    fault, inject = _FIX11_CI_RUN1[case]
    _earned_prefix(root, monkeypatch)
    machine = _fix11_round(root, monkeypatch, fault, inject, rounds=1, start=0, mode=Mode.CI)
    carried = (machine._state, machine._written_cycles, machine._clean_window_cycles)
    carried = copy.deepcopy(carried)
    after_run1 = _files(root)
    _fix11_round(root, monkeypatch, None, machine=machine, mode=Mode.CI)
    same_instance = _files(root)
    _restore_files(root, after_run1)
    assert _files(root) == after_run1
    _fix11_round(root, monkeypatch, None, mode=Mode.CI, state=carried)
    fresh = _files(root)
    advisories = ".code-forge/advisory-findings.json"
    reused, new = json.loads(same_instance.pop(advisories)), json.loads(fresh.pop(advisories))
    assert reused[len(reused) - len(new) :] == new
    assert set(fresh) - set(after_run1), "run 2 wrote nothing"
    state = ".code-forge/state.json"
    assert json.loads(same_instance[state]) == json.loads(fresh[state])
    assert same_instance == fresh


_FIX12_O2 = "evidence-only attestation requires one cycle"


def test_fix12_ci_outside_root_runs_reach_the_measurement(
    offline_workspace, monkeypatch, tmp_path_factory
):
    """fix12 (J-N2): the CI differential with a trusted gate config, so the runs reach the measurement.

    Both instances read gate.yaml from a plain copy outside the reviewed tree
    (--test-config-root), so CI does not refuse it as untracked. Run 1 measures
    and PASSes. Run 2 on the same instance measures again, then FAILs at
    receipt acceptance: the receipt cycle windows are kept for the life of the
    instance by design, and a second run's evidence-only attestation is
    refused. A reused CI instance therefore cannot reach PASS in run 2. The new
    instance starts from copies of the State and those windows, and must save
    the same files.
    """
    root = offline_workspace
    outside = tmp_path_factory.mktemp("forge-config")
    (outside / ".code-forge").mkdir()
    (outside / ".code-forge" / "gate.yaml").write_bytes(
        (root / ".code-forge" / "gate.yaml").read_bytes()
    )
    measured = []

    def trusted_root(mp, machine):
        machine.test_config_root = outside
        runner = machine.l2_runner

        def l2(*args, **kwargs):
            measured.append(kwargs["baseline_timeout"])
            return runner(*args, **kwargs)

        machine.l2_runner = l2

    _earned_prefix(root, monkeypatch)
    machine = _fix11_round(root, monkeypatch, None, trusted_root, rounds=1, start=0, mode=Mode.CI)
    run1 = json.loads((root / ".code-forge" / "state.json").read_text())
    assert run1["verdict"] == Verdict.PASS.value, run1["infra_errors"]
    assert len(measured) == 1
    carried = copy.deepcopy((machine._state, machine._written_cycles, machine._clean_window_cycles))
    after_run1 = _files(root)
    _fix11_round(root, monkeypatch, None, machine=machine, mode=Mode.CI)
    assert len(measured) == 2  # run 2 reached the measurement
    same_instance = _files(root)
    _restore_files(root, after_run1)
    assert _files(root) == after_run1
    _fix11_round(root, monkeypatch, None, trusted_root, mode=Mode.CI, state=carried)
    assert len(measured) == 3
    fresh = _files(root)
    state = ".code-forge/state.json"
    saved = json.loads(same_instance[state])
    assert saved["verdict"] == Verdict.FAIL.value
    assert [e for e in saved["infra_errors"] if _FIX12_O2 in e], saved["infra_errors"]
    assert not [e for e in saved["infra_errors"] if "untracked gate.yaml" in e], saved["infra_errors"]
    assert set(fresh) - set(after_run1), "run 2 wrote nothing"
    assert saved == json.loads(fresh[state])
    assert same_instance == fresh


# fix12 (J-N6): run 1 double-faults, so its failed path never finishes the host
# attempt and leaves the attempt's round open. Run 2 on the same instance then
# stops before its first attempt.
def _fix12_double_fault(mp, machine, primary):
    """`primary` raises inside the round; a second Ctrl-C hits the failed path's first is_host_round."""
    real = machine_mod.is_host_round
    flag = {"armed": False, "fired": False}
    if primary == "crash":
        _crash_before_publish(mp, machine)
        error = RuntimeError
    else:
        _timeout_after_l1(mp, machine)
        error = TimeoutBreaker
    failed_path = machine._finish_failed_host_attempt

    def finish_failed(primary_error):
        flag["armed"] = True
        return failed_path(primary_error)

    def host_round(row):
        if flag["armed"] and not flag["fired"]:
            flag["fired"] = True
            raise KeyboardInterrupt("constructed second Ctrl-C inside the failed path")
        return real(row)

    mp.setattr(machine, "_finish_failed_host_attempt", finish_failed)
    mp.setattr(machine_mod, "is_host_round", host_round)
    return error, flag


@pytest.mark.parametrize("primary", ["crash", "l1-timeout"])
def test_fix12_double_fault_then_early_second_run_saves_nothing(offline_workspace, monkeypatch, primary):
    """fix12 (J-N6): a second run() that stops before its first attempt does not finish run 1's attempt.

    Run 1 fails in round 2, and a second Ctrl-C inside its failed path stops it
    before it finishes the host attempt, so round 2's row stays open on disk.
    Run 2 on the same instance is interrupted at `_receipt_diff`, before its
    first attempt. A new instance from the same files saves nothing here; the
    reused instance must not finalize run 1's round-2 row or turn the verdict
    to FAIL. Red before fix10's run() entry reset (23dfad66, main).
    """
    root = offline_workspace
    _earned_prefix(root, monkeypatch)
    machine, _ = _new_machine(root, monkeypatch, fault="schema")
    with monkeypatch.context() as mp:
        error, flag = _fix12_double_fault(mp, machine, primary)
        with pytest.raises(error):
            machine.run()
    assert flag["fired"]
    path = root / ".code-forge" / "state.json"
    after_run1 = path.read_bytes()
    with monkeypatch.context() as mp:
        _interrupt_receipt_diff(mp, machine)
        with pytest.raises(_AfterRoundInterrupt):
            machine.run()
    same_instance = path.read_bytes()
    path.write_bytes(after_run1)
    fresh, _ = _new_machine(root, monkeypatch, fault="schema")
    with monkeypatch.context() as mp:
        _interrupt_receipt_diff(mp, fresh)
        with pytest.raises(_AfterRoundInterrupt):
            fresh.run()
    assert path.read_bytes() == after_run1  # the new instance saved nothing
    saved = json.loads(same_instance)
    assert saved["verdict"] == json.loads(after_run1)["verdict"], saved["infra_errors"]
    assert same_instance == after_run1


_FIX12_REFUSED = "CI: trusted gate config refused:"


def test_fix12_reused_ci_instance_drops_a_fixed_trusted_config_refusal(tmp_path, monkeypatch):
    """fix12 (J-N1): run 2 does not keep run 1's refusal of the outside gate root.

    CI run 1 refuses the outside test config root (its gate.yaml is a hard
    link, st_nlink > 1) and FAILs with the refusal. The operator replaces the
    link with a plain copy. Run 2 on the same instance must save what a new
    instance saves. As in the CI differential above, the new instance starts
    from copies of the State and the receipt cycle windows (instance-lifetime by
    design). The round is stubbed as in main's
    test_ci_refused_trusted_gate_data_fails_even_without_mutation, so the
    verdict comes from the gate data alone. Only run() is called.
    """
    from code_forge import mutation
    from tests import test_trusted_test_config as trusted

    repo, base = trusted._repo(tmp_path, gate_at_base=True)
    outside = tmp_path / "forge-config"
    (outside / ".code-forge").mkdir(parents=True)
    os.link(repo / trusted.GATE, outside / trusted.GATE)  # refused: st_nlink > 1
    monkeypatch.setattr(mutation.shutil, "which", lambda name: None)
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(StateMachine, "_execute_round", lambda self, round_index: None)

    def make():
        machine = trusted._machine(repo, base, mode=Mode.CI)
        machine.test_config_root = outside
        return machine

    state_path = repo / ".code-forge" / "state.json"
    reused = make()
    assert reused.run() == Verdict.FAIL
    run1 = json.loads(state_path.read_text())
    assert any(e.startswith(_FIX12_REFUSED) for e in run1["infra_errors"]), run1["infra_errors"]
    carried = copy.deepcopy((reused._state, reused._written_cycles, reused._clean_window_cycles))
    data = (outside / trusted.GATE).read_bytes()
    (outside / trusted.GATE).unlink()
    (outside / trusted.GATE).write_bytes(data)  # the operator's fix: nlink 1
    files = repo / ".code-forge"
    before = {p: p.read_bytes() for p in files.rglob("*") if p.is_file()}
    second = reused.run()
    saved_reused = json.loads(state_path.read_text())
    for p in [p for p in files.rglob("*") if p.is_file() and p not in before]:
        p.unlink()
    for p, data in before.items():
        p.write_bytes(data)
    fresh = make()
    fresh._state = copy.deepcopy(carried[0])
    fresh._written_cycles, fresh._clean_window_cycles = list(carried[1]), list(carried[2])
    new = fresh.run()
    saved_new = json.loads(state_path.read_text())
    assert new == Verdict.PASS, saved_new["infra_errors"]
    assert second == new, saved_reused["infra_errors"]
    refused = [e for e in run1["infra_errors"] if e.startswith(_FIX12_REFUSED)]
    # the carried State keeps run 1's entry; run 2 adds no refusal of its own
    assert [e for e in saved_reused["infra_errors"] if e.startswith(_FIX12_REFUSED)] == refused
    assert saved_reused["infra_errors"] == saved_new["infra_errors"]
    assert saved_reused["verdict"] == saved_new["verdict"]


# --- FTD-04 (C1-2g): `_fixpoint_reached` stays side-effect-free -------------
#
# `_fixpoint_without` evaluates the counterfactual by swapping
# `_state.findings` in place and calling `_fixpoint_reached`; the main path,
# the counterfactual and the failed-attempt path all read the same machine.
# These tests snapshot the whole machine (identity and contents, recursively)
# around every real call and require the same result on repeated calls.


def _freeze(value, seen=frozenset()):
    """Comparable deep snapshot: contents plus object identity of containers."""
    if value is None or type(value) in (bool, int, float, str, bytes):
        return value
    if isinstance(value, enum.Enum):
        return ("enum", type(value).__name__, value.value)
    if isinstance(value, Path):
        return ("path", str(value))
    if id(value) in seen:
        return ("cycle", id(value))
    if isinstance(value, (type, types.ModuleType, types.FunctionType, types.BuiltinFunctionType)):
        return ("ref", id(value))
    if isinstance(value, types.MethodType):
        return ("method", value.__func__.__qualname__, id(value.__self__))
    inner = seen | {id(value)}
    if isinstance(value, dict):
        items = tuple((_freeze(key, inner), _freeze(item, inner)) for key, item in value.items())
        return ("dict", type(value).__name__, id(value), items)
    if isinstance(value, (set, frozenset)):
        members = tuple(sorted(repr(_freeze(item, inner)) for item in value))
        return ("set", type(value).__name__, id(value), members)
    if isinstance(value, (list, tuple)):
        return ("seq", type(value).__name__, id(value), tuple(_freeze(item, inner) for item in value))
    if dataclasses.is_dataclass(value):
        fields = tuple(
            (field.name, _freeze(getattr(value, field.name), inner))
            for field in dataclasses.fields(value)
        )
        return ("dataclass", type(value).__name__, id(value), fields)
    attributes = getattr(value, "__dict__", None)
    if type(attributes) is dict:
        return ("object", type(value).__name__, id(value), _freeze(attributes, inner))
    return ("opaque", type(value).__name__, id(value))


def _machine_snapshot(machine):
    """Every instance attribute except the guard itself, keyed by name."""
    return {name: _freeze(value) for name, value in vars(machine).items() if name != "_fixpoint_reached"}


def _guard_fixpoint(monkeypatch, machine):
    """Wrap the real `_fixpoint_reached`; record call site, result and any side effect.

    Failures are recorded, not raised: `_finish_failed_host_attempt` swallows
    secondary exceptions, so an assertion raised there would be lost.
    """
    original = machine._fixpoint_reached
    record = {"sites": [], "results": [], "violations": []}

    def guarded():
        site = sys._getframe(1).f_code.co_name
        before = _machine_snapshot(machine)
        first = original()
        repeats = [original(), original()]
        after = _machine_snapshot(machine)
        record["sites"].append(site)
        record["results"].append(first)
        if after != before:
            changed = sorted(
                name for name in before.keys() | after.keys() if before.get(name) != after.get(name)
            )
            record["violations"].append((site, "machine state changed", changed))
        if any(result is not first for result in repeats):
            record["violations"].append((site, "not idempotent", [first, *repeats]))
        return first

    monkeypatch.setattr(machine, "_fixpoint_reached", guarded)
    return record


def _fixpoint_case_earned(root, monkeypatch):
    machine, calls = _new_machine(root, monkeypatch, rounds=2)
    record = _guard_fixpoint(monkeypatch, machine)
    assert machine.run() is Verdict.ESCALATED
    _assert_calls(calls, [1, 2])
    return record, {"_run_local"}


def _fixpoint_case_c3_companion(root, monkeypatch):
    _earned_prefix(root, monkeypatch)
    machine, calls = _new_machine(root, monkeypatch, fault="schema-findings")
    record = _guard_fixpoint(monkeypatch, machine)
    assert machine.run() is not Verdict.PASS
    _assert_calls(calls, [3])
    assert _state(root)[0].round_history[-1]["clean_credit_action"] == "preserved"
    return record, {"_run_local", "_fixpoint_without"}


def _fixpoint_case_p3_coverage(root, monkeypatch):
    _p3_prefix(root, monkeypatch)
    machine, calls = _new_machine(
        root, monkeypatch, fault="coverage", mixed_product=True, product_severity="P3"
    )
    record = _guard_fixpoint(monkeypatch, machine)
    assert machine.run() is Verdict.ESCALATED
    _assert_calls(calls, [4])
    assert _state(root)[0].round_history[-1]["clean_credit_action"] == "preserved"
    return record, {"_run_local", "_fixpoint_without"}


def _fixpoint_case_failed_attempt(root, monkeypatch):
    _earned_prefix(root, monkeypatch)
    machine, calls = _new_machine(root, monkeypatch, fault="schema")

    def crash(*_args, **_kwargs):
        raise RuntimeError("constructed L2 crash")

    monkeypatch.setattr(machine, "_run_l2_phase", crash)
    record = _guard_fixpoint(monkeypatch, machine)
    with pytest.raises(RuntimeError, match="constructed L2 crash"):
        machine.run()
    _assert_calls(calls, [3])
    return record, {"_finish_failed_host_attempt"}


@pytest.mark.parametrize(
    "case",
    [
        _fixpoint_case_earned,
        _fixpoint_case_c3_companion,
        _fixpoint_case_p3_coverage,
        _fixpoint_case_failed_attempt,
    ],
    ids=["earned-clean", "c3-companion-counterfactual", "recurring-p3-coverage", "failed-attempt"],
)
def test_ftd04_fixpoint_reached_has_no_side_effects_on_real_runs(offline_workspace, monkeypatch, case):
    """FTD-04 / C1-2g: every real call leaves the whole machine unchanged and repeats its result."""
    record, sites = case(offline_workspace, monkeypatch)
    assert record["violations"] == []
    # Non-vacuous: the guard saw each call site this case is meant to exercise.
    assert sites <= set(record["sites"]), record["sites"]


def test_ftd04_counterfactual_restores_the_same_findings_list(offline_workspace, monkeypatch):
    """FTD-04: `_fixpoint_without` hands back the identical list object, contents untouched."""
    root = offline_workspace
    _earned_prefix(root, monkeypatch)
    machine, _ = _new_machine(root, monkeypatch, fault="schema-findings")
    assert machine.run() is not Verdict.PASS
    findings = machine._state.findings
    excluded = findings[:1]
    assert excluded and len(findings) > 1
    before = _machine_snapshot(machine)
    results = [machine._fixpoint_without(excluded) for _ in range(3)]
    assert machine._state.findings is findings
    assert _machine_snapshot(machine) == before
    assert len(set(results)) == 1
    # And the direct call agrees with itself before and after the counterfactual.
    direct = machine._fixpoint_reached()
    assert machine._fixpoint_reached() is direct and _machine_snapshot(machine) == before


# --- FTD-05 (C1-2f): run-level P3 density/distinct negatives ---------------


def _p3_prefix_thresholds(root, monkeypatch, *, density, distinct_per_file):
    """A recurring P3 product finding earns cycles 2-3 under the given thresholds."""
    monkeypatch.setattr("code_forge.flow_contract.P3_DENSITY_THRESHOLD", density)
    monkeypatch.setattr("code_forge.flow_contract.P3_DISTINCT_PER_FILE_THRESHOLD", distinct_per_file)
    machine, calls = _new_machine(
        root,
        monkeypatch,
        rounds=3,
        mixed_product=True,
        product_severity="P3",
    )
    machine.run()
    _assert_calls(calls, [1, 2, 3])
    state, _ = _state(root)
    assert [row["clean_credit_action"] for row in state.round_history] == ["reset", "earned", "earned"]
    proof = copy.deepcopy(state.earned_clean_window)
    assert [entry["cycle"] for entry in proof["cycles"]] == [2, 3]
    product = [fp for fp, disp in state.round_history[-1]["dispositions"].items() if disp == "CONFIRMED"]
    assert len(product) == 1
    return proof, product


def _coverage_round_with_counterfactual(root, monkeypatch):
    """Cycle 4: the same recurring P3 plus a new incomplete-coverage finding."""
    machine, calls = _new_machine(
        root, monkeypatch, fault="coverage", mixed_product=True, product_severity="P3"
    )
    without = machine._fixpoint_without
    counterfactuals = []

    def spy(excluded):
        result = without(excluded)
        counterfactuals.append(result)
        return result

    monkeypatch.setattr(machine, "_fixpoint_without", spy)
    assert machine.run() is Verdict.ESCALATED
    _assert_calls(calls, [4])
    state, _ = _state(root)
    return state, counterfactuals


def _assert_round_four(state, product, proof, over):
    latest = state.round_history[-1]
    assert all(latest["dispositions"][fp] == "CONFIRMED" for fp in product)
    assert latest["dispositions"]["incomplete-coverage-adversarial"] == "CONFIRMED"
    assert latest["fixpoint"] == "RESET"  # the new coverage finding is clause (a)
    if over:
        assert latest["clean_credit_action"] == "reset" and latest["clean_rounds_after"] == 0
        assert state.consecutive_clean_rounds == 0 and state.earned_clean_window["cycles"] == []
        assert not any("clean credit preserved" in err for err in state.infra_errors)
    else:
        assert latest["clean_credit_action"] == "preserved" and latest["clean_rounds_after"] == 2
        assert state.consecutive_clean_rounds == 2 and state.earned_clean_window == proof


@pytest.mark.parametrize(
    "over", [False, True], ids=["within-threshold-preserves", "over-threshold-resets"]
)
def test_ftd05_run_level_p3_density_over_threshold_resets(offline_workspace, monkeypatch, over):
    """FTD-05 / C1-2f: a recurring P3 over the production density threshold beside
    incomplete-coverage is not (iii)-only, so the earned window is wiped (no companion)."""
    from code_forge import flow_contract
    from code_forge.diff import count_diff_lines

    production = flow_contract.P3_DENSITY_THRESHOLD
    density = 1 / max(1, count_diff_lines(DIFF))
    assert production < density <= 1.0  # one P3 on this diff is over production, under 1.0
    root = offline_workspace
    proof, product = _p3_prefix_thresholds(
        root,
        monkeypatch,
        density=1.0,
        distinct_per_file=flow_contract.P3_DISTINCT_PER_FILE_THRESHOLD,
    )
    if over:
        monkeypatch.setattr("code_forge.flow_contract.P3_DENSITY_THRESHOLD", production)
    state, counterfactuals = _coverage_round_with_counterfactual(root, monkeypatch)
    # The cause is the density rule: with coverage removed, the fixpoint is
    # CYCLE_RESTART over the threshold and CLEAN within it.
    assert counterfactuals == ["CYCLE_RESTART" if over else "CLEAN"]
    _assert_round_four(state, product, proof, over)


@pytest.mark.parametrize(
    "over", [False, True], ids=["within-threshold-preserves", "over-threshold-resets"]
)
def test_ftd05_run_level_p3_distinct_over_threshold_resets(offline_workspace, monkeypatch, over):
    """FTD-05 / C1-2f: a recurring P3 whose file exceeds the distinct-per-file
    threshold beside incomplete-coverage is not (iii)-only, so the window is wiped.

    The production threshold (5 distinct rules in one file) cannot be reached on
    this fixture: L1 fingerprints are (file, 10-line bucket, pass), the fixture
    files are three lines long and only qodo emits the product finding, so one
    file carries at most one recurring product rule. The run therefore moves the
    threshold below that single rule (1 > 0) for cycle 4 only; density is
    switched off throughout so only the distinct rule decides.

    Division of proof (CoS ruling 2026-10-30): the production threshold values
    are pinned by the `_p3_over_threshold` unit negatives in
    test_phase6_schema_coverage_credit.py; this integration test proves that
    the distinct-per-file rule, once it fires, drives the real run to the
    reset (a mutant removing that rule from `_fixpoint_reached` is killed
    here). The per-diff rule is reachable only through more files than this
    fixture has and is not separately run here.
    """
    root = offline_workspace
    proof, product = _p3_prefix_thresholds(root, monkeypatch, density=100.0, distinct_per_file=1)
    if over:
        monkeypatch.setattr("code_forge.flow_contract.P3_DISTINCT_PER_FILE_THRESHOLD", 0)
    state, counterfactuals = _coverage_round_with_counterfactual(root, monkeypatch)
    assert counterfactuals == ["CYCLE_RESTART" if over else "CLEAN"]
    _assert_round_four(state, product, proof, over)


# --- FTD-05 (C1-2f): L-A inadmissible-dict pin on the real path -------------


@pytest.mark.parametrize(
    "io_error", [False, True], ids=["audit-unavailable", "audit-unavailable-plus-io"]
)
def test_ftd05_la_inadmissible_dict_beside_attempted_schema_wipes_on_real_path(
    offline_workspace, monkeypatch, io_error
):
    """FTD-05 / C1-2f: the L-A pin without injected gate errors.

    The adversarial reply is an admissible schema-invalid dict (attempted
    payload); the qodo reply is a schema-invalid dict the audit writer cannot
    encode, so the real factory marks qodo "audit payload unavailable". The
    I/O variant makes the real receipt writes fail: the receipts directory is
    read-only for this round. Both are (iii)-only by finding, so the predicate
    alone would preserve; the real gate damage decides the M6 wipe. The older
    monkeypatched pin (test_la_audit_unavailable_beside_attempted_schema_wipes)
    stays as the narrower gate-contract control.
    """
    root = offline_workspace
    _, original = _earned_prefix(root, monkeypatch)
    receipts = root / ".code-forge" / "receipts"
    machine, calls = _new_machine(root, monkeypatch, fault="schema", qodo_fault="inadmissible-dict")
    predicate = machine._schema_coverage_credit_preserve
    decisions = []

    def spy(fixpoint):
        decision = predicate(fixpoint)
        decisions.append(decision)
        return decision

    monkeypatch.setattr(machine, "_schema_coverage_credit_preserve", spy)
    mode = receipts.stat().st_mode
    if io_error:
        receipts.chmod(0o555)  # real EACCES on this round's receipt writes
    try:
        assert machine.run() is Verdict.FAIL
        errors = machine._receipt_gate_round_errors()
    finally:
        receipts.chmod(mode)
    _assert_calls(calls, [3])
    assert decisions == [True]
    # Outcome first, so a lost audit-unavailable signal fails on the wipe itself.
    state = _assert_wiped(root)
    latest = state.round_history[-1]
    assert {"schema-fail-qodo", "schema-fail-adversarial"} <= set(latest["dispositions"])
    assert not any("clean credit preserved" in err for err in state.infra_errors)
    assert machine._unavailable_rejected_passes_last_round == {"qodo"}
    assert [item["pass_name"] for item in machine._attempted_last_round] == ["adversarial"]
    assert machine_mod._ATTEMPTED_EVIDENCE_GATE_ERROR in errors and _AUDIT_UNAVAILABLE in errors
    assert any(err.startswith("receipt write failed: ") for err in errors) is io_error
    _recover(root, monkeypatch, original, [4, 5, 6], rounds=3)
