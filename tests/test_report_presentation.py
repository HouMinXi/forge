# SPDX-License-Identifier: Apache-2.0
"""IC-01 unit tests: prepare_legacy_report / prepare_report_views (§1.1)."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

from code_forge.disposition import Disposition
from code_forge.report_presentation import (
    LegacyReportSubstrate,
    PurePreparedReport,
    prepare_legacy_report,
    prepare_report_views,
    parse_report_classification,
    wire_to_json,
)
from code_forge.report_projection import ReportProjection, classify_report_records
from code_forge.sarif import build_sarif_log
from code_forge.state import State, StateFinding, Verdict, Mode


def _sf(**kw) -> StateFinding:
    defaults = dict(
        id="f1",
        fingerprint="fp1",
        source="L1",
        disposition=Disposition.CONFIRMED,
        file="a.py",
        line_range=[1, 2],
        description="desc",
    )
    defaults.update(kw)
    return StateFinding(**defaults)  # type: ignore[arg-type]


def _state(findings=None) -> State:
    return State(
        schema_version=1,
        mode=Mode.CI,
        verdict=Verdict.PASS,
        findings=list(findings or []),
    )


def _minimal_sarif(findings=None) -> dict:
    return build_sarif_log(_state(findings), tool_versions={}, forge_version="0.0-test")


def _stdout_for(doc: dict) -> str:
    return json.dumps(doc, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def test_prepare_legacy_report_owns_safe_substrate_no_alias():
    doc = _minimal_sarif()
    stdout = _stdout_for(doc)
    stderr = "summary line\n"
    # Mutate caller's dict after; substrate must not alias it.
    substrate = prepare_legacy_report(doc, stdout=stdout, stderr=stderr)
    assert substrate.sarif_document is not None
    assert substrate.stdout is stdout or substrate.stdout == stdout
    assert substrate.stderr == stderr
    doc["runs"][0]["results"].append({"evil": True})
    assert "evil" not in json.dumps(substrate.sarif_document)
    # Mutating owned graph must not affect a second prepare
    owned = substrate.sarif_document
    owned["marker"] = "x"
    again = prepare_legacy_report(
        json.loads(stdout), stdout=stdout, stderr=stderr
    )
    assert "marker" not in again.sarif_document


def test_prepare_legacy_report_raw_legacy_only_exact_string_identity():
    # Non-finite duration-like value inside otherwise JSON → acceptance fails
    # Use duplicate key via raw string the parser rejects
    bad_stdout = '{"version":"2.1.0","runs":[{"tool":{"driver":{"name":"code-forge"}},"results":[],"properties":{"a":1,"a":2}}]}'
    # Actually json.loads with object_pairs_hook rejects duplicates; without
    # the hook standard json.loads keeps last. Our acceptor uses the hook.
    # Provide a NaN which parse_float rejects.
    bad_stdout = (
        '{"version":"2.1.0","runs":[{"tool":{"driver":{"name":"code-forge"}},'
        '"results":[],"properties":{"duration":NaN}}]}'
    )
    legacy = {"version": "2.1.0", "runs": []}  # unrelated
    stderr = "kept-stderr-exact"
    sub = prepare_legacy_report(legacy, stdout=bad_stdout, stderr=stderr)
    assert sub.sarif_document is None
    assert sub.stdout is bad_stdout  # exact identity
    assert sub.stderr is stderr


def test_prepare_legacy_report_mismatch_stdout_vs_document_is_raw():
    doc = _minimal_sarif()
    other = _minimal_sarif([_sf(id="other")])
    stdout = _stdout_for(other)
    sub = prepare_legacy_report(doc, stdout=stdout, stderr="")
    assert sub.sarif_document is None
    assert sub.stdout is stdout


def test_prepare_report_views_typeerror_mismatched_modes():
    doc = _minimal_sarif()
    stdout = _stdout_for(doc)
    safe = prepare_legacy_report(doc, stdout=stdout, stderr="")
    assert safe.sarif_document is not None
    with pytest.raises(TypeError):
        prepare_report_views(safe, None)

    raw = LegacyReportSubstrate(sarif_document=None, stdout=stdout, stderr="e")
    proj = classify_report_records(_state())
    with pytest.raises(TypeError):
        prepare_report_views(raw, proj)


def test_prepare_report_views_raw_legacy_only_passthrough_and_unavailable():
    bad_stdout = (
        '{"version":"2.1.0","runs":[{"tool":{"driver":{"name":"code-forge"}},'
        '"results":[],"properties":{"duration":NaN}}]}'
    )
    raw = LegacyReportSubstrate(sarif_document=None, stdout=bad_stdout, stderr="E")
    prepared = prepare_report_views(raw, None)
    assert prepared.stdout is bad_stdout
    assert prepared.stderr is raw.stderr
    assert prepared.report_classification.availability == "unavailable"
    assert prepared.report_classification.unavailable_reason == "invalid-report"


def test_prepare_report_views_empty_safe_projection_available():
    doc = _minimal_sarif()
    stdout = _stdout_for(doc)
    safe = prepare_legacy_report(doc, stdout=stdout, stderr="sum\n")
    assert safe.sarif_document is not None
    proj = classify_report_records(_state())
    prepared = prepare_report_views(safe, proj)
    assert isinstance(prepared, PurePreparedReport)
    assert prepared.report_classification.availability == "available"
    assert prepared.report_classification.delivery_mode == "projection"
    assert prepared.report_classification.classification_complete is True
    assert prepared.report_classification.product_list_count == 0
    # Extension present in stdout
    out_doc = json.loads(prepared.stdout)
    assert "report_classification" in out_doc["runs"][0]["properties"]
    # Reader agrees
    rc = parse_report_classification(
        out_doc["runs"][0], legacy_report_state="accepted"
    )
    assert rc.availability == "available"
    assert wire_to_json(rc)["product"] == []


def test_prepare_report_views_no_classifier_call(monkeypatch):
    doc = _minimal_sarif()
    stdout = _stdout_for(doc)
    safe = prepare_legacy_report(doc, stdout=stdout, stderr="")
    proj = classify_report_records(_state())

    def boom(*a, **k):
        raise AssertionError("classifier must not be called from prepare_*")

    monkeypatch.setattr(
        "code_forge.report_projection.classify_report_records", boom
    )
    monkeypatch.setattr(
        "code_forge.report_presentation.classify_report_records", boom, raising=False
    )
    prepare_report_views(safe, proj)
    prepare_legacy_report(doc, stdout=stdout, stderr="")


def test_prepare_functions_no_io(tmp_path, monkeypatch):
    # Refuse filesystem touches during prepare_*
    real_open = open

    def guarded_open(file, *a, **k):
        p = str(file)
        if p.startswith(str(tmp_path)) or p.startswith("/proc") or p.startswith("/dev"):
            return real_open(file, *a, **k)
        # allow importlib / pytest internals under site-packages
        if "site-packages" in p or "pytest" in p or p.endswith(".pyc"):
            return real_open(file, *a, **k)
        raise AssertionError(f"unexpected open: {file!r}")

    monkeypatch.setattr("builtins.open", guarded_open)
    doc = _minimal_sarif()
    stdout = _stdout_for(doc)
    safe = prepare_legacy_report(doc, stdout=stdout, stderr="")
    proj = classify_report_records(_state())
    prepare_report_views(safe, proj)


def test_prepare_does_not_mutate_projection_or_substrate():
    doc = _minimal_sarif([_sf()])
    stdout = _stdout_for(doc)
    safe = prepare_legacy_report(doc, stdout=stdout, stderr="s")
    proj = classify_report_records(_state([_sf()]))
    before_proj = copy.deepcopy(proj)
    before_doc = copy.deepcopy(safe.sarif_document)
    prepare_report_views(safe, proj)
    assert proj == before_proj
    assert safe.sarif_document == before_doc


def test_wp2_prepare_report_views_embeds_host_failure_kind_in_sarif():
    """Include consumers reading report_classification see additive labels (「未经独立复现」).

    Labels flow via existing prepare_report_views → SARIF property → parse/wire_to_json
    without editing cli.py / sarif.py / mcp_*.py.

    Uses S2 + X2 fixtures (regression). X1/S1 prepare→available covered by WP-R2
    tests below (IC-07-RAO Option B serialize recovery).
    """
    from code_forge.state import FindingDiagnosticKind

    findings = [
        # X2 gate-label on product path
        _sf(
            id="RECEIPT_INVALID",
            fingerprint="receipt-deadbeef0001",
            source="INFRA",
            file="<receipt-evidence>",
            line_range=[0, 0],
            description="generic receipt invalid",
        ),
        # S2 capacity-incomplete → gate host
        _sf(
            id="RECEIPT_INVALID_S2",
            fingerprint="receipt-abcdef012345",
            source="INFRA",
            file="<receipt-evidence>",
            line_range=[0, 0],
            description="capacity incomplete",
            diagnostic_kind=FindingDiagnosticKind.CAPACITY_INCOMPLETE,
        ),
    ]
    # S2 requires id==RECEIPT_INVALID — use only S2 + a plain product for dual cover
    findings = [
        _sf(id="prod-1", fingerprint="prod-fp", disposition=Disposition.CONFIRMED),
        _sf(
            id="RECEIPT_INVALID",
            fingerprint="receipt-abcdef012345",
            source="INFRA",
            file="<receipt-evidence>",
            line_range=[0, 0],
            description="capacity incomplete",
            diagnostic_kind=FindingDiagnosticKind.CAPACITY_INCOMPLETE,
        ),
    ]
    state = _state(findings)
    doc = _minimal_sarif(findings)
    stdout = _stdout_for(doc)
    safe = prepare_legacy_report(doc, stdout=stdout, stderr="sum\n")
    assert safe.sarif_document is not None
    proj = classify_report_records(state)
    prepared = prepare_report_views(safe, proj)
    assert prepared.report_classification.availability == "available"
    out_doc = json.loads(prepared.stdout)
    block = out_doc["runs"][0]["properties"]["report_classification"]
    assert block["product"][0]["host_failure_kind"] is None  # plain product
    assert "host_failure_kind" in block["product"][0]  # always-emit
    assert "host_failure_kind" not in block["product"][0]["record"]["fields"]
    assert block["gate"][0]["host_failure_kind"] == "CAPACITY_INCOMPLETE"
    # Round-trip through reader (MCP path shape: parse then wire_to_json)
    rc = parse_report_classification(out_doc["runs"][0], legacy_report_state="accepted")
    assert rc.availability == "available"
    assert rc.gate[0].host_failure_kind == "CAPACITY_INCOMPLETE"
    re_emitted = wire_to_json(rc)
    assert re_emitted["gate"][0]["host_failure_kind"] == "CAPACITY_INCOMPLETE"


def test_wp2_prepare_embeds_x2_product_gate_label():
    """X2 RECEIPT_INVALID_GATE on product path embeds via prepare (「未经独立复现」)."""
    findings = [
        _sf(
            id="RECEIPT_INVALID",
            fingerprint="receipt-deadbeef0001",
            source="INFRA",
            file="<receipt-evidence>",
            line_range=[0, 0],
            description="generic receipt invalid",
        ),
    ]
    state = _state(findings)
    doc = _minimal_sarif(findings)
    stdout = _stdout_for(doc)
    safe = prepare_legacy_report(doc, stdout=stdout, stderr="sum\n")
    proj = classify_report_records(state)
    prepared = prepare_report_views(safe, proj)
    assert prepared.report_classification.availability == "available"
    out_doc = json.loads(prepared.stdout)
    block = out_doc["runs"][0]["properties"]["report_classification"]
    assert block["product"][0]["host_failure_kind"] == "RECEIPT_INVALID_GATE"
    assert block["gate"] == []


# ---------------------------------------------------------------------------
# IC-07-RAO WP-R2 — serialize recovery + prepare available for X1/S1
# 「未经独立复现」 — unit/integration in allowlist; full Include matrix = WP-R3
# ---------------------------------------------------------------------------


def _x1_finding(pf_kind: str = "conn") -> StateFinding:
    return _sf(
        id="l1-qodo-invoke-fail",
        fingerprint="invoke-fail-qodo",
        source="INFRA",
        file="<llm-invoke>",
        line_range=[0, 0],
        description="invoke fail",
        provider_failure={"kind": pf_kind},
    )


def _s1_finding() -> StateFinding:
    from code_forge.state import FindingDiagnosticKind

    return _sf(
        id="l1-adversarial-invoke-fail",
        fingerprint="invoke-fail-adversarial",
        source="INFRA",
        file="<llm-invoke>",
        line_range=[0, 0],
        description="capacity",
        diagnostic_kind=FindingDiagnosticKind.PROVIDER_CAPACITY,
        provider_failure={"kind": "truncated"},
    )


@pytest.mark.parametrize(
    "pf_kind,expected_label",
    [
        ("conn", "PROVIDER_TRANSPORT"),
        ("credentials", "PROVIDER_AUTH"),
        ("empty", "PROVIDER_EMPTY"),
        ("no_json", "PROVIDER_PROTOCOL"),
    ],
)
def test_wpr2_prepare_x1_available_with_host_failure_kind(pf_kind, expected_label):
    """X1 LABEL-ONLY: prepare_report_views → available + label (not unavailable/invalid-block)."""
    findings = [_x1_finding(pf_kind)]
    state = _state(findings)
    doc = _minimal_sarif(findings)
    stdout = _stdout_for(doc)
    safe = prepare_legacy_report(doc, stdout=stdout, stderr="sum\n")
    assert safe.sarif_document is not None
    proj = classify_report_records(state)
    assert proj.acquisition_reason == "pass-outcome-not-completed"
    assert proj.persisted_pass_outcomes is not None
    prepared = prepare_report_views(safe, proj)
    assert prepared.report_classification.availability == "available"
    assert prepared.report_classification.unavailable_reason is None
    assert prepared.report_classification.acquisition_reason == "pass-outcome-not-completed"
    outcomes = prepared.report_classification.persisted_pass_outcomes
    assert outcomes is not None
    assert any(o != "completed" for _, o in outcomes)
    out_doc = json.loads(prepared.stdout)
    block = out_doc["runs"][0]["properties"]["report_classification"]
    assert block["availability"] == "available"
    assert block["acquisition_reason"] == "pass-outcome-not-completed"
    assert block["persisted_pass_outcomes"] is not None
    assert any(o != "completed" for _, o in block["persisted_pass_outcomes"])
    assert block["product"][0]["host_failure_kind"] == expected_label
    rc = parse_report_classification(out_doc["runs"][0], legacy_report_state="accepted")
    assert rc.availability == "available"
    assert rc.product[0].host_failure_kind == expected_label


def test_wpr2_prepare_s1_available_with_host_failure_kind():
    """S1 PROVIDER_CAPACITY: prepare_report_views → available + label."""
    findings = [_s1_finding()]
    state = _state(findings)
    doc = _minimal_sarif(findings)
    stdout = _stdout_for(doc)
    safe = prepare_legacy_report(doc, stdout=stdout, stderr="sum\n")
    assert safe.sarif_document is not None
    proj = classify_report_records(state)
    assert proj.acquisition_reason == "pass-outcome-not-completed"
    assert proj.persisted_pass_outcomes is not None
    prepared = prepare_report_views(safe, proj)
    assert prepared.report_classification.availability == "available"
    assert prepared.report_classification.unavailable_reason is None
    assert prepared.report_classification.acquisition_reason == "pass-outcome-not-completed"
    outcomes = prepared.report_classification.persisted_pass_outcomes
    assert outcomes is not None
    assert any(o != "completed" for _, o in outcomes)
    out_doc = json.loads(prepared.stdout)
    block = out_doc["runs"][0]["properties"]["report_classification"]
    assert block["availability"] == "available"
    assert block["execution"][0]["host_failure_kind"] == "PROVIDER_CAPACITY"
    rc = parse_report_classification(out_doc["runs"][0], legacy_report_state="accepted")
    assert rc.availability == "available"
    assert rc.execution[0].host_failure_kind == "PROVIDER_CAPACITY"


def test_wpr2_serialize_emits_projection_outcomes_for_pass_outcome_not_completed():
    """serialize_projection reads projection.persisted_pass_outcomes (does not invent)."""
    from code_forge.report_presentation import serialize_projection, validate_block_dict

    findings = [_x1_finding("conn")]
    proj = classify_report_records(_state(findings))
    assert proj.persisted_pass_outcomes is not None
    wire = serialize_projection(proj)
    assert wire.persisted_pass_outcomes == proj.persisted_pass_outcomes
    assert any(o != "completed" for _, o in wire.persisted_pass_outcomes)
    block = wire_to_json(wire)
    assert validate_block_dict(block) is None
    assert block["persisted_pass_outcomes"] is not None


def test_wpr2_serialize_null_field_fail_closed():
    """When projection field is null under pass-outcome-not-completed, do not invent."""
    from dataclasses import replace
    from code_forge.report_presentation import serialize_projection, validate_block_dict

    findings = [_x1_finding("conn")]
    proj = classify_report_records(_state(findings))
    assert proj.persisted_pass_outcomes is not None
    cleared = replace(proj, persisted_pass_outcomes=None)
    wire = serialize_projection(cleared)
    assert wire.acquisition_reason == "pass-outcome-not-completed"
    assert wire.persisted_pass_outcomes is None
    assert validate_block_dict(wire_to_json(wire)) == "invalid-block"


def test_wpr2_all_completed_recovery_unchanged_for_s2_and_completion():
    """Keep all-completed recovery for capacity-incomplete / completion-not-provable."""
    from code_forge.state import FindingDiagnosticKind
    from code_forge.report_presentation import serialize_projection, validate_block_dict, PASS_NAMES

    s2 = _sf(
        id="RECEIPT_INVALID",
        fingerprint="receipt-abcdef012345",
        source="INFRA",
        file="<receipt-evidence>",
        line_range=[0, 0],
        description="capacity incomplete",
        diagnostic_kind=FindingDiagnosticKind.CAPACITY_INCOMPLETE,
    )
    proj_s2 = classify_report_records(_state([s2]))
    assert proj_s2.acquisition_reason == "capacity-incomplete-record"
    assert proj_s2.persisted_pass_outcomes is None
    wire_s2 = serialize_projection(proj_s2)
    assert wire_s2.persisted_pass_outcomes == tuple((n, "completed") for n in PASS_NAMES)
    assert validate_block_dict(wire_to_json(wire_s2)) is None

    proj_c = classify_report_records(_state([_sf(id="prod-1", fingerprint="prod-fp")]))
    assert proj_c.acquisition_reason == "completion-not-provable-from-state"
    assert proj_c.persisted_pass_outcomes is None
    wire_c = serialize_projection(proj_c)
    assert wire_c.persisted_pass_outcomes == tuple((n, "completed") for n in PASS_NAMES)
    assert validate_block_dict(wire_to_json(wire_c)) is None


def test_wpr2_rao_validator_rule_text_unchanged():
    """RA-O _validate_acq rule text for pass-outcome-not-completed must stay intact."""
    from pathlib import Path
    import code_forge.report_presentation as rp

    src = Path(rp.__file__).read_text()
    # Exact coherence clauses from WP-R0 freeze (must not be softened to accept null)
    assert 'if reason == "pass-outcome-not-completed":' in src
    assert "_require(complete is False and outcomes is not None)" in src
    assert '_require(any(o != "completed" for _, o in outcomes))' in src
