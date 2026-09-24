"""V07: real mutmut run inside the isolation sandbox.

One killed mutant (test detects it) and one surviving mutant (x*0 -> x*1
at x=0 is still 0, so the test cannot see it) prove the adapter maps both
sides from real native artifacts, not fixtures of its own making.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from code_forge.mutation_engines.adapters.base import (
    ExecutionContext,
    InputEntry,
    InputSnapshot,
)
from code_forge.mutation_engines.adapters.python_mutmut import (
    ADAPTER_ID,
    MutmutAdapter,
)
from code_forge.mutation_engines.schemas import (
    BaselineState,
    Budget,
    NormalizedStatus,
    RunState,
    TargetDeclaration,
)
from code_forge.mutation_engines.targets import TargetSelection

_build_outcomes = MutmutAdapter._build_outcomes
_collect_meta = MutmutAdapter._collect_meta

CGROUP_ROOT = "/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service"

import mutmut  # noqa: E402

MUTMUT_SITE = str(Path(mutmut.__file__).resolve().parent.parent)

CALC = '''def add(a, b):
    return a + b


def double(x):
    return x * 0
'''

TEST_CALC = '''from calc import add, double


def test_add():
    assert add(1, 2) == 3


def test_double():
    assert double(0) == 0
'''


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _snapshot(root: Path) -> InputSnapshot:
    entries = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(root))
            entries.append(
                InputEntry(
                    path=rel,
                    digest=_sha256(path.read_bytes()),
                    mode=path.stat().st_mode & 0o777,
                    symlink_target=None,
                )
            )
    return InputSnapshot(
        reviewed_source_id="review-v07",
        manifest_digest=_sha256(b"manifest"),
        selection_digest=_sha256(b"selection"),
        root=str(root),
        files=tuple(entries),
    )


def _target() -> TargetDeclaration:
    return TargetDeclaration(
        id="py-t",
        adapter=ADAPTER_ID,
        root=".",
        sources=("calc.py",),
        tests=("test_calc.py",),
        inputs=(),
        oracle="pytest",
        command=("python3", "-m", "pytest"),
        execution_profile="p",
        environment="e",
        budget=Budget(
            total_seconds=420,
            baseline_seconds=120,
            mutant_seconds=300,
            concurrency=1,
            memory_mb=1024,
            processes=128,
            workspace_mb=128,
            evidence_mb=32,
        ),
    )


def _context(state_root: str) -> ExecutionContext:
    return ExecutionContext(
        run_id="run-v07",
        config_digest="c",
        execution_policy_digest="e",
        toolchain_fingerprint="t",
        cgroup_root=CGROUP_ROOT,
        state_root=state_root,
        approved_python="/usr/bin/python3",
        memory_mb=1024,
        pids=128,
        workspace_mb=128,
        process_headroom_mb=128,
        extra_python_paths=(MUTMUT_SITE,),
    )


def _selection() -> TargetSelection:
    return TargetSelection(
        target_id="py-t",
        granularity="full",
        files=("calc.py",),
        line_ranges={},
        reasons=("test",),
    )


@pytest.mark.skipif(
    not os.path.isdir(CGROUP_ROOT), reason="delegated cgroup root unavailable"
)
def test_real_mutmut_run_killed_and_survived(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "calc.py").write_text(CALC)
    (source_root / "test_calc.py").write_text(TEST_CALC)
    state_root = tmp_path / "state"
    state_root.mkdir()

    result = MutmutAdapter().run(
        _target(), _selection(), _snapshot(source_root), _context(str(state_root))
    )

    assert result.run_state is RunState.COMPLETE, result.infrastructure_errors
    assert result.reason_code == "complete"
    assert result.baseline.state is BaselineState.PASSED
    assert result.baseline.test_count == 2

    normalized = {o.mutant_id: o.normalized_status for o in result.outcomes}
    assert result.inventory.generated == len(normalized) >= 2
    killed = [k for k, v in normalized.items() if v is NormalizedStatus.KILLED]
    survived = [k for k, v in normalized.items() if v is NormalizedStatus.SURVIVED]
    assert killed, "expected at least one killed mutant, got %r" % (normalized,)
    assert survived, "expected at least one survived mutant, got %r" % (normalized,)

    # killed verdicts must carry plugin event evidence, not just the label
    for outcome in result.outcomes:
        if outcome.normalized_status is NormalizedStatus.KILLED:
            assert outcome.test_evidence, outcome.mutant_id

    # artifacts really published into the run directory
    results_dir = state_root / "runs" / "run-v07" / "results"
    assert (results_dir / "mutmut-meta.json").exists()

    # receipts: baseline plus mutmut invocation
    assert len(result.command_receipts) == 2
    baseline_receipt = result.command_receipts[0]
    assert baseline_receipt.exit_code == 0
    assert "pytest" in " ".join(baseline_receipt.argv)


def test_build_outcomes_native_killed_without_event_is_unknown(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "calc.py").write_text(CALC)
    events = tmp_path / "events"
    events.mkdir()
    run_dir = tmp_path / "run"
    meta = {"exit_code_by_key": {"x_calc.add__mutmut_1": 1}}
    outcomes, artifacts = _build_outcomes(
        {Path("calc.py"): meta}, events, "run-x", run_dir, workspace
    )
    assert len(outcomes) == 1
    assert outcomes[0].normalized_status is NormalizedStatus.UNKNOWN
    assert outcomes[0].native_status == "killed"
    assert outcomes[0].test_evidence == ()


def test_collect_meta_missing_source_is_hold_signal(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "mutants").mkdir(parents=True)
    (workspace / "mutants" / "a.py.meta").write_text(
        '{"exit_code_by_key": {}, "durations_by_key": {},'
        ' "estimated_durations_by_key": {}}'
    )
    found, missing = _collect_meta(workspace, [Path("a.py"), Path("b.py")])
    assert list(found) == [Path("a.py")]
    assert missing == [Path("b.py")]


def test_collect_meta_empty_map_still_completes(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "mutants").mkdir(parents=True)
    (workspace / "mutants" / "a.py.meta").write_text(
        '{"exit_code_by_key": {}, "durations_by_key": {},'
        ' "estimated_durations_by_key": {}}'
    )
    found, missing = _collect_meta(workspace, [Path("a.py")])
    assert missing == []
    assert found[Path("a.py")]["exit_code_by_key"] == {}
