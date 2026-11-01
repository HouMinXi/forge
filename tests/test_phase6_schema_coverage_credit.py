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
from code_forge.factories import _ExcerptRejection, _L1Call
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
    load_state,
    product_round_history,
    save_state,
    validate_round_history,
)
from tests import test_review_interruption_source_controls as _sc

# Re-export the shared fixture by plain assignment, not `from ... import`, so test
# parameters named `offline_workspace` are not ruff F811 redefinitions of an
# unused import. pytest registers a fixture under its module attribute name, so
# the attribute must stay `offline_workspace`.
offline_workspace = _sc.offline_workspace


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


@pytest.mark.parametrize(
    "cause",
    [f"{kind}-{name}" for kind in ("schema-fail", "incomplete-coverage") for name in _PASS_NAMES],
)
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
        (
            lambda r: r.update(falsify_protocol_failures=["schema-fail-adversarial"]),
            "falsifier protocol failure lacks L1 observation",
        ),
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
            source_files=[Path("test.py")],
            baseline_content=None,
            git_diff=None,
            mode_hint="git",
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


@pytest.mark.parametrize("fixpoint", [None, _FixpointResult.CLEAN, _FixpointResult.INCOMPLETE])
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


@pytest.mark.parametrize("l2_infra", [False, True], ids=["no-l2-infra", "l2-infra"])
def test_mixed_schema_and_coverage_preserves_with_or_without_l2_infra(tmp_path, monkeypatch, l2_infra):
    """Plan omission: schema-fail and incomplete coverage in one round, with and without an L2 infra error.

    Both (iii) findings leave the counterfactual; what is left is CLEAN, or
    INCOMPLETE only because of the L2 infra error, and both preserve.
    """
    sm = _guard_machine(tmp_path, monkeypatch)
    sm._state.findings = [_schema_finding("adversarial"), _coverage_finding("qodo")]
    if l2_infra:
        sm._l2_infra_error = "constructed L2 runner failure"
    expected = _FixpointResult.INCOMPLETE if l2_infra else _FixpointResult.CLEAN
    assert sm._fixpoint_without(list(sm._state.findings)) is expected
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET) is True
    assert sm._schema_coverage_credit_preserve(_FixpointResult.CYCLE_RESTART) is True


@pytest.mark.parametrize("l2_infra", [False, True], ids=["no-l2-infra", "l2-infra"])
def test_mixed_schema_and_coverage_with_new_product_keeps_reset(tmp_path, monkeypatch, l2_infra):
    """Plan omission, negative: a new CONFIRMED product finding beside both (iii) findings resets."""
    sm = _guard_machine(tmp_path, monkeypatch)
    sm._state.findings = [
        _schema_finding("adversarial"),
        _coverage_finding("qodo"),
        _product_finding(desc="P3: constructed new defect"),
    ]
    if l2_infra:
        sm._l2_infra_error = "constructed L2 runner failure"
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


def test_m4_loader_accepts_recurring_uncertain_then_confirmed_without_protocol():
    """CR2R2-L3: a fingerprint seen UNCERTAIN (no protocol observation) recurs, as in the fixpoint."""
    rows = [
        _m4_prior_row(0, {"fp-p3": "UNCERTAIN"}),
        _m4_prior_row(1, {"fp-p3": "UNCERTAIN"}),
        _m4_preserved(2, 2, {"fp-p3": "CONFIRMED"}),
    ]
    validate_round_history(rows, 2)


def test_i9_product_history_and_preserved_validation_share_the_projection(monkeypatch):
    """Joint I-9: the fixpoint history and the split-out validator both project through one function.

    Each module binds the shared definition at import; spying on both bindings
    shows `product_round_history` and the preserved-row validator call it.
    """
    import sys

    from code_forge import state as state_module
    from code_forge import state_preserved, state_rows

    original = state_rows._project_product_row
    callers = []

    def spy(row):
        callers.append(sys._getframe(1).f_globals["__name__"])
        return original(row)

    monkeypatch.setattr(state_module, "_project_product_row", spy)
    monkeypatch.setattr(state_preserved, "_project_product_row", spy)
    rows = [
        _m4_prior_row(0, {"fp-p3": "CONFIRMED"}),
        _m4_prior_row(1, {"fp-p3": "CONFIRMED"}),
        _m4_preserved(2, 2, {"fp-p3": "CONFIRMED"}),
    ]
    validate_round_history(rows, 2)
    assert "code_forge.state_preserved" in callers
    callers.clear()
    product_round_history(rows)
    assert "code_forge.state" in callers


