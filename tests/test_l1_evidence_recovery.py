"""Trusted L1 recovery asks once for evidence and never grants acceptance."""

import copy
import inspect
import json
from pathlib import Path

import pytest

import code_forge.llm_invoke as invoke
from code_forge.backend import BackendConfig
from code_forge.baseline import ResolvedReview
from code_forge.cli import _make_subagent_spawn
from code_forge.factories import build_grouped_l1_provider, build_l1_provider
from code_forge.reviewer_json import _requires_l1_excerpts
from tests.test_machine_receipt_gate import CONTENT, DIFF
from code_forge.state import Mode, Verdict
from code_forge.machine import StateMachine
from code_forge.autofix import StubAutoFixer
from code_forge.falsify import StubFalsifier
from code_forge.source import compute_source_hash

EMPTY = {"findings": [], "code_excerpts": []}
EXCERPTS = [
    {
        "file": "control.ts",
        "start_line": 1,
        "end_line": 3,
        "content": CONTENT.rstrip("\n"),
        "rationale": "checked",
    }
]
VALID = {"findings": [], "code_excerpts": EXCERPTS}
FINDING = {"file": "control.ts", "line": 2, "severity": "P2", "description": "owned candidate"}


def _call(prompt, backend, *, scoped=True, expected_keys=None):
    # Baseline adapter: old source reaches a pointed call-count assertion,
    # rather than failing on a not-yet-implemented keyword.
    keywords = {}
    if "l1_evidence_required" in inspect.signature(invoke.llm_invoke).parameters:
        keywords["l1_evidence_required"] = scoped
    return invoke.llm_invoke(
        prompt, backend=backend, expected_keys=expected_keys, max_attempts=1, **keywords
    )


def _transport(monkeypatch, kind, first, follow=VALID):
    calls = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            return invoke.LLMResult(copy.deepcopy(first), invoke.Usage(3, 14, 2), 1.5)
        if isinstance(follow, Exception):
            raise follow
        return invoke.LLMResult(copy.deepcopy(follow), invoke.Usage(5, 7, 4), 2.5)

    monkeypatch.setattr(invoke, "_invoke_api" if kind == "api" else "_invoke_cli", fake)
    return BackendConfig(name="owned", type=kind, model="owned", timeout_s=7), calls


@pytest.mark.parametrize("kind", ["api", "cli"])
@pytest.mark.parametrize("root", [False, True])
@pytest.mark.parametrize("with_findings", [False, True])
def test_scoped_empty_evidence_gets_one_repair(monkeypatch, kind, root, with_findings):
    first = {"findings": [FINDING] if with_findings else []}
    if root:
        first["code_excerpts"] = []
    backend, calls = _transport(monkeypatch, kind, first, VALID | {"findings": [FINDING]})
    result = _call("inspect this changed code\n" + DIFF, backend)
    assert len(calls) == 2, "trusted L1 missing evidence must receive one recovery request"
    assert result.content["findings"] == first["findings"], "follow-up cannot replace findings"
    assert result.content["code_excerpts"] == EXCERPTS
    assert result.usage == invoke.Usage(8, 21, 6)
    assert result.duration_s == 4.0
    prompt = calls[1][0][0]
    assert "changed" in prompt and "even if findings is empty" in prompt
    assert "for those findings" not in prompt
    assert calls[1][0][2] == 7
    if kind == "api":
        assert calls[1][1] == {"expected_keys": None, "max_attempts": 1}


@pytest.mark.parametrize(
    "first",
    [
        {"findings": [], "code_excerpts": None},
        {"findings": [], "code_excerpts": {}},
        {"findings": [None]},
        {"findings": [{}]},
        {"code_excerpts": []},
        [],
    ],
)
def test_scoped_malformed_envelope_is_not_repaired(monkeypatch, first):
    backend, calls = _transport(monkeypatch, "api", first)
    _call("inspect changed code", backend)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "scoped,expected_keys", [(False, None), (True, frozenset({"verdict", "reasoning"}))]
)
def test_unscoped_or_explicit_envelope_stays_outside_recovery(monkeypatch, scoped, expected_keys):
    backend, calls = _transport(monkeypatch, "api", EMPTY)
    _call("not an L1 publisher", backend, scoped=scoped, expected_keys=expected_keys)
    assert len(calls) == 1


def test_usable_nested_evidence_needs_no_repair(monkeypatch):
    first = {"findings": [FINDING | {"code_excerpts": EXCERPTS}]}
    backend, calls = _transport(monkeypatch, "api", first)
    result = _call("inspect changed code", backend)
    assert len(calls) == 1 and result.content == first


@pytest.mark.parametrize("follow", [{}, EMPTY, invoke.LLMInvokeError("owned failure")])
def test_empty_or_failed_repair_keeps_original_and_no_false_evidence(monkeypatch, follow):
    backend, calls = _transport(monkeypatch, "api", EMPTY, follow)
    if isinstance(follow, Exception):
        monkeypatch.setattr(invoke.time, "monotonic", lambda: 10.0)
    result = _call("inspect changed code", backend)
    assert len(calls) == 2
    assert result.content == EMPTY
    assert result.usage == (
        invoke.Usage(3, 14, 2) if isinstance(follow, Exception) else invoke.Usage(8, 21, 6)
    )
    assert result.duration_s == (1.5 if isinstance(follow, Exception) else 4.0)


