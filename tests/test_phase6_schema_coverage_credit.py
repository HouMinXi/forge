# SPDX-License-Identifier: Apache-2.0
"""Phase 6 Entry: schema_fail / incomplete-coverage credit preserve (Q2=(iii)).

Authority: Entry 19 / plan a84fbc6a… / Plan Review b2016030… (token preserved;
credit-side skip/undo of _reset_clean_credit; _fixpoint_reached untouched).
All greens 「未经独立复现」.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.errors import CorruptedStateError
from code_forge.falsify import StubFalsifier
from code_forge.llm_invoke import Usage
from code_forge.machine import StateMachine, _FixpointResult
from code_forge.state import (
    ROUND_PHASES,
    Mode,
    State,
    StateFinding,
    Verdict,
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


def _preserved_row(index, count, *, fixpoint="RESET", cause="schema-fail-adversarial"):
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
        "dispositions": {cause: "CONFIRMED"},
        "l1_fingerprints": [cause],
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


@pytest.mark.parametrize("cause", [
    f"{kind}-{name}" for kind in ("schema-fail", "incomplete-coverage") for name in _PASS_NAMES
])
@pytest.mark.parametrize("fixpoint", ["RESET", "CYCLE_RESTART"])
def test_preserved_accepts_reset_shaped_observation(fixpoint, cause):
    rows = [_earned_row(0, 1), _earned_row(1, 2), _preserved_row(2, 2, fixpoint=fixpoint, cause=cause)]
    validate_round_history(rows, 2)
    state = State(round=2, source_hash=SOURCE, round_history=rows, consecutive_clean_rounds=2)
    assert earned_history_cycles(state, SOURCE, None) == [1, 2]


_SHAPE = "preserved host round lacks schema/coverage reset observation"
_NO_CAUSE = "preserved host round lacks Q2=\\(iii\\) schema/coverage disposition"
_NOT_ONLY = "preserved host round lacks \\(iii\\)-only product dispositions"


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda r: r.update(reset_observed=False), _SHAPE),
        (lambda r: r.update(fixpoint="CLEAN"), _SHAPE),
        (lambda r: r.update(fixpoint="INCOMPLETE"), "incomplete host round lacks protocol interruption"),
        (
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
            _SHAPE,
        ),
        (lambda r: r.update(phase_status={**_phases_returned(), "l1": "not_run"}), _SHAPE),
        # H1/M1: every phase must have returned, not only L1.
        (lambda r: r.update(phase_status={**_phases_returned(), "l2": "not_run"}), _SHAPE),
        (lambda r: r.update(phase_status={**_phases_returned(), "l2": "failed"}), _SHAPE),
        (lambda r: r.update(phase_status={**_phases_returned(), "e2e": "not_run"}), _SHAPE),
        (lambda r: r.update(phase_status={**_phases_returned(), "coverage": "not_run"}), _SHAPE),
        (lambda r: r.update(falsify_protocol_failures=["schema-fail-adversarial"]),
         "falsifier protocol failure lacks L1 observation"),
        (lambda r: r.pop("dispositions"), "finalized host round lacks product snapshot"),
    ],
)
def test_preserved_rejects_malformed_observation(mutate, match):
    row = _preserved_row(0, 0)
    mutate(row)
    with pytest.raises(CorruptedStateError, match=match):
        validate_round_history([row])


@pytest.mark.parametrize(
    ("dispositions", "l1_fingerprints", "match"),
    [
        ({}, [], _NO_CAUSE),
        ({"product-fp": "CONFIRMED"}, ["product-fp"], _NO_CAUSE),
        ({"schema-fail-adversarial": "UNCERTAIN"}, ["schema-fail-adversarial"], _NO_CAUSE),
        ({"schema-fail-adversarial": "DISMISSED"}, ["schema-fail-adversarial"], _NO_CAUSE),
        ({"schema-fail-adversarial": "CONFIRMED"}, [], _NO_CAUSE),
        ({"schema-fail-adversarial": "CONFIRMED"}, None, _NO_CAUSE),
        ({"schema-fail-other": "CONFIRMED"}, ["schema-fail-other"], _NO_CAUSE),
        ({"invoke-fail-qodo": "CONFIRMED"}, ["invoke-fail-qodo"], _NO_CAUSE),
        ({"xschema-fail-qodo": "CONFIRMED"}, ["xschema-fail-qodo"], _NO_CAUSE),
        (
            {"schema-fail-qodo": "CONFIRMED", "product-fp": "CONFIRMED"},
            ["schema-fail-qodo", "product-fp"],
            _NOT_ONLY,
        ),
        (
            {"incomplete-coverage-expert": "CONFIRMED", "product-fp": "UNCERTAIN"},
            ["incomplete-coverage-expert", "product-fp"],
            _NOT_ONLY,
        ),
        (
            {"schema-fail-qodo": "CONFIRMED", "receipt-0123456789ab": "CONFIRMED"},
            ["schema-fail-qodo"],
            _NOT_ONLY,
        ),
        (
            {"schema-fail-qodo": "CONFIRMED", "invoke-fail-expert": "CONFIRMED"},
            ["schema-fail-qodo", "invoke-fail-expert"],
            _NOT_ONLY,
        ),
        (
            # A (iii) id that L1 did not observe this round is not a cause.
            {"schema-fail-qodo": "CONFIRMED", "schema-fail-expert": "CONFIRMED"},
            ["schema-fail-qodo"],
            _NOT_ONLY,
        ),
    ],
)
def test_preserved_requires_iii_only_dispositions(dispositions, l1_fingerprints, match):
    """M1: preserved needs an observed (iii) cause and nothing else open."""
    row = _preserved_row(0, 0)
    row["dispositions"] = dispositions
    if l1_fingerprints is None:
        row.pop("l1_fingerprints")
    else:
        row["l1_fingerprints"] = l1_fingerprints
    with pytest.raises(CorruptedStateError, match=match):
        validate_round_history([row])


def test_preserved_allows_credit_neutral_diagnostics_beside_iii_cause():
    row = _preserved_row(0, 0)
    row["dispositions"] = {
        "schema-fail-adversarial": "CONFIRMED",
        "incomplete-coverage-qodo": "CONFIRMED",
        "receipt-0123456789ab": "UNCERTAIN",
        "mutation-evidence-error": "CONFIRMED",
        "old-product-fp": "DISMISSED",
        "fixed-product-fp": "FIXED",
    }
    row["l1_fingerprints"] = ["schema-fail-adversarial", "incomplete-coverage-qodo"]
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
    """Machine eligibility keys must stay aligned with factories.py ids.

    Constructed here; the factory-built findings are checked against the same
    helper in test_review_interruption_source_controls (real provider path).
    """
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


@pytest.mark.parametrize("label", [None, "PROVIDER_TRANSPORT", "PROVIDER_CAPACITY", "SCHEMA"])
@pytest.mark.parametrize("action_row", ["earned", "preserved"])
def test_label_presence_alone_is_not_a_credit_authority_field(label, action_row):
    """Phase 5 host_failure_kind is report-wire only (plan §6 label-inert).

    Adding, flipping, or omitting the label on the row leaves validation and the
    earned window identical to the unlabelled history.
    """
    def rows():
        tail = _preserved_row(2, 2) if action_row == "preserved" else _earned_row(2, 3)
        return [_earned_row(0, 1), _earned_row(1, 2), tail]

    def cycles(history):
        validate_round_history(history, 2)
        count = history[-1]["clean_rounds_after"]
        state = State(round=2, source_hash=SOURCE, round_history=history, consecutive_clean_rounds=count)
        return earned_history_cycles(state, SOURCE, None)

    baseline = cycles(rows())
    labelled = rows()
    if label is not None:
        labelled[-1]["host_failure_kind"] = label
    assert cycles(labelled) == baseline
    assert baseline == ([1, 2] if action_row == "preserved" else [1, 2, 3])


@pytest.mark.parametrize(
    "change",
    [
        {"file": "<coverage-guard>"},
        {"file": "test.py"},
        {"line_range": [1, 1]},
        {"source": "L1"},
        {"disposition": Disposition.UNCERTAIN},
        {"id": "l1-other-schema-fail", "fingerprint": "schema-fail-other"},
        {"fingerprint": "schema-fail-expert"},
    ],
)
def test_schema_finding_near_miss_is_not_iii(tmp_path, monkeypatch, change):
    """M2: each identity field of the (iii) key is load-bearing, incl. file."""
    sm = _guard_machine(tmp_path, monkeypatch)
    near = _schema_finding("qodo")
    for key, value in change.items():
        setattr(near, key, value)
    assert sm._is_schema_or_incomplete_coverage_finding(near) is False
    sm._state.findings = [near]
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET) is False


def _coverage_finding(name="qodo"):
    return StateFinding(
        id=f"l1-{name}-incomplete-coverage",
        fingerprint=f"incomplete-coverage-{name}",
        source="INFRA",
        disposition=Disposition.CONFIRMED,
        file="<coverage-guard>",
        line_range=[0, 0],
        description="incomplete coverage: constructed",
    )


@pytest.mark.parametrize(
    "change",
    [
        {"file": "<schema-validation>"},
        {"file": "test.py"},
        {"line_range": [1, 1]},
        {"source": "L1"},
        {"disposition": Disposition.UNCERTAIN},
        {"id": "l1-other-incomplete-coverage", "fingerprint": "incomplete-coverage-other"},
        {"fingerprint": "incomplete-coverage-expert"},
    ],
)
def test_coverage_finding_near_miss_is_not_iii(tmp_path, monkeypatch, change):
    """M2: the incomplete-coverage key is load-bearing on every field, incl. file."""
    sm = _guard_machine(tmp_path, monkeypatch)
    exact = _coverage_finding("qodo")
    assert sm._is_schema_or_incomplete_coverage_finding(exact) is True
    near = _coverage_finding("qodo")
    for key, value in change.items():
        setattr(near, key, value)
    assert sm._is_schema_or_incomplete_coverage_finding(near) is False
    sm._state.findings = [near]
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET) is False


# ---------------------------------------------------------------------------
# Predicate guards on a real StateMachine (M2). Each test flips exactly one
# guard from the accepted baseline so a removed guard fails here.
# ---------------------------------------------------------------------------


def _schema_finding(name="qodo", severity=None):
    finding = StateFinding(
        id=f"l1-{name}-schema-fail",
        fingerprint=f"schema-fail-{name}",
        source="INFRA",
        disposition=Disposition.CONFIRMED,
        file="<schema-validation>",
        line_range=[0, 0],
        description="schema validation failed: constructed",
    )
    if severity is not None:
        finding.severity = severity
    return finding


def _product_finding(fp="product-fp", disposition=Disposition.CONFIRMED, desc="P0: constructed defect"):
    return StateFinding(
        id=fp,
        fingerprint=fp,
        source="L1",
        disposition=disposition,
        file="test.py",
        line_range=[1, 1],
        description=desc,
    )


def _guard_machine(tmp_path, monkeypatch, *, receipt_active=True):
    sm = StateMachine(
        resolved_review=ResolvedReview(
            source_files=[Path("test.py")], baseline_content=None, git_diff=None, mode_hint="git",
        ),
        falsifier=StubFalsifier(),
        source_hash="abc",
        baseline_spec_repr="empty",
        cwd=tmp_path,
        registry={},
        mode=Mode.LOCAL,
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        l0_runner=lambda r, f: ([], []),
        l1_provider=lambda r: [],
    )
    if receipt_active:
        monkeypatch.setattr(sm, "_host_receipt_active", lambda: True)
    sm._phase_status = {name: "returned" for name in ROUND_PHASES}
    sm._state.findings = [_schema_finding()]
    return sm


@pytest.mark.parametrize("fixpoint", [_FixpointResult.RESET, _FixpointResult.CYCLE_RESTART])
def test_guard_baseline_schema_only_round_preserves(tmp_path, monkeypatch, fixpoint):
    sm = _guard_machine(tmp_path, monkeypatch)
    assert sm._schema_coverage_credit_preserve(fixpoint) is True


@pytest.mark.parametrize(
    "fixpoint", [None, _FixpointResult.CLEAN, _FixpointResult.INCOMPLETE]
)
def test_guard_non_reset_fixpoint_never_preserves(tmp_path, monkeypatch, fixpoint):
    sm = _guard_machine(tmp_path, monkeypatch)
    assert sm._schema_coverage_credit_preserve(fixpoint) is False


def test_guard_non_receipt_machine_never_preserves(tmp_path, monkeypatch):
    """H2: first guard is host receipt mode."""
    sm = _guard_machine(tmp_path, monkeypatch, receipt_active=False)
    assert sm._host_receipt_active() is False
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET) is False


@pytest.mark.parametrize("phase", ROUND_PHASES)
@pytest.mark.parametrize("status", ["not_run", "failed"])
def test_guard_any_unreturned_phase_never_preserves(tmp_path, monkeypatch, phase, status):
    """H1: a partial attempt (e.g. L2 crash) cannot keep credit."""
    sm = _guard_machine(tmp_path, monkeypatch)
    sm._phase_status[phase] = status
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET) is False


def test_guard_acquisition_marker_never_preserves(tmp_path, monkeypatch):
    sm = _guard_machine(tmp_path, monkeypatch)
    marker = StateFinding(
        id="l1-expert-invoke-fail",
        fingerprint="invoke-fail-expert",
        source="INFRA",
        disposition=Disposition.CONFIRMED,
        file="<llm-invoke>",
        line_range=[0, 0],
        description="constructed acquisition marker",
    )
    sm._state.findings.append(marker)
    sm._acquisition_markers = [marker]
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET) is False


def test_guard_hold_fingerprint_never_preserves(tmp_path, monkeypatch):
    sm = _guard_machine(tmp_path, monkeypatch)
    sm._hold_confirmed_fingerprints = ["held-fp"]
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET) is False


def test_guard_falsifier_transport_failure_never_preserves(tmp_path, monkeypatch):
    sm = _guard_machine(tmp_path, monkeypatch)
    failed = _product_finding("transport-fp", Disposition.DISMISSED)
    sm._falsify_failed_findings = [failed]
    sm._falsify_protocol_findings = []
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET) is False
    # The same finding classified as a protocol failure is not transport.
    sm._falsify_protocol_findings = [failed]
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET) is True


def test_guard_requires_a_iii_finding(tmp_path, monkeypatch):
    sm = _guard_machine(tmp_path, monkeypatch)
    sm._state.findings = []
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET) is False


@pytest.mark.parametrize("disposition", [Disposition.CONFIRMED, Disposition.UNCERTAIN])
def test_guard_open_product_finding_never_preserves(tmp_path, monkeypatch, disposition):
    sm = _guard_machine(tmp_path, monkeypatch)
    sm._state.findings.append(_product_finding(disposition=disposition))
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET) is False


@pytest.mark.parametrize("disposition", [Disposition.DISMISSED, Disposition.FIXED])
def test_guard_closed_product_finding_does_not_block(tmp_path, monkeypatch, disposition):
    sm = _guard_machine(tmp_path, monkeypatch)
    sm._state.findings.append(_product_finding(disposition=disposition))
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET) is True


def test_recurring_schema_cycle_restart_preserves(tmp_path, monkeypatch):
    """M2: a recurring P2 schema finding yields CYCLE_RESTART and still preserves."""
    sm = _guard_machine(tmp_path, monkeypatch)
    sm._state.findings = [_schema_finding(severity="P2")]
    sm._state.round_history = [{"dispositions": {"schema-fail-qodo": "CONFIRMED"}}, {}]
    fixpoint = sm._fixpoint_reached()
    assert fixpoint is _FixpointResult.CYCLE_RESTART
    assert sm._schema_coverage_credit_preserve(fixpoint) is True


def _m4_machine(tmp_path, monkeypatch, product, *, prior="CONFIRMED"):
    """M4: a recurring product finding (seen in the prior snapshot) beside a new schema-fail."""
    sm = _guard_machine(tmp_path, monkeypatch)
    sm._state.findings = [product]
    sm._state.round_history = [{"dispositions": {product.fingerprint: prior}}, {}]
    return sm


def test_m4_recurring_p3_plus_new_schema_preserves(tmp_path, monkeypatch):
    """M4 (plan Q7 / section 2.2): preserve iff the fixpoint without (iii) is CLEAN.

    A recurring sub-threshold P3 alone is CLEAN; a new schema-fail turns the
    observed fixpoint into a reset kind, and the counterfactual keeps credit.
    """
    p3 = _product_finding("fp-p3", desc="P3: naming nit")
    sm = _m4_machine(tmp_path, monkeypatch, p3)
    assert sm._fixpoint_reached() is _FixpointResult.CLEAN
    sm._state.findings = [p3, _schema_finding()]
    fixpoint = sm._fixpoint_reached()
    assert fixpoint is _FixpointResult.RESET
    assert sm._schema_coverage_credit_preserve(fixpoint) is True
    # The counterfactual restores the observed findings exactly.
    assert [f.fingerprint for f in sm._state.findings] == ["fp-p3", "schema-fail-qodo"]
    assert sm._fixpoint_reached() is _FixpointResult.RESET


@pytest.mark.parametrize(
    ("desc", "prior", "counterfactual"),
    [
        ("P2: recurring bug", "CONFIRMED", _FixpointResult.CYCLE_RESTART),
        ("P1: recurring defect", "CONFIRMED", _FixpointResult.RESET),
        ("P0: recurring defect", "CONFIRMED", _FixpointResult.RESET),
        # FIXED -> CONFIRMED reversion is clause (b), even for a P3.
        ("P3: naming nit", "FIXED", _FixpointResult.RESET),
    ],
    ids=["p2", "p1", "p0", "p3-reversion"],
)
def test_m4_non_clean_counterfactual_keeps_reset(tmp_path, monkeypatch, desc, prior, counterfactual):
    product = _product_finding("fp-product", desc=desc)
    sm = _m4_machine(tmp_path, monkeypatch, product, prior=prior)
    assert sm._fixpoint_reached() is counterfactual
    sm._state.findings = [product, _schema_finding()]
    fixpoint = sm._fixpoint_reached()
    assert fixpoint in (_FixpointResult.RESET, _FixpointResult.CYCLE_RESTART)
    assert sm._schema_coverage_credit_preserve(fixpoint) is False


@pytest.mark.parametrize(
    "product",
    [
        _product_finding("fp-new", desc="P3: naming nit"),
        _product_finding("fp-new", disposition=Disposition.UNCERTAIN, desc="P3: unsure nit"),
    ],
    ids=["new-confirmed-p3", "semantic-uncertain"],
)
def test_m4_new_confirmed_or_uncertain_keeps_reset(tmp_path, monkeypatch, product):
    """A NEW CONFIRMED (clause a) or a semantic UNCERTAIN never preserves, even at P3."""
    sm = _guard_machine(tmp_path, monkeypatch)
    sm._state.findings = [product, _schema_finding()]
    sm._state.round_history = [{"dispositions": {}}, {}]
    assert sm._fixpoint_without([sm._state.findings[1]]) is _FixpointResult.RESET
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET) is False


def _m4_prior_row(index, dispositions, **extra):
    row = _earned_row(index, index + 1)
    row["dispositions"] = dispositions
    row.update(extra)
    return row


def _m4_preserved(index, count, other):
    row = _preserved_row(index, count)
    row["dispositions"].update(other)
    row["l1_fingerprints"].extend(other)
    return row


def test_l1_l2_infra_incomplete_counterfactual_preserves(tmp_path, monkeypatch):
    """L-1: an L2 infra error alone only downgrades the counterfactual CLEAN to INCOMPLETE; credit-neutral."""
    sm = _guard_machine(tmp_path, monkeypatch)
    sm._l2_infra_error = "constructed L2 runner failure"
    assert sm._fixpoint_without([sm._state.findings[0]]) is _FixpointResult.INCOMPLETE
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET) is True
    assert sm._schema_coverage_credit_preserve(_FixpointResult.CYCLE_RESTART) is True


def test_l1_l2_infra_incomplete_with_pending_protocol_keeps_reset(tmp_path, monkeypatch):
    """L-1: the same INCOMPLETE arm refuses when a falsifier protocol observation is pending."""
    sm = _guard_machine(tmp_path, monkeypatch)
    sm._l2_infra_error = "constructed L2 runner failure"
    proto = _product_finding("fp-proto", disposition=Disposition.UNCERTAIN, desc="P3: protocol")
    sm._state.findings = sm._state.findings + [proto]
    sm._falsify_protocol_findings = [proto]
    sm._falsify_failed_findings = [proto]
    sm._host_attempt_round = 0
    assert sm._fixpoint_without([sm._state.findings[0]]) is _FixpointResult.INCOMPLETE
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET) is False
    # Without the L2 infra error the protocol-only INCOMPLETE never preserved.
    sm._l2_infra_error = None
    assert sm._fixpoint_without([sm._state.findings[0]]) is _FixpointResult.INCOMPLETE
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET) is False


def test_m4_loader_accepts_recurring_confirmed_beside_iii_cause():
    rows = [
        _m4_prior_row(0, {"fp-p3": "CONFIRMED"}),
        _m4_prior_row(1, {"fp-p3": "CONFIRMED"}),
        _m4_preserved(2, 2, {"fp-p3": "CONFIRMED"}),
    ]
    validate_round_history(rows, 2)
    state = State(round=2, source_hash=SOURCE, round_history=rows, consecutive_clean_rounds=2)
    assert earned_history_cycles(state, SOURCE, None) == [1, 2]


@pytest.mark.parametrize(
    ("prior", "other"),
    [
        # New CONFIRMED product finding: clause (a).
        ([{}, {}], {"fp-new": "CONFIRMED"}),
        # FIXED -> CONFIRMED reversion against the previous snapshot: clause (b).
        ([{"fp-p3": "CONFIRMED"}, {"fp-p3": "FIXED"}], {"fp-p3": "CONFIRMED"}),
        # Recurring but UNCERTAIN: semantic uncertainty is never credit-neutral.
        ([{"fp-p3": "UNCERTAIN"}, {"fp-p3": "UNCERTAIN"}], {"fp-p3": "UNCERTAIN"}),
    ],
    ids=["new-confirmed", "fixed-reversion", "recurring-uncertain"],
)
def test_m4_loader_refuses_non_recurring_product(prior, other):
    rows = [_m4_prior_row(i, disps) for i, disps in enumerate(prior)]
    rows.append(_m4_preserved(len(prior), len(prior), other))
    with pytest.raises(CorruptedStateError, match=_NOT_ONLY):
        validate_round_history(rows, len(prior))


def test_m4_loader_refuses_first_confirmation_after_protocol_observation():
    observed = {"fp-p3": "UNCERTAIN", "schema-fail-adversarial": "DISMISSED"}
    protocol = _m4_prior_row(0, observed, falsify_protocol_failures=["fp-p3"])
    protocol["l1_fingerprints"] = ["fp-p3"]
    protocol.update(clean_credit_action="interrupted", fixpoint="INCOMPLETE", reset_observed=False)
    protocol["clean_rounds_after"] = 0
    rows = [protocol, _m4_preserved(1, 0, {"fp-p3": "CONFIRMED"})]
    with pytest.raises(CorruptedStateError, match=_NOT_ONLY):
        validate_round_history(rows, 1)
    # Once the fingerprint was adjudicated CONFIRMED, it is a plain repeat.
    rows.insert(1, _m4_prior_row(1, {"fp-p3": "CONFIRMED"}))
    rows[1]["clean_rounds_after"] = 0
    rows[2]["round"] = 2
    validate_round_history(rows, 2)


def test_m4_loader_recurrence_ignores_synthetic_acquisition_markers():
    """A prior synthetic acquisition marker is not a product observation (projection, as the fixpoint)."""
    marker = "invoke-fail-qodo"
    interrupted = _m4_prior_row(1, {marker: "CONFIRMED"})
    interrupted.update(clean_credit_action="interrupted", fixpoint="CLEAN", reset_observed=False)
    interrupted["clean_rounds_after"] = 1
    interrupted["acquisition_failures"] = [{
        "id": "l1-qodo-invoke-fail", "fingerprint": marker, "pass_name": "qodo", "outcome": "error",
    }]
    rows = [_m4_prior_row(0, {}), interrupted, _m4_preserved(2, 1, {marker: "CONFIRMED"})]
    with pytest.raises(CorruptedStateError, match=_NOT_ONLY):
        validate_round_history(rows, 1)
    # The same fingerprint seen as a real product finding is a plain repeat.
    rows = [_m4_prior_row(0, {}), _m4_prior_row(1, {marker: "CONFIRMED"}),
            _m4_preserved(2, 2, {marker: "CONFIRMED"})]
    validate_round_history(rows, 2)


def test_h2_non_receipt_local_schema_round_keeps_m6_reset(tmp_path):
    """H2 regression: without host receipts the round resets exactly as on M6."""
    calls = {"n": 0}

    def l1():
        n = calls["n"]
        calls["n"] += 1
        return ([_schema_finding()] if n == 2 else [], [], Usage(), 0.0)

    machine = StateMachine(
        mode=Mode.LOCAL,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=ResolvedReview(
            source_files=[Path("test.py")], baseline_content=None, git_diff=None, mode_hint="git",
        ),
        source_hash="abc",
        baseline_spec_repr="empty",
        cwd=tmp_path,
        registry={},
        l0_runner=lambda r, f: ([], []),
        l1_provider=l1,
        max_total_rounds=8,
        clean_round_threshold=3,
    )
    assert machine._host_receipt_active() is False
    assert machine.run() is Verdict.PASS
    history = [
        (row.get("round"), row.get("fixpoint"), row.get("clean_rounds_after"))
        for row in machine._state.round_history
    ]
    assert history == [
        (0, "CLEAN", 1), (1, "CLEAN", 2), (2, "RESET", 0),
        (3, "CLEAN", 1), (4, "CLEAN", 2), (5, "CLEAN", 3),
    ]
    assert all("clean_credit_action" not in row for row in machine._state.round_history)
    assert earned_history_cycles(machine._state, "abc", None) == [4, 5, 6]


def test_l3_compat_preserved_token_is_the_only_new_action():
    """L3: the closed token set grew by exactly `preserved`; unknown tokens stay refused.

    M6 (ec8cca99) refuses a `preserved` row with "invalid host clean credit
    action" (evidence: review-fix1/L3-compat-m6-refusal.txt); this branch
    accepts only a well-formed (iii) preserved row.
    """
    for token in ("preserve", "Preserved", "preserved_credit", "restore"):
        row = _preserved_row(0, 0)
        row["clean_credit_action"] = token
        with pytest.raises(CorruptedStateError, match="invalid host clean credit action"):
            validate_round_history([row])
    validate_round_history([_preserved_row(0, 0)])
