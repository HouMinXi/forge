"""Controlled ACK issuers and pure protocol tests, not storage qualification."""

from dataclasses import replace
import io
import os
import threading
import time
from types import SimpleNamespace
import uuid

import pytest

from code_forge import invocation_audit as audit, invocation_io
from code_forge.invocation_buffered_capture import SharedBufferedCaptureSession
from code_forge.invocation_owner import InvocationOwner, AttemptLease, _Lease
from code_forge.invocation_owner_stage import OwnerStageUnavailable
from code_forge.llm_invoke import LLMInvokeError
from tests.test_invocation_stage_sink import Recorder


class StageRecorder(Recorder):
    def __init__(self):
        super().__init__()
        self.adopt_entered = self.adopt_release = None
        self.adoptions = []
        self.fatal = None

    def begin_stage(self, attempt, *, layer, action_deadline):
        self.calls.append(("begin",))
        return self.begin()

    def adopt_stage(
        self, handle, *, checkpoint, complete, original_bytes, usage, dispatch_state, action_deadline
    ):
        self.adoptions.append((complete, original_bytes, usage, dispatch_state))
        if self.adopt_entered:
            self.adopt_entered.set()
            assert self.adopt_release.wait(2)
        if self.fatal:
            raise self.fatal
        identity = handle.identity
        ref = audit.RawRef(
            identity.artifact_id,
            identity.layer,
            checkpoint.sha256,
            checkpoint.retained_bytes,
            original_bytes,
            not complete,
        )
        return audit.AcquiredAcknowledgement(
            complete,
            identity.observation_id,
            ref,
            None if complete else audit.AuditFault("quota_refused", "acquired", "attempt"),
        )


@pytest.fixture
def stage_setup(monkeypatch):
    monkeypatch.setattr(invocation_io, "_PROCESS", None)
    supervisor = invocation_io.process_audit_io(
        limits=invocation_io.IoLimits(32, 8192, 512, 8), warn=lambda _: None
    )
    session = SharedBufferedCaptureSession(
        supervisor, buffer_bytes=4096, batch_bytes=512, max_delay=0, flush_timeout_s=0.2
    )
    owner = InvocationOwner()
    owner._mutex = threading.RLock()
    owner._pid = os.getpid()
    owner._audit_schema_version = 2
    owner._refused = False
    owner._faults = []
    recorder = StageRecorder()
    lease = AttemptLease(
        "attempt", SimpleNamespace(context=SimpleNamespace(run_id="run")), time.monotonic() + 5, owner
    )
    record = _Lease(lease, recorder, dispatch="entered_api_send")
    owner._leases = {id(lease): record}
    args = dict(supervisor=supervisor, session=session, warn=lambda _: None, limit_bytes=1000)
    yield owner, lease, record, recorder, args
    session.close()
    supervisor.close()
    if supervisor._worker:
        supervisor._worker.join(3)
        assert not supervisor._worker.is_alive()


def completed(stage, body=b"abc"):
    assert b"".join(stage.capture.chunks(io.BytesIO(body))) == body
    return stage.seal()


def test_public_stage_only_adoption_commits_credit(stage_setup):
    owner, lease, record, recorder, args = stage_setup
    stage = lease.begin_capture_stage(**args)
    assert completed(stage).complete
    assert not record.refs and not record.observations and not record.captures
    ack = stage.adopt()
    assert ack.admitted and record.refs == [ack.ref]
    assert record.observations == {ack.observation_id}
    assert record.captures == {"wire": (3, True)} and not record.usage_known
    assert stage.adopt() is ack and len(recorder.adoptions) == 1


@pytest.mark.parametrize("mutation", ["foreign", "v1", "not_entered", "settled", "refused", "duplicate"])
def test_stage_admission_identity_and_lifecycle(stage_setup, mutation):
    owner, lease, record, recorder, args = stage_setup
    if mutation == "foreign":
        lease = replace(lease)
    elif mutation == "v1":
        owner._audit_schema_version = 1
    elif mutation == "not_entered":
        record.dispatch = "not_dispatched"
    elif mutation == "settled":
        record.settlement_started = True
    elif mutation == "refused":
        owner._refused = True
    elif mutation == "duplicate":
        lease.begin_capture_stage(**args)
    prior = len(recorder.calls)
    with pytest.raises((OwnerStageUnavailable, LLMInvokeError)):
        lease.begin_capture_stage(**args)
    assert len(recorder.calls) == prior and not record.refs