def test_i3_project_product_row_has_one_definition():
    """fix8 I-3: one real definition, in the leaf module state_rows; no call-time shim; it pickles."""
    import pickle

    from code_forge import state as state_module
    from code_forge import state_preserved, state_rows

    real = state_rows._project_product_row
    assert real.__module__ == "code_forge.state_rows" and real.__qualname__ == "_project_product_row"
    assert pickle.loads(pickle.dumps(real)) is real  # noqa: S301 - our own function object
    assert state_module._project_product_row is real
    assert state_preserved._project_product_row is real
    assert state_module.is_host_round is state_rows.is_host_round
    assert state_module.HOST_ROUND_FIELDS is state_rows.HOST_ROUND_FIELDS
    assert not hasattr(state_preserved, "is_host_round")
    assert not hasattr(state_preserved, "_moved_project_product_row")


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
    interrupted["acquisition_failures"] = [
        {
            "id": "l1-qodo-invoke-fail",
            "fingerprint": marker,
            "pass_name": "qodo",
            "outcome": "error",
        }
    ]
    rows = [_m4_prior_row(0, {}), interrupted, _m4_preserved(2, 1, {marker: "CONFIRMED"})]
    with pytest.raises(CorruptedStateError, match=_NOT_ONLY):
        validate_round_history(rows, 1)
    # The same fingerprint seen as a real product finding is a plain repeat.
    rows = [
        _m4_prior_row(0, {}),
        _m4_prior_row(1, {marker: "CONFIRMED"}),
        _m4_preserved(2, 2, {marker: "CONFIRMED"}),
    ]
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
            source_files=[Path("test.py")],
            baseline_content=None,
            git_diff=None,
            mode_hint="git",
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
        (0, "CLEAN", 1),
        (1, "CLEAN", 2),
        (2, "RESET", 0),
        (3, "CLEAN", 1),
        (4, "CLEAN", 2),
        (5, "CLEAN", 3),
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


# ---------------------------------------------------------------------------
# C3 (joint spec c3-impl-spec-joint-v1, sha bc577a5a…): UNTRUSTED companions
# of ONE parsed schema-fail response are excluded from the counterfactual and
# omitted from the preserved row. NARROW scope (user-question-pending): any
# second response, excerpt rejection, unavailable payload, scoped review or
# non-_L1Call producer keeps the M6 reset (TD-P6-C3c).
# ---------------------------------------------------------------------------

_C3_FP = "loc-companion"


def _companion(name="adversarial", fp=_C3_FP, desc="[adversarial] constructed candidate"):
    return StateFinding(
        id=f"l1-{name}-untrusted-{fp}",
        fingerprint=fp,
        source="UNTRUSTED",
        disposition=Disposition.UNCERTAIN,
        file="test.py",
        line_range=[2, 2],
        description=desc,
    )


def _rejection(call, name="adversarial"):
    return _ExcerptRejection(call._excerpt_invocation, name, {"pass_name": name}, "{}", "")


def _c3_machine(tmp_path, monkeypatch, *, name="adversarial", companions=None):
    """One parsed schema-fail response for `name` plus its UNTRUSTED companion(s)."""
    sm = _guard_machine(tmp_path, monkeypatch)
    # Rule 2a: the response count enters through a real _L1Call recorder and
    # the machine's own snapshot point (`_capture_reviewer_responses`); the
    # fixture never sets `_responses_last_round` directly. The
    # `_attempted_last_round` assignment below is a legacy-fixture hand-set
    # kept for the v1 tests per v3 T17; it is NOT the production injection
    # path (production assigns it only from the L1 phase result).
    sm.l1_provider = _L1Call(lambda call: call.record_reviewer_response(name))
    sm.l1_provider()
    sm._capture_reviewer_responses()
    schema = _schema_finding(name)
    comps = [_companion(name)] if companions is None else companions
    sm._state.findings = [schema, *comps]
    sm._phase_findings = {"l1": [schema, *comps]}
    sm._attempted_last_round = [{"pass_name": name, "findings": [], "code_excerpts": []}]
    sm._state.round_history = [{"dispositions": {}}, {}]
    return sm, schema, comps


