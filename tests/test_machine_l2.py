# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for StateMachine L2 integration (mutation pipeline).

Covers: l2_runner wiring, consecutive_survivor_rounds, MUTANT autofix skip,
CI async mutation, bug-inject teeth test (EC-6).
"""

import json
from pathlib import Path

from forge.autofix import StubAutoFixer
from forge.baseline import ResolvedReview
from forge.disposition import Disposition
from forge.falsify import StubFalsifier
from forge.machine import StateMachine
from forge.state import Mode, StateFinding, Verdict


def _make_finding(fp="fp-1", source="L0", disp=Disposition.CONFIRMED):
    return StateFinding(
        id=fp,
        fingerprint=fp,
        source=source,
        disposition=disp,
        file="test.py",
        line_range=[1, 1],
        description="test finding",
    )


def _make_resolved():
    return ResolvedReview(
        source_files=[Path("test.py")],
        baseline_content=None,
        git_diff=None,
        mode_hint="git",
    )


def _setup_gate_yaml(tmp_path):
    """Create .forge/gate.yaml for l2_runner tests."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir(exist_ok=True)
    gate_yaml = forge_dir / "gate.yaml"
    gate_yaml.write_text("test:\n  command: ['pytest']\n")


class TestL2RunnerDefaultNoOp:
    """Test 1: l2_runner default (no-op) -> zero L2 findings, PASS as before."""

    def test_default_l2_runner_produces_zero_findings(self, tmp_path):
        def mock_l0(registry, files):
            return ([], [])

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=StubAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
        )
        verdict = machine.run()
        assert verdict == Verdict.PASS
        assert machine._state.consecutive_survivor_rounds == 0


class TestL2RunnerConfirmedMutant:
    """Test 2: l2_runner returns CONFIRMED MUTANT finding -> prevents fixpoint."""

    def test_mutant_prevents_fixpoint(self, tmp_path):
        _setup_gate_yaml(tmp_path)

        def mock_l0(registry, files):
            return ([], [])

        def mock_l2(diff_files, baseline_cmd):
            finding = _make_finding(
                fp="mutant-1", source="MUTANT", disp=Disposition.CONFIRMED
            )
            return ([finding], [])

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=StubAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
            l2_runner=mock_l2,
        )
        verdict = machine.run()
        assert verdict == Verdict.FAIL
        assert machine._state.consecutive_survivor_rounds == 3


class TestMutantFindingsSkipAutofix:
    """Test 3: MUTANT findings skip autofix."""

    def test_mutant_source_skips_autofix_loop(self, tmp_path):
        _setup_gate_yaml(tmp_path)
        autofix_called = {"count": 0}

        class TrackingAutoFixer(StubAutoFixer):
            def fix(self, finding, mode_hint):
                autofix_called["count"] += 1
                return super().fix(finding, mode_hint)

        def mock_l0(registry, files):
            return ([], [])

        def mock_l2(diff_files, baseline_cmd):
            finding = _make_finding(
                fp="mutant-1", source="MUTANT", disp=Disposition.CONFIRMED
            )
            return ([finding], [])

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=TrackingAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
            l2_runner=mock_l2,
        )
        verdict = machine.run()
        assert verdict == Verdict.FAIL
        assert autofix_called["count"] == 0


class TestConsecutiveSurvivorRounds:
    """Test 4 & 5: consecutive_survivor_rounds increments and resets."""

    def test_counter_increments_with_survivors(self, tmp_path):
        _setup_gate_yaml(tmp_path)
        round_counter = {"n": 0}

        def mock_l0(registry, files):
            return ([], [])

        def mock_l2(diff_files, baseline_cmd):
            round_counter["n"] += 1
            if round_counter["n"] <= 2:
                finding = _make_finding(
                    fp="mutant-1", source="MUTANT", disp=Disposition.CONFIRMED
                )
                return ([finding], [])
            return ([], [])

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=StubAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
            l2_runner=mock_l2,
        )
        verdict = machine.run()
        assert verdict == Verdict.PASS
        assert machine._state.consecutive_survivor_rounds == 0

    def test_counter_resets_when_no_survivors(self, tmp_path):
        _setup_gate_yaml(tmp_path)
        round_counter = {"n": 0}

        def mock_l0(registry, files):
            return ([], [])

        def mock_l2(diff_files, baseline_cmd):
            round_counter["n"] += 1
            if round_counter["n"] == 1:
                finding = _make_finding(
                    fp="mutant-1", source="MUTANT", disp=Disposition.CONFIRMED
                )
                return ([finding], [])
            return ([], [])

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=StubAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
            l2_runner=mock_l2,
        )
        verdict = machine.run()
        assert verdict == Verdict.PASS
        assert machine._state.consecutive_survivor_rounds == 0


