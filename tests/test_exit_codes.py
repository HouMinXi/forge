# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""CLI-02 exit code constants + verdict_to_exit tests."""

import pytest

from code_forge.exit_codes import (
    EXIT_BUSY,
    EXIT_CLI_ERROR,
    EXIT_DELEGATED,
    EXIT_ESCALATED,
    EXIT_FAIL,
    EXIT_PASS,
    EXIT_TIMEOUT,
    verdict_to_exit,
)
from code_forge.state import Verdict


class TestExitCodeConstants:
    """Verify EXIT_* constants are correct and distinct."""

    def test_all_constants_distinct(self):
        """SC-4: all 7 EXIT_* constants are int 0-6 distinct."""
        values = [EXIT_PASS, EXIT_FAIL, EXIT_CLI_ERROR, EXIT_BUSY,
                  EXIT_ESCALATED, EXIT_DELEGATED, EXIT_TIMEOUT]
        assert all(isinstance(v, int) for v in values)
        assert len(set(values)) == 7
        assert set(values) == {0, 1, 2, 3, 4, 5, 6}

    def test_constant_values(self):
        """Constants match REQUIREMENTS spec literal."""
        assert EXIT_PASS == 0
        assert EXIT_FAIL == 1
        assert EXIT_CLI_ERROR == 2
        assert EXIT_BUSY == 3
        assert EXIT_ESCALATED == 4
        assert EXIT_DELEGATED == 5
        assert EXIT_TIMEOUT == 6


class TestVerdictToExit:
    """Verify verdict_to_exit mapping."""

    def test_pass_maps_to_zero(self):
        """SC-4(a): PASS -> 0."""
        assert verdict_to_exit(Verdict.PASS) == 0

    def test_fail_maps_to_one(self):
        """SC-4(b): FAIL -> 1."""
        assert verdict_to_exit(Verdict.FAIL) == 1

    def test_escalated_maps_to_four(self):
        """SC-4(c): ESCALATED -> 4."""
        assert verdict_to_exit(Verdict.ESCALATED) == 4

    def test_pending_raises_value_error(self):
        """SC-4(d): PENDING raises ValueError."""
        with pytest.raises(ValueError, match="PENDING"):
            verdict_to_exit(Verdict.PENDING)


class TestDelegatedVerdict:
    """Verdict.DELEGATED exit code tests (Phase 24.1-01)."""

    def test_verdict_delegated_exit_code(self):
        """verdict_to_exit(Verdict.DELEGATED) == 5."""
        assert verdict_to_exit(Verdict.DELEGATED) == 5

    def test_exit_delegated_constant(self):
        """EXIT_DELEGATED constant equals 5."""
        assert EXIT_DELEGATED == 5

    def test_pass_exit_unchanged(self):
        """Regression: PASS still maps to 0."""
        assert verdict_to_exit(Verdict.PASS) == 0

    def test_pending_still_raises(self):
        """Regression: PENDING still raises ValueError."""
        with pytest.raises(ValueError, match="PENDING"):
            verdict_to_exit(Verdict.PENDING)


class TestInitReExport:
    """Verify __init__.py re-exports EXIT_* constants (H5, R3-L3)."""

    def test_all_exit_constants_importable_from_forge(self):
        """SC-42 R3-4: from code_forge import EXIT_* works."""
        from code_forge import (
            EXIT_BUSY,
            EXIT_CLI_ERROR,
            EXIT_DELEGATED,
            EXIT_ESCALATED,
            EXIT_FAIL,
            EXIT_PASS,
            EXIT_TIMEOUT,
        )
        from code_forge.exit_codes import (
            EXIT_BUSY as EC_BUSY,
            EXIT_CLI_ERROR as EC_CLI,
            EXIT_DELEGATED as EC_DEL,
            EXIT_ESCALATED as EC_ESC,
            EXIT_FAIL as EC_FAIL,
            EXIT_PASS as EC_PASS,
            EXIT_TIMEOUT as EC_TIMEOUT,
        )
        assert EXIT_PASS == EC_PASS
        assert EXIT_FAIL == EC_FAIL
        assert EXIT_CLI_ERROR == EC_CLI
        assert EXIT_BUSY == EC_BUSY
        assert EXIT_ESCALATED == EC_ESC
        assert EXIT_DELEGATED == EC_DEL
        assert EXIT_TIMEOUT == EC_TIMEOUT