def _add(sm, *findings):
    sm._state.findings = sm._state.findings + list(findings)
    sm._phase_findings["l1"] = sm._phase_findings["l1"] + list(findings)


def test_c3_single_schema_response_with_companions_preserves(tmp_path, monkeypatch):
    """§4.1: the companions alone held the reset; excluding them, the round is (iii)-only."""
    comps = [_companion(), _companion(fp="loc-other")]
    sm, schema, _ = _c3_machine(tmp_path, monkeypatch, companions=comps)
    observed = list(sm._state.findings)
    assert sm._fixpoint_reached() is _FixpointResult.RESET
    # Excluding only the schema-fail still resets on the companions (fix2 behaviour).
    assert sm._fixpoint_without([schema]) is _FixpointResult.RESET
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET) is True
    assert sm._c3_strip_fps == {_C3_FP, "loc-other"}
    assert sm._state.findings == observed and all(
        a is b for a, b in zip(sm._state.findings, observed, strict=True)
    )


def _new_product(fp, desc, disposition=Disposition.CONFIRMED):
    return _product_finding(fp, disposition, desc)


def _proto(sm):
    proto = _new_product("fp-proto", "P3: protocol", Disposition.UNCERTAIN)
    _add(sm, proto)
    sm._falsify_protocol_findings = [proto]
    sm._falsify_failed_findings = [proto]
    sm._host_attempt_round = 0


def _recurring(sm, finding, prior="CONFIRMED"):
    _add(sm, finding)
    sm._state.round_history = [{"dispositions": {finding.fingerprint: prior}}, {}]


def _p3_over_threshold(sm):
    p3s = [_new_product(f"fp-p3-{i}", f"P3: rule {i}") for i in range(6)]
    _add(sm, *p3s)
    sm._state.round_history = [{"dispositions": {f.fingerprint: "CONFIRMED" for f in p3s}}, {}]


def _p3_density(sm):
    _recurring(sm, _new_product("fp-p3", "P3: nit"))
    # One recurring P3 on a two-changed-line diff: 0.5 > P3_DENSITY_THRESHOLD.
    sm.resolved_review = ResolvedReview(
        source_files=[Path("test.py")],
        baseline_content=None,
        mode_hint="git",
        git_diff="--- a/test.py\n+++ b/test.py\n@@ -1 +1 @@\n-a = 1\n+a = 2\n",
    )


def _swap_to_coverage(sm):
    coverage = _coverage_finding("adversarial")
    sm._state.findings = [
        coverage if f.fingerprint == "schema-fail-adversarial" else f for f in sm._state.findings
    ]
    sm._phase_findings["l1"] = list(sm._state.findings)


def _other_pass(sm):
    for finding in sm._state.findings:
        if finding.source == "UNTRUSTED":
            finding.id = f"l1-expert-untrusted-{finding.fingerprint}"


def _two_attempts(sm):
    # Deviation 7 (extended in fix6, CR2G-N1): edits the legacy _c3_machine
    # hand-set snapshot; this decision-level test never runs _execute_round.
    sm._attempted_last_round = sm._attempted_last_round * 2


def _with_rejection(sm):
    sm.l1_provider._excerpt_rejections = (_rejection(sm.l1_provider),)


def _stale(sm):
    sm._phase_findings["l1"] = [f for f in sm._phase_findings["l1"] if f.source != "UNTRUSTED"]


