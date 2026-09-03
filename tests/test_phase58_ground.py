# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Phase 58-0 GROUND-THE-KNOBS: executable checks for the four facts.

These tests turn the plan's four code-citation facts into assertions that
fail if the behaviour changes.  They are not regression tests for fixes;
they pin the design invariants that the ablation and depth-sweep arms
depend on.

Fact 1: FORGE_CLEAN_ROUND_THRESHOLD env override wins at every diff size.
Fact 2: StubFalsifier() with no fixture defaults to CONFIRMED.
Fact 3: --required-cycles is on verify_parser only, not on review.
Fact 4: FORGE_FALSIFICATION_ENGINE selects engine (cli > env > auto).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from code_forge.diff import tier_threshold
from code_forge.disposition import Disposition
from code_forge.env_resolver import (
    resolve_falsification_engine,
    resolve_max_total_rounds,
)
from code_forge.errors import CliError
from code_forge.falsify import StubFalsifier
from code_forge.state import StateFinding


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_finding(fp: str = "test-fp") -> StateFinding:
    """Minimal StateFinding for StubFalsifier tests."""
    return StateFinding(
        id="test-id",
        fingerprint=fp,
        source="L1",
        disposition=Disposition.CONFIRMED,
        file="test.py",
        line_range=[1, 10],
        description="test finding",
    )


# ---------------------------------------------------------------------------
# Fact 1: FORGE_CLEAN_ROUND_THRESHOLD override honours env_override
#         at every diff size bracket.
# ---------------------------------------------------------------------------


class TestFact1TierThresholdEnvOverride:
    """env_override must dominate the tier curve at every diff-size bracket.

    The tier curve (diff.py:84-93) returns 2/3/4/4 for line counts
    30/120/300/2000. env_override must override all of them, and must
    clamp to 1 at the floor.  This is what lets the depth-sweep set
    FORGE_CLEAN_ROUND_THRESHOLD=1,2,3 and know the machine obeys.
    """

    # Sizes that span every tier bracket: 0, 30, 120, 300, 2000
    TIER_SIZES_AND_DEFAULTS = [
        (0, 3),      # empty/error: default 3
        (30, 2),     # small: 2
        (120, 3),    # medium: 3
        (300, 4),    # large: 4
        (2000, 4),   # very large: 4
    ]

    @pytest.mark.parametrize("line_count,default", TIER_SIZES_AND_DEFAULTS)
    @pytest.mark.parametrize("override", [1, 2, 3, 5, 10])
    def test_override_wins(self, line_count, default, override):
        """env_override=N returns N regardless of line_count."""
        result = tier_threshold(line_count, env_override=override)
        assert result == override

    def test_default_curve_without_override(self):
        """Without override, the tier curve returns its designed values."""
        for line_count, expected in self.TIER_SIZES_AND_DEFAULTS:
            actual = tier_threshold(line_count)
            assert actual == expected, (
                f"tier_threshold({line_count}) = {actual}, expected {expected}"
            )

    @pytest.mark.parametrize("override", [0, -1, -100])
    def test_override_clamps_to_1(self, override):
        """env_override <= 0 is clamped to 1 (min 1 cycle)."""
        for line_count, _ in self.TIER_SIZES_AND_DEFAULTS:
            assert tier_threshold(line_count, env_override=override) == 1

    def test_override_beats_whole_file(self):
        """env_override has higher priority than whole_file flag."""
        assert tier_threshold(10, whole_file=True, env_override=5) == 5


# ---------------------------------------------------------------------------
# Fact 2: StubFalsifier() with no fixture defaults to CONFIRMED.
# ---------------------------------------------------------------------------


