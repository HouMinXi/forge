"""Stylesheet width mutation through the patch-corpus adapter.

A one-pixel change to the panel width fails the strong browser
assertion and passes the weakened one. Both run inside the sandbox.
"""

import hashlib
import json
import os
import shutil
import stat
from pathlib import Path

import pytest

from code_forge.mutation_engines.adapters.base import ExecutionContext, InputEntry, InputSnapshot
from code_forge.mutation_engines.adapters.patch_corpus import PatchCorpusAdapter
from code_forge.mutation_engines.corpus import compute_source_digest
from code_forge.mutation_engines.schemas import BaselineState, Budget, NormalizedStatus, RunState, TargetDeclaration
from code_forge.mutation_engines.targets import TargetSelection

CGROUP_ROOT = "/sys/fs/cgroup/user.slice/user-%d.slice/user@%d.service" % (os.getuid(), os.getuid())
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mutation_contract" / "css_button_width"
NODE = "/home/houminxi/code/hermes/node/bin"
PLAYWRIGHT = "/home/houminxi/code/hermes/cache/scratch/css-tools/node_modules"
CHROME = "/opt/google/chrome"

requires = pytest.mark.skipif(
    not (os.path.isdir(CGROUP_ROOT) and os.path.isdir(NODE) and os.path.isdir(PLAYWRIGHT) and os.path.isdir(CHROME)),
    reason="host lacks the browser toolchain or a delegated cgroup",
)


def _budget() -> Budget:
    return Budget(
        total_seconds=180, baseline_seconds=60, mutant_seconds=60, concurrency=1,
        memory_mb=1024, processes=128, workspace_mb=128, evidence_mb=16,
    )


def _target() -> TargetDeclaration:
    return TargetDeclaration(
        id="css-button", adapter="patch-corpus", root=".", sources=("style.css",),
        tests=("test_width.py",), inputs=("index.html", "probe.mjs"), oracle="pytest",
        command=("/usr/bin/python3", "-m", "pytest", "-q"),
        execution_profile="local", environment="host", budget=_budget(), corpus="corpus.json",
    )


def _context(tmp_path: Path, strong: bool) -> ExecutionContext:
    return ExecutionContext(
        run_id="run-css1", config_digest="c" * 64, execution_policy_digest="e" * 64,
        toolchain_fingerprint="css-test", cgroup_root=CGROUP_ROOT,
        state_root=str(tmp_path / "state"), approved_python="/usr/bin/python3",
        memory_mb=1024, pids=128, workspace_mb=128, process_headroom_mb=64,
        extra_python_paths=("/home/houminxi/.local/lib/python3.12/site-packages",),
        extra_node_paths=(NODE, PLAYWRIGHT, CHROME),
        approved_node="strong" if strong else "weak",
    )


def _copy(dest: Path, weak: bool) -> None:
    dest.mkdir()
    for name in ("index.html", "style.css", "probe.mjs", "test_width.py"):
        shutil.copy(FIXTURE / name, dest / name)
    if weak:
        env = {"FORGE_CSS_STRONG": "0"}
    else:
        env = {"FORGE_CSS_STRONG": "1"}
    text = (dest / "test_width.py").read_text().replace(
        'os.environ.get("FORGE_CSS_STRONG", "1")', '"%s"' % env["FORGE_CSS_STRONG"],
    )
    (dest / "test_width.py").write_text(text)
    source = (dest / "style.css").read_bytes()
    body = {
        "schema_version": 1, "target_id": "css-button",
        "entries": [{
            "id": "shrink-panel", "source": "style.css",
            "source_digest": compute_source_digest(source),
            "old": "width: 120px;", "new": "width: 119px;",
            "operator": "width-shrink", "test_selector": "test_width.py::test_panel_width",
        }],
    }
    (dest / "corpus.json").write_text(json.dumps(body))


def _snapshot(root: Path) -> InputSnapshot:
    files = [
        InputEntry(
            path=path.relative_to(root).as_posix(),
            digest=hashlib.sha256(path.read_bytes()).hexdigest(),
            mode=stat.S_IMODE(path.stat().st_mode), symlink_target=None,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]
    return InputSnapshot(
        reviewed_source_id="rev-css", manifest_digest="m" * 64, selection_digest="s" * 64,
        root=str(root), files=tuple(files),
    )


def _selection() -> TargetSelection:
    return TargetSelection(
        target_id="css-button", granularity="file", files=("style.css",), line_ranges={}, reasons=("source",),
    )


def _status(tmp_path: Path, weak: bool) -> tuple[BaselineState, dict[str, NormalizedStatus]]:
    project = tmp_path / "proj"
    _copy(project, weak)
    result = PatchCorpusAdapter().run(_target(), _selection(), _snapshot(project), _context(tmp_path, not weak))
    assert result.run_state is RunState.COMPLETE, result.reason_code
    return result.baseline.state, {item.mutant_id: item.normalized_status for item in result.outcomes}


@requires
def test_width_mutation_is_killed(tmp_path):
    baseline, statuses = _status(tmp_path, weak=False)
    assert baseline is BaselineState.PASSED
    assert statuses == {"shrink-panel": NormalizedStatus.KILLED}


@requires
def test_weakened_width_assertion_lets_it_survive(tmp_path):
    baseline, statuses = _status(tmp_path, weak=True)
    assert baseline is BaselineState.PASSED
    assert statuses == {"shrink-panel": NormalizedStatus.SURVIVED}
