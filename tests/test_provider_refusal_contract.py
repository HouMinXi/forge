"""Explicit provider refusals terminate acquisition; every response is synthetic."""

import io
import json
import socket
import subprocess
import urllib.error
from unittest.mock import Mock

import pytest

import code_forge.llm_invoke as invoke
from code_forge.backend import BackendConfig

GOOD = '{"findings":[],"code_excerpts":[]}'
BROKEN = '{"findings":[{"ok":true},,{"ok":false}]}'
PRIVATE = "PRIVATE-provider-refusal-/home/synthetic-user"
MISSING = object()
FORMATS = ("openai", "anthropic", "vertex")


def _usage(fmt, count=2):
    if fmt == "openai":
        return {
            "prompt_tokens": count,
            "completion_tokens": count + 1,
            "prompt_tokens_details": {"cached_tokens": 0},
        }
    return {"input_tokens": count, "output_tokens": count + 1, "cache_read_input_tokens": 0}


def _payload(fmt, content=GOOD, *, signal=MISSING, finish=None, count=2):
    if fmt == "openai":
        message = {} if content is MISSING else {"content": content}
        if signal is not MISSING:
            message["refusal"] = signal
        return {
            "choices": [{"message": message, "finish_reason": finish or "stop"}],
            "usage": _usage(fmt, count),
        }
    payload = {"stop_reason": "end_turn" if signal is MISSING else signal, "usage": _usage(fmt, count)}
    if finish is not None:
        payload["stop_reason"] = finish
    if content is not MISSING:
        payload["content"] = [{"type": "text", "text": content}]
    return payload


def _refusal(fmt, content=GOOD, **kwargs):
    return _payload(fmt, content, signal=PRIVATE if fmt == "openai" else "refusal", **kwargs)


def _sse(content=MISSING, signal=PRIVATE, *, finish="stop", count=2, later_signals=()):
    chunks = []
    if content is not MISSING:
        chunks.append({"choices": [{"delta": {"content": content}}]})
    chunks.append({"choices": [{"delta": {"refusal": signal}, "finish_reason": finish}]})
    chunks.extend({"choices": [{"delta": {"refusal": item}}]} for item in later_signals)
    chunks.append({"choices": [], "usage": _usage("openai", count)})
    return (
        b"".join(b"data: " + json.dumps(chunk).encode() + b"\n\n" for chunk in chunks)
        + b"data: [DONE]\n"
    )


