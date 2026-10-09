"""Provider capacity stays incomplete without appearing as a product defect."""

import contextlib
import copy
import io
import json
from pathlib import Path

import pytest

from code_forge import cli, llm_invoke, verify
from code_forge.autofix import NoChangeAutoFixer
from code_forge.backend import BackendConfig
from code_forge.baseline import ResolvedReview
from code_forge.factories import build_grouped_l1_provider, build_l1_provider
from code_forge.falsify import StubFalsifier
from code_forge.outlet_c import run_outlet_c
from code_forge.sarif import format_summary
from code_forge.state import Disposition, StateFinding, Verdict, load_state
from tests.test_resume_receipt_provenance import (
    CONTENT, DIFF, SOURCE_HASH, VALID, _machine, _receipts, _verify, review_workspace as review_workspace,
)


def _capacity(machine, prompt):
    if machine._state.round == 2 and "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
        return llm_invoke.LLMInvokeError(
            "provider output cap", kind="truncated", retryable=False,
            duration_s=0.125, usage=llm_invoke.Usage(17, 19, 3),
        )
    return VALID


def _reported(root):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        cli._emit_ci_output(root / ".code-forge/state.json", {})
    return stderr.getvalue(), json.loads(stdout.getvalue())["runs"][0]


@pytest.mark.parametrize("shape", ["parallel", "serial", "grouped"])
def test_capacity_public_pipeline_and_recovery(review_workspace, monkeypatch, capsys, shape):
    root = review_workspace
    original_invoke = llm_invoke.llm_invoke
    machine = _machine(root, monkeypatch, payload=_capacity)
    if shape == "serial":
        backend = BackendConfig(name="offline-cli", type="cli", model="offline", command=["unused"])

        def invoke(prompt, **kwargs):
            body = _capacity(machine, prompt)
            if isinstance(body, Exception):
                raise body
            return llm_invoke.LLMResult(copy.deepcopy(body), llm_invoke.Usage(), 0.0)

        monkeypatch.setattr(llm_invoke, "llm_invoke", invoke)
        # The provider captures llm_invoke when constructed.
        machine.l1_provider = build_l1_provider("auto", machine.resolved_review, backend=backend, max_attempts=1)
    elif shape == "grouped":
        machine.l1_provider = build_grouped_l1_provider(
            "auto", [{"name": name, "resolved": machine.resolved_review} for name in ("a", "b")],
            backend=BackendConfig(name="owned-offline", type="api", format="openai", model="offline", base_url="http://127.0.0.1:1"), max_attempts=1,
        )
    assert machine.run() == Verdict.FAIL
    progress = capsys.readouterr().err
    state = load_state(root / ".code-forge/state.json")
    assert state.consecutive_clean_rounds == 2
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == [1, 2]
    proof = copy.deepcopy(state.earned_clean_window["cycles"])
    assert state.round_history[-1]["clean_credit_action"] == "interrupted"
    assert state.round_history[-1]["reset_observed"] is False
    assert machine.active_findings == []
    assert "run done: verdict=FAIL findings=0 confirmed=0" in progress
    summary, report = _reported(root)
    assert "FAIL findings=0 confirmed=0" in summary
    assert "provider_capacity=1" in summary and "capacity_incomplete=1" in summary
    assert "passes=2/3" in summary
    assert report["results"] == []
    diagnostics = report["properties"]["providerDiagnostics"]
    assert {item["diagnostic_kind"] for item in diagnostics} == {"provider-capacity", "capacity-incomplete"}
    provider = next(item for item in diagnostics if item["id"].endswith("invoke-fail"))
    assert provider["provider_failure"]["kind"] == "truncated"
    assert provider["provider_failure"]["duration_s"] == 0.125
    assert provider["provider_failure"]["usage"] == {"input_tokens": 17, "output_tokens": 19, "cached_input_tokens": 3}
    latest = [json.loads(p.read_text()) for p in _receipts(root) if "c3p" in p.name]
    assert [r["pass_status"] for r in latest] == ["completed", "completed", "error"]
    assert not _verify(root).passed
    assert len(state.findings) == 2 and all(f.disposition is Disposition.CONFIRMED for f in state.findings)
    monkeypatch.setattr(llm_invoke, "llm_invoke", original_invoke)
    resumed = _machine(root, monkeypatch, rounds=1)
    assert resumed.run() == Verdict.PASS
    state = load_state(root / ".code-forge/state.json")
    assert state.earned_clean_window["cycles"][:2] == proof
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == [1, 2, 4]
    assert _verify(root).passed


@pytest.mark.parametrize("kind", ["", "conn", "empty"])
def test_text_truncation_without_typed_capacity_stays_hard(review_workspace, monkeypatch, kind):
    machine = _machine(review_workspace, monkeypatch, payload=lambda machine, prompt: llm_invoke.LLMInvokeError("output truncated at provider cap", kind=kind, retryable=False) if machine._state.round == 2 and "adversarial" in prompt.rsplit("You are a ", 1)[-1] else VALID)
    assert machine.run() == Verdict.FAIL
    summary, report = _reported(review_workspace)
    assert "findings=2 confirmed=2" in summary
    assert "provider_capacity=" not in summary
    assert len(report["results"]) == 2
    assert "providerDiagnostics" not in report.get("properties", {})


@pytest.mark.parametrize("collision", [False, True])
def test_real_product_reset_and_collision_stay_visible(review_workspace, monkeypatch, collision):
    machine = _machine(review_workspace, monkeypatch, payload=_capacity)
    machine.autofixer = NoChangeAutoFixer()
    machine.l0_runner = lambda *args: ([StateFinding("actual-product", "invoke-fail-adversarial" if collision else "actual-product", "L0", Disposition.CONFIRMED, "control.ts", [2, 2], "P1: actual product")] if machine._state.round == 2 else [], [])
    assert machine.run() != Verdict.PASS
    assert machine._state.consecutive_clean_rounds == 0
    assert machine._state.earned_clean_window["cycles"] == []
    assert machine._state.round_history[-1]["clean_credit_action"] == "reset"
    assert any(f.id == "actual-product" for f in machine.active_findings)
    summary, report = _reported(review_workspace)
    assert "findings=1 confirmed=1" in summary
    assert [r["message"]["text"] for r in report["results"]] == ["P1: actual product"]


@pytest.mark.parametrize("damage", ["literal", "schema", "hash", "anchor", "missing", "unreadable", "writer", "scope"])
def test_independent_receipt_damage_with_capacity_is_hard(review_workspace, monkeypatch, damage):
    root = review_workspace

    def hook(round_index):
        if round_index != 2 or damage in ("literal", "schema", "writer"):
            return
        path = root / ".code-forge/receipts/receipt-c3p1.json"
        if damage == "missing":
            path.unlink()
        elif damage == "unreadable":
            path.write_text("{")
        else:
            receipt = json.loads(path.read_text())
            if damage == "hash":
                receipt["diff_sha256"] = "wrong"
            elif damage == "anchor":
                receipt["anchors"] = [{"file": "elsewhere.ts", "line": 1}]
            elif damage == "scope":
                receipt["reviewed_repositories"] = {"wrong": "scope"}
            path.write_text(json.dumps(receipt))

    def payload(machine, prompt):
        outcome = _capacity(machine, prompt)
        if machine._state.round == 2 and "structural code reviewer" in prompt and damage in ("literal", "schema"):
            body = copy.deepcopy(VALID)
            if damage == "literal":
                body["code_excerpts"][0]["content"] = CONTENT.replace("value = 2", "value = 200")
            else:
                body["code_excerpts"] = [{"file": "control.ts"}]
            return body
        return outcome

    if damage == "writer":
        from code_forge import receipt as receipt_module
        original = receipt_module.write_receipts

        def writer(**kwargs):
            if kwargs["round_index"] == 2:
                raise OSError("owned receipt write failure")
            return original(**kwargs)

        monkeypatch.setattr(receipt_module, "write_receipts", writer)
    machine = _machine(root, monkeypatch, payload=payload, hook=hook)
    assert machine.run() == Verdict.FAIL
    summary, report = _reported(root)
    assert report["results"], "independent evidence damage vanished"
    assert any(f.id == "RECEIPT_INVALID" for f in machine.active_findings)
    assert "capacity_incomplete=" not in summary
    assert not _verify(root).passed


def test_model_supplied_diagnostic_cannot_hide_product(review_workspace, monkeypatch):
    def payload(machine, prompt):
        result = _capacity(machine, prompt)
        if result is VALID and machine._state.round == 2 and "structural code reviewer" in prompt:
            result = copy.deepcopy(VALID)
            result["findings"] = [{"id": "l1-qodo-invoke-fail", "source": "INFRA", "fingerprint": "invoke-fail-qodo", "file": "control.ts", "line": 2, "severity": "P1", "description": "provider capacity model mimic", "excerpt": "const value = 2;", "diagnostic_kind": "provider-capacity", "provider_failure": {"kind": "truncated"}}]
        return result

    machine = _machine(review_workspace, monkeypatch, payload=payload)
    machine.autofixer = NoChangeAutoFixer()
    assert machine.run() != Verdict.PASS
    summary, report = _reported(review_workspace)
    assert report["results"], summary
    assert any("model mimic" in r["message"]["text"] for r in report["results"])
    assert all(getattr(f, "diagnostic_kind", None) is None for f in machine.active_findings)


