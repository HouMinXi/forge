"""The target-line record observes execution and refuses a bad target."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

CLI = Path(__file__).with_name("target_hits.py")

MODULE = """\
VALUE = 1


def hit():
    return VALUE


def other():
    return 0
"""

TESTS = """\
import threading

import mod


def test_hits_the_line():
    assert mod.hit() == 1


def test_misses_the_line():
    assert mod.other() == 0


def test_hits_from_a_thread():
    worker = threading.Thread(target=mod.hit)
    worker.start()
    worker.join()
"""


ORDERED = """\
import threading

import pytest

import mod

release = threading.Event()
done = threading.Event()


def _work():
    release.wait(5)
    mod.hit()
    done.set()


def test_a_starts_a_waiting_thread():
    worker = threading.Thread(target=_work)
    worker.start()


@pytest.fixture()
def released():
    release.set()
    assert done.wait(5)
    yield


def test_b_releases_the_thread(released):
    assert mod.other() == 0
"""


def _target_line(source: str, needle: str) -> int:
    for number, text in enumerate(source.splitlines(), start=1):
        if text.strip() == needle:
            return number
    raise AssertionError(f"{needle} not found")


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "mod.py").write_text(MODULE, encoding="utf-8")
    (root / "test_mod.py").write_text(TESTS, encoding="utf-8")
    return root


def _run(root: Path, target: Path, line: int, *nodes: str, output: Path | None = None):
    record = output or (root.parent / "record.json")
    command = [
        sys.executable,
        str(CLI),
        "--root",
        str(root),
        "--file",
        str(target),
        "--line",
        str(line),
        "--output",
        str(record),
        *nodes,
    ]
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    return subprocess.run(command, capture_output=True, text=True, env=env)


def _record(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_a_passing_node_that_runs_the_line_is_recorded(project):
    line = _target_line(MODULE, "return VALUE")
    result = _run(project, project / "mod.py", line, "test_mod.py::test_hits_the_line")
    assert result.returncode == 0, result.stderr
    data = _record(project.parent / "record.json")
    assert data["schema"] == "forge-r3-target-hits-v1"
    assert data["target"] == {"path": "mod.py", "line": line}
    assert data["collected"] == ["test_mod.py::test_hits_the_line"]
    assert data["hits"]["test_mod.py::test_hits_the_line"] == 1
    assert data["pytest_exit"] == 0


def test_a_passing_node_that_never_runs_the_line_records_zero(project):
    line = _target_line(MODULE, "return VALUE")
    result = _run(project, project / "mod.py", line, "test_mod.py::test_misses_the_line")
    assert result.returncode == 0, result.stderr
    data = _record(project.parent / "record.json")
    assert data["hits"]["test_mod.py::test_misses_the_line"] == 0


def test_a_line_reached_from_another_thread_is_counted(project):
    line = _target_line(MODULE, "return VALUE")
    result = _run(project, project / "mod.py", line, "test_mod.py::test_hits_from_a_thread")
    assert result.returncode == 0, result.stderr
    data = _record(project.parent / "record.json")
    assert data["hits"]["test_mod.py::test_hits_from_a_thread"] == 1


def test_counts_stay_separate_per_node(project):
    line = _target_line(MODULE, "return VALUE")
    result = _run(
        project,
        project / "mod.py",
        line,
        "test_mod.py::test_hits_the_line",
        "test_mod.py::test_misses_the_line",
    )
    assert result.returncode == 0, result.stderr
    data = _record(project.parent / "record.json")
    assert data["hits"] == {
        "test_mod.py::test_hits_the_line": 1,
        "test_mod.py::test_misses_the_line": 0,
    }


def test_a_leading_separator_before_the_selection_is_accepted(project):
    line = _target_line(MODULE, "return VALUE")
    result = _run(project, project / "mod.py", line, "--", "test_mod.py::test_hits_the_line")
    assert result.returncode == 0, result.stderr
    data = _record(project.parent / "record.json")
    assert data["hits"]["test_mod.py::test_hits_the_line"] == 1


def test_a_late_thread_hit_is_not_credited_to_another_node(project):
    (project / "test_thread_order.py").write_text(ORDERED, encoding="utf-8")
    line = _target_line(MODULE, "return VALUE")
    result = _run(
        project,
        project / "mod.py",
        line,
        "test_thread_order.py::test_a_starts_a_waiting_thread",
        "test_thread_order.py::test_b_releases_the_thread",
    )
    assert result.returncode == 0, result.stderr
    data = _record(project.parent / "record.json")
    assert data["hits"] == {
        "test_thread_order.py::test_a_starts_a_waiting_thread": 0,
        "test_thread_order.py::test_b_releases_the_thread": 0,
    }


def test_a_target_outside_the_root_is_refused(project, tmp_path):
    other = tmp_path / "elsewhere.py"
    other.write_text("value = 1\n", encoding="utf-8")
    result = _run(project, other, 1, "test_mod.py")
    assert result.returncode == 2
    assert "must live under the given root" in result.stderr
    assert not (project.parent / "record.json").exists()


def test_a_line_past_the_end_of_the_file_is_refused(project):
    result = _run(project, project / "mod.py", 99, "test_mod.py")
    assert result.returncode == 2
    assert "past the end of the file" in result.stderr


def test_a_missing_target_is_refused(project):
    result = _run(project, project / "absent.py", 1, "test_mod.py")
    assert result.returncode == 2
    assert "does not exist" in result.stderr


def test_a_record_written_inside_the_checkout_is_refused(project):
    line = _target_line(MODULE, "return VALUE")
    result = _run(
        project,
        project / "mod.py",
        line,
        "test_mod.py::test_hits_the_line",
        output=project / "record.json",
    )
    assert result.returncode == 2
    assert "outside the checkout" in result.stderr
    assert not (project / "record.json").exists()