_C3_NEGATIVES = {
    "new-confirmed-p1": lambda sm: _add(sm, _new_product("fp-new", "P1: new defect")),
    "new-confirmed-p3": lambda sm: _add(sm, _new_product("fp-new", "P3: new nit")),
    "fixed-reversion": lambda sm: _recurring(sm, _new_product("fp-p3", "P3: nit"), prior="FIXED"),
    "trusted-l1-uncertain": lambda sm: _add(
        sm, _new_product("fp-unsure", "P3: unsure", Disposition.UNCERTAIN)
    ),
    "protocol-failure": _proto,
    "hold-fp": lambda sm: setattr(sm, "_hold_confirmed_fingerprints", ["held-fp"]),
    "recurring-p2": lambda sm: _recurring(sm, _new_product("fp-p2", "P2: bug")),
    "p3-distinct-over-threshold": _p3_over_threshold,
    "p3-density-over-threshold": _p3_density,
    "l2-infra-plus-protocol": lambda sm: (_proto(sm), setattr(sm, "_l2_infra_error", "constructed L2")),
    "coverage-cause-only": _swap_to_coverage,
    "companion-of-pass-without-schema-fail": _other_pass,
    "two-attempted-items": _two_attempts,
    "two-attempted-items-plus-rejection": lambda sm: (_two_attempts(sm), _with_rejection(sm)),
    # Deviation 7 (extended in fix6): empties the legacy fixture hand-set.
    "non-json-schema-fail-plus-rejection": lambda sm: (
        setattr(sm, "_attempted_last_round", []),
        _with_rejection(sm),
    ),
    "excerpt-rejection-same-pass": _with_rejection,
    "pass-in-unavailable-set": lambda sm: setattr(
        sm, "_unavailable_rejected_passes_last_round", {"adversarial"}
    ),
    "reviewed-repositories-set": lambda sm: setattr(sm, "reviewed_repositories", {"repo": "0" * 40}),
    "non-l1call-provider": lambda sm: setattr(sm, "l1_provider", lambda: ([], [], Usage(), 0.0)),
    "stale-prior-round-companion": _stale,
}


@pytest.mark.parametrize("case", list(_C3_NEGATIVES))
def test_c3_negative_beside_companion_keeps_reset(tmp_path, monkeypatch, case):
    """§4.2: every non-(iii) cause, and every narrow-scope gate, keeps the M6 reset."""
    sm, _, _ = _c3_machine(tmp_path, monkeypatch)
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET) is True
    _C3_NEGATIVES[case](sm)
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET) is False
    assert sm._c3_strip_fps == set()


def test_c3_companion_eligibility_is_per_pass(tmp_path, monkeypatch):
    """A rejection or second response on ANOTHER pass does not disqualify P."""
    sm, _, _ = _c3_machine(tmp_path, monkeypatch)
    # Deviation 7 (extended in fix6, CR2G-N1): edits the legacy _c3_machine
    # hand-set snapshot; this decision-level test never runs _execute_round.
    sm._attempted_last_round = sm._attempted_last_round + [{"pass_name": "qodo"}] * 2
    sm.l1_provider._excerpt_rejections = (_rejection(sm.l1_provider, "qodo"),)
    sm._unavailable_rejected_passes_last_round = {"expert"}
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET) is True
    assert sm._c3_strip_fps == {_C3_FP}


def _host_rows(sm, prior_dispositions=None):
    disps = prior_dispositions or {}
    rows = []
    for index in (0, 1):
        row = _earned_row(index, index + 1)
        row.update(source_hash=sm.source_hash, dispositions=dict(disps))
        rows.append(row)
    rows.append(
        {
            "round": 2,
            "source_hash": sm.source_hash,
            "reviewed_repositories": None,
            "clean_credit_action": "unavailable",
            "phase_status": _phases_returned(),
            "acquisition_failures": [],
            "reset_observed": False,
            "clean_rounds_after": 2,
        }
    )
    sm._state.round_history = rows
    sm._state.round = 2
    sm._state.consecutive_clean_rounds = 2


def _finish(sm, action):
    """Drive the real snapshot + writer for host attempt 2."""
    sm._host_attempt_round = 2
    sm._append_round_snapshot(2, [], list(sm._phase_findings["l1"]))
    sm._finish_host_attempt(action, _FixpointResult.RESET)
    return sm._state.round_history[-1]


def test_c3_writer_omits_companion_from_preserved_row(tmp_path, monkeypatch):
    """§4.4: F omitted from dispositions, cause kept, F still in l1_fingerprints; loads."""
    sm, _, _ = _c3_machine(tmp_path, monkeypatch)
    _host_rows(sm)
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET) is True
    row = _finish(sm, "preserved")
    assert row["clean_credit_action"] == "preserved"
    assert row["dispositions"] == {"schema-fail-adversarial": "CONFIRMED"}
    assert _C3_FP in row["l1_fingerprints"] and "schema-fail-adversarial" in row["l1_fingerprints"]
    assert sm._c3_strip_fps == set()
    validate_round_history(sm._state.round_history, 2)
    state = State(
        round=2,
        source_hash=sm.source_hash,
        round_history=sm._state.round_history,
        consecutive_clean_rounds=2,
    )
    assert earned_history_cycles(state, sm.source_hash, None) == [1, 2]


