"""Counterexamples for the timeout tests' independent process-exit oracle."""

import os
import select
import signal
import subprocess
import sys

import pytest

from tests import _descendant_watch as watch


def _command(pidfile):
    return [
        sys.executable,
        "-c",
        "import pathlib, subprocess, sys, time; "
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        f"pathlib.Path({str(pidfile)!r}).write_text(str(p.pid)); "
        "time.sleep(60)",
    ]


def test_live_descendant_fails_and_rescue_stops_it(tmp_path, monkeypatch):
    pidfile = tmp_path / "pid"
    command = _command(pidfile)
    fd = None
    try:
        with watch.watch_descendant(monkeypatch, command, pidfile) as exited:
            parent = subprocess.Popen(command, start_new_session=True)
            pid = int(pidfile.read_text())
            if sys.platform == "linux":
                fd = os.pidfd_open(pid)
            with pytest.raises(AssertionError, match="outlived"):
                exited(timeout=0)
        if fd is not None:
            assert select.select([fd], [], [], 5)[0] == [fd]
        assert parent.poll() is not None
    finally:
        if fd is not None:
            os.close(fd)


def test_exited_unreaped_child_uses_platform_oracle(tmp_path, monkeypatch):
    pidfile = tmp_path / "pid"
    command = _command(pidfile)
    with watch.watch_descendant(monkeypatch, command, pidfile) as exited:
        parent = subprocess.Popen(command, start_new_session=True)
        pid = int(pidfile.read_text())
        # The still-live parent never calls wait: keep its killed child a zombie.
        os.kill(pid, signal.SIGKILL)
        if sys.platform == "linux":
            exited()
        else:
            # Preserve, rather than pretend to fix, the non-Linux oracle.
            with pytest.raises(AssertionError, match="outlived"):
                exited(timeout=0)
        os.kill(pid, 0)  # The previous oracle falsely rejected this state.
        assert parent.poll() is None


@pytest.mark.parametrize("record", [None, "", "not-a-pid", "0", "-1"])
def test_missing_or_invalid_startup_fails_and_reaps_parent(tmp_path, monkeypatch, record):
    pidfile = tmp_path / "pid"
    if record is not None:
        pidfile.write_text(record)
    command = [sys.executable, "-c", "import time; time.sleep(60)"]
    started = []
    real_popen = subprocess.Popen

    def capture(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        started.append(proc)
        return proc

    monkeypatch.setattr(subprocess, "Popen", capture)
    with pytest.raises((AssertionError, ValueError)):
        with watch.watch_descendant(monkeypatch, command, pidfile, startup_timeout=0.1):
            subprocess.Popen(command, start_new_session=True)
    assert len(started) == 1
    assert started[0].poll() is not None


def test_unwatched_command_does_not_supply_launch_evidence(tmp_path, monkeypatch):
    command = [sys.executable, "-c", "raise AssertionError('must not run')"]
    with watch.watch_descendant(monkeypatch, command, tmp_path / "pid") as exited:
        assert subprocess.run([sys.executable, "-c", "pass"], check=True).returncode == 0
        with pytest.raises(AssertionError, match="no live descendant"):
            exited()


@pytest.mark.parametrize("failure", [False, True])
def test_watch_resources_close_on_success_and_assertion(tmp_path, monkeypatch, failure):
    pidfile = tmp_path / "pid"
    command = _command(pidfile)
    opened = []
    closed = []
    original_open = getattr(os, "pidfd_open", None)
    original_close = os.close

    def open_fd(pid):
        fd = original_open(pid)
        opened.append(fd)
        return fd

    def close_fd(fd):
        closed.append(fd)
        original_close(fd)

    monkeypatch.setattr(os, "pidfd_open", open_fd, raising=False)
    monkeypatch.setattr(os, "close", close_fd)
    try:
        with watch.watch_descendant(monkeypatch, command, pidfile):
            parent = subprocess.Popen(command, start_new_session=True)
            if failure:
                raise RuntimeError("body failure")
    except RuntimeError as exc:
        assert failure and str(exc) == "body failure"
    assert parent.poll() is not None
    if sys.platform == "linux":
        assert len(opened) == 1
        assert opened[0] in closed
        with pytest.raises(OSError):
            os.fstat(opened[0])
    else:
        assert opened == []


@pytest.mark.parametrize("exited", [False, True])
def test_non_linux_retains_kill_zero_oracle(tmp_path, monkeypatch, exited):
    from types import SimpleNamespace

    # Select only the helper's compatibility branch; never fake the OS globally.
    monkeypatch.setattr(watch, "sys", SimpleNamespace(platform="darwin"))
    pidfile = tmp_path / "pid"
    pidfile.write_text("4321")
    command = ["owned-fake"]
    proc = SimpleNamespace(poll=lambda: 0, wait=lambda timeout: 0)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: proc)
    calls = []

    def kill(pid, sig):
        calls.append((pid, sig))
        if len(calls) > 1 and sig == 0 and exited:
            raise ProcessLookupError

    monkeypatch.setattr(os, "kill", kill)
    with watch.watch_descendant(monkeypatch, command, pidfile) as assert_exited:
        subprocess.Popen(command)
        if exited:
            assert_exited(timeout=0)
        else:
            with pytest.raises(AssertionError, match="outlived"):
                assert_exited(timeout=0)
    assert calls[:2] == [(4321, 0), (4321, 0)]
    if exited:
        assert calls == [(4321, 0), (4321, 0)], "an exited/recycled PID must not be signalled"
    else:
        assert calls[-1] == (4321, signal.SIGKILL)


