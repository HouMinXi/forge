# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Source-proven count regeneration through the real acquisition path."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from code_forge import llm_invoke as invoke
from code_forge.baseline import ResolvedReview
from code_forge.factories import build_grouped_l1_provider, build_l1_provider
from code_forge.reviewer_json import REVIEW_REPLY_KEYS
from code_forge.verify import ExcerptStatus, _diff_validation_context, assess_excerpt_evidence
from tests import test_review_pass_evidence_contract as contract
from tests.test_literal_excerpt_factory import _backend, _provider

isolated_execution = contract.isolated_execution

LINES = [f"value_{number:03d} = {number}" for number in range(1, 151)]


def source_diff(lines=LINES):
    return (
        "diff --git a/a.py b/a.py\nnew file mode 100644\n--- /dev/null\n+++ b/a.py\n"
        f"@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{line}\n" for line in lines)
    )


def count_reply(lines=LINES):
    return {
        "findings": [
            {
                "file": "a.py",
                "line": 60,
                "severity": "P2",
                "description": "controlled count candidate",
                "excerpt": lines[59],
            }
        ],
        "code_excerpts": [
            {"file": "a.py", "start_line": 1, "end_line": 50, "content": "\n".join(lines[:51])},
            {"file": "a.py", "start_line": 60, "end_line": 99, "content": "\n".join(lines[59:93])},
            {"file": "a.py", "start_line": 110, "end_line": 129, "content": "\n".join(lines[109:134])},
        ],
    }


def corrected_reply(previous=None):
    candidate = copy.deepcopy(count_reply() if previous is None else previous)
    candidate["code_excerpts"][1]["content"] = "\n".join(LINES[59:99])
    candidate["code_excerpts"][2]["content"] = "\n".join(LINES[109:129])
    return candidate


def install_transport(monkeypatch, original, replacement=None, *, error=None, fenced=False):
    calls = []

    def transport(prompt, backend, key, timeout_s, **kwargs):
        correction = invoke._CORRECTION_READ_POLICY.get() is not None
        calls.append({"correction": correction, "prompt": prompt, "backend": backend})
        if correction and error is not None:
            raise error
        payload = replacement if correction else original
        raw = json.dumps(payload, ensure_ascii=True)
        if fenced and not correction:
            raw = "```json\n" + raw + "\n```"
        usage = (
            {"prompt_tokens": 5, "completion_tokens": 7}
            if correction
            else {"prompt_tokens": 2, "completion_tokens": 3}
        )
        return raw, usage

    monkeypatch.setattr(invoke, "_invoke_openai", transport)
    return calls


def test_complete_count_regeneration_changes_only_two_contents(monkeypatch):
    original = count_reply()
    snapshot = copy.deepcopy(original)
    candidate = corrected_reply(original)
    diff = source_diff()
    post, hunks, exempt = _diff_validation_context(diff, cwd=None)
    assert (
        assess_excerpt_evidence(copy.deepcopy(original["code_excerpts"][0]), hunks, post, exempt).status
        is ExcerptStatus.VALID
    )
    calls = install_transport(monkeypatch, original, candidate)
    provider = _provider(_backend(monkeypatch), diff)
    findings, excerpts, usage, _ = provider()
    assert len(calls) == 6 and sum(call["correction"] for call in calls) == 3
    assert original == snapshot
    assert len(findings) == 3 and all(f.excerpt == LINES[59] for f in findings)
    assert [excerpt["content"] for excerpt in excerpts] == [
        exc["content"] for exc in candidate["code_excerpts"]
    ] * 3
    assert all(
        excerpts[index]["content"] == original["code_excerpts"][0]["content"] for index in (0, 3, 6)
    )
    assert (usage.input_tokens, usage.output_tokens) == (21, 30)
    assert len(provider.correction_observations) == 3
    for record in provider.correction_observations:
        assert record["kind"] == "count-excerpt-correction" and record["selected"] is True
        assert record["indexes"] == [1, 2]
        assert json.loads(record["initial_raw"]) == original
        assert json.loads(record["correction_raw"]) == candidate
    assert provider.attempted_excerpts == provider.raw_observations == []


