"""Drive the stdlib HTTP parser and writer against a socketpair, without TLS/network."""

import http.client
import json
import socket
from dataclasses import replace

import pytest

from code_forge.eval.review_followup import smoke_runner
from code_forge.eval.review_followup.attempt_plan import AttemptSlot
from code_forge.eval.review_followup.fixtures import InputError, digest
from code_forge.eval.review_followup.smoke_transport import DirectSmokeTransport, SmokeWireError
from test_attempt_plan import gate, pinned
from test_smoke_runner import journal

MODEL = "synthetic-model"
KEY = "test-key-not-real"
OK = {
    "model": MODEL,
    "choices": [
        {"finish_reason": "stop", "message": {"role": "assistant", "content": "synthetic answer"}}
    ],
    "usage": {
        "prompt_tokens": 13,
        "completion_tokens": 2,
        "prompt_tokens_details": {"cached_tokens": 0},
    },
}


def request(**updates):
    return json.dumps(
        {"model": MODEL, "messages": [{"role": "user", "content": "sample"}], "max_tokens": 4} | updates,
        indent=1,
    ).encode()


def response(payload=OK, status=200, *, framing="length", other=b""):
    raw = payload if type(payload) is bytes else json.dumps(payload).encode()
    header = f"HTTP/1.1 {status} Fixture\r\nContent-Type: application/json\r\n".encode()
    if framing == "chunked":
        header += b"Transfer-Encoding: chunked\r\n"
        raw = f"{len(raw):x}\r\n".encode() + raw + b"\r\n0\r\n\r\n"
    elif framing == "length":
        header += f"Content-Length: {len(raw)}\r\n".encode()
    return header + b"Connection: close\r\n" + other + b"\r\n" + raw


@pytest.fixture
def peer(monkeypatch):
    pending, accepted = [], []

    def connect(connection):
        assert connection.host == "smoke.invalid" and connection.port == 443
        assert connection._context.check_hostname
        assert connection._context.keylog_filename is None
        assert connection._tunnel_host is None
        wire = pending.pop(0)
        client, server = socket.socketpair()
        accepted.append(server)
        client.settimeout(0.03)
        server.settimeout(2)
        connection.sock = client
        if wire is not None:
            server.sendall(wire)
            server.shutdown(socket.SHUT_WR)

    monkeypatch.setattr(http.client.HTTPSConnection, "connect", connect)
    yield pending, accepted
    for server in accepted:
        server.close()


def observed(server, request_id=None):
    chunks = []
    while chunk := server.recv(8192):
        chunks.append(chunk)
    headers, body = b"".join(chunks).split(b"\r\n\r\n", 1)
    assert headers.splitlines()[0] == b"POST /v1/chat/completions HTTP/1.1"
    assert b"Host: smoke.invalid\r\n" in headers
    assert f"Authorization: Bearer {KEY}".encode() in headers
    if request_id is not None:
        assert f"X-Client-Request-Id: {request_id}".encode() in headers
    return body


def transport(**kwargs):
    return DirectSmokeTransport("smoke.invalid", MODEL, KEY, output_cap=4, **kwargs)


def plan(body, last=None):
    slots = [
        (AttemptSlot(row.arm, row.group, row.pass_name, digest(data)), data)
        for (row, _), data in zip(
            pinned(["storage"]), (body,) * 5 + (body if last is None else last,), strict=True
        )
    ]
    return gate(["storage"], slots, max_body_bytes=512), tuple(data for _, data in slots)


def run(root, body, sender, last=None):
    admission, bodies = plan(body, last)
    return smoke_runner.run_smoke_once(
        journal_dir=root, run_id="wire-pair", gate=admission, request_bodies=bodies, send_once=sender
    )


@pytest.mark.parametrize("framing", ["length", "chunked"])
def test_real_http_bytes_and_known_zero_usage(peer, framing):
    pending, servers = peer
    pending.append(response(framing=framing))
    body = request()
    result = transport()(body)
    assert observed(servers[0]) == body
    assert result.outcome == "accepted" and result.finish_reason == "stop"
    assert result.response_sha256 == digest(json.dumps(OK).encode())
    assert result.reported_model == MODEL
    assert (result.input_tokens, result.cached_input_tokens, result.output_tokens) == (13, 0, 2)


