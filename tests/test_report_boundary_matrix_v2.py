# SPDX-License-Identifier: Apache-2.0
"""Source-bound B05/B07/B08/B10 matrices; no deployment defaults."""

import json

import pytest

from code_forge import report_reader_v2 as reader
from code_forge.report_reader_v2 import ReaderLimits, read_report_view
from tests.test_report_reader_v2 import document, raw, block

LIMITS = ReaderLimits(100000, 80, 100)


def select(encoded, version="v2"):
    return read_report_view(encoded, requested_version=version, limits=LIMITS)


def actual_three_records(version):
    from code_forge.state import StateFinding
    from code_forge.disposition import Disposition
    from code_forge.report_baseline_v2 import build_owned_report_pair
    from tests.test_report_baseline_v2 import fixture_state, context, LIMITS as snapshot_limits
    from tests.test_report_orchestration_v2 import prepare

    source = fixture_state("product")
    source.findings += [
        StateFinding("same", "u", "L1", Disposition.UNCERTAIN, "a.py", [1, 1], "U"),
        StateFinding("same", "p2", "L1", Disposition.CONFIRMED, "b.py", [2, 2], "P2"),
    ]
    text = (
        build_owned_report_pair(source, context(), limits=snapshot_limits).baseline.stdout
        if version == "v1"
        else prepare(source).text.stdout
    )
    assert select(text.encode(), version).selected_version == version
    return json.loads(text)


@pytest.mark.parametrize("version", ["v1", "v2"])
@pytest.mark.parametrize("index", [-1, True, 0.0, 3, 10**80, 1])
def test_b05_cross_partition_bad_index_refused_before_semantic_validator(monkeypatch, version, index):
    data = actual_three_records(version)
    selected_block = data["runs"][0]["properties"]["report_classification"]
    # Product indexes are [0,2], unresolved index is 1. Value 1 therefore
    # duplicates an index across partitions; 3 makes a sparse [0,1,3] union.
    selected_block["product"][1]["record"]["record_index"] = index

    def forbidden(_):
        pytest.fail("bad index reached semantic validator")

    monkeypatch.setattr(
        reader, "validate_block_dict" if version == "v1" else "_validate_v2_block", forbidden
    )
    result = select(raw(data), version)
    assert (result.envelope_condition, result.selection_reason, result.block) == (
        "accepted",
        "invalid-block",
        None,
    )


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_b05_dense_descending_order_reaches_real_schema_validator(monkeypatch, version):
    data = actual_three_records(version)
    selected_block = data["runs"][0]["properties"]["report_classification"]
    selected_block["product"].reverse()  # Union still 0,1,2; product order now 2,0.
    symbol = "validate_block_dict" if version == "v1" else "_validate_v2_block"
    real = getattr(reader, symbol)
    seen = []

    def observe(candidate):
        seen.append(candidate)
        return real(candidate)

    monkeypatch.setattr(reader, symbol, observe)
    result = select(raw(data), version)
    assert len(seen) == 1
    assert result.selection_reason == "invalid-block"
    selected_block["product"].reverse()
    assert select(raw(data), version).selected_version == version
    assert len(seen) == 2


def extended(fragment):
    # Known-good complete envelope, mutate only an unrelated extension.
    valid = raw(document())
    assert select(valid).selected_version == "v2"
    return valid[:-1] + b',"extension":' + fragment + b"}"


@pytest.mark.parametrize(
    "fragment", [b'{"\\ud800":1}', b'{"nested":{"\\udfff":1}}', b'{"value":"\\ud800"}']
)
def test_b07_scalar_guard_omission_and_restoration_on_valid_envelope(monkeypatch, fragment):
    encoded = extended(fragment)
    assert select(encoded).selection_reason == "invalid-json"
    with monkeypatch.context() as patch:
        patch.setattr(reader, "_scalar_strings", lambda _: None)
        assert select(encoded).selected_version == "v2"
    assert select(encoded).selection_reason == "invalid-json"


def test_b07_valid_surrogate_pair_key_remains_selected():
    encoded = extended(b'{"\\ud83d\\ude42":"valid"}')
    assert select(encoded).selected_version == "v2"


@pytest.mark.parametrize(
    "fragment,reason,hook,replacement",
    [
        (b'{"x":1,"x":2}', "duplicate-key", "_pairs", dict),
        (b'{"nested":{"x":1,"\\u0078":2}}', "duplicate-key", "_pairs", dict),
        (b'{"x":NaN}', "nonfinite-number", "_float", float),
        (b'{"x":Infinity}', "nonfinite-number", "_float", float),
        (b'{"x":-Infinity}', "nonfinite-number", "_float", float),
        (b'{"x":1e999}', "nonfinite-number", "_float", float),
        (b'{"x":-1e999}', "nonfinite-number", "_float", float),
    ],
)
def test_b08_parser_hook_omission_and_exact_restoration(
    monkeypatch, fragment, reason, hook, replacement
):
    encoded = extended(fragment)
    assert select(encoded).selection_reason == reason
    with monkeypatch.context() as patch:
        patch.setattr(reader, hook, replacement)
        assert select(encoded).selected_version == "v2"
    assert select(encoded).selection_reason == reason


