# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""CLI-02 exit code constants + Verdict -> exit mapping.

Phase 1 cli.py had EXIT_PASS / EXIT_FAIL inline. 02-05 promotes them
to a dedicated module and adds CLI_ERROR / BUSY / ESCALATED / DELEGATED.
"""
from __future__ import annotations

from .state import Verdict


EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_CLI_ERROR = 2
EXIT_BUSY = 3
EXIT_ESCALATED = 4
EXIT_DELEGATED = 5
EXIT_TIMEOUT = 6
EXIT_UNRELIABLE = 7


def verdict_to_exit(verdict: Verdict) -> int:
    """Map terminal Verdict to CLI-02 exit code.

    Raises ValueError on Verdict.PENDING (caller bug: HOLD should have
    been consumed by HOLD-resume loop before reaching this mapping).
    """
    if verdict == Verdict.PASS:
        return EXIT_PASS
    if verdict == Verdict.FAIL:
        return EXIT_FAIL
    if verdict == Verdict.ESCALATED:
        return EXIT_ESCALATED
    if verdict == Verdict.DELEGATED:
        return EXIT_DELEGATED
    if verdict == Verdict.UNRELIABLE:
        return EXIT_UNRELIABLE
    if verdict == Verdict.PENDING:
        raise ValueError(
            "verdict_to_exit called with PENDING; HOLD-resume loop "
            "must consume PENDING before terminal mapping"
        )
    raise ValueError("unknown verdict: %r" % verdict)
