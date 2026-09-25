"""Real gremlins run against the go boundary fixture.

The strong assertion kills both mutants of `n >= 0`. Weakening it so the
zero boundary is unchecked lets one mutant live. Both run inside the sandbox.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

import pytest

from code_forge.mutation_engines.adapters.base import ExecutionContext, InputEntry, InputSnapshot
from code_forge.mutation_engines.adapters.go_gremlins import GremlinsAdapter
from code_forge.mutation_engines.schemas import (
    Budget,
    NormalizedStatus,
    RunState,
    TargetDeclaration,
)
from code_forge.mutation_engines.targets import TargetSelection

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mutation_contract" / "go_boundary"
GREMLINS = "/home/houminxi/code/hermes/cache/scratch/go-tools"
CGROUP_ROOT = "/sys/fs/cgroup/user.slice/user-%d.slice/user@%d.service" % (
    os.getuid(),
    os.getuid(),
)


def _budget() -> Budget:
    return Budget(
        total_seconds=300,
        baseline_seconds=60,
        mutant_seconds=180,
        concurrency=1,
        memory_mb=1024,
        processes=256,
        workspace_mb=256,
        evidence_mb=16,
    )


def _target() -> TargetDeclaration:
    return TargetDeclaration(
        id="go-boundary",
        adapter="go-gremlins",
        root=".",
        sources=("*.go",),
        tests=("*_test.go",),
        inputs=(),
        oracle="go-test",
        command=("go", "test", "./..."),
        execution_profile="local",
        environment="host",
        budget=_budget(),
    )


def _context(tmp_path: Path) -> ExecutionContext:
    return ExecutionContext(
        run_id="run-go1",
        config_digest="c" * 64,
        execution_policy_digest="e" * 64,
        toolchain_fingerprint="go-test",
        cgroup_root=CGROUP_ROOT,
        state_root=str(tmp_path / "state"),
        approved_python="/usr/bin/python3",
        approved_node=GREMLINS + "/gremlins",
        memory_mb=1024,
        pids=256,
        workspace_mb=256,
        process_headroom_mb=64,
        extra_node_paths=(GREMLINS,),
    )


def _copy(dest: Path, weak: bool) -> None:
    dest.mkdir()
    for name in ("go.mod", "probe.go", "probe_test.go"):
        (dest / name).write_bytes((FIXTURE / name).read_bytes())
    if weak:
        text = (dest / "probe_test.go").read_text()
        text = text.replace(
            '    if !Allows(0) {\n        t.Fatal("boundary")\n    }\n',
            "",
        )
        (dest / "probe_test.go").write_text(text)


def _snapshot(root: Path) -> InputSnapshot:
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        files.append(
            InputEntry(
                path=rel,
                digest=hashlib.sha256(path.read_bytes()).hexdigest(),
                mode=stat.S_IMODE(path.stat().st_mode),
                symlink_target=None,
            )
        )
    return InputSnapshot(
        reviewed_source_id="rev-go",
        manifest_digest="m" * 64,
        selection_digest="s" * 64,
        root=str(root),
        files=tuple(files),
    )


def _selection() -> TargetSelection:
    return TargetSelection(
        target_id="go-boundary",
        granularity="full",
        files=("probe.go",),
        line_ranges={},
        reasons=("source-change",),
    )


def _statuses(tmp_path: Path, weak: bool) -> dict[str, NormalizedStatus]:
    root = tmp_path / "fixture"
    _copy(root, weak)
    result = GremlinsAdapter().run(
        _target(), _selection(), _snapshot(root), _context(tmp_path)
    )
    assert result.run_state is RunState.COMPLETE, result.reason_code
    return {item.mutant_id: item.normalized_status for item in result.outcomes}


requires_isolation = pytest.mark.skipif(
    not os.path.isdir(CGROUP_ROOT) or not os.path.isfile(GREMLINS + "/gremlins"),
    reason="host lacks delegated cgroup root or the gremlins binary",
)


@requires_isolation
def test_strong_boundary_kills_both_mutants(tmp_path):
    statuses = _statuses(tmp_path, weak=False)
    assert statuses
    assert set(statuses.values()) == {NormalizedStatus.KILLED}


@requires_isolation
def test_weakened_boundary_lets_one_mutant_live(tmp_path):
    statuses = _statuses(tmp_path, weak=True)
    assert NormalizedStatus.KILLED in statuses.values()
    assert NormalizedStatus.SURVIVED in statuses.values()
