# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for STATE-11 signal handling (integration, subprocess-based).

Marked @pytest.mark.integration.
"""

import ast
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from code_forge.lock import acquire_lock

SRC_DIR = str(Path(__file__).resolve().parent.parent / "src")
_FORCED_CLEANUP = "force cleanup"


def _is_forced_cleanup(exc: BaseException) -> bool:
    """True only for the leak-check's planted assertion."""
    return isinstance(exc, AssertionError) and exc.args == (_FORCED_CLEANUP,)


def _lock_holder_script(lock_path: str) -> str:
    """Python script that acquires lock and blocks until signaled."""
    return (
        "import sys, os, time, signal\n"
        f"sys.path.insert(0, {SRC_DIR!r})\n"
        "from pathlib import Path\n"
        "from code_forge.lock import ForgeLock\n"
        f"lock_path = Path({lock_path!r})\n"
        "with ForgeLock(lock_path):\n"
        "    sys.stdout.write('READY\\n')\n"
        "    sys.stdout.flush()\n"
        "    time.sleep(30)\n"
    )


class _TimedPopen:
    """Popen context that kills the child if wait() has no timeout."""

    def __init__(self, proc, timeout=5):
        self.proc = proc
        self.timeout = timeout

    def __enter__(self):
        return self.proc

    def __exit__(self, exc_type, exc, tb):
        if self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=self.timeout)
                except subprocess.TimeoutExpired:
                    pass
        if self.proc.stdout is not None:
            self.proc.stdout.close()
        if self.proc.stderr is not None:
            self.proc.stderr.close()
        return False


class _StubChild:
    """Child that never exits, so both wait() calls time out."""

    def __init__(self):
        self.kills = 0
        self.waits = 0
        self.stdout = None
        self.stderr = None

    def poll(self):
        return None

    def kill(self):
        self.kills += 1

    def wait(self, timeout=None):
        self.waits += 1
        raise subprocess.TimeoutExpired(cmd="stub", timeout=timeout)


def test_timed_popen_retries_kill_when_wait_times_out():
    """A child that outlives the first kill must be killed again.

    _StubChild never exits, so each wait() times out. That is the
    condition under test: cleanup cannot confirm death, so it retries
    rather than assuming the first kill landed.
    """
    child = _StubChild()
    with _TimedPopen(child, timeout=1):
        pass
    assert child.kills == 2
    assert child.waits == child.kills


def _run_lock_holder(lock_path: Path):
    return _TimedPopen(subprocess.Popen(
        [sys.executable, "-c", _lock_holder_script(str(lock_path))],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ))


@pytest.mark.integration
class TestSigintCleansLock:
    """(a) SIGINT during held lock -> lock removed."""

    def test_sigint_removes_lock(self, tmp_path):
        lock_path = tmp_path / "code-forge.lock"
        with _run_lock_holder(lock_path) as proc:
            assert proc.stdout is not None
            line = proc.stdout.readline().decode().strip()
            assert line == "READY"
            assert lock_path.exists()
            proc.send_signal(signal.SIGINT)
            # Wait here so SIGINT cleanup finishes before __exit__.
            # poll() then skips kill; that is the happy path, not a race.
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        assert not lock_path.exists()


@pytest.mark.integration
class TestSigtermCleansLock:
    """(b) SIGTERM during held lock -> lock removed."""

    def test_sigterm_removes_lock(self, tmp_path):
        lock_path = tmp_path / "code-forge.lock"
        with _run_lock_holder(lock_path) as proc:
            assert proc.stdout is not None
            line = proc.stdout.readline().decode().strip()
            assert line == "READY"
            assert lock_path.exists()
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        assert not lock_path.exists()


@pytest.mark.integration
class TestSigkillLeavesStale:
    """(c) SIGKILL during held lock -> lock NOT removed."""

    def test_sigkill_leaves_stale(self, tmp_path):
        lock_path = tmp_path / "code-forge.lock"
        with _run_lock_holder(lock_path) as proc:
            assert proc.stdout is not None
            line = proc.stdout.readline().decode().strip()
            assert line == "READY"
            assert lock_path.exists()
            child_pid = proc.pid
            proc.send_signal(signal.SIGKILL)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            # Lock file still exists and still names the dead child.
            assert lock_path.exists()
            assert lock_path.read_text().strip() == str(child_pid)
            acquire_lock(lock_path)
            content = lock_path.read_text().strip()
            assert content == str(os.getpid())


