"""Real dispatch + shared acquisition, with simulated host settlement only."""

import dataclasses
import hashlib
import io
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from code_forge import invocation_io, invocation_send_audit, llm_invoke as invoke
from code_forge.backend import BackendConfig
from code_forge.invocation_buffered_capture import BufferedCaptureSession, SharedBufferedCaptureSession
from code_forge.invocation_io import IoLimits, process_audit_io
from code_forge.llm_invoke import AuditPersistenceError, LLMInvokeError


GOOD = '{"findings":[],"code_excerpts":[]}'
PARTIAL = '{"findings":['
BROKEN = '{"findings":[{"ok":true},,{"ok":false}]}'
FINDING = {"file": "a.py", "line": 1, "severity": "LOW", "description": "inspect"}


class Response(io.BytesIO):
    status = 200
    headers = {}

    def __init__(self, raw):
        super().__init__(raw)
        self.raw = raw
        self.reads = []

    def read(self, size=-1):
        self.reads.append(size)
        return super().read(size)

    def read1(self, size=-1):
        self.reads.append(size)
        return super().read1(size)


def reply(content=GOOD, *, finish="stop", stream=False, tokens=1):
    usage = {"prompt_tokens": tokens, "completion_tokens": tokens}
    if stream:
        event = {
            "choices": [
                {
                    "delta": {"reasoning_content": "private-thinking", "content": content},
                    "finish_reason": finish,
                }
            ],
            "usage": usage,
        }
        return Response(b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\nraw-tail")
    return Response(
        json.dumps(
            {
                "choices": [{"message": {"content": content}, "finish_reason": finish}],
                "usage": usage,
            }
        ).encode()
    )


def http_error(
    status=503,
    raw=b"provider failed",
    *,
    url="https://provider.invalid/v1/chat/completions",
    location=None,
):
    return urllib.error.HTTPError(
        url,
        status,
        "provider status",
        {} if location is None else {"location": location},
        Response(raw),
    )


class FakeAttempt:
    """True is a simulated host ACK, never a recorder qualification."""

    def __init__(self, host, request, number):
        self.host, self.request, self.number = host, request, number
        self.captured = None
        self.raw = bytearray()
        self.finished = False
        self.entered = False

    def enter_send(self):
        self.host.events.append(("entered", self.number))
        if self.host.enter_hook is not None:
            self.host.enter_hook()
        if self.number == self.host.fail_enter:
            raise OSError("private enter failure")
        self.entered = True
        return True

    def new_response(self, *, status, redirect_hop, deadline):
        assert self.captured is None
        self.host.events.append(("response", self.number, status, redirect_hop, deadline))
        if self.number == self.host.fail_response:
            raise OSError("private host failure")

        def write(data):
            if self.host.write_hook is not None:
                self.host.write_hook(data)
            if self.number == self.host.fail_write:
                raise OSError("private sink failure")
            self.raw.extend(data)
            return len(data)

        self.captured = self.host.session.new_capture(
            write=write,
            warn=self.host.warn,
            limit_bytes=8 * 1024 * 1024,
            action_deadline=deadline,
            chunk_bytes=128,
            strict=self.host.strict,
        )
        return self.captured

    def finish(self, **fields):
        assert not self.finished
        self.host.events.append(("finish", self.number, fields))
        if self.number == self.host.fail_finish:
            raise self.host.finish_error
        complete = (
            self.captured is None
            or self.captured.seal(interrupted=fields["outcome"] != "completed").complete
        )
        self.finished = True
        # Rejection of an incomplete body is simulated policy. Even a complete
        # capture here is not a real recorder ACK or proof of durable storage.
        return complete


class FakeHost:
    def __init__(self, session, *, strict=False):
        self.session, self.strict = session, strict
        self.attempts, self.requests, self.events, self.warnings = [], [], [], []
        self.fail_start = self.fail_response = self.fail_finish = self.fail_write = 0
        self.fail_enter = 0
        self.finish_error = OSError("private finish failure")
        self.write_hook = None
        self.start_hook = self.enter_hook = None

    def start_send(self, request):
        self.requests.append(request)
        number = len(self.requests)
        assert request.parent is None or request.parent.finished
        self.events.append(("start", number))
        if self.start_hook is not None:
            self.start_hook()
        if number == self.fail_start:
            raise OSError("private start failure")
        attempt = FakeAttempt(self, request, number)
        self.attempts.append(attempt)
        return attempt

    def warn(self, code):
        self.warnings.append(code)


@pytest.fixture
def host_factory(monkeypatch):
    monkeypatch.setattr(invocation_io, "_PROCESS", None)
    process = process_audit_io(limits=IoLimits(64, 65536, 512, 16), warn=lambda _: None)
    sessions = []

    def factory(**kwargs):
        session = SharedBufferedCaptureSession(
            process, buffer_bytes=4096, batch_bytes=512, max_delay=0, flush_timeout_s=0.2
        )
        sessions.append(session)
        return FakeHost(session, **kwargs)

    yield factory
    for session in sessions:
        session.close()
    process.close()
    if process._worker is not None:
        process._worker.join(2)
        assert not process._worker.is_alive()


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setenv("TEST_SEND_KEY", "secret-auth-token")
    monkeypatch.setattr(invoke, "_CONTINUE_DELAY_S", 0)
    return BackendConfig(
        name="test-send",
        type="api",
        format="openai",
        model="unit",
        base_url="https://provider.invalid/v1",
        api_key_env="TEST_SEND_KEY",
        max_tokens=8,
        output_token_limit=64,
    )


def scripted(monkeypatch, responses, *, host=None):
    def forbidden(*_, **__):
        raise AssertionError("unexpected unaudited provider send")

    monkeypatch.setattr(invoke.urllib.request, "urlopen", forbidden)

    class Transport:
        def __init__(self):
            self.requests = []

        def open(self, request, *, timeout):
            self.requests.append((request, timeout))
            if host is not None:
                assert len(host.requests) >= len(self.requests)
            response = responses[len(self.requests) - 1]
            if isinstance(response, BaseException):
                raise response
            return response

    transport = Transport()
    monkeypatch.setattr(invocation_send_audit.urllib.request, "build_opener", lambda *_: transport)
    return transport


def scenario(name):
    if name == "initial":
        return [reply()], ["initial"]
    if name == "retry":
        return [http_error(), reply()], ["initial", "retry"]
    if name == "correction":
        return [reply(BROKEN), reply()], ["initial", "correction"]
    if name == "continuation":
        return [reply(PARTIAL, finish="length"), reply('],"code_excerpts":[]}')], [
            "initial",
            "continuation",
        ]
    if name == "headroom":
        return [reply(PARTIAL, finish="length"), reply("nonsense"), reply("nonsense"), reply()], [
            "initial",
            "continuation",
            "continuation",
            "headroom",
        ]
    if name == "excerpt_repair":
        first = json.dumps({"findings": [FINDING]})
        second = json.dumps({"findings": [FINDING], "code_excerpts": [{"file": "a.py", "content": "x"}]})
        return [reply(first), reply(second)], ["initial", "excerpt_repair"]
    raise AssertionError(name)


@pytest.mark.parametrize(
    "name", ["initial", "retry", "correction", "continuation", "headroom", "excerpt_repair"]
)
def test_real_public_dispatch_propagates_each_actual_send(monkeypatch, host_factory, backend, name):
    host = host_factory()
    responses, causes = scenario(name)
    expected_raw = [
        item.fp.raw if isinstance(item, urllib.error.HTTPError) else item.raw for item in responses
    ]
    transport = scripted(monkeypatch, responses, host=host)

    def forbidden_serial_worker(_self):
        pytest.fail("old serial buffered worker was started")

    monkeypatch.setattr(BufferedCaptureSession, "_run", forbidden_serial_worker)
    result = invoke.llm_invoke(
        "review actual prompt",
        backend,
        timeout_s=3,
        max_attempts=5,
        initial_delay_s=0,
        host_audit=host,
    )
    assert isinstance(result.content, dict)
    assert [item.cause for item in host.requests] == causes
    assert len(transport.requests) == len(host.attempts) == len(causes)
    assert len({id(item.captured) for item in host.attempts}) == len(causes)
    assert [bytes(item.raw) for item in host.attempts] == expected_raw
    for number, (attempt, (request, _timeout)) in enumerate(
        zip(host.attempts, transport.requests, strict=True)
    ):
        assert attempt.entered and attempt.finished and attempt.captured.seal().complete
        assert attempt.request.parent is (host.attempts[number - 1] if number else None)
        expected = hashlib.sha256(
            request.get_method().encode() + b"\0" + request.full_url.encode() + b"\0" + request.data
        ).hexdigest()
        assert attempt.request.request_digest == expected
        assert attempt.request.request_bytes == len(request.data)
        assert "secret-auth-token" not in repr(attempt.request)
        assert "review actual prompt" not in repr(attempt.request)


@pytest.mark.parametrize(
    "name,fail_at",
    [
        ("initial", 1),
        ("correction", 2),
        ("continuation", 2),
        ("headroom", 4),
        ("excerpt_repair", 2),
    ],
)
@pytest.mark.parametrize("failure", ["start", "finish"])
def test_strict_audit_error_escapes_every_recovery_without_extra_send(
    monkeypatch, host_factory, backend, name, fail_at, failure
):
    host = host_factory(strict=True)
    setattr(host, "fail_" + failure, fail_at)
    responses, _ = scenario(name)
    transport = scripted(monkeypatch, responses + [reply()] * 5)
    with pytest.raises(AuditPersistenceError) as caught:
        invoke.llm_invoke("p", backend, timeout_s=3, max_attempts=5, initial_delay_s=0, host_audit=host)
    assert caught.value.kind == "audit_persistence_error" and not caught.value.retryable
    assert len(transport.requests) == fail_at - (failure == "start")
    assert "private" not in str(caught.value)


def test_default_failure_warns_and_keeps_result_and_provider_call_count(
    monkeypatch, host_factory, backend
):
    host = host_factory()
    host.fail_start = 1
    transport = scripted(monkeypatch, [reply(BROKEN), reply()])
    result = invoke._invoke_api("p", backend, 3, initial_delay_s=0, host_audit=host)
    assert result.content["findings"] == []
    assert len(transport.requests) == 2 and len(host.requests) == 1
    assert host.warnings == ["audit_send_unavailable"]


@pytest.mark.parametrize("primary", [ValueError("parser primary"), MemoryError("fatal primary")])
def test_settlement_never_replaces_parser_or_fatal_primary(monkeypatch, host_factory, backend, primary):
    host = host_factory(strict=True)
    host.fail_finish = 1
    host.finish_error = MemoryError("secondary settlement")
    scripted(monkeypatch, [reply()])

    def broken(*_):
        raise primary

    monkeypatch.setattr(invoke, "_parse_response_body", broken)
    with pytest.raises(type(primary)) as caught:
        invoke._invoke_api("p", backend, 3, host_audit=host)
    assert caught.value is primary


def test_http_primary_survives_capture_and_settlement_failure_without_strict_retry(
    monkeypatch, host_factory, backend
):
    host = host_factory(strict=True)
    host.fail_response = host.fail_finish = 1
    host.finish_error = MemoryError("secondary settlement")
    primary = http_error()
    transport = scripted(monkeypatch, [primary, reply()])
    with pytest.raises(LLMInvokeError) as caught:
        invoke._invoke_api("p", backend, 3, initial_delay_s=0, host_audit=host)
    assert caught.value.__cause__ is primary
    assert caught.value.exit_code == 503 and caught.value.retryable
    assert len(transport.requests) == 1


def test_cap_and_breaker_skips_do_not_create_send_records(monkeypatch, host_factory, backend):
    host = host_factory()
    transport = scripted(monkeypatch, [])
    scope = invocation_send_audit.SendAuditScope(host)
    assert (
        invoke._retry_with_more_headroom(
            "p",
            dataclasses.replace(backend, output_token_limit=0),
            "key",
            3,
            None,
            _audit_scope=scope,
        )
        is None
    )
    breaker = invoke.TruncationBreaker(threshold=1)
    with pytest.raises(invoke.TruncationBreakerError):
        breaker.record_truncation()
    with pytest.raises(invoke.TruncationBreakerError):
        invoke._invoke_api("p", backend, 3, continuation_breaker=breaker, host_audit=host)
    assert not host.requests and not transport.requests


@pytest.mark.parametrize("kind", ["audit_persistence_error", "send_deadline"])
def test_provider_kind_string_cannot_become_typed_strict_host_failure(monkeypatch, backend, kind):
    provider = LLMInvokeError("provider selected label", kind=kind, retryable=False)

    def fail(*_, **__):
        raise provider

    monkeypatch.setattr(invoke, "_invoke_openai", fail)
    assert invoke._retry_with_more_headroom("p", backend, "key", 3, None) is None
    parsed = {"findings": [FINDING]}
    assert invoke._repair_missing_excerpts(parsed, "p", backend, 3)[0] is parsed


@pytest.mark.parametrize("stream", [False, True])
def test_local_http_redirect_hops_are_distinct_and_settled_before_next_send(
    host_factory, backend, stream
):
    host = host_factory(strict=True)
    final = reply(stream=stream).raw
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            requests.append(
                (self.command, self.path, self.rfile.read(int(self.headers["Content-Length"])))
            )
            self.send_response(302)
            self.send_header("Location", "/final?private-query")
            self.end_headers()
            self.wfile.write(b"redirect-private-body")

        def do_GET(self):
            assert host.attempts[0].finished
            requests.append((self.command, self.path, b""))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(final)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        configured = dataclasses.replace(
            backend, base_url=f"http://127.0.0.1:{server.server_port}/v1", stream=stream
        )
        result = invoke._invoke_api("p", configured, 3, host_audit=host)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
    assert result.content["findings"] == []
    assert [item.cause for item in host.requests] == ["initial", "redirect"]
    assert [item.method for item in host.requests] == ["POST", "GET"]
    assert host.requests[0].deadline == host.requests[1].deadline
    assert host.requests[1].parent is host.attempts[0]
    assert [bytes(item.raw) for item in host.attempts] == [b"redirect-private-body", final]
    assert len(requests) == len(host.attempts) == 2
    assert host.requests[0].request_digest != host.requests[1].request_digest
    assert all(item.captured.seal().complete for item in host.attempts)
    assert not host.warnings


def test_refused_redirect_final_body_is_captured_once_and_never_followed(
    monkeypatch, host_factory, backend
):
    host = host_factory()
    primary = http_error(302, b"refused-body", location="https://other.invalid/private?secret")
    transport = scripted(monkeypatch, [primary])
    with pytest.raises(LLMInvokeError) as caught:
        invoke._invoke_api("p", backend, 3, host_audit=host)
    assert caught.value.__cause__ is primary and caught.value.exit_code == 302
    assert len(transport.requests) == len(host.attempts) == 1
    assert bytes(host.attempts[0].raw) == b"refused-body"
    responses = [event for event in host.events if event[0] == "response"]
    assert len(responses) == 1 and responses[0][3].decision == "redirect_cross_origin_refused"
    assert "secret" not in repr(responses[0][3])


@pytest.mark.parametrize("strict", [False, True])
def test_real_stream_write_failure_default_continues_strict_never_retries(
    monkeypatch, host_factory, backend, strict
):
    host = host_factory(strict=strict)
    host.fail_write = 1
    transport = scripted(monkeypatch, [reply(stream=True), reply(stream=True)])
    configured = dataclasses.replace(backend, stream=True)
    if strict:
        with pytest.raises(AuditPersistenceError):
            invoke._invoke_api("p", configured, 2, initial_delay_s=0, host_audit=host)
    else:
        assert invoke._invoke_api("p", configured, 2, host_audit=host).content["findings"] == []
    assert len(transport.requests) == len(host.requests) == 1
    assert host.warnings and host.attempts[0].captured.seal().failed


def test_real_strict_stream_waits_for_shared_writer_ack_before_parser(
    monkeypatch, host_factory, backend
):
    host = host_factory(strict=True)
    entered, release, parsed = threading.Event(), threading.Event(), threading.Event()
    original = invoke._sse_data_payload

    def write(_):
        entered.set()
        assert release.wait(2)

    def decode(line):
        parsed.set()
        return original(line)

    host.write_hook = write
    monkeypatch.setattr(invoke, "_sse_data_payload", decode)
    scripted(monkeypatch, [reply(stream=True)])
    results = []

    def run():
        try:
            results.append(
                invoke._invoke_api("p", dataclasses.replace(backend, stream=True), 3, host_audit=host)
            )
        except BaseException as error:  # noqa: BLE001 - assert exact threaded outcome
            results.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(1)
        assert not parsed.is_set()
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive() and len(results) == 1
    assert isinstance(results[0], invoke.LLMResult)
    assert parsed.is_set() and b"private-thinking" in host.attempts[0].raw


@pytest.mark.parametrize("strict", [False, True])
def test_real_stream_unknown_late_write_never_upgrades_or_retries(
    monkeypatch, host_factory, backend, strict
):
    host = host_factory(strict=strict)
    entered, release = threading.Event(), threading.Event()

    def write(_):
        entered.set()
        assert release.wait(3)

    host.write_hook = write
    transport = scripted(monkeypatch, [reply(stream=True), reply(stream=True)])
    configured = dataclasses.replace(backend, stream=True)
    try:
        if strict:
            with pytest.raises(LLMInvokeError) as caught:
                invoke._invoke_api("p", configured, 1, initial_delay_s=0, host_audit=host)
            # The transport's absolute read deadline can win the race with the
            # strict writer deadline. Keep that primary instead of relabeling it.
            assert isinstance(caught.value, AuditPersistenceError) or caught.value.is_timeout
            assert not caught.value.retryable
        else:
            result = invoke._invoke_api("p", configured, 2, host_audit=host)
            assert result.content["findings"] == []
        assert entered.is_set()
        capture = host.attempts[0].captured
        snapshot = capture.seal()
        assert snapshot.write_uncertain and snapshot.accepted_bytes == 0 and not snapshot.complete
        process = host.session._io
        worker = process._worker
        assert process.unavailable and process.retained_bytes > 0
    finally:
        release.set()
    with process._condition:
        assert process._condition.wait_for(lambda: process._running is None, timeout=1)
    assert capture.seal() is snapshot and snapshot.accepted_bytes == 0
    assert process.unavailable and process._worker is worker and process.retained_bytes == 0
    assert len(transport.requests) == 1


@pytest.mark.parametrize("max_attempts", [1, 5])
def test_redirect_hard_limit_is_ten_independently_of_provider_attempt_budget(
    monkeypatch, host_factory, backend, max_attempts
):
    host = host_factory(strict=True)
    responses = [
        http_error(302, f"hop-{index}".encode(), location=f"/hop-{index + 1}") for index in range(11)
    ]
    transport = scripted(monkeypatch, responses)
    with pytest.raises(LLMInvokeError) as caught:
        invoke._invoke_api("p", backend, 3, max_attempts=max_attempts, host_audit=host)
    assert caught.value.exit_code == 302 and not caught.value.retryable
    assert len(transport.requests) == len(host.requests) == 11
    assert [item.cause for item in host.requests] == ["initial"] + ["redirect"] * 10
    assert host.events[-2][3].decision == "redirect_limit_reached"
    assert all(bytes(item.raw) == f"hop-{i}".encode() for i, item in enumerate(host.attempts))
    assert all(item.finished for item in host.attempts)


def test_request_digest_binds_method_endpoint_and_body_without_retaining_url(host_factory):
    host = host_factory()
    scope = invocation_send_audit.SendAuditScope(host)
    variations = [
        ("GET", "https://unit.invalid/a?secret-one", None),
        ("GET", "https://unit.invalid/b?secret-two", None),
        ("POST", "https://unit.invalid/a?secret-one", b""),
        ("POST", "https://unit.invalid/a?secret-one", b"private-prompt"),
    ]
    for method, url, body in variations:
        request = urllib.request.Request(url, data=body, method=method)  # noqa: S310 - fixed HTTPS test inputs
        scope.start(
            request, cause="initial", backend_name="unit", model="unit", deadline=time.monotonic() + 1
        )
    assert len({item.request_digest for item in host.requests}) == len(variations)
    assert "secret-" not in repr(host.requests) and "private-prompt" not in repr(host.requests)
    assert all(
        set(vars(item))
        == {
            "cause",
            "parent",
            "request_digest",
            "request_bytes",
            "method",
            "backend_name",
            "model",
            "deadline",
        }
        for item in host.requests
    )


def test_nonbool_finish_result_cannot_grant_settled_parent(monkeypatch, host_factory, backend):
    host = host_factory(strict=True)
    scripted(monkeypatch, [reply()])
    monkeypatch.setattr(FakeAttempt, "finish", lambda self, **fields: {"admitted": True})
    with pytest.raises(AuditPersistenceError):
        invoke._invoke_api("p", backend, 3, host_audit=host)
    assert len(host.requests) == 1 and host.requests[0].parent is None


@pytest.mark.parametrize("strict", [False, True])
def test_warning_callback_failure_cannot_replace_policy_result(
    monkeypatch, host_factory, backend, strict
):
    host = host_factory(strict=strict)
    host.fail_start = 1
    scripted(monkeypatch, [reply()])

    def broken(_):
        raise RuntimeError("private warning failure")

    host.warn = broken
    if strict:
        with pytest.raises(AuditPersistenceError):
            invoke._invoke_api("p", backend, 3, host_audit=host)
    else:
        assert invoke._invoke_api("p", backend, 3, host_audit=host).content["findings"] == []


def test_two_public_calls_have_independent_scopes_on_one_shared_worker(
    monkeypatch, host_factory, backend
):
    hosts = [host_factory(strict=True), host_factory(strict=True)]
    barrier = threading.Barrier(2)

    class Transport:
        def open(self, request, *, timeout):
            barrier.wait(1)
            return reply(stream=True)

    monkeypatch.setattr(invocation_send_audit.urllib.request, "build_opener", lambda *_: Transport())
    results = []

    def run(host):
        try:
            results.append(
                invoke.llm_invoke(
                    "p", dataclasses.replace(backend, stream=True), timeout_s=3, host_audit=host
                )
            )
        except BaseException as error:  # noqa: BLE001 - assert exact threaded outcome
            results.append(error)

    threads = [threading.Thread(target=run, args=(host,)) for host in hosts]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2)
    assert not any(thread.is_alive() for thread in threads)
    assert len(results) == 2 and all(isinstance(item, invoke.LLMResult) for item in results)
    assert all(len(host.requests) == 1 and host.requests[0].parent is None for host in hosts)
    assert hosts[0].session._io._worker is hosts[1].session._io._worker


