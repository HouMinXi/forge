# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""CLI-03 env override resolution tests."""

import pytest

from forge.env_resolver import (
    DEFAULT_MAX_TOTAL_ROUNDS,
    MAX_REASONABLE_FIX_ATTEMPTS,
    MAX_REASONABLE_TOTAL_ROUNDS,
    resolve_falsification_engine,
    resolve_max_fix_attempts,
    resolve_max_total_rounds,
)
from forge.disposition import MAX_FIX_ATTEMPTS_PER_FINGERPRINT
from forge.errors import CliError


class TestResolveMaxTotalRounds:
    """Precedence: cli > env > default."""

    def test_cli_wins_over_env(self):
        """SC-5(a): cli value set + env set -> cli wins."""
        result = resolve_max_total_rounds(
            10, {"FORGE_MAX_TOTAL_ROUNDS": "50"}
        )
        assert result == 10

    def test_env_used_when_cli_none(self):
        """SC-5(b): cli None + env set -> env value."""
        result = resolve_max_total_rounds(
            None, {"FORGE_MAX_TOTAL_ROUNDS": "50"}
        )
        assert result == 50

    def test_default_when_both_absent(self):
        """SC-5(c): cli None + env unset -> default."""
        result = resolve_max_total_rounds(None, {})
        assert result == DEFAULT_MAX_TOTAL_ROUNDS

    def test_empty_env_falls_to_default(self):
        """Empty env value -> fall through to default."""
        result = resolve_max_total_rounds(
            None, {"FORGE_MAX_TOTAL_ROUNDS": ""}
        )
        assert result == DEFAULT_MAX_TOTAL_ROUNDS

    def test_whitespace_env_raises(self):
        """SC-6(d): env whitespace raises CliError."""
        with pytest.raises(CliError):
            resolve_max_total_rounds(
                None, {"FORGE_MAX_TOTAL_ROUNDS": "  "}
            )

    def test_non_int_env_raises(self):
        """SC-6(e): env non-int raises CliError."""
        with pytest.raises(CliError, match="expected int"):
            resolve_max_total_rounds(
                None, {"FORGE_MAX_TOTAL_ROUNDS": "abc"}
            )

    def test_zero_env_raises(self):
        """SC-6(f): env=0 raises CliError (must >= 1)."""
        with pytest.raises(CliError, match="must be >= 1"):
            resolve_max_total_rounds(
                None, {"FORGE_MAX_TOTAL_ROUNDS": "0"}
            )

    def test_exceeds_sanity_cap_raises(self):
        """env exceeds sanity cap -> CliError."""
        with pytest.raises(CliError, match="exceeds sanity cap"):
            resolve_max_total_rounds(
                None,
                {"FORGE_MAX_TOTAL_ROUNDS": str(
                    MAX_REASONABLE_TOTAL_ROUNDS + 1
                )},
            )

    def test_env_with_whitespace_padding_accepted(self):
        """env=" 42 " -> stripped to 42."""
        result = resolve_max_total_rounds(
            None, {"FORGE_MAX_TOTAL_ROUNDS": " 42 "}
        )
        assert result == 42


class TestResolveMaxFixAttempts:
    """Precedence: cli > env > default."""

    def test_cli_wins_over_env(self):
        result = resolve_max_fix_attempts(
            5, {"FORGE_MAX_FIX_ATTEMPTS_PER_FINGERPRINT": "10"}
        )
        assert result == 5

    def test_env_used_when_cli_none(self):
        result = resolve_max_fix_attempts(
            None, {"FORGE_MAX_FIX_ATTEMPTS_PER_FINGERPRINT": "10"}
        )
        assert result == 10

    def test_default_when_both_absent(self):
        result = resolve_max_fix_attempts(None, {})
        assert result == MAX_FIX_ATTEMPTS_PER_FINGERPRINT

    def test_exceeds_sanity_cap_raises(self):
        """SC-6(g): env=999 for max_fix_attempts > 100 -> CliError."""
        with pytest.raises(CliError, match="exceeds sanity cap"):
            resolve_max_fix_attempts(
                None,
                {"FORGE_MAX_FIX_ATTEMPTS_PER_FINGERPRINT": str(
                    MAX_REASONABLE_FIX_ATTEMPTS + 1
                )},
            )


class TestResolveFalsificationEngine:
    """Precedence: cli > env > default (auto)."""

    def test_cli_wins(self):
        result = resolve_falsification_engine(
            "stub", {"FORGE_FALSIFICATION_ENGINE": "real"}
        )
        assert result == "stub"

    def test_env_used_when_cli_none(self):
        result = resolve_falsification_engine(
            None, {"FORGE_FALSIFICATION_ENGINE": "stub"}
        )
        assert result == "stub"

    def test_default_auto(self):
        result = resolve_falsification_engine(None, {})
        assert result == "auto"

    def test_invalid_env_raises(self):
        with pytest.raises(CliError, match="invalid"):
            resolve_falsification_engine(
                None, {"FORGE_FALSIFICATION_ENGINE": "bogus"}
            )

    def test_case_insensitive(self):
        result = resolve_falsification_engine(
            None, {"FORGE_FALSIFICATION_ENGINE": "STUB"}
        )
        assert result == "stub"

    def test_empty_env_falls_to_default(self):
        result = resolve_falsification_engine(
            None, {"FORGE_FALSIFICATION_ENGINE": ""}
        )
        assert result == "auto"
