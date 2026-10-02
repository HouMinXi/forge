"""Pin how _invoke_vertex classifies a failed connection.

An HTTP status in the retryable set is retried and carries that status as
its exit code. A status outside the set is not retried. A URL error never
reached an HTTP status: it is a connection failure, retried, and a timeout
is told apart from any other reason. The response body is quoted only up
to the first 200 characters.
"""

import http.client
import io
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import code_forge.llm_invoke as invoke
from code_forge.backend import BackendConfig
from code_forge.llm_invoke import LLMInvokeError, _invoke_vertex


google_auth = pytest.importorskip("google.auth")
pytest.importorskip("requests")
pytest.importorskip("google.oauth2.service_account")
pytest.importorskip("google.auth.transport.requests")


class _Creds:
    token = "tok"

    def refresh(self, request):
        return None


def _raise(monkeypatch, error):
    monkeypatch.setattr(google_auth, "default", lambda scopes: (_Creds(), None))

    def fake_open(req, timeout):
        raise error

    monkeypatch.setattr(invoke.urllib.request, "urlopen", fake_open)
    backend = BackendConfig(name="b", type="api", format="vertex", model="m", project_id="p")
    return backend


def _http(code, body):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body))


def test_retryable_http_status_is_retried_with_its_code(monkeypatch):
    backend = _raise(monkeypatch, _http(503, b"overloaded"))

    try:
        _invoke_vertex("prompt", backend, 30)
    except LLMInvokeError as exc:
        caught = exc
    else:
        raise AssertionError("no error raised")

    assert caught.retryable is True
    assert caught.exit_code == 503
    assert "503" in str(caught)
    assert "overloaded" in str(caught)


def test_other_http_status_is_not_retried(monkeypatch):
    backend = _raise(monkeypatch, _http(400, b"bad request"))

    try:
        _invoke_vertex("prompt", backend, 30)
    except LLMInvokeError as exc:
        caught = exc
    else:
        raise AssertionError("no error raised")

    assert caught.retryable is False
    assert caught.exit_code == 400


def test_long_http_body_is_cut_at_200_characters(monkeypatch):
    backend = _raise(monkeypatch, _http(500, b"y" * 500))

    try:
        _invoke_vertex("prompt", backend, 30)
    except LLMInvokeError as exc:
        caught = exc
    else:
        raise AssertionError("no error raised")

    assert str(caught).count("y") == 200


def test_url_error_is_a_retryable_connection_failure(monkeypatch):
    backend = _raise(monkeypatch, urllib.error.URLError("name not resolved"))

    try:
        _invoke_vertex("prompt", backend, 30)
    except LLMInvokeError as exc:
        caught = exc
    else:
        raise AssertionError("no error raised")

    assert caught.retryable is True
    assert caught.kind == "conn"
    assert caught.is_timeout is False
    assert "name not resolved" in str(caught)


def test_url_timeout_is_flagged(monkeypatch):
    backend = _raise(monkeypatch, urllib.error.URLError(TimeoutError("slow")))

    try:
        _invoke_vertex("prompt", backend, 30)
    except LLMInvokeError as exc:
        caught = exc
    else:
        raise AssertionError("no error raised")

    assert caught.is_timeout is True
    assert caught.kind == "conn"


def test_error_body_is_decoded_and_cut_at_200(monkeypatch):
    backend = _raise(monkeypatch, _http(500, b"\xff" + b"z" * 400))

    try:
        _invoke_vertex("prompt", backend, 30)
    except LLMInvokeError as exc:
        caught = exc
    else:
        raise AssertionError("no error raised")

    quoted = str(caught).split("vertex backend: ", 1)[1]
    assert quoted[0] == "\ufffd"
    assert quoted[1:] == "z" * 199
    assert len(quoted) == 200


@pytest.mark.parametrize(
    "error",
    [ConnectionResetError("reset by peer"), http.client.IncompleteRead(b"partial", 13)],
    ids=["socket-reset", "incomplete-http-body"],
)
@pytest.mark.parametrize("phase", ["open", "read"])
def test_transport_failure_is_a_retryable_connection_error(monkeypatch, error, phase):
    backend = _raise(monkeypatch, error)

    if phase == "read":

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                raise error

        monkeypatch.setattr(invoke.urllib.request, "urlopen", lambda req, timeout: Response())

    with pytest.raises(LLMInvokeError) as caught:
        _invoke_vertex("prompt", backend, 30)

    assert caught.value.kind == "conn"
    assert caught.value.retryable is True
    assert caught.value.is_timeout is False
    assert str(caught.value) == "connection error from b backend: %s" % error
    assert caught.value.__cause__ is error


def test_real_chunked_disconnect_is_a_retryable_connection_error(monkeypatch):
    requests_seen = []

    class BrokenChunkHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            requests_seen.append((self.path, body))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b'20\r\n{"content":')
            self.wfile.flush()
            self.close_connection = True

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), BrokenChunkHandler)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    monkeypatch.setattr(google_auth, "default", lambda scopes: (_Creds(), None))
    monkeypatch.setattr(
        invoke,
        "_build_vertex_url",
        lambda project, region, model: "http://127.0.0.1:%d/rawPredict" % server.server_port,
    )
    # Bypass any host proxy for the loopback request without replacing the HTTP reader.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    backend = BackendConfig(name="b", type="api", format="vertex", model="m", project_id="p")
    worker.start()
    try:
        with pytest.raises(LLMInvokeError) as caught:
            _invoke_vertex("prompt", backend, 5)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert len(requests_seen) == 1
    assert requests_seen[0][0] == "/rawPredict"
    assert b'"content": "prompt"' in requests_seen[0][1]
    assert isinstance(caught.value.__cause__, http.client.IncompleteRead)
    assert caught.value.kind == "conn"
    assert caught.value.retryable is True
    assert caught.value.is_timeout is False
    assert str(caught.value).startswith("connection error from b backend: IncompleteRead(")