@pytest.mark.parametrize("route", ["unchunked", "fallback", "chunked"])
def test_outlet_c_capacity_retains_error_and_reporting(review_workspace, monkeypatch, route):
    root = review_workspace
    monkeypatch.setenv("FORGE_DIFF_CHUNK_THRESHOLD_KB", "100" if route == "unchunked" else "0")
    diff = DIFF
    if route == "fallback":
        diff = "diff --git a/image.png b/image.png\nBinary files a/image.png and b/image.png differ\n"
    elif route == "chunked":
        diff += DIFF.replace("control.ts", "second.ts")
        (root / "second.ts").write_text(CONTENT)
    resolved = ResolvedReview([Path("control.ts")], None, diff, "git")

    def spawn(role, chunk):
        if role == "adversarial":
            raise llm_invoke.LLMInvokeError("typed provider capacity", kind="truncated", retryable=False)
        if route == "fallback":
            return {"findings": [], "code_excerpts": []}
        return {"findings": [], "code_excerpts": [dict(VALID["code_excerpts"][0], file="second.ts" if "second.ts" in chunk else "control.ts")]}

    from code_forge.source import compute_source_hash
    assert run_outlet_c(resolved, compute_source_hash(git_diff=diff), root, spawn, falsifier=StubFalsifier(), max_total_rounds=1) == Verdict.FAIL
    summary, report = _reported(root)
    assert "findings=0 confirmed=0" in summary
    assert report["results"] == []
    assert report["properties"]["providerDiagnostics"]
    records = [json.loads(p.read_text()) for p in _receipts(root)]
    assert [r["pass_status"] for r in records] == ["completed", "completed", "error"]


@pytest.mark.parametrize("usage", [None, llm_invoke.Usage()])
def test_available_zero_and_unavailable_usage_remain_distinct(review_workspace, monkeypatch, usage):
    machine = _machine(review_workspace, monkeypatch, rounds=1, payload=lambda machine, prompt: llm_invoke.LLMInvokeError("capacity", kind="truncated", retryable=False, usage=usage) if "adversarial" in prompt.rsplit("You are a ", 1)[-1] else VALID)
    assert machine.run() == Verdict.FAIL
    _, report = _reported(review_workspace)
    metadata = next(f["provider_failure"] for f in report["properties"]["providerDiagnostics"] if f["id"].endswith("invoke-fail"))
    assert metadata["usage"] == (None if usage is None else {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0})


def test_third_capacity_breaker_truthful_cli(review_workspace, monkeypatch):
    root = review_workspace
    def payload(machine, prompt):
        return (
            llm_invoke.LLMInvokeError("capacity", kind="truncated", retryable=False)
            if "adversarial" in prompt.rsplit("You are a ", 1)[-1] else VALID
        )
    for _ in range(2):
        assert _machine(root, monkeypatch, rounds=1, payload=payload).run() == Verdict.FAIL
    machine = _machine(root, monkeypatch, rounds=1, payload=payload)
    monkeypatch.setattr(cli, "_run", lambda *args, **kwargs: machine.run())
    monkeypatch.setattr("sys.argv", ["code-forge", "review"])
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        assert cli.main() == 6
    state = load_state(root / ".code-forge/state.json")
    assert state.verdict == Verdict.FAIL and state.rounds_with_failed_pass == 3
    assert state.round_history[-1]["phase_status"]["l2"] == "not_run"
    assert state.round_history[-1]["phase_status"]["e2e"] == "not_run"
    assert state.round_history[-1]["phase_status"]["coverage"] == "not_run"
    assert "resets the clean-round counter" not in stderr.getvalue()
    assert "completed perspectives" in stderr.getvalue()
    summary, report = _reported(root)
    assert "FAIL findings=0 confirmed=0" in summary
    assert "passes=2/3" in summary and report["results"] == []


@pytest.mark.parametrize("kind", ["truncated", "conn"])
def test_ci_real_terminal_and_mcp_report_relay(review_workspace, monkeypatch, kind):
    import asyncio
    from code_forge import mcp_server
    from code_forge.state import Mode

    root = review_workspace
    machine = _machine(root, monkeypatch, rounds=1, payload=lambda machine, prompt: llm_invoke.LLMInvokeError("capacity", kind=kind, retryable=False) if "adversarial" in prompt.rsplit("You are a ", 1)[-1] else VALID)
    machine.mode = Mode.CI
    assert machine.run() == Verdict.FAIL
    state = load_state(root / ".code-forge/state.json")
    summary, report = _reported(root)
    assert state.verdict == Verdict.FAIL
    assert [json.loads(p.read_text())["pass_status"] for p in _receipts(root)] == ["completed", "completed", "error"]
    if kind == "truncated":
        assert "FAIL findings=0 confirmed=0" in summary
        assert "provider_capacity=1" in summary and "capacity_incomplete=1" in summary
        assert report["results"] == [] and machine.active_findings == []
    else:
        assert "FAIL findings=2 confirmed=2" in summary
        assert len(report["results"]) == 2
        assert "providerDiagnostics" not in report.get("properties", {})
    actual_stdout = json.dumps({"version": "2.1.0", "runs": [report]})
    monkeypatch.setattr(mcp_server, "get_job", lambda job_id: {"status": "failed", "result": {"stdout": actual_stdout, "stderr": summary, "exit_code": 1, "verdict": "FAIL", "duration_s": 0.0}} if job_id == "owned-capacity" else None)
    response = asyncio.run(mcp_server.forge_job_status("owned-capacity"))
    result = response.structuredContent["result"]
    assert result["exit_code"] == 1 and result["verdict"] == "FAIL"
    assert result["findings_count"] is None
    assert actual_stdout in result["output"] and summary in result["output"]


@pytest.mark.parametrize("damage", ["literal", "hash", "anchor", "schema", "writer"])
def test_ci_capacity_independent_failure_stays_hard(review_workspace, monkeypatch, damage):
    from code_forge.state import Mode
    root = review_workspace

    def payload(machine, prompt):
        if "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("capacity", kind="truncated", retryable=False)
        body = copy.deepcopy(VALID)
        if "structural code reviewer" in prompt and damage in ("literal", "schema"):
            body["code_excerpts"] = [{"file": "control.ts"}] if damage == "schema" else [dict(VALID["code_excerpts"][0], content=CONTENT.replace("value = 2", "value = 200"))]
        return body

    def hook(round_index):
        if damage not in ("hash", "anchor"):
            return
        path = root / ".code-forge/receipts/receipt-c1p1.json"
        receipt = json.loads(path.read_text())
        if damage == "hash":
            receipt["diff_sha256"] = "wrong"
        else:
            receipt["anchors"] = [{"file": "elsewhere.ts", "line": 1}]
        path.write_text(json.dumps(receipt))

    if damage == "writer":
        from code_forge import receipt as receipt_module
        monkeypatch.setattr(receipt_module, "write_receipts", lambda **kwargs: (_ for _ in ()).throw(OSError("write failed")))
    machine = _machine(root, monkeypatch, payload=payload, rounds=1, hook=hook)
    machine.mode = Mode.CI
    assert machine.run() == Verdict.FAIL
    summary, report = _reported(root)
    assert any(f.id == "RECEIPT_INVALID" for f in machine.active_findings)
    assert report["results"]
    assert "capacity_incomplete=" not in summary


@pytest.mark.parametrize("damage", ["enum-string", "source", "file", "fingerprint", "range", "boolean-range", "kind", "disposition"])
def test_diagnostic_projection_rejects_untrusted_structure(review_workspace, monkeypatch, damage):
    from code_forge.state import save_state
    root = review_workspace
    machine = _machine(root, monkeypatch, rounds=1, payload=lambda machine, prompt: llm_invoke.LLMInvokeError("capacity", kind="truncated", retryable=False) if "adversarial" in prompt.rsplit("You are a ", 1)[-1] else VALID)
    machine.run()
    finding = next(f for f in machine._state.findings if f.id.endswith("invoke-fail"))
    if damage == "enum-string":
        finding.diagnostic_kind = "provider-capacity"
    elif damage == "source":
        finding.source = "L0"
    elif damage == "file":
        finding.file = "control.ts"
    elif damage == "fingerprint":
        finding.fingerprint = "actual-product"
    elif damage == "range":
        finding.line_range = [2, 2]
    elif damage == "boolean-range":
        finding.line_range = [False, False]
    elif damage == "kind":
        finding.provider_failure["kind"] = "conn"
    else:
        finding.disposition = Disposition.UNCERTAIN
    assert finding in machine.active_findings
    # Invalid direct enum-like values fail closed before persisted conversion.
    if damage == "enum-string":
        assert "findings=1" in format_summary(machine._state)
    else:
        save_state(machine._state, root / ".code-forge/state.json")
        _, report = _reported(root)
        assert report["results"]