def test_known_partial_adoption_preserves_prefix_without_complete_credit(stage_setup):
    owner, lease, record, recorder, args = stage_setup
    recorder.limit = 2
    stage = lease.begin_capture_stage(**args)
    snapshot = completed(stage, b"abcdef")
    assert snapshot.accepted_bytes == 2 and not snapshot.complete
    ack = stage.adopt()
    assert not ack.admitted and ack.ref.retained_bytes == 2 and ack.ref.partial
    assert record.refs == [ack.ref] and owner._refused and not record.usage_known


def test_late_adoption_never_updates_owner(stage_setup):
    owner, lease, record, recorder, args = stage_setup
    lease = replace(lease, action_deadline=time.monotonic() + 0.15)
    record.value = lease
    owner._leases = {id(lease): record}
    stage = lease.begin_capture_stage(**args)
    completed(stage)
    recorder.adopt_entered, recorder.adopt_release = threading.Event(), threading.Event()
    try:
        with pytest.raises(OwnerStageUnavailable):
            stage.adopt()
        assert recorder.adopt_entered.is_set() and not record.refs and not record.observations
        assert args["supervisor"].unavailable
    finally:
        recorder.adopt_release.set()
    with args["supervisor"]._condition:
        assert args["supervisor"]._condition.wait_for(lambda: args["supervisor"]._running is None, 1)
    assert not record.refs and not record.captures


def test_cancel_after_publication_before_credit_commit(stage_setup, monkeypatch):
    owner, lease, record, recorder, args = stage_setup
    stage = lease.begin_capture_stage(**args)
    completed(stage)
    original = stage._request

    def request(phase, **kwargs):
        result = original(phase, **kwargs)
        if phase is invocation_io.AuditPhase.ADOPT:
            stage.cancel()
        return result

    monkeypatch.setattr(stage, "_request", request)
    with pytest.raises(OwnerStageUnavailable):
        stage.adopt()
    assert recorder.adoptions and not record.refs and not record.observations
    stage.cancel()


def test_owner_lock_wait_is_bounded_after_publication(stage_setup):
    owner, lease, record, recorder, args = stage_setup
    stage = lease.begin_capture_stage(**args)
    completed(stage)
    entered, release = threading.Event(), threading.Event()

    def hold():
        with owner._mutex:
            entered.set()
            release.wait(2)

    thread = threading.Thread(target=hold)
    thread.start()
    assert entered.wait(1)
    try:
        start = time.monotonic()
        with pytest.raises(OwnerStageUnavailable):
            stage.adopt()
        assert time.monotonic() - start < 0.5 and not record.refs
    finally:
        release.set()
        thread.join(1)


def test_fatal_adoption_identity_not_replaced(stage_setup):
    owner, lease, record, recorder, args = stage_setup
    stage = lease.begin_capture_stage(**args)
    completed(stage)
    recorder.fatal = MemoryError("controlled")
    with pytest.raises(MemoryError) as caught:
        stage.adopt()
    assert caught.value is recorder.fatal and not record.refs


def test_cancel_running_capture_preserves_unknown_and_charge(stage_setup):
    owner, lease, record, recorder, args = stage_setup
    stage = lease.begin_capture_stage(**args)
    recorder.entered, recorder.release = threading.Event(), threading.Event()
    try:
        stage.capture.append(b"abc")
        assert recorder.entered.wait(1)
        stage.cancel()
        stage.cancel()
        snapshot = stage.capture.seal()
        assert snapshot.failed and snapshot.write_uncertain
        assert args["supervisor"].retained_bytes == 3
        with pytest.raises(OwnerStageUnavailable):
            stage.adopt()
    finally:
        recorder.release.set()
    assert not record.refs


def test_v2_redirect_same_logical_requires_finished_latest_parent():
    ctx = dict(run_id="run", source_hash=None, snapshot_id="snapshot", parent_logical_id=None)
    logical = {"logical": ctx}
    parent = {"start": {"logical_id": "logical"}, "finish": {"outcome": "completed"}}
    attempts = {"first": parent}
    audit._admit_start(
        ctx, "logical", "first", "redirect", attempts, logical, ctx, code="identity_conflict", version=2
    )
    assert "redirect" not in audit._causes(1) and "redirect" in audit._causes(2)
    for version, values in [
        (1, attempts),
        (2, {"first": dict(parent, finish=None)}),
        (2, {**attempts, "later": dict(parent)}),
    ]:
        with pytest.raises(audit._Rejected):
            audit._admit_start(
                ctx,
                "logical",
                "first",
                "redirect",
                values,
                logical,
                ctx,
                code="identity_conflict",
                version=version,
            )


