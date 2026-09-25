"""Exercise stream read bounds through real loopback HTTP connections."""

import contextlib
import http.server
import threading
import time
import urllib.request

import pytest

import code_forge.llm_invoke as invoke


@contextlib.contextmanager
def _stream_server(*, drip=False, complete=False):
    stop = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.do_GET()

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(
                b'data: {"choices":[{"delta":{"content":"prefix"}}]}\n\n'
            )
            self.wfile.flush()
            if complete:
                self.wfile.write(b'data: [DONE]\n\n')
                self.wfile.flush()
            elif drip:
                expires = time.monotonic() + 1.5
                while time.monotonic() < expires and not stop.wait(0.02):
                    try:
                        self.wfile.write(b":")
                        self.wfile.flush()
                    except OSError:
                        break
            else:
                stop.wait(3)

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 3
    worker = threading.Thread(target=server.handle_request)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        stop.set()
        worker.join(timeout=4)
        server.server_close()
        assert not worker.is_alive(), "loopback server did not stop"


def test_silent_stream_uses_idle_bound(monkeypatch):
    monkeypatch.setattr(invoke, "_IDLE_READ_TIMEOUT_S", 0.1)
    with _stream_server() as url:
        with urllib.request.urlopen(url, timeout=0.8) as response:  # noqa: S310 - local server URL
            started = time.monotonic()
            with pytest.raises(invoke.LLMInvokeError) as caught:
                invoke._read_sse(
                    response, deadline=started + 2, backend_name="loopback"
                )
            elapsed = time.monotonic() - started
    assert caught.value.is_timeout
    assert not caught.value.retryable
    assert "went silent" in str(caught.value)
    assert elapsed < 0.6


def test_partial_line_cannot_outlive_total_deadline(monkeypatch):
    monkeypatch.setattr(invoke, "_IDLE_READ_TIMEOUT_S", 0.5)
    with _stream_server(drip=True) as url:
        with urllib.request.urlopen(url, timeout=0.8) as response:  # noqa: S310 - local server URL
            started = time.monotonic()
            # The server keeps sending bytes without a newline, so a socket
            # idle timeout alone cannot enforce the total read deadline.
            with pytest.raises(invoke.LLMInvokeError) as caught:
                invoke._read_sse(
                    response, deadline=started + 0.15, backend_name="loopback"
                )
            elapsed = time.monotonic() - started
    assert caught.value.is_timeout
    assert not caught.value.retryable
    assert "total read deadline" in str(caught.value)
    assert elapsed < 0.6


def test_custom_iterator_cannot_bypass_total_deadline():
    release = threading.Event()
    exited = threading.Event()
    closed = []

    class Stream:
        def __iter__(self):
            return self

        def __next__(self):
            try:
                release.wait(1.5)
                raise StopIteration
            finally:
                exited.set()

        def close(self):
            closed.append(True)

    started = time.monotonic()
    try:
        with pytest.raises(invoke.LLMInvokeError, match="total read deadline"):
            invoke._read_sse(Stream(), deadline=started + 0.1)
        assert time.monotonic() - started < 0.6
        assert not closed
    finally:
        release.set()
        assert exited.wait(2), "custom iterator did not exit"


def test_completed_http_stream_preserves_content():
    with _stream_server(complete=True) as url:
        with urllib.request.urlopen(url, timeout=0.8) as response:  # noqa: S310 - local server URL
            result = invoke._read_sse(
                response, deadline=time.monotonic() + 2, backend_name="loopback"
            )
    assert result["choices"][0]["message"]["content"] == "prefix"


def test_api_silent_stream_does_not_enter_retry(monkeypatch):
    monkeypatch.setattr(invoke, "_IDLE_READ_TIMEOUT_S", 0.1)
    monkeypatch.setenv("FORGE_TEST_LOOPBACK_KEY", "test-only")

    def reject_retry(_delay):
        pytest.fail("silent stream entered retry backoff")

    monkeypatch.setattr(invoke.time, "sleep", reject_retry)
    with _stream_server() as url:
        backend = invoke.BackendConfig(
            name="loopback", type="api", model="test", format="openai",
            base_url=url, api_key_env="FORGE_TEST_LOOPBACK_KEY", stream=True,
        )
        started = time.monotonic()
        with pytest.raises(invoke.LLMInvokeError) as caught:
            invoke.llm_invoke(
                "test", backend, timeout_s=1, max_attempts=5, retry_timeout=True,
            )
        elapsed = time.monotonic() - started
    assert caught.value.is_timeout
    assert not caught.value.retryable
    assert "went silent" in str(caught.value)
    assert elapsed < 0.6
