"""Bounded recovery of malformed model JSON without local content repair."""
import dataclasses
import importlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from code_forge.backend import BackendConfig

invoke = importlib.import_module("code_forge.llm_invoke")
BAD = r'{"description":"calls.\q end"}'
SOURCE = 'int f(void)\r\n{\n\treturn 0;  \n}\n\n'
GOOD = {"description": "calls. end", "excerpt": SOURCE}


def backend():
    return BackendConfig(
        name="json-test", type="api", format="openai",
        base_url="http://127.0.0.1:1/v1", api_key_env="JSON_TEST_KEY",
        model="test", max_tokens=1024,
    )


def test_api_corrects_complete_syntax_error(monkeypatch, caplog):
    caplog.set_level("INFO", logger="code_forge.llm_invoke")
    monkeypatch.setenv("JSON_TEST_KEY", "local-test")
    request = Mock(side_effect=[
        (BAD, {"prompt_tokens": 10, "completion_tokens": 20,
               "prompt_tokens_details": {"cached_tokens": 3},
               "_forge_finish_reason": "stop"}),
        (json.dumps(GOOD), {"prompt_tokens": 11, "completion_tokens": 21,
                            "prompt_tokens_details": {"cached_tokens": 4}}),
    ])
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    config = backend()
    result = invoke._invoke_api("original task", config, 7, max_attempts=2)
    assert result.content == GOOD
    assert result.usage == invoke.Usage(21, 41, 7)
    assert request.call_count == 2
    first, second = request.call_args_list
    assert first.args == ("original task", config, "local-test", 7)
    assert second.args[1:] == first.args[1:]
    assert second.args[0].startswith("original task")
    assert BAD not in second.args[0]
    assert "JSON" in second.args[0]
    records = [record for record in caplog.records if record.levelname == "INFO"]
    assert [(record.name, record.getMessage()) for record in records] == [
        ("code_forge.llm_invoke", "JSON correction at attempt 2/2 after error at line 1 column 23"),
    ]


@pytest.mark.asyncio
async def test_sampling_corrects_with_one_fresh_message(caplog):
    caplog.set_level("INFO", logger="code_forge.llm_invoke")
    from mcp.types import TextContent

    session = SimpleNamespace(create_message=AsyncMock(side_effect=[
        SimpleNamespace(content=TextContent(type="text", text=BAD),
                        model="test", stopReason="endTurn"),
        SimpleNamespace(content=TextContent(type="text", text=json.dumps(GOOD)),
                        model="test", stopReason="endTurn"),
    ]))
    result = await invoke.invoke_sampling(
        session, "original", system_prompt="system", model_hint="test",
        max_attempts=2,
    )
    assert result.content == GOOD
    assert result.usage == invoke.Usage()
    first, second = session.create_message.call_args_list
    assert first.kwargs == second.kwargs
    assert len(second.args[0]) == 1
    assert second.args[0][0].role == "user"
    text = second.args[0][0].content.text
    assert text.startswith("original")
    assert text != first.args[0][0].content.text
    assert BAD not in text
    records = [record for record in caplog.records if record.levelname == "INFO"]
    assert [(record.name, record.getMessage()) for record in records] == [
        ("code_forge.llm_invoke", "JSON correction at sampling attempt 2/2 after error at line 1 column 23"),
    ]


def parse_error(text):
    try:
        json.loads(text, strict=False)
    except json.JSONDecodeError as exc:
        return exc
    raise AssertionError("fixture must be malformed JSON")


@pytest.mark.parametrize("text", [
    BAD, '{"s":"calls.\\\u201d end"}', '{"a":1,,"b":2}',
    '{"a" 1}', '{"a":1,}', '[1,,2]', '{1:2}',
    '{"a":01}', '{"a":True}', '{"a":undefined}',
    '{"a":"head="" tail"}', r'{"a":"\uZZZZ"}',
])
def test_complete_syntax_classes_request_correction(text):
    error = parse_error(text)
    result = invoke._json_correction_prompt("task", text, error)
    assert result is not None
    assert result.startswith("task")
    assert f"line {error.lineno}, column {error.colno}" in result
    assert text not in result


@pytest.mark.parametrize("text", [
    "", " ", "refused", "X not JSON", "\ufeff" + BAD, "\u200b" + BAD,
    '{"a":', '{"a":"unfinished', '[1,', '{"a":tru',
    '{"a":1e', r'{"a":"\u12',
])
def test_ineligible_or_cut_input_not_corrected(text):
    assert invoke._json_correction_prompt("task", text, parse_error(text)) is None


def test_correction_prompt_contract():
    result = invoke._json_correction_prompt("task", BAD, parse_error(BAD))
    assert result == (
        "task\n\nYour previous response could not be decoded as JSON "
        "(JSONDecodeError at line 1, column 23). "
        "Return the complete response again as valid JSON, not a continuation. "
        "Use valid JSON escaping for quotes and backslashes in strings. "
        "Follow all original task requirements, fields, and source excerpts. "
        "Preserve source whitespace and literal characters. "
        "Do not replace required content with empty or default values."
    )


