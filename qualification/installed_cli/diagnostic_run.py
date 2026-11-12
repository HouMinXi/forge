# SPDX-License-Identifier: Apache-2.0
"""Reusable prelaunch tracing for synthetic installed-CLI diagnostics."""

from contextlib import contextmanager
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time


SYSCALLS = "fsync,fdatasync,rename,renameat,renameat2,open,openat,openat2,close,exit,exit_group"
CASE_IDS = frozenset(("normal", "correction", "partial", "http_error", "start", "prefix"))


@contextmanager
def _private_stream(path):
    # Retain raw FD ownership even if buffered-wrapper construction fails after
    # allocating a non-owning FileIO. Never retry close: the number may be reused.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    stream = None
    try:
        stream = os.fdopen(descriptor, "wb", closefd=False)
        yield stream
    finally:
        primary = sys.exc_info()[1]
        stream_error = None
        if stream is not None:
            try:
                stream.close()
            except BaseException as error:  # noqa: BLE001 - preserve primary, then first cleanup error
                stream_error = error
        try:
            os.close(descriptor)
        except BaseException:
            if primary is None and stream_error is None:
                raise
        if primary is None and stream_error is not None:
            raise stream_error


def _write_private(path, value):
    with _private_stream(path) as stream:
        stream.write(value.encode("utf-8"))


def _terminal_observed(path, pid, *, timeout=2.0):
    """Bounded collector drain after process exit; not a product deadline."""
    terminal = re.compile(
        rb"(?m)^"
        + str(pid).encode()
        + rb"[ \t]+[0-9]+\.[0-9]+[ \t]+\+\+\+ (?:exited with [0-9]+|killed by SIG[A-Z0-9_]+(?: \(core dumped\))?) \+\+\+\n"
    )
    deadline = time.monotonic() + timeout
    while True:
        try:
            with path.open("rb") as stream:
                size = os.fstat(stream.fileno()).st_size
                if size > 32 * 1024 * 1024:
                    return False
                offset = max(0, size - 65536)
                stream.seek(max(0, offset - 1))
                # One guard byte distinguishes a real line start from a PID
                # suffix at the bounded tail's first byte.
                at_line_start = offset == 0 or stream.read(1) == b"\n"
                tail = stream.read(65536)
                if not at_line_start:
                    tail = tail.partition(b"\n")[2]
                if terminal.search(tail):
                    return True
        except OSError:
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)


@contextmanager
def traced_child(argv, *, case_id, cwd, env, output, stdout, stderr, startup_mode="ordinary"):
    """Trace before exec, keeping the actual console as the exact Popen child.

    Linux strace -D runs the tracer as a grandchild, leaving the tracee in place.
    No PID lookup, signal forwarding or deadline change is used. The caller owns
    waiting, reaping, and watchdog cleanup of its entire private session. This
    context does not stop or reap the child on an exception or early return.
    case_id is a fixed public label, not proof of a scenario or roster coverage.
    """
    if startup_mode not in {"ordinary", "isolated-no-site"}:
        raise ValueError("unknown diagnostic startup mode")
    if startup_mode == "isolated-no-site" and (
        len(argv) < 7
        or list(argv[1:5]) != ["-I", "-S", "-B", "-X"]
        or not str(argv[5]).startswith("pycache_prefix=")
    ):
        raise ValueError("no-site trace command differs")
    if type(case_id) is not str:
        raise TypeError("diagnostic case identity must be a string")
    if case_id not in CASE_IDS:
        raise ValueError("unknown diagnostic case identity")
    # Anchor paths before changing the child's cwd, without following an
    # existing output symlink past the exclusive directory creation below.
    output = Path(output).absolute()
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    strace = shutil.which("strace", path=os.defpath)
    if strace is None:
        raise RuntimeError("diagnostic collector unavailable")
    if env.get("PYTHONPATH"):
        raise ValueError("diagnostic input environment must not contain PYTHONPATH")
    shim = output / "shim"
    shim.mkdir(mode=0o700)
    probe = Path(__file__).with_name("diagnostic_probe.py").resolve()
    # The shim loads no product modules. install() observes subsequent ordinary
    # imports passively; the installed launcher's provenance checks stay active.
    shim_code = (
        "import importlib.util, os\n"
        "fd = os.environ.pop('FORGE_CLI_DIAGNOSTIC_FD', None)\n"
        "if fd is not None:\n"
        "    os.set_inheritable(int(fd), False)\n"
        f"    spec = importlib.util.spec_from_file_location('forge_cli_diagnostic_probe', {str(probe)!r})\n"
        "    module = importlib.util.module_from_spec(spec)\n"
        "    spec.loader.exec_module(module)\n"
        "    module.install(int(fd))\n"
    )
    if startup_mode == "ordinary":
        _write_private(shim / "sitecustomize.py", shim_code)
    child_env = dict(env)
    if startup_mode == "ordinary":
        child_env["PYTHONPATH"] = str(shim)
    child_env["PYTHONDONTWRITEBYTECODE"] = "1"
    child = None
    events = os.open(output / "events.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        child_env["FORGE_CLI_DIAGNOSTIC_FD"] = str(events)
        with _private_stream(output / "syscalls.log"):
            pass
        command = [
            strace,
            "-D",
            "-f",
            "-ttt",
            "-T",
            "-yy",
            "-s",
            "4096",
            "-e",
            f"trace={SYSCALLS}",
            "-o",
            str(output / "syscalls.log"),
            "--",
            *map(str, argv),
        ]
        started = time.monotonic_ns()
        child = subprocess.Popen(
            command,
            cwd=cwd,
            env=child_env,
            stdout=stdout,
            stderr=stderr,
            pass_fds=(events,),
        )
        yield child
    finally:
        # Preserve the caller/setup exception before any cleanup can replace it.
        # With no primary, the first cleanup failure wins, but metadata is still
        # attempted. Never retry close: the descriptor may already be reused.
        primary = sys.exc_info()[1]
        close_error = None
        try:
            os.close(events)
        except BaseException as error:  # noqa: BLE001 - defer cleanup without masking the primary
            close_error = error
        if child is not None:
            try:
                _write_private(
                    output / "execution.json",
                    json.dumps(
                        {
                            "schema": 1,
                            "mode": "instrumented",
                            "case_id": case_id,
                            "pid": child.pid,
                            "returncode": child.poll(),
                            "collector_terminal_observed": child.poll() is not None
                            and _terminal_observed(output / "syscalls.log", child.pid),
                            "elapsed_ns": time.monotonic_ns() - started,
                        }
                    )
                    + "\n",
                )
            except BaseException:
                if primary is None and close_error is None:
                    raise
        if primary is None and close_error is not None:
            raise close_error
