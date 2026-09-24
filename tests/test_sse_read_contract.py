"""Pin how an OpenAI SSE body becomes one response dict.

Bytes decode as utf-8 with replacement. A past deadline raises a
non-retryable timeout. A data line that is not JSON raises a retryable
connection error with exit code 0 and kind conn. An error-only stream
returns the error object, a bare string normalized to code stream_error.
An error after content raises, and a missing error code becomes
stream_error. The assembled message keeps the model, joined content,
finish reason, and usage. The first content delta emits one first-token
line.
"""

import json

import pytest

import code_forge.llm_invoke as invoke
from code_forge.llm_invoke import LLMInvokeError, _read_sse


def _lines(*payloads):
    out = []
    for payload in payloads:
        if isinstance(payload, str):
            out.append(payload.encode())
        else:
            out.append(b"data: " + json.dumps(payload).encode())
    return out


def _chunk(content=None, finish=None, model="", usage=None, error=None):
    body = {"model": model, "choices": []}
    if content is not None or finish is not None:
        choice = {"delta": {}}
        if content is not None:
            choice["delta"]["content"] = content
        if finish is not None:
            choice["finish_reason"] = finish
        body["choices"] = [choice]
    if usage is not None:
        body["usage"] = usage
    if error is not None:
        body["error"] = error
    return body


def test_assembles_content_model_finish_and_usage(monkeypatch):
    messages = []
    monkeypatch.setattr(invoke.progress, "emit", messages.append)
    result = _read_sse(
        _lines(
            _chunk(content="hel", model="m1"),
            _chunk(content="lo", finish="stop", usage={"total_tokens": 3}),
            "[DONE]",
        ),
        backend_name="agnes",
    )
    assert result == {
        "model": "m1",
        "choices": [{
            "message": {"role": "assistant", "content": "hello"},
            "finish_reason": "stop",
        }],
        "usage": {"total_tokens": 3},
    }
    assert messages == ["backend agnes: first token"]


def test_replaces_undecodable_bytes_outside_json():
    # The replacement character sits in a comment line, not in JSON.
    # A broken byte inside a data payload is a dropped stream instead.
    good = b"data: " + json.dumps(_chunk(content="a", finish="stop")).encode()
    result = _read_sse([b": note \xff\n", good])
    assert result["choices"][0]["message"]["content"] == "a"


def test_deadline_is_a_non_retryable_timeout(monkeypatch):
    monkeypatch.setattr(invoke.time, "monotonic", lambda: 10.0)
    with pytest.raises(LLMInvokeError) as caught:
        _read_sse(
            _lines(_chunk(content="a")),
            deadline=9.0,
            backend_name="agnes",
        )
    err = caught.value
    assert err.is_timeout is True
    assert err.retryable is False
    assert "agnes backend exceeded total read deadline" in str(err)


def test_bad_json_is_a_retryable_connection_error():
    with pytest.raises(LLMInvokeError) as caught:
        _read_sse([b"data: {not json"], backend_name="agnes")
    err = caught.value
    assert err.retryable is True
    assert err.exit_code == 0
    assert err.kind == "conn"
    assert "incomplete SSE event" in str(err)


def test_error_only_stream_returns_normalized_error():
    result = _read_sse(_lines({"error": "rate limit"}))
    assert result == {
        "error": {"code": "stream_error", "message": "rate limit"},
    }


def test_error_without_message_uses_empty_default():
    with pytest.raises(LLMInvokeError) as caught:
        _read_sse(
            _lines(
                _chunk(content="hel"),
                {"error": {"code": "429"}},
            ),
            backend_name="agnes",
        )
    text = str(caught.value)
    assert "mid-response:  (code 429)" in text
    assert "Check provider status page" in text
    with pytest.raises(LLMInvokeError) as caught:
        _read_sse(
            _lines(
                _chunk(content="hel"),
                {"error": {"message": "cut"}},
            ),
            backend_name="agnes",
        )
    err = caught.value
    assert err.retryable is True
    assert "code stream_error" in str(err)
    assert "cut" in str(err)