@pytest.mark.parametrize("damage", ["bad-role", "bad-fingerprint", "bad-outcome", "extra-key"])
def test_acquisition_history_marker_validation_remains_closed(review_workspace, monkeypatch, damage):
    from code_forge.errors import CorruptedStateError
    root = review_workspace
    machine = _machine(root, monkeypatch, rounds=1, payload=lambda machine, prompt: llm_invoke.LLMInvokeError("capacity", kind="truncated", retryable=False) if "adversarial" in prompt.rsplit("You are a ", 1)[-1] else VALID)
    machine.run()
    path = root / ".code-forge/state.json"
    state = json.loads(path.read_text())
    marker = state["round_history"][-1]["acquisition_failures"][0]
    if damage == "bad-role":
        marker["pass_name"] = "other"
    elif damage == "bad-fingerprint":
        marker["fingerprint"] = "spoof"
    elif damage == "bad-outcome":
        marker["outcome"] = "completed"
    else:
        marker["diagnostic_kind"] = "provider-capacity"
    path.write_text(json.dumps(state))
    with pytest.raises(CorruptedStateError):
        load_state(path)


def test_independent_unresolved_completed_role_wins_before_incomplete(review_workspace, monkeypatch):
    root = review_workspace
    _machine(root, monkeypatch, rounds=1).run()
    paths = _receipts(root)
    first = json.loads(paths[0].read_text())
    first["findings"] = [{"file": "control.ts", "line": 2, "description": "unverified independent product", "disposition": "CONFIRMED", "basis": {"authority": "infra-unavailable", "manifest_tier": "declared", "exec_evidence": None, "falsification_survived": False}}]
    first["findings_count"] = 1
    paths[0].write_text(json.dumps(first))
    last = json.loads(paths[2].read_text())
    last["pass_status"] = "error"
    paths[2].write_text(json.dumps(last))
    proof, result = verify._capture_earned_cycle(root, SOURCE_HASH, verify.parse_diff_files(DIFF), cycle=1, diff_text=DIFF)
    assert proof is None and not result.passed
    assert result.failure_kind is None and "unresolved unverified product" in result.reason


@pytest.mark.parametrize("order", ["capacity-first", "generic-first", "all-capacity"])
def test_grouped_capacity_and_generic_same_role_deny_companion_exemption(review_workspace, monkeypatch, order):
    root = review_workspace
    machine = _machine(root, monkeypatch)
    calls = []

    def transport(prompt, *args, **kwargs):
        if "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            calls.append(prompt)
            raise llm_invoke.LLMInvokeError(
                f"owned original acquisition {len(calls)}",
                kind=("truncated" if order == "all-capacity" or (len(calls) == 1) == (order == "capacity-first") else "conn"), retryable=False,
            )
        return llm_invoke.LLMResult(copy.deepcopy(VALID), llm_invoke.Usage(), 0.0)

    monkeypatch.setattr(llm_invoke, "_invoke_api", transport)
    machine.l1_provider = build_grouped_l1_provider(
        "auto", [{"name": name, "resolved": machine.resolved_review} for name in ("a", "b")],
        backend=BackendConfig(name="owned-offline", type="api", format="openai", model="offline", base_url="http://127.0.0.1:1"), max_attempts=1,
    )
    assert machine.run() == Verdict.FAIL
    summary, report = _reported(root)
    if order == "all-capacity":
        assert "capacity_incomplete=1" in summary and report["results"] == []
    else:
        assert "capacity_incomplete=" not in summary
        assert any(f.id == "RECEIPT_INVALID" for f in machine.active_findings)
        assert report["results"], "a deduplicated generic acquisition failure borrowed capacity authority"
    raw = json.loads((root / ".code-forge/state.json").read_text())
    original = next(f for f in raw["findings"] if f["id"].endswith("invoke-fail"))
    observations = original["provider_failure"]["observations"]
    assert len(observations) == 2
    assert all("observations" not in item["provider_failure"] for item in observations)
    assert "owned original acquisition 1" in json.dumps(report)
    assert "owned original acquisition 2" in json.dumps(report)



def test_capacity_pause_cannot_lower_new_public_floor(review_workspace, monkeypatch):
    root = review_workspace
    assert _machine(root, monkeypatch, payload=_capacity).run() == Verdict.FAIL
    (root / ".code-forge/gate.yaml").write_text("verify:\n  required_cycles: 4\n")
    assert _machine(root, monkeypatch, rounds=1).run() != Verdict.PASS
    summary, report = _reported(root)
    assert "capacity_incomplete=" not in summary
    result = _verify(root)
    assert not result.passed and "floor demands 4" in result.reason


def test_spawn_real_timeout_and_generic_failure_keep_truthful_outcomes(review_workspace, monkeypatch):
    from code_forge.factories import _L1Call
    from code_forge.outlet_c import _run_chunk
    root = review_workspace
    machine = _machine(root, monkeypatch, rounds=1)

    def spawn(role, diff):
        if role == "adversarial":
            raise llm_invoke.LLMInvokeError("owned timeout", kind="conn", is_timeout=True)
        if role == "qodo":
            raise RuntimeError("generic host spawn")
        return copy.deepcopy(VALID)

    machine.l1_provider = _L1Call(lambda call: _run_chunk(DIFF, spawn, ("qodo", "expert", "adversarial"), attempted=call.attempted_excerpts, rejection_state=call))
    assert machine.run() == Verdict.FAIL
    statuses = [json.loads(p.read_text())["pass_status"] for p in _receipts(root)]
    assert statuses == ["timeout", "completed", "timeout"]
    summary, report = _reported(root)
    assert "provider_capacity=" not in summary and report["results"]


@pytest.mark.parametrize("order", ["capacity-first", "generic-first", "all-capacity", "unexpected-first"])
def test_outlet_c_mixed_chunk_acquisition_is_not_capacity_only(review_workspace, monkeypatch, order):
    root = review_workspace
    monkeypatch.setenv("FORGE_DIFF_CHUNK_THRESHOLD_KB", "0")
    diff = DIFF + DIFF.replace("control.ts", "second.ts")
    (root / "second.ts").write_text(CONTENT)
    resolved = ResolvedReview([Path("control.ts"), Path("second.ts")], None, diff, "git")

    def spawn(role, chunk):
        second = "second.ts" in chunk
        if role == "adversarial":
            if order == "unexpected-first" and not second:
                raise RuntimeError("independent unexpected original")
            kind = "truncated" if order == "all-capacity" or second == (order == "generic-first") else "conn"
            raise llm_invoke.LLMInvokeError("capacity or independent connection", kind=kind, retryable=False)
        return {"findings": [], "code_excerpts": [dict(VALID["code_excerpts"][0], file="second.ts" if second else "control.ts")]}

    from code_forge.source import compute_source_hash
    assert run_outlet_c(resolved, compute_source_hash(git_diff=diff), root, spawn, falsifier=StubFalsifier(), max_total_rounds=1) == Verdict.FAIL
    summary, report = _reported(root)
    if order == "all-capacity":
        assert "capacity_incomplete=1" in summary and report["results"] == []
    else:
        assert "capacity_incomplete=" not in summary and report["results"]
    assert [json.loads(p.read_text())["pass_status"] for p in _receipts(root)] == [
        "completed", "completed", "timeout" if order == "unexpected-first" else "error"
    ]
    if order == "unexpected-first":
        assert "independent unexpected original" in json.dumps(report)


def test_reused_provider_does_not_keep_old_generic_acquisition_authority(review_workspace, monkeypatch):
    root = review_workspace
    machine = _machine(root, monkeypatch, rounds=1)
    provider = machine.l1_provider
    stage = {"kind": "conn"}

    def transport(prompt, *args, **kwargs):
        if "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            raise llm_invoke.LLMInvokeError("current acquisition", kind=stage["kind"], retryable=False)
        return llm_invoke.LLMResult(copy.deepcopy(VALID), llm_invoke.Usage(), 0.0)

    monkeypatch.setattr(llm_invoke, "_invoke_api", transport)
    assert provider()[0]
    stage["kind"] = "truncated"
    assert machine.run() == Verdict.FAIL
    summary, report = _reported(root)
    assert "capacity_incomplete=1" in summary
    assert report["results"] == []


def test_absent_custom_producer_originals_cannot_authorize_companion(review_workspace, monkeypatch):
    from code_forge.state import FindingDiagnosticKind
    root = review_workspace
    machine = _machine(root, monkeypatch, rounds=1)
    finding = StateFinding("l1-adversarial-invoke-fail", "invoke-fail-adversarial", "INFRA", Disposition.CONFIRMED, "<llm-invoke>", [0, 0], "custom capacity", diagnostic_kind=FindingDiagnosticKind.PROVIDER_CAPACITY, provider_failure={"kind": "truncated"})
    machine.l1_provider = lambda: ([finding], [dict(item, pass_name=role) for role in ("qodo", "expert") for item in VALID["code_excerpts"]], llm_invoke.Usage(), 0.0)
    assert machine.run() == Verdict.FAIL
    summary, report = _reported(root)
    assert "capacity_incomplete=" not in summary and report["results"]


