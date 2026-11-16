# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Refusal classification composes with immutable count recovery and its gates."""

import copy
import dataclasses
import io
import json
import time
from types import SimpleNamespace

import pytest

from code_forge import llm_invoke as invoke
from code_forge.reviewer_json import REVIEW_REPLY_KEYS
from code_forge.state import Mode, Verdict
from tests import test_review_pass_evidence_contract as contract
from tests.test_count_excerpt_recovery import LINES, count_reply, source_diff
from tests.test_literal_excerpt_factory import BAD, DIFF, _backend, _envelope, _provider

isolated_execution = contract.isolated_execution
PRIVATE = "PRIVATE-REFUSAL-INTERACTION-SYNTHETIC"
INITIAL_USAGE = {
    "prompt_tokens": 2,
    "completion_tokens": 3,
    "prompt_tokens_details": {"cached_tokens": 0},
}
FOLLOWUP_USAGE = {
    "prompt_tokens": 5,
    "completion_tokens": 7,
    "prompt_tokens_details": {"cached_tokens": 0},
}


def install_wire(
    monkeypatch,
    original,
    *,
    stream=False,
    finish="stop",
    initial_refusal=False,
    followup_refusal=True,
    report_followup_usage=True,
):
    raw = json.dumps(original, indent=2)
    requests, refusals = [], []
    actual_api = invoke._invoke_api

    def record_api(*args, **kwargs):
        try:
            return actual_api(*args, **kwargs)
        except invoke.ProviderRefusalError as error:
            refusals.append(error)
            raise

    def open_response(request, timeout_s):
        correction = invoke._CORRECTION_READ_POLICY.get() is not None
        requests.append({"correction": correction, "body": json.loads(request.data)})
        refused = initial_refusal if not correction else followup_refusal
        content = raw if not correction else "{unfinished"
        reason = finish if refused or correction else "stop"
        usage = FOLLOWUP_USAGE if correction else INITIAL_USAGE
        if stream:
            delta = {"content": content}
            if refused:
                delta["refusal"] = PRIVATE
            events = [{"choices": [{"delta": delta, "finish_reason": reason}]}]
            if not correction or report_followup_usage:
                events.append({"choices": [], "usage": usage})
            payload = b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)
            payload += b"data: [DONE]\n"
        else:
            message = {"content": content}
            if refused:
                message["refusal"] = PRIVATE
            body = {"choices": [{"message": message, "finish_reason": reason}]}
            if not correction or report_followup_usage:
                body["usage"] = usage
            payload = json.dumps(body).encode()
        return io.BytesIO(payload)

    monkeypatch.setattr(invoke, "_open_url", open_response)
    monkeypatch.setattr(invoke, "_invoke_api", record_api)
    backend = dataclasses.replace(_backend(monkeypatch), stream=stream)
    return backend, requests, refusals, raw


def assert_private(capsys, caplog, errors, diagnostics=()):
    output = capsys.readouterr()
    public = output.out + output.err + caplog.text + json.dumps(diagnostics)
    public += "".join(str(error) + error.stderr for error in errors)
    assert PRIVATE not in public


@pytest.mark.parametrize("stream", [False, True], ids=["json", "sse"])
@pytest.mark.parametrize("finish", ["stop", "length"])
def test_count_correction_refusal_wins_over_length_and_keeps_complete_initial_response(
    monkeypatch, capsys, caplog, stream, finish
):
    original = count_reply()
    snapshot = copy.deepcopy(original)
    backend, requests, refusals, raw = install_wire(monkeypatch, original, stream=stream, finish=finish)
    result = invoke.llm_invoke(
        "synthetic count review",
        backend=backend,
        max_attempts=5,
        initial_delay_s=0,
        reply_keys=REVIEW_REPLY_KEYS,
        review_diff=source_diff(),
        l1_evidence_required=True,
    )
    assert [request["correction"] for request in requests] == [False, True]
    assert result.content == original == snapshot and result.raw_response == raw
    assert (result.usage.input_tokens, result.usage.output_tokens) == (7, 10)
    assert len(refusals) == 1 and refusals[0].kind == "refusal" and not refusals[0].retryable
    assert (refusals[0].observed_usage.input_tokens, refusals[0].observed_usage.output_tokens) == (5, 7)
    (diagnostic,) = result.excerpt_diagnostics
    assert diagnostic["kind"] == "count-excerpt-correction"
    assert diagnostic["reason"] == "provider_refusal" and diagnostic["selected"] is False
    assert diagnostic["indexes"] == [1, 2]
    assert diagnostic["initial_raw"] == raw and diagnostic["correction_raw"] is None
    assert_private(capsys, caplog, refusals, result.excerpt_diagnostics)


