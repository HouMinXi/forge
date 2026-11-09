"""Synthetic-sink fault oracle. No filesystem admission or native qualification.
Real InvocationOwner methods; only recorder and failing container/mutex seam are controlled.
"""

import asyncio
import threading
import pytest
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import uuid

from code_forge import invocation_owner as mod
from code_forge.invocation_audit import (
    AcquiredAcknowledgement,
    AuditAcknowledgement,
    BackendObservation,
    InvocationContext,
    RawRef,
    StartAcknowledgement,
)


class Sink:
    def __init__(self, *args, **kwargs):
        self.calls = []
        self.snapshot_error = None

    def open(self):
        return AuditAcknowledgement(True, None)

    def start(self, **kwargs):
        self.calls.append("start")
        return StartAcknowledgement(True, str(uuid.uuid4()), None)

    def settle_acquired(self, **kwargs):
        self.calls.append("acquired")
        ref = RawRef(str(uuid.uuid4()), "wire", hashlib.sha256(b"{}").hexdigest(), 2, 2, False)
        return AcquiredAcknowledgement(True, str(uuid.uuid4()), ref, None)

    def publish_input_manifest(self, **kwargs):
        self.calls.append("manifest")
        return AuditAcknowledgement(True, None)

    def scoped_snapshot(self):
        if self.snapshot_error is not None:
            error, self.snapshot_error = self.snapshot_error, None
            raise error
        return SimpleNamespace(input_manifest={})

    def finish(self, **kwargs):
        self.calls.append("finish")
        return AuditAcknowledgement(True, None)


class FailList(list):
    def __init__(self, error, after=False):
        super().__init__()
        self.error = error
        self.after = after

    def append(self, value):
        if self.after:
            super().append(value)
        raise self.error


class FailSet(set):
    def __init__(self, error):
        super().__init__()
        self.error = error

    def add(self, value):
        raise self.error


class FailDict(dict):
    def __init__(self, error):
        super().__init__()
        self.error = error

    def __setitem__(self, key, value):
        raise self.error


class ExitFault:
    def __init__(self, lock, error):
        self.lock = lock
        self.error = error

    def __enter__(self):
        return self.lock.__enter__()

    def __exit__(self, *args):
        self.lock.__exit__(*args)
        error, self.error = self.error, None
        if error is not None:
            raise error


BACKEND = BackendObservation("openai", "fixture", "requested", None, None)


def start(owner, op):
    return owner.begin_attempt(
        op, backend=BACKEND, request_digest="1" * 64, request_bytes=1, timeout_s=10
    )


def case(site, cls, after=False, diagnostic_failure=False, cleanup_error=None):
    context = InvocationContext(
        str(uuid.uuid4()), "2" * 64, str(uuid.uuid4()), None, None, None, None, "review", None
    )
    with patch.object(mod, "AttemptRecorder", Sink):
        owner = mod.InvocationOwner.create(
            artifact_root=Path("/unused-synthetic-root"),
            context=context,
            monotonic=lambda: 1.0,
            utc_now=lambda: "2026-11-09T00:00:00Z",
            audit_schema_version=2,
        )
    op = owner.operation(context, cause="initial")
    sink = owner._current.recorder
    error = cls("synthetic primary")
    original_cause, original_context = KeyError("cause"), ValueError("context")
    error.__cause__, error.__context__ = original_cause, original_context
    error.__suppress_context__ = False
    action = None
    if site == "lease_list":
        owner._operations[id(op)].leases = FailList(error, after)

        def action():
            return start(owner, op)
    elif site == "lease_dict":
        owner._leases = FailDict(error)

        def action():
            return start(owner, op)
    elif site == "manifest_snapshot":
        sink.snapshot_error = error

        def action():
            return owner.publish_input({})
    else:
        lease = start(owner, op)
        lease.entered("entered_api_send")
        record = owner._leases[id(lease)]
        if site == "refs_append":
            record.refs = FailList(error, after)
        elif site == "observation_add":
            record.observations = FailSet(error)
        elif site == "finish_mutex_exit":
            owner._mutex = ExitFault(owner._mutex, error)
        if site == "finish_mutex_exit":

            def action():
                return lease.finish_once(outcome="completed", error_class=None, duration_s=0)
        else:

            def action():
                return lease.acquired(layer="wire", data=b"{}", complete=True)

    if cleanup_error is not None:
        owner._mutex = ExitFault(owner._mutex, cleanup_error)
    if diagnostic_failure:

        def broken_diagnostic(*args, **kwargs):
            raise OSError("diagnostic evidence failure")

        owner._fault = broken_diagnostic
    before = len(sink.calls)
    caught = None
    returned = False
    try:
        action()
        returned = True
    except BaseException as exc:  # noqa: BLE001 - Assert exact primary identity.
        caught = exc
    chain_preserved = (
        caught is error
        and error.__cause__ is original_cause
        and error.__context__ is original_context
        and error.__suppress_context__ is False
    )
    fault_call_count = len(sink.calls) - before
    refused_before_probe = owner._refused
    before_probe = len(sink.calls)
    blocked = False
    try:
        next_op = owner.operation(context, cause="initial")
        start(owner, next_op)
    except BaseException:  # noqa: BLE001 - Record refusal without hiding sink calls.
        blocked = True
    return dict(
        site=site,
        after_write=after,
        primary=cls.__name__,
        same_primary=caught is error,
        chain_preserved=chain_preserved,
        cleanup_evidence=(
            cleanup_error is None
            or cleanup_error in getattr(error, "_invocation_audit_cleanup_errors", ())
        ),
        outward_returned=returned,
        sink_calls_at_fault=fault_call_count,
        refused_before_probe=refused_before_probe,
        next_owner_start_blocked=blocked,
        additional_sink_calls=len(sink.calls) - before_probe,
        contract_pass=caught is error
        and chain_preserved
        and not returned
        and fault_call_count == 1
        and refused_before_probe
        and blocked
        and len(sink.calls) == before_probe,
    )


