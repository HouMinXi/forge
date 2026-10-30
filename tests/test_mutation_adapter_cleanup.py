"""Every sandboxing adapter turns a sandbox failure into an incomplete result."""

from __future__ import annotations

import hashlib
import json
import shutil
import stat
import sys
import tempfile
from pathlib import Path

import pytest

from code_forge.mutation_engines import isolate
from code_forge.mutation_engines.adapters.base import (
    CapabilityState,
    ExecutionContext,
    InputEntry,
    InputSnapshot,
)
from code_forge.mutation_engines.adapters import patch_corpus
from code_forge.mutation_engines.adapters.go_gremlins import GremlinsAdapter
from code_forge.mutation_engines.adapters.js_stryker import StrykerAdapter
from code_forge.mutation_engines.adapters.python_mutmut import MutmutAdapter
from code_forge.mutation_engines.adapters.rust_cargo_mutants import CargoMutantsAdapter
from code_forge.mutation_engines.corpus import compute_source_digest
from code_forge.mutation_engines.schemas import (
    BaselineState,
    Budget,
    CleanupState,
    CommandReceipt,
    RunState,
    TargetDeclaration,
)
from code_forge.mutation_engines.targets import TargetSelection

_LEFT_BEHIND = "run cgroup was not removed before the deadline: /sys/fs/cgroup/forge-run"


def _receipt(receipt_id: str) -> CommandReceipt:
    return CommandReceipt(
        id=receipt_id,
        run_id="run-a",
        target_id="t1",
        executable_digest="b" * 64,
        argv=("tool",),
        started_at="t0",
        finished_at="t1",
        exit_code=4,
        signal=None,
        timeout=False,
        applied_limits={},
        resource_events={},
        evidence_refs=(),
    )


def _left_behind(receipt_id: str) -> isolate.CgroupCleanupError:
    error = isolate.CgroupCleanupError(_LEFT_BEHIND)
    error.receipt = _receipt(receipt_id)
    error.exit_code = 4
    error.timed_out = False
    return error


def _context(tmp_path: Path, **overrides) -> ExecutionContext:
    fields = dict(
        run_id="run-a",
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
        approved_node=sys.executable,
    )
    fields.update(overrides)
    return ExecutionContext(**fields)


