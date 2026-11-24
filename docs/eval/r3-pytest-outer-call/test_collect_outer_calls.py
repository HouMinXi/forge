"""Exercise the standalone collector against real pytest 9.2.0 reports."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PYTEST_DRIVER = """
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, sys.argv[1])
from collect_outer_calls import OuterCallEvidence


class ObserveRawLog:
    def __init__(self):
        self.phase_only_call_types = []
        self.intermediate_call_types = []

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(self, call):
        report = yield
        if call.when == "call" and report.outcome in {"passed", "failed"}:
            self.intermediate_call_types.append(type(report).__name__)
        return report

    def pytest_runtest_logreport(self, report):
        if report.when == "call" and report.outcome in {"passed", "failed"}:
            self.phase_only_call_types.append(type(report).__name__)


evidence = OuterCallEvidence()
raw = ObserveRawLog()
status = pytest.main(["-q", "test_case.py"], plugins=[evidence, raw])
Path("result.json").write_text(json.dumps({
    "observed_files": sorted(evidence.observed_files),
    "phase_only_call_types": raw.phase_only_call_types,
    "intermediate_call_types": raw.intermediate_call_types,
}))
sys.exit(status)
"""


def run_case(tmp_path: Path, source: str):
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_case.py").write_text(source, encoding="utf-8")
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_PLUGINS", None)
    result = subprocess.run(
        [sys.executable, "-B", "-c", PYTEST_DRIVER, str(Path(__file__).resolve().parent)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    report = tmp_path / "result.json"
    assert report.is_file(), result.stdout + result.stderr
    return result, json.loads(report.read_text(encoding="utf-8"))


def test_passing_setup_subtest_does_not_prove_call(tmp_path: Path):
    result, report = run_case(
        tmp_path,
        """from pathlib import Path
import pytest

@pytest.fixture
def fails_after_subtest(subtests):
    with subtests.test("fixture check"):
        assert 2 + 2 == 4
    raise RuntimeError("setup failed after a passing subtest")

def test_body_not_entered(fails_after_subtest):
    Path("body-marker").touch()
""",
    )
    assert result.returncode == pytest.ExitCode.TESTS_FAILED, result.stdout + result.stderr
    assert "setup failed after a passing subtest" in result.stdout
    assert not (tmp_path / "body-marker").exists()
    assert report["phase_only_call_types"] == ["SubtestReport"]
    assert report["intermediate_call_types"] == ["TestReport"]
    assert report["observed_files"] == []


@pytest.mark.parametrize(
    ("statement", "status", "call_types", "files"),
    [
        ("assert 2 + 2 == 4", pytest.ExitCode.OK, ["TestReport"], ["test_case.py"]),
        ("assert 2 + 2 == 5", pytest.ExitCode.TESTS_FAILED, ["TestReport"], ["test_case.py"]),
        ('pytest.skip("inside body")', pytest.ExitCode.OK, [], []),
        (
            'with subtests.test("inside body"): assert 2 + 2 == 4',
            pytest.ExitCode.OK,
            ["SubtestReport", "TestReport"],
            ["test_case.py"],
        ),
    ],
)
def test_outer_call_controls(tmp_path: Path, statement, status, call_types, files):
    result, report = run_case(
        tmp_path,
        "from pathlib import Path\nimport pytest\n\n"
        "def test_body(subtests):\n"
        '    Path("body-marker").touch()\n'
        f"    {statement}\n",
    )
    assert result.returncode == status, result.stdout + result.stderr
    assert (tmp_path / "body-marker").exists()
    assert report["observed_files"] == files
    assert report["phase_only_call_types"] == call_types
