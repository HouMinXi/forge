# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Forge #332 IC-01 candidate pure renderer/serializer/reader (unwired).

Public pure interfaces only:
  prepare_legacy_report, prepare_report_views, parse_report_classification

Phase 1 DTO → wire mapping (actual shipped shapes, not Part B sketch):
  ReportRecord → RecordView.fields (18-key inventory) + ProductRecord /
    HostDiagnostic. Copied fields: id←finding_id, fingerprint, file,
    line_range (tuple→list), description, disposition. AssociatedDiagnostic
    reason is also copied into fields.error / fields.evidence_downgrade for
    §3.2 local coherence. Fields absent from ReportRecord (source, anchor,
    evidence_files, is_timeout, backend, severity, excerpt, falsify_reasoning,
    diagnostic_kind, provider_failure) are emitted as JSON null.
  ReportRecord.host_failure_kind (Phase 5 WP2, naming-domain b) is a
    parallel report-only sibling on ProductRecord / HostDiagnostic — NOT
    part of FIELD_INVENTORY / RecordView.fields. wire_to_json always emits
    the key (null or freeze-set string); parsers accept a missing key as
    None so pre-WP2 blocks still load.
  ReportRecord.channel/shape → destination list + HostDiagnostic.diagnostic
    (S1→provider-capacity; S2→capacity-incomplete; S3→receipt-audit;
    product-path S4/S5/S0 → ProductRecord associated_diagnostics mapped
    falsifier-failure→persisted-falsify-error, evidence-downgrade→
    persisted-evidence-downgrade; basis/scope per §3.1).
  FallbackRecord → WireFallbackRecord: safe_fields from non-None owned
    scalars; unavailable_fields = declaration-order complement of the
    18-key inventory; reason from the matching ClassificationError code.
  ClassificationError.stage shape|diagnostics|record → wire stage "record";
    projection → "projection". error_type (exception class name at pin) maps
    to record-computation-fault / projection-computation-fault (Phase 1 does
    not emit the Part C code literals). Wire-only render /
    renderer-computation-fault are added only by §6.1 renderer-fallback.
  ReportProjection acquisition_complete/reason copied. persisted_pass_outcomes
    is additive on Phase 1 DTO (IC-07-RAO WP-R1). For reasons that entail
    all-completed outcomes (completion-not-provable-from-state,
    capacity-incomplete-record) the serializer recovers the entailed triple.
    For pass-outcome-not-completed, serialize emits projection.persisted_pass_outcomes
    when present (non-null, ≥1 non-completed); when null, leave null (fail closed).

No paths, callbacks, clock, publication, logging, network, or workers.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Literal

from code_forge.report_projection import (
    AssociatedDiagnostic,
    ClassificationError,
    FallbackRecord,
    HostFailureKind,
    ReportProjection,
    ReportRecord,
)

JSONValue = None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]

SCHEMA_V1 = "forge-report-classification/v1"
PROJECTION_SCHEMA_V1 = "forge-report-projection-proposal-v1"
LEGACY_REPORT_STATE = Literal["accepted", "empty", "rejected"]

FIELD_INVENTORY: tuple[str, ...] = (
    "id",
    "fingerprint",
    "source",
    "disposition",
    "file",
    "line_range",
    "description",
    "error",
    "anchor",
    "evidence_files",
    "is_timeout",
    "backend",
    "severity",
    "excerpt",
    "falsify_reasoning",
    "evidence_downgrade",
    "diagnostic_kind",
    "provider_failure",
)

UNAVAILABLE_REASONS = frozenset(
    {
        "no-report",
        "legacy-report",
        "invalid-report",
        "unsupported-schema",
        "invalid-block",
        "budget-exceeded",
    }
)
DELIVERY_MODES = frozenset({"projection", "renderer-fallback"})
ACQ_REASONS = frozenset(
    {
        "pass-outcome-not-completed",
        "capacity-incomplete-record",
        "completion-not-provable-from-state",
        "attempt-binding-unavailable",
        "acquisition-input-incomplete",
        "acquisition-derivation-fault",
    }
)
PASS_NAMES = ("qodo", "expert", "adversarial")
PASS_OUTCOMES = frozenset({"completed", "timeout", "error", "schema_fail", "incomplete"})
RECORD_STAGE_CODES = frozenset(
    {
        "unsupported-record-type",
        "record-shape-invalid",
        "record-value-unsupported",
        "record-depth-limit",
        "record-computation-fault",
    }
)
PROJECTION_STAGE_CODES = frozenset(
    {
        "product-helper-fault",
        "product-helper-contract-invalid",
        "projection-computation-fault",
    }
)
RENDER_STAGE_CODES = frozenset({"renderer-computation-fault"})
OBS_KINDS_HOST_EXEC = frozenset({"provider-capacity"})
OBS_KINDS_HOST_GATE = frozenset({"capacity-incomplete", "receipt-audit"})
OBS_KINDS_ASSOC = frozenset({"persisted-falsify-error", "persisted-evidence-downgrade"})
# Phase 5 WP2 freeze set (subtype-include-table); parallel report-only labels.
HOST_FAILURE_KINDS: frozenset[str] = frozenset(
    {
        "PROVIDER_CAPACITY",
        "CAPACITY_INCOMPLETE",
        "PROVIDER_TRANSPORT",
        "PROVIDER_AUTH",
        "PROVIDER_EMPTY",
        "PROVIDER_PROTOCOL",
        "RECEIPT_INVALID_GATE",
    }
)

_MAX_SARIF_REPORT_CHARS = 1024 * 1024
_MAX_FIELD_CONTAINER_DEPTH = 64
_MAX_BLOCK_DEPTH = 69

_BUDGET_QUALIFIER = (
    "Report classification unavailable: additive report budget exceeded; legacy report preserved."
)


# ---------------------------------------------------------------------------
# Frozen outer / nested models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LegacyReportSubstrate:
    sarif_document: dict[str, JSONValue] | None
    stdout: str
    stderr: str


@dataclass(frozen=True)
class DiagnosticObservation:
    kind: str
    basis: str
    observation_scope: str
    text: str | None


@dataclass(frozen=True)
class RecordView:
    record_index: int
    fields: dict[str, JSONValue]


@dataclass(frozen=True)
class ProductRecord:
    record: RecordView
    associated_diagnostics: tuple[DiagnosticObservation, ...]
    # Parallel report-only label (Phase 5 WP2). Not a State / FIELD_INVENTORY key.
    host_failure_kind: HostFailureKind | None = None


