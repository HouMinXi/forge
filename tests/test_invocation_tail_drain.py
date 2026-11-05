"""DONE is a parsing boundary, not a transport EOF or an unbounded wait."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import threading
import time
import urllib.request

import pytest

from code_forge.invocation_stream import WireCapture
from code_forge.llm_invoke import LLMInvokeError, _read_sse
from code_forge.invocation_tail_drain import TAIL_BYTES

BODY = b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n'


def capture(*, strict=False, chunk=65536):
    raw, warnings = io.BytesIO(), []
    wire = WireCapture(
        write=raw.write, warn=warnings.append, limit_bytes=1000000, chunk_bytes=chunk, strict=strict
    )
    return wire, raw, warnings


def test_prefetched_tail_is_counted_once_and_never_parsed():
    tail = b'\ndata: not json\ndata: {"usage":{"completion_tokens":999}}\n'
    wire, raw, warnings = capture()
    result = _read_sse(io.BytesIO(BODY + tail), wire_capture=wire)
    assert result["choices"][0]["message"]["content"] == "ok"
    assert result.get("usage", {}) == {}
    assert raw.getvalue() == BODY + tail
    assert wire.seal().complete
    assert not warnings


@pytest.mark.parametrize("prefetch", [1, 63000, TAIL_BYTES, TAIL_BYTES + 10])
def test_tail_budget_counts_prefetch_and_bounds_next_read(prefetch):
    calls = []

    class Reader(io.BytesIO):
        def read1(self, n):
            calls.append(n)
            return super().read1(n)

    chunk = len(BODY) + prefetch
    wire, raw, warnings = capture(chunk=chunk)
    reader = Reader(BODY + b"x" * (TAIL_BYTES * 3))
    _read_sse(reader, wire_capture=wire)
    expected = len(BODY) + max(prefetch, TAIL_BYTES)
    assert len(raw.getvalue()) == expected
    assert wire.seal().observed_bytes == wire.seal().accepted_bytes == expected
    assert wire.seal().truncated and not wire.seal().eof_seen
    assert warnings == ["capture_tail_limit"]
    if prefetch >= TAIL_BYTES:
        assert calls == [chunk]
    else:
        assert calls[1] <= TAIL_BYTES - prefetch


@contextmanager
def server(mode):
    stop = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                self.wfile.write(BODY)
                self.wfile.flush()
                if mode == "slow_eof":
                    stop.wait(0.04)
                    self.wfile.write(b"\n: tail\n")
                    self.wfile.flush()
                    self.close_connection = True
                elif mode == "keepalive":
                    while not stop.wait(0.01):
                        self.wfile.write(b": keepalive\n")
                        self.wfile.flush()
                else:
                    stop.wait(3)
            except (BrokenPipeError, ConnectionResetError):
                pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d/" % httpd.server_port
    finally:
        stop.set()
        httpd.shutdown()
        httpd.server_close()
        thread.join(2)


@pytest.mark.parametrize("mode", ["held", "keepalive", "slow_eof"])
@pytest.mark.parametrize("strict", [False, True])
def test_real_localhost_tail_drain(mode, strict):
    wire, raw, warnings = capture(strict=strict)
    with server(mode) as url, urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310 - local HTTP fixture
        start = time.monotonic()
        if strict and mode != "slow_eof":
            with pytest.raises(LLMInvokeError) as caught:
                _read_sse(response, start + 2, wire_capture=wire)
            assert caught.value.kind == "audit_persistence_error"
            assert not caught.value.retryable
        else:
            result = _read_sse(response, start + 2, wire_capture=wire)
            assert result["choices"][0]["message"]["content"] == "ok"
        assert time.monotonic() - start < 0.8
    state = wire.seal()
    assert state.complete is (mode == "slow_eof")
    assert state.eof_seen is (mode == "slow_eof")
    assert raw.getvalue().startswith(BODY)
    assert warnings == ([] if mode == "slow_eof" else ["capture_tail_deadline"])


@pytest.mark.parametrize("strict", [False, True])
def test_original_deadline_bounds_tail_without_reclassifying_provider(strict):
    wire, _, warnings = capture(strict=strict)
    with server("held") as url, urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310 - local HTTP fixture
        start = time.monotonic()
        if strict:
            with pytest.raises(LLMInvokeError) as caught:
                _read_sse(response, start + 0.06, wire_capture=wire)
            assert caught.value.kind == "audit_persistence_error"
        else:
            assert _read_sse(response, start + 0.06, wire_capture=wire)["choices"]
        assert time.monotonic() - start < 0.4
    assert warnings in [["capture_tail_deadline"], ["capture_tail_interrupted"]]
    assert not wire.seal().complete


def test_late_reader_cannot_upgrade_snapshot():
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    class Reader:
        def __init__(self):
            self.first = True

        def read(self, n):
            if self.first:
                self.first = False
                return BODY
            entered.set()
            release.wait(3)
            finished.set()
            return b""

    wire, raw, warnings = capture()
    try:
        assert _read_sse(Reader(), wire_capture=wire)["choices"]
        assert entered.is_set()
        before = wire.seal()
        assert before.interrupted and not before.eof_seen
    finally:
        release.set()
        assert finished.wait(2)
    time.sleep(0.01)
    assert wire.seal() == before
    assert raw.getvalue() == BODY
    assert warnings == ["capture_tail_deadline"]


@pytest.mark.parametrize("fatal", [MemoryError, KeyboardInterrupt, SystemExit])
def test_primary_exception_survives_failing_seal(fatal):
    primary = fatal("primary")

    class Reader:
        def read(self, n):
            raise primary

    class BrokenSeal(WireCapture):
        def seal(self, **kwargs):
            super().seal(**kwargs)
            raise RuntimeError("secondary")

    wire = BrokenSeal(write=len, warn=lambda _: None, limit_bytes=1000)
    with pytest.raises(fatal) as caught:
        _read_sse(Reader(), wire_capture=wire)
    assert caught.value is primary


def test_predone_timeout_survives_seal_failure_and_shuts_down_socket():
    released = threading.Event()
    shutdown = threading.Event()

    class Socket:
        def settimeout(self, value):
            pass

        def shutdown(self, how):
            shutdown.set()
            released.set()

    class Reader:
        def __init__(self):
            from types import SimpleNamespace

            self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=Socket()))

        def read(self, n):
            released.wait(2)
            return b""

    class BrokenSeal(WireCapture):
        def seal(self, **kwargs):
            super().seal(**kwargs)
            raise RuntimeError("secondary cleanup")

    wire = BrokenSeal(write=len, warn=lambda _: None, limit_bytes=1000)
    with pytest.raises(LLMInvokeError) as caught:
        _read_sse(Reader(), time.monotonic() + 0.02, wire_capture=wire)
    assert caught.value.is_timeout
    assert not caught.value.retryable
    assert shutdown.is_set()


def test_transport_primary_and_cause_survive_seal_failure():
    cause = OSError("connection cause")
    primary = OSError("transport primary")
    primary.__cause__ = cause

    class Reader:
        def read(self, n):
            raise primary

    class BrokenSeal(WireCapture):
        def seal(self, **kwargs):
            super().seal(**kwargs)
            raise MemoryError("secondary cleanup")

    wire = BrokenSeal(write=len, warn=lambda _: None, limit_bytes=1000)
    with pytest.raises(OSError) as caught:
        _read_sse(Reader(), wire_capture=wire)
    assert caught.value is primary
    assert caught.value.__cause__ is cause


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("code", ["rate_limit_exceeded", "invalid_api_key", "unknown_fixture_error"])
def test_api_error_envelope_keeps_provider_classification_with_tail_limit(monkeypatch, strict, code):
    import json

    from code_forge.backend import BackendConfig
    from code_forge.llm_invoke import _invoke_openai

    body = b"data: " + json.dumps({"error": {"code": code, "message": "fixture"}}).encode()
    body += b"\n\ndata: [DONE]\n" + b"x" * (TAIL_BYTES + 1)
    monkeypatch.setattr(
        "code_forge.llm_invoke.urllib.request.urlopen", lambda *a, **kw: io.BytesIO(body)
    )
    backend = BackendConfig(
        name="openai",
        type="api",
        model="fixture",
        format="openai",
        stream=True,
        base_url="http://fixture",
        api_key_env="UNUSED_CONTROLLED_FIXTURE",
    )
    with pytest.raises(LLMInvokeError) as baseline:
        _invoke_openai("prompt", backend, "fixture-not-a-secret", 5)
    wire, _, warnings = capture(strict=strict)
    with pytest.raises(LLMInvokeError) as caught:
        _invoke_openai("prompt", backend, "fixture-not-a-secret", 5, wire_capture=wire)
    assert str(caught.value) == str(baseline.value)
    assert caught.value.kind == baseline.value.kind
    assert caught.value.retryable == baseline.value.retryable
    assert caught.value.exit_code == baseline.value.exit_code
    assert warnings == ["capture_tail_limit"]
    assert wire.seal().truncated and not wire.seal().complete


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("secondary", [RuntimeError, MemoryError, KeyboardInterrupt])
@pytest.mark.parametrize("postdone", [False, True])
def test_shutdown_failure_respects_existing_primary(strict, secondary, postdone):
    from types import SimpleNamespace

    release = threading.Event()
    cleanup = secondary("secondary shutdown")

    class Socket:
        def settimeout(self, value):
            pass

        def shutdown(self, how):
            release.set()
            raise cleanup

    class Reader:
        fp = SimpleNamespace(raw=SimpleNamespace(_sock=Socket()))
        first = postdone

        def read(self, n):
            if self.first:
                self.first = False
                return BODY
            release.wait(2)
            return b""

    wire, _, _ = capture(strict=strict)
    try:
        with pytest.raises(LLMInvokeError if not postdone or strict else secondary) as caught:
            _read_sse(Reader(), time.monotonic() + 0.03, wire_capture=wire)
        if not postdone:
            assert caught.value.is_timeout and not caught.value.retryable
        elif strict:
            assert caught.value.kind == "audit_persistence_error"
            assert not caught.value.retryable
        else:
            assert caught.value is cleanup
    finally:
        release.set()


@pytest.mark.parametrize("strict", [False, True])
def test_error_envelope_survives_tail_deadline_and_strict_seal(monkeypatch, strict):
    from code_forge.backend import BackendConfig
    from code_forge.invocation_stream import CaptureUnavailable
    from code_forge.llm_invoke import _invoke_openai

    release = threading.Event()
    body = b'data: {"error":{"code":"rate_limit_exceeded","message":"fixture"}}\n\ndata: [DONE]\n'

    class Reader:
        first = True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, n):
            if self.first:
                self.first = False
                return body
            release.wait(2)
            return b""

    class StrictSeal(WireCapture):
        def seal(self, **kwargs):
            state = super().seal(**kwargs)
            if strict:
                raise CaptureUnavailable("secondary seal failure")
            return state

    backend = BackendConfig(
        name="openai",
        type="api",
        model="fixture",
        format="openai",
        stream=True,
        base_url="http://fixture",
        api_key_env="UNUSED_CONTROLLED_FIXTURE",
    )
    monkeypatch.setattr(
        "code_forge.llm_invoke.urllib.request.urlopen", lambda *a, **kw: io.BytesIO(body)
    )
    with pytest.raises(LLMInvokeError) as baseline:
        _invoke_openai("prompt", backend, "fixture-not-a-secret", 5)
    monkeypatch.setattr("code_forge.llm_invoke.urllib.request.urlopen", lambda *a, **kw: Reader())
    warnings = []
    wire = StrictSeal(write=len, warn=warnings.append, limit_bytes=1000000, strict=strict)
    try:
        with pytest.raises(LLMInvokeError) as caught:
            _invoke_openai("prompt", backend, "fixture-not-a-secret", 0.03, wire_capture=wire)
        assert str(caught.value) == str(baseline.value)
        assert caught.value.retryable == baseline.value.retryable
        assert caught.value.kind == baseline.value.kind
        assert warnings == ["capture_tail_deadline"]
        assert WireCapture.seal(wire).interrupted
    finally:
        release.set()
