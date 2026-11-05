# SPDX-License-Identifier: Apache-2.0
"""Real pure-layer end-to-end controls, not CLI/MCP enablement."""

import asyncio
import copy
import json

import pytest

from code_forge import report_orchestration_v2 as orchestration
from code_forge.report_baseline_v2 import build_owned_report_pair
from code_forge.report_preparation_v2 import PreparationLimits
from code_forge.report_reader_v2 import ReaderLimits, read_report_view
from code_forge.state import load_state, save_state, StateFinding
from code_forge.disposition import Disposition
from tests.test_report_baseline_v2 import fixture_state, context, LIMITS

BUDGET = PreparationLimits(100000, 100000, 100000, ReaderLimits(100000, 64, 100))


def prepare(source, captured=None, budget=BUDGET):
    return orchestration.prepare_from_owned_snapshot(
        source,
        context() if captured is None else captured,
        snapshot_limits=LIMITS,
        preparation_limits=budget,
    )


@pytest.mark.parametrize("kind", ["empty", "product", "unresolved", "mutation", "infra"])
def test_real_saved_state_through_complete_pure_pipeline(tmp_path, kind):
    path = tmp_path / "state.json"
    save_state(fixture_state(kind), path)
    source = load_state(path)
    original = copy.deepcopy(source)
    original_bytes = path.read_bytes()
    result = prepare(source)
    assert result.writer_reason == "v2-prepared"
    assert result.selection.selected_version == "v2"
    assert result.selection == read_report_view(
        result.text.stdout.encode(), requested_version="v2", limits=BUDGET.reader
    )
    assert source == original
    assert path.read_bytes() == original_bytes
    assert "not invocation status" in result.text.stderr
    if kind == "mutation":
        assert "mutation-infra=1" in result.text.stderr
        assert len(result.selection.block["execution"]) == 1
    if kind == "unresolved":
        assert "unresolved=1" in result.text.stderr


def test_mixed_occurrences_and_unrelated_quarantine_remain_visible():
    source = fixture_state("mutation")
    source.findings += [
        StateFinding("same", "p", "L1", Disposition.CONFIRMED, "a.py", [1, 1], "P"),
        StateFinding("same", "u", "L1", Disposition.UNCERTAIN, "a.py", [1, 1], "U"),
        StateFinding("bad", "q", "L1", Disposition.CONFIRMED, "a.py", [1, 1], None),
    ]
    result = prepare(source)
    assert result.writer_reason == "v2-prepared"
    block = result.selection.block
    assert block["classification_complete"] is False
    assert [row["record"]["record_index"] for row in block["execution"]] == [0]
    assert [row["record"]["record_index"] for row in block["product"]] == [1]
    assert [row["record"]["record_index"] for row in block["unresolved_product_candidates"]] == [2]
    assert [row["record_index"] for row in block["quarantine"]] == [3]


def test_same_private_state_and_context_baseline_first(monkeypatch):
    source = fixture_state("mutation")
    seen = []
    baseline = orchestration._build_baseline_parts
    projection = orchestration.classify_report_records_v2

    def capture_baseline(state, inputs):
        seen.append(("baseline", state, inputs))
        return baseline(state, inputs)

    def capture_projection(state):
        seen.append(("projection", state))
        return projection(state)

    monkeypatch.setattr(orchestration, "_build_baseline_parts", capture_baseline)
    monkeypatch.setattr(orchestration, "classify_report_records_v2", capture_projection)
    assert prepare(source).writer_reason == "v2-prepared"
    assert [row[0] for row in seen] == ["baseline", "projection"]
    assert seen[0][1] is seen[1][1] and seen[0][1] is not source


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("projection", "projection-failed"),
        ("render", "v2-preparation-failed"),
        ("global-return", "v2-global-fallback"),
    ],
)
def test_faults_restore_complete_actual_baseline(monkeypatch, fault, reason):
    from code_forge import report_projection_v2

    source = fixture_state("mutation")
    expected = build_owned_report_pair(source, context(), limits=LIMITS).baseline
    if fault == "projection":

        def fail(_):
            raise orchestration.ProjectionComputationFault("private detail")

        monkeypatch.setattr(orchestration, "classify_report_records_v2", fail)
    elif fault == "render":

        def fail(*args):
            raise orchestration.RenderComputationFault("private detail")

        monkeypatch.setattr(orchestration, "_render_candidate", fail)
    else:

        def fail(_):
            raise report_projection_v2.ProjectionComputationFault("private detail")

        monkeypatch.setattr(report_projection_v2, "reporting_product_findings", fail)
    result = prepare(source)
    assert result.text == expected
    assert result.writer_reason == reason
    assert "private detail" not in str(result)
    assert result.selection.selected_version == "v1"


