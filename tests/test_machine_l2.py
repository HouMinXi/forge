# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for StateMachine L2 integration (mutation pipeline).

Covers: l2_runner wiring, consecutive_survivor_rounds, MUTANT autofix skip,
CI async mutation, bug-inject teeth test.
"""

import json
from pathlib import Path

from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.machine import StateMachine
from code_forge.state import Mode, StateFinding, Verdict


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
    """Create .code-forge/gate.yaml for l2_runner tests."""
    forge_dir = tmp_path / ".code-forge"
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
        forge_dir = tmp_path / ".code-forge"
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

    def test_ci_status_running_dead_pid_appends_skipped(
        self, tmp_path, monkeypatch
    ):
        forge_dir = tmp_path / ".code-forge"
        forge_dir.mkdir()
        result_path = forge_dir / "mutation-result.json"
        result_data = {
            "pid": 999999999,
            "started_at": 1234567890.0,
            "status": "running",
            "survivors": [],
        }
        result_path.write_text(json.dumps(result_data))

        # This test is about the dead-PID cleanup path above, not about
        # the relaunch attempt below it. Force the relaunch check to a
        # deterministic no-op regardless of whether this machine happens
        # to have mutmut on PATH: fake "mutmut present" so the skip-
        # finding elif branches are never reached, but leave gate.yaml
        # absent so baseline_cmd resolution fails and no thread starts.
        monkeypatch.setattr("shutil.which", lambda cmd: "/fake/mutmut")

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


class TestCIMutationResultNotSticky:
    """A prior run's mutation skip/error must not poison later runs.

    mutmut is an optional dev dependency; a default install does not have
    it. Reviewing twice in the same repo without mutmut must not turn the
    second review red because of a note the first review left behind.
    """

    def _machine(self, tmp_path):
        def mock_l0(registry, files):
            return ([], [])

        return StateMachine(
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

    def test_two_ci_runs_without_mutmut_both_pass(self, tmp_path, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda cmd: None)
        _setup_gate_yaml(tmp_path)

        assert self._machine(tmp_path).run() == Verdict.PASS
        second = self._machine(tmp_path)
        assert second.run() == Verdict.PASS
        assert not any(
            "mutation error" in e for e in second._state.infra_errors
        )

    def test_skip_leaves_no_result_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda cmd: None)
        _setup_gate_yaml(tmp_path)

        assert self._machine(tmp_path).run() == Verdict.PASS
        result_path = tmp_path / ".code-forge" / "mutation-result.json"
        assert not result_path.exists()

    def test_prior_error_result_degrades_gracefully(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("shutil.which", lambda cmd: None)
        _setup_gate_yaml(tmp_path)
        result_path = tmp_path / ".code-forge" / "mutation-result.json"
        result_path.write_text(
            json.dumps(
                {
                    "pid": 12345,
                    "started_at": 1234567890.0,
                    "status": "error",
                    "message": "boom",
                }
            )
        )

        machine = self._machine(tmp_path)
        assert machine.run() == Verdict.PASS
        assert any(
            "mutation error: boom" in e for e in machine._state.infra_errors
        )
        assert not result_path.exists()

    def test_unlink_failure_reports_remove_not_read(
        self, tmp_path, monkeypatch
    ):
        """A failed delete must be labeled as a delete failure, not
        folded into the JSON-read except clause's "failed to read"
        message -- the two are different problems for an operator to
        act on.
        """
        monkeypatch.setattr("shutil.which", lambda cmd: None)
        _setup_gate_yaml(tmp_path)
        result_path = tmp_path / ".code-forge" / "mutation-result.json"
        result_path.write_text(
            json.dumps(
                {
                    "pid": 12345,
                    "started_at": 1234567890.0,
                    "status": "error",
                    "message": "boom",
                }
            )
        )

        def _boom_unlink(self):
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "unlink", _boom_unlink)

        machine = self._machine(tmp_path)
        assert machine.run() == Verdict.PASS
        errors = machine._state.infra_errors
        assert any("failed to remove" in e for e in errors)
        assert not any("failed to read" in e for e in errors)

    def test_missing_status_field_consumes_result_file(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("shutil.which", lambda cmd: None)
        _setup_gate_yaml(tmp_path)
        result_path = tmp_path / ".code-forge" / "mutation-result.json"
        result_path.write_text(json.dumps({"pid": 12345}))

        machine = self._machine(tmp_path)
        assert machine.run() == Verdict.PASS
        assert any(
            "missing status field" in e
            for e in machine._state.infra_errors
        )
        assert not result_path.exists()

    def test_running_status_missing_pid_consumes_result_file(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("shutil.which", lambda cmd: None)
        _setup_gate_yaml(tmp_path)
        result_path = tmp_path / ".code-forge" / "mutation-result.json"
        result_path.write_text(
            json.dumps(
                {
                    "started_at": 1234567890.0,
                    "status": "running",
                    "survivors": [],
                }
            )
        )

        machine = self._machine(tmp_path)
        assert machine.run() == Verdict.PASS
        assert any(
            "missing pid field" in e for e in machine._state.infra_errors
        )
        assert not result_path.exists()

    def test_non_dict_json_degrades_gracefully(self, tmp_path, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda cmd: None)
        _setup_gate_yaml(tmp_path)
        result_path = tmp_path / ".code-forge" / "mutation-result.json"
        # Valid JSON, but not an object: "status" not in result_data
        # would raise TypeError on a bare int with no dict guard.
        result_path.write_text("42")

        machine = self._machine(tmp_path)
        assert machine.run() == Verdict.PASS
        assert any(
            "not a JSON object" in e for e in machine._state.infra_errors
        )
        assert not result_path.exists()

    def test_done_no_survivors_consumes_result_file(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("shutil.which", lambda cmd: None)
        _setup_gate_yaml(tmp_path)
        result_path = tmp_path / ".code-forge" / "mutation-result.json"
        result_path.write_text(
            json.dumps(
                {
                    "pid": 12345,
                    "started_at": 1234567890.0,
                    "status": "done",
                    "survivors": [],
                }
            )
        )

        machine = self._machine(tmp_path)
        assert machine.run() == Verdict.PASS
        assert not result_path.exists()

    def test_survivors_fail_consumes_result_file(self, tmp_path):
        _setup_gate_yaml(tmp_path)
        result_path = tmp_path / ".code-forge" / "mutation-result.json"
        result_path.write_text(
            json.dumps(
                {
                    "pid": 12345,
                    "started_at": 1234567890.0,
                    "status": "done",
                    "survivors": ["test.py:1"],
                }
            )
        )

        machine = self._machine(tmp_path)
        assert machine.run() == Verdict.FAIL
        assert not result_path.exists()


class TestCISkipIsVisibleAsFinding:
    """CI-mode mutation skips are a DISMISSED finding on the same run,
    not just a file only a later run would read (and could get stuck
    on, per TestCIMutationResultNotSticky above).
    """

    def _skipped(self, machine):
        return [
            f for f in machine._state.findings if f.id == "MUTATION_SKIPPED"
        ]

    def test_no_python_files_appends_dismissed_finding(self, tmp_path):
        # No shutil.which mock needed: `if py_files and ...` short-
        # circuits on the empty py_files list below, so mutmut's real
        # presence on this machine never enters the decision.
        _setup_gate_yaml(tmp_path)

        def mock_l0(registry, files):
            return ([], [])

        resolved = ResolvedReview(
            source_files=[Path("test.txt")],
            baseline_content=None,
            git_diff=None,
            mode_hint="git",
        )
        machine = StateMachine(
            mode=Mode.CI,
            falsifier=StubFalsifier(),
            autofixer=StubAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=resolved,
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
        )
        assert machine.run() == Verdict.PASS
        skipped = self._skipped(machine)
        assert len(skipped) == 1
        assert skipped[0].fingerprint == "mutation-no-python"
        assert skipped[0].disposition == Disposition.DISMISSED

    def test_mutmut_absent_appends_dismissed_finding(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("shutil.which", lambda cmd: None)
        _setup_gate_yaml(tmp_path)

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
        assert machine.run() == Verdict.PASS
        skipped = self._skipped(machine)
        assert len(skipped) == 1
        assert skipped[0].fingerprint == "mutation-unavailable"
        assert skipped[0].disposition == Disposition.DISMISSED


class TestBugInjectTeeth:
    """Test 10: Bug-inject teeth test."""

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
