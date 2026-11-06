# SPDX-License-Identifier: Apache-2.0
"""Experimental #400 S1a serializer. No reader or consumer wiring.

The v1 field serializers are reused only for shapes whose semantics are
unchanged. Mutation observations never enter a block labeled v1.
"""

from dataclasses import replace

from code_forge.report_projection_v2 import ReportProjectionV2
from code_forge.report_presentation import (
    DiagnosticObservation,
    HostDiagnostic,
    ReportClassificationWire,
    _record_view_from_report_record,
    serialize_projection,
)

SCHEMA_V2 = "forge-report-classification/v2"
PROJECTION_SCHEMA_V2 = "forge-report-projection-proposal-v2"


def serialize_report_projection_v2(projection: ReportProjectionV2) -> ReportClassificationWire:
    """Return an owned versioned wire DTO, preserving acquisition and counts.

    This S1a candidate deliberately has no fallback policy: renderer errors
    propagate to the future preparation boundary. It does not validate a
    caller-supplied projection or claim that a reader will accept the result.
    """
    if type(projection) is not ReportProjectionV2:
        raise TypeError("expected ReportProjectionV2")
    unchanged = replace(
        projection,
        execution=tuple(r for r in projection.execution if r.shape != "MUTATION_INFRA"),
    )
    wire = serialize_projection(unchanged)
    mutation = tuple(
        HostDiagnostic(
            record=_record_view_from_report_record(r),
            diagnostic=DiagnosticObservation(
                kind="mutation-infra",
                basis="inherited-predicate",
                observation_scope="persisted-record-unbound",
                text=None,
            ),
            host_failure_kind=None,
        )
        for r in projection.execution
        if r.shape == "MUTATION_INFRA"
    )
    return replace(
        wire,
        schema=SCHEMA_V2,
        projection_schema=PROJECTION_SCHEMA_V2,
        execution=tuple(sorted((*wire.execution, *mutation), key=lambda h: h.record.record_index)),
    )