def test_redirect_cannot_be_first_attempt():
    ctx = dict(run_id="run", source_hash=None, snapshot_id="snapshot", parent_logical_id=None)
    with pytest.raises(audit._Rejected):
        audit._admit_start(
            ctx, "new", None, "redirect", {}, {}, ctx, code="identity_conflict", version=2
        )


def test_observation_has_no_unadopted_or_late_usage_credit(stage_setup):
    owner, lease, record, recorder, args = stage_setup
    stage = lease.begin_capture_stage(**args, control_timeout_s=0.03)
    with pytest.raises(OwnerStageUnavailable):
        stage.observe(frame_offset=0, frame_length=1)
    # This local refusal does not prevent settlement of the existing known stage.
    completed(stage)
    stage.adopt()
    entered, release = threading.Event(), threading.Event()

    def observe(**kwargs):
        assert kwargs["source_observation_id"] in record.observations
        entered.set()
        assert release.wait(2)
        return audit.AuditAcknowledgement(True, None)

    recorder.observe_response = observe
    usage = SimpleNamespace(availability="known")
    try:
        with pytest.raises(OwnerStageUnavailable):
            stage.observe(frame_offset=0, frame_length=1, usage=usage)
        assert entered.is_set() and not record.usage_known
    finally:
        release.set()
    with args["supervisor"]._condition:
        assert args["supervisor"]._condition.wait_for(lambda: args["supervisor"]._running is None, 1)
    assert not record.usage_known


def test_foreign_stage_ack_rejected(stage_setup):
    owner, lease, record, recorder, args = stage_setup
    recorder.handle = audit.StageHandle(replace(recorder.handle.identity, attempt_id="foreign"))
    with pytest.raises(OwnerStageUnavailable):
        lease.begin_capture_stage(**args)
    assert not record.refs


def test_controller_methods_cannot_recursively_wait_on_worker(stage_setup):
    owner, lease, record, recorder, args = stage_setup
    stage = lease.begin_capture_stage(**args)

    class Target:
        def audit_io(self, phase, data):
            return stage._request(invocation_io.AuditPhase.SEAL)

    ticket = args["supervisor"].submit(
        Target(), invocation_io.AuditPhase.SEAL, deadline=time.monotonic() + 1
    )
    outcome = args["supervisor"].wait(ticket)
    assert outcome.state == "failed" and not args["supervisor"].unavailable


class OwnerRecorder:
    instances = []

    def __init__(self, root, *, context, monotonic, utc_now, audit_schema_version=1):
        self.version = audit_schema_version
        self.calls = []
        self.manifest = None
        self.instances.append(self)

    def open(self):
        return audit.AuditAcknowledgement(True, None)

    def start(self, **kwargs):
        self.calls.append(kwargs)
        return audit.StartAcknowledgement(True, str(uuid.uuid4()), None)

    def finish(self, **kwargs):
        return audit.AuditAcknowledgement(True, None)

    def publish_input_manifest(self, *, manifest):
        self.manifest = manifest
        return audit.AuditAcknowledgement(True, None)

    def scoped_snapshot(self):
        return SimpleNamespace(input_manifest=self.manifest)

    def finalize(self, **kwargs):
        return audit.AuditAcknowledgement(True, None)


def pure_owner(monkeypatch, version=2):
    from pathlib import Path
    from code_forge import invocation_owner

    monkeypatch.setattr(invocation_owner, "AttemptRecorder", OwnerRecorder)
    OwnerRecorder.instances = []
    ctx = audit.InvocationContext(
        str(uuid.uuid4()), None, str(uuid.uuid4()), None, None, None, None, "review", None
    )
    owner = InvocationOwner.create(
        artifact_root=Path("/tmp/not-used"),
        context=ctx,
        monotonic=time.monotonic,
        utc_now=lambda: "2026-11-04T00:00:00Z",
        audit_schema_version=version,
    )
    return owner, ctx


