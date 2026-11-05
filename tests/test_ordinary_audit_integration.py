"""Ordinary factories/machine -> real invocation/host/owner, MOCK storage.

The transport is in-memory and routes by the actual request prompt, never by
thread arrival order. These tests do not admit a filesystem, contact a
provider, or establish whole-review recording/strict guarantees. Isolated CLI
and privacy seams at the end are not evidence for the real invocation chain.
"""

from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

from code_forge import cli, invocation_host, invocation_send_audit, llm_invoke
from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.factories import _L1Call, build_grouped_l1_provider, build_l1_provider
from code_forge.falsify import StubFalsifier
from code_forge.falsify_real import RealFalsifier
from code_forge.invocation_review import OrdinaryReviewAudit, ReviewAuditDiagnostics
from code_forge.llm_invoke import AuditPersistenceError, LLMInvokeError, Usage
from code_forge.machine import StateMachine
from code_forge.source import compute_source_hash
from code_forge.state import Mode, State, Verdict
from tests import test_invocation_host as host_tests, test_invocation_send_audit as send_tests

MockRecorder = host_tests.MockRecorder
host_setup = host_tests.setup
api_backend = send_tests.backend

DIFF = (
    "diff --git a/sample.py b/sample.py\n--- a/sample.py\n+++ b/sample.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
)
PASSES = ("qodo", "expert", "adversarial")
PRIVATE = "PRIVATE_RESPONSE_SENTINEL bearer PRIVATE_TOKEN /private/audit/path"


def resolved():
    return ResolvedReview(
        source_files=[Path("sample.py")], baseline_content=None, git_diff=DIFF, mode_hint="git"
    )


def review_reply(name, *, finding=True):
    return {
        "findings": [
            dict(
                file="sample.py",
                line=1,
                severity="P2",
                description=f"{name} candidate",
                excerpt="x = 2",
            )
        ]
        if finding
        else [],
        "code_excerpts": [dict(file="sample.py", start_line=1, end_line=1, content="x = 2")],
    }


def wire(content, *, tokens=1):
    return send_tests.Response(
        json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(content),
                            "reasoning_content": PRIVATE,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": tokens, "completion_tokens": tokens + 1},
            }
        ).encode()
    )


def request_lane(request):
    prompt = json.loads(request.data)["messages"][0]["content"]
    if prompt.startswith("You are a code review verifier."):
        return "falsify", prompt
    for name, role in (
        ("qodo", "structural code reviewer"),
        ("expert", "senior engineer: SOLID"),
        ("adversarial", "adversarial QE"),
    ):
        if "You are a " + role in prompt:
            return name, prompt
    # Only the qodo fixture truncates. Its short continuation prompt carries
    # this candidate text as fenced response data instead of the original role.
    if "<partial>" in prompt and "qodo candidate" in prompt:
        return "qodo", prompt
    raise AssertionError("request does not belong to an ordinary review pass")


class RoutedTransport:
    """Only HTTP transport is replaced; factories and llm_invoke stay real."""

    def __init__(self, monkeypatch, respond=None):
        self.requests = []
        self.counts = {}
        self.lock = threading.Lock()
        self.respond = respond or self.default_response
        monkeypatch.setattr(llm_invoke.urllib.request, "urlopen", self.open_plain)
        monkeypatch.setattr(invocation_send_audit.urllib.request, "build_opener", lambda *_: self)

    @staticmethod
    def default_response(lane, _prompt, _number):
        if lane == "falsify":
            return wire({"verdict": "DISMISSED", "reasoning": "The change is intentional."})
        return wire(review_reply(lane), tokens=PASSES.index(lane) + 1)

    def _open(self, request, *, audited):
        lane, prompt = request_lane(request)
        with self.lock:
            self.counts[lane] = number = self.counts.get(lane, 0) + 1
            self.requests.append((lane, request, audited))
        response = self.respond(lane, prompt, number)
        if isinstance(response, BaseException):
            raise response
        return response

    def open(self, request, *, timeout):
        return self._open(request, audited=True)

    def open_plain(self, request, *, timeout):
        return self._open(request, audited=False)


