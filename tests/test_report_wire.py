# SPDX-License-Identifier: Apache-2.0
"""IC-01 wire schema / reader stage tests (§3, §4, §5, §6.1)."""
from __future__ import annotations

import json

import pytest

from code_forge.disposition import Disposition
from code_forge.report_presentation import (
    FIELD_INVENTORY,
    PROJECTION_SCHEMA_V1,
    SCHEMA_V1,
    DiagnosticObservation,
    HostDiagnostic,
    ProductRecord,
    RecordView,
    ReportClassificationWire,
    WireClassificationError,
    WireFallbackRecord,
    build_renderer_fallback,
    parse_block_dict,
    parse_report_classification,
    serialize_projection,
    validate_block_dict,
    wire_to_json,
    _unavailable,
)
from code_forge.report_projection import classify_report_records
from code_forge.state import Mode, State, StateFinding, Verdict


def _sf(**kw) -> StateFinding:
    defaults = dict(
        id="f1",
        fingerprint="fp1",
        source="L1",
        disposition=Disposition.CONFIRMED,
        file="a.py",
        line_range=[1],
        description="d",
    )
    defaults.update(kw)
    return StateFinding(**defaults)  # type: ignore[arg-type]


def _empty_fields(**overrides):
    fields = {k: None for k in FIELD_INVENTORY}
    fields.update(
        id="f1",
        fingerprint="fp1",
        source="L1",
        disposition="CONFIRMED",
        file="a.py",
        line_range=[1],
        description="d",
        is_timeout=False,
    )
    fields.update(overrides)
    return fields


def _available_empty(**kw) -> dict:
    base = {
        "schema": SCHEMA_V1,
        "availability": "available",
        "unavailable_reason": None,
        "delivery_mode": "projection",
        "projection_schema": PROJECTION_SCHEMA_V1,
        "product": [],
        "execution": [],
        "gate": [],
        "quarantine": [],
        "unresolved_product_candidates": [],
        "classification_errors": [],
        "classification_complete": True,
        "product_list_count": 0,
        "unresolved_candidate_count": 0,
        "product_path_record_count": 0,
        "acquisition_complete": None,
        "acquisition_reason": "completion-not-provable-from-state",
        "persisted_pass_outcomes": [
            ["qodo", "completed"],
            ["expert", "completed"],
            ["adversarial", "completed"],
        ],
    }
    base.update(kw)
    return base


def test_unavailable_block_all_other_fields_null():
    for reason in (
        "no-report",
        "legacy-report",
        "invalid-report",
        "unsupported-schema",
        "invalid-block",
        "budget-exceeded",
    ):
        block = wire_to_json(_unavailable(reason))
        assert block["schema"] == SCHEMA_V1
        assert block["availability"] == "unavailable"
        assert block["unavailable_reason"] == reason
        for k, v in block.items():
            if k in ("schema", "availability", "unavailable_reason"):
                continue
            assert v is None, k
        assert validate_block_dict(block) is None


def test_reader_stages_no_report_legacy_invalid_unsupported_available():
    # empty
    assert (
        parse_report_classification(None, legacy_report_state="empty").unavailable_reason
        == "no-report"
    )
    # rejected
    assert (
        parse_report_classification(None, legacy_report_state="rejected").unavailable_reason
        == "invalid-report"
    )
    # accepted, no property → legacy-report
    run = {"results": [], "properties": {}}
    assert (
        parse_report_classification(run, legacy_report_state="accepted").unavailable_reason
        == "legacy-report"
    )
    # property not object
    run2 = {"properties": {"report_classification": "x"}}
    assert (
        parse_report_classification(run2, legacy_report_state="accepted").unavailable_reason
        == "invalid-block"
    )
    # unsupported schema
    run3 = {
        "properties": {
            "report_classification": {
                "schema": "forge-report-classification/v0",
                "availability": "unavailable",
                "unavailable_reason": "no-report",
            }
        }
    }
    assert (
        parse_report_classification(run3, legacy_report_state="accepted").unavailable_reason
        == "unsupported-schema"
    )
    # available valid (empty A3: P+U=pprc=len(results)=0)
    block = _available_empty()
    run4 = {"results": [], "properties": {"report_classification": block}}
    got = parse_report_classification(run4, legacy_report_state="accepted")
    assert got.availability == "available"
    assert got.product_list_count == 0


