# SPDX-License-Identifier: Apache-2.0
"""Run one mutation command in a private Linux process owner.

The owner is a subreaper in a fresh interpreter. Orphans remain its children
even if they create another session; pidfds bind signals to the observed
process identity instead of a potentially reused numeric PID.
"""

from __future__ import annotations

import base64
import ctypes
import errno
import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

_CLEANUP_SECONDS = 5.0
# #121: each cleanup poll walks the whole tree, so a fixed 5s can run out on
# thousands of test descendants and turn a plain timeout into a cleanup
# error. The budget grows with the discovered tree, capped; the outer waits
# in run_owned_command use the cap so they stay in step.
_CLEANUP_PER_PROCESS_SECONDS = 0.01
_CLEANUP_MAX_SECONDS = 30.0
# Slack for the owner to finish teardown and write its report after its own
# deadline. Each cleanup poll can run long on a tree of thousands of test
# descendants, so a few seconds is not enough (#116). 30s is a heuristic,
# not a bound: a report that still arrives late is rescued in
# run_owned_command when it says timed_out with cleanup complete.
_OWNER_REPORT_SLACK = 30.0
# #190: after an interruption the owner must still leave its run loop (one
# reap+discover poll over the whole tree), run cleanup for up to
# _CLEANUP_MAX_SECONDS (whose last poll may end after that deadline), and
# serialize its report. That is the same post-deadline work the normal wait
# covers with _OWNER_REPORT_SLACK, so the drain gets the same slack instead of
# a 2s margin that reported "did not finish cleanup" for owners that finish.
_INTERRUPT_DRAIN_SECONDS = _CLEANUP_MAX_SECONDS + _OWNER_REPORT_SLACK
_TERM_SECONDS = 0.25
_POLL_SECONDS = 0.01


def _ignore_sigint(signum, frame) -> None:
    """Caught no-op: the parent still ignores SIGINT, but exec resets a caught
    signal to SIG_DFL in the child, unlike an ignored one."""


def reset_child_sigint() -> None:
    """preexec_fn fallback: the child starts with SIGINT at its default."""
    signal.signal(signal.SIGINT, signal.SIG_DFL)


def child_sigint_options() -> dict:
    """Popen kwargs so a child test run starts with default SIGINT (#230).

    An ignored signal survives exec, so forge launched from a background shell
    (SIGINT=SIG_IGN) would hand that to the user's test suite and break tests
    expecting KeyboardInterrupt. On the main thread, swap SIG_IGN for a caught
    no-op (same behaviour for forge, reset on exec, thread-safe). Off the main
    thread the disposition can't be changed, so fall back to preexec_fn.
    """
    if signal.getsignal(signal.SIGINT) != signal.SIG_IGN:
        return {}
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, _ignore_sigint)
        return {}
    return {"preexec_fn": reset_child_sigint}  # rare: non-main thread under SIG_IGN


class MutationProcessError(RuntimeError):
    def __init__(self, message: str, *, cleanup_complete: bool, report: dict | None = None):
        super().__init__(message)
        self.cleanup_complete = cleanup_complete
        self.report = report or {}


def _bind_cancellation_evidence(cancellation: BaseException, *, note: str | None = None, **evidence):
    """Propagate the first control interruption, retaining an already pending one."""
    try:
        cancellation.__dict__.update(evidence)
        if note is not None:
            cancellation.add_note(note)
    except BaseException as exc:  # noqa: BLE001 - retain cancellation while recording interrupted evidence
        if isinstance(cancellation, Exception) and not isinstance(exc, Exception):
            raise
        cancellation.__dict__.update(evidence, cleanup_complete=False, cleanup_evidence_error=exc)


def limit_address_space(memory_limit_bytes: int) -> None:
    import resource

    _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    if hard != resource.RLIM_INFINITY:
        memory_limit_bytes = min(memory_limit_bytes, hard)
    resource.setrlimit(resource.RLIMIT_AS, (memory_limit_bytes, memory_limit_bytes))


@dataclass(frozen=True)
class _Identity:
    pid: int
    parent: int
    start_ticks: int
    state: str


def _identity(pid: int) -> _Identity | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_bytes()
    except (FileNotFoundError, ProcessLookupError):
        return None
    fields = raw[raw.rfind(b")") + 2 :].split()
    return _Identity(pid, int(fields[1]), int(fields[19]), fields[0].decode("ascii"))


def _children(identity: _Identity) -> list[int]:
    before = _identity(identity.pid)
    if before is None or before.start_ticks != identity.start_ticks:
        return []
    children: set[int] = set()
    try:
        for task in Path(f"/proc/{identity.pid}/task").iterdir():
            try:
                children.update(int(pid) for pid in (task / "children").read_bytes().split())
            except (FileNotFoundError, ProcessLookupError):
                continue
    except (FileNotFoundError, ProcessLookupError):
        return []
    after = _identity(identity.pid)
    if after is None or after.start_ticks != identity.start_ticks:
        return []
    return sorted(children)


