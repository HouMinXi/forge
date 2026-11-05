# SPDX-License-Identifier: Apache-2.0
"""Original-byte reader controls
local candidate contracts, not consumer tests."""

import copy
import json

import pytest

from code_forge.report_reader_v2 import ReaderLimits, read_report_view

LIMITS = ReaderLimits(1_000_000, 64, 100)


def block(version="v2"):
    # Handwritten independently of the production serializer.
    return dict(
        schema=f"forge-report-classification/{version}",
        availability="available",
        unavailable_reason=None,
        delivery_mode="projection",
        projection_schema=f"forge-report-projection-proposal-{version}",
        product=[],
        execution=[],
        gate=[],
        quarantine=[],
        unresolved_product_candidates=[],
        classification_errors=[],
        classification_complete=True,
        product_list_count=0,
        unresolved_candidate_count=0,
        product_path_record_count=0,
        acquisition_complete=None,
        acquisition_reason="completion-not-provable-from-state",
        persisted_pass_outcomes=[
            ["qodo", "completed"],
            ["expert", "completed"],
            ["adversarial", "completed"],
        ],
    )


def document(selected=None):
    return {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "code-forge"}},
                "results": [],
                "properties": {"report_classification": block() if selected is None else selected},
            }
        ],
    }


def raw(doc):
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def read(value, *, requested="v2", limits=LIMITS):
    return read_report_view(value, requested_version=requested, limits=limits)


def mutation_block():
    result = block()
    fields = dict(
        id="MUTATION_ERROR",
        fingerprint="mutation-evidence-error",
        source="INFRA",
        disposition="CONFIRMED",
        file="<mutation>",
        line_range=[0, 0],
        description="failed",
        error=None,
        anchor=None,
        evidence_files=None,
        is_timeout=None,
        backend=None,
        severity=None,
        excerpt=None,
        falsify_reasoning=None,
        evidence_downgrade=None,
        diagnostic_kind=None,
        provider_failure=None,
    )
    result["execution"] = [
        dict(
            record=dict(record_index=0, fields=fields),
            host_failure_kind=None,
            diagnostic=dict(
                kind="mutation-infra",
                basis="inherited-predicate",
                observation_scope="persisted-record-unbound",
                text=None,
            ),
        )
    ]
    return result


@pytest.mark.parametrize(
    "requested,version,selected,reason",
    [
        ("v1", "v1", "v1", "selected"),
        ("v2", "v1", "v1", "v2-absent"),
        ("v2", "v2", "v2", "selected"),
        ("v1", "v2", None, "requested-version-unavailable"),
    ],
)
def test_version_matrix(requested, version, selected, reason):
    result = read(raw(document(block(version))), requested=requested)
    assert (result.selected_version, result.selection_reason) == (selected, reason)


def test_valid_unavailable_is_selected_not_successful_empty():
    value = block()
    for key in value:
        if key not in ("schema", "availability", "unavailable_reason"):
            value[key] = None
    value.update(availability="unavailable", unavailable_reason="budget-exceeded")
    result = read(raw(document(value)))
    assert result.selected_version == "v2"
    assert result.block == value
    assert result.block["availability"] == "unavailable"


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (lambda d: d["runs"][0]["properties"].pop("report_classification"), "missing-block"),
        (lambda d: d["runs"][0]["properties"].update(report_classification=None), "null-block"),
        (lambda d: d["runs"][0]["properties"].update(report_classification=[]), "invalid-block"),
        (
            lambda d: d["runs"][0]["properties"]["report_classification"].update(schema="unknown"),
            "unsupported-schema",
        ),
        (lambda d: d.update(version="1.0"), "invalid-envelope"),
        (lambda d: d["runs"].append(copy.deepcopy(d["runs"][0])), "invalid-envelope"),
        (lambda d: d["runs"][0]["tool"]["driver"].update(name="other"), "invalid-envelope"),
        (lambda d: d["runs"][0].update(results=[1]), "invalid-envelope"),
        (lambda d: d["runs"][0]["properties"].update(receiptAudit=[1]), "invalid-envelope"),
        (lambda d: d["runs"][0].update(results=[{}]), "invalid-block"),
    ],
)
def test_envelope_and_selection_failures(mutation, reason):
    doc = document()
    mutation(doc)
    result = read(raw(doc))
    assert result.selected_version is None
    assert result.selection_reason == reason