def test_reader_typeerror_on_bad_args():
    with pytest.raises(TypeError):
        parse_report_classification(None, legacy_report_state="accepted")
    with pytest.raises(TypeError):
        parse_report_classification({"x": 1}, legacy_report_state="empty")
    with pytest.raises(TypeError):
        parse_report_classification(None, legacy_report_state="nope")  # type: ignore[arg-type]


def test_coherent_product_with_associated_diagnostics():
    err = "falsify() raised: boom"
    ed = "downgrade-reason"
    fields = _empty_fields(
        disposition="UNCERTAIN",
        error=err,
        evidence_downgrade=ed,
        id="u1",
        fingerprint="ufp",
    )
    product_block = {
        "record": {"record_index": 0, "fields": fields},
        "associated_diagnostics": [
            {
                "kind": "persisted-falsify-error",
                "basis": "persisted-field",
                "observation_scope": "persisted-record-unbound",
                "text": err,
            },
            {
                "kind": "persisted-evidence-downgrade",
                "basis": "persisted-field",
                "observation_scope": "persisted-record-unbound",
                "text": ed,
            },
        ],
    }
    block = _available_empty(
        product=[],
        unresolved_product_candidates=[product_block],
        product_list_count=0,
        unresolved_candidate_count=1,
        product_path_record_count=1,
    )
    assert validate_block_dict(block) is None
    # Missing associated diagnostic → invalid
    bad = json.loads(json.dumps(block))
    bad["unresolved_product_candidates"][0]["associated_diagnostics"] = []
    assert validate_block_dict(bad) == "invalid-block"


def test_host_diagnostic_kinds():
    exec_h = {
        "record": {"record_index": 0, "fields": _empty_fields(id="e", fingerprint="e")},
        "diagnostic": {
            "kind": "provider-capacity",
            "basis": "inherited-predicate",
            "observation_scope": "persisted-record-unbound",
            "text": None,
        },
    }
    gate_h = {
        "record": {"record_index": 1, "fields": _empty_fields(id="g", fingerprint="g")},
        "diagnostic": {
            "kind": "receipt-audit",
            "basis": "inherited-predicate",
            "observation_scope": "persisted-record-unbound",
            "text": None,
        },
    }
    block = _available_empty(
        product=[],
        execution=[exec_h],
        gate=[gate_h],
        product_list_count=0,
        unresolved_candidate_count=0,
        product_path_record_count=0,
    )
    assert validate_block_dict(block) is None


def test_error_stage_code_correlation_and_quarantine():
    fb = {
        "record_index": 0,
        "safe_fields": {"id": "x"},
        "unavailable_fields": [k for k in FIELD_INVENTORY if k != "id"],
        "reason": "record-computation-fault",
    }
    err = {
        "record_index": 0,
        "stage": "record",
        "code": "record-computation-fault",
    }
    block = _available_empty(
        classification_complete=False,
        quarantine=[fb],
        classification_errors=[err],
        product_list_count=0,
        unresolved_candidate_count=0,
        product_path_record_count=0,
    )
    assert validate_block_dict(block) is None
    # Mismatched reason
    bad = json.loads(json.dumps(block))
    bad["quarantine"][0]["reason"] = "record-shape-invalid"
    assert validate_block_dict(bad) == "invalid-block"


def test_renderer_fallback_shape_from_phase1_projection():
    state = State(
        findings=[_sf()],
        mode=Mode.CI,
        verdict=Verdict.PASS,
    )
    proj = classify_report_records(state)
    fb = build_renderer_fallback(proj)
    assert fb.delivery_mode == "renderer-fallback"
    assert fb.product == ()
    assert fb.classification_complete is False
    assert fb.product_list_count is None
    assert fb.classification_errors[-1].stage == "render"
    assert fb.classification_errors[-1].code == "renderer-computation-fault"
    assert validate_block_dict(wire_to_json(fb)) is None