def test_c3_writer_keeps_sticky_confirmed_companion(tmp_path, monkeypatch):
    """§4.4: a companion promoted to CONFIRMED is not a companion; its fp stays."""
    comp = _companion(desc="P3: [adversarial] constructed candidate")
    comp.disposition = Disposition.CONFIRMED
    comp.severity = "P3"
    sm, _, _ = _c3_machine(tmp_path, monkeypatch, companions=[comp])
    _host_rows(sm, {_C3_FP: "CONFIRMED"})
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET) is True
    assert sm._c3_strip_fps == set()
    row = _finish(sm, "preserved")
    assert row["dispositions"] == {"schema-fail-adversarial": "CONFIRMED", _C3_FP: "CONFIRMED"}
    validate_round_history(sm._state.round_history, 2)


def test_c3_writer_keeps_same_fp_trusted_finding(tmp_path, monkeypatch):
    """§4.4: the state object at F is a trusted finding, so F is not stripped."""
    trusted = _new_product(_C3_FP, "P3: trusted nit")
    sm, schema, comps = _c3_machine(tmp_path, monkeypatch)
    # T16 (fix4): the companion and the trusted finding share F in state, so
    # the companion is eligible and only the single-object guard keeps F.
    sm._state.findings = [schema, *comps, trusted]
    sm._phase_findings["l1"] = [schema, *comps, trusted]
    assert sm._schema_fail_companions([schema]) == comps
    _host_rows(sm, {_C3_FP: "CONFIRMED"})
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET) is True
    assert sm._c3_strip_fps == set()
    row = _finish(sm, "preserved")
    assert row["dispositions"][_C3_FP] == "CONFIRMED"
    validate_round_history(sm._state.round_history, 2)


def test_c3_writer_leaves_reset_row_unchanged(tmp_path, monkeypatch):
    """§4.4: a non-preserved row keeps the companion fingerprint (TD-P6-C3a)."""
    sm, _, _ = _c3_machine(tmp_path, monkeypatch)
    _host_rows(sm)
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET) is True
    row = _finish(sm, "reset")
    assert row["dispositions"] == {"schema-fail-adversarial": "CONFIRMED", _C3_FP: "UNCERTAIN"}
    assert sm._c3_strip_fps == set()


def test_c3_begin_host_attempt_clears_strip_set(tmp_path, monkeypatch):
    sm, _, _ = _c3_machine(tmp_path, monkeypatch)
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET) is True
    assert sm._c3_strip_fps
    sm._state.round_history = []
    sm._state.earned_clean_window = {"reviewed_repositories": None}
    monkeypatch.setattr(sm, "_persist_state", lambda: None)
    sm._begin_host_attempt(0)
    assert sm._c3_strip_fps == set()


@pytest.mark.parametrize("resume", [False, True], ids=["in-memory", "resume-from-disk"])
def test_c3_trusted_first_confirmation_at_companion_fp_resets_next_round(tmp_path, monkeypatch, resume):
    """§4.5: preserved N omits F, so a trusted P3 first CONFIRMED at F in N+1 is new (clause a)."""
    sm, _, _ = _c3_machine(tmp_path, monkeypatch)
    _host_rows(sm)
    assert sm._schema_coverage_credit_preserve(_FixpointResult.RESET) is True
    _finish(sm, "preserved")
    if resume:
        path = tmp_path / "state.json"
        save_state(sm._state, path)
        loaded = load_state(path)
        sm = _guard_machine(tmp_path, monkeypatch)
        sm._state = loaded
        assert _C3_FP not in loaded.round_history[-1]["dispositions"]
    trusted = _new_product(_C3_FP, "P3: trusted nit")
    sm._state.findings = [trusted]
    sm._phase_findings = {"l1": [trusted]}
    sm._state.round = 3
    assert sm._fixpoint_reached() is _FixpointResult.RESET


