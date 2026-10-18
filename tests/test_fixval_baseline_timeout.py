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