@dataclass(frozen=True)
class HostDiagnostic:
    record: RecordView
    diagnostic: DiagnosticObservation
    # Parallel report-only label (Phase 5 WP2). Not a State / FIELD_INVENTORY key.
    host_failure_kind: HostFailureKind | None = None


@dataclass(frozen=True)
class WireFallbackRecord:
    record_index: int
    safe_fields: dict[str, JSONValue]
    unavailable_fields: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class WireClassificationError:
    record_index: int | None
    stage: str
    code: str


@dataclass(frozen=True)
class ReportClassificationWire:
    schema: str
    availability: str
    unavailable_reason: str | None
    delivery_mode: str | None
    projection_schema: str | None
    product: tuple[ProductRecord, ...] | None
    execution: tuple[HostDiagnostic, ...] | None
    gate: tuple[HostDiagnostic, ...] | None
    quarantine: tuple[WireFallbackRecord, ...] | None
    unresolved_product_candidates: tuple[ProductRecord, ...] | None
    classification_errors: tuple[WireClassificationError, ...] | None
    classification_complete: bool | None
    product_list_count: int | None
    unresolved_candidate_count: int | None
    product_path_record_count: int | None
    acquisition_complete: Literal[False] | None
    acquisition_reason: str | None
    persisted_pass_outcomes: tuple[tuple[str, str], ...] | None


@dataclass(frozen=True)
class PurePreparedReport:
    stdout: str
    stderr: str
    report_classification: ReportClassificationWire


# ---------------------------------------------------------------------------
# Safe JSON helpers (candidate-side; mirrors pinned legacy acceptance)
# ---------------------------------------------------------------------------


def _sarif_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate SARIF key")
        result[key] = value
    return result


def _sarif_float(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite SARIF number")
    return number


def _is_safe_json(value: Any, *, depth: int = 0) -> bool:
    if value is None or type(value) is bool or type(value) is str:
        return True
    if type(value) is int and not isinstance(value, bool):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) is list:
        if depth >= _MAX_FIELD_CONTAINER_DEPTH:
            return False
        return all(_is_safe_json(v, depth=depth + 1) for v in value)
    if type(value) is dict:
        if depth >= _MAX_FIELD_CONTAINER_DEPTH:
            return False
        return all(type(k) is str and _is_safe_json(v, depth=depth + 1) for k, v in value.items())
    return False


def _detach_json(value: Any) -> JSONValue:
    """Owned deep copy via JSON domain; rejects non-finite / non-JSON."""
    return json.loads(json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":")))


def _legacy_envelope_accepted(stdout: str) -> tuple[bool, dict[str, Any] | None]:
    """Mirror mcp_server._sarif_projection_with_status acceptance (read-only).

    Returns (accepted, document_or_None). Does not import mcp_server (side
    effects); replicates the pinned predicate for finite JSON + structure.
    """
    if type(stdout) is not str or len(stdout) > _MAX_SARIF_REPORT_CHARS:
        return False, None
    if stdout == "":
        return False, None
    try:
        document = json.loads(
            stdout,
            object_pairs_hook=_sarif_object,
            parse_float=_sarif_float,
            parse_constant=_sarif_float,
        )
        if type(document) is not dict or document.get("version") != "2.1.0":
            return False, None
        runs = document.get("runs")
        if type(runs) is not list or len(runs) != 1 or type(runs[0]) is not dict:
            return False, None
        run = runs[0]
        tool = run.get("tool")
        if type(tool) is not dict or type(tool.get("driver")) is not dict:
            return False, None
        if tool["driver"].get("name") != "code-forge":
            return False, None
        findings = run.get("results")
        if type(findings) is not list or any(type(row) is not dict for row in findings):
            return False, None
        properties = run.get("properties", {})
        if type(properties) is not dict:
            return False, None
        audit = properties.get("receiptAudit")
        if "receiptAudit" in properties and (
            type(audit) is not list or any(type(row) is not dict for row in audit)
        ):
            return False, None
        # UTF-8 / non-finite check on projected fields (same as pin)
        json.dumps((findings, audit), ensure_ascii=False, allow_nan=False).encode("utf-8")
        return True, document
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        return False, None


def _documents_represent_same(stdout_doc: Any, legacy_sarif: Any) -> bool:
    try:
        a = _detach_json(stdout_doc)
        b = _detach_json(legacy_sarif)
    except (TypeError, ValueError, OverflowError):
        return False
    return a == b


# ---------------------------------------------------------------------------
# Unavailable / available constructors
# ---------------------------------------------------------------------------


def _unavailable(reason: str) -> ReportClassificationWire:
    if reason not in UNAVAILABLE_REASONS:
        raise ValueError(f"unknown unavailable_reason: {reason}")
    return ReportClassificationWire(
        schema=SCHEMA_V1,
        availability="unavailable",
        unavailable_reason=reason,
        delivery_mode=None,
        projection_schema=None,
        product=None,
        execution=None,
        gate=None,
        quarantine=None,
        unresolved_product_candidates=None,
        classification_errors=None,
        classification_complete=None,
        product_list_count=None,
        unresolved_candidate_count=None,
        product_path_record_count=None,
        acquisition_complete=None,
        acquisition_reason=None,
        persisted_pass_outcomes=None,
    )