def test_c3_loader_still_refuses_uncertain_companion_in_preserved_row():
    """§4.6: no loader relaxation; an unstripped preserved row is refused."""
    row = _m4_preserved(0, 0, {_C3_FP: "UNCERTAIN"})
    with pytest.raises(CorruptedStateError, match=_NOT_ONLY):
        validate_round_history([row])


def test_c3a_reset_row_seeds_ever_seen(tmp_path, monkeypatch):
    """T18 / TD-P6-C3a debt pin: a non-preserved (reset) row keeps the
    companion fp, so it seeds ever_seen and a trusted first confirmation at F
    in N+1 is NOT new under clause (a). Pinned as-is; not fixed by C3."""
    sm, _, _ = _c3_machine(tmp_path, monkeypatch)
    _host_rows(sm)
    monkeypatch.setattr(sm, "_persist_state", lambda: None)
    # The real reset order: credit is wiped, then the attempt is finalized.
    sm._state.earned_clean_window = {"reviewed_repositories": None}
    sm._host_attempt_round = 2
    sm._append_round_snapshot(2, [], list(sm._phase_findings["l1"]))
    sm._reset_clean_credit()
    sm._finish_host_attempt("reset", _FixpointResult.RESET)
    row = sm._state.round_history[-1]
    assert row["clean_credit_action"] == "reset" and row["dispositions"][_C3_FP] == "UNCERTAIN"
    validate_round_history(sm._state.round_history, 2)
    ever_seen = {fp for past in sm._state.round_history for fp in past.get("dispositions", {})}
    assert _C3_FP in ever_seen
    trusted = _new_product(_C3_FP, "P3: trusted nit")
    sm._state.findings = [trusted]
    sm._phase_findings = {"l1": [trusted]}
    sm._state.round = 3
    assert sm._fixpoint_reached() is not _FixpointResult.RESET


# ---------------------------------------------------------------------------
# C3 v3 (fix4) machine-level rule 2a/2b/2g and rule 3 tests. Every count and
# attempt enters through the real production _L1Call (its body, or the public
# recorders) and the machine's own snapshot point; no test sets
# `_responses_last_round` or `_attempted_last_round`.
# ---------------------------------------------------------------------------


def _c3v2_wrap_body(machine, after):
    """Run the real provider body, then apply `after(call)` to the producer."""
    provider = machine.l1_provider
    assert type(provider) is _L1Call
    original = provider._body

    def body(call):
        result = original(call)
        after(call)
        return result

    provider._body = body


def _c3v2_run_reset(root, monkeypatch, after, *, fault="schema-findings"):
    machine, calls = _sc._new_machine(root, monkeypatch, fault=fault)
    _c3v2_wrap_body(machine, after)
    seen = _sc._inject_gate_errors(monkeypatch, machine, [])
    assert machine.run() is not Verdict.PASS
    assert len(seen) == 1 and seen[0][0] is False, seen
    _sc._c3_companion(machine)
    state, _ = _sc._state(root)
    latest = state.round_history[-1]
    assert latest["clean_credit_action"] == "reset"
    return machine, latest


def _adversarial_attempts(call):
    return [a for a in call.attempted_excerpts if a.get("pass_name") == "adversarial"]


def test_c3v2_run_control_single_response_preserves(offline_workspace, monkeypatch):
    """Control for T10-T15: the same run without the extra effect preserves."""
    machine, calls = _sc._new_machine(offline_workspace, monkeypatch, fault="schema-findings")
    _c3v2_wrap_body(machine, lambda call: None)
    seen = _sc._inject_gate_errors(monkeypatch, machine, [])
    machine.run()
    assert seen[0][0] is True
    assert machine._responses_last_round == {"qodo": 1, "expert": 1, "adversarial": 1}
    assert _sc._state(offline_workspace)[0].round_history[-1]["clean_credit_action"] == "preserved"


def test_c3v2_rule2_count2_one_attempt_resets(offline_workspace, monkeypatch):
    """T10: two received responses for P, one attempted payload -> reset (2a)."""

    def after(call):
        assert len(_adversarial_attempts(call)) == 1
        call.record_reviewer_response("adversarial")

    machine, _ = _c3v2_run_reset(offline_workspace, monkeypatch, after)
    assert machine._responses_last_round["adversarial"] == 2


