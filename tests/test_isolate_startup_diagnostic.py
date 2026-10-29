"""#31: a failed sandbox start keeps a bounded child startup diagnostic.

These tests run without cgroups or bubblewrap. The cgroup root is a plain
directory, so the limit files and cgroup.procs are regular files: the real
forked child writes its pid there and the placement check finds it. Each
test then breaks one step of the real start path and reads the error.
"""

import errno
import os
import shutil
import time
from types import SimpleNamespace

import pytest

from code_forge.mutation_engines import isolate
from code_forge.mutation_engines.isolate import (
    IsolationError,
    SandboxSpec,
    Supervisor,
    SupervisorThread,
)

# The real start path forks from a threaded pytest process, as the real
# isolation tests do; the child only execs or calls os._exit.
pytestmark = pytest.mark.filterwarnings("ignore:This process .* is multi-threaded:DeprecationWarning")


@pytest.fixture
def plain_root(tmp_path, monkeypatch):
    """A cgroup root that is an ordinary directory."""
    monkeypatch.setattr(isolate, "verify_isolation_support", lambda root: None)
    monkeypatch.setattr(
        Supervisor,
        "_remove_cgroup",
        lambda self, force=False: shutil.rmtree(self.cgroup_path, ignore_errors=True),
    )
    return tmp_path


def _procs_open(monkeypatch, flags):
    """Open cgroup.procs (absent in a plain directory) with the given flags."""
    real_open = os.open

    def fake_open(path, mode, *args, **kwargs):
        if str(path).endswith("cgroup.procs"):
            return real_open(path, flags | os.O_CREAT, 0o600)
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(isolate.os, "open", fake_open)


def _start_error(root, argv=("/bin/true",)):
    spec = SandboxSpec(
        run_id="diag",
        command=("/bin/true",),
        cwd="/workspace",
        memory_mb=64,
        pids=32,
        workspace_mb=16,
    )
    sup = Supervisor(spec, str(root))
    sup._bwrap_argv = lambda: list(argv)
    thread = SupervisorThread(sup)
    thread.start()
    try:
        with pytest.raises(IsolationError) as info:
            thread.wait_started(30)
    finally:
        thread.join(timeout=30)
        if sup._process is not None:
            sup._process.wait(timeout=30)
    return str(info.value)


def _open_fds():
    return sorted(os.listdir("/proc/self/fd"))


def test_placement_failure_reports_child_stage_and_errno(plain_root, monkeypatch):
    # A read-only cgroup.procs makes the child's pid write fail with EBADF.
    _procs_open(monkeypatch, os.O_RDONLY)
    before = _open_fds()

    message = _start_error(plain_root)

    assert message == (
        "payload process was not placed in the run cgroup "
        "(child exit 127; error stage=place type=OSError errno=%d)" % errno.EBADF
    )
    assert _open_fds() == before
    assert not (plain_root / "forge-diag").exists()


def test_exec_failure_reports_child_stage_and_errno(plain_root, monkeypatch):
    _procs_open(monkeypatch, os.O_WRONLY)
    before = _open_fds()

    message = _start_error(plain_root, argv=("/nonexistent/forge-bwrap",))

    assert message == (
        "payload failed to exec bubblewrap "
        "(error stage=exec type=FileNotFoundError errno=%d)" % errno.ENOENT
    )
    assert _open_fds() == before


def test_unplaced_child_still_running_at_deadline(plain_root, monkeypatch):
    # The placement deadline has passed before the first check, while the
    # child is blocked on the gate and has written no diagnostic.
    _procs_open(monkeypatch, os.O_WRONLY)
    clock = iter([0.0])
    monkeypatch.setattr(
        isolate,
        "time",
        SimpleNamespace(
            monotonic=lambda: next(clock, 100.0),
            sleep=time.sleep,
            monotonic_ns=time.monotonic_ns,
        ),
    )
    before = _open_fds()

    message = _start_error(plain_root)

    assert message == (
        "payload process was not placed in the run cgroup "
        "(child still running at deadline; no startup diagnostic)"
    )
    assert _open_fds() == before


def test_successful_exec_raises_nothing(plain_root, monkeypatch):
    _procs_open(monkeypatch, os.O_WRONLY)
    spec = SandboxSpec(
        run_id="diag",
        command=("/bin/true",),
        cwd="/workspace",
        memory_mb=64,
        pids=32,
        workspace_mb=16,
    )
    sup = Supervisor(spec, str(plain_root))
    sup._bwrap_argv = lambda: ["/bin/true"]
    thread = SupervisorThread(sup)
    thread.start()
    thread.wait_started(30)
    assert sup.wait(timeout=30) == 0
    thread.join(timeout=30)
    sup.teardown()


def test_diagnostic_without_errno_says_none():
    assert isolate._startup_diagnostic("gate", KeyboardInterrupt()) == (
        b"error stage=gate type=KeyboardInterrupt errno=none"
    )


def test_diagnostic_is_bounded_and_ascii():
    long_type = type("\u00e9" + "X" * 400, (Exception,), {})

    raw = isolate._startup_diagnostic("exec", long_type())

    assert len(raw) == isolate._STARTUP_DIAGNOSTIC_LIMIT
    assert raw.startswith(b"error stage=exec type=?XXX")
    raw.decode("ascii")


def test_read_diagnostic_does_not_block_on_open_writer():
    read_fd, write_fd = os.pipe()
    try:
        assert isolate._read_startup_diagnostic(read_fd) == "no startup diagnostic"
        os.write(write_fd, b"error stage=place type=OSError errno=13")
        assert isolate._read_startup_diagnostic(read_fd) == "error stage=place type=OSError errno=13"
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_read_diagnostic_keeps_only_the_limit():
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"\xff" + b"y" * 500)
        text = isolate._read_startup_diagnostic(read_fd)
    finally:
        os.close(read_fd)
        os.close(write_fd)
    assert text == "\ufffd" + "y" * (isolate._STARTUP_DIAGNOSTIC_LIMIT - 1)
