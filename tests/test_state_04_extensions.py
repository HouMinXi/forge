# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for State.py 02-04 extensions (4 cases a-d)."""

import json

from code_forge.state import State, load_state, save_state


class TestHoldReasonRoundTrip:
    """(a) hold_reason round-trip via save/load."""

    def test_round_trip(self, tmp_path):
        state = State()
        state.hold_reason = "3 UNCERTAIN finding(s) awaiting human disposition"
        state_path = tmp_path / "state.json"
        save_state(state, state_path)
        loaded = load_state(state_path)
        assert loaded.hold_reason == (
            "3 UNCERTAIN finding(s) awaiting human disposition"
        )


class TestHoldReasonNoneDefault:
    """(b) hold_reason None default."""

    def test_default_none(self):
        state = State()
        assert state.hold_reason is None

    def test_saved_none(self, tmp_path):
        state = State()
        state_path = tmp_path / "state.json"
        save_state(state, state_path)
        loaded = load_state(state_path)
        assert loaded.hold_reason is None


class TestPromotedFingerprintsRoundTrip:
    """(c) promoted_fingerprints round-trip preserves membership.

    H3 verification: State(promoted_fingerprints={"a","b","c"}) ->
    save_state -> read raw JSON -> assert ["a","b","c"] (sorted) ->
    load_state -> assert set("a","b","c").
    """

    def test_set_serialization_round_trip(self, tmp_path):
        state = State()
        state.promoted_fingerprints = {"a", "b", "c"}
        state_path = tmp_path / "state.json"
        save_state(state, state_path)
        # Verify raw JSON format
        raw = json.loads(state_path.read_text())
        assert raw["promoted_fingerprints"] == ["a", "b", "c"]
        # Verify load returns set
        loaded = load_state(state_path)
        assert isinstance(loaded.promoted_fingerprints, set)
        assert loaded.promoted_fingerprints == {"a", "b", "c"}


class TestPre0204BackwardCompat:
    """(d) pre-02-04 state.json (no new keys) loads with defaults."""

    def test_missing_keys_load_defaults(self, tmp_path):
        # Write valid state.json, then strip 02-04 keys
        state = State()
        state_path = tmp_path / "state.json"
        save_state(state, state_path)
        raw = json.loads(state_path.read_text())
        del raw["hold_reason"]
        del raw["promoted_fingerprints"]
        state_path.write_text(json.dumps(raw, indent=2))
        loaded = load_state(state_path)
        assert loaded.hold_reason is None
        assert loaded.promoted_fingerprints == set()
