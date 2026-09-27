"""Real cargo-mutants run against the rust boundary fixture.

The strong assertion kills every mutant of `n >= 0`. Weakening it so the
negative side is unchecked lets one mutant survive. Both run inside the
sandbox. A caught mutant counts only when its own log shows a failed test.
"""

import hashlib
import os
import shutil
import stat
from pathlib import Path

import pytest

from code_forge.mutation_engines.adapters.base import ExecutionContext, InputEntry, InputSnapshot
from code_forge.mutation_engines.adapters.rust_cargo_mutants import CargoMutantsAdapter
from code_forge.mutation_engines.schemas import BaselineState, Budget, NormalizedStatus, RunState, TargetDeclaration
from code_forge.mutation_engines.targets import TargetSelection

CARGO_MUTANTS = "/home/houminxi/code/hermes/cache/scratch/cargo-tools/bin/cargo-mutants"
CGROUP_ROOT = "/sys/fs/cgroup/user.slice/user-%d.slice/user@%d.service" % (
    os.getuid(),
    os.getuid(),
)
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mutation_contract" / "rust_boundary"
NEGATIVE = "        assert!(!allows(-1));\n"

requires = pytest.mark.skipif(
    not (os.path.isdir(CGROUP_ROOT) and os.path.isfile(CARGO_MUTANTS)),
    reason="host lacks cgroup delegation or the qualified cargo-mutants",
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
        evidence_mb=32,
    )


def _target() -> TargetDeclaration:
    return TargetDeclaration(
        id="rust-boundary",
        adapter="rust-cargo-mutants",
        root=".",
        sources=("src/*.rs",),
        tests=("src/lib.rs",),
        inputs=(),
        oracle="cargo-test",
        command=("/opt/cargo/cargo", "test", "--offline"),
        execution_profile="local",
        environment="host",
        budget=_budget(),
    )


def _context(tmp_path: Path) -> ExecutionContext:
    return ExecutionContext(
        run_id="run-rust1",
        config_digest="c" * 64,
        execution_policy_digest="e" * 64,
        toolchain_fingerprint="rust-test",
        cgroup_root=CGROUP_ROOT,
        state_root=str(tmp_path / "state"),
        approved_python="/usr/bin/python3",
        approved_node=os.path.expanduser("~/.cargo/bin/cargo"),
        memory_mb=1024,
        pids=256,
        workspace_mb=256,
        process_headroom_mb=128,
        extra_node_paths=(CARGO_MUTANTS,),
    )


def _copy(dest: Path, weak: bool) -> None:
    dest.mkdir(parents=True)
    shutil.copytree(FIXTURE, dest, dirs_exist_ok=True)
    if weak:
        lib = dest / "src" / "lib.rs"
        lib.write_text(lib.read_text().replace(NEGATIVE, ""))


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
        reviewed_source_id="rev-rust",
        manifest_digest="m" * 64,
        selection_digest="s" * 64,
        root=str(root),
        files=tuple(files),
    )


def _selection() -> TargetSelection:
    return TargetSelection(
        target_id="rust-boundary",
        granularity="full",
        files=("src/lib.rs",),
        line_ranges={},
        reasons=("source-change",),
    )


def _statuses(tmp_path: Path, weak: bool) -> dict[str, NormalizedStatus]:
    dest = tmp_path / "proj"
    _copy(dest, weak)
    result = CargoMutantsAdapter().run(
        _target(), _selection(), _snapshot(dest), _context(tmp_path)
    )
    assert result.run_state is RunState.COMPLETE, result.reason_code
    assert result.baseline.state is BaselineState.PASSED
    assert result.baseline.test_count >= 1
    return {item.mutant_id: item.normalized_status for item in result.outcomes}


@requires
def test_strong_boundary_kills_every_mutant(tmp_path):
    statuses = _statuses(tmp_path, weak=False)
    assert statuses
    assert set(statuses.values()) == {NormalizedStatus.KILLED}


@requires
def test_weakened_boundary_lets_one_mutant_survive(tmp_path):
    statuses = _statuses(tmp_path, weak=True)
    assert NormalizedStatus.KILLED in statuses.values()
    assert NormalizedStatus.SURVIVED in statuses.values()