@pytest.mark.parametrize("helper", ["continuation", "headroom", "excerpt_repair", "spent"])
@pytest.mark.parametrize("stop_type", ["AuditPersistenceError", "SendDeadlineError"])
def test_typed_audit_primary_identity_survives_recovery_boundaries(
    monkeypatch, backend, helper, stop_type
):
    primary = getattr(invoke, stop_type)()
    calls = []
    truncated = invoke._TruncatedResponse(
        "cut",
        content=PARTIAL,
        usage_data={"prompt_tokens": 1, "completion_tokens": 1},
        resolved_cap=8,
        kind="truncated",
        retryable=False,
    )

    def fail(*_, **__):
        calls.append("recovery")
        raise primary

    if helper == "spent":

        def initial(*_, **__):
            calls.append("initial")
            raise truncated

        monkeypatch.setattr(invoke, "_invoke_openai", initial)

        def spent(*_, **__):
            raise primary

        monkeypatch.setattr(invoke, "_continue_truncated", spent)

        def operation():
            return invoke._invoke_api("p", backend, 3)
    else:
        monkeypatch.setattr(invoke, "_invoke_openai", fail)
        operations = {
            "continuation": lambda: invoke._continue_truncated("p", backend, "key", 3, truncated, None),
            "headroom": lambda: invoke._retry_with_more_headroom("p", backend, "key", 3, None),
            "excerpt_repair": lambda: invoke._repair_missing_excerpts(
                {"findings": [FINDING]}, "p", backend, 3
            ),
        }
        operation = operations[helper]
    with pytest.raises(type(primary)) as caught:
        operation()
    assert caught.value is primary and len(calls) == 1