@pytest.fixture
def ordinary_host(monkeypatch, host_setup):
    """Replace the storage construction boundary, retaining real host/owner."""
    make, _ = host_setup
    hosts, factories, reviews = [], [], []

    def create(*, context, manifest, strict, environ):
        factories.append(dict(context=context, manifest=manifest, strict=strict, environ=environ))
        host, _ = make(
            strict=strict,
            control_timeout_s=2,
            flush_timeout_s=2,
            warn=lambda _: print(
                "code-forge: invocation audit unavailable; recording is incomplete", file=sys.stderr
            ),
        )
        host.bind_source(context, manifest=manifest)
        hosts.append(host)
        return host

    monkeypatch.setattr(invocation_host.HostAuditSession, "from_user_state", create)

    def review(**kwargs):
        result = OrdinaryReviewAudit(source=DIFF, environ={}, **kwargs)
        reviews.append(result)
        return result

    yield SimpleNamespace(review=review, hosts=hosts, factories=factories)
    for review in reviews:
        review.finalize(primary=RuntimeError("fixture cleanup"))


def provider(backend, **kwargs):
    return build_l1_provider(
        "real", resolved(), backend=backend, max_attempts=1, initial_delay_s=0, **kwargs
    )


def machine(tmp_path, backend, review, *, round_index=0, l1=None):
    result = StateMachine(
        mode=Mode.CI,
        falsifier=RealFalsifier(backend=backend, diff_text=DIFF),
        autofixer=StubAutoFixer(),
        revert_fn=lambda _: None,
        resolved_review=resolved(),
        source_hash=compute_source_hash(git_diff=DIFF),
        baseline_spec_repr="fixture",
        cwd=tmp_path,
        registry={},
        l1_provider=provider(backend) if l1 is None else l1,
        invocation_audit=review,
    )
    result._state = State(round=round_index)
    return result


def test_real_l1_three_pass_attribution_order_prompt_parity_usage_and_private_raw(
    ordinary_host, api_backend, monkeypatch, capsys
):
    transport = RoutedTransport(monkeypatch)
    plain = provider(api_backend)
    expected = plain()
    plain_requests = {lane: request.data for lane, request, audited in transport.requests if not audited}
    review = ordinary_host.review()
    call = provider(api_backend)
    actual = call.call_with_audit(review.round(4))
    review.finalize()

    assert type(call) is _L1Call and call.supports_invocation_audit
    assert [asdict(f) for f in actual[0]] == [asdict(f) for f in expected[0]]
    assert [f.description for f in actual[0]] == [f"[{name}] {name} candidate" for name in PASSES]
    assert actual[1] == expected[1]
    assert [item["pass_name"] for item in actual[1]] == list(PASSES)
    assert actual[2] == expected[2] == Usage(6, 9)
    assert [row["pass_name"] for row in call.pass_usage] == list(PASSES)
    assert call.reviewer_responses == {name: 1 for name in PASSES}
    assert len(call.pending_raw_responses) == 3
    assert call.attempted_excerpts == call.acquisition_failures == []
    audited_requests = {lane: request.data for lane, request, audited in transport.requests if audited}
    assert audited_requests == plain_requests

    assert len(ordinary_host.factories) == len(MockRecorder.instances) == 1
    recorder = MockRecorder.instances[0]
    assert len(recorder.started) == len(recorder.finished) == 3
    contexts = {item["context"].pass_name: item for item in recorder.started}
    assert set(contexts) == set(PASSES)
    assert len({item["logical_id"] for item in recorder.started}) == 3
    for name, start in contexts.items():
        assert start["context"].round_index == 4
        assert start["context"].purpose == "review"
        assert start["context"].group_id is None
        assert start["context"].parent_logical_id is None
        assert start["cause"] == "initial" and start["parent_id"] is None
        request = next(
            request for lane, request, audited in transport.requests if lane == name and audited
        )
        expected_digest = hashlib.sha256(
            request.get_method().encode() + b"\0" + request.full_url.encode() + b"\0" + request.data
        ).hexdigest()
        assert start["request_digest"] == expected_digest
        assert start["request_bytes"] == len(audited_requests[name])
    assert recorder.manifest["exact_diff_sha256"] == hashlib.sha256(DIFF.encode()).hexdigest()
    assert b"".join(bytes(raw) for raw in recorder.stages.values()).count(PRIVATE.encode()) == 3
    assert ordinary_host.hosts[0].safe_summary().audit_complete
    assert PRIVATE not in capsys.readouterr().err


