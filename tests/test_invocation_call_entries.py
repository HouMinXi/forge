# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Pure caller routing controls, not durable recorder/publication qualification.

The finish caller has its own recovery diagnostic. These standalone controls use
one-shot diagnostic failure there; persistent failure needs the separate #359 fix.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

import code_forge.invocation_owner as owner_module
from code_forge.invocation_audit import AuditAcknowledgement, StartAcknowledgement, InvocationContext
from code_forge.invocation_owner import InvocationOwner
from code_forge.llm_invoke import LLMInvokeError


ENTRIES = (
    "create.open",
    "publish_input.publish_input_manifest",
    "begin_attempt.start",
    "acquired.settle_acquired",
    "observe.observe_response",
    "finish.finish",
    "advance_source.open",
    "advance_source.publish_input_manifest",
    "advance_source.finalize",
    "finalize.finalize",
)


def _context(label):
    return InvocationContext(label, label, label, None, None, None, None, "review", None)


def _manifest(label):
    return dict(mode="diff", exact_diff_sha256=label, diff_bytes=1, files=[], repositories=[])


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("diagnostic", ["healthy", "before", "after"])
@pytest.mark.parametrize("chain", ["implicit", "explicit", "suppressed"])
def test_actual_call_entries_preserve_fatal_exception(monkeypatch, entry, diagnostic, chain):
    calls = []
    recorders = []
    target = None
    error = KeyboardInterrupt("recorder interruption")
    entering = []

    class Recorder:
        def __init__(self, *args, context, **kwargs):
            self.context = context
            self.manifest = None
            recorders.append(self)

        def invoke(self, method, **kwargs):
            calls.append((self, method, kwargs))
            if (self, method) == target:
                try:
                    raise LookupError("entering context")
                except LookupError:
                    try:
                        if chain == "explicit":
                            raise error from ValueError("entering cause")
                        if chain == "suppressed":
                            raise error from None
                        raise error  # noqa: B904 - deliberately create implicit chaining
                    except BaseException:
                        entering.extend(
                            (
                                error.__cause__,
                                error.__context__,
                                error.__suppress_context__,
                                error.__traceback__,
                            )
                        )
                        raise
            if method == "publish_input_manifest":
                self.manifest = kwargs["manifest"]
            if method == "start":
                return StartAcknowledgement(True, "attempt", None)
            return AuditAcknowledgement(True, None)

        def open(self, **kwargs):
            return self.invoke("open", **kwargs)

        def publish_input_manifest(self, **kwargs):
            return self.invoke("publish_input_manifest", **kwargs)

        def start(self, **kwargs):
            return self.invoke("start", **kwargs)

        def settle_acquired(self, **kwargs):
            return self.invoke("settle_acquired", **kwargs)

        def observe_response(self, **kwargs):
            return self.invoke("observe_response", **kwargs)

        def finish(self, **kwargs):
            return self.invoke("finish", **kwargs)

        def finalize(self, **kwargs):
            return self.invoke("finalize", **kwargs)

        def scoped_snapshot(self):
            return SimpleNamespace(input_manifest=self.manifest)

    monkeypatch.setattr(owner_module, "AttemptRecorder", Recorder)
    context = _context("first")

    def create():
        return InvocationOwner.create(
            artifact_root=Path("unused-pure-recorder"),
            context=context,
            monotonic=lambda: 1.0,
            utc_now=lambda: "2026-11-02T00:00:00Z",
        )

    owner = create()
    owner.publish_input(_manifest("first"))
    operation = owner.operation(context, cause="initial")

    def begin():
        return owner.begin_attempt(
            operation, backend=None, request_digest="digest", request_bytes=1, timeout_s=2
        )

    next_operation = owner.operation(context, cause="initial")
    lease = begin()
    lease.entered("entered_api_send")
    record = owner._leases[id(lease)]
    record.observations.add("source")
    method = entry.split(".")[1]
    target = (recorders[0], method)
    actions = {
        "publish_input": lambda: owner.publish_input(_manifest("first")),
        "begin_attempt": begin,
        "acquired": lambda: lease.acquired(layer="body", data=b"x", complete=True),
        "observe": lambda: lease.observe(
            source_observation_id="source",
            frame_offset=0,
            frame_length=1,
            usage=None,
            observed_backend=None,
        ),
        "finish": lambda: lease.finish_once(outcome="cancelled", error_class=None, duration_s=1.0),
        "advance_source": lambda: owner.advance_source(_context("second"), manifest=_manifest("second")),
        "finalize": lambda: owner.finalize("completed"),
    }
    prefix = entry.split(".")[0]
    if prefix in ("begin_attempt", "advance_source", "finalize"):
        record.settled = True
        record.terminal = {"outcome": "completed"}
    if prefix in ("create", "advance_source") and method != "finalize":
        # The real entry constructs its recorder; select that fresh instance.
        original_init = Recorder.__init__

        def targeted_init(self, *args, **kwargs):
            nonlocal target
            original_init(self, *args, **kwargs)
            target = (self, method)

        monkeypatch.setattr(Recorder, "__init__", targeted_init)
    action = create if prefix == "create" else actions[prefix]
    fault_calls = []
    original_fault = InvocationOwner._fault

    def diagnostic_fault(self, *args):
        fault_calls.append((self, args))
        # _finish's independent recovery diagnostic remains healthy in this
        # standalone slice. The combined #359 regression covers persistent loss.
        fail = diagnostic != "healthy" and not (prefix == "finish" and len(fault_calls) > 1)
        if fail and diagnostic == "before":
            raise MemoryError("diagnostic before append")
        acknowledgement = original_fault(self, *args)
        if fail:
            raise MemoryError("diagnostic after append")
        return acknowledgement

    monkeypatch.setattr(InvocationOwner, "_fault", diagnostic_fault)
    calls.clear()
    with pytest.raises(KeyboardInterrupt) as caught:
        action()
    assert caught.value is error
    assert (error.__cause__, error.__context__, error.__suppress_context__) == tuple(entering[:3])
    tb = error.__traceback__
    while tb is not None and tb is not entering[3]:
        tb = tb.tb_next
    assert tb is entering[3]
    assert sum((recorder, name) == target for recorder, name, _ in calls) == 1
    assert calls[-1][:2] == target
    assert len(fault_calls) == (2 if prefix == "finish" else 1)
    fault_owner = fault_calls[0][0]
    expected_recorded = diagnostic != "before" or prefix == "finish"
    assert bool(fault_owner._faults) is expected_recorded
    if fault_owner._faults:
        assert len(fault_owner._faults) == 1
        assert fault_owner._faults[0].code == "persistence_error"

    # Diagnostic storage may be empty, but a real later admission must still
    # refuse before entering the recorder. Restore diagnostics first so this
    # assertion measures the owner's sticky decision, not another injected loss.
    monkeypatch.setattr(InvocationOwner, "_fault", original_fault)
    target = None
    before = list(calls)
    if prefix != "create":
        with pytest.raises(LLMInvokeError) as denied:
            fault_owner.begin_attempt(
                next_operation, backend=None, request_digest="digest", request_bytes=1, timeout_s=2
            )
        assert denied.value.kind == "audit_persistence_error"
        assert not denied.value.retryable
    with pytest.raises(LLMInvokeError):
        fault_owner.operation(fault_owner._current.context, cause="initial")
    assert calls == before
