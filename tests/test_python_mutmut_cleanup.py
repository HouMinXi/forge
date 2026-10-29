"""The mutmut adapter keeps the in-flight error and the evidence when cgroup cleanup fails."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

from code_forge.mutation_engines import isolate
from code_forge.mutation_engines.adapters.base import ExecutionContext, InputSnapshot
from code_forge.mutation_engines.adapters import python_mutmut
from code_forge.mutation_engines.adapters.base import CapabilityState
from code_forge.mutation_engines.adapters.python_mutmut import ADAPTER_ID, MutmutAdapter
from code_forge.mutation_engines.schemas import (
    BaselineState,
    Budget,
    CleanupState,
    RunState,
    TargetDeclaration,
)
from code_forge.mutation_engines.targets import TargetSelection

_LEFT_BEHIND = "/sys/fs/cgroup/forge-run: run cgroup was not removed before the deadline"


class _FakeSupervisor:
    """Plays one scripted run: an optional start error, an exit code, a teardown outcome."""

    def __init__(self, script: dict) -> None:
        self.script = script
        self.teardowns = 0

    def wait(self, timeout: float) -> int:
        return self.script.get("code", 0)

    def read_resource_peak(self, name: str) -> int | None:
        error = self.script.get("peak_error")
        if error is not None:
            raise error
        return None

    def teardown(self) -> None:
        self.teardowns += 1
        if "teardown_error" in self.script:
            raise self.script["teardown_error"]
        if self.script.get("cleanup_fails"):
            raise isolate.CgroupCleanupError(_LEFT_BEHIND)


class _FakeThread:
    def __init__(self, supervisor: _FakeSupervisor) -> None:
        self.supervisor = supervisor
        self.joined = False

    def start(self) -> None:
        pass

    def wait_started(self, timeout: float) -> None:
        error = self.supervisor.script.get("start_error")
        if error is not None:
            raise error

    def join(self, timeout: float) -> None:
        self.joined = True


@pytest.fixture
def runs(monkeypatch):
    """Each sandboxed run takes the next script; the fakes are kept for asserts."""
    scripts: list[dict] = []
    made: list[tuple[_FakeSupervisor, _FakeThread]] = []

    def supervisor(spec, root):
        return _FakeSupervisor(scripts.pop(0))

    def thread(sup):
        made.append((sup, _FakeThread(sup)))
        return made[-1][1]

    monkeypatch.setattr(isolate, "Supervisor", supervisor)
    monkeypatch.setattr(isolate, "SupervisorThread", thread)
    return scripts, made


def _context(tmp_path: Path) -> ExecutionContext:
    return ExecutionContext(
        run_id="run-c",
        config_digest="c",
        execution_policy_digest="e",
        toolchain_fingerprint="t",
        cgroup_root="/sys/fs/cgroup/forge",
        state_root=str(tmp_path / "state"),
        approved_python=sys.executable,
        memory_mb=512,
        pids=64,
        workspace_mb=64,
        process_headroom_mb=128,
    )


def _target() -> TargetDeclaration:
    return TargetDeclaration(
        id="t1",
        adapter=ADAPTER_ID,
        root=".",
        sources=("src/**",),
        tests=("tests/**",),
        inputs=(),
        oracle="test",
        command=("true",),
        execution_profile="p",
        environment="e",
        budget=Budget(
            total_seconds=60,
            baseline_seconds=30,
            mutant_seconds=30,
            concurrency=1,
            memory_mb=512,
            processes=8,
            workspace_mb=64,
            evidence_mb=16,
        ),
    )


def _sandboxed(tmp_path: Path):
    return MutmutAdapter()._run_sandboxed(
        _context(tmp_path), ("python", "-V"), 30, tmp_path, "receipt-1", "t1"
    )


@pytest.mark.parametrize(
    "error",
    [RuntimeError("start failed"), KeyboardInterrupt(), SystemExit(3), asyncio.CancelledError()],
    ids=["exception", "keyboard-interrupt", "system-exit", "cancelled"],
)
def test_in_flight_error_survives_a_cleanup_failure(tmp_path, runs, error):
    scripts, made = runs
    scripts.append({"start_error": error, "cleanup_fails": True})

    with pytest.raises(type(error)) as info:
        _sandboxed(tmp_path)

    sup, thread = made[0]
    assert info.value is error
    assert info.value.__notes__ == [_LEFT_BEHIND]
    assert sup.teardowns == 1
    assert thread.joined


def test_in_flight_error_survives_when_the_note_cannot_be_added(tmp_path, runs):
    scripts, made = runs
    error = RuntimeError("start failed")
    error.__notes__ = "not a list"
    scripts.append({"start_error": error, "cleanup_fails": True})

    with pytest.raises(RuntimeError) as info:
        _sandboxed(tmp_path)

    assert info.value is error
    assert made[0][1].joined


def test_in_flight_error_without_cleanup_failure_has_no_note(tmp_path, runs):
    scripts, made = runs
    scripts.append({"start_error": RuntimeError("start failed")})

    with pytest.raises(RuntimeError) as info:
        _sandboxed(tmp_path)

    assert not hasattr(info.value, "__notes__")
    assert made[0][1].joined


@pytest.mark.parametrize(
    "start_error", [None, RuntimeError("start failed")], ids=["after-run", "after-start-error"]
)
def test_thread_is_joined_when_teardown_fails_otherwise(tmp_path, runs, start_error):
    # A teardown error that is not a cgroup cleanup failure propagates, but
    # the supervisor thread is still joined.
    scripts, made = runs
    script = {"teardown_error": OSError(5, "teardown failed")}
    if start_error is not None:
        script["start_error"] = start_error
    scripts.append(script)

    with pytest.raises(OSError):
        _sandboxed(tmp_path)

    assert made[0][1].joined


def test_finished_command_with_a_cgroup_left_behind_fails_closed_with_its_receipt(tmp_path, runs):
    scripts, made = runs
    scripts.append({"code": 7, "cleanup_fails": True})

    with pytest.raises(isolate.CgroupCleanupError) as info:
        _sandboxed(tmp_path)

    assert info.value.exit_code == 7
    assert info.value.timed_out is False
    assert info.value.receipt.exit_code == 7
    assert info.value.receipt.id == "receipt-1"
    assert made[0][1].joined


def test_clean_run_returns_its_receipt(tmp_path, runs):
    scripts, made = runs
    scripts.append({"code": 0})

    code, timed_out, receipt = _sandboxed(tmp_path)

    assert (code, timed_out, receipt.exit_code) == (0, False, 0)
    assert made[0][1].joined


def _run(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(MutmutAdapter, "_materialize", lambda self, snapshot, workspace: None)
    monkeypatch.setattr(MutmutAdapter, "_selected_sources", lambda self, t, s, w: ())
    monkeypatch.setattr(MutmutAdapter, "_write_mutmut_config", lambda self, t, w, s: None)
    snapshot = InputSnapshot(
        reviewed_source_id="rev-1",
        manifest_digest="m" * 64,
        selection_digest="s" * 64,
        root=str(tmp_path),
        files=(),
    )
    selection = TargetSelection(
        target_id="t1", granularity="file", files=(), line_ranges={}, reasons=("source-change",)
    )
    return MutmutAdapter().run(_target(), selection, snapshot, _context(tmp_path))


def _assert_cleanup_failed(result, run_directory: Path, exit_code: int):
    assert result.run_state is RunState.INCOMPLETE
    assert result.reason_code == "cgroup-cleanup-failed"
    assert result.cleanup.state is CleanupState.INCOMPLETE
    assert result.cleanup.owned_group_empty is False
    assert [e.message for e in result.cleanup.errors] == [_LEFT_BEHIND]
    assert result.cleanup.errors[0].phase == "cleanup"
    assert result.cleanup.errors[0].retryable is True
    assert result.cleanup.errors[0] in result.infrastructure_errors
    report = json.loads((run_directory / "results" / "cleanup-failure.json").read_text())
    assert report == {"message": _LEFT_BEHIND, "exit_code": exit_code, "timed_out": False}
    assert result.native_artifacts[-1].relative_run_path == "results/cleanup-failure.json"
    assert result.generation.completion_evidence == result.native_artifacts[-1]


def test_baseline_cleanup_failure_is_an_incomplete_run(tmp_path, runs, monkeypatch):
    scripts, _ = runs
    scripts.append({"code": 3, "cleanup_fails": True})

    result = _run(tmp_path, monkeypatch)

    _assert_cleanup_failed(result, tmp_path / "state" / "runs" / "run-c", 3)
    assert result.baseline.state is BaselineState.UNKNOWN
    assert result.baseline.command_receipt.id == "baseline-run-c"
    assert [r.id for r in result.command_receipts] == ["baseline-run-c"]


def test_mutmut_cleanup_failure_keeps_the_passed_baseline(tmp_path, runs, monkeypatch):
    scripts, _ = runs
    scripts.extend([{"code": 0}, {"code": 5, "cleanup_fails": True}])

    def baseline(self, target, selection, workspace, events_dir, context):
        _, _, receipt = self._run_sandboxed(
            context, ("pytest",), 30, workspace, "baseline-" + context.run_id, target.id
        )
        return BaselineState.PASSED, receipt

    def mutmut(self, workspace, events_dir, context, target):
        _, _, receipt = self._run_sandboxed(
            context, ("mutmut", "run"), 30, workspace, "mutmut-" + context.run_id, target.id
        )
        return receipt

    monkeypatch.setattr(MutmutAdapter, "_run_baseline", baseline)
    monkeypatch.setattr(MutmutAdapter, "_run_mutmut", mutmut)

    result = _run(tmp_path, monkeypatch)

    _assert_cleanup_failed(result, tmp_path / "state" / "runs" / "run-c", 5)
    assert result.baseline.state is BaselineState.PASSED
    assert [r.id for r in result.command_receipts] == ["baseline-run-c", "mutmut-run-c"]


def _two_commands(monkeypatch):
    def baseline(self, target, selection, workspace, events_dir, context):
        _, _, receipt = self._run_sandboxed(
            context, ("pytest",), 30, workspace, "baseline-" + context.run_id, target.id
        )
        return BaselineState.PASSED, receipt

    def mutmut(self, workspace, events_dir, context, target):
        _, _, receipt = self._run_sandboxed(
            context, ("mutmut", "run"), 30, workspace, "mutmut-" + context.run_id, target.id
        )
        return receipt

    monkeypatch.setattr(MutmutAdapter, "_run_baseline", baseline)
    monkeypatch.setattr(MutmutAdapter, "_run_mutmut", mutmut)


def test_isolation_unavailable_at_start_keeps_the_passed_baseline(tmp_path, runs, monkeypatch):
    scripts, made = runs
    scripts.extend([{"code": 0}, {"start_error": isolate.IsolationUnavailable("no cgroup2")}])
    _two_commands(monkeypatch)

    result = _run(tmp_path, monkeypatch)

    assert result.run_state is RunState.INCOMPLETE
    assert result.reason_code == "isolation-unavailable"
    assert result.baseline.state is BaselineState.PASSED
    assert [r.id for r in result.command_receipts] == ["baseline-run-c"]
    [error] = result.infrastructure_errors
    assert (error.code, error.phase, error.retryable) == ("isolation-unavailable", "mutation", False)
    assert result.cleanup.state is CleanupState.COMPLETE
    assert result.cleanup.owned_group_empty is True
    assert made[1][0].teardowns == 1
    assert made[1][1].joined


def test_isolation_unavailable_on_the_baseline_command_is_a_baseline_error(tmp_path, runs, monkeypatch):
    scripts, _ = runs
    scripts.append({"start_error": isolate.IsolationUnavailable("no cgroup2")})
    _two_commands(monkeypatch)

    result = _run(tmp_path, monkeypatch)

    assert result.run_state is RunState.INCOMPLETE
    assert result.baseline.state is BaselineState.UNKNOWN
    assert result.command_receipts == ()
    [error] = result.infrastructure_errors
    assert (error.code, error.phase, error.retryable) == ("isolation-unavailable", "baseline", False)
    assert result.cleanup.state is CleanupState.COMPLETE


def test_isolation_unavailable_with_the_run_cgroup_left_behind(tmp_path, runs, monkeypatch):
    scripts, _ = runs
    start_error = isolate.IsolationUnavailable("probe left", reason="probe_cleanup_failed")
    scripts.append({"start_error": start_error, "cleanup_fails": True})

    result = _run(tmp_path, monkeypatch)

    assert result.run_state is RunState.INCOMPLETE
    assert result.reason_code == "probe-cleanup-failed"
    assert result.baseline.state is BaselineState.UNKNOWN
    assert result.command_receipts == ()
    assert result.cleanup.state is CleanupState.INCOMPLETE
    assert result.cleanup.owned_group_empty is False
    codes = [(e.code, e.phase, e.retryable) for e in result.cleanup.errors]
    assert codes == [
        ("probe-cleanup-failed", "cleanup", True),
        ("cgroup-cleanup-failed", "cleanup", True),
    ]
    assert result.cleanup.errors[1].message == _LEFT_BEHIND
    run_directory = tmp_path / "state" / "runs" / "run-c"
    report = json.loads((run_directory / "results" / "isolation-failure.json").read_text())
    assert report == {
        "message": "probe_cleanup_failed: probe left",
        "reason": "probe_cleanup_failed",
        "cleanup_failure": _LEFT_BEHIND,
    }


def test_interrupt_while_reading_the_peak_still_tears_down_and_joins(tmp_path, runs):
    scripts, made = runs
    scripts.append({"code": 0, "peak_error": KeyboardInterrupt()})

    with pytest.raises(KeyboardInterrupt):
        _sandboxed(tmp_path)

    assert made[0][0].teardowns == 1
    assert made[0][1].joined


def _probe_with(monkeypatch, error: isolate.IsolationUnavailable):
    version = SimpleNamespace(returncode=0, stdout=python_mutmut.SUPPORTED_MUTMUT_VERSION + "\n")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: version)

    def verify(root):
        raise error

    monkeypatch.setattr(isolate, "verify_isolation_support", verify)
    return MutmutAdapter().probe(_target(), _context(Path("/nonexistent")))


def test_probe_cgroup_left_behind_is_a_retryable_cleanup_error(monkeypatch):
    report = _probe_with(
        monkeypatch, isolate.IsolationUnavailable("probe left", reason="probe_cleanup_failed")
    )

    assert report.state is CapabilityState.UNAVAILABLE_ISOLATION
    [error] = report.errors
    assert (error.code, error.phase, error.retryable) == ("probe-cleanup-failed", "cleanup", True)


def test_probe_without_isolation_is_a_permanent_probe_error(monkeypatch):
    report = _probe_with(monkeypatch, isolate.IsolationUnavailable("no cgroup2"))

    assert report.state is CapabilityState.UNAVAILABLE_ISOLATION
    [error] = report.errors
    assert (error.code, error.phase, error.retryable) == ("isolation-unavailable", "probe", False)
