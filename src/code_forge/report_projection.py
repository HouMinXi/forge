# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Pure report-projection adapter for Forge #332 Phase 1 (unwired).

classify_report_records(state) -> ReportProjection. No I/O by design; no
production callers. Imports only the §2 whitelist from code_forge.state.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, fields
from typing import Literal

from code_forge.state import (
    Disposition,
    PassOutcome,
    State,
    StateFinding,
    _PASS_NAMES,
    derive_pass_outcomes,
    is_provider_capacity,
    is_provider_diagnostic,
    is_receipt_audit,
    reporting_product_findings,
)

Channel = Literal["product", "execution", "gate", "quarantine"]
Shape = Literal["S0", "S1", "S2", "S3", "S4", "S5", "S4+S5"]
AssocKind = Literal["falsifier-failure", "evidence-downgrade"]
FaultStage = Literal["shape", "diagnostics", "record", "projection"]
AcqReason = Literal[
    "pass-outcome-not-completed",
    "capacity-incomplete-record",
    "completion-not-provable-from-state",
    "acquisition-derivation-fault",
]

_S4_PREFIXES: tuple[str, ...] = (
    "falsify() protocol violation: ",
    "falsify() raised: ",
    "falsify() backend timed out: ",
    "falsify() backend unavailable: ",
    "falsify() response truncated: ",
    "falsify() unusable response: ",
    "falsify() credential failure: ",
    "falsify() invocation failed: ",
)

_IMMUTABLE_SCALARS = (str, int, bool, float, type(None))


class _RecordTypeError(Exception):
    """Wrong exact type on a finding field during record construction or shape select."""


class _ShapeContradiction(Exception):
    """P1 membership and rows 1–3 disagree (reachable only under test patches at the pin)."""


class _AcquisitionShapeError(Exception):
    """derive_pass_outcomes result failed the exact-type Q2 check."""


@dataclass(frozen=True)
class AssociatedDiagnostic:
    kind: AssocKind
    reason: str


@dataclass(frozen=True)
class ReportRecord:
    index: int
    finding_id: str
    fingerprint: str
    channel: Channel
    shape: Shape
    selector: str
    file: str
    line_range: tuple[int, ...]
    description: str
    disposition: str
    associated_diagnostics: tuple[AssociatedDiagnostic, ...] = ()


@dataclass(frozen=True)
class FallbackRecord:
    index: int
    shape: None
    channel: Literal["quarantine"]
    finding_id: str | None
    fingerprint: str | None
    file: str | None
    line_range: tuple[int, ...] | None
    description: str | None
    disposition: str | None


@dataclass(frozen=True)
class ClassificationError:
    index: int | None
    stage: FaultStage
    error_type: str


@dataclass(frozen=True)
class ReportProjection:
    product: tuple[ReportRecord, ...]
    unresolved_product_candidates: tuple[ReportRecord, ...]
    execution: tuple[ReportRecord, ...]
    gate: tuple[ReportRecord, ...]
    quarantine: tuple[FallbackRecord, ...]
    classification_errors: tuple[ClassificationError, ...]
    classification_complete: bool
    classification_incomplete_reasons: tuple[Literal["record-fault", "projection-fault"], ...]
    product_list_count: int | None
    unresolved_candidate_count: int | None
    product_path_record_count: int | None
    acquisition_complete: Literal[False] | None
    acquisition_reason: AcqReason


def _safe_str(v) -> str | None:
    return v if type(v) is str else None


def _safe_line_range(v) -> tuple[int, ...] | None:
    if type(v) is not list:
        return None
    if not all(type(x) is int for x in v):
        return None
    return tuple(v)


def _safe_disposition_value(v) -> str | None:
    if type(v) is Disposition:
        return v.value
    return None


