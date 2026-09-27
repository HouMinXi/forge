"""Check graceful process-group cleanup and escalation before reaping."""

import os
import selectors
import signal
import subprocess
import sys
from unittest.mock import Mock, call, patch

import pytest

import code_forge.llm_invoke as invoke


@pytest.mark.parametrize("expired", [False, True])
def test_cleanup_signals_group_then_waits(expired):
    proc = Mock(spec=subprocess.Popen)
    proc.pid = 12345
    proc.wait.return_value = 0
    if expired:
        proc.wait.side_effect = [subprocess.TimeoutExpired("child", 5), 0]
    events = Mock()
    events.attach_mock(proc.wait, "wait")
    with patch.object(invoke.os, "killpg") as killpg:
        events.attach_mock(killpg, "signal")
        invoke._kill_tree(proc)

    expected = [call.signal(proc.pid, signal.SIGTERM), call.wait(timeout=5)]
    if expired:
        expected.extend([call.signal(proc.pid, signal.SIGKILL), call.wait()])
    assert events.mock_calls == expected


def test_missing_process_group_is_tolerated():
    proc = Mock(spec=subprocess.Popen)
    proc.pid = 12345
    with patch.object(invoke.os, "killpg", side_effect=ProcessLookupError) as killpg:
        invoke._kill_tree(proc)
    killpg.assert_called_once_with(proc.pid, signal.SIGTERM)


def test_permission_error_is_not_tolerated():
    proc = Mock(spec=subprocess.Popen)
    proc.pid = 12345
    with patch.object(invoke.os, "killpg", side_effect=PermissionError("denied")) as killpg:
        with pytest.raises(PermissionError, match="denied"):
            invoke._kill_tree(proc)
    killpg.assert_called_once_with(proc.pid, signal.SIGTERM)
    proc.wait.assert_not_called()


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX process groups")
def test_cleanup_reaps_its_own_real_session_leader():
    child = (
        "import signal\n"
        "signal.signal(signal.SIGTERM, signal.SIG_DFL)\n"
        "print('ready', flush=True)\n"
        "while True: signal.pause()\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", child],
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert proc.stdout is not None
        with selectors.DefaultSelector() as ready:
            ready.register(proc.stdout, selectors.EVENT_READ)
            assert ready.select(timeout=10), "Child did not signal readiness"
        assert proc.stdout.readline() == b"ready\n"
        assert proc.poll() is None
        assert os.getpgid(proc.pid) == proc.pid
        invoke._kill_tree(proc)
        assert proc.returncode == -signal.SIGTERM
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()
