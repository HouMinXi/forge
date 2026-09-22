# SPDX-License-Identifier: Apache-2.0
"""Address-space cap must stay under an inherited hard ceiling.

An outer prlimit of 4GiB plus the default 8GiB request used to die in
preexec_fn with "not allowed to raise maximum limit", before mutmut ran.
"""
import os
import resource
import sys

import pytest

from code_forge.mutation import _limit_address_space


@pytest.mark.skipif(sys.platform == "win32", reason="RLIMIT_AS is POSIX")
def test_limit_clamps_when_inherited_hard_ceiling_is_lower():
    if not hasattr(os, "fork"):
        pytest.skip("fork unavailable")
    requested = 8 * 1024**3
    inherited = 64 * 1024**2
    try:
        pid = os.fork()
    except OSError as exc:
        pytest.skip("fork refused: %s" % exc)
    if pid == 0:  # pragma: no cover - child
        try:
            resource.setrlimit(resource.RLIMIT_AS, (inherited, inherited))
            _limit_address_space(requested)
            soft, hard = resource.getrlimit(resource.RLIMIT_AS)
            os._exit(0 if soft == inherited and hard == inherited else 1)
        except (OSError, ValueError):
            os._exit(2)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