def _fallback_record(index: int, f: StateFinding) -> FallbackRecord:
    finding_id = None
    fingerprint = None
    file = None
    line_range = None
    description = None
    disposition = None
    try:
        finding_id = _safe_str(f.id)
    except Exception:
        finding_id = None
    try:
        fingerprint = _safe_str(f.fingerprint)
    except Exception:
        fingerprint = None
    try:
        file = _safe_str(f.file)
    except Exception:
        file = None
    try:
        line_range = _safe_line_range(f.line_range)
    except Exception:
        line_range = None
    try:
        description = _safe_str(f.description)
    except Exception:
        description = None
    try:
        disposition = _safe_disposition_value(f.disposition)
    except Exception:
        disposition = None
    return FallbackRecord(
        index=index,
        shape=None,
        channel="quarantine",
        finding_id=finding_id,
        fingerprint=fingerprint,
        file=file,
        line_range=line_range,
        description=description,
        disposition=disposition,
    )


def _require_str(v, name: str) -> str:
    if type(v) is not str:
        raise _RecordTypeError(f"{name} must be str, got {type(v).__name__}")
    return v


def _require_line_range(v) -> tuple[int, ...]:
    if type(v) is not list or not all(type(x) is int for x in v):
        raise _RecordTypeError("line_range must be list[int] (bool excluded)")
    return tuple(v)


def _require_disposition(v) -> str:
    if type(v) is not Disposition:
        raise _RecordTypeError(f"disposition must be Disposition, got {type(v).__name__}")
    return v.value


def _s4_hit(f: StateFinding) -> bool:
    if f.disposition is not Disposition.UNCERTAIN:
        return False
    if type(f.error) is not str:
        if f.error is not None:
            raise _RecordTypeError("error must be str or None")
        return False
    if f.id == "FIXVAL_HOLLOW":
        return False
    return f.error.startswith(_S4_PREFIXES)


def _s5_hit(f: StateFinding) -> bool:
    if f.disposition is not Disposition.UNCERTAIN:
        return False
    ed = f.evidence_downgrade
    if type(ed) is not str:
        if ed is not None:
            raise _RecordTypeError("evidence_downgrade must be str or None")
        return False
    return ed != ""


def _select_shape(f: StateFinding, product_path: bool) -> tuple[Shape, str]:
    """Stage 'shape'. Returns (shape, selector). Raises on contradiction / type fault."""
    if is_receipt_audit(f):
        if product_path:
            raise _ShapeContradiction("P1 set and is_receipt_audit both true")
        return "S3", "is_receipt_audit"
    if is_provider_capacity(f):
        if product_path:
            raise _ShapeContradiction("P1 set and is_provider_capacity both true")
        return "S1", "is_provider_capacity"
    if is_provider_diagnostic(f) and not is_provider_capacity(f):
        if product_path:
            raise _ShapeContradiction("P1 set and S2 both true")
        return "S2", "is_provider_diagnostic"
    if not product_path:
        raise _ShapeContradiction("not in P1 and matched none of rows 1–3")

    s4 = _s4_hit(f)
    s5 = _s5_hit(f)
    if s4 and s5:
        return "S4+S5", "falsifier-failure+evidence-downgrade"
    if s4:
        return "S4", "falsifier-failure"
    if s5:
        return "S5", "evidence-downgrade"
    return "S0", "reporting_product_findings"


def _attach_diagnostics(f: StateFinding, shape: Shape) -> tuple[AssociatedDiagnostic, ...]:
    """Stage 'diagnostics'. Called for every product-path record; () for S0."""
    if shape == "S0":
        return ()
    diags: list[AssociatedDiagnostic] = []
    if shape in ("S4", "S4+S5"):
        reason = f.error
        if type(reason) is not str:
            raise _RecordTypeError("associated diagnostic reason must be str")
        diags.append(AssociatedDiagnostic(kind="falsifier-failure", reason=reason))
    if shape in ("S5", "S4+S5"):
        reason = f.evidence_downgrade
        if type(reason) is not str:
            raise _RecordTypeError("associated diagnostic reason must be str")
        diags.append(AssociatedDiagnostic(kind="evidence-downgrade", reason=reason))
    return tuple(diags)