@pytest.mark.parametrize("status", [302, 429, 503])
def test_known_failure_stops_pair_without_redirect_retry_or_leak(tmp_path, peer, status):
    pending, servers = peer
    pending.append(response({"error": KEY}, status, other=b"Location: https://else.invalid/\r\n"))
    body = request()
    rows = run(tmp_path, body, transport())
    assert len(servers) == 1 and observed(servers[0]) == body
    assert [row.state for row in rows] == ["failed", *["not_started"] * 5]
    records = journal(tmp_path)
    assert records[0]["wire_policy"]["model"] == MODEL
    assert records[-1]["wire"]["status"] == status
    assert records[-1]["wire"]["outcome"] == "http_status"
    assert records[-1]["wire"]["response_sha256"] is None
    assert KEY not in repr(records) + repr(transport())
    with pytest.raises(InputError, match="already-started"):
        run(tmp_path, body, transport())
    assert len(servers) == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, "wire_timeout_unknown"), (response()[:-10], "wire_truncated_unknown")],
)
def test_incomplete_answer_leaves_reservation_unknown(tmp_path, peer, value, expected):
    pending, servers = peer
    pending.append(value)
    body = request()
    with pytest.raises(SmokeWireError, match=expected) as caught:
        run(tmp_path, body, transport())
    assert caught.value.__context__ is None
    records = journal(tmp_path)
    assert [row["event"] for row in records] == ["started", "reserved"]
    request_id = records[-1]["client_request_id"]
    assert len(request_id) == 32
    assert len(servers) == 1 and observed(servers[0], request_id) == body
    with pytest.raises(InputError, match="already-started"):
        run(tmp_path, body, transport())


@pytest.mark.parametrize(
    "bad",
    [
        request(model="other"),
        request(stream=True),
        request(n=True),
        request(tools=[]),
        request(max_tokens=0),
        request(max_tokens=True),
        request(max_tokens=5),
        request(max_completion_tokens=2),
        request(messages=[{"role": "tool", "content": "bad"}]),
        b'{"model":"one","model":"two"}',
    ],
)
def test_last_body_invalid_prevents_first_send(tmp_path, peer, bad):
    with pytest.raises(InputError, match="smoke_wire"):
        run(tmp_path, request(), transport(), bad)
    assert not list(tmp_path.iterdir())
    assert not peer[1]


@pytest.mark.parametrize("host", ["http://example.com", "user@example.com", "example.com:443", "x/y"])
def test_host_is_not_a_url(host):
    with pytest.raises(InputError, match="smoke_wire.host"):
        replace(transport(), host=host)


@pytest.mark.parametrize(
    ("wire", "outcome"),
    [
        (response(OK | {"model": "unexpected"}), "model_mismatch"),
        (response(b'{"model":"synthetic-model","model":"synthetic-model"}'), "malformed_response"),
        (response(OK | {"error": "upstream rejected"}), "malformed_response"),
        (response(OK | {"usage": {"completion_tokens": 5}}), "output_over_limit"),
        (response(other=b"Content-Encoding: gzip\r\n"), "unsupported_response"),
        (response(framing="none"), "unsupported_response"),
        (response(other=b"Content-Length: 0\r\n"), "unsupported_response"),
        (response(other=b"Transfer-Encoding: chunked\r\n"), "unsupported_response"),
    ],
)
def test_unsuitable_reply_is_not_completion(tmp_path, peer, wire, outcome):
    peer[0].append(wire)
    rows = run(tmp_path, request(), transport())
    assert [row.state for row in rows] == ["failed", *["not_started"] * 5]
    assert journal(tmp_path)[-1]["wire"]["outcome"] == outcome


@pytest.mark.parametrize("framing", ["length", "chunked"])
def test_response_ceiling(peer, framing):
    peer[0].append(response(framing=framing))
    assert transport(response_cap=16)(request()).outcome == "response_over_limit"


def test_all_six_slots_and_unknown_usage(tmp_path, peer):
    pending, servers = peer
    no_counts = OK | {"usage": {"prompt_tokens": False, "completion_tokens": -1}}
    pending.extend([response(no_counts)] * 6)
    body = request()
    rows = run(tmp_path, body, transport())
    assert all(row.state == "completed" for row in rows)
    assert len(servers) == 6 and all(observed(server) == body for server in servers)
    records = [e["wire"] for e in journal(tmp_path) if e["event"] == "finished"]
    assert len(records) == 6
    assert all(
        r["input_tokens"] is r["cached_input_tokens"] is r["output_tokens"] is None for r in records
    )
    assert "synthetic answer" not in repr(records)