@pytest.fixture
def run(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("offline refusal test attempted external I/O")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setenv("REFUSAL_CONTRACT_KEY", "synthetic-not-secret")
    slept = Mock()
    monkeypatch.setattr(invoke.time, "sleep", slept)

    def execute(fmt, payloads, *, stream=False, attempts=5):
        if fmt == "vertex":
            auth = pytest.importorskip("google.auth")
            transport = pytest.importorskip("google.auth.transport.requests")
            pytest.importorskip("google.oauth2.service_account")
            monkeypatch.setattr(auth, "default", Mock(return_value=(Mock(token="synthetic"), "project")))
            monkeypatch.setattr(transport, "Request", Mock())
        # urllib3's import performs a local IPv6 capability check. All SDK
        # imports finish before the guard; dispatch cannot open a socket.
        monkeypatch.setattr(socket, "socket", forbidden)
        backend = BackendConfig(
            name="offline",
            type="api",
            model="synthetic",
            format=fmt,
            base_url="https://example.test",
            api_key_env="REFUSAL_CONTRACT_KEY",
            project_id="synthetic-project",
            max_tokens=100,
            output_token_limit=200,
            stream=stream,
        )
        queue = iter(payloads)
        requests = []

        def open_url(request, timeout_s):
            requests.append(request)
            item = next(queue, MISSING)
            if item is MISSING:
                pytest.fail("unexpected additional physical send")
            if isinstance(item, BaseException):
                raise item
            return io.BytesIO(item if isinstance(item, bytes) else json.dumps(item).encode())

        monkeypatch.setattr(invoke, "_open_url", open_url)
        try:
            result = invoke._invoke_api("synthetic prompt", backend, 10, max_attempts=attempts)
        except invoke.LLMInvokeError as error:
            result = error
        return result, requests, slept

    return execute


def _assert_refusal(result, caplog, capsys):
    assert type(result) is invoke.ProviderRefusalError
    assert result.kind == "refusal" and result.retryable is False
    assert not result.is_timeout and not isinstance(result, invoke._TruncatedResponse)
    captured = capsys.readouterr()
    assert PRIVATE not in str(result) + result.stderr + caplog.text + captured.out + captured.err
    assert not hasattr(result, "raw_response")


def _counts(result):
    assert result.usage is not None
    assert {"input_tokens", "output_tokens"} <= result.observed_usage.known_fields
    return result.usage.input_tokens, result.usage.output_tokens


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize(
    "content", [GOOD, "", None, MISSING], ids=["valid-looking", "empty", "null", "missing"]
)
def test_explicit_refusal_overrides_content_and_stops_after_one_send(run, fmt, content, caplog, capsys):
    result, requests, slept = run(fmt, [_refusal(fmt, content)])
    _assert_refusal(result, caplog, capsys)
    assert len(requests) == 1 and _counts(result) == (2, 3)
    slept.assert_not_called()


@pytest.mark.parametrize("fmt", ["anthropic", "vertex"])
@pytest.mark.parametrize("blocks", [None, [], [{"type": "thinking", "thinking": PRIVATE}], 17])
def test_block_refusal_precedes_text_block_extraction(run, fmt, blocks, caplog, capsys):
    payload = _refusal(fmt)
    payload["content"] = blocks
    result, requests, _ = run(fmt, [payload])
    _assert_refusal(result, caplog, capsys)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "content", [GOOD, "", MISSING], ids=["content-before", "empty-before", "only-refusal"]
)
@pytest.mark.parametrize("finish", ["stop", "length"])
def test_sse_refusal_survives_assembly_and_keeps_final_usage(run, content, finish, caplog, capsys):
    result, requests, slept = run("openai", [_sse(content, finish=finish)], stream=True)
    _assert_refusal(result, caplog, capsys)
    assert len(requests) == 1 and _counts(result) == (2, 3)
    slept.assert_not_called()


def test_openai_refusal_takes_precedence_over_contradictory_length(run, caplog, capsys):
    result, requests, _ = run("openai", [_refusal("openai", finish="length")])
    _assert_refusal(result, caplog, capsys)
    assert len(requests) == 1


@pytest.mark.parametrize("later_signal", [None, "", False])
def test_sse_later_unpopulated_signal_cannot_clear_refusal(run, later_signal, caplog, capsys):
    result, requests, _ = run("openai", [_sse(GOOD, later_signals=(later_signal,))], stream=True)
    _assert_refusal(result, caplog, capsys)
    assert len(requests) == 1 and _counts(result) == (2, 3)


@pytest.mark.parametrize("stream", [False, True])
def test_existing_stream_error_precedes_refusal_metadata(run, stream):
    payload = _sse(GOOD, finish="error") if stream else _refusal("openai", finish="error")
    result, requests, _ = run("openai", [payload], stream=stream, attempts=1)
    assert type(result) is invoke.LLMInvokeError and result.retryable is True
    assert "finish_reason=error" in str(result) and len(requests) == 1
    assert PRIVATE not in str(result)


@pytest.mark.parametrize("stream", [False, True])
def test_content_filter_without_refusal_keeps_existing_content_handling(run, stream):
    payload = (
        _sse(GOOD, None, finish="content_filter")
        if stream
        else _payload("openai", finish="content_filter")
    )
    result, requests, _ = run("openai", [payload], stream=stream, attempts=1)
    assert isinstance(result, invoke.LLMResult) and result.content == json.loads(GOOD)
    assert len(requests) == 1


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("signal", [None, "", False, 17, {}, []])
def test_null_empty_or_nonstring_signal_is_not_a_provider_refusal(run, fmt, signal):
    result, requests, _ = run(fmt, [_payload(fmt, signal=signal)])
    assert isinstance(result, invoke.LLMResult) and result.content == json.loads(GOOD)
    assert len(requests) == 1


@pytest.mark.parametrize("signal", [None, "", False, 17, {}, []])
def test_sse_ignores_unpopulated_or_nonstring_refusal_signal(run, signal):
    result, requests, _ = run("openai", [_sse(GOOD, signal)], stream=True)
    assert isinstance(result, invoke.LLMResult) and result.content == json.loads(GOOD)
    assert len(requests) == 1