@pytest.mark.parametrize("stream", [False, True], ids=["json", "sse"])
@pytest.mark.parametrize("strategy", ["count", "literal"])
def test_initial_refusal_never_enters_either_evidence_strategy(
    monkeypatch, capsys, caplog, stream, strategy
):
    original = count_reply() if strategy == "count" else json.loads(_envelope(BAD))
    diff = source_diff() if strategy == "count" else DIFF
    backend, requests, refusals, _ = install_wire(
        monkeypatch, original, stream=stream, finish="length", initial_refusal=True
    )
    allowance = invoke._ExcerptCorrectionAllowance(1)
    with pytest.raises(invoke.ProviderRefusalError) as caught:
        invoke.llm_invoke(
            "synthetic initial review",
            backend=backend,
            max_attempts=5,
            initial_delay_s=0,
            reply_keys=REVIEW_REPLY_KEYS,
            review_diff=diff,
            l1_evidence_required=True,
            _excerpt_allowance=allowance,
        )
    assert len(requests) == len(refusals) == 1 and requests[0]["correction"] is False
    assert caught.value is refusals[0] and not caught.value.retryable
    assert (caught.value.observed_usage.input_tokens, caught.value.observed_usage.output_tokens) == (
        2,
        3,
    )
    assert allowance.reserve() is True
    assert_private(capsys, caplog, refusals)


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_refused_count_correction_retains_original_gate_sidecar_and_all_costs(
    tmp_path, monkeypatch, capsys, caplog, mode
):
    original = count_reply()
    backend, requests, refusals, raw = install_wire(monkeypatch, original, finish="length")
    provider = _provider(backend, source_diff())
    machine = contract.machine_for(tmp_path, mode, provider, source_diff(), ["a.py"])
    machine.max_total_rounds = 1
    (tmp_path / "a.py").write_text("\n".join(LINES) + "\n")
    assert machine.run() is not Verdict.PASS
    assert len(requests) == 6 and sum(request["correction"] for request in requests) == 3
    assert len(refusals) == 3
    assert machine._state.cost_total_input == 21 and machine._state.cost_total_output == 30
    assert machine._state.consecutive_clean_rounds == 0 and not machine._state.converged
    assert any(f.id == "RECEIPT_INVALID" for f in machine._state.findings)
    assert len(provider.attempted_excerpts) == 3 and provider.raw_observations == []
    root = tmp_path / ".code-forge/receipts"
    receipts = [json.loads(path.read_text()) for path in root.glob("receipt-c1p*.json")]
    assert len(receipts) == 3 and all(
        row["pass_status"] == "incomplete" and row["code_excerpts"] == [] for row in receipts
    )
    attempts = [json.loads(path.read_text())["payload"] for path in (root / "attempted").glob("*.json")]
    assert len(attempts) == 3 and all(
        {key: payload[key] for key in original} == original for payload in attempts
    )
    diagnostics = [
        json.loads(path.read_text())["payload"] for path in (root / "correction").glob("*.json")
    ]
    assert len(diagnostics) == 3
    assert all(
        row["kind"] == "count-excerpt-correction"
        and row["reason"] == "provider_refusal"
        and row["selected"] is False
        and row["initial_raw"] == raw
        and row["correction_raw"] is None
        for row in diagnostics
    )
    assert_private(capsys, caplog, refusals, diagnostics)
    assert PRIVATE not in (tmp_path / ".code-forge/state.json").read_text()