@pytest.mark.parametrize("grouped,multi_repo", [(False, False), (True, False), (False, True)])
def test_trusted_producer_passes_host_applicability(monkeypatch, grouped, multi_repo):
    calls = []

    def fake(prompt, **kwargs):
        calls.append(kwargs)
        return invoke.LLMResult(copy.deepcopy(VALID))

    monkeypatch.setattr(invoke, "llm_invoke", fake)
    resolved = ResolvedReview([Path("control.ts")], None, DIFF, "git")
    kwargs = {"backend": None, "max_attempts": 1}
    repositories = {"owned": DIFF} if multi_repo else None
    if repositories is not None:
        kwargs["reviewed_repositories"] = repositories
    provider = (
        build_grouped_l1_provider("auto", [{"name": "owned", "resolved": resolved}], **kwargs)
        if grouped
        else build_l1_provider("auto", resolved, **kwargs)
    )
    provider()
    assert len(calls) == 3
    assert all(
        c.get("l1_evidence_required") is _requires_l1_excerpts(DIFF, reviewed_repositories=repositories)
        for c in calls
    )


@pytest.mark.parametrize("diff", [DIFF, ""])
def test_subagent_scope_comes_from_actual_diff(monkeypatch, diff):
    calls = []

    def fake(prompt, **kwargs):
        calls.append(kwargs)
        return invoke.LLMResult(copy.deepcopy(VALID))

    monkeypatch.setattr(invoke, "llm_invoke", fake)
    spawn = _make_subagent_spawn(None, "", "")
    spawn("adversarial", diff)
    assert calls[0].get("l1_evidence_required") is _requires_l1_excerpts(diff)


@pytest.mark.parametrize("kind", ["api", "cli"])
@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
@pytest.mark.parametrize("repaired", [False, True])
def test_recovery_uses_real_publication_and_gate(monkeypatch, tmp_path, kind, mode, repaired):
    (tmp_path / "control.ts").write_text(CONTENT)
    (tmp_path / ".code-forge").mkdir()
    (tmp_path / ".code-forge/gate.yaml").write_text("verify:\n  required_cycles: 3\n")
    calls = []

    def fake(prompt, *args, **kwargs):
        is_repair = prompt.startswith("The previous JSON")
        calls.append(is_repair)
        content = VALID if repaired and is_repair else EMPTY
        return invoke.LLMResult(copy.deepcopy(content), invoke.Usage(), 0.0)

    monkeypatch.setattr(invoke, "_invoke_api" if kind == "api" else "_invoke_cli", fake)
    backend = BackendConfig(name="owned", type=kind, model="owned", timeout_s=7)
    resolved = ResolvedReview([Path("control.ts")], None, DIFF, "git")
    provider = build_l1_provider("auto", resolved, backend=backend, max_attempts=1)
    machine = StateMachine(
        mode=mode,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=resolved,
        source_hash=compute_source_hash(git_diff=DIFF),
        baseline_spec_repr="owned recovery",
        cwd=tmp_path,
        registry={},
        l0_runner=lambda *a: ([], []),
        l1_provider=provider,
        l2_runner=lambda *a, **kw: ([], []),
        max_total_rounds=3,
        clean_round_threshold=3,
    )
    machine._state.env_manifest = {}
    returned = machine.run()
    disk = json.loads((tmp_path / ".code-forge/state.json").read_text())
    expected_passes = 9 if mode == Mode.LOCAL and repaired else 3
    assert calls.count(True) == expected_passes, "each missing required pass gets one repair"
    assert calls.count(False) == expected_passes
    receipts = [
        json.loads(p.read_text()) for p in (tmp_path / ".code-forge/receipts").glob("receipt-*.json")
    ]
    if repaired:
        assert returned == Verdict.PASS and disk["converged"] is True
        assert all(r["pass_status"] == "completed" and r["code_excerpts"] for r in receipts)
        assert not provider.attempted_excerpts
    else:
        assert returned != Verdict.PASS and disk["converged"] is False
        assert all(r["pass_status"] == "incomplete" for r in receipts)
        assert len(provider.attempted_excerpts) == 3


@pytest.mark.parametrize("flag", [1, None, "true"])
def test_scope_flag_is_builtin_bool_and_refuses_before_dispatch(monkeypatch, flag):
    backend, calls = _transport(monkeypatch, "api", EMPTY)
    with pytest.raises(ValueError, match="l1_evidence_required must be a bool"):
        _call("inspect changed code", backend, scoped=flag)
    assert calls == []


def test_failed_repair_accounts_for_observed_duration(monkeypatch):
    backend, calls = _transport(
        monkeypatch, "api", EMPTY, invoke.LLMInvokeError("owned failure", duration_s=2.5)
    )
    result = _call("inspect changed code", backend)
    assert len(calls) == 2 and result.content == EMPTY
    assert result.duration_s == 4.0, "failed follow-up duration must remain observable"
    assert result.usage == invoke.Usage(3, 14, 2)  # failure tokens are unavailable


