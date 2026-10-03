"""Output widening requires an explicit maximum; transport stays local."""

import dataclasses
import importlib
import io
import json
import sys
import types
import urllib.request
from pathlib import Path

import pytest

from code_forge.backend import BackendConfig, _parse_backend_entry, probe_backend_live
from code_forge.errors import CliError

invoke = importlib.import_module("code_forge.llm_invoke")
ENVELOPE = {"findings": [], "code_excerpts": []}


@pytest.fixture
def transport(monkeypatch):
    """Substitute HTTP and Vertex auth; use actual request/body constructors."""
    state = {"requests": [], "auth": [], "truncate": 0}

    class Credentials:
        token = "local-token"

        def refresh(self, request):
            state["auth"].append("refresh")

    def default(**kwargs):
        state["auth"].append("default")
        return Credentials(), None

    google = types.ModuleType("google")
    google.__path__ = []
    oauth = types.ModuleType("google.oauth2")
    oauth.__path__ = []
    service = types.ModuleType("google.oauth2.service_account")
    auth = types.ModuleType("google.auth")
    auth.__path__ = []
    auth.default = default
    auth_transport = types.ModuleType("google.auth.transport")
    auth_transport.__path__ = []
    requests = types.ModuleType("google.auth.transport.requests")
    requests.Request = object
    exceptions = types.ModuleType("google.auth.exceptions")
    exceptions.DefaultCredentialsError = type("DefaultCredentialsError", (Exception,), {})
    exceptions.RefreshError = type("RefreshError", (Exception,), {})
    google.oauth2 = oauth
    oauth.service_account = service
    google.auth = auth
    auth.transport = auth_transport
    auth_transport.requests = requests
    for module in (google, oauth, service, auth, auth_transport, requests, exceptions):
        monkeypatch.setitem(sys.modules, module.__name__, module)

    def urlopen(request, *, timeout):
        assert isinstance(request, urllib.request.Request)
        body = json.loads(request.data)
        keys = set(body) & {"max_tokens", "max_completion_tokens"}
        assert len(keys) == 1
        assert "output_token_limit" not in body
        key = keys.pop()
        state["requests"].append({"url": request.full_url, "body": body, "key": key})
        truncated = len(state["requests"]) <= state["truncate"]
        text = '{"findings":[' if truncated else json.dumps(ENVELOPE)
        usage = {
            "prompt_tokens": 7,
            "completion_tokens": body[key] if truncated else 11,
            "input_tokens": 7,
            "output_tokens": body[key] if truncated else 11,
        }
        if request.full_url.endswith("/chat/completions"):
            response = {
                "choices": [
                    {"message": {"content": text}, "finish_reason": "length" if truncated else "stop"}
                ],
                "usage": usage,
            }
        else:
            response = {
                "content": [{"type": "text", "text": text}],
                "stop_reason": "max_tokens" if truncated else "end_turn",
                "usage": usage,
            }
        return io.BytesIO(json.dumps(response).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(invoke.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("LOCAL_BUDGET_KEY", "local-dummy-key")
    return state


def backend(format_name, **fields):
    return BackendConfig(
        name="local",
        type="api",
        model="local-model",
        format=format_name,
        base_url="https://local.invalid",
        project_id="local-project",
        api_key_env="LOCAL_BUDGET_KEY",
        **fields,
    )


def dispatch(config):
    if config.format == "vertex":
        return invoke._invoke_vertex("prompt", config, 2)
    return getattr(invoke, "_invoke_" + config.format)("prompt", config, "local-key", 2)


@pytest.mark.parametrize("format_name", ["openai", "anthropic", "vertex"])
@pytest.mark.parametrize("field", ["max_tokens", "max_completion_tokens"])
@pytest.mark.parametrize(
    ("initial", "limit", "wider"),
    [(65536, 0, None), (65536, 65536, None), (40000, 65536, 65536), (20000, 65536, 40000)],
)
def test_actual_request_budget_requires_bounded_headroom(
    transport, format_name, field, initial, limit, wider
):
    config = backend(format_name, **{field: initial}, output_token_limit=limit)
    before = dataclasses.asdict(config)
    dispatch(config)
    result = invoke._retry_with_more_headroom("prompt", config, "local-key", 2, None)
    expected = [initial] + ([] if wider is None else [wider])
    assert [r["body"][r["key"]] for r in transport["requests"]] == expected
    assert (result is None) == (wider is None)
    if wider is not None:
        assert result[0] == ENVELOPE
        assert result[1] == invoke.Usage(7, 11)
    assert dataclasses.asdict(config) == before


@pytest.mark.parametrize("format_name", ["openai", "anthropic", "vertex"])
@pytest.mark.parametrize("override", [16384, 65536])
def test_fixed_override_preserves_initial_wire_budget_without_retry(transport, format_name, override):
    config = backend(format_name, max_tokens=20000, output_ceiling=override, output_token_limit=131072)
    dispatch(config)
    assert invoke._retry_with_more_headroom("prompt", config, "local-key", 2, None) is None
    assert [r["body"][r["key"]] for r in transport["requests"]] == [override]


@pytest.mark.parametrize("format_name", ["openai", "anthropic", "vertex"])
def test_negative_override_keeps_existing_absent_override_semantics(transport, format_name):
    config = backend(format_name, max_tokens=20000, output_ceiling=-1, output_token_limit=40000)
    dispatch(config)
    result = invoke._retry_with_more_headroom("prompt", config, "local-key", 2, None)
    assert result[0] == ENVELOPE
    assert [r["body"][r["key"]] for r in transport["requests"]] == [20000, 40000]


@pytest.mark.parametrize("format_name", ["openai", "anthropic", "vertex"])
@pytest.mark.parametrize("limit", [0, 40000])
def test_public_invoke_retains_continuations_and_bounds_final_retry(transport, format_name, limit):
    config = backend(format_name, max_tokens=20000, output_token_limit=limit)
    transport["truncate"] = 3
    if limit:
        result = invoke.llm_invoke("prompt", backend=config, timeout_s=2)
        assert result.content == ENVELOPE
        assert result.is_truncated is True
    else:
        with pytest.raises(
            invoke.LLMInvokeError, match="continuation exhausted after 2 attempts"
        ) as caught:
            invoke.llm_invoke("prompt", backend=config, timeout_s=2)
        assert caught.value.kind == "truncated"
        assert caught.value.retryable is False
    assert [r["body"][r["key"]] for r in transport["requests"]] == [20000] * 3 + (
        [40000] if limit else []
    )


@pytest.mark.parametrize("format_name", ["openai", "anthropic", "vertex"])
@pytest.mark.parametrize(
    "fields", [{"max_tokens": 65537}, {"max_completion_tokens": 65537}, {"output_ceiling": 65537}]
)
def test_incompatible_initial_cap_refused_before_transport_and_vertex_auth(
    transport, format_name, fields
):
    config = backend(format_name, output_token_limit=65536, **fields)
    with pytest.raises(invoke.LLMInvokeError, match="exceeds output_token_limit") as caught:
        dispatch(config)
    assert caught.value.retryable is False
    assert transport["requests"] == []
    assert transport["auth"] == []


def entry(format_name="openai", **fields):
    base = {"name": "local", "type": "api", "format": format_name, "max_tokens": 10}
    if format_name == "vertex":
        base.update(project_id="local-project")
    else:
        base.update(base_url="https://local.invalid", api_key_env="LOCAL_BUDGET_KEY")
    return {**base, **fields}


@pytest.mark.parametrize("format_name", ["openai", "anthropic", "vertex"])
@pytest.mark.parametrize("limit", [None, 0, 20])
def test_parser_stores_limit_and_keeps_initial_selection(format_name, limit):
    config = _parse_backend_entry(entry(format_name, output_token_limit=limit))
    assert config.output_token_limit == (limit or 0)
    assert config.max_tokens == 10


@pytest.mark.parametrize("limit", [-1, True, False, 1.5, "20", [], {}])
def test_invalid_limit_refused_by_parser_and_direct_request(transport, limit):
    with pytest.raises(CliError, match="output_token_limit must be a nonnegative integer"):
        _parse_backend_entry(entry(output_token_limit=limit))
    with pytest.raises(invoke.LLMInvokeError, match="output_token_limit must be a nonnegative integer"):
        dispatch(backend("openai", max_tokens=10, output_token_limit=limit))
    assert transport["requests"] == []


@pytest.mark.parametrize(
    "fields", [{"max_tokens": 21}, {"max_completion_tokens": 21}, {"output_ceiling": 21}]
)
def test_parser_rejects_initial_cap_above_known_limit(fields):
    with pytest.raises(CliError, match="exceeds output_token_limit"):
        _parse_backend_entry(entry(output_token_limit=20, **fields))


def test_limit_is_protected_and_api_only(transport, monkeypatch):
    with pytest.raises(CliError, match="protected key 'output_token_limit'"):
        _parse_backend_entry(entry(params={"output_token_limit": 20}))
    with pytest.raises(invoke.LLMInvokeError, match="protected key 'output_token_limit'"):
        dispatch(backend("openai", params={"output_token_limit": 20}))
    with pytest.raises(CliError, match="only valid on api backends"):
        _parse_backend_entry({"name": "cli", "type": "cli", "output_token_limit": 20})
    calls = []
    monkeypatch.setattr(
        invoke,
        "_invoke_cli",
        lambda *args: calls.append(args) or invoke.LLMResult(ENVELOPE, invoke.Usage()),
    )
    with pytest.raises(invoke.LLMInvokeError, match="only valid on api backends"):
        invoke.llm_invoke(
            "prompt", backend=BackendConfig(name="cli", type="cli", model="", output_token_limit=20)
        )
    assert calls == []
    assert transport["requests"] == []


def test_cli_zero_limit_preserves_existing_route_without_http(transport, monkeypatch):
    calls = []
    monkeypatch.setattr(
        invoke,
        "_invoke_cli",
        lambda *args: calls.append(args) or invoke.LLMResult(ENVELOPE, invoke.Usage()),
    )
    config = BackendConfig(name="cli", type="cli", model="")
    assert invoke.llm_invoke("prompt", backend=config).content == ENVELOPE
    assert len(calls) == 1
    assert transport["requests"] == []


@pytest.mark.parametrize("format_name", ["openai", "anthropic", "vertex"])
def test_explicit_wire_key_preserved_on_bounded_retry(transport, format_name):
    config = backend(
        format_name, max_tokens=20000, output_token_limit=40000, outcap_key="max_completion_tokens"
    )
    dispatch(config)
    assert invoke._retry_with_more_headroom("prompt", config, "local-key", 2, None)[0] == ENVELOPE
    assert [r["key"] for r in transport["requests"]] == ["max_completion_tokens"] * 2
    assert [r["body"][r["key"]] for r in transport["requests"]] == [20000, 40000]


@pytest.mark.parametrize(("limit", "expected"), [(0, 32), (12, 12), (64, 32)])
def test_probe_request_budget_honors_known_limit(monkeypatch, limit, expected):
    config = backend("openai", max_tokens=8, output_token_limit=limit)
    calls = []
    monkeypatch.setattr(invoke, "llm_invoke", lambda *args, **kwargs: calls.append(kwargs["backend"]))
    assert probe_backend_live(config).ok is True
    assert len(calls) == 1
    assert calls[0].max_tokens == expected
    assert calls[0].output_token_limit == limit
    assert config.max_tokens == 8


def test_small_known_probe_limit_has_accurate_truncation_diagnosis(monkeypatch):
    config = backend("openai", max_tokens=8, output_token_limit=12)
    calls = []

    def truncated(*args, **kwargs):
        calls.append(kwargs["backend"].max_tokens)
        raise invoke.LLMInvokeError("owned truncated probe", kind="truncated", retryable=False)

    monkeypatch.setattr(invoke, "llm_invoke", truncated)
    result = probe_backend_live(config)
    assert calls == [12]
    assert result.ok is False
    assert result.error_class == "truncated-output"
    assert "bounded probe reply" in result.suggestion


def test_schema_preserves_fixed_override_and_documents_distinct_limit():
    schema = json.loads((Path(__file__).parents[1] / "src/code_forge/gate.schema.json").read_text())
    fields = schema["$defs"]["backendEntry"]["properties"]
    assert fields["output_token_limit"]["type"] == "integer"
    assert fields["output_token_limit"]["minimum"] == 0
    assert "unknown" in fields["output_token_limit"]["description"]
    assert "Fixed override" in fields["output_ceiling"]["description"]