def test_wire_receipt_must_be_durable_before_completion(tmp_path, peer, monkeypatch):
    pending, servers = peer
    pending.append(response())
    body = request()
    admission, bodies = plan(body)
    original_append = smoke_runner._append

    def append(journal_file, event):
        if event["event"] == "finished":
            assert event["wire"]["reported_model"] == MODEL
            raise OSError("terminal journal unavailable")
        original_append(journal_file, event)

    monkeypatch.setattr(smoke_runner, "_append", append)
    with pytest.raises(OSError, match="terminal journal unavailable"):
        smoke_runner.run_smoke_once(
            journal_dir=tmp_path,
            run_id="wire-pair",
            gate=admission,
            request_bodies=bodies,
            send_once=transport(),
        )
    assert [row.state for row in admission.rows] == ["unknown_outcome", *["not_started"] * 5]
    assert len(servers) == 1 and observed(servers[0]) == body
    assert journal(tmp_path)[-1]["event"] == "reserved"
    with pytest.raises(InputError, match="already-started"):
        run(tmp_path, body, transport())


def test_tls_context_does_not_enable_keylog(tmp_path, peer, monkeypatch):
    keylog = tmp_path / "keylog"
    monkeypatch.setenv("SSLKEYLOGFILE", str(keylog))
    peer[0].append(response())
    assert transport()(request()).outcome == "accepted"
    assert not keylog.exists()


def test_cached_usage_is_subset_and_reply_id_is_hashed(peer):
    payload = OK | {
        "usage": {
            "prompt_tokens": 10,
            "prompt_tokens_details": {"cached_tokens": 4},
            "completion_tokens": 2,
        }
    }
    peer[0].append(response(payload, other=b"X-Request-Id: server-123\r\n"))
    receipt = transport()(request())
    assert (receipt.input_tokens, receipt.cached_input_tokens, receipt.output_tokens) == (10, 4, 2)
    assert receipt.server_request_id_sha256 == digest(b"server-123")


@pytest.mark.parametrize(
    ("reason_fields", "normalized", "content"),
    [
        ({"finish_reason": "length"}, "length", "partial answer"),
        ({"finish_reason": "content_filter"}, "content_filter", "partial answer"),
        ({"finish_reason": "content_filter"}, "content_filter", None),
        ({"finish_reason": "tool_calls"}, "tool_calls", "partial answer"),
        ({"finish_reason": "function_call"}, "function_call", None),
        ({}, None, "partial answer"),
        ({"finish_reason": None}, None, "partial answer"),
        ({"finish_reason": {"unexpected": True}}, None, "partial answer"),
        ({"finish_reason": KEY}, None, "partial answer"),
    ],
)
def test_nonstop_finish_keeps_wire_evidence_and_stops_pair(
    tmp_path, peer, reason_fields, normalized, content
):
    pending, servers = peer
    payload = OK | {"choices": [{"message": {"role": "assistant", "content": content}, **reason_fields}]}
    pending.extend([response(payload, other=b"X-Request-Id: server-123\r\n"), response()])
    body = request()
    rows = run(tmp_path, body, transport())
    assert len(servers) == 1 and observed(servers[0]) == body
    assert len(pending) == 1
    assert [row.state for row in rows] == ["failed", *["not_started"] * 5]
    wire = journal(tmp_path)[-1]["wire"]
    assert (wire["status"], wire["outcome"], wire["finish_reason"]) == (
        200,
        "unfinished_response",
        normalized,
    )
    assert wire["response_sha256"] == digest(json.dumps(payload).encode())
    assert wire["reported_model"] == MODEL
    assert wire["server_request_id_sha256"] == digest(b"server-123")
    assert (wire["input_tokens"], wire["cached_input_tokens"], wire["output_tokens"]) == (13, 0, 2)
    assert KEY not in repr(wire) and "partial answer" not in repr(wire)
    with pytest.raises(InputError, match="already-started"):
        run(tmp_path, body, transport())
    assert len(servers) == 1