@pytest.mark.parametrize(
    "value,reason",
    [
        (b"", "no-report"),
        (b"\xff", "invalid-utf8"),
        (b"{", "invalid-json"),
        (b'{"x":1,"x":2}', "duplicate-key"),
        (b'{"x":NaN}', "nonfinite-number"),
        (b'{"x":Infinity}', "nonfinite-number"),
        (b'{"x":1e999}', "nonfinite-number"),
        (b'{"\\ud800":1}', "invalid-json"),
        (b'{"x":"\\udfff"}', "invalid-json"),
    ],
)
def test_original_bytes_refusals(value, reason):
    assert read(value).selection_reason == reason


@pytest.mark.parametrize("text", ["\ufffd", "\U0001f642", '[\\"{}]'])
def test_valid_unicode_and_quoted_brackets(text):
    doc = document()
    doc["note"] = text
    assert read(raw(doc)).selected_version == "v2"


def test_byte_boundary_and_independent_unicode_accounting():
    doc = document()
    doc["note"] = "汉字"
    value = raw(doc)
    assert len(value) > len(value.decode())
    for adjustment, accepted in [(-1, False), (0, True), (1, True)]:
        result = read(value, limits=ReaderLimits(len(value) + adjustment, 64, 100))
        assert (result.selected_version == "v2") is accepted


@pytest.mark.parametrize("limit,accepted", [(2, False), (3, True), (4, True)])
def test_depth_exact_count_including_empty_containers(limit, accepted):
    value = b'{"a":[{}]}'
    result = read(value, limits=ReaderLimits(100, limit, 10))
    assert (result.selection_reason != "depth-limit") is accepted


@pytest.mark.parametrize("index", [-1, True, 0.0, 1, 10**80])
@pytest.mark.parametrize("version", ["v1", "v2"])
def test_invalid_indexes_bounded_before_legacy_validator(index, version):
    value = mutation_block()
    value["schema"] = f"forge-report-classification/{version}"
    value["projection_schema"] = f"forge-report-projection-proposal-{version}"
    value["execution"][0]["record"]["record_index"] = index
    assert read(raw(document(value))).selection_reason == "invalid-block"


def test_zero_index_is_valid_and_record_limit_counts_occurrences():
    value = mutation_block()
    assert read(raw(document(value))).selected_version == "v2"
    value["execution"].append(copy.deepcopy(value["execution"][0]))
    value["execution"][1]["record"]["record_index"] = 1
    assert (
        read(raw(document(value)), limits=ReaderLimits(10000, 64, 1)).selection_reason == "record-limit"
    )
    assert read(raw(document(value)), limits=ReaderLimits(10000, 64, 2)).selected_version == "v2"
    value["execution"][1]["record"]["record_index"] = 0
    assert read(raw(document(value))).selection_reason == "invalid-block"


@pytest.mark.parametrize(
    "location,key,value",
    [
        ("diagnostic", "kind", "future-kind"),
        ("diagnostic", "basis", "trusted"),
        ("diagnostic", "observation_scope", "current-invocation"),
        ("diagnostic", "text", "oops"),
        ("diagnostic", "annotation", "not-approved"),
        ("host", "host_failure_kind", "PROVIDER_AUTH"),
        ("fields", "id", "other"),
        ("fields", "fingerprint", "other"),
        ("fields", "source", "L1"),
    ],
)
def test_mutation_closed_semantics(location, key, value):
    candidate = mutation_block()
    host = candidate["execution"][0]
    target = (
        host
        if location == "host"
        else host["record"]["fields"]
        if location == "fields"
        else host["diagnostic"]
    )
    target[key] = value
    assert read(raw(document(candidate))).selection_reason == "invalid-block"


