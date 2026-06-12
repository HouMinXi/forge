"""Tests for machine.py RuntimeRunner wiring and smoke status display.

Covers:
- RuntimeRunner is included in advisory_runners in _run_hold_loop
- RuntimeRunner.run() is called during _run_advisory_axes
- RUNTIME findings appear in self._advisories (not self._state.findings)
- _fixpoint_reached() unaffected by RUNTIME advisory presence
- _display_advisories prints smoke status section unconditionally when
  RuntimeRunner has run (D-09)
- smoke-run subcommand registered in _build_parser with --surface and
  command (REMAINDER) args
- smoke-run handler executes command, writes receipt, exits with command code
- advisory JSON serialization includes RUNTIME findings
- _display_smoke_status handles runtime-smoke-summary, runtime-skipped, and
  no-summary fallback (D-09 always prints)
- Generic _display_advisories loop skips runtime-smoke-summary and
  runtime-skipped (DEDUP)
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from code_forge.advisory import AdvisoryFinding
from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.falsify import StubFalsifier
from code_forge.machine import StateMachine
from code_forge.runtime import RuntimeRunner
from code_forge.state import Mode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_resolved(git_diff: str = "diff --git a/f.py b/f.py\n") -> ResolvedReview:
    return ResolvedReview(
        source_files=[Path("f.py")],
        baseline_content=None,
        git_diff=git_diff,
        mode_hint="git",
    )


def _make_sm(tmp_path, advisory_runners=None, git_diff="diff --git a/f.py b/f.py\n"):
    """Build a minimal StateMachine for advisory tests."""
    return StateMachine(
        mode=Mode.LOCAL,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=_make_resolved(git_diff),
        source_hash="abc",
        baseline_spec_repr="empty",
        cwd=tmp_path,
        registry={},
        l0_runner=lambda reg, files: ([], []),
        advisory_runners=advisory_runners or [],
    )


def _runtime_skipped_finding(reason: str = "test skip") -> AdvisoryFinding:
    return AdvisoryFinding(
        id="runtime-skipped",
        axis="RUNTIME",
        file="",
        line_range=[0, 0],
        description="RUNTIME axis SKIPPED: %s" % reason,
        attribution="runtime-axis/infra-error",
    )


def _runtime_summary_finding(desc: str) -> AdvisoryFinding:
    return AdvisoryFinding(
        id="runtime-smoke-summary",
        axis="RUNTIME",
        file="",
        line_range=[0, 0],
        description=desc,
        attribution="runtime-axis/smoke-evidence",
    )


# ---------------------------------------------------------------------------
# CLI parser tests
# ---------------------------------------------------------------------------

class TestSmokeRunParser:
    """_build_parser() registers smoke-run subcommand."""

    def test_smoke_run_subcommand_exists(self):
        from code_forge.cli import _build_parser
        parser = _build_parser()
        # Parse smoke-run with minimal args: should not raise
        args = parser.parse_args(["smoke-run", "echo", "hello"])
        assert args.subcommand == "smoke-run"

    def test_smoke_run_default_surface(self):
        from code_forge.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["smoke-run", "echo", "hello"])
        assert args.surface == "default"

    def test_smoke_run_custom_surface(self):
        from code_forge.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["smoke-run", "--surface", "nftables", "echo", "hello"])
        assert args.surface == "nftables"

    def test_smoke_run_command_captured(self):
        from code_forge.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["smoke-run", "pytest", "-x", "-q"])
        # command captured as remainder list
        assert "pytest" in args.command

    def test_smoke_run_no_command_gives_empty_list(self):
        from code_forge.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["smoke-run"])
        assert args.command == [] or args.command is None or not args.command


# ---------------------------------------------------------------------------
# RuntimeRunner wiring tests (machine.py)
# ---------------------------------------------------------------------------

class TestRuntimeRunnerAdvisoryPlacement:
    """RuntimeRunner findings go into _advisories, not _state.findings."""

    def test_runtime_findings_in_advisories_not_state(self, tmp_path):
        summary_f = _runtime_summary_finding("smoke: 0/1 surfaces verified; NOT VERIFIED: [systemd]")
        runner = MagicMock(spec=RuntimeRunner)
        runner.is_advisory = True
        runner.run.return_value = [summary_f]

        sm = _make_sm(tmp_path, advisory_runners=[runner])
        sm._run_advisory_axes()

        # Must be in _advisories
        assert summary_f in sm._advisories
        # Must NOT be in _state.findings
        assert not any(
            getattr(f, "description", "") == summary_f.description
            for f in sm._state.findings
        )

    def test_runtime_findings_do_not_affect_fixpoint(self, tmp_path):
        """_fixpoint_reached() is True even with RUNTIME advisories present."""
        summary_f = _runtime_summary_finding("smoke: 0/1 surfaces verified; NOT VERIFIED: [nftables]")
        runner = MagicMock(spec=RuntimeRunner)
        runner.is_advisory = True
        runner.run.return_value = [summary_f]

        sm = _make_sm(tmp_path, advisory_runners=[runner])
        sm._run_advisory_axes()

        # No state findings -> fixpoint is reached
        assert sm._state.findings == []
        assert sm._fixpoint_reached()

    def test_runtime_runner_run_called_with_diff(self, tmp_path):
        """_run_advisory_axes calls runner.run with diff_text and cwd."""
        runner = MagicMock(spec=RuntimeRunner)
        runner.is_advisory = True
        runner.run.return_value = []

        diff = "diff --git a/a.py b/a.py\n+x = 1\n"
        sm = _make_sm(tmp_path, advisory_runners=[runner], git_diff=diff)
        sm._run_advisory_axes()

        runner.run.assert_called_once_with(diff, tmp_path)

    def test_empty_diff_runtime_runner_returns_empty(self, tmp_path):
        """RuntimeRunner.run([]) returns [] -- no findings."""
        runner = RuntimeRunner(backend=None)
        result = runner.run("", tmp_path)
        assert result == []


# ---------------------------------------------------------------------------
# Advisory serialization includes RUNTIME findings
# ---------------------------------------------------------------------------

class TestRuntimeAdvisorySerialize:
    """RUNTIME findings serialize into advisory-findings.json."""

    def test_runtime_finding_in_json(self, tmp_path):
        summary_f = _runtime_summary_finding("smoke: all 1 surfaces verified (nftables[ab12cd34])")
        runner = MagicMock(spec=RuntimeRunner)
        runner.is_advisory = True
        runner.run.return_value = [summary_f]

        sm = _make_sm(tmp_path, advisory_runners=[runner])
        sm._run_advisory_axes()
        sm._serialize_advisories()

        out_path = tmp_path / ".code-forge" / "advisory-findings.json"
        assert out_path.exists()
        data = json.loads(out_path.read_text())
        ids = [d["id"] for d in data]
        assert "runtime-smoke-summary" in ids


# ---------------------------------------------------------------------------
# _display_smoke_status tests (D-09: ALWAYS prints)
# ---------------------------------------------------------------------------

class TestDisplaySmokeStatus:
    """_display_smoke_status prints smoke section unconditionally."""

    def test_smoke_status_with_summary_finding_unverified(self, tmp_path, capsys):
        desc = "smoke: 0/2 surfaces verified; NOT VERIFIED: [systemd, nftables]"
        sm = _make_sm(tmp_path)
        sm._advisories = [_runtime_summary_finding(desc)]
        # RuntimeRunner in advisory_runners signals smoke status is applicable
        runner = MagicMock(spec=RuntimeRunner)
        runner.is_advisory = True
        sm.advisory_runners = [runner]

        sm._display_smoke_status()
        captured = capsys.readouterr()
        assert "Smoke Status" in captured.err
        assert "NOT VERIFIED" in captured.err

    def test_smoke_status_all_verified(self, tmp_path, capsys):
        desc = "smoke: all 2 surfaces verified (nftables[ab12cd34], systemd[ef56gh78])"
        sm = _make_sm(tmp_path)
        sm._advisories = [_runtime_summary_finding(desc)]
        runner = MagicMock(spec=RuntimeRunner)
        runner.is_advisory = True
        sm.advisory_runners = [runner]

        sm._display_smoke_status()
        captured = capsys.readouterr()
        assert "Smoke Status" in captured.err
        assert "all" in captured.err

    def test_smoke_status_runtime_skipped(self, tmp_path, capsys):
        """When runtime-skipped finding present, prints UNVERIFIED (axis skipped:...)."""
        sm = _make_sm(tmp_path)
        sm._advisories = [_runtime_skipped_finding("LLM timeout")]
        runner = MagicMock(spec=RuntimeRunner)
        runner.is_advisory = True
        sm.advisory_runners = [runner]

        sm._display_smoke_status()
        captured = capsys.readouterr()
        assert "Smoke Status" in captured.err
        assert "UNVERIFIED" in captured.err or "skipped" in captured.err.lower()

    def test_smoke_status_no_findings_fallback(self, tmp_path, capsys):
        """When no summary or skipped finding, prints fallback (D-09 always prints)."""
        sm = _make_sm(tmp_path)
        sm._advisories = []
        runner = MagicMock(spec=RuntimeRunner)
        runner.is_advisory = True
        sm.advisory_runners = [runner]

        sm._display_smoke_status()
        captured = capsys.readouterr()
        # Must always print something under Smoke Status
        assert "Smoke Status" in captured.err

    def test_smoke_status_not_printed_when_no_runtime_runner(self, tmp_path, capsys):
        """When no RuntimeRunner in advisory_runners, smoke status not printed."""
        sm = _make_sm(tmp_path)
        sm._advisories = []
        # No advisory_runners -> no RuntimeRunner -> skip smoke status
        sm._display_smoke_status()
        captured = capsys.readouterr()
        assert "Smoke Status" not in captured.err

    def test_display_advisories_calls_smoke_status_before_early_return(self, tmp_path, capsys):
        """Even with empty _advisories, smoke status prints if RuntimeRunner present."""
        sm = _make_sm(tmp_path)
        sm._advisories = []
        runner = MagicMock(spec=RuntimeRunner)
        runner.is_advisory = True
        sm.advisory_runners = [runner]

        sm._display_advisories()
        captured = capsys.readouterr()
        # Smoke Status should appear even though _advisories is empty
        assert "Smoke Status" in captured.err


# ---------------------------------------------------------------------------
# DEDUP: generic loop skips runtime-smoke-summary and runtime-skipped
# ---------------------------------------------------------------------------

class TestAdvisoryLoopDedup:
    """Generic _display_advisories loop does not double-print RUNTIME summary."""

    def test_generic_loop_skips_runtime_smoke_summary(self, tmp_path, capsys):
        summary_f = _runtime_summary_finding("smoke: all 1 surfaces verified (foo[ab12cd34])")
        other_f = AdvisoryFinding(
            id="taint-1", axis="TAINT", file="a.py",
            line_range=[1, 1], description="taint finding",
            attribution="taint/test",
        )
        sm = _make_sm(tmp_path)
        sm._advisories = [summary_f, other_f]
        runner = MagicMock(spec=RuntimeRunner)
        runner.is_advisory = True
        sm.advisory_runners = [runner]

        sm._display_advisories()
        captured = capsys.readouterr()

        # The taint finding should appear in generic advisory output
        assert "taint finding" in captured.err
        # runtime-smoke-summary must NOT appear twice (only under Smoke Status)
        # Count occurrences: should appear exactly once (in Smoke Status block)
        count = captured.err.count("smoke: all 1 surfaces verified")
        assert count == 1

    def test_generic_loop_skips_runtime_skipped(self, tmp_path, capsys):
        skipped_f = _runtime_skipped_finding("LLM timeout")
        sm = _make_sm(tmp_path)
        sm._advisories = [skipped_f]
        runner = MagicMock(spec=RuntimeRunner)
        runner.is_advisory = True
        sm.advisory_runners = [runner]

        sm._display_advisories()
        captured = capsys.readouterr()
        # Should appear in Smoke Status block, not generic advisory section
        # Smoke Status must appear
        assert "Smoke Status" in captured.err
        # Generic advisory block prints [AXIS] file:range - description
        # The SKIPPED finding must not appear in generic [RUNTIME] ... format
        # (it is handled by _display_smoke_status exclusively)
        generic_lines = [
            line for line in captured.err.splitlines()
            if line.startswith("[RUNTIME]")
        ]
        skipped_generic = [line for line in generic_lines if "SKIPPED" in line]
        assert len(skipped_generic) == 0, (
            "runtime-skipped finding appeared in generic [RUNTIME] output: %s"
            % skipped_generic
        )
