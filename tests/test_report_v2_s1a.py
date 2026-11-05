# SPDX-License-Identifier: Apache-2.0
"""Offline S1a candidate checks, not integrated S1/consumer acceptance."""

import asyncio
import copy

import pytest

from code_forge import report_projection_v2 as v2
from code_forge.disposition import Disposition
from code_forge.report_projection import classify_report_records
from code_forge.report_presentation import serialize_projection, wire_to_json, validate_block_dict
from code_forge.report_presentation_v2 import serialize_report_projection_v2
from code_forge.state import State, StateFinding, Mode, Verdict, FindingDiagnosticKind


def finding(**changes):
    defaults = dict(
        id="product",
        fingerprint="product-fp",
        source="L1",
        disposition=Disposition.CONFIRMED,
        file="a.py",
        line_range=[1, 2],
        description="description",
    )
    return StateFinding(**(defaults | changes))


def mutation(**changes):
    return finding(
        **(
            dict(
                id="MUTATION_ERROR",
                fingerprint="mutation-evidence-error",
                source="INFRA",
                diagnostic_kind=FindingDiagnosticKind.MUTATION_INFRA,
            )
            | changes
        )
    )


def state(findings):
    return State(schema_version=1, mode=Mode.CI, verdict=Verdict.PASS, findings=findings)


def wire(snapshot):
    return wire_to_json(serialize_report_projection_v2(v2.classify_report_records_v2(snapshot)))


def test_mixed_occurrence_visibility_and_shadow():
    snapshot = state(
        [
            finding(),
            finding(disposition=Disposition.UNCERTAIN),
            mutation(),
            finding(description=None),
            mutation(),
        ]
    )
    before = copy.deepcopy(snapshot)
    legacy_before = wire_to_json(serialize_projection(classify_report_records(snapshot)))
    result = wire(snapshot)
    assert snapshot == before
    assert result["schema"] == "forge-report-classification/v2"
    assert [x["record"]["record_index"] for x in result["product"]] == [0]
    assert [x["record"]["record_index"] for x in result["unresolved_product_candidates"]] == [1]
    assert [x["record"]["record_index"] for x in result["execution"]] == [2, 4]
    assert [x["record_index"] for x in result["quarantine"]] == [3]
    assert result["classification_complete"] is False
    assert result["product_list_count"] == legacy_before["product_list_count"]
    assert result["unresolved_candidate_count"] == legacy_before["unresolved_candidate_count"]
    assert result["product_path_record_count"] == legacy_before["product_path_record_count"]
    for key in ("acquisition_complete", "acquisition_reason", "persisted_pass_outcomes"):
        assert result[key] == legacy_before[key]
    assert wire_to_json(serialize_projection(classify_report_records(snapshot))) == legacy_before
    for item in result["execution"]:
        assert item["diagnostic"] == dict(
            kind="mutation-infra",
            basis="inherited-predicate",
            observation_scope="persisted-record-unbound",
            text=None,
        )
        assert item["host_failure_kind"] is None
    result["execution"][0]["record"]["fields"]["line_range"].append(999)
    assert snapshot == before
    assert validate_block_dict(result) == "unsupported-schema"


def test_pure_mutation_complete_but_no_execution_success_claim():
    result = wire(state([mutation()]))
    assert result["classification_complete"] is True
    assert result["classification_errors"] == []
    assert result["quarantine"] == []
    assert result["product_list_count"] == result["unresolved_candidate_count"] == 0
    assert result["acquisition_complete"] is not True


@pytest.mark.parametrize(
    "changes",
    [
        dict(id="other"),
        dict(fingerprint="other"),
        dict(source="L2"),
        dict(diagnostic_kind="mutation-infra"),
        dict(diagnostic_kind=None),
    ],
)
def test_exact_predicate_lookalikes_not_promoted(changes):
    result = wire(state([mutation(**changes)]))
    assert not any(x["diagnostic"]["kind"] == "mutation-infra" for x in result["execution"])


def test_malformed_eligible_record_remains_quarantined():
    result = wire(state([mutation(description=None)]))
    assert result["execution"] == []
    assert [x["record_index"] for x in result["quarantine"]] == [0]
    assert result["classification_complete"] is False


def test_global_fault_does_not_promote_mutation(monkeypatch):
    def fail(_):
        raise v2.ProjectionComputationFault("private detail")

    monkeypatch.setattr(v2, "reporting_product_findings", fail)
    result = wire(state([finding(), mutation()]))
    assert result["execution"] == result["product"] == []
    assert len(result["quarantine"]) == 2
    assert result["classification_complete"] is False
    assert "private detail" not in str(result)


