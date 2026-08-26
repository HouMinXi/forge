# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <[EMAIL_REDACTED]>
"""Unit tests for code_forge.basis module (Phase 51: BASIS-DISCLOSE & Phase 52: ENV-MANIFEST)."""

from __future__ import annotations

from typing import Any, cast

import pytest

from code_forge.basis import (
    AUTHORITY_DETERMINISTIC_EXECUTED,
    AUTHORITY_LLM_DOCS_LATEST,
    AUTHORITY_LLM_DOCS_PINNED,
    EpistemicBasis,
    derive_basis,
)
from code_forge.disposition import Disposition
from code_forge.manifest import ManifestTier
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
            not_verified_against_declared_env=False,
        )
        assert basis.authority == AUTHORITY_DETERMINISTIC_EXECUTED
        assert basis.falsification_survived is True
        assert basis.convergence_rounds == 2
        assert basis.not_verified_against_declared_env is False

        with pytest.raises(AttributeError):
            basis.authority = "modified"  # type: ignore[misc]

    def test_to_dict(self):
        basis = EpistemicBasis(
            authority=AUTHORITY_LLM_DOCS_PINNED,
            falsification_survived=False,
            convergence_rounds=3,
            not_verified_against_declared_env=True,
        )
        assert basis.to_dict() == {
            "authority": "llm-docs-pinned",
            "falsification_survived": False,
            "convergence_rounds": 3,
            "not_verified_against_declared_env": True,
        }

    def test_to_dict_omits_when_false(self):
        basis = EpistemicBasis(
            authority=AUTHORITY_DETERMINISTIC_EXECUTED,
            falsification_survived=True,
            convergence_rounds=2,
            not_verified_against_declared_env=False,
        )
        assert basis.to_dict() == {
            "authority": "deterministic-executed",
            "falsification_survived": True,
            "convergence_rounds": 2,
        }
        assert "not_verified_against_declared_env" not in basis.to_dict()


class TestDeriveBasis:
    @pytest.mark.parametrize(
        "source",
        [
            "L0",
            "MUTANT",
            "E2E_CHECK",
            "COVERAGE",
            "EXEC",
            "FIXVAL",
            "LINT",
            "FORMAT",
            "SECURITY",
            "RULEPACK",
        ],
    )
    def test_deterministic_sources_authority_and_survival(self, source: str):
        # Deterministic sources under DECLARED tier
        for disp in (
            Disposition.CONFIRMED,
            Disposition.DISMISSED,
            Disposition.UNCERTAIN,
            Disposition.FIXED,
            Disposition.STYLE,
        ):
            finding = _make_finding(source=source, disposition=disp)
            basis = derive_basis(finding, convergence_rounds=2, manifest_tier=ManifestTier.DECLARED)
            assert basis.authority == AUTHORITY_DETERMINISTIC_EXECUTED
            assert basis.falsification_survived is True
            assert basis.convergence_rounds == 2
            assert basis.not_verified_against_declared_env is False

    def test_infra_basis_authority_unavailable(self):
        finding = _make_finding(source="INFRA", disposition=Disposition.CONFIRMED)
        basis = derive_basis(finding, convergence_rounds=2)
        assert basis.authority == "infra-unavailable"
        assert basis.falsification_survived is False
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
        basis = derive_basis(finding, convergence_rounds=4, manifest_tier=ManifestTier.DECLARED)
        assert basis.authority == AUTHORITY_LLM_DOCS_PINNED
        assert basis.falsification_survived is expected_survived
        assert basis.convergence_rounds == 4
        assert basis.not_verified_against_declared_env is False

    def test_absent_manifest_degrades_version_sensitive_l1(self):
        finding = _make_finding(source="L1", disposition=Disposition.CONFIRMED)
        basis = derive_basis(finding, convergence_rounds=3, manifest_tier=ManifestTier.ABSENT)
        assert basis.authority == AUTHORITY_LLM_DOCS_LATEST
        assert basis.falsification_survived is True
        assert basis.convergence_rounds == 3
        assert basis.not_verified_against_declared_env is True

    def test_absent_manifest_degrades_version_sensitive_mutant(self):
        finding = _make_finding(source="MUTANT", disposition=Disposition.CONFIRMED)
        basis = derive_basis(finding, convergence_rounds=2, manifest_tier=ManifestTier.ABSENT)
        assert basis.authority == AUTHORITY_DETERMINISTIC_EXECUTED
        assert basis.falsification_survived is True
        assert basis.convergence_rounds == 2
        assert basis.not_verified_against_declared_env is False

    @pytest.mark.parametrize(
        "source",
        [
            "L0",
            "E2E_CHECK",
            "COVERAGE",
            "EXEC",
            "FIXVAL",
            "LINT",
            "FORMAT",
            "SECURITY",
        ],
    )
    def test_absent_manifest_does_not_degrade_non_version_sensitive(self, source: str):
        finding = _make_finding(source=source, disposition=Disposition.CONFIRMED)
        basis = derive_basis(finding, convergence_rounds=2, manifest_tier=ManifestTier.ABSENT)
        assert basis.authority == AUTHORITY_DETERMINISTIC_EXECUTED
        assert basis.falsification_survived is True
        assert basis.not_verified_against_declared_env is False

    @pytest.mark.parametrize(
        ("disposition", "expected_exec"),
        [
            (Disposition.CONFIRMED, "fail_before"),
            (Disposition.UNCERTAIN, None),
            (Disposition.FIXED, None),
            (Disposition.STYLE, None),
            (Disposition.DISMISSED, None),
        ],
    )
    def test_fail_before_strengthens_confirmed_l1_only(
        self, disposition: Disposition, expected_exec: str | None
    ):
        finding = _make_finding(source="L1", disposition=disposition)
        basis = derive_basis(
            finding,
            convergence_rounds=2,
            manifest_tier=ManifestTier.DECLARED,
            exec_evidence="fail_before",
        )
        assert basis.exec_evidence == expected_exec

    def test_observed_manifest_does_not_degrade(self):
        finding_l1 = _make_finding(source="L1", disposition=Disposition.CONFIRMED)
        basis_l1 = derive_basis(finding_l1, manifest_tier=ManifestTier.OBSERVED)
        assert basis_l1.authority == AUTHORITY_LLM_DOCS_LATEST
        assert basis_l1.not_verified_against_declared_env is False

        finding_mut = _make_finding(source="MUTANT", disposition=Disposition.CONFIRMED)
        basis_mut = derive_basis(finding_mut, manifest_tier=ManifestTier.OBSERVED)
        assert basis_mut.authority == AUTHORITY_DETERMINISTIC_EXECUTED
        assert basis_mut.not_verified_against_declared_env is False

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