def test_non_linux_missing_startup_pid_is_never_signalled(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(watch, "sys", SimpleNamespace(platform="darwin"))
    pidfile = tmp_path / "pid"
    pidfile.write_text("4321")
    command = ["owned-fake"]
    proc = SimpleNamespace(poll=lambda: 0, wait=lambda timeout: 0)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: proc)
    calls = []

    def kill(pid, sig):
        calls.append((pid, sig))
        if len(calls) == 1:
            raise ProcessLookupError
        # A new unrelated process could now own the same PID.
        raise AssertionError("recycled PID was signalled")

    monkeypatch.setattr(os, "kill", kill)
    with pytest.raises(ProcessLookupError):
        with watch.watch_descendant(monkeypatch, command, pidfile):
            subprocess.Popen(command)
    assert calls == [(4321, 0)]


@pytest.mark.parametrize("escaped", [False, True])
@pytest.mark.parametrize("failure", ["pidfd", "missing_pid"])
def test_startup_failure_uses_cooperative_parent_cleanup(tmp_path, monkeypatch, escaped, failure):
    import errno

    # Linux acquisition failure is injected; the real child still has a real
    # owner that handles TERM and waits, including across a session boundary.
    pidfile = tmp_path / "pid"
    command = [
        sys.executable,
        "-c",
        "import pathlib, signal, subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
        f"start_new_session={escaped!r})\n"
        "def stop(signum, frame):\n"
        "    p.kill()\n"
        "    p.wait(timeout=5)\n"
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        f"pathlib.Path({str(pidfile)!r}).write_text(str(p.pid))\n"
        "time.sleep(60)\n",
    ]
    from types import SimpleNamespace

    # The fault branch is selected locally, not by changing the actual OS.
    monkeypatch.setattr(watch, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(signal, "pidfd_send_signal", lambda *args: None, raising=False)

    def unavailable(pid):
        raise OSError(errno.EMFILE, "injected pidfd exhaustion")

    monkeypatch.setattr(os, "pidfd_open", unavailable, raising=False)
    watched_file = pidfile if failure == "pidfd" else tmp_path / "not-published"
    error = OSError if failure == "pidfd" else AssertionError
    message = "injected pidfd exhaustion" if failure == "pidfd" else "never started"
    with pytest.raises(error, match=message):
        with watch.watch_descendant(monkeypatch, command, watched_file, startup_timeout=2):
            subprocess.Popen(command, start_new_session=True)
    # The owner published this independent record, then reaped its child.
    assert pidfile.exists(), "control never established its real descendant"
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)