@pytest.mark.parametrize(
    "message",
    [
        {"content": ""},
        {"content": " \t\r\n\u00a0"},
        {"content": "not a usable answer", "refusal": KEY},
        {"content": None, "refusal": KEY},
        {"content": "not a usable answer", "refusal": True},
    ],
)
def test_stop_without_usable_reply_keeps_receipt_and_stops(tmp_path, peer, message):
    pending, servers = peer
    payload = OK | {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", **message}}]}
    pending.extend([response(payload, other=b"X-Request-Id: server-123\r\n"), response()])
    body = request()
    rows = run(tmp_path, body, transport())
    events = journal(tmp_path)
    assert [row.state for row in rows] == ["failed", *["not_started"] * 5]
    assert [event["event"] for event in events] == ["started", "reserved", "finished"]
    assert len(servers) == 1 and observed(servers[0], events[1]["client_request_id"]) == body
    assert len(pending) == 1
    wire = events[-1]["wire"]
    assert (wire["status"], wire["outcome"], wire["finish_reason"]) == (200, "no_usable_reply", "stop")
    assert wire["response_sha256"] == digest(json.dumps(payload).encode())
    assert wire["server_request_id_sha256"] == digest(b"server-123")
    assert (wire["input_tokens"], wire["cached_input_tokens"], wire["output_tokens"]) == (13, 0, 2)
    assert KEY not in repr(events) and "not a usable answer" not in repr(events)
    with pytest.raises(InputError, match="already-started"):
        run(tmp_path, body, transport())
    assert len(servers) == 1


def test_stop_with_text_and_explicit_null_refusal_is_accepted(peer):
    message = {"role": "assistant", "content": "  a usable answer\n", "refusal": None}
    peer[0].append(response(OK | {"choices": [{"finish_reason": "stop", "message": message}]}))
    assert transport()(request()).outcome == "accepted"


@pytest.fixture
def retention(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    return private


def run_retained(root, private, body, *, last=None):
    admission, bodies = plan(body, last)
    return smoke_runner.run_smoke_once(
        journal_dir=root,
        run_id="wire-pair",
        gate=admission,
        request_bodies=bodies,
        send_once=transport(),
        artifact_dir=private,
    )


@pytest.mark.parametrize("framing", ["length", "chunked"])
def test_retained_payloads_match_exact_bytes_and_receipts(tmp_path, retention, peer, framing):
    raw = json.dumps(OK, indent=2).encode() + b"\n"
    peer[0].extend([response(raw, framing=framing, other=b"X-Request-Id: header-only-id\r\n")] * 6)
    body = request()
    rows = run_retained(tmp_path, retention, body)
    assert all(row.state == "completed" for row in rows)
    start, *events = journal(tmp_path)
    namespace = retention / start["artifact_namespace"]
    assert namespace.stat().st_mode & 0o777 == 0o700
    assert len(list(namespace.iterdir())) == 12
    for i, server in enumerate(peer[1]):
        reserved, finished = events[2 * i : 2 * i + 2]
        assert observed(server, reserved["client_request_id"]) == body
        for event, kind, payload in [(reserved, "request", body), (finished, "response", raw)]:
            ref = event[f"{kind}_artifact"]
            path = namespace / ref["file"]
            assert path.stat().st_mode & 0o777 == 0o600
            assert path.read_bytes() == payload and ref["sha256"] == digest(payload)
            assert ref["bytes"] == len(payload)
            assert KEY.encode() not in payload and b"header-only-id" not in payload
        assert reserved["request_artifact"]["sha256"] == start["slots"][i]["request_sha256"]
        assert finished["response_artifact"]["sha256"] == finished["wire"]["response_sha256"]
    assert "synthetic answer" not in repr(events) and "header-only-id" not in repr(events)


@pytest.mark.parametrize("case", ["public", "symlink"])
def test_invalid_private_root_rejects_before_marker_or_send(tmp_path, retention, peer, case):
    if case == "public":
        retention.chmod(0o755)
        target = retention
    else:
        target = tmp_path / "alias"
        target.symlink_to(retention, target_is_directory=True)
    with pytest.raises((OSError, InputError)):
        run_retained(tmp_path, target, request())
    assert not list(tmp_path.glob("*.jsonl")) and not list(retention.iterdir()) and not peer[1]


def test_rejected_and_refused_replies_are_not_retained(tmp_path, retention, peer):
    refused = {
        "model": MODEL,
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": "not an answer",
                    "refusal": "not retained",
                },
            }
        ],
    }
    peer[0].append(response(refused))
    rows = run_retained(tmp_path, retention, request())
    assert [row.state for row in rows] == ["failed", *["not_started"] * 5]
    start, reservation, finished = journal(tmp_path)
    assert finished["wire"]["outcome"] == "no_usable_reply"
    assert "response_artifact" not in finished
    assert [p.name for p in (retention / start["artifact_namespace"]).iterdir()] == [
        reservation["request_artifact"]["file"]
    ]
    assert len(peer[1]) == 1