def _pidfd_ready(fd: int) -> bool:
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    events = poller.poll(0)
    if any(mask & (select.POLLERR | select.POLLNVAL) for _, mask in events):
        raise OSError("invalid mutation process descriptor")
    return any(mask & select.POLLIN for _, mask in events)


def _cleanup_budget(processes: int) -> float:
    """Cleanup budget for a tree of ``processes`` discovered descendants."""
    return min(_CLEANUP_MAX_SECONDS, _CLEANUP_SECONDS + _CLEANUP_PER_PROCESS_SECONDS * processes)


class _OwnedTree:
    def __init__(self):
        self.owner = _identity(os.getpid())
        if self.owner is None:
            raise RuntimeError("cannot identify mutation process owner")
        probe = os.pidfd_open(self.owner.pid)
        os.close(probe)
        self.identities: dict[int, _Identity] = {}
        self.fds: dict[int, int] = {}
        self.reaped: dict[int, int] = {}
        self.cleanup_deadline: float | None = None
        self.cleanup_started: float | None = None

    def discover(self) -> None:
        pending = [self.owner]
        visited: set[int] = set()
        while pending:
            parent = pending.pop()
            if parent.pid in visited:
                continue
            visited.add(parent.pid)
            if parent.pid in self.fds and _pidfd_ready(self.fds[parent.pid]):
                continue
            for pid in _children(parent):
                identity = _identity(pid)
                if identity is None or identity.parent != parent.pid:
                    continue
                if pid in self.identities:
                    if self.identities[pid].start_ticks != identity.start_ticks:
                        raise RuntimeError("mutation descendant PID identity changed")
                    if pid not in self.fds:
                        continue
                if pid not in self.fds:
                    try:
                        fd = os.pidfd_open(pid)
                    except ProcessLookupError:
                        continue
                    except OSError as exc:
                        # #113: an exiting child whose thread-group task is
                        # already gone gives EINVAL, not ESRCH. Skip it like
                        # ESRCH only if /proc confirms it is gone, a zombie,
                        # dead, or a different process; otherwise fail closed.
                        if exc.errno != errno.EINVAL:
                            raise
                        gone = _identity(pid)
                        if (
                            gone is None
                            or gone.state in ("Z", "X", "x")
                            or gone.start_ticks != identity.start_ticks
                        ):
                            continue
                        raise
                    current = _identity(pid)
                    if (
                        current is None
                        or current.start_ticks != identity.start_ticks
                        or current.parent not in (parent.pid, self.owner.pid)
                    ):
                        os.close(fd)
                        continue
                    self.fds[pid] = fd
                    self.identities[pid] = identity
                pending.append(identity)

    def live(self) -> list[int]:
        live = []
        for pid in self.fds:
            identity = self.identities[pid]
            current = _identity(pid)
            if current is None:
                if not _pidfd_ready(self.fds[pid]):
                    live.append(pid)
            elif current.start_ticks == identity.start_ticks:
                live.append(pid)
        return live

    def reap(self, driver: subprocess.Popen) -> None:
        driver_status = driver.poll()
        for pid in list(self.fds):
            identity = self.identities[pid]
            if pid == driver.pid:
                if driver_status is not None:
                    self.reaped[pid] = driver_status
                    self._retire(pid)
                continue
            current = _identity(pid)
            if current is None:
                if not _pidfd_ready(self.fds[pid]):
                    continue
            elif current.start_ticks != identity.start_ticks or current.parent != self.owner.pid:
                continue
            try:
                result = os.waitid(os.P_PIDFD, self.fds[pid], os.WEXITED | os.WNOHANG)
            except ChildProcessError:
                if current is None:
                    self._retire(pid)
                continue
            if result is not None:
                self.reaped[pid] = result.si_status
                self._retire(pid)

    def _retire(self, pid: int) -> None:
        os.close(self.fds.pop(pid))

    def signal(self, pid: int, sig: int) -> None:
        if pid not in self.fds:
            return
        identity = self.identities[pid]
        current = _identity(pid)
        if current is None or current.start_ticks != identity.start_ticks:
            return
        try:
            signal.pidfd_send_signal(self.fds[pid], sig)
        except ProcessLookupError:
            pass

    def _extend_cleanup_deadline(self) -> None:
        """Grow the shared deadline with the discovered tree (#121). A deadline
        set from outside (no cleanup_started) stays fixed."""
        if self.cleanup_started is not None:
            self.cleanup_deadline = max(
                self.cleanup_deadline or 0.0,
                self.cleanup_started + _cleanup_budget(len(self.identities)),
            )

    def cleanup(self, driver: subprocess.Popen) -> bool:
        started = time.monotonic()
        if self.cleanup_deadline is None:
            self.cleanup_started = started
            self._extend_cleanup_deadline()
        terminated: set[int] = set()
        empty_polls = 0
        while time.monotonic() < self.cleanup_deadline:
            self.discover()
            self._extend_cleanup_deadline()
            self.reap(driver)
            live = self.live()
            for pid in live:
                if time.monotonic() - started >= _TERM_SECONDS:
                    self.signal(pid, signal.SIGKILL)
                elif pid not in terminated:
                    self.signal(pid, signal.SIGTERM)
                    terminated.add(pid)
            if not live and not _children(self.owner) and driver.returncode is not None:
                empty_polls += 1
                if empty_polls >= 2:
                    return True
            else:
                empty_polls = 0
            time.sleep(_POLL_SECONDS)
        return False

    def report(self) -> list[dict]:
        return [
            {
                "pid": pid,
                "start_ticks": item.start_ticks,
                "observed_parent": item.parent,
                "remaining": pid in self.live(),
                "reaped_status": self.reaped.get(pid),
            }
            for pid, item in self.identities.items()
        ]

    def close(self) -> None:
        for fd in self.fds.values():
            os.close(fd)


