"""Actual SSE decoder with controlled byte readers; no durability claim."""

from dataclasses import replace
from http.client import HTTPResponse, IncompleteRead
import io
import json
import threading
import time
import urllib.error

import pytest

from code_forge.invocation_stream import WireCapture
from code_forge.backend import BackendConfig
from code_forge.llm_invoke import LLMInvokeError, _invoke_openai, _read_captured_body, _read_sse


def _stream():
    chunks = [
        {"choices": [{"delta": {"reasoning_content": "private reasoning 中文"}}]},
        {"choices": [{"delta": {"content": "完整结果"}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 3}},
    ]
    return (
        b"".join(
            b"data: " + json.dumps(chunk, ensure_ascii=False).encode() + b"\r\n\r\n" for chunk in chunks
        )
        + b"data: [DONE]\r\n\r\n"
    )


def _capture(*, limit=1_000_000, chunk=11, strict=False, write=None, warn=None):
    sink, warnings = io.BytesIO(), []
    capture = WireCapture(
        write=sink.write if write is None else write,
        warn=warnings.append if warn is None else warn,
        limit_bytes=limit,
        chunk_bytes=chunk,
        strict=strict,
    )
    return capture, sink, warnings


@pytest.mark.parametrize("chunk", [1, 2, 3, 7, 64, 65536])
def test_real_sse_decoder_preserves_raw_reasoning_and_existing_result(chunk):
    data = _stream()
    capture, sink, warnings = _capture(chunk=chunk)
    result = _read_sse(io.BytesIO(data), wire_capture=capture)
    assert result == _read_sse(io.BytesIO(data))
    # A parser can stop at DONE before reading the final blank line. Do not
    # manufacture unread bytes to claim byte-for-byte transport completion.
    recorded = sink.getvalue()
    assert data.startswith(recorded)
    assert b"private reasoning" in recorded
    assert b"data: [DONE]" in recorded
    assert "reasoning_content" not in str(result)
    state = capture.seal()
    assert state.observed_bytes == state.accepted_bytes == len(recorded)
    assert not state.interrupted and not state.failed and not state.truncated
    assert not warnings


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_capture_capacity_does_not_truncate_decoder_input(delta):
    data = _stream()
    capture, sink, warnings = _capture(limit=len(data) + delta, chunk=len(data))
    assert _read_sse(io.BytesIO(data), wire_capture=capture) == _read_sse(io.BytesIO(data))
    assert sink.getvalue() == data[: len(data) + delta]
    assert capture.seal().truncated is (delta < 0)
    assert warnings == (["capture_limit_reached"] if delta < 0 else [])


def test_failed_recording_warns_once_without_changing_decoder_result():
    def broken(_):
        raise OSError("private data must not reach warning")

    capture, _, warnings = _capture(write=broken)
    assert _read_sse(io.BytesIO(_stream()), wire_capture=capture) == _read_sse(io.BytesIO(_stream()))
    assert warnings == ["capture_write_failed"]
    state = capture.seal()
    assert state.failed and state.write_uncertain and state.accepted_bytes == 0


def test_warning_callback_failure_does_not_change_decoder_result():
    def broken(_):
        raise OSError("warning delivery unavailable")

    capture, _, _ = _capture(limit=0, warn=broken)
    assert _read_sse(io.BytesIO(_stream()), wire_capture=capture) == _read_sse(io.BytesIO(_stream()))
    assert capture.seal().failed


def test_strict_failure_is_nonretryable_and_never_reports_complete():
    capture, _, warnings = _capture(limit=0, strict=True)
    with pytest.raises(LLMInvokeError) as caught:
        _read_sse(io.BytesIO(_stream()), wire_capture=capture)
    assert caught.value.kind == "audit_persistence_error" and not caught.value.retryable
    assert capture.seal().interrupted and capture.seal().truncated
    assert warnings == ["capture_limit_reached"]


def test_invalid_json_retains_received_raw_before_parser_rejects_it():
    data = b"data: {invalid\n\n"
    capture, sink, _ = _capture()
    with pytest.raises(LLMInvokeError):
        _read_sse(io.BytesIO(data), wire_capture=capture)
    assert sink.getvalue() == data
    assert capture.seal().interrupted


def test_incomplete_read_partial_is_captured_once_and_original_error_survives():
    error = IncompleteRead(b"partial", 10)

    class Reader:
        def read(self, n):
            assert n == 11
            raise error

    capture, sink, _ = _capture()
    with pytest.raises(IncompleteRead) as caught:
        _read_sse(Reader(), wire_capture=capture)
    assert caught.value is error
    assert sink.getvalue() == b"partial"
    assert capture.seal().interrupted


def test_timeout_freezes_prefix_and_rejects_late_reader_completion():
    release, finished = threading.Event(), threading.Event()

    class Reader:
        def read(self, n):
            try:
                assert n == 11
                release.wait(5)
                return b"late bytes"
            finally:
                finished.set()

    capture, sink, _ = _capture()
    try:
        with pytest.raises(LLMInvokeError) as caught:
            _read_sse(Reader(), time.monotonic() + 0.03, "fixture", wire_capture=capture)
        assert caught.value.is_timeout
        before = capture.seal()
        assert before.interrupted and before.accepted_bytes == 0
    finally:
        release.set()
        assert finished.wait(5)
    assert capture.seal() == before
    assert sink.getvalue() == b""


def test_reader_requests_are_bounded_even_with_no_newline():
    calls = []

    class Reader(io.BytesIO):
        def read1(self, n):
            calls.append(n)
            return super().read1(n)

    capture, sink, _ = _capture(chunk=13)
    assert list(capture.lines(Reader(b"x" * 1000))) == [b"x" * 1000]
    assert set(calls) == {13}
    assert sink.getvalue() == b"x" * 1000
    assert capture.seal().eof_seen


@pytest.mark.parametrize("error_type", [MemoryError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("threaded", [False, True])
def test_fatal_sink_error_survives_exactly_even_through_reader_thread(error_type, threaded):
    error = error_type("controlled primary")

    def broken(_):
        raise error

    capture, _, warnings = _capture(write=broken)
    with pytest.raises(error_type) as caught:
        _read_sse(
            io.BytesIO(_stream()),
            time.monotonic() + 5 if threaded else None,
            wire_capture=capture,
        )
    assert caught.value is error
    assert capture.seal().interrupted
    assert not warnings


def test_strict_partial_capture_never_replaces_incomplete_read():
    error = IncompleteRead(b"partial", 10)

    class Reader:
        def read(self, n):
            raise error

    capture, _, warnings = _capture(limit=0, strict=True)
    with pytest.raises(IncompleteRead) as caught:
        _read_sse(Reader(), wire_capture=capture)
    assert caught.value is error
    assert capture.seal().truncated
    assert warnings == ["capture_limit_reached"]


@pytest.mark.parametrize("site", ["read_eof", "write", "warn"])
@pytest.mark.parametrize("overflow", [False, True])
def test_sealed_snapshot_stays_stable_after_late_io_or_warning(site, overflow):
    entered, release = threading.Event(), threading.Event()

    def block():
        entered.set()
        assert release.wait(5)

    def write(data):
        block()
        return len(data)

    def warn(_):
        block()
        raise OSError("late reporting failure")

    class Reader:
        def read(self, n):
            block()
            return b""

    capture, _, _ = _capture(
        limit=0 if site == "warn" else (2 if overflow else 100),
        write=write if site == "write" else None,
        warn=warn if site == "warn" else None,
    )
    worker = threading.Thread(
        target=lambda: list(capture.lines(Reader())) if site == "read_eof" else capture.append(b"raw")
    )
    worker.start()
    try:
        assert entered.wait(5)
        frozen = capture.seal(interrupted=True)
        assert frozen.write_uncertain is (site == "write")
        assert frozen.accepted_bytes == 0
        assert frozen.truncated is (site == "warn" or (site == "write" and overflow))
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert capture.seal() == frozen


def test_fatal_write_preserves_already_observed_overflow():
    error = MemoryError("controlled")

    def broken(_):
        raise error

    capture, _, _ = _capture(limit=2, write=broken)
    with pytest.raises(MemoryError):
        capture.append(b"four")
    frozen = capture.seal(interrupted=True)
    assert frozen.truncated and frozen.write_uncertain
    assert frozen.observed_bytes == 4 and frozen.accepted_bytes == 0


@pytest.mark.parametrize("error_type", [MemoryError, KeyboardInterrupt, SystemExit])
def test_fatal_uncertainty_prevents_another_sink_write_before_sealing(error_type):
    error = error_type("controlled fatal sink error")
    calls = []

    def write(data):
        calls.append(data)
        if len(calls) == 1:
            raise error
        return len(data)

    capture, _, _ = _capture(write=write)
    with pytest.raises(error_type) as caught:
        capture.append(b"first")
    assert caught.value is error
    # Even a caller that catches the fatal exception cannot resume this sink.
    capture.append(b"second")
    assert calls == [b"first"]
    state = capture.seal()
    assert state.write_uncertain and state.accepted_bytes == 0 and not state.complete


@pytest.mark.parametrize("limit", [0, 11, 1000])
def test_nonstream_body_keeps_parser_input_independent_of_recording_cap(limit):
    data = b'{"reasoning_content":"private","choices":[]}'
    capture, sink, warnings = _capture(limit=limit)
    result = _read_captured_body(io.BytesIO(data), time.monotonic() + 5, "fixture", capture)
    assert result == data
    assert sink.getvalue() == data[:limit]
    state = capture.seal()
    assert state.eof_seen and not state.interrupted
    assert state.truncated is (limit < len(data))
    assert warnings == (["capture_limit_reached"] if limit < len(data) else [])


@pytest.mark.parametrize("stream", [False, True])
def test_openai_actual_decoder_path_uses_capture_without_changing_request(monkeypatch, stream):
    body = (
        _stream()
        if stream
        else json.dumps(
            {
                "reasoning_content": "private reasoning",
                "choices": [{"message": {"content": "完整结果"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 3},
            }
        ).encode()
    )
    requests = []

    def open_response(request, timeout):
        requests.append((request.data, timeout))
        return io.BytesIO(body)

    monkeypatch.setattr("code_forge.llm_invoke.urllib.request.urlopen", open_response)
    backend = BackendConfig(
        name="fixture",
        type="api",
        model="model",
        format="openai",
        base_url="http://fixture",
        api_key_env="UNUSED_CONTROLLED_FIXTURE",
        stream=stream,
    )
    expected = _invoke_openai("fixture prompt", backend, "fixture-not-a-secret", 5)
    capture, sink, warnings = _capture(chunk=65536)
    actual = _invoke_openai("fixture prompt", backend, "fixture-not-a-secret", 5, wire_capture=capture)
    assert actual == expected
    assert len(requests) == 2 and requests[0] == requests[1]
    assert sink.getvalue() == body
    assert not warnings and not capture.seal().interrupted


@pytest.mark.parametrize("strict,limit", [(False, 1000), (False, 0), (True, 0)])
def test_http_error_body_capture_preserves_http_classification(monkeypatch, strict, limit):
    body = b'{"error":{"message":"controlled HTTP failure"}}'
    errors = []

    def reject(request, timeout):
        error = urllib.error.HTTPError(
            "http://fixture", 429, "controlled", {"Retry-After": "1"}, io.BytesIO(body)
        )
        errors.append(error)
        raise error

    monkeypatch.setattr("code_forge.llm_invoke.urllib.request.urlopen", reject)
    backend = BackendConfig(
        name="fixture",
        type="api",
        model="model",
        format="openai",
        base_url="http://fixture",
        api_key_env="UNUSED_CONTROLLED_FIXTURE",
    )
    with pytest.raises(LLMInvokeError) as baseline:
        _invoke_openai("prompt", backend, "fixture-not-a-secret", 5)
    capture, sink, warnings = _capture(strict=strict, limit=limit)
    with pytest.raises(LLMInvokeError) as caught:
        _invoke_openai("prompt", backend, "fixture-not-a-secret", 5, wire_capture=capture)
    assert str(caught.value) == str(baseline.value)
    assert caught.value.exit_code == 429 and caught.value.retryable
    assert caught.value.retry_after == baseline.value.retry_after == 1
    assert caught.value.__cause__ is errors[-1]
    assert sink.getvalue() == body[:limit]
    assert capture.seal().eof_seen
    assert capture.seal().truncated is (limit < len(body))
    assert warnings == (["capture_limit_reached"] if limit < len(body) else [])


def test_content_length_short_body_is_not_reinterpreted_as_complete_json():
    class Socket:
        def makefile(self, mode):
            return io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 12\r\n\r\n{}")

    baseline = HTTPResponse(Socket())
    baseline.begin()
    with pytest.raises(IncompleteRead):
        baseline.read()
    response = HTTPResponse(Socket())
    response.begin()
    capture, sink, _ = _capture()
    with pytest.raises(IncompleteRead) as caught:
        _read_captured_body(response, time.monotonic() + 5, "fixture", capture)
    assert caught.value.expected == 10
    assert sink.getvalue() == b"{}"
    assert capture.seal().interrupted and not capture.seal().eof_seen


def test_known_http_error_survives_error_body_deadline(monkeypatch):
    error = urllib.error.HTTPError(
        "http://fixture", 429, "controlled", {"Retry-After": "1"}, io.BytesIO(b"body")
    )

    def reject(request, timeout):
        raise error

    monkeypatch.setattr("code_forge.llm_invoke.urllib.request.urlopen", reject)
    backend = BackendConfig(
        name="fixture",
        type="api",
        model="model",
        format="openai",
        base_url="http://fixture",
        api_key_env="UNUSED_CONTROLLED_FIXTURE",
    )
    capture, _, _ = _capture()
    with pytest.raises(LLMInvokeError) as caught:
        _invoke_openai("prompt", backend, "fixture-not-a-secret", 0, wire_capture=capture)
    assert caught.value.exit_code == 429 and caught.value.retry_after == 1
    assert caught.value.__cause__ is error
    assert capture.seal().interrupted and not capture.seal().eof_seen


def test_sse_done_drains_to_actual_transport_eof():
    data = _stream() + b": later keepalive\n\n"
    capture, sink, _ = _capture(chunk=1)
    result = _read_sse(io.BytesIO(data), wire_capture=capture)
    assert result["choices"][0]["message"]["content"] == "完整结果"
    state = capture.seal()
    assert state.eof_seen and state.complete
    assert sink.getvalue() == data


@pytest.mark.parametrize(
    "change",
    [
        {"sealed": False},
        {"eof_seen": False},
        {"truncated": True},
        {"interrupted": True},
        {"failed": True},
        {"write_uncertain": True},
        {"accepted_bytes": 0},
    ],
)
def test_acquisition_complete_requires_eof_and_every_byte_accepted(change):
    capture, sink, _ = _capture()
    assert _read_captured_body(io.BytesIO(b"{}"), time.monotonic() + 5, "fixture", capture) == b"{}"
    state = capture.seal()
    assert sink.getvalue() == b"{}" and state.complete
    assert not replace(state, **change).complete


@pytest.mark.parametrize("body", [b"abc", b"abc\r"])
@pytest.mark.parametrize("fallback", [False, True], ids=["native-read1", "native-read"])
def test_chunk_delimiter_partial_is_not_entity_data(fallback, body):
    class Socket:
        def makefile(self, mode):
            return io.BytesIO(
                b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
                + f"{len(body):x}\r\n".encode()
                + body
                + b"\r"
            )

    response = HTTPResponse(Socket())
    response.begin()
    if fallback:
        response.read1 = None
    capture, sink, _ = _capture()
    try:
        with pytest.raises(IncompleteRead) as caught:
            _read_captured_body(response, time.monotonic() + 5, "fixture", capture)
        assert sink.getvalue() == body
        assert caught.value.partial == (body if fallback else b"\r")
        assert capture.seal().interrupted and not capture.seal().eof_seen
    finally:
        response.close()


@pytest.mark.parametrize("partial", [b"\r", b"\r\n"])
@pytest.mark.parametrize("read1", [False, True])
def test_custom_reader_chunk_attributes_do_not_discard_partial(read1, partial):
    primary = IncompleteRead(partial, 1)

    class Reader:
        chunked = True
        chunk_left = 0

        def read(self, size):
            raise primary

    reader = Reader()
    if read1:
        reader.read1 = reader.read
    capture, sink, _ = _capture()
    with pytest.raises(IncompleteRead) as caught:
        _read_captured_body(reader, time.monotonic() + 5, "fixture", capture)
    assert caught.value is primary
    assert sink.getvalue() == partial
    assert capture.seal().interrupted


@pytest.mark.parametrize("override", ["read1", "_read1_chunked"])
def test_http_response_overridden_read1_keeps_entity_partial(override):
    primary = IncompleteRead(b"\r\n", 1)

    def fail(self, size=-1):
        raise primary

    Response = type("Response", (HTTPResponse,), {override: fail})

    class Socket:
        def makefile(self, mode):
            return io.BytesIO(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")

    response = Response(Socket())
    response.begin()
    response.chunk_left = 0
    capture, sink, _ = _capture()
    try:
        with pytest.raises(IncompleteRead) as caught:
            _read_captured_body(response, time.monotonic() + 5, "fixture", capture)
        assert caught.value is primary and sink.getvalue() == b"\r\n"
    finally:
        response.close()
