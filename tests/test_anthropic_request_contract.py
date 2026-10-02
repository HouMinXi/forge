"""Pin the request _invoke_anthropic builds and how it classifies failure.

The URL is the configured base plus /v1/messages. The body names the model
and carries the prompt as the single user message, with the output cap
under max_tokens. The key travels in x-api-key beside the pinned
anthropic-version. Reasoning effort is not forwarded: this format passes
allow_effort=False. A retryable HTTP status is retried with its code, any
other status is not, and a URL error is a retryable connection failure.
"""

import io
import json
import urllib.error

import code_forge.llm_invoke as invoke
from code_forge.backend import BackendConfig
from code_forge.llm_invoke import LLMInvokeError, _invoke_anthropic


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps({"content": [{"type": "text", "text": "answer"}], "usage": {}}).encode()


def _backend():
    return BackendConfig(
        name="b",
        type="api",
        format="anthropic",
        model="claude",
        base_url="https://api.anthropic.com",
        max_tokens=1000,
    )


def _capture(monkeypatch, opener):
    monkeypatch.setattr(invoke.urllib.request, "urlopen", opener)
    return _invoke_anthropic("hello", _backend(), "secret", 30)


def test_request_carries_url_model_prompt_cap_and_key(monkeypatch):
    seen = {}

    def fake_open(req, timeout):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data.decode())
        seen["headers"] = dict(req.headers)
        seen["timeout"] = timeout
        return _Response()

    result = _capture(monkeypatch, fake_open)

    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["body"]["model"] == "claude"
    assert seen["body"]["messages"] == [{"role": "user", "content": "hello"}]
    assert seen["body"]["max_tokens"] == 1000
    assert seen["headers"]["X-api-key"] == "secret"
    assert seen["headers"]["Anthropic-version"] == "2023-06-01"
    assert seen["headers"]["Content-type"] == "application/json"
    assert seen["timeout"] == 30
    assert result == ("answer", {})


def test_reasoning_effort_is_not_forwarded(monkeypatch):
    seen = {}

    def fake_open(req, timeout):
        seen["body"] = json.loads(req.data.decode())
        return _Response()

    monkeypatch.setattr(invoke.urllib.request, "urlopen", fake_open)
    _invoke_anthropic(
        "hello",
        BackendConfig(
            name="b",
            type="api",
            format="anthropic",
            model="claude",
            base_url="https://api.anthropic.com",
            max_tokens=1000,
            reasoning_effort="high",
        ),
        "secret",
        30,
    )

    assert "reasoning_effort" not in seen["body"]
    assert "output_config" not in seen["body"]


def _http(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(b"detail"))


def test_retryable_status_is_retried_with_its_code(monkeypatch):
    def fake_open(req, timeout):
        raise _http(429)

    try:
        _capture(monkeypatch, fake_open)
    except LLMInvokeError as exc:
        caught = exc
    else:
        raise AssertionError("no error raised")

    assert caught.retryable is True
    assert caught.exit_code == 429


def test_other_status_is_not_retried(monkeypatch):
    def fake_open(req, timeout):
        raise _http(401)

    try:
        _capture(monkeypatch, fake_open)
    except LLMInvokeError as exc:
        caught = exc
    else:
        raise AssertionError("no error raised")

    assert caught.retryable is False
    assert caught.exit_code == 401


def test_url_error_is_a_retryable_connection_failure(monkeypatch):
    def fake_open(req, timeout):
        raise urllib.error.URLError(TimeoutError("slow"))

    try:
        _capture(monkeypatch, fake_open)
    except LLMInvokeError as exc:
        caught = exc
    else:
        raise AssertionError("no error raised")

    assert caught.retryable is True
    assert caught.kind == "conn"
    assert caught.is_timeout is True


def test_error_body_is_decoded_and_cut_at_200(monkeypatch):
    body = b"\xff" + b"z" * 400

    def fake_open(req, timeout):
        raise urllib.error.HTTPError("http://x", 500, "err", {}, io.BytesIO(body))

    try:
        _capture(monkeypatch, fake_open)
    except LLMInvokeError as exc:
        caught = exc
    else:
        raise AssertionError("no error raised")

    text = str(caught)
    quoted = text.split("body: ", 1)[1]
    assert quoted[0] == "\ufffd"
    assert quoted[1:] == "z" * 199
    assert len(quoted) == 200