class TestFact2StubFalsifierDefaultConfirmed:
    """StubFalsifier with no fixture path returns CONFIRMED for all inputs.

    The ablation-off arm uses StubFalsifier() (no fixture) so every L1
    candidate promotes to CONFIRMED.  If this default changes, the
    ablation arm measures something different from what the plan says.
    """

    def test_no_fixture_returns_confirmed(self):
        stub = StubFalsifier()
        assert stub.falsify(_make_finding()) == Disposition.CONFIRMED

    def test_no_fixture_confirmed_for_any_fingerprint(self):
        stub = StubFalsifier()
        for fp in ["fp-001", "fp-999", "anything", ""]:
            assert stub.falsify(_make_finding(fp)) == Disposition.CONFIRMED

    def test_fixture_with_default_overrides(self, tmp_path):
        """fixture_path with 'default: DISMISSED' overrides the built-in."""
        fixture = tmp_path / "dismissed.json"
        fixture.write_text(json.dumps({"default": "DISMISSED"}))
        stub = StubFalsifier(fixture)
        assert stub.falsify(_make_finding()) == Disposition.DISMISSED

    def test_factory_stub_engine_no_fixture(self):
        """build_falsifier('stub') returns a StubFalsifier that CONFIRMs."""
        from code_forge.factories import build_falsifier
        falsifier = build_falsifier("stub")
        assert isinstance(falsifier, StubFalsifier)
        assert falsifier.falsify(_make_finding()) == Disposition.CONFIRMED


# ---------------------------------------------------------------------------
# Fact 3: --required-cycles lives on verify_parser only.
# ---------------------------------------------------------------------------


class TestFact3RequiredCyclesVerifyOnly:
    """--required-cycles is registered on verify, not on review.

    The plan uses FORGE_CLEAN_ROUND_THRESHOLD to control round depth in
    the review loop.  --required-cycles is a verify-time check and must
    NOT drive a live review.  If it appeared on the review parser, it
    could interfere with the depth-sweep.
    """

    def test_verify_parser_has_required_cycles(self):
        from code_forge.cli import _build_parser
        parser = _build_parser()
        # 'verify' subcommand should accept --required-cycles
        args = parser.parse_args(["verify", "--required-cycles", "5"])
        assert getattr(args, "required_cycles", None) == 5

    def test_review_parser_does_not_have_required_cycles(self):
        from code_forge.cli import _build_parser
        parser = _build_parser()
        # 'review' subcommand must NOT accept --required-cycles
        with pytest.raises(SystemExit):
            parser.parse_args(["review", "--required-cycles", "5"])


# ---------------------------------------------------------------------------
# Fact 4: FORGE_FALSIFICATION_ENGINE precedence: cli > env > auto.
# ---------------------------------------------------------------------------


class TestFact4FalsificationEnginePrecedence:
    """FORGE_FALSIFICATION_ENGINE resolves with cli > env > auto.

    The ablation arms set this env var to select 'stub' (off) or 'auto'
    (on).  The resolver must honour the env var when no CLI flag is given.
    """

    def test_cli_value_wins_over_env(self):
        result = resolve_falsification_engine(
            "real", {"FORGE_FALSIFICATION_ENGINE": "stub"}
        )
        assert result == "real"

    def test_env_used_when_cli_is_none(self):
        result = resolve_falsification_engine(
            None, {"FORGE_FALSIFICATION_ENGINE": "stub"}
        )
        assert result == "stub"

    def test_default_is_auto(self):
        result = resolve_falsification_engine(None, {})
        assert result == "auto"

    def test_env_case_insensitive(self):
        result = resolve_falsification_engine(
            None, {"FORGE_FALSIFICATION_ENGINE": "STUB"}
        )
        assert result == "stub"

    def test_invalid_env_raises(self):
        with pytest.raises(CliError):
            resolve_falsification_engine(
                None, {"FORGE_FALSIFICATION_ENGINE": "bogus"}
            )

    def test_empty_env_falls_to_auto(self):
        result = resolve_falsification_engine(
            None, {"FORGE_FALSIFICATION_ENGINE": ""}
        )
        assert result == "auto"