def _channel_for(shape: Shape, disposition: Disposition) -> Channel:
    if shape == "S1":
        return "execution"
    if shape in ("S2", "S3"):
        return "gate"
    # product path shapes
    if disposition is Disposition.UNCERTAIN or shape in ("S4", "S5", "S4+S5"):
        return "product"  # placement into unresolved list is separate; channel label for product-path
    return "product"


def _build_record(
    index: int,
    f: StateFinding,
    shape: Shape,
    selector: str,
    diags: tuple[AssociatedDiagnostic, ...],
) -> ReportRecord:
    """Stage 'record'. Applies exact-type rules; raises _RecordTypeError on mismatch."""
    finding_id = _require_str(f.id, "finding_id")
    fingerprint = _require_str(f.fingerprint, "fingerprint")
    file = _require_str(f.file, "file")
    description = _require_str(f.description, "description")
    line_range = _require_line_range(f.line_range)
    disposition = _require_disposition(f.disposition)
    for d in diags:
        if type(d.reason) is not str:
            raise _RecordTypeError("associated diagnostic reason must be str")

    if shape == "S1":
        channel: Channel = "execution"
    elif shape in ("S2", "S3"):
        channel = "gate"
    else:
        channel = "product"

    return ReportRecord(
        index=index,
        finding_id=finding_id,
        fingerprint=fingerprint,
        channel=channel,
        shape=shape,
        selector=selector,
        file=file,
        line_range=line_range,
        description=description,
        disposition=disposition,
        associated_diagnostics=diags,
    )


def _assemble(
    records: list[ReportRecord],
    fallbacks: list[FallbackRecord],
    path_count: int,
) -> tuple:
    """P2: list assembly and the three counts. Returns bucketed tuples + counts."""
    product: list[ReportRecord] = []
    unresolved: list[ReportRecord] = []
    execution: list[ReportRecord] = []
    gate: list[ReportRecord] = []
    for r in records:
        if r.channel == "execution":
            execution.append(r)
        elif r.channel == "gate":
            gate.append(r)
        elif r.disposition == Disposition.UNCERTAIN.value or r.shape in ("S4", "S5", "S4+S5"):
            unresolved.append(r)
        else:
            product.append(r)
    return (
        tuple(product),
        tuple(unresolved),
        tuple(execution),
        tuple(gate),
        tuple(fallbacks),
        len(product),
        len(unresolved),
        path_count,
    )


def _evaluate_acquisition(findings: list) -> tuple:
    """Q1–Q4. Returns (acquisition_complete, acquisition_reason)."""
    try:
        outcomes = derive_pass_outcomes(findings)
        if not (
            type(outcomes) is dict
            and set(outcomes) == set(_PASS_NAMES)
            and all(type(v) is PassOutcome for v in outcomes.values())
        ):
            raise _AcquisitionShapeError("malformed derive_pass_outcomes result")
        if any(v is not PassOutcome.COMPLETED for v in outcomes.values()):
            return False, "pass-outcome-not-completed"
        if any(is_provider_diagnostic(f) and not is_provider_capacity(f) for f in findings):
            return False, "capacity-incomplete-record"
        return None, "completion-not-provable-from-state"
    except (MemoryError, RecursionError):
        raise
    except Exception:
        return None, "acquisition-derivation-fault"


