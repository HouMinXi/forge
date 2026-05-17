# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for forge.state 02-02 additions.

SC-14: baseline_spec_repr, round_history, infra_errors round-trip +
backward compat with pre-02-02 state.json.
"""

import json


from forge.disposition import Disposition
from forge.state import (
    Mode,
    State,
    StateFinding,
    Verdict,
    load_state,
    save_state,
)


def _make_finding(fid="f-001", fp="fp-001"):
    return StateFinding(
        id=fid,
        fingerprint=fp,
        source="L0",
        disposition=Disposition.CONFIRMED,
        file="test.py",
        line_range=[1, 1],
        description="test finding",
    )


class TestBaselineSpecReprRoundTrip:
    """(a) baseline_spec_repr survives round-trip via save/load."""

    def test_none_round_trips(self, tmp_path):
        state = State()
        assert state.baseline_spec_repr is None
        p = tmp_path / "state.json"
        save_state(state, p)
        loaded = load_state(p)
        assert loaded.baseline_spec_repr is None

    def test_string_round_trips(self, tmp_path):
        state = State(baseline_spec_repr="git:HEAD")
        p = tmp_path / "state.json"
        save_state(state, p)
        loaded = load_state(p)
        assert loaded.baseline_spec_repr == "git:HEAD"

    def test_snapshot_repr_round_trips(self, tmp_path):
        state = State(
            baseline_spec_repr="snapshot:.forge/snapshots/abc123.json"
        )
        p = tmp_path / "state.json"
        save_state(state, p)
        loaded = load_state(p)
        assert loaded.baseline_spec_repr == (
            "snapshot:.forge/snapshots/abc123.json"
        )


class TestRoundHistoryGrowth:
    """(b) round_history list grows by 1 per round."""

    def test_empty_default(self, tmp_path):
        state = State()
        p = tmp_path / "state.json"
        save_state(state, p)
        loaded = load_state(p)
        assert loaded.round_history == []

    def test_grows_on_append(self, tmp_path):
        state = State()
        state.round_history.append({
            "round": 0,
            "l0_fingerprints": ["fp-1"],
            "l1_fingerprints": [],
            "dispositions": {"fp-1": "CONFIRMED"},
            "fixed_fingerprints": [],
        })
        p = tmp_path / "state.json"
        save_state(state, p)
        loaded = load_state(p)
        assert len(loaded.round_history) == 1
        assert loaded.round_history[0]["round"] == 0

    def test_multiple_rounds_preserved(self, tmp_path):
        state = State()
        for i in range(5):
            state.round_history.append({
                "round": i,
                "l0_fingerprints": [],
                "l1_fingerprints": [],
                "dispositions": {},
                "fixed_fingerprints": [],
            })
        p = tmp_path / "state.json"
        save_state(state, p)
        loaded = load_state(p)
        assert len(loaded.round_history) == 5
        assert loaded.round_history[4]["round"] == 4


class TestInfraErrorsOrder:
    """(c) infra_errors round-trip preserves order."""

    def test_empty_default(self, tmp_path):
        state = State()
        p = tmp_path / "state.json"
        save_state(state, p)
        loaded = load_state(p)
        assert loaded.infra_errors == []

    def test_order_preserved(self, tmp_path):
        state = State()
        state.infra_errors = [
            "L0 ToolError tool=ruff msg=not found",
            "falsify exception on fp-1: timeout",
            "L0 runner failed: OSError",
        ]
        p = tmp_path / "state.json"
        save_state(state, p)
        loaded = load_state(p)
        assert loaded.infra_errors == [
            "L0 ToolError tool=ruff msg=not found",
            "falsify exception on fp-1: timeout",
            "L0 runner failed: OSError",
        ]


class TestBackwardCompat:
    """(d) Pre-02-02 state.json loads with new fields defaulted.

    R1 B1: state.json written by 02-01 (no baseline_spec_repr,
    round_history, infra_errors keys) must load cleanly via
    data.get() defaults.
    """

    def test_pre_0202_loads_cleanly(self, tmp_path):
        # Write a state.json without 02-02 fields (simulating 02-01)
        finding = _make_finding()
        pre_0202 = {
            "schema_version": 1,
            "disposition_protocol_version": 1,
            "round": 0,
            "mode": "LOCAL",
            "source_hash": None,
            "findings": [
                {
                    "id": finding.id,
                    "fingerprint": finding.fingerprint,
                    "source": "L0",
                    "disposition": "CONFIRMED",
                    "file": "test.py",
                    "line_range": [1, 1],
                    "description": "test finding",
                    "error": None,
                    "anchor": None,
                    "evidence_files": None,
                }
            ],
            "dispositions": {"f-001": "CONFIRMED"},
            "fix_attempts": {},
            "verdict": "PENDING",
            "converged": False,
        }
        p = tmp_path / "state.json"
        p.write_text(json.dumps(pre_0202))
        loaded = load_state(p)

        # 02-01 fields work
        assert loaded.schema_version == 1
        assert loaded.round == 0
        assert loaded.mode == Mode.LOCAL
        assert len(loaded.findings) == 1
        assert loaded.findings[0].id == "f-001"
        assert loaded.verdict == Verdict.PENDING

        # 02-02 fields default
        assert loaded.baseline_spec_repr is None
        assert loaded.round_history == []
        assert loaded.infra_errors == []

    def test_0202_fields_do_not_break_0201_fields(self, tmp_path):
        """Full state with all fields still round-trips."""
        finding = _make_finding()
        state = State(
            findings=[finding],
            baseline_spec_repr="git:HEAD",
            round_history=[{"round": 0, "dispositions": {}}],
            infra_errors=["test error"],
        )
        p = tmp_path / "state.json"
        save_state(state, p)
        loaded = load_state(p)

        # 02-01 fields
        assert loaded.schema_version == 1
        assert len(loaded.findings) == 1
        assert loaded.findings[0].disposition == Disposition.CONFIRMED

        # 02-02 fields
        assert loaded.baseline_spec_repr == "git:HEAD"
        assert len(loaded.round_history) == 1
        assert loaded.infra_errors == ["test error"]