def wire_to_json(block: ReportClassificationWire) -> dict[str, JSONValue]:
    """Serialize a typed wire block to a plain JSON object (owned)."""
    if block.availability == "unavailable":
        return {
            "schema": block.schema,
            "availability": "unavailable",
            "unavailable_reason": block.unavailable_reason,
            "delivery_mode": None,
            "projection_schema": None,
            "product": None,
            "execution": None,
            "gate": None,
            "quarantine": None,
            "unresolved_product_candidates": None,
            "classification_errors": None,
            "classification_complete": None,
            "product_list_count": None,
            "unresolved_candidate_count": None,
            "product_path_record_count": None,
            "acquisition_complete": None,
            "acquisition_reason": None,
            "persisted_pass_outcomes": None,
        }

    def obs(o: DiagnosticObservation) -> dict[str, JSONValue]:
        return {
            "kind": o.kind,
            "basis": o.basis,
            "observation_scope": o.observation_scope,
            "text": o.text,
        }

    def record_view(rv: RecordView) -> dict[str, JSONValue]:
        return {"record_index": rv.record_index, "fields": dict(rv.fields)}

    def product(p: ProductRecord) -> dict[str, JSONValue]:
        # Always emit host_failure_kind (null|string) for stable schema; readers
        # must still treat a missing key as None (pre-WP2 / old writers).
        return {
            "record": record_view(p.record),
            "associated_diagnostics": [obs(d) for d in p.associated_diagnostics],
            "host_failure_kind": p.host_failure_kind,
        }

    def host(h: HostDiagnostic) -> dict[str, JSONValue]:
        return {
            "record": record_view(h.record),
            "diagnostic": obs(h.diagnostic),
            "host_failure_kind": h.host_failure_kind,
        }

    def fb(f: WireFallbackRecord) -> dict[str, JSONValue]:
        return {
            "record_index": f.record_index,
            "safe_fields": dict(f.safe_fields),
            "unavailable_fields": list(f.unavailable_fields),
            "reason": f.reason,
        }

    def err(e: WireClassificationError) -> dict[str, JSONValue]:
        return {"record_index": e.record_index, "stage": e.stage, "code": e.code}

    outcomes: list[list[str]] | None
    if block.persisted_pass_outcomes is None:
        outcomes = None
    else:
        outcomes = [[a, b] for a, b in block.persisted_pass_outcomes]

    return {
        "schema": block.schema,
        "availability": "available",
        "unavailable_reason": None,
        "delivery_mode": block.delivery_mode,
        "projection_schema": block.projection_schema,
        "product": [product(p) for p in (block.product or ())],
        "execution": [host(h) for h in (block.execution or ())],
        "gate": [host(h) for h in (block.gate or ())],
        "quarantine": [fb(f) for f in (block.quarantine or ())],
        "unresolved_product_candidates": [
            product(p) for p in (block.unresolved_product_candidates or ())
        ],
        "classification_errors": [err(e) for e in (block.classification_errors or ())],
        "classification_complete": block.classification_complete,
        "product_list_count": block.product_list_count,
        "unresolved_candidate_count": block.unresolved_candidate_count,
        "product_path_record_count": block.product_path_record_count,
        "acquisition_complete": block.acquisition_complete,
        "acquisition_reason": block.acquisition_reason,
        "persisted_pass_outcomes": outcomes,
    }


# ---------------------------------------------------------------------------
# Phase 1 → wire mapping
# ---------------------------------------------------------------------------


def _map_error_stage_code(err: ClassificationError) -> tuple[str, str]:
    if err.stage == "projection":
        return "projection", "projection-computation-fault"
    # shape / diagnostics / record → record stage
    return "record", "record-computation-fault"


def _empty_fields() -> dict[str, JSONValue]:
    return {k: None for k in FIELD_INVENTORY}


def _record_view_from_report_record(r: ReportRecord) -> RecordView:
    fields = _empty_fields()
    fields["id"] = r.finding_id
    fields["fingerprint"] = r.fingerprint
    fields["file"] = r.file
    fields["line_range"] = list(r.line_range)
    fields["description"] = r.description
    fields["disposition"] = r.disposition
    # Restore field slots implied by associated diagnostics (owned reason copy).
    for d in r.associated_diagnostics:
        if d.kind == "falsifier-failure":
            fields["error"] = d.reason
        elif d.kind == "evidence-downgrade":
            fields["evidence_downgrade"] = d.reason
    # is_timeout is boolean in domain; Phase 1 omits it → null (residual).
    return RecordView(record_index=r.index, fields=fields)


def _assoc_from_phase1(d: AssociatedDiagnostic) -> DiagnosticObservation:
    if d.kind == "falsifier-failure":
        kind = "persisted-falsify-error"
    elif d.kind == "evidence-downgrade":
        kind = "persisted-evidence-downgrade"
    else:
        raise ValueError(f"unknown AssociatedDiagnostic.kind: {d.kind}")
    return DiagnosticObservation(
        kind=kind,
        basis="persisted-field",
        observation_scope="persisted-record-unbound",
        text=d.reason,
    )


def _host_diag_for_shape(shape: str) -> DiagnosticObservation:
    if shape == "S1":
        kind = "provider-capacity"
    elif shape == "S2":
        kind = "capacity-incomplete"
    elif shape == "S3":
        kind = "receipt-audit"
    else:
        raise ValueError(f"not a host shape: {shape}")
    return DiagnosticObservation(
        kind=kind,
        basis="inherited-predicate",
        observation_scope="persisted-record-unbound",
        text=None,
    )


def _fallback_from_phase1(fb: FallbackRecord, reason: str) -> WireFallbackRecord:
    safe: dict[str, JSONValue] = {}
    if fb.finding_id is not None:
        safe["id"] = fb.finding_id
    if fb.fingerprint is not None:
        safe["fingerprint"] = fb.fingerprint
    if fb.file is not None:
        safe["file"] = fb.file
    if fb.line_range is not None:
        safe["line_range"] = list(fb.line_range)
    if fb.description is not None:
        safe["description"] = fb.description
    if fb.disposition is not None:
        safe["disposition"] = fb.disposition
    unavailable = tuple(k for k in FIELD_INVENTORY if k not in safe)
    return WireFallbackRecord(
        record_index=fb.index,
        safe_fields=safe,
        unavailable_fields=unavailable,
        reason=reason,
    )


def _product_from_report_record(r: ReportRecord) -> ProductRecord:
    return ProductRecord(
        record=_record_view_from_report_record(r),
        associated_diagnostics=tuple(_assoc_from_phase1(d) for d in r.associated_diagnostics),
        host_failure_kind=r.host_failure_kind,
    )


def _host_from_report_record(r: ReportRecord) -> HostDiagnostic:
    return HostDiagnostic(
        record=_record_view_from_report_record(r),
        diagnostic=_host_diag_for_shape(r.shape),
        host_failure_kind=r.host_failure_kind,
    )


