# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Pure owner adapter controls, not durable publication qualification."""

import asyncio

import pytest

from code_forge.invocation_audit import AuditAcknowledgement, AuditFault
from code_forge.invocation_owner import InvocationOwner


def _chain(error):
    return tuple(
        descriptor.__get__(error)
        for descriptor in (
            BaseException.__cause__,
            BaseException.__context__,
            BaseException.__suppress_context__,
        )
    )


def _fatal(kind):
    if kind is BaseExceptionGroup:
        return kind("sink", [KeyboardInterrupt("child"), ValueError("child")])
    return kind("sink")


@pytest.mark.parametrize(
    "kind", [KeyboardInterrupt, SystemExit, GeneratorExit, asyncio.CancelledError, BaseExceptionGroup]
)
@pytest.mark.parametrize("chain", ["implicit", "explicit", "suppressed"])
@pytest.mark.parametrize("point", ["healthy", "before", "after"])
@pytest.mark.parametrize(
    "secondary_kind", [RuntimeError, MemoryError, KeyboardInterrupt, asyncio.CancelledError]
)
@pytest.mark.parametrize("phase", ["open", "start", "acquired", "finish", "finalize"])
def test_call_preserves_fatal_sink_exception(kind, chain, point, secondary_kind, phase):
    original = _fatal(kind)
    secondary = secondary_kind("diagnostic")
    sticky = AuditFault("identity_conflict", "start", None)
    appends = []

    class Faults(list):
        def append(self, fault):
            appends.append(fault)
            if point == "before":
                raise secondary
            super().append(fault)
            if point == "after":
                raise secondary

    owner = InvocationOwner.__new__(InvocationOwner)
    owner._faults = Faults([sticky])
    seen = {}
    calls = []

    def sink(**kwargs):
        calls.append(kwargs)
        seen["kwargs"] = kwargs
        try:
            try:
                raise LookupError("context")
            except LookupError:
                if chain == "explicit":
                    raise original from ValueError("cause")
                if chain == "suppressed":
                    raise original from None
                raise original  # noqa: B904 - deliberately establish implicit context
        except BaseException:
            seen["chain"] = _chain(original)
            seen["traceback"] = original.__traceback__
            raise

    with pytest.raises(BaseException) as caught:
        owner._call(sink, phase, attempt_id="attempt", marker="once")
    assert caught.value is original
    assert _chain(original) == seen["chain"]
    tail = original.__traceback__
    while tail is not None and tail is not seen["traceback"]:
        tail = tail.tb_next
    assert tail is seen["traceback"]
    fault = AuditFault("persistence_error", phase, "attempt")
    assert appends == [fault]
    assert owner._faults == [sticky] + ([] if point == "before" else [fault])
    assert calls == [{"attempt_id": "attempt", "marker": "once"}]
    assert seen["kwargs"] == calls[0]


@pytest.mark.parametrize("kind", [RuntimeError, MemoryError, ExceptionGroup])
def test_ordinary_sink_exception_still_returns_uncertain_ack(kind):
    owner = InvocationOwner.__new__(InvocationOwner)
    # This legacy control intentionally exercises the schema-v1 exception policy.
    owner._audit_schema_version = 1
    owner._faults = []
    error = kind("sink", [ValueError()]) if kind is ExceptionGroup else kind("sink")

    def sink():
        raise error

    ack = owner._call(sink, "open")
    assert not ack.admitted
    assert ack.publication_uncertain
    assert ack.fault == AuditFault("persistence_error", "open", None)
    assert owner._faults == [ack.fault]


def test_successful_ack_retains_identity():
    owner = InvocationOwner.__new__(InvocationOwner)
    owner._faults = []
    ack = AuditAcknowledgement(True, None)
    assert owner._call(lambda: ack, "open") is ack
    assert not owner._faults


def test_existing_matching_fault_is_not_appended_again():
    fault = AuditFault("persistence_error", "open", None)

    class NoAppend(list):
        def append(self, value):
            pytest.fail("matching sticky fault must not be appended again")

    owner = InvocationOwner.__new__(InvocationOwner)
    owner._faults = NoAppend([fault])
    original = KeyboardInterrupt("sink")

    def sink():
        raise original

    with pytest.raises(KeyboardInterrupt) as caught:
        owner._call(sink, "open")
    assert caught.value is original
    assert owner._faults == [fault]


def test_fatal_exception_needs_no_attribute_hooks():
    class Hostile(KeyboardInterrupt):
        def __getattribute__(self, name):
            if name in ("__cause__", "__context__", "__suppress_context__", "__traceback__"):
                raise AssertionError("attribute read")
            return super().__getattribute__(name)

        def __setattr__(self, name, value):
            raise AssertionError("attribute write")

    owner = InvocationOwner.__new__(InvocationOwner)
    owner._faults = []
    original = Hostile("sink")

    def diagnostic(*args):
        raise KeyboardInterrupt("diagnostic")

    owner._fault = diagnostic

    def sink():
        raise original

    preserved = False
    try:
        owner._call(sink, "open")
    except BaseException as caught:  # noqa: BLE001 - detect replacement by any fatal diagnostic
        preserved = caught is original
    assert preserved
    assert _chain(original) == (None, None, False)
    assert owner._faults == []
