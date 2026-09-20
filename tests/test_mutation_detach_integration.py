"""End-to-end coverage for the detached mutation launcher.

Every other test of ``launch_detached_mutation`` replaces ``subprocess.Popen``
and then runs the generated payload in-process, which means the fork/_exit
detach prologue itself is never executed. That prologue is what reparents the
run to init, so it is exactly the part worth proving. These tests spawn the
real interpreter, let it fork for real, and assert on what the grandchild
leaves on disk.

``run_mutation`` is swapped out through ``sitecustomize`` (loaded at
interpreter start-up, before the payload's own ``sys.path`` manipulation) so
the process tree, the Popen call and the reparenting all stay real while the
mutmut invocation at the bottom is replaced by a recorder.
"""

import json
import os
import sys
import time
from pathlib import Path

import pytest

from code_forge.mutation import launch_detached_mutation

pytestmark = pytest.mark.integration


SITECUSTOMIZE = '''
import json
import os
from pathlib import Path

import code_forge.mutation as _m
from code_forge.disposition import Disposition
from code_forge.state import StateFinding

_RECORD = Path(os.environ["FORGE_TEST_RECORD"])


def _fake_run_mutation(**kwargs):
    """Record the real call the detached grandchild makes, then return clean."""
    payload = {
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "cwd": os.getcwd(),
        "diff_files": kwargs.get("diff_files"),
        "baseline_cmd": kwargs.get("baseline_cmd"),
        "baseline_timeout": kwargs.get("baseline_timeout"),
        "also_copy": kwargs.get("also_copy"),
        "max_children": kwargs.get("max_children"),
        "memory_limit_bytes": kwargs.get("memory_limit_bytes"),
        "mutation_skip_globs": kwargs.get("mutation_skip_globs"),
        "mutation_include_globs": kwargs.get("mutation_include_globs"),
    }
    _RECORD.write_text(json.dumps(payload), encoding="utf-8")
    survivor = StateFinding(
        id="MUTANT_1",
        fingerprint="mutant-1",
        source="MUTANT",
        disposition=Disposition.CONFIRMED,
        file="src/thing.py",
        line_range=[1, 1],
        description="survivor",
    )
    return ([survivor], [])


_m.run_mutation = _fake_run_mutation
'''


def _wait_for(path: Path, timeout: float = 30.0) -> None:
    """Block until the detached grandchild writes ``path``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size > 0:
            return
        time.sleep(0.05)
    raise AssertionError(f"{path} was never written by the detached run")


@pytest.fixture
def detach_env(tmp_path, monkeypatch):
    """Point the spawned interpreter at a sitecustomize that stubs mutmut."""
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    record = tmp_path / "record.json"
    src = str(Path(__file__).resolve().parents[1] / "src")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(shim), src]))
    monkeypatch.setenv("FORGE_TEST_RECORD", str(record))
    return record


@pytest.mark.skipif(not hasattr(os, "fork"), reason="detach path requires fork")
def test_detached_run_reparents_and_reports(tmp_path, detach_env):
    """The launcher returns before the run finishes, and the run survives it."""
    work = tmp_path / "work"
    work.mkdir()
    (work / "thing.py").write_text("x = 1\n", encoding="utf-8")
    result_path = tmp_path / "result.json"

    started = launch_detached_mutation(
        diff_files=["thing.py"],
        baseline_cmd=[sys.executable, "-c", "pass"],
        cwd=work,
        result_path=result_path,
        baseline_timeout=321,
        also_copy=["fixtures/", "conftest.py"],
        max_children=3,
        memory_limit_bytes=64 * 1024**2,
        mutation_skip_globs=["legacy/*.py"],
        mutation_include_globs=["src/*.py"],
    )
    assert started is True, "launcher reported the detached run failed to start"

    _wait_for(result_path)
    data = json.loads(result_path.read_text(encoding="utf-8"))
    assert data["status"] == "done"
    assert data["survivors"] == ["MUTANT_1"]

    record = json.loads(detach_env.read_text(encoding="utf-8"))
    # The middle process exits immediately, so the run is an orphan by the
    # time it calls run_mutation. Its new parent is whoever reaps orphans
    # here -- init under a bare shell, the systemd user manager under a
    # service -- so assert that it is neither this process nor the launched
    # one, rather than hard-coding PID 1 and failing under a subreaper.
    assert record["pid"] != os.getpid()
    assert record["ppid"] != os.getpid()
    assert record["ppid"] != record["pid"]
    assert record["ppid"] > 0


@pytest.mark.skipif(not hasattr(os, "fork"), reason="detach path requires fork")
def test_detached_run_receives_configured_arguments(tmp_path, detach_env):
    """Every caller-supplied knob survives the trip through the fork."""
    work = tmp_path / "work"
    work.mkdir()
    (work / "thing.py").write_text("x = 1\n", encoding="utf-8")
    result_path = tmp_path / "result.json"

    launch_detached_mutation(
        diff_files=["thing.py"],
        baseline_cmd=[sys.executable, "-c", "pass"],
        cwd=work,
        result_path=result_path,
        baseline_timeout=321,
        also_copy=["fixtures/", "conftest.py"],
        max_children=3,
        memory_limit_bytes=64 * 1024**2,
        mutation_skip_globs=["legacy/*.py"],
        mutation_include_globs=["src/*.py"],
    )
    _wait_for(result_path)

    record = json.loads(detach_env.read_text(encoding="utf-8"))
    assert record["baseline_timeout"] == 321
    assert record["also_copy"] == ["fixtures/", "conftest.py"]
    assert record["max_children"] == 3
    assert record["memory_limit_bytes"] == 64 * 1024**2
    assert record["mutation_skip_globs"] == ["legacy/*.py"]
    assert record["mutation_include_globs"] == ["src/*.py"]
    assert record["diff_files"] == ["thing.py"]
    assert record["cwd"] == str(work)