def test_real_repeated_l1_calls_and_rounds_share_one_source_but_fresh_logical_scopes(
    ordinary_host, api_backend, monkeypatch
):
    transport = RoutedTransport(monkeypatch)
    review = ordinary_host.review()
    call = provider(api_backend)
    earlier, later = review.round(2), review.round(7)
    for view in (later, earlier, earlier):
        findings, excerpts, usage, _ = call.call_with_audit(view)
        assert len(findings) == len(excerpts) == 3
        assert usage == Usage(6, 9)
        assert call.reviewer_responses == {name: 1 for name in PASSES}
    review.finalize()
    assert len(transport.requests) == 9
    assert len(ordinary_host.factories) == len(MockRecorder.instances) == 1
    starts = MockRecorder.instances[0].started
    assert [item["context"].round_index for item in starts] == [7] * 3 + [2] * 6
    assert len({item["logical_id"] for item in starts}) == 9
    assert len({item["context"].run_id for item in starts}) == 1
    assert len({item["context"].snapshot_id for item in starts}) == 1
    assert len({item["context"].source_hash for item in starts}) == 1


def test_real_streaming_l1_keeps_reasoning_and_wire_tail_private(
    ordinary_host, api_backend, monkeypatch, capsys
):
    backend = replace(api_backend, stream=True)

    def respond(lane, _prompt, _number):
        return send_tests.reply(
            json.dumps(review_reply(lane)), stream=True, tokens=PASSES.index(lane) + 1
        )

    transport = RoutedTransport(monkeypatch, respond)
    review = ordinary_host.review()
    findings, excerpts, usage, _ = provider(backend).call_with_audit(review.round(2))
    review.finalize()
    assert len(findings) == len(excerpts) == len(transport.requests) == 3
    assert all(f.source == "L1" for f in findings)
    assert usage == Usage(6, 6)
    raw = b"".join(bytes(raw) for raw in MockRecorder.instances[0].stages.values())
    assert raw.count(b"private-thinking") == raw.count(b"raw-tail") == 3
    assert ordinary_host.hosts[0].safe_summary().audit_complete
    assert "private-thinking" not in capsys.readouterr().err


def test_real_machine_repeated_rounds_keep_one_host_and_distinct_review_falsifier_scopes(
    ordinary_host, api_backend, monkeypatch, tmp_path
):
    transport = RoutedTransport(monkeypatch)
    review = ordinary_host.review()
    sm = machine(tmp_path, api_backend, review, round_index=2)
    first, _ = sm._run_l1_phase()
    sm._state.round = 7
    second, _ = sm._run_l1_phase()
    review.finalize()
    assert [asdict(f) for f in first] == [asdict(f) for f in second]
    assert all(f.disposition is Disposition.DISMISSED for f in second)
    assert len(transport.requests) == 12
    assert len(ordinary_host.factories) == len(MockRecorder.instances) == 1
    starts = MockRecorder.instances[0].started
    assert len(starts) == len({item["logical_id"] for item in starts}) == 12
    assert [item["context"].round_index for item in starts] == [2] * 6 + [7] * 6
    assert [item["context"].purpose for item in starts] == (["review"] * 3 + ["falsify"] * 3) * 2
    assert len({item["context"].run_id for item in starts}) == 1
    assert len({item["context"].snapshot_id for item in starts}) == 1


def test_real_machine_falsifier_scopes_have_machine_round_and_exclude_infra_untrusted(
    ordinary_host, api_backend, monkeypatch, tmp_path
):
    def respond(lane, prompt, number):
        if lane == "expert":
            return send_tests.http_error(status=400, raw=PRIVATE.encode())
        if lane == "adversarial":
            content = review_reply(lane)
            content["code_excerpts"][0]["end_line"] = 70
            content["code_excerpts"][0]["content"] = "\n".join(["wrong = 1"] * 85)
            return wire(content)
        return RoutedTransport.default_response(lane, prompt, number)

    transport = RoutedTransport(monkeypatch, respond)
    review = ordinary_host.review()
    sm = machine(tmp_path, api_backend, review, round_index=6)
    findings, _ = sm._run_l1_phase()
    review.finalize()
    assert {f.source for f in findings} == {"L1", "INFRA", "UNTRUSTED"}
    real = [f for f in findings if f.source == "L1"]
    assert len(real) == 1 and real[0].disposition is Disposition.DISMISSED
    assert transport.counts["falsify"] == 1
    assert all(audited for _, _, audited in transport.requests)
    starts = MockRecorder.instances[0].started
    falsify = [item for item in starts if item["context"].purpose == "falsify"]
    assert len(falsify) == 1
    assert falsify[0]["context"].round_index == 6
    assert falsify[0]["context"].pass_name is falsify[0]["context"].group_id is None
    assert all(item["context"].round_index == 6 for item in starts)
    assert len({item["logical_id"] for item in starts}) == len(starts)
    assert sm._round_input_tokens > 0 and sm._round_output_tokens > 0
    assert PRIVATE not in json.dumps([asdict(f) for f in findings])