@pytest.mark.parametrize("boundary", ["classify_report_records_v2", "_render_candidate"])
@pytest.mark.parametrize("error", [MemoryError, RecursionError, TypeError, asyncio.CancelledError])
def test_critical_and_programming_failures_propagate(monkeypatch, boundary, error):
    def fail(*args):
        raise error()

    monkeypatch.setattr(orchestration, boundary, fail)
    with pytest.raises(error):
        prepare(fixture_state("mutation"))


def test_mismatched_parallel_block_metadata_restores_baseline(monkeypatch):
    real = orchestration._render_candidate
    source = fixture_state("mutation")
    expected = build_owned_report_pair(source, context(), limits=LIMITS).baseline

    def mismatch(*args):
        body, stderr, block = real(*args)
        wrong = copy.deepcopy(block)
        wrong["execution"][0]["record"]["fields"]["description"] = "not actual bytes"
        return body, stderr, wrong

    monkeypatch.setattr(orchestration, "_render_candidate", mismatch)
    result = prepare(source)
    assert result.writer_reason == "v2-content-mismatch"
    assert result.text == expected


def test_json_value_fault_at_real_encoding_site_restores(monkeypatch):
    real = json.dumps

    def fail_candidate(document, *args, **kwargs):
        if (
            type(document) is dict
            and "runs" in document
            and document["runs"][0].get("properties", {}).get("report_classification", {}).get("schema")
            == "forge-report-classification/v2"
        ):
            raise ValueError("synthetic value-domain fault")
        return real(document, *args, **kwargs)

    monkeypatch.setattr(orchestration.json, "dumps", fail_candidate)
    result = prepare(fixture_state("mutation"))
    assert result.writer_reason == "v2-preparation-failed"
    assert result.selection.selected_version == "v1"


def test_late_budget_failure_restores_v1_summary_not_v2_counts():
    source = fixture_state("mutation")
    expected = build_owned_report_pair(source, context(), limits=LIMITS).baseline
    result = prepare(source, budget=PreparationLimits(100000, 100000, 1, BUDGET.reader))
    assert result.text == expected
    assert "report-v2 execution-diagnostics" not in result.text.stderr
    assert result.writer_reason == "v2-budget-refused"


def test_baseline_failure_never_invokes_optional_classifier(monkeypatch):
    def fail(*args):
        raise ValueError("baseline")

    def forbidden(*args):
        pytest.fail("optional classifier started")

    monkeypatch.setattr(orchestration, "_build_baseline_parts", fail)
    monkeypatch.setattr(orchestration, "classify_report_records_v2", forbidden)
    with pytest.raises(ValueError, match="baseline"):
        prepare(fixture_state("mutation"))


def test_valid_unavailable_baseline_can_prepare_v2_without_stale_budget_notice(monkeypatch):
    from code_forge import report_presentation

    monkeypatch.setattr(report_presentation, "_block_fits", lambda *args: False)
    result = prepare(fixture_state("mutation"))
    assert result.writer_reason == "v2-prepared"
    assert "classification unavailable" not in result.text.stderr
    assert "budget" not in result.text.stderr
    assert "mutation-infra=1" in result.text.stderr


def test_returned_unavailable_v2_restores_identical_actual_baseline(monkeypatch):
    from dataclasses import replace
    from code_forge.report_presentation import _unavailable

    observed = []
    real_baseline = orchestration._build_baseline_parts

    def capture(*args):
        built = real_baseline(*args)
        observed.append(built.text)
        return built

    def unavailable(_):
        return replace(_unavailable("budget-exceeded"), schema="forge-report-classification/v2")

    monkeypatch.setattr(orchestration, "_build_baseline_parts", capture)
    monkeypatch.setattr(orchestration, "serialize_report_projection_v2", unavailable)
    result = prepare(fixture_state("mutation"))
    assert result.writer_reason == "v2-unavailable"
    assert result.text is observed[0]


