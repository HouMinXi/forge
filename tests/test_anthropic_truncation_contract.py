"""Pin how _invoke_anthropic treats a response cut off at the cap.

stop_reason max_tokens raises _TruncatedResponse: not retryable, kind
truncated, carrying the text already produced and the usage the backend
reported. A null usage degrades to a question mark rather than crashing.
A response that finished normally returns its first text block.
"""

import json

import pytest

import code_forge.llm_invoke as invoke
from code_forge.backend import BackendConfig
from code_forge.llm_invoke import _TruncatedResponse, _invoke_anthropic


class _Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


def _serve(monkeypatch, payload):
    monkeypatch.setattr(invoke.urllib.request, "urlopen", lambda req, timeout: _Response(payload))


def _backend():
    return BackendConfig(
        name="b",
        type="api",
        format="anthropic",
        model="claude",
        base_url="https://api.anthropic.com",
        max_tokens=1000,
    )


def test_max_tokens_stop_is_a_truncated_response(monkeypatch):
    _serve(
        monkeypatch,
        {
            "stop_reason": "max_tokens",
            "content": [
                {"type": "thinking", "thinking": "..."},
                {"type": "text", "text": "partial answer"},
            ],
            "usage": {"input_tokens": 12, "output_tokens": 1000},
        },
    )

    with pytest.raises(_TruncatedResponse) as caught:
        _invoke_anthropic("prompt", _backend(), "k", 30)

    err = caught.value
    assert err.retryable is False
    assert err.kind == "truncated"
    assert err.content == "partial answer"
    assert err.usage_data == {"input_tokens": 12, "output_tokens": 1000}
    assert err.resolved_cap == 1000
    assert "input=12" in str(err)
    assert "output=1000" in str(err)


def test_null_usage_on_truncation_degrades_to_unknown(monkeypatch):
    _serve(monkeypatch, {"stop_reason": "max_tokens", "content": [], "usage": None})

    with pytest.raises(_TruncatedResponse) as caught:
        _invoke_anthropic("prompt", _backend(), "k", 30)

    assert caught.value.content is None
    assert "input=?" in str(caught.value)
    assert "output=?" in str(caught.value)


def test_finished_response_returns_the_first_text_block(monkeypatch):
    _serve(
        monkeypatch,
        {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "complete answer"}],
            "usage": {"input_tokens": 4, "output_tokens": 6},
        },
    )

    content, usage = _invoke_anthropic("prompt", _backend(), "k", 30)

    assert content == "complete answer"
    assert usage == {"input_tokens": 4, "output_tokens": 6}
