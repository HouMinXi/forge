"""Bubblewrap sandbox supervisor (spec trust-and-execution boundary).

Each run executes inside its own bubblewrap sandbox: user/pid/network/
uts/ipc namespaces unshared, no network route, a private tmpfs-backed
workspace, and resource limits applied to an owned cgroup v2 child
BEFORE the payload starts.  Limits (memory, pids, zero swap) are set
and read back; a host that cannot apply them maps to the
specification's isolation_unavailable refusal, never a degraded run.

Gate mechanism: the forked child writes its own pid into the run
cgroup and blocks on a gate pipe inside preexec (supervisor code, not
a shell or interpreter in the trusted path); only after the limits are
set and read back does the supervisor open the gate, letting the child
exec bubblewrap.  The payload therefore never runs outside the limits.

Parent-death watchdog: bubblewrap's --die-with-parent binds the death
signal to the thread that forked it, so the supervisor may only be
started from a dedicated long-lived SupervisorThread.  Starting from
any other thread (including a short-lived one) is refused.
"""

import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass

_REQUIRED_CONTROLLERS = ("memory", "pids")
_CGROUP2_MAGIC = "cgroup2fs"


class IsolationUnavailable(Exception):
    """The host cannot provide the required isolation envelope."""

    def __init__(self, detail: str, reason: str = "isolation_unavailable") -> None:
        super().__init__("%s: %s" % (reason, detail))
        self.reason = reason
        self.detail = detail


class IsolationError(Exception):
    """A supervisor usage or lifecycle error."""


