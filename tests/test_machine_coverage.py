# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""StateMachine wiring tests for the per-file review coverage gate.

CI: a coverage gap (in-scope file no layer examined) -> FAIL.
LOCAL: a coverage gap -> PENDING (HOLD for human disposition).
Covered files (L0 match or L1 active) and exemptions -> no gate.
"""

from pathlib import Path

from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.machine import StateMachine
from code_forge.registry import ToolConfig
from code_forge.state import Mode, StateFinding, Verdict


def _tool(name, patterns):
    return ToolConfig(
        name=name,
        command=name,
        args=[],
        output_format=name + "_json",
        file_patterns=patterns,
    )


def _machine(
    tmp_path,
    *,
    mode=Mode.CI,
    source_files,
    registry=None,
    l1_active=False,
    exempt=None,
    l0_findings=None,
):
    resolved = ResolvedReview(
        source_files=[Path(f) for f in source_files],
        baseline_content=None,
        git_diff=None,
        mode_hint="non-git",
    )

    def mock_l0(_registry, _files):
        return (l0_findings or [], [])

    return StateMachine(
        mode=mode,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=resolved,
        source_hash="abc123",
        baseline_spec_repr="empty",
        cwd=tmp_path,
        registry=registry or {},
        l0_runner=mock_l0,
        coverage_l1_active=l1_active,
        coverage_exempt_patterns=exempt or [],
    )


def _coverage_findings(machine):
    return [f for f in machine._state.findings if f.source == "COVERAGE"]


# ---------------------------------------------------------------------------
# CI mode: coverage gap -> FAIL
# ---------------------------------------------------------------------------

def test_ci_uncovered_file_fails(tmp_path):
    machine = _machine(
        tmp_path,
        source_files=["a.sh"],
        registry={"ruff": _tool("ruff", ["*.py"])},
        l1_active=False,
    )
    assert machine.run() == Verdict.FAIL
    gaps = _coverage_findings(machine)
    assert [f.file for f in gaps] == ["a.sh"]
    assert gaps[0].disposition == Disposition.UNCERTAIN


def test_ci_mixed_scope_fails_on_shell_not_python(tmp_path):
    # The user's case: 1 linted .py covered, shell files flagged.
    machine = _machine(
        tmp_path,
        source_files=["proxy.py", "a.sh", "aicc"],
        registry={"ruff": _tool("ruff", ["*.py"])},
        l1_active=False,
    )
    assert machine.run() == Verdict.FAIL
    assert sorted(f.file for f in _coverage_findings(machine)) == [
        "a.sh",
        "aicc",
    ]


def test_ci_l1_active_passes(tmp_path):
    # L1 ran over the diff -> every file covered, even with no L0 tools.
    machine = _machine(
        tmp_path,
        source_files=["a.sh", "b.txt"],
        registry={},
        l1_active=True,
    )
    assert machine.run() == Verdict.PASS
    assert _coverage_findings(machine) == []


def test_ci_l0_covered_file_passes(tmp_path):
    machine = _machine(
        tmp_path,
        source_files=["mod.py"],
        registry={"ruff": _tool("ruff", ["*.py"])},
        l1_active=False,
    )
    assert machine.run() == Verdict.PASS
    assert _coverage_findings(machine) == []


def test_ci_exempt_pattern_passes(tmp_path):
    machine = _machine(
        tmp_path,
        source_files=["notes.txt"],
        registry={"ruff": _tool("ruff", ["*.py"])},
        l1_active=False,
        exempt=["*.txt"],
    )
    assert machine.run() == Verdict.PASS
    assert _coverage_findings(machine) == []


def test_ci_default_l1_active_is_backward_compatible(tmp_path):
    # Construct WITHOUT passing coverage_l1_active -> defaults to True ->
    # the gate is inert (no behavior change for existing callers/tests).
    resolved = ResolvedReview(
        source_files=[Path("a.sh")],
        baseline_content=None,
        git_diff=None,
        mode_hint="non-git",
    )
    machine = StateMachine(
        mode=Mode.CI,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=resolved,
        source_hash="abc123",
        baseline_spec_repr="empty",
        cwd=tmp_path,
        registry={},
        l0_runner=lambda r, f: ([], []),
    )
    assert machine.run() == Verdict.PASS
    assert _coverage_findings(machine) == []


def test_ci_coverage_gap_with_confirmed_still_fails(tmp_path):
    confirmed = StateFinding(
        id="real-bug",
        fingerprint="real-bug",
        source="L0",
        disposition=Disposition.CONFIRMED,
        file="mod.py",
        line_range=[1, 1],
        description="real defect",
    )
    machine = _machine(
        tmp_path,
        source_files=["mod.py", "a.sh"],
        registry={"ruff": _tool("ruff", ["*.py"])},
        l1_active=False,
        l0_findings=[confirmed],
    )
    assert machine.run() == Verdict.FAIL


# ---------------------------------------------------------------------------
# LOCAL mode: coverage gap -> PENDING (HOLD)
# ---------------------------------------------------------------------------

def test_local_uncovered_file_holds(tmp_path):
    machine = _machine(
        tmp_path,
        mode=Mode.LOCAL,
        source_files=["a.sh"],
        registry={"ruff": _tool("ruff", ["*.py"])},
        l1_active=False,
    )
    assert machine.run() == Verdict.PENDING
    assert machine._state.hold_reason is not None
    assert _coverage_findings(machine)


def test_local_covered_file_passes(tmp_path):
    machine = _machine(
        tmp_path,
        mode=Mode.LOCAL,
        source_files=["mod.py"],
        registry={"ruff": _tool("ruff", ["*.py"])},
        l1_active=False,
    )
    assert machine.run() == Verdict.PASS