@pytest.mark.parametrize("malformed", ["object", "nan", "usage", "dict-subclass"])
def test_malformed_optional_provider_metadata_does_not_execute_callbacks(review_workspace, monkeypatch, malformed):
    class Unsafe:
        def __deepcopy__(self, memo):
            raise AssertionError("arbitrary copy callback")

        def __str__(self):
            raise AssertionError("arbitrary coercion callback")

    class UnsafeDict(dict):
        def get(self, key, default=None):
            raise AssertionError("arbitrary dictionary callback")

    error = llm_invoke.LLMInvokeError("capacity", kind="truncated", retryable=False)
    if malformed == "object":
        error.duration_s = Unsafe()
        error.stderr = Unsafe()
    elif malformed == "nan":
        error.duration_s = float("nan")
    elif malformed == "dict-subclass":
        error.usage = UnsafeDict(input_tokens=1)
    else:
        error.usage = Unsafe()
    # Duration aggregation is a separate unchanged factory contract. Use the
    # public spawn route, whose diagnostics must serialize safely.
    root = review_workspace

    def spawn(role, diff):
        if role == "adversarial":
            raise error
        return copy.deepcopy(VALID)

    assert run_outlet_c(ResolvedReview([Path("control.ts")], None, DIFF, "git"), SOURCE_HASH, root, spawn, falsifier=StubFalsifier(), max_total_rounds=1) == Verdict.FAIL
    summary, report = _reported(root)
    assert "FAIL findings=0 confirmed=0" in summary
    metadata = next(f["provider_failure"] for f in report["properties"]["providerDiagnostics"] if f["id"].endswith("spawn-fail"))
    if malformed in ("object", "nan"):
        assert metadata["duration_s"] is None
    else:
        assert metadata["usage"] is None
    assert "NaN" not in json.dumps(report)


@pytest.mark.parametrize("mode", ["LOCAL", "CI"])
@pytest.mark.parametrize("omission", ["absent", "null"])
@pytest.mark.parametrize("capacity", [False, True])
def test_independent_host_completion_never_borrows_capacity(review_workspace, monkeypatch, mode, omission, capacity):
    from code_forge.state import Mode

    root = review_workspace

    def payload(machine, prompt):
        if capacity and "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("independent capacity", kind="truncated", retryable=False)
        return VALID

    def hook(round_index):
        path = root / ".code-forge/receipts/receipt-c1p1.json"
        receipt = json.loads(path.read_text())
        if omission == "absent":
            del receipt["pass_status"]
        else:
            receipt["pass_status"] = None
        path.write_text(json.dumps(receipt))

    machine = _machine(root, monkeypatch, payload=payload, rounds=1, hook=hook)
    machine.mode = Mode(mode)
    verdict = machine.run()
    legacy = verify.run_verify(
        root, SOURCE_HASH, verify.parse_diff_files(DIFF), diff_text=DIFF,
        required_cycles=1, cycles=[1], respect_floor=False, require_convergence=False,
    )
    summary, report = _reported(root)
    if capacity or mode == "LOCAL":
        assert verdict is Verdict.FAIL
        assert "RECEIPT_INVALID" in [finding.id for finding in machine.active_findings]
        assert report["results"] and "capacity_incomplete=" not in summary
        if capacity:
            assert not legacy.passed and legacy.failure_kind is None
            assert "completion status missing: c1p1" in legacy.reason
    else:
        assert verdict is Verdict.PASS and legacy.passed
        assert machine._receipt_gate_terminal_errors() == []
    if mode == "LOCAL":
        proof, captured = verify._capture_earned_cycle(
            root, SOURCE_HASH, verify.parse_diff_files(DIFF), cycle=1, diff_text=DIFF,
        )
        assert proof is None and not captured.passed and captured.failure_kind is None
        assert load_state(root / ".code-forge/state.json").consecutive_clean_rounds == 0
    if not capacity:
        assert legacy.passed, "public legacy receipts retain missing/null status tolerance"


@pytest.mark.parametrize("mode", ["LOCAL", "CI"])
@pytest.mark.parametrize("omission", ["absent", "null"])
@pytest.mark.parametrize("damage", ["schema", "hash", "anchor", "literal", "scope"])
def test_completion_and_capacity_preserve_independent_integrity_priority(review_workspace, monkeypatch, mode, omission, damage):
    from code_forge.state import Mode

    root = review_workspace

    def payload(machine, prompt):
        if "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("independent capacity", kind="truncated", retryable=False)
        return VALID

    def hook(round_index):
        path = root / ".code-forge/receipts/receipt-c1p1.json"
        receipt = json.loads(path.read_text())
        if omission == "absent":
            del receipt["pass_status"]
        else:
            receipt["pass_status"] = None
        if damage == "schema":
            receipt["code_excerpts"][0]["start_line"] = "bad"
        elif damage == "hash":
            receipt["diff_sha256"] = "wrong"
        elif damage == "anchor":
            receipt["anchors"] = [{"file": "elsewhere.ts", "line": 1}]
        elif damage == "literal":
            receipt["code_excerpts"][0]["content"] = CONTENT.replace("value = 2", "value = 200")
        else:
            receipt["reviewed_repositories"] = {"wrong": "scope"}
        path.write_text(json.dumps(receipt))

    machine = _machine(root, monkeypatch, payload=payload, rounds=1, hook=hook)
    machine.mode = Mode(mode)
    assert machine.run() is Verdict.FAIL
    result = verify.run_verify(
        root, SOURCE_HASH, verify.parse_diff_files(DIFF), diff_text=DIFF,
        required_cycles=1, cycles=[1], respect_floor=False, require_convergence=False,
    )
    assert not result.passed and result.failure_kind is None
    assert result.checks_run < 8, "independent checks1-7 must precede completion classification"
    assert "completion status missing" not in result.reason
    summary, report = _reported(root)
    assert "capacity_incomplete=" not in summary and report["results"]
    assert "RECEIPT_INVALID" in [finding.id for finding in machine.active_findings]


@pytest.mark.parametrize("mode", ["LOCAL", "CI"])
@pytest.mark.parametrize("kind", ["truncated", "timeout", "schema", "healthy"])
@pytest.mark.parametrize("status", ["original", "error", "timeout", "schema_fail", "incomplete", "skipped", "completed", None, "absent", "invented", False, {"bad": "status"}], ids=["original", "error", "timeout", "schema", "incomplete", "skipped", "completed", "null", "absent", "invented", "false", "object"])
def test_actual_writer_outcomes_and_fresh_status_contradictions(review_workspace, monkeypatch, mode, kind, status):
    from code_forge.state import Mode

    root = review_workspace
    expected = {"truncated": "error", "timeout": "timeout", "schema": "schema_fail", "healthy": "completed"}[kind]

    def payload(machine, prompt):
        if "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            if kind in ("truncated", "timeout"):
                return llm_invoke.LLMInvokeError("actual acquired failure", kind=kind, is_timeout=kind == "timeout", retryable=False)
            if kind == "schema":
                return {"findings": "not a list", "code_excerpts": []}
        return VALID

    def hook(round_index):
        path = root / ".code-forge/receipts/receipt-c1p3.json"
        receipt = json.loads(path.read_text())
        assert receipt["pass_status"] == expected
        if status == "original":
            return
        if status == "absent":
            del receipt["pass_status"]
        else:
            receipt["pass_status"] = status
        path.write_text(json.dumps(receipt))

    machine = _machine(root, monkeypatch, payload=payload, rounds=1, hook=hook)
    machine.mode = Mode(mode)
    verdict = machine.run()
    summary, report = _reported(root)
    honest = status == "original" or status == expected
    legacy = kind == "healthy" and (status is None or status == "absent")
    if kind != "healthy" or not (honest or legacy):
        assert verdict is not Verdict.PASS
    elif mode == "CI":
        assert verdict is Verdict.PASS
    if kind == "truncated" and honest:
        assert report["results"] == [] and "capacity_incomplete=1" in summary
    elif not (kind == "healthy" and honest):
        assert "capacity_incomplete=" not in summary
        if not (legacy and mode == "CI"):
            assert report["results"]
    if kind in ("truncated", "timeout"):
        assert load_state(root / ".code-forge/state.json").consecutive_clean_rounds == 0
        if not honest:
            assert "RECEIPT_INVALID" in [finding.id for finding in machine.active_findings]


@pytest.mark.parametrize("moment", ["before-return", "after-hook"])
@pytest.mark.parametrize("role", [1, 3])
@pytest.mark.parametrize("status", [None, "completed", "timeout", "schema_fail"])
def test_writer_boundary_and_hook_corruption_cannot_authorize_capacity(review_workspace, monkeypatch, moment, role, status):
    from code_forge import receipt as receipt_module
    from code_forge.state import Mode

    root = review_workspace
    original_write = receipt_module.write_receipts

    def damage():
        path = root / f".code-forge/receipts/receipt-c1p{role}.json"
        receipt = json.loads(path.read_text())
        receipt["pass_status"] = status
        path.write_text(json.dumps(receipt))

    def writer(**kwargs):
        paths = original_write(**kwargs)
        if moment == "before-return":
            damage()
        return paths

    def hook(round_index):
        if moment == "after-hook":
            damage()

    def payload(machine, prompt):
        if "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("actual capacity", kind="truncated", retryable=False)
        return VALID

    monkeypatch.setattr(receipt_module, "write_receipts", writer)
    machine = _machine(root, monkeypatch, payload=payload, rounds=1, hook=hook)
    machine.mode = Mode.CI
    assert machine.run() is Verdict.FAIL
    summary, report = _reported(root)
    # A healthy role honestly completed before/after the boundary has no damage.
    if role == 1 and status == "completed":
        assert "capacity_incomplete=1" in summary and report["results"] == []
    else:
        assert "capacity_incomplete=" not in summary and report["results"]
        assert "RECEIPT_INVALID" in [finding.id for finding in machine.active_findings]


