# SPDX-License-Identifier: Apache-2.0
"""Phase 59-A1: a malformed falsifier answer is a protocol violation.

Before this change, RealFalsifier.falsify turned a non-dict response, a
missing verdict key, or an unknown verdict string into Disposition.UNCERTAIN
(falsify_real.py:68-80). machine.py then could not tell that apart from
the model saying "I am not sure". After: those three shapes raise
FalsifyProtocolError, a LLMInvokeError subclass, and machine.py's infra
path names the cause in f.error and state.infra_errors.

Clean-round behaviour is unchanged: the finding is still UNCERTAIN and
clause d (machine.py:1248-1251) still resets. This task is attribution.
"""

from __future__ import annotations

import json
import socket
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, patch

import pytest

from code_forge.disposition import Disposition
from code_forge.backend import BackendConfig
from code_forge.falsify_real import RealFalsifier
from code_forge.llm_invoke import (
    FalsifyProtocolError,
    InvalidJSONResponseError,
    LLMResult,
    Usage,
    llm_invoke,
)
from code_forge.machine import TimeoutBreaker
from code_forge.state import StateFinding, Verdict, load_state


def _finding() -> StateFinding:
    return StateFinding(
        id="f1",
        fingerprint="fp-1",
        source="L1",
        disposition=Disposition.CONFIRMED,
        file="a.py",
        line_range=[1, 2],
        description="off by one",
    )


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        {"reasoning": "no verdict key"},
        {"verdict": None, "reasoning": "x"},
        {"verdict": "MAYBE", "reasoning": "x"},
        {"verdict": " CONFIRMED ", "reasoning": "x"},
    ],
)
def test_malformed_response_raises_protocol_error(content):
    with patch("code_forge.falsify_real.llm_invoke") as inv:
        inv.return_value = LLMResult(content=content)
        with pytest.raises(FalsifyProtocolError) as ei:
            RealFalsifier(backend=MagicMock()).falsify(_finding())
    assert ei.value.raw == content


def test_valid_verdict_still_returns_disposition():
    with patch("code_forge.falsify_real.llm_invoke") as inv:
        inv.return_value = LLMResult(content={"verdict": "DISMISSED", "reasoning": "x"})
        assert RealFalsifier(backend=MagicMock()).falsify(_finding()) == Disposition.DISMISSED


def test_fixed_is_a_protocol_error_not_a_crash(tmp_path):
    """Review round 1 on ee45427: FIXED raised a bare ValueError, which
    falls past machine.py's LLMInvokeError/RuntimeError arms into the
    re-raising except Exception and aborts the review. It is the same
    class of violation as an unknown verdict and gets the same arm."""
    with patch("code_forge.falsify_real.llm_invoke") as inv:
        inv.return_value = LLMResult(content={"verdict": "FIXED", "reasoning": "x"})
        with pytest.raises(FalsifyProtocolError, match="only verify"):
            RealFalsifier(backend=MagicMock()).falsify(_finding())

    from tests.test_runtime_machine import _make_sm

    sm = _make_sm(tmp_path)
    sm.falsifier = RealFalsifier(backend=MagicMock())
    f = _finding()
    sm.l1_provider = lambda: ([f], [], Usage(), 0.0)
    with patch("code_forge.falsify_real.llm_invoke") as inv:
        inv.return_value = LLMResult(content={"verdict": "FIXED", "reasoning": "x"})
        sm._run_l1_phase()  # must not raise
    assert f.disposition == Disposition.UNCERTAIN
    assert f.error.startswith("falsify() protocol violation:")


def test_protocol_error_attributed_in_state(tmp_path):
    """machine.py names the cause: protocol violation, not backend outage.

    Drives the real falsifier through the real state machine with only
    the backend call mocked, so a stub falsifier cannot make this green.
    """
    from tests.test_runtime_machine import _make_sm

    sm = _make_sm(tmp_path)
    sm.falsifier = RealFalsifier(backend=MagicMock())
    f = _finding()
    sm.l1_provider = lambda: ([f], [], Usage(), 0.0)
    with patch("code_forge.falsify_real.llm_invoke") as inv:
        inv.return_value = LLMResult(content="garbage")
        sm._run_l1_phase()
    assert f.disposition == Disposition.UNCERTAIN
    assert f.error is not None
    assert f.error.startswith("falsify() protocol violation:")
    assert any("protocol violation" in e for e in sm._state.infra_errors)
    assert not any("backend unavailable" in e for e in sm._state.infra_errors)


