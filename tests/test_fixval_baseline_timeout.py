# SPDX-License-Identifier: Apache-2.0
"""FIXVAL's baseline guard honours the configured test timeout.

It used to run with the guard's 120s default no matter what gate.yaml said,
so a full-suite test command timed out every time (#142 run, round 4:
"baseline tests timed out after 120s" with timeout_seconds: 2400)."""

from __future__ import annotations

import pytest

from code_forge import fixval

PATCH = (
    "diff --git a/src/pkg/mod.py b/src/pkg/mod.py\n"
    "--- a/src/pkg/mod.py\n+++ b/src/pkg/mod.py\n@@ -1 +1 @@\n-value = 0\n+value = 1\n"
)


@pytest.mark.parametrize("retry", [False, True], ids=["initial", "env-retry"])
def test_guard_gets_the_configured_timeout(tmp_path, monkeypatch, retry):
    calls = []

    def guard(cmd, env, root, *, allow_strip_retry, timeout=120, run_command=None):
        calls.append(timeout)
        if retry and allow_strip_retry:
            return ("needs_strip_retry", [], [])
        return ("skip", [], ["stop here"])

    monkeypatch.setattr(fixval, "_run_baseline_guard", guard)
    candidate = fixval.FixvalCandidate(["tests/test_mod.py"], ["src/pkg/mod.py"])
    result = fixval.run_fixval(
        candidate, ["python3", "-m", "pytest"], tmp_path, "fix", PATCH, baseline_timeout=2400,
    )
    assert result.status == fixval.FixvalStatus.SKIPPED
    assert calls == ([2400, 2400] if retry else [2400])


def test_default_stays_120_for_direct_callers(tmp_path, monkeypatch):
    calls = []

    def guard(cmd, env, root, *, allow_strip_retry, timeout=120, run_command=None):
        calls.append(timeout)
        return ("skip", [], ["stop"])

    monkeypatch.setattr(fixval, "_run_baseline_guard", guard)
    candidate = fixval.FixvalCandidate(["tests/test_mod.py"], ["src/pkg/mod.py"])
    fixval.run_fixval(candidate, ["python3", "-m", "pytest"], tmp_path, "fix", PATCH)
    assert calls == [120]


def _git(root, *args):
    import subprocess

    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
        cwd=root, check=True, capture_output=True,
    )


def test_reverted_run_timeout_is_a_skip_with_the_gate_timeout(tmp_path, monkeypatch):
    """#187: the reverted run gets the gate timeout, and a timeout there is a
    SKIPPED FIXVAL with the reason, not a FIXVAL_TRANSACTION BLOCK."""
    import subprocess

    mod = tmp_path / "src" / "pkg" / "mod.py"
    mod.parent.mkdir(parents=True)
    mod.write_text("value = 0\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    mod.write_text("value = 1\n")
    monkeypatch.setattr(fixval, "_run_baseline_guard", lambda *a, **k: ("passed", [], []))
    real = subprocess.run
    seen = []

    def run(argv, **kw):
        if "tests/test_mod.py" in argv:
            seen.append(kw.get("timeout"))
            raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
        return real(argv, **kw)

    monkeypatch.setattr(fixval.subprocess, "run", run)
    candidate = fixval.FixvalCandidate(["tests/test_mod.py"], ["src/pkg/mod.py"])
    result = fixval.run_fixval(
        candidate, ["python3", "-m", "pytest"], tmp_path, "fix", PATCH, baseline_timeout=2400,
    )
    assert seen == [2400]
    assert result.status == fixval.FixvalStatus.SKIPPED, result
    assert not any(f.id == "FIXVAL_TRANSACTION" for f in result.findings)
    assert any("timed out after 2400s" in a.description for a in result.advisories)
    assert mod.read_text() == "value = 1\n"


def test_overfit_run_timeout_is_advisory_silence(tmp_path, monkeypatch):
    """#187: the overfit run gets the gate timeout and never raises on it."""
    import subprocess

    src = tmp_path / "src" / "module.py"
    src.parent.mkdir()
    original = "def compute():\n    result = 42\n    return result\n"
    src.write_text(original)
    seen = []

    def run(argv, **kw):
        seen.append(kw.get("timeout"))
        raise subprocess.TimeoutExpired(argv, kw.get("timeout"))

    monkeypatch.setattr(fixval.subprocess, "run", run)
    candidate = fixval.FixvalCandidate(["tests/test_module.py"], [str(src)])
    assert fixval.run_overfit_guard(candidate, ["pytest"], tmp_path, timeout=2400) == []
    assert seen == [2400]
    assert src.read_text() == original
