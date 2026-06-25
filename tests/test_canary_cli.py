# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for canary infrastructure: Verdict.UNRELIABLE, EXIT_UNRELIABLE,
gate.yaml canary: block validation, and init template."""
from __future__ import annotations

import pytest

from code_forge.state import Verdict
from code_forge.exit_codes import EXIT_UNRELIABLE, verdict_to_exit


class TestVerdictUnreliable:
    """Verdict.UNRELIABLE enum member exists with the correct value."""

    def test_verdict_unreliable_exists(self):
        assert Verdict.UNRELIABLE is not None
        assert Verdict.UNRELIABLE.value == "UNRELIABLE"
        # Round-trip from string
        assert Verdict("UNRELIABLE") is Verdict.UNRELIABLE

    def test_exit_unreliable_value(self):
        assert EXIT_UNRELIABLE == 7

    def test_verdict_to_exit_unreliable(self):
        assert verdict_to_exit(Verdict.UNRELIABLE) == 7

    def test_exit_code_uniqueness(self):
        """All EXIT_* constants must have distinct integer values."""
        import code_forge.exit_codes as ec
        exit_names = [
            name for name in dir(ec)
            if name.startswith("EXIT_") and isinstance(getattr(ec, name), int)
        ]
        values = [getattr(ec, name) for name in exit_names]
        assert len(values) == len(set(values)), (
            "Duplicate exit code values: %s"
            % {v: [n for n in exit_names if getattr(ec, n) == v]
               for v in values if values.count(v) > 1}
        )
        # Verify the expected set after adding UNRELIABLE
        assert set(values) == {0, 1, 2, 3, 4, 5, 6, 7}