def test_backend_outage_still_attributed_as_unavailable(tmp_path):
    """The other arm keeps its wording; the two causes stay distinguishable."""
    from code_forge.llm_invoke import LLMInvokeError
    from tests.test_runtime_machine import _make_sm

    sm = _make_sm(tmp_path)
    sm.falsifier = RealFalsifier(backend=MagicMock())
    f = _finding()
    sm.l1_provider = lambda: ([f], [], Usage(), 0.0)
    with patch("code_forge.falsify_real.llm_invoke") as inv:
        inv.side_effect = LLMInvokeError("connection refused")
        sm._run_l1_phase()
    assert f.disposition == Disposition.UNCERTAIN
    assert f.error is not None
    assert f.error.startswith("falsify() backend unavailable:")
    assert not any("protocol violation" in e for e in sm._state.infra_errors)


@pytest.fixture
def local_falsifier_api(monkeypatch):
    """Serve real OpenAI envelopes; model content is supplied by each test."""
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("FALSIFIER_TEST_KEY", "local-test-key")
    replies = {"content": "not JSON", "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.connection.settimeout(2)
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            replies["requests"].append(request)
            body = json.dumps(
                {
                    "choices": [{"message": {"content": replies["content"]}, "finish_reason": "stop"}],
                    "usage": {
                        "prompt_tokens": 17,
                        "completion_tokens": 9,
                        "prompt_tokens_details": {"cached_tokens": 3},
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    backend = BackendConfig(
        name="local-falsifier",
        type="api",
        model="local-model",
        format="openai",
        base_url=f"http://127.0.0.1:{server.server_port}/v1",
        api_key_env="FALSIFIER_TEST_KEY",
        timeout_s=2,
    )
    thread.start()
    try:
        yield backend, replies
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def _real_machine(tmp_path, backend):
    from tests.test_runtime_machine import _make_sm

    sm = _make_sm(tmp_path)
    sm.falsifier = RealFalsifier(backend=backend)

    def candidates():
        finding = _finding()
        finding.excerpt = "value = 1\n"
        return [finding], [], Usage(), 0.0

    sm.l1_provider = candidates
    return sm


@pytest.mark.parametrize(
    "content, requests",
    [
        ("The model answered without a JSON verdict.", 2),
        ('{"verdict":"DISMISSED" "reasoning":"missing comma"}', 4),
        ("connection refused; no_json is still a received answer", 2),
    ],
)
def test_received_invalid_json_uses_protocol_path(tmp_path, local_falsifier_api, content, requests):
    backend, replies = local_falsifier_api
    replies["content"] = content
    with pytest.raises(InvalidJSONResponseError) as caught:
        RealFalsifier(backend).falsify(_finding())
    error = caught.value
    assert error.raw_response == replies["content"]
    assert error.kind == "no_json"
    assert error.exit_code == 0
    assert error.stderr
    assert error.duration_s > 0
    assert error.usage == Usage(input_tokens=17, output_tokens=9, cached_input_tokens=3)
    assert not error.retryable
    sm = _real_machine(tmp_path, backend)
    findings, _ = sm._run_l1_phase()
    assert findings[0].disposition == Disposition.UNCERTAIN
    assert findings[0].error == f"falsify() protocol violation: {error}"
    assert sm._state.rounds_with_falsify_infra == 1
    assert any("protocol violation" in e for e in sm._state.infra_errors)
    assert not any("backend unavailable" in e for e in sm._state.infra_errors)
    assert len(replies["requests"]) == requests


@pytest.mark.parametrize(
    "content, verdict, infra",
    [
        ('["decoded but not an envelope"]', Disposition.UNCERTAIN, True),
        ('{"reasoning":"no verdict"}', Disposition.UNCERTAIN, True),
        ('{"verdict":"MAYBE","reasoning":"x"}', Disposition.UNCERTAIN, True),
        ('{"verdict":"DISMISSED","reasoning":"diff is correct"}', Disposition.DISMISSED, False),
        ('{"verdict":"CONFIRMED","reasoning":"diff is wrong"}', Disposition.CONFIRMED, False),
        ('{"verdict":"UNCERTAIN","reasoning":"need evidence"}', Disposition.UNCERTAIN, False),
    ],
)
def test_real_decoded_and_semantic_controls(tmp_path, local_falsifier_api, content, verdict, infra):
    backend, replies = local_falsifier_api
    replies["content"] = content
    sm = _real_machine(tmp_path, backend)
    findings, _ = sm._run_l1_phase()
    assert findings[0].disposition == verdict
    assert sm._state.rounds_with_falsify_infra == int(infra)
    if infra:
        assert findings[0].error.startswith("falsify() protocol violation:")
    else:
        assert findings[0].error is None
        assert findings[0].falsify_reasoning == json.loads(content)["reasoning"]
        assert not sm._state.infra_errors
    assert len(replies["requests"]) == 1


def test_real_refused_connection_stays_backend_failure(tmp_path, monkeypatch, local_falsifier_api):
    backend, _ = local_falsifier_api
    from dataclasses import replace

    # Keep the port reserved without listening, so no other server can acquire it.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        backend = replace(backend, base_url=f"http://127.0.0.1:{reserved.getsockname()[1]}/v1")
        monkeypatch.setattr("code_forge.falsify_real.llm_invoke", partial(llm_invoke, max_attempts=1))
        sm = _real_machine(tmp_path, backend)
        findings, _ = sm._run_l1_phase()
    assert findings[0].disposition == Disposition.UNCERTAIN
    assert findings[0].error.startswith("falsify() backend unavailable:")
    assert "Connection refused" in findings[0].error
    assert sm._state.rounds_with_falsify_infra == 1
    assert not any("protocol violation" in e for e in sm._state.infra_errors)


@pytest.mark.parametrize("content", ["not JSON", '{"verdict":"MAYBE"}'])
def test_real_protocol_breaker_and_recovery(tmp_path, local_falsifier_api, content):
    backend, replies = local_falsifier_api
    sm = _real_machine(tmp_path, backend)
    replies["content"] = content
    sm._run_l1_phase()
    sm._run_l1_phase()
    assert sm._state.rounds_with_falsify_infra == 2
    replies["content"] = '{"verdict":"UNCERTAIN","reasoning":"need evidence"}'
    sm._run_l1_phase()
    assert sm._state.rounds_with_falsify_infra == 0
    replies["content"] = content
    sm._run_l1_phase()
    sm._run_l1_phase()
    with pytest.raises(TimeoutBreaker, match="cannot converge") as caught:
        sm._run_l1_phase()
    assert "could not adjudicate" in str(caught.value)
    assert "reach its backend" not in str(caught.value)
    assert any("could not adjudicate" in e for e in sm._state.infra_errors)
    persisted = load_state(tmp_path / ".code-forge" / "state.json")
    assert persisted.rounds_with_falsify_infra == 3
    assert persisted.verdict == Verdict.FAIL
    assert not persisted.converged


def test_mixed_real_failure_rounds_share_breaker(tmp_path, monkeypatch, local_falsifier_api):
    from dataclasses import replace

    backend, replies = local_falsifier_api
    monkeypatch.setattr("code_forge.falsify_real.llm_invoke", partial(llm_invoke, max_attempts=1))
    sm = _real_machine(tmp_path, backend)
    replies["content"] = "not JSON"
    sm._run_l1_phase()
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        offline = replace(backend, base_url=f"http://127.0.0.1:{reserved.getsockname()[1]}/v1")
        sm.falsifier = RealFalsifier(offline)
        sm._run_l1_phase()
    assert sm._state.rounds_with_falsify_infra == 2
    sm.falsifier = RealFalsifier(backend)
    with pytest.raises(TimeoutBreaker, match="could not adjudicate"):
        sm._run_l1_phase()
    assert sm._state.rounds_with_falsify_infra == 3
    assert any("protocol violation" in e for e in sm._state.infra_errors)
    assert any("backend unavailable" in e for e in sm._state.infra_errors)
    assert not any("could not reach" in e for e in sm._state.infra_errors)


@pytest.mark.parametrize("workers", [1, 3])
def test_mixed_candidates_count_one_infra_round(
    tmp_path,
    monkeypatch,
    local_falsifier_api,
    workers,
):
    from dataclasses import replace

    backend, _ = local_falsifier_api
    monkeypatch.setenv("FORGE_FALSIFY_WORKERS", str(workers))
    monkeypatch.setattr("code_forge.falsify_real.llm_invoke", partial(llm_invoke, max_attempts=1))
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        offline = replace(backend, base_url=f"http://127.0.0.1:{reserved.getsockname()[1]}/v1")

        class RoutedFalsifier(RealFalsifier):
            def falsify(self, finding):
                target = offline if finding.file == "offline.py" else backend
                return RealFalsifier(target).falsify(finding)

        sm = _real_machine(tmp_path, backend)
        sm.falsifier = RoutedFalsifier(backend)
        candidates = [_finding(), _finding()]
        candidates[1].file = "offline.py"
        candidates[1].fingerprint = "fp-offline"
        sm.l1_provider = lambda: (candidates, [], Usage(), 0.0)
        findings, _ = sm._run_l1_phase()
    assert [f.disposition for f in findings] == [Disposition.UNCERTAIN] * 2
    assert findings[0].error.startswith("falsify() protocol violation:")
    assert findings[1].error.startswith("falsify() backend unavailable:")
    assert sm._state.rounds_with_falsify_infra == 1
    assert sum("could not adjudicate 2 finding(s)" in e for e in sm._state.infra_errors) == 1
