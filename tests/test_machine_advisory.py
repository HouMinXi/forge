"""Tests for machine.py advisory wiring.

Covers:
- StateMachine initializes with empty _advisories list (D-14)
- Advisory findings do NOT affect _fixpoint_reached (D-14)
- _run_advisory_axes dispatch point exists and runs advisory_runners
- Advisory findings serialize to advisory-findings.json (D-15)
- Advisory findings display after separator on stderr (D-17)
- advisory_runners injection point on StateMachine dataclass
"""
from __future__ import annotations

from pathlib import Path

from code_forge.advisory import AdvisoryFinding, AxisRunner
from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.falsify import StubFalsifier
from code_forge.machine import StateMachine
from code_forge.state import Mode, Verdict


def _make_resolved():
    return ResolvedReview(
        source_files=[Path("test.py")],
        baseline_content=None,
        git_diff="diff --git a/test.py b/test.py\n",
        mode_hint="git",
    )


def _make_sm(tmp_path, advisory_runners=None):
    """Build a minimal StateMachine for advisory tests."""
    return StateMachine(
        mode=Mode.LOCAL,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=_make_resolved(),
        source_hash="abc",
        baseline_spec_repr="empty",
        cwd=tmp_path,
        registry={},
        l0_runner=lambda reg, files: ([], []),
        advisory_runners=advisory_runners or [],
    )


class _StubAxisRunner:
    """Stub AxisRunner that returns canned advisory findings."""

    def __init__(self, findings=None):
        self._findings = findings or []

    @property
    def is_advisory(self) -> bool:
        return True

    def run(self, diff_text: str, repo_root: Path) -> list[AdvisoryFinding]:
        return self._findings


class TestAdvisoryInit:
    """StateMachine initializes with empty _advisories list."""

    def test_advisories_empty_on_init(self, tmp_path):
        sm = _make_sm(tmp_path)
        assert hasattr(sm, "_advisories")
        assert sm._advisories == []


class TestAdvisoryFixpointIsolation:
    """Advisory findings do NOT affect _fixpoint_reached."""

    def test_advisory_does_not_block_fixpoint(self, tmp_path):
        sm = _make_sm(tmp_path)
        # Manually add an advisory finding
        sm._advisories.append(AdvisoryFinding(
            id="adv-1",
            axis="TEST",
            file="test.py",
            line_range=[1, 10],
            description="test advisory",
            attribution="test",
        ))
        # _fixpoint_reached should still return True (no blocking findings)
        assert sm._fixpoint_reached() is True


class TestAdvisoryRunners:
    """advisory_runners injection point and _run_advisory_axes dispatch."""

    def test_advisory_runners_default_empty(self, tmp_path):
        sm = _make_sm(tmp_path)
        assert sm.advisory_runners == []

    def test_run_advisory_axes_dispatches(self, tmp_path):
        finding = AdvisoryFinding(
            id="adv-2",
            axis="RUNTIME",
            file="app.py",
            line_range=[5, 15],
            description="runtime concern",
            attribution="runtime-axis",
        )
        runner = _StubAxisRunner(findings=[finding])
        sm = _make_sm(tmp_path, advisory_runners=[runner])
        sm._run_advisory_axes()
        assert len(sm._advisories) == 1
        assert sm._advisories[0].id == "adv-2"

    def test_run_dispatches_advisory_axes_after_local(self, tmp_path):
        """run() calls _run_advisory_axes after _run_local."""
        finding = AdvisoryFinding(
            id="adv-3",
            axis="LEGACY",
            file="old.py",
            line_range=[1, 1],
            description="legacy code",
            attribution="legacy-axis",
        )
        runner = _StubAxisRunner(findings=[finding])
        sm = _make_sm(tmp_path, advisory_runners=[runner])
        verdict = sm.run()
        assert verdict == Verdict.PASS
        assert len(sm._advisories) == 1


class TestAdvisorySerialization:
    """Advisory findings serialize to advisory-findings.json (D-15)."""

    def test_advisory_findings_written_to_file(self, tmp_path):
        import json

        finding = AdvisoryFinding(
            id="adv-s1",
            axis="TRUST",
            file="config.py",
            line_range=[10, 20],
            description="trust concern",
            attribution="trust-axis",
        )
        runner = _StubAxisRunner(findings=[finding])
        sm = _make_sm(tmp_path, advisory_runners=[runner])
        sm.run()

        advisory_path = tmp_path / ".code-forge" / "advisory-findings.json"
        assert advisory_path.exists()
        data = json.loads(advisory_path.read_text())
        assert len(data) == 1
        assert data[0]["id"] == "adv-s1"
        assert data[0]["axis"] == "TRUST"


class TestAdvisoryDisplay:
    """Advisory findings display after separator on stderr (D-17)."""

    def test_advisory_display_separator(self, tmp_path, capsys):
        finding = AdvisoryFinding(
            id="adv-d1",
            axis="RUNTIME",
            file="handler.py",
            line_range=[42, 50],
            description="unchecked return value",
            attribution="runtime-axis",
        )
        runner = _StubAxisRunner(findings=[finding])
        sm = _make_sm(tmp_path, advisory_runners=[runner])
        sm.run()

        captured = capsys.readouterr()
        assert "--- Advisory ---" in captured.err
        assert "[RUNTIME]" in captured.err
        assert "unchecked return value" in captured.err
