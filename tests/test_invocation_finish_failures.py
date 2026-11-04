# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Pure finish-recovery controls, not recorder or publication qualification."""

import asyncio
import os
from types import SimpleNamespace

import pytest

from code_forge.invocation_audit import AuditAcknowledgement, AuditFault
from code_forge.invocation_owner import InvocationOwner


PRIMARY_TYPES = [
    None,
    RuntimeError,
    KeyboardInterrupt,
    MemoryError,
    asyncio.CancelledError,
    ExceptionGroup,
    BaseExceptionGroup,
]
DIAGNOSTICS = [(None, None)] + [
    (kind, point)
    for kind in (OSError, MemoryError, KeyboardInterrupt, asyncio.CancelledError)
    for point in ("before", "after")
]


def _primary(kind):
    if kind is None:
        return None
    if kind is BaseExceptionGroup:
        return kind("primary", [KeyboardInterrupt("child")])
    if kind is ExceptionGroup:
        return kind("primary", [ValueError("child")])
    return kind("primary")


def _tracebacks(error):
    node = error.__traceback__
    while node is not None:
        yield node
        node = node.tb_next


def _case(point, primary, diagnostic_kind, append_point, *, suppressed=True):
    origin = KeyboardInterrupt("original interrupted boundary")
    origin_cause = ValueError("original cause")
    origin_context = LookupError("original context")
    origin.__cause__ = origin_cause
    origin.__context__ = origin_context
    origin.__suppress_context__ = suppressed
    secondary = diagnostic_kind("diagnostic failed") if diagnostic_kind else None
    preexisting = AuditFault("identity_conflict", "start", None)
    calls = []
    boundary_traceback = []

    class Faults(list):
        def append(self, fault):
            calls.append("fault")
            if append_point == "before":
                raise secondary
            super().append(fault)
            if append_point == "after":
                raise secondary

    def interrupt():
        try:
            raise origin
        except KeyboardInterrupt:
            boundary_traceback.append(origin.__traceback__)
            raise

    class Mutex:
        def __enter__(self):
            calls.append("enter")
            if point == "entry":
                interrupt()

        def __exit__(self, *args):
            calls.append("exit")
            interrupt()

    def finish(**kwargs):
        calls.append("finish")
        return AuditAcknowledgement(True, None)

    lease = SimpleNamespace(attempt_id="fixture-attempt")
    record = SimpleNamespace(
        value=lease,
        recorder=SimpleNamespace(finish=finish),
        dispatch="not_dispatched",
        settled=False,
        settlement_started=False,
        terminal=None,
        captures={},
        refs=[],
        usage_known=False,
    )
    owner = InvocationOwner.__new__(InvocationOwner)
    owner._pid = os.getpid()
    owner._mutex = Mutex()
    owner._faults = Faults([preexisting])
    owner._refused = True
    owner._lease = lambda value: record
    with pytest.raises(BaseException) as caught:
        owner._finish(lease, "failed", None, None, primary)
    return SimpleNamespace(
        owner=owner,
        record=record,
        caught=caught.value,
        origin=origin,
        origin_cause=origin_cause,
        origin_context=origin_context,
        secondary=secondary,
        preexisting=preexisting,
        calls=calls,
        boundary_traceback=boundary_traceback,
    )


@pytest.mark.parametrize("kind", PRIMARY_TYPES)
@pytest.mark.parametrize("point", ["entry", "exit"])
@pytest.mark.parametrize("diagnostic_kind,append_point", DIAGNOSTICS)
def test_finish_diagnostic_failure_cannot_replace_entering_error(
    kind, point, diagnostic_kind, append_point
):
    primary = _primary(kind)
    cause = ValueError("primary cause")
    if primary is not None:
        primary.__cause__ = cause
    result = _case(point, primary, diagnostic_kind, append_point)
    assert result.caught is (primary if primary is not None else result.origin)
    if primary is None:
        assert result.caught.__cause__ is result.origin_cause
        assert result.caught.__context__ is result.origin_context
        assert result.caught.__suppress_context__ is True
        assert result.boundary_traceback[0] in list(_tracebacks(result.caught))
    else:
        assert result.caught.__cause__ is cause
        # Match the healthy recovery path: the boundary error is context,
        # never the failed diagnostic. Successful attachment is unchanged.
        assert result.caught.__context__ is result.origin
        assert result.caught.__suppress_context__ is True
        assert hasattr(result.caught, "invocation_observation")
    assert all(node.tb_frame.f_code.co_name != "append" for node in _tracebacks(result.caught))
    assert result.owner._faults[0] is result.preexisting
    added = result.owner._faults[1:]
    assert added == (
        []
        if append_point == "before"
        else [AuditFault("persistence_error", "finish", "fixture-attempt" if point == "exit" else None)]
    )
    assert result.calls == (
        ["enter", "fault"] if point == "entry" else ["enter", "finish", "exit", "fault"]
    )
    assert result.record.settlement_started is (point == "exit")
    assert result.record.settled is (point == "exit")


@pytest.mark.parametrize("suppressed", [False, True])
def test_no_primary_retains_original_suppression_flag(suppressed):
    result = _case("entry", None, MemoryError, "before", suppressed=suppressed)
    assert result.caught is result.origin
    assert result.caught.__cause__ is result.origin_cause
    assert result.caught.__context__ is result.origin_context
    assert result.caught.__suppress_context__ is suppressed


def test_primary_metadata_failure_still_preserves_primary_when_both_diagnostics_fail():
    class HostileError(RuntimeError):
        def __setattr__(self, name, value):
            if name == "invocation_observation":
                raise KeyboardInterrupt("metadata refused")
            super().__setattr__(name, value)

    primary = HostileError("primary")
    cause = ValueError("primary cause")
    primary.__cause__ = cause
    result = _case("entry", primary, MemoryError, "before")
    assert result.caught is primary
    assert primary.__cause__ is cause
    assert primary.__context__ is None  # _raise retains its entering chain on metadata failure.
    assert result.calls == ["enter", "fault", "fault"]
    assert result.owner._faults == [result.preexisting]