@pytest.mark.parametrize("missing", ["none", "tuple", "malformed-extra"])
def test_missing_or_invalid_writer_snapshot_stays_hard(review_workspace, monkeypatch, missing):
    from code_forge import receipt as receipt_module
    from code_forge.state import Mode

    original_write = receipt_module.write_receipts

    def writer(**kwargs):
        paths = original_write(**kwargs)
        return None if missing == "none" else tuple(paths) if missing == "tuple" else paths

    def payload(machine, prompt):
        if "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("actual capacity", kind="truncated", retryable=False)
        return VALID

    def hook(round_index):
        if missing == "malformed-extra":
            machine._written_pass_outcomes += ((True, 3, "error"),)

    monkeypatch.setattr(receipt_module, "write_receipts", writer)
    machine = _machine(review_workspace, monkeypatch, payload=payload, rounds=1, hook=hook)
    machine.mode = Mode.CI
    assert machine.run() is Verdict.FAIL
    summary, report = _reported(review_workspace)
    assert "capacity_incomplete=" not in summary and report["results"]


def test_ci_live_failure_stays_nonpass_without_receipt_attestation(review_workspace, monkeypatch):
    from code_forge.state import Mode

    def payload(machine, prompt):
        if "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("actual capacity", kind="truncated", retryable=False)
        return VALID

    machine = _machine(review_workspace, monkeypatch, payload=payload, rounds=1)
    machine.mode = Mode.CI
    machine.coverage_l1_active = False
    machine.coverage_exempt_patterns = ["control.ts"]
    assert machine.run() is Verdict.FAIL
    state = load_state(review_workspace / ".code-forge/state.json")
    assert state.verdict is Verdict.FAIL and not state.converged
    summary, report = _reported(review_workspace)
    assert "provider_capacity=1" in summary
    assert not any(finding["ruleId"].startswith("l1-") for finding in report["results"])


@pytest.mark.parametrize("status", ["invented", False, {"bad": "status"}])
def test_public_verifier_invalid_status_never_gets_typed_incomplete(review_workspace, monkeypatch, status):
    from code_forge.state import Mode

    machine = _machine(review_workspace, monkeypatch, rounds=1)
    machine.mode = Mode.CI
    assert machine.run() is Verdict.PASS
    path = review_workspace / ".code-forge/receipts/receipt-c1p3.json"
    receipt = json.loads(path.read_text())
    receipt["pass_status"] = status
    path.write_text(json.dumps(receipt))
    result = verify.run_verify(review_workspace, SOURCE_HASH, verify.parse_diff_files(DIFF), diff_text=DIFF, required_cycles=1, cycles=[1], respect_floor=False, require_convergence=False)
    assert not result.passed and result.failure_kind is None


def test_local_sticky_dismissal_cannot_attest_actual_failed_acquisition(review_workspace, monkeypatch):
    def payload(machine, prompt):
        if "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("actual capacity", kind="truncated", retryable=False)
        return VALID

    def hook(round_index):
        path = review_workspace / ".code-forge/receipts/receipt-c1p3.json"
        receipt = json.loads(path.read_text())
        assert receipt["pass_status"] == "error"
        receipt["pass_status"] = "completed"
        path.write_text(json.dumps(receipt))

    machine = _machine(review_workspace, monkeypatch, payload=payload, rounds=1, hook=hook)
    machine._state.findings.append(StateFinding("prior", "invoke-fail-adversarial", "INFRA", Disposition.DISMISSED, "<llm-invoke>", [0, 0], "prior human disposition"))
    assert machine.run() is Verdict.FAIL
    state = load_state(review_workspace / ".code-forge/state.json")
    assert state.consecutive_clean_rounds == 0 and state.earned_clean_window["cycles"] == []
    assert "RECEIPT_INVALID" in [finding.id for finding in machine.active_findings]


def test_reused_ci_clears_live_failure_authority_before_transport(review_workspace, monkeypatch):
    from code_forge.state import Mode

    stage = {"fail": True}

    def payload(machine, prompt):
        if not stage["fail"]:
            assert not machine._acquisition_markers, "transport observed an earlier round's failure authority"
            assert machine._written_pass_outcomes == (), "transport observed an earlier round's completion snapshot"
        if stage["fail"] and "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("first capacity", kind="truncated", retryable=False)
        return VALID

    machine = _machine(review_workspace, monkeypatch, payload=payload, rounds=1)
    machine.mode = Mode.CI
    machine.coverage_l1_active = False
    machine.coverage_exempt_patterns = ["control.ts"]
    assert machine.run() is Verdict.FAIL
    stage["fail"] = False
    assert machine.run() is Verdict.PASS
    state = load_state(review_workspace / ".code-forge/state.json")
    assert state.converged and state.verdict is Verdict.PASS


def test_custom_verifier_result_cannot_create_capacity_authority(review_workspace, monkeypatch):
    from code_forge.state import Mode

    class CustomResult(verify.VerifyResult):
        pass

    original_verify = verify.run_verify

    def custom_verify(*args, **kwargs):
        result = original_verify(*args, **kwargs)
        return CustomResult(**vars(result))

    def payload(machine, prompt):
        if "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("actual capacity", kind="truncated", retryable=False)
        return VALID

    monkeypatch.setattr(verify, "run_verify", custom_verify)
    machine = _machine(review_workspace, monkeypatch, payload=payload, rounds=1)
    machine.mode = Mode.CI
    assert machine.run() is Verdict.FAIL
    summary, report = _reported(review_workspace)
    assert "capacity_incomplete=" not in summary and report["results"]
    assert "RECEIPT_INVALID" in [finding.id for finding in machine.active_findings]


def test_healthy_legacy_writer_return_without_failure_keeps_ci_policy(review_workspace, monkeypatch):
    from code_forge import receipt as receipt_module
    from code_forge.state import Mode

    original_write = receipt_module.write_receipts

    def writer(**kwargs):
        original_write(**kwargs)
        return None

    monkeypatch.setattr(receipt_module, "write_receipts", writer)
    machine = _machine(review_workspace, monkeypatch, rounds=1)
    machine.mode = Mode.CI
    assert machine.run() is Verdict.PASS
    state = load_state(review_workspace / ".code-forge/state.json")
    assert state.converged and state.verdict is Verdict.PASS


def test_terminal_reclassification_rechecks_fresh_completion_authority(review_workspace, monkeypatch):
    from code_forge.state import Mode

    def payload(machine, prompt):
        if "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("actual capacity", kind="truncated", retryable=False)
        return VALID

    machine = _machine(review_workspace, monkeypatch, payload=payload, rounds=1)
    machine.mode = Mode.CI
    original_terminal = machine._receipt_gate_terminal_errors

    def terminal():
        errors = original_terminal()
        assert errors, "actual incomplete receipts must fail terminal attestation"
        machine._written_pass_outcomes = ()
        return errors

    monkeypatch.setattr(machine, "_receipt_gate_terminal_errors", terminal)
    assert machine.run() is Verdict.FAIL
    summary, report = _reported(review_workspace)
    assert "capacity_incomplete=" not in summary and report["results"]
    assert "RECEIPT_INVALID" in [finding.id for finding in machine.active_findings]


@pytest.mark.parametrize("failed_round", [0, 2])
def test_local_without_attestation_capacity_pauses_credit(review_workspace, monkeypatch, failed_round):
    def payload(machine, prompt):
        if machine._state.round == failed_round and "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("actual capacity", kind="truncated", retryable=False)
        return VALID

    machine = _machine(review_workspace, monkeypatch, payload=payload, rounds=3)
    machine.coverage_l1_active = False
    machine.coverage_exempt_patterns = ["control.ts"]
    assert machine.run() is Verdict.ESCALATED
    state = load_state(review_workspace / ".code-forge/state.json")
    assert not state.converged and state.consecutive_clean_rounds == 2
    expected = [0, 1, 2] if failed_round == 0 else [1, 2, 2]
    assert [row["clean_rounds_after"] for row in state.round_history] == expected
    assert all(row["fixpoint"] == "CLEAN" for row in state.round_history)
    assert machine._clean_window_cycles == ([2, 3] if failed_round == 0 else [1, 2])
    failed = json.loads((review_workspace / f".code-forge/receipts/receipt-c{failed_round + 1}p3.json").read_text())
    assert failed["pass_status"] == "error"
    summary, report = _reported(review_workspace)
    assert "confirmed=0" in summary and report["results"] == []
    if failed_round == 2:
        assert "provider_capacity=1" in summary and "capacity_incomplete=" not in summary


@pytest.mark.parametrize("fails", [False, True])
def test_local_without_attestation_recovers_with_three_healthy_rounds(review_workspace, monkeypatch, fails):
    attempted = []

    def payload(machine, prompt):
        if "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            attempted.append(machine._state.round)
            if fails and machine._state.round == 0:
                return llm_invoke.LLMInvokeError("initial capacity", kind="truncated", retryable=False)
        return VALID

    machine = _machine(review_workspace, monkeypatch, payload=payload, rounds=4)
    machine.coverage_l1_active = False
    machine.coverage_exempt_patterns = ["control.ts"]
    assert machine.run() is Verdict.PASS
    state = load_state(review_workspace / ".code-forge/state.json")
    assert state.consecutive_clean_rounds == 3 and state.converged
    assert attempted == ([0, 1, 2, 3] if fails else [0, 1, 2])
    assert machine._clean_window_cycles == ([2, 3, 4] if fails else [1, 2, 3])
    assert not machine._acquisition_markers


