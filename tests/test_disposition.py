# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for forge.disposition -- enum stability + protocol constants."""

from forge.disposition import (
    DISPOSITION_PROTOCOL_VERSION,
    FEEDBACK_SCHEMA_VERSION,
    MAX_FIX_ATTEMPTS_PER_FINGERPRINT,
    Disposition,
)


class TestDispositionEnum:
    """Verify Disposition enum has exactly 4 members with correct values."""

    def test_exactly_four_members(self):
        members = list(Disposition)
        assert len(members) == 4

    def test_confirmed_value(self):
        assert Disposition.CONFIRMED.value == "CONFIRMED"

    def test_dismissed_value(self):
        assert Disposition.DISMISSED.value == "DISMISSED"

    def test_uncertain_value(self):
        assert Disposition.UNCERTAIN.value == "UNCERTAIN"

    def test_fixed_value(self):
        assert Disposition.FIXED.value == "FIXED"

    def test_str_enum_identity(self):
        """Disposition members ARE strings (str, Enum)."""
        for d in Disposition:
            assert isinstance(d, str)
            assert d == d.value


class TestProtocolConstants:
    """Verify protocol constants are exported with correct values."""

    def test_disposition_protocol_version(self):
        assert DISPOSITION_PROTOCOL_VERSION == 1
        assert isinstance(DISPOSITION_PROTOCOL_VERSION, int)

    def test_max_fix_attempts(self):
        assert MAX_FIX_ATTEMPTS_PER_FINGERPRINT == 3
        assert isinstance(MAX_FIX_ATTEMPTS_PER_FINGERPRINT, int)

    def test_feedback_schema_version(self):
        assert FEEDBACK_SCHEMA_VERSION == 1
        assert isinstance(FEEDBACK_SCHEMA_VERSION, int)
