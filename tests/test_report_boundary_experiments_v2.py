# SPDX-License-Identifier: Apache-2.0
"""Bounded B experiments; explicit experimental limits, no production defaults."""

import json
import asyncio

import pytest

from code_forge import mcp_server, report_reader_v2 as reader
from code_forge.report_preparation_v2 import FinalText, PreparationLimits, prepare_final_candidate
from code_forge.report_reader_v2 import ReaderLimits, read_report_view
from tests.test_report_legacy_corpus_v2 import CORPUS
from tests.test_report_reader_v2 import document, raw

C = 1_048_576  # Existing v1 contract, not a new deployment limit.


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_b01_actual_legacy_character_boundary_includes_final_newline(delta):
    # Whitespace-pad a frozen actual CLI report. This is an adapted byte fixture,
    # not a claim that the old emitter itself emitted padding.
    base = CORPUS["cases"][0]["stdout"].rstrip("\n")
    final = base + " " * (C + delta - len(base) - 1) + "\n"
    assert len(final) == C + delta
    old = mcp_server._sarif_projection_with_status(final)
    assert (old[0] == 0) is (delta <= 0)
    baseline = FinalText(final, CORPUS["cases"][0]["stderr"])
    result = prepare_final_candidate(
        baseline,
        candidate_body="{",
        candidate_stderr="candidate",
        limits=PreparationLimits(C, 4 * C, C, ReaderLimits(4 * C, 80, 100)),
    )
    assert result.text is baseline
    assert result.writer_reason == ("baseline-unusable" if delta > 0 else "v2-preparation-failed")
    assert result.text.stdout.endswith("\n")


@pytest.mark.parametrize("char", ["x", "汉", "🙂"])
def test_b02_equal_characters_different_bytes_with_real_old_reader(char):
    value = json.loads(CORPUS["cases"][0]["stdout"])
    value["note"] = char * 16
    text = json.dumps(value, ensure_ascii=False) + "\n"
    reference = json.dumps(value | {"note": "x" * 16}, ensure_ascii=False) + "\n"
    assert len(text) == len(reference)
    assert len(text.encode()) - len(reference.encode()) == 16 * (len(char.encode()) - 1)
    assert mcp_server._sarif_projection_with_status(text)[0] == 0
    for adjustment in (-1, 0, 1):
        selected = read_report_view(
            text.encode(),
            requested_version="v2",
            limits=ReaderLimits(len(text.encode()) + adjustment, 80, 100),
        )
        assert (selected.selected_version == "v1") is (adjustment >= 0)
        if adjustment < 0:
            assert selected.selection_reason == "byte-limit"


@pytest.mark.parametrize("leaf", [None, [], {}])
def test_b03_valid_envelope_depth_is_checked_before_json_parser(monkeypatch, leaf):
    # Root + extension list chain: depth 13 for container leaves, 12 for scalar.
    nested = leaf
    for _ in range(11):
        nested = [nested]
    data = document()
    data["extension"] = nested
    encoded = raw(data)
    depth = 13 if type(leaf) in (list, dict) else 12
    for adjustment in (-1, 0, 1):
        selected = read_report_view(
            encoded, requested_version="v2", limits=ReaderLimits(10000, depth + adjustment, 100)
        )
        assert (selected.selected_version == "v2") is (adjustment >= 0)

    def forbidden(*args, **kwargs):
        pytest.fail("over-depth input reached JSON materialization")

    monkeypatch.setattr(reader.json, "loads", forbidden)
    result = read_report_view(
        encoded, requested_version="v2", limits=ReaderLimits(10000, depth - 1, 100)
    )
    assert result.selection_reason == "depth-limit"


