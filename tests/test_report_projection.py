"""Phase 1 unit tests for code_forge.report_projection (Forge #332 slice §5)."""
from __future__ import annotations

import copy
import hashlib
import io
import os
from dataclasses import fields, replace
from pathlib import Path
from typing import Any

import pytest

from code_forge.disposition import Disposition
from code_forge.report_projection import (
    AssociatedDiagnostic,
    ClassificationError,
    FallbackRecord,
    ReportProjection,
    ReportRecord,
    _assemble,
    _attach_diagnostics,
    _build_record,
    _evaluate_acquisition,
    _select_shape,
    classify_report_records,
)
from code_forge.sarif import _build_run
from code_forge.state import (
    FindingDiagnosticKind,
    PassOutcome,
    State,
    StateFinding,
    _PASS_NAMES,
    save_state,
    load_state,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sf(
    *,
    id: str = "f1",
    fingerprint: str = "fp1",
    source: str = "L1",
    disposition: Disposition = Disposition.CONFIRMED,
    file: str = "a.py",
    line_range: list | None = None,
    description: str = "desc",
    error: str | None = None,
    evidence_downgrade: str | None = None,
    diagnostic_kind: FindingDiagnosticKind | None = None,
    provider_failure: dict | None = None,
    is_timeout: bool = False,
) -> StateFinding:
    return StateFinding(
        id=id,
        fingerprint=fingerprint,
        source=source,  # type: ignore[arg-type]
        disposition=disposition,
        file=file,
        line_range=[] if line_range is None else line_range,
        description=description,
        error=error,
        evidence_downgrade=evidence_downgrade,
        diagnostic_kind=diagnostic_kind,
        provider_failure=provider_failure,
        is_timeout=is_timeout,
    )


def _product_s0(**kw) -> StateFinding:
    defaults = dict(id="prod-1", fingerprint="prod-fp", source="L1", disposition=Disposition.CONFIRMED)
    defaults.update(kw)
    return _sf(**defaults)


def _uncertain_s0(**kw) -> StateFinding:
    defaults = dict(id="unc-1", fingerprint="unc-fp", source="L1", disposition=Disposition.UNCERTAIN)
    defaults.update(kw)
    return _sf(**defaults)


def _s1_capacity(pass_name: str = "adversarial", kind: str = "invoke") -> StateFinding:
    file = "<llm-invoke>" if kind == "invoke" else "<spawn>"
    return _sf(
        id=f"l1-{pass_name}-{kind}-fail",
        fingerprint=f"{kind}-fail-{pass_name}",
        source="INFRA",
        disposition=Disposition.CONFIRMED,
        file=file,
        line_range=[0, 0],
        description="capacity",
        diagnostic_kind=FindingDiagnosticKind.PROVIDER_CAPACITY,
        provider_failure={"kind": "truncated"},
    )


def _s2_incomplete(fp: str = "receipt-abcdef012345") -> StateFinding:
    return _sf(
        id="RECEIPT_INVALID",
        fingerprint=fp,
        source="INFRA",
        disposition=Disposition.CONFIRMED,
        file="<receipt-evidence>",
        line_range=[0, 0],
        description="capacity incomplete",
        diagnostic_kind=FindingDiagnosticKind.CAPACITY_INCOMPLETE,
    )


def _s3_receipt_audit() -> StateFinding:
    return _sf(
        id="RECEIPT_UNTRUSTED",
        fingerprint="ru-fp",
        source="UNTRUSTED",
        disposition=Disposition.UNCERTAIN,
        file="x.py",
        line_range=[1, 2],
        description="audit",
    )


def _x1_invoke_fail(pass_name: str = "qodo") -> StateFinding:
    return _sf(
        id=f"l1-{pass_name}-invoke-fail",
        fingerprint=f"invoke-fail-{pass_name}",
        source="INFRA",
        disposition=Disposition.CONFIRMED,
        file="<llm-invoke>",
        line_range=[0, 0],
        description="invoke fail",
        # no diagnostic_kind → not S1; stays product-path / deferred X1
    )


def _x2_receipt_invalid() -> StateFinding:
    return _sf(
        id="RECEIPT_INVALID",
        fingerprint="receipt-deadbeef0001",
        source="INFRA",
        disposition=Disposition.CONFIRMED,
        file="<receipt-evidence>",
        line_range=[0, 0],
        description="generic receipt invalid",
        # no diagnostic_kind → not S2
    )


def _mutation_skipped_dismissed() -> StateFinding:
    return _sf(
        id="MUTATION_SKIPPED",
        fingerprint="mutation-other-adapter",
        source="MUTANT",
        disposition=Disposition.DISMISSED,
        file="a.py",
        line_range=[],
        description="other adapter",
    )


def _roundtrip(tmp_path: Path, findings: list[StateFinding]) -> State:
    d = tmp_path / "state_dir"
    d.mkdir(exist_ok=True)
    path = d / "state.json"
    state = State(findings=list(findings))
    save_state(state, path)
    loaded = load_state(path)
    assert loaded is not None
    return loaded


def _tree_hash(root: Path) -> str:
    pairs = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            pairs.append((str(p.relative_to(root)), hashlib.sha256(p.read_bytes()).hexdigest()))
    h = hashlib.sha256()
    for rel, digest in pairs:
        h.update(rel.encode())
        h.update(digest.encode())
    return h.hexdigest()


class RaisingDescription(StateFinding):
    """Undecorated subclass; description getter raises _Boom (Exception subclass)."""

    @property
    def description(self):  # type: ignore[override]
        raise _Boom("boom")

    @description.setter
    def description(self, value):  # type: ignore[override]
        self.__dict__["_desc_store"] = value


class _Boom(Exception):
    pass


def _write_api_guard(monkeypatch):
    calls: list[tuple] = []

    def _raise(*a, **k):
        calls.append((a, k))
        raise RuntimeError("write-api-guard")

    import builtins
    import io as _io
    import shutil
    from pathlib import Path as P

    monkeypatch.setattr(builtins, "open", _raise)
    monkeypatch.setattr(_io, "open", _raise)
    for name in ("open", "fdopen", "replace", "rename", "remove", "unlink", "mkdir", "makedirs"):
        monkeypatch.setattr(os, name, _raise, raising=False)
    for name in ("open", "write_text", "write_bytes", "mkdir", "unlink", "rename", "replace", "touch"):
        monkeypatch.setattr(P, name, _raise)
    for name in ("copy", "copy2", "copyfile", "copytree", "move", "rmtree"):
        monkeypatch.setattr(shutil, name, _raise)
    return calls


# ---------------------------------------------------------------------------
# Empty / mixed / shapes
# ---------------------------------------------------------------------------

def test_empty_findings_is_classification_complete_and_acquisition_unproven(tmp_path):
    state = _roundtrip(tmp_path, [])
    proj = classify_report_records(state)
    assert proj.classification_complete is True
    assert proj.classification_incomplete_reasons == ()
    assert proj.classification_errors == ()
    assert proj.product == ()
    assert proj.unresolved_product_candidates == ()
    assert proj.execution == ()
    assert proj.gate == ()
    assert proj.quarantine == ()
    assert proj.product_list_count == 0
    assert proj.unresolved_candidate_count == 0
    assert proj.product_path_record_count == 0
    assert proj.acquisition_complete is None
    assert proj.acquisition_reason == "completion-not-provable-from-state"


def test_mixed_state_list_membership_and_counts(tmp_path):
    findings = [
        _product_s0(id="c1", fingerprint="c1fp"),  # CONFIRMED S0 → product
        _uncertain_s0(id="u1", fingerprint="u1fp"),  # UNCERTAIN S0 → unresolved
        _mutation_skipped_dismissed(),  # DISMISSED → product
        _x1_invoke_fail("qodo"),  # X1 → product; makes qodo non-COMPLETED → A1
        _x2_receipt_invalid(),  # X2 → product
        _s1_capacity("adversarial"),  # S1 → execution; also A1
        _s3_receipt_audit(),  # S3 → gate
    ]
    state = _roundtrip(tmp_path, findings)
    proj = classify_report_records(state)
    product_ids = {r.finding_id for r in proj.product}
    unresolved_ids = {r.finding_id for r in proj.unresolved_product_candidates}
    exec_ids = {r.finding_id for r in proj.execution}
    gate_ids = {r.finding_id for r in proj.gate}
    assert product_ids == {"c1", "MUTATION_SKIPPED", "l1-qodo-invoke-fail", "RECEIPT_INVALID"}
    assert unresolved_ids == {"u1"}
    assert exec_ids == {"l1-adversarial-invoke-fail"}
    assert gate_ids == {"RECEIPT_UNTRUSTED"}
    assert proj.quarantine == ()
    assert proj.product_list_count == 4
    assert proj.unresolved_candidate_count == 1
    assert proj.product_path_record_count == 5
    sarif_results = _build_run(state, {}, "test")["results"]
    assert proj.product_path_record_count == len(sarif_results)
    assert proj.acquisition_complete is False
    assert proj.acquisition_reason == "pass-outcome-not-completed"
    assert proj.classification_complete is True


@pytest.mark.parametrize("shape", ["parallel", "serial", "grouped", "outlet_c"])
def test_capacity_marker_uses_existing_predicate_only(tmp_path, shape):
    # Shape param is documentary (S1 predicate is shape-independent).
    f = _s1_capacity("adversarial", "invoke")
    state = _roundtrip(tmp_path, [f, _product_s0()])
    proj = classify_report_records(state)
    assert len(proj.execution) == 1
    rec = proj.execution[0]
    assert rec.shape == "S1"
    assert rec.selector == "is_provider_capacity"
    assert rec.channel == "execution"
    assert proj.acquisition_complete is False
    assert proj.acquisition_reason == "pass-outcome-not-completed"


def test_capacity_incomplete_is_gate_and_acquisition_false(tmp_path):
    """Synthetic A2 state (F24): S2 only, no S1 marker."""
    state = _roundtrip(tmp_path, [_s2_incomplete(), _product_s0()])
    proj = classify_report_records(state)
    assert len(proj.gate) == 1
    assert proj.gate[0].shape == "S2"
    assert proj.gate[0].selector == "is_provider_diagnostic"
    assert proj.acquisition_complete is False
    assert proj.acquisition_reason == "capacity-incomplete-record"


def test_provider_capacity_without_incomplete_is_acquisition_false(tmp_path):
    state = _roundtrip(tmp_path, [_s1_capacity("adversarial")])
    proj = classify_report_records(state)
    assert proj.acquisition_complete is False
    assert proj.acquisition_reason == "pass-outcome-not-completed"
    assert len(proj.execution) == 1


@pytest.mark.parametrize("kind", ["schema", "coverage"])
def test_schema_fail_or_incomplete_coverage_only_is_acquisition_false(tmp_path, kind):
    if kind == "schema":
        f = _sf(
            id="l1-qodo-schema-fail",
            fingerprint="schema-qodo",
            source="INFRA",
            disposition=Disposition.CONFIRMED,
            file="<schema>",
            line_range=[0, 0],
            description="schema",
        )
    else:
        f = _sf(
            id="l1-qodo-incomplete-coverage",
            fingerprint="inc-qodo",
            source="INFRA",
            disposition=Disposition.CONFIRMED,
            file="<coverage>",
            line_range=[0, 0],
            description="incomplete",
        )
    state = _roundtrip(tmp_path, [f])
    proj = classify_report_records(state)
    assert proj.acquisition_complete is False
    assert proj.acquisition_reason == "pass-outcome-not-completed"
    # deferred → product path S0
    assert len(proj.product) == 1
    assert proj.product[0].shape == "S0"
    assert proj.product[0].selector == "reporting_product_findings"


def test_a1_short_circuits_q4(tmp_path, monkeypatch):
    state = _roundtrip(tmp_path, [_x1_invoke_fail("qodo"), _product_s0()])
    q4_calls = {"n": 0}
    armed = {"v": False}

    import code_forge.report_projection as rp

    real_derive = rp.derive_pass_outcomes

    def derive_wrap(findings):
        out = real_derive(findings)
        armed["v"] = True
        return out

    def diag_wrap(f):
        if armed["v"]:
            q4_calls["n"] += 1
            raise RuntimeError("q4 should not run")
        return rp.is_provider_diagnostic.__wrapped__(f) if hasattr(rp.is_provider_diagnostic, "__wrapped__") else __import__("code_forge.state", fromlist=["is_provider_diagnostic"]).is_provider_diagnostic(f)

    # Use state module's real function for non-armed path via fresh import binding
    from code_forge import state as st_mod

    def diag_wrap2(f):
        if armed["v"]:
            q4_calls["n"] += 1
            raise RuntimeError("q4 should not run")
        return st_mod.is_provider_diagnostic(f)

    with monkeypatch.context() as m:
        m.setattr(rp, "derive_pass_outcomes", derive_wrap)
        m.setattr(rp, "is_provider_diagnostic", diag_wrap2)
        proj = classify_report_records(state)
    assert proj.acquisition_complete is False
    assert proj.acquisition_reason == "pass-outcome-not-completed"
    assert q4_calls["n"] == 0


def test_receipt_audit_stays_gate_metadata(tmp_path):
    state = _roundtrip(tmp_path, [_s3_receipt_audit(), _product_s0()])
    proj = classify_report_records(state)
    assert len(proj.gate) == 1
    assert proj.gate[0].shape == "S3"
    assert proj.gate[0].selector == "is_receipt_audit"
    assert proj.gate[0].channel == "gate"


def test_model_supplied_diagnostic_shape_stays_product(tmp_path):
    # Model-shaped INFRA without host diagnostic_kind stays product S0.
    f = _sf(
        id="model-diag",
        fingerprint="md",
        source="L1",
        disposition=Disposition.CONFIRMED,
        description="looks like capacity but is product",
    )
    state = _roundtrip(tmp_path, [f])
    proj = classify_report_records(state)
    assert len(proj.product) == 1
    assert proj.product[0].shape == "S0"


_S4_CASES = {
    "protocol": "falsify() protocol violation: x",
    "timeout": "falsify() backend timed out: x",
    "unavailable": "falsify() backend unavailable: x",
    "truncated": "falsify() response truncated: x",
    "unusable": "falsify() unusable response: x",
    "credentials": "falsify() credential failure: x",
    "invocation": "falsify() invocation failed: x",
    "raised": "falsify() raised: Boom",
}


@pytest.mark.parametrize("name", list(_S4_CASES))
def test_falsifier_failure_prefixes_exact(tmp_path, name):
    err = _S4_CASES[name]
    f = _uncertain_s0(id="s4", fingerprint="s4fp", error=err)
    state = _roundtrip(tmp_path, [f])
    proj = classify_report_records(state)
    assert len(proj.unresolved_product_candidates) == 1
    rec = proj.unresolved_product_candidates[0]
    assert rec.shape == "S4"
    assert rec.associated_diagnostics == (AssociatedDiagnostic("falsifier-failure", err),)


def test_fixval_hollow_and_near_miss_prefixes_are_not_s4(tmp_path):
    hollow = _sf(
        id="FIXVAL_HOLLOW",
        fingerprint="fh",
        source="FIXVAL",
        disposition=Disposition.UNCERTAIN,  # defensive: even if UNCERTAIN
        error="falsify() protocol violation: x",
        description="hollow",
    )
    near = _uncertain_s0(
        id="near",
        fingerprint="near",
        error="falsify() protocol violationx",  # missing ": "
    )
    near2 = _uncertain_s0(
        id="near2",
        fingerprint="near2",
        error="Xfalsify() protocol violation: x",
    )
    state = _roundtrip(tmp_path, [hollow, near, near2])
    proj = classify_report_records(state)
    shapes = {r.finding_id: r.shape for r in proj.unresolved_product_candidates + proj.product}
    # FIXVAL_HOLLOW exclusion + UNCERTAIN → S0 unresolved (not S4)
    assert shapes["FIXVAL_HOLLOW"] == "S0"
    assert shapes["near"] == "S0"
    assert shapes["near2"] == "S0"


def test_evidence_downgrade_keeps_candidate_and_attach_diagnostic(tmp_path):
    f = _uncertain_s0(id="s5", fingerprint="s5fp", evidence_downgrade="excerpt unbound")
    state = _roundtrip(tmp_path, [f])
    proj = classify_report_records(state)
    assert len(proj.unresolved_product_candidates) == 1
    rec = proj.unresolved_product_candidates[0]
    assert rec.shape == "S5"
    assert rec.associated_diagnostics == (
        AssociatedDiagnostic("evidence-downgrade", "excerpt unbound"),
    )


def test_s4_and_s5_double_hit_one_record_two_diagnostics_counted_once(tmp_path):
    err = "falsify() raised: Boom"
    f = _uncertain_s0(
        id="both",
        fingerprint="bothfp",
        error=err,
        evidence_downgrade="downgrade",
    )
    state = _roundtrip(tmp_path, [f])
    proj = classify_report_records(state)
    assert len(proj.unresolved_product_candidates) == 1
    assert proj.unresolved_candidate_count == 1
    rec = proj.unresolved_product_candidates[0]
    assert rec.shape == "S4+S5"
    assert rec.associated_diagnostics == (
        AssociatedDiagnostic("falsifier-failure", err),
        AssociatedDiagnostic("evidence-downgrade", "downgrade"),
    )


@pytest.mark.parametrize(
    "kind",
    ["x1_conn", "x2_receipt_invalid", "schema_fail", "incomplete_coverage", "coverage", "mutation_skipped_dismissed"],
)
def test_deferred_shapes_stay_s0_on_product_path(tmp_path, kind):
    if kind == "x1_conn":
        # WP1: X1 LABEL-ONLY still S0 product; provider_failure makes label exercisable.
        f = _x1_invoke_fail("expert")
        f = _sf(
            id=f.id,
            fingerprint=f.fingerprint,
            source=f.source,
            disposition=f.disposition,
            file=f.file,
            line_range=f.line_range,
            description=f.description,
            provider_failure={"kind": "conn"},
        )
    elif kind == "x2_receipt_invalid":
        f = _x2_receipt_invalid()
    elif kind == "schema_fail":
        f = _sf(id="l1-expert-schema-fail", fingerprint="s", source="INFRA", disposition=Disposition.CONFIRMED, file="<s>", line_range=[0, 0], description="s")
    elif kind == "incomplete_coverage":
        f = _sf(id="l1-expert-incomplete-coverage", fingerprint="i", source="INFRA", disposition=Disposition.CONFIRMED, file="<i>", line_range=[0, 0], description="i")
    elif kind == "coverage":
        f = _sf(id="cov", fingerprint="cov", source="COVERAGE", disposition=Disposition.CONFIRMED, description="cov")
    else:
        f = _mutation_skipped_dismissed()
    state = _roundtrip(tmp_path, [f])
    proj = classify_report_records(state)
    bucket = proj.product + proj.unresolved_product_candidates
    assert len(bucket) == 1
    assert bucket[0].shape == "S0"
    assert bucket[0].selector == "reporting_product_findings"


def test_duplicate_id_fingerprint_and_multi_chunk_are_not_promoted(tmp_path):
    a = _product_s0(id="same", fingerprint="fp-a")
    b = _product_s0(id="same", fingerprint="fp-b", description="other")
    state = _roundtrip(tmp_path, [a, b])
    proj = classify_report_records(state)
    assert len(proj.product) == 2
    assert {r.fingerprint for r in proj.product} == {"fp-a", "fp-b"}
    assert all(r.shape == "S0" for r in proj.product)


@pytest.mark.parametrize("direction", ["not_p1_no_row", "p1_and_row"])
def test_shape_contradiction_both_directions(tmp_path, monkeypatch, direction):
    import code_forge.report_projection as rp
    from code_forge import state as st_mod

    findings = [_product_s0(id="t0"), _product_s0(id="t1", fingerprint="t1fp"), _s3_receipt_audit()]
    state = _roundtrip(tmp_path, findings)
    baseline = classify_report_records(copy.deepcopy(state))
    target_idx = 0

    with monkeypatch.context() as m:
        if direction == "not_p1_no_row":
            real = st_mod.reporting_product_findings

            def wrap(fs):
                out = real(fs)
                return [x for x in out if x is not state.findings[target_idx]]

            m.setattr(rp, "reporting_product_findings", wrap)
        else:
            real = st_mod.is_receipt_audit

            def wrap(f):
                if f is state.findings[target_idx]:
                    return True
                return real(f)

            m.setattr(rp, "is_receipt_audit", wrap)
        proj = classify_report_records(state)

    assert proj.classification_complete is False
    assert proj.classification_incomplete_reasons == ("record-fault",)
    assert any(
        e.index == target_idx and e.stage == "shape" and e.error_type == "_ShapeContradiction"
        for e in proj.classification_errors
    )
    q = [f for f in proj.quarantine if f.index == target_idx]
    assert len(q) == 1 and q[0].shape is None
    # other records equal baseline (by index)
    base_by_idx = {
        r.index: r
        for r in baseline.product
        + baseline.unresolved_product_candidates
        + baseline.execution
        + baseline.gate
    }
    for bucket in (proj.product, proj.unresolved_product_candidates, proj.execution, proj.gate):
        for r in bucket:
            if r.index == target_idx:
                continue
            assert r == base_by_idx[r.index]


def test_count_invariants_hold_with_quarantined_product_records(tmp_path, monkeypatch):
    import code_forge.report_projection as rp

    findings = [_product_s0(id="ok"), _product_s0(id="bad", fingerprint="badfp"), _uncertain_s0()]
    state = _roundtrip(tmp_path, findings)
    target = state.findings[1]

    real = rp._build_record

    def boom(index, f, shape, selector, diags):
        if f is target:
            raise RuntimeError("boom")
        return real(index, f, shape, selector, diags)

    with monkeypatch.context() as m:
        m.setattr(rp, "_build_record", boom)
        proj = classify_report_records(state)

    assert proj.product_list_count is not None
    assert proj.unresolved_candidate_count is not None
    assert proj.product_path_record_count is not None
    q_product = sum(1 for q in proj.quarantine if q.index in (0, 1, 2))  # all are product-path
    # only index 1 quarantined; 0 and 2 classified
    assert len(proj.quarantine) == 1
    assert (
        proj.product_list_count
        + proj.unresolved_candidate_count
        + len(proj.quarantine)
        == proj.product_path_record_count
    )


def test_count_invariants_not_asserted_when_counts_none(tmp_path, monkeypatch):
    import code_forge.report_projection as rp

    state = _roundtrip(tmp_path, [_product_s0()])

    def boom(*a, **k):
        raise RuntimeError("p1")

    with monkeypatch.context() as m:
        m.setattr(rp, "reporting_product_findings", boom)
        proj = classify_report_records(state)
    assert proj.product_list_count is None
    assert proj.unresolved_candidate_count is None
    assert proj.product_path_record_count is None
    assert proj.classification_incomplete_reasons == ("projection-fault",)


# ---------------------------------------------------------------------------
# Type rejection (F16)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "case",
    [
        "line_range_str",
        "line_range_bool_item",
        "description_int",
        "file_none",
        "disposition_str",
        "error_int",
        "evidence_downgrade_list",
    ],
)
def test_report_record_rejects_wrong_field_types(tmp_path, case):
    if case == "disposition_str":
        base = State(findings=[_product_s0()])
        d = tmp_path / "d"
        d.mkdir()
        save_state(base, d / "state.json")
        loaded = load_state(d / "state.json")
        assert loaded is not None
        orig = loaded.findings[0]
        loaded.findings[0] = replace(orig, disposition=orig.disposition.value)  # type: ignore[arg-type]
        proj = classify_report_records(loaded)
        assert proj.classification_complete is False
        assert any(e.stage == "record" for e in proj.classification_errors)
        assert len(proj.quarantine) == 1
        return

    # Hand-edit state file after save for wrong JSON types that still load.
    base_findings = [_uncertain_s0(id="t", fingerprint="t", error="falsify() raised: x") if case.startswith("error") or case.startswith("evidence") else _product_s0()]
    if case == "evidence_downgrade_list":
        base_findings = [_uncertain_s0(id="t", fingerprint="t", evidence_downgrade="ok")]
    d = tmp_path / "d"
    d.mkdir()
    path = d / "state.json"
    save_state(State(findings=base_findings), path)
    import json

    doc = json.loads(path.read_text())
    f0 = doc["findings"][0]
    if case == "line_range_str":
        f0["line_range"] = "12"
    elif case == "line_range_bool_item":
        f0["line_range"] = [True, False]
    elif case == "description_int":
        f0["description"] = 7
    elif case == "file_none":
        f0["file"] = None
    elif case == "error_int":
        f0["error"] = 99
        f0["disposition"] = "UNCERTAIN"
    elif case == "evidence_downgrade_list":
        f0["evidence_downgrade"] = ["x"]
        f0["disposition"] = "UNCERTAIN"
    path.write_text(json.dumps(doc))
    loaded = load_state(path)
    assert loaded is not None
    proj = classify_report_records(loaded)
    assert proj.classification_complete is False
    assert len(proj.quarantine) == 1
    stages = {e.stage for e in proj.classification_errors}
    if case in ("error_int", "evidence_downgrade_list"):
        assert "shape" in stages
    else:
        assert "record" in stages