@pytest.mark.parametrize("delta,accepted", [(-1, True), (0, True), (1, False)])
def test_correction_size_boundary(delta, accepted):
    size = 1_048_576 + delta
    text = BAD[:-2] + "x" * (size - len(BAD)) + BAD[-2:]
    assert len(text) == size
    result = invoke._json_correction_prompt("task", text, parse_error(text))
    assert (result is not None) == accepted


@pytest.mark.parametrize("attempts,prefix", [(1, 0), (2, 1), (3, 1)])
def test_correction_uses_remaining_budget(monkeypatch, attempts, prefix):
    monkeypatch.setenv("JSON_TEST_KEY", "local-test")
    monkeypatch.setattr(invoke.time, "sleep", Mock())
    responses = [invoke.LLMInvokeError("connection", kind="conn")] * prefix
    responses += [(BAD, {"_forge_finish_reason": "stop"}),
                  (json.dumps(GOOD), {})]
    request = Mock(side_effect=responses)
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    if attempts > prefix + 1:
        assert invoke._invoke_api("task", backend(), 7,
                                  max_attempts=attempts).content == GOOD
    else:
        with pytest.raises(invoke.LLMInvokeError):
            invoke._invoke_api("task", backend(), 7, max_attempts=attempts)
    assert request.call_count == attempts


@pytest.mark.parametrize("failure", ["bad", "empty", "cut", "unmarked_cut",
                                      "conn", "timeout", "http"])
def test_api_correction_failure_is_terminal(monkeypatch, failure):
    monkeypatch.setenv("JSON_TEST_KEY", "local-test")
    errors = {
        "bad": (BAD, {"_forge_finish_reason": "stop"}),
        "empty": (None, {}),
        "cut": invoke._TruncatedResponse(
            "cut", '{"a":', {}, 1024, kind="truncated", retryable=False),
        "unmarked_cut": ('{"a":', {"_forge_finish_reason": "stop"}),
        "conn": invoke.LLMInvokeError("connection", kind="conn"),
        "timeout": TimeoutError("test timeout"),
        "http": invoke.LLMInvokeError("HTTP 503", exit_code=503),
    }
    request = Mock(side_effect=[(BAD, {"_forge_finish_reason": "stop"}),
                                errors[failure], (json.dumps(GOOD), {})])
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    slept = Mock()
    monkeypatch.setattr(invoke.time, "sleep", slept)
    with pytest.raises(invoke.LLMInvokeError) as caught:
        invoke._invoke_api("task", backend(), 7, max_attempts=5, retry_timeout=True)
    assert request.call_count == 2
    assert caught.value.retryable is False
    assert not slept.called
    expected_kind = {"bad": "no_json", "empty": "empty", "cut": "truncated",
                     "unmarked_cut": "truncated", "conn": "conn"}
    if failure in expected_kind:
        assert caught.value.kind == expected_kind[failure]
    if failure == "timeout":
        assert caught.value.is_timeout
        assert isinstance(caught.value.__cause__, TimeoutError)
    if failure == "http":
        assert caught.value.exit_code == 503


@pytest.mark.parametrize("value", [
    None, True, False, 1, -1.5, "", [], {},
    {"a": [1, True, None, {"b": "value"}]},
    *[{"excerpt": SOURCE + "\\" * n + '\"'} for n in range(9)],
    *[{"s": chr(c)} for c in range(32)],
    {"s": "\u4e2d\u6587\U0001f642\ud800"},
])
def test_legal_inputs_remain_unchanged(monkeypatch, value):
    monkeypatch.setenv("JSON_TEST_KEY", "local-test")
    request = Mock(return_value=(json.dumps(value), {}))
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    result = invoke._invoke_api("task", backend(), 7)
    assert result.content == value
    assert request.call_count == 1


@pytest.mark.parametrize("text", [
    '{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}',
    '{"excerpt":"raw\n\tline\r\n"}',
])
def test_existing_nonstandard_compatibility(monkeypatch, text):
    monkeypatch.setenv("JSON_TEST_KEY", "local-test")
    request = Mock(return_value=(text, {}))
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    actual = invoke._invoke_api("task", backend(), 7).content
    assert json.dumps(actual) == json.dumps(json.loads(text, strict=False))
    assert request.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["bad", "empty", "cut", "network"])
async def test_sampling_correction_failure_is_terminal(failure):
    from mcp.types import TextContent

    def response(text, stop="endTurn"):
        return SimpleNamespace(content=TextContent(type="text", text=text),
                               model="test", stopReason=stop)

    failures = {"bad": response(BAD), "empty": response(""),
                "cut": response('{"a":', "maxTokens"),
                "network": invoke.LLMInvokeError("network", kind="conn")}
    session = SimpleNamespace(create_message=AsyncMock(
        side_effect=[response(BAD), failures[failure], response('{}')]))
    with pytest.raises(invoke.LLMInvokeError) as caught:
        await invoke.invoke_sampling(session, "task", max_attempts=5)
    assert caught.value.retryable is False
    assert session.create_message.call_count == 2


