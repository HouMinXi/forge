# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for location-stable L1 fingerprints and disposition stickiness.

Validates that:
  - L1 fingerprints are determined by (file, line_bucket, pass_name), not
    by description text, so a model restating the same issue in different
    words across rounds produces the same fingerprint.
  - The convergence state machine treats a reworded finding at the same
    location as the same finding (not a new one that resets counters).
  - DISMISSED/STYLE dispositions stick across rounds when the same
    location is re-reported.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.machine import (
    Mode,
    StateMachine,
    _FixpointResult,
)
from code_forge.state import Verdict
from code_forge.reviewer_json import (
    _json_to_state_findings,
    _location_fingerprint,
    _LINE_BUCKET_SIZE,
)
from code_forge.state import StateFinding


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sm(tmp_path, git_diff=None):
    """Create a minimal StateMachine for unit tests."""
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


def _sf(fp, desc, disp=Disposition.CONFIRMED, source="L1",
        file="test.py", line=1):
    """Shorthand StateFinding constructor."""
    return StateFinding(
        id=fp, fingerprint=fp, source=source,
        disposition=disp,
        file=file, line_range=[line, line],
        description=desc,
    )


# ===========================================================================
# Fix 1: Location-stable fingerprints (reviewer_json.py)
# ===========================================================================

class TestLocationFingerprint:
    """_location_fingerprint produces stable hashes keyed by location."""

    def test_same_location_same_fp(self):
        """Same file+bucket+pass -> identical fingerprint."""
        fp1 = _location_fingerprint("chat.ts", 982, "qodo")
        fp2 = _location_fingerprint("chat.ts", 982, "qodo")
        assert fp1 == fp2

    def test_description_irrelevant(self):
        """Two calls at the same location produce the same fp regardless of
        what description the model wrote (description is not an input)."""
        # _location_fingerprint does not take description at all --
        # verify by constructing findings with different descriptions.
        data1 = {"findings": [
            {"file": "chat.ts", "line": 982, "severity": "P2",
             "description": "hasForcedConnection is dead code"},
        ], "code_excerpts": [{"file": "chat.ts", "start_line": 980,
                              "end_line": 985, "content": "x"}]}
        data2 = {"findings": [
            {"file": "chat.ts", "line": 982, "severity": "P2",
             "description": "The property hasForcedConnection is never read"},
        ], "code_excerpts": [{"file": "chat.ts", "start_line": 980,
                              "end_line": 985, "content": "x"}]}
        findings1 = _json_to_state_findings(data1, "qodo")
        findings2 = _json_to_state_findings(data2, "qodo")
        assert findings1[0].fingerprint == findings2[0].fingerprint

    def test_line_jitter_within_bucket(self):
        """Lines 979, 981, 982 (all in the same 5-line bucket) produce
        the same fingerprint.  This is the specific jitter pattern from
        the field report."""
        fps = {_location_fingerprint("chat.ts", line, "qodo")
               for line in [979, 981, 982, 983]}
        assert len(fps) == 1, "lines within one bucket must share fp"

    def test_different_bucket_different_fp(self):
        """Lines 970 and 982 are in different buckets -> different fp."""
        fp1 = _location_fingerprint("chat.ts", 970, "qodo")
        fp2 = _location_fingerprint("chat.ts", 982, "qodo")
        assert fp1 != fp2

    def test_different_file_different_fp(self):
        fp1 = _location_fingerprint("chat.ts", 982, "qodo")
        fp2 = _location_fingerprint("combo.ts", 982, "qodo")
        assert fp1 != fp2

    def test_different_pass_different_fp(self):
        """Different passes at the same location stay distinct."""
        fp1 = _location_fingerprint("chat.ts", 982, "qodo")
        fp2 = _location_fingerprint("chat.ts", 982, "adversarial")
        assert fp1 != fp2

    def test_bucket_size_is_ten(self):
        """Document the bucket size for regression."""
        assert _LINE_BUCKET_SIZE == 10

    def test_line_zero_bucket(self):
        """Line 0 normalises to bucket 0."""
        fp = _location_fingerprint("f.py", 0, "expert")
        expected_src = "f.py:0:expert"
        expected = hashlib.sha256(expected_src.encode()).hexdigest()[:16]
        assert fp == expected