# ---------------------------------------------------------------------------
# F8 stage faults
# ---------------------------------------------------------------------------

def _fault_free_and_patched(tmp_path, monkeypatch, patch_name, target_builder, raise_on):
    import code_forge.report_projection as rp

    findings = [
        _product_s0(id="keep", fingerprint="keep"),
        target_builder(),
        _product_s0(id="keep2", fingerprint="keep2"),
    ]
    state = _roundtrip(tmp_path, findings)
    baseline = classify_report_records(copy.deepcopy(state))
    target = state.findings[1]
    real = getattr(rp, patch_name)

    def wrapper(*a, **k):
        # stage fns take finding as 2nd arg (index, f, ...) or 1st (_select_shape, _attach)
        finding = a[0] if patch_name in ("_select_shape", "_attach_diagnostics") else a[1]
        if finding is target:
            raise RuntimeError("injected")
        return real(*a, **k)

    with monkeypatch.context() as m:
        m.setattr(rp, patch_name, wrapper)
        proj = classify_report_records(state)
    return state, baseline, proj, 1


def test_fault_before_shape_quarantines_record_keeps_others(tmp_path, monkeypatch):
    state, baseline, proj, idx = _fault_free_and_patched(
        tmp_path, monkeypatch, "_select_shape", lambda: _product_s0(id="t", fingerprint="t"), None
    )
    assert proj.classification_complete is False
    assert proj.classification_incomplete_reasons == ("record-fault",)
    assert ClassificationError(idx, "shape", "RuntimeError") in proj.classification_errors
    assert any(q.index == idx and q.shape is None for q in proj.quarantine)
    base_map = {r.index: r for r in baseline.product + baseline.unresolved_product_candidates + baseline.execution + baseline.gate}
    for r in proj.product + proj.unresolved_product_candidates + proj.execution + proj.gate:
        assert r == base_map[r.index]
    assert proj.acquisition_complete == baseline.acquisition_complete
    assert proj.acquisition_reason == baseline.acquisition_reason