@pytest.mark.parametrize("fmt", ["anthropic", "vertex"])
def test_other_api_formats_sum_usage(monkeypatch, fmt):
    monkeypatch.setenv("JSON_TEST_KEY", "local-test")
    request = Mock(side_effect=[
        (BAD, {"input_tokens": 10, "output_tokens": 20,
               "cache_read_input_tokens": 3, "_forge_finish_reason": "end_turn"}),
        (json.dumps(GOOD), {"input_tokens": 11, "output_tokens": 21,
                            "cache_read_input_tokens": 4}),
    ])
    monkeypatch.setattr(invoke, "_invoke_" + fmt, request)
    config = dataclasses.replace(backend(), format=fmt)
    result = invoke._invoke_api("task", config, 7, max_attempts=2)
    assert result.content == GOOD
    assert result.usage == invoke.Usage(21, 41, 7)
    assert request.call_count == 2
    assert request.call_args_list[0].args[1:] == request.call_args_list[1].args[1:]


@pytest.mark.asyncio
@pytest.mark.parametrize("attempts,prefix", [(1, 0), (2, 1), (3, 1)])
async def test_sampling_uses_remaining_budget(monkeypatch, attempts, prefix):
    from mcp.types import TextContent

    monkeypatch.setattr(invoke.asyncio, "sleep", AsyncMock())
    responses = [invoke.LLMInvokeError("connection", kind="conn")] * prefix
    responses += [
        SimpleNamespace(content=TextContent(type="text", text=text),
                        model="test", stopReason="endTurn")
        for text in (BAD, json.dumps(GOOD))
    ]
    session = SimpleNamespace(create_message=AsyncMock(side_effect=responses))
    if attempts > prefix + 1:
        result = await invoke.invoke_sampling(session, "task", max_attempts=attempts)
        assert result.content == GOOD
    else:
        with pytest.raises(invoke.LLMInvokeError):
            await invoke.invoke_sampling(session, "task", max_attempts=attempts)
    assert session.create_message.call_count == attempts


@pytest.mark.parametrize("status", [429, 503])
def test_terminal_error_identity_preserved(monkeypatch, status):
    monkeypatch.setenv("JSON_TEST_KEY", "local-test")
    original = invoke.LLMInvokeError(
        "server failed", exit_code=status, stderr="server diagnostic",
        retryable=True, kind="conn",
    )
    original.__cause__ = OSError("original cause")
    request = Mock(side_effect=[(BAD, {}), original])
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    with pytest.raises(invoke.LLMInvokeError) as caught:
        invoke._invoke_api("task", backend(), 7)
    assert caught.value is original
    assert caught.value.exit_code == status
    assert caught.value.stderr == "server diagnostic"
    assert caught.value.kind == "conn"
    assert caught.value.__cause__ is original.__cause__
    assert not caught.value.retryable
    assert request.call_count == 2


def test_diagnostic_comes_from_correction_response(monkeypatch):
    monkeypatch.setenv("JSON_TEST_KEY", "local-test")
    second = '{"second":1,,"marker":2}'
    request = Mock(side_effect=[(BAD, {}), (second, {"_forge_finish_reason": "stop"})])
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    with pytest.raises(invoke.LLMInvokeError) as caught:
        invoke._invoke_api("task", backend(), 7)
    assert isinstance(caught.value.__cause__, json.JSONDecodeError)
    assert caught.value.__cause__.doc == second
    assert "second" in caught.value.stderr
    assert "calls" not in caught.value.stderr
    assert request.call_count == 2


@pytest.mark.parametrize("second_status", [200, 503])
def test_real_http_recovery(monkeypatch, second_status):
    monkeypatch.setenv("JSON_TEST_KEY", "local-test")
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(
                int(self.headers["Content-Length"]))))
            status = 200 if len(requests) == 1 else second_status
            body = json.dumps({
                "choices": [{"message": {"content": BAD if len(requests) == 1
                                         else json.dumps(GOOD)},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            }).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = dataclasses.replace(
                backend(), base_url=f"http://127.0.0.1:{server.server_port}/v1",
                stream=False,
            )
            if second_status == 200:
                result = invoke._invoke_api("task", config, 3, max_attempts=5)
                assert result.content == GOOD
                assert result.usage == invoke.Usage(20, 40)
            else:
                with pytest.raises(invoke.LLMInvokeError) as caught:
                    invoke._invoke_api("task", config, 3, max_attempts=5)
                assert caught.value.retryable is False
                assert "503" in str(caught.value)
            assert len(requests) == 2
            assert requests[0]["model"] == requests[1]["model"] == "test"
            assert requests[0]["max_tokens"] == requests[1]["max_tokens"] == 1024
            assert len(requests[1]["messages"]) == 1
            text = requests[1]["messages"][0]["content"]
            assert text.startswith("task") and BAD not in text
        finally:
            server.shutdown()
            thread.join(timeout=5)
        assert not thread.is_alive()