def test_real_oversize_baseline_never_starts_optional_work(monkeypatch):
    from code_forge.report_baseline_v2 import SnapshotLimits

    source = fixture_state("product")
    source.findings[0].description = "x" * 1_048_576

    def forbidden(*args):
        pytest.fail("optional classifier started on oversize baseline")

    monkeypatch.setattr(orchestration, "classify_report_records_v2", forbidden)
    result = orchestration.prepare_from_owned_snapshot(
        source,
        context(),
        snapshot_limits=SnapshotLimits(10000, 2_000_000, 100),
        preparation_limits=PreparationLimits(
            1_048_576, 2_000_000, 10000, ReaderLimits(2_000_000, 64, 100)
        ),
    )
    assert result.writer_reason == "baseline-unusable"
    assert len(result.text.stdout) > 1_048_576
    assert "x" * 1000 in result.text.stdout


@pytest.mark.parametrize(
    "change", ["omit-mutation", "alter-description", "alter-acquisition", "alter-host-kind"]
)
def test_serializer_omission_or_alteration_cannot_self_certify(monkeypatch, change):
    from dataclasses import replace

    source = fixture_state("mutation")
    expected = build_owned_report_pair(source, context(), limits=LIMITS).baseline
    real = orchestration.serialize_report_projection_v2

    def corrupt(projection):
        wire = real(projection)
        if change == "omit-mutation":
            return replace(wire, execution=())
        if change == "alter-acquisition":
            return replace(
                wire, acquisition_reason="acquisition-derivation-fault", persisted_pass_outcomes=None
            )
        host = wire.execution[0]
        if change == "alter-host-kind":
            host = replace(host, diagnostic=replace(host.diagnostic, kind="provider-capacity"))
        else:
            fields = dict(host.record.fields)
            fields["description"] = "different but schema-valid"
            host = replace(host, record=replace(host.record, fields=fields))
        return replace(wire, execution=(host,))

    monkeypatch.setattr(orchestration, "serialize_report_projection_v2", corrupt)
    result = prepare(source)
    assert result.writer_reason == "v2-content-mismatch"
    assert result.text == expected


@pytest.mark.parametrize(
    "change", ["quarantine-safe-fields", "conditional-path-count", "associated-diagnostic"]
)
def test_projection_oracle_retains_other_schema_valid_semantics(monkeypatch, change):
    from dataclasses import replace

    source = fixture_state("mutation")
    if change == "associated-diagnostic":
        source.findings.append(
            StateFinding(
                "u",
                "u",
                "L1",
                Disposition.UNCERTAIN,
                "a.py",
                [1, 1],
                "U",
                error="falsify() raised: original",
            )
        )
    else:
        source.findings.append(StateFinding("q", "q", "L1", Disposition.CONFIRMED, "a.py", [1, 1], None))
    expected = build_owned_report_pair(source, context(), limits=LIMITS).baseline
    real = orchestration.serialize_report_projection_v2

    def corrupt(projection):
        wire = real(projection)
        if change == "conditional-path-count":
            return replace(wire, product_path_record_count=0)
        if change == "quarantine-safe-fields":
            fallback = wire.quarantine[0]
            altered = dict(fallback.safe_fields)
            altered["id"] = "different"
            return replace(wire, quarantine=(replace(fallback, safe_fields=altered),))
        unresolved = wire.unresolved_product_candidates[0]
        altered = dict(unresolved.record.fields)
        altered["error"] = None
        unresolved = replace(
            unresolved, record=replace(unresolved.record, fields=altered), associated_diagnostics=()
        )
        return replace(wire, unresolved_product_candidates=(unresolved,))

    monkeypatch.setattr(orchestration, "serialize_report_projection_v2", corrupt)
    result = prepare(source)
    assert result.writer_reason == "v2-content-mismatch"
    assert result.text == expected


@pytest.mark.parametrize("altered_range", [[True, True], [1.0, 1.0]])
def test_quarantine_numeric_type_substitution_cannot_pass_projection_oracle(monkeypatch, altered_range):
    from dataclasses import replace

    source = fixture_state("mutation")
    source.findings.append(StateFinding("q", "q", "L1", Disposition.CONFIRMED, "a.py", [1, 1], None))
    expected = build_owned_report_pair(source, context(), limits=LIMITS).baseline
    real = orchestration.serialize_report_projection_v2

    def corrupt(projection):
        wire = real(projection)
        fallback = wire.quarantine[0]
        safe = dict(fallback.safe_fields)
        safe["line_range"] = altered_range
        return replace(wire, quarantine=(replace(fallback, safe_fields=safe),))

    monkeypatch.setattr(orchestration, "serialize_report_projection_v2", corrupt)
    result = prepare(source)
    assert result.writer_reason == "v2-content-mismatch"
    assert result.text == expected


