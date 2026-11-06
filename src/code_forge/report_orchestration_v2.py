# SPDX-License-Identifier: Apache-2.0
"""Unwired #400 pure same-snapshot orchestration candidate.

No caller-supplied callbacks, baseline/projection pairs, filesystem, CLI,
streams or status carriers. Context acquisition remains outside this layer.
"""

import json

from code_forge.report_baseline_v2 import (
    RenderingInputs,
    SnapshotLimits,
    _capture_owned_rendering,
    _build_baseline_parts,
)
from code_forge.report_projection_v2 import classify_report_records_v2, ProjectionComputationFault
from code_forge.report_presentation_v2 import serialize_report_projection_v2
from code_forge.report_presentation import wire_to_json, _legacy_envelope_accepted
from code_forge.report_preparation_v2 import (
    PreparationLimits,
    PreparedText,
    prepare_final_candidate,
    _restore_baseline,
)
from code_forge.report_projection_check_v2 import projection_matches_block, exact_json_equal
from code_forge.sarif import format_classification_line
from code_forge.state import State


class RenderComputationFault(Exception):
    """An admitted JSON rendering value-domain fault; never a critical wrapper."""


def _render_candidate(baseline_parts, projection):
    wire = serialize_report_projection_v2(projection)
    block = wire_to_json(wire)
    document = json.loads(baseline_parts.text.stdout)
    document["runs"][0].setdefault("properties", {})["report_classification"] = block
    # Only the known built-in JSON value-domain error is translated here.
    # Serializer/type/recursion/memory/cancellation errors are not hidden.
    try:
        body = json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except ValueError as error:
        raise RenderComputationFault() from error
    stderr = baseline_parts.legacy_summary_final
    stderr += format_classification_line(wire) + "\n"
    mutation_count = sum(item.diagnostic.kind == "mutation-infra" for item in wire.execution or ())
    stderr += (
        "code-forge: report-v2 execution-diagnostics=%d mutation-infra=%d "
        "[persisted-record-unbound; not invocation status]\n"
        % (len(wire.execution or ()), mutation_count)
    )
    stderr += baseline_parts.infra_footer
    return body, stderr, block


def prepare_from_owned_snapshot(
    state: State,
    context: RenderingInputs,
    *,
    snapshot_limits: SnapshotLimits,
    preparation_limits: PreparationLimits,
) -> PreparedText:
    """Build baseline first; optional failures restore its exact final text.

    The caller supplies captured inputs, never independently prepared baseline
    or projection. All private helpers consume one owned State/context. This
    establishes in-process consistency, not current-invocation provenance.
    Human v2 diagnostic-count wording is a local candidate, not a CLI contract.
    """
    if type(preparation_limits) is not PreparationLimits:
        raise TypeError("expected PreparationLimits")
    owned_state, owned_context = _capture_owned_rendering(state, context, limits=snapshot_limits)
    baseline_parts = _build_baseline_parts(owned_state, owned_context)
    baseline = baseline_parts.text

    def restore(reason):
        return _restore_baseline(baseline, preparation_limits.reader, reason)

    readable, _ = _legacy_envelope_accepted(baseline.stdout)
    if not readable or len(baseline.stdout) > preparation_limits.legacy_chars:
        return restore("baseline-unusable")
    try:
        projection = classify_report_records_v2(owned_state)
    except ProjectionComputationFault:
        return restore("projection-failed")
    if any(error.stage == "projection" for error in projection.classification_errors):
        return restore("v2-global-fallback")
    try:
        body, stderr, expected_block = _render_candidate(baseline_parts, projection)
    except RenderComputationFault:
        return restore("v2-preparation-failed")
    prepared = prepare_final_candidate(
        baseline, candidate_body=body, candidate_stderr=stderr, limits=preparation_limits
    )
    if prepared.writer_reason == "v2-prepared" and (
        not exact_json_equal(expected_block, prepared.selection.block)
        or not projection_matches_block(projection, prepared.selection.block)
    ):
        return restore("v2-content-mismatch")
    return prepared
