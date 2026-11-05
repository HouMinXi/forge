# SPDX-License-Identifier: Apache-2.0
"""Final-text kernel controls only; no claim of snapshot or CLI binding."""

import io
import json

import pytest

from code_forge.report_preparation_v2 import (
    FinalText,
    PreparationLimits,
    finalize_payload,
    prepare_final_candidate,
)
from code_forge.report_reader_v2 import ReaderLimits, read_report_view
from tests.test_report_reader_v2 import block, document, mutation_block


def body(doc):
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":"))


def baseline():
    return FinalText(body(document(block("v1"))) + "\n", "summary\nclassification\nlocator\nfooter\n")


def limits(chars=10000, byte_limit=10000, reader_bytes=10000):
    return PreparationLimits(chars, byte_limit, 10000, ReaderLimits(reader_bytes, 64, 100))


def prepare(original=None, candidate=None, budget=None):
    return prepare_final_candidate(
        baseline() if original is None else original,
        candidate_body=body(document(mutation_block())) if candidate is None else candidate,
        candidate_stderr="candidate summary\n",
        limits=limits() if budget is None else budget,
    )


@pytest.mark.parametrize("value", ["{}", "{}\n", "{}\n\n"])
def test_framing_exactly_matches_print(value):
    out = io.StringIO()
    print(value, file=out)
    assert finalize_payload(value, "stderr").stdout == out.getvalue()


def test_metadata_comes_from_exact_final_stdout():
    result = prepare()
    assert result.writer_reason == "v2-prepared"
    assert result.selection == read_report_view(
        result.text.stdout.encode(), requested_version="v2", limits=limits().reader
    )
    assert result.selection.block["execution"][0]["record"]["record_index"] == 0


@pytest.mark.parametrize(
    "adjustment,reason", [(-1, "v2-budget-refused"), (0, "v2-prepared"), (1, "v2-prepared")]
)
def test_final_newline_character_boundary(adjustment, reason):
    candidate = body(document(mutation_block()))
    result = prepare(candidate=candidate, budget=limits(chars=len(candidate) + 1 + adjustment))
    assert result.writer_reason == reason


def test_byte_boundary_independent_from_characters():
    doc = document(mutation_block())
    doc["annotation"] = "汉字"
    candidate = body(doc)
    old_doc = document(block("v1"))
    old_doc["annotation"] = "汉字"
    original = FinalText(body(old_doc) + "\n", baseline().stderr)
    for adjustment, accepted in [(-1, False), (0, True), (1, True)]:
        result = prepare(
            original=original,
            candidate=candidate,
            budget=limits(byte_limit=len((candidate + "\n").encode()) + adjustment),
        )
        assert (result.writer_reason == "v2-prepared") is accepted


@pytest.mark.parametrize("candidate", ["{", "{}", body(document(block("v1"))), "\ud800"])
def test_failed_candidate_restores_all_baseline_streams(candidate):
    original = baseline()
    result = prepare(original=original, candidate=candidate)
    assert result.text is original
    assert result.selection.block["schema"] == "forge-report-classification/v1"
    assert result.writer_reason == "v2-preparation-failed"


@pytest.mark.parametrize("original", [FinalText("invalid\n", "keep"), FinalText("", "keep")])
def test_invalid_baseline_not_repaired(original):
    result = prepare(original=original)
    assert result.text is original
    assert result.writer_reason == "baseline-unusable"
    assert result.selection.selected_version is None


def test_oversize_baseline_not_repaired():
    original = baseline()
    result = prepare(original=original, budget=limits(chars=len(original.stdout) - 1))
    assert result.text is original
    assert result.writer_reason == "baseline-unusable"


def test_old_readable_baseline_above_new_reader_bound_preserved_honestly():
    original = baseline()
    result = prepare(original=original, budget=limits(reader_bytes=1))
    assert result.text is original
    assert result.selection.selected_version is None
    assert result.selection.selection_reason == "byte-limit"


def test_unavailable_candidate_restores_baseline():
    candidate = block()
    for key in candidate:
        if key not in ("schema", "availability", "unavailable_reason"):
            candidate[key] = None
    candidate.update(availability="unavailable", unavailable_reason="budget-exceeded")
    original = baseline()
    result = prepare(original=original, candidate=body(document(candidate)))
    assert result.text is original
    assert result.writer_reason == "v2-unavailable"


def test_returned_global_projection_failure_restores_baseline():
    candidate = block()
    candidate.update(
        classification_complete=False,
        product_list_count=None,
        unresolved_candidate_count=None,
        product_path_record_count=None,
        classification_errors=[
            dict(record_index=None, stage="projection", code="projection-computation-fault")
        ],
    )
    original = baseline()
    result = prepare(original=original, candidate=body(document(candidate)))
    assert result.text is original
    assert result.writer_reason == "v2-global-fallback"


def test_readable_unavailable_baseline_does_not_block_valid_v2():
    value = block("v1")
    for key in value:
        if key not in ("schema", "availability", "unavailable_reason"):
            value[key] = None
    value.update(availability="unavailable", unavailable_reason="budget-exceeded")
    original = FinalText(body(document(value)) + "\n", "unavailable baseline stderr\n")
    assert prepare(original=original).writer_reason == "v2-prepared"
    result = prepare(original=original, candidate="{")
    assert result.text is original
    assert result.selection.block == value


def test_invalid_candidate_stderr_restores_baseline_before_any_write():
    original = baseline()
    result = prepare_final_candidate(
        original,
        candidate_body=body(document(mutation_block())),
        candidate_stderr="\ud800",
        limits=limits(),
    )
    assert result.text is original
    assert result.writer_reason == "v2-preparation-failed"


@pytest.mark.parametrize("error", [MemoryError, RecursionError, TypeError])
def test_reader_critical_errors_propagate(monkeypatch, error):
    from code_forge import report_preparation_v2 as module

    def fail(*args, **kwargs):
        raise error()

    monkeypatch.setattr(module, "read_report_view", fail)
    with pytest.raises(error):
        prepare()


def test_candidate_cannot_drop_baseline_extension_or_products():
    old_doc = document(block("v1"))
    old_doc["runs"][0]["properties"]["retained"] = {"value": 1}
    original = FinalText(body(old_doc) + "\n", "complete baseline\n")
    result = prepare(original=original)
    assert result.text is original
    assert result.writer_reason == "v2-substrate-mismatch"


def test_oversize_body_rejected_before_framing(monkeypatch):
    from code_forge import report_preparation_v2 as module

    original = baseline()

    def forbidden(*args):
        pytest.fail("oversize candidate was framed")

    monkeypatch.setattr(module, "finalize_payload", forbidden)
    result = prepare(original=original, candidate="x" * 8_000_000)
    assert result.text is original
    assert result.writer_reason == "v2-budget-refused"


def test_oversize_baseline_metadata_refused_without_encoding_copy():
    import tracemalloc

    original = FinalText("x" * 8_000_000, "keep")
    tracemalloc.start()
    try:
        result = prepare(original=original)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result.text is original
    assert result.selection.selection_reason == "byte-limit"
    assert peak < 1_000_000


def test_candidate_stderr_explicit_byte_budget():
    original = baseline()
    result = prepare_final_candidate(
        original,
        candidate_body=body(document(mutation_block())),
        candidate_stderr="汉字",
        limits=PreparationLimits(10000, 10000, 5, ReaderLimits(10000, 64, 100)),
    )
    assert result.text is original
    assert result.writer_reason == "v2-budget-refused"