@pytest.mark.parametrize("stage", ["open", "append", "finalize"])
def test_real_default_storage_failures_keep_results_and_fixed_warning(
    ordinary_host, api_backend, monkeypatch, capsys, stage
):
    monkeypatch.setattr(MockRecorder, "fail", stage)
    monkeypatch.setattr(MockRecorder, "error", OSError(PRIVATE))
    transport = RoutedTransport(monkeypatch)
    review = ordinary_host.review()
    findings, excerpts, usage, _ = provider(api_backend).call_with_audit(review.round(1))
    review.finalize()
    assert len(findings) == len(excerpts) == len(transport.requests) == 3
    assert usage == Usage(6, 9)
    assert all(f.source == "L1" for f in findings)
    assert all(audited for _, _, audited in transport.requests)
    assert not ordinary_host.hosts[0].safe_summary().audit_complete
    output = capsys.readouterr().err
    assert output.count("invocation audit unavailable; recording is incomplete") == 1
    assert PRIVATE not in output


def test_real_internal_strict_start_failure_propagates_without_infra_or_transport(
    ordinary_host, api_backend, monkeypatch
):
    monkeypatch.setattr(MockRecorder, "fail", "open")
    monkeypatch.setattr(MockRecorder, "error", OSError(PRIVATE))
    transport = RoutedTransport(monkeypatch)
    review = ordinary_host.review(strict=True)
    call = provider(api_backend)
    with pytest.raises(AuditPersistenceError) as caught:
        call.call_with_audit(review.round(1))
    assert review.diagnostics.contains(caught.value)
    assert call.acquisition_failures == []
    assert transport.requests == []
    review.finalize(primary=caught.value)


def test_real_setup_failure_keeps_selected_retry_privacy_and_no_capture_admission(
    ordinary_host, api_backend, monkeypatch, capsys
):
    setups = []

    def fail_setup(**kwargs):
        setups.append(kwargs)
        raise OSError(PRIVATE)

    monkeypatch.setattr(invocation_host.HostAuditSession, "from_user_state", fail_setup)

    def respond(lane, prompt, number):
        if number == 1:
            return send_tests.http_error(status=503, raw=PRIVATE.encode())
        return RoutedTransport.default_response(lane, prompt, number)

    transport = RoutedTransport(monkeypatch, respond)
    review = ordinary_host.review()
    call = build_l1_provider("real", resolved(), backend=api_backend, max_attempts=2, initial_delay_s=0)
    findings, _, usage, _ = call.call_with_audit(review.round(3))
    review.finalize()
    assert len(findings) == 3 and all(f.source == "L1" for f in findings)
    assert usage == Usage(6, 9)
    assert transport.counts == {name: 2 for name in PASSES}
    assert all(audited for _, _, audited in transport.requests)
    assert len(setups) == 1 and MockRecorder.instances == []
    output = capsys.readouterr().err
    assert output.count("invocation audit unavailable; recording is incomplete") == 1
    assert "retry" in output and PRIVATE not in output


@pytest.mark.parametrize("failure", ["http", "protocol", "runtime"])
def test_real_machine_falsifier_failures_remain_uncertain_and_public_safe(
    ordinary_host, api_backend, monkeypatch, tmp_path, capsys, failure
):
    def respond(lane, prompt, number):
        if lane != "falsify":
            return RoutedTransport.default_response(lane, prompt, number)
        if failure == "http":
            return send_tests.http_error(status=400, raw=PRIVATE.encode())
        if failure == "protocol":
            return wire({"verdict": PRIVATE, "reasoning": PRIVATE})
        return RuntimeError(PRIVATE)

    transport = RoutedTransport(monkeypatch, respond)
    review = ordinary_host.review()
    sm = machine(tmp_path, api_backend, review)
    findings, _ = sm._run_l1_phase()
    review.finalize()
    assert len(findings) == transport.counts["falsify"] == 3
    assert all(f.disposition is Disposition.UNCERTAIN and f.error for f in findings)
    assert sm._state.rounds_with_falsify_infra == 1
    public = json.dumps([asdict(f) for f in findings]) + json.dumps(sm._state.infra_errors)
    assert PRIVATE not in public + capsys.readouterr().err
    assert all(audited for _, _, audited in transport.requests)


def test_real_no_audit_call_retains_legacy_provider_diagnostics(api_backend, monkeypatch, capsys):
    transport = RoutedTransport(
        monkeypatch, lambda *_: send_tests.http_error(status=400, raw=PRIVATE.encode())
    )
    findings, _, _, _ = provider(api_backend)()
    assert len(findings) == 3 and all(f.source == "INFRA" for f in findings)
    assert all(not audited for _, _, audited in transport.requests)
    assert PRIVATE in capsys.readouterr().err