def healthy():
    context = InvocationContext(
        str(uuid.uuid4()), "2" * 64, str(uuid.uuid4()), None, None, None, None, "review", None
    )
    with patch.object(mod, "AttemptRecorder", Sink):
        owner = mod.InvocationOwner.create(
            artifact_root=Path("/unused-synthetic-root"),
            context=context,
            monotonic=lambda: 1.0,
            utc_now=lambda: "2026-11-09T00:00:00Z",
            audit_schema_version=2,
        )
    assert owner.publish_input({}).admitted
    op = owner.operation(context, cause="initial")
    lease = start(owner, op)
    lease.entered("entered_api_send")
    ack = lease.acquired(layer="wire", data=b"{}", complete=True)
    assert ack.admitted and owner._leases[id(lease)].refs == [ack.ref]
    assert owner._leases[id(lease)].observations == {ack.observation_id}
    assert lease.finish_once(outcome="completed", error_class=None, duration_s=0).admitted
    assert not owner._refused
    next_op = owner.operation(context, cause="initial")
    start(owner, next_op)
    assert owner._current.recorder.calls == ["manifest", "start", "acquired", "finish", "start"]
    return {"status": "PASS", "scope": "synthetic sink; real owner public methods"}


@pytest.mark.parametrize(
    "site,after",
    [
        ("refs_append", False),
        ("refs_append", True),
        ("observation_add", False),
        ("lease_list", False),
        ("lease_list", True),
        ("lease_dict", False),
        ("manifest_snapshot", False),
        ("finish_mutex_exit", False),
    ],
)
@pytest.mark.parametrize(
    "cls", [RuntimeError, MemoryError, KeyboardInterrupt, SystemExit, asyncio.CancelledError]
)
@pytest.mark.parametrize("diagnostic_failure", [False, True])
def test_owner_handoff_failure_blocks_next_sink_start(site, after, cls, diagnostic_failure):
    result = case(site, cls, after, diagnostic_failure)
    assert result["contract_pass"], result


def test_owner_handoff_healthy_control():
    assert healthy()["status"] == "PASS"


@pytest.mark.parametrize(
    "site", ["refs_append", "observation_add", "lease_list", "lease_dict", "manifest_snapshot"]
)
@pytest.mark.parametrize(
    "cls", [RuntimeError, MemoryError, KeyboardInterrupt, SystemExit, asyncio.CancelledError]
)
def test_owner_handoff_body_failure_survives_mutex_exit_failure(site, cls):
    result = case(site, cls, cleanup_error=OSError("private exit failure"))
    assert result["contract_pass"] and result["cleanup_evidence"], result


def test_handoff_refusal_precedes_unlock_and_competing_start():
    from code_forge.llm_invoke import LLMInvokeError

    context = InvocationContext(
        str(uuid.uuid4()), "2" * 64, str(uuid.uuid4()), None, None, None, None, "review", None
    )
    with patch.object(mod, "AttemptRecorder", Sink):
        owner = mod.InvocationOwner.create(
            artifact_root=Path("/unused-synthetic-root"),
            context=context,
            monotonic=lambda: 1.0,
            utc_now=lambda: "2026-11-09T00:00:00Z",
            audit_schema_version=2,
        )
    op = owner.operation(context, cause="initial")
    lease = start(owner, op)
    lease.entered("entered_api_send")
    primary = RuntimeError("private handoff failure")
    owner._leases[id(lease)].refs = FailList(primary)
    released, done = threading.Event(), threading.Event()
    results = []
    original_lock = owner._mutex

    class HandoffLock:
        once = True

        def __enter__(self):
            return original_lock.__enter__()

        def __exit__(self, *args):
            original_lock.__exit__(*args)
            if self.once:
                self.once = False
                released.set()
                assert done.wait(2), "contender did not complete"

    owner._mutex = HandoffLock()

    def contender():
        try:
            assert released.wait(2), "body did not unlock"
            next_op = owner.operation(context, cause="initial")
            results.append(start(owner, next_op))
        except BaseException as error:  # noqa: BLE001 - inspect exact competing result
            results.append(error)
        finally:
            done.set()

    worker = threading.Thread(target=contender)
    worker.start()
    try:
        with pytest.raises(RuntimeError) as caught:
            lease.acquired(layer="wire", data=b"{}", complete=True)
        assert caught.value is primary
    finally:
        released.set()
        worker.join(3)
    assert not worker.is_alive()
    assert len(results) == 1 and isinstance(results[0], LLMInvokeError)
    assert owner._current.recorder.calls == ["start", "acquired"]