class TestThreeConsecutiveSurvivorRounds:
    """Test 6: 3 consecutive survivor rounds -> Verdict.FAIL with infra_errors."""

    def test_fail_after_3_rounds(self, tmp_path):
        _setup_gate_yaml(tmp_path)

        def mock_l0(registry, files):
            return ([], [])

        def mock_l2(diff_files, baseline_cmd):
            finding = _make_finding(
                fp="mutant-1", source="MUTANT", disp=Disposition.CONFIRMED
            )
            return ([finding], [])

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=StubAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
            l2_runner=mock_l2,
        )
        verdict = machine.run()
        assert verdict == Verdict.FAIL
        assert machine._state.consecutive_survivor_rounds == 3
        assert any("demonstrably weak" in e for e in machine._state.infra_errors)


class TestL2RunnerException:
    """Test 7: l2_runner exception does not crash state machine."""

    def test_exception_graceful_degradation(self, tmp_path):
        _setup_gate_yaml(tmp_path)

        def mock_l0(registry, files):
            return ([], [])

        def mock_l2(diff_files, baseline_cmd):
            raise RuntimeError("mutation crash")

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=StubAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
            l2_runner=mock_l2,
        )
        verdict = machine.run()
        assert verdict == Verdict.PASS
        assert any("L2 runner failed" in e for e in machine._state.infra_errors)


class TestCIModeReadsMutationResult:
    """Test 8 & 9: CI mode reads mutation-result.json."""

    def test_ci_status_done_with_survivors_fails(self, tmp_path):
        forge_dir = tmp_path / ".forge"
        forge_dir.mkdir()
        result_path = forge_dir / "mutation-result.json"
        result_data = {
            "pid": 12345,
            "started_at": 1234567890.0,
            "status": "done",
            "survivors": ["test.py:1", "test.py:2"],
        }
        result_path.write_text(json.dumps(result_data))

        gate_yaml_path = forge_dir / "gate.yaml"
        gate_yaml_path.write_text("test:\n  command: ['pytest']\n")

        def mock_l0(registry, files):
            return ([], [])

        machine = StateMachine(
            mode=Mode.CI,
            falsifier=StubFalsifier(),
            autofixer=StubAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
        )
        verdict = machine.run()
        assert verdict == Verdict.FAIL
        assert any("mutation survivors" in e for e in machine._state.infra_errors)

    def test_ci_status_running_dead_pid_appends_skipped(self, tmp_path):
        forge_dir = tmp_path / ".forge"
        forge_dir.mkdir()
        result_path = forge_dir / "mutation-result.json"
        result_data = {
            "pid": 999999999,
            "started_at": 1234567890.0,
            "status": "running",
            "survivors": [],
        }
        result_path.write_text(json.dumps(result_data))

        gate_yaml_path = forge_dir / "gate.yaml"
        gate_yaml_path.write_text("test:\n  command: ['pytest']\n")

        def mock_l0(registry, files):
            return ([], [])

        machine = StateMachine(
            mode=Mode.CI,
            falsifier=StubFalsifier(),
            autofixer=StubAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
        )
        verdict = machine.run()
        assert verdict == Verdict.PASS
        skipped_findings = [
            f for f in machine._state.findings if f.id == "MUTATION_SKIPPED"
        ]
        assert len(skipped_findings) == 1
        assert "process died" in skipped_findings[0].description


class TestBugInjectTeeth:
    """Test 10 (EC-6): Bug-inject teeth test."""

    def test_toothless_test_surfaces_survivor(self, tmp_path):
        """Toothless test scenario -- l2_runner always returns a survivor."""
        _setup_gate_yaml(tmp_path)

        def mock_l0(registry, files):
            return ([], [])

        def mock_l2_toothless(diff_files, baseline_cmd):
            finding = _make_finding(
                fp="mutant-toothless", source="MUTANT", disp=Disposition.CONFIRMED
            )
            return ([finding], [])

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=StubAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
            l2_runner=mock_l2_toothless,
        )
        verdict = machine.run()
        assert verdict == Verdict.FAIL
        assert machine._state.consecutive_survivor_rounds == 3
        assert any("demonstrably weak" in e for e in machine._state.infra_errors)

    def test_remove_toothless_clears(self, tmp_path):
        """Remove the survivor (l2_runner returns clean) -> Verdict.PASS."""

        def mock_l0(registry, files):
            return ([], [])

        def mock_l2_clean(diff_files, baseline_cmd):
            return ([], [])

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=StubAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
            l2_runner=mock_l2_clean,
        )
        verdict = machine.run()
        assert verdict == Verdict.PASS
        assert machine._state.consecutive_survivor_rounds == 0
