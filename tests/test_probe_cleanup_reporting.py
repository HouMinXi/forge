"""Mixed probe failures retain typed cleanup evidence through adapter boundaries."""

import asyncio
import errno
import hashlib
import json

import pytest

from code_forge.mutation_engines import isolate
from code_forge.mutation_engines.adapters.base import (
    CapabilityState,
    isolation_probe_errors,
    sandbox_failure_result,
)
from code_forge.mutation_engines.adapters.patch_corpus import _identity
from code_forge.mutation_engines.schemas import CleanupState, RunState
from tests.test_isolate_startup_diagnostic import _probe
from tests.test_mutation_adapter_cleanup import (
    _FAILURE_POINTS,
    _context,
    _corpus_run,
    _gremlins_probe,
    _run_failing_at,
    _target,
)
from tests.test_python_mutmut_cleanup import _probe_with, _run


def _failed_probe(tmp_path, monkeypatch, *, primary=None, supported=False):
    root = tmp_path / "probe-root"
    root.mkdir()
    removal = OSError(errno.EBUSY, "busy")
    with monkeypatch.context() as patch:
        check = _probe(root, patch, supported, removal)
        if primary is not None:

            def missing(path):
                raise primary

            patch.setattr(isolate.os.path, "exists", missing)
        with pytest.raises(isolate.IsolationUnavailable) as found:
            check()
    error = found.value
    assert len(list(root.iterdir())) == 1
    assert isinstance(error.probe_cleanup_failure, isolate.ProbeCleanupError)
    assert error.probe_cleanup_failure.__cause__ is removal
    assert len(str(error.probe_cleanup_failure)) <= 200
    assert all(32 <= ord(c) < 127 for c in str(error.probe_cleanup_failure))
    assert error.cleanup_failure is None
    return error


def _assert_errors(errors, primary_phase, *, run=False, sole=False):
    expected = [] if sole else [("isolation-unavailable", primary_phase, False)]
    expected += [("probe-cleanup-failed", "cleanup", True)]
    if run:
        expected += [("cgroup-cleanup-failed", "cleanup", True)]
    assert [(e.code, e.phase, e.retryable) for e in errors] == expected
    assert all(e.target_id == "t1" for e in errors)


def _assert_result(result, error, directory, *, phase="baseline", run=False, sole=False):
    assert result.run_state is RunState.INCOMPLETE
    assert result.reason_code == ("probe-cleanup-failed" if sole else "isolation-unavailable")
    assert result.cleanup.state is CleanupState.INCOMPLETE
    assert result.cleanup.owned_group_empty is (not run)
    assert result.cleanup.owned_mounts_removed is True
    _assert_errors(result.infrastructure_errors, phase, run=run, sole=sole)
    assert result.cleanup.errors == tuple(
        e for e in result.infrastructure_errors if e.phase == "cleanup"
    )
    assert result.cleanup.errors[0].message == (str(error) if sole else str(error.probe_cleanup_failure))
    payload = (directory / "results" / "isolation-failure.json").read_bytes()
    assert json.loads(payload) == {
        "message": str(error),
        "reason": error.reason,
        "cleanup_failure": str(error.cleanup_failure) if run else None,
        "probe_cleanup_failure": str(error.probe_cleanup_failure),
    }
    artifact = result.native_artifacts[-1]
    assert (artifact.digest, artifact.bytes) == (hashlib.sha256(payload).hexdigest(), len(payload))
    assert result.generation.completion_evidence == artifact


@pytest.mark.parametrize("sole", [False, True])
@pytest.mark.parametrize("run", [False, True])
def test_real_probe_failure_survives_result_serialization(tmp_path, monkeypatch, sole, run):
    primary = None if sole else isolate.IsolationUnavailable("missing controller")
    error = _failed_probe(tmp_path, monkeypatch, primary=primary, supported=sole)
    if primary is not None:
        assert error is primary
        assert error.detail == "missing controller"
        assert error.__notes__ == [str(error.probe_cleanup_failure)]
    if run:
        error.cleanup_failure = isolate.CgroupCleanupError("payload group left")
    directory = tmp_path / "report"
    result = sandbox_failure_result(
        error, _identity(_target("python-mutmut"), _context(tmp_path)), directory
    )
    _assert_result(result, error, directory, run=run, sole=sole)


def test_mixed_probe_failure_does_not_depend_on_notes(tmp_path, monkeypatch):
    primary = isolate.IsolationUnavailable("missing controller")
    primary.__notes__ = "cannot append notes"
    error = _failed_probe(tmp_path, monkeypatch, primary=primary)
    assert error is primary
    _assert_errors(isolation_probe_errors(error, "t1"), "probe")


