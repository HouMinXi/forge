"""Contract tests for llm_invoke entrypoint defaults, dispatch, and wire payloads."""

import io
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import Mock

import pytest

from code_forge.backend import BackendConfig
from code_forge.llm_invoke import LLMInvokeError, LLMResult, llm_invoke


def _make_api_backend(name: str = "test-api") -> BackendConfig:
    return BackendConfig(
        name=name,
        type="api",
        model="test-model",
        format="openai",
        base_url="http://127.0.0.1:8080/v1",
        api_key_env="TEST_API_KEY",
    )


def test_default_max_attempts_omitted_fails_after_five_requests(monkeypatch):
    """Omitting max_attempts defaults to 5.

    Pins mutmut_1 (max_attempts: 5 -> 6): the baseline fails after 5 retries,
    while the mutant would attempt a 6th time and succeed.
    """
    backend = _make_api_backend()
    monkeypatch.setenv("TEST_API_KEY", "test-key-value")
    monkeypatch.setattr("random.uniform", lambda *_: 0.0)
    monkeypatch.setattr("code_forge.llm_invoke.time.sleep", lambda *_: None)

    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        if len(calls) <= 5:
            raise urllib.error.HTTPError(
                req.full_url, 503, "Service Unavailable", {}, io.BytesIO(b"service unavailable")
            )
        resp = Mock()
        resp.read.return_value = json.dumps(
            {"choices": [{"message": {"content": json.dumps({"summary": "ok"})}}]}
        ).encode("utf-8")
        resp.__enter__ = Mock(return_value=resp)
        resp.__exit__ = Mock(return_value=False)
        return resp

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(LLMInvokeError) as exc_info:
        llm_invoke("review a.py", backend=backend)

    assert len(calls) == 5
    assert exc_info.value.exit_code == 503


def test_default_initial_delay_omitted_sleeps_two_seconds(monkeypatch):
    """Omitting initial_delay_s defaults to 2.0s without server backoff headers.

    Pins mutmut_2 (initial_delay_s: 2.0 -> 3.0): records sleep duration and
    asserts exactly one retry sleeping 2.0s before succeeding on the second call.
    """
    backend = _make_api_backend()
    monkeypatch.setenv("TEST_API_KEY", "test-key-value")
    monkeypatch.setattr("random.uniform", lambda *_: 0.0)

    sleeps = []
    monkeypatch.setattr("code_forge.llm_invoke.time.sleep", sleeps.append)

    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                req.full_url, 503, "Service Unavailable", {}, io.BytesIO(b"transient 503")
            )
        resp = Mock()
        resp.read.return_value = json.dumps(
            {
                "choices": [{"message": {"content": json.dumps({"summary": "ok"})}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        ).encode("utf-8")
        resp.__enter__ = Mock(return_value=resp)
        resp.__exit__ = Mock(return_value=False)
        return resp

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    res = llm_invoke("review a.py", backend=backend)

    assert len(calls) == 2
    assert sleeps == [2.0]
    assert res.content == {"summary": "ok"}


def test_api_request_payload_carries_exact_user_prompt(monkeypatch):
    """Calling llm_invoke with an API backend passes prompt to _invoke_api.

    Pins mutmut_56 (_invoke_api prompt: prompt -> None): asserts the real
    request payload contains the user prompt rather than None or empty.
    """
    prompt = "review a.py"
    backend = _make_api_backend()
    monkeypatch.setenv("TEST_API_KEY", "test-key-value")

    captured_requests = []

    def fake_urlopen(req, timeout=None):
        captured_requests.append(req)
        resp = Mock()
        resp.read.return_value = json.dumps(
            {"choices": [{"message": {"content": json.dumps({"summary": "ok"})}}]}
        ).encode("utf-8")
        resp.__enter__ = Mock(return_value=resp)
        resp.__exit__ = Mock(return_value=False)
        return resp

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    llm_invoke(prompt, backend=backend)

    assert len(captured_requests) == 1
    req_body = json.loads(captured_requests[0].data.decode("utf-8"))
    assert req_body["messages"][0]["role"] == "user"
    assert req_body["messages"][0]["content"] == "review a.py"


def test_unsupported_backend_type_raises_exact_error():
    """An unsupported backend type raises LLMInvokeError with exact message.

    Pins mutmut_74 ('unsupported backend type: %r' corrupted with XX markers).
    """
    backend = BackendConfig(name="b", type="grpc", model="m")
    expected_error = "unsupported backend type: 'grpc'"

    with pytest.raises(LLMInvokeError) as exc_info:
        llm_invoke("review a.py", backend=backend)

    assert str(exc_info.value) == expected_error


def test_explicit_expected_keys_prevents_excerpt_repair(monkeypatch):
    """Passing explicit expected_keys prevents excerpt repair invocation.

    Pins mutmut_77 (_needs_excerpt_repair(result.content, None)): passing
    explicit expected_keys keeps the findings intact without an extra repair
    request or excerpt mutation.
    """
    prompt = "review a.py"
    expected_keys = frozenset({"summary"})
    content = {"summary": "ok", "findings": [{"file": "a.py", "line_range": [3, 5]}]}
    backend = _make_api_backend()
    monkeypatch.setenv("TEST_API_KEY", "test-key-value")

    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        resp = Mock()
        resp.read.return_value = json.dumps(
            {
                "choices": [{"message": {"content": json.dumps(content)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            }
        ).encode("utf-8")
        resp.__enter__ = Mock(return_value=resp)
        resp.__exit__ = Mock(return_value=False)
        return resp

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    res = llm_invoke(prompt, backend=backend, expected_keys=expected_keys)

    assert len(calls) == 1
    assert "code_excerpts" not in res.content
    assert res.content == content
    assert res.usage.input_tokens == 10
    assert res.usage.output_tokens == 20


def test_real_loopback_smoke(monkeypatch):
    """End-to-end smoke test against a real local loopback HTTP server.

    Ensures the entire network request construction, transmission, response
    reading, JSON parsing, and LLMResult construction pass on the real path.
    """
    for env_var in (
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
    ):
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("LOOPBACK_API_KEY", "local-test-key")

    class LoopbackHandler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(2)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            _ = self.rfile.read(length)
            resp = json.dumps(
                {
                    "choices": [
                        {
                            "message": {"content": json.dumps({"summary": "loopback ok"})},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 8},
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), LoopbackHandler)
    server.timeout = 2
    server_thread = threading.Thread(target=server.handle_request, daemon=True)

    try:
        server_thread.start()
        port = server.server_address[1]
        backend = BackendConfig(
            name="loopback-api",
            type="api",
            model="local-model",
            format="openai",
            base_url=f"http://127.0.0.1:{port}/v1",
            api_key_env="LOOPBACK_API_KEY",
        )
        res = llm_invoke("review a.py", backend=backend, timeout_s=5, max_attempts=1)
        assert isinstance(res, LLMResult)
        assert res.content == {"summary": "loopback ok"}
        assert res.usage.input_tokens == 12
        assert res.usage.output_tokens == 8
    finally:
        try:
            if server_thread.ident is not None:
                server_thread.join(timeout=5)
        finally:
            server.server_close()
    assert not server_thread.is_alive()