def test_nonstream_read_primary_is_not_replaced_by_fatal_capture_seal():
    primary = ValueError("read primary")

    class Capture:
        def chunks(self, *_args, **_kwargs):
            raise primary

        def seal(self, **_):
            raise MemoryError("secondary seal")

    with pytest.raises(ValueError) as caught:
        invoke._read_captured_body(Response(b""), time.monotonic() + 1, "unit", Capture())
    assert caught.value is primary


def test_http_primary_survives_fatal_shared_write(monkeypatch, host_factory, backend):
    host = host_factory(strict=True)

    def fatal_write(_):
        raise MemoryError("audit write fatal")

    host.write_hook = fatal_write
    primary = http_error()
    transport = scripted(monkeypatch, [primary, reply()])
    with pytest.raises(LLMInvokeError) as caught:
        invoke._invoke_api("p", backend, 3, initial_delay_s=0, host_audit=host)
    assert caught.value.__cause__ is primary and caught.value.exit_code == 503
    assert caught.value.retryable and len(transport.requests) == 1


@pytest.mark.parametrize("changes", [{"type": "cli"}, {"format": "anthropic"}, {"format": "vertex"}])
def test_unwired_formats_refuse_explicit_audit_without_claiming_coverage(host_factory, backend, changes):
    host = host_factory()
    with pytest.raises(ValueError, match="OpenAI API only"):
        invoke.llm_invoke("p", dataclasses.replace(backend, **changes), host_audit=host)
    assert not host.requests


