# SPDX-License-Identifier: Apache-2.0
"""The reviewer's severity must survive the trip to the convergence gate.

`_severity_tier` used to recover severity by looking for a "P0:".."P3:"
prefix on the description. L1 descriptions are formatted "[qodo] <text>",
so that prefix never matched and every L1 finding fell through to the
source-based default of P1.

Two consequences, both measured below:

* A P0 remote-execution finding and a P3 naming nit arrived at the gate
  as the same tier.
* The P3-only density path in `_evaluate_fixpoint` was unreachable, so a
  handful of nits forced a RESET exactly like a security defect.

The severity was never missing -- `parse_reviewer_json` validates it
against {"P0","P1","P2","P3"} and then dropped it on the floor.
"""
from __future__ import annotations

import json

import pytest

from code_forge.disposition import Disposition
from code_forge.machine import _severity_tier
from code_forge.reviewer_json import _json_to_state_findings, validate_reviewer_json
from code_forge.state import StateFinding, _finding_from_dict


def _parse(raw: str, pass_name: str = "qodo") -> list[StateFinding]:
    return _json_to_state_findings(validate_reviewer_json(raw), pass_name)


def _envelope(**finding) -> str:
    """A minimal valid reviewer envelope carrying one finding."""
    base = {"file": "chat.ts", "line": 42, "description": "x"}
    base.update(finding)
    return json.dumps({"findings": [base], "code_excerpts": []})


def _finding(**kw) -> StateFinding:
    base: dict = dict(
        id="l1-qodo-abc",
        fingerprint="abc",
        source="L1",
        disposition=Disposition.CONFIRMED,
        file="chat.ts",
        line_range=[10, 10],
        description="[qodo] something",
    )
    base.update(kw)
    return StateFinding(**base)


class TestSeveritySurvivesParsing:
    def test_parse_carries_severity_through(self):
        findings = _parse(_envelope(
            severity="P3", description="variable name could be clearer",
        ))
        assert findings[0].severity == "P3"

    @pytest.mark.parametrize("sev", ["P0", "P1", "P2", "P3"])
    def test_every_valid_severity_round_trips(self, sev):
        findings = _parse(_envelope(severity=sev), "expert")
        assert findings[0].severity == sev


class TestSeverityReachesTheGate:
    def test_a_p3_arrives_as_p3(self):
        """The measured failure: this returned P1 before the field existed."""
        assert _severity_tier(_finding(severity="P3")) == "P3"

    def test_a_p0_arrives_as_p0(self):
        assert _severity_tier(_finding(severity="P0")) == "P0"

    def test_p0_and_p3_are_distinguishable(self):
        """They were not, which is the whole defect."""
        assert _severity_tier(_finding(severity="P0")) != _severity_tier(
            _finding(severity="P3")
        )


class TestFallbacksStillWork:
    """Findings forge raises itself carry no reviewer opinion."""

    def test_description_prefix_is_still_honoured(self):
        assert _severity_tier(
            _finding(severity=None, description="P3: naming", source="L0")
        ) == "P3"

    def test_unprefixed_l0_defaults_to_p1(self):
        assert _severity_tier(
            _finding(severity=None, description="no prefix", source="L0")
        ) == "P1"

    def test_unprefixed_other_defaults_to_p2(self):
        assert _severity_tier(
            _finding(severity=None, description="no prefix", source="MUTANT")
        ) == "P2"

    def test_a_bogus_severity_falls_through(self):
        """A value outside the enum must not be trusted as a tier.

        parse_reviewer_json validates, but state.json can be edited by
        hand and a finding can arrive from an older writer.
        """
        assert _severity_tier(
            _finding(severity="URGENT", description="no prefix", source="L0")
        ) == "P1"


class TestPersistence:
    def test_severity_survives_a_state_json_round_trip(self):
        from dataclasses import asdict

        original = _finding(severity="P2")
        restored = _finding_from_dict(json.loads(json.dumps(asdict(original))))
        assert restored.severity == "P2"

    def test_a_state_file_written_before_the_field_still_loads(self):
        """Old state.json files have no severity key at all.

        They must keep loading and simply carry no reviewer severity --
        the position they were already in when they were written.
        """
        from dataclasses import asdict

        d = asdict(_finding(severity="P1"))
        del d["severity"]
        restored = _finding_from_dict(d)
        assert restored.severity is None
        assert _severity_tier(restored) == "P1"  # falls back, does not crash