@pytest.mark.parametrize("fmt", FORMATS)
def test_empty_response_without_refusal_keeps_existing_retry(run, fmt):
    result, requests, slept = run(fmt, [_payload(fmt, ""), _payload(fmt)], attempts=2)
    assert isinstance(result, invoke.LLMResult) and result.content == json.loads(GOOD)
    assert len(requests) == 2
    slept.assert_called_once()


@pytest.mark.parametrize("fmt", FORMATS)
def test_refusal_during_json_correction_is_terminal_and_sums_usage(run, fmt, caplog, capsys):
    result, requests, slept = run(fmt, [_payload(fmt, BROKEN), _refusal(fmt, count=3)])
    _assert_refusal(result, caplog, capsys)
    assert len(requests) == 2 and requests[0].data != requests[1].data
    assert _counts(result) == (5, 7)
    slept.assert_not_called()


@pytest.mark.parametrize("fmt", FORMATS)
def test_refusal_during_first_continuation_stops_before_retry_or_headroom(run, fmt, caplog, capsys):
    cut = _payload(fmt, '{"findings":[', finish="length" if fmt == "openai" else "max_tokens")
    result, requests, slept = run(fmt, [cut, _refusal(fmt, count=3)])
    _assert_refusal(result, caplog, capsys)
    assert len(requests) == 2 and _counts(result) == (5, 7)
    slept.assert_not_called()


@pytest.mark.parametrize("fmt", FORMATS)
def test_refusal_during_headroom_retry_retains_all_usage_and_stops(run, fmt, caplog, capsys):
    payloads = [
        _payload(fmt, '{"findings":[', finish="length" if fmt == "openai" else "max_tokens", count=2),
        _payload(fmt, "not JSON", count=3),
        _payload(fmt, "still not JSON", count=5),
        _refusal(fmt, count=7),
    ]
    result, requests, slept = run(fmt, payloads)
    _assert_refusal(result, caplog, capsys)
    assert len(requests) == 4 and _counts(result) == (17, 21)
    bodies = [json.loads(request.data) for request in requests]
    assert bodies[-1]["messages"][0]["content"] == "synthetic prompt"
    caps = [body.get("max_tokens", body.get("max_completion_tokens")) for body in bodies]
    assert caps == [100, 100, 100, 200]
    slept.assert_called_once_with(invoke._CONTINUE_DELAY_S)


@pytest.mark.parametrize("fmt", FORMATS)
def test_ordinary_malformed_json_still_gets_correction(run, fmt):
    result, requests, _ = run(fmt, [_payload(fmt, BROKEN), _payload(fmt)])
    assert isinstance(result, invoke.LLMResult) and result.content == json.loads(GOOD)
    assert len(requests) == 2 and requests[0].data != requests[1].data


@pytest.mark.parametrize("fmt", FORMATS)
def test_refusal_key_inside_business_json_does_not_select_error_kind(run, fmt):
    text = '{"refusal":"user declined","findings":[]}'
    result, requests, _ = run(fmt, [_payload(fmt, text)])
    assert isinstance(result, invoke.LLMResult) and result.content == json.loads(text)
    assert len(requests) == 1


@pytest.mark.parametrize("fmt", FORMATS)
def test_prose_about_refusing_is_an_ordinary_json_failure(run, fmt):
    result, requests, _ = run(fmt, [_payload(fmt, "I refuse to format this as JSON.")], attempts=1)
    assert isinstance(result, invoke.LLMInvokeError) and result.kind == "no_json"
    assert not isinstance(result, invoke.ProviderRefusalError) and len(requests) == 1


@pytest.mark.parametrize("fmt", FORMATS)
def test_schema_http_400_is_not_a_provider_refusal(run, fmt):
    body = b'{"error":{"code":"invalid_json_schema","message":"refusal unsupported response_format"}}'
    error = urllib.error.HTTPError("https://example.test", 400, "Bad Request", {}, io.BytesIO(body))
    result, requests, slept = run(fmt, [error])
    assert type(result) is invoke.LLMInvokeError and result.exit_code == 400
    assert result.kind != "refusal" and result.retryable is False and len(requests) == 1
    assert "invalid_json_schema" in str(result)
    slept.assert_not_called()
