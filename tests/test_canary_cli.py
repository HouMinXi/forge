# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for canary infrastructure: Verdict.UNRELIABLE, EXIT_UNRELIABLE,
gate.yaml canary: block validation, and init template."""
from __future__ import annotations

import io

import pytest

from code_forge.state import Verdict
from code_forge.exit_codes import EXIT_UNRELIABLE, verdict_to_exit
from code_forge.gate_check import load_gate_config, validate_canary_config
from code_forge.init_template import GATE_YAML_TEMPLATE


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


def _make_fs_open(yaml_text: str):
    """Return an fs_open callable that serves yaml_text for any path."""
    def fs_open(path, mode="r", encoding=None):
        return io.StringIO(yaml_text)
    return fs_open


# Minimal valid gate.yaml that satisfies load_gate_config's test: requirement.
_MINIMAL_GATE = "test:\n  command: ['true']\n"


class TestCanaryValidation:
    """validate_canary_config type-checks and range-checks all fields."""

    def test_gate_yaml_canary_valid(self):
        yaml_text = _MINIMAL_GATE + (
            "canary:\n"
            "  enabled: true\n"
            "  n: 5\n"
            "  threshold_ratio: 0.6\n"
        )
        result = load_gate_config("gate.yaml", fs_open=_make_fs_open(yaml_text))
        assert "canary" in result

    def test_gate_yaml_canary_enabled_not_bool(self):
        with pytest.raises(ValueError, match="bool"):
            validate_canary_config({"enabled": "yes"})

    def test_gate_yaml_canary_n_not_int(self):
        with pytest.raises(ValueError, match="int"):
            validate_canary_config({"n": "five"})

    def test_gate_yaml_canary_n_out_of_range_low(self):
        with pytest.raises(ValueError, match="3..5"):
            validate_canary_config({"n": 2})

    def test_gate_yaml_canary_n_out_of_range_high(self):
        with pytest.raises(ValueError, match="3..5"):
            validate_canary_config({"n": 6})

    def test_gate_yaml_canary_ratio_not_float(self):
        with pytest.raises(ValueError):
            validate_canary_config({"threshold_ratio": "high"})

    def test_gate_yaml_canary_ratio_zero(self):
        """0.0 rejected because ceil(0.0 * n) = 0, which crashes M1."""
        with pytest.raises(ValueError):
            validate_canary_config({"threshold_ratio": 0.0})

    def test_gate_yaml_canary_ratio_out_of_range(self):
        with pytest.raises(ValueError, match=r"0\.0\.\.1\.0"):
            validate_canary_config({"threshold_ratio": 1.5})

    def test_gate_yaml_canary_not_mapping(self):
        with pytest.raises(ValueError, match="mapping"):
            validate_canary_config("invalid")

    def test_gate_yaml_no_canary(self):
        """Backward compat: no canary section still loads fine."""
        result = load_gate_config(
            "gate.yaml", fs_open=_make_fs_open(_MINIMAL_GATE)
        )
        assert "canary" not in result

    def test_gate_yaml_canary_n_boundary_3(self):
        """n=3 is the minimum valid value."""
        validate_canary_config({"n": 3})

    def test_gate_yaml_canary_n_boundary_5(self):
        """n=5 is the maximum valid value."""
        validate_canary_config({"n": 5})

    def test_gate_yaml_canary_ratio_boundary_1(self):
        """threshold_ratio=1.0 is valid (require catching all canaries)."""
        validate_canary_config({"threshold_ratio": 1.0})

    def test_gate_yaml_canary_ratio_small_positive(self):
        """A small positive ratio like 0.01 is valid."""
        validate_canary_config({"threshold_ratio": 0.01})

    def test_gate_yaml_canary_int_ratio_accepted(self):
        """An integer ratio (1) is accepted as a valid number."""
        validate_canary_config({"threshold_ratio": 1})


class TestInitTemplateCanary:
    """Init template includes commented-out canary block."""

    def test_init_template_canary_block(self):
        assert "# canary:" in GATE_YAML_TEMPLATE
        assert "#   threshold_ratio" in GATE_YAML_TEMPLATE
