# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for forge.falsify -- StubFalsifier contract."""

import json
from pathlib import Path

import pytest

from forge.disposition import Disposition
from forge.falsify import Falsifier, StubFalsifier
from forge.state import StateFinding


def _make_finding(fingerprint="fp-001"):
    """Create a minimal StateFinding for testing."""
    return StateFinding(
        id="f-001",
        fingerprint=fingerprint,
        source="L0",
        disposition=Disposition.CONFIRMED,
        file="test.py",
        line_range=[1, 10],
        description="test finding",
    )


class TestStubFalsifierDefaults:
    """StubFalsifier without fixture returns CONFIRMED for everything."""

    def test_default_returns_confirmed(self):
        stub = StubFalsifier()
        result = stub.falsify(_make_finding())
        assert result == Disposition.CONFIRMED

    def test_is_falsifier_subclass(self):
        stub = StubFalsifier()
        assert isinstance(stub, Falsifier)


class TestStubFalsifierFixtures:
    """StubFalsifier respects fixture dispositions."""

    def test_all_confirmed(self, tmp_path):
        fixture = tmp_path / "all_confirmed.json"
        fixture.write_text(json.dumps({"default": "CONFIRMED"}))
        stub = StubFalsifier(fixture)
        assert stub.falsify(_make_finding()) == Disposition.CONFIRMED

    def test_all_dismissed(self, tmp_path):
        fixture = tmp_path / "all_dismissed.json"
        fixture.write_text(json.dumps({"default": "DISMISSED"}))
        stub = StubFalsifier(fixture)
        assert stub.falsify(_make_finding()) == Disposition.DISMISSED

    def test_all_uncertain(self, tmp_path):
        fixture = tmp_path / "all_uncertain.json"
        fixture.write_text(json.dumps({"default": "UNCERTAIN"}))
        stub = StubFalsifier(fixture)
        assert stub.falsify(_make_finding()) == Disposition.UNCERTAIN

    def test_per_fingerprint_override(self, tmp_path):
        fixture = tmp_path / "mixed.json"
        fixture.write_text(json.dumps({
            "default": "CONFIRMED",
            "dispositions": {
                "fp-style": "DISMISSED",
                "fp-vague": "UNCERTAIN",
            },
        }))
        stub = StubFalsifier(fixture)
        assert stub.falsify(_make_finding("fp-style")) == Disposition.DISMISSED
        assert stub.falsify(
            _make_finding("fp-vague")
        ) == Disposition.UNCERTAIN
        assert stub.falsify(
            _make_finding("fp-other")
        ) == Disposition.CONFIRMED


class TestStubFalsifierErrors:
    """B4: StubFalsifier errors key raises RuntimeError."""

    def test_error_key_raises_runtime_error(self, tmp_path):
        fixture = tmp_path / "errors.json"
        fixture.write_text(json.dumps({
            "default": "CONFIRMED",
            "errors": {"fp-timeout": "simulated timeout"},
        }))
        stub = StubFalsifier(fixture)
        with pytest.raises(RuntimeError, match="simulated timeout"):
            stub.falsify(_make_finding("fp-timeout"))

    def test_error_key_precedes_dispositions(self, tmp_path):
        """Errors take precedence over dispositions for same fingerprint."""
        fixture = tmp_path / "overlap.json"
        fixture.write_text(json.dumps({
            "default": "CONFIRMED",
            "dispositions": {"fp-both": "DISMISSED"},
            "errors": {"fp-both": "crash"},
        }))
        stub = StubFalsifier(fixture)
        with pytest.raises(RuntimeError, match="crash"):
            stub.falsify(_make_finding("fp-both"))

    def test_non_error_fingerprint_unaffected(self, tmp_path):
        fixture = tmp_path / "errors.json"
        fixture.write_text(json.dumps({
            "default": "CONFIRMED",
            "dispositions": {"fp-ok": "DISMISSED"},
            "errors": {"fp-bad": "crash"},
        }))
        stub = StubFalsifier(fixture)
        assert stub.falsify(_make_finding("fp-ok")) == Disposition.DISMISSED
        assert stub.falsify(
            _make_finding("fp-other")
        ) == Disposition.CONFIRMED


class TestFixedRejection:
    """FIXED rejected at constructor and at falsify() call."""

    def test_fixed_default_rejected_at_constructor(self, tmp_path):
        fixture = tmp_path / "bad.json"
        fixture.write_text(json.dumps({"default": "FIXED"}))
        with pytest.raises(ValueError, match="FIXED.*default"):
            StubFalsifier(fixture)

    def test_fixed_per_entry_rejected_at_constructor(self, tmp_path):
        fixture = tmp_path / "bad.json"
        fixture.write_text(json.dumps({
            "default": "CONFIRMED",
            "dispositions": {"fp-bad": "FIXED"},
        }))
        with pytest.raises(ValueError, match="FIXED.*fp-bad"):
            StubFalsifier(fixture)

    def test_fixed_rejected_at_falsify_call(self):
        """Defense in depth: even if somehow FIXED gets into _dispositions."""
        stub = StubFalsifier()
        # Manually inject FIXED (bypassing constructor check)
        stub._dispositions["fp-sneaky"] = Disposition.FIXED
        with pytest.raises(ValueError, match="FIXED"):
            stub.falsify(_make_finding("fp-sneaky"))


class TestStubFalsifierFromRealFixtures:
    """Test against actual fixture files in tests/fixtures/."""

    _FIXTURE_DIR = Path(__file__).parent / "fixtures" / "stub_dispositions"

    def test_all_confirmed_fixture(self):
        stub = StubFalsifier(self._FIXTURE_DIR / "all_confirmed.json")
        assert stub.falsify(_make_finding()) == Disposition.CONFIRMED

    def test_all_dismissed_fixture(self):
        stub = StubFalsifier(self._FIXTURE_DIR / "all_dismissed.json")
        assert stub.falsify(_make_finding()) == Disposition.DISMISSED

    def test_all_uncertain_fixture(self):
        stub = StubFalsifier(self._FIXTURE_DIR / "all_uncertain.json")
        assert stub.falsify(_make_finding()) == Disposition.UNCERTAIN

    def test_mixed_fixture(self):
        stub = StubFalsifier(self._FIXTURE_DIR / "mixed.json")
        assert stub.falsify(
            _make_finding("fp-leak-001")
        ) == Disposition.CONFIRMED
        assert stub.falsify(
            _make_finding("fp-style-002")
        ) == Disposition.DISMISSED
        assert stub.falsify(
            _make_finding("fp-vague-003")
        ) == Disposition.UNCERTAIN
        assert stub.falsify(
            _make_finding("fp-unknown")
        ) == Disposition.CONFIRMED

    def test_with_errors_fixture(self):
        stub = StubFalsifier(self._FIXTURE_DIR / "with_errors.json")
        assert stub.falsify(
            _make_finding("fp-ok-001")
        ) == Disposition.DISMISSED
        with pytest.raises(RuntimeError, match="stub-simulated timeout"):
            stub.falsify(_make_finding("fp-timeout-001"))
        with pytest.raises(
            RuntimeError, match="stub-simulated stack overflow"
        ):
            stub.falsify(_make_finding("fp-crash-002"))
