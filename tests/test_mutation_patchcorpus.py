"""Real patch-corpus run against the bash_required_ref fixture.

Baseline passes. Removing the empty-ref guard is killed by the pytest
oracle. An entry whose source is outside the selection is listed, not
silently dropped.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from code_forge.mutation_engines.adapters.base import (
    ExecutionContext,
    InputEntry,
    InputSnapshot,
)
from code_forge.mutation_engines.adapters.patch_corpus import PatchCorpusAdapter
from code_forge.mutation_engines.corpus import CorpusEntry
from code_forge.mutation_engines.corpus import compute_source_digest
from code_forge.mutation_engines.schemas import (
    BaselineState,
    Budget,
    NormalizedStatus,
    RunState,
    TargetDeclaration,
)
from code_forge.mutation_engines.targets import TargetSelection

FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "mutation_contract"
    / "bash_required_ref"
)
CGROUP_ROOT = "/sys/fs/cgroup/user.slice/user-%d.slice/user@%d.service" % (
    os.getuid(),
    os.getuid(),
)

SCRIPT = "scripts/check-ref.sh"
GUARD = '''if [ -z "$ref" ]; then
    echo "empty ref" >&2
    exit 2
fi
'''


def _budget() -> Budget:
    return Budget(
        total_seconds=120,
        baseline_seconds=60,
        mutant_seconds=20,
        concurrency=1,
        memory_mb=256,
        processes=32,
        workspace_mb=64,
        evidence_mb=16,
    )


def _target() -> TargetDeclaration:
    return TargetDeclaration(
        id="shell-config",
        adapter="patch-corpus",
        root=".",
        sources=("scripts/*.sh",),
        tests=("tests",),
        inputs=(),
        oracle="pytest",
        command=("/usr/bin/python3", "-m", "pytest", "-q"),
        execution_profile="local",
        environment="host",
        budget=_budget(),
        corpus="corpus.json",
    )


def _context(tmp_path: Path) -> ExecutionContext:
    return ExecutionContext(
        run_id="run-corpus1",
        config_digest="c" * 64,
        execution_policy_digest="e" * 64,
        toolchain_fingerprint="py-test",
        cgroup_root=CGROUP_ROOT,
        state_root=str(tmp_path / "state"),
        approved_python="/usr/bin/python3",
        memory_mb=256,
        pids=64,
        workspace_mb=64,
        process_headroom_mb=32,
        extra_python_paths=(
            "/home/houminxi/.local/lib/python3.12/site-packages",
        ),
    )


def _snapshot(root: Path) -> InputSnapshot:
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files.append(
            InputEntry(
                path=rel,
                digest=digest,
                mode=stat.S_IMODE(path.stat().st_mode),
                symlink_target=None,
            )
        )
    return InputSnapshot(
        reviewed_source_id="rev-1",
        manifest_digest="m" * 64,
        selection_digest="s" * 64,
        root=str(root),
        files=tuple(files),
    )


def _write_corpus(root: Path) -> None:
    source = (root / SCRIPT).read_bytes()
    digest = compute_source_digest(source)
    body = {
        "schema_version": 1,
        "target_id": "shell-config",
        "entries": [
            {
                "id": "drop-empty-guard",
                "source": SCRIPT,
                "source_digest": digest,
                "old": GUARD,
                "new": "",
                "operator": "guard-removal",
                "test_selector": "tests/test_check_ref.py::test_rejects_empty_ref",
            },
            {
                "id": "other-source",
                "source": SCRIPT,
                "source_digest": digest,
                "old": 'echo "ok $ref"\n',
                "new": 'echo "ok"\n',
                "operator": "message-change",
                "test_selector": "tests/test_check_ref.py::test_accepts_ref",
            },
        ],
    }
    (root / "corpus.json").write_text(json.dumps(body))


requires_isolation = pytest.mark.skipif(
    not os.path.isdir(CGROUP_ROOT),
    reason="host lacks delegated cgroup root",
)


@requires_isolation
def test_real_corpus_kills_guard_removal(tmp_path):
    root = tmp_path / "proj"
    # copy fixture without the tests' __pycache__
    import shutil

    shutil.copytree(
        FIXTURE,
        root,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    _write_corpus(root)
    selection = TargetSelection(
        target_id="shell-config",
        granularity="file",
        files=(SCRIPT,),
        line_ranges={},
        reasons=("source-change",),
    )
    # select only the guard-removal entry's source, but both entries share
    # that source, so both are in selection. Add a third file-scoped run
    # that excludes the second by selecting a path neither uses? Both share
    # SCRIPT. Outside-selection is covered by a second selection below.
    result = PatchCorpusAdapter().run(
        _target(), selection, _snapshot(root), _context(tmp_path)
    )
    assert result.run_state is RunState.COMPLETE, result.reason_code
    assert result.baseline.state is BaselineState.PASSED
    assert result.reason_code == "corpus-limited"
    by_id = {item.mutant_id: item.normalized_status for item in result.outcomes}
    assert by_id["drop-empty-guard"] is NormalizedStatus.KILLED
    # source bytes are unchanged after the run
    assert GUARD in (root / SCRIPT).read_text()


@requires_isolation
def test_real_corpus_lists_entries_outside_selection(tmp_path):
    root = tmp_path / "proj"
    import shutil

    shutil.copytree(
        FIXTURE, root, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    _write_corpus(root)
    selection = TargetSelection(
        target_id="shell-config",
        granularity="file",
        files=("scripts/not-this.sh",),
        line_ranges={},
        reasons=("unrelated",),
    )
    result = PatchCorpusAdapter().run(
        _target(), selection, _snapshot(root), _context(tmp_path)
    )
    assert result.outcomes == ()
    coverage = [
        item for item in result.native_artifacts
        if item.relative_run_path.endswith("corpus-coverage.json")
    ]
    assert coverage, "coverage statement missing"
    payload = json.loads(
        (tmp_path / "state" / "runs" / "run-corpus1" / coverage[0].relative_run_path).read_text()
    )
    assert payload["coverage"] == "corpus-limited"
    assert "drop-empty-guard" in payload["outside_selection"]
    assert "other-source" in payload["outside_selection"]


def test_apply_rejects_repeated_old_text():
    from code_forge.mutation_engines.adapters.patch_corpus import _apply_entry
    from code_forge.mutation_engines.adapters.python_mutmut import AdapterError
    from code_forge.mutation_engines.corpus import CorpusEntry, compute_source_digest

    source = b"echo a\necho a\n"
    entry = CorpusEntry(
        id="dup",
        source="s.sh",
        source_digest=compute_source_digest(source),
        old="echo a\n",
        new="echo b\n",
        operator="x",
        test_selector="t::t",
    )
    with pytest.raises(AdapterError):
        _apply_entry(source, entry)


def test_mutant_run_uses_mutant_budget(monkeypatch):
    """A corpus entry must not inherit the baseline timeout."""
    seen = []

    def fake_run(self, context, argv, timeout, workspace, receipt_id, target_id, mutant_id=None):
        seen.append((timeout, mutant_id))
        receipt = t_receipt(receipt_id, target_id)
        return 0, False, receipt

    monkeypatch.setattr(
        "code_forge.mutation_engines.adapters.python_mutmut.MutmutAdapter._run_sandboxed",
        fake_run,
    )
    monkeypatch.setattr(
        "code_forge.mutation_engines.adapters.patch_corpus._load_event",
        lambda events, run_id, mutant: _passed_event(run_id, mutant),
    )
    adapter = PatchCorpusAdapter()
    target = _target()
    entry = CorpusEntry(
        id="drop-empty-guard",
        source="scripts/check-ref.sh",
        source_digest="a" * 64,
        old="old",
        new="",
        operator="guard-removal",
        test_selector="tests/test_check_ref.py::test_rejects_empty_ref",
    )
    state, _receipt, _event = adapter._run_selector(
        target, Path("/tmp"), Path("/tmp"), _context(Path("/tmp")), entry, "entry"
    )
    assert seen == [(target.budget.mutant_seconds, "drop-empty-guard")]
    assert state is BaselineState.PASSED


def t_receipt(receipt_id, target_id):
    from code_forge.mutation_engines.schemas import CommandReceipt

    return CommandReceipt(
        id=receipt_id,
        run_id="run-corpus1",
        target_id=target_id,
        executable_digest="b" * 64,
        argv=("/usr/bin/python3", "-m", "pytest"),
        started_at="t0",
        finished_at="t1",
        exit_code=0,
        signal=None,
        timeout=False,
        applied_limits={},
        resource_events={},
        evidence_refs=(),
    )


def _passed_event(run_id, mutant):
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
