"""Runtime response provenance through the real invocation transport (#30)."""

import dataclasses
import importlib
import json
from unittest.mock import Mock

import pytest

from code_forge.backend import BackendConfig
from code_forge.runtime import RuntimeRunner

invoke = importlib.import_module("code_forge.llm_invoke")
DIFF = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
RISK = {"surfaces": ["network"], "findings": [{"file": "a.py", "line": 1, "description": "risk"}]}
EMPTY = '{"surfaces":[],"findings":[]}'
TRAP = (
    '{"surfaces":["network"],"findings":[{"file":"a.py","line":1,'
    '"description":"risk","details":{"surfaces":[],"findings":[]}}'
)


@pytest.fixture(params=["openai", "anthropic", "vertex"])
def api(monkeypatch, request):
    monkeypatch.setenv("RUNTIME_TEST_KEY", "local-test")
    monkeypatch.setattr(invoke.time, "sleep", Mock())
    format_name = request.param
    provider = Mock()
    monkeypatch.setattr(invoke, "_invoke_" + format_name, provider)
    backend = BackendConfig(
        name="runtime-test",
        type="api",
        format=format_name,
        base_url="http://127.0.0.1:1/v1",
        api_key_env="RUNTIME_TEST_KEY",
        model="test",
        max_tokens=1024,
    )
    return RuntimeRunner(backend=backend), provider


def response(text):
    return text, {"prompt_tokens": 1, "completion_tokens": 2, "_forge_finish_reason": "stop"}


def test_valid_risk_needs_one_request(api, tmp_path):
    runner, request = api
    request.return_value = response(json.dumps(RISK))
    findings = runner.run(DIFF, tmp_path)
    assert request.call_count == 1
    assert any(f.description == "risk" for f in findings)
    assert runner.last_surfaces == ["network"]
    assert not runner.infra_errors


def test_continuation_cannot_replace_original_risk(api, tmp_path):
    runner, request = api
    request.side_effect = [response(TRAP), response(EMPTY), response(EMPTY)]
    findings = runner.run(DIFF, tmp_path)
    assert any(f.id == "runtime-skipped" for f in findings)
    assert runner.infra_errors
    assert runner.last_surfaces == []
    assert request.call_count == 3


@pytest.mark.parametrize("text", [EMPTY + " explanation", "```json\n" + EMPTY + "\n```"])
def test_non_root_text_is_not_silently_extracted(api, tmp_path, text):
    runner, request = api
    request.return_value = response(text)
    findings = runner.run(DIFF, tmp_path)
    assert any(f.id == "runtime-skipped" for f in findings)
    assert runner.infra_errors
    assert runner.last_surfaces == []


@pytest.mark.parametrize(
    "text",
    [
        '{"surfaces":[],"findings":[],"findings":[]}',
        '{"surfaces":[],"findings":[],"meta":NaN}',
        '{"surfaces":[],"findings":[],"meta":1e999}',
        '{"surfaces":[],"findings":[{"file":"a.py","line":true,"description":"risk"}]}',
        '{"surfaces":["network"],"findings":[{"file":"a.py","line":1,"description":"say "hi""}]}',
        '{"surfaces":[],"findings":[{"file":"a.py","line":1,"description":"ok"},null]}',
    ],
)
def test_malformed_api_response_visible_skip(api, tmp_path, text):
    runner, request = api
    request.return_value = response(text)
    assert any(f.id == "runtime-skipped" for f in runner.run(DIFF, tmp_path))
    assert runner.infra_errors
    assert runner.last_surfaces == []
    assert request.call_count <= 2


@pytest.mark.parametrize("wrapped", [False, True])
def test_complete_response_keeps_metadata_and_singleton_compat(api, tmp_path, wrapped):
    runner, request = api
    payload = {**RISK, "meta": {"unknown": "inert"}}
    request.return_value = response(json.dumps([payload] if wrapped else payload))
    assert any(f.description == "risk" for f in runner.run(DIFF, tmp_path))
    assert request.call_count == 1
    assert not runner.infra_errors


def test_genuine_continuation_preserves_original_risk(api, tmp_path):
    runner, request = api
    request.side_effect = [response(TRAP), response("]}")]
    findings = runner.run(DIFF, tmp_path)
    assert any(f.description == "risk" for f in findings)
    assert runner.last_surfaces == ["network"]
    assert not runner.infra_errors
    assert request.call_count == 2


def test_correction_must_be_another_complete_root(api, tmp_path):
    runner, request = api
    request.side_effect = [response('{"surfaces":[], "findings":[],}'), response(json.dumps(RISK))]
    assert any(f.description == "risk" for f in runner.run(DIFF, tmp_path))
    assert not runner.infra_errors
    assert request.call_count == 2


@pytest.mark.parametrize("valid", [False, True])
def test_fresh_wider_retry_uses_same_boundary(api, tmp_path, valid):
    runner, request = api
    runner._backend = dataclasses.replace(runner._backend, output_token_limit=4096)
    last = json.dumps(RISK) if valid else EMPTY + " unrelated prose"
    request.side_effect = [response(TRAP), response(EMPTY), response(EMPTY), response(last)]
    findings = runner.run(DIFF, tmp_path)
    assert request.call_count == 4
    assert bool(runner.infra_errors) is not valid
    assert any(f.description == "risk" for f in findings) is valid
    assert any(f.id == "runtime-skipped" for f in findings) is not valid


