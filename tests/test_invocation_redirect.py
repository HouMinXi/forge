"""Local-only redirect transport and capture contracts."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import threading
import time
import urllib.error
import urllib.request
from email.message import Message

import pytest

from code_forge.invocation_redirect import open_with_redirects, safe_address
from code_forge.invocation_stream import WireCapture


@contextmanager
def local_server(status=302, location="/final?private=yes", body=b"redirect body"):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.do_GET()

        def do_GET(self):
            calls.append((self.command, self.path, self.headers.get("Authorization")))
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            first = self.path.startswith("/start")
            payload = body if first else b"final body"
            self.send_response(status if first else 200)
            if first:
                self.send_header("Location", location)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def recorder(records):
    def capture(response, hop, deadline):
        parts = []

        def write(data):
            parts.append(data)
            return len(data)

        wire = WireCapture(write=write, warn=lambda _: None, limit_bytes=10000, chunk_bytes=3)
        interrupted = True
        try:
            for _block in wire.chunks(response):
                if time.monotonic() >= deadline:
                    raise TimeoutError("capture deadline")
            interrupted = False
        finally:
            records.append((hop, b"".join(parts), wire.seal(interrupted=interrupted)))

    return capture


@pytest.mark.parametrize("status", [301, 302, 303])
def test_post_redirect_captures_intermediate_and_final_is_unread(status):
    records = []
    with local_server(status) as (base, calls):
        request = urllib.request.Request(  # noqa: S310 - local HTTP fixture
            base + "/start?secret=value", data=b"request", headers={"Authorization": "Bearer test"}
        )
        with open_with_redirects(
            request, deadline=time.monotonic() + 5, capture_hop=recorder(records)
        ) as response:
            assert response.read() == b"final body"
        assert [call[0] for call in calls] == ["POST", "GET"]
        assert all(call[2] == "Bearer test" for call in calls)
    assert len(records) == 1
    hop, data, snapshot = records[0]
    assert data == b"redirect body"
    assert snapshot.complete
    assert hop.source == base + "/start"
    assert hop.target == base + "/final"
    assert hop.decision == "follow"


@pytest.mark.parametrize("status", [307, 308])
def test_post_307_308_remains_original_unread_http_error(status):
    records = []
    with local_server(status) as (base, calls):
        with pytest.raises(urllib.error.HTTPError) as caught:
            open_with_redirects(
                urllib.request.Request(base + "/start", data=b"x"),  # noqa: S310 - local fixture
                deadline=time.monotonic() + 5,
                capture_hop=recorder(records),
            )
        error = caught.value
        assert error.code == status
        assert error.redirect_hop.decision == "redirect_method_refused"
        assert error.read() == b"redirect body"
        error.close()
        assert len(calls) == 1
        assert records == []


@pytest.mark.parametrize(
    "location,reason",
    [
        ("http://127.0.0.1:1/secret?token=value", "redirect_cross_origin_refused"),
        ("https://example.test/", "redirect_cross_origin_refused"),
        ("http://user:password@127.0.0.1/", "redirect_invalid_target"),
        ("file:///tmp/private", "redirect_invalid_target"),
    ],
)
def test_refusal_never_dials_target_or_consumes_terminal_body(location, reason):
    records = []
    with local_server(location=location) as (base, calls):
        with pytest.raises(urllib.error.HTTPError) as caught:
            open_with_redirects(
                urllib.request.Request(  # noqa: S310 - local HTTP fixture
                    base + "/start", headers={"Authorization": "Bearer test"}
                ),
                deadline=time.monotonic() + 5,
                capture_hop=recorder(records),
            )
        assert caught.value.redirect_hop.decision == reason
        assert caught.value.read() == b"redirect body"
        caught.value.close()
        assert len(calls) == 1
        assert records == []
        assert "password" not in repr(caught.value.redirect_hop)
        assert "token" not in repr(caught.value.redirect_hop)


class SequenceOpener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response


def redirect(source="https://host.test/start", target="/next", status=302):
    headers = Message()
    headers["Location"] = target
    return urllib.error.HTTPError(source, status, "redirect", headers, BytesIO(b"hop body"))


def test_each_hop_gets_separate_capture_shared_budget_and_terminal_is_independent():
    first, second, final = redirect(), redirect(target="/last"), BytesIO(b"final")
    opener = SequenceOpener([first, second, final])
    records = []
    result = open_with_redirects(
        urllib.request.Request("https://host.test/start"),
        deadline=time.monotonic() + 5,
        capture_hop=recorder(records),
        opener=opener,
    )
    assert result is final
    assert result.read() == b"final"
    assert [record[0].index for record in records] == [1, 2]
    assert all(record[1] == b"hop body" and record[2].complete for record in records)
    assert opener.calls[0][1] >= opener.calls[1][1] >= opener.calls[2][1] > 0
    assert first.closed and second.closed


def test_limit_preserves_original_error_identity_status_body_and_cause():
    error = redirect()
    cause = OSError("original cause")
    error.__cause__ = cause
    opener = SequenceOpener([error])
    with pytest.raises(urllib.error.HTTPError) as caught:
        open_with_redirects(
            urllib.request.Request("https://host.test/start"),
            deadline=time.monotonic() + 5,
            capture_hop=recorder([]),
            opener=opener,
            max_hops=0,
        )
    assert caught.value is error and caught.value.__cause__ is cause
    assert error.code == 302
    assert error.redirect_hop.decision == "redirect_limit_reached"
    assert error.read() == b"hop body"


def test_downgrade_and_default_ports():
    error = redirect(target="http://host.test/next")
    with pytest.raises(urllib.error.HTTPError) as caught:
        open_with_redirects(
            urllib.request.Request("https://host.test/start"),
            deadline=time.monotonic() + 5,
            capture_hop=recorder([]),
            opener=SequenceOpener([error]),
        )
    assert caught.value.redirect_hop.decision == "redirect_downgrade_refused"
    records = []
    final = BytesIO(b"final")
    assert (
        open_with_redirects(
            urllib.request.Request("https://HOST.test/start"),
            deadline=time.monotonic() + 5,
            capture_hop=recorder(records),
            opener=SequenceOpener([redirect(target="https://host.test:443/next"), final]),
        )
        is final
    )
    assert len(records) == 1


def test_callback_failure_stops_chain_and_closes_intermediate():
    error = redirect()
    opener = SequenceOpener([error])
    failure = RuntimeError("capture failed")

    def fail(*_args):
        raise failure

    with pytest.raises(RuntimeError) as caught:
        open_with_redirects(
            urllib.request.Request("https://host.test/start"),
            deadline=time.monotonic() + 5,
            capture_hop=fail,
            opener=opener,
        )
    assert caught.value is failure
    assert error.closed and len(opener.calls) == 1


def test_expired_budget_does_not_open():
    opener = SequenceOpener([])
    with pytest.raises(TimeoutError):
        open_with_redirects(
            urllib.request.Request("https://host.test/start"),
            deadline=time.monotonic() - 1,
            capture_hop=recorder([]),
            opener=opener,
        )
    assert opener.calls == []


def test_safe_address_omits_secrets():
    assert safe_address("https://user:secret@host.test:443/path?key=value#private") == (
        "https://host.test:443/path"
    )
    assert safe_address("https://host.test:invalid/path?secret=x") == "[invalid address]"


def test_callback_uses_bounded_reads_and_no_intermediate_bytes_are_skipped():
    class BoundedBody(BytesIO):
        def read1(self, size=-1):
            assert size == 3
            return super().read(size)

        def read(self, size=-1):
            raise AssertionError("unbounded builtin redirect drain must never run")

    error = redirect()
    error.fp = BoundedBody(b"0123456789")
    # HTTPError delegates reads through a temporary-file wrapper created at init.
    error.file = error.fp
    records = []
    open_with_redirects(
        urllib.request.Request("https://host.test/start"),
        deadline=time.monotonic() + 5,
        capture_hop=recorder(records),
        opener=SequenceOpener([error, BytesIO()]),
    )
    assert records[0][1] == b"0123456789"
    assert records[0][2].complete


def test_deadline_exhausted_during_capture_prevents_next_open(monkeypatch):
    now = [10.0]
    monkeypatch.setattr("code_forge.invocation_redirect.time.monotonic", lambda: now[0])
    error = redirect()
    opener = SequenceOpener([error])

    def capture(_response, _hop, deadline):
        assert deadline == 11.0
        now[0] = 12.0

    with pytest.raises(TimeoutError):
        open_with_redirects(
            urllib.request.Request("https://host.test/start"),
            deadline=11.0,
            capture_hop=capture,
            opener=opener,
        )
    assert len(opener.calls) == 1


@pytest.mark.parametrize("failure", [MemoryError(), KeyboardInterrupt()])
def test_fatal_capture_failures_are_preserved(failure):
    def fail(*_args):
        raise failure

    with pytest.raises(type(failure)) as caught:
        open_with_redirects(
            urllib.request.Request("https://host.test/start"),
            deadline=time.monotonic() + 5,
            capture_hop=fail,
            opener=SequenceOpener([redirect()]),
        )
    assert caught.value is failure


@pytest.mark.parametrize("primary_type", [RuntimeError, KeyboardInterrupt, MemoryError])
@pytest.mark.parametrize("cleanup_type", [OSError, KeyboardInterrupt, MemoryError])
def test_capture_primary_survives_cleanup_failure(primary_type, cleanup_type):
    primary = primary_type("original capture failure")
    cause = ValueError("original cause")
    primary.__cause__ = cause
    cleanup = cleanup_type("close failed")
    error = redirect()
    close_calls = []

    def close():
        close_calls.append(True)
        raise cleanup

    error.close = close
    opener = SequenceOpener([error])

    def fail(*_args):
        raise primary

    with pytest.raises(primary_type) as caught:
        open_with_redirects(
            urllib.request.Request("https://host.test/start"),
            deadline=time.monotonic() + 5,
            capture_hop=fail,
            opener=opener,
        )
    assert caught.value is primary
    assert caught.value.__cause__ is cause
    assert close_calls == [True]
    assert len(opener.calls) == 1


@pytest.mark.parametrize("cleanup_type", [OSError, KeyboardInterrupt, MemoryError])
def test_cleanup_failure_without_primary_remains_visible(cleanup_type):
    cleanup = cleanup_type("close failed")
    error = redirect()

    def close():
        raise cleanup

    error.close = close
    records = []
    opener = SequenceOpener([error])
    with pytest.raises(cleanup_type) as caught:
        open_with_redirects(
            urllib.request.Request("https://host.test/start"),
            deadline=time.monotonic() + 5,
            capture_hop=recorder(records),
            opener=opener,
        )
    assert caught.value is cleanup
    assert len(records) == 1 and records[0][2].complete
    assert len(opener.calls) == 1


def test_successful_cleanup_preserves_primary_failure():
    primary = RuntimeError("original capture failure")
    cause = ValueError("original cause")
    primary.__cause__ = cause
    error = redirect()

    def fail(*_args):
        raise primary

    with pytest.raises(RuntimeError) as caught:
        open_with_redirects(
            urllib.request.Request("https://host.test/start"),
            deadline=time.monotonic() + 5,
            capture_hop=fail,
            opener=SequenceOpener([error]),
        )
    assert caught.value is primary
    assert caught.value.__cause__ is cause
    assert error.closed