def test_b12_old_readable_strict_refusal_preserves_exact_artifact():
    text = CORPUS["cases"][2]["stdout"]
    baseline = FinalText(text, CORPUS["cases"][2]["stderr"])
    old_before = mcp_server._sarif_projection_with_status(text)
    assert old_before[0] is not None
    result = prepare_final_candidate(
        baseline,
        candidate_body="{",
        candidate_stderr="candidate",
        limits=PreparationLimits(C, 4 * C, C, ReaderLimits(len(text.encode()) - 1, 80, 100)),
    )
    assert result.text is baseline
    assert result.selection.block is None
    assert result.selection.selection_reason == "byte-limit"
    assert mcp_server._sarif_projection_with_status(result.text.stdout) == old_before


@pytest.mark.parametrize("error_type", [RecursionError, asyncio.CancelledError])
def test_b14_injected_parser_recursion_refusal_and_cancellation_propagation(monkeypatch, error_type):
    error = error_type("injected parser fault")

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(reader.json, "loads", fail)
    if error_type is RecursionError:
        result = read_report_view(b"{}", requested_version="v2", limits=ReaderLimits(10000, 80, 10))
        assert (result.envelope_condition, result.selection_reason, result.block) == (
            "invalid-report",
            "invalid-json",
            None,
        )
    else:
        with pytest.raises(asyncio.CancelledError) as caught:
            read_report_view(b"{}", requested_version="v2", limits=ReaderLimits(10000, 80, 10))
        assert caught.value is error


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_b11_actual_candidate_c_boundary_and_complete_notice_byte_fit(delta):
    from tests.test_report_baseline_v2 import fixture_state, context, LIMITS
    from tests.test_report_orchestration_v2 import prepare
    from code_forge.report_baseline_v2 import build_owned_report_pair

    source = fixture_state("empty")
    baseline = build_owned_report_pair(source, context(), limits=LIMITS).baseline
    ready = prepare(source)
    assert ready.writer_reason == "v2-prepared"
    unframed = ready.text.stdout[:-1]
    padded = unframed + " " * (C + delta - 1 - len(unframed))
    result = prepare_final_candidate(
        baseline,
        candidate_body=padded,
        candidate_stderr=ready.text.stderr,
        limits=PreparationLimits(C, 4 * C, C, ReaderLimits(4 * C, 80, 100)),
    )
    if delta <= 0:
        assert result.writer_reason == "v2-prepared"
        assert len(result.text.stdout) == C + delta
    else:
        assert result.writer_reason == "v2-budget-refused"
        assert result.text is baseline
    notice_bytes = len(ready.text.stderr.encode())
    result = prepare_final_candidate(
        baseline,
        candidate_body=unframed,
        candidate_stderr=ready.text.stderr,
        limits=PreparationLimits(C, 4 * C, notice_bytes + delta, ReaderLimits(4 * C, 80, 100)),
    )
    if delta < 0:
        assert result.writer_reason == "v2-budget-refused"
        assert result.text is baseline
        assert "report-v2 execution-diagnostics=" not in result.text.stderr
    else:
        assert result.writer_reason == "v2-prepared"
        assert result.text.stderr == ready.text.stderr


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("projection", "projection-failed"),
        ("global", "v2-global-fallback"),
        ("render", "v2-preparation-failed"),
        ("unavailable", "v2-unavailable"),
    ],
)
def test_b13_mixed_occurrence_fallback_restores_actual_baseline(monkeypatch, fault, reason):
    from dataclasses import replace
    from code_forge import report_orchestration_v2 as orchestration, report_projection_v2
    from code_forge.report_presentation import _unavailable
    from code_forge.state import StateFinding
    from code_forge.disposition import Disposition
    from tests.test_report_baseline_v2 import fixture_state
    from tests.test_report_orchestration_v2 import prepare

    source = fixture_state("mutation")
    source.findings += [
        StateFinding("same", "p", "L1", Disposition.CONFIRMED, "a.py", [1, 1], "P"),
        StateFinding("same", "u", "L1", Disposition.UNCERTAIN, "a.py", [1, 1], "U"),
        StateFinding("bad", "q", "L1", Disposition.CONFIRMED, "a.py", [1, 1], None),
    ]
    observed = []
    real_baseline = orchestration._build_baseline_parts

    def capture(*args):
        result = real_baseline(*args)
        observed.append(result.text)
        return result

    monkeypatch.setattr(orchestration, "_build_baseline_parts", capture)

    def projection_fail(_):
        raise orchestration.ProjectionComputationFault("injected")

    def render_fail(*args):
        raise orchestration.RenderComputationFault("injected")

    if fault == "projection":
        monkeypatch.setattr(orchestration, "classify_report_records_v2", projection_fail)
    elif fault == "global":
        monkeypatch.setattr(report_projection_v2, "reporting_product_findings", projection_fail)
    elif fault == "render":
        monkeypatch.setattr(orchestration, "_render_candidate", render_fail)
    else:
        monkeypatch.setattr(
            orchestration,
            "serialize_report_projection_v2",
            lambda _: replace(_unavailable("budget-exceeded"), schema="forge-report-classification/v2"),
        )
    result = prepare(source)
    assert result.writer_reason == reason
    assert result.text is observed[0]
    old = mcp_server._report_classification_from_stdout(result.text.stdout)
    assert result.selection.block == old
    assert [row["record"]["record_index"] for row in old["product"]] == [1]
    assert [row["record"]["record_index"] for row in old["unresolved_product_candidates"]] == [2]
    assert [row["record_index"] for row in old["quarantine"]] == [0, 3]
    assert old["execution"] == []  # Historical v1 mutation is still quarantine.


