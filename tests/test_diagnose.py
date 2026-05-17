# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for forge.diagnose -- STATE-05 A/B/C/D classification."""

import json
from pathlib import Path


from forge.diagnose import diagnose_non_convergence

FIXTURES = Path(__file__).parent.parent / "fixtures" / "machine_scenarios"


class TestCategoryA:
    """FIXED -> CONFIRMED toggle in consecutive rounds -> A."""

    def test_oscillation_from_fixture(self):
        data = json.loads((FIXTURES / "oscillation_A.json").read_text())
        result = diagnose_non_convergence(
            data["rounds"], data["infra_errors"]
        )
        assert result == "A"

    def test_toggle_detected(self):
        history = [
            {"dispositions": {"fp-1": "CONFIRMED"}},
            {"dispositions": {"fp-1": "FIXED"}},
            {"dispositions": {"fp-1": "CONFIRMED"}},
        ]
        assert diagnose_non_convergence(history, []) == "A"


class TestCategoryB:
    """Net CONFIRMED count not decreasing across last 3 rounds -> B."""

    def test_regression_from_fixture(self):
        data = json.loads((FIXTURES / "regression_B.json").read_text())
        result = diagnose_non_convergence(
            data["rounds"], data["infra_errors"]
        )
        assert result == "B"

    def test_monotonic_confirmed_count(self):
        history = [
            {"dispositions": {"fp-1": "CONFIRMED"}},
            {"dispositions": {"fp-1": "CONFIRMED", "fp-2": "CONFIRMED"}},
            {"dispositions": {
                "fp-1": "CONFIRMED",
                "fp-2": "CONFIRMED",
                "fp-3": "CONFIRMED",
            }},
        ]
        assert diagnose_non_convergence(history, []) == "B"

    def test_stuck_count_is_b(self):
        """Same CONFIRMED count across 3 rounds = non-decreasing."""
        history = [
            {"dispositions": {"fp-1": "CONFIRMED"}},
            {"dispositions": {"fp-1": "CONFIRMED"}},
            {"dispositions": {"fp-1": "CONFIRMED"}},
        ]
        assert diagnose_non_convergence(history, []) == "B"

    def test_fewer_than_3_rounds_not_b(self):
        history = [
            {"dispositions": {"fp-1": "CONFIRMED"}},
            {"dispositions": {"fp-1": "CONFIRMED", "fp-2": "CONFIRMED"}},
        ]
        # Insufficient data for B; falls through to default A
        assert diagnose_non_convergence(history, []) == "A"


class TestCategoryC:
    """UNCERTAIN count grows monotonically over >= 3 rounds -> C."""

    def test_accumulation_from_fixture(self):
        data = json.loads(
            (FIXTURES / "accumulation_C.json").read_text()
        )
        result = diagnose_non_convergence(
            data["rounds"], data["infra_errors"]
        )
        assert result == "C"

    def test_uncertain_growth(self):
        history = [
            {"dispositions": {"fp-u1": "UNCERTAIN"}},
            {"dispositions": {
                "fp-u1": "UNCERTAIN",
                "fp-u2": "UNCERTAIN",
            }},
            {"dispositions": {
                "fp-u1": "UNCERTAIN",
                "fp-u2": "UNCERTAIN",
                "fp-u3": "UNCERTAIN",
            }},
        ]
        assert diagnose_non_convergence(history, []) == "C"


class TestCategoryD:
    """ANY infra_errors entry -> D (binary trigger per R3 MED2)."""

    def test_infra_failure_from_fixture(self):
        data = json.loads(
            (FIXTURES / "infra_failure_D.json").read_text()
        )
        result = diagnose_non_convergence(
            data["rounds"], data["infra_errors"]
        )
        assert result == "D"

    def test_single_error_is_d(self):
        assert diagnose_non_convergence(
            [], ["L0 ToolError tool=ruff msg=not found"]
        ) == "D"


class TestTieBreaker:
    """D wins over A/B/C when both signals present."""

    def test_d_dominates_a(self):
        # A-toggle pattern + infra_errors -> D wins
        history = [
            {"dispositions": {"fp-1": "CONFIRMED"}},
            {"dispositions": {"fp-1": "FIXED"}},
            {"dispositions": {"fp-1": "CONFIRMED"}},
        ]
        result = diagnose_non_convergence(
            history, ["infra error present"]
        )
        assert result == "D"

    def test_d_dominates_b(self):
        history = [
            {"dispositions": {"fp-1": "CONFIRMED"}},
            {"dispositions": {"fp-1": "CONFIRMED", "fp-2": "CONFIRMED"}},
            {"dispositions": {
                "fp-1": "CONFIRMED",
                "fp-2": "CONFIRMED",
                "fp-3": "CONFIRMED",
            }},
        ]
        result = diagnose_non_convergence(
            history, ["infra error"]
        )
        assert result == "D"

    def test_a_over_b(self):
        """A (oscillation) wins over B (non-decreasing)."""
        # Both A and B signals present, no infra errors
        history = [
            {"dispositions": {
                "fp-1": "CONFIRMED",
                "fp-x": "CONFIRMED",
            }},
            {"dispositions": {
                "fp-1": "FIXED",
                "fp-x": "CONFIRMED",
                "fp-y": "CONFIRMED",
            }},
            {"dispositions": {
                "fp-1": "CONFIRMED",
                "fp-x": "CONFIRMED",
                "fp-y": "CONFIRMED",
                "fp-z": "CONFIRMED",
            }},
        ]
        result = diagnose_non_convergence(history, [])
        assert result == "A"


class TestDefaultFallback:
    """Empty history with no signals -> default A."""

    def test_empty_history(self):
        assert diagnose_non_convergence([], []) == "A"

    def test_single_round(self):
        history = [{"dispositions": {"fp-1": "CONFIRMED"}}]
        assert diagnose_non_convergence(history, []) == "A"