def test_owner_actual_hop_cause_parent_digest_total_budget_and_deadline(monkeypatch):
    owner, ctx = pure_owner(monkeypatch)
    operation = owner.operation(ctx, cause="initial", max_attempts=3)
    backend = audit.BackendObservation("openai", "fixture", "model", None, None)
    deadline = time.monotonic() + 0.5
    common = dict(backend=backend, request_bytes=0, timeout_s=10, action_deadline=deadline)
    first = owner.begin_attempt(operation, request_digest="a" * 64, **common)
    assert first.action_deadline == deadline
    first.entered("entered_api_send")
    first.finish_once(outcome="completed", error_class=None, duration_s=0.01)
    second = owner.begin_attempt(operation, cause="redirect", request_digest="b" * 64, **common)
    second.entered("entered_api_send")
    second.finish_once(outcome="failed", error_class="http", duration_s=0.01)
    third = owner.begin_attempt(operation, cause="retry", request_digest="c" * 64, **common)
    third.entered("entered_api_send")
    third.finish_once(outcome="completed", error_class=None, duration_s=0.01)
    calls = OwnerRecorder.instances[0].calls
    assert [row["cause"] for row in calls] == ["initial", "redirect", "retry"]
    assert [row["parent_id"] for row in calls] == [None, first.attempt_id, second.attempt_id]
    assert [row["request_digest"] for row in calls] == ["a" * 64, "b" * 64, "c" * 64]
    from code_forge.llm_invoke import LLMInvokeError

    with pytest.raises(LLMInvokeError):
        owner.begin_attempt(operation, cause="redirect", request_digest="d" * 64, **common)
    assert len(calls) == 3


@pytest.mark.parametrize("version", [1, 2])
def test_owner_redirect_rejects_v1_and_unsettled_parent(monkeypatch, version):
    owner, ctx = pure_owner(monkeypatch, version)
    operation = owner.operation(ctx, cause="initial")
    common = dict(
        backend=audit.BackendObservation("openai", "fixture", "model", None, None),
        request_digest="a" * 64,
        request_bytes=0,
        timeout_s=10,
    )
    first = owner.begin_attempt(operation, **common)
    if version == 1:
        first.entered("entered_api_send")
        first.finish_once(outcome="completed", error_class=None, duration_s=0.01)
    from code_forge.llm_invoke import LLMInvokeError

    with pytest.raises(LLMInvokeError):
        owner.begin_attempt(operation, cause="redirect", **common)
    assert len(OwnerRecorder.instances[0].calls) == 1


def test_owner_schema_explicit_and_stable_across_epochs(monkeypatch):
    owner, ctx = pure_owner(monkeypatch)

    def manifest(digest):
        return dict(mode="diff", exact_diff_sha256=digest, diff_bytes=1, files=[], repositories=[])

    owner.publish_input(manifest("a" * 64))
    next_ctx = replace(
        ctx, run_id=str(uuid.uuid4()), snapshot_id=str(uuid.uuid4()), source_hash="b" * 64
    )
    assert owner.advance_source(next_ctx, manifest=manifest("b" * 64)).admitted
    assert [r.version for r in OwnerRecorder.instances] == [2, 2]
    old, _ = pure_owner(monkeypatch, 1)
    assert OwnerRecorder.instances[0].version == 1


def test_v1_replay_rejects_redirect_while_v2_accepts_real_parent_chain():
    from tests.test_invocation_stage_contract import history, add, ATTEMPT, OTHER

    events = history()[:2]
    add(
        events,
        "finish",
        attempt_id=ATTEMPT,
        outcome="completed",
        error_class=None,
        duration_s=0.01,
        dispatch_state="entered_api_send",
    )
    fields = {key: value for key, value in events[1].items() if key in audit._EXTRA["start"]}
    fields.update(
        attempt_id=OTHER, parent_id=ATTEMPT, cause="redirect", request_digest="c" * 64, request_bytes=0
    )
    add(events, "start", **fields)
    assert audit._fold(events)[0][OTHER]["start"]["cause"] == "redirect"
    for event in events:
        event["schema_version"] = 1
    events[0]["reservation"] = audit._reservation_policy(1)
    with pytest.raises(audit._Rejected):
        audit._fold(events)
    events[-1]["cause"] = "retry"
    assert audit._fold(events)[0][OTHER]["start"]["cause"] == "retry"


