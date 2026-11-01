# SPDX-License-Identifier: Apache-2.0
"""#252: an unjudged reverted-code run is not FIXVAL PASS.

A signal death is inconclusive for any runner. For pytest, exit 1 (tests
failed) and exit 2 (interrupted or collection error) count as failing only when
the JUnit report shows it (#28, tests/test_fixval_junit_witness.py); 3-5 and
other codes are inconclusive. Other runners keep "nonzero = fails"."""

import os
import stat
import sys

import pytest

from code_forge.disposition import Disposition
from code_forge.fixval import (
    FixvalCandidate,
    FixvalStatus,
    _runs_pytest,
    _test_reverted_candidate,
    run_overfit_guard,
)

CANDIDATE = FixvalCandidate(["tests/test_mod.py"], ["src/mod.py"])
KILL = "import os, signal; os.kill(os.getpid(), signal.%s)"


def _reverted(tmp_path, cmd):
    return _test_reverted_candidate(CANDIDATE, cmd, dict(os.environ), str(tmp_path))


def _fake_pytest(tmp_path, code):
    """A real executable named pytest that prints and exits with `code`."""
    path = tmp_path / "bin" / "pytest"
    path.parent.mkdir(exist_ok=True)
    path.write_text("#!%s\nimport sys\nprint('boom')\nsys.exit(%d)\n" % (sys.executable, code))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return [str(path), "-q"]


def _assert_inconclusive(result, *needles):
    assert result.status == FixvalStatus.BLOCK
    assert [f.id for f in result.findings] == ["FIXVAL_INCONCLUSIVE"]
    finding = result.findings[0]
    assert finding.disposition == Disposition.UNCERTAIN
    assert finding.description == result.block_message
    assert "the fix is unverified" in finding.description
    for needle in needles:
        assert needle in finding.description


@pytest.mark.parametrize(("name", "code"), [("SIGHUP", -1), ("SIGTERM", -15), ("SIGKILL", -9)])
def test_reverted_run_killed_by_signal_is_inconclusive_for_any_runner(tmp_path, name, code):
    result = _reverted(tmp_path, [sys.executable, "-c", KILL % name])
    _assert_inconclusive(result, "killed by %s (exit %d)" % (name, code))


@pytest.mark.parametrize(
    ("code", "label"),
    [
        (3, "internal error"),
        (4, "usage error"),
        (5, "no tests collected"),
        (7, "not a test-failure exit"),
    ],
)
def test_pytest_non_failure_exit_is_inconclusive(tmp_path, code, label):
    _assert_inconclusive(
        _reverted(tmp_path, _fake_pytest(tmp_path, code)),
        "pytest exit %d (%s)" % (code, label),
        "Output: boom",
    )


@pytest.mark.parametrize("code", [1, 2])
def test_pytest_exit_1_and_2_without_a_report_are_inconclusive(tmp_path, code):
    # #28: the fake pytest writes no JUnit report, so nothing shows a failed test
    _assert_inconclusive(
        _reverted(tmp_path, _fake_pytest(tmp_path, code)),
        "pytest exit %d without a failed test or an import of a name the fix adds" % code,
    )


@pytest.mark.parametrize("code", [1, 2, 3, 5, 7, 127])
def test_other_runner_nonzero_exit_keeps_main_behaviour(tmp_path, code):
    # e.g. make exits 2 for a failed recipe; only pytest's codes are interpreted.
    result = _reverted(tmp_path, [sys.executable, "-c", "import sys; sys.exit(%d)" % code])
    assert result.status == FixvalStatus.PASS


def test_passing_run_is_still_hollow(tmp_path):
    result = _reverted(tmp_path, [sys.executable, "-c", "pass"])
    assert result.status == FixvalStatus.BLOCK and [f.id for f in result.findings] == ["FIXVAL_HOLLOW"]