def test_scoped_recovery_over_real_loopback_http(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    monkeypatch.setenv("L1_RECOVERY_TEST_KEY", "local-test")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            response = EMPTY if len(requests) == 1 else VALID
            body = json.dumps(
                {
                    "choices": [{"message": {"content": json.dumps(response)}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 14},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        try:
            thread.start()
            backend = BackendConfig(
                name="owned",
                type="api",
                model="test",
                format="openai",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key_env="L1_RECOVERY_TEST_KEY",
                stream=False,
            )
            result = invoke.llm_invoke(
                "inspect changed code\n" + DIFF,
                backend,
                timeout_s=3,
                max_attempts=1,
                l1_evidence_required=True,
            )
            assert result.content == VALID
            assert result.usage == invoke.Usage(6, 28)
            assert len(requests) == 2
            assert requests[1]["messages"][0]["content"].startswith("The previous JSON")
        finally:
            if thread.is_alive():
                server.shutdown()
                thread.join(timeout=5)
            assert not thread.is_alive()


@pytest.mark.parametrize("kind", ["api", "cli"])
@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("cycles", [1, 3])
@pytest.mark.parametrize(
    "follow_kind", ["missing-field", "line-count", "mixed-list", "healthy", "wrong-literal"]
)
def test_repair_cannot_borrow_sibling_evidence(
    monkeypatch, tmp_path, kind, grouped, cycles, follow_kind
):
    from code_forge.receipt import write_receipts
    from code_forge.verify import parse_diff_files, run_verify

    (tmp_path / "control.ts").write_text(CONTENT)
    follow = copy.deepcopy(VALID)
    if follow_kind == "missing-field":
        del follow["code_excerpts"][0]["file"]
    elif follow_kind == "line-count":
        follow["code_excerpts"][0].update(end_line=5, content="const context = 1;")
    elif follow_kind == "mixed-list":
        follow["code_excerpts"].append(None)
    elif follow_kind == "wrong-literal":
        follow["code_excerpts"][0]["content"] = CONTENT.rstrip("\n").replace("value = 2", "value = 999")
    calls = []

    def fake(prompt, *args, **kwargs):
        repairing = prompt.startswith("The previous JSON")
        expert = "senior engineer:" in prompt
        calls.append((repairing, expert))
        content = follow if repairing else EMPTY if expert else VALID
        return invoke.LLMResult(copy.deepcopy(content), invoke.Usage(3, 14, 2), 1.5)

    monkeypatch.setattr(invoke, "_invoke_api" if kind == "api" else "_invoke_cli", fake)
    backend = BackendConfig(name="owned", type=kind, model="owned", timeout_s=7)
    resolved = ResolvedReview([Path("control.ts")], None, DIFF, "git")
    kwargs = {"backend": backend, "max_attempts": 1}
    provider = (
        build_grouped_l1_provider("auto", [{"name": "owned", "resolved": resolved}], **kwargs)
        if grouped
        else build_l1_provider("auto", resolved, **kwargs)
    )
    source_hash = compute_source_hash(git_diff=DIFF)
    receipt_dir = tmp_path / ".code-forge" / "receipts"
    for round_index in range(cycles):
        findings, excerpts, usage, duration = provider()
        assert usage == invoke.Usage(12, 56, 8)
        assert duration >= 0  # API provider measures parallel wall time.
        write_receipts(
            receipt_dir,
            round_index,
            findings,
            source_hash,
            resolved.source_files,
            tmp_path,
            diff_files=parse_diff_files(DIFF),
            diff_text=DIFF,
            reviewer_excerpts=excerpts,
            manifest={},
            attempted_excerpts=provider.attempted_excerpts,
            unavailable_rejected_passes=provider.unavailable_rejected_passes,
            raw_observations=provider.raw_observations,
        )
    result = run_verify(
        tmp_path,
        source_hash,
        parse_diff_files(DIFF),
        diff_text=DIFF,
        required_cycles=cycles,
        cycles=list(range(1, cycles + 1)),
        respect_floor=False,
        require_convergence=cycles == 3,
    )
    receipts = [json.loads(p.read_text()) for p in sorted(receipt_dir.glob("receipt-*.json"))]
    (tmp_path / "observed-verifier.json").write_text(json.dumps(result.__dict__, indent=2))
    assert calls.count((True, True)) == cycles and len(calls) == 4 * cycles
    expert_receipts = [r for r in receipts if r["pass"] == 2]
    siblings = [r for r in receipts if r["pass"] != 2]
    assert all(r["pass_status"] == "completed" and r["code_excerpts"] for r in siblings)
    if follow_kind == "healthy":
        assert all(r["pass_status"] == "completed" for r in expert_receipts)
        assert result.passed and result.checks_passed == result.checks_run == 8
    elif follow_kind == "wrong-literal":
        assert all(r["pass_status"] == "schema_fail" for r in expert_receipts)
        assert not result.passed
    else:
        assert all(r["pass_status"] == "incomplete" for r in expert_receipts), (
            "invalid repair must preserve missing evidence despite healthy sibling coverage"
        )
        assert not result.passed and result.checks_run == 8 and "status=incomplete" in result.reason


def test_scoped_repair_with_no_retained_excerpts_keeps_original(monkeypatch):
    original = {"findings": [copy.deepcopy(FINDING)], "code_excerpts": []}
    follow = copy.deepcopy(VALID)
    follow["code_excerpts"][0]["content"] = ""
    backend, calls = _transport(monkeypatch, "api", original, follow)
    result = _call("inspect changed code", backend)
    assert len(calls) == 2 and result.content == original
    assert result.usage == invoke.Usage(8, 21, 6) and result.duration_s == 4.0


def test_repair_validation_does_not_mutate_original_or_followup(monkeypatch):
    import code_forge.reviewer_json as reviewer_json

    original = {"findings": [copy.deepcopy(FINDING)], "code_excerpts": []}
    follow = copy.deepcopy(VALID)
    before = copy.deepcopy((original, follow))
    validate = reviewer_json.validate_reviewer_json

    def mutating_validation(candidate):
        validated = validate(candidate)
        # The shared validator owns and normalizes its argument. A nested
        # normalization must not leak into either retained caller object.
        candidate["findings"][0]["description"] = "normalized by validator"
        candidate["code_excerpts"][0]["rationale"] = "normalized by validator"
        return validated

    monkeypatch.setattr(reviewer_json, "validate_reviewer_json", mutating_validation)
    monkeypatch.setattr(invoke, "_invoke_api", lambda *a, **kw: invoke.LLMResult(follow))
    repaired, _, _ = invoke._repair_missing_excerpts(
        original,
        "inspect changed code",
        BackendConfig(name="owned", type="api", model="owned"),
        7,
        l1_evidence_required=True,
    )
    assert repaired["findings"] is original["findings"] and (original, follow) == before


def test_unscoped_repair_keeps_legacy_nonempty_followup(monkeypatch):
    malformed = copy.deepcopy(VALID)
    malformed["code_excerpts"][0].update(end_line=5, content="const context = 1;")
    backend, calls = _transport(monkeypatch, "api", {"findings": [FINDING]}, malformed)
    result = _call("legacy caller", backend, scoped=False)
    assert len(calls) == 2 and result.content["code_excerpts"] == malformed["code_excerpts"]


@pytest.fixture
def billed_failed_repair_http(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    import time

    monkeypatch.setenv("L1_RECOVERY_TEST_KEY", "local-test")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    requests = []
    failure = {"content": None}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            prompt = request["messages"][0]["content"]
            repairing = prompt.startswith("The previous JSON")
            if repairing:
                time.sleep(0.03)
                content = failure["content"]
            else:
                content = json.dumps(EMPTY if "senior engineer:" in prompt else VALID)
            body = json.dumps(
                {
                    "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                    "usage": {
                        "prompt_tokens": 5 if repairing else 3,
                        "completion_tokens": 7 if repairing else 14,
                        "prompt_tokens_details": {"cached_tokens": 4 if repairing else 2},
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        backend = BackendConfig(
            name="owned",
            type="api",
            model="test",
            format="openai",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            api_key_env="L1_RECOVERY_TEST_KEY",
            stream=False,
            timeout_s=3,
        )
        try:
            thread.start()
            yield backend, requests, failure
        finally:
            if thread.is_alive():
                server.shutdown()
                thread.join(timeout=5)
            assert not thread.is_alive()


@pytest.mark.parametrize("content", [None, "a complete non-JSON answer"])
def test_failed_http_repair_keeps_billed_usage_and_original(billed_failed_repair_http, content):
    backend, requests, failure = billed_failed_repair_http
    failure["content"] = content
    result = _call("senior engineer: inspect changed code\n" + DIFF, backend)
    assert len(requests) == 2 and result.content == EMPTY
    assert result.usage == invoke.Usage(8, 21, 6), "billed failed repair must remain accounted"
    assert result.duration_s >= 0.03, "actual failed repair elapsed time must remain accounted"


@pytest.mark.parametrize("content,kind", [(None, "empty"), ("complete prose", "no_json")])
def test_response_rejection_carries_decoded_usage_and_time(billed_failed_repair_http, content, kind):
    backend, requests, failure = billed_failed_repair_http
    failure["content"] = content
    with pytest.raises(invoke.LLMInvokeError) as caught:
        invoke._invoke_api("The previous JSON controlled rejection", backend, 3, max_attempts=1)
    assert len(requests) == 1 and caught.value.kind == kind
    assert getattr(caught.value, "usage", None) == invoke.Usage(5, 7, 4)
    assert caught.value.duration_s >= 0.03


@pytest.mark.parametrize("kind", ["api", "cli"])
def test_failed_repair_measures_elapsed_when_error_duration_is_unavailable(monkeypatch, kind):
    backend, calls = _transport(monkeypatch, kind, EMPTY, invoke.LLMInvokeError("unavailable"))
    ticks = iter([10.0, 12.5])
    monkeypatch.setattr(invoke.progress, "emit", lambda *a, **kw: None)
    monkeypatch.setattr(invoke.time, "monotonic", lambda: next(ticks))
    result = _call("inspect changed code", backend)
    assert len(calls) == 2 and result.content == EMPTY
    assert result.duration_s == 4.0, "local repair elapsed must survive zero error duration"
    assert result.usage == invoke.Usage(3, 14, 2)  # No failure token metadata was acquired.


@pytest.mark.parametrize("kind", ["api", "cli"])
def test_failed_repair_preserves_available_error_usage(monkeypatch, kind):
    error = invoke.LLMInvokeError("owned failure", duration_s=2.5, usage=invoke.Usage(5, 7, 4))
    backend, calls = _transport(monkeypatch, kind, EMPTY, error)
    monkeypatch.setattr(invoke.time, "monotonic", lambda: 10.0)
    result = _call("inspect changed code", backend)
    assert len(calls) == 2 and result.content == EMPTY
    assert result.usage == invoke.Usage(8, 21, 6)
    assert result.duration_s == 4.0


@pytest.mark.parametrize("grouped", [False, True])
def test_billed_failed_repair_persists_cost_without_acceptance(
    billed_failed_repair_http, tmp_path, grouped, monkeypatch, capsys
):
    backend, requests, _ = billed_failed_repair_http
    (tmp_path / "control.ts").write_text(CONTENT)
    (tmp_path / ".code-forge").mkdir()
    (tmp_path / ".code-forge/gate.yaml").write_text("verify:\n  required_cycles: 3\n")
    resolved = ResolvedReview([Path("control.ts")], None, DIFF, "git")
    provider = (
        build_grouped_l1_provider(
            "auto", [{"name": "owned", "resolved": resolved}], backend=backend, max_attempts=1
        )
        if grouped
        else build_l1_provider("auto", resolved, backend=backend, max_attempts=1)
    )
    machine = StateMachine(
        mode=Mode.LOCAL,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=resolved,
        source_hash=compute_source_hash(git_diff=DIFF),
        baseline_spec_repr="billed incomplete review",
        cwd=tmp_path,
        registry={},
        l0_runner=lambda *a: ([], []),
        l1_provider=provider,
        l2_runner=lambda *a, **kw: ([], []),
        max_total_rounds=1,
        clean_round_threshold=3,
    )
    from code_forge import cli

    machine._state.env_manifest = {}
    # Exercise the CLI's persisted-cost consumer with the real controlled
    # machine; unused advisory/mutation runners must never execute here.
    monkeypatch.setattr(cli, "StateMachine", lambda **kw: machine)
    assert (
        cli._run_hold_loop(
            mode=Mode.LOCAL,
            falsifier=machine.falsifier,
            autofixer=machine.autofixer,
            revert_fn=lambda f: None,
            l1_provider=provider,
            resolved=resolved,
            source_hash=compute_source_hash(git_diff=DIFF),
            baseline_repr="billed incomplete review",
            cwd=tmp_path,
            registry={},
            max_rounds=1,
            max_fix_attempts=1,
            state_path=tmp_path / ".code-forge/state.json",
        )
        != Verdict.PASS
    )
    disk = json.loads((tmp_path / ".code-forge/state.json").read_text())
    receipts = [json.loads(p.read_text()) for p in (tmp_path / ".code-forge/receipts").glob("*.json")]
    assert len(requests) == 4 and len(receipts) == 3
    assert disk["verdict"] != "PASS" and disk["converged"] is False
    assert [r["pass_status"] for r in receipts].count("incomplete") == 1
    assert "cost: 63 tokens (14 in + 49 out, 10 cached), 3 passes" in capsys.readouterr().err
    assert disk["cost"]["total_input_tokens"] == 14
    assert disk["cost"]["total_output_tokens"] == 49
    assert disk["cost"]["total_cached_tokens"] == 10
    assert disk["cost"]["total_duration_s"] >= 0.03


@pytest.fixture
def response_usage_http(monkeypatch, api_format):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from unittest.mock import Mock

    if api_format == "vertex":
        pytest.importorskip("google.auth")
        pytest.importorskip("google.auth.transport.requests")
        monkeypatch.setattr("google.auth.default", lambda **kw: (Mock(token="local-test"), "owned"))
        monkeypatch.setattr("google.auth.transport.requests.Request", object)

    monkeypatch.setenv("L1_RECOVERY_TEST_KEY", "local-test")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    packets = []
    requests = []
    profile = {"stream": False}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            packet = packets.pop(0)
            if profile["stream"]:
                choice = packet["choices"][0]
                chunk = {
                    "choices": [
                        {"delta": {"content": choice["message"]["content"]}, "finish_reason": "stop"}
                    ]
                }
                if "usage" in packet:
                    chunk["usage"] = packet["usage"]
                body = ("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode()
            else:
                body = json.dumps(packet).encode()
            self.send_response(200)
            self.send_header(
                "Content-Type", "text/event-stream" if profile["stream"] else "application/json"
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01))
        url = f"http://127.0.0.1:{server.server_port}"
        # Only credentials and endpoint selection are replaced; the Vertex
        # HTTP extraction and dispatcher run against the owned server.
        if api_format == "vertex":
            monkeypatch.setattr(invoke, "_build_vertex_url", lambda *a: url + "/vertex")
        try:
            thread.start()
            yield url, packets, requests, profile
        finally:
            if thread.is_alive():
                server.shutdown()
                thread.join(timeout=5)
            assert not thread.is_alive()


def _response_usage_packet(api_format, content, usage_state):
    packet = (
        {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
        if api_format == "openai"
        else {"content": [{"type": "text", "text": content}], "stop_reason": "end_turn"}
    )
    if usage_state == "missing":
        return packet
    if usage_state == "null":
        packet["usage"] = None
    elif usage_state == "empty":
        packet["usage"] = {}
    else:
        keys = (
            ("prompt_tokens", "completion_tokens")
            if api_format == "openai"
            else ("input_tokens", "output_tokens")
        )
        packet["usage"] = dict(zip(keys, (0, 0) if usage_state == "zero" else (5, 7), strict=True))
    return packet


@pytest.mark.parametrize(
    "api_format,stream", [("openai", False), ("openai", True), ("anthropic", False), ("vertex", False)]
)
@pytest.mark.parametrize("usage_state", ["missing", "null", "empty", "zero", "populated"])
@pytest.mark.parametrize("content,kind", [(None, "empty"), ("complete prose", "no_json")])
def test_response_error_usage_availability_over_http(
    response_usage_http, api_format, stream, usage_state, content, kind
):
    url, packets, requests, profile = response_usage_http
    profile["stream"] = stream
    packets.append(_response_usage_packet(api_format, content, usage_state))
    backend = BackendConfig(
        name="owned",
        type="api",
        model="test",
        format=api_format,
        base_url=url,
        api_key_env="L1_RECOVERY_TEST_KEY",
        project_id="owned",
        stream=stream,
    )
    with pytest.raises(invoke.LLMInvokeError) as caught:
        invoke._invoke_api("owned response availability", backend, 3, max_attempts=1)
    assert len(requests) == 1 and not packets and caught.value.kind == kind
    expected = (
        None
        if usage_state in {"missing", "null", "empty"}
        else invoke.Usage(*((0, 0) if usage_state == "zero" else (5, 7)))
    )
    assert caught.value.usage == expected, "unreported response usage must not become known zero"
    assert caught.value.duration_s > 0
    if kind == "no_json":
        assert caught.value.raw_response == content


@pytest.mark.parametrize(
    "api_format,stream", [("openai", False), ("openai", True), ("anthropic", False), ("vertex", False)]
)
@pytest.mark.parametrize("usage_state", ["missing", "null", "empty"])
def test_unavailable_http_repair_usage_keeps_legacy_numeric_fallback(
    response_usage_http, api_format, stream, usage_state
):
    url, packets, requests, profile = response_usage_http
    profile["stream"] = stream
    packets.extend(
        [
            _response_usage_packet(api_format, json.dumps(EMPTY), "populated"),
            _response_usage_packet(api_format, None, usage_state),
        ]
    )
    backend = BackendConfig(
        name="owned",
        type="api",
        model="test",
        format=api_format,
        base_url=url,
        api_key_env="L1_RECOVERY_TEST_KEY",
        project_id="owned",
        stream=stream,
    )
    result = _call("inspect changed code\n" + DIFF, backend)
    assert len(requests) == 2 and not packets and result.content == EMPTY
    assert result.usage == invoke.Usage(5, 7) and result.duration_s > 0


@pytest.mark.parametrize("api_format", ["anthropic", "vertex"])
@pytest.mark.parametrize("cached", [0, 4])
def test_cache_only_response_usage_remains_available(response_usage_http, api_format, cached):
    url, packets, requests, _ = response_usage_http
    packet = _response_usage_packet(api_format, None, "missing")
    packet["usage"] = {"cache_read_input_tokens": cached}
    packets.append(packet)
    backend = BackendConfig(
        name="owned",
        type="api",
        model="test",
        format=api_format,
        base_url=url,
        api_key_env="L1_RECOVERY_TEST_KEY",
        project_id="owned",
        stream=False,
    )
    with pytest.raises(invoke.LLMInvokeError) as caught:
        invoke._invoke_api("owned cached response", backend, 3, max_attempts=1)
    assert len(requests) == 1 and not packets and caught.value.kind == "empty"
    assert caught.value.usage == invoke.Usage(0, 0, cached)


@pytest.mark.parametrize("api_format", ["openai", "anthropic", "vertex"])
@pytest.mark.parametrize("invalid", [None, "7", True, -1, 1.5])
@pytest.mark.parametrize("content,kind", [(None, "empty"), ("complete prose", "no_json")])
def test_failed_response_normalizes_partial_usage(
    response_usage_http, api_format, invalid, content, kind
):
    url, packets, requests, _ = response_usage_http
    packet = _response_usage_packet(api_format, content, "missing")
    if api_format == "openai":
        packet["usage"] = {
            "prompt_tokens": invalid,
            "completion_tokens": 7,
            "prompt_tokens_details": {"cached_tokens": 4},
        }
    else:
        packet["usage"] = {
            "input_tokens": invalid,
            "output_tokens": 7,
            "cache_read_input_tokens": 4,
        }
    packets.append(packet)
    backend = BackendConfig(
        name="owned",
        type="api",
        model="test",
        format=api_format,
        base_url=url,
        api_key_env="L1_RECOVERY_TEST_KEY",
        project_id="owned",
        stream=False,
    )
    with pytest.raises(invoke.LLMInvokeError) as caught:
        invoke._invoke_api("owned partial usage", backend, 3, max_attempts=1)
    assert len(requests) == 1 and not packets and caught.value.kind == kind
    assert caught.value.usage == invoke.Usage(0, 7, 4)
    if kind == "no_json":
        assert caught.value.raw_response == content


@pytest.mark.parametrize("api_format", ["openai", "anthropic", "vertex"])
@pytest.mark.parametrize("cached", [None, "4", True, -1, 0, 4])
def test_failed_response_invalid_only_or_valid_cache(response_usage_http, api_format, cached):
    url, packets, requests, _ = response_usage_http
    packet = _response_usage_packet(api_format, None, "missing")
    packet["usage"] = (
        {
            "prompt_tokens": None,
            "completion_tokens": "bad",
            "prompt_tokens_details": {"cached_tokens": cached},
        }
        if api_format == "openai"
        else {"input_tokens": None, "output_tokens": "bad", "cache_read_input_tokens": cached}
    )
    packets.append(packet)
    backend = BackendConfig(
        name="owned",
        type="api",
        model="test",
        format=api_format,
        base_url=url,
        api_key_env="L1_RECOVERY_TEST_KEY",
        project_id="owned",
        stream=False,
    )
    with pytest.raises(invoke.LLMInvokeError) as caught:
        invoke._invoke_api("owned invalid counters", backend, 3, max_attempts=1)
    expected = invoke.Usage(0, 0, cached) if type(cached) is int and cached >= 0 else None
    assert len(requests) == 1 and not packets and caught.value.kind == "empty"
    assert caught.value.usage == expected


@pytest.mark.parametrize("content", [None, "complete prose"])
def test_partial_failed_repair_retains_findings_through_writer(monkeypatch, tmp_path, content):
    from code_forge.receipt import write_receipts
    from code_forge.verify import parse_diff_files

    original = {"findings": [copy.deepcopy(FINDING)], "code_excerpts": []}
    requests = []

    class Response:
        def __init__(self, packet):
            self.packet = packet

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(self.packet).encode()

    def network(request, **kwargs):
        prompt = json.loads(request.data)["messages"][0]["content"]
        repairing = prompt.startswith("The previous JSON")
        requests.append(repairing)
        return Response(
            {
                "choices": [
                    {
                        "message": {"content": content if repairing else json.dumps(original)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": None if repairing else 3,
                    "completion_tokens": 7 if repairing else 14,
                    "prompt_tokens_details": {"cached_tokens": 4 if repairing else 2},
                },
            }
        )

    monkeypatch.setenv("L1_RECOVERY_TEST_KEY", "local-test")
    monkeypatch.setattr(invoke.urllib.request, "urlopen", network)
    backend = BackendConfig(
        name="owned",
        type="api",
        model="test",
        format="openai",
        base_url="http://offline.invalid",
        api_key_env="L1_RECOVERY_TEST_KEY",
    )
    result = _call("inspect changed code\n" + DIFF, backend)
    assert result.content == original and result.usage == invoke.Usage(3, 21, 6)
    assert result.duration_s > 0 and requests == [False, True]
    (tmp_path / "control.ts").write_text(CONTENT)
    provider = build_l1_provider(
        "auto",
        ResolvedReview([Path("control.ts")], None, DIFF, "git"),
        backend,
        pass_stagger_s=0,
        max_attempts=1,
    )
    findings, excerpts, usage, duration = provider()
    assert not any("invoke-fail" in f.id for f in findings)
    assert usage == invoke.Usage(9, 63, 18) and duration > 0
    assert requests.count(True) == requests.count(False) == 4
    assert len(provider.attempted_excerpts) == 3
    assert {item["pass_name"] for item in provider.attempted_excerpts} == {
        "qodo",
        "expert",
        "adversarial",
    }
    assert all(
        {k: v for k, v in item.items() if k != "pass_name"} == original
        for item in provider.attempted_excerpts
    )
    receipt_dir = tmp_path / "receipts"
    write_receipts(
        receipt_dir,
        1,
        findings,
        compute_source_hash(git_diff=DIFF),
        [Path("control.ts")],
        tmp_path,
        diff_files=parse_diff_files(DIFF),
        diff_text=DIFF,
        reviewer_excerpts=excerpts,
        manifest={},
        attempted_excerpts=provider.attempted_excerpts,
        unavailable_rejected_passes=provider.unavailable_rejected_passes,
        raw_observations=provider.raw_observations,
    )
    receipts = [json.loads(p.read_text()) for p in receipt_dir.glob("receipt-*.json")]
    assert len(receipts) == 3 and all(r["pass_status"] == "incomplete" for r in receipts)
    artifacts = [json.loads(p.read_text()) for p in (receipt_dir / "attempted").glob("*.json")]
    assert len(artifacts) == 3
    assert all(
        {k: v for k, v in a["payload"].items() if k != "pass_name"} == original for a in artifacts
    )


@pytest.mark.parametrize("api_format", ["openai"])
@pytest.mark.parametrize("nested,expected", [(None, 7), (0, 7), (4, 4)])
def test_openai_failure_retains_actual_flat_cache_dialect(
    response_usage_http, api_format, nested, expected
):
    url, packets, requests, _ = response_usage_http
    packets.append(
        {
            "choices": [{"message": {"content": None}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": None,
                "completion_tokens": None,
                "prompt_tokens_details": {"cached_tokens": nested},
                "prompt_cache_hit_tokens": 7,
            },
        }
    )
    backend = BackendConfig(
        name="owned",
        type="api",
        model="test",
        format=api_format,
        base_url=url,
        api_key_env="L1_RECOVERY_TEST_KEY",
        stream=False,
    )
    with pytest.raises(invoke.LLMInvokeError) as caught:
        invoke._invoke_api("owned flat cache", backend, 3, max_attempts=1)
    assert len(requests) == 1 and not packets and caught.value.kind == "empty"
    assert caught.value.usage == invoke.Usage(0, 0, expected)


@pytest.mark.parametrize("api_format", ["openai", "anthropic", "vertex"])
def test_http_fixture_missing_vertex_extra_is_scoped(monkeypatch, api_format):
    import http.server
    import sys
    import threading

    monkeypatch.setitem(sys.modules, "google.auth", None)
    monkeypatch.setitem(sys.modules, "google.auth.transport.requests", None)
    if "google" in sys.modules:
        monkeypatch.delattr(sys.modules["google"], "auth", raising=False)
    servers = []
    threads = []
    real_server = http.server.ThreadingHTTPServer
    real_start = threading.Thread.start

    def observed_server(*args, **kwargs):
        server = real_server(*args, **kwargs)
        servers.append(server)
        return server

    def observed_start(thread, *args, **kwargs):
        threads.append(thread)
        return real_start(thread, *args, **kwargs)

    monkeypatch.setattr(http.server, "ThreadingHTTPServer", observed_server)
    monkeypatch.setattr(threading.Thread, "start", observed_start)
    fixture = response_usage_http.__wrapped__(monkeypatch, api_format)
    try:
        if api_format == "vertex":
            with pytest.raises(pytest.skip.Exception):
                next(fixture)
            assert not servers and not threads, "skip must precede owned server acquisition"
        else:
            try:
                next(fixture)
            except pytest.skip.Exception as exc:
                pytest.fail(f"{api_format} fixture incorrectly requires optional Vertex extra: {exc}")
            fixture.close()
            assert len(servers) == len(threads) == 1
            assert not any(t.is_alive() for t in threads), "fixture must close its own thread"
    finally:
        # Keep the omission control safe: record assertions before this outside cleanup.
        for server in servers:
            if any(t.is_alive() for t in threads):
                server.shutdown()
            server.server_close()
        for thread in threads:
            if thread.ident is not None:
                thread.join(timeout=5)
        assert not any(t.is_alive() for t in threads)


@pytest.mark.parametrize("site", ["ordinary", "billed", "response"])
@pytest.mark.parametrize("after_start", [False, True])
def test_http_setup_failure_closes_started_thread(monkeypatch, site, after_start):
    import http.server
    import threading

    class SetupFailure(Exception):
        pass

    primary = SetupFailure("owned setup failure")
    servers = []
    threads = []
    real_server = http.server.ThreadingHTTPServer
    real_start = threading.Thread.start

    def observed_server(*args, **kwargs):
        server = real_server(*args, **kwargs)
        servers.append(server)
        return server

    def failing_start(thread, *args, **kwargs):
        threads.append(thread)
        if after_start:
            real_start(thread, *args, **kwargs)
        raise primary

    monkeypatch.setattr(http.server, "ThreadingHTTPServer", observed_server)
    monkeypatch.setattr(threading.Thread, "start", failing_start)
    try:
        with pytest.raises(SetupFailure) as caught:
            if site == "ordinary":
                test_scoped_recovery_over_real_loopback_http(monkeypatch)
            else:
                fixture = (
                    billed_failed_repair_http.__wrapped__(monkeypatch)
                    if site == "billed"
                    else response_usage_http.__wrapped__(monkeypatch, "openai")
                )
                next(fixture)
        assert caught.value is primary, "cleanup must retain the actual setup error"
        assert len(servers) == len(threads) == 1
        assert not any(t.is_alive() for t in threads), "setup finally must close already started thread"
    finally:
        for server in servers:
            if any(t.is_alive() for t in threads):
                server.shutdown()
            server.server_close()
        for thread in threads:
            if thread.ident is not None:
                thread.join(timeout=5)
        assert not any(t.is_alive() for t in threads)