def test_real_machine_strict_falsifier_storage_failure_stops_later_selected_sends(
    ordinary_host, api_backend, monkeypatch, tmp_path
):
    monkeypatch.setenv("FORGE_FALSIFY_WORKERS", "1")

    def respond(lane, prompt, number):
        if lane == "falsify":
            monkeypatch.setattr(MockRecorder, "fail", "append")
            monkeypatch.setattr(MockRecorder, "error", OSError(PRIVATE))
        return RoutedTransport.default_response(lane, prompt, number)

    transport = RoutedTransport(monkeypatch, respond)
    review = ordinary_host.review(strict=True)
    sm = machine(tmp_path, api_backend, review, round_index=8)
    with pytest.raises(AuditPersistenceError) as caught:
        sm._run_l1_phase()
    assert review.diagnostics.contains(caught.value)
    assert transport.counts == {"qodo": 1, "expert": 1, "adversarial": 1, "falsify": 1}
    assert sm._falsify_failed_findings == []
    assert sm._state.infra_errors == []
    review.finalize(primary=caught.value)


@pytest.mark.parametrize("consumer", ["l1", "falsify"])
def test_real_typed_strict_primary_identity_survives_consumer_and_cleanup(
    ordinary_host, api_backend, monkeypatch, tmp_path, consumer
):
    monkeypatch.setenv("FORGE_FALSIFY_WORKERS", "1")
    primary = AuditPersistenceError()
    cause = OSError(PRIVATE)
    primary.__cause__ = cause

    def respond(lane, prompt, number):
        if lane == ("qodo" if consumer == "l1" else "falsify"):
            return primary
        return RoutedTransport.default_response(lane, prompt, number)

    transport = RoutedTransport(monkeypatch, respond)
    review = ordinary_host.review(strict=True)
    sm = machine(tmp_path, api_backend, review)
    with pytest.raises(AuditPersistenceError) as caught:
        sm._run_l1_phase()
    assert caught.value is primary and primary.__cause__ is cause
    assert review.diagnostics.contains(primary)
    assert sm._falsify_failed_findings == []
    assert sm.l1_provider.acquisition_failures == []
    assert transport.counts.get("falsify", 0) == (0 if consumer == "l1" else 1)
    monkeypatch.setattr(MockRecorder, "fail", "finalize")
    monkeypatch.setattr(MockRecorder, "error", RuntimeError(PRIVATE))
    review.finalize(primary=primary)
    assert primary.__cause__ is cause


def recovery_responses(branch):
    complete = json.dumps(review_reply("qodo"), separators=(",", ":"))
    partial = complete[: complete.index("qodo candidate") + len("qodo candidate")]
    if branch == "retry":
        return [send_tests.http_error(raw=PRIVATE.encode()), wire(review_reply("qodo"))], [
            "initial",
            "retry",
        ]
    if branch == "correction":
        return [send_tests.reply(send_tests.BROKEN), wire(review_reply("qodo"))], [
            "initial",
            "correction",
        ]
    if branch == "continuation":
        return [
            send_tests.reply(partial, finish="length"),
            send_tests.reply(complete[len(partial) :]),
        ], [
            "initial",
            "continuation",
        ]
    if branch == "headroom":
        return [
            send_tests.reply(partial, finish="length"),
            send_tests.reply("nonsense"),
            send_tests.reply("nonsense"),
            wire(review_reply("qodo")),
        ], ["initial", "continuation", "continuation", "headroom"]
    assert branch == "excerpt_repair"
    content = review_reply("qodo")
    del content["code_excerpts"]
    return [wire(content), wire(review_reply("qodo"))], ["initial", "excerpt_repair"]