def _supervise(request: dict) -> dict:
    report = {"cleanup_complete": True, "timed_out": False, "cancelled": False, "owned": []}
    driver = None
    tree = None
    cancelled = False

    def cancel(_signal, _frame):
        nonlocal cancelled
        cancelled = True

    try:
        if not sys.platform.startswith("linux") or not all(
            (hasattr(os, "P_PIDFD"), hasattr(os, "pidfd_open"), hasattr(signal, "pidfd_send_signal"))
        ):
            raise RuntimeError("mutation process ownership requires Linux pidfds and /proc")
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(signum, cancel)
        parent = request["caller_pid"]
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0 or libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "cannot establish mutation process ownership")
        if os.getppid() != parent:
            cancelled = True
        tree = _OwnedTree()
        report["owner_pid"] = tree.owner.pid
        report["owner_start_ticks"] = tree.owner.start_ticks
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            if cancelled:
                raise RuntimeError("mutation caller exited before command launch")
            cap = request.get("memory_limit_bytes")
            if cap is not None:
                limit_address_space(cap)
            driver = subprocess.Popen(
                request["argv"],
                cwd=request.get("cwd"),
                env=request.get("env"),
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                **child_sigint_options(),
            )
            report["cleanup_complete"] = False
            deadline = time.monotonic() + request["timeout"]
            try:
                while driver.poll() is None:
                    tree.reap(driver)
                    tree.discover()
                    if cancelled or time.monotonic() >= deadline:
                        report["cancelled"] = cancelled
                        report["timed_out"] = not cancelled
                        break
                    time.sleep(_POLL_SECONDS)
            finally:
                report["cleanup_complete"] = tree.cleanup(driver)
                report["owned"] = tree.report()
            report["returncode"] = driver.returncode
            stdout.seek(0)
            stderr.seek(0)
            report["stdout"] = base64.b64encode(stdout.read()).decode("ascii")
            report["stderr"] = base64.b64encode(stderr.read()).decode("ascii")
    except BaseException as exc:  # noqa: BLE001 - every exit must attempt owned teardown
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["error_kind"] = type(exc).__name__
        if driver is not None and not report["cleanup_complete"]:
            try:
                report["cleanup_complete"] = tree.cleanup(driver)
                report["owned"] = tree.report()
            except Exception as cleanup_exc:  # noqa: BLE001 - preserve the original failure
                report["error"] += f"; cleanup: {cleanup_exc}"
    finally:
        if tree is not None:
            tree.close()
    return report