def verify_isolation_support(cgroup_root: str) -> None:
    """Raise IsolationUnavailable unless the delegated root is usable."""
    if shutil.which("bwrap") is None:
        raise IsolationUnavailable("bubblewrap (bwrap) binary not found")
    try:
        fs_type = subprocess.run(
            ["stat", "-fc", "%T", cgroup_root],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise IsolationUnavailable("cannot stat cgroup root: %s" % exc) from exc
    if fs_type != _CGROUP2_MAGIC:
        raise IsolationUnavailable(
            "cgroup root %r is not on a cgroup2 mount (got %r)"
            % (cgroup_root, fs_type)
        )
    if not os.path.isdir(cgroup_root):
        raise IsolationUnavailable("cgroup root %r is not a directory" % cgroup_root)
    probe = os.path.join(cgroup_root, ".forge-probe-%d" % os.getpid())
    try:
        os.mkdir(probe)
    except OSError as exc:
        raise IsolationUnavailable(
            "cannot create child cgroup under %r: %s" % (cgroup_root, exc)
        ) from exc
    try:
        for fname in ("memory.max", "pids.max", "memory.swap.max", "cgroup.procs"):
            if not os.path.exists(os.path.join(probe, fname)):
                raise IsolationUnavailable(
                    "delegated cgroup lacks %s (controllers memory+pids required)"
                    % fname
                )
    finally:
        try:
            os.rmdir(probe)
        except OSError:
            pass


@dataclass(frozen=True)
class SandboxSpec:
    """One sandboxed run: command, limits, workspace and environment."""

    run_id: str
    command: tuple[str, ...]
    cwd: str
    memory_mb: int
    pids: int
    workspace_mb: int
    env: tuple[tuple[str, str], ...] = ()
    workspace_host: str = ""
    runtime_root: str | None = None

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("sandbox command must be nonempty")
        if self.memory_mb <= 0 or self.pids <= 0 or self.workspace_mb <= 0:
            raise ValueError("sandbox limits must be positive")


def _write(path: str, value: str) -> None:
    with open(path, "w") as fh:
        fh.write(value)


def _read(path: str) -> str:
    with open(path) as fh:
        return fh.read().strip()


class _ChildProcess:
    """Minimal forked-child handle (poll/wait/kill) without subprocess."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._status: int | None = None
        self._lock = threading.Lock()

    def poll(self) -> int | None:
        # Two threads may reap concurrently (the supervisor thread and a
        # caller of Supervisor.wait); only one waitpid can win.  The loser
        # gets ChildProcessError and must not clobber the winner's status.
        with self._lock:
            if self._status is not None:
                return self._status
            try:
                done, status = os.waitpid(self.pid, os.WNOHANG)
            except ChildProcessError:
                return self._status
            if done == self.pid:
                self._status = os.waitstatus_to_exitcode(status)
            return self._status

    def wait(self, timeout: float | None = None) -> int:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            code = self.poll()
            if code is not None:
                return code
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.pid, timeout)
            time.sleep(0.02)

    def kill(self) -> None:
        try:
            os.kill(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class Supervisor:
    """Owns one sandboxed run and its cgroup; starts from its thread only."""

    def __init__(self, spec: SandboxSpec, cgroup_root: str) -> None:
        self.spec = spec
        self.cgroup_root = cgroup_root
        self.cgroup_path = os.path.join(cgroup_root, "forge-" + spec.run_id)
        self.limits_readback: dict[str, str] = {}
        self.gate_opened_monotonic_ns = 0
        self.payload_pid = 0
        self._process: _ChildProcess | None = None
        self._owner_thread: SupervisorThread | None = None
        self._torn_down = False

    def _bind_thread(self, thread: "SupervisorThread") -> None:
        self._owner_thread = thread

    def _bwrap_argv(self) -> list[str]:
        spec = self.spec
        argv = [
            "bwrap", "--die-with-parent", "--new-session",
            "--unshare-user", "--unshare-pid", "--unshare-net",
            "--unshare-uts", "--unshare-ipc",
            "--clearenv",
        ]
        if spec.runtime_root:
            argv += ["--ro-bind", spec.runtime_root, "/"]
        else:
            for d in ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc"):
                argv += ["--ro-bind", d, d]
        argv += ["--tmpfs", "/workspace"]
        if spec.workspace_host:
            argv += ["--bind", spec.workspace_host, "/workspace"]
        for key, value in spec.env:
            argv += ["--setenv", key, value]
        argv += ["--chdir", spec.cwd, "--"]
        argv += list(spec.command)
        return argv

    def start(self) -> None:
        thread = threading.current_thread()
        if self._owner_thread is None or thread is not self._owner_thread:
            raise IsolationError(
                "supervisor must be started from its dedicated long-lived "
                "supervisor thread (parent-death signal binds to the forking "
                "thread)"
            )
        verify_isolation_support(self.cgroup_root)
        if self._process is not None:
            raise IsolationError("supervisor already started")

        os.mkdir(self.cgroup_path)
        try:
            limits = {
                "memory.max": str(self.spec.memory_mb * 1024 * 1024),
                "pids.max": str(self.spec.pids),
                "memory.swap.max": "0",
            }
            for fname, value in limits.items():
                _write(os.path.join(self.cgroup_path, fname), value)
            for fname, expected in limits.items():
                got = _read(os.path.join(self.cgroup_path, fname))
                if got != expected:
                    raise IsolationUnavailable(
                        "limit readback mismatch for %s: wrote %s read %s"
                        % (fname, expected, got)
                    )
            self.limits_readback = dict(limits)

            gate_r, gate_w = os.pipe()
            status_r, status_w = os.pipe()
            procs_fd = os.open(
                os.path.join(self.cgroup_path, "cgroup.procs"), os.O_WRONLY
            )
            argv = self._bwrap_argv()
            env = {"PATH": "/usr/bin:/bin"}

            # os.fork directly: subprocess.Popen waits for the child's
            # exec confirmation on its internal error pipe, so a child
            # that blocks on the placement gate before exec would
            # deadlock the parent's Popen call.  Forking by hand keeps
            # the gate under our control; status_w is close-on-exec, so
            # EOF means the exec succeeded.
            pid = os.fork()  # noqa: S606 - raw fork is required for the placement gate
            if pid == 0:
                try:
                    os.close(gate_w)
                    os.close(status_r)
                    os.write(procs_fd, str(os.getpid()).encode())
                    os.close(procs_fd)
                    buf = b""
                    while not buf:
                        buf = os.read(gate_r, 1)
                    os.close(gate_r)
                    devnull = os.open(os.devnull, os.O_RDWR)
                    for stdfd in (0, 1, 2):
                        os.dup2(devnull, stdfd)
                    os.execvpe(argv[0], argv, env)  # noqa: S606 - exec into bwrap, no shell
                except BaseException:  # noqa: BLE001 - child must never escape
                    try:
                        os.write(status_w, b"error")
                    finally:
                        os._exit(127)

            os.close(gate_r)
            os.close(procs_fd)
            os.close(status_w)
            self._process = _ChildProcess(pid)
            self.payload_pid = pid

            deadline = time.monotonic() + 10
            placed = False
            while time.monotonic() < deadline:
                procs = _read(os.path.join(self.cgroup_path, "cgroup.procs"))
                if str(pid) in procs.split():
                    placed = True
                    break
                if self._process.poll() is not None:
                    break
                time.sleep(0.02)
            if not placed:
                raise IsolationError(
                    "payload process was not placed in the run cgroup"
                )

            self.gate_opened_monotonic_ns = time.monotonic_ns()
            os.write(gate_w, b"1")
            os.close(gate_w)
            status = b""
            while True:
                chunk = os.read(status_r, 64)
                if not chunk:
                    break
                status += chunk
            os.close(status_r)
            if status:
                raise IsolationError("payload failed to exec bubblewrap")
        except BaseException:  # noqa: BLE001 - cleanup must run on any failure
            try:
                if self._process is not None and self._process.poll() is None:
                    self._process.kill()
            finally:
                self._remove_cgroup(force=True)
            raise

    def wait(self, timeout: float | None = None) -> int:
        if self._process is None:
            raise IsolationError("supervisor not started")
        return self._process.wait(timeout=timeout)

    def _remove_cgroup(self, force: bool = False) -> None:
        kill_file = os.path.join(self.cgroup_path, "cgroup.kill")
        if os.path.exists(kill_file):
            try:
                _write(kill_file, "1")
            except OSError:
                pass
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                os.rmdir(self.cgroup_path)
                return
            except OSError:
                if not force:
                    time.sleep(0.1)
                else:
                    time.sleep(0.1)

    def teardown(self) -> None:
        if self._torn_down:
            return
        self._torn_down = True
        if self._process is not None and self._process.poll() is None:
            self._process.kill()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        self._remove_cgroup()


class SupervisorThread(threading.Thread):
    """Dedicated long-lived thread that owns a Supervisor lifecycle."""

    def __init__(self, supervisor: Supervisor) -> None:
        super().__init__(name="forge-supervisor-" + supervisor.spec.run_id)
        self._supervisor = supervisor
        self._ready = threading.Event()
        self._error: BaseException | None = None
        supervisor._bind_thread(self)

    def run(self) -> None:
        try:
            self._supervisor.start()
            self._ready.set()
            if self._supervisor._process is not None:
                self._supervisor._process.wait()
        except BaseException as exc:  # noqa: BLE001 - deliver any failure to wait_started
            self._error = exc
            self._ready.set()

    def wait_started(self, timeout: float) -> None:
        if not self._ready.wait(timeout):
            raise IsolationError("supervisor thread did not start in time")
        if self._error is not None:
            raise self._error
