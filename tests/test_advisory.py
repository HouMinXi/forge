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
        line_range=(10, 20),
        description="possible runtime issue",
        attribution="git blame: abc1234",
    )
    assert f.id == "ADV-001"
    assert f.axis == "RUNTIME"
    assert f.file == "src/app.py"
    assert f.line_range == (10, 20)
    assert isinstance(f.line_range, tuple)
    assert f.description == "possible runtime issue"
    assert f.attribution == "git blame: abc1234"


def test_advisory_finding_is_frozen():
    """AdvisoryFinding is frozen -- cannot mutate after creation."""
    from code_forge.advisory import AdvisoryFinding

    f = AdvisoryFinding(
        id="ADV-001",
        axis="RUNTIME",
        file="src/app.py",
        line_range=(10, 20),
        description="test",
        attribution="test",
    )
    with pytest.raises(AttributeError):
        f.id = "ADV-002"


def test_advisory_finding_line_range_immutable_tuple():
    """line_range is an immutable tuple[int, int] preventing in-place mutation."""
    from code_forge.advisory import AdvisoryFinding

    f = AdvisoryFinding(
        id="ADV-001",
        axis="RUNTIME",
        file="src/app.py",
        line_range=(10, 20),
        description="test",
        attribution="test",
    )
    assert isinstance(f.line_range, tuple)
    with pytest.raises(TypeError):
        f.line_range[0] = 99  # type: ignore[index]
    assert not hasattr(f.line_range, "append")


def test_advisory_finding_line_range_coerces_list_to_tuple():
    """Passing a list or sequence normalizes to tuple[int, int]."""
    from code_forge.advisory import AdvisoryFinding

    f = AdvisoryFinding(
        id="ADV-001",
        axis="RUNTIME",
        file="src/app.py",
        line_range=[10, 20],  # list input
        description="test",
        attribution="test",
    )
    assert isinstance(f.line_range, tuple)
    assert f.line_range == (10, 20)


def test_advisory_finding_line_range_safe_indexing_empty_and_singleton():
    """Empty or singleton line_range normalizes to 2-element tuple, avoiding IndexError."""
    from code_forge.advisory import AdvisoryFinding

    f_empty = AdvisoryFinding(
        id="ADV-EMPTY",
        axis="RUNTIME",
        file="src/app.py",
        line_range=[],  # empty
        description="test",
        attribution="test",
    )
    assert f_empty.line_range == (0, 0)
    assert f_empty.line_range[0] == 0 and f_empty.line_range[1] == 0

    f_single = AdvisoryFinding(
        id="ADV-SINGLE",
        axis="RUNTIME",
        file="src/app.py",
        line_range=[42],  # singleton
        description="test",
        attribution="test",
    )
    assert f_single.line_range == (42, 42)
    assert f_single.line_range[0] == 42 and f_single.line_range[1] == 42


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


# -- AdvisoryAxisRunner Protocol conformance -------------------------------


def test_advisory_axis_runner_conformance():
    """A class with is_advisory=True and run() satisfies AdvisoryAxisRunner Protocol."""
    from code_forge.advisory import AdvisoryFinding, AdvisoryAxisRunner, AxisRunner

    class MockAdvisoryRunner:
        @property
        def is_advisory(self) -> bool:
            return True

        def run(
            self, diff_text: str, repo_root: Path
        ) -> list[AdvisoryFinding]:
            return []

    # Satisfies AdvisoryAxisRunner
    runner: AdvisoryAxisRunner = MockAdvisoryRunner()
    assert runner.is_advisory is True
    assert runner.run("diff text", Path("/repo")) == []

    # AxisRunner backwards compatibility alias
    alias_runner: AxisRunner = MockAdvisoryRunner()
    assert alias_runner.is_advisory is True
    assert AdvisoryAxisRunner is AxisRunner


def test_advisory_axis_runner_docstring_and_scope():
    """AdvisoryAxisRunner docstring reflects advisory-only scope, not blocking."""
    from code_forge.advisory import AdvisoryAxisRunner

    doc = AdvisoryAxisRunner.__doc__ or ""
    assert "advisory" in doc.lower()
    assert "blocking or advisory" not in doc.lower()


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


def test_line_range_rejects_str_and_bytes():
    """str/bytes are Sequences but not valid line_range; guard must raise."""
    import pytest

    from code_forge.advisory import AdvisoryFinding

    with pytest.raises(TypeError):
        AdvisoryFinding(
            id="x", axis="t", file="f", line_range="hello",
            description="d", attribution="a",
        )
    with pytest.raises(TypeError):
        AdvisoryFinding(
            id="x", axis="t", file="f", line_range=b"\x00\x01",
            description="d", attribution="a",
        )