def test_ack_cannot_commit_after_its_control_deadline(stage_setup, monkeypatch):
    owner, lease, record, recorder, args = stage_setup
    stage = lease.begin_capture_stage(**args, control_timeout_s=0.03)
    completed(stage)
    original = stage._request

    def delayed(phase, **kwargs):
        result = original(phase, **kwargs)
        if phase is invocation_io.AuditPhase.ADOPT:
            time.sleep(0.04)
        return result

    monkeypatch.setattr(stage, "_request", delayed)
    with pytest.raises(OwnerStageUnavailable):
        stage.adopt()
    assert not record.refs and not record.observations


def test_credit_allocation_failure_latches_refusal_without_partial_commit(stage_setup):
    owner, lease, record, recorder, args = stage_setup
    stage = lease.begin_capture_stage(**args)
    completed(stage)
    fatal = MemoryError("controlled allocation failure")

    class Refs(list):
        def __add__(self, other):
            raise fatal

    record.refs = Refs()
    with pytest.raises(MemoryError) as caught:
        stage.adopt()
    assert caught.value is fatal and owner._refused
    assert not record.refs and not record.captures and not record.observations


def test_v2_owner_control_memory_error_keeps_identity_and_refuses(monkeypatch):
    owner, _ = pure_owner(monkeypatch)
    fatal, cause = MemoryError("controlled"), ValueError("original cause")

    def fail():
        raise fatal from cause

    with pytest.raises(MemoryError) as caught:
        owner._call(fail, "open")
    assert caught.value is fatal and fatal.__cause__ is cause and owner._refused


def test_v1_owner_memory_error_preserves_existing_ack_policy(monkeypatch):
    owner, _ = pure_owner(monkeypatch, 1)

    def fail():
        raise MemoryError("controlled")

    ack = owner._call(fail, "open")
    assert not ack.admitted and ack.publication_uncertain and owner._refused


def test_cancel_after_adoption_does_not_revoke_credit_or_refuse_healthy_owner(stage_setup):
    owner, lease, record, recorder, args = stage_setup
    stage = lease.begin_capture_stage(**args)
    completed(stage)
    ack = stage.adopt()
    stage.cancel()
    assert not owner._refused and record.refs == [ack.ref]
    assert stage.adopt() is ack


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("fatal_type", [MemoryError, KeyboardInterrupt, SystemExit])
def test_capture_fatal_refuses_owner_and_keeps_original_identity(stage_setup, strict, fatal_type):
    owner, lease, record, recorder, args = stage_setup
    stage = lease.begin_capture_stage(**args, strict=strict)
    fatal, cause = fatal_type("controlled capture fatal"), ValueError("original cause")

    def broken(**kwargs):
        raise fatal from cause

    recorder.append_stage = lambda *args, **kwargs: broken(**kwargs)
    with pytest.raises(fatal_type) as caught:
        stage.capture.append(b"abc")
        stage.seal()
    assert caught.value is fatal and fatal.__cause__ is cause
    assert owner._refused and not record.refs


def test_public_worker_seal_rejects_before_touching_capture(stage_setup):
    owner, lease, record, recorder, args = stage_setup
    stage = lease.begin_capture_stage(**args)
    entered, release = threading.Event(), threading.Event()

    class Target:
        def audit_io(self, phase, payload):
            entered.set()
            assert release.wait(2)
            return stage.seal()

    supervisor = args["supervisor"]
    ticket = supervisor.submit(Target(), invocation_io.AuditPhase.SEAL, deadline=time.monotonic() + 1)
    assert entered.wait(1)
    stage.capture.append(b"abc")
    release.set()
    assert supervisor.wait(ticket).state == "failed"
    assert not stage._capture._wire._sealed
    snapshot = stage.seal()
    assert snapshot.accepted_bytes == 3 and not snapshot.failed


def test_public_worker_begin_rejects_before_reserving_stage(stage_setup):
    owner, lease, record, recorder, args = stage_setup

    class Target:
        def audit_io(self, phase, payload):
            return lease.begin_capture_stage(**args)

    supervisor = args["supervisor"]
    ticket = supervisor.submit(Target(), invocation_io.AuditPhase.BEGIN, deadline=time.monotonic() + 1)
    assert supervisor.wait(ticket).state == "failed"
    assert not record.stages and not recorder.calls