def test_fault_during_diagnostic_attach_quarantines_record_keeps_others(tmp_path, monkeypatch):
    state, baseline, proj, idx = _fault_free_and_patched(
        tmp_path,
        monkeypatch,
        "_attach_diagnostics",
        lambda: _uncertain_s0(id="t", fingerprint="t", evidence_downgrade="d"),
        None,
    )
    assert ClassificationError(idx, "diagnostics", "RuntimeError") in proj.classification_errors
    assert any(q.index == idx for q in proj.quarantine)


def test_fault_during_record_construction_quarantines_record_keeps_others(tmp_path, monkeypatch):
    state, baseline, proj, idx = _fault_free_and_patched(
        tmp_path, monkeypatch, "_build_record", lambda: _product_s0(id="t", fingerprint="t"), None
    )
    assert ClassificationError(idx, "record", "RuntimeError") in proj.classification_errors


@pytest.mark.parametrize("which", ["P1", "P2"])
def test_projection_level_fault_all_fallback_counts_none(tmp_path, monkeypatch, which):
    import code_forge.report_projection as rp

    state = _roundtrip(tmp_path, [_product_s0(), _s1_capacity(), _s3_receipt_audit()])
    with monkeypatch.context() as m:
        if which == "P1":
            m.setattr(rp, "reporting_product_findings", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("p1")))
        else:
            m.setattr(rp, "_assemble", lambda *a, **k: (_ for _ in ()).throw(ValueError("p2")))
        proj = classify_report_records(state)
    assert proj.classification_complete is False
    assert "projection-fault" in proj.classification_incomplete_reasons
    assert proj.product_list_count is None
    assert len(proj.quarantine) == 3
    assert proj.product == () and proj.execution == () and proj.gate == ()
    assert any(e.index is None and e.stage == "projection" for e in proj.classification_errors)


