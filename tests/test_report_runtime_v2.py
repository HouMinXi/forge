# SPDX-License-Identifier: Apache-2.0
"""Independent production-selector contracts; not CLI/MCP publication proof."""

import asyncio
import copy
from dataclasses import replace

import pytest

from code_forge import report_runtime_v2 as runtime
from code_forge.report_baseline_v2 import build_owned_report_pair, SnapshotLimits
from code_forge.report_preparation_v2 import FinalText
from tests.test_report_baseline_v2 import context, fixture_state, LIMITS


def inputs(kind="mutation"):
    source, captured = fixture_state(kind), context()
    baseline = build_owned_report_pair(source, captured, limits=LIMITS).baseline
    return source, captured, baseline


@pytest.mark.parametrize("kind", ["empty", "product", "unresolved", "mutation", "infra"])
def test_healthy_kinds_use_real_pipeline_without_mutating_inputs(kind):
    source, captured, baseline = inputs(kind)
    before = copy.deepcopy((source, captured))
    result = runtime.prepare_production_report(source, captured, baseline)
    assert result.writer_reason == "v2-prepared"
    assert result.selection.selected_version == "v2"
    assert result.selection == runtime.select_report_bytes(result.text.stdout.encode())
    assert (source, captured) == before
    assert "not invocation status" in result.text.stderr


@pytest.mark.parametrize("rejection", ["shape", "limit"])
def test_snapshot_rejection_returns_original_stream_object(monkeypatch, rejection):
    source, captured, baseline = inputs()
    if rejection == "shape":
        source.findings = tuple(source.findings)
    else:
        monkeypatch.setattr(runtime, "SNAPSHOT_LIMITS", SnapshotLimits(1, 1, 1))

    def forbidden(*args):
        pytest.fail("optional projection must not run")

    monkeypatch.setattr(runtime, "classify_report_records_v2", forbidden)
    result = runtime.prepare_production_report(source, captured, baseline)
    assert result.text is baseline
    assert result.writer_reason == "snapshot-rejected"
    assert result.selection.selected_version == "v1"


def test_actual_baseline_mismatch_does_not_reconstruct_or_publish(monkeypatch):
    source, captured, baseline = inputs()
    supplied = FinalText(baseline.stdout + " \n", baseline.stderr + "synthetic suffix\n")

    def forbidden(*args):
        pytest.fail("mismatch must precede optional projection")

    monkeypatch.setattr(runtime, "classify_report_records_v2", forbidden)
    result = runtime.prepare_production_report(source, captured, supplied)
    assert result.text is supplied and result.writer_reason == "baseline-mismatch"


@pytest.mark.parametrize(
    "boundary,error,reason",
    [
        ("classify_report_records_v2", runtime.ProjectionComputationFault, "projection-failed"),
        ("_render_candidate", runtime.RenderComputationFault, "v2-preparation-failed"),
    ],
)
def test_only_typed_optional_faults_restore_safe_baseline(monkeypatch, boundary, error, reason):
    source, captured, baseline = inputs()

    def fail(*args):
        raise error("synthetic-private-detail")

    monkeypatch.setattr(runtime, boundary, fail)
    result = runtime.prepare_production_report(source, captured, baseline)
    assert result.text is baseline and result.writer_reason == reason
    assert "synthetic-private-detail" not in repr(result)
    assert result.selection.selected_version == "v1"


@pytest.mark.parametrize(
    "boundary",
    [
        "_capture_owned_rendering",
        "_build_baseline_parts",
        "classify_report_records_v2",
        "_render_candidate",
        "prepare_final_candidate",
    ],
)
@pytest.mark.parametrize(
    "error",
    [MemoryError, RecursionError, KeyboardInterrupt, SystemExit, asyncio.CancelledError, RuntimeError],
)
def test_fatal_cancel_and_unexpected_errors_preserve_identity(monkeypatch, boundary, error):
    source, captured, baseline = inputs()
    primary = error("synthetic-private-detail")

    def fail(*args, **kwargs):
        raise primary

    monkeypatch.setattr(runtime, boundary, fail)
    with pytest.raises(error) as caught:
        runtime.prepare_production_report(source, captured, baseline)
    assert caught.value is primary


@pytest.mark.parametrize(
    "boundary",
    [
        "_build_baseline_parts",
        "classify_report_records_v2",
        "_render_candidate",
        "prepare_final_candidate",
    ],
)
@pytest.mark.parametrize("error", [TypeError, ValueError])
def test_nonadmission_programming_errors_are_not_optional_faults(monkeypatch, boundary, error):
    source, captured, baseline = inputs()
    primary = error("synthetic-private-detail")

    def fail(*args, **kwargs):
        raise primary

    monkeypatch.setattr(runtime, boundary, fail)
    with pytest.raises(error) as caught:
        runtime.prepare_production_report(source, captured, baseline)
    assert caught.value is primary


def test_mismatched_candidate_metadata_restores_exact_baseline(monkeypatch):
    source, captured, baseline = inputs()
    real = runtime._render_candidate

    def mismatch(*args):
        body, stderr, block = real(*args)
        wrong = copy.deepcopy(block)
        wrong["execution"][0]["record"]["fields"]["description"] = "synthetic wrong record"
        return body, stderr, wrong

    monkeypatch.setattr(runtime, "_render_candidate", mismatch)
    result = runtime.prepare_production_report(source, captured, baseline)
    assert result.text is baseline and result.writer_reason == "v2-content-mismatch"


def test_original_invalid_utf8_is_not_lossily_decoded():
    raw = b'{"synthetic-private": "\xff"}'
    result = runtime.select_report_bytes(raw)
    assert raw == b'{"synthetic-private": "\xff"}'
    assert result.selected_version is None and result.selection_reason == "invalid-utf8"
    assert result.block is None
    assert "synthetic-private" not in repr(runtime.selection_metadata(result))


def test_selector_receives_identical_original_bytes(monkeypatch):
    raw = b"\xff \n"
    real = runtime.read_report_view
    seen = []

    def observe(value, **kwargs):
        seen.append(value)
        return real(value, **kwargs)

    monkeypatch.setattr(runtime, "read_report_view", observe)
    runtime.select_report_bytes(raw)
    assert len(seen) == 1 and seen[0] is raw


def test_metadata_is_four_fixed_fields_never_block_contents():
    source, captured, baseline = inputs()
    selection = runtime.prepare_production_report(source, captured, baseline).selection
    selection = replace(selection, block={"synthetic-private-detail": "not metadata"})
    assert runtime.selection_metadata(selection) == {
        "requested_version": "v2",
        "selected_version": "v2",
        "envelope_condition": selection.envelope_condition,
        "selection_reason": selection.selection_reason,
    }
    assert "synthetic-private-detail" not in repr(runtime.selection_metadata(selection))


def test_optional_reader_limit_does_not_change_baseline_streams(monkeypatch):
    source, captured, baseline = inputs()
    monkeypatch.setattr(
        runtime, "PREPARATION_LIMITS", replace(runtime.PREPARATION_LIMITS, legacy_chars=1)
    )
    result = runtime.prepare_production_report(source, captured, baseline)
    assert result.text is baseline and result.writer_reason == "baseline-unusable"
