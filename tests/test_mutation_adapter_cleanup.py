"""Every sandboxing adapter reports a run cgroup left behind as an incomplete result."""

from __future__ import annotations

import hashlib
import json
import shutil
import stat
import sys
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

_LEFT_BEHIND = "/sys/fs/cgroup/forge-run: run cgroup was not removed before the deadline"


def _left_behind(receipt_id: str) -> isolate.CgroupCleanupError:
    error = isolate.CgroupCleanupError(_LEFT_BEHIND)
    error.receipt = CommandReceipt(
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
    error.exit_code = 4
    error.timed_out = False
    return error


def _context(tmp_path: Path) -> ExecutionContext:
    return ExecutionContext(
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


@pytest.mark.parametrize(
    "adapter",
    [GremlinsAdapter, StrykerAdapter, CargoMutantsAdapter],
    ids=["go-gremlins", "js-stryker", "rust-cargo-mutants"],
)
def test_run_cgroup_left_behind_is_an_incomplete_result(tmp_path, monkeypatch, adapter):
    def stuck(self, *args):
        raise _left_behind("baseline-run-a")

    monkeypatch.setattr(adapter, "_run", stuck)
    selection = TargetSelection(
        target_id="t1", granularity="file", files=(), line_ranges={}, reasons=("source-change",)
    )

    result = adapter().run(_target(adapter.id), selection, _snapshot(tmp_path), _context(tmp_path))

    assert result.adapter_id == adapter.id
    assert result.run_state is RunState.INCOMPLETE
    assert result.reason_code == "cgroup-cleanup-failed"
    assert result.cleanup.state is CleanupState.INCOMPLETE
    assert result.cleanup.owned_group_empty is False
    [error] = result.cleanup.errors
    assert (error.message, error.phase, error.retryable) == (_LEFT_BEHIND, "cleanup", True)
    assert result.infrastructure_errors == (error,)
    assert result.baseline.state is BaselineState.UNKNOWN
    assert [r.id for r in result.command_receipts] == ["baseline-run-a"]
    report = tmp_path / "state" / "runs" / "run-a" / "results" / "cleanup-failure.json"
    assert json.loads(report.read_text()) == {
        "message": _LEFT_BEHIND,
        "exit_code": 4,
        "timed_out": False,
    }
    assert [a.relative_run_path for a in result.native_artifacts] == ["results/cleanup-failure.json"]


def test_gremlins_probe_cgroup_left_behind_is_a_retryable_cleanup_error(tmp_path, monkeypatch):
    def stuck(self, *args, **kwargs):
        raise _left_behind("probe-run-a")

    monkeypatch.setattr(MutmutAdapter, "_run_sandboxed", stuck)

    report = GremlinsAdapter().probe(_target(GremlinsAdapter.id), _context(tmp_path))

    assert report.state is CapabilityState.UNAVAILABLE_ISOLATION
    [error] = report.errors
    assert (error.code, error.phase, error.retryable) == ("cgroup-cleanup-failed", "cleanup", True)


_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mutation_contract" / "bash_required_ref"
_SCRIPT = "scripts/check-ref.sh"


def _corpus_project(tmp_path: Path) -> InputSnapshot:
    root = tmp_path / "proj"
    shutil.copytree(_FIXTURE, root, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    entry = {
        "id": "drop-empty-guard",
        "source": _SCRIPT,
        "source_digest": compute_source_digest((root / _SCRIPT).read_bytes()),
        "old": 'echo "ok $ref"\n',
        "new": 'echo "ok"\n',
        "operator": "message-change",
        "test_selector": "tests/test_check_ref.py::test_accepts_ref",
    }
    body = {"schema_version": 1, "target_id": "t1", "entries": [entry]}
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


def test_patch_corpus_entry_cgroup_left_behind_keeps_the_baseline(tmp_path, monkeypatch):
    def sandboxed(self, context, argv, timeout, workspace, receipt_id, target_id, mutant_id=None):
        if mutant_id is None:
            return 0, False, _left_behind(receipt_id).receipt
        raise _left_behind(receipt_id)

    monkeypatch.setattr(MutmutAdapter, "_run_sandboxed", sandboxed)
    monkeypatch.setattr(
        patch_corpus,
        "_load_event",
        lambda events, run_id, mutant: _passed_event(run_id, mutant),
    )
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

    assert result.run_state is RunState.INCOMPLETE
    assert result.reason_code == "cgroup-cleanup-failed"
    assert result.cleanup.state is CleanupState.INCOMPLETE
    [error] = result.cleanup.errors
    assert (error.message, error.phase, error.retryable) == (_LEFT_BEHIND, "cleanup", True)
    assert result.baseline.state is BaselineState.PASSED
    assert len(result.command_receipts) == 2
    assert result.command_receipts[0] == result.baseline.command_receipt