@pytest.mark.parametrize(
    ("cmd", "expected"),
    [
        (["pytest", "-q"], True),
        (["/venv/bin/pytest"], True),
        (["py.test"], True),
        (["python3", "-m", "pytest", "tests/test_x.py"], True),
        (["uv", "run", "pytest"], True),
        (["poetry", "run", "python", "-m", "pytest"], True),
        (["python3.12", "-B", "-W", "error", "-X", "dev", "-m", "pytest"], True),
        (["python", "-mpytest"], True),
        (["C:\\venv\\Scripts\\pytest.exe", "-q"], True),
        (["C:\\Python312\\python.exe", "-m", "py.test"], True),
        (["make", "pytest"], False),
        (["tox", "-e", "pytest"], False),
        (["uv", "pip", "pytest"], False),
        (["uv", "run"], False),
        (["python3", "-m", "pytest_cov"], False),
        (["python3", "-m"], False),
        (["python3", "run_pytest.py", "-m", "pytest"], False),
        (["python3", "-c", "import pytest", "-m", "pytest"], False),
        (["python3", "-c", "-m", "pytest"], False),
        (["python3", "-mpytest_cov"], False),
        (["C:\\Python312\\Python.EXE", "-m", "pytest"], True),
        (["python3", "-", "-m", "pytest"], False),
        (["python3", "-W", "-m", "pytest"], False),
        (["pythonista", "-m", "pytest"], False),
        (["mypytest"], False),
        ([], False),
        (["make", "test"], False),
        (["python3", "-m", "unittest"], False),
        (["python3", "tests/test_pytest_helpers.py"], False),
        (["npm", "test"], False),
    ],
)
def test_runs_pytest_detection(cmd, expected):
    assert _runs_pytest(cmd) is expected


def _real_pytest(tmp_path, body):
    (tmp_path / "test_probe.py").write_text(body)
    return [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-p",
        "no:xdist",
        "test_probe.py",
    ]


def test_real_pytest_failing_test_is_pass(tmp_path):
    assert (
        _reverted(tmp_path, _real_pytest(tmp_path, "def test_it():\n    assert False\n")).status
        == FixvalStatus.PASS
    )


def test_real_pytest_import_error_without_added_names_is_inconclusive(tmp_path):
    # #28: with no diff saying the fix adds `helper`, this collection error judged nothing
    cmd = _real_pytest(tmp_path, "from added_by_fix import helper\ndef test_it():\n    helper()\n")
    _assert_inconclusive(_reverted(tmp_path, cmd), "pytest exit 2 without a failed test")


def test_real_pytest_no_tests_is_inconclusive(tmp_path):
    _assert_inconclusive(
        _reverted(tmp_path, _real_pytest(tmp_path, "x = 1\n")), "pytest exit 5 (no tests collected)"
    )


def _overfit(tmp_path, cmd):
    source = tmp_path / "src" / "mod.py"
    source.parent.mkdir(exist_ok=True)
    original = "def f():\n    value = 1\n    return value\n"
    source.write_text(original)
    advisories = run_overfit_guard(FixvalCandidate(["tests/test_mod.py"], ["src/mod.py"]), cmd, tmp_path)
    assert source.read_text() == original
    return advisories


@pytest.mark.parametrize("kind", ["signal", "pytest-exit-5"])
def test_overfit_guard_unjudged_run_is_inconclusive_not_overfit(tmp_path, kind):
    if kind == "signal":
        cmd, needle = [sys.executable, "-c", KILL % "SIGHUP"], "killed by SIGHUP (exit -1)"
    else:
        cmd, needle = _fake_pytest(tmp_path, 5), "pytest exit 5 (no tests collected)"
    advisories = _overfit(tmp_path, cmd)
    assert [a.id for a in advisories] == ["FIXVAL_OVERFIT_INCONCLUSIVE"]
    assert needle in advisories[0].description
    assert "'value' -> 'value_renamed'" in advisories[0].description


@pytest.mark.parametrize("runner", ["pytest", "other"])
def test_overfit_guard_failing_test_is_overfit(tmp_path, runner):
    cmd = (
        _fake_pytest(tmp_path, 1)
        if runner == "pytest"
        else [sys.executable, "-c", "import sys; sys.exit(2)"]
    )
    assert [a.id for a in _overfit(tmp_path, cmd)] == ["FIXVAL_OVERFIT"]


def test_overfit_guard_passing_test_is_clean(tmp_path):
    assert _overfit(tmp_path, [sys.executable, "-c", "pass"]) == []