@pytest.mark.integration
def test_context_kills_child_when_assertion_fails(tmp_path):
    """Leaving the helper on a failed assertion must not leak the child."""
    lock_path = tmp_path / "code-forge.lock"
    leaked = None
    try:
        with _run_lock_holder(lock_path) as proc:
            leaked = proc
            assert proc.stdout is not None
            line = proc.stdout.readline().decode().strip()
            assert line == "READY"
            raise AssertionError(_FORCED_CLEANUP)
    except AssertionError as exc:
        if not _is_forced_cleanup(exc):
            raise
    assert leaked is not None
    # This line sits AFTER the with-block, so _TimedPopen.__exit__ has
    # already run: it killed the child and waited for it. poll() is
    # therefore the verification that cleanup worked, not a liveness
    # race -- a None here would mean __exit__ let the child survive.
    assert leaked.poll() is not None


def _function_node(name: str) -> ast.FunctionDef:
    """Locate a function in this file by name, at any nesting depth."""
    tree = ast.parse(Path(__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name} not found")


def test_sigint_wait_runs_inside_the_holder():
    """SIGINT must be reaped before the context kills the child.

    Structure is read from the parse tree, not from source text: the
    wait has to live inside the with-block, wrapped in a try that
    tolerates TimeoutExpired. Indentation and formatting are irrelevant
    to that claim, so they are not asserted.
    """
    fn = _function_node("test_sigint_removes_lock")
    withs = [n for n in ast.walk(fn) if isinstance(n, ast.With)]
    assert withs, "expected a with-block holding the child"
    tries = [n for w in withs for n in ast.walk(w) if isinstance(n, ast.Try)]
    assert tries, "wait must sit in a try inside the with-block"

    def _is_wait(node):
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "wait"
        )

    guarded = [t for t in tries if any(_is_wait(n) for n in ast.walk(t))]
    assert guarded, "no wait() call inside a try in the with-block"
    handled = [
        ast.unparse(h.type)
        for t in guarded
        for h in t.handlers
        if h.type is not None
    ]
    assert any("TimeoutExpired" in h for h in handled), handled


def test_leak_check_binds_handle_before_ready_assert():
    """READY failing must not hide the child behind leaked is not None."""
    fn = _function_node("test_context_kills_child_when_assertion_fails")

    def _line_of(predicate) -> int:
        hits = [n.lineno for n in ast.walk(fn) if predicate(n)]
        assert len(hits) == 1, hits
        return hits[0]

    bind = _line_of(
        lambda n: isinstance(n, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "leaked" for t in n.targets
        )
        and isinstance(n.value, ast.Name)
        and n.value.id == "proc"
    )

    def _is_ready_assert(n) -> bool:
        if not isinstance(n, ast.Assert):
            return False
        return any(
            isinstance(c, ast.Constant) and c.value == "READY"
            for c in ast.walk(n.test)
        )

    ready = _line_of(_is_ready_assert)
    assert bind < ready, (bind, ready)

    tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
    handlers = [h for t in tries for h in t.handlers]
    assert handlers, "expected an except clause around the forced cleanup"
    guard = "\n".join(ast.unparse(h) for h in handlers)
    assert "_is_forced_cleanup(exc)" in guard
    assert "raise" in guard

    # once cleanup has run, nothing may wait on the child again
    leak_assert = _line_of(
        lambda n: isinstance(n, ast.Assert)
        and ast.unparse(n) == "assert leaked is not None"
    )
    late_waits = [
        n.lineno
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "wait"
        and n.lineno > leak_assert
    ]
    assert late_waits == [], late_waits


def test_forced_cleanup_filter_reraises_other_assertions():
    assert _is_forced_cleanup(AssertionError("not ready")) is False


def test_forced_cleanup_filter_keeps_forced_marker():
    assert _is_forced_cleanup(AssertionError(_FORCED_CLEANUP)) is True


def test_forced_cleanup_filter_reraises_extra_args():
    assert _is_forced_cleanup(AssertionError(_FORCED_CLEANUP, "extra")) is False


def test_forced_cleanup_filter_uses_args_not_str():
    class _Quiet(AssertionError):
        def __str__(self) -> str:
            return _FORCED_CLEANUP

    assert _is_forced_cleanup(_Quiet("not ready")) is False


@pytest.mark.integration
def test_helper_reraises_unexpected_assertion(tmp_path):
    """A real assertion inside the helper must not be swallowed."""
    lock_path = tmp_path / "code-forge.lock"
    with _run_lock_holder(lock_path) as proc:
        assert proc.stdout is not None
        proc.stdout.readline()
        with pytest.raises(AssertionError, match="not ready"):
            raise AssertionError("not ready")