def _target(adapter_id: str, **overrides) -> TargetDeclaration:
    fields = dict(
        id="t1",
        adapter=adapter_id,
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
    fields.update(overrides)
    return TargetDeclaration(**fields)


def _snapshot(tmp_path: Path) -> InputSnapshot:
    return InputSnapshot(
        reviewed_source_id="rev-1",
        manifest_digest="m" * 64,
        selection_digest="s" * 64,
        root=str(tmp_path),
        files=(),
    )


def _node_modules(tmp_path: Path) -> dict:
    modules = tmp_path / "node_modules"
    modules.mkdir(exist_ok=True)
    return {"extra_node_paths": (str(modules),)}


# Each adapter: its command receipt ids in order, the context it needs, and
# the native baseline output that reads as PASSED.
_ADAPTERS = {
    "go-gremlins": (
        GremlinsAdapter,
        ("gotest-run-a", "inventory-run-a", "outcomes-run-a"),
        lambda tmp_path: {},
        ("baseline.json", '{"Action": "pass", "Test": "TestOk"}\n'),
    ),
    "js-stryker": (
        StrykerAdapter,
        ("vitest-run-a", "stryker-run-a"),
        _node_modules,
        (
            "vitest-baseline.json",
            json.dumps({"numTotalTests": 1, "numFailedTests": 0, "numPassedTests": 1, "success": True}),
        ),
    ),
    "rust-cargo-mutants": (
        CargoMutantsAdapter,
        ("cargo-run-a", "mutants-run-a"),
        lambda tmp_path: {},
        ("baseline.txt", "test result: ok. 1 passed; 0 failed; 0 ignored;\n"),
    ),
}

# (adapter, command that fails): every command of every adapter.
_FAILURE_POINTS = [
    (name, index)
    for name, (_, receipt_ids, _, _) in _ADAPTERS.items()
    for index in range(len(receipt_ids))
]


def _run_failing_at(tmp_path, monkeypatch, name: str, fail_at: int, error):
    """Run the adapter's real run body; command number fail_at raises error(receipt_id)."""
    adapter, _, context_overrides, (baseline_file, baseline_text) = _ADAPTERS[name]
    calls: list[str] = []

    def sandboxed(self, context, argv, timeout, workspace, receipt_id, target_id, **kwargs):
        calls.append(receipt_id)
        if len(calls) - 1 == fail_at:
            raise error(receipt_id)
        (workspace / baseline_file).write_text(baseline_text, encoding="utf-8")
        return 0, False, _receipt(receipt_id)

    monkeypatch.setattr(MutmutAdapter, "_run_sandboxed", sandboxed)
    selection = TargetSelection(
        target_id="t1", granularity="file", files=(), line_ranges={}, reasons=("source-change",)
    )
    context = _context(tmp_path, **context_overrides(tmp_path))
    result = adapter().run(_target(adapter.id), selection, _snapshot(tmp_path), context)
    assert len(calls) == fail_at + 1
    return result


def _assert_baseline_kept(result, fail_at: int, receipt_ids, last_artifact: str):
    if fail_at == 0:
        assert result.baseline.state is BaselineState.UNKNOWN
        assert result.native_artifacts[:-1] == ()
    else:
        assert result.baseline.state is BaselineState.PASSED
        assert result.baseline.command_receipt.id == receipt_ids[0]
        assert result.native_artifacts[:-1] == result.baseline.native_evidence
    assert result.native_artifacts[-1].relative_run_path == "results/" + last_artifact
    assert result.generation.completion_evidence == result.native_artifacts[-1]


def _assert_reported(result, tmp_path: Path, artifact_name: str):
    """The result names its target and versions, and its evidence matches the file."""
    identity = result.identity
    assert identity.target_id == "t1"
    assert identity.adapter_version != identity.tool_version
    assert (result.target_id, result.adapter_version, result.tool_version) == (
        "t1",
        identity.adapter_version,
        identity.tool_version,
    )
    assert result.generation.extractor_version == identity.adapter_version
    assert result.cleanup.owned_mounts_removed is True
    for error in result.infrastructure_errors + result.cleanup.errors:
        assert error.target_id == "t1"
    payload = (tmp_path / "state" / "runs" / "run-a" / "results" / artifact_name).read_bytes()
    evidence = result.native_artifacts[-1]
    assert evidence.relative_run_path == "results/" + artifact_name
    assert (evidence.digest, evidence.bytes) == (hashlib.sha256(payload).hexdigest(), len(payload))


@pytest.mark.parametrize(("name", "fail_at"), _FAILURE_POINTS)
def test_run_cgroup_left_behind_keeps_everything_gathered_before_it(
    tmp_path, monkeypatch, name, fail_at
):
    adapter, receipt_ids, _, _ = _ADAPTERS[name]

    result = _run_failing_at(tmp_path, monkeypatch, name, fail_at, _left_behind)

    assert result.adapter_id == adapter.id
    assert result.run_state is RunState.INCOMPLETE
    assert result.reason_code == "cgroup-cleanup-failed"
    assert result.cleanup.state is CleanupState.INCOMPLETE
    assert result.cleanup.owned_group_empty is False
    [error] = result.cleanup.errors
    assert (error.message, error.phase, error.retryable) == (_LEFT_BEHIND, "cleanup", True)
    assert result.infrastructure_errors == (error,)
    # Every earlier receipt, then the one carried on the error.
    assert [r.id for r in result.command_receipts] == list(receipt_ids[: fail_at + 1])
    report = tmp_path / "state" / "runs" / "run-a" / "results" / "cleanup-failure.json"
    assert json.loads(report.read_text()) == {
        "message": _LEFT_BEHIND,
        "exit_code": 4,
        "timed_out": False,
    }
    _assert_baseline_kept(result, fail_at, receipt_ids, "cleanup-failure.json")
    _assert_reported(result, tmp_path, "cleanup-failure.json")


@pytest.mark.parametrize(("name", "fail_at"), _FAILURE_POINTS)
def test_run_without_isolation_is_an_incomplete_result(tmp_path, monkeypatch, name, fail_at):
    adapter, receipt_ids, _, _ = _ADAPTERS[name]

    def unavailable(receipt_id):
        return isolate.IsolationUnavailable("no cgroup2")

    result = _run_failing_at(tmp_path, monkeypatch, name, fail_at, unavailable)

    assert result.adapter_id == adapter.id
    assert result.run_state is RunState.INCOMPLETE
    assert result.reason_code == "isolation-unavailable"
    [error] = result.infrastructure_errors
    # The isolation check failed on a run command, not in the probe: the
    # baseline command if it was first, otherwise a mutation command.
    phase = "baseline" if fail_at == 0 else "mutation"
    assert (error.code, error.phase, error.retryable) == ("isolation-unavailable", phase, False)
    assert error.message == "isolation_unavailable: no cgroup2"
    # The command never started, so it has no receipt; earlier ones stay.
    assert [r.id for r in result.command_receipts] == list(receipt_ids[:fail_at])
    assert result.cleanup.state is CleanupState.COMPLETE
    assert result.cleanup.owned_group_empty is True
    assert result.cleanup.errors == ()
    report = tmp_path / "state" / "runs" / "run-a" / "results" / "isolation-failure.json"
    assert json.loads(report.read_text()) == {
        "message": "isolation_unavailable: no cgroup2",
        "reason": "isolation_unavailable",
        "cleanup_failure": None,
    }
    _assert_baseline_kept(result, fail_at, receipt_ids, "isolation-failure.json")
    _assert_reported(result, tmp_path, "isolation-failure.json")


def test_run_probe_cgroup_and_run_cgroup_left_behind_are_both_cleanup_errors(tmp_path, monkeypatch):
    def unavailable(receipt_id):
        error = isolate.IsolationUnavailable("probe left", reason="probe_cleanup_failed")
        error.cleanup_failure = _left_behind(receipt_id)
        return error

    result = _run_failing_at(tmp_path, monkeypatch, "go-gremlins", 2, unavailable)

    assert result.run_state is RunState.INCOMPLETE
    assert result.reason_code == "probe-cleanup-failed"
    assert result.cleanup.state is CleanupState.INCOMPLETE
    assert result.cleanup.owned_group_empty is False
    codes = [(e.code, e.phase, e.retryable) for e in result.cleanup.errors]
    assert codes == [
        ("probe-cleanup-failed", "cleanup", True),
        ("cgroup-cleanup-failed", "cleanup", True),
    ]
    assert [e.message for e in result.cleanup.errors] == [
        "probe_cleanup_failed: probe left",
        _LEFT_BEHIND,
    ]
    assert result.infrastructure_errors == result.cleanup.errors
    report = tmp_path / "state" / "runs" / "run-a" / "results" / "isolation-failure.json"
    assert json.loads(report.read_text())["cleanup_failure"] == _LEFT_BEHIND
    assert result.baseline.state is BaselineState.PASSED
    _assert_reported(result, tmp_path, "isolation-failure.json")


def test_gremlins_probe_cgroup_left_behind_is_a_retryable_cleanup_error(tmp_path, monkeypatch):
    def stuck(self, *args, **kwargs):
        raise _left_behind("probe-run-a")

    monkeypatch.setattr(MutmutAdapter, "_run_sandboxed", stuck)

    report = GremlinsAdapter().probe(_target(GremlinsAdapter.id), _context(tmp_path))

    assert report.state is CapabilityState.UNAVAILABLE_ISOLATION
    [error] = report.errors
    assert (error.code, error.phase, error.retryable) == ("cgroup-cleanup-failed", "cleanup", True)


def _gremlins_probe(tmp_path, monkeypatch, outcome):
    """Probe gremlins with a fake sandbox; return the report and the probe workspaces."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    binary = tmp_path / "gremlins"
    binary.write_text("")
    workspaces: list[Path] = []

    def sandboxed(self, context, argv, timeout, workspace, receipt_id, target_id, **kwargs):
        assert workspace.is_dir()
        workspaces.append(workspace)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome, False, _receipt(receipt_id)

    monkeypatch.setattr(MutmutAdapter, "_run_sandboxed", sandboxed)
    context = _context(tmp_path, extra_node_paths=(str(binary),))
    return GremlinsAdapter().probe(_target(GremlinsAdapter.id), context), workspaces, scratch


@pytest.mark.parametrize(
    "outcome",
    [0, 1, _left_behind("probe-run-a"), isolate.IsolationUnavailable("no cgroup2")],
    ids=["supported", "failed", "cgroup-left-behind", "no-isolation"],
)
def test_gremlins_probe_leaves_no_workspace_behind(tmp_path, monkeypatch, outcome):
    _, workspaces, scratch = _gremlins_probe(tmp_path, monkeypatch, outcome)

    [workspace] = workspaces
    assert workspace.parent == scratch
    assert workspace.name.startswith("forge-gremlins-probe-")
    assert list(scratch.iterdir()) == []


@pytest.mark.parametrize(
    ("reason", "code", "phase", "retryable"),
    [
        ("isolation_unavailable", "isolation-unavailable", "probe", False),
        ("probe_cleanup_failed", "probe-cleanup-failed", "cleanup", True),
    ],
)
def test_gremlins_probe_without_isolation_is_unavailable_isolation(
    tmp_path, monkeypatch, reason, code, phase, retryable
):
    error = isolate.IsolationUnavailable("no cgroup2", reason=reason)

    report, _, _ = _gremlins_probe(tmp_path, monkeypatch, error)

    assert report.state is CapabilityState.UNAVAILABLE_ISOLATION
    [found] = report.errors
    assert (found.code, found.phase, found.retryable) == (code, phase, retryable)


def test_gremlins_probe_reports_a_run_cgroup_left_behind_after_a_start_failure(tmp_path, monkeypatch):
    error = isolate.IsolationUnavailable("limit readback mismatch")
    error.cleanup_failure = _left_behind("probe-run-a")

    report, _, _ = _gremlins_probe(tmp_path, monkeypatch, error)

    assert report.state is CapabilityState.UNAVAILABLE_ISOLATION
    codes = [(e.code, e.phase, e.retryable) for e in report.errors]
    assert codes == [
        ("isolation-unavailable", "probe", False),
        ("cgroup-cleanup-failed", "cleanup", True),
    ]


_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mutation_contract" / "bash_required_ref"
_SCRIPT = "scripts/check-ref.sh"


def _corpus_project(tmp_path: Path) -> InputSnapshot:
    root = tmp_path / "proj"
    shutil.copytree(_FIXTURE, root, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    digest = compute_source_digest((root / _SCRIPT).read_bytes())
    entries = [
        {
            "id": "drop-empty-guard",
            "source": _SCRIPT,
            "source_digest": digest,
            "old": 'echo "ok $ref"\n',
            "new": 'echo "ok"\n',
            "operator": "message-change",
            "test_selector": "tests/test_check_ref.py::test_accepts_ref",
        },
        {
            "id": "change-empty-message",
            "source": _SCRIPT,
            "source_digest": digest,
            "old": 'echo "empty ref" >&2\n',
            "new": 'echo "empty" >&2\n',
            "operator": "message-change",
            "test_selector": "tests/test_check_ref.py::test_accepts_ref",
        },
    ]
    body = {"schema_version": 1, "target_id": "t1", "entries": entries}
    (root / "corpus.json").write_text(json.dumps(body))
    files = tuple(
        InputEntry(
            path=path.relative_to(root).as_posix(),
            digest=hashlib.sha256(path.read_bytes()).hexdigest(),
            mode=stat.S_IMODE(path.stat().st_mode),
            symlink_target=None,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )
    return InputSnapshot(
        reviewed_source_id="rev-1",
        manifest_digest="m" * 64,
        selection_digest="s" * 64,
        root=str(root),
        files=files,
    )


def _passed_event(run_id: str, mutant: str | None) -> dict:
    return {
        "schema_version": 1,
        "plugin": "forge-mutation-report",
        "plugin_version": "1",
        "run_id": run_id,
        "mutant_id": mutant,
        "final": True,
        "collected": 1,
        "executed": 1,
        "failed_assertions": 0,
        "setup_errors": 0,
        "teardown_errors": 0,
        "collection_errors": 0,
        "internal_errors": 0,
        "skipped": 0,
        "exit_status": 0,
        "failed_nodes": [],
    }


def _corpus_run(tmp_path, monkeypatch, error, fail_at: int = 1):
    """Run the patch-corpus adapter; sandboxed command number fail_at raises error(receipt_id).

    Command 0 is the baseline, then one command per corpus entry.
    """
    calls: list[str] = []

    def sandboxed(self, context, argv, timeout, workspace, receipt_id, target_id, mutant_id=None):
        calls.append(receipt_id)
        if len(calls) - 1 == fail_at:
            raise error(receipt_id)
        return 0, False, _receipt(receipt_id)

    def event_bytes(events_dir, run_id, mutant):
        return json.dumps(_passed_event(run_id, mutant), sort_keys=True).encode("utf-8")

    monkeypatch.setattr(MutmutAdapter, "_run_sandboxed", sandboxed)
    monkeypatch.setattr(
        patch_corpus,
        "_load_event",
        lambda events, run_id, mutant: _passed_event(run_id, mutant),
    )
    monkeypatch.setattr(patch_corpus, "_event_bytes", event_bytes)
    target = _target(
        patch_corpus.ADAPTER_ID,
        command=(sys.executable, "-m", "pytest", "-q"),
        corpus="corpus.json",
    )
    selection = TargetSelection(
        target_id="t1", granularity="full", files=(), line_ranges={}, reasons=("source-change",)
    )

    result = patch_corpus.PatchCorpusAdapter().run(
        target, selection, _corpus_project(tmp_path), _context(tmp_path)
    )
    assert len(calls) == fail_at + 1
    return result


def test_patch_corpus_entry_cgroup_left_behind_keeps_the_baseline(tmp_path, monkeypatch):
    result = _corpus_run(tmp_path, monkeypatch, _left_behind)

    assert result.run_state is RunState.INCOMPLETE
    assert result.reason_code == "cgroup-cleanup-failed"
    assert result.cleanup.state is CleanupState.INCOMPLETE
    [error] = result.cleanup.errors
    assert (error.message, error.phase, error.retryable) == (_LEFT_BEHIND, "cleanup", True)
    assert result.baseline.state is BaselineState.PASSED
    assert len(result.command_receipts) == 2
    assert result.command_receipts[0] == result.baseline.command_receipt
    assert result.native_artifacts[-1].relative_run_path == "results/cleanup-failure.json"
    assert len(result.native_artifacts) == 2
    _assert_reported(result, tmp_path, "cleanup-failure.json")


def test_patch_corpus_entry_without_isolation_keeps_the_baseline(tmp_path, monkeypatch):
    def unavailable(receipt_id):
        return isolate.IsolationUnavailable("no cgroup2")

    result = _corpus_run(tmp_path, monkeypatch, unavailable)

    assert result.run_state is RunState.INCOMPLETE
    assert result.reason_code == "isolation-unavailable"
    [error] = result.infrastructure_errors
    assert (error.code, error.phase) == ("isolation-unavailable", "mutation")
    assert result.cleanup.state is CleanupState.COMPLETE
    assert result.baseline.state is BaselineState.PASSED
    assert list(result.command_receipts) == [result.baseline.command_receipt]
    assert result.native_artifacts[-1].relative_run_path == "results/isolation-failure.json"
    assert len(result.native_artifacts) == 2
    _assert_reported(result, tmp_path, "isolation-failure.json")


@pytest.mark.parametrize("error", [_left_behind, lambda receipt_id: isolate.IsolationUnavailable("x")])
def test_patch_corpus_baseline_sandbox_failure_has_an_unknown_baseline(tmp_path, monkeypatch, error):
    result = _corpus_run(tmp_path, monkeypatch, error, fail_at=0)

    assert result.run_state is RunState.INCOMPLETE
    assert result.baseline.state is BaselineState.UNKNOWN
    assert result.native_artifacts[:-1] == ()
    assert result.baseline.native_evidence == (result.native_artifacts[-1],)


def test_patch_corpus_later_entry_failure_keeps_earlier_entries(tmp_path, monkeypatch):
    result = _corpus_run(tmp_path, monkeypatch, _left_behind, fail_at=2)

    assert result.run_state is RunState.INCOMPLETE
    assert result.reason_code == "cgroup-cleanup-failed"
    assert result.baseline.state is BaselineState.PASSED
    # Baseline, first entry, and the failing second entry.
    assert len(result.command_receipts) == 3
    assert result.command_receipts[0] == result.baseline.command_receipt
    paths = [a.relative_run_path for a in result.native_artifacts]
    assert paths == [
        "results/corpus.json",
        "results/event-drop-empty-guard.json",
        "results/cleanup-failure.json",
    ]
    _assert_reported(result, tmp_path, "cleanup-failure.json")