@pytest.mark.parametrize(
    "name,fail_at",
    [
        ("initial", 1),
        ("correction", 2),
        ("continuation", 2),
        ("headroom", 4),
        ("excerpt_repair", 2),
    ],
)
def test_strict_secondary_failure_stops_without_mutating_any_primary_fields(
    monkeypatch, host_factory, backend, name, fail_at
):
    host = host_factory(strict=True)
    host.fail_finish = fail_at
    host.finish_error = MemoryError("secondary host fatal")
    responses, _ = scenario(name)
    transport = scripted(monkeypatch, responses + [reply()] * 4)
    primary = LLMInvokeError(
        "provider primary",
        exit_code=503,
        stderr="safe diagnostic",
        duration_s=1.5,
        is_timeout=False,
        retryable=True,
        retry_after=7,
        kind="conn",
        usage=invoke.Usage(2, 3),
        observed_usage=invoke.Usage(4, 5),
    )
    primary.__cause__ = ValueError("existing primary cause")
    fields, args, cause = dict(vars(primary)), primary.args, primary.__cause__
    original, observed = invoke._check_body_error, []

    def check(data, configured):
        observed.append(data)
        if len(observed) == fail_at:
            raise primary
        return original(data, configured)

    monkeypatch.setattr(invoke, "_check_body_error", check)
    with pytest.raises(LLMInvokeError) as caught:
        invoke.llm_invoke("p", backend, timeout_s=3, max_attempts=5, initial_delay_s=0, host_audit=host)
    assert caught.value is primary
    assert vars(primary) == fields and primary.args == args and primary.__cause__ is cause
    assert primary.retryable is True and len(transport.requests) == fail_at