def test_serialize_projection_empty_from_phase1():
    proj = classify_report_records(State(findings=[], mode=Mode.CI, verdict=Verdict.PASS))
    wire = serialize_projection(proj)
    block = wire_to_json(wire)
    assert validate_block_dict(block) is None
    assert block["availability"] == "available"
    assert block["delivery_mode"] == "projection"
    assert block["classification_complete"] is True
    assert block["acquisition_reason"] == "completion-not-provable-from-state"
    assert block["persisted_pass_outcomes"] == [
        ["qodo", "completed"],
        ["expert", "completed"],
        ["adversarial", "completed"],
    ]


def test_counts_section5_empty_a3():
    block = _available_empty()
    assert validate_block_dict(block) is None
    typed = parse_block_dict(block)
    assert typed.product_list_count == 0
    assert typed.unresolved_candidate_count == 0
    assert typed.product_path_record_count == 0


def test_acquisition_derivation_fault_null_outcomes():
    block = _available_empty(
        acquisition_complete=None,
        acquisition_reason="acquisition-derivation-fault",
        persisted_pass_outcomes=None,
    )
    assert validate_block_dict(block) is None


def test_index_union_must_be_dense():
    p0 = {
        "record": {"record_index": 0, "fields": _empty_fields()},
        "associated_diagnostics": [],
    }
    p2 = {
        "record": {
            "record_index": 2,
            "fields": _empty_fields(id="f2", fingerprint="fp2"),
        },
        "associated_diagnostics": [],
    }
    block = _available_empty(
        product=[p0, p2],
        product_list_count=2,
        product_path_record_count=2,
    )
    assert validate_block_dict(block) == "invalid-block"


def _product_entry(index: int = 0, **field_overrides) -> dict:
    return {
        "record": {
            "record_index": index,
            "fields": _empty_fields(**field_overrides),
        },
        "associated_diagnostics": [],
    }


def test_cross_envelope_legacy1_new0_invalid_block():
    """§3.2: results len 1 but P=U=pprc=0 → invalid-block."""
    block = _available_empty()
    run = {
        "results": [{"ruleId": "x"}],
        "properties": {"report_classification": block},
    }
    got = parse_report_classification(run, legacy_report_state="accepted")
    assert got.availability == "unavailable"
    assert got.unavailable_reason == "invalid-block"
    # Structural validate alone still passes (cross-envelope is reader-only)
    assert validate_block_dict(block) is None


def test_cross_envelope_legacy0_new1_invalid_block():
    """§3.2: results=[] but counts claim one product-path record → invalid-block."""
    block = _available_empty(
        product=[_product_entry()],
        product_list_count=1,
        unresolved_candidate_count=0,
        product_path_record_count=1,
    )
    assert validate_block_dict(block) is None
    run = {"results": [], "properties": {"report_classification": block}}
    got = parse_report_classification(run, legacy_report_state="accepted")
    assert got.availability == "unavailable"
    assert got.unavailable_reason == "invalid-block"


def test_cross_envelope_normal_match_available():
    """§3.2: P+U=pprc=len(results) → available accepted."""
    block = _available_empty(
        product=[_product_entry()],
        product_list_count=1,
        unresolved_candidate_count=0,
        product_path_record_count=1,
    )
    assert validate_block_dict(block) is None
    run = {
        "results": [{"ruleId": "x"}],
        "properties": {"report_classification": block},
    }
    got = parse_report_classification(run, legacy_report_state="accepted")
    assert got.availability == "available"
    assert got.product_list_count == 1
    assert got.product_path_record_count == 1


def test_cross_envelope_excludes_nonempty_quarantine():
    """Record fallback with quarantine: cardinality does not apply."""
    fb = {
        "record_index": 0,
        "safe_fields": {"id": "x"},
        "unavailable_fields": [k for k in FIELD_INVENTORY if k != "id"],
        "reason": "record-computation-fault",
    }
    err = {
        "record_index": 0,
        "stage": "record",
        "code": "record-computation-fault",
    }
    # pprc=0 with results len 1 would mismatch if check applied; must stay available
    block = _available_empty(
        classification_complete=False,
        quarantine=[fb],
        classification_errors=[err],
        product_list_count=0,
        unresolved_candidate_count=0,
        product_path_record_count=0,
    )
    assert validate_block_dict(block) is None
    run = {
        "results": [{"ruleId": "x"}],
        "properties": {"report_classification": block},
    }
    got = parse_report_classification(run, legacy_report_state="accepted")
    assert got.availability == "available"
    assert got.delivery_mode == "projection"
    assert len(got.quarantine or ()) == 1


