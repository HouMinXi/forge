"""Provider retry budget with real audit admission; mock HTTP/synthetic recorder.

No native durability or mixed correction/continuation/redirect budget claim.
"""

import copy
import hashlib
import threading
import time
from types import SimpleNamespace

import pytest

from code_forge import llm_invoke as invoke
from code_forge.invocation_io import AuditPhase
from tests import _review_harness
from tests.test_invocation_host import MockRecorder
from tests.test_invocation_r03_host_dispatch import backend as _backend
from tests.test_invocation_send_audit import http_error, reply, scripted

pytest_plugins = ["tests.test_invocation_host"]
backend = _backend
offline_workspace = _review_harness.offline_workspace


def invoke_budget(backend, scope, maximum):
    return invoke.llm_invoke(
        "public retry budget",
        backend,
        timeout_s=3,
        max_attempts=maximum,
        initial_delay_s=0,
        host_audit=scope,
    )


def assert_spare_unconsumed(transport, spare, maximum):
    assert len(transport.requests) == maximum, "spare-send-oracle"
    assert spare.reads == [] and not spare.closed and spare.tell() == 0, "spare-read-oracle"


def assert_budget(rows, raw, sends, sleeps, maximum):
    assert len(rows) == len(raw) == sends == maximum
    assert sleeps == maximum - 1
    assert len({row["artifact"] for row in rows}) == maximum
    assert len({row["attempt"] for row in rows}) == maximum
    assert len({row["logical"] for row in rows}) == 1
    for index, (row, body) in enumerate(zip(rows, raw, strict=True)):
        assert row["cause"] == ("initial" if index == 0 else "retry")
        assert row["parent"] == (None if index == 0 else rows[index - 1]["attempt"])
        assert row["raw"] == body and row["digest"] == hashlib.sha256(body).hexdigest()
        assert row["retained"] == row["original"] == len(body)
        assert row["partial"] is False and row["settled"] is True
        assert row["capture"] == (len(body), True)


