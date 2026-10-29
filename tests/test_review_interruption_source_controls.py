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
import hashlib
import io
import json
import sys
import threading
from collections import Counter
from pathlib import Path

import pytest

from code_forge import cli, llm_invoke, verify
from code_forge.autofix import NoChangeAutoFixer
from code_forge.backend import BackendConfig
from code_forge.baseline import ResolvedReview
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