def test_cross_envelope_excludes_renderer_fallback():
    """renderer-fallback mode: no false invalid-block from cardinality."""
    state = State(
        findings=[_sf()],
        mode=Mode.CI,
        verdict=Verdict.PASS,
    )
    proj = classify_report_records(state)
    fb = build_renderer_fallback(proj)
    block = wire_to_json(fb)
    assert validate_block_dict(block) is None
    # results empty while quarantine has the finding — would mismatch if applied
    run = {"results": [], "properties": {"report_classification": block}}
    got = parse_report_classification(run, legacy_report_state="accepted")
    assert got.availability == "available"
    assert got.delivery_mode == "renderer-fallback"


def test_cross_envelope_excludes_projection_stage_global_error():
    """Global projection fallback (null counts + projection error): excluded."""
    fb = {
        "record_index": 0,
        "safe_fields": {"id": "x"},
        "unavailable_fields": [k for k in FIELD_INVENTORY if k != "id"],
        "reason": "projection-computation-fault",
    }
    err = {
        "record_index": None,
        "stage": "projection",
        "code": "projection-computation-fault",
    }
    block = _available_empty(
        classification_complete=False,
        product=[],
        execution=[],
        gate=[],
        unresolved_product_candidates=[],
        quarantine=[fb],
        classification_errors=[err],
        product_list_count=None,
        unresolved_candidate_count=None,
        product_path_record_count=None,
    )
    assert validate_block_dict(block) is None
    run = {
        "results": [{"ruleId": "x"}],
        "properties": {"report_classification": block},
    }
    got = parse_report_classification(run, legacy_report_state="accepted")
    assert got.availability == "available"
    assert got.product_list_count is None


def test_cross_envelope_host_only_empty_results_available():
    """Host-only: results=[] with P=U=pprc=0 and nonempty execution/gate → available."""
    exec_h = {
        "record": {"record_index": 0, "fields": _empty_fields(id="e", fingerprint="e")},
        "diagnostic": {
            "kind": "provider-capacity",
            "basis": "inherited-predicate",
            "observation_scope": "persisted-record-unbound",
            "text": None,
        },
    }
    gate_h = {
        "record": {"record_index": 1, "fields": _empty_fields(id="g", fingerprint="g")},
        "diagnostic": {
            "kind": "receipt-audit",
            "basis": "inherited-predicate",
            "observation_scope": "persisted-record-unbound",
            "text": None,
        },
    }
    block = _available_empty(
        product=[],
        execution=[exec_h],
        gate=[gate_h],
        product_list_count=0,
        unresolved_candidate_count=0,
        product_path_record_count=0,
    )
    assert validate_block_dict(block) is None
    run = {"results": [], "properties": {"report_classification": block}}
    got = parse_report_classification(run, legacy_report_state="accepted")
    assert got.availability == "available"
    assert got.product_list_count == 0
    assert len(got.execution or ()) == 1
    assert len(got.gate or ()) == 1



def test_cross_envelope_missing_results_invalid_block():
    """Accepted run without a list results under active preconditions → invalid-block."""
    block = _available_empty()
    run = {"properties": {"report_classification": block}}  # no results key
    got = parse_report_classification(run, legacy_report_state="accepted")
    assert got.availability == "unavailable"
    assert got.unavailable_reason == "invalid-block"


# ---------------------------------------------------------------------------
# Phase 5 WP2 — additive host_failure_kind on product/host wire objects
# ---------------------------------------------------------------------------


def _sf_wp2(**kw) -> StateFinding:
    defaults = dict(
        id="f1",
        fingerprint="fp1",
        source="L1",
        disposition=Disposition.CONFIRMED,
        file="a.py",
        line_range=[1],
        description="d",
    )
    defaults.update(kw)
    return StateFinding(**defaults)  # type: ignore[arg-type]