@pytest.mark.parametrize("reuse", [False, True])
def test_local_satisfied_threshold_cannot_hide_current_acquisition_failure(review_workspace, monkeypatch, reuse):
    stage = {"fails": False}

    def payload(machine, prompt):
        if stage["fails"] and "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("current capacity", kind="truncated", retryable=False)
        return VALID

    machine = _machine(review_workspace, monkeypatch, payload=payload)
    machine.coverage_l1_active = False
    machine.coverage_exempt_patterns = ["control.ts"]
    assert machine.run() is Verdict.PASS
    assert machine._state.consecutive_clean_rounds == 3
    stage["fails"] = True
    if not reuse:
        machine = _machine(review_workspace, monkeypatch, payload=payload, rounds=1)
        machine.coverage_l1_active = False
        machine.coverage_exempt_patterns = ["control.ts"]
    machine.max_total_rounds = 1
    assert machine.run() is Verdict.FAIL
    state = load_state(review_workspace / ".code-forge/state.json")
    assert state.verdict is Verdict.FAIL and not state.converged
    assert state.consecutive_clean_rounds == 3
    assert machine._acquisition_markers
    summary, report = _reported(review_workspace)
    assert "provider_capacity=1" in summary
    if reuse:
        assert "RECEIPT_INVALID" in [finding.id for finding in machine.active_findings]
        assert any("receipt window stale" in item["message"]["text"] for item in report["results"])
    else:
        assert "FAIL findings=0 confirmed=0" in summary and report["results"] == []


@pytest.mark.parametrize("rounds", [3, 4])
def test_outlet_without_diff_counts_only_healthy_rounds(review_workspace, monkeypatch, rounds):
    attempted = []

    def spawn(role, diff):
        assert diff == ""
        attempted.append(role)
        if len(attempted) <= 3 and role == "adversarial":
            raise llm_invoke.LLMInvokeError("initial capacity", kind="truncated", retryable=False)
        return {"findings": [], "code_excerpts": []}

    result = run_outlet_c(
        ResolvedReview([Path("control.ts")], None, None, "non-git"),
        "owned-no-diff-source", review_workspace, spawn,
        falsifier=StubFalsifier(), max_total_rounds=rounds,
    )
    state = load_state(review_workspace / ".code-forge/state.json")
    assert result is (Verdict.ESCALATED if rounds == 3 else Verdict.PASS)
    assert state.consecutive_clean_rounds == rounds - 1
    assert [row["clean_rounds_after"] for row in state.round_history] == list(range(rounds))
    assert len(attempted) == 3 * rounds


@pytest.mark.parametrize("mode", ["LOCAL", "CI"])
@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("role", [1, 3])
@pytest.mark.parametrize("entry", ["none", "product", "sentinel", "copied", "replaced", "extra-flag", "false-line"])
def test_incomplete_receipt_exemption_requires_exact_host_evidence(review_workspace, monkeypatch, mode, grouped, role, entry):
    from code_forge.state import Mode

    root = review_workspace

    def payload(machine, prompt):
        if "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("actual acquisition", kind="truncated", retryable=False)
        return VALID

    def hook(round_index):
        if entry == "none":
            return
        source = json.loads((root / ".code-forge/receipts/receipt-c1p3.json").read_text())
        path = root / f".code-forge/receipts/receipt-c1p{role}.json"
        receipt = json.loads(path.read_text())
        added = copy.deepcopy(source["findings"][0])
        if entry in ("product", "sentinel", "replaced"):
            added["description"] = "independent unresolved candidate"
        if entry in ("product", "replaced"):
            added["file"], added["line"] = "control.ts", 2
        elif entry == "extra-flag":
            added["diagnostic_kind"] = "provider-capacity"
            added["provider_failure"] = {"kind": "truncated"}
        elif entry == "false-line":
            added["line"] = False
        if entry == "replaced" and role == 3:
            receipt["findings"] = [added]
        else:
            receipt["findings"].append(added)
        receipt["findings_count"] = len(receipt["findings"])
        path.write_text(json.dumps(receipt))

    machine = _machine(root, monkeypatch, payload=payload, rounds=1, hook=hook)
    machine.mode = Mode(mode)
    if grouped:
        machine.l1_provider = build_grouped_l1_provider(
            "auto", [{"name": name, "resolved": machine.resolved_review} for name in ("a", "b")],
            backend=BackendConfig(name="owned-offline", type="api", format="openai", model="offline", base_url="http://127.0.0.1:1"), max_attempts=1,
        )
    assert machine.run() is Verdict.FAIL
    summary, report = _reported(root)
    assert "provider_capacity=1" in summary and "passes=2/3" in summary
    receipts = [json.loads(p.read_text()) for p in _receipts(root)]
    assert [r["pass_status"] for r in receipts] == ["completed", "completed", "error"]
    assert load_state(root / ".code-forge/state.json").consecutive_clean_rounds == 0
    if entry == "none":
        assert report["results"] == [] and "capacity_incomplete=1" in summary
    else:
        assert report["results"] and "capacity_incomplete=" not in summary
        assert "RECEIPT_INVALID" in [f.id for f in machine.active_findings]
        proof, captured = verify._capture_earned_cycle(
            root, SOURCE_HASH, verify.parse_diff_files(DIFF), cycle=1, diff_text=DIFF,
        )
        assert proof is None and not captured.passed and captured.failure_kind is None


@pytest.mark.parametrize("mode", ["LOCAL", "CI"])
@pytest.mark.parametrize("moment", ["before-writer", "after-writer"])
def test_host_original_mutation_cannot_rewrite_acquisition_receipt_authority(review_workspace, monkeypatch, mode, moment):
    from code_forge.state import Mode

    def payload(machine, prompt):
        if "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("actual acquisition", kind="truncated", retryable=False)
        return VALID

    machine = _machine(review_workspace, monkeypatch, payload=payload, rounds=1)
    machine.mode = Mode(mode)
    publish = machine._publish_l1_receipts

    def mutate():
        original = machine.l1_provider.acquisition_failures[0]
        original.description = "independent altered original"

    def writer(*args, **kwargs):
        if moment == "before-writer":
            mutate()
        publish(*args, **kwargs)
        if moment == "after-writer":
            mutate()
            path = review_workspace / ".code-forge/receipts/receipt-c1p3.json"
            receipt = json.loads(path.read_text())
            receipt["findings"][0]["description"] = machine.l1_provider.acquisition_failures[0].description
            path.write_text(json.dumps(receipt))

    monkeypatch.setattr(machine, "_publish_l1_receipts", writer)
    assert machine.run() is Verdict.FAIL
    summary, report = _reported(review_workspace)
    assert report["results"] and "capacity_incomplete=" not in summary
    assert "RECEIPT_INVALID" in [f.id for f in machine.active_findings]


@pytest.mark.parametrize("mode", ["LOCAL", "CI"])
def test_removed_acquisition_diagnostic_cannot_attest_capacity_only(review_workspace, monkeypatch, mode):
    from code_forge.state import Mode

    def payload(machine, prompt):
        if "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("actual acquisition", kind="truncated", retryable=False)
        return VALID

    def hook(round_index):
        path = review_workspace / ".code-forge/receipts/receipt-c1p3.json"
        receipt = json.loads(path.read_text())
        receipt["findings"] = []
        receipt["findings_count"] = 0
        path.write_text(json.dumps(receipt))

    machine = _machine(review_workspace, monkeypatch, payload=payload, rounds=1, hook=hook)
    machine.mode = Mode(mode)
    assert machine.run() is Verdict.FAIL
    summary, report = _reported(review_workspace)
    assert report["results"] and "capacity_incomplete=" not in summary
    result = verify.run_verify(
        review_workspace, SOURCE_HASH, verify.parse_diff_files(DIFF), diff_text=DIFF,
        required_cycles=1, cycles=[1], respect_floor=False, require_convergence=False,
    )
    assert not result.passed and result.failure_kind is verify.VerifyFailureKind.INCOMPLETE_PASS
    assert result.unresolved_findings == ()


@pytest.mark.parametrize("mode", ["LOCAL", "CI"])
@pytest.mark.parametrize("damage", ["empty", "list", "row", "cycle", "boolean-cycle", "pass", "source", "payload", "duplicate"])
def test_current_acquisition_snapshot_refuses_invalid_or_stale_authority(review_workspace, monkeypatch, mode, damage):
    from code_forge.state import Mode

    def payload(machine, prompt):
        if "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("actual acquisition", kind="truncated", retryable=False)
        return VALID

    def hook(round_index):
        snapshot = machine._acquisition_receipts
        assert type(snapshot) is tuple and len(snapshot) == 1
        row = list(snapshot[0])
        if damage == "empty":
            snapshot = ()
        elif damage == "list":
            snapshot = list(snapshot)
        elif damage == "row":
            snapshot = (list(row),)
        elif damage == "duplicate":
            snapshot += snapshot
        else:
            index, value = {"cycle": (0, 2), "boolean-cycle": (0, True), "pass": (1, True), "source": (2, "earlier-source"), "payload": (3, {})}[damage]
            row[index] = value
            snapshot = (tuple(row),)
        machine._acquisition_receipts = snapshot

    machine = _machine(review_workspace, monkeypatch, payload=payload, rounds=1, hook=hook)
    machine.mode = Mode(mode)
    assert machine.run() is Verdict.FAIL
    summary, report = _reported(review_workspace)
    assert report["results"] and "capacity_incomplete=" not in summary