def classify_report_records(state: State) -> ReportProjection:
    """Classify every finding. Recoverable faults degrade into quarantine / A4."""
    findings = state.findings
    n = len(findings)
    record_errors: list[ClassificationError] = []
    fallbacks_by_index: dict[int, FallbackRecord] = {}
    classified: dict[int, ReportRecord] = {}
    projection_error: ClassificationError | None = None
    path_count: int | None = None
    product_path_ids: set[int] = set()

    # P1: product-path membership by object identity
    try:
        product_path_list = reporting_product_findings(findings)
        path_count = len(product_path_list)
        id_map = {id(f): i for i, f in enumerate(findings)}
        for pf in product_path_list:
            idx = id_map.get(id(pf))
            if idx is not None:
                product_path_ids.add(idx)
    except (MemoryError, RecursionError):
        raise
    except Exception as exc:
        projection_error = ClassificationError(None, "projection", type(exc).__name__)
        for i, f in enumerate(findings):
            fallbacks_by_index[i] = _fallback_record(i, f)
        # acquisition still runs
        acq_complete, acq_reason = _evaluate_acquisition(findings)
        return ReportProjection(
            product=(),
            unresolved_product_candidates=(),
            execution=(),
            gate=(),
            quarantine=tuple(fallbacks_by_index[i] for i in range(n)),
            classification_errors=(projection_error,),
            classification_complete=False,
            classification_incomplete_reasons=("projection-fault",),
            product_list_count=None,
            unresolved_candidate_count=None,
            product_path_record_count=None,
            acquisition_complete=acq_complete,
            acquisition_reason=acq_reason,
        )

    # Per-record classification
    for i, f in enumerate(findings):
        product_path = i in product_path_ids
        try:
            shape, selector = _select_shape(f, product_path)
        except (MemoryError, RecursionError):
            raise
        except Exception as exc:
            record_errors.append(ClassificationError(i, "shape", type(exc).__name__))
            fallbacks_by_index[i] = _fallback_record(i, f)
            continue

        diags: tuple[AssociatedDiagnostic, ...] = ()
        if product_path:
            try:
                diags = _attach_diagnostics(f, shape)
            except (MemoryError, RecursionError):
                raise
            except Exception as exc:
                record_errors.append(ClassificationError(i, "diagnostics", type(exc).__name__))
                fallbacks_by_index[i] = _fallback_record(i, f)
                continue

        try:
            classified[i] = _build_record(i, f, shape, selector, diags)
        except (MemoryError, RecursionError):
            raise
        except Exception as exc:
            record_errors.append(ClassificationError(i, "record", type(exc).__name__))
            fallbacks_by_index[i] = _fallback_record(i, f)

    # P2: assemble
    records_in_order = [classified[i] for i in sorted(classified)]
    fallbacks_in_order = [fallbacks_by_index[i] for i in sorted(fallbacks_by_index)]
    try:
        (
            product,
            unresolved,
            execution,
            gate,
            quarantine,
            product_list_count,
            unresolved_candidate_count,
            product_path_record_count,
        ) = _assemble(records_in_order, fallbacks_in_order, path_count if path_count is not None else 0)
    except (MemoryError, RecursionError):
        raise
    except Exception as exc:
        projection_error = ClassificationError(None, "projection", type(exc).__name__)
        # Every index ends in quarantine exactly once; keep original FallbackRecord objects
        for i, f in enumerate(findings):
            if i not in fallbacks_by_index:
                fallbacks_by_index[i] = _fallback_record(i, f)
        quarantine = tuple(fallbacks_by_index[i] for i in range(n))
        product = ()
        unresolved = ()
        execution = ()
        gate = ()
        product_list_count = None
        unresolved_candidate_count = None
        product_path_record_count = None

    # Completeness
    reasons: list[Literal["record-fault", "projection-fault"]] = []
    if record_errors:
        reasons.append("record-fault")
    if projection_error is not None:
        reasons.append("projection-fault")
    classification_complete = not reasons
    errors = tuple(record_errors) + ((projection_error,) if projection_error is not None else ())

    # Acquisition last
    acq_complete, acq_reason = _evaluate_acquisition(findings)

    return ReportProjection(
        product=product,
        unresolved_product_candidates=unresolved,
        execution=execution,
        gate=gate,
        quarantine=quarantine,
        classification_errors=errors,
        classification_complete=classification_complete,
        classification_incomplete_reasons=tuple(reasons),
        product_list_count=product_list_count,
        unresolved_candidate_count=unresolved_candidate_count,
        product_path_record_count=product_path_record_count,
        acquisition_complete=acq_complete,
        acquisition_reason=acq_reason,
    )


# Re-export for tests that introspect; keep unused-import silence for copy/fields if needed later.
_ = (copy, fields)