def test_mixed_record_and_projection_fault_merge(tmp_path, monkeypatch):
    import code_forge.report_projection as rp

    state = _roundtrip(tmp_path, [_product_s0(id="a"), _product_s0(id="b", fingerprint="b"), _product_s0(id="c", fingerprint="c")])
    target = state.findings[1]
    real_build = rp._build_record
    kept = {}

    def build_wrap(index, f, shape, selector, diags):
        if f is target:
            raise RuntimeError("record")
        return real_build(index, f, shape, selector, diags)

    real_assemble = rp._assemble

    def assemble_wrap(records, fallbacks, path_count):
        for fb in fallbacks:
            if fb.index == 1:
                kept["fb"] = fb
        raise ValueError("proj")

    with monkeypatch.context() as m:
        m.setattr(rp, "_build_record", build_wrap)
        m.setattr(rp, "_assemble", assemble_wrap)
        proj = classify_report_records(state)

    assert proj.classification_incomplete_reasons == ("record-fault", "projection-fault")
    assert proj.classification_errors == (
        ClassificationError(1, "record", "RuntimeError"),
        ClassificationError(None, "projection", "ValueError"),
    )
    assert len(proj.quarantine) == 3
    assert proj.product_list_count is None
    q1 = [q for q in proj.quarantine if q.index == 1][0]
    assert q1 is kept["fb"]