def run_owned_command(
    argv: list[str],
    *,
    timeout: int,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    memory_limit_bytes: int | None = None,
    capture_output: bool = True,
    text: bool = False,
    encoding: str | None = None,
    errors: str | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess:
    """Preserve subprocess results and timeout semantics after owned teardown."""
    if not argv or not capture_output or check:
        raise ValueError("owned mutation commands require argv, capture_output and check=False")
    from ._mutation_imports import ISOLATED_BOOTSTRAP, prepare_owner

    try:
        authority = prepare_owner(__file__, __name__)
    except (OSError, ValueError, TypeError, ImportError, SyntaxError) as exc:
        raise MutationProcessError(
            f"mutation owner import authority unavailable: {exc}", cleanup_complete=True
        ) from exc
    request = {
        "argv": argv,
        "timeout": timeout,
        "cwd": cwd,
        "env": env,
        "memory_limit_bytes": memory_limit_bytes,
        "caller_pid": os.getpid(),
    }
    helper = subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", ISOLATED_BOOTSTRAP],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    interruption = None
    report = {}
    try:
        try:
            raw, diagnostic = helper.communicate(
                json.dumps({"request": request, "authority": authority}).encode("utf-8"),
                timeout=timeout + 2 * _CLEANUP_MAX_SECONDS + _OWNER_REPORT_SLACK,
            )
        except BaseException as exc:  # noqa: BLE001 - every interruption drains the private owner
            interruption = exc
            try:
                helper.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                raw, diagnostic = helper.communicate(timeout=_INTERRUPT_DRAIN_SECONDS)
            except Exception as cleanup_exc:  # noqa: BLE001 - ordinary drain failures need cleanup evidence
                raise MutationProcessError(
                    f"mutation owner {helper.pid} did not finish cleanup",
                    cleanup_complete=False,
                ) from cleanup_exc
        try:
            report = json.loads(raw)
            if not isinstance(report, dict) or type(report.get("cleanup_complete")) is not bool:
                raise ValueError("invalid owner report")
        except (ValueError, TypeError) as exc:
            report = {}
            raise MutationProcessError(
                f"mutation owner failed to report cleanup: {diagnostic.decode('utf-8', 'replace')}",
                cleanup_complete=False,
            ) from exc
        if (
            isinstance(interruption, subprocess.TimeoutExpired)
            and report.get("timed_out") is True
            and report["cleanup_complete"]
        ):
            # #116: the owner hit its own command deadline and finished
            # teardown, but its report arrived after our outer wait. That is
            # the command's timeout, not an owner communication failure.
            # (_supervise sets timed_out and cancelled mutually exclusively.)
            interruption = None
        elif interruption is None and report.get("cancelled"):
            interruption = KeyboardInterrupt("mutation command was cancelled")
        if not report["cleanup_complete"]:
            remaining = ", ".join(
                f"{item['pid']}:{item['start_ticks']}"
                for item in report.get("owned", [])
                if item.get("remaining")
            )
            raise MutationProcessError(
                f"mutation descendants did not finish cleanup: {remaining}",
                cleanup_complete=False,
                report=report,
            )
        if interruption is not None and not isinstance(interruption, Exception):
            _bind_cancellation_evidence(interruption, cleanup_complete=True, ownership=report)
    except BaseException as cleanup_exc:  # noqa: BLE001 - cleanup must not replace control flow
        cancelled = interruption is not None and not isinstance(interruption, Exception)
        if cancelled or not isinstance(cleanup_exc, Exception):
            cancellation = interruption if cancelled else cleanup_exc
            cause = cleanup_exc if cancellation is interruption else interruption
            try:
                _bind_cancellation_evidence(
                    cancellation,
                    cleanup_complete=False,
                    ownership=report,
                    **({"cleanup_error": cause} if cause is not None else {}),
                )
                if cause is not None:
                    label = (
                        "mutation cleanup failed" if cancelled else "mutation owner communication failed"
                    )
                    _bind_cancellation_evidence(cancellation, note=f"{label}: {cause}")
            except BaseException as evidence_exc:  # noqa: BLE001 - diagnostic failure is secondary to cancellation
                _bind_cancellation_evidence(
                    cancellation,
                    cleanup_complete=False,
                    ownership=report,
                    cleanup_error=cause if cause is not None else evidence_exc,
                    cleanup_evidence_error=evidence_exc,
                )
            finally:
                if cause is not None:
                    raise cancellation from cause
                raise cancellation
        if interruption is not None:
            failure = MutationProcessError(
                f"mutation owner communication failed: {interruption}; cleanup: {cleanup_exc}",
                cleanup_complete=False,
                report=report,
            )
            failure.cleanup_error = cleanup_exc
            raise failure from interruption
        raise
    if interruption is not None:
        if not isinstance(interruption, Exception):
            raise interruption
        raise MutationProcessError(
            f"mutation owner communication failed: {interruption}",
            cleanup_complete=True,
            report=report,
        ) from interruption
    if report.get("error"):
        if report.get("error_kind") == "FileNotFoundError":
            raise FileNotFoundError(report["error"])
        raise MutationProcessError(report["error"], cleanup_complete=True, report=report)
    stdout = base64.b64decode(report.get("stdout", ""))
    stderr = base64.b64decode(report.get("stderr", ""))
    if text or encoding is not None or errors is not None:
        stdout = stdout.decode(encoding or "utf-8", errors or "strict")
        stderr = stderr.decode(encoding or "utf-8", errors or "strict")
    if report.get("timed_out"):
        error = subprocess.TimeoutExpired(argv, timeout, output=stdout, stderr=stderr)
        error.ownership = report
        raise error
    result = subprocess.CompletedProcess(argv, report["returncode"], stdout, stderr)
    result.ownership = report
    return result


if __name__ == "__main__":
    print(json.dumps(_supervise(json.load(sys.stdin))))
