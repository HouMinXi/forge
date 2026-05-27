# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""StateMachine integration tests for E2E_CHECK wiring.

Covers: e2e_runner field default, _run_e2e_phase invocation, merged state,
_merge_findings priority, _append_round_snapshot e2e_fingerprints,
autofix skip for E2E_CHECK, falsifier bypass, and the end-to-end UNCERTAIN
-> Verdict.PENDING path.
"""

from pathlib import Path

from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.machine import StateMachine
from code_forge.state import Mode, StateFinding, Verdict


def _make_finding(fp="fp-1", source="L0", disp=Disposition.CONFIRMED, desc=""):
    return StateFinding(
        id=fp,
        fingerprint=fp,
        source=source,
        disposition=disp,
        file="",
        line_range=[],
        description=desc or "test finding",
    )


_STUB_DIFF = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,2 @@\n x\n+y\n"


def _make_resolved(diff_text=None):
    """Return a minimal ResolvedReview.

    git_diff must be non-None for _run_e2e_phase to forward the diff to
    e2e_runner; a None value causes the phase to record a non-fatal infra
    error and return no findings.
    """
    return ResolvedReview(
        source_files=[Path("test.py")],
        baseline_content=None,
        git_diff=diff_text if diff_text is not None else _STUB_DIFF,
        mode_hint="git",
    )


def _make_machine(tmp_path, *, e2e_runner=None, l0_runner=None, diff_text=None,
                  falsifier=None, autofixer=None):
    """Construct a minimal StateMachine for E2E integration tests."""
    return StateMachine(
        mode=Mode.LOCAL,
        falsifier=falsifier or StubFalsifier(),
        autofixer=autofixer or StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=_make_resolved(diff_text),
        source_hash="test-hash",
        baseline_spec_repr="empty",
        cwd=tmp_path,
        registry={},
        l0_runner=l0_runner or (lambda registry, files: ([], [])),
        e2e_runner=e2e_runner or (lambda dt, rr: ([], [])),
    )


class TestE2eRunnerFieldDefault:
    """e2e_runner field exists with a no-op default."""

    def test_field_exists_and_default_returns_empty_tuple(self, tmp_path):
        machine = _make_machine(tmp_path)
        assert hasattr(machine, "e2e_runner")
        result = machine.e2e_runner("", Path("."))
        findings, errors = result
        assert findings == []
        assert errors == []


class TestRunE2ePhaseInvokedOncePerRound:
    """_run_e2e_phase is called exactly once per _execute_round call."""

    def test_e2e_runner_called_once_per_round(self, tmp_path):
        call_log = []
        expected_diff = _STUB_DIFF

        def tracking_runner(dt, rr):
            call_log.append((dt, rr))
            return ([], [])

        machine = _make_machine(tmp_path, e2e_runner=tracking_runner)
        machine._execute_round(round_index=0)
        assert len(call_log) == 1
        dt_arg, rr_arg = call_log[0]
        assert dt_arg == expected_diff
        assert rr_arg == tmp_path


class TestE2eFindingsReachState:
    """E2E_CHECK findings from e2e_runner appear in self._state.findings."""

    def test_e2e_finding_in_state_after_round(self, tmp_path):
        fp = "e2e-test-1"

        def e2e_runner(dt, rr):
            f = _make_finding(fp=fp, source="E2E_CHECK", disp=Disposition.DISMISSED)
            return ([f], [])

        machine = _make_machine(tmp_path, e2e_runner=e2e_runner)
        machine._execute_round(round_index=0)
        fps = [f.fingerprint for f in machine._state.findings]
        assert fp in fps


class TestMergeFindingsPriority:
    """_merge_findings priority -- L0 wins on collision; all appear disjoint."""

    def test_l0_wins_on_fingerprint_collision(self, tmp_path):
        machine = _make_machine(tmp_path)
        shared_fp = "collision-fp"
        l0 = _make_finding(fp=shared_fp, source="L0", disp=Disposition.CONFIRMED)
        l1 = _make_finding(fp=shared_fp, source="L1", disp=Disposition.DISMISSED)
        l2 = _make_finding(fp=shared_fp, source="MUTANT", disp=Disposition.DISMISSED)
        e2e = _make_finding(fp=shared_fp, source="E2E_CHECK", disp=Disposition.DISMISSED)
        merged = machine._merge_findings([l0], [l1], [l2], [e2e])
        assert len(merged) == 1
        assert merged[0].source == "L0"

    def test_all_four_sources_appear_when_disjoint(self, tmp_path):
        machine = _make_machine(tmp_path)
        l0 = _make_finding(fp="fp-l0", source="L0", disp=Disposition.CONFIRMED)
        l1 = _make_finding(fp="fp-l1", source="L1", disp=Disposition.DISMISSED)
        l2 = _make_finding(fp="fp-l2", source="MUTANT", disp=Disposition.DISMISSED)
        e2e = _make_finding(
            fp="fp-e2e", source="E2E_CHECK", disp=Disposition.DISMISSED
        )
        merged = machine._merge_findings([l0], [l1], [l2], [e2e])
        fps = {f.fingerprint for f in merged}
        assert fps == {"fp-l0", "fp-l1", "fp-l2", "fp-e2e"}


class TestAppendRoundSnapshotRecordsE2eFp:
    """_append_round_snapshot records e2e_fingerprints."""

    def test_e2e_fingerprints_in_snapshot(self, tmp_path):
        fp = "e2e-snap-1"

        def e2e_runner(dt, rr):
            f = _make_finding(fp=fp, source="E2E_CHECK", disp=Disposition.DISMISSED)
            return ([f], [])

        machine = _make_machine(tmp_path, e2e_runner=e2e_runner)
        machine._execute_round(round_index=0)
        snapshot = machine._state.round_history[-1]
        assert "e2e_fingerprints" in snapshot
        assert fp in snapshot["e2e_fingerprints"]


class TestAutofixSkipsE2eCheck:
    """_apply_autofix_loop_to never invokes autofixer for E2E_CHECK or MUTANT."""

    def test_autofixer_not_called_for_e2e_and_mutant(self, tmp_path):
        call_count = {"n": 0}

        class TrackingAutoFixer(StubAutoFixer):
            def fix(self, finding, mode_hint):
                call_count["n"] += 1
                return super().fix(finding, mode_hint)

        machine = _make_machine(tmp_path, autofixer=TrackingAutoFixer())
        e2e_finding = _make_finding(
            fp="e2e-skip", source="E2E_CHECK", disp=Disposition.CONFIRMED
        )
        mutant_finding = _make_finding(
            fp="mut-skip", source="MUTANT", disp=Disposition.CONFIRMED
        )
        machine._apply_autofix_loop_to([e2e_finding, mutant_finding])
        assert call_count["n"] == 0, (
            "autofixer must not be called for E2E_CHECK or MUTANT sources"
        )


class TestE2eCheckBypassesFalsifier:
    """e2e findings pass through _run_e2e_phase, not through falsifier."""

    def test_falsifier_not_called_on_e2e_only_round(self, tmp_path):
        call_count = {"n": 0}

        class TrackingFalsifier(StubFalsifier):
            def falsify(self, finding):
                call_count["n"] += 1
                return super().falsify(finding)

        e2e_fp = "e2e-bypass"

        def e2e_runner(dt, rr):
            f = _make_finding(
                fp=e2e_fp, source="E2E_CHECK", disp=Disposition.UNCERTAIN
            )
            return ([f], [])

        machine = _make_machine(
            tmp_path,
            falsifier=TrackingFalsifier(),
            e2e_runner=e2e_runner,
        )
        machine._execute_round(round_index=0)
        assert call_count["n"] == 0, (
            "falsifier must not be invoked for E2E_CHECK findings"
        )


class TestUncertainE2eLeadsToHoldVerdict:
    """An UNCERTAIN E2E_CHECK finding drives the pipeline to PENDING.

    This is the primary user-visible behavior: Layer 2's UNCERTAIN output
    must enter the _should_enter_hold path and produce Verdict.PENDING.
    """

    def test_uncertain_e2e_finding_produces_pending_verdict(self, tmp_path):
        """UNCERTAIN E2E_CHECK finding -> Verdict.PENDING (HOLD).

        Setup: L0 and L1 produce zero findings so no CONFIRMED remains.
        The single UNCERTAIN e2e finding triggers _should_enter_hold, which
        sets Verdict.PENDING and populates hold_reason.
        """
        e2e_fp = "e2e-l2-hold-test"

        def e2e_runner(dt, rr):
            f = StateFinding(
                id="e2e-layer2",
                fingerprint=e2e_fp,
                source="E2E_CHECK",
                disposition=Disposition.UNCERTAIN,
                file="",
                line_range=[],
                description="cross-component e2e coverage gap",
            )
            return ([f], [])

        machine = _make_machine(tmp_path, e2e_runner=e2e_runner)
        verdict = machine.run()

        assert verdict == Verdict.PENDING, (
            "UNCERTAIN E2E_CHECK finding must cause HOLD (Verdict.PENDING)"
        )

        # hold_reason is set by _run_local on _should_enter_hold() -> True.
        assert machine._state.hold_reason is not None
        assert len(machine._state.hold_reason) > 0

        # The e2e finding with the expected fingerprint must be in state.
        fps_in_state = {f.fingerprint for f in machine._state.findings}
        assert e2e_fp in fps_in_state, (
            "e2e finding fingerprint %r must appear in state.findings" % e2e_fp
        )


class TestRunE2ePhaseNonGitMode:
    """_run_e2e_phase with git_diff=None records the infra signal."""

    def test_run_e2e_phase_non_git_mode_returns_empty_with_infra_signal(
        self, tmp_path
    ):
        """non-git mode (git_diff=None) records the infra signal and
        returns no findings without invoking the runner.
        """
        runner_call_count = {"n": 0}

        def counting_runner(dt, rr):
            runner_call_count["n"] += 1
            return ([], [])

        # Build a machine whose resolved_review has git_diff=None.
        # _make_resolved with diff_text=None falls back to _STUB_DIFF (see
        # module docstring), so construct ResolvedReview directly here.
        resolved = ResolvedReview(
            source_files=[],
            baseline_content=None,
            git_diff=None,
            mode_hint="non-git",
        )
        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=StubAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=resolved,
            source_hash="test-hash",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=lambda registry, files: ([], []),
            e2e_runner=counting_runner,
        )

        findings = machine._run_e2e_phase()

        assert findings == [], "non-git mode must return no findings"
        assert any(
            "e2e: no git diff available (non-git review)" in msg
            for msg in machine._state.infra_errors
        ), "infra signal must be recorded when git_diff is None"
        assert runner_call_count["n"] == 0, (
            "e2e_runner must not be called when git_diff is None"
        )