def cardinality_case(kind, version):
    candidate = block(version)
    if kind == "unavailable":
        for key in candidate:
            if key not in ("schema", "availability", "unavailable_reason"):
                candidate[key] = None
        candidate.update(availability="unavailable", unavailable_reason="budget-exceeded")
    elif kind in ("global", "renderer"):
        stage = "projection" if kind == "global" else "render"
        code = "projection-computation-fault" if kind == "global" else "renderer-computation-fault"
        candidate.update(
            classification_complete=False,
            product_list_count=None,
            unresolved_candidate_count=None,
            product_path_record_count=None,
            classification_errors=[dict(record_index=None, stage=stage, code=code)],
        )
        if kind == "renderer":
            candidate["delivery_mode"] = "renderer-fallback"
    elif kind == "quarantine":
        from code_forge.report_baseline_v2 import build_owned_report_pair
        from tests.test_report_baseline_v2 import fixture_state, context, LIMITS as snapshot_limits
        from tests.test_report_orchestration_v2 import prepare

        source = fixture_state("product")
        source.findings[0].description = None
        text = (
            build_owned_report_pair(source, context(), limits=snapshot_limits).baseline.stdout
            if version == "v1"
            else prepare(source).text.stdout
        )
        candidate = json.loads(text)["runs"][0]["properties"]["report_classification"]
    return document(candidate)


@pytest.mark.parametrize("version", ["v1", "v2"])
@pytest.mark.parametrize("kind", ["ordinary", "unavailable", "global", "renderer", "quarantine"])
@pytest.mark.parametrize("results_count", [0, 1, 2])
def test_b10_conditional_cardinality_matrix_calls_actual_guard(
    monkeypatch, version, kind, results_count
):
    data = cardinality_case(kind, version)
    # Validate each block with the actual schema validator, independently of
    # cross-envelope equality. Invalid block fixtures cannot mask this test.
    candidate = data["runs"][0]["properties"]["report_classification"]
    validator = reader.validate_block_dict if version == "v1" else reader._validate_v2_block
    assert validator(candidate) is None
    data["runs"][0]["results"] = [{} for _ in range(results_count)]
    real = reader._cross_envelope_cardinality_ok
    seen = []

    def observe(selected_block, run):
        result = real(selected_block, run)
        seen.append(result)
        return result

    monkeypatch.setattr(reader, "_cross_envelope_cardinality_ok", observe)
    result = select(raw(data), version)
    expected = kind != "ordinary" or results_count == 0
    assert seen == [expected]
    assert (result.selected_version == version) is expected
    if not expected:
        assert result.selection_reason == "invalid-block"
        # Omit just this guard: actual consumer now falsely selects the report.
        with monkeypatch.context() as patch:
            patch.setattr(reader, "_cross_envelope_cardinality_ok", lambda *_: True)
            assert select(raw(data), version).selected_version == version
        assert select(raw(data), version).selection_reason == "invalid-block"


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_b10_incomplete_flag_alone_is_schema_invalid(monkeypatch, version):
    data = document(block(version))
    data["runs"][0]["properties"]["report_classification"]["classification_complete"] = False
    data["runs"][0]["results"] = [{}]

    def forbidden(*args):
        pytest.fail("schema-invalid flag reached cardinality guard")

    monkeypatch.setattr(reader, "_cross_envelope_cardinality_ok", forbidden)
    assert select(raw(data), version).selection_reason == "invalid-block"


@pytest.mark.parametrize("version", ["v1", "v2"])
@pytest.mark.parametrize("results_count", [2, 3, 4])
def test_b10_real_product_plus_unresolved_cardinality_not_product_alone(version, results_count):
    data = actual_three_records(version)
    candidate = data["runs"][0]["properties"]["report_classification"]
    assert (
        candidate["product_list_count"],
        candidate["unresolved_candidate_count"],
        candidate["product_path_record_count"],
    ) == (2, 1, 3)
    data["runs"][0]["results"] = [{} for _ in range(results_count)]
    # receiptAudit is a separate envelope field, not extra product occurrences.
    data["runs"][0]["properties"]["receiptAudit"] = [{}, {}]
    result = select(raw(data), version)
    assert (result.selected_version == version) is (results_count == 3)
    if results_count != 3:
        assert result.selection_reason == "invalid-block"