def serialize_projection(
    projection: ReportProjection,
    *,
    delivery_mode: str = "projection",
) -> ReportClassificationWire:
    """Map a completed Phase 1 ReportProjection into a typed available wire block."""
    if delivery_mode not in DELIVERY_MODES:
        raise ValueError(delivery_mode)

    errors_by_index: dict[int, str] = {}
    wire_errors: list[WireClassificationError] = []
    for err in projection.classification_errors:
        stage, code = _map_error_stage_code(err)
        wire_errors.append(WireClassificationError(record_index=err.index, stage=stage, code=code))
        if err.index is not None:
            errors_by_index[err.index] = code

    # Global projection error code (null index), if any
    global_code: str | None = None
    for we in wire_errors:
        if we.record_index is None and we.stage == "projection":
            global_code = we.code
            break

    quarantine: list[WireFallbackRecord] = []
    for fb in projection.quarantine:
        if fb.index in errors_by_index:
            reason = errors_by_index[fb.index]
        elif global_code is not None:
            reason = global_code
        else:
            reason = "record-computation-fault"
        quarantine.append(_fallback_from_phase1(fb, reason))

    product = tuple(_product_from_report_record(r) for r in projection.product)
    unresolved = tuple(_product_from_report_record(r) for r in projection.unresolved_product_candidates)
    execution = tuple(_host_from_report_record(r) for r in projection.execution)
    gate = tuple(_host_from_report_record(r) for r in projection.gate)

    acq_complete = projection.acquisition_complete
    if acq_complete is not False and acq_complete is not None:
        raise ValueError("acquisition_complete must be False or None")
    acq_reason = projection.acquisition_reason
    # Recover outcomes for RA-O coherence without inventing pass results:
    # - completion-not-provable / capacity-incomplete → all-completed (reason entails it)
    # - pass-outcome-not-completed → emit additive projection.persisted_pass_outcomes
    #   when present (IC-07-RAO Option B / WP-R1); leave null if field is null (fail closed)
    if acq_reason in (
        "completion-not-provable-from-state",
        "capacity-incomplete-record",
    ):
        outcomes: tuple[tuple[str, str], ...] | None = tuple((n, "completed") for n in PASS_NAMES)
    elif acq_reason == "pass-outcome-not-completed":
        outcomes = projection.persisted_pass_outcomes
    else:
        outcomes = None
    return ReportClassificationWire(
        schema=SCHEMA_V1,
        availability="available",
        unavailable_reason=None,
        delivery_mode=delivery_mode,
        projection_schema=PROJECTION_SCHEMA_V1,
        product=product,
        execution=execution,
        gate=gate,
        quarantine=tuple(quarantine),
        unresolved_product_candidates=unresolved,
        classification_errors=tuple(wire_errors),
        classification_complete=projection.classification_complete,
        product_list_count=projection.product_list_count,
        unresolved_candidate_count=projection.unresolved_candidate_count,
        product_path_record_count=projection.product_path_record_count,
        acquisition_complete=acq_complete,
        acquisition_reason=acq_reason,
        persisted_pass_outcomes=outcomes,
    )


def build_renderer_fallback(projection: ReportProjection) -> ReportClassificationWire:
    """§6.1 renderer-fallback block from a completed Phase 1 projection."""
    base = serialize_projection(projection, delivery_mode="projection")
    # Collect every occurrence as WireFallbackRecord
    by_index: dict[int, WireFallbackRecord] = {}

    def as_fb_from_view(rv: RecordView, reason: str) -> WireFallbackRecord:
        safe = {k: v for k, v in rv.fields.items() if v is not None and k in FIELD_INVENTORY}
        # Keep false/0/"" — only drop None
        safe = {k: v for k, v in rv.fields.items() if v is not None}
        unavailable = tuple(k for k in FIELD_INVENTORY if k not in safe)
        return WireFallbackRecord(
            record_index=rv.record_index,
            safe_fields=dict(safe),
            unavailable_fields=unavailable,
            reason=reason,
        )

    for p in base.product or ():
        by_index[p.record.record_index] = as_fb_from_view(p.record, "renderer-computation-fault")
    for p in base.unresolved_product_candidates or ():
        by_index[p.record.record_index] = as_fb_from_view(p.record, "renderer-computation-fault")
    for h in base.execution or ():
        by_index[h.record.record_index] = as_fb_from_view(h.record, "renderer-computation-fault")
    for h in base.gate or ():
        by_index[h.record.record_index] = as_fb_from_view(h.record, "renderer-computation-fault")
    for q in base.quarantine or ():
        # Preserve original reason for already-faulted entries
        by_index[q.record_index] = q

    quarantine = tuple(by_index[i] for i in sorted(by_index))
    errors = list(base.classification_errors or ())
    errors.append(
        WireClassificationError(record_index=None, stage="render", code="renderer-computation-fault")
    )
    return ReportClassificationWire(
        schema=SCHEMA_V1,
        availability="available",
        unavailable_reason=None,
        delivery_mode="renderer-fallback",
        projection_schema=PROJECTION_SCHEMA_V1,
        product=(),
        execution=(),
        gate=(),
        quarantine=quarantine,
        unresolved_product_candidates=(),
        classification_errors=tuple(errors),
        classification_complete=False,
        product_list_count=None,
        unresolved_candidate_count=None,
        product_path_record_count=None,
        acquisition_complete=base.acquisition_complete,
        acquisition_reason=base.acquisition_reason,
        persisted_pass_outcomes=base.persisted_pass_outcomes,
    )


# ---------------------------------------------------------------------------
# Block validation (§3 / §3.2) — returns None if ok, else unavailable reason
# ---------------------------------------------------------------------------


class _BlockInvalid(Exception):
    pass


def _require(cond: bool, msg: str = "") -> None:
    if not cond:
        raise _BlockInvalid(msg)


def _container_depth(value: Any, depth: int = 1) -> int:
    """Depth where root array/object is 1."""
    if type(value) is dict:
        if not value:
            return depth
        return max(_container_depth(v, depth + 1) for v in value.values())
    if type(value) is list:
        if not value:
            return depth
        return max(_container_depth(v, depth + 1) for v in value)
    return depth - 1  # primitive: do not count


def _validate_obs(obs: Any, *, allowed_kinds: frozenset[str], assoc: bool) -> None:
    _require(type(obs) is dict)
    _require(set(obs.keys()) >= {"kind", "basis", "observation_scope", "text"})
    kind = obs.get("kind")
    basis = obs.get("basis")
    scope = obs.get("observation_scope")
    text = obs.get("text")
    _require(kind in allowed_kinds)
    _require(scope == "persisted-record-unbound")
    if assoc:
        _require(basis == "persisted-field")
        _require(type(text) is str)
    else:
        _require(basis == "inherited-predicate")
        _require(text is None)