# ---------------------------------------------------------------------------
# Acquisition faults
# ---------------------------------------------------------------------------

def test_acquisition_derive_fault_keeps_records(tmp_path, monkeypatch):
    import code_forge.report_projection as rp

    state = _roundtrip(tmp_path, [_product_s0(), _uncertain_s0()])
    baseline = classify_report_records(copy.deepcopy(state))
    with monkeypatch.context() as m:
        m.setattr(rp, "derive_pass_outcomes", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("q1")))
        proj = classify_report_records(state)
    assert proj.acquisition_complete is None
    assert proj.acquisition_reason == "acquisition-derivation-fault"
    assert proj.product == baseline.product
    assert proj.unresolved_product_candidates == baseline.unresolved_product_candidates
    assert proj.classification_complete is True
    assert proj.product_list_count == baseline.product_list_count


@pytest.mark.parametrize("case", ["missing_pass", "extra_key", "raw_str_completed", "non_dict"])
def test_acquisition_malformed_derive_result_is_fault(tmp_path, monkeypatch, case):
    import code_forge.report_projection as rp
    from code_forge import state as st_mod

    state = _roundtrip(tmp_path, [_product_s0()])
    real = st_mod.derive_pass_outcomes

    def wrap(findings):
        out = real(findings)
        if case == "missing_pass":
            del out["adversarial"]
            return out
        if case == "extra_key":
            out = dict(out)
            out["extra"] = PassOutcome.COMPLETED
            return out
        if case == "raw_str_completed":
            out = dict(out)
            out["qodo"] = "completed"  # == PassOutcome.COMPLETED but type is str
            return out
        return [("qodo", PassOutcome.COMPLETED)]

    with monkeypatch.context() as m:
        m.setattr(rp, "derive_pass_outcomes", wrap)
        proj = classify_report_records(state)
    assert proj.acquisition_complete is None
    assert proj.acquisition_reason == "acquisition-derivation-fault"


def test_acquisition_s2_scan_fault_after_derive_success_keeps_classification(tmp_path, monkeypatch):
    import code_forge.report_projection as rp
    from code_forge import state as st_mod

    state = _roundtrip(tmp_path, [_product_s0(), _uncertain_s0()])
    baseline = classify_report_records(copy.deepcopy(state))
    armed = {"v": False}
    spy = {}

    def derive_wrap(findings):
        out = st_mod.derive_pass_outcomes(findings)
        spy["out"] = dict(out)
        armed["v"] = True
        return out

    def diag_wrap(f):
        if armed["v"]:
            raise RuntimeError("q4")
        return st_mod.is_provider_diagnostic(f)

    with monkeypatch.context() as m:
        m.setattr(rp, "derive_pass_outcomes", derive_wrap)
        m.setattr(rp, "is_provider_diagnostic", diag_wrap)
        proj = classify_report_records(state)

    assert all(v is PassOutcome.COMPLETED for v in spy["out"].values())
    assert proj.acquisition_complete is None
    assert proj.acquisition_reason == "acquisition-derivation-fault"
    assert proj.classification_complete is True
    assert proj.classification_errors == ()
    assert proj.product == baseline.product
    assert proj.unresolved_product_candidates == baseline.unresolved_product_candidates


def test_s2_predicate_fault_on_both_axes(tmp_path, monkeypatch):
    import code_forge.report_projection as rp

    state = _roundtrip(tmp_path, [_product_s0(), _uncertain_s0()])

    def boom(f):
        raise RuntimeError("diag")

    with monkeypatch.context() as m:
        m.setattr(rp, "is_provider_diagnostic", boom)
        # is_provider_capacity also called from row 2; leave it — row 2 calls capacity first
        # Actually row order: receipt_audit, capacity, then diagnostic. For product S0,
        # receipt_audit False, capacity False, then diagnostic raises → shape fault.
        m.setattr(rp, "is_provider_capacity", lambda f: False)
        m.setattr(rp, "is_receipt_audit", lambda f: False)
        proj = classify_report_records(state)

    assert proj.classification_complete is False
    assert proj.classification_incomplete_reasons == ("record-fault",)
    assert len(proj.quarantine) == 2
    assert all(e.stage == "shape" for e in proj.classification_errors)
    assert proj.product_list_count == 0
    assert proj.unresolved_candidate_count == 0
    assert proj.product_path_record_count == 2
    assert proj.acquisition_complete is None
    assert proj.acquisition_reason == "acquisition-derivation-fault"


