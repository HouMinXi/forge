"""Linux timeout-test oracle: observe exit, not init's orphan-reaping speed."""

from contextlib import contextmanager
import os
import select
import signal
import subprocess
import sys
import time


@contextmanager
def watch_descendant(monkeypatch, command, pidfile, *, startup_timeout=5.0):
    """Bind a live descendant before the product starts its timeout wait.

    Only the specified launch is intercepted. Its real Popen, wait, timeout,
    and teardown still run. A pidfd remains readable after exit even while
    init has not reaped the zombie, and cannot target a recycled PID.
    """
    linux = sys.platform == "linux"
    if linux:
        assert hasattr(os, "pidfd_open"), "Linux descendant tests require pidfds"
        assert hasattr(signal, "pidfd_send_signal"), "pidfd cleanup support is required"
    real_popen = subprocess.Popen
    child = None
    pidfd = None
    pid = None
    rescue_pid = None
    launches = 0
    owns_group = False

    def start(argv, *args, **kwargs):
        nonlocal child, pidfd, pid, rescue_pid, launches, owns_group
        if argv != command:
            return real_popen(argv, *args, **kwargs)
        launches += 1
        assert launches == 1, "expected one watched launch"
        owns_group = kwargs.get("start_new_session", False)
        child = real_popen(argv, *args, **kwargs)
        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            recorded = pidfile.read_text().strip() if pidfile.exists() else ""
            if recorded:
                parsed = int(recorded)
                assert parsed > 0, "invalid descendant PID"
                pid = parsed
                if linux:
                    pidfd = os.pidfd_open(pid)
                    assert not select.select([pidfd], [], [], 0)[0], "descendant exited during setup"
                else:
                    os.kill(pid, 0)
                    rescue_pid = pid
                return child
            if child.poll() is not None:
                break
            time.sleep(0.01)
        raise AssertionError("descendant never started; test proves nothing")

    def assert_exited(timeout=5.0):
        nonlocal rescue_pid
        assert launches == 1 and pid is not None, "no live descendant was observed"
        if linux:
            assert pidfd is not None
            assert select.select([pidfd], [], [], timeout)[0], "descendant outlived the timeout"
            return
        # Preserve the existing POSIX oracle off Linux. This change only
        # qualifies zombie-aware observation where pidfds are available.
        deadline = time.monotonic() + timeout
        while True:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                rescue_pid = None
                return
            if time.monotonic() >= deadline:
                raise AssertionError("descendant outlived the timeout")
            time.sleep(0.01)

    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "Popen", start)
        try:
            yield assert_exited
        finally:
            # Rescue test-owned processes after an assertion or mutation control.
            # This runs after the oracle, so rescue cannot make a broken product pass.
            try:
                if pidfd is not None:
                    try:
                        signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    finally:
                        os.close(pidfd)
                elif not linux and rescue_pid is not None:
                    try:
                        os.kill(rescue_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            finally:
                if child is not None:
                    if child.poll() is None:
                        # Give a test reviewer its cooperative teardown even
                        # when pidfd acquisition failed before binding its kid.
                        if owns_group:
                            try:
                                os.killpg(child.pid, signal.SIGTERM)
                            except ProcessLookupError:
                                pass
                        else:
                            child.terminate()
                        try:
                            child.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            if owns_group:
                                try:
                                    os.killpg(child.pid, signal.SIGKILL)
                                except ProcessLookupError:
                                    pass
                            child.kill()
                    child.wait(timeout=5)
