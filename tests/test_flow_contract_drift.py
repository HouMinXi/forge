# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Drift and freeze guards for flow_contract.py constants.

Two guards:
  1. Freeze test  -- hard-asserts the exact constant values so accidental edits
     to flow_contract.py surface immediately.
  2. Drift test   -- parses SKILL.md threshold prose with regex and asserts each
     parsed value matches the constant. Fails when SKILL.md and flow_contract.py
     diverge. Deterministic (no LLM).
"""

import pathlib
import re

import pytest

from code_forge.flow_contract import (
    DEFAULT_CLEAN_ROUND_THRESHOLD,
    P3_DENSITY_THRESHOLD,
    P3_DISTINCT_PER_DIFF_THRESHOLD,
    P3_DISTINCT_PER_FILE_THRESHOLD,
)

_SKILL_MD = (
    pathlib.Path(__file__).parent.parent / "skills" / "code-forge" / "SKILL.md"
)


class TestFlowContractFreeze:
    """Freeze test: hard-assert exact constant values."""

    def test_flow_contract_constants_are_canonical(self):
        """Constants must match the values documented in SKILL.md and this test.

        Changing these values requires updating SKILL.md prose AND this test
        intentionally -- the dual update is the signal that the change was deliberate.
        """
        assert P3_DISTINCT_PER_FILE_THRESHOLD == 5
        assert P3_DISTINCT_PER_DIFF_THRESHOLD == 10
        assert P3_DENSITY_THRESHOLD == 0.15
        assert DEFAULT_CLEAN_ROUND_THRESHOLD == 3


class TestFlowContractDrift:
    """Drift guard: SKILL.md prose must agree with flow_contract.py constants."""

    def test_skill_md_p3_thresholds_match_flow_contract(self):
        """Parse SKILL.md threshold lines and assert they equal the constants.

        If SKILL.md is missing (e.g. installed without skills/), skip rather than
        fail -- the module is optional at install time.
        """
        if not _SKILL_MD.exists():
            pytest.skip("skills/code-forge/SKILL.md not found -- skipping drift check")

        skill_text = _SKILL_MD.read_text()

        m_file = re.search(r"distinct_per_file\s*>\s*(\d+)", skill_text)
        m_diff = re.search(r"distinct_per_diff\s*>\s*(\d+)", skill_text)
        m_density = re.search(r"density\s*>\s*([\d.]+)", skill_text)

        assert m_file is not None, (
            "SKILL.md: could not find 'distinct_per_file > N' threshold line"
        )
        assert m_diff is not None, (
            "SKILL.md: could not find 'distinct_per_diff > N' threshold line"
        )
        assert m_density is not None, (
            "SKILL.md: could not find 'density > N.NN' threshold line"
        )

        skill_per_file = int(m_file.group(1))
        skill_per_diff = int(m_diff.group(1))
        skill_density = float(m_density.group(1))

        assert skill_per_file == P3_DISTINCT_PER_FILE_THRESHOLD, (
            f"SKILL.md distinct_per_file threshold ({skill_per_file}) "
            f"!= flow_contract.P3_DISTINCT_PER_FILE_THRESHOLD "
            f"({P3_DISTINCT_PER_FILE_THRESHOLD})"
        )
        assert skill_per_diff == P3_DISTINCT_PER_DIFF_THRESHOLD, (
            f"SKILL.md distinct_per_diff threshold ({skill_per_diff}) "
            f"!= flow_contract.P3_DISTINCT_PER_DIFF_THRESHOLD "
            f"({P3_DISTINCT_PER_DIFF_THRESHOLD})"
        )
        assert skill_density == P3_DENSITY_THRESHOLD, (
            f"SKILL.md density threshold ({skill_density}) "
            f"!= flow_contract.P3_DENSITY_THRESHOLD ({P3_DENSITY_THRESHOLD})"
        )