@pytest.mark.parametrize(
    "damage",
    [
        "drop-finding",
        "change-finding",
        "coordinates",
        "order",
        "partial-root",
        "one-good-fragment",
        "still-wrong",
        "unaffected-content",
        "host-tail",
    ],
)
def test_invalid_replacement_keeps_whole_original_and_never_stacks(monkeypatch, damage):
    original = count_reply()
    candidate = corrected_reply(original)
    if damage == "drop-finding":
        candidate["findings"] = []
    elif damage == "change-finding":
        candidate["findings"][0]["description"] = "replacement changed the candidate"
    elif damage == "coordinates":
        candidate["code_excerpts"][1]["end_line"] += 1
    elif damage == "order":
        candidate["code_excerpts"][1:] = reversed(candidate["code_excerpts"][1:])
    elif damage == "partial-root":
        del candidate["findings"]
    elif damage == "one-good-fragment":
        candidate["code_excerpts"][2] = copy.deepcopy(original["code_excerpts"][2])
    elif damage == "still-wrong":
        candidate["code_excerpts"][1]["content"] = candidate["code_excerpts"][1]["content"].replace(
            "value_070", "other_070"
        )
    elif damage == "unaffected-content":
        candidate["code_excerpts"][0]["content"] = "\n".join(LINES[:50])
    else:
        candidate["code_excerpts"][1]["content"] = candidate["code_excerpts"][1]["content"][:-1]
    calls = install_transport(monkeypatch, original, candidate)
    provider = _provider(_backend(monkeypatch), source_diff())
    findings, excerpts, usage, _ = provider()
    assert len(calls) == 6 and excerpts == []
    assert findings and all(f.source == "UNTRUSTED" for f in findings)
    assert all(
        attempt == original | {"pass_name": name}
        for attempt, name in zip(
            provider.attempted_excerpts, ("qodo", "expert", "adversarial"), strict=True
        )
    )
    assert all(record["selected"] is False for record in provider.correction_observations)
    assert (usage.input_tokens, usage.output_tokens) == (21, 30)


@pytest.mark.parametrize(
    "damage",
    [
        "unknown-file",
        "huge-coordinate",
        "offset",
        "repeated",
        "unknown-gap",
        "invalid-finding",
        "invalid-excerpt",
        "aliases",
        "unrelated-untrusted",
        "fenced",
        "no-source",
    ],
)
def test_ineligible_count_reply_has_zero_added_requests(monkeypatch, damage):
    lines = list(LINES)
    if damage == "repeated":
        lines[:34] = lines[59:93]
    original = count_reply(lines)
    diff = source_diff(lines)
    if damage == "unknown-file":
        original["code_excerpts"][1]["file"] = "other.py"
    elif damage == "huge-coordinate":
        original["code_excerpts"][1]["end_line"] = 2**63 - 1
    elif damage == "offset":
        original["code_excerpts"][1]["content"] = "\n".join(lines[60:94])
    elif damage == "unknown-gap":
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
        for start, part in ((1, lines[:60]), (91, lines[90:])):
            diff += (
                f"@@ -{start},60 +{start},60 @@\n"
                + "".join(f"-old_{start + i}\n" for i in range(60))
                + "".join(f"+{line}\n" for line in part)
            )
    elif damage == "invalid-finding":
        original["findings"][0]["severity"] = "P9"
    elif damage == "invalid-excerpt":
        original["code_excerpts"][2] = None
    elif damage == "aliases":
        for excerpt in original["code_excerpts"]:
            excerpt["line_start"] = excerpt.pop("start_line")
    elif damage == "unrelated-untrusted":
        original["code_excerpts"][0]["content"] = "\n".join("    " + line for line in lines[:51])
    elif damage == "no-source":
        diff = "diff --git a/a.py b/a.py\nold mode 100644\nnew mode 100755\n"
    calls = install_transport(monkeypatch, original, corrected_reply(), fenced=damage == "fenced")
    provider = _provider(_backend(monkeypatch), diff)
    _, excerpts, usage, _ = provider()
    assert len(calls) == 3 and not any(call["correction"] for call in calls)
    assert provider.correction_observations == [] and excerpts == []
    assert (usage.input_tokens, usage.output_tokens) == (6, 9)