def test_unreported_refusal_usage_remains_unknown_while_initial_cost_survives(monkeypatch):
    backend, requests, refusals, _ = install_wire(
        monkeypatch, count_reply(), finish="length", report_followup_usage=False
    )
    result = invoke.llm_invoke(
        "synthetic count review",
        backend=backend,
        max_attempts=5,
        initial_delay_s=0,
        reply_keys=REVIEW_REPLY_KEYS,
        review_diff=source_diff(),
    )
    assert len(requests) == 2 and len(refusals) == 1
    assert result.usage.known_fields == frozenset()
    assert (result.usage.input_tokens, result.usage.output_tokens) == (2, 3)
    assert refusals[0].usage is None and refusals[0].observed_usage.known_fields == frozenset()
    assert result.excerpt_diagnostics[0]["reason"] == "provider_refusal"


def test_nonrefusal_truncation_keeps_observed_cost_and_does_not_continue(monkeypatch):
    original = count_reply()
    backend, requests, refusals, raw = install_wire(
        monkeypatch, original, finish="length", followup_refusal=False
    )
    result = invoke.llm_invoke(
        "synthetic count review",
        backend=backend,
        max_attempts=5,
        initial_delay_s=0,
        reply_keys=REVIEW_REPLY_KEYS,
        review_diff=source_diff(),
    )
    assert len(requests) == 2 and refusals == []
    assert result.content == original and result.raw_response == raw
    assert (result.usage.input_tokens, result.usage.output_tokens) == (7, 10)
    assert result.excerpt_diagnostics[0]["reason"] == "correction_failed"
    assert result.excerpt_diagnostics[0]["selected"] is False


@pytest.mark.parametrize("disabled", [False, True], ids=["ordinary-fallback", "strict-stop"])
def test_strict_audit_refusal_control_preserves_primary_and_exact_cost_once(monkeypatch, disabled):
    backend = _backend(monkeypatch)
    primary = invoke.ProviderRefusalError(backend, FOLLOWUP_USAGE)
    cause = RuntimeError("synthetic audit cause")
    primary.__cause__ = cause
    calls = []

    def fail(*args, **kwargs):
        calls.append(kwargs)
        raise primary

    monkeypatch.setattr(invoke, "llm_invoke", fail)
    payload = count_reply()
    scope = SimpleNamespace(strict=True, disabled=disabled)
    started = time.monotonic()
    kwargs = dict(
        prompt="synthetic count review",
        backend=backend,
        reply_keys=REVIEW_REPLY_KEYS,
        review_diff=source_diff(),
        allowance=invoke._ExcerptCorrectionAllowance(1),
        entry_started_at=started,
        entry_deadline=started + 10,
        audit_scope=scope,
        timeout_s=10,
    )
    result = invoke.LLMResult(payload, usage=invoke.Usage(2, 3, 0), raw_response=json.dumps(payload))
    if disabled:
        with pytest.raises(invoke.ProviderRefusalError) as caught:
            invoke._maybe_correct_literal_excerpts(result, **kwargs)
        assert caught.value is primary and caught.value.__cause__ is cause
        assert (primary.observed_usage.input_tokens, primary.observed_usage.output_tokens) == (7, 10)
    else:
        output = invoke._maybe_correct_literal_excerpts(result, **kwargs)
        assert output.content is payload
        assert (output.usage.input_tokens, output.usage.output_tokens) == (7, 10)
        assert (primary.observed_usage.input_tokens, primary.observed_usage.output_tokens) == (5, 7)
        assert output.excerpt_diagnostics[0]["reason"] == "provider_refusal"
    assert len(calls) == 1 and calls[0]["max_attempts"] == 1 and calls[0]["_no_continuation"] is True
    assert calls[0]["strict_response"] is True and calls[0]["_audit_scope"] is scope
    assert calls[0]["_audit_cause"] == "excerpt_repair"
    assert invoke._CORRECTION_READ_POLICY.get() is None