def test_record_fault_does_not_change_acquisition(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    base_state = State(findings=[_product_s0(id="t"), _product_s0(id="other", fingerprint="o")])
    save_state(base_state, d / "state.json")
    base = load_state(d / "state.json")
    assert base is not None
    baseline = copy.deepcopy(base)
    orig = base.findings[0]
    names = [f.name for f in fields(StateFinding)]
    target = RaisingDescription(**{n: getattr(orig, n) for n in names})
    base.findings[0] = target
    proj = classify_report_records(base)
    base_proj = classify_report_records(baseline)
    assert proj.classification_complete is False
    assert any(e.stage == "record" and e.index == 0 for e in proj.classification_errors)
    assert proj.acquisition_complete == base_proj.acquisition_complete
    assert proj.acquisition_reason == base_proj.acquisition_reason
    # non-target equal
    assert [r for r in proj.product if r.index != 0] == [r for r in base_proj.product if r.index != 0]


@pytest.mark.parametrize(
    "where_exc",
    [
        ("Q1", MemoryError),
        ("Q1", RecursionError),
        ("Q4", KeyboardInterrupt),
        ("Q4", SystemExit),
    ],
)
def test_acquisition_resource_and_control_flow_propagate(tmp_path, monkeypatch, where_exc):
    import code_forge.report_projection as rp
    from code_forge import state as st_mod

    where, Exc = where_exc
    state = _roundtrip(tmp_path, [_product_s0()])
    armed = {"v": False}

    def derive_wrap(findings):
        if where == "Q1":
            raise Exc()
        out = st_mod.derive_pass_outcomes(findings)
        armed["v"] = True
        return out

    def diag_wrap(f):
        if where == "Q4" and armed["v"]:
            raise Exc()
        return st_mod.is_provider_diagnostic(f)

    with monkeypatch.context() as m:
        m.setattr(rp, "derive_pass_outcomes", derive_wrap)
        m.setattr(rp, "is_provider_diagnostic", diag_wrap)
        with pytest.raises(Exc):
            classify_report_records(state)


def test_fallback_copies_only_safely_readable_fields_and_never_reclassifies(tmp_path, monkeypatch):
    import code_forge.report_projection as rp

    d = tmp_path / "d"
    d.mkdir()
    base_state = State(findings=[_product_s0(id="t"), _product_s0(id="o", fingerprint="o")])
    save_state(base_state, d / "state.json")
    base = load_state(d / "state.json")
    assert base is not None
    orig = base.findings[0]
    names = [f.name for f in fields(StateFinding)]
    kwargs = {n: getattr(orig, n) for n in names}
    kwargs["file"] = 7  # wrong type on target only
    target = RaisingDescription(**kwargs)
    base.findings[0] = target

    stage_calls: list[tuple] = []

    def spy(name, real):
        def wrap(*a, **k):
            finding = None
            if name in ("_select_shape", "_attach_diagnostics"):
                finding = a[0]
            elif name == "_build_record":
                finding = a[1]
            elif name in ("is_receipt_audit", "is_provider_capacity", "is_provider_diagnostic"):
                finding = a[0]
            stage_calls.append((name, finding))
            return real(*a, **k)

        return wrap

    with monkeypatch.context() as m:
        for name in ("_select_shape", "_attach_diagnostics", "_build_record", "is_receipt_audit", "is_provider_capacity", "is_provider_diagnostic"):
            m.setattr(rp, name, spy(name, getattr(rp, name)))
        # Force record fault via raising description during _build_record
        proj = classify_report_records(base)

    assert len(proj.quarantine) == 1
    fb = proj.quarantine[0]
    assert fb.description is None
    assert fb.file is None
    assert fb.finding_id == "t"
    # After fault, no classification-stage call should receive target again
    # Find first fault moment: first _build_record on target that... actually description raises inside _build_record
    seen_fault = False
    post_fault_classif = []
    for name, finding in stage_calls:
        if finding is target and name == "_build_record":
            seen_fault = True
            continue
        if seen_fault and finding is target and name in (
            "_select_shape",
            "_attach_diagnostics",
            "_build_record",
            "is_receipt_audit",
            "is_provider_capacity",
            "is_provider_diagnostic",
        ):
            # acquisition may still call predicates — only flag classification stages
            if name in ("_select_shape", "_attach_diagnostics", "_build_record"):
                post_fault_classif.append(name)
            # predicates after fault during classification would be bad; during acquisition OK
    assert post_fault_classif == []


@pytest.mark.parametrize("Exc", [MemoryError, RecursionError])
def test_resource_errors_propagate(tmp_path, monkeypatch, Exc):
    import code_forge.report_projection as rp

    state = _roundtrip(tmp_path, [_product_s0()])

    def boom(*a, **k):
        raise Exc()

    with monkeypatch.context() as m:
        m.setattr(rp, "_select_shape", boom)
        with pytest.raises(Exc):
            classify_report_records(state)


@pytest.mark.parametrize("Exc", [KeyboardInterrupt, SystemExit])
def test_control_flow_exceptions_propagate(tmp_path, monkeypatch, Exc):
    import code_forge.report_projection as rp

    state = _roundtrip(tmp_path, [_product_s0()])

    def boom(*a, **k):
        raise Exc()

    with monkeypatch.context() as m:
        m.setattr(rp, "_select_shape", boom)
        with pytest.raises(Exc):
            classify_report_records(state)


def test_fault_path_writes_nothing(tmp_path, monkeypatch):
    import code_forge.report_projection as rp

    state = _roundtrip(tmp_path, [_product_s0(), _product_s0(id="b", fingerprint="b")])
    target = state.findings[0]
    real = rp._build_record

    def boom(index, f, shape, selector, diags):
        if f is target:
            raise RuntimeError("x")
        return real(index, f, shape, selector, diags)

    with monkeypatch.context() as m:
        m.setattr(rp, "_build_record", boom)
        calls = _write_api_guard(m)
        classify_report_records(state)
    assert calls == []


@pytest.mark.parametrize(
    "kind",
    ["fault_free", "record_fault", "projection_fault", "acquisition_fault", "legacy_survivor"],
)
def test_adapter_call_leaves_state_file_and_receipts_unchanged(tmp_path, monkeypatch, kind):
    import code_forge.report_projection as rp
    from code_forge import state as st_mod

    d = tmp_path / "D"
    d.mkdir()
    path = d / "state.json"
    findings = [_product_s0(), _uncertain_s0(), _s1_capacity()]
    if kind == "legacy_survivor":
        state = State(findings=findings, consecutive_survivor_rounds=2)
        # omit mutation_survivor_counter_version by loading from hand-edited JSON
        save_state(state, path)
        import json

        doc = json.loads(path.read_text())
        doc.pop("mutation_survivor_counter_version", None)
        path.write_text(json.dumps(doc))
        state = load_state(path)
        assert state is not None
        assert state._legacy_survivor_state is not None
    else:
        state = State(findings=findings)
        save_state(state, path)
        state = load_state(path)
        assert state is not None

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    scratch_path = scratch / "state.json"

    # Before
    save_state(state, scratch_path)
    bytes_saved_before = scratch_path.read_bytes()
    if kind == "legacy_survivor":
        assert state._legacy_survivor_state is None  # cleared by scratch save
    snap = copy.deepcopy(state)
    priv = {
        "_legacy_survivor_state": state._legacy_survivor_state,
        "_earned_window_present": state._earned_window_present,
        "_clean_state_sha256": state._clean_state_sha256,
    }
    findings_list_before = state.findings
    refs_before = list(state.findings)
    bytes_file_before = path.read_bytes()
    tree_before = _tree_hash(tmp_path)

    with monkeypatch.context() as m:
        if kind == "record_fault":
            target = state.findings[0]
            real = rp._build_record

            def boom(index, f, shape, selector, diags):
                if f is target:
                    raise RuntimeError("x")
                return real(index, f, shape, selector, diags)

            m.setattr(rp, "_build_record", boom)
        elif kind == "projection_fault":
            m.setattr(rp, "reporting_product_findings", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("p1")))
        elif kind == "acquisition_fault":
            m.setattr(rp, "derive_pass_outcomes", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("q1")))
        calls = _write_api_guard(m)
        classify_report_records(state)
    assert calls == []
    assert state == snap
    assert state._legacy_survivor_state == priv["_legacy_survivor_state"]
    assert state._earned_window_present == priv["_earned_window_present"]
    assert state._clean_state_sha256 == priv["_clean_state_sha256"]
    assert state.findings is findings_list_before
    assert all(a is b for a, b in zip(state.findings, refs_before))
    assert path.read_bytes() == bytes_file_before
    assert _tree_hash(tmp_path) == tree_before
    # fresh scratch save after tree compare
    save_state(state, scratch_path)
    assert scratch_path.read_bytes() == bytes_saved_before


