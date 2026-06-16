# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for StateMachine LOCAL mode.

STATE-01/02/04: LOCAL loop, fixpoint, MAX_TOTAL_ROUNDS exhaustion.
"""

from pathlib import Path


from code_forge.autofix import FixOutcome, StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.llm_invoke import Usage
from code_forge.machine import StateMachine
from code_forge.state import Mode, StateFinding, Verdict


def _make_finding(fp="fp-local-1", disp=Disposition.CONFIRMED):
    return StateFinding(
        id=fp,
        fingerprint=fp,
        source="L0",
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


class TestLocalZeroFindings:
    """(a) Zero findings -> PASS round 0."""

    def test_pass_immediately(self, tmp_path):
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
        assert machine._state.converged is True
        assert machine._state.round == 2  # 3 consecutive clean rounds


class TestLocalAutofixSuccess:
    """(b) CONFIRMED + autofix SUCCESS -> FIXED -> next round clean -> PASS."""

    def test_fix_then_pass(self, tmp_path):
        round_counter = {"n": 0}

        def mock_l0(registry, files):
            n = round_counter["n"]
            round_counter["n"] += 1
            if n == 0:
                # Round 0: finding present
                return ([_make_finding()], [])
            # Round 1+: finding gone (fix succeeded)
            return ([], [])

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=StubAutoFixer(),  # default SUCCESS
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
        assert machine._state.converged is True


class TestLocalMaxRoundsExhausted:
    """(c) MAX_TOTAL_ROUNDS exhaust -> ESCALATED + diagnosis recorded."""

    def test_escalated_on_stuck(self, tmp_path):
        """CONFIRMED that re-appears every round, autofix NO_CHANGE.

        max_fix_attempts set higher than max_total_rounds so promotion
        to UNCERTAIN never happens -- CONFIRMED persists until
        MAX_TOTAL_ROUNDS exhaustion triggers ESCALATED.
        """
        def mock_l0(registry, files):
            return ([_make_finding()], [])

        class NoChangeAutoFixer(StubAutoFixer):
            def fix(self, finding, mode_hint):
                return FixOutcome.NO_CHANGE

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=NoChangeAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
            max_total_rounds=5,
            max_fix_attempts=100,
        )
        verdict = machine.run()
        assert verdict == Verdict.ESCALATED
        assert machine._state.converged is False
        # STATE-05 diagnosis recorded in infra_errors
        assert any(
            "ESCALATED category=" in e
            for e in machine._state.infra_errors
        )


class TestLocalConvergedSemantics:
    """SC-17: LOCAL PASS -> converged=True, ESCALATED -> False,
    PENDING (HOLD) -> False.
    """

    def test_pass_converged_true(self, tmp_path):
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
        machine.run()
        assert machine._state.converged is True

    def test_escalated_converged_false(self, tmp_path):
        def mock_l0(registry, files):
            return ([_make_finding()], [])

        class NoChangeAutoFixer(StubAutoFixer):
            def fix(self, finding, mode_hint):
                return FixOutcome.NO_CHANGE

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=NoChangeAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
            max_total_rounds=3,
            max_fix_attempts=100,
        )
        machine.run()
        assert machine._state.converged is False


class TestPostRoundHook:
    """SC-15: post_round_hook(round_index) invoked at end of each round."""

    def test_hook_called_per_round(self, tmp_path):
        calls = []

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
            post_round_hook=lambda r: calls.append(r),
        )
        machine.run()
        assert calls == [0, 1, 2]  # 3 consecutive clean rounds

    def test_hook_none_is_noop(self, tmp_path):
        """Default None post_round_hook does not raise."""
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
        machine.run()  # should not raise


class TestCostAccumulation:
    """CLI-08: StateMachine accumulates cost from l1_provider usage."""

    def test_cost_accumulated_per_round(self, tmp_path):
        """After 2 rounds, cost_passes=6 (2 rounds x 3 passes each)."""
        round_counter = {"n": 0}

        def mock_l0(registry, files):
            n = round_counter["n"]
            round_counter["n"] += 1
            if n == 0:
                return ([_make_finding()], [])
            return ([], [])

        def mock_l1():
            return ([], [], Usage(input_tokens=1000, output_tokens=500), 12.5)

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
            l1_provider=mock_l1,
        )
        machine.run()

        state = machine._state
        # Rounds until fixpoint (>= 2); each round = 3 passes.
        assert state.cost_passes % 3 == 0  # always multiple of 3
        assert state.cost_passes >= 6  # at least 2 rounds needed
        assert state.cost_total_input == state.cost_passes // 3 * 1000
        assert state.cost_total_output == state.cost_passes // 3 * 500
        assert abs(state.cost_total_duration - state.cost_passes // 3 * 12.5) < 0.5
        assert len(state.cost_per_pass) == state.cost_passes

    def test_cost_per_pass_structure(self, tmp_path):
        """cost_per_pass entries have pass (1-3), cycle, input, output, duration_s."""
        def mock_l0(registry, files):
            return ([], [])

        def mock_l1():
            return ([], [], Usage(input_tokens=300, output_tokens=150), 6.0)

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
            l1_provider=mock_l1,
        )
        machine.run()

        state = machine._state
        # Multiple rounds until fixpoint; entries are multiples of 3
        assert len(state.cost_per_pass) % 3 == 0
        assert len(state.cost_per_pass) > 0
        entry = state.cost_per_pass[0]
        assert entry["pass"] == 1
        assert entry["cycle"] == 0
        assert "input" in entry
        assert "output" in entry
        assert "duration_s" in entry

    def test_zero_cost_when_no_l1(self, tmp_path):
        """No l1_provider calls -> cost fields remain zero."""
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
            l0_runner=lambda r, f: ([], []),
        )
        machine.run()

        state = machine._state
        assert state.cost_total_input == 0
        assert state.cost_total_output == 0
        assert state.cost_passes % 3 == 0  # always multiple of 3 passes
        assert state.cost_passes >= 3  # at least 1 round


class TestInfraFindingSkipsFalsifier:
    """F3 regression: INFRA findings skip falsifier, block fixpoint."""

    def test_infra_finding_blocks_fixpoint(self, tmp_path):
        """INFRA finding stays CONFIRMED even with a dismiss-all falsifier.

        Machine must not converge to PASS because the INFRA finding
        blocks fixpoint every round (consecutive_clean_rounds stays 0).
        """
        import json as _json
        fixture = tmp_path / "falsify.json"
        fixture.write_text(_json.dumps({"default": "DISMISSED"}))
        dismisser = StubFalsifier(fixture_path=fixture)

        infra_finding = StateFinding(
            id="l1-qodo-invoke-fail",
            fingerprint="invoke-fail-qodo",
            source="INFRA",
            disposition=Disposition.CONFIRMED,
            file="<llm-invoke>",
            line_range=[0, 0],
            description="L1 invoke failed: timeout",
        )

        def mock_l1():
            return ([infra_finding], [], Usage(), 0.0)

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=dismisser,
            autofixer=StubAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=lambda r, f: ([], []),
            l1_provider=mock_l1,
            max_total_rounds=5,
            clean_round_threshold=3,
        )
        verdict = machine.run()
        assert verdict == Verdict.ESCALATED
        assert machine._state.consecutive_clean_rounds == 0
        # INFRA finding disposition must remain CONFIRMED (not DISMISSED)
        infra_in_state = [
            f for f in machine._state.findings
            if f.source == "INFRA"
        ]
        assert len(infra_in_state) > 0
        for f in infra_in_state:
            assert f.disposition == Disposition.CONFIRMED


# ---------------------------------------------------------------------------
# Severity-tiered _fixpoint_reached guards
# ---------------------------------------------------------------------------
from code_forge.machine import _FixpointResult, _severity_tier  # noqa: E402


def _make_sm_fp(tmp_path, git_diff=None):
    resolved = ResolvedReview(
        source_files=[Path("test.py")],
        baseline_content=None,
        git_diff=git_diff,
        mode_hint="git",
    )
    return StateMachine(
        resolved_review=resolved,
        falsifier=StubFalsifier(),
        source_hash="abc",
        baseline_spec_repr="empty",
        cwd=tmp_path,
        registry={},
        mode=Mode.LOCAL,
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        l0_runner=lambda r, f: ([], []),
        l1_provider=lambda r: [],
    )


def _sf(fp, desc, source="L1"):
    return StateFinding(
        id=fp, fingerprint=fp, source=source,
        disposition=Disposition.CONFIRMED,
        file="test.py", line_range=[1, 1],
        description=desc,
    )


def _prime(sm, *fingerprints):
    """Make fingerprints appear as CONFIRMED in prior round (not new)."""
    sm._state.round_history = [
        {"dispositions": {fp: "CONFIRMED" for fp in fingerprints}},
        {},
    ]


class TestTieredReset:
    """_fixpoint_reached() returns the correct _FixpointResult for each severity."""

    def test_tiered_reset_p2_returns_cycle_restart(self, tmp_path):
        sm = _make_sm_fp(tmp_path)
        sm._state.findings.append(_sf("fp-p2", "P2: missing docstring"))
        _prime(sm, "fp-p2")
        sm._state.consecutive_clean_rounds = 2
        result = sm._fixpoint_reached()
        assert result == _FixpointResult.CYCLE_RESTART

    def test_tiered_reset_p0_resets_counter(self, tmp_path):
        sm = _make_sm_fp(tmp_path)
        sm._state.findings.append(_sf("fp-p0", "P0: null dereference"))
        _prime(sm, "fp-p0")
        result = sm._fixpoint_reached()
        assert result == _FixpointResult.RESET

    def test_tiered_reset_p3_below_threshold_is_clean(self, tmp_path):
        # 2 P3 findings, 100 changed lines -> density = 0.02 < 0.15 -> CLEAN
        added = "\n".join("+line %d" % i for i in range(100))
        fake_diff = (
            "diff --git a/t.py b/t.py\n--- a/t.py\n+++ b/t.py\n"
            "@@ -0,0 +1,100 @@\n" + added
        )
        sm = _make_sm_fp(tmp_path, git_diff=fake_diff)
        sm._state.findings.append(_sf("fp-p3a", "P3: trailing whitespace"))
        sm._state.findings.append(_sf("fp-p3b", "P3: unused import"))
        _prime(sm, "fp-p3a", "fp-p3b")
        assert sm._fixpoint_reached() == _FixpointResult.CLEAN

    def test_p3_distinct_per_file_exceeds_triggers_restart(self, tmp_path):
        # 6 P3 findings in ONE file, each a different rule type -> distinct_per_file=6 > 5
        # density kept low (6 findings / 1000 lines = 0.006), distinct_per_diff=6 <= 10
        added = "\n".join("+line %d" % i for i in range(1000))
        fake_diff = (
            "diff --git a/t.py b/t.py\n--- a/t.py\n+++ b/t.py\n"
            "@@ -0,0 +1,1000 @@\n" + added
        )
        sm = _make_sm_fp(tmp_path, git_diff=fake_diff)
        rules = ["missing-docstring", "trailing-whitespace", "unused-import",
                 "line-too-long", "bare-except", "bad-indentation"]
        fps = ["fp-f-%d" % i for i in range(6)]
        for fp, rule in zip(fps, rules):
            sm._state.findings.append(_sf(fp, "P3: %s" % rule))
        _prime(sm, *fps)
        assert sm._fixpoint_reached() == _FixpointResult.CYCLE_RESTART

    def test_p3_distinct_per_diff_exceeds_triggers_restart(self, tmp_path):
        # 12 P3 findings across 3 files, 12 distinct rule types -> distinct_per_diff=12 > 10
        # Each file gets 4 rule types -> distinct_per_file=4 <= 5 (only diff threshold fires)
        # density = 12/1000 = 0.012 <= 0.15
        added = "\n".join("+line %d" % i for i in range(1000))
        fake_diff = (
            "diff --git a/t.py b/t.py\n--- a/t.py\n+++ b/t.py\n"
            "@@ -0,0 +1,1000 @@\n" + added
        )
        sm = _make_sm_fp(tmp_path, git_diff=fake_diff)
        files = ["a.py", "b.py", "c.py"]
        fps = ["fp-d-%d" % i for i in range(12)]
        rules = ["rule-%d" % i for i in range(12)]
        for i, (fp, rule) in enumerate(zip(fps, rules)):
            fname = files[i % 3]  # 4 findings per file, each a distinct rule type
            sf = StateFinding(
                id=fp, fingerprint=fp, source="L1",
                disposition=Disposition.CONFIRMED,
                file=fname, line_range=[1, 1],
                description="P3: %s" % rule,
            )
            sm._state.findings.append(sf)
        _prime(sm, *fps)
        assert sm._fixpoint_reached() == _FixpointResult.CYCLE_RESTART

    def test_p3_density_exceeds_triggers_restart(self, tmp_path):
        # 3 P3 findings, same rule type, 10 changed lines -> density=0.3 > 0.15
        # distinct_per_file=1 <= 5, distinct_per_diff=1 <= 10 -- only density fires
        added = "\n".join("+line %d" % i for i in range(10))
        fake_diff = (
            "diff --git a/t.py b/t.py\n--- a/t.py\n+++ b/t.py\n"
            "@@ -0,0 +1,10 @@\n" + added
        )
        sm = _make_sm_fp(tmp_path, git_diff=fake_diff)
        fps = ["fp-dens-%d" % i for i in range(3)]
        for fp in fps:
            sm._state.findings.append(_sf(fp, "P3: trailing whitespace"))
        _prime(sm, *fps)
        assert sm._fixpoint_reached() == _FixpointResult.CYCLE_RESTART

    def test_severity_tier_l0_defaults_p1(self):
        sf = _sf("x", "unprefixed finding", source="L0")
        assert _severity_tier(sf) == "P1"

    def test_severity_tier_prefix_p2(self):
        sf = _sf("x", "P2: missing docstring")
        assert _severity_tier(sf) == "P2"


class TestPersistentP2NoPass:
    """run()-level guard: a persistent unfixed P2 must never converge to PASS."""

    def test_recurring_p2_prevents_convergence(self, tmp_path):
        """A P2 that is never fixed recurs every round and must block PASS."""
        p2 = StateFinding(
            id="p2-sticky",
            fingerprint="p2-sticky",
            source="L0",
            disposition=Disposition.CONFIRMED,
            file="test.py",
            line_range=[1, 1],
            description="P2: never-fixed recurring issue",
        )

        class NoChangeAutoFixer(StubAutoFixer):
            def fix(self, finding, mode_hint):
                return FixOutcome.NO_CHANGE

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=NoChangeAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=lambda r, f: ([p2], []),
            max_total_rounds=6,
            max_fix_attempts=100,
            clean_round_threshold=3,
        )
        verdict = machine.run()
        assert verdict != Verdict.PASS, (
            "machine must not converge while a recurring P2 is present"
        )
        assert machine._state.consecutive_clean_rounds == 0, (
            "CYCLE_RESTART must reset consecutive_clean_rounds to 0"
        )