def _validate_record_view(rv: Any, *, full_domain: bool) -> None:
    _require(type(rv) is dict)
    _require(type(rv.get("record_index")) is int and not isinstance(rv.get("record_index"), bool))
    _require(rv["record_index"] >= 0)
    fields = rv.get("fields")
    _require(type(fields) is dict)
    _require(set(fields.keys()) == set(FIELD_INVENTORY))
    for k, v in fields.items():
        _require(_is_safe_json(v), f"unsafe field {k}")
        if type(v) in (list, dict):
            _require(_container_depth(v) <= _MAX_FIELD_CONTAINER_DEPTH)
    if not full_domain:
        return
    # Full domain for successful RecordViews (IC-01 residual: source may be
    # null when produced from shipped Phase 1 ReportRecord; still require
    # id/fingerprint/file/description strings and disposition enum string).
    for req in ("id", "fingerprint", "file", "description"):
        _require(type(fields.get(req)) is str, req)
    disp = fields.get("disposition")
    _require(
        disp in ("CONFIRMED", "DISMISSED", "UNCERTAIN", "FIXED", "STYLE"),
        "disposition",
    )
    lr = fields.get("line_range")
    _require(type(lr) is list and all(type(x) is int and not isinstance(x, bool) for x in lr))
    _require(fields.get("is_timeout") is None or type(fields.get("is_timeout")) is bool)
    # source: preferred str; null tolerated as Phase 1 residual (documented).
    src = fields.get("source")
    _require(src is None or type(src) is str)


def _validate_host_failure_kind_field(obj: dict[str, Any]) -> None:
    """Additive sibling: missing key OK (old writers); null OK; freeze-set only."""
    if "host_failure_kind" not in obj:
        return
    v = obj["host_failure_kind"]
    _require(v is None or (type(v) is str and v in HOST_FAILURE_KINDS), "host_failure_kind")


def _validate_product(obj: Any) -> None:
    _require(type(obj) is dict)
    _validate_host_failure_kind_field(obj)
    _validate_record_view(obj.get("record"), full_domain=True)
    diags = obj.get("associated_diagnostics")
    _require(type(diags) is list)
    fields = obj["record"]["fields"]
    expected: list[dict[str, JSONValue]] = []
    err = fields.get("error")
    if type(err) is str and err.startswith("falsify()"):
        expected.append(
            {
                "kind": "persisted-falsify-error",
                "basis": "persisted-field",
                "observation_scope": "persisted-record-unbound",
                "text": err,
            }
        )
    ed = fields.get("evidence_downgrade")
    if type(ed) is str and ed != "":
        expected.append(
            {
                "kind": "persisted-evidence-downgrade",
                "basis": "persisted-field",
                "observation_scope": "persisted-record-unbound",
                "text": ed,
            }
        )
    _require(len(diags) == len(expected))
    for got, exp in zip(diags, expected, strict=False):
        _validate_obs(got, allowed_kinds=OBS_KINDS_ASSOC, assoc=True)
        _require(got["kind"] == exp["kind"])
        _require(got["text"] == exp["text"])


def _validate_host(obj: Any, *, gate: bool) -> None:
    _require(type(obj) is dict)
    _validate_host_failure_kind_field(obj)
    _validate_record_view(obj.get("record"), full_domain=True)
    allowed = OBS_KINDS_HOST_GATE if gate else OBS_KINDS_HOST_EXEC
    _validate_obs(obj.get("diagnostic"), allowed_kinds=allowed, assoc=False)


def _validate_fallback(obj: Any) -> None:
    _require(type(obj) is dict)
    _require(type(obj.get("record_index")) is int and obj["record_index"] >= 0)
    safe = obj.get("safe_fields")
    _require(type(safe) is dict)
    for k, v in safe.items():
        _require(k in FIELD_INVENTORY)
        _require(_is_safe_json(v))
    unavail = obj.get("unavailable_fields")
    _require(type(unavail) is list)
    _require(all(type(x) is str for x in unavail))
    # Complement in declaration order
    expected_unavail = [k for k in FIELD_INVENTORY if k not in safe]
    _require(unavail == expected_unavail)
    _require(not (set(safe) & set(unavail)))
    reason = obj.get("reason")
    _require(
        reason in RECORD_STAGE_CODES or reason in PROJECTION_STAGE_CODES or reason in RENDER_STAGE_CODES
    )


def _validate_error(obj: Any) -> None:
    _require(type(obj) is dict)
    stage = obj.get("stage")
    code = obj.get("code")
    idx = obj.get("record_index")
    if stage == "record":
        _require(code in RECORD_STAGE_CODES)
        _require(type(idx) is int and idx >= 0)
    elif stage == "projection":
        _require(code in PROJECTION_STAGE_CODES)
        _require(idx is None)
    elif stage == "render":
        _require(code in RENDER_STAGE_CODES)
        _require(idx is None)
    else:
        raise _BlockInvalid("bad stage")


def _validate_acq(complete: Any, reason: Any, outcomes: Any) -> None:
    _require(complete is False or complete is None)
    _require(reason in ACQ_REASONS)
    if outcomes is not None:
        _require(type(outcomes) is list and len(outcomes) == 3)
        names = []
        for pair in outcomes:
            _require(type(pair) is list and len(pair) == 2)
            n, o = pair
            _require(type(n) is str and type(o) is str)
            _require(o in PASS_OUTCOMES)
            names.append(n)
        _require(tuple(names) == PASS_NAMES)
    # RA-O coherence
    if reason == "pass-outcome-not-completed":
        _require(complete is False and outcomes is not None)
        _require(any(o != "completed" for _, o in outcomes))
    elif reason == "capacity-incomplete-record":
        _require(complete is False and outcomes is not None)
        _require(all(o == "completed" for _, o in outcomes))
    elif reason == "completion-not-provable-from-state":
        _require(complete is None and outcomes is not None)
        _require(all(o == "completed" for _, o in outcomes))
    elif reason == "attempt-binding-unavailable":
        _require(complete is None and outcomes is not None)
    elif reason in ("acquisition-input-incomplete", "acquisition-derivation-fault"):
        _require(complete is None and outcomes is None)
    else:
        raise _BlockInvalid("acq")