@pytest.mark.parametrize("case", ["raising_description", "disposition_str", "raising_description_file_int"])
def test_in_memory_fault_objects_reduced_invariance(tmp_path, monkeypatch, case):
    d = tmp_path / "D"
    d.mkdir()
    path = d / "state.json"
    save_state(State(findings=[_product_s0(id="t"), _product_s0(id="o", fingerprint="o")]), path)
    base = load_state(path)
    assert base is not None
    baseline_state = copy.deepcopy(base)
    orig = base.findings[0]
    names = [f.name for f in fields(StateFinding)]
    if case == "disposition_str":
        target = replace(orig, disposition=orig.disposition.value)  # type: ignore[arg-type]
    elif case == "raising_description":
        target = RaisingDescription(**{n: getattr(orig, n) for n in names})
    else:
        kw = {n: getattr(orig, n) for n in names}
        kw["file"] = 7
        target = RaisingDescription(**kw)
    base.findings[0] = target

    bytes_file_before = path.read_bytes()
    tree_before = _tree_hash(tmp_path)
    findings_list_before = base.findings
    refs_before = list(base.findings)
    nonfinding_before = {n: copy.deepcopy(getattr(base, n)) for n in [f.name for f in fields(State)] if n != "findings"}
    target_vars_before = copy.deepcopy(dict(vars(target)))
    per_field = []
    for f in base.findings:
        entry = {}
        for fld in fields(StateFinding):
            try:
                entry[fld.name] = copy.deepcopy(getattr(f, fld.name))
            except Exception as exc:
                entry[fld.name] = type(exc)
        per_field.append(entry)
    nontarget_copies = [copy.deepcopy(f) for f in base.findings[1:]]

    with monkeypatch.context() as m:
        calls = _write_api_guard(m)
        classify_report_records(base)
    assert calls == []
    assert path.read_bytes() == bytes_file_before
    assert _tree_hash(tmp_path) == tree_before
    assert base.findings is findings_list_before
    assert all(a is b for a, b in zip(base.findings, refs_before))
    assert dict(vars(target)) == target_vars_before
    for n, v in nonfinding_before.items():
        assert getattr(base, n) == v
    for f, c in zip(base.findings[1:], nontarget_copies):
        assert f == c


def test_no_mutation_and_no_mutable_aliasing(tmp_path):
    state = _roundtrip(tmp_path, [_product_s0(), _uncertain_s0(error="falsify() raised: x"), _s1_capacity(), _s2_incomplete(), _s3_receipt_audit()])
    # collect mutable objects reachable from input
    mutable_ids = set()

    def walk(o, seen=None):
        if seen is None:
            seen = set()
        if id(o) in seen:
            return
        seen.add(id(o))
        t = type(o)
        if t in (list, dict, set) or t is StateFinding or t is State:
            mutable_ids.add(id(o))
        if t is list or t is tuple:
            for x in o:
                walk(x, seen)
        elif t is dict:
            for k, v in o.items():
                walk(k, seen)
                walk(v, seen)
        elif hasattr(o, "__dict__"):
            for v in vars(o).values():
                walk(v, seen)

    walk(state)
    proj1 = classify_report_records(state)
    proj2 = classify_report_records(state)
    assert proj1 == proj2

    def walk_out(o, seen=None):
        if seen is None:
            seen = set()
        if id(o) in seen:
            return
        seen.add(id(o))
        t = type(o)
        if t in (list, dict, set) or t is StateFinding or t is State:
            assert id(o) not in mutable_ids
        if isinstance(o, tuple):
            for x in o:
                walk_out(x, seen)
        elif hasattr(o, "__dataclass_fields__"):
            for f in fields(o):
                walk_out(getattr(o, f.name), seen)

    walk_out(proj1)

    c1 = copy.deepcopy(state)
    c2 = copy.deepcopy(state)
    a = classify_report_records(c1)
    b = classify_report_records(c2)
    assert a == b
    # order-swap
    c1b = copy.deepcopy(state)
    c2b = copy.deepcopy(state)
    b2 = classify_report_records(c2b)
    a2 = classify_report_records(c1b)
    assert a2 == b2 == a


# ---------------------------------------------------------------------------
# Phase 5 WP1 — parallel host_failure_kind (report-only; naming-domain b)
# 「未经独立复现」
# ---------------------------------------------------------------------------

def _x1_with_kind(kind: str, pass_name: str = "qodo") -> StateFinding:
    return _sf(
        id=f"l1-{pass_name}-invoke-fail",
        fingerprint=f"invoke-fail-{pass_name}",
        source="INFRA",
        disposition=Disposition.CONFIRMED,
        file="<llm-invoke>",
        line_range=[0, 0],
        description=f"x1 {kind}",
        provider_failure={"kind": kind},
        # diagnostic_kind None → not S1; stays product S0
    )


@pytest.mark.parametrize(
    "pf_kind,expected",
    [
        ("conn", "PROVIDER_TRANSPORT"),
        ("credentials", "PROVIDER_AUTH"),
        ("empty", "PROVIDER_EMPTY"),
        ("stub_model", "PROVIDER_EMPTY"),
        ("no_json", "PROVIDER_PROTOCOL"),
        ("sse_body", "PROVIDER_PROTOCOL"),
        ("bad_body", "PROVIDER_PROTOCOL"),
        ("protocol", "PROVIDER_PROTOCOL"),
    ],
)
def test_wp1_x1_host_failure_kind_label_only_stays_s0_product(tmp_path, pf_kind, expected):
    labeled = _x1_with_kind(pf_kind)
    twin = _x1_invoke_fail("qodo")  # unlabeled twin (no provider_failure)
    (tmp_path / "l").mkdir()
    (tmp_path / "t").mkdir()
    state_l = _roundtrip(tmp_path / "l", [labeled, _product_s0(id="anchor", fingerprint="a")])
    state_t = _roundtrip(tmp_path / "t", [twin, _product_s0(id="anchor", fingerprint="a")])
    proj_l = classify_report_records(state_l)
    proj_t = classify_report_records(state_t)
    bucket_l = proj_l.product + proj_l.unresolved_product_candidates
    bucket_t = proj_t.product + proj_t.unresolved_product_candidates
    assert len(bucket_l) == len(bucket_t) == 2
    assert proj_l.product_list_count == proj_t.product_list_count
    assert proj_l.product_path_record_count == proj_t.product_path_record_count
    assert len(proj_l.execution) == 0 and len(proj_l.gate) == 0
    rec = next(r for r in bucket_l if r.finding_id.startswith("l1-"))
    assert rec.shape == "S0"
    assert rec.channel == "product"
    assert rec.host_failure_kind == expected
    twin_rec = next(r for r in bucket_t if r.finding_id.startswith("l1-"))
    assert twin_rec.host_failure_kind is None


def test_wp1_x2_receipt_invalid_gate_never_capacity(tmp_path):
    state = _roundtrip(tmp_path, [_x2_receipt_invalid()])
    proj = classify_report_records(state)
    bucket = proj.product + proj.unresolved_product_candidates
    assert len(bucket) == 1
    rec = bucket[0]
    assert rec.shape == "S0"
    assert rec.host_failure_kind == "RECEIPT_INVALID_GATE"
    assert rec.host_failure_kind not in ("PROVIDER_CAPACITY", "CAPACITY_INCOMPLETE")
    assert len(proj.execution) == 0


def test_wp1_s1_s2_capacity_labels(tmp_path):
    state = _roundtrip(tmp_path, [_s1_capacity("adversarial"), _s2_incomplete()])
    proj = classify_report_records(state)
    assert len(proj.execution) == 1
    assert proj.execution[0].shape == "S1"
    assert proj.execution[0].host_failure_kind == "PROVIDER_CAPACITY"
    assert len(proj.gate) == 1
    assert proj.gate[0].shape == "S2"
    assert proj.gate[0].host_failure_kind == "CAPACITY_INCOMPLETE"