@pytest.mark.parametrize(
    "error", [MemoryError, RecursionError, asyncio.CancelledError, TypeError, ValueError]
)
def test_critical_errors_propagate(monkeypatch, error):
    def fail(_):
        raise error()

    monkeypatch.setattr(v2, "reporting_product_findings", fail)
    with pytest.raises(error):
        wire(state([mutation()]))


def test_v1_input_not_accepted_by_v2_serializer():
    with pytest.raises(TypeError):
        serialize_report_projection_v2(classify_report_records(state([])))


def test_repeated_identical_object_occurrences_remain_visible():
    item = mutation()
    result = wire(state([item, item]))
    assert [x["record"]["record_index"] for x in result["execution"]] == [0, 1]


@pytest.mark.parametrize("helper", ["_safe_str", "_safe_line_range", "_safe_disposition_value"])
@pytest.mark.parametrize("error", [MemoryError, RecursionError, asyncio.CancelledError])
def test_fallback_helpers_never_hide_critical_fault(monkeypatch, helper, error):
    def fail(_):
        raise error()

    monkeypatch.setattr(v2, helper, fail)
    with pytest.raises(error):
        wire(state([mutation(description=None)]))


@pytest.mark.parametrize(
    "disposition,channel",
    [
        (Disposition.CONFIRMED, "product"),
        (Disposition.UNCERTAIN, "unresolved_product_candidates"),
    ],
)
@pytest.mark.parametrize("shared", [False, True])
def test_product_occurrences_survive_aliases_and_duplicate_ids(disposition, channel, shared):
    item = finding(disposition=disposition)
    other = item if shared else copy.deepcopy(item)
    result = wire(state([item, other]))
    assert [x["record"]["record_index"] for x in result[channel]] == [0, 1]
    assert result["quarantine"] == []
    assert result["product_path_record_count"] == 2


@pytest.mark.parametrize(
    "case,expected_shapes,expected_quarantine",
    [
        ([], [], 0),
        ([finding()], ["S0"], 0),
        ([finding(disposition=Disposition.UNCERTAIN)], ["S0"], 0),
        ([finding(disposition=Disposition.UNCERTAIN, error="falsify() raised: synthetic")], ["S4"], 0),
        (
            [finding(disposition=Disposition.UNCERTAIN, evidence_downgrade="synthetic downgrade")],
            ["S5"],
            0,
        ),
        (
            [
                finding(
                    disposition=Disposition.UNCERTAIN,
                    error="falsify() raised: synthetic",
                    evidence_downgrade="synthetic downgrade",
                )
            ],
            ["S4+S5"],
            0,
        ),
        ([finding(description=None)], [], 1),
        (
            [
                finding(
                    id="l1-adversarial-invoke-fail",
                    fingerprint="invoke-fail-adversarial",
                    source="INFRA",
                    file="<llm-invoke>",
                    line_range=[0, 0],
                    diagnostic_kind=FindingDiagnosticKind.PROVIDER_CAPACITY,
                    provider_failure={"kind": "truncated"},
                )
            ],
            ["S1"],
            0,
        ),
    ],
)
def test_nonmutation_full_wire_differential(case, expected_shapes, expected_quarantine):
    snapshot = state(case)
    legacy_projection = classify_report_records(snapshot)
    projection = v2.classify_report_records_v2(snapshot)
    for projected in (legacy_projection, projection):
        records = (
            *projected.product,
            *projected.unresolved_product_candidates,
            *projected.execution,
            *projected.gate,
        )
        assert [record.shape for record in records] == expected_shapes
        assert len(projected.quarantine) == expected_quarantine
    old = wire_to_json(serialize_projection(legacy_projection))
    new = wire(snapshot)
    assert new.pop("schema") == "forge-report-classification/v2"
    assert new.pop("projection_schema") == "forge-report-projection-proposal-v2"
    old.pop("schema")
    old.pop("projection_schema")
    assert new == old