@pytest.mark.parametrize(
    "left,right",
    [(1, True), (1, 1.0), ({"x": [1]}, {"x": [True]}), ([0], [0.0]), ({"a": None}, {"b": None})],
)
def test_exact_json_equality_rejects_type_and_key_mismatch(left, right):
    from code_forge.report_projection_check_v2 import exact_json_equal

    assert not exact_json_equal(left, right)


def test_projection_guard_omission_and_exact_restoration_are_attributable(monkeypatch):
    from dataclasses import replace

    source = fixture_state("mutation")
    real_serializer = orchestration.serialize_report_projection_v2
    monkeypatch.setattr(
        orchestration,
        "serialize_report_projection_v2",
        lambda projection: replace(real_serializer(projection), execution=()),
    )
    with monkeypatch.context() as omitted:
        omitted.setattr(orchestration, "projection_matches_block", lambda *args: True)
        unguarded = prepare(source)
        assert unguarded.writer_reason == "v2-prepared"
        assert unguarded.selection.block["execution"] == []
    restored = prepare(source)
    assert restored.writer_reason == "v2-content-mismatch"
    assert restored.selection.selected_version == "v1"


def test_exact_type_guard_omission_and_restoration_are_attributable(monkeypatch):
    from dataclasses import replace
    from code_forge import report_projection_check_v2 as checker

    source = fixture_state("mutation")
    source.findings.append(StateFinding("q", "q", "L1", Disposition.CONFIRMED, "a.py", [1, 1], None))
    real_serializer = orchestration.serialize_report_projection_v2

    def corrupt(projection):
        wire = real_serializer(projection)
        fallback = wire.quarantine[0]
        safe = dict(fallback.safe_fields, line_range=[True, True])
        return replace(wire, quarantine=(replace(fallback, safe_fields=safe),))

    monkeypatch.setattr(orchestration, "serialize_report_projection_v2", corrupt)
    with monkeypatch.context() as omitted:
        omitted.setattr(checker, "exact_json_equal", lambda left, right: left == right)
        assert prepare(source).writer_reason == "v2-prepared"
    assert prepare(source).writer_reason == "v2-content-mismatch"


@pytest.mark.parametrize(
    "receipt_subtype", [None, "RECEIPT_SCHEMA", "RECEIPT_COVERAGE", "RECEIPT_EVIDENCE"]
)
@pytest.mark.parametrize("audit_provider", [None, "conn"])
def test_real_s1_s2_s3_mutation_combined_pipeline(receipt_subtype, audit_provider):
    from code_forge.state import FindingDiagnosticKind

    source = fixture_state("mutation")
    source.findings.extend(
        [
            StateFinding(
                "l1-qodo-invoke-fail",
                "invoke-fail-qodo",
                "INFRA",
                Disposition.CONFIRMED,
                "<llm-invoke>",
                [0, 0],
                "capacity",
                diagnostic_kind=FindingDiagnosticKind.PROVIDER_CAPACITY,
                provider_failure={"kind": "truncated"},
            ),
            StateFinding(
                "RECEIPT_INVALID",
                "receipt-abcdef012345",
                "INFRA",
                Disposition.CONFIRMED,
                "<receipt-evidence>",
                [0, 0],
                "incomplete",
                diagnostic_kind=FindingDiagnosticKind.CAPACITY_INCOMPLETE,
                receipt_subtype=receipt_subtype,
            ),
            StateFinding(
                "RECEIPT_UNTRUSTED",
                "audit",
                "UNTRUSTED",
                Disposition.UNCERTAIN,
                "a.py",
                [1, 1],
                "audit",
                provider_failure=None if audit_provider is None else {"kind": audit_provider},
            ),
        ]
    )
    result = prepare(source)
    assert result.writer_reason == "v2-prepared"
    block = result.selection.block
    assert [row["record"]["record_index"] for row in block["execution"]] == [0, 1]
    assert [row["diagnostic"]["kind"] for row in block["execution"]] == [
        "mutation-infra",
        "provider-capacity",
    ]
    assert [row["record"]["record_index"] for row in block["gate"]] == [2, 3]
    assert [row["diagnostic"]["kind"] for row in block["gate"]] == [
        "capacity-incomplete",
        "receipt-audit",
    ]
    assert [row["host_failure_kind"] for row in block["gate"]] == [
        receipt_subtype or "CAPACITY_INCOMPLETE",
        None if audit_provider is None else "PROVIDER_TRANSPORT",
    ]
    assert block["product"] == block["quarantine"] == []
    assert block["product_list_count"] == block["unresolved_candidate_count"] == 0
    assert block["classification_complete"] is True
    assert block["acquisition_complete"] is False