def test_c3v2_rule2_count1_zero_attempts_resets(offline_workspace, monkeypatch):
    """T11: one received response for P but no attempted payload -> reset (2b)."""

    def after(call):
        call.attempted_excerpts = [
            a for a in call.attempted_excerpts if a.get("pass_name") != "adversarial"
        ]

    machine, _ = _c3v2_run_reset(offline_workspace, monkeypatch, after)
    assert machine._responses_last_round["adversarial"] == 1


def test_c3v2_rule2_count1_dict_subclass_attempt_resets(offline_workspace, monkeypatch):
    """T12. Defense-in-depth, production-equivalent under 2a/2g.

    The real _L1Call records count 1 for P; P's only attempted item is a
    GroupedReviewAttempt (a dict subclass). The exact-dict filter resets."""
    from code_forge.reviewer_json import GroupedReviewAttempt, ReviewGroupScope

    def after(call):
        scope = ReviewGroupScope(name="g", diff_sha256="0" * 64, source_files=("control.ts",))
        call.attempted_excerpts = [
            GroupedReviewAttempt(a, scope) if a.get("pass_name") == "adversarial" else a
            for a in call.attempted_excerpts
        ]
        assert [type(a) for a in _adversarial_attempts(call)] == [GroupedReviewAttempt]

    machine, _ = _c3v2_run_reset(offline_workspace, monkeypatch, after)
    assert machine._responses_last_round["adversarial"] == 1


def _set_responses(value):
    def after(call):
        call.reviewer_responses = value(dict(call.reviewer_responses or {}))

    return after


_C3V2_MALFORMED = {
    "none": _set_responses(lambda r: None),
    "missing-key": _set_responses(lambda r: {k: v for k, v in r.items() if k != "adversarial"}),
    "zero": _set_responses(lambda r: {**r, "adversarial": 0}),
    "bool": _set_responses(lambda r: {**r, "adversarial": True}),
    "str": _set_responses(lambda r: {**r, "adversarial": "1"}),
    "negative": _set_responses(lambda r: {**r, "adversarial": -1}),
    "float": _set_responses(lambda r: {**r, "adversarial": 1.0}),
    "non-dict": _set_responses(lambda r: list(r.items())),
    "malformed-sibling": _set_responses(lambda r: {**r, "qodo": True}),
    "unknown-key": _set_responses(lambda r: {**r, "l2": 1}),
}


@pytest.mark.parametrize("case", [*_C3V2_MALFORMED, "never-recorded"])
def test_c3v2_rule2_count_none_or_malformed_resets(offline_workspace, monkeypatch, case):
    """T13: None, missing, 0, bool/str/negative/float or a non-dict -> reset.
    Set on the producer's reviewer_responses (or never recorded), never on
    the host snapshot; the machine's own capture validates it."""
    if case == "never-recorded":
        monkeypatch.setattr(_L1Call, "record_reviewer_response", lambda self, name: None)
        after = lambda call: None  # noqa: E731
    else:
        after = _C3V2_MALFORMED[case]
    machine, _ = _c3v2_run_reset(offline_workspace, monkeypatch, after)
    kept = {"zero": {"qodo": 1, "expert": 1, "adversarial": 0}, "missing-key": {"qodo": 1, "expert": 1}}
    assert machine._responses_last_round == kept.get(case)


def test_c3v2_snapshot_cleared_before_l1(offline_workspace, monkeypatch):
    """T14: round N captures a snapshot; L1 raises in N+1 -> snapshot None
    (cleared before L1, never carried over)."""
    machine, calls = _sc._new_machine(offline_workspace, monkeypatch, rounds=2)
    provider = machine.l1_provider
    original = provider._body
    at_l1 = []
    after_round = []
    gate = machine._receipt_gate_round_errors

    def errors():
        after_round.append(machine._responses_last_round)
        return gate()

    monkeypatch.setattr(machine, "_receipt_gate_round_errors", errors)

    def body(call):
        at_l1.append(machine._responses_last_round)
        if len(at_l1) == 2:
            raise RuntimeError("constructed L1 crash in round N+1")
        return original(call)

    provider._body = body
    with pytest.raises(RuntimeError, match="constructed L1 crash"):
        machine.run()
    assert after_round == [{"qodo": 1, "expert": 1, "adversarial": 1}]
    assert at_l1 == [None, None]
    assert machine._responses_last_round is None