@pytest.mark.parametrize("branch", ["retry", "correction", "continuation", "headroom", "excerpt_repair"])
@pytest.mark.parametrize("setup_failure", [False, True], ids=["recording", "unavailable"])
def test_real_l1_recovery_send_and_usage_parity_with_selected_or_unavailable_capture(
    ordinary_host, api_backend, monkeypatch, capsys, caplog, branch, setup_failure
):
    def transport_for_run():
        replies, causes = recovery_responses(branch)

        def respond(lane, prompt, number):
            if lane == "qodo":
                return replies[number - 1]
            return RoutedTransport.default_response(lane, prompt, number)

        return RoutedTransport(monkeypatch, respond), causes

    def build():
        return build_l1_provider(
            "real",
            resolved(),
            backend=api_backend,
            max_attempts=2,
            initial_delay_s=0,
        )

    plain_transport, causes = transport_for_run()
    plain = build()()
    capsys.readouterr()
    caplog.clear()
    if setup_failure:

        def unavailable(**_kwargs):
            raise OSError(PRIVATE)

        monkeypatch.setattr(invocation_host.HostAuditSession, "from_user_state", unavailable)
    transport, _ = transport_for_run()
    review = ordinary_host.review()
    actual = build().call_with_audit(review.round(5))
    review.finalize()
    assert [asdict(f) for f in actual[0]] == [asdict(f) for f in plain[0]]
    assert all(f.source == "L1" for f in actual[0]) and len(actual[0]) == 3
    assert actual[1:3] == plain[1:3]
    assert actual[2].input_tokens > 0 and actual[2].output_tokens > 0
    assert len(transport.requests) == len(plain_transport.requests) == len(causes) + 2
    assert transport.counts == dict(qodo=len(causes), expert=1, adversarial=1)
    assert all(audited for _, _, audited in transport.requests)
    for lane in PASSES:
        assert [request.data for name, request, _ in transport.requests if name == lane] == [
            request.data for name, request, _ in plain_transport.requests if name == lane
        ]
    if setup_failure:
        assert MockRecorder.instances == []
    else:
        starts = MockRecorder.instances[0].started
        assert len(starts) == len(transport.requests)
        qodo = [item for item in starts if item["context"].pass_name == "qodo"]
        assert [item["cause"] for item in qodo] == causes
        assert all(item["context"].round_index == 5 for item in qodo)
        assert ordinary_host.hosts[0].safe_summary().audit_complete
    output = capsys.readouterr().err
    assert PRIVATE not in output + caplog.text
    assert output.count("invocation audit unavailable; recording is incomplete") == int(setup_failure)


@pytest.mark.parametrize("branch", ["retry", "continuation_headroom"])
def test_real_unavailable_scope_keeps_private_provider_failure_out_of_recovery_logs(
    ordinary_host, api_backend, monkeypatch, capsys, caplog, branch
):
    def transport_for_run():
        complete = json.dumps(review_reply("qodo"), separators=(",", ":"))
        partial = complete[: complete.index("qodo candidate") + len("qodo candidate")]

        def respond(lane, prompt, number):
            if lane != "qodo":
                return RoutedTransport.default_response(lane, prompt, number)
            if branch == "continuation_headroom" and number == 1:
                return send_tests.reply(partial, finish="length")
            return send_tests.http_error(status=503 if branch == "retry" else 400, raw=PRIVATE.encode())

        return RoutedTransport(monkeypatch, respond)

    def build():
        return build_l1_provider(
            "real",
            resolved(),
            backend=api_backend,
            max_attempts=2,
            initial_delay_s=0,
        )

    plain_transport = transport_for_run()
    plain = build()()
    assert PRIVATE in capsys.readouterr().err + caplog.text
    caplog.clear()
    setups = []

    def unavailable(**kwargs):
        setups.append(kwargs)
        raise OSError(PRIVATE)

    monkeypatch.setattr(invocation_host.HostAuditSession, "from_user_state", unavailable)
    transport = transport_for_run()
    review = ordinary_host.review()
    actual = build().call_with_audit(review.round(5))
    review.finalize()
    assert [(f.source, f.disposition) for f in actual[0]] == [
        (f.source, f.disposition) for f in plain[0]
    ]
    assert [f.source for f in actual[0]] == ["INFRA", "L1", "L1"]
    assert actual[2] == plain[2] == (Usage(5, 7) if branch == "retry" else Usage(6, 8))
    assert len(transport.requests) == len(plain_transport.requests) == (4 if branch == "retry" else 6)
    assert all(audited for _, _, audited in transport.requests)
    for lane in PASSES:
        assert [request.data for name, request, _ in transport.requests if name == lane] == [
            request.data for name, request, _ in plain_transport.requests if name == lane
        ]
    assert len(setups) == 1 and ordinary_host.hosts == [] and MockRecorder.instances == []
    output = capsys.readouterr().err
    assert output.count("invocation audit unavailable; recording is incomplete") == 1
    assert PRIVATE not in output + caplog.text + json.dumps([asdict(f) for f in actual[0]])
    if branch == "continuation_headroom":
        assert "continuation request failed" in caplog.text
        assert "wider retry failed" in caplog.text