def test_real_canonical_reload_is_read_only_and_duplicate_occurrences_survive(tmp_path):
    from code_forge.sarif import format_summary, _build_run
    from code_forge.state import load_state, save_state

    item = finding()
    path = tmp_path / "state.json"
    save_state(state([item, item, mutation()]), path)
    before_bytes = path.read_bytes()
    loaded = load_state(path)
    assert loaded is not None
    assert loaded.findings[0] is not loaded.findings[1]
    before_state = copy.deepcopy(loaded)
    before_run = _build_run(loaded, {}, "test")
    before_summary = format_summary(loaded)
    old = wire_to_json(serialize_projection(classify_report_records(loaded)))
    result = wire(loaded)
    assert [x["record"]["record_index"] for x in result["product"]] == [0, 1]
    assert [x["record"]["record_index"] for x in old["product"]] == [0, 1]
    assert [x["record"]["record_index"] for x in result["execution"]] == [2]
    assert loaded == before_state
    assert path.read_bytes() == before_bytes
    assert _build_run(loaded, {}, "test") == before_run
    assert format_summary(loaded) == before_summary


@pytest.mark.parametrize("error", [MemoryError, RecursionError])
def test_historical_v1_fallback_characterization_not_v2_policy(error):
    from code_forge import report_projection as legacy

    class BrokenField:
        @property
        def id(self):
            raise error("injected")

    assert legacy._fallback_record(0, BrokenField()).finding_id is None


@pytest.mark.parametrize("error", [MemoryError, RecursionError])
def test_historical_v1_outer_boundary_propagates(monkeypatch, error):
    from code_forge import report_projection as legacy

    def fail(_):
        raise error("injected")

    monkeypatch.setattr(legacy, "reporting_product_findings", fail)
    with pytest.raises(error):
        legacy.classify_report_records(state([]))


def assert_nonmutation_shape_and_full_wire(record, shape, partition, host_kind):
    from code_forge.report_projection_check_v2 import exact_json_equal

    snapshot = state([record])
    before = copy.deepcopy(snapshot)
    old_projection = classify_report_records(snapshot)
    new_projection = v2.classify_report_records_v2(snapshot)
    for projected in (old_projection, new_projection):
        actual = getattr(projected, partition)
        assert len(actual) == 1
        assert (actual[0].index, actual[0].shape, actual[0].host_failure_kind) == (0, shape, host_kind)
        for other in ("product", "unresolved_product_candidates", "execution", "gate", "quarantine"):
            if other != partition:
                assert getattr(projected, other) == ()
    old_wire = wire_to_json(serialize_projection(old_projection))
    new_wire = wire_to_json(serialize_report_projection_v2(new_projection))
    assert old_wire.pop("schema") == "forge-report-classification/v1"
    assert new_wire.pop("schema") == "forge-report-classification/v2"
    assert old_wire.pop("projection_schema") == "forge-report-projection-proposal-v1"
    assert new_wire.pop("projection_schema") == "forge-report-projection-proposal-v2"
    assert exact_json_equal(old_wire, new_wire)
    assert snapshot == before


@pytest.mark.parametrize("pass_name", ["qodo", "expert", "adversarial"])
@pytest.mark.parametrize("failure,file", [("invoke", "<llm-invoke>"), ("spawn", "<spawn>")])
@pytest.mark.parametrize("subtype", [None, "RECEIPT_SCHEMA", "RECEIPT_COVERAGE", "RECEIPT_EVIDENCE"])
def test_s1_capacity_full_wire_matrix(pass_name, failure, file, subtype):
    record = finding(
        id=f"l1-{pass_name}-{failure}-fail",
        fingerprint=f"{failure}-fail-{pass_name}",
        source="INFRA",
        file=file,
        line_range=[0, 0],
        diagnostic_kind=FindingDiagnosticKind.PROVIDER_CAPACITY,
        provider_failure={"kind": "truncated"},
        receipt_subtype=subtype,
    )
    assert_nonmutation_shape_and_full_wire(record, "S1", "execution", "PROVIDER_CAPACITY")


@pytest.mark.parametrize(
    "subtype,expected",
    [
        (None, "CAPACITY_INCOMPLETE"),
        ("RECEIPT_SCHEMA", "RECEIPT_SCHEMA"),
        ("RECEIPT_COVERAGE", "RECEIPT_COVERAGE"),
        ("RECEIPT_EVIDENCE", "RECEIPT_EVIDENCE"),
        ("unknown", "CAPACITY_INCOMPLETE"),
    ],
)
@pytest.mark.parametrize("provider_kind", [None, "truncated", "conn"])
def test_s2_receipt_capacity_subtype_matrix(subtype, expected, provider_kind):
    record = finding(
        id="RECEIPT_INVALID",
        fingerprint="receipt-abcdef012345",
        source="INFRA",
        file="<receipt-evidence>",
        line_range=[0, 0],
        diagnostic_kind=FindingDiagnosticKind.CAPACITY_INCOMPLETE,
        receipt_subtype=subtype,
        provider_failure=None if provider_kind is None else {"kind": provider_kind},
    )
    assert_nonmutation_shape_and_full_wire(record, "S2", "gate", expected)


