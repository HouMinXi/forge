# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for StateMachine CI mode.

STATE-02/03: CI linear -- single round, FAIL on CONFIRMED, PASS otherwise.
"""

import json
from pathlib import Path


from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.machine import StateMachine
from code_forge.state import Mode, StateFinding, Verdict


def _make_finding(fp="fp-ci-1", disp=Disposition.CONFIRMED):
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


def _make_machine(tmp_path, l0_findings=None, l0_infra=None,
                  source_hash="abc123"):
    """Build a CI-mode StateMachine with injectable L0 results."""
    findings = l0_findings if l0_findings is not None else []
    infra = l0_infra if l0_infra is not None else []

    def mock_l0(registry, files):
        return (findings, infra)

    return StateMachine(
        mode=Mode.CI,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=_make_resolved(),
        source_hash=source_hash,
        baseline_spec_repr="empty",
        cwd=tmp_path,
        registry={},
        l0_runner=mock_l0,
    )


class TestCIZeroFindings:
    """(a) Zero CONFIRMED -> PASS + converged=True."""

    def test_pass_on_clean(self, tmp_path):
        machine = _make_machine(tmp_path, l0_findings=[])
        verdict = machine.run()
        assert verdict == Verdict.PASS

    def test_converged_true_on_pass(self, tmp_path):
        machine = _make_machine(tmp_path, l0_findings=[])
        machine.run()
        assert machine._state.converged is True


class TestCIConfirmedFail:
    """(b) Any CONFIRMED -> FAIL after 1 round + converged=False (R1 H5)."""

    def test_fail_on_confirmed(self, tmp_path):
        findings = [_make_finding()]
        machine = _make_machine(tmp_path, l0_findings=findings)
        verdict = machine.run()
        assert verdict == Verdict.FAIL

    def test_converged_false_on_fail(self, tmp_path):
        findings = [_make_finding()]
        machine = _make_machine(tmp_path, l0_findings=findings)
        machine.run()
        assert machine._state.converged is False

    def test_single_round_only(self, tmp_path):
        """CI executes exactly 1 round."""
        findings = [_make_finding()]
        machine = _make_machine(tmp_path, l0_findings=findings)
        machine.run()
        assert machine._state.round == 0
        assert len(machine._state.round_history) == 1


class TestCIUncertainPass:
    """(c) UNCERTAIN only -> PASS (CI non-blocking) + converged=True."""

    def test_uncertain_is_pass(self, tmp_path):
        findings = [_make_finding(
            fp="fp-unc",
            disp=Disposition.UNCERTAIN,
        )]
        machine = _make_machine(tmp_path, l0_findings=findings)
        verdict = machine.run()
        assert verdict == Verdict.PASS

    def test_converged_true_on_uncertain_pass(self, tmp_path):
        findings = [_make_finding(
            fp="fp-unc",
            disp=Disposition.UNCERTAIN,
        )]
        machine = _make_machine(tmp_path, l0_findings=findings)
        machine.run()
        assert machine._state.converged is True


class TestCIContinuation:
    """CI invocations continue the cycle count when the diff is unchanged.

    Three separate invocations each restart at round 0 by default and
    would overwrite receipt-c1p{1,2,3}; continuing the cycle count is
    what lets three clean rounds across three invocations satisfy the
    commit gate without renaming or hand-editing a receipt.
    """

    def _machine_for(self, tmp_path):
        return _make_machine(tmp_path, source_hash="sha")

    def test_no_receipts_starts_at_zero(self, tmp_path):
        m = self._machine_for(tmp_path)
        assert m._continuation_round_index() == 0

    def test_same_hash_continues_after_highest_cycle(self, tmp_path):
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (rd / "receipt-c1p1.json").write_text(
            json.dumps({"cycle": 1, "pass": 1, "diff_sha256": "sha"}))
        (rd / "receipt-c1p2.json").write_text(
            json.dumps({"cycle": 1, "pass": 2, "diff_sha256": "sha"}))
        m = self._machine_for(tmp_path)
        assert m._continuation_round_index() == 1

    def test_changed_hash_continues_past_the_highest_cycle_on_disk(self, tmp_path):
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (rd / "receipt-c1p1.json").write_text(
            json.dumps({"cycle": 1, "pass": 1, "diff_sha256": "other"}))
        m = self._machine_for(tmp_path)
        # receipt filenames carry no diff identity, so a changed diff must
        # not restart at cycle 1: that would overwrite the other diff's
        # receipt-c1p1.json still on disk. Continue after the highest
        # cycle written by any diff instead.
        assert m._continuation_round_index() == 1

    def test_same_diff_resumes_above_a_foreign_higher_cycle(self, tmp_path):
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        # This diff wrote cycle 1, another diff later wrote cycle 2.
        (rd / "receipt-c1p1.json").write_text(
            json.dumps({"cycle": 1, "pass": 1, "diff_sha256": "sha"}))
        (rd / "receipt-c2p1.json").write_text(
            json.dumps({"cycle": 2, "pass": 1, "diff_sha256": "other"}))
        m = self._machine_for(tmp_path)
        # Resuming this diff's own sequence (1) would write cycle 2 and
        # overwrite the foreign diff's receipt-c2p1.json. The next cycle
        # must sit above everything on disk.
        assert m._continuation_round_index() == 2

    def test_corrupt_receipt_is_skipped(self, tmp_path):
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (rd / "receipt-c1p1.json").write_text(
            json.dumps({"cycle": 1, "pass": 1, "diff_sha256": "sha"}))
        (rd / "receipt-c1p2.json").write_text("{ not json")
        m = self._machine_for(tmp_path)
        assert m._continuation_round_index() == 1

    def test_bool_cycle_is_skipped_not_counted_as_one(self, tmp_path):
        """A receipt carrying cycle: true must be skipped: bool is an
        int subclass, and counting it would shift the next round to 2
        as if cycle 1 had been reviewed."""
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (rd / "receipt-c1p1.json").write_text(
            json.dumps({"cycle": True, "pass": 1, "diff_sha256": "sha"}))
        m = self._machine_for(tmp_path)
        assert m._continuation_round_index() == 0

    def test_ci_run_does_not_overwrite_a_foreign_diff_receipt(self, tmp_path):
        """The helper is only as good as its caller. _run_ci wires
        _continuation_round_index into _execute_round; a full run must
        leave a foreign diff's receipt byte-for-byte intact and write
        this diff's receipts above it."""
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        foreign = {"cycle": 2, "pass": 1, "diff_sha256": "other"}
        (rd / "receipt-c2p1.json").write_text(json.dumps(foreign))
        machine = _make_machine(tmp_path, l0_findings=[], source_hash="sha")
        machine.run()
        assert json.loads((rd / "receipt-c2p1.json").read_text()) == foreign, (
            "a run must not overwrite a receipt a different diff wrote"
        )
        names = sorted(p.name for p in rd.glob("receipt-*.json"))
        for p in range(1, 4):
            assert "receipt-c3p%d.json" % p in names, (
                "this run's receipts must land above the foreign cycle 2"
            )


class TestCINeverHolds:
    """CI mode never enters HOLD."""

    def test_uncertain_does_not_hold(self, tmp_path):
        findings = [_make_finding(
            fp="fp-unc",
            disp=Disposition.UNCERTAIN,
        )]
        machine = _make_machine(tmp_path, l0_findings=findings)
        verdict = machine.run()
        # CI returns PASS not PENDING
        assert verdict == Verdict.PASS