@pytest.mark.parametrize("kind", ["stub", "grouped"])
def test_real_unsupported_factory_routes_and_machine_keep_legacy_calls(
    ordinary_host, api_backend, monkeypatch, tmp_path, kind
):
    transport = RoutedTransport(monkeypatch)
    review = ordinary_host.review()
    call = (
        build_l1_provider("stub", resolved())
        if kind == "stub"
        else build_grouped_l1_provider(
            "real",
            [{"name": "fixture-group", "resolved": resolved()}],
            backend=api_backend,
            max_attempts=1,
            initial_delay_s=0,
        )
    )
    assert type(call) is _L1Call and not call.supports_invocation_audit
    with pytest.raises(ValueError, match="no ordinary audit route"):
        call.call_with_audit(review.round(0))
    sm = machine(tmp_path, api_backend, review, l1=call)
    findings, _ = sm._run_l1_phase()
    review.finalize()
    assert len(findings) == (0 if kind == "stub" else 3)
    assert len(transport.requests) == (0 if kind == "stub" else 6)
    assert all(not audited for _, _, audited in transport.requests)
    assert ordinary_host.factories == [] and MockRecorder.instances == []


def test_real_selected_l1_keeps_custom_falsifier_legacy_signature(
    ordinary_host, api_backend, monkeypatch, tmp_path
):
    transport = RoutedTransport(monkeypatch)
    review = ordinary_host.review()
    sm = machine(tmp_path, api_backend, review)
    sm.falsifier = StubFalsifier()
    findings, _ = sm._run_l1_phase()
    review.finalize()
    assert len(findings) == len(transport.requests) == 3
    assert all(audited for _, _, audited in transport.requests)
    assert {item["context"].purpose for item in MockRecorder.instances[0].started} == {"review"}


# Isolated compatibility/privacy seams below do not replace the real call-chain
# controls above, and do not imply installed CLI or native filesystem coverage.


@pytest.fixture
def isolated_cli(monkeypatch, tmp_path, api_backend):
    """Freeze unrelated acquisition/advisory services, not L1 factories."""
    from contextlib import nullcontext
    from code_forge import backend, context_sources, gate_check, outlet_resolver, user_config, workspace

    def forbidden_command(*_args, **_kwargs):
        raise AssertionError("isolated CLI seam must not execute an external command")

    monkeypatch.setattr(cli.subprocess, "run", forbidden_command)
    monkeypatch.setattr(cli, "is_git_repo", lambda _: False)
    monkeypatch.setattr(cli, "_load_gate_backends", lambda _: ([api_backend], {}))
    monkeypatch.setattr(cli, "_merge_user_into", lambda configs, _: configs)
    monkeypatch.setattr(cli, "_load_trusted_review_lenses", lambda *_: False)
    monkeypatch.setattr(user_config, "load_user_retry", lambda: {})
    monkeypatch.setattr(workspace, "resolve_workspace", lambda *_: tmp_path)
    monkeypatch.setattr(backend, "resolve_backend", lambda *_args, **_kwargs: api_backend)
    monkeypatch.setattr(cli, "resolve_backend_with_fallback", lambda value, *_args, **_kwargs: value)
    monkeypatch.setattr(outlet_resolver, "resolve_outlet", lambda *_args, **_kwargs: "subprocess")
    monkeypatch.setattr(cli, "load_registry", lambda _: {})
    monkeypatch.setattr(cli, "resolve_baseline", lambda *_: resolved())
    monkeypatch.setattr(cli, "_paths", lambda *_args, **_kwargs: [Path("sample.py")])
    monkeypatch.setattr(cli, "_assemble_post_image", lambda *_: ("", ""))
    monkeypatch.setattr(gate_check, "trusted_gate_data_or_error", lambda **_: {})
    monkeypatch.setattr(
        context_sources, "gather", lambda *_args, **_kwargs: context_sources.GatherResult()
    )
    monkeypatch.setattr(cli, "_repo_display_name", lambda _: "fixture")
    monkeypatch.setattr(cli, "ForgeLock", lambda _: nullcontext())
    monkeypatch.setattr(cli, "_run_test_assertion_phase", lambda *_: None)
    monkeypatch.setattr(cli, "_emit_ci_output", lambda *_args, **_kwargs: None)
    args = cli._build_parser().parse_args(
        [
            "review",
            "--outlet",
            "subprocess",
            "--mode",
            "ci",
            "--registry",
            "fixture.yaml",
            "--quiet",
        ]
    )
    return args, {"TEST_SEND_KEY": "test fixture credential"}, tmp_path


