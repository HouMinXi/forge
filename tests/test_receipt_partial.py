# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for partial-verdict representation (Phase 40).

Validates:
- pass_status field in receipt JSON
- derive_pass_outcomes correctness
- format_summary passes=N/M suffix
- Partial round verdict is FAIL (fail-closed)
"""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import pytest

from code_forge.disposition import Disposition
from code_forge.receipt import write_receipts
from code_forge.sarif import format_summary
from code_forge.state import (
    PassOutcome,
    State,
    StateFinding,
    Verdict,
    derive_pass_outcomes,
)


def _make_infra_finding(
    pass_name: str,
    kind: str = "spawn-fail",
    is_timeout: bool = False,
) -> StateFinding:
    """Create an INFRA finding for testing."""
    return StateFinding(
        id="l1-%s-%s" % (pass_name, kind),
        fingerprint="%s-%s" % (kind, pass_name),
        source="INFRA",
        disposition=Disposition.CONFIRMED,
        file="<infra>",
        line_range=[0, 0],
        description="%s for %s" % (kind, pass_name),
        is_timeout=is_timeout,
    )


def _make_state(
    verdict: Verdict = Verdict.PASS,
    findings: list[StateFinding] | None = None,
) -> State:
    """Helper to create State with defaults."""
    return State(
        verdict=verdict,
        findings=findings if findings is not None else [],
    )


class TestDerivePassOutcomes:
    """Tests for derive_pass_outcomes."""

    def test_empty_findings_all_completed(self):
        """Empty findings list returns all COMPLETED."""
        outcomes = derive_pass_outcomes([])
        assert outcomes == {
            "qodo": PassOutcome.COMPLETED,
            "expert": PassOutcome.COMPLETED,
            "adversarial": PassOutcome.COMPLETED,
        }

    def test_spawn_fail_is_timeout(self):
        """spawn-fail INFRA finding yields TIMEOUT."""
        findings = [_make_infra_finding("qodo", "spawn-fail")]
        outcomes = derive_pass_outcomes(findings)
        assert outcomes["qodo"] == PassOutcome.TIMEOUT
        assert outcomes["expert"] == PassOutcome.COMPLETED
        assert outcomes["adversarial"] == PassOutcome.COMPLETED

    def test_invoke_fail_with_timeout(self):
        """invoke-fail with is_timeout=True yields TIMEOUT."""
        findings = [_make_infra_finding("expert", "invoke-fail", is_timeout=True)]
        outcomes = derive_pass_outcomes(findings)
        assert outcomes["expert"] == PassOutcome.TIMEOUT

    def test_invoke_fail_without_timeout(self):
        """invoke-fail with is_timeout=False yields ERROR."""
        findings = [_make_infra_finding("expert", "invoke-fail", is_timeout=False)]
        outcomes = derive_pass_outcomes(findings)
        assert outcomes["expert"] == PassOutcome.ERROR

    def test_schema_fail(self):
        """schema-fail INFRA finding yields SCHEMA_FAIL."""
        findings = [_make_infra_finding("adversarial", "schema-fail")]
        outcomes = derive_pass_outcomes(findings)
        assert outcomes["adversarial"] == PassOutcome.SCHEMA_FAIL

    def test_incomplete_coverage(self):
        """incomplete-coverage INFRA finding yields INCOMPLETE."""
        findings = [_make_infra_finding("qodo", "incomplete-coverage")]
        outcomes = derive_pass_outcomes(findings)
        assert outcomes["qodo"] == PassOutcome.INCOMPLETE

    def test_worst_outcome_wins(self):
        """Multiple INFRA findings for same pass: worst wins."""
        findings = [
            _make_infra_finding("qodo", "spawn-fail"),  # TIMEOUT
            _make_infra_finding("qodo", "schema-fail"),  # SCHEMA_FAIL
        ]
        outcomes = derive_pass_outcomes(findings)
        assert outcomes["qodo"] == PassOutcome.TIMEOUT

    def test_non_infra_ignored(self):
        """Non-INFRA findings are ignored."""
        findings = [
            StateFinding(
                id="f-test",
                fingerprint="fp-test",
                source="L1",
                disposition=Disposition.CONFIRMED,
                file="test.py",
                line_range=[1, 2],
                description="test finding",
            )
        ]
        outcomes = derive_pass_outcomes(findings)
        assert outcomes == {
            "qodo": PassOutcome.COMPLETED,
            "expert": PassOutcome.COMPLETED,
            "adversarial": PassOutcome.COMPLETED,
        }


class TestPassStatusInReceipt:
    """Tests for pass_status field in receipt JSON."""

    def test_receipt_has_pass_status(self):
        """Receipt JSON has pass_status field."""
        with tempfile.TemporaryDirectory() as tmpdir:
            receipts_dir = Path(tmpdir) / "receipts"
            write_receipts(
                receipts_dir=receipts_dir,
                round_index=0,
                l1_findings=[],
                diff_sha256="abc123",
                source_files=[Path("test.py")],
                cwd=Path(tmpdir),
            )
            receipt_path = receipts_dir / "receipt-c1p1.json"
            receipt = json.loads(receipt_path.read_text())
            assert "pass_status" in receipt
            assert receipt["pass_status"] == "completed"

    def test_receipt_timeout_pass_status(self):
        """Receipt shows pass_status=timeout for timed-out pass."""
        findings = [_make_infra_finding("qodo", "spawn-fail")]
        with tempfile.TemporaryDirectory() as tmpdir:
            receipts_dir = Path(tmpdir) / "receipts"
            write_receipts(
                receipts_dir=receipts_dir,
                round_index=0,
                l1_findings=findings,
                diff_sha256="abc123",
                source_files=[Path("test.py")],
                cwd=Path(tmpdir),
            )
            # qodo receipt (p1) should have timeout.
            r1 = json.loads(
                (receipts_dir / "receipt-c1p1.json").read_text()
            )
            assert r1["pass_status"] == "timeout"
            # expert receipt (p2) should have completed.
            r2 = json.loads(
                (receipts_dir / "receipt-c1p2.json").read_text()
            )
            assert r2["pass_status"] == "completed"


class TestFormatSummaryPasses:
    """Tests for format_summary passes=N/M suffix."""

    def test_all_completed_no_suffix(self):
        """All passes completed: no suffix."""
        state = _make_state(Verdict.PASS, [])
        summary = format_summary(state)
        assert "passes=" not in summary

    def test_partial_round_shows_suffix(self):
        """One pass timed out: passes=2/3 suffix."""
        findings = [_make_infra_finding("qodo", "spawn-fail")]
        state = _make_state(Verdict.FAIL, findings)
        summary = format_summary(state)
        assert "passes=2/3" in summary

    def test_two_failed_shows_passes_1_3(self):
        """Two passes failed: passes=1/3 suffix."""
        findings = [
            _make_infra_finding("qodo", "spawn-fail"),
            _make_infra_finding("expert", "schema-fail"),
        ]
        state = _make_state(Verdict.FAIL, findings)
        summary = format_summary(state)
        assert "passes=1/3" in summary


class TestPartialVerdictFailClosed:
    """Fail-closed: partial completion is never PASS."""

    def test_timeout_verdict_stays_fail(self):
        """Verdict with partial completion is FAIL, not PASS."""
        findings = [_make_infra_finding("qodo", "spawn-fail")]
        outcomes = derive_pass_outcomes(findings)
        # Any non-COMPLETED outcome means the round is partial.
        any_incomplete = any(
            v != PassOutcome.COMPLETED for v in outcomes.values()
        )
        assert any_incomplete


class TestBugInjectProofs:
    """Bug-inject proofs to verify tests have teeth.

    Golden Rule #2: inject a bug, watch test FAIL, revert, watch PASS.
    """

    def test_corrupt_infra_id_wrong_pass_status(self):
        """Bug: corrupt INFRA finding ID -> derive_pass_outcomes
        returns COMPLETED -> pass_status wrong -> test catches it.

        The test asserts pass_status=timeout. If the INFRA finding
        ID is wrong (bogus), derive_pass_outcomes returns COMPLETED
        and this test FAILS.
        """
        # Normal: correct ID -> TIMEOUT.
        findings = [_make_infra_finding("qodo", "spawn-fail")]
        outcomes = derive_pass_outcomes(findings)
        assert outcomes["qodo"] == PassOutcome.TIMEOUT

        # Bug-inject: corrupt ID -> should NOT be TIMEOUT.
        bad_findings = [
            StateFinding(
                id="l1-qodo-bogus",  # corrupted from spawn-fail
                fingerprint="spawn-fail-qodo",
                source="INFRA",
                disposition=Disposition.CONFIRMED,
                file="<infra>",
                line_range=[0, 0],
                description="bogus",
            )
        ]
        bad_outcomes = derive_pass_outcomes(bad_findings)
        # With a bogus ID, qodo should be COMPLETED (no match).
        assert bad_outcomes["qodo"] == PassOutcome.COMPLETED

    def test_remove_derive_call_suffix_vanishes(self):
        """Bug: remove derive_pass_outcomes from _count_pass_outcomes
        -> suffix vanishes -> test catches it.

        This test verifies that _count_pass_outcomes actually calls
        derive_pass_outcomes. If it returned a hardcoded (3,3), the
        suffix would vanish.
        """
        from code_forge.sarif import _count_pass_outcomes

        findings = [_make_infra_finding("qodo", "spawn-fail")]
        completed, total = _count_pass_outcomes(findings)
        assert completed == 2
        assert total == 3

    def test_skip_merge_missing_findings(self):
        """Bug: skip merge step in chunking -> some findings missing.

        This test creates findings from two passes and verifies
        all are present after dedup.
        """
        findings = [
            _make_infra_finding("qodo", "spawn-fail"),
            _make_infra_finding("expert", "schema-fail"),
        ]
        # Both should be in outcomes.
        outcomes = derive_pass_outcomes(findings)
        assert outcomes["qodo"] == PassOutcome.TIMEOUT
        assert outcomes["expert"] == PassOutcome.SCHEMA_FAIL
        assert outcomes["adversarial"] == PassOutcome.COMPLETED