@pytest.mark.parametrize("late_phase", ["failed_admission", "admission", "entered"])
def test_expired_host_control_budget_never_enters_network(
    monkeypatch, host_factory, backend, late_phase
):
    from code_forge import invocation_redirect

    clock = [10.0]
    fake_time = SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda _: None)
    for module in (invoke, invocation_send_audit, invocation_redirect):
        monkeypatch.setattr(module, "time", fake_time)
    host = host_factory()

    def consume_budget():
        clock[0] = 14.0

    if late_phase == "entered":
        host.enter_hook = consume_budget
    else:
        host.start_hook = consume_budget
        if late_phase == "failed_admission":
            host.fail_start = 1
    transport = scripted(monkeypatch, [reply()])
    with pytest.raises(LLMInvokeError) as caught:
        invoke._invoke_api(
            "p", backend, 3, max_attempts=3, retry_timeout=True, initial_delay_s=0, host_audit=host
        )
    assert caught.value.is_timeout and not caught.value.retryable
    assert not transport.requests, "expired host control still entered provider open"
    assert len(host.requests) == 1 and host.requests[0].deadline == 13.0
    if late_phase == "failed_admission":
        assert not host.attempts
    else:
        attempt = host.attempts[0]
        assert attempt.finished and attempt.captured is None
        assert attempt.entered is (late_phase == "entered")
        fields = host.events[-1][2]
        assert fields["outcome"] == "failed"
        assert fields["error_class"] == "SendDeadlineBeforeNetwork"