@pytest.mark.parametrize("strict", [False, True])
def test_shared_assembly_fatal_refuses_owner_on_public_capture(stage_setup, monkeypatch, strict):
    owner, lease, record, recorder, args = stage_setup
    stage = lease.begin_capture_stage(**args, strict=strict)
    fatal = MemoryError("controlled assembly fatal")

    def broken_join(ticket):
        ticket._pieces.clear()
        return None, fatal

    monkeypatch.setattr(invocation_io, "_join_pieces", broken_join)
    with pytest.raises(MemoryError) as caught:
        stage.capture.append(b"abc")
        stage.capture.seal()
    assert caught.value is fatal and owner._refused


@pytest.mark.parametrize("method", ["chunks", "lines"])
@pytest.mark.parametrize("fatal_type", [MemoryError, KeyboardInterrupt, SystemExit])
def test_capture_facade_catches_fatal_during_iteration(stage_setup, monkeypatch, method, fatal_type):
    owner, lease, record, recorder, args = stage_setup
    stage = lease.begin_capture_stage(**args, strict=True)
    fatal, cause = fatal_type("iteration fatal"), ValueError("original cause")
    fatal.__cause__ = cause

    def broken_join(ticket):
        ticket._pieces.clear()
        return None, fatal

    monkeypatch.setattr(invocation_io, "_join_pieces", broken_join)
    iterator = getattr(stage.capture, method)(io.BytesIO(b"abc\n"))
    assert not owner._refused
    with pytest.raises(fatal_type) as caught:
        list(iterator)
    assert caught.value is fatal and fatal.__cause__ is cause and owner._refused


def test_capture_facade_preserves_ordinary_transport_exception(stage_setup):
    owner, lease, record, recorder, args = stage_setup
    stage = lease.begin_capture_stage(**args)
    original = OSError("controlled transport failure")

    class Response:
        def read(self, size):
            raise original

    with pytest.raises(OSError) as caught:
        list(stage.capture.chunks(Response()))
    assert caught.value is original and not owner._refused


def test_capture_facade_seal_rejects_worker_before_touching_capture(stage_setup):
    owner, lease, record, recorder, args = stage_setup
    stage = lease.begin_capture_stage(**args)

    class Target:
        def audit_io(self, phase, payload):
            return stage.capture.seal()

    supervisor = args["supervisor"]
    ticket = supervisor.submit(Target(), invocation_io.AuditPhase.SEAL, deadline=time.monotonic() + 1)
    assert supervisor.wait(ticket).state == "failed"
    assert not stage._capture._wire._sealed
    assert not hasattr(stage.capture, "_wire")
    assert stage.capture.append(b"abc") is None
    snapshot = stage.capture.seal()
    assert isinstance(
        snapshot,
        __import__("code_forge.invocation_stream", fromlist=["CaptureSnapshot"]).CaptureSnapshot,
    )
    assert snapshot.accepted_bytes == 3
    assert stage.capture.seal() is snapshot


@pytest.mark.parametrize("strict", [False, True])
def test_actual_sse_consumer_preserves_capture_assembly_fatal(stage_setup, monkeypatch, strict):
    from code_forge.llm_invoke import _read_sse

    owner, lease, record, recorder, args = stage_setup
    stage = lease.begin_capture_stage(**args, strict=strict)
    fatal, cause = MemoryError("actual SSE capture fatal"), ValueError("cause")
    fatal.__cause__ = cause

    def broken_join(ticket):
        ticket._pieces.clear()
        return None, fatal

    monkeypatch.setattr(invocation_io, "_join_pieces", broken_join)
    body = b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'
    with pytest.raises(MemoryError) as caught:
        _read_sse(io.BytesIO(body), time.monotonic() + 1, "fixture", wire_capture=stage.capture)
    assert caught.value is fatal and fatal.__cause__ is cause and owner._refused
    assert not record.refs


def test_sink_fatal_refuses_before_controller_handoff(stage_setup):
    owner, lease, record, recorder, args = stage_setup
    stage = lease.begin_capture_stage(**args)
    fatal = MemoryError("sink fatal")

    def broken(*args, **kwargs):
        raise fatal

    recorder.append_stage = broken
    stage.capture.append(b"abc")
    supervisor = args["supervisor"]
    with supervisor._condition:
        assert supervisor._condition.wait_for(lambda: bool(supervisor._errors), 1)
    assert owner._refused
    with pytest.raises(MemoryError) as caught:
        stage.capture.seal()
    assert caught.value is fatal