@pytest.mark.parametrize("maximum", [1, 3, 6])
@pytest.mark.parametrize("succeed", [False, True], ids=["exhausted-429", "last-success"])
def test_retry_budget_matches_actual_audited_attempts(
    setup, offline_workspace, monkeypatch, backend, maximum, succeed
):
    host, notices = setup[0](control_timeout_s=2)
    responses = [http_error(429, f"public rate-limit attempt {n}".encode()) for n in range(maximum)]
    if succeed:
        responses[-1] = reply()
    raw = [
        bytes(response.raw if hasattr(response, "raw") else response.fp.raw) for response in responses
    ]
    spare = reply()
    transport = scripted(monkeypatch, [*responses, spare])
    sleeps = []
    # Replace this module's binding only, never time.sleep on the shared module.
    monkeypatch.setattr(invoke, "time", SimpleNamespace(monotonic=time.monotonic, sleep=sleeps.append))
    started = []
    original = MockRecorder.start

    def start(recorder, **kwargs):
        assert threading.current_thread() is setup[1]._worker
        assert setup[1]._running._phase is AuditPhase.START
        if started:
            prior = list(host._owner._leases.values())[-1]
            assert prior.settled and len(recorder.finished) == len(started)
            assert kwargs["parent_id"] == prior.value.attempt_id
        started.append(dict(kwargs))
        return original(recorder, **kwargs)

    monkeypatch.setattr(MockRecorder, "start", start)
    # Host allowance is deliberately larger than model M, not the limiting oracle.
    scope = host.scope(max_provider_sends=8, max_redirect_hops=0)
    assert scope._budget == 8 > maximum
    if succeed:
        result = invoke_budget(backend, scope, maximum)
        assert result.content["findings"] == []
        # The successful response reports these tokens; prior 429 cost is unknown.
        assert result.usage.input_tokens == result.usage.output_tokens == 1
        assert result.usage.known_fields == frozenset({"input_tokens", "output_tokens"})
    else:
        with pytest.raises(invoke.LLMInvokeError) as caught:
            invoke_budget(backend, scope, maximum)
        assert caught.value.exit_code == 429 and caught.value.retryable is True
        assert caught.value.__cause__ is responses[-1]
        assert caught.value.observed_usage is None and caught.value.usage is None
    assert_spare_unconsumed(transport, spare, maximum)
    records = list(host._owner._leases.values())
    rows = []
    for record, metadata in zip(records, started, strict=True):
        assert len(record.refs) == 1
        ref = record.refs[0]
        bodies = [
            bytes(body)
            for handle, body in record.recorder.stages.items()
            if handle.identity.attempt_id == record.value.attempt_id
        ]
        assert len(bodies) == 1
        rows.append(
            dict(
                attempt=record.value.attempt_id,
                logical=record.value.operation.logical_id,
                cause=metadata["cause"],
                parent=metadata["parent_id"],
                artifact=ref.artifact_id,
                raw=bodies[0],
                digest=ref.sha256,
                retained=ref.retained_bytes,
                original=ref.original_bytes,
                partial=ref.partial,
                settled=record.settled,
                capture=record.captures["wire"],
            )
        )
    assert_budget(rows, raw, len(transport.requests), len(sleeps), maximum)
    assert len(started) == host._started == host.safe_summary().settled_sends == maximum
    assert len(records[0].recorder.finished) == maximum
    assert [row["outcome"] for row in records[0].recorder.finished] == [
        "completed" if succeed and n == maximum - 1 else "failed" for n in range(maximum)
    ]
    assert [row["error_class"] for row in records[0].recorder.finished] == [
        None if succeed and n == maximum - 1 else "LLMInvokeError" for n in range(maximum)
    ]
    for metadata, (request, _) in zip(started, transport.requests, strict=True):
        body = request.data
        expected_digest = hashlib.sha256(
            request.get_method().encode() + b"\0" + request.full_url.encode() + b"\0" + body
        ).hexdigest()
        assert metadata["request_digest"] == expected_digest
        assert metadata["request_bytes"] == len(body)
    assert not host.failed and notices == []
    for field, value in (("raw", b"merged-or-missing"), ("parent", "wrong-parent")):
        corrupted = copy.deepcopy(rows)
        corrupted[-1][field] = value
        with pytest.raises(AssertionError):
            assert_budget(corrupted, raw, maximum, len(sleeps), maximum)
    with pytest.raises(AssertionError):
        assert_budget(rows, raw, maximum + 1, len(sleeps), maximum)


def test_spare_oracle_rejects_actual_extra_provider_send(setup, offline_workspace, monkeypatch, backend):
    maximum = 6
    # Sensitivity control, not a production mutation: deliberately grant M+1
    # to the same real invocation path and prove it consumes the spare reply.
    host, notices = setup[0](control_timeout_s=2)
    spare = reply()
    responses = [http_error(429, b"public rate limit") for _ in range(maximum)]
    transport = scripted(monkeypatch, [*responses, spare])
    sleeps = []
    monkeypatch.setattr(invoke, "time", SimpleNamespace(monotonic=time.monotonic, sleep=sleeps.append))
    scope = host.scope(max_provider_sends=8, max_redirect_hops=0)
    assert scope._budget == 8 > maximum + 1
    result = invoke_budget(backend, scope, maximum + 1)
    assert result.content["findings"] == []
    assert len(transport.requests) == maximum + 1 and len(sleeps) == maximum
    assert spare.reads and spare.closed
    records = list(host._owner._leases.values())
    assert len(records) == maximum + 1
    assert len({record.value.operation.logical_id for record in records}) == 1
    assert host.safe_summary().settled_sends == maximum + 1
    with pytest.raises(AssertionError, match="spare-send-oracle"):
        assert_spare_unconsumed(transport, spare, maximum)
    # Even if count evidence is interpreted as M+1, consumed bytes are rejected.
    with pytest.raises(AssertionError, match="spare-read-oracle"):
        assert_spare_unconsumed(transport, spare, maximum + 1)
    assert not host.failed and notices == []
