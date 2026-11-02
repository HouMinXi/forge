# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Pure owner exception controls; these do not qualify durable publication."""

import asyncio
import traceback

import pytest

from code_forge.invocation_audit import AuditFault
from code_forge.invocation_owner import InvocationOwner


PRIMARY_TYPES = [
    RuntimeError,
    KeyboardInterrupt,
    MemoryError,
    asyncio.CancelledError,
    ExceptionGroup,
    BaseExceptionGroup,
]


def _owner():
    # Exercise only the real owner helper. No recorder or filesystem is opened.
    owner = InvocationOwner.__new__(InvocationOwner)
    owner._faults = [AuditFault("identity_conflict", "start", None)]
    return owner


def _primary(kind, attachment_error=None):
    if attachment_error is not None:

        class Hostile(kind):
            def __setattr__(self, name, value):
                raise attachment_error

        kind = Hostile
    if issubclass(kind, BaseExceptionGroup):
        child = (
            KeyboardInterrupt()
            if issubclass(kind, BaseExceptionGroup) and not issubclass(kind, ExceptionGroup)
            else ValueError("child")
        )
        return kind("primary", [child])
    return kind("primary")


def _chain(error):
    return tuple(
        descriptor.__get__(error)
        for descriptor in (
            BaseException.__cause__,
            BaseException.__context__,
            BaseException.__suppress_context__,
        )
    )


def _enter(error, chain):
    try:
        raise LookupError("entering context")
    except LookupError:
        if chain == "explicit":
            raise error from ValueError("entering cause")
        if chain == "suppressed":
            raise error from None
        raise error  # noqa: B904 -- This control deliberately creates implicit chaining.


@pytest.mark.parametrize("kind", PRIMARY_TYPES)
@pytest.mark.parametrize("point", ["attachment", "observation"])
@pytest.mark.parametrize("chain", ["explicit", "implicit", "suppressed"])
@pytest.mark.parametrize("recording_failure", [None, "before", "after"])
def test_metadata_failure_preserves_entering_exception_chain(kind, point, chain, recording_failure):
    owner = _owner()
    secondary = KeyboardInterrupt("secondary metadata failure")
    error = _primary(kind, secondary if point == "attachment" else None)
    calls = []

    if point == "observation":

        def failed_observation(record):
            raise secondary

        owner._observation = failed_observation
    if recording_failure:
        record_fault = owner._fault

        def failed_recording(*args):
            calls.append(args)
            if recording_failure == "after":
                record_fault(*args)
            raise MemoryError("secondary recording failure")

        owner._fault = failed_recording

    try:
        _enter(error, chain)
    except BaseException:  # noqa: BLE001 -- Inspect the exact fatal or grouped primary under test.
        original = _chain(error)
        original_frames = traceback.extract_tb(error.__traceback__)
        with pytest.raises(BaseException) as caught:
            owner._raise(primary=error)

    assert caught.value is error
    actual = _chain(error)
    assert actual[0] is original[0]
    assert actual[1] is original[1]
    assert actual[2] is original[2]
    frames = traceback.extract_tb(error.__traceback__)
    assert frames[-len(original_frames) :] == original_frames
    assert any(frame.name == "_raise" for frame in frames[: -len(original_frames)])
    assert owner._faults[0] == AuditFault("identity_conflict", "start", None)
    if recording_failure:
        assert calls == [("persistence_error", "finish", None)]
    if recording_failure == "before":
        assert len(owner._faults) == 1  # No invented evidence of a failed append.
    else:
        assert owner._faults[1:] == [AuditFault("persistence_error", "finish", None)]


def test_successful_attachment_keeps_existing_behavior():
    owner = _owner()
    error = RuntimeError("primary")
    cause = ValueError("cause")
    try:
        raise error from cause
    except RuntimeError:
        with pytest.raises(RuntimeError) as caught:
            owner._raise(primary=error)
    assert caught.value is error
    assert error.__cause__ is cause
    assert error.invocation_observation.secondary_faults == tuple(owner._faults)
    assert len(owner._faults) == 1


def test_chain_access_bypasses_hostile_attribute_hooks():
    class HostileError(RuntimeError):
        def __getattribute__(self, name):
            if name in {"__cause__", "__context__", "__suppress_context__", "__traceback__"}:
                raise AssertionError("exception chaining must not invoke a subclass hook")
            return super().__getattribute__(name)

        def __setattr__(self, name, value):
            raise KeyboardInterrupt("metadata denied")

    owner = _owner()
    error = HostileError("primary")
    cause = ValueError("cause")
    try:
        raise error from cause
    except HostileError:
        original = _chain(error)
        with pytest.raises(HostileError) as caught:
            owner._raise(primary=error)
    assert caught.value is error
    assert _chain(error) == original


def test_observation_failure_cannot_rewrite_entering_chain_or_traceback():
    owner = _owner()
    error = RuntimeError("primary")

    def mutate_then_fail(record):
        error.__cause__ = ValueError("replacement")
        error.__context__ = None
        error.__suppress_context__ = False
        error.__traceback__ = None
        raise MemoryError("metadata failure")

    owner._observation = mutate_then_fail
    try:
        _enter(error, "explicit")
    except RuntimeError:
        original = _chain(error)
        original_frames = traceback.extract_tb(error.__traceback__)
        with pytest.raises(RuntimeError) as caught:
            owner._raise(primary=error)
    assert caught.value is error
    assert _chain(error) == original
    assert traceback.extract_tb(error.__traceback__)[-len(original_frames) :] == original_frames