def test_escaped_key_in_later_request_is_refused_during_preflight(tmp_path, retention, peer):
    contaminated = request(messages=[{"role": "user", "content": KEY}]).replace(
        KEY.encode(), b"\\u0074" + KEY.encode()[1:]
    )
    with pytest.raises(InputError, match="credential_in_body"):
        run_retained(tmp_path, retention, request(), last=contaminated)
    assert not list(tmp_path.glob("*.jsonl")) and not list(retention.iterdir()) and not peer[1]


def test_escaped_key_in_reply_is_not_retained_or_retried(tmp_path, retention, peer):
    raw = (
        json.dumps(OK | {"extra": {KEY: "value"}})
        .encode()
        .replace(KEY.encode(), b"\\u0074" + KEY.encode()[1:])
    )
    peer[0].append(response(raw))
    rows = run_retained(tmp_path, retention, request())
    assert [row.state for row in rows] == ["failed", *["not_started"] * 5]
    start, _, finished = journal(tmp_path)
    assert finished["wire"]["outcome"] == "private_reply_rejected"
    assert finished["wire"]["response_sha256"] == digest(raw)
    assert "response_artifact" not in finished
    assert sorted(p.name for p in (retention / start["artifact_namespace"]).iterdir()) == [
        "01-request.json"
    ]
    with pytest.raises(InputError, match="already-started"):
        run_retained(tmp_path, retention, request())
    assert len(peer[1]) == 1


@pytest.mark.parametrize("kind", ["request", "response"])
def test_disk_error_never_publishes_unstored_evidence_or_resends(
    tmp_path, retention, peer, monkeypatch, kind
):
    from code_forge.eval.review_followup import smoke_artifacts

    fsync, open_fd = smoke_artifacts.os.fsync, smoke_artifacts.os.open
    opened = {}

    def record_open(name, *args, **kwargs):
        fd = open_fd(name, *args, **kwargs)
        opened[fd] = name
        return fd

    def sync(fd):
        if str(opened[fd]).endswith(f"-{kind}.json"):
            raise OSError("fake sync failure")
        fsync(fd)

    monkeypatch.setattr(smoke_artifacts.os, "open", record_open)
    monkeypatch.setattr(smoke_artifacts.os, "fsync", sync)
    peer[0].append(response())
    with pytest.raises(OSError if kind == "request" else smoke_runner.ArtifactWriteError):
        run_retained(tmp_path, retention, request())
    assert [entry["event"] for entry in journal(tmp_path)] == (
        ["started"] if kind == "request" else ["started", "reserved"]
    )
    with pytest.raises(InputError, match="already-started"):
        run_retained(tmp_path, retention, request())
    assert len(peer[1]) == (kind == "response")


def test_existing_namespace_is_not_reused(tmp_path, retention, peer):
    namespace = retention / digest(b"wire-pair")
    namespace.mkdir(mode=0o700)
    existing = namespace / "01-request.json"
    existing.write_bytes(b"unaltered")
    with pytest.raises(FileExistsError):
        run_retained(tmp_path, retention, request())
    assert existing.read_bytes() == b"unaltered"
    assert [entry["event"] for entry in journal(tmp_path)] == ["started"]
    assert not peer[1]


def test_default_does_not_retain_payloads(tmp_path, peer):
    peer[0].extend([response()] * 6)
    assert all(row.state == "completed" for row in run(tmp_path, request(), transport()))
    assert {path.suffix for path in tmp_path.iterdir()} == {".jsonl"}
    assert all("artifact" not in field for row in journal(tmp_path) for field in row)


def test_retention_requires_direct_transport(tmp_path, retention):
    admission, bodies = plan(request())
    calls = []
    with pytest.raises(InputError, match="smoke_artifacts.transport"):
        smoke_runner.run_smoke_once(
            journal_dir=tmp_path,
            run_id="wire-pair",
            gate=admission,
            request_bodies=bodies,
            send_once=calls.append,
            artifact_dir=retention,
        )
    assert not list(tmp_path.glob("*.jsonl")) and not list(retention.iterdir()) and not calls