def validate_block_dict(block: Any) -> str | None:
    """Return None if valid; otherwise an unavailable_reason tag for failure.

    Structural failures → 'invalid-block'. Unsupported schema literal →
    'unsupported-schema'. Caller handles missing property separately.
    """
    if type(block) is not dict:
        return "invalid-block"
    if "schema" not in block or type(block.get("schema")) is not str:
        return "invalid-block"
    if block["schema"] != SCHEMA_V1:
        return "unsupported-schema"
    try:
        # Whole-block depth
        _require(_container_depth(block) <= _MAX_BLOCK_DEPTH)
        avail = block.get("availability")
        _require(avail in ("available", "unavailable"))
        if avail == "unavailable":
            reason = block.get("unavailable_reason")
            _require(reason in UNAVAILABLE_REASONS)
            for k in (
                "delivery_mode",
                "projection_schema",
                "product",
                "execution",
                "gate",
                "quarantine",
                "unresolved_product_candidates",
                "classification_errors",
                "classification_complete",
                "product_list_count",
                "unresolved_candidate_count",
                "product_path_record_count",
                "acquisition_complete",
                "acquisition_reason",
                "persisted_pass_outcomes",
            ):
                _require(k in block and block[k] is None, k)
            return None

        # available
        _require(block.get("unavailable_reason") is None)
        mode = block.get("delivery_mode")
        _require(mode in DELIVERY_MODES)
        _require(block.get("projection_schema") == PROJECTION_SCHEMA_V1)
        product = block.get("product")
        execution = block.get("execution")
        gate = block.get("gate")
        quarantine = block.get("quarantine")
        unresolved = block.get("unresolved_product_candidates")
        errors = block.get("classification_errors")
        _require(
            all(type(x) is list for x in (product, execution, gate, quarantine, unresolved, errors))
        )
        for p in product:
            _validate_product(p)
        for p in unresolved:
            _validate_product(p)
        for h in execution:
            _validate_host(h, gate=False)
        for h in gate:
            _validate_host(h, gate=True)
        for f in quarantine:
            _validate_fallback(f)
        for e in errors:
            _validate_error(e)

        # Disposition partition
        for p in product:
            _require(p["record"]["fields"]["disposition"] != "UNCERTAIN")
        for p in unresolved:
            _require(p["record"]["fields"]["disposition"] == "UNCERTAIN")

        # Index union uniqueness 0..N-1
        indexes: list[int] = []
        indexes.extend((p["record"]["record_index"]) for p in product)
        indexes.extend((p["record"]["record_index"]) for p in unresolved)
        indexes.extend((h["record"]["record_index"]) for h in execution)
        indexes.extend((h["record"]["record_index"]) for h in gate)
        indexes.extend((f["record_index"]) for f in quarantine)
        _require(len(indexes) == len(set(indexes)))
        if indexes:
            _require(set(indexes) == set(range(max(indexes) + 1)) and min(indexes) == 0)

        def _increasing(seq: list[int]) -> bool:
            return all(seq[i] < seq[i + 1] for i in range(len(seq) - 1))

        _require(_increasing([p["record"]["record_index"] for p in product]))
        _require(_increasing([p["record"]["record_index"] for p in unresolved]))
        _require(_increasing([h["record"]["record_index"] for h in execution]))
        _require(_increasing([h["record"]["record_index"] for h in gate]))
        _require(_increasing([f["record_index"] for f in quarantine]))

        # Error / quarantine correlation
        local_errors = [e for e in errors if e["stage"] == "record"]
        proj_errors = [e for e in errors if e["stage"] == "projection"]
        render_errors = [e for e in errors if e["stage"] == "render"]
        _require(len(proj_errors) <= 1)
        _require(len(render_errors) <= 1)
        # Order: locals by index, then at most one projection, then at most one render
        reordered = sorted(local_errors, key=lambda e: e["record_index"]) + proj_errors + render_errors
        _require(errors == reordered)
        q_by_idx = {f["record_index"]: f for f in quarantine}
        for e in local_errors:
            fb = q_by_idx.get(e["record_index"])
            _require(fb is not None and fb["reason"] == e["code"])
        if proj_errors:
            # Every occurrence in quarantine
            _require(set(q_by_idx) == set(indexes))
        if mode == "projection":
            _require(len(render_errors) == 0)
            _require(all(f["reason"] != "renderer-computation-fault" for f in quarantine))
        if mode == "renderer-fallback":
            _require(len(render_errors) == 1)
            _require(render_errors[0]["code"] == "renderer-computation-fault")
            _require(render_errors[0] is errors[-1])

        # Counts
        cc = block.get("classification_complete")
        _require(type(cc) is bool)
        plc = block.get("product_list_count")
        ucc = block.get("unresolved_candidate_count")
        pprc = block.get("product_path_record_count")
        if errors:
            _require(cc is False)
        else:
            _require(cc is True)
            _require(len(quarantine) == 0)

        has_proj = len(proj_errors) > 0
        has_render = len(render_errors) > 0
        if has_proj or has_render or mode == "renderer-fallback":
            _require(plc is None and ucc is None and pprc is None)
            if has_proj or mode == "renderer-fallback":
                _require(len(product) == 0 and len(unresolved) == 0)
                _require(len(execution) == 0 and len(gate) == 0)
        else:
            _require(plc == len(product))
            _require(ucc == len(unresolved))
            if pprc is not None:
                _require(type(pprc) is int and pprc >= 0)
                lo = len(product) + len(unresolved)
                hi = lo + len(quarantine)
                _require(lo <= pprc <= hi)

        _validate_acq(
            block.get("acquisition_complete"),
            block.get("acquisition_reason"),
            block.get("persisted_pass_outcomes"),
        )
        # UTF-8 strict on all strings in exposed block
        json.dumps(block, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return None
    except _BlockInvalid:
        return "invalid-block"
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        return "invalid-block"


def parse_block_dict(block: dict[str, Any]) -> ReportClassificationWire:
    """Build typed model from a validated block dict (available or unavailable)."""
    reason = validate_block_dict(block)
    if reason is not None:
        # Should not happen if caller validated; treat as invalid
        return _unavailable(reason if reason in UNAVAILABLE_REASONS else "invalid-block")
    if block["availability"] == "unavailable":
        return _unavailable(block["unavailable_reason"])

    def load_obs(d: dict) -> DiagnosticObservation:
        return DiagnosticObservation(
            kind=d["kind"],
            basis=d["basis"],
            observation_scope=d["observation_scope"],
            text=d["text"],
        )

    def load_rv(d: dict) -> RecordView:
        return RecordView(record_index=d["record_index"], fields=dict(d["fields"]))

    def load_host_failure_kind(d: dict) -> HostFailureKind | None:
        # Missing key → None (old reader / old writer compatibility).
        if "host_failure_kind" not in d:
            return None
        v = d["host_failure_kind"]
        if v is None:
            return None
        # validate_block_dict already restricted to freeze set when present.
        return v  # type: ignore[return-value]

    def load_product(d: dict) -> ProductRecord:
        return ProductRecord(
            record=load_rv(d["record"]),
            associated_diagnostics=tuple(load_obs(x) for x in d["associated_diagnostics"]),
            host_failure_kind=load_host_failure_kind(d),
        )

    def load_host(d: dict) -> HostDiagnostic:
        return HostDiagnostic(
            record=load_rv(d["record"]),
            diagnostic=load_obs(d["diagnostic"]),
            host_failure_kind=load_host_failure_kind(d),
        )

    def load_fb(d: dict) -> WireFallbackRecord:
        return WireFallbackRecord(
            record_index=d["record_index"],
            safe_fields=dict(d["safe_fields"]),
            unavailable_fields=tuple(d["unavailable_fields"]),
            reason=d["reason"],
        )

    def load_err(d: dict) -> WireClassificationError:
        return WireClassificationError(record_index=d["record_index"], stage=d["stage"], code=d["code"])

    outcomes = block["persisted_pass_outcomes"]
    typed_outcomes: tuple[tuple[str, str], ...] | None
    if outcomes is None:
        typed_outcomes = None
    else:
        typed_outcomes = tuple((a, b) for a, b in outcomes)

    acq = block["acquisition_complete"]
    acq_typed: Literal[False] | None = False if acq is False else None

    return ReportClassificationWire(
        schema=block["schema"],
        availability="available",
        unavailable_reason=None,
        delivery_mode=block["delivery_mode"],
        projection_schema=block["projection_schema"],
        product=tuple(load_product(p) for p in block["product"]),
        execution=tuple(load_host(h) for h in block["execution"]),
        gate=tuple(load_host(h) for h in block["gate"]),
        quarantine=tuple(load_fb(f) for f in block["quarantine"]),
        unresolved_product_candidates=tuple(
            load_product(p) for p in block["unresolved_product_candidates"]
        ),
        classification_errors=tuple(load_err(e) for e in block["classification_errors"]),
        classification_complete=block["classification_complete"],
        product_list_count=block["product_list_count"],
        unresolved_candidate_count=block["unresolved_candidate_count"],
        product_path_record_count=block["product_path_record_count"],
        acquisition_complete=acq_typed,
        acquisition_reason=block["acquisition_reason"],
        persisted_pass_outcomes=typed_outcomes,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def prepare_legacy_report(
    legacy_sarif: dict[str, object],
    *,
    stdout: str,
    stderr: str,
) -> LegacyReportSubstrate:
    """Build an owned legacy substrate or raw-legacy-only (§1.1)."""
    if type(legacy_sarif) is not dict:
        raise TypeError("legacy_sarif must be dict")
    if type(stdout) is not str or type(stderr) is not str:
        raise TypeError("stdout/stderr must be str")

    accepted, stdout_doc = _legacy_envelope_accepted(stdout)
    if not accepted or stdout_doc is None:
        return LegacyReportSubstrate(sarif_document=None, stdout=stdout, stderr=stderr)

    if not _documents_represent_same(stdout_doc, legacy_sarif):
        return LegacyReportSubstrate(sarif_document=None, stdout=stdout, stderr=stderr)

    try:
        owned = _detach_json(stdout_doc)
    except (TypeError, ValueError, OverflowError):
        return LegacyReportSubstrate(sarif_document=None, stdout=stdout, stderr=stderr)
    if type(owned) is not dict:
        return LegacyReportSubstrate(sarif_document=None, stdout=stdout, stderr=stderr)
    return LegacyReportSubstrate(sarif_document=owned, stdout=stdout, stderr=stderr)


def _inject_classification(
    sarif_document: dict[str, JSONValue], block: dict[str, JSONValue]
) -> dict[str, JSONValue]:
    doc = _detach_json(sarif_document)
    assert type(doc) is dict
    runs = doc.get("runs")
    assert type(runs) is list and runs and type(runs[0]) is dict
    run = dict(runs[0])
    props = run.get("properties")
    if type(props) is not dict:
        props = {}
    else:
        props = dict(props)
    props["report_classification"] = block
    run["properties"] = props
    doc["runs"] = [run] + list(runs[1:])
    return doc


def _serialize_stdout(doc: dict[str, JSONValue]) -> str:
    return json.dumps(doc, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _block_fits(legacy_stdout: str, extended_stdout: str) -> bool:
    if len(extended_stdout) > _MAX_SARIF_REPORT_CHARS:
        return False
    ok, _ = _legacy_envelope_accepted(extended_stdout)
    return ok


def _prepare_safe_views(
    legacy: LegacyReportSubstrate, projection: ReportProjection
) -> PurePreparedReport:
    assert legacy.sarif_document is not None
    # Prefer normal projection; on validation failure use renderer-fallback.
    try:
        wire = serialize_projection(projection, delivery_mode="projection")
        block = wire_to_json(wire)
        # Adjust acquisition for Phase 1 residual: completion-not-provable
        # with null outcomes is invalid under RA-O (needs all-completed
        # outcomes). Map null-outcomes + completion-not-provable to
        # acquisition-derivation-fault? That would reinterpret. Instead:
        # if validation fails, renderer-fallback; if still fails, budget
        # unavailable path.
        # Fix RA-O for the common Phase 1 success path: when reason is
        # completion-not-provable-from-state and outcomes is null, rewrite
        # reason to acquisition-derivation-fault ONLY if we consider missing
        # outcomes a derivation gap — authorize says do not reinterpret.
        # Keep as-is; validate; fallback on failure.
        vreason = validate_block_dict(block)
        if vreason is not None:
            raise _BlockInvalid(vreason)
    except (_BlockInvalid, ValueError, TypeError):
        wire = build_renderer_fallback(projection)
        block = wire_to_json(wire)
        vreason = validate_block_dict(block)
        if vreason is not None:
            # Cannot form a valid available block — emit canonical invalid-block
            # unavailable via fit gate below using unavailable block.
            wire = _unavailable("invalid-block")
            block = wire_to_json(wire)

    extended_doc = _inject_classification(legacy.sarif_document, block)
    try:
        extended_stdout = _serialize_stdout(extended_doc)
    except (TypeError, ValueError, OverflowError):
        # Serialization domain fault → try unavailable invalid-block
        wire = _unavailable("invalid-block")
        block = wire_to_json(wire)
        extended_doc = _inject_classification(legacy.sarif_document, block)
        extended_stdout = _serialize_stdout(extended_doc)

    stderr = legacy.stderr
    if _block_fits(legacy.stdout, extended_stdout):
        # Reader-derived classification from the selected stdout
        run = extended_doc["runs"][0]  # type: ignore[index]
        assert type(run) is dict
        classification = parse_report_classification(run, legacy_report_state="accepted")
        return PurePreparedReport(
            stdout=extended_stdout, stderr=stderr, report_classification=classification
        )

    # Budget exceeded: try canonical unavailable/budget-exceeded
    budget_wire = _unavailable("budget-exceeded")
    budget_block = wire_to_json(budget_wire)
    budget_doc = _inject_classification(legacy.sarif_document, budget_block)
    budget_stdout = _serialize_stdout(budget_doc)
    if _block_fits(legacy.stdout, budget_stdout):
        q = _BUDGET_QUALIFIER
        new_stderr = stderr + (("\n" if stderr and not stderr.endswith("\n") else "") + q)
        run = budget_doc["runs"][0]  # type: ignore[index]
        assert type(run) is dict
        classification = parse_report_classification(run, legacy_report_state="accepted")
        return PurePreparedReport(
            stdout=budget_stdout, stderr=new_stderr, report_classification=classification
        )

    # Omit extension entirely — byte-identical legacy stdout
    classification = parse_report_classification(
        # accepted run without the property → legacy-report
        legacy.sarif_document["runs"][0]  # type: ignore[index]
        if isinstance(legacy.sarif_document.get("runs"), list)
        and legacy.sarif_document["runs"]
        and type(legacy.sarif_document["runs"][0]) is dict
        else None,
        legacy_report_state="accepted",
    )
    q = _BUDGET_QUALIFIER
    new_stderr = stderr + (("\n" if stderr and not stderr.endswith("\n") else "") + q)
    return PurePreparedReport(
        stdout=legacy.stdout, stderr=new_stderr, report_classification=classification
    )


def _prepare_raw_legacy_views(legacy: LegacyReportSubstrate) -> PurePreparedReport:
    assert legacy.sarif_document is None
    # Exact string identity for stdout/stderr; classification from §4 on
    # unchanged legacy parser outcome.
    accepted, doc = _legacy_envelope_accepted(legacy.stdout)
    if legacy.stdout == "":
        classification = _unavailable("no-report")
    elif not accepted or doc is None:
        classification = _unavailable("invalid-report")
    else:
        run = doc["runs"][0]
        classification = parse_report_classification(run, legacy_report_state="accepted")
    return PurePreparedReport(
        stdout=legacy.stdout, stderr=legacy.stderr, report_classification=classification
    )


def prepare_report_views(
    legacy: LegacyReportSubstrate,
    projection: ReportProjection | None,
) -> PurePreparedReport:
    """Prepare complete normal/fallback or raw-legacy-only views (§1.1)."""
    if not isinstance(legacy, LegacyReportSubstrate):
        raise TypeError("legacy must be LegacyReportSubstrate")
    if legacy.sarif_document is not None:
        if projection is None or not isinstance(projection, ReportProjection):
            raise TypeError(
                f"safe substrate requires a completed ReportProjection; got {type(projection).__name__}"
            )
        # Do not mutate inputs; work on owned copies inside helpers.
        return _prepare_safe_views(legacy, projection)
    if projection is not None:
        raise TypeError(
            f"raw-legacy-only substrate requires projection=None; got {type(projection).__name__}"
        )
    return _prepare_raw_legacy_views(legacy)


def _cross_envelope_cardinality_ok(block: dict[str, Any], accepted_run: dict[str, Any]) -> bool:
    """§3.2 cross-envelope cardinality vs accepted_run['results'].

    Returns True when the check does not apply or when it passes.
    Returns False when preconditions hold and the equality fails (or
    results is missing / not a list) — caller maps that to invalid-block.
    Does not mutate block or accepted_run.
    """
    if block.get("availability") != "available":
        return True
    if block.get("delivery_mode") != "projection":
        return True
    errors = block.get("classification_errors")
    if type(errors) is not list:
        return True
    for err in errors:
        if type(err) is dict and err.get("stage") in ("projection", "render"):
            return True  # global projection / render fallback excluded
    quarantine = block.get("quarantine")
    if type(quarantine) is not list or len(quarantine) != 0:
        return True  # record fallback with quarantine excluded
    # Preconditions hold: apply check against already-validated results list.
    results = accepted_run.get("results")
    if type(results) is not list:
        return False
    n = len(results)
    plc = block.get("product_list_count")
    ucc = block.get("unresolved_candidate_count")
    pprc = block.get("product_path_record_count")
    if type(plc) is not int or isinstance(plc, bool):
        return False
    if type(ucc) is not int or isinstance(ucc, bool):
        return False
    if type(pprc) is not int or isinstance(pprc, bool):
        return False
    return plc + ucc == pprc == n


def parse_report_classification(
    accepted_run: dict[str, JSONValue] | None,
    *,
    legacy_report_state: Literal["accepted", "empty", "rejected"],
) -> ReportClassificationWire:
    """§4 reader on an already strict-parsed selected run."""
    if legacy_report_state not in ("accepted", "empty", "rejected"):
        raise TypeError("legacy_report_state must be accepted|empty|rejected")
    if legacy_report_state == "accepted":
        if type(accepted_run) is not dict:
            raise TypeError("accepted requires exact dict run")
    else:
        if accepted_run is not None:
            raise TypeError("empty/rejected require accepted_run is None")
        if legacy_report_state == "empty":
            return _unavailable("no-report")
        return _unavailable("invalid-report")

    assert accepted_run is not None
    props = accepted_run.get("properties")
    if type(props) is not dict or "report_classification" not in props:
        return _unavailable("legacy-report")
    raw = props.get("report_classification")
    if raw is None or type(raw) is not dict:
        return _unavailable("invalid-block")
    if type(raw.get("schema")) is not str:
        return _unavailable("invalid-block")
    if raw["schema"] != SCHEMA_V1:
        return _unavailable("unsupported-schema")
    reason = validate_block_dict(raw)
    if reason is not None:
        return _unavailable(reason)
    # Unavailable blocks skip §3.2 cross-envelope cardinality.
    if raw.get("availability") == "unavailable":
        return parse_block_dict(raw)
    if not _cross_envelope_cardinality_ok(raw, accepted_run):
        return _unavailable("invalid-block")
    return parse_block_dict(raw)


# Re-exports useful for tests
__all__ = [
    "LegacyReportSubstrate",
    "PurePreparedReport",
    "ReportClassificationWire",
    "DiagnosticObservation",
    "RecordView",
    "ProductRecord",
    "HostDiagnostic",
    "WireFallbackRecord",
    "WireClassificationError",
    "prepare_legacy_report",
    "prepare_report_views",
    "parse_report_classification",
    "serialize_projection",
    "build_renderer_fallback",
    "wire_to_json",
    "validate_block_dict",
    "parse_block_dict",
    "SCHEMA_V1",
    "PROJECTION_SCHEMA_V1",
    "FIELD_INVENTORY",
    "HOST_FAILURE_KINDS",
]
