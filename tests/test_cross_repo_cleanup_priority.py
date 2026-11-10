# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Keep pending host failures authoritative while cleaning owned review cwds.

Native controls use real Git, SQLite, a local CLI backend and CI verdicts. They
qualify Linux docs-only review, not outer CLI/MCP, HOLD or mutation workloads.
Faults happen at owned roots' actual os.rmdir calls; attempts are not deletions.
"""

from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import signal
import sys
import threading
from unittest.mock import patch

import pytest

from code_forge import _advisory_diagnostics as storage
from code_forge import cli, cross_repo
from code_forge._cross_repo_diagnostics import DiagnosticCapture
from code_forge.baseline import GitRefBaseline
from code_forge.machine import StateMachine
from code_forge.state import Mode, Verdict
from tests import test_cross_repo_diagnostic_gate, test_cross_repo_immediate_diagnostic


# Reuse the committed native input and real worker-lifecycle observer.
native_ci_input = test_cross_repo_diagnostic_gate.native_ci_input
worker_completion = test_cross_repo_immediate_diagnostic.worker_completion


NATIVE_LINUX = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="persistent diagnostics qualify Linux local filesystems"
)
VERDICTS = pytest.mark.parametrize("verdict", [Verdict.PASS, Verdict.FAIL], ids=["PASS", "FAIL"])


def _body_error(error_type):
    error = error_type("owned body failure")
    error.__context__ = LookupError("pre-existing context")
    error.__cause__ = ValueError("pre-existing cause")
    error.__suppress_context__ = True
    return error


def _assert_body_error(caught, expected, context, cause):
    assert caught is expected
    assert expected.__context__ is context
    assert expected.__cause__ is cause
    assert expected.__suppress_context__ is True


@pytest.fixture
def native_cleanup_run(native_ci_input, worker_completion, monkeypatch):
    """Run the actual host, faulting only the requested failure boundary."""
    primary, sibling, gate, backend, env = native_ci_input
    observed = worker_completion
    native_make = cross_repo.make_per_repo_cwd
    native_run = StateMachine.run
    native_start = threading.Thread.start
    native_copy = cross_repo.shutil.copy2
    native_rmtree = cross_repo.shutil.rmtree
    native_rmdir = cross_repo.os.rmdir
    native_capture_init = DiagnosticCapture.__init__

    def run(verdict, cleanup_errors, *, boundary=None, body_error=None, sibling_warning=False):
        captures, publications, returns, receipts = [], [], {}, {}
        attempts, removals = [], []
        original_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

        def assert_workers_finished():
            labels = set() if boundary == "partial" else {"primary"}
            if boundary not in ("partial", "start"):
                labels.add("consumer")
            assert len(observed.started) == len(observed.joined) == len(labels)
            assert set(observed.started) == set(observed.joined)
            assert all(not thread.is_alive() for thread in observed.started)
            assert set(observed.completed) == set(observed.exited) == labels
            assert observed.escaped == []

        def capture_init(owner, *args, **kwargs):
            native_capture_init(owner, *args, **kwargs)
            captures.append(owner)

        def make(label, *args, **kwargs):
            if boundary == "partial" and label == "consumer":
                raise body_error
            return native_make(label, *args, **kwargs)

        def start(thread, *args, **kwargs):
            is_worker = getattr(getattr(thread, "_target", None), "__name__", None) == "_thread_fn"
            if boundary == "start" and is_worker and thread._args[0] == "consumer":
                raise body_error
            return native_start(thread, *args, **kwargs)

        def machine_run(machine):
            result = native_run(machine)
            label = machine.baseline_spec_repr
            returns[label] = result
            if label != "primary":
                if sibling_warning:
                    raise RuntimeError("owned sibling advisory")
                return result
            for path in (machine.cwd / ".code-forge/receipts").glob("receipt-*.json"):
                receipts[path.name] = path.read_bytes()
            # Retain expected context before unwinding, independently of the
            # later reader and deletion of the ephemeral cwd.
            for owner in captures:
                publications.extend(
                    (record.publication, record.observation.context, record.observation)
                    for record in owner.records
                )
            if boundary == "primary":
                raise body_error
            if boundary == "replay":
                signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
            return result

        def copy(src, dst, *args, **kwargs):
            if boundary == "receipt" and Path(dst).name.startswith("primary-receipt-"):
                raise body_error
            return native_copy(src, dst, *args, **kwargs)

        def cleanup(path, *args, **kwargs):
            if Path(path) in observed.roots:
                assert args == (True,) and kwargs == {}
                assert_workers_finished()
                attempts.append(Path(path))
            return native_rmtree(path, *args, **kwargs)

        def rmdir(path, *args, **kwargs):
            path = Path(path)
            if path in observed.roots:
                removals.append(path)
                error = cleanup_errors[observed.roots.index(path)]
                if error is not None:
                    raise error
            return native_rmdir(path, *args, **kwargs)

        def replay_handler(signum, frame):
            assert signum == signal.SIGINT
            assert_workers_finished()
            raise body_error

        monkeypatch.setattr(DiagnosticCapture, "__init__", capture_init)
        monkeypatch.setattr(cross_repo, "make_per_repo_cwd", make)
        monkeypatch.setattr(StateMachine, "run", machine_run)
        monkeypatch.setattr(threading.Thread, "start", start)
        monkeypatch.setattr(cross_repo.shutil, "copy2", copy)
        monkeypatch.setattr(cross_repo.shutil, "rmtree", cleanup)
        monkeypatch.setattr(cross_repo.os, "rmdir", rmdir)
        output, errors = io.StringIO(), io.StringIO()
        caught, actual = None, None
        try:
            if boundary == "replay":
                signal.signal(signal.SIGINT, replay_handler)
            expected_handlers = {sig: signal.getsignal(sig) for sig in original_handlers}
            with (
                patch.dict(os.environ, dict(env, FORGE_FIXTURE_GATE=verdict.value), clear=True),
                redirect_stdout(output),
                redirect_stderr(errors),
            ):
                try:
                    actual = cli._cross_repo_verdict_or_none(
                        gate_yaml_path=gate,
                        cwd=primary,
                        baseline_spec=GitRefBaseline("main"),
                        head_spec=GitRefBaseline("feature"),
                        mode=Mode.CI,
                        engine_choice="real",
                        backend=backend,
                        max_rounds=1,
                        max_fix=1,
                        _clean_threshold=1,
                        warn=pytest.fail,
                    )
                except BaseException as error:  # noqa: BLE001 -- Inspect exact process-control identity too.
                    caught = error
            assert {sig: signal.getsignal(sig) for sig in original_handlers} == expected_handlers
            assert len(observed.roots) == len(cleanup_errors) == (1 if boundary == "partial" else 2)
            assert attempts == removals == list(reversed(observed.roots))
            assert_workers_finished()
            # Assert exact residue before teardown, including ignored OSError.
            assert [root for root in observed.roots if root.exists()] == [
                root
                for root, error in zip(observed.roots, cleanup_errors, strict=True)
                if error is not None
            ]
            for root in (primary, sibling):
                assert not (root / ".code-forge/code-forge.lock").exists()
            if boundary == "partial":
                assert returns == {} and captures == [] and publications == []
            else:
                assert returns["primary"] is verdict
                assert len(captures) == len(publications) == 1
                assert not captures[0]._active
                publication, context, observation = publications[0]
                assert publication.status == "published"
                assert storage.read_observation(publication, context) == observation
                assert len(receipts) == 3
            if boundary is None:
                for name, raw in receipts.items():
                    assert (primary / ".code-forge" / ("primary-" + name)).read_bytes() == raw
            if sibling_warning:
                assert "WARNING: sibling 'consumer' crashed: owned sibling advisory" in output.getvalue()
            return actual, caught
        finally:
            for sig, handler in original_handlers.items():
                signal.signal(sig, handler)
            monkeypatch.setattr(cross_repo.os, "rmdir", native_rmdir)
            for root in observed.roots:
                native_rmtree(root, ignore_errors=True)
                assert not root.exists()

    return run


@NATIVE_LINUX
@VERDICTS
@pytest.mark.parametrize(
    ("boundary", "error_type"),
    [
        ("primary", RuntimeError),
        ("primary", KeyboardInterrupt),
        ("primary", SystemExit),
        ("receipt", RuntimeError),
        ("replay", KeyboardInterrupt),
        ("start", SystemExit),
    ],
    ids=["primary-error", "primary-interrupt", "primary-exit", "receipt", "replay", "second-start"],
)
def test_pending_body_failure_survives_cleanup(native_cleanup_run, verdict, boundary, error_type):
    body = _body_error(error_type)
    context, cause = body.__context__, body.__cause__
    _, caught = native_cleanup_run(
        verdict, (None, SystemExit("consumer cleanup")), boundary=boundary, body_error=body
    )
    _assert_body_error(caught, body, context, cause)


@NATIVE_LINUX
@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_partial_setup_cleans_only_registered_cwd(native_cleanup_run, error_type):
    body = _body_error(error_type)
    context, cause = body.__context__, body.__cause__
    _, caught = native_cleanup_run(
        Verdict.PASS, (SystemExit("primary cleanup"),), boundary="partial", body_error=body
    )
    _assert_body_error(caught, body, context, cause)


@NATIVE_LINUX
@VERDICTS
def test_normal_return_deletes_both_cwds(native_cleanup_run, verdict):
    actual, caught = native_cleanup_run(verdict, (None, None))
    assert caught is None
    assert actual is verdict


@NATIVE_LINUX
@VERDICTS
@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit, StopIteration])
def test_cleanup_only_error_keeps_identity(native_cleanup_run, verdict, error_type):
    cleanup_error = error_type("consumer cleanup")
    _, caught = native_cleanup_run(verdict, (None, cleanup_error))
    assert caught is cleanup_error


@NATIVE_LINUX
@VERDICTS
@pytest.mark.parametrize("pending", [False, True], ids=["cleanup-only", "pending-body"])
def test_multiple_cleanup_failures_keep_priority(native_cleanup_run, verdict, pending):
    body = _body_error(RuntimeError)
    context, cause = body.__context__, body.__cause__
    last = KeyboardInterrupt("primary cleanup runs last")
    first = SystemExit("consumer cleanup runs first")
    _, caught = native_cleanup_run(
        verdict, (last, first), boundary="primary" if pending else None, body_error=body
    )
    if pending:
        _assert_body_error(caught, body, context, cause)
    else:
        assert caught is last
        assert last.__context__ is None


@NATIVE_LINUX
@VERDICTS
@pytest.mark.parametrize("pending", [False, True], ids=["normal-return", "pending-body"])
def test_ignored_permission_error_keeps_residue_and_result(native_cleanup_run, verdict, pending):
    body = _body_error(RuntimeError)
    context, cause = body.__context__, body.__cause__
    actual, caught = native_cleanup_run(
        verdict,
        (PermissionError("primary root"), PermissionError("consumer root")),
        boundary="primary" if pending else None,
        body_error=body,
    )
    if pending:
        _assert_body_error(caught, body, context, cause)
    else:
        assert caught is None
        assert actual is verdict


@NATIVE_LINUX
@VERDICTS
@pytest.mark.parametrize("cleanup_fails", [False, True], ids=["normal-cleanup", "cleanup-error"])
def test_sibling_warning_is_not_a_pending_error(native_cleanup_run, verdict, cleanup_fails):
    cleanup_error = SystemExit("consumer cleanup") if cleanup_fails else None
    actual, caught = native_cleanup_run(verdict, (None, cleanup_error), sibling_warning=True)
    assert caught is cleanup_error
    if not cleanup_fails:
        assert actual is verdict


EXCEPTION_TYPES = [
    RuntimeError,
    KeyboardInterrupt,
    SystemExit,
    StopIteration,
    GeneratorExit,
    BaseException,
]


@pytest.mark.parametrize("body_type", [None, *EXCEPTION_TYPES])
@pytest.mark.parametrize("cleanup_type", [None, *EXCEPTION_TYPES])
def test_owner_exception_matrix(body_type, cleanup_type):
    """The private owner itself is not Linux- or diagnostic-storage-specific."""
    body = _body_error(body_type) if body_type else None
    context, cause = (body.__context__, body.__cause__) if body else (None, None)
    first = cleanup_type("first cleanup") if cleanup_type else None
    last = cleanup_type("last cleanup") if cleanup_type else None
    attempts = []

    def cleanup(label, error):
        attempts.append(label)
        if error is not None:
            raise error

    caught = None
    try:
        with cross_repo._PerRepoCwdCleanup() as stack:
            stack.callback(cleanup, "last", last)
            stack.callback(cleanup, "first", first)
            if body is not None:
                raise body
    except BaseException as error:  # noqa: BLE001 -- Verify every exception kind, including process control.
        caught = error
    assert attempts == ["first", "last"]
    if body is not None:
        _assert_body_error(caught, body, context, cause)
    else:
        assert caught is last


def test_owner_preserves_unsuppressed_context_and_existing_notes():
    body = _body_error(RuntimeError)
    body.__suppress_context__ = False
    body.add_note("pre-existing note")
    context, cause = body.__context__, body.__cause__

    def cleanup():
        raise SystemExit("cleanup")

    with pytest.raises(RuntimeError) as caught:
        with cross_repo._PerRepoCwdCleanup() as stack:
            stack.callback(cleanup)
            raise body
    assert caught.value is body
    assert body.__context__ is context
    assert body.__cause__ is cause
    assert body.__suppress_context__ is False
    assert body.__notes__ == ["pre-existing note"]


def test_active_caller_exception_is_not_a_pending_body_error():
    unrelated = RuntimeError("already handled outside the cwd owner")
    cleanup_error = SystemExit("cleanup")

    def cleanup():
        raise cleanup_error

    try:
        raise unrelated
    except RuntimeError:
        with pytest.raises(SystemExit) as caught:
            with cross_repo._PerRepoCwdCleanup() as stack:
                stack.callback(cleanup)
        assert caught.value is cleanup_error