def test_wp2_serialize_projection_emits_host_failure_kind_labels():
    """X1/X2/S1/S2 → wire JSON carries correct host_failure_kind (「未经独立复现」).

    IC-07-RAO WP-R2: X1/S1 now emit projection.persisted_pass_outcomes under
    pass-outcome-not-completed so validate_block_dict succeeds (same bar as
    S2/X2). Labels remain on typed wire / JSON.
    """
    from code_forge.state import FindingDiagnosticKind

    # --- X1 LABEL-ONLY (product) ---
    x1 = _sf_wp2(
        id="l1-qodo-invoke-fail",
        fingerprint="invoke-fail-qodo",
        source="INFRA",
        file="<llm-invoke>",
        line_range=[0, 0],
        description="invoke fail",
        provider_failure={"kind": "conn"},
    )
    proj = classify_report_records(State(findings=[x1], mode=Mode.CI, verdict=Verdict.PASS))
    wire = serialize_projection(proj)
    assert len(wire.product) == 1
    assert wire.product[0].host_failure_kind == "PROVIDER_TRANSPORT"
    assert wire.execution == () and wire.gate == ()
    block = wire_to_json(wire)
    assert block["product"][0]["host_failure_kind"] == "PROVIDER_TRANSPORT"
    assert "host_failure_kind" in block["product"][0]
    assert "host_failure_kind" not in block["product"][0]["record"]["fields"]
    assert wire.acquisition_reason == "pass-outcome-not-completed"
    assert wire.persisted_pass_outcomes is not None
    assert any(o != "completed" for _, o in wire.persisted_pass_outcomes)
    assert validate_block_dict(block) is None

    # --- S1 capacity (execution) ---
    s1 = _sf_wp2(
        id="l1-adversarial-invoke-fail",
        fingerprint="invoke-fail-adversarial",
        source="INFRA",
        file="<llm-invoke>",
        line_range=[0, 0],
        description="capacity",
        diagnostic_kind=FindingDiagnosticKind.PROVIDER_CAPACITY,
        provider_failure={"kind": "truncated"},
    )
    proj = classify_report_records(State(findings=[s1], mode=Mode.CI, verdict=Verdict.PASS))
    wire = serialize_projection(proj)
    assert len(wire.execution) == 1
    assert wire.execution[0].host_failure_kind == "PROVIDER_CAPACITY"
    block = wire_to_json(wire)
    assert block["execution"][0]["host_failure_kind"] == "PROVIDER_CAPACITY"
    assert "host_failure_kind" in block["execution"][0]
    assert wire.acquisition_reason == "pass-outcome-not-completed"
    assert wire.persisted_pass_outcomes is not None
    assert any(o != "completed" for _, o in wire.persisted_pass_outcomes)
    assert validate_block_dict(block) is None

    # --- S2 capacity-incomplete (gate) — full validate ---
    s2 = _sf_wp2(
        id="RECEIPT_INVALID",
        fingerprint="receipt-abcdef012345",
        source="INFRA",
        file="<receipt-evidence>",
        line_range=[0, 0],
        description="capacity incomplete",
        diagnostic_kind=FindingDiagnosticKind.CAPACITY_INCOMPLETE,
    )
    proj = classify_report_records(State(findings=[s2], mode=Mode.CI, verdict=Verdict.PASS))
    block = wire_to_json(serialize_projection(proj))
    assert validate_block_dict(block) is None
    assert len(block["gate"]) == 1
    assert block["gate"][0]["host_failure_kind"] == "CAPACITY_INCOMPLETE"

    # --- X2 gate-label on product path (WP1) — full validate ---
    x2 = _sf_wp2(
        id="RECEIPT_INVALID",
        fingerprint="receipt-deadbeef0001",
        source="INFRA",
        file="<receipt-evidence>",
        line_range=[0, 0],
        description="generic receipt invalid",
    )
    proj = classify_report_records(State(findings=[x2], mode=Mode.CI, verdict=Verdict.PASS))
    wire = serialize_projection(proj)
    assert len(wire.product) == 1
    assert wire.product[0].host_failure_kind == "RECEIPT_INVALID_GATE"
    assert wire.gate == ()
    block = wire_to_json(wire)
    assert validate_block_dict(block) is None
    assert block["product"][0]["host_failure_kind"] == "RECEIPT_INVALID_GATE"
    assert block["product"][0]["host_failure_kind"] not in (
        "PROVIDER_CAPACITY",
        "CAPACITY_INCOMPLETE",
    )


