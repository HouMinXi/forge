"""T23 real loopback urllib status/header limits; synthetic recorder only.

Socket inactivity timeout is not a universal whole-exchange wall-clock bound.
No native persistence, external provider, DNS or power-loss qualification.
"""

import dataclasses
import http.client
import hashlib
import socket
import threading
import time

import pytest

from code_forge import llm_invoke as invoke
from code_forge.invocation_host import _HostAttempt
from tests.test_invocation_host import MockRecorder
from tests.test_invocation_r03_host_dispatch import backend as _backend
from tests.test_invocation_send_audit import reply

pytest_plugins = ["tests.test_invocation_host"]
backend = _backend


@pytest.mark.parametrize(
    "mode", ["healthy", "stall-status", "stall-headers", "drip-status", "drip-headers"]
)
def test_real_loopback_status_header_limit_and_cleanup(setup, monkeypatch, backend, mode):
    body = reply().raw
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(5)
    address = listener.getsockname()
    stop = threading.Event()
    eof = threading.Event()
    errors = []
    requests = []
    sent = []
    server_deadline = time.monotonic() + 8

    def serve():
        try:
            with listener.accept()[0] as peer:

                def recv(size):
                    remaining = server_deadline - time.monotonic()
                    assert remaining > 0 and not stop.is_set()
                    peer.settimeout(remaining)
                    return peer.recv(size)

                peer.settimeout(5)
                request = bytearray()
                while b"\r\n\r\n" not in request:
                    chunk = recv(4096)
                    assert chunk
                    request.extend(chunk)
                    assert len(request) <= 65536
                headers, payload = request.split(b"\r\n\r\n", 1)
                length = next(
                    int(line.split(b":", 1)[1])
                    for line in headers.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
                assert 0 <= length <= 65536
                while len(payload) < length:
                    chunk = recv(4096)
                    assert chunk
                    payload.extend(chunk)
                requests.append(bytes(headers.split(b"\r\n")[0]))
                status = b"HTTP/1.1 200 OK\r\n"
                tail = (
                    b"Content-Type: application/json\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\nConnection: close\r\n\r\n"
                )
                if mode.endswith("headers"):
                    peer.sendall(status)
                if mode.startswith("stall"):
                    # No response bytes until client actually closes after timeout.
                    assert recv(1) == b""
                    eof.set()
                    return
                if mode.startswith("drip"):
                    # Each read progresses, but the finite header exchange exceeds 1s.
                    prefix = b"HTTP/1.1 200 " if mode.endswith("status") else b"X-Synthetic: "
                    peer.sendall(prefix)
                    for _ in range(12):
                        assert not stop.wait(0.15)
                        peer.sendall(b"x")
                        sent.append(time.monotonic())
                    peer.sendall(b"\r\n" + tail + body)
                else:
                    peer.sendall(status + tail + body)
                assert recv(1) == b""
                eof.set()
        except BaseException as error:  # noqa: BLE001 - relay server failure to test thread
            errors.append(error)

    capture_calls, finish_calls = [], []
    original_capture = _HostAttempt.new_response
    original_finish = _HostAttempt.finish

    def capture(attempt, **kwargs):
        capture_calls.append(kwargs["status"])
        return original_capture(attempt, **kwargs)

    def finish(attempt, **kwargs):
        finish_calls.append((kwargs["outcome"], kwargs["error_class"]))
        return original_finish(attempt, **kwargs)

    monkeypatch.setattr(_HostAttempt, "new_response", capture)
    monkeypatch.setattr(_HostAttempt, "finish", finish)
    sockets, responses, closed = [], [], []
    original_connect = http.client.HTTPConnection.connect
    original_init = http.client.HTTPResponse.__init__
    original_close = http.client.HTTPResponse.close

    def connect(connection):
        assert (connection.host, connection.port) == address, "loopback-only-oracle"
        original_connect(connection)
        sockets.append(connection.sock)

    def init(response, *args, **kwargs):
        original_init(response, *args, **kwargs)
        responses.append(response)

    def close(response):
        result = original_close(response)
        closed.append(response)
        return result

    original_socket_connect = socket.socket.connect
    original_getaddrinfo = socket.getaddrinfo

    def socket_connect(sock, destination):
        assert destination == address, "loopback-only-oracle"
        return original_socket_connect(sock, destination)

    def getaddrinfo(hostname, port, *args, **kwargs):
        assert (hostname, port) == address, "loopback-only-oracle"
        return original_getaddrinfo(hostname, port, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", socket_connect)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", connect)
    monkeypatch.setattr(http.client.HTTPResponse, "__init__", init)
    monkeypatch.setattr(http.client.HTTPResponse, "close", close)
    # Ignore ambient proxy configuration for this explicitly local fixture only.
    for key in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(key, raising=False)
    thread = threading.Thread(target=serve)
    host = None
    try:
        thread.start()
        try:
            host, notices = setup[0](control_timeout_s=2, flush_timeout_s=2)
            configured = dataclasses.replace(backend, base_url=f"http://127.0.0.1:{address[1]}/v1")
            start = time.monotonic()
            if mode == "healthy":
                result = invoke.llm_invoke(
                    "synthetic", configured, timeout_s=1, max_attempts=1, host_audit=host.scope()
                )
                assert result.content["findings"] == []
                assert result.usage.input_tokens == result.usage.output_tokens == 1
                assert result.usage.known_fields == frozenset({"input_tokens", "output_tokens"})
            else:
                with pytest.raises(invoke.LLMInvokeError) as caught:
                    invoke.llm_invoke(
                        "synthetic", configured, timeout_s=1, max_attempts=1, host_audit=host.scope()
                    )
                assert caught.value.is_timeout is True
                assert caught.value.usage is None and caught.value.observed_usage is None
                assert caught.value.retryable is False  # default retry_timeout=False
            elapsed = time.monotonic() - start
            assert eof.wait(3), "peer-eof-oracle"
        finally:
            stop.set()
            listener.close()
            thread.join(9)
            assert not thread.is_alive()
        assert not thread.is_alive() and not errors
        assert requests == [b"POST /v1/chat/completions HTTP/1.1"]
        assert len(sockets) == len(responses) == 1
        assert sockets[0].fileno() == -1 and responses[0].fp is None
        assert responses[0] in closed
        assert elapsed < 8
        if mode.startswith("drip"):
            assert len(sent) == 12 and elapsed > 1.5
        assert host._started == 1
        records = list(host._owner._leases.values())
        assert len(records) == 1
        record = records[0]
        summary = host.safe_summary()
        expected_finish = [("completed", None)] if mode == "healthy" else [("failed", "TimeoutError")]
        assert finish_calls == ([] if mode.startswith("drip") else expected_finish), finish_calls
        assert capture_calls == ([] if mode.startswith("stall") else [200])
        assert len(record.recorder.finished) == (0 if mode.startswith("drip") else 1)
        for finished in record.recorder.finished:
            assert finished["attempt_id"] == record.value.attempt_id
            assert (finished["outcome"], finished["error_class"]) == expected_finish[0]
        if mode == "healthy":
            assert record.settled and len(record.refs) == 1
            assert record.captures == {"wire": (len(body), True)}
            assert record.refs[0].sha256 == hashlib.sha256(body).hexdigest()
            assert [bytes(value) for value in record.recorder.stages.values()] == [body]
            assert summary.settled_sends == summary.adopted_responses == 1
            assert not host.failed and notices == []
        elif mode.startswith("stall"):
            assert record.settled and record.refs == [] and record.captures == {}
            assert summary.settled_sends == 1 and summary.adopted_responses == 0
            assert not host.failed and notices == []
        else:
            assert not record.settled and record.refs == [] and record.captures == {}
            assert summary.state == "incomplete" and summary.publication_uncertain
            assert summary.settled_sends == summary.adopted_responses == 0
            assert notices == ["invocation_audit_unavailable"]
        assert sum(name == "start" for name, _ in MockRecorder.calls) == 1
        assert_cleanup(sockets, responses, closed, eof)
        # Missing observer evidence cannot pass even when the underlying close worked.
        with pytest.raises(AssertionError, match="close-observer-oracle"):
            assert_cleanup(sockets, responses, [], eof)
        with pytest.raises(AssertionError, match="transport-cleanup-oracle"):
            assert_cleanup(sockets, responses, closed, threading.Event())
        with socket.socket() as intentionally_open:
            with pytest.raises(AssertionError, match="transport-cleanup-oracle"):
                assert_cleanup([intentionally_open], responses, closed, eof)
    finally:
        if host is not None:
            host.close(primary=RuntimeError("fixture cleanup"))
        setup[1].close()
        if setup[1]._worker is not None:
            setup[1]._worker.join(3)
            assert not setup[1]._worker.is_alive()


def assert_cleanup(sockets, responses, closed, eof):
    assert len(sockets) == len(responses) == 1 and eof.is_set(), "transport-cleanup-oracle"
    assert sockets[0].fileno() == -1 and responses[0].fp is None, "transport-cleanup-oracle"
    assert responses[0] in closed, "close-observer-oracle"