def test_failed_count_request_keeps_original_and_observed_cost(monkeypatch):
    original = count_reply()
    error = invoke.LLMInvokeError(
        "controlled correction failure", retryable=False, observed_usage=invoke.Usage(5, 7, 0)
    )
    calls = install_transport(monkeypatch, original, error=error)
    provider = _provider(_backend(monkeypatch), source_diff())
    _, excerpts, usage, _ = provider()
    assert len(calls) == 6 and excerpts == []
    assert (usage.input_tokens, usage.output_tokens) == (21, 30)
    assert all(
        record["selected"] is False and record["reason"] == "correction_failed"
        for record in provider.correction_observations
    )
    assert len(provider.attempted_excerpts) == 3 and provider.raw_observations == []


@pytest.mark.parametrize("slots,expected_corrections", [(0, 0), (1, 1)])
def test_passes_share_existing_allowance_without_extra_credit(monkeypatch, slots, expected_corrections):
    original = count_reply()
    calls = install_transport(monkeypatch, original, corrected_reply(original))
    backend = _backend(monkeypatch)
    provider = build_l1_provider(
        "real",
        ResolvedReview([Path("a.py")], None, source_diff(), "git"),
        backend=backend,
        max_attempts=1,
        initial_delay_s=0,
        excerpt_allowance=invoke._ExcerptCorrectionAllowance(slots),
    )
    _, excerpts, usage, _ = provider()
    assert len(calls) == 3 + expected_corrections
    assert len(excerpts) == 3 * expected_corrections
    assert len(provider.attempted_excerpts) == 3 - expected_corrections
    assert len(provider.correction_observations) == expected_corrections
    assert (usage.input_tokens, usage.output_tokens) == (
        6 + 5 * expected_corrections,
        9 + 7 * expected_corrections,
    )


def test_grouped_correction_cap_and_existing_owner_lifetime_are_unchanged(monkeypatch):
    original = count_reply()
    calls = install_transport(monkeypatch, original, corrected_reply(original))
    backend = _backend(monkeypatch)
    groups = [
        {
            "name": f"group-{index}",
            "resolved": ResolvedReview([Path("a.py")], None, source_diff(), "git"),
        }
        for index in range(3)
    ]
    provider = build_grouped_l1_provider(
        "real", groups, backend=backend, max_attempts=1, initial_delay_s=0
    )
    provider()
    assert len(calls) == 17 and sum(call["correction"] for call in calls) == 8
    assert len(provider.correction_observations) == 8 and len(provider.attempted_excerpts) == 1
    provider()
    assert len(calls) == 26 and sum(call["correction"] for call in calls) == 8
    assert provider.correction_observations == [] and len(provider.attempted_excerpts) == 9


def test_missing_repair_cannot_stack_a_count_request(monkeypatch):
    original = count_reply()
    # A finding that carries its own quote is now evidence by itself (#521),
    # so the reply is only missing its excerpts when the findings have none.
    initial = {
        "findings": [
            {key: value for key, value in finding.items() if key != "excerpt"}
            for finding in original["findings"]
        ]
    }
    calls = []

    def transport(prompt, backend, key, timeout_s, **kwargs):
        missing_repair = "without required code_excerpts" in prompt
        assert invoke._CORRECTION_READ_POLICY.get() is None
        calls.append(missing_repair)
        return json.dumps(original if missing_repair else initial), {
            "prompt_tokens": 5 if missing_repair else 2,
            "completion_tokens": 7 if missing_repair else 3,
        }

    monkeypatch.setattr(invoke, "_invoke_openai", transport)
    provider = _provider(_backend(monkeypatch), source_diff())
    _, excerpts, usage, _ = provider()
    assert len(calls) == 6 and calls.count(True) == 3 and excerpts == []
    assert provider.correction_observations == [] and len(provider.attempted_excerpts) == 3
    assert (usage.input_tokens, usage.output_tokens) == (21, 30)