@pytest.mark.parametrize("mode", ["LOCAL", "CI"])
def test_reused_run_clears_acquisition_receipt_snapshot_before_transport(review_workspace, monkeypatch, mode):
    from code_forge.state import Mode

    stage = {"capacity": True}

    def payload(machine, prompt):
        if not stage["capacity"]:
            assert machine._acquisition_receipts == (), "previous call's authority reached a fresh acquisition"
            assert machine._acquisition_authority is None
        if stage["capacity"] and "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("actual acquisition", kind="truncated", retryable=False)
        return VALID

    machine = _machine(review_workspace, monkeypatch, payload=payload, rounds=1)
    machine.mode = Mode(mode)
    assert machine.run() is Verdict.FAIL
    assert machine._acquisition_receipts
    stage["capacity"] = False
    assert machine.run() is (Verdict.FAIL if mode == "CI" else Verdict.ESCALATED)
    assert machine._acquisition_receipts == ()
    if mode == "CI":
        _, report = _reported(review_workspace)
        assert any("evidence-only attestation requires one cycle" in result["message"]["text"] for result in report["results"])


@pytest.mark.parametrize("mode", ["LOCAL", "CI"])
def test_failed_host_attempt_clears_acquisition_receipt_snapshot(review_workspace, monkeypatch, mode):
    from code_forge.state import Mode
    def payload(machine, prompt):
        if "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("actual acquisition", kind="truncated", retryable=False)
        return VALID

    def hook(round_index):
        assert machine._acquisition_receipts
        raise KeyboardInterrupt("owned post-publication interrupt")

    machine = _machine(review_workspace, monkeypatch, payload=payload, rounds=1, hook=hook)
    machine.mode = Mode(mode)
    with pytest.raises(KeyboardInterrupt, match="owned post-publication interrupt"):
        machine.run()
    assert machine._acquisition_receipts == ()
    assert machine._acquisition_authority is None
    assert load_state(review_workspace / ".code-forge/state.json").verdict is not Verdict.PASS


@pytest.mark.parametrize("mode", ["LOCAL", "CI"])
@pytest.mark.parametrize("authority", ["tuple", "copied", "absent", "proxy"])
def test_public_acquisition_requires_retained_original_identity(review_workspace, monkeypatch, mode, authority):
    from code_forge.state import Mode

    def payload(machine, prompt):
        if "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("actual acquisition", kind="truncated", retryable=False)
        return VALID

    machine = _machine(review_workspace, monkeypatch, payload=payload, rounds=1)
    machine.mode = Mode(mode)
    original_body = machine.l1_provider._body

    def body(call):
        result = original_body(call)
        if authority == "tuple":
            call.acquisition_failures = tuple(call.acquisition_failures)
        elif authority == "copied":
            result = (copy.deepcopy(result[0]), *result[1:])
        elif authority == "absent":
            call.acquisition_failures.clear()
        return result

    machine.l1_provider._body = body
    if authority == "proxy":
        producer = machine.l1_provider

        class Proxy:
            def __call__(self):
                return producer()

            def __getattr__(self, name):
                return getattr(producer, name)

        machine.l1_provider = Proxy()
    assert machine.run() is Verdict.FAIL
    assert machine._acquisition_receipts == (), "unowned values acquired host receipt authority"
    summary, report = _reported(review_workspace)
    assert report["results"] and "capacity_incomplete=" not in summary


def test_reserved_local_attempt_discards_prior_acquisition_receipt_snapshot(review_workspace, monkeypatch):
    from code_forge import machine as machine_module

    def payload(machine, prompt):
        if "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("actual acquisition", kind="truncated", retryable=False)
        return VALID

    machine = _machine(review_workspace, monkeypatch, payload=payload, rounds=1)
    assert machine.run() is Verdict.FAIL
    assert machine._acquisition_receipts

    def frozen(state):
        assert machine._acquisition_receipts == (), "new reservation retained previous acquisition authority"
        return True

    monkeypatch.setattr(machine_module, "check_escalated_frozen", frozen)
    assert machine.run() is Verdict.ESCALATED


@pytest.mark.parametrize("stage", [1, 7])
def test_earlier_hard_result_cannot_borrow_unresolved_observations(review_workspace, monkeypatch, stage):
    from code_forge.state import Mode

    def payload(machine, prompt):
        if "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("actual acquisition", kind="truncated", retryable=False)
        return VALID

    real_verify = verify.run_verify

    def mixed(*args, **kwargs):
        result = real_verify(*args, **kwargs)
        assert result.unresolved_findings
        result.checks_run = stage
        result.checks_passed = stage - 1
        return result

    monkeypatch.setattr(verify, "run_verify", mixed)
    machine = _machine(review_workspace, monkeypatch, payload=payload, rounds=1)
    machine.mode = Mode.CI
    assert machine.run() is Verdict.FAIL
    summary, report = _reported(review_workspace)
    assert report["results"] and "capacity_incomplete=" not in summary


def test_completed_ci_retains_evidence_only_unresolved_semantics(review_workspace, monkeypatch):
    from code_forge.state import Mode

    def hook(round_index):
        path = review_workspace / ".code-forge/receipts/receipt-c1p3.json"
        receipt = json.loads(path.read_text())
        receipt["findings"].append({
            "file": "control.ts", "line": 2, "description": "unresolved completed evidence",
            "disposition": "UNCERTAIN", "basis": {"authority": "infra-unavailable"},
        })
        receipt["findings_count"] = len(receipt["findings"])
        path.write_text(json.dumps(receipt))

    machine = _machine(review_workspace, monkeypatch, rounds=1, hook=hook)
    machine.mode = Mode.CI
    assert machine.run() is Verdict.PASS
    summary, report = _reported(review_workspace)
    assert not any("receipt" in r["ruleId"].lower() for r in report["results"])
    assert "provider_capacity=" not in summary
    kwargs = dict(diff_text=DIFF, required_cycles=1, cycles=[1], respect_floor=False)
    evidence = verify.run_verify(review_workspace, SOURCE_HASH, verify.parse_diff_files(DIFF), require_convergence=False, **kwargs)
    converged = verify.run_verify(review_workspace, SOURCE_HASH, verify.parse_diff_files(DIFF), require_convergence=True, **kwargs)
    assert evidence.passed and evidence.unresolved_findings == ()
    assert not converged.passed and converged.failure_kind is None
    assert len(converged.unresolved_findings) == 1 and converged.incomplete_passes == ()


@pytest.mark.parametrize("mode", ["LOCAL", "CI"])
def test_reused_call_preflight_exception_has_no_old_receipt_authority(review_workspace, monkeypatch, mode):
    from code_forge.state import Mode

    def payload(machine, prompt):
        if "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("actual acquisition", kind="truncated", retryable=False)
        return VALID

    machine = _machine(review_workspace, monkeypatch, payload=payload, rounds=1)
    machine.mode = Mode(mode)
    assert machine.run() is Verdict.FAIL and machine._acquisition_receipts
    primary = KeyboardInterrupt("owned preflight interrupt")

    def preflight():
        raise primary

    monkeypatch.setattr(machine, "_maybe_load_prior_state", preflight)
    with pytest.raises(KeyboardInterrupt) as caught:
        machine.run()
    assert caught.value is primary and machine._acquisition_receipts == ()
    assert machine._acquisition_authority is None


@pytest.mark.parametrize("mode", ["LOCAL", "CI"])
@pytest.mark.parametrize("phase", ["_run_advisory_axes", "_serialize_advisories", "_display_advisories"])
def test_terminal_advisory_exception_clears_current_receipt_authority(review_workspace, monkeypatch, mode, phase):
    from code_forge.state import Mode

    def payload(machine, prompt):
        if "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("actual acquisition", kind="truncated", retryable=False)
        return VALID

    machine = _machine(review_workspace, monkeypatch, payload=payload, rounds=1)
    machine.mode = Mode(mode)
    primary = KeyboardInterrupt("owned terminal interrupt")

    def interrupt():
        assert machine._acquisition_receipts
        raise primary

    monkeypatch.setattr(machine, phase, interrupt)
    with pytest.raises(KeyboardInterrupt) as caught:
        machine.run()
    assert caught.value is primary and machine._acquisition_receipts == ()
    assert machine._acquisition_authority is None
    assert load_state(review_workspace / ".code-forge/state.json").verdict is Verdict.FAIL


def test_each_local_round_clears_prior_acquisition_receipt_authority(review_workspace, monkeypatch):
    seen = []

    def payload(machine, prompt):
        if machine._state.round > 0:
            assert machine._acquisition_receipts == (), "prior round authority reached next transport"
            assert machine._acquisition_authority is None
        seen.append(machine._state.round)
        if machine._state.round == 0 and "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("actual acquisition", kind="truncated", retryable=False)
        return VALID

    machine = _machine(review_workspace, monkeypatch, payload=payload, rounds=2)
    machine.coverage_l1_active = False
    assert machine.run() is not Verdict.PASS
    assert seen.count(0) == seen.count(1) == 3
    assert machine._acquisition_receipts == ()