def test_network_timeout_is_recomputed_after_both_host_controls(monkeypatch, host_factory, backend):
    from code_forge import invocation_redirect

    clock = [10.0]
    fake_time = SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda _: None)
    for module in (invoke, invocation_send_audit, invocation_redirect):
        monkeypatch.setattr(module, "time", fake_time)
    host = host_factory()
    host.start_hook = lambda: clock.__setitem__(0, 10.5)
    host.enter_hook = lambda: clock.__setitem__(0, 11.0)
    transport = scripted(monkeypatch, [urllib.error.URLError("controlled transport")])
    with pytest.raises(LLMInvokeError):
        invoke._invoke_api("p", backend, 3, max_attempts=1, host_audit=host)
    assert transport.requests[0][1] == 2.0
    assert host.requests[0].deadline == 13.0 and host.attempts[0].entered


def test_strict_continuation_truncation_primary_escapes_before_usage_breaker_or_sleep(
    monkeypatch, host_factory, backend
):
    host = host_factory(strict=True)
    host.fail_finish = 2
    transport = scripted(
        monkeypatch,
        [
            reply(PARTIAL, finish="length"),
            reply('{"findings":[{"file":', finish="length"),
            reply(),
        ],
    )
    real_core, primaries, sleeps = invoke._invoke_openai_impl, [], []

    def observe(*args, **kwargs):
        try:
            return real_core(*args, **kwargs)
        except invoke._TruncatedResponse as error:
            primaries.append((error, dict(vars(error)), error.args, error.__cause__))
            raise

    monkeypatch.setattr(invoke, "_invoke_openai_impl", observe)
    monkeypatch.setattr(invoke, "time", SimpleNamespace(monotonic=time.monotonic, sleep=sleeps.append))
    breaker = invoke.TruncationBreaker(threshold=10)
    with pytest.raises(LLMInvokeError) as caught:
        invoke._invoke_api(
            "p", backend, 3, continuation_breaker=breaker, initial_delay_s=0, host_audit=host
        )
    primary, fields, args, cause = primaries[1]
    assert caught.value is primary
    assert vars(primary) == fields and primary.args == args and primary.__cause__ is cause
    assert breaker.count == 1 and not sleeps and len(transport.requests) == 2