@pytest.mark.parametrize("kind", ["count", "literal"])
def test_expired_prompt_preparation_spends_no_slot_or_request(monkeypatch, kind):
    from code_forge import excerpt_count_recovery
    from tests.test_literal_excerpt_factory import BAD, DIFF, _envelope

    previous = count_reply() if kind == "count" else json.loads(_envelope(BAD))
    diff = source_diff() if kind == "count" else DIFF
    result = invoke.LLMResult(previous, invoke.Usage(2, 3, 0), 0.0, raw_response=json.dumps(previous))
    clock = [100.0]
    monkeypatch.setattr(invoke.time, "monotonic", lambda: clock[0])

    def prepare(*args):
        clock[0] = 111.0
        return "controlled prompt that completed after D"

    if kind == "count":
        monkeypatch.setattr(excerpt_count_recovery, "count_correction_prompt", prepare)
    else:
        monkeypatch.setattr(invoke, "_correction_prompt", prepare)
    monkeypatch.setattr(
        invoke, "llm_invoke", lambda *a, **k: pytest.fail("expired preparation sent a request")
    )
    allowance = invoke._ExcerptCorrectionAllowance(1)
    output = invoke._maybe_correct_literal_excerpts(
        result,
        prompt="controlled original",
        backend=_backend(monkeypatch),
        reply_keys=REVIEW_REPLY_KEYS,
        review_diff=diff,
        allowance=allowance,
        entry_started_at=100.0,
        entry_deadline=110.0,
        audit_scope=None,
        timeout_s=10,
    )
    assert output is result and allowance.reserve() is True
    assert output.excerpt_diagnostics == ()