# ===========================================================================
# Bucket collision: two different defects at nearby lines, same pass
# ===========================================================================

class TestBucketCollision:
    """Pin the behaviour when two genuinely different defects at nearby
    lines from the same pass collide into one fingerprint.

    This is an explicit trade-off: location stability for convergence
    is worth losing the rare same-bucket duplicate.  The dedup layer
    keeps whichever finding it encounters first (insertion order, not
    severity).  These tests document and pin that decision.
    """

    def test_same_bucket_same_pass_produces_one_fp(self):
        """Two findings at lines 979 and 982, same file, same pass,
        collapse to one fingerprint."""
        fp1 = _location_fingerprint("chat.ts", 979, "qodo")
        fp2 = _location_fingerprint("chat.ts", 982, "qodo")
        assert fp1 == fp2, (
            "same-bucket collision is the intended trade-off"
        )

    def test_first_in_wins_dedup(self):
        """When two genuine defects share a fingerprint, the REAL L1 fold
        in factories.py keeps the first one encountered.  This drives
        build_l1_provider end-to-end (llm_invoke stubbed) with two
        same-bucket findings from the same pass -- the exact collision
        the location-stable fingerprint creates -- and pins that the
        survivor is the first in insertion order, not the higher
        severity.  Breaking factories.py's dedup must make this red.
        """
        import json
        from unittest.mock import patch as _patch

        from code_forge.factories import build_l1_provider
        from code_forge.llm_invoke import LLMResult, Usage as LLMUsage

        resolved = ResolvedReview(
            source_files=[Path("chat.ts")],
            baseline_content=None,
            git_diff=(
                "diff --git a/chat.ts b/chat.ts\n"
                "--- a/chat.ts\n"
                "+++ b/chat.ts\n"
                "@@ -975,3 +975,5 @@\n"
                " export class X {}\n"
                "+  private dead(): void {}\n"
            ),
            mode_hint="git",
        )
        payload = json.dumps({
            "findings": [
                {"file": "chat.ts", "line": 979, "severity": "P3",
                 "description": "minor style issue"},
                {"file": "chat.ts", "line": 982, "severity": "P1",
                 "description": "critical logic error"},
            ],
            "code_excerpts": [{"file": "chat.ts", "start_line": 975,
                               "end_line": 985, "content": "x"}],
        })
        with _patch("code_forge.llm_invoke.llm_invoke") as mock_invoke:
            mock_invoke.return_value = LLMResult(
                content=payload,
                usage=LLMUsage(input_tokens=10, output_tokens=10),
                duration_s=0.1,
            )
            provider = build_l1_provider("real", resolved)
            findings, excerpts, usage, duration = provider()

        # build_l1_provider runs three passes (qodo/expert/adversarial);
        # each pass's two same-bucket findings fold to ONE survivor.
        assert mock_invoke.call_count >= 1
        # Every survivor is the first-in-wins member of its pass pair.
        l1 = [f for f in findings if f.source == "L1"]
        assert len(l1) == 3, (
            "one survivor per pass, got %d: %s"
            % (len(l1), [f.description for f in l1])
        )
        for f in l1:
            assert "minor style" in f.description, (
                "first-in-wins by insertion order, not severity: %s"
                % f.description
            )
            assert "critical logic error" not in f.description

    def test_different_passes_same_bucket_survive(self):
        """Even if two findings are at the same bucket, different passes
        produce different fps -- no collision."""
        data1 = {"findings": [
            {"file": "chat.ts", "line": 979, "severity": "P2",
             "description": "null deref"},
        ], "code_excerpts": []}
        data2 = {"findings": [
            {"file": "chat.ts", "line": 982, "severity": "P1",
             "description": "buffer overflow"},
        ], "code_excerpts": []}
        f1 = _json_to_state_findings(data1, "qodo")
        f2 = _json_to_state_findings(data2, "expert")
        assert f1[0].fingerprint != f2[0].fingerprint, (
            "different passes at same bucket must not collide"
        )


# ===========================================================================
# Fix 2: Clause (a) does not reset on reworded findings
# ===========================================================================

class TestFixpointRewordedFinding:
    """A reworded finding at the same location no longer triggers RESET."""

    def test_same_fp_not_new(self, tmp_path):
        """When a finding has the same fingerprint as a prior round's
        CONFIRMED finding, clause (a) does not count it as NEW."""
        sm = _make_sm(tmp_path)
        fp = _location_fingerprint("chat.ts", 982, "qodo")
        # Current round: finding with this fp, CONFIRMED
        sm._state.findings = [_sf(fp, "P2: reworded version", line=982)]
        # Prior round: same fp was CONFIRMED
        sm._state.round_history = [
            {"dispositions": {fp: "CONFIRMED"}},
            {},
        ]
        result = sm._fixpoint_reached()
        # It is a recurring CONFIRMED, not a NEW one -> not RESET by clause (a)
        # It hits severity tiering (P2) -> CYCLE_RESTART
        assert result == _FixpointResult.CYCLE_RESTART

    def test_genuinely_new_location_still_resets(self, tmp_path):
        """A finding at a genuinely new location still triggers RESET."""
        sm = _make_sm(tmp_path)
        fp_old = _location_fingerprint("chat.ts", 982, "qodo")
        fp_new = _location_fingerprint("chat.ts", 500, "qodo")
        sm._state.findings = [_sf(fp_new, "P2: new issue", line=500)]
        sm._state.round_history = [
            {"dispositions": {fp_old: "CONFIRMED"}},
            {},
        ]
        # fp_new is not in prior_disps -> NEW -> RESET
        result = sm._fixpoint_reached()
        assert result == _FixpointResult.RESET


# ===========================================================================
# Fix 3: Disposition stickiness (DISMISSED carries forward)
# ===========================================================================

class TestDismissedStickiness:
    """DISMISSED/STYLE dispositions stick when the same fp reappears."""

    def test_dismissed_sticks(self, tmp_path):
        """A finding that was DISMISSED in the prior round retains
        DISMISSED when re-reported with the same fingerprint."""
        sm = _make_sm(tmp_path)
        fp = _location_fingerprint("chat.ts", 982, "qodo")
        # Current round: re-reported as UNCERTAIN (default from L1)
        findings = [_sf(fp, "[qodo] dead code", disp=Disposition.UNCERTAIN)]
        # Prior round: same fp was DISMISSED
        sm._state.round_history = [
            {"dispositions": {fp: "DISMISSED"}},
        ]
        result = sm._apply_dismissed_stickiness(findings)
        assert result[0].disposition == Disposition.DISMISSED

    def test_style_sticks(self, tmp_path):
        """STYLE disposition also sticks."""
        sm = _make_sm(tmp_path)
        fp = _location_fingerprint("chat.ts", 100, "expert")
        findings = [_sf(fp, "naming nit", disp=Disposition.UNCERTAIN)]
        sm._state.round_history = [
            {"dispositions": {fp: "STYLE"}},
        ]
        result = sm._apply_dismissed_stickiness(findings)
        assert result[0].disposition == Disposition.STYLE

    def test_confirmed_does_not_stick(self, tmp_path):
        """CONFIRMED in prior round does NOT override current disposition
        (not a terminal disposition)."""
        sm = _make_sm(tmp_path)
        fp = _location_fingerprint("f.py", 10, "qodo")
        findings = [_sf(fp, "issue", disp=Disposition.UNCERTAIN)]
        sm._state.round_history = [
            {"dispositions": {fp: "CONFIRMED"}},
        ]
        result = sm._apply_dismissed_stickiness(findings)
        assert result[0].disposition == Disposition.UNCERTAIN

    def test_fixed_does_not_stick(self, tmp_path):
        """FIXED in prior round does NOT stick -- re-reported after fix
        is a genuine regression that should not be suppressed."""
        sm = _make_sm(tmp_path)
        fp = _location_fingerprint("f.py", 10, "qodo")
        findings = [_sf(fp, "issue", disp=Disposition.CONFIRMED)]
        sm._state.round_history = [
            {"dispositions": {fp: "FIXED"}},
        ]
        result = sm._apply_dismissed_stickiness(findings)
        assert result[0].disposition == Disposition.CONFIRMED

    def test_no_history_is_noop(self, tmp_path):
        """No prior round history -> no stickiness applied."""
        sm = _make_sm(tmp_path)
        findings = [_sf("fp1", "issue", disp=Disposition.UNCERTAIN)]
        sm._state.round_history = []
        result = sm._apply_dismissed_stickiness(findings)
        assert result[0].disposition == Disposition.UNCERTAIN


# ===========================================================================
# Integration: full non-convergence scenario from field report
# ===========================================================================

class TestNonConvergenceScenario:
    """End-to-end scenario: 7 reworded findings at the same location
    that previously caused non-convergence now produce the same fp."""

    def test_seven_rewordings_one_fingerprint(self):
        """The field report's hasForcedConnection scenario:
        7 different descriptions at lines 979/981/982 should all produce
        the same fingerprint."""
        descriptions = [
            "hasForcedConnection is dead code that should be removed",
            "The property hasForcedConnection appears to be unused",
            "Dead code: hasForcedConnection is never read by any caller",
            "Unused property hasForcedConnection can be safely deleted",
            "hasForcedConnection: this field is vestigial dead code",
            "Remove hasForcedConnection -- it has no readers",
            "The hasForcedConnection property is unreachable dead code",
        ]
        lines = [982, 982, 982, 979, 981, 979, 979]

        fps = set()
        for desc, line in zip(descriptions, lines):
            data = {"findings": [
                {"file": "chat.ts", "line": line, "severity": "P2",
                 "description": desc},
            ], "code_excerpts": [{"file": "chat.ts", "start_line": 975,
                                  "end_line": 985, "content": "x"}]}
            findings = _json_to_state_findings(data, "qodo")
            fps.add(findings[0].fingerprint)

        assert len(fps) == 1, (
            "all 7 rewordings at lines 979-982 must share one fingerprint, "
            "got %d: %s" % (len(fps), fps)
        )

    def test_dismissed_finding_does_not_block_convergence(self, tmp_path):
        """After a finding is DISMISSED, re-reporting it in subsequent
        rounds should not prevent consecutive_clean_rounds from reaching
        the threshold."""
        sm = _make_sm(tmp_path)
        fp = _location_fingerprint("chat.ts", 982, "qodo")

        # Simulate: prior round (most recent = last entry) had this fp
        # as DISMISSED. Clause (a) reads prior_disps from [-2], so we
        # also place it there for the newness check.
        sm._state.round_history = [
            {"dispositions": {fp: "DISMISSED"}},
            {"dispositions": {fp: "DISMISSED"}},
        ]

        # Current round: finding re-reported, comes in as UNCERTAIN
        finding = _sf(fp, "[qodo] dead code", disp=Disposition.UNCERTAIN)
        findings = [finding]

        # Apply dismissed stickiness BEFORE fixpoint check
        findings = sm._apply_dismissed_stickiness(findings)
        sm._state.findings = findings

        # The finding should now be DISMISSED, not UNCERTAIN
        assert findings[0].disposition == Disposition.DISMISSED

        # _fixpoint_reached should not RESET
        # DISMISSED findings are not CONFIRMED and not UNCERTAIN,
        # so they don't trigger clause (a) or (d)
        result = sm._fixpoint_reached()
        assert result == _FixpointResult.CLEAN


# ===========================================================================
# GAP 1: dismissal survives a gap round (finding absent for one round)
# ===========================================================================

class TestGapRoundDismissalStickiness:
    """A model may not restate every finding every round.  If a finding
    is DISMISSED in round 1, absent in round 2, and restated in round 3,
    the DISMISSED disposition must carry forward across the gap.

    This is the specific scenario from the field report:
      round 1 DISMISSED, ABSENT round 2, restated round 3 -> DISMISSED
    (was: CONFIRMED, because only round_history[-1] was checked).
    """

    def test_dismissed_survives_gap_round(self, tmp_path):
        """DISMISSED in round 1, absent in round 2, restated in round 3
        -> still DISMISSED, not reverted to UNCERTAIN."""
        sm = _make_sm(tmp_path)
        fp = _location_fingerprint("chat.ts", 982, "qodo")
        # Round history: round 1 had DISMISSED, round 2 did NOT mention
        # this fp at all (gap round).
        sm._state.round_history = [
            {"dispositions": {fp: "DISMISSED"}},  # round 1
            {"dispositions": {}},                  # round 2: absent
        ]
        # Round 3: model restates the finding as UNCERTAIN
        findings = [_sf(fp, "[qodo] dead code", disp=Disposition.UNCERTAIN)]
        result = sm._apply_dismissed_stickiness(findings)
        assert result[0].disposition == Disposition.DISMISSED, (
            "DISMISSED must survive a gap round, got %s"
            % result[0].disposition
        )

    def test_dismissed_survives_multiple_gap_rounds(self, tmp_path):
        """DISMISSED in round 1, absent in rounds 2-4, restated
        in round 5 -> still DISMISSED."""
        sm = _make_sm(tmp_path)
        fp = _location_fingerprint("combo.ts", 128, "expert")
        sm._state.round_history = [
            {"dispositions": {fp: "DISMISSED"}},   # round 1
            {"dispositions": {}},                   # round 2: absent
            {"dispositions": {}},                   # round 3: absent
            {"dispositions": {}},                   # round 4: absent
        ]
        findings = [_sf(fp, "unused import", disp=Disposition.UNCERTAIN)]
        result = sm._apply_dismissed_stickiness(findings)
        assert result[0].disposition == Disposition.DISMISSED

    def test_later_reconfirmation_overrides_dismissed(self, tmp_path):
        """If a later round explicitly set CONFIRMED (non-terminal),
        that override is respected -- DISMISSED does not leak through."""
        sm = _make_sm(tmp_path)
        fp = _location_fingerprint("chat.ts", 982, "qodo")
        sm._state.round_history = [
            {"dispositions": {fp: "DISMISSED"}},    # round 1: dismissed
            {"dispositions": {fp: "CONFIRMED"}},    # round 2: re-confirmed
        ]
        # Round 3: re-reported as UNCERTAIN
        findings = [_sf(fp, "dead code", disp=Disposition.UNCERTAIN)]
        result = sm._apply_dismissed_stickiness(findings)
        # CONFIRMED is not a sticky terminal disposition, so the
        # current disposition (UNCERTAIN) should remain.
        assert result[0].disposition == Disposition.UNCERTAIN, (
            "explicit re-confirmation must override earlier DISMISSED"
        )

    def test_gap_round_dismissed_does_not_block_convergence(self, tmp_path):
        """Integration: after a gap round, the DISMISSED stickiness
        prevents the fixpoint check from resetting the counter."""
        sm = _make_sm(tmp_path)
        fp = _location_fingerprint("chat.ts", 982, "qodo")
        sm._state.round_history = [
            {"dispositions": {fp: "DISMISSED"}},   # round 1
            {"dispositions": {}},                   # round 2: gap
            {"dispositions": {fp: "DISMISSED"}},   # round 3 (after stickiness)
        ]
        # Apply stickiness to the current finding
        finding = _sf(fp, "[qodo] dead code", disp=Disposition.UNCERTAIN)
        findings = sm._apply_dismissed_stickiness([finding])
        sm._state.findings = findings
        assert findings[0].disposition == Disposition.DISMISSED

        # fixpoint: DISMISSED is not CONFIRMED or UNCERTAIN -> CLEAN
        result = sm._fixpoint_reached()
        assert result == _FixpointResult.CLEAN


# ===========================================================================
# GAP 2: HOLD path writes dismissed findings to ledger
# ===========================================================================

