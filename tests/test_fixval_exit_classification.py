# SPDX-License-Identifier: Apache-2.0
"""#252: only a judged test failure on the reverted code counts as FIXVAL PASS;
signal deaths and pytest exits 2-5 are inconclusive (non-PASS, with the cause)."""

import os
import subprocess
import sys

import pytest

from code_forge import fixval
from code_forge.disposition import Disposition
from code_forge.fixval import FixvalCandidate, FixvalStatus, _test_reverted_candidate, run_overfit_guard

CANDIDATE = FixvalCandidate(["tests/test_mod.py"], ["src/mod.py"])
KILL = "import os, signal; os.kill(os.getpid(), signal.%s)"


def _reverted(tmp_path, cmd):
    return _test_reverted_candidate(CANDIDATE, cmd, dict(os.environ), str(tmp_path))


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
def test_reverted_run_killed_by_signal_is_inconclusive(tmp_path, name, code):
    result = _reverted(tmp_path, [sys.executable, "-c", KILL % name])
    _assert_inconclusive(result, "killed by %s (exit %d)" % (name, code))


@pytest.mark.parametrize(
    ("code", "label"),
    [(2, "interrupted"), (3, "internal error"), (4, "usage error"), (5, "no tests collected"), (7, "not a test-failure exit")],
)
def test_reverted_run_non_failure_exit_is_inconclusive(tmp_path, code, label):
    result = _reverted(tmp_path, [sys.executable, "-c", "import sys; print('boom'); sys.exit(%d)" % code])
    _assert_inconclusive(result, "exit %d (%s)" % (code, label), "Output: boom")


def test_reverted_run_failing_test_is_pass(tmp_path):
    assert _reverted(tmp_path, [sys.executable, "-c", "assert False"]).status == FixvalStatus.PASS


def test_reverted_run_passing_test_is_still_hollow(tmp_path):
    result = _reverted(tmp_path, [sys.executable, "-c", "pass"])
    assert result.status == FixvalStatus.BLOCK and [f.id for f in result.findings] == ["FIXVAL_HOLLOW"]


def _pytest(tmp_path, body):
    (tmp_path / "test_probe.py").write_text(body)
    return [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist", "test_probe.py"]


def test_real_pytest_collection_error_from_the_revert_is_pass(tmp_path):
    # The fix added what the test imports; the revert removes it.
    result = _reverted(tmp_path, _pytest(tmp_path, "from added_by_fix import helper\ndef test_it():\n    helper()\n"))
    assert result.status == FixvalStatus.PASS


def test_real_pytest_failing_test_is_pass(tmp_path):
    assert _reverted(tmp_path, _pytest(tmp_path, "def test_it():\n    assert False\n")).status == FixvalStatus.PASS


def test_real_pytest_keyboard_interrupt_is_inconclusive(tmp_path):
    result = _reverted(tmp_path, _pytest(tmp_path, "def test_it():\n    raise KeyboardInterrupt\n"))
    _assert_inconclusive(result, "exit 2 (interrupted)")


def test_real_pytest_no_tests_is_inconclusive(tmp_path):
    _assert_inconclusive(_reverted(tmp_path, _pytest(tmp_path, "x = 1\n")), "exit 5 (no tests collected)")


def test_collection_banner_with_keyboard_interrupt_is_inconclusive(monkeypatch, tmp_path):
    out = "!!!!! Interrupted: 1 error during collection !!!!!\nKeyboardInterrupt\n"
    monkeypatch.setattr(fixval.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 2, out, ""))
    _assert_inconclusive(_reverted(tmp_path, ["pytest"]), "exit 2 (interrupted)")


def _overfit(tmp_path, cmd):
    source = tmp_path / "src" / "mod.py"
    source.parent.mkdir(exist_ok=True)
    original = "def f():\n    value = 1\n    return value\n"
    source.write_text(original)
    advisories = run_overfit_guard(FixvalCandidate(["tests/test_mod.py"], ["src/mod.py"]), cmd, tmp_path)
    assert source.read_text() == original
    return advisories


@pytest.mark.parametrize(("cmd", "needle"), [
    ([sys.executable, "-c", KILL % "SIGHUP"], "killed by SIGHUP (exit -1)"),
    ([sys.executable, "-c", "import sys; sys.exit(5)"], "exit 5 (no tests collected)"),
])
def test_overfit_guard_unjudged_run_is_inconclusive_not_overfit(tmp_path, cmd, needle):
    advisories = _overfit(tmp_path, cmd)
    assert [a.id for a in advisories] == ["FIXVAL_OVERFIT_INCONCLUSIVE"]
    assert needle in advisories[0].description
    assert "'value' -> 'value_renamed'" in advisories[0].description


def test_overfit_guard_failing_test_is_overfit(tmp_path):
    assert [a.id for a in _overfit(tmp_path, [sys.executable, "-c", "assert False"])] == ["FIXVAL_OVERFIT"]


def test_overfit_guard_passing_test_is_clean(tmp_path):
    assert _overfit(tmp_path, [sys.executable, "-c", "pass"]) == []
