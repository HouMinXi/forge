# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for promoted_fingerprints set lifecycle (4 cases a-d)."""

import json
from pathlib import Path

from forge.autofix import FixOutcome, StubAutoFixer
from forge.baseline import ResolvedReview
from forge.disposition import Disposition
from forge.falsify import StubFalsifier
from forge.machine import StateMachine
from forge.state import (
    Mode,
    State,
    StateFinding,
    Verdict,
    load_state,
    save_state,
)


def _make_finding(fp="fp-pt-1", disp=Disposition.CONFIRMED):
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


class TestPromotionAdds:
    """(a) promotion event adds fp to set."""

    def test_promotion_adds_fingerprint(self, tmp_path):
        def mock_l0(registry, files):
            return ([_make_finding()], [])

        class ParseFailAutoFixer(StubAutoFixer):
            def fix(self, finding, mode_hint):
                return FixOutcome.PARSE_FAIL

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=ParseFailAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
            max_total_rounds=10,
            max_fix_attempts=3,
        )
        machine.run()
        assert "fp-pt-1" in machine._state.promoted_fingerprints


class TestSaveLoadRoundTrip:
    """(b) set survives save/load round-trip via state.py."""

    def test_round_trip(self, tmp_path):
        state = State()
        state.promoted_fingerprints = {"fp-a", "fp-b", "fp-c"}
        state_path = tmp_path / "state.json"
        save_state(state, state_path)
        loaded = load_state(state_path)
        assert loaded.promoted_fingerprints == {"fp-a", "fp-b", "fp-c"}


class TestSerializesSortedList:
    """(c) set serializes as sorted list, deserializes to set."""

    def test_sorted_list_in_json(self, tmp_path):
        state = State()
        state.promoted_fingerprints = {"c", "a", "b"}
        state_path = tmp_path / "state.json"
        save_state(state, state_path)
        raw = json.loads(state_path.read_text())
        assert raw["promoted_fingerprints"] == ["a", "b", "c"]
        loaded = load_state(state_path)
        assert isinstance(loaded.promoted_fingerprints, set)
        assert loaded.promoted_fingerprints == {"a", "b", "c"}


class TestBackwardCompat:
    """(d) 02-02 state.json (no key) loads with empty set default."""

    def test_missing_key_defaults_empty(self, tmp_path):
        # Write a minimal state.json without promoted_fingerprints
        state = State()
        state_path = tmp_path / "state.json"
        save_state(state, state_path)
        # Remove the key from raw JSON to simulate 02-02 file
        raw = json.loads(state_path.read_text())
        del raw["promoted_fingerprints"]
        del raw["hold_reason"]
        state_path.write_text(json.dumps(raw, indent=2))
        loaded = load_state(state_path)
        assert loaded.promoted_fingerprints == set()
        assert loaded.hold_reason is None