def test_original_window_expired_during_initial_acquisition_has_no_added_cost(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(invoke.time, "monotonic", lambda: clock[0])
    calls = []

    def transport(prompt, backend, key, timeout_s, **kwargs):
        assert invoke._CORRECTION_READ_POLICY.get() is None
        calls.append(prompt)
        clock[0] += 11.0
        return json.dumps(count_reply()), {"prompt_tokens": 2, "completion_tokens": 3}

    monkeypatch.setattr(invoke, "_invoke_openai", transport)
    allowance = invoke._ExcerptCorrectionAllowance(1)
    result = invoke.llm_invoke(
        "controlled review",
        backend=_backend(monkeypatch),
        max_attempts=1,
        initial_delay_s=0,
        reply_keys=REVIEW_REPLY_KEYS,
        review_diff=source_diff(),
        _excerpt_allowance=allowance,
    )
    assert len(calls) == 1 and allowance.reserve() is True
    assert result.excerpt_diagnostics == ()
    assert (result.usage.input_tokens, result.usage.output_tokens) == (2, 3)


def test_absolute_deadline_blocks_opener_entry_and_cannot_be_renewed(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(invoke.time, "monotonic", lambda: clock[0])
    openings = []
    monkeypatch.setattr(
        invoke.urllib.request,
        "build_opener",
        lambda *a: SimpleNamespace(
            open=lambda req, **kwargs: openings.append(kwargs) or "controlled response"
        ),
    )
    policy = invoke._CorrectionReadPolicy(deadline=105.0)
    token = invoke._CORRECTION_READ_POLICY.set(policy)
    try:
        assert invoke._correction_transport_deadline(1000) == 105.0
        assert invoke._open_url(object(), 1000) == "controlled response"
        assert openings == [{"timeout": 5.0}]
        clock[0] = 104.5
        assert invoke._correction_transport_deadline(1000) == 105.0
        clock[0] = 105.0
        with pytest.raises(invoke.SendDeadlineError):
            invoke._open_url(object(), 1000)
        assert len(openings) == 1
    finally:
        invoke._CORRECTION_READ_POLICY.reset(token)


def test_correction_retains_entry_deadline_and_does_not_mutate_backend(monkeypatch):
    import dataclasses

    clock = [100.0]
    monkeypatch.setattr(invoke.time, "monotonic", lambda: clock[0])
    backend = _backend(monkeypatch)
    before = dataclasses.asdict(backend)
    calls = []

    def transport(prompt, current, key, timeout_s, **kwargs):
        policy = invoke._CORRECTION_READ_POLICY.get()
        calls.append(policy is not None)
        if policy is None:
            clock[0] = 102.0
            payload = count_reply()
        else:
            assert policy.deadline == 110.0
            assert current is not backend and current.timeout_s == timeout_s == 8
            assert current.output_ceiling <= min(8192, backend.output_ceiling or backend.max_tokens)
            clock[0] = 106.0
            assert invoke._correction_transport_deadline(timeout_s) == 110.0
            payload = corrected_reply()
        return json.dumps(payload), {"prompt_tokens": 2, "completion_tokens": 3}

    monkeypatch.setattr(invoke, "_invoke_openai", transport)
    result = invoke.llm_invoke(
        "controlled review",
        backend=backend,
        max_attempts=1,
        initial_delay_s=0,
        reply_keys=REVIEW_REPLY_KEYS,
        review_diff=source_diff(),
    )
    assert calls == [False, True]
    assert result.content == corrected_reply()
    assert (result.usage.input_tokens, result.usage.output_tokens) == (4, 6)
    assert dataclasses.asdict(backend) == before


def test_read_boundary_uses_absolute_deadline_even_with_larger_caller_window(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(invoke.time, "monotonic", lambda: clock[0])
    joins = []
    reads = []

    class CompletedThread:
        def __init__(self, *, target, daemon):
            self.target = target

        def start(self):
            self.target()

        def join(self, *, timeout):
            joins.append(timeout)

        def is_alive(self):
            return False

    monkeypatch.setattr(invoke.threading, "Thread", CompletedThread)
    policy = invoke._CorrectionReadPolicy(deadline=105.0)
    token = invoke._CORRECTION_READ_POLICY.set(policy)
    try:
        assert (
            invoke._read_with_deadline(
                object(), 1000.0, "controlled", read=lambda: reads.append(True) or b"payload"
            )
            == b"payload"
        )
        assert joins == [5.0] and reads == [True]
        clock[0] = 105.0
        with pytest.raises(invoke.LLMInvokeError) as caught:
            invoke._read_with_deadline(
                object(),
                1000.0,
                "controlled",
                read=lambda: pytest.fail("expired deadline entered a reader"),
            )
        assert caught.value.is_timeout and caught.value.retryable is False
        assert joins == [5.0] and reads == [True]
    finally:
        invoke._CORRECTION_READ_POLICY.reset(token)


def test_small_raw_cannot_make_oversized_native_integers_reach_canonicalization(monkeypatch):
    from code_forge.excerpt_count_recovery import count_excerpt_indexes

    content = count_reply() | {"metadata": [1 << 4095] * 1000}
    raw = json.dumps(count_reply())

    def forbidden(*args, **kwargs):
        pytest.fail("native JSON byte budget must reject before decode/canonicalization")

    monkeypatch.setattr(invoke, "_decode_reply", forbidden)
    monkeypatch.setattr(invoke, "_canonical_json", forbidden)
    assert count_excerpt_indexes(content, raw, REVIEW_REPLY_KEYS, source_diff()) == ()


@pytest.mark.parametrize("kind", ["count", "literal"])
def test_strict_audit_failure_keeps_original_primary_identity(monkeypatch, kind):
    import time

    if kind == "count":
        payload, diff = count_reply(), source_diff()
    else:
        from tests.test_literal_excerpt_factory import BAD, DIFF, _envelope

        payload, diff = json.loads(_envelope(BAD)), DIFF
    primary = invoke.LLMInvokeError("synthetic private audit failure", retryable=False)

    def fail(*args, **kwargs):
        raise primary

    monkeypatch.setattr(invoke, "llm_invoke", fail)
    started = time.monotonic()
    with pytest.raises(invoke.LLMInvokeError) as caught:
        invoke._maybe_correct_literal_excerpts(
            invoke.LLMResult(payload, raw_response=json.dumps(payload)),
            prompt="synthetic review",
            backend=_backend(monkeypatch),
            reply_keys=REVIEW_REPLY_KEYS,
            review_diff=diff,
            allowance=invoke._ExcerptCorrectionAllowance(1),
            entry_started_at=started,
            entry_deadline=started + 10,
            audit_scope=SimpleNamespace(strict=True, disabled=True),
            timeout_s=10,
        )
    assert caught.value is primary


def test_expired_opener_construction_never_enters_transport(monkeypatch):
    clock = [0.0]
    opened = []

    def build(*args):
        clock[0] = 2.0
        return SimpleNamespace(open=lambda *args, **kwargs: opened.append(True))

    monkeypatch.setattr(invoke.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(invoke.urllib.request, "build_opener", build)
    token = invoke._CORRECTION_READ_POLICY.set(invoke._CorrectionReadPolicy(deadline=1.0))
    try:
        with pytest.raises(invoke.SendDeadlineError):
            invoke._open_url(object(), 100)
    finally:
        invoke._CORRECTION_READ_POLICY.reset(token)
    assert opened == []


def test_truncated_count_request_retains_already_observed_usage_without_continuation(monkeypatch):
    error = invoke._TruncatedResponse(
        "controlled correction truncation",
        content="{",
        usage_data={"prompt_tokens": 5, "completion_tokens": 7},
        resolved_cap=10,
        kind="truncated",
        retryable=False,
    )
    calls = install_transport(monkeypatch, count_reply(), error=error)
    provider = _provider(_backend(monkeypatch), source_diff())
    _, excerpts, usage, _ = provider()
    assert len(calls) == 6 and sum(call["correction"] for call in calls) == 3
    assert excerpts == [] and len(provider.attempted_excerpts) == 3
    assert (usage.input_tokens, usage.output_tokens) == (21, 30)
    assert all(row["input"] == 7 and row["output"] == 10 for row in provider.pass_usage)
    assert all(record["selected"] is False for record in provider.correction_observations)


@pytest.mark.parametrize("kind", ["strict-primary", "audit", "send-deadline"])
def test_escaping_control_failure_retains_initial_and_failed_observed_cost(monkeypatch, kind):
    import time

    if kind == "audit":
        primary = invoke.AuditPersistenceError()
    elif kind == "send-deadline":
        primary = invoke.SendDeadlineError()
    else:
        primary = invoke.LLMInvokeError("controlled strict failure", retryable=False)
    primary.observed_usage = invoke.Usage(7, 11, 0)

    def fail(*args, **kwargs):
        raise primary

    monkeypatch.setattr(invoke, "llm_invoke", fail)
    started = time.monotonic()
    payload = count_reply()
    with pytest.raises(invoke.LLMInvokeError) as caught:
        invoke._maybe_correct_literal_excerpts(
            invoke.LLMResult(
                payload, usage=invoke.Usage(2, 3, 0), duration_s=0.5, raw_response=json.dumps(payload)
            ),
            prompt="synthetic review",
            backend=_backend(monkeypatch),
            reply_keys=REVIEW_REPLY_KEYS,
            review_diff=source_diff(),
            allowance=invoke._ExcerptCorrectionAllowance(1),
            entry_started_at=started,
            entry_deadline=started + 10,
            audit_scope=SimpleNamespace(strict=True, disabled=kind == "strict-primary"),
            timeout_s=10,
        )
    assert caught.value is primary
    assert (primary.observed_usage.input_tokens, primary.observed_usage.output_tokens) == (9, 14)
    assert primary.usage is primary.observed_usage
    assert primary.duration_s >= 0.5


def test_real_audit_wrapper_keeps_successful_followup_cost_when_finish_fails(monkeypatch):
    import time

    from code_forge import invocation_send_audit
    from code_forge.invocation_stream import CaptureUnavailable

    failure = CaptureUnavailable("controlled audit finalization failure")
    finished = []

    class Transport:
        def __init__(self, *args, **kwargs):
            pass

        def finish(self, *, primary, usage):
            assert primary is None
            finished.append(usage)
            raise failure

    monkeypatch.setattr(invocation_send_audit, "OpenAiSendTransport", Transport)
    monkeypatch.setattr(
        invoke,
        "_invoke_openai_impl",
        lambda *args, **kwargs: (
            json.dumps(corrected_reply()),
            {"prompt_tokens": 5, "completion_tokens": 7},
        ),
    )
    started = time.monotonic()
    payload = count_reply()
    with pytest.raises(invoke.AuditPersistenceError) as caught:
        invoke._maybe_correct_literal_excerpts(
            invoke.LLMResult(payload, usage=invoke.Usage(2, 3, 0), raw_response=json.dumps(payload)),
            prompt="synthetic review",
            backend=_backend(monkeypatch),
            reply_keys=REVIEW_REPLY_KEYS,
            review_diff=source_diff(),
            allowance=invoke._ExcerptCorrectionAllowance(1),
            entry_started_at=started,
            entry_deadline=started + 10,
            audit_scope=SimpleNamespace(strict=True, disabled=False),
            timeout_s=10,
        )
    assert caught.value.__cause__ is failure
    assert len(finished) == 1
    assert (finished[0].input_tokens, finished[0].output_tokens) == (5, 7)
    assert (caught.value.observed_usage.input_tokens, caught.value.observed_usage.output_tokens) == (
        7,
        10,
    )


def test_parallel_count_calls_compete_for_one_existing_eight_slot_allowance(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    barrier = threading.Barrier(12)
    lock = threading.Lock()
    calls = []
    original, corrected = count_reply(), corrected_reply()

    def transport(*args, **kwargs):
        correction = invoke._CORRECTION_READ_POLICY.get() is not None
        with lock:
            calls.append(correction)
        if not correction:
            barrier.wait(timeout=10)
        return (
            json.dumps(corrected if correction else original),
            {"prompt_tokens": 5 if correction else 2, "completion_tokens": 7 if correction else 3},
        )

    monkeypatch.setattr(invoke, "_invoke_openai", transport)
    backend = _backend(monkeypatch)
    allowance = invoke._ExcerptCorrectionAllowance(8)

    def acquire(_):
        return invoke.llm_invoke(
            "controlled parallel review",
            backend=backend,
            max_attempts=1,
            initial_delay_s=0,
            reply_keys=REVIEW_REPLY_KEYS,
            review_diff=source_diff(),
            _excerpt_allowance=allowance,
        )

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(acquire, range(12)))
    assert len(calls) == 20 and sum(calls) == 8
    assert sum(result.content == corrected for result in results) == 8
    assert sum(result.content == original for result in results) == 4
    assert sum(result.usage.input_tokens for result in results) == 64
    assert sum(result.usage.output_tokens for result in results) == 92
    assert allowance.reserve() is False


def test_replacement_cannot_extend_target_evidence_past_its_declared_span(monkeypatch):
    original = count_reply()
    candidate = corrected_reply(original)
    # Existing count tolerance would accept this 40/41 quote and prove line100,
    # but this regeneration was granted only the original 60-99 source range.
    candidate["code_excerpts"][1]["content"] = "\n".join(LINES[59:100])
    calls = install_transport(monkeypatch, original, candidate)
    provider = _provider(_backend(monkeypatch), source_diff())
    _, excerpts, _, _ = provider()
    assert len(calls) == 6 and excerpts == []
    assert len(provider.attempted_excerpts) == 3
    assert all(record["selected"] is False for record in provider.correction_observations)