@pytest.mark.parametrize("bad", [True, 0, -1, 1.0])
def test_limits_require_positive_exact_integers(bad):
    with pytest.raises(ValueError):
        ReaderLimits(bad, 10, 10)


def test_programming_misuse_raises():
    with pytest.raises(TypeError):
        read("{}")
    with pytest.raises(ValueError):
        read(b"{}", requested="v3")


def test_real_projection_serializer_sarif_bytes_reader():
    from code_forge.state import State, StateFinding, FindingDiagnosticKind, Mode
    from code_forge.disposition import Disposition
    from code_forge.sarif import build_sarif_log
    from code_forge.report_projection_v2 import classify_report_records_v2
    from code_forge.report_presentation_v2 import serialize_report_projection_v2
    from code_forge.report_presentation import wire_to_json

    snapshot = State(
        mode=Mode.CI,
        findings=[
            StateFinding(
                "MUTATION_ERROR",
                "mutation-evidence-error",
                "INFRA",
                Disposition.CONFIRMED,
                "<mutation>",
                [0, 0],
                "failed",
                diagnostic_kind=FindingDiagnosticKind.MUTATION_INFRA,
            )
        ],
    )
    doc = build_sarif_log(snapshot, tool_versions={}, forge_version="test")
    candidate = wire_to_json(serialize_report_projection_v2(classify_report_records_v2(snapshot)))
    doc["runs"][0].setdefault("properties", {})["report_classification"] = candidate
    result = read(raw(doc))
    assert result.selected_version == "v2"
    assert result.block == candidate


def test_invalid_envelope_and_huge_index_never_reach_v1_validator(monkeypatch):
    from code_forge import report_reader_v2 as reader

    def forbidden(_):
        pytest.fail("unsafe legacy validation reached")

    monkeypatch.setattr(reader, "validate_block_dict", forbidden)
    doc = document(block("v1"))
    doc["version"] = "wrong"
    assert read(raw(doc)).selection_reason == "invalid-envelope"
    candidate = mutation_block()
    candidate["schema"] = "forge-report-classification/v1"
    candidate["execution"][0]["record"]["record_index"] = 10**80
    assert read(raw(document(candidate))).selection_reason == "invalid-block"


@pytest.mark.parametrize("error", [MemoryError, TypeError])
def test_critical_and_programming_faults_not_refused(monkeypatch, error):
    from code_forge import report_reader_v2 as reader

    def fail(*args, **kwargs):
        raise error()

    monkeypatch.setattr(reader.json, "loads", fail)
    with pytest.raises(error):
        read(b"{}")


def test_closed_top_level_and_bool_count():
    for changed in (dict(note="unapproved"), dict(product_list_count=False)):
        candidate = block()
        candidate.update(changed)
        assert read(raw(document(candidate))).selection_reason == "invalid-block"


def test_scalar_valid_surrogate_pair_and_unpaired_extension():
    value = raw(document())
    assert read(value[:-1] + b',"extra":"\\ud83d\\ude42"}').selected_version == "v2"
    assert read(value[:-1] + b',"extra":"\\ud800"}').selection_reason == "invalid-json"


