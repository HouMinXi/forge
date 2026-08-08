# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Signalling a child that was started in its own process group.

Two teardown paths need this and neither can call the other. The MCP job
watchdog is async and sits in a module that imports pydantic; the eval
runner is synchronous and has to stay importable with only the two
runtime dependencies. What they share is the question both must answer
before signalling anything: does this child lead a group, so that a group
signal reaches whatever it shelled out to, or does it share ours, where
the same signal arrives back here. Getting that one wrong is not a
cleanup that failed, it is this process taking SIGKILL from its own hand,
which is how it first presented -- a test runner that vanished mid-run
leaving nothing but an exit code to explain it.

What does not move here is the waiting, because one caller awaits and the
other blocks, and the liveness check, because it does not survive the
trip: asyncio's transport fills in returncode when the child exits, while
Popen.returncode stays None until someone calls poll() or wait(). The
same expression is a live-child test on one type and a constant on the
other, so each caller keeps its own.

The signal names stay here too, rather than being handed in by the
caller. Windows has no SIGKILL, and a name is resolved where it is
written: spelled at a call site it is evaluated while the call is being
assembled, which is before the called function runs and therefore before
any guard inside it can decline. The guard only protects the symbol when
the two sit together, so the two sit together.
"""
from __future__ import annotations

import os
import signal
from typing import Protocol


class Signalable(Protocol):
    """The process members used here, shared by both process types.

    subprocess.Popen and asyncio.subprocess.Process both expose these,
    and on both the terminate/kill pair is an ordinary method rather than
    a coroutine. That is what lets one helper serve an async caller
    without itself becoming one.
    """

    pid: int

    def kill(self) -> None:
        ...

    def terminate(self) -> None:
        ...


def group_of(proc: Signalable) -> int | None:
    """The child's process group, or None when there is none to name.

    Windows has no process group to signal and no os.getpgid to ask with.
    The name is absent from the module rather than failing when called,
    so reaching for it raises AttributeError, which is not an OSError and
    would escape a caller whose contract is to never raise -- turning a
    recorded timeout into a crashed run on the one platform no amount of
    testing here would cover.
    """
    if os.name == "nt":
        return None
    try:
        return os.getpgid(proc.pid)
    except OSError:
        return None


def terminate_group_or_child(proc: Signalable, pgid: int | None) -> None:
    """Ask the child's group to stop if it leads one, else the child.

    Never raises. Both callers run this while already handling a failure,
    so anything raised here replaces the exception that failure needed to
    report, and their own recovery never happens.

    A child that does not lead a group is sharing the caller's, and
    signalling that group would deliver here as well. Such a child is
    signalled on its own instead, which leaves behind whatever it spawned
    rather than taking the caller down along with it -- the same reason
    the group signal is worth aiming for when it is available, since it
    is the only one that reaches what the child shelled out to.
    """
    if pgid is not None and pgid == proc.pid:
        try:
            os.killpg(pgid, signal.SIGTERM)
            return
        except OSError:
            pass
    try:
        proc.terminate()
    except OSError:
        pass


def kill_group_or_child(proc: Signalable, pgid: int | None) -> None:
    """The same, with the signal that cannot be caught or declined.

    Never raises, for the reason above.

    SIGKILL is named inside the group branch and nowhere else. That
    branch is unreachable on Windows, where group_of returns None because
    there is no group to ask about -- and unreachable is what the name
    needs there, because Windows has no SIGKILL to resolve. The other
    branch reaches for proc.kill, which on that platform is proc.terminate
    under another name and is the only kill it has.
    """
    if pgid is not None and pgid == proc.pid:
        try:
            os.killpg(pgid, signal.SIGKILL)
            return
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass
