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

import pytest

from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.machine import (
    Mode,
    StateMachine,
    _FixpointResult,
)
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
        """When two findings share a fingerprint, factories dedup keeps
        the first one encountered.  This test pins the insertion-order
        behaviour rather than severity-based selection."""
        data = {"findings": [
            {"file": "chat.ts", "line": 979, "severity": "P3",
             "description": "minor style issue"},
            {"file": "chat.ts", "line": 982, "severity": "P1",
             "description": "critical logic error"},
        ], "code_excerpts": []}
        findings = _json_to_state_findings(data, "qodo")
        # Both produce the same fp (same bucket)
        assert findings[0].fingerprint == findings[1].fingerprint
        # Dedup filter (as used in factories.py) keeps the first
        seen = set()
        kept = []
        for f in findings:
            if f.fingerprint not in seen:
                seen.add(f.fingerprint)
                kept.append(f)
        assert len(kept) == 1
        assert "minor style" in kept[0].description, (
            "first-in-wins: insertion order, not severity"
        )

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