@pytest.mark.parametrize("wrapper", ["raw", "string", "object", "events"])
@pytest.mark.parametrize(
    "payload", ["risk", "duplicate", "nonfinite", "string-root", "fence", "truncated"]
)
def test_cli_root_contract(monkeypatch, tmp_path, wrapper, payload):
    from unittest.mock import MagicMock

    # An encoded string inserted as an envelope member is the normal
    # string-result wire format, not a double-encoded model response.
    valid = payload == "risk" or (payload == "string-root" and wrapper == "object")
    risk = json.dumps(RISK)
    text = {
        "risk": risk,
        "duplicate": risk[:-1] + ',"findings":[]}',
        "nonfinite": risk[:-1] + ',"meta":NaN}',
        "string-root": json.dumps(risk),
        "fence": "```json\n" + risk + "\n```",
        "truncated": TRAP,
    }[payload]
    if wrapper in {"string", "events"}:
        envelope = {"type": "result", "result": text}
        text = json.dumps([{"type": "system"}, envelope] if wrapper == "events" else envelope)
    elif wrapper == "object":
        text = '{"type":"result","result":' + text + "}"
    proc = MagicMock(returncode=0, pid=12345)
    proc.communicate.return_value = (text, "")
    monkeypatch.setattr(invoke.shutil, "which", Mock(return_value="/usr/bin/claude"))
    popen = Mock(return_value=proc)
    monkeypatch.setattr(invoke.subprocess, "Popen", popen)
    runner = RuntimeRunner(backend=BackendConfig(name="runtime-cli", type="cli", model="test"))
    findings = runner.run(DIFF, tmp_path)
    assert popen.call_count == 1
    assert bool(runner.infra_errors) is not valid
    assert any(f.description == "risk" for f in findings) is valid
    assert any(f.id == "runtime-skipped" for f in findings) is not valid


@pytest.mark.parametrize("value", [None, 0, 1, "true"])
def test_strict_response_requires_bool_before_io(monkeypatch, value):
    request = Mock(side_effect=AssertionError("unexpected dispatch"))
    monkeypatch.setattr(invoke, "_invoke_api", request)
    with pytest.raises(ValueError, match="strict_response must be a bool"):
        invoke.llm_invoke("prompt", strict_response=value)
    request.assert_not_called()


@pytest.mark.parametrize("format_name", ["openai", "anthropic"])
@pytest.mark.parametrize("truncated", [False, True])
def test_actual_http_adapter_keeps_runtime_root(monkeypatch, tmp_path, format_name, truncated):
    """Only the HTTP byte boundary is replaced, not the provider adapter."""
    from unittest.mock import MagicMock

    monkeypatch.setenv("RUNTIME_TEST_KEY", "local-test")
    monkeypatch.setattr(invoke.time, "sleep", Mock())

    def wire(text, cut=False):
        if format_name == "openai":
            payload = {
                "choices": [{"message": {"content": text}, "finish_reason": "length" if cut else "stop"}]
            }
        else:
            payload = {
                "content": [{"type": "text", "text": text}],
                "stop_reason": "max_tokens" if cut else "end_turn",
            }
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(payload).encode()
        return response

    replies = [wire(TRAP, True), wire(EMPTY), wire(EMPTY)] if truncated else [wire(json.dumps(RISK))]
    http = Mock(side_effect=replies)
    monkeypatch.setattr(invoke.urllib.request, "urlopen", http)
    backend = BackendConfig(
        name="runtime-http",
        type="api",
        format=format_name,
        base_url="https://example.invalid/v1",
        api_key_env="RUNTIME_TEST_KEY",
        model="test",
        max_tokens=1024,
    )
    runner = RuntimeRunner(backend=backend)
    findings = runner.run(DIFF, tmp_path)
    assert bool(runner.infra_errors) is truncated
    assert any(f.id == "runtime-skipped" for f in findings) is truncated
    assert any(f.description == "risk" for f in findings) is not truncated
    assert http.call_count == (3 if truncated else 1)


def test_string_root_cannot_be_decoded_twice(api, tmp_path):
    runner, request = api
    request.return_value = response(json.dumps(json.dumps(RISK)))
    findings = runner.run(DIFF, tmp_path)
    assert any(f.id == "runtime-skipped" for f in findings)
    assert runner.infra_errors
    assert runner.last_surfaces == []


def test_strict_response_cannot_request_l1_evidence():
    with pytest.raises(ValueError, match="incompatible"):
        invoke.llm_invoke("prompt", strict_response=True, l1_evidence_required=True)


def test_strict_response_without_keys_does_not_repair_excerpts(api):
    runner, request = api
    request.return_value = response(json.dumps(RISK))
    result = invoke.llm_invoke("prompt", backend=runner._backend, strict_response=True)
    assert result.content == RISK
    assert request.call_count == 1
