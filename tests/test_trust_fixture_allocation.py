# SPDX-License-Identifier: Apache-2.0
"""Trust fixture allocation stays private without per-test numbering scans."""

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import pytest

from tests.conftest import _isolate_trust_store, _real_config_home


ROOT = Path(__file__).resolve().parents[1]


def _allocate(parent, monkeypatch):
    request = SimpleNamespace(node=SimpleNamespace(stash={}))
    _isolate_trust_store.__wrapped__(parent, monkeypatch, request)
    return Path(os.environ["XDG_CONFIG_HOME"]), request


def test_active_child_is_private_and_outside_test_tree(
    _trust_store_parent, tmp_path_factory, tmp_path, monkeypatch
):
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.chdir(repository)
    home = Path(os.environ["XDG_CONFIG_HOME"])
    assert home.parent == _trust_store_parent
    assert _trust_store_parent.parent == tmp_path_factory.getbasetemp()
    assert home != tmp_path and tmp_path not in home.parents
    assert home not in tmp_path.parents
    assert home != Path.cwd() and Path.cwd() not in home.parents
    if os.name == "posix":
        assert stat.S_IMODE(home.stat().st_mode) & 0o077 == 0


def test_allocations_are_unique_retained_and_restore_overrides(_trust_store_parent, tmp_path):
    original = os.environ["XDG_CONFIG_HOME"]
    children = []
    for number in range(3):
        with pytest.MonkeyPatch.context() as patcher:
            home, request = _allocate(_trust_store_parent, patcher)
            assert request.node.stash[_real_config_home] == Path(original)
            assert home.parent == _trust_store_parent and home not in children
            (home / "owner").write_text(str(number))
            children.append(home)
            override = tmp_path / str(number)
            patcher.setenv("XDG_CONFIG_HOME", str(override))
            from code_forge.trust import _config_dir

            assert _config_dir() == override / "code-forge"
        assert os.environ["XDG_CONFIG_HOME"] == original
    assert [(home / "owner").read_text() for home in children] == ["0", "1", "2"]


def test_collision_never_reuses_existing_child(_trust_store_parent):
    existing = _trust_store_parent / "trust-collision"
    existing.mkdir(mode=0o700)
    marker = existing / "owner"
    marker.write_text("existing owner")
    before = existing.stat()
    names = iter(["collision", "fresh"])
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(tempfile, "_get_candidate_names", lambda: names)
        home, _ = _allocate(_trust_store_parent, patcher)
        assert home == _trust_store_parent / "trust-fresh"
        assert home != existing and not list(home.iterdir())
    after = existing.stat()
    assert (before.st_dev, before.st_ino, before.st_mode) == (
        after.st_dev, after.st_ino, after.st_mode
    )
    assert marker.read_text() == "existing owner"


def test_allocation_failure_propagates_without_environment_fallback(_trust_store_parent):
    original = os.environ["XDG_CONFIG_HOME"]
    failure = PermissionError("controlled trust allocation refusal")
    calls = []

    def refuse(*, dir, prefix):
        calls.append((dir, prefix))
        raise failure

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(tempfile, "mkdtemp", refuse)
        with pytest.raises(PermissionError) as caught:
            _allocate(_trust_store_parent, patcher)
        assert caught.value is failure
        assert os.environ["XDG_CONFIG_HOME"] == original
    assert calls == [(_trust_store_parent, "trust-")]


_CHILD_CONFTEST = '''
import json
import os
from pathlib import Path
import pytest
from tests.conftest import _isolate_trust_store, _real_config_home, _trust_store_parent

seen = []
original = os.environ["XDG_CONFIG_HOME"]

@pytest.fixture
def dependent(_isolate_trust_store, _trust_store_parent, request):
    home = Path(os.environ["XDG_CONFIG_HOME"])
    assert request.node.stash[_real_config_home] == Path(original)
    assert home.parent == _trust_store_parent
    assert home not in seen
    assert all(path.is_dir() for path in seen)
    seen.append(home)
    yield home
    assert home.is_dir()
    assert (home / "owned").read_text() == "retained"
    (home / "finalized").write_text("dependent finished")

def pytest_sessionfinish(session, exitstatus):
    report = {"children": [str(path) for path in seen],
              "restored": os.environ["XDG_CONFIG_HOME"] == original}
    Path(os.environ["TRUST_ALLOCATION_REPORT"]).write_text(json.dumps(report))
'''

_CHILD_TEST = '''
import os
from pathlib import Path

def test_first(dependent, tmp_path, monkeypatch):
    assert tmp_path not in dependent.parents and dependent not in tmp_path.parents
    assert Path.cwd() not in dependent.parents
    (dependent / "owned").write_text("retained")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "explicit-override"))
    assert os.environ["XDG_CONFIG_HOME"] == str(tmp_path / "explicit-override")

def test_second(dependent):
    assert os.environ["XDG_CONFIG_HOME"] == str(dependent)
    (dependent / "owned").write_text("retained")
'''


def test_real_sessions_keep_distinct_parents_and_dependent_teardown(tmp_path):
    """Nested pytest sessions and simulated worker layouts remain separate."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "conftest.py").write_text(_CHILD_CONFTEST)
    (project / "test_lifetime.py").write_text(_CHILD_TEST)
    original_home = tmp_path / "original-home"
    original_home.mkdir()
    marker = original_home / "untouched"
    marker.write_text("original")
    parents = []
    children = []
    for layout in ("session-one", "session-two", "simulated-workers/gw0", "simulated-workers/gw1"):
        base = tmp_path / layout
        base.parent.mkdir(parents=True, exist_ok=True)
        report = tmp_path / (layout.replace("/", "-") + ".json")
        env = dict(os.environ)
        env.pop("PYTEST_ADDOPTS", None)
        env.pop("PYTEST_PLUGINS", None)
        env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT)))
        env["XDG_CONFIG_HOME"] = str(original_home)
        env["TRUST_ALLOCATION_REPORT"] = str(report)
        result = subprocess.run(
            [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
             "--basetemp", str(base), "test_lifetime.py"],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        observed = json.loads(report.read_text())
        assert observed["restored"] is True
        paths = [Path(path) for path in observed["children"]]
        assert len(paths) == 2 and paths[0] != paths[1]
        parent = paths[0].parent
        assert parent.parent == base and parent not in parents
        assert all(path.parent == parent and path.is_dir() for path in paths)
        parents.append(parent)
        children.extend(paths)
    assert len(set(children)) == 8
    assert all((path / "finalized").read_text() == "dependent finished" for path in children)
    assert list(original_home.iterdir()) == [marker]
    assert marker.read_text() == "original"
