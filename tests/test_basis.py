# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <[EMAIL_REDACTED]>
"""Unit tests for code_forge.basis module (Phase 51: BASIS-DISCLOSE)."""
from __future__ import annotations

from typing import Any, cast

import pytest

from code_forge.basis import (
    AUTHORITY_DETERMINISTIC_EXECUTED,
    AUTHORITY_LLM_TRAINED,
    EpistemicBasis,
    derive_basis,
)
from code_forge.disposition import Disposition
from code_forge.state import StateFinding


def _make_finding(
    source: Any = "L0",
    disposition: Disposition = Disposition.CONFIRMED,
) -> StateFinding:
    return StateFinding(
        id="f-test-1",
        fingerprint="fp1",
        source=cast(Any, source),
        disposition=disposition,
        file="src/code_forge/example.py",
        line_range=[1, 5],
        description="example finding",
    )


class TestEpistemicBasisDataclass:
    def test_frozen_immutability(self):
        basis = EpistemicBasis(
            authority=AUTHORITY_DETERMINISTIC_EXECUTED,
            falsification_survived=True,
            convergence_rounds=2,
        )
        assert basis.authority == AUTHORITY_DETERMINISTIC_EXECUTED
        assert basis.falsification_survived is True
        assert basis.convergence_rounds == 2

        with pytest.raises(AttributeError):
            basis.authority = "modified"  # type: ignore[misc]

    def test_to_dict(self):
        basis = EpistemicBasis(
            authority=AUTHORITY_LLM_TRAINED,
            falsification_survived=False,
            convergence_rounds=3,
        )
        assert basis.to_dict() == {
            "authority": "llm-trained",
            "falsification_survived": False,
            "convergence_rounds": 3,
        }


class TestDeriveBasis:
    @pytest.mark.parametrize(
        "source",
        [
            "L0",
            "MUTANT",
            "E2E_CHECK",
            "COVERAGE",
            "INFRA",
            "FIXVAL",
            "LINT",
            "FORMAT",
            "SECURITY",
        ],
    )
    def test_deterministic_sources_authority_and_survival(self, source: str):
        # Deterministic sources always have authority="deterministic-executed" and survived=True
        for disp in (Disposition.CONFIRMED, Disposition.DISMISSED, Disposition.UNCERTAIN, Disposition.FIXED, Disposition.STYLE):
            finding = _make_finding(source=source, disposition=disp)
            basis = derive_basis(finding, convergence_rounds=2)
            assert basis.authority == AUTHORITY_DETERMINISTIC_EXECUTED
            assert basis.falsification_survived is True
            assert basis.convergence_rounds == 2

    @pytest.mark.parametrize(
        ("disposition", "expected_survived"),
        [
            (Disposition.CONFIRMED, True),
            (Disposition.UNCERTAIN, True),
            (Disposition.FIXED, True),
            (Disposition.STYLE, True),
            (Disposition.DISMISSED, False),
        ],
    )
    def test_l1_falsification_survival(
        self,
        disposition: Disposition,
        expected_survived: bool,
    ):
        finding = _make_finding(source="L1", disposition=disposition)
        basis = derive_basis(finding, convergence_rounds=4)
        assert basis.authority == AUTHORITY_LLM_TRAINED
        assert basis.falsification_survived is expected_survived
        assert basis.convergence_rounds == 4

    def test_convergence_rounds_default(self):
        finding = _make_finding(source="L1", disposition=Disposition.CONFIRMED)
        basis = derive_basis(finding)
        assert basis.convergence_rounds == 1

    def test_unknown_source_raises_value_error(self):
        finding = _make_finding(source="UNKNOWN_SOURCE")
        with pytest.raises(
            ValueError,
            match=r"unknown finding source 'UNKNOWN_SOURCE'; add to basis derivation table",
        ):
            derive_basis(finding)