@pytest.mark.parametrize("mode", ["LOCAL", "CI"])
@pytest.mark.parametrize("moment", ["before-writer", "after-writer"])
@pytest.mark.parametrize("scenario", [
    "capacity-control", "capacity-reorder", "generic-retag", "generic-kind", "generic-enum",
    "mixed-retag", "mixed-drop", "mixed-reverse-retag", "capacity-add-copy",
    "capacity-replace-copy", "mixed-replace-duplicate", "capacity-duplicate", "capacity-remove",
    "timeout-retag", "producer-swap", "marker-copy", "marker-add", "marker-drop",
])
def test_acquired_eligibility_and_membership_are_frozen(review_workspace, monkeypatch, mode, moment, scenario):
    from code_forge.factories import _L1Call
    from code_forge.state import FindingDiagnosticKind, Mode

    kinds = (["truncated", "conn"] if scenario.startswith("mixed") else
             ["conn"] if scenario.startswith("generic") else
             ["timeout"] if scenario == "timeout-retag" else
             ["truncated", "truncated"] if scenario in ("capacity-control", "capacity-reorder") else
             ["truncated"])
    acquired = []

    def payload(machine, prompt):
        if "adversarial" not in prompt.rsplit("You are a ", 1)[-1]:
            return VALID
        kind = kinds[len(acquired)]
        acquired.append(kind)
        return llm_invoke.LLMInvokeError("same acquisition text", kind=kind, is_timeout=kind == "timeout", retryable=False)

    machine = _machine(review_workspace, monkeypatch, payload=payload, rounds=1)
    machine.mode = Mode(mode)
    if len(kinds) > 1:
        machine.l1_provider = build_grouped_l1_provider(
            "auto", [{"name": str(i), "resolved": machine.resolved_review} for i in range(len(kinds))],
            backend=BackendConfig(name="owned-offline", type="api", format="openai", model="offline", base_url="http://127.0.0.1:1"), max_attempts=1,
        )

    def mutate():
        originals = machine.l1_provider.acquisition_failures
        assert len(originals) == len(kinds)
        if scenario in ("generic-retag", "mixed-retag", "mixed-reverse-retag", "timeout-retag", "generic-kind", "generic-enum"):
            for finding in originals:
                if scenario != "generic-enum":
                    finding.provider_failure["kind"] = "truncated"
                if scenario != "generic-kind":
                    finding.diagnostic_kind = FindingDiagnosticKind.PROVIDER_CAPACITY
                if scenario == "timeout-retag":
                    finding.is_timeout = False
            if scenario == "mixed-reverse-retag":
                originals.reverse()
        elif scenario == "mixed-drop":
            originals.pop()
        elif scenario == "capacity-add-copy":
            originals.append(copy.deepcopy(originals[0]))
        elif scenario == "capacity-replace-copy":
            originals[:] = copy.deepcopy(originals)
        elif scenario == "mixed-replace-duplicate":
            originals[:] = [originals[0], originals[0]]
        elif scenario == "capacity-duplicate":
            originals.append(originals[0])
        elif scenario == "capacity-remove":
            originals.clear()
        elif scenario == "capacity-reorder":
            originals.reverse()
        elif scenario == "producer-swap":
            replacement = _L1Call(lambda call: pytest.fail("replacement producer must not run"))
            replacement.acquisition_failures = originals
            machine.l1_provider = replacement
        elif scenario == "marker-copy":
            machine._acquisition_markers = copy.deepcopy(machine._acquisition_markers)
        elif scenario == "marker-add":
            machine._acquisition_markers += machine._acquisition_markers
        elif scenario == "marker-drop":
            machine._acquisition_markers.clear()

    publish = machine._publish_l1_receipts

    def writer(*args, **kwargs):
        if moment == "before-writer":
            mutate()
        publish(*args, **kwargs)
        if moment == "after-writer":
            mutate()

    monkeypatch.setattr(machine, "_publish_l1_receipts", writer)
    verdict = machine.run()
    if scenario in ("marker-copy", "marker-drop") and mode == "LOCAL":
        assert verdict is Verdict.ESCALATED
        assert machine._current_acquisition() is None
        assert machine._state.consecutive_clean_rounds == 0
        assert "capacity_incomplete=" not in _reported(review_workspace)[0]
        return
    assert verdict is Verdict.FAIL
    assert acquired == kinds and machine._state.consecutive_clean_rounds == 0
    summary, report = _reported(review_workspace)
    if scenario in ("capacity-control", "capacity-reorder"):
        assert "capacity_incomplete=1" in summary and report["results"] == []
    else:
        assert "capacity_incomplete=" not in summary and report["results"]
        assert "RECEIPT_INVALID" in [finding.id for finding in machine.active_findings]


@pytest.mark.parametrize("mode", ["LOCAL", "CI"])
@pytest.mark.parametrize("damage", [
    "none", "mapping", "producer", "cycle", "boolean-cycle", "source", "facts-list", "fact-list",
    "outcomes-list", "outcomes-size", "outcomes-string", "markers-list", "empty-facts",
    "fact-original", "fact-role", "fact-boolean-role", "fact-outcome", "fact-capacity", "fact-emitted",
])
def test_acquisition_authority_requires_current_strict_frozen_facts(review_workspace, monkeypatch, mode, damage):
    from dataclasses import replace
    from code_forge.state import Mode

    def payload(machine, prompt):
        role = "structural code reviewer" if damage == "fact-boolean-role" else "adversarial"
        if role in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("actual acquisition", kind="truncated", retryable=False)
        return VALID

    def hook(round_index):
        snapshot = machine._acquisition_authority
        assert snapshot is not None and len(snapshot.facts) == 1
        if damage == "none":
            damaged = None
        elif damage == "mapping":
            damaged = {"facts": snapshot.facts}
        elif damage == "fact-list":
            damaged = replace(snapshot, facts=([snapshot.facts[0]],))
        elif damage.startswith("fact-"):
            name, value = {
                "fact-original": ("original", copy.deepcopy(snapshot.facts[0].original)),
                "fact-role": ("number", 0), "fact-boolean-role": ("number", True),
                "fact-outcome": ("outcome", "error"), "fact-capacity": ("capacity", 1),
                "fact-emitted": ("emitted", 1),
            }[damage]
            damaged = replace(snapshot, facts=(replace(snapshot.facts[0], **{name: value}),))
        else:
            name, value = {
                "producer": ("producer", object()), "cycle": ("cycle", 2),
                "boolean-cycle": ("cycle", True), "source": ("source_hash", "old-source"),
                "facts-list": ("facts", list(snapshot.facts)), "empty-facts": ("facts", ()),
                "outcomes-list": ("outcomes", list(snapshot.outcomes)),
                "outcomes-size": ("outcomes", snapshot.outcomes[:2]),
                "outcomes-string": ("outcomes", tuple(outcome.value for outcome in snapshot.outcomes)),
                "markers-list": ("markers", list(snapshot.markers)),
            }[damage]
            damaged = replace(snapshot, **{name: value})
        machine._acquisition_authority = damaged

    machine = _machine(review_workspace, monkeypatch, payload=payload, rounds=1, hook=hook)
    machine.mode = Mode(mode)
    assert machine.run() is Verdict.FAIL
    summary, report = _reported(review_workspace)
    assert "capacity_incomplete=" not in summary and report["results"]


def test_acquired_fact_and_envelope_reject_in_place_reclassification(review_workspace, monkeypatch):
    from dataclasses import FrozenInstanceError

    def payload(machine, prompt):
        if "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("actual acquisition", kind="truncated", retryable=False)
        return VALID

    def hook(round_index):
        snapshot = machine._acquisition_authority
        with pytest.raises(FrozenInstanceError):
            snapshot.facts = ()
        with pytest.raises(FrozenInstanceError):
            snapshot.facts[0].capacity = False

    machine = _machine(review_workspace, monkeypatch, payload=payload, rounds=1, hook=hook)
    assert machine.run() is Verdict.FAIL
    assert _reported(review_workspace)[1]["results"] == []


@pytest.mark.parametrize("mode", ["LOCAL", "CI"])
def test_failed_recapture_discards_all_previous_acquisition_authority(review_workspace, monkeypatch, mode):
    from code_forge import basis
    from code_forge.state import Mode

    def payload(machine, prompt):
        if "adversarial" in prompt.rsplit("You are a ", 1)[-1]:
            return llm_invoke.LLMInvokeError("actual acquisition", kind="truncated", retryable=False)
        return VALID

    primary = KeyboardInterrupt("owned recapture interrupt")

    def broken(*args, **kwargs):
        raise primary

    def hook(round_index):
        assert machine._acquisition_authority is not None and machine._acquisition_receipts
        monkeypatch.setattr(basis, "derive_basis", broken)
        with pytest.raises(KeyboardInterrupt) as caught:
            machine._capture_acquisition_receipts()
        assert caught.value is primary
        assert machine._acquisition_authority is None and machine._acquisition_receipts == ()

    machine = _machine(review_workspace, monkeypatch, payload=payload, rounds=1, hook=hook)
    machine.mode = Mode(mode)
    assert machine.run() is Verdict.FAIL
    summary, report = _reported(review_workspace)
    assert "capacity_incomplete=" not in summary and report["results"]
