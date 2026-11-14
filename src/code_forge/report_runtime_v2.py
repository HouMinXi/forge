# SPDX-License-Identifier: Apache-2.0
"""Opt-in production report selection; never changes verdict or exit status.

The CLI supplies its already completed v1 streams from the same canonical
reload and captured context. Optional admission failures restore those exact
streams, not a reconstructed surrogate. MCP selects from original bytes.
"""

from code_forge.report_baseline_v2 import (
    RenderingInputs,
    SnapshotLimits,
    _capture_owned_rendering,
    _build_baseline_parts,
)
from code_forge.report_orchestration_v2 import _render_candidate, RenderComputationFault
from code_forge.report_preparation_v2 import (
    FinalText,
    PreparationLimits,
    PreparedText,
    prepare_final_candidate,
    _restore_baseline,
)
from code_forge.report_projection_v2 import classify_report_records_v2, ProjectionComputationFault
from code_forge.report_projection_check_v2 import projection_matches_block, exact_json_equal
from code_forge.report_reader_v2 import ReaderLimits, ReadSelection, read_report_view
from code_forge.state import State

# These bound optional presentation work, not review/provider execution.
READER_LIMITS = ReaderLimits(8 * 1024 * 1024, 64, 50_000)
SNAPSHOT_LIMITS = SnapshotLimits(1_000_000, 8 * 1024 * 1024, 50_000)
PREPARATION_LIMITS = PreparationLimits(1_048_576, 8 * 1024 * 1024, 1024 * 1024, READER_LIMITS)


def select_report_bytes(raw: bytes) -> ReadSelection:
    return read_report_view(raw, requested_version="v2", limits=READER_LIMITS)


def selection_metadata(selection: ReadSelection) -> dict:
    return {
        "requested_version": selection.requested_version,
        "selected_version": selection.selected_version,
        "envelope_condition": selection.envelope_condition,
        "selection_reason": selection.selection_reason,
    }


def prepare_production_report(
    state: State, context: RenderingInputs, baseline: FinalText
) -> PreparedText:
    """Optional v2 after the caller has successfully built its complete v1.

    Baseline errors occur before this call. Only bounded snapshot admission
    and explicitly classified optional computation faults fall back. Fatal,
    cancellation and unexpected implementation errors retain their identity.
    """
    if type(baseline) is not FinalText:
        raise TypeError("expected completed FinalText baseline")

    def restore(reason):
        return _restore_baseline(baseline, READER_LIMITS, reason)

    if len(baseline.stdout) > PREPARATION_LIMITS.legacy_chars:
        return restore("baseline-unusable")
    try:
        owned, captured = _capture_owned_rendering(state, context, limits=SNAPSHOT_LIMITS)
    except (TypeError, ValueError):
        return restore("snapshot-rejected")
    parts = _build_baseline_parts(owned, captured)
    if parts.text != baseline:
        return restore("baseline-mismatch")
    try:
        projection = classify_report_records_v2(owned)
    except ProjectionComputationFault:
        return restore("projection-failed")
    if any(error.stage == "projection" for error in projection.classification_errors):
        return restore("v2-global-fallback")
    try:
        body, stderr, block = _render_candidate(parts, projection)
    except RenderComputationFault:
        return restore("v2-preparation-failed")
    result = prepare_final_candidate(
        baseline, candidate_body=body, candidate_stderr=stderr, limits=PREPARATION_LIMITS
    )
    if result.writer_reason == "v2-prepared" and (
        not exact_json_equal(block, result.selection.block)
        or not projection_matches_block(projection, result.selection.block)
    ):
        return restore("v2-content-mismatch")
    return result