class TestHoldLedgerPersistence:
    """When LOCAL enters HOLD, DISMISSED findings must be written to the
    ledger so they survive and suppress re-reported findings in future
    rounds.  Previously, _write_ledger_rows only ran on the PASS path
    via _finalize_local_terminal."""

    def test_hold_path_writes_dismissed_to_ledger(self, tmp_path):
        """The HOLD exit path in _run_local must write DISMISSED findings
        as DISPROVED rows to the ledger.

        This is an integration test: we drive StateMachine.run() in LOCAL
        mode where L1 produces findings that enter HOLD (UNCERTAIN present,
        no unfixed CONFIRMED), and verify the ledger gets a DISPROVED row for any
        DISMISSED finding.  Without the GAP 2 fix (_write_ledger_rows() on the
        HOLD path in machine.py:712), this test FAILS because the ledger remains empty.
        """
        import subprocess
        from code_forge.ledger import iter_rows, TerminalState
        from code_forge.llm_invoke import Usage

        # Initialise git so resolve_ledger_root works
        subprocess.run(["git", "init"], cwd=str(tmp_path),
                       capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                       cwd=str(tmp_path), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                       cwd=str(tmp_path), capture_output=True, check=True)
        (tmp_path / "test.py").write_text("pass\n")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path),
                       capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"],
                       cwd=str(tmp_path), capture_output=True, check=True)

        resolved = ResolvedReview(
            source_files=[Path("test.py")],
            baseline_content=None,
            git_diff=(
                "diff --git a/test.py b/test.py\n"
                "--- a/test.py\n"
                "+++ b/test.py\n"
                "@@ -1,1 +1,2 @@\n"
                " pass\n"
                "+x = 1\n"
            ),
            mode_hint="git",
            base_sha="aaa",
            head_sha="bbb",
        )

        fp_dismissed = _location_fingerprint("test.py", 2, "qodo")
        fp_uncertain = _location_fingerprint("test.py", 50, "expert")

        candidates = [
            _sf(fp_dismissed, "dead code", disp=Disposition.CONFIRMED,
                file="test.py", line=2),
            _sf(fp_uncertain, "magic number", disp=Disposition.CONFIRMED,
                file="test.py", line=50),
        ]

        class _CustomFalsifier(StubFalsifier):
            def falsify(self, finding):
                if finding.fingerprint == fp_dismissed:
                    return Disposition.DISMISSED
                return Disposition.UNCERTAIN

        sm = StateMachine(
            resolved_review=resolved,
            falsifier=_CustomFalsifier(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            mode=Mode.LOCAL,
            autofixer=StubAutoFixer(),
            revert_fn=lambda f: None,
            l0_runner=lambda r, f: ([], []),
            l1_provider=lambda: (candidates, [], Usage(), 0.0),
        )

        verdict = sm.run()
        assert verdict == Verdict.PENDING
        assert sm._state.hold_reason is not None

        # Verify DISMISSED -> DISPROVED row written
        ledger_rows = list(iter_rows(tmp_path))
        disproved = [r for r in ledger_rows
                     if r.terminal_state == TerminalState.DISPROVED]
        assert len(disproved) == 1, (
            "expected 1 DISPROVED row in ledger from HOLD path, got %d"
            % len(disproved)
        )
        assert disproved[0].fingerprint == fp_dismissed

    def test_hold_ledger_suppresses_next_round(self, tmp_path):
        """After HOLD writes a DISPROVED row, known_terminal_fingerprints
        should return that fingerprint for suppression."""
        from code_forge.ledger import (
            known_terminal_fingerprints, append_row,
            TerminalState, LedgerRow,
        )
        import subprocess
        from datetime import datetime, timezone

        subprocess.run(["git", "init"], cwd=str(tmp_path),
                       capture_output=True, check=True)
        fp = _location_fingerprint("test.py", 2, "qodo")
        row = LedgerRow(
            fingerprint=fp,
            repo_root=str(tmp_path),
            base_sha="aaa",
            head_sha="bbb",
            file="test.py",
            line=2,
            axis_claim="defect",
            pass_provenance="L1",
            terminal_state=TerminalState.DISPROVED,
            evidence_class="falsifier_rejected",
            ts=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        append_row(tmp_path, row)
        known = known_terminal_fingerprints(tmp_path)
        assert fp in known, (
            "DISPROVED fingerprint must appear in known_terminal_fingerprints"
        )