@pytest.mark.parametrize("subtype", [None, "RECEIPT_SCHEMA", "RECEIPT_COVERAGE", "RECEIPT_EVIDENCE"])
@pytest.mark.parametrize(
    "provider_kind,expected", [(None, None), ("truncated", None), ("conn", "PROVIDER_TRANSPORT")]
)
def test_s3_receipt_audit_preserves_existing_label_semantics(subtype, provider_kind, expected):
    # Existing v1 applies the provider label even to this audit record when
    # diagnostic_kind is absent. Preserve that label-only behavior, not policy.
    record = finding(
        id="RECEIPT_UNTRUSTED",
        fingerprint="audit",
        source="UNTRUSTED",
        disposition=Disposition.UNCERTAIN,
        receipt_subtype=subtype,
        provider_failure=None if provider_kind is None else {"kind": provider_kind},
    )
    assert_nonmutation_shape_and_full_wire(record, "S3", "gate", expected)


@pytest.mark.parametrize(
    "provider_kind,expected",
    [
        ("conn", "PROVIDER_TRANSPORT"),
        ("credentials", "PROVIDER_AUTH"),
        ("empty", "PROVIDER_EMPTY"),
        ("stub_model", "PROVIDER_EMPTY"),
        ("no_json", "PROVIDER_PROTOCOL"),
        ("sse_body", "PROVIDER_PROTOCOL"),
        ("bad_body", "PROVIDER_PROTOCOL"),
        ("protocol", "PROVIDER_PROTOCOL"),
        ("truncated", None),
        ("spawn", None),
        ("timeout", None),
        ("unknown", None),
        ("no-json", None),
    ],
)
def test_s0_provider_subtype_full_wire_matrix(provider_kind, expected):
    record = finding(
        id="l1-qodo-invoke-fail",
        fingerprint="invoke-fail-qodo",
        source="INFRA",
        file="<llm-invoke>",
        line_range=[0, 0],
        provider_failure={"kind": provider_kind},
        receipt_subtype="RECEIPT_SCHEMA",
    )
    assert_nonmutation_shape_and_full_wire(record, "S0", "product", expected)


@pytest.mark.parametrize(
    "subtype,expected",
    [
        (None, "RECEIPT_INVALID_GATE"),
        ("RECEIPT_SCHEMA", "RECEIPT_SCHEMA"),
        ("RECEIPT_COVERAGE", "RECEIPT_COVERAGE"),
        ("RECEIPT_EVIDENCE", "RECEIPT_EVIDENCE"),
    ],
)
@pytest.mark.parametrize("provider_kind", [None, "truncated", "conn"])
def test_s0_receipt_integrity_priority_full_wire_matrix(subtype, expected, provider_kind):
    record = finding(
        id="RECEIPT_INVALID",
        fingerprint="receipt-abcdef012345",
        source="INFRA",
        file="<receipt-evidence>",
        line_range=[0, 0],
        receipt_subtype=subtype,
        provider_failure=None if provider_kind is None else {"kind": provider_kind},
    )
    assert_nonmutation_shape_and_full_wire(record, "S0", "product", expected)


@pytest.mark.parametrize("pass_name", ["qodo", "expert", "adversarial"])
@pytest.mark.parametrize(
    "suffix,file,subtype",
    [
        ("schema-fail", "<schema-validation>", "RECEIPT_SCHEMA"),
        ("incomplete-coverage", "<coverage-guard>", "RECEIPT_COVERAGE"),
    ],
)
@pytest.mark.parametrize("match_subtype", [True, False])
def test_producer_receipt_subtype_priority_matrix(pass_name, suffix, file, subtype, match_subtype):
    record = finding(
        id=f"l1-{pass_name}-{suffix}",
        fingerprint=f"{suffix}-{pass_name}",
        source="INFRA",
        file=file,
        line_range=[0, 0],
        receipt_subtype=subtype if match_subtype else "RECEIPT_EVIDENCE",
        provider_failure={"kind": "conn"},
    )
    assert_nonmutation_shape_and_full_wire(
        record, "S0", "product", subtype if match_subtype else "PROVIDER_TRANSPORT"
    )