def test_wp2_parse_missing_host_failure_kind_key_succeeds():
    """Old writer / old reader: product/host without the key still loads as None."""
    product_block = {
        "record": {
            "record_index": 0,
            "fields": _empty_fields(id="p", fingerprint="p", disposition="CONFIRMED"),
        },
        "associated_diagnostics": [],
        # deliberately omit host_failure_kind
    }
    exec_h = {
        "record": {"record_index": 1, "fields": _empty_fields(id="e", fingerprint="e")},
        "diagnostic": {
            "kind": "provider-capacity",
            "basis": "inherited-predicate",
            "observation_scope": "persisted-record-unbound",
            "text": None,
        },
        # omit host_failure_kind
    }
    block = _available_empty(
        product=[product_block],
        execution=[exec_h],
        gate=[],
        product_list_count=1,
        unresolved_candidate_count=0,
        product_path_record_count=1,
    )
    assert validate_block_dict(block) is None
    got = parse_block_dict(block)
    assert got.availability == "available"
    assert got.product[0].host_failure_kind is None
    assert got.execution[0].host_failure_kind is None


def test_wp2_parse_null_host_failure_kind_and_roundtrip():
    product_block = {
        "record": {
            "record_index": 0,
            "fields": _empty_fields(id="p", fingerprint="p", disposition="CONFIRMED"),
        },
        "associated_diagnostics": [],
        "host_failure_kind": None,
    }
    block = _available_empty(
        product=[product_block],
        product_list_count=1,
        unresolved_candidate_count=0,
        product_path_record_count=1,
    )
    assert validate_block_dict(block) is None
    got = parse_block_dict(block)
    assert got.product[0].host_failure_kind is None
    again = wire_to_json(got)
    assert again["product"][0]["host_failure_kind"] is None
    assert "host_failure_kind" in again["product"][0]


def test_wp2_invented_subtype_rejected():
    product_block = {
        "record": {
            "record_index": 0,
            "fields": _empty_fields(id="p", fingerprint="p", disposition="CONFIRMED"),
        },
        "associated_diagnostics": [],
        "host_failure_kind": "NOT_A_REAL_SUBTYPE",
    }
    block = _available_empty(
        product=[product_block],
        product_list_count=1,
        unresolved_candidate_count=0,
        product_path_record_count=1,
    )
    assert validate_block_dict(block) == "invalid-block"

    exec_h = {
        "record": {"record_index": 0, "fields": _empty_fields(id="e", fingerprint="e")},
        "diagnostic": {
            "kind": "provider-capacity",
            "basis": "inherited-predicate",
            "observation_scope": "persisted-record-unbound",
            "text": None,
        },
        "host_failure_kind": "RECEIPT_SCHEMA",  # DEFER'd — not in freeze emit set
    }
    block2 = _available_empty(
        product=[],
        execution=[exec_h],
        product_list_count=0,
        unresolved_candidate_count=0,
        product_path_record_count=0,
    )
    assert validate_block_dict(block2) == "invalid-block"


def test_wp2_freeze_set_values_accepted_on_product_and_host():
    from code_forge.report_presentation import HOST_FAILURE_KINDS

    for label in sorted(HOST_FAILURE_KINDS):
        product_block = {
            "record": {
                "record_index": 0,
                "fields": _empty_fields(id="p", fingerprint="p", disposition="CONFIRMED"),
            },
            "associated_diagnostics": [],
            "host_failure_kind": label,
        }
        block = _available_empty(
            product=[product_block],
            product_list_count=1,
            unresolved_candidate_count=0,
            product_path_record_count=1,
        )
        assert validate_block_dict(block) is None, label
        got = parse_block_dict(block)
        assert got.product[0].host_failure_kind == label