@pytest.mark.parametrize("strict", [False, True])
def test_enter_control_failure_keeps_default_send_but_stops_strict(
    monkeypatch, host_factory, backend, strict
):
    host = host_factory(strict=strict)
    host.fail_enter = 1
    transport = scripted(monkeypatch, [reply()])
    if strict:
        with pytest.raises(AuditPersistenceError):
            invoke._invoke_api("p", backend, 3, host_audit=host)
        assert not transport.requests
    else:
        assert invoke._invoke_api("p", backend, 3, host_audit=host).content["findings"] == []
        assert len(transport.requests) == 1
    assert host.warnings == ["audit_send_unavailable"]
    assert not host.attempts[0].entered and host.attempts[0].captured is None


def test_strict_breaker_subclass_primary_does_not_acquire_new_usage_fields(
    monkeypatch, host_factory, backend
):
    scope = invocation_send_audit.SendAuditScope(host_factory(strict=True))
    primary = invoke.TruncationBreakerError(10, 10)
    fields, args, cause = dict(vars(primary)), primary.args, primary.__cause__
    truncated = invoke._TruncatedResponse(
        "cut",
        content=PARTIAL,
        usage_data={"prompt_tokens": 1, "completion_tokens": 1},
        resolved_cap=8,
        kind="truncated",
        retryable=False,
    )

    def fail(*_, **__):
        scope.disabled = True
        raise primary

    monkeypatch.setattr(invoke, "_invoke_openai", fail)
    breaker = invoke.TruncationBreaker(threshold=10)
    with pytest.raises(invoke.TruncationBreakerError) as caught:
        invoke._continue_truncated(
            "p", backend, "key", 3, truncated, None, breaker=breaker, _audit_scope=scope
        )
    assert caught.value is primary
    assert vars(primary) == fields and primary.args == args and primary.__cause__ is cause
    assert breaker.count == 1


