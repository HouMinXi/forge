"""T04 complete JSON syntax errors: real parser/host, mock HTTP/synthetic recorder.

No native persistence, EOF truncation, continuation, or #33 qualification.
"""

import copy
import hashlib
import json
import threading

import pytest

from code_forge.llm_invoke import InvalidJSONResponseError, llm_invoke
from tests import _review_harness
from tests.test_invocation_host import MockRecorder
from code_forge.invocation_io import AuditPhase
from tests.test_invocation_r03_host_dispatch import backend  # noqa: F401
from tests.test_invocation_send_audit import reply, scripted

pytest_plugins = ["tests.test_invocation_host"]
offline_workspace = _review_harness.offline_workspace
BROKEN = ('{"findings":[{"a":1},,{"b":2}]}', '{"findings":[{"c":3},,{"d":4}]}')
GOOD = '{"findings":[],"code_excerpts":[]}'


def assert_evidence(rows, expected_raw, request_count):
    assert request_count == len(rows) == len(expected_raw) == 2
    assert [r["cause"] for r in rows] == ["initial", "correction"]
    assert rows[0]["parent"] is None
    assert rows[1]["parent"] == rows[0]["attempt"]
    assert rows[0]["attempt"] != rows[1]["attempt"]
    assert rows[0]["artifact"] != rows[1]["artifact"]
    for row, raw in zip(rows, expected_raw, strict=True):
        assert row["raw"] == raw
        assert row["sha256"] == hashlib.sha256(raw).hexdigest()
        assert row["retained"] == row["original"] == len(raw)
        assert not row["truncated"]
        assert row["settled"] and row["capture"] == (len(raw), True)


@pytest.mark.parametrize("corrected", [False, True], ids=["double-malformed", "valid-correction"])
def test_complete_malformed_has_one_correction_and_separate_raws(
    setup,
    offline_workspace,
    monkeypatch,
    backend,  # noqa: F811
    corrected,
):
    host, notices = setup[0](control_timeout_s=2)
    responses = [reply(BROKEN[0], finish="stop"), reply(GOOD if corrected else BROKEN[1], finish="stop")]
    expected_raw = [bytes(response.raw) for response in responses]
    transport = scripted(monkeypatch, responses)
    original_start = MockRecorder.start
    starts_observed = []

    def start(recorder, **values):
        assert threading.current_thread() is setup[1]._worker
        assert setup[1]._running._phase is AuditPhase.START
        if starts_observed:
            prior = next(iter(host._owner._leases.values()))
            assert prior.settled and len(recorder.finished) == 1
            assert recorder.finished[0]["outcome"] == "completed"
            assert recorder.finished[0]["error_class"] is None
            assert values["parent_id"] == prior.value.attempt_id
        starts_observed.append(dict(values))
        return original_start(recorder, **values)

    monkeypatch.setattr(MockRecorder, "start", start)
    kwargs = dict(timeout_s=3, max_attempts=5, initial_delay_s=0, host_audit=host.scope())
    if corrected:
        result = llm_invoke("review public fixture", backend, **kwargs)
        assert result.content == {"findings": [], "code_excerpts": []}
    else:
        with pytest.raises(InvalidJSONResponseError) as caught:
            llm_invoke("review public fixture", backend, **kwargs)
        assert caught.value.kind == "no_json" and caught.value.retryable is False
        assert caught.value.raw_response == BROKEN[1]
        assert caught.value.observed_usage.input_tokens == 2
        assert caught.value.observed_usage.output_tokens == 2
    owner = host._owner
    assert owner is not None
    records = list(owner._leases.values())
    assert len(records) == 2
    recorder = records[0].recorder
    assert records[1].recorder is recorder
    # Correction is a child logical operation; only retry/redirect reuse an operation.
    first_operation, second_operation = (record.value.operation for record in records)
    assert first_operation.logical_id != second_operation.logical_id
    assert second_operation.context.parent_logical_id == first_operation.logical_id
    rows = []
    for record, start in zip(records, recorder.started, strict=True):
        assert len(record.refs) == 1
        ref = record.refs[0]
        bodies = [
            bytes(body)
            for handle, body in recorder.stages.items()
            if handle.identity.attempt_id == record.value.attempt_id
        ]
        assert len(bodies) == 1
        rows.append(
            dict(
                attempt=record.value.attempt_id,
                parent=start["parent_id"],
                cause=start["cause"],
                artifact=ref.artifact_id,
                sha256=ref.sha256,
                retained=ref.retained_bytes,
                original=ref.original_bytes,
                truncated=ref.partial,
                raw=bodies[0],
                settled=record.settled,
                capture=record.captures["wire"],
            )
        )
    assert_evidence(rows, expected_raw, len(transport.requests))
    assert len(recorder.finished) == 2
    assert all(row["outcome"] == "completed" and row["error_class"] is None for row in recorder.finished)
    prompts = []
    for start_row, (request, _) in zip(starts_observed, transport.requests, strict=True):
        data = request.data
        expected_digest = hashlib.sha256(
            request.get_method().encode() + b"\0" + request.full_url.encode() + b"\0" + data
        ).hexdigest()
        assert start_row["request_digest"] == expected_digest
        assert start_row["request_bytes"] == len(data)
        prompts.append(json.loads(data)["messages"])
    assert prompts[0] != prompts[1]
    assert BROKEN[0] not in str(prompts[1])
    assert host.safe_summary().settled_sends == 2
    assert not host.failed and notices == []
    merged = copy.deepcopy(rows)
    merged[0]["raw"] += merged[1]["raw"]
    with pytest.raises(AssertionError):
        assert_evidence(merged, expected_raw, 2)
    wrong_parent = copy.deepcopy(rows)
    wrong_parent[1]["parent"] = wrong_parent[1]["attempt"]
    with pytest.raises(AssertionError):
        assert_evidence(wrong_parent, expected_raw, 2)
    with pytest.raises(AssertionError):
        assert_evidence(rows, expected_raw, 3)
