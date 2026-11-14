"""Real HTTP bounds for the correction reader, against a local server.

Every other correction case stubs the transport, so this is the only place the
1 MiB wire cap and the no-redirect rule meet an actual socket. The three API
formats share `_open_url` and `_read_with_deadline`, so exercising those two
covers all of them.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from code_forge import llm_invoke as invoke
from code_forge.backend import BackendConfig

_CHUNK = b"x" * 65536


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):  # noqa: N802 - stdlib name
        length = int(self.headers.get("Content-Length", 0) or 0)
        self.rfile.read(length)
        if "/redirect/" in self.path:
            self.send_response(302)
            self.send_header("Location", "/elsewhere/chat/completions")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if "/huge/" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(_CHUNK) * 20))  # 1.25 MiB
            self.end_headers()
            for _ in range(20):
                self.wfile.write(_CHUNK)
            return
        body = json.dumps(
            {
                "id": "1",
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: D401 - silence the server
        return


@pytest.fixture()
def base_url():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d" % server.server_port
    finally:
        server.shutdown()
        server.server_close()


def _backend(base_url: str, case: str) -> BackendConfig:
    return BackendConfig(
        name="http-bounds",
        type="api",
        model="fixture",
        format="openai",
        base_url=base_url + "/" + case,
        api_key_env="HTTP_BOUNDS_KEY",
        timeout_s=20,
    )


def test_huge_body_is_refused_while_reading(base_url):
    """The cap bites during the read, not after the whole body is buffered."""
    policy = invoke._CorrectionReadPolicy()
    token = invoke._CORRECTION_READ_POLICY.set(policy)
    try:
        with pytest.raises(invoke.LLMInvokeError) as caught:
            invoke._invoke_openai_impl("prompt", _backend(base_url, "huge"), "fixture", 20)
    finally:
        invoke._CORRECTION_READ_POLICY.reset(token)

    assert "exceeded" in str(caught.value)
    assert caught.value.retryable is False
    # Stopped just past the cap instead of draining 1.25 MiB.
    assert policy.consumed <= invoke._EXCERPT_WIRE_LIMIT + 65536


def test_redirect_is_not_followed_during_correction(base_url):
    """A 3xx must surface as a failure, never as a second request."""
    policy = invoke._CorrectionReadPolicy()
    token = invoke._CORRECTION_READ_POLICY.set(policy)
    try:
        with pytest.raises(Exception) as caught:
            invoke._invoke_openai_impl("prompt", _backend(base_url, "redirect"), "fixture", 20)
    finally:
        invoke._CORRECTION_READ_POLICY.reset(token)

    # urllib turns the unfollowed 302 into an HTTPError, which the format layer
    # re-raises as an LLMInvokeError carrying the status code.
    status = getattr(caught.value, "exit_code", None) or getattr(caught.value, "code", None)
    assert status == 302


def test_normal_call_still_reads_without_a_policy(base_url):
    """The bound is scoped to corrections; ordinary reads keep working."""
    content, usage = invoke._invoke_openai_impl("prompt", _backend(base_url, "ok"), "fixture", 20)
    assert content == "{}"  # the message content, not the whole envelope
    assert usage["prompt_tokens"] == 3


def test_policy_reader_is_not_installed_when_unset(base_url):
    """Sanity: outside a correction the context variable is empty."""
    assert invoke._CORRECTION_READ_POLICY.get() is None
    content, _usage = invoke._invoke_openai_impl("prompt", _backend(base_url, "ok"), "fixture", 20)
    assert content


class _LineStream:
    """Minimal file-like iterator: only the line protocol _read_sse consumes."""

    def __init__(self, lines):
        self._lines = lines

    def __iter__(self):
        return iter(self._lines)


def test_streamed_body_is_refused_while_reading():
    """A stream never passes _read_with_deadline, so it needs its own bound."""
    line = ('data: {"choices":[{"delta":{"content":"' + "x" * 4096 + '"}}]}\n\n').encode()
    policy = invoke._CorrectionReadPolicy()
    token = invoke._CORRECTION_READ_POLICY.set(policy)
    try:
        with pytest.raises(invoke.LLMInvokeError) as caught:
            invoke._read_sse(_LineStream([line] * 400), deadline=None, backend_name="fixture")
    finally:
        invoke._CORRECTION_READ_POLICY.reset(token)

    assert "exceeded" in str(caught.value)
    assert caught.value.retryable is False
    assert policy.consumed > invoke._EXCERPT_WIRE_LIMIT


class _StreamingResponse:
    """Context-manager body that yields SSE lines; stands in for urlopen."""

    def __init__(self, lines):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._lines)

    def read(self, size=-1):  # pragma: no cover - non-stream callers only
        return b""

    def close(self):
        return None


def test_streamed_body_is_refused_through_the_production_path(monkeypatch):
    """The bound must bite on `_invoke_openai_impl`, not only on `_read_sse`.

    `_read_sse` runs on the caller's thread (the only thread in this module is
    inside `_read_with_deadline`), so the context variable set around the
    correction is visible to it.
    """
    line = ('data: {"choices":[{"delta":{"content":"' + "x" * 4096 + '"}}]}\n\n').encode()
    monkeypatch.setattr(invoke, "_open_url", lambda req, timeout_s: _StreamingResponse([line] * 400))
    backend = BackendConfig(
        name="http-bounds",
        type="api",
        model="fixture",
        format="openai",
        base_url="https://example.invalid",
        api_key_env="HTTP_BOUNDS_KEY",
        timeout_s=20,
        stream=True,
    )
    policy = invoke._CorrectionReadPolicy()
    token = invoke._CORRECTION_READ_POLICY.set(policy)
    try:
        with pytest.raises(invoke.LLMInvokeError) as caught:
            invoke._invoke_openai_impl("prompt", backend, "fixture", 20)
    finally:
        invoke._CORRECTION_READ_POLICY.reset(token)

    assert "exceeded" in str(caught.value)
    assert policy.consumed > invoke._EXCERPT_WIRE_LIMIT


def test_unterminated_sse_line_is_refused_before_it_is_buffered():
    """A single line with no newline must not be sized in memory first.

    Iterating the response would read to EOF before the byte budget is
    consulted; asking for one byte past the remaining budget caps the read.
    """
    limit = 4096
    served = []

    class Resp:
        def readline(self, n=-1):
            served.append(n)
            return b"x" * (n if n > 0 else 10)

    policy = invoke._CorrectionReadPolicy(limit)
    with pytest.raises(invoke.LLMInvokeError) as caught:
        list(invoke._limited_lines(Resp(), policy))

    assert caught.value.kind == "sse_body"
    assert served[0] == limit + 1
    assert policy.consumed == limit + 1


def test_capture_wrapper_refuses_an_unterminated_line_at_the_budget():
    from code_forge.invocation_tail_drain import _Lines, _TailLimit

    class Capture:
        def chunks(self, source):
            return iter([b"y" * 512] * 8)

    class Resp:
        def read(self, n=-1):
            return b""

    lines = _Lines(Resp(), Capture(), None, line_limit=2048)
    with pytest.raises(_TailLimit):
        next(lines)


def test_timed_out_reader_closes_the_response_when_asked():
    closed = []

    class Resp:
        # A real urllib response carries `fp`; that attribute is what marks it
        # as ours to close (a bare iterator is not).
        fp = object()

        def read(self, n=-1):
            time.sleep(5)
            return b""

        def close(self):
            closed.append(True)

    response = Resp()
    with pytest.raises(invoke.LLMInvokeError) as caught:
        invoke._read_with_deadline(
            response,
            time.monotonic() + 0.2,
            "closed-test",
            read=lambda: response.read(1),
            close_on_timeout=True,
        )

    assert caught.value.is_timeout is True
    assert closed == [True]
