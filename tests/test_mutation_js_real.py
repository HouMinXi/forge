"""Real Stryker run against the javascript boundary fixture.

The strong assertion kills every mutant of `n >= 0`. Weakening it so the
zero boundary is unchecked lets `n > 0` survive. Both run inside the sandbox.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from pathlib import Path

import pytest

from code_forge.mutation_engines.adapters.base import (
    ExecutionContext,
    InputEntry,
    InputSnapshot,
)
from code_forge.mutation_engines.adapters.js_stryker import StrykerAdapter
from code_forge.mutation_engines.schemas import (
    BaselineState,
    Budget,
    NormalizedStatus,
    RunState,
    TargetDeclaration,
)
from code_forge.mutation_engines.targets import TargetSelection

CGROUP_ROOT = "/sys/fs/cgroup/user.slice/user-%d.slice/user@%d.service" % (
    os.getuid(),
    os.getuid(),
)
NODE_MODULES = "/home/houminxi/code/hermes/cache/scratch/js-qual/node_modules"
FIXTURE = Path(__file__).resolve().parent / "fixtures/mutation_contract/javascript_boundary"
NODE = "/home/houminxi/.local/bin/node"

requires = pytest.mark.skipif(
    not (os.path.isdir(CGROUP_ROOT) and os.path.isdir(NODE_MODULES) and os.path.isfile(NODE)),
    reason="host lacks cgroup, node, or the qualified stryker tree",
)


def _budget() -> Budget:
    return Budget(
        total_seconds=300,
        baseline_seconds=60,
        mutant_seconds=180,
        concurrency=1,
        memory_mb=1024,
        processes=64,
        workspace_mb=128,
        evidence_mb=16,
    )


def _target() -> TargetDeclaration:
    return TargetDeclaration(
        id="js-boundary",
        adapter="js-stryker",
        root=".",
        sources=("src/*.js",),
        tests=("probe.test.js",),
        inputs=(),
        oracle="vitest",
        command=(NODE, "node_modules/vitest/vitest.mjs", "run"),
        execution_profile="local",
        environment="host",
        budget=_budget(),
    )


def _context(tmp_path: Path) -> ExecutionContext:
    return ExecutionContext(
        run_id="run-js1",
        config_digest="c" * 64,
        execution_policy_digest="e" * 64,
        toolchain_fingerprint="node-test",
        cgroup_root=CGROUP_ROOT,
        state_root=str(tmp_path / "state"),
        approved_python="/usr/bin/python3",
        approved_node=NODE,
        memory_mb=1024,
        pids=128,
        workspace_mb=128,
        process_headroom_mb=64,
        extra_node_paths=(NODE_MODULES,),
    )


def _copy_fixture(dest: Path, weak: bool) -> None:
    shutil.copytree(FIXTURE, dest)
    if not weak:
        return
    test = dest / "probe.test.js"
    text = test.read_text()
    text = text.replace("  expect(allows(0)).toBe(true);\n", "")
    test.write_text(text)


def _snapshot(root: Path) -> InputSnapshot:
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
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
        reviewed_source_id="rev-js",
        manifest_digest="m" * 64,
        selection_digest="s" * 64,
        root=str(root),
        files=tuple(files),
    )


def _selection() -> TargetSelection:
    return TargetSelection(
        target_id="js-boundary",
        granularity="full",
        files=("src/probe.js",),
        line_ranges={},
        reasons=("source-change",),
    )


def _statuses(tmp_path: Path, weak: bool) -> dict[str, NormalizedStatus]:
    root = tmp_path / "proj"
    _copy_fixture(root, weak)
    result = StrykerAdapter().run(_target(), _selection(), _snapshot(root), _context(tmp_path))
    assert result.run_state is RunState.COMPLETE, result.reason_code
    assert result.baseline.state is BaselineState.PASSED
    assert result.inventory.generated == len(result.outcomes) >= 1
    return {item.mutant_id: item.normalized_status for item in result.outcomes}


@requires
def test_strong_boundary_kills_every_mutant(tmp_path):
    statuses = _statuses(tmp_path, weak=False)
    assert NormalizedStatus.KILLED in statuses.values()
    assert NormalizedStatus.SURVIVED not in statuses.values(), statuses


@requires
def test_weakened_boundary_lets_one_mutant_survive(tmp_path):
    statuses = _statuses(tmp_path, weak=True)
    assert NormalizedStatus.KILLED in statuses.values()
    assert NormalizedStatus.SURVIVED in statuses.values(), statuses