def test_real_mixed_occurrences_round_trip_and_conditional_cardinality():
    from code_forge.state import State, StateFinding, FindingDiagnosticKind, Mode
    from code_forge.disposition import Disposition
    from code_forge.sarif import build_sarif_log
    from code_forge.report_projection_v2 import classify_report_records_v2
    from code_forge.report_presentation_v2 import serialize_report_projection_v2
    from code_forge.report_presentation import wire_to_json

    product = StateFinding("same", "p", "L1", Disposition.CONFIRMED, "a.py", [1, 1], "product")
    unresolved = StateFinding("same", "u", "L1", Disposition.UNCERTAIN, "a.py", [1, 1], "unresolved")
    mutation = StateFinding(
        "MUTATION_ERROR",
        "mutation-evidence-error",
        "INFRA",
        Disposition.CONFIRMED,
        "<mutation>",
        [0, 0],
        "failed",
        diagnostic_kind=FindingDiagnosticKind.MUTATION_INFRA,
    )
    quarantine = StateFinding("bad", "bad", "L1", Disposition.CONFIRMED, "a.py", [1, 1], None)
    snapshot = State(mode=Mode.CI, findings=[product, unresolved, mutation, quarantine])
    doc = build_sarif_log(snapshot, tool_versions={}, forge_version="test")
    candidate = wire_to_json(serialize_report_projection_v2(classify_report_records_v2(snapshot)))
    doc["runs"][0].setdefault("properties", {})["report_classification"] = candidate
    result = read(raw(doc))
    assert result.selected_version == "v2"
    assert result.block == candidate
    assert [x["record"]["record_index"] for x in result.block["product"]] == [0]
    assert [x["record"]["record_index"] for x in result.block["unresolved_product_candidates"]] == [1]
    assert [x["record"]["record_index"] for x in result.block["execution"]] == [2]
    assert [x["record_index"] for x in result.block["quarantine"]] == [3]
    assert result.block["classification_complete"] is False


def test_record_limit_distinguishes_envelope_from_classification_failure():
    limits = ReaderLimits(10000, 64, 1)
    doc = document()
    doc["runs"][0]["results"] = [{}, {}]
    result = read(raw(doc), limits=limits)
    assert (result.envelope_condition, result.selection_reason, result.block) == (
        "limit-exceeded",
        "record-limit",
        None,
    )
    candidate = mutation_block()
    candidate["execution"].append(copy.deepcopy(candidate["execution"][0]))
    candidate["execution"][1]["record"]["record_index"] = 1
    result = read(raw(document(candidate)), limits=limits)
    assert (result.envelope_condition, result.selection_reason, result.block) == (
        "accepted",
        "record-limit",
        None,
    )
    assert result.selected_version is None


@pytest.mark.parametrize(
    "edit",
    [
        lambda b: None,
        lambda b: b.update(annotation="legacy annotation preserved"),
        lambda b: b.update(product_list_count=False),
        lambda b: b.update(classification_complete=False),
        lambda b: b.update(product_path_record_count=1),
        lambda b: b.update(acquisition_reason="unknown"),
        lambda b: b.update(projection_schema="unknown"),
        lambda b: b.update(delivery_mode="unknown"),
    ],
)
def test_v1_block_acceptance_differential(edit):
    from code_forge.report_presentation import (
        validate_block_dict,
        parse_report_classification,
        wire_to_json,
    )

    candidate = block("v1")
    edit(candidate)
    doc = document(candidate)
    result = read(raw(doc))
    old_reason = validate_block_dict(candidate)
    old_selected = wire_to_json(
        parse_report_classification(doc["runs"][0], legacy_report_state="accepted")
    )
    expected = old_reason is None and old_selected["availability"] == "available"
    assert (result.selected_version == "v1") is expected
    if expected:
        assert result.block == candidate
        assert result.selection_reason == "v2-absent"


def test_inherited_field_depth_remains_a_schema_domain_limit():
    # Outer ReaderLimits is not an override of the inherited field's domain.
    candidate = mutation_block()
    nested = []
    for _ in range(70):
        nested = [nested]
    candidate["execution"][0]["record"]["fields"]["provider_failure"] = nested
    result = read(raw(document(candidate)), limits=ReaderLimits(10000, 100, 10))
    assert result.envelope_condition == "accepted"
    assert result.selection_reason == "invalid-block"
