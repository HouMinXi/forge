"""Real-pytest tests for the Forge report plugin.

Each test runs a real pytest subprocess with the plugin loaded and reads
back the JSON event file. No hook mocking; the real hook chain fires.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent / "src")
PLUGIN = "code_forge.mutation_engines.pytest_report_plugin"


def _run_pytest(
    suite_dir: Path,
    events_dir: Path,
    run_id: str = "run-t",
    mutant: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "PYTHONPATH": SRC,
        "FORGE_MUTATION_EVENTS_DIR": str(events_dir),
        "FORGE_MUTATION_RUN_ID": run_id,
    }
    if mutant is not None:
        env["MUTANT_UNDER_TEST"] = mutant
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", PLUGIN, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=str(suite_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _write(suite: Path, name: str, content: str) -> None:
    (suite / name).write_text(content)


def _read_event(events_dir: Path, run_id: str = "run-t", mutant: str | None = None) -> dict:
    name = "%s__%s.json" % (run_id, mutant if mutant else "baseline")
    return json.loads((events_dir / name).read_text())


def test_passing_suite_records_final_event(tmp_path):
    suite = tmp_path / "suite"
    suite.mkdir()
    _write(suite, "test_ok.py", "def test_a():\n    assert 1 == 1\n\n\ndef test_b():\n    assert 'x'\n")
    events = tmp_path / "events"
    result = _run_pytest(suite, events)
    assert result.returncode == 0, result.stderr
    record = _read_event(events)
    assert record["final"] is True
    assert record["schema_version"] == 1
    assert record["plugin_version"] == "1"
    assert record["run_id"] == "run-t"
    assert record["mutant_id"] is None
    assert record["collected"] == 2
    assert record["executed"] == 2
    assert record["failed_assertions"] == 0
    assert record["exit_status"] == 0
    assert record["setup_errors"] == 0
    assert record["teardown_errors"] == 0
    assert record["collection_errors"] == 0
    assert record["internal_errors"] == 0


def test_failing_assertion_counted_with_node(tmp_path):
    suite = tmp_path / "suite"
    suite.mkdir()
    _write(
        suite,
        "test_fail.py",
        "def test_good():\n    assert True\n\n\ndef test_bad():\n    assert 1 == 2\n",
    )
    events = tmp_path / "events"
    result = _run_pytest(suite, events)
    assert result.returncode == 1
    record = _read_event(events)
    assert record["executed"] == 2
    assert record["failed_assertions"] == 1
    assert any("test_bad" in node for node in record["failed_nodes"])
    assert record["exit_status"] == 1


def test_mutant_identity_rides_environment(tmp_path):
    suite = tmp_path / "suite"
    suite.mkdir()
    _write(suite, "test_ok.py", "def test_a():\n    assert True\n")
    events = tmp_path / "events"
    key = "x_calc.add__mutmut_2"
    result = _run_pytest(suite, events, mutant=key)
    assert result.returncode == 0, result.stderr
    record = _read_event(events, mutant=key)
    assert record["mutant_id"] == key
    assert record["executed"] == 1


def test_disarmed_without_environment(tmp_path):
    suite = tmp_path / "suite"
    suite.mkdir()
    _write(suite, "test_ok.py", "def test_a():\n    assert True\n")
    events = tmp_path / "events"
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "PYTHONPATH": SRC,
    }
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", PLUGIN, "-q", "-p", "no:cacheprovider"],
        cwd=str(suite),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0
    assert not events.exists() or not list(events.glob("*.json"))


def test_collection_error_counted(tmp_path):
    suite = tmp_path / "suite"
    suite.mkdir()
    _write(suite, "test_broken.py", "def test_x(:\n    pass\n")
    events = tmp_path / "events"
    result = _run_pytest(suite, events)
    assert result.returncode != 0
    record = _read_event(events)
    assert record["collection_errors"] >= 1
    assert record["final"] is True


def test_setup_error_counted(tmp_path):
    suite = tmp_path / "suite"
    suite.mkdir()
    _write(
        suite,
        "test_setup.py",
        "import pytest\n\n\n@pytest.fixture\ndef broken():\n    raise RuntimeError('boom')\n\n\ndef test_needs(broken):\n    assert True\n",
    )
    events = tmp_path / "events"
    result = _run_pytest(suite, events)
    assert result.returncode != 0
    record = _read_event(events)
    assert record["setup_errors"] >= 1
    assert record["failed_assertions"] == 0


def test_skipped_test_is_not_executed(tmp_path):
    """A skip never enters the call phase, so it must not count as executed.

    Counting skips as executed would let a fully skipped suite look like a
    clean survival.
    """
    suite = tmp_path / "suite"
    suite.mkdir()
    _write(
        suite,
        "test_skip.py",
        "import pytest\n\n\n"
        "@pytest.mark.skip(reason='not this run')\n"
        "def test_skipped():\n"
        "    assert False\n",
    )
    events = tmp_path / "events"
    result = _run_pytest(suite, events, "run-skip")
    event = _read_event(events, "run-skip")
    assert event["skipped"] == 1
    assert event["executed"] == 0
    assert event["failed_assertions"] == 0
    assert result.returncode != 1
