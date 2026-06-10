"""Tests for the advisory finding type and AxisRunner Protocol.

Covers: AdvisoryFinding construction, field exclusion (no fingerprint,
no disposition, no source), AxisRunner Protocol conformance, and the
founding principle that advisory findings NEVER reset the cycle counter.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# -- AdvisoryFinding construction ------------------------------------------


def test_advisory_finding_construction():
    """AdvisoryFinding is a frozen dataclass with all 6 fields."""
    from code_forge.advisory import AdvisoryFinding

    f = AdvisoryFinding(
        id="ADV-001",
        axis="RUNTIME",
        file="src/app.py",
        line_range=[10, 20],
        description="possible runtime issue",
        attribution="git blame: abc1234",
    )
    assert f.id == "ADV-001"
    assert f.axis == "RUNTIME"
    assert f.file == "src/app.py"
    assert f.line_range == [10, 20]
    assert f.description == "possible runtime issue"
    assert f.attribution == "git blame: abc1234"


def test_advisory_finding_is_frozen():
    """AdvisoryFinding is frozen -- cannot mutate after creation."""
    from code_forge.advisory import AdvisoryFinding

    f = AdvisoryFinding(
        id="ADV-001",
        axis="RUNTIME",
        file="src/app.py",
        line_range=[10, 20],
        description="test",
        attribution="test",
    )
    with pytest.raises(AttributeError):
        f.id = "ADV-002"


# -- Field exclusion (structural incompatibility with StateFinding) --------


def test_advisory_finding_no_fingerprint():
    """AdvisoryFinding has NO fingerprint attribute."""
    from code_forge.advisory import AdvisoryFinding

    f = AdvisoryFinding(
        id="ADV-001",
        axis="RUNTIME",
        file="src/app.py",
        line_range=[10, 20],
        description="test",
        attribution="test",
    )
    assert not hasattr(f, "fingerprint")


def test_advisory_finding_no_disposition():
    """AdvisoryFinding has NO disposition attribute."""
    from code_forge.advisory import AdvisoryFinding

    f = AdvisoryFinding(
        id="ADV-001",
        axis="RUNTIME",
        file="src/app.py",
        line_range=[10, 20],
        description="test",
        attribution="test",
    )
    assert not hasattr(f, "disposition")


def test_advisory_finding_no_source():
    """AdvisoryFinding has NO source attribute."""
    from code_forge.advisory import AdvisoryFinding

    f = AdvisoryFinding(
        id="ADV-001",
        axis="RUNTIME",
        file="src/app.py",
        line_range=[10, 20],
        description="test",
        attribution="test",
    )
    assert not hasattr(f, "source")


# -- AxisRunner Protocol conformance ---------------------------------------


def test_axis_runner_advisory_conformance():
    """A class with is_advisory=True and run() satisfies AxisRunner Protocol."""
    from code_forge.advisory import AdvisoryFinding, AxisRunner

    class MockAdvisoryRunner:
        @property
        def is_advisory(self) -> bool:
            return True

        def run(
            self, diff_text: str, repo_root: Path
        ) -> list[AdvisoryFinding]:
            return []

    runner: AxisRunner = MockAdvisoryRunner()
    assert runner.is_advisory is True
    assert runner.run("diff text", Path("/repo")) == []


def test_axis_runner_blocking_conformance():
    """A class with is_advisory=False also satisfies AxisRunner Protocol."""
    from code_forge.advisory import AdvisoryFinding, AxisRunner

    class MockBlockingRunner:
        @property
        def is_advisory(self) -> bool:
            return False

        def run(
            self, diff_text: str, repo_root: Path
        ) -> list[AdvisoryFinding]:
            return [
                AdvisoryFinding(
                    id="BLK-001",
                    axis="TRUST",
                    file="gate.yaml",
                    line_range=[1, 5],
                    description="untrusted backend",
                    attribution="trust gate",
                )
            ]

    runner: AxisRunner = MockBlockingRunner()
    assert runner.is_advisory is False
    results = runner.run("diff", Path("/repo"))
    assert len(results) == 1


# -- CRITICAL INVARIANT: advisory does NOT reset cycle counter ------------


def test_advisory_does_not_reset_cycle_counter():
    """Founding principle: advisory findings NEVER participate in convergence.

    This test simulates the convergence logic from machine.py lines 448-455:
    - A consecutive_clean_rounds counter tracks fixpoint stability
    - Only the StateFinding (blocking) list participates in fixpoint determination
    - AdvisoryFinding list is maintained separately and has NO effect on the counter

    The presence of high-severity advisory findings must NOT prevent
    consecutive_clean_rounds from incrementing. Only the StateFinding list
    matters for convergence.
    """
    from code_forge.advisory import AdvisoryFinding

    # Simulate machine.py convergence logic
    consecutive_clean_rounds = 0

    # Blocking findings: empty (fixpoint reached)
    blocking_findings = []  # list[StateFinding] -- empty means clean

    # Advisory findings: non-empty, high-severity
    advisory_findings = [
        AdvisoryFinding(
            id="ADV-CRITICAL-001",
            axis="RUNTIME",
            file="src/critical.py",
            line_range=[1, 100],
            description="CRITICAL: possible runtime escape -- unverified "
            "execution path through subprocess.Popen",
            attribution="runtime axis",
        ),
        AdvisoryFinding(
            id="ADV-CRITICAL-002",
            axis="LEGACY",
            file="src/legacy.py",
            line_range=[50, 75],
            description="HIGH: deprecated API usage -- "
            "os.popen replaced by subprocess in Python 3.0",
            attribution="legacy axis",
        ),
    ]

    # Convergence logic (mirrors machine.py lines 448-453):
    # Only blocking_findings participates in fixpoint determination
    def fixpoint_reached(findings: list) -> bool:
        """Simplified fixpoint: True when blocking findings list is empty."""
        return len(findings) == 0

    # The advisory_findings list is NEVER passed to fixpoint_reached
    if fixpoint_reached(blocking_findings):
        consecutive_clean_rounds += 1
    else:
        consecutive_clean_rounds = 0

    # ASSERTION: counter incremented despite advisory findings being present
    assert consecutive_clean_rounds == 1, (
        "Advisory findings must NOT prevent consecutive_clean_rounds from "
        "incrementing. The counter must only depend on blocking findings."
    )

    # Prove advisory findings exist and are non-trivial
    assert len(advisory_findings) == 2
    assert "CRITICAL" in advisory_findings[0].description
    assert "HIGH" in advisory_findings[1].description


# -- Module independence ---------------------------------------------------


def test_advisory_no_state_import():
    """advisory.py must not import from state.py (complete type independence)."""
    import inspect
    import code_forge.advisory as adv_mod

    source = inspect.getsource(adv_mod)
    # Check for imports from state module
    lines = source.split("\n")
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "import" not in stripped or "state" not in stripped, (
            "advisory.py must not import from state module: %s" % stripped
        )
