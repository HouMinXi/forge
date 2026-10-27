# SPDX-License-Identifier: Apache-2.0
"""Phase 6 Entry: schema_fail / incomplete-coverage credit preserve (Q2=(iii)).

Authority: Entry 19 / plan a84fbc6a… / Plan Review b2016030… (token preserved;
credit-side skip/undo of _reset_clean_credit; _fixpoint_reached untouched).
All greens 「未经独立复现」.
"""

from __future__ import annotations

import pytest

from code_forge.disposition import Disposition
from code_forge.errors import CorruptedStateError
from code_forge.state import (
    ROUND_PHASES,
    State,
    StateFinding,
    earned_history_cycles,
    validate_round_history,
)


SOURCE = "a" * 64
_PASS_NAMES = ("qodo", "expert", "adversarial")


def _phases_returned():
    return {name: "returned" for name in ROUND_PHASES}


def _earned_row(index, count):
    return {
        "round": index,
        "source_hash": SOURCE,
        "reviewed_repositories": None,
        "clean_credit_action": "earned",
        "phase_status": _phases_returned(),
        "acquisition_failures": [],
        "reset_observed": False,
        "clean_rounds_after": count,
        "fixpoint": "CLEAN",
        "dispositions": {},
    }


def _preserved_row(index, count, *, fixpoint="RESET"):
    return {
        "round": index,
        "source_hash": SOURCE,
        "reviewed_repositories": None,
        "clean_credit_action": "preserved",
        "phase_status": _phases_returned(),
        "acquisition_failures": [],
        "reset_observed": True,
        "clean_rounds_after": count,
        "fixpoint": fixpoint,
        "dispositions": {"schema-fail-adversarial": "CONFIRMED"},
    }


def test_preserved_in_closed_set_and_survives_earned_window():
    rows = [_earned_row(0, 1), _earned_row(1, 2), _preserved_row(2, 2)]
    validate_round_history(rows, 2)
    state = State(
        round=2,
        source_hash=SOURCE,
        round_history=rows,
        consecutive_clean_rounds=2,
    )
    assert earned_history_cycles(state, SOURCE, None) == [1, 2]


@pytest.mark.parametrize("fixpoint", ["RESET", "CYCLE_RESTART"])
def test_preserved_accepts_reset_shaped_observation(fixpoint):
    validate_round_history([_preserved_row(0, 0, fixpoint=fixpoint)], 0)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(reset_observed=False),
        lambda r: r.update(fixpoint="CLEAN"),
        lambda r: r.update(fixpoint="INCOMPLETE"),
        lambda r: r.update(
            acquisition_failures=[
                {
                    "id": "l1-qodo-invoke-fail",
                    "fingerprint": "invoke-fail-qodo",
                    "pass_name": "qodo",
                    "outcome": "error",
                }
            ]
        ),
        lambda r: r.update(phase_status={**_phases_returned(), "l1": "not_run"}),
        lambda r: r.pop("dispositions"),
    ],
)
def test_preserved_rejects_malformed_observation(mutate):
    row = _preserved_row(0, 0)
    mutate(row)
    with pytest.raises(CorruptedStateError):
        validate_round_history([row])


def test_preserved_does_not_append_earn_and_reset_still_clears():
    rows = [
        _earned_row(0, 1),
        _earned_row(1, 2),
        _preserved_row(2, 2),
        _earned_row(3, 3),
    ]
    state = State(round=3, source_hash=SOURCE, round_history=rows, consecutive_clean_rounds=3)
    assert earned_history_cycles(state, SOURCE, None) == [1, 2, 4]

    reset = {
        "round": 4,
        "source_hash": SOURCE,
        "reviewed_repositories": None,
        "clean_credit_action": "reset",
        "phase_status": _phases_returned(),
        "acquisition_failures": [],
        "reset_observed": True,
        "clean_rounds_after": 0,
        "fixpoint": "RESET",
        "dispositions": {"fp": "CONFIRMED"},
    }
    rows.append(reset)
    state = State(round=4, source_hash=SOURCE, round_history=rows, consecutive_clean_rounds=0)
    assert earned_history_cycles(state, SOURCE, None) == []


def test_unknown_token_still_refused():
    row = _preserved_row(0, 0)
    row["clean_credit_action"] = "gate_preserved"
    with pytest.raises(CorruptedStateError, match="invalid host clean credit action"):
        validate_round_history([row])


@pytest.mark.parametrize("name", _PASS_NAMES)
def test_schema_coverage_finding_helpers_match_factory_ids(name):
    """Machine eligibility keys must stay aligned with factories.py ids."""
    schema = StateFinding(
        id=f"l1-{name}-schema-fail",
        fingerprint=f"schema-fail-{name}",
        source="INFRA",
        disposition=Disposition.CONFIRMED,
        file="<schema-validation>",
        line_range=[0, 0],
        description="schema",
    )
    coverage = StateFinding(
        id=f"l1-{name}-incomplete-coverage",
        fingerprint=f"incomplete-coverage-{name}",
        source="INFRA",
        disposition=Disposition.CONFIRMED,
        file="<coverage-guard>",
        line_range=[0, 0],
        description="coverage",
    )
    from code_forge.machine import StateMachine

    # Use unbound helpers via a minimal stub that carries the methods.
    class _Probe:
        _is_schema_or_incomplete_coverage_finding = (
            StateMachine._is_schema_or_incomplete_coverage_finding
        )

    probe = _Probe()
    assert probe._is_schema_or_incomplete_coverage_finding(schema) is True
    assert probe._is_schema_or_incomplete_coverage_finding(coverage) is True
    other = StateFinding(
        id=f"l1-{name}-invoke-fail",
        fingerprint=f"invoke-fail-{name}",
        source="INFRA",
        disposition=Disposition.CONFIRMED,
        file="<llm-invoke>",
        line_range=[0, 0],
        description="acq",
    )
    assert probe._is_schema_or_incomplete_coverage_finding(other) is False


def test_label_presence_alone_is_not_a_credit_authority_field():
    """Phase 5 host_failure_kind is report-wire only; history authority ignores it."""
    rows = [_earned_row(0, 1), _earned_row(1, 2), _preserved_row(2, 2)]
    rows[-1]["host_failure_kind"] = "PROVIDER_TRANSPORT"
    validate_round_history(rows, 2)
    state = State(
        round=2,
        source_hash=SOURCE,
        round_history=rows,
        consecutive_clean_rounds=2,
    )
    # Label key is inert: preserve still keeps prior earned; no new earn.
    assert earned_history_cycles(state, SOURCE, None) == [1, 2]