@pytest.mark.parametrize("note", ["probe cgroup was not removed", "probe_cleanup_failed"])
def test_unstructured_notes_do_not_become_cleanup_evidence(tmp_path, note):
    error = isolate.IsolationUnavailable("missing controller")
    error.add_note(note)
    directory = tmp_path / "report"
    result = sandbox_failure_result(
        error, _identity(_target("python-mutmut"), _context(tmp_path)), directory
    )
    assert result.cleanup.state is CleanupState.COMPLETE
    assert result.cleanup.errors == ()
    assert "probe_cleanup_failure" not in json.loads(
        (directory / "results/isolation-failure.json").read_bytes()
    )


@pytest.mark.parametrize("supported", [False, True])
@pytest.mark.parametrize("removal", [None, FileNotFoundError(errno.ENOENT, "gone")])
def test_successful_or_already_gone_probe_has_no_typed_failure(
    tmp_path, monkeypatch, supported, removal
):
    check = _probe(tmp_path, monkeypatch, supported, removal)
    if supported:
        assert check() is None
    else:
        with pytest.raises(isolate.IsolationUnavailable) as found:
            check()
        assert found.value.probe_cleanup_failure is None
        assert not hasattr(found.value, "__notes__")


@pytest.mark.parametrize(
    "primary", [KeyboardInterrupt(), SystemExit(3), asyncio.CancelledError(), MemoryError()]
)
def test_probe_cleanup_keeps_fatal_exception_identity(tmp_path, monkeypatch, primary):
    check = _probe(tmp_path, monkeypatch, False, OSError(errno.EBUSY, "busy"))

    def fail(path):
        raise primary

    monkeypatch.setattr(isolate.os.path, "exists", fail)
    with pytest.raises(type(primary)) as found:
        check()
    assert found.value is primary
    assert "probe cgroup was not removed" in primary.__notes__[0]


@pytest.mark.parametrize("sole", [False, True])
def test_mutmut_capability_keeps_real_probe_cleanup(tmp_path, monkeypatch, sole):
    error = _failed_probe(tmp_path, monkeypatch, supported=sole)
    report = _probe_with(monkeypatch, error)
    assert report.state is CapabilityState.UNAVAILABLE_ISOLATION
    _assert_errors(report.errors, "probe", sole=sole)


@pytest.mark.parametrize("run", [False, True])
@pytest.mark.parametrize("sole", [False, True])
def test_gremlins_capability_keeps_real_probe_and_run_cleanup(tmp_path, monkeypatch, run, sole):
    error = _failed_probe(tmp_path, monkeypatch, supported=sole)
    if run:
        error.cleanup_failure = isolate.CgroupCleanupError("payload group left")
    report, _, _ = _gremlins_probe(tmp_path, monkeypatch, error)
    assert report.state is CapabilityState.UNAVAILABLE_ISOLATION
    _assert_errors(report.errors, "probe", run=run, sole=sole)


@pytest.mark.parametrize(("name", "fail_at"), _FAILURE_POINTS)
def test_real_adapter_run_keeps_combined_probe_failure(tmp_path, monkeypatch, name, fail_at):
    error = _failed_probe(tmp_path, monkeypatch)
    result = _run_failing_at(tmp_path, monkeypatch, name, fail_at, lambda _: error)
    _assert_result(
        result, error, tmp_path / "state/runs/run-a", phase="baseline" if fail_at == 0 else "mutation"
    )
    assert len(result.command_receipts) == fail_at


@pytest.mark.parametrize("fail_at", [0, 1])
def test_patch_corpus_run_keeps_combined_probe_failure(tmp_path, monkeypatch, fail_at):
    error = _failed_probe(tmp_path, monkeypatch)
    result = _corpus_run(tmp_path, monkeypatch, lambda _: error, fail_at=fail_at)
    _assert_result(
        result, error, tmp_path / "state/runs/run-a", phase="baseline" if fail_at == 0 else "mutation"
    )


def test_mutmut_run_keeps_combined_probe_failure(tmp_path, monkeypatch):
    error = _failed_probe(tmp_path, monkeypatch)
    from code_forge.mutation_engines.adapters.python_mutmut import MutmutAdapter

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(MutmutAdapter, "_run_sandboxed", fail)
    result = _run(tmp_path, monkeypatch)
    _assert_result(result, error, tmp_path / "state/runs/run-c")
