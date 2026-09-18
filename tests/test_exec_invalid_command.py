# SPDX-License-Identifier: Apache-2.0
"""Invalid subprocess arguments must not abort a review."""
import sys

import pytest

from code_forge.exec_falsify import ExecFalsifier, ExecStatus


@pytest.mark.parametrize("command", [
    ["bad\x00command"],
    [sys.executable, "-c", "print('not executed')", "bad\x00argument"],
])
def test_nul_command_returns_unavailable(tmp_path, command):
    evidence = ExecFalsifier(
        manifest={"tier": "declared"}, timeout_seconds=2, command=command,
    ).run(tmp_path)

    assert evidence.status == ExecStatus.UNAVAILABLE
    assert evidence.exit_code is None
    assert evidence.command == command
    assert evidence.reason.strip()
    assert evidence.stdout_tail == ""
    assert evidence.stderr_tail == ""


def test_valid_command_still_executes(tmp_path):
    evidence = ExecFalsifier(
        manifest={"tier": "declared"}, timeout_seconds=2,
        command=[sys.executable, "-c", "print('executed')"],
    ).run(tmp_path)

    assert evidence.status == ExecStatus.PASS_AFTER
    assert evidence.exit_code == 0
    assert evidence.stdout_tail.strip() == "executed"