@pytest.mark.parametrize("retry_timeout", [False, True])
def test_ordinary_network_timeout_keeps_existing_retry_timeout_policy(
    monkeypatch, host_factory, backend, retry_timeout
):
    host = host_factory()
    primary = TimeoutError("ordinary provider timeout")
    transport = scripted(monkeypatch, [primary, reply()])
    if retry_timeout:
        assert (
            invoke._invoke_api(
                "p", backend, 3, max_attempts=3, retry_timeout=True, initial_delay_s=0, host_audit=host
            ).content["findings"]
            == []
        )
        assert len(transport.requests) == 2
        assert [request.cause for request in host.requests] == ["initial", "retry"]
    else:
        with pytest.raises(LLMInvokeError) as caught:
            invoke._invoke_api("p", backend, 3, max_attempts=3, host_audit=host)
        assert caught.value.__cause__ is primary and caught.value.is_timeout
        assert not caught.value.retryable and len(transport.requests) == 1


@pytest.mark.parametrize(
    "name,late_at",
    [("initial", 1), ("correction", 2), ("continuation", 2), ("headroom", 4), ("excerpt_repair", 2)],
)
def test_all_actual_send_paths_normalize_late_admission_without_recovery(
    monkeypatch, host_factory, backend, name, late_at
):
    from code_forge import invocation_redirect

    host = host_factory()
    responses, _ = scenario(name)
    transport = scripted(monkeypatch, responses + [reply()] * 4)
    skew = [0.0]
    real_monotonic = time.monotonic
    fake_time = SimpleNamespace(monotonic=lambda: real_monotonic() + skew[0], sleep=lambda _: None)
    for module in (invoke, invocation_send_audit, invocation_redirect):
        monkeypatch.setattr(module, "time", fake_time)

    def late_admission():
        if len(host.requests) == late_at:
            skew[0] = 4.0

    host.start_hook = late_admission
    raw_errors, normalized = [], []
    original_open = invocation_send_audit.OpenAiSendTransport.open
    original_dispatch = invoke._invoke_openai

    def observe_open(*args, **kwargs):
        try:
            return original_open(*args, **kwargs)
        except invocation_send_audit.SendDeadlineBeforeNetwork as error:
            raw_errors.append((error, dict(vars(error)), error.args, error.__cause__))
            raise

    def observe_dispatch(*args, **kwargs):
        try:
            return original_dispatch(*args, **kwargs)
        except LLMInvokeError as error:
            if isinstance(error.__cause__, invocation_send_audit.SendDeadlineBeforeNetwork):
                normalized.append((error, dict(vars(error)), error.args, error.__cause__))
            raise

    monkeypatch.setattr(invocation_send_audit.OpenAiSendTransport, "open", observe_open)
    monkeypatch.setattr(invoke, "_invoke_openai", observe_dispatch)
    with pytest.raises(LLMInvokeError) as caught:
        invoke.llm_invoke(
            "p",
            backend,
            timeout_s=3,
            max_attempts=3,
            retry_timeout=True,
            initial_delay_s=0,
            host_audit=host,
        )
    assert type(caught.value) is getattr(invoke, "SendDeadlineError", None)
    assert caught.value.is_timeout and not caught.value.retryable
    assert caught.value.kind == "send_deadline" and caught.value.usage is None
    assert caught.value.observed_usage is None
    assert len(raw_errors) == len(normalized) == 1
    raw, raw_fields, raw_args, raw_cause = raw_errors[0]
    stop, stop_fields, stop_args, stop_cause = normalized[0]
    assert caught.value is stop and stop_cause is raw
    assert vars(raw) == raw_fields and raw.args == raw_args and raw.__cause__ is raw_cause
    assert vars(stop) == stop_fields and stop.args == stop_args and stop.__cause__ is stop_cause
    assert len(host.requests) == late_at and len(transport.requests) == late_at - 1
    assert host.attempts[-1].finished and not host.attempts[-1].entered
    assert host.attempts[-1].captured is None
    assert host.events[-1][2]["error_class"] == "SendDeadlineBeforeNetwork"