def test_wp2_x1_does_not_change_host_diagnostic_kinds():
    """X1 stays product path; no HostDiagnostic.diagnostic kind invented for X1."""
    finding = _sf_wp2(
        id="l1-qodo-invoke-fail",
        fingerprint="invoke-fail-qodo",
        source="INFRA",
        file="<llm-invoke>",
        line_range=[0, 0],
        description="invoke fail",
        provider_failure={"kind": "credentials"},
    )
    proj = classify_report_records(
        State(findings=[finding], mode=Mode.CI, verdict=Verdict.PASS)
    )
    wire = serialize_projection(proj)
    assert len(wire.product) == 1
    assert wire.product[0].host_failure_kind == "PROVIDER_AUTH"
    assert wire.execution == ()
    assert wire.gate == ()
    assert wire.product[0].associated_diagnostics == ()


def test_wp2_hand_built_labeled_product_roundtrips_through_parse():
    """Additive key on a valid available scaffold: emit → parse → re-emit."""
    product_block = {
        "record": {
            "record_index": 0,
            "fields": _empty_fields(id="p", fingerprint="p", disposition="CONFIRMED"),
        },
        "associated_diagnostics": [],
        "host_failure_kind": "PROVIDER_TRANSPORT",
    }
    exec_h = {
        "record": {"record_index": 1, "fields": _empty_fields(id="e", fingerprint="e")},
        "diagnostic": {
            "kind": "provider-capacity",
            "basis": "inherited-predicate",
            "observation_scope": "persisted-record-unbound",
            "text": None,
        },
        "host_failure_kind": "PROVIDER_CAPACITY",
    }
    gate_h = {
        "record": {"record_index": 2, "fields": _empty_fields(id="g", fingerprint="g")},
        "diagnostic": {
            "kind": "capacity-incomplete",
            "basis": "inherited-predicate",
            "observation_scope": "persisted-record-unbound",
            "text": None,
        },
        "host_failure_kind": "CAPACITY_INCOMPLETE",
    }
    block = _available_empty(
        product=[product_block],
        execution=[exec_h],
        gate=[gate_h],
        product_list_count=1,
        unresolved_candidate_count=0,
        product_path_record_count=1,
    )
    assert validate_block_dict(block) is None
    got = parse_block_dict(block)
    assert got.product[0].host_failure_kind == "PROVIDER_TRANSPORT"
    assert got.execution[0].host_failure_kind == "PROVIDER_CAPACITY"
    assert got.gate[0].host_failure_kind == "CAPACITY_INCOMPLETE"
    again = wire_to_json(got)
    assert again["product"][0]["host_failure_kind"] == "PROVIDER_TRANSPORT"
    assert again["execution"][0]["host_failure_kind"] == "PROVIDER_CAPACITY"
    assert again["gate"][0]["host_failure_kind"] == "CAPACITY_INCOMPLETE"


def test_wpr2_x1_s1_serialize_validates_with_real_outcomes():
    """WP-R2: X1/S1 serialize → RA-O-valid non-null outcomes (「未经独立复现」)."""
    from code_forge.state import FindingDiagnosticKind

    x1 = _sf_wp2(
        id="l1-qodo-invoke-fail",
        fingerprint="invoke-fail-qodo",
        source="INFRA",
        file="<llm-invoke>",
        line_range=[0, 0],
        description="invoke fail",
        provider_failure={"kind": "conn"},
    )
    proj = classify_report_records(State(findings=[x1], mode=Mode.CI, verdict=Verdict.PASS))
    wire = serialize_projection(proj)
    assert proj.persisted_pass_outcomes is not None
    assert wire.persisted_pass_outcomes == proj.persisted_pass_outcomes
    block = wire_to_json(wire)
    assert validate_block_dict(block) is None
    assert block["acquisition_reason"] == "pass-outcome-not-completed"
    assert any(o != "completed" for _, o in block["persisted_pass_outcomes"])

    s1 = _sf_wp2(
        id="l1-adversarial-invoke-fail",
        fingerprint="invoke-fail-adversarial",
        source="INFRA",
        file="<llm-invoke>",
        line_range=[0, 0],
        description="capacity",
        diagnostic_kind=FindingDiagnosticKind.PROVIDER_CAPACITY,
        provider_failure={"kind": "truncated"},
    )
    proj = classify_report_records(State(findings=[s1], mode=Mode.CI, verdict=Verdict.PASS))
    wire = serialize_projection(proj)
    block = wire_to_json(wire)
    assert validate_block_dict(block) is None
    assert block["execution"][0]["host_failure_kind"] == "PROVIDER_CAPACITY"
