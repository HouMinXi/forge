"""Pin the parameters _invoke_vertex forwards into the request body.

The output cap goes out under max_tokens, and an output_ceiling replaces
both configured caps. A thinking block is sent only when a thinking type
is configured, with its budget. Reasoning effort goes under
output_config.effort, not as its own field.
"""

import json

import pytest

import code_forge.llm_invoke as invoke
from code_forge.backend import BackendConfig
from code_forge.llm_invoke import _invoke_vertex


google_auth = pytest.importorskip("google.auth")
pytest.importorskip("requests")
pytest.importorskip("google.auth.transport.requests")


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps({"content": [{"type": "text", "text": "answer"}], "usage": {}}).encode()


class _Creds:
    token = "tok"

    def refresh(self, request):
        return None


def _body(monkeypatch, **kwargs):
    monkeypatch.setattr(google_auth, "default", lambda scopes: (_Creds(), None))
    seen = {}

    def fake_open(req, timeout):
        seen["body"] = json.loads(req.data.decode())
        return _Response()

    monkeypatch.setattr(invoke.urllib.request, "urlopen", fake_open)
    backend = BackendConfig(name="b", type="api", format="vertex", model="m", project_id="p", **kwargs)
    _invoke_vertex("prompt", backend, 30)
    return seen["body"]


def test_output_cap_is_sent_as_max_tokens(monkeypatch):
    body = _body(monkeypatch, max_tokens=1000)

    assert body["max_tokens"] == 1000
    assert "max_completion_tokens" not in body


def test_output_ceiling_overrides_the_configured_cap(monkeypatch):
    body = _body(monkeypatch, max_tokens=1000, output_ceiling=4000)

    assert body["max_tokens"] == 4000


def test_thinking_block_carries_type_and_budget(monkeypatch):
    body = _body(monkeypatch, max_tokens=1000, thinking_type="enabled", thinking_budget=2000)

    assert body["thinking"] == {"type": "enabled", "budget_tokens": 2000}


def test_no_thinking_block_without_a_type(monkeypatch):
    body = _body(monkeypatch, max_tokens=1000)

    assert "thinking" not in body


def test_reasoning_effort_goes_under_output_config(monkeypatch):
    body = _body(monkeypatch, max_tokens=1000, reasoning_effort="high")

    assert body["output_config"]["effort"] == "high"
    assert "reasoning_effort" not in body
