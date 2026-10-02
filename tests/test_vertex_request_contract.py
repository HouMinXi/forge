"""Pin the request _invoke_vertex builds.

The URL carries the project, the region and the model. The body is the
anthropic vertex envelope with the prompt as the single user message. The
content type is JSON and the bearer token comes from the refreshed
credentials. A regional endpoint differs from the global one only in the
host.
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
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class _Creds:
    token = "tok"

    def refresh(self, request):
        return None


def _capture(monkeypatch, region):
    monkeypatch.setattr(google_auth, "default", lambda scopes: (_Creds(), None))
    seen = {}

    def fake_open(req, timeout):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data.decode())
        seen["headers"] = dict(req.headers)
        seen["timeout"] = timeout
        return _Response({"content": [{"type": "text", "text": "answer"}], "usage": {}})

    monkeypatch.setattr(invoke.urllib.request, "urlopen", fake_open)
    backend = BackendConfig(
        name="b", type="api", format="vertex", model="claude", project_id="proj", region=region
    )
    result = _invoke_vertex("hello", backend, 30)
    return seen, result


def test_global_request_carries_project_region_model_and_prompt(monkeypatch):
    seen, result = _capture(monkeypatch, "global")

    assert seen["url"] == (
        "https://aiplatform.googleapis.com/v1/projects/proj/locations/global/"
        "publishers/anthropic/models/claude:rawPredict"
    )
    assert seen["body"]["messages"] == [{"role": "user", "content": "hello"}]
    assert seen["body"]["anthropic_version"] == "vertex-2023-10-16"
    assert seen["headers"]["Content-type"] == "application/json"
    assert seen["headers"]["Authorization"] == "Bearer tok"
    assert seen["timeout"] == 30
    assert result == ("answer", {})


def test_regional_endpoint_uses_the_region_host(monkeypatch):
    seen, _ = _capture(monkeypatch, "us-central1")

    assert seen["url"].startswith("https://us-central1-aiplatform.googleapis.com/")
    assert "/locations/us-central1/" in seen["url"]


def test_empty_region_falls_back_to_global(monkeypatch):
    seen, _ = _capture(monkeypatch, "")

    assert seen["url"].startswith("https://aiplatform.googleapis.com/")
    assert "/locations/global/" in seen["url"]
