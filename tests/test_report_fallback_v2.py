# SPDX-License-Identifier: Apache-2.0
"""Six-field policy qualification; probes are not canonical/native failures."""

import asyncio
from dataclasses import asdict

import pytest

from code_forge import report_projection as v1, report_projection_v2 as v2
from code_forge.disposition import Disposition
from code_forge.state import State, StateFinding, Mode

FIELDS = ("id", "fingerprint", "file", "line_range", "description", "disposition")
OUTPUT = dict(
    zip(
        FIELDS,
        ("finding_id", "fingerprint", "file", "line_range", "description", "disposition"),
        strict=True,
    )
)
ORDINARY = (AttributeError, ValueError, TypeError, RuntimeError)
CRITICAL = (MemoryError, RecursionError, KeyboardInterrupt, SystemExit, asyncio.CancelledError)
DATA = dict(
    id="id-1",
    fingerprint="fp-1",
    file="a.py",
    line_range=[1, 2],
    description="description",
    disposition=Disposition.CONFIRMED,
)


class FieldProbe:
    def __init__(self, field, error):
        self._field = field
        self._error = error
        self._reads = []
        self._data = DATA.copy()

    def __getattribute__(self, name):
        data = object.__getattribute__(self, "_data")
        if name in data:
            object.__getattribute__(self, "_reads").append(name)
            if name == object.__getattribute__(self, "_field"):
                raise object.__getattribute__(self, "_error")
            return data[name]
        return object.__getattribute__(self, name)


def expected_record(field, index=7):
    expected = dict(
        index=index,
        shape=None,
        channel="quarantine",
        finding_id="id-1",
        fingerprint="fp-1",
        file="a.py",
        line_range=(1, 2),
        description="description",
        disposition="CONFIRMED",
    )
    expected[OUTPUT[field]] = None
    return expected


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("error_type", ORDINARY)
def test_v1_historical_ordinary_getter_retains_siblings(field, error_type):
    probe = FieldProbe(field, error_type("private-marker"))
    result = v1._fallback_record(7, probe)
    assert asdict(result) == expected_record(field)
    assert probe._reads == list(FIELDS)
    assert "private-marker" not in repr(result)


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("error_type", (MemoryError, RecursionError))
def test_v1_historical_critical_getter_fold(field, error_type):
    result = v1._fallback_record(7, FieldProbe(field, error_type("private-marker")))
    assert asdict(result) == expected_record(field)


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("error_type", ORDINARY)
def test_v2_ordinary_getter_retains_siblings(field, error_type):
    probe = FieldProbe(field, error_type("private-marker"))
    result = v2._fallback_record(7, probe)
    assert asdict(result) == expected_record(field)
    assert probe._reads == list(FIELDS)
    assert "private-marker" not in repr(result)


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("error_type", CRITICAL)
def test_v2_critical_getter_preserves_exception_object_and_stops(field, error_type):
    original = error_type("private-marker")
    probe = FieldProbe(field, original)
    with pytest.raises(error_type) as caught:
        v2._fallback_record(7, probe)
    assert caught.value is original
    assert probe._reads == list(FIELDS[: FIELDS.index(field) + 1])


def inject_conversion(monkeypatch, record, field, original):
    helper_name = (
        "_safe_line_range"
        if field == "line_range"
        else "_safe_disposition_value"
        if field == "disposition"
        else "_safe_str"
    )
    real = getattr(v2, helper_name)
    target = getattr(record, field)
    calls = []

    def failing(value):
        if value is target:
            calls.append(field)
            raise original
        return real(value)

    monkeypatch.setattr(v2, helper_name, failing)
    return calls


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("error_type", (*ORDINARY, *CRITICAL))
def test_v2_each_conversion_boundary(field, error_type, monkeypatch):
    record = StateFinding(source="L1", **DATA)
    original = error_type("private-marker")
    calls = inject_conversion(monkeypatch, record, field, original)
    if error_type in ORDINARY:
        result = v2._fallback_record(7, record)
        assert asdict(result) == expected_record(field)
        assert "private-marker" not in repr(result)
    else:
        with pytest.raises(error_type) as caught:
            v2._fallback_record(7, record)
        assert caught.value is original
    assert calls == [field]


@pytest.mark.parametrize("path", ["record", "global"])
@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("error_type", (*ORDINARY, *CRITICAL))
def test_live_classifier_reaches_v2_conversion_fallback(path, field, error_type, monkeypatch):
    record = StateFinding(source="L1", **DATA)
    snapshot = State(mode=Mode.CI, findings=[record])
    if path == "record":

        def record_fault(*args):
            raise v2._RecordTypeError("record setup")

        monkeypatch.setattr(v2, "_build_record", record_fault)
    else:

        def global_fault(*args):
            raise v2.ProjectionComputationFault("global setup")

        monkeypatch.setattr(v2, "reporting_product_findings", global_fault)
    original = error_type("private-marker")
    calls = inject_conversion(monkeypatch, record, field, original)
    if error_type in ORDINARY:
        result = v2.classify_report_records_v2(snapshot)
        assert len(result.quarantine) == 1
        assert asdict(result.quarantine[0]) == expected_record(field, index=0)
        assert result.classification_complete is False
        assert "private-marker" not in repr(result)
    else:
        with pytest.raises(error_type) as caught:
            v2.classify_report_records_v2(snapshot)
        assert caught.value is original
    assert calls == [field]


@pytest.mark.parametrize(
    "field,value",
    [
        ("line_range", [True, 2]),
        ("line_range", [1.0, 2]),
        ("line_range", (1, 2)),
        ("disposition", "CONFIRMED"),
        ("disposition", None),
    ],
)
def test_fallback_exact_scalar_domains(field, value):
    record = StateFinding(source="L1", **DATA)
    setattr(record, field, value)
    assert asdict(v2._fallback_record(7, record)) == expected_record(field)


@pytest.mark.parametrize("error_type", [MemoryError, RecursionError])
def test_critical_guard_omission_causes_attributable_consumer_failure_then_restores(
    monkeypatch, error_type
):
    from code_forge.report_orchestration_v2 import prepare_from_owned_snapshot
    from code_forge.report_baseline_v2 import RenderingInputs, SnapshotLimits
    from code_forge.report_preparation_v2 import PreparationLimits
    from code_forge.report_reader_v2 import ReaderLimits
    from code_forge.state import Verdict

    source = State(
        mode=Mode.CI,
        verdict=Verdict.FAIL,
        findings=[StateFinding("bad", "fp", "L1", Disposition.CONFIRMED, "a.py", [1, 2], None)],
    )
    original = error_type("private-marker")
    real = v1._safe_str

    def fail_conversion(value):
        if value is None:
            raise original
        return real(value)

    monkeypatch.setattr(v1, "_safe_str", fail_conversion)
    monkeypatch.setattr(v2, "_safe_str", fail_conversion)

    def prepare():
        return prepare_from_owned_snapshot(
            source,
            RenderingInputs("test", {}),
            snapshot_limits=SnapshotLimits(10000, 100000, 100),
            preparation_limits=PreparationLimits(100000, 100000, 100000, ReaderLimits(100000, 64, 100)),
        )

    with monkeypatch.context() as omitted:
        # The actual historical helper is the control with the critical guard
        # absent, not a fake policy implementation in a test adapter.
        omitted.setattr(v2, "_fallback_record", v1._fallback_record)
        result = prepare()
        assert result.writer_reason == "v2-prepared"
        assert len(result.selection.block["quarantine"]) == 1
    with pytest.raises(error_type) as caught:
        prepare()
    assert caught.value is original
