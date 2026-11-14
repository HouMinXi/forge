"""T24 real loopback chunked decoding; synthetic recorder, no native claim."""

import dataclasses
import http.client
import hashlib
import socket
import threading
import time

import pytest

from code_forge import llm_invoke as invoke
from tests.test_invocation_host import MockRecorder
from tests.test_invocation_r03_host_dispatch import backend as _backend
from tests.test_invocation_send_audit import reply
from tests.test_invocation_t23_header_boundaries import assert_cleanup

pytest_plugins = ["tests.test_invocation_host"]
backend = _backend


def chunk(data):
    return f"{len(data):x}\r\n".encode() + data + b"\r\n"


@pytest.mark.parametrize(
    "mode", ["complete", "mid-chunk", "first-chunk", "missing-zero", "trim-chunk-crlf"]
)
def test_real_chunked_capture_and_cleanup(setup, monkeypatch, backend, mode):
    body = reply().raw
    split = len(body) // 2
    if mode == "complete":
        wire = chunk(body[:split]) + chunk(body[split:]) + b"0\r\n\r\n"
    elif mode == "trim-chunk-crlf":
        wire = chunk(body)[:-1]
    elif mode == "missing-zero":
        wire = chunk(body)
    elif mode == "mid-chunk":
        wire = chunk(body[:split]) + f"{len(body) - split:x}\r\n".encode() + body[split : split + 7]
    else:
        wire = f"{len(body):x}\r\n".encode() + body[:7]
    expected = body[:7] if mode == "first-chunk" else body[: split + 7] if mode == "mid-chunk" else body
    append_acked = threading.Event()
    supervisor = setup[1]

    def wait_for_ack_prefix():
        deadline = time.monotonic() + 2
        with supervisor._condition:
            while True:
                attempts = list(host._attempts)
                stage = attempts[0]._stage if len(attempts) == 1 else None
                capture = stage._capture if stage is not None else None
                if capture is not None:
                    outcomes = [ticket._outcome for ticket in capture._tickets]
                    if all(
                        outcome is not None
                        and outcome.state == "ack"
                        and outcome.error is None
                        and not outcome.publication_uncertain
                        for outcome in outcomes
                    ):
                        accepted = capture._committed + sum(
                            outcome.accepted_bytes for outcome in outcomes
                        )
                        if accepted == len(expected):
                            assert capture._snapshot is None
                            assert [
                                bytes(value) for value in MockRecorder.instances[-1].stages.values()
                            ] == [expected]
                            append_acked.set()
                            return
                remaining = deadline - time.monotonic()
                assert remaining > 0, "append-ack-before-eof-oracle"
                supervisor._condition.wait(min(remaining, 0.01))

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(5)
    address = listener.getsockname()
    eof, stop = threading.Event(), threading.Event()
    errors, requests = [], []
    end = time.monotonic() + 8

    def serve():
        try:
            with listener.accept()[0] as peer:

                def recv(size):
                    remaining = end - time.monotonic()
                    assert remaining > 0 and not stop.is_set()
                    peer.settimeout(remaining)
                    return peer.recv(size)

                request = bytearray()
                while b"\r\n\r\n" not in request:
                    data = recv(4096)
                    assert data
                    request.extend(data)
                    assert len(request) <= 65536
                headers, payload = request.split(b"\r\n\r\n", 1)
                length = next(
                    int(line.split(b":", 1)[1])
                    for line in headers.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
                assert 0 <= length <= 65536
                while len(payload) < length:
                    data = recv(4096)
                    assert data
                    payload.extend(data)
                requests.append(bytes(headers.split(b"\r\n")[0]))
                peer.sendall(
                    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n"
                    + wire
                )
                # Qualify the acknowledged prefix, not every byte merely read.
                # Observe real APPEND completion before exposing transport EOF.
                if mode != "complete":
                    wait_for_ack_prefix()
                # Half-close only. Client EOF remains independent cleanup evidence.
                peer.shutdown(socket.SHUT_WR)
                assert recv(1) == b""
                eof.set()
        except BaseException as error:  # noqa: BLE001 - relay worker evidence
            errors.append(error)

    sockets, responses, closed, reads, read_errors = [], [], [], [], []
    real_connect = http.client.HTTPConnection.connect
    real_socket_connect, real_getaddrinfo = socket.socket.connect, socket.getaddrinfo
    real_init, real_close, real_read1 = (
        http.client.HTTPResponse.__init__,
        http.client.HTTPResponse.close,
        http.client.HTTPResponse._read1_chunked,
    )

    def connect(connection):
        assert (connection.host, connection.port) == address
        real_connect(connection)
        sockets.append(connection.sock)

    def socket_connect(sock, destination):
        assert destination == address
        return real_socket_connect(sock, destination)

    def getaddrinfo(hostname, port, *args, **kwargs):
        assert (hostname, port) == address
        return real_getaddrinfo(hostname, port, *args, **kwargs)

    def init(response, *args, **kwargs):
        real_init(response, *args, **kwargs)
        responses.append(response)

    def close(response):
        result = real_close(response)
        closed.append(response)
        return result

    def read1(response, size=-1):
        try:
            data = real_read1(response, size)
        except http.client.IncompleteRead as error:
            read_errors.append(error)
            raise
        reads.append(data)
        return data

    monkeypatch.setattr(http.client.HTTPConnection, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect", socket_connect)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(http.client.HTTPResponse, "__init__", init)
    monkeypatch.setattr(http.client.HTTPResponse, "close", close)
    monkeypatch.setattr(http.client.HTTPResponse, "_read1_chunked", read1)
    for key in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(key, raising=False)
    thread = threading.Thread(target=serve)
    host = None
    try:
        thread.start()
        try:
            host, notices = setup[0](control_timeout_s=2, flush_timeout_s=2)
            configured = dataclasses.replace(backend, base_url=f"http://127.0.0.1:{address[1]}/v1")
            if mode == "complete":
                result = invoke.llm_invoke(
                    "synthetic", configured, timeout_s=3, max_attempts=1, host_audit=host.scope()
                )
                assert result.content["findings"] == []
                assert result.usage.input_tokens == result.usage.output_tokens == 1
            else:
                with pytest.raises(invoke.LLMInvokeError) as caught:
                    invoke.llm_invoke(
                        "synthetic", configured, timeout_s=3, max_attempts=1, host_audit=host.scope()
                    )
                assert len(read_errors) == 1 and caught.value.__cause__ is read_errors[0]
                assert caught.value.usage is None and caught.value.observed_usage is None
                assert not caught.value.is_timeout and caught.value.retryable
            assert eof.wait(3)
        finally:
            stop.set()
            listener.close()
            thread.join(9)
            assert not thread.is_alive()
        assert not errors and requests == [b"POST /v1/chat/completions HTTP/1.1"]
        assert_cleanup(sockets, responses, closed, eof)
        records = list(host._owner._leases.values())
        assert len(records) == host._started == 1
        record = records[0]
        expected = (
            body[:7] if mode == "first-chunk" else body[: split + 7] if mode == "mid-chunk" else body
        )
        complete = mode == "complete"
        assert b"".join(reads) == expected
        if not complete:
            assert read_errors[0].partial == (b"\r" if mode == "trim-chunk-crlf" else b"")
        else:
            assert read_errors == []
        assert append_acked.is_set() is (not complete)
        assert record.settled and len(record.refs) == 1
        ref = record.refs[0]
        assert [bytes(value) for value in record.recorder.stages.values()] == [expected]
        assert ref.sha256 == hashlib.sha256(expected).hexdigest()
        assert ref.retained_bytes == len(expected)
        assert ref.original_bytes == (len(body) if complete else None)
        assert ref.partial is (not complete)
        assert record.captures == {"wire": (len(expected), complete)}
        assert len(record.recorder.finished) == 1
        finish = record.recorder.finished[0]
        assert finish["attempt_id"] == record.value.attempt_id
        assert finish["outcome"] == ("completed" if complete else "failed")
        assert finish["error_class"] == (None if complete else "LLMInvokeError")
        assert finish["dispatch_state"] == "entered_api_send"
        assert host.safe_summary().settled_sends == 1
        assert notices == ([] if complete else ["invocation_audit_unavailable"])
        # Missing-zero carries syntactically complete JSON but must not succeed.
        # Other negative controls reject fictitious cleanup evidence.
        with pytest.raises(AssertionError, match="close-observer-oracle"):
            assert_cleanup(sockets, responses, [], eof)
    finally:
        if host is not None:
            host.close(primary=RuntimeError("fixture cleanup"))
        setup[1].close()
        if setup[1]._worker is not None:
            setup[1]._worker.join(3)
            assert not setup[1]._worker.is_alive()
