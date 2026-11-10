"""R03-02 synthetic-recorder object evidence, not native persistence acceptance.

Real public owner/lease methods run through settle_acquired. This checks in-memory
handoff evidence after a definite ACK, not a journal, crash, or recovery guarantee.
"""

from asyncio import CancelledError
from dataclasses import astuple, replace
import hashlib
from pathlib import Path

import pytest

from code_forge import invocation_owner as module
from code_forge.invocation_audit import (
    AcquiredAcknowledgement,
    AuditAcknowledgement,
    BackendObservation,
    InvocationContext,
    RawRef,
    StartAcknowledgement,
)
from code_forge.llm_invoke import LLMInvokeError


PAYLOAD = b"\x00raw audit evidence\n\xff"
PAYLOAD_SHA256 = "3a22861727f59e7b630c0cbf2af9c028aa37ae864795147e2c797326b0ba3a2e"
ARTIFACT_ID = "00000000-0000-0000-0000-000000000101"
OBSERVATION_ID = "00000000-0000-0000-0000-000000000102"
BACKEND = BackendObservation("openai", "synthetic", "requested", None, None)


class EvidenceRecorder:
    def __init__(self, *args, **kwargs):
        self.calls = []
        self.raw = self.ack = self.published_ref = None
        self.starts = 0

    def open(self):
        self.calls.append("open")
        return AuditAcknowledgement(True, None)

    def start(self, **kwargs):
        self.calls.append("start")
        self.starts += 1
        return StartAcknowledgement(True, f"00000000-0000-0000-0000-{self.starts:012d}", None)

    def settle_acquired(self, *, layer, data, complete, **kwargs):
        self.calls.append("acquired")
        assert layer == "wire" and data == PAYLOAD
        assert complete is True
        self.raw = memoryview(data).tobytes()
        self.published_ref = RawRef(
            ARTIFACT_ID, layer, hashlib.sha256(self.raw).hexdigest(), len(self.raw), len(data), False
        )
        self.ack = AcquiredAcknowledgement(True, OBSERVATION_ID, self.published_ref, None)
        return self.ack


class AppendFault(list):
    def __init__(self, error, *, mode):
        super().__init__()
        self.error, self.mode = error, mode

    def append(self, ref):
        if self.mode == "after":
            super().append(ref)
        elif self.mode == "equal_clone":
            super().append(replace(ref))
        elif self.mode == "duplicate":
            super().append(ref)
            super().append(ref)
        raise self.error


class AddFault(set):
    def __init__(self, error):
        super().__init__()
        self.error = error

    def add(self, observation):
        assert observation == OBSERVATION_ID
        raise self.error


def begin(owner, operation):
    return owner.begin_attempt(
        operation, backend=BACKEND, request_digest="1" * 64, request_bytes=1, timeout_s=10
    )


@pytest.fixture
def setup_owner(monkeypatch):
    monkeypatch.setattr(module, "AttemptRecorder", EvidenceRecorder)
    context = InvocationContext(
        "00000000-0000-0000-0000-000000000201",
        "2" * 64,
        "00000000-0000-0000-0000-000000000202",
        None,
        None,
        None,
        None,
        "review",
        None,
    )
    owner = module.InvocationOwner.create(
        artifact_root=Path("/unused-synthetic-r03-root"),
        context=context,
        monotonic=lambda: 1.0,
        utc_now=lambda: "2026-11-10T00:00:00Z",
        audit_schema_version=2,
    )
    operation = owner.operation(context, cause="initial")
    spare = owner.operation(context, cause="initial")
    lease = begin(owner, operation)
    lease.entered("entered_api_send")
    return owner, context, spare, lease, owner._leases[id(lease)], owner._current.recorder


def assert_sink_evidence(sink):
    """Literal expected fields and bytes, not reconstructed from the owner's state."""
    assert sink.ack.admitted is True
    assert sink.ack.publication_uncertain is False and sink.ack.fault is None
    assert sink.ack.observation_id == OBSERVATION_ID
    assert sink.ack.ref is sink.published_ref
    assert type(sink.published_ref) is RawRef
    assert astuple(sink.published_ref) == (ARTIFACT_ID, "wire", PAYLOAD_SHA256, 21, 21, False)
    assert tuple(type(value) for value in astuple(sink.published_ref)) == (str, str, str, int, int, bool)
    assert type(sink.raw) is bytes and sink.raw == PAYLOAD
    assert len(sink.raw) == 21
    assert hashlib.sha256(sink.raw).hexdigest() == PAYLOAD_SHA256
    assert sink.published_ref.sha256 == hashlib.sha256(sink.raw).hexdigest()