def test_wp1_integrity_outranks_capacity_when_both_signals(tmp_path):
    """X2 geometry + capacity-ish truncated signal → RECEIPT_INVALID_GATE, never capacity."""
    f = _sf(
        id="RECEIPT_INVALID",
        fingerprint="receipt-deadbeef0001",
        source="INFRA",
        disposition=Disposition.CONFIRMED,
        file="<receipt-evidence>",
        line_range=[0, 0],
        description="x2 with truncated hint",
        provider_failure={"kind": "truncated"},
        diagnostic_kind=FindingDiagnosticKind.PROVIDER_CAPACITY,
        # Wrong id/file for is_provider_capacity → stays product S0; X2 geometry wins.
    )
    state = _roundtrip(tmp_path, [f])
    proj = classify_report_records(state)
    bucket = proj.product + proj.unresolved_product_candidates
    assert len(bucket) == 1
    rec = bucket[0]
    assert rec.shape == "S0"
    assert rec.host_failure_kind == "RECEIPT_INVALID_GATE"
    assert rec.host_failure_kind != "PROVIDER_CAPACITY"
    assert rec.host_failure_kind != "CAPACITY_INCOMPLETE"


@pytest.mark.parametrize(
    "mode",
    ["no_pf", "truncated", "spawn", "timeout", "weird_kind", "hyphen_no_json"],
)
def test_wp1_unrecognized_or_non_label_provider_failure_kind_is_none(tmp_path, mode):
    if mode == "no_pf":
        f = _x1_invoke_fail("expert")
    elif mode == "hyphen_no_json":
        f = _x1_with_kind("no-json")
    else:
        f = _x1_with_kind(mode)
    state = _roundtrip(tmp_path, [f])
    proj = classify_report_records(state)
    bucket = proj.product + proj.unresolved_product_candidates
    assert len(bucket) == 1
    assert bucket[0].shape == "S0"
    assert bucket[0].host_failure_kind is None


def test_wp1_s0_product_default_host_failure_kind_none(tmp_path):
    state = _roundtrip(tmp_path, [_product_s0()])
    proj = classify_report_records(state)
    assert len(proj.product) == 1
    assert proj.product[0].host_failure_kind is None


def test_wp1_fallback_record_has_no_host_failure_kind_field():
    names = {f.name for f in fields(FallbackRecord)}
    assert "host_failure_kind" not in names


# ---------------------------------------------------------------------------
# IC-07-RAO WP-R1 — additive persisted_pass_outcomes on ReportProjection
# 「未经独立复现」 — projection field only; serialize/prepare = WP-R2
# ---------------------------------------------------------------------------


def _assert_non_completed_outcomes(proj: ReportProjection) -> None:
    assert proj.acquisition_reason == "pass-outcome-not-completed"
    assert proj.acquisition_complete is False
    assert proj.persisted_pass_outcomes is not None
    assert len(proj.persisted_pass_outcomes) == len(_PASS_NAMES)
    names = [n for n, _ in proj.persisted_pass_outcomes]
    assert names == list(_PASS_NAMES)
    for n, o in proj.persisted_pass_outcomes:
        assert type(n) is str and type(o) is str
    assert any(o != PassOutcome.COMPLETED.value for _, o in proj.persisted_pass_outcomes)


@pytest.mark.parametrize(
    "pf_kind,expected_label",
    [
        ("conn", "PROVIDER_TRANSPORT"),
        ("credentials", "PROVIDER_AUTH"),
        ("empty", "PROVIDER_EMPTY"),
        ("no_json", "PROVIDER_PROTOCOL"),
    ],
)
def test_wpr1_x1_persisted_pass_outcomes_non_null(tmp_path, pf_kind, expected_label):
    """X1 LABEL-ONLY: reason unchanged; projection carries real non-completed outcomes."""
    state = _roundtrip(tmp_path, [_x1_with_kind(pf_kind), _product_s0(id="anchor", fingerprint="a")])
    proj = classify_report_records(state)
    _assert_non_completed_outcomes(proj)
    bucket = proj.product + proj.unresolved_product_candidates
    rec = next(r for r in bucket if r.finding_id.startswith("l1-"))
    assert rec.host_failure_kind == expected_label
    # qodo invoke-fail → error (not timeout unless is_timeout)
    by_name = dict(proj.persisted_pass_outcomes)
    assert by_name["qodo"] != PassOutcome.COMPLETED.value


def test_wpr1_s1_persisted_pass_outcomes_non_null(tmp_path):
    """S1 PROVIDER_CAPACITY: reason unchanged; projection carries real non-completed outcomes."""
    state = _roundtrip(tmp_path, [_s1_capacity("adversarial"), _product_s0()])
    proj = classify_report_records(state)
    _assert_non_completed_outcomes(proj)
    assert len(proj.execution) == 1
    assert proj.execution[0].host_failure_kind == "PROVIDER_CAPACITY"
    by_name = dict(proj.persisted_pass_outcomes)
    assert by_name["adversarial"] != PassOutcome.COMPLETED.value


def test_wpr1_s2_capacity_incomplete_outcomes_null_on_projection(tmp_path):
    """Recoverable all-completed reasons leave projection field null (serialize recovers in WP-R2 path)."""
    state = _roundtrip(tmp_path, [_s2_incomplete(), _product_s0()])
    proj = classify_report_records(state)
    assert proj.acquisition_reason == "capacity-incomplete-record"
    assert proj.acquisition_complete is False
    assert proj.persisted_pass_outcomes is None


def test_wpr1_completion_not_provable_outcomes_null_on_projection(tmp_path):
    state = _roundtrip(tmp_path, [_product_s0()])
    proj = classify_report_records(state)
    assert proj.acquisition_reason == "completion-not-provable-from-state"
    assert proj.acquisition_complete is None
    assert proj.persisted_pass_outcomes is None


def test_wpr1_acquisition_derivation_fault_outcomes_null(tmp_path, monkeypatch):
    import code_forge.report_projection as rp

    state = _roundtrip(tmp_path, [_product_s0()])

    def boom(_findings):
        raise RuntimeError("forced derive fault")

    with monkeypatch.context() as m:
        m.setattr(rp, "derive_pass_outcomes", boom)
        proj = classify_report_records(state)
    assert proj.acquisition_reason == "acquisition-derivation-fault"
    assert proj.acquisition_complete is None
    assert proj.persisted_pass_outcomes is None


def test_wpr1_reason_selection_unchanged_for_x1_and_s1(tmp_path):
    """WP-R1 must not reinterpret X1/S1 into capacity-incomplete / completion-not-provable."""
    x1_dir = tmp_path / "x1"
    s1_dir = tmp_path / "s1"
    x1_dir.mkdir()
    s1_dir.mkdir()
    x1 = classify_report_records(_roundtrip(x1_dir, [_x1_with_kind("conn")]))
    s1 = classify_report_records(_roundtrip(s1_dir, [_s1_capacity("adversarial")]))
    assert x1.acquisition_reason == "pass-outcome-not-completed"
    assert s1.acquisition_reason == "pass-outcome-not-completed"
    assert x1.persisted_pass_outcomes is not None
    assert s1.persisted_pass_outcomes is not None


def test_wpr1_report_projection_has_persisted_pass_outcomes_field():
    names = {f.name for f in fields(ReportProjection)}
    assert "persisted_pass_outcomes" in names