def test_c3v2_rule3_injected_rejection_resets(offline_workspace, monkeypatch):
    """T15. Defense-in-depth, production-equivalent under 2a/2g.

    The real _L1Call records count 1 for P and, through the public
    record_excerpt_rejection, a real ExcerptEvidenceError bound to P's
    admissible attempted dict and the diff text. Rule 3 resets."""
    from code_forge.reviewer_json import ExcerptEvidenceError

    def after(call):
        (attempt,) = _adversarial_attempts(call)
        call.record_excerpt_rejection(
            ExcerptEvidenceError("constructed excerpt rejection"),
            "adversarial",
            attempt,
            diff_text=_sc.DIFF,
        )
        assert [r.pass_name for r in call._excerpt_rejections] == ["adversarial"]

    machine, _ = _c3v2_run_reset(offline_workspace, monkeypatch, after)
    assert machine._responses_last_round["adversarial"] == 1


def _c3v2_group_transport(monkeypatch, groups):
    """Grouped reviewer replies: excerpts only for the group's files; the
    adversarial pass of the control.ts group returns the schema-findings decoy."""

    def transport(prompt, *args, **kwargs):
        files = [name for name in _sc.CONTENT if f"diff --git a/{name}" in prompt]
        assert files and tuple(files) in groups
        body = {
            "findings": [],
            "code_excerpts": [
                {"file": name, "start_line": 1, "end_line": 3, "content": _sc.CONTENT[name]}
                for name in files
            ],
        }
        if _sc._reviewer_role(prompt) == "adversarial" and "control.ts" in files:
            body = _sc._apply_reviewer_fault(body, "schema-findings")
        return _sc.llm_invoke.LLMResult(body)

    monkeypatch.setattr(_sc.llm_invoke, "_invoke_api", transport)


def _c3v2_section(name):
    parts = _sc.DIFF.split("diff --git ")
    (part,) = [p for p in parts if p.startswith(f"a/{name} ")]
    return "diff --git " + part


@pytest.mark.parametrize(
    "groups", [(("control.ts",), ("other.ts",)), (("control.ts", "other.ts"),)], ids=["g2", "g1"]
)
def test_c3v2_grouped_resets(offline_workspace, monkeypatch, groups):
    """T4 (grouped probe, promoted): the real composite from
    build_grouped_l1_provider drives a real machine round. The composite's
    count is None (2g), so the round resets at G=2 and G=1."""
    from code_forge.backend import BackendConfig
    from code_forge.factories import build_grouped_l1_provider
    from code_forge.reviewer_json import GroupedReviewAttempt

    machine, _ = _sc._new_machine(offline_workspace, monkeypatch, fault="schema-findings")
    _c3v2_group_transport(monkeypatch, groups)
    backend = BackendConfig(
        name="constructed-offline",
        type="api",
        format="openai",
        model="offline",
        base_url="http://127.0.0.1:1",
        timeout_s=1,
    )
    specs = []
    for index, files in enumerate(groups):
        diff = "".join(_c3v2_section(name) for name in files)
        specs.append(
            {
                "name": f"g{index}",
                "conventions_digest": "",
                "split_context": "",
                "post_image": "".join(f"# {n}\n{_sc.CONTENT[n]}" for n in files),
                "resolved": ResolvedReview([Path(n) for n in files], None, diff, "git"),
            }
        )
    machine.l1_provider = build_grouped_l1_provider("auto", specs, backend=backend, max_attempts=1)
    assert type(machine.l1_provider) is _L1Call
    seen = _sc._inject_gate_errors(monkeypatch, machine, [])
    machine.run()
    assert machine._responses_last_round is None
    attempts = [a for a in machine._attempted_last_round if a.get("pass_name") == "adversarial"]
    assert [type(a) for a in attempts] == [GroupedReviewAttempt]
    _sc._c3_companion(machine)
    assert len(seen) == 1 and seen[0][0] is False
    state, _ = _sc._state(offline_workspace)
    assert state.round_history[-1]["clean_credit_action"] == "reset"