def test_isolated_run_forwards_real_adapter_and_finalizes_after_optional_work(
    isolated_cli, ordinary_host, api_backend, monkeypatch, capsys
):
    args, env, cwd = isolated_cli
    events, reviews = [], []
    transport = RoutedTransport(monkeypatch)

    def pipeline(**kwargs):
        review = kwargs["invocation_audit"]
        reviews.append(review)
        assert type(review) is OrdinaryReviewAudit
        assert type(kwargs["l1_provider"]) is _L1Call
        assert type(kwargs["falsifier"]) is RealFalsifier
        assert not ordinary_host.factories
        kwargs["l1_provider"].call_with_audit(review.round(3))
        events.append("review")
        assert not review._closed
        return Verdict.FAIL

    def optional(*_args):
        assert not reviews[0]._closed
        events.append("optional")

    monkeypatch.setattr(cli, "_run_hold_loop", pipeline)
    monkeypatch.setattr(cli, "_run_test_assertion_phase", optional)
    assert cli._run(args, env, cwd) is Verdict.FAIL
    assert events == ["review", "optional"]
    assert reviews[0]._closed
    assert len(transport.requests) == 3
    assert len(ordinary_host.factories) == 1
    assert ordinary_host.factories[0]["environ"] is env
    assert sum(name == "finalize" for name, _ in MockRecorder.calls) == 1
    assert "whole-review recording is incomplete" in capsys.readouterr().err


@pytest.mark.parametrize("marked", [False, True])
def test_isolated_run_finalization_preserves_marked_primary_and_legacy_unmarked_wrapper(
    isolated_cli, ordinary_host, monkeypatch, marked
):
    args, env, cwd = isolated_cli
    primary = LLMInvokeError(PRIVATE)
    cause = OSError(PRIVATE)
    primary.__cause__ = cause
    reviews = []
    finalized = []
    original_finalize = OrdinaryReviewAudit.finalize

    def finalize(review, entering=None):
        finalized.append((review, entering))
        return original_finalize(review, entering)

    def pipeline(**kwargs):
        review = kwargs["invocation_audit"]
        reviews.append(review)
        if marked:
            review.round(0).note_escape(primary)
        raise primary

    monkeypatch.setattr(OrdinaryReviewAudit, "finalize", finalize)
    monkeypatch.setattr(cli, "_run_hold_loop", pipeline)
    expected = LLMInvokeError if marked else cli.CliError
    with pytest.raises(expected) as caught:
        cli._run(args, env, cwd)
    assert caught.value is primary if marked else caught.value.__cause__ is primary
    assert primary.__cause__ is cause
    assert finalized == [(reviews[0], primary)]
    assert reviews[0]._closed
    assert ordinary_host.factories == []


def test_isolated_run_rejects_ordinary_strict_before_calling_pipeline(isolated_cli, monkeypatch):
    args, env, cwd = isolated_cli
    args.audit_strict = True
    diagnostics = ReviewAuditDiagnostics()
    stale = RuntimeError("previous invocation")
    diagnostics.note(stale)

    def forbidden(**_kwargs):
        raise AssertionError("ordinary strict must be rejected before the review loop")

    monkeypatch.setattr(cli, "_run_hold_loop", forbidden)
    with pytest.raises(cli.CliError, match="only the inline canary outlet"):
        cli._run(args, env, cwd, audit_diagnostics=diagnostics)
    assert not diagnostics.contains(stale)


def test_isolated_hold_controller_reuses_same_adapter_across_machine_instances(
    isolated_cli, ordinary_host, monkeypatch
):
    args, env, cwd = isolated_cli
    from code_forge import state

    constructors, held = [], []
    loaded = State(mode=Mode.LOCAL)

    class ControlledMachine:
        def __init__(self, **kwargs):
            constructors.append(kwargs)

        def run(self):
            return Verdict.PENDING if len(constructors) == 1 else Verdict.FAIL

    monkeypatch.setattr(cli, "StateMachine", ControlledMachine)
    monkeypatch.setattr(state, "load_state", lambda _: loaded)
    monkeypatch.setattr(cli, "run_hold_ui", lambda *_args, **_kwargs: held.append(True))
    monkeypatch.setattr(cli, "emit_local_assertion_report", lambda *_: None)
    args.mode = "local"
    assert cli._run(args, env, cwd) is Verdict.FAIL
    assert len(constructors) == 2 and held == [True]
    review = constructors[0]["invocation_audit"]
    assert type(review) is OrdinaryReviewAudit
    assert constructors[1]["invocation_audit"] is review
    assert review._closed
    assert ordinary_host.factories == []