@pytest.mark.parametrize("strict_stop", [False, True], ids=["ordinary", "strict-stop"])
@pytest.mark.parametrize("kind", ["refusal", "ordinary", "audit", "send-deadline", "truncation"])
def test_json_correction_failure_preserves_initial_cost_before_strict_escape(
    monkeypatch, strict_stop, kind
):
    backend = _backend(monkeypatch)
    if kind == "refusal":
        primary = invoke.ProviderRefusalError(backend, FOLLOWUP_USAGE)
    elif kind == "audit":
        primary = invoke.AuditPersistenceError()
        primary.observed_usage = invoke.Usage(5, 7, 0)
    elif kind == "send-deadline":
        primary = invoke.SendDeadlineError()
        primary.observed_usage = invoke.Usage(5, 7, 0)
    elif kind == "truncation":
        primary = invoke._TruncatedResponse(
            "controlled truncation", "{", FOLLOWUP_USAGE, 10, retryable=False
        )
    else:
        primary = invoke.LLMInvokeError(
            "controlled correction failure", observed_usage=invoke.Usage(5, 7, 0)
        )
    cause = RuntimeError("synthetic original cause")
    primary.__cause__ = cause
    scope = SimpleNamespace(strict=True, disabled=False)
    calls = []

    def transport(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return '{"findings": [],, "code_excerpts": []}', INITIAL_USAGE
        scope.disabled = strict_stop
        raise primary

    monkeypatch.setattr(invoke, "_invoke_openai", transport)
    with pytest.raises(invoke.LLMInvokeError) as caught:
        invoke._invoke_api(
            "synthetic review",
            backend,
            10,
            max_attempts=5,
            initial_delay_s=0,
            reply_keys=REVIEW_REPLY_KEYS,
            _audit_scope=scope,
        )
    assert len(calls) == 2 and caught.value is primary and caught.value.__cause__ is cause
    assert (primary.observed_usage.input_tokens, primary.observed_usage.output_tokens) == (7, 10)
    assert primary.usage is primary.observed_usage


@pytest.mark.parametrize("strict_stop", [False, True], ids=["ordinary", "strict-stop"])
def test_json_correction_refusal_with_unknown_cost_does_not_invent_usage(monkeypatch, strict_stop):
    backend = _backend(monkeypatch)
    primary = invoke.ProviderRefusalError(backend, None)
    scope = SimpleNamespace(strict=True, disabled=False)
    calls = []

    def transport(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return '{"findings": [],, "code_excerpts": []}', INITIAL_USAGE
        scope.disabled = strict_stop
        raise primary

    monkeypatch.setattr(invoke, "_invoke_openai", transport)
    with pytest.raises(invoke.ProviderRefusalError) as caught:
        invoke._invoke_api(
            "synthetic review",
            backend,
            10,
            max_attempts=5,
            initial_delay_s=0,
            reply_keys=REVIEW_REPLY_KEYS,
            _audit_scope=scope,
        )
    assert len(calls) == 2 and caught.value is primary
    assert (primary.observed_usage.input_tokens, primary.observed_usage.output_tokens) == (2, 3)
    assert primary.observed_usage.known_fields == frozenset() and primary.usage is None


@pytest.mark.parametrize("strict_stop", [False, True], ids=["ordinary", "strict-stop"])
@pytest.mark.parametrize("stage", ["initial", "syntax", "continuation", "headroom"])
def test_escaping_refusal_duration_is_entire_api_entry_elapsed(monkeypatch, strict_stop, stage):
    backend = dataclasses.replace(
        _backend(monkeypatch),
        max_tokens=10,
        max_completion_tokens=0,
        output_ceiling=0,
        output_token_limit=20,
    )
    clock = [10.0]
    monkeypatch.setattr(invoke.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(invoke.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    scope = SimpleNamespace(strict=True, disabled=False)
    primary = invoke.ProviderRefusalError(
        backend, INITIAL_USAGE if stage == "initial" else FOLLOWUP_USAGE
    )
    cause = RuntimeError("synthetic duration cause")
    primary.__cause__ = cause
    calls = []
    final_call = {"initial": 1, "syntax": 2, "continuation": 2, "headroom": 4}[stage]

    def transport(prompt, current, key, timeout_s, **kwargs):
        calls.append(current.max_tokens)
        if len(calls) == final_call:
            clock[0] += 2.5
            scope.disabled = strict_stop
            raise primary
        if len(calls) == 1:
            clock[0] += 2.0
            if stage == "syntax":
                return '{"findings": [],, "code_excerpts": []}', INITIAL_USAGE
            raise invoke._TruncatedResponse(
                "controlled initial cut",
                '{"findings":[',
                INITIAL_USAGE,
                10,
                kind="truncated",
                retryable=False,
            )
        clock[0] += 1.0
        return "not-json", {
            "prompt_tokens": 3,
            "completion_tokens": 4,
            "prompt_tokens_details": {"cached_tokens": 0},
        }

    monkeypatch.setattr(invoke, "_invoke_openai", transport)
    with pytest.raises(invoke.ProviderRefusalError) as caught:
        invoke._invoke_api(
            "synthetic duration review",
            backend,
            10,
            max_attempts=5,
            initial_delay_s=0,
            reply_keys=REVIEW_REPLY_KEYS,
            _audit_scope=scope,
        )
    assert len(calls) == final_call and caught.value is primary and primary.__cause__ is cause
    assert primary.duration_s == pytest.approx(clock[0] - 10.0)
    expected = {"initial": (2, 3), "syntax": (7, 10), "continuation": (7, 10), "headroom": (13, 18)}[
        stage
    ]
    assert (primary.observed_usage.input_tokens, primary.observed_usage.output_tokens) == expected
    if stage == "headroom":
        assert calls == [10, 10, 10, 20]


@pytest.mark.parametrize("strict_stop", [False, True], ids=["ordinary-fallback", "strict-stop"])
def test_count_refusal_duration_includes_each_request_once(monkeypatch, strict_stop):
    clock = [10.0]
    monkeypatch.setattr(invoke.time, "monotonic", lambda: clock[0])
    backend = _backend(monkeypatch)
    scope = SimpleNamespace(strict=True, disabled=False)
    primary = invoke.ProviderRefusalError(backend, FOLLOWUP_USAGE)
    original = count_reply()
    calls = []

    def transport(*args, **kwargs):
        calls.append(invoke._CORRECTION_READ_POLICY.get() is not None)
        if len(calls) == 1:
            clock[0] = 12.0
            return json.dumps(original), INITIAL_USAGE
        clock[0] = 14.5
        scope.disabled = strict_stop
        raise primary

    monkeypatch.setattr(invoke, "_invoke_openai", transport)
    kwargs = dict(
        backend=backend,
        max_attempts=1,
        initial_delay_s=0,
        reply_keys=REVIEW_REPLY_KEYS,
        review_diff=source_diff(),
        _audit_scope=scope,
    )
    if strict_stop:
        with pytest.raises(invoke.ProviderRefusalError) as caught:
            invoke.llm_invoke("synthetic count duration review", **kwargs)
        assert caught.value is primary and primary.duration_s == 4.5
        assert (primary.observed_usage.input_tokens, primary.observed_usage.output_tokens) == (7, 10)
    else:
        result = invoke.llm_invoke("synthetic count duration review", **kwargs)
        assert result.content == original and result.duration_s == 4.5
        assert primary.duration_s == 2.5
        assert (result.usage.input_tokens, result.usage.output_tokens) == (7, 10)
    assert calls == [False, True]
