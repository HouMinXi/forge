# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Pure owner controls for lost diagnostics, not durable recorder qualification.

The fake recorder acknowledges calls and supplies a clean summary. Assertions
measure owner routing/state only; no artifact, provider or network is opened.
"""

from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

import code_forge.invocation_owner as owner_module
from code_forge.invocation_audit import (
    AcquiredAcknowledgement,
    AuditAcknowledgement,
    AuditFault,
    RawRef,
    StartAcknowledgement,
)
from code_forge.invocation_owner import InvocationOwner
from code_forge.llm_invoke import LLMInvokeError
from tests.test_invocation_owner import (
    Clock,
    begin,
    bound_context,
    call_result,
    complete,
    finish,
    manifest,
    operation,
    usage,
    utc,
)


@pytest.fixture
def setup_owner(monkeypatch):
    recorders = {}

    class Recorder:
        def __init__(self, root, *, context, **kwargs):
            self.context = context
            self.calls = []
            self.manifest = None
            self.state = "active"
            recorders[context.run_id] = self

        def acknowledge(self, method, kwargs):
            self.calls.append((method, kwargs))
            return AuditAcknowledgement(True, None)

        def open(self):
            return self.acknowledge("open", {})

        def publish_input_manifest(self, *, manifest):
            self.manifest = manifest
            return self.acknowledge("publish_input_manifest", {"manifest": manifest})

        def start(self, **kwargs):
            self.acknowledge("start", kwargs)
            return StartAcknowledgement(True, str(len(self.calls)), None)

        def settle_acquired(self, **kwargs):
            self.acknowledge("settle_acquired", kwargs)
            data = kwargs["data"]
            ref = RawRef(
                str(len(self.calls)),
                kwargs["layer"],
                hashlib.sha256(data).hexdigest(),
                len(data),
                len(data),
                False,
            )
            return AcquiredAcknowledgement(True, str(len(self.calls)), ref, None)

        def observe_response(self, **kwargs):
            return self.acknowledge("observe_response", kwargs)

        def finish(self, **kwargs):
            return self.acknowledge("finish", kwargs)

        def finalize(self, *, outcome):
            self.state = outcome
            return self.acknowledge("finalize", {"outcome": outcome})

        def scoped_snapshot(self):
            return SimpleNamespace(
                input_manifest=self.manifest,
                faults=(),
                audit_complete=True,
                summary=dict(
                    state=self.state,
                    observed_api_send_count=0,
                    observed_cli_launch_count=0,
                    unknown_dispatch_count=0,
                    usage={
                        name: dict(known_subtotal=0, unknown_attempt_count=0, invalid_attempt_count=0)
                        for name in (
                            "input_tokens",
                            "output_tokens",
                            "cached_input_tokens",
                            "reasoning_tokens",
                        )
                    },
                    timing=dict(known_work_s=0, unknown_duration_count=0),
                ),
            )

        @staticmethod
        def reopen_scoped(root, run_id):
            return recorders[run_id].scoped_snapshot()

    monkeypatch.setattr(owner_module, "AttemptRecorder", Recorder)
    ctx = bound_context()
    root = Path("unused-pure-owner")
    clock = Clock()
    owner = InvocationOwner.create(artifact_root=root, context=ctx, monotonic=clock, utc_now=utc)
    assert owner.publish_input(manifest(ctx)).admitted
    return owner, root, ctx, clock


class Faults(list):
    def __init__(self, point, before_append=None):
        super().__init__()
        self.point = point
        self.error = MemoryError("fault metadata unavailable")
        self.before_append = before_append

    def append(self, fault):
        if self.before_append is not None:
            self.before_append()
        if self.point == "before":
            raise self.error
        super().append(fault)
        if self.point == "after":
            raise self.error


def lose_manifest(owner, ctx, monkeypatch, *, point="before", result="fatal", after_ack=False):
    faults = Faults(point)
    owner._faults = faults
    original = KeyboardInterrupt("recorder interrupted")
    prior = owner._current.recorder.publish_input_manifest

    def interrupted(**kwargs):
        if after_ack:
            assert prior(**kwargs).admitted
        if result == "denied":
            return AuditAcknowledgement(False, AuditFault("persistence_error", "start", None))
        if result == "ordinary":
            raise OSError("recorder failed")
        raise original

    with monkeypatch.context() as patch:
        patch.setattr(owner._current.recorder, "publish_input_manifest", interrupted)
        caught = call_result(lambda: owner.publish_input(manifest(ctx)))
    assert caught is (original if result == "fatal" else faults.error)
    assert faults == ([] if point == "before" else [AuditFault("persistence_error", "start", None)])
    return faults


def assert_admission_refused(owner, op):
    faults = tuple(owner._faults)
    for action in (lambda: begin(owner, op), lambda: operation(owner)):
        with pytest.raises(LLMInvokeError) as denied:
            action()
        assert denied.value.kind == "audit_persistence_error"
        assert not denied.value.retryable
        assert denied.value.invocation_observation.secondary_faults == faults


@pytest.mark.parametrize("point", ["before", "after"])
@pytest.mark.parametrize("result", ["fatal", "ordinary", "denied"])
@pytest.mark.parametrize("after_ack", [False, True])
def test_lost_fault_metadata_cannot_authorize_new_attempts(
    setup_owner, monkeypatch, point, result, after_ack
):
    owner, root, ctx, _ = setup_owner
    op = operation(owner)
    lose_manifest(owner, ctx, monkeypatch, point=point, result=result, after_ack=after_ack)
    assert_admission_refused(owner, op)
    assert not any(method == "start" for method, _ in owner._current.recorder.calls)


@pytest.mark.parametrize("point", ["before", "after"])
@pytest.mark.parametrize(
    "dispatch", ["entered_api_send", "entered_cli_launch", "possibly_sent", "unknown"]
)
def test_lost_fault_metadata_cannot_enter_a_reserved_attempt(setup_owner, monkeypatch, point, dispatch):
    owner, root, ctx, _ = setup_owner
    lease = begin(owner)
    faults = lose_manifest(owner, ctx, monkeypatch, point=point)
    result = call_result(lambda: lease.entered(dispatch))
    assert result is faults.error
    assert owner._leases[id(lease)].dispatch == "not_dispatched"
    faults.point = None
    assert finish(lease, outcome="cancelled").admitted
    assert owner._current.recorder.calls[-1][1]["dispatch_state"] == "not_dispatched"


def test_refusal_keeps_inflight_evidence_and_failed_settlement_available(setup_owner, monkeypatch):
    owner, root, ctx, _ = setup_owner
    op = operation(owner)
    lease = begin(owner, op)
    lease.entered("entered_api_send")
    faults = lose_manifest(owner, ctx, monkeypatch)
    # The same already-entered dispatch and its response are observations,
    # not authorization for a fresh invocation.
    lease.entered("entered_api_send")
    raw = lease.acquired(layer="wire", data=b"{}", complete=True)
    assert raw.admitted
    assert lease.observe(
        source_observation_id=raw.observation_id,
        frame_offset=0,
        frame_length=2,
        usage=usage(),
        observed_backend=None,
    ).admitted
    assert finish(lease, outcome="failed").admitted
    assert faults == []
    assert_admission_refused(owner, op)
    record = owner._leases[id(lease)]
    assert record.settled and record.terminal["outcome"] == "failed"
    assert record.refs == [raw.ref]
    assert record.usage_known
    assert [name for name, _ in record.recorder.calls[-3:]] == [
        "settle_acquired",
        "observe_response",
        "finish",
    ]


def test_first_completed_settlement_cannot_hide_lost_fault_metadata(setup_owner, monkeypatch):
    owner, root, ctx, _ = setup_owner
    lease = begin(owner)
    lease.entered("entered_api_send")
    assert lease.acquired(layer="wire", data=b"{}", complete=True).admitted
    lose_manifest(owner, ctx, monkeypatch)
    with pytest.raises(LLMInvokeError) as caught:
        finish(lease)
    assert caught.value.invocation_observation.secondary_faults == ()
    assert owner._current.recorder.calls[-1][1]["outcome"] == "completed"
    assert owner._leases[id(lease)].settled
    # An identical replay remains a recovery operation, as before this fix.
    assert finish(lease).admitted


def test_lost_fault_metadata_refuses_epoch_before_constructing_recorder(setup_owner, monkeypatch):
    owner, _, ctx, _ = setup_owner
    lose_manifest(owner, ctx, monkeypatch)
    next_ctx = bound_context(b"next")

    def unexpected(*args, **kwargs):
        pytest.fail("refused owner must not construct a new recorder")

    monkeypatch.setattr(owner_module, "AttemptRecorder", unexpected)
    with pytest.raises(MemoryError):
        owner.advance_source(next_ctx, manifest=manifest(next_ctx, b"next"))
    assert len(owner._epochs) == 1
    assert owner._current.context is ctx


def test_finalize_and_summary_remain_incomplete_without_inventing_faults(setup_owner, monkeypatch):
    owner, root, ctx, _ = setup_owner
    complete(owner)
    lose_manifest(owner, ctx, monkeypatch)
    result = owner.finalize("completed")
    assert result.state == "incomplete"
    assert not result.audit_complete
    assert result.fault_codes == ()
    assert owner._faults == []
    assert owner._current.recorder.calls[-1] == ("finalize", {"outcome": "incomplete"})
    assert owner.finalize("completed") == result


@pytest.mark.parametrize("boundary", ["fault", "ordinary_ack", "denied_ack"])
def test_refusal_precedes_diagnostic_construction(setup_owner, monkeypatch, boundary):
    owner, _, ctx, _ = setup_owner
    op = operation(owner)
    error = MemoryError("diagnostic construction")

    def unavailable(*args, **kwargs):
        raise error

    def ordinary(**kwargs):
        raise OSError("recorder failed")

    class Denied:
        admitted = False

        @property
        def fault(self):
            raise error

    with monkeypatch.context() as patch:
        if boundary == "fault":
            patch.setattr(owner_module, "AuditFault", unavailable)

            def action():
                return owner.operation(bound_context(), cause="initial")
        elif boundary == "ordinary_ack":
            patch.setattr(owner_module, "AuditAcknowledgement", unavailable)
            patch.setattr(owner._current.recorder, "publish_input_manifest", ordinary)

            def action():
                return owner.publish_input(manifest(ctx))
        else:
            patch.setattr(owner._current.recorder, "publish_input_manifest", lambda **kwargs: Denied())

            def action():
                return owner.publish_input(manifest(ctx))

        assert call_result(action) is error
    assert owner._faults == []
    assert_admission_refused(owner, op)


@pytest.mark.parametrize("point", ["before", "after"])
@pytest.mark.parametrize("diagnostic", ["append", "helper"])
def test_primary_metadata_loss_is_sticky_without_replacing_primary(
    setup_owner, monkeypatch, point, diagnostic
):
    owner, _, _, _ = setup_owner
    op = operation(owner)
    lease = begin(owner, op)
    owner._faults = Faults(point)
    if diagnostic == "helper":
        original_fault = owner._fault

        def unavailable(*args):
            if point == "after":
                original_fault(*args)
            raise MemoryError("diagnostic helper")

        monkeypatch.setattr(owner, "_fault", unavailable)

    class Primary(RuntimeError):
        def __setattr__(self, name, value):
            if name == "invocation_observation":
                raise MemoryError("observation attachment")
            super().__setattr__(name, value)

    primary = Primary("entering failure")
    cause = LookupError("entering cause")
    primary.__cause__ = cause
    assert call_result(lambda: finish(lease, outcome="failed", primary_error=primary)) is primary
    assert primary.__cause__ is cause
    assert primary.__context__ is None
    assert primary.__suppress_context__
    assert_admission_refused(owner, op)


@pytest.mark.parametrize("point", ["entry", "exit"])
@pytest.mark.parametrize("with_primary", [False, True])
@pytest.mark.parametrize("diagnostic", ["append", "helper"])
def test_interrupted_recovery_refuses_without_a_second_mutex_wait(
    setup_owner, monkeypatch, point, with_primary, diagnostic
):
    owner, _, _, _ = setup_owner
    op = operation(owner)
    lease = begin(owner, op)
    next_op = operation(owner)
    owner._faults = Faults("before")
    if diagnostic == "helper":

        def unavailable(*args):
            raise MemoryError("diagnostic helper")

        monkeypatch.setattr(owner, "_fault", unavailable)
    mutex = owner._mutex
    error = KeyboardInterrupt("mutex interrupted")
    entries = []

    class InterruptedMutex:
        def __enter__(self):
            entries.append("entry")
            if point == "entry":
                raise error
            return mutex.__enter__()

        def __exit__(self, *args):
            mutex.__exit__(*args)
            raise error

    owner._mutex = InterruptedMutex()
    primary = RuntimeError("primary") if with_primary else None
    try:
        result = call_result(lambda: finish(lease, outcome="failed", primary_error=primary))
    finally:
        owner._mutex = mutex
    assert result is (primary if primary is not None else error)
    assert entries == ["entry"]
    assert owner._faults == []
    assert_admission_refused(owner, next_op)


@pytest.mark.parametrize("reader", ["operation", "begin", "entered"])
def test_waiting_admission_observes_refusal_after_diagnostics_fail(setup_owner, monkeypatch, reader):
    owner, _, ctx, _ = setup_owner
    op = operation(owner)
    reserved = begin(owner) if reader == "entered" else None
    in_diagnostic = threading.Event()
    release_diagnostic = threading.Event()
    reader_started = threading.Event()
    original = KeyboardInterrupt("recorder interrupted")

    def pause_diagnostic():
        in_diagnostic.set()
        assert release_diagnostic.wait(5), "test did not release diagnostic"

    faults = Faults("before", pause_diagnostic)
    owner._faults = faults

    def interrupted(**kwargs):
        raise original

    def read():
        reader_started.set()
        if reader == "operation":
            return operation(owner)
        if reader == "begin":
            return begin(owner, op)
        return reserved.entered("entered_api_send")

    with monkeypatch.context() as patch:
        patch.setattr(owner._current.recorder, "publish_input_manifest", interrupted)
        with ThreadPoolExecutor(max_workers=2) as pool:
            failure = pool.submit(call_result, lambda: owner.publish_input(manifest(ctx)))
            try:
                assert in_diagnostic.wait(5)
                waiting = pool.submit(call_result, read)
                assert reader_started.wait(5)
                assert not waiting.done()
            finally:
                release_diagnostic.set()
            assert failure.result(timeout=5) is original
            result = waiting.result(timeout=5)
    if reader == "entered":
        assert result is faults.error
        assert owner._leases[id(reserved)].dispatch == "not_dispatched"
    else:
        assert isinstance(result, LLMInvokeError)
    assert owner._faults == []


def test_refusal_is_owner_local_and_healthy_primary_can_retry(setup_owner, monkeypatch):
    owner, root, ctx, clock = setup_owner
    other_ctx = bound_context()
    other = InvocationOwner.create(
        artifact_root=root.parent / other_ctx.run_id,
        context=other_ctx,
        monotonic=clock,
        utc_now=utc,
    )
    assert other.publish_input(manifest(other_ctx)).admitted
    op = operation(other)
    lease = begin(other, op)
    primary = RuntimeError("ordinary provider failure")
    assert call_result(lambda: finish(lease, outcome="failed", primary_error=primary)) is primary
    lose_manifest(owner, ctx, monkeypatch)
    complete(other, op)
    assert other.finalize("completed").audit_complete


def test_refusal_precedes_fault_deduplication(setup_owner):
    owner, _, _, _ = setup_owner
    op = operation(owner)
    error = MemoryError("diagnostic membership")

    class UnavailableMembership(list):
        def __contains__(self, value):
            raise error

    owner._faults = UnavailableMembership()
    assert call_result(lambda: owner.operation(bound_context(), cause="initial")) is error
    assert owner._faults == []
    assert_admission_refused(owner, op)