@pytest.mark.parametrize("count", [3, 4, 5])
def test_b04_canonical_partition_occurrences_at_n_boundary(tmp_path, count):
    from code_forge.state import StateFinding, save_state, load_state
    from code_forge.disposition import Disposition
    from tests.test_report_baseline_v2 import fixture_state
    from tests.test_report_orchestration_v2 import prepare

    source = fixture_state("mutation")
    records = [
        StateFinding("same", "p", "L1", Disposition.CONFIRMED, "a.py", [1, 1], "P"),
        StateFinding("same", "u", "L1", Disposition.UNCERTAIN, "a.py", [1, 1], "U"),
        source.findings[0],
        StateFinding("same", "q", "L1", Disposition.CONFIRMED, "a.py", [1, 1], None),
        StateFinding("same", "p2", "L1", Disposition.CONFIRMED, "b.py", [2, 2], "P2"),
    ]
    source.findings = records[:count]
    path = tmp_path / "state.json"
    save_state(source, path)
    loaded = load_state(path)
    ready = prepare(loaded)
    assert ready.writer_reason == "v2-prepared"
    result = read_report_view(
        ready.text.stdout.encode(), requested_version="v2", limits=ReaderLimits(100000, 80, 4)
    )
    if count > 4:
        assert (result.envelope_condition, result.selection_reason, result.block) == (
            "accepted",
            "record-limit",
            None,
        )
    else:
        assert result.selected_version == "v2"
        indexes = [
            row["record"]["record_index"]
            for channel in ("product", "unresolved_product_candidates", "execution", "gate")
            for row in result.block[channel]
        ]
        indexes += [row["record_index"] for row in result.block["quarantine"]]
        assert sorted(indexes) == list(range(count))


@pytest.mark.parametrize("index", [-1, True, 0.0, 1, 10**80])
@pytest.mark.parametrize("version", ["v1", "v2"])
def test_b05_invalid_index_has_independent_valid_host_witness(index, version):
    from tests.test_report_reader_v2 import mutation_block

    if version == "v1":
        data = json.loads(next(case["stdout"] for case in CORPUS["cases"] if case["name"] == "capacity"))
    else:
        data = document(mutation_block())
    encoded = raw(data)
    limits = ReaderLimits(100000, 80, 100)
    assert (
        read_report_view(encoded, requested_version=version, limits=limits).selected_version == version
    )
    data["runs"][0]["properties"]["report_classification"]["execution"][0]["record"]["record_index"] = (
        index
    )
    selected = read_report_view(raw(data), requested_version=version, limits=limits)
    assert (selected.envelope_condition, selected.selection_reason, selected.block) == (
        "accepted",
        "invalid-block",
        None,
    )