def assert_owner_evidence(record, sink, site):
    assert record.captures == {"wire": (21, True)}
    assert not record.usage_known
    if site == "append_before":
        assert record.refs == []
    else:
        assert len(record.refs) == 1
        assert record.refs[0] is sink.ack.ref
    assert record.observations == set()


def assert_public_retry_cannot_add_evidence(owner, context, spare, lease, record, sink):
    calls, refs, captures = list(sink.calls), tuple(record.refs), dict(record.captures)
    denied = lease.acquired(layer="wire", data=PAYLOAD, complete=True)
    assert denied.admitted is False and denied.ref is None
    assert denied.fault.code == "identity_conflict"
    assert sink.calls == calls
    assert len(record.refs) == len(refs)
    assert all(actual is expected for actual, expected in zip(record.refs, refs, strict=True))
    assert record.captures == captures and record.observations == set()
    with pytest.raises(LLMInvokeError):
        begin(owner, spare)
    assert sink.calls == calls
    with pytest.raises(LLMInvokeError):
        owner.operation(context, cause="initial")
    assert sink.calls == calls
    assert_sink_evidence(sink)


@pytest.mark.parametrize("kind", [RuntimeError, KeyboardInterrupt, SystemExit, CancelledError])
@pytest.mark.parametrize("site", ["append_before", "append_after", "observation_add"])
def test_rawref_survives_post_ack_bookkeeping_failure(setup_owner, kind, site):
    owner, context, spare, lease, record, sink = setup_owner
    primary = kind("controlled owner bookkeeping failure")
    cause, prior_context = ValueError("cause"), LookupError("context")
    primary.__cause__, primary.__context__ = cause, prior_context
    primary.__suppress_context__ = False
    if site.startswith("append"):
        record.refs = AppendFault(primary, mode=site.removeprefix("append_"))
    else:
        record.observations = AddFault(primary)
    assert sink.calls == ["open", "start"]
    with pytest.raises(kind) as caught:
        lease.acquired(layer="wire", data=PAYLOAD, complete=True)
    assert caught.value is primary
    assert primary.__cause__ is cause and primary.__context__ is prior_context
    assert primary.__suppress_context__ is False
    assert sink.calls == ["open", "start", "acquired"]
    assert owner._refused is True
    assert_sink_evidence(sink)
    assert_owner_evidence(record, sink, site)
    assert_public_retry_cannot_add_evidence(owner, context, spare, lease, record, sink)


def test_healthy_rawref_handoff_preserves_sink_object_and_fields(setup_owner):
    owner, context, spare, lease, record, sink = setup_owner
    ack = lease.acquired(layer="wire", data=PAYLOAD, complete=True)
    assert ack is sink.ack and ack.admitted
    assert owner._refused is False
    assert_sink_evidence(sink)
    assert len(record.refs) == 1 and record.refs[0] is sink.published_ref
    assert record.captures == {"wire": (21, True)}
    assert record.observations == {OBSERVATION_ID}
    assert not record.usage_known
    assert begin(owner, spare).attempt_id != lease.attempt_id
    assert sink.calls == ["open", "start", "acquired", "start"]
    denied = lease.acquired(layer="wire", data=PAYLOAD, complete=True)
    assert not denied.admitted and denied.ref is None
    assert sink.calls == ["open", "start", "acquired", "start"]
    assert len(record.refs) == 1 and record.refs[0] is ack.ref
    assert record.captures == {"wire": (21, True)}
    assert record.observations == {OBSERVATION_ID}
    assert_sink_evidence(sink)


@pytest.mark.parametrize("mutation", ["equal_clone", "duplicate"])
def test_owner_evidence_oracle_rejects_bad_reference_handoff(setup_owner, mutation):
    """Negative controls exercise the same oracle against controlled bad storage."""
    owner, context, spare, lease, record, sink = setup_owner
    primary = RuntimeError("fault after mutated container storage")
    record.refs = AppendFault(primary, mode=mutation)
    with pytest.raises(RuntimeError) as caught:
        lease.acquired(layer="wire", data=PAYLOAD, complete=True)
    assert caught.value is primary and owner._refused
    assert_sink_evidence(sink)
    if mutation == "equal_clone":
        assert len(record.refs) == 1 and record.refs[0] == sink.published_ref
        assert record.refs[0] is not sink.published_ref
    else:
        assert len(record.refs) == 2
        assert all(ref is sink.published_ref for ref in record.refs)
    with pytest.raises(AssertionError):
        assert_owner_evidence(record, sink, "append_after")
