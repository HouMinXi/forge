# SPDX-License-Identifier: Apache-2.0
"""IC-01 unit tests: prepare_legacy_report / prepare_report_views (§1.1)."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

from code_forge.disposition import Disposition
from code_forge.report_presentation import (
    LegacyReportSubstrate,
    PurePreparedReport,
    prepare_legacy_report,
    prepare_report_views,
    parse_report_classification,
    wire_to_json,
)
from code_forge.report_projection import ReportProjection, classify_report_records
from code_forge.sarif import build_sarif_log
from code_forge.state import State, StateFinding, Verdict, Mode


def _sf(**kw) -> StateFinding:
    defaults = dict(
        id="f1",
        fingerprint="fp1",
        source="L1",
        disposition=Disposition.CONFIRMED,
        file="a.py",
        line_range=[1, 2],
        description="desc",
    )
    defaults.update(kw)
    return StateFinding(**defaults)  # type: ignore[arg-type]


def _state(findings=None) -> State:
    return State(
        schema_version=1,
        mode=Mode.CI,
        verdict=Verdict.PASS,
        findings=list(findings or []),
    )


def _minimal_sarif(findings=None) -> dict:
    return build_sarif_log(_state(findings), tool_versions={}, forge_version="0.0-test")


def _stdout_for(doc: dict) -> str:
    return json.dumps(doc, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def test_prepare_legacy_report_owns_safe_substrate_no_alias():
    doc = _minimal_sarif()
    stdout = _stdout_for(doc)
    stderr = "summary line\n"
    # Mutate caller's dict after; substrate must not alias it.
    substrate = prepare_legacy_report(doc, stdout=stdout, stderr=stderr)
    assert substrate.sarif_document is not None
    assert substrate.stdout is stdout or substrate.stdout == stdout
    assert substrate.stderr == stderr
    doc["runs"][0]["results"].append({"evil": True})
    assert "evil" not in json.dumps(substrate.sarif_document)
    # Mutating owned graph must not affect a second prepare
    owned = substrate.sarif_document
    owned["marker"] = "x"
    again = prepare_legacy_report(
        json.loads(stdout), stdout=stdout, stderr=stderr
    )
    assert "marker" not in again.sarif_document


def test_prepare_legacy_report_raw_legacy_only_exact_string_identity():
    # Non-finite duration-like value inside otherwise JSON → acceptance fails
    # Use duplicate key via raw string the parser rejects
    bad_stdout = '{"version":"2.1.0","runs":[{"tool":{"driver":{"name":"code-forge"}},"results":[],"properties":{"a":1,"a":2}}]}'
    # Actually json.loads with object_pairs_hook rejects duplicates; without
    # the hook standard json.loads keeps last. Our acceptor uses the hook.
    # Provide a NaN which parse_float rejects.
    bad_stdout = (
        '{"version":"2.1.0","runs":[{"tool":{"driver":{"name":"code-forge"}},'
        '"results":[],"properties":{"duration":NaN}}]}'
    )
    legacy = {"version": "2.1.0", "runs": []}  # unrelated
    stderr = "kept-stderr-exact"
    sub = prepare_legacy_report(legacy, stdout=bad_stdout, stderr=stderr)
    assert sub.sarif_document is None
    assert sub.stdout is bad_stdout  # exact identity
    assert sub.stderr is stderr


def test_prepare_legacy_report_mismatch_stdout_vs_document_is_raw():
    doc = _minimal_sarif()
    other = _minimal_sarif([_sf(id="other")])
    stdout = _stdout_for(other)
    sub = prepare_legacy_report(doc, stdout=stdout, stderr="")
    assert sub.sarif_document is None
    assert sub.stdout is stdout


def test_prepare_report_views_typeerror_mismatched_modes():
    doc = _minimal_sarif()
    stdout = _stdout_for(doc)
    safe = prepare_legacy_report(doc, stdout=stdout, stderr="")
    assert safe.sarif_document is not None
    with pytest.raises(TypeError):
        prepare_report_views(safe, None)

    raw = LegacyReportSubstrate(sarif_document=None, stdout=stdout, stderr="e")
    proj = classify_report_records(_state())
    with pytest.raises(TypeError):
        prepare_report_views(raw, proj)


def test_prepare_report_views_raw_legacy_only_passthrough_and_unavailable():
    bad_stdout = (
        '{"version":"2.1.0","runs":[{"tool":{"driver":{"name":"code-forge"}},'
        '"results":[],"properties":{"duration":NaN}}]}'
    )
    raw = LegacyReportSubstrate(sarif_document=None, stdout=bad_stdout, stderr="E")
    prepared = prepare_report_views(raw, None)
    assert prepared.stdout is bad_stdout
    assert prepared.stderr is raw.stderr
    assert prepared.report_classification.availability == "unavailable"
    assert prepared.report_classification.unavailable_reason == "invalid-report"


def test_prepare_report_views_empty_safe_projection_available():
    doc = _minimal_sarif()
    stdout = _stdout_for(doc)
    safe = prepare_legacy_report(doc, stdout=stdout, stderr="sum\n")
    assert safe.sarif_document is not None
    proj = classify_report_records(_state())
    prepared = prepare_report_views(safe, proj)
    assert isinstance(prepared, PurePreparedReport)
    assert prepared.report_classification.availability == "available"
    assert prepared.report_classification.delivery_mode == "projection"
    assert prepared.report_classification.classification_complete is True
    assert prepared.report_classification.product_list_count == 0
    # Extension present in stdout
    out_doc = json.loads(prepared.stdout)
    assert "report_classification" in out_doc["runs"][0]["properties"]
    # Reader agrees
    rc = parse_report_classification(
        out_doc["runs"][0], legacy_report_state="accepted"
    )
    assert rc.availability == "available"
    assert wire_to_json(rc)["product"] == []


def test_prepare_report_views_no_classifier_call(monkeypatch):
    doc = _minimal_sarif()
    stdout = _stdout_for(doc)
    safe = prepare_legacy_report(doc, stdout=stdout, stderr="")
    proj = classify_report_records(_state())

    def boom(*a, **k):
        raise AssertionError("classifier must not be called from prepare_*")

    monkeypatch.setattr(
        "code_forge.report_projection.classify_report_records", boom
    )
    monkeypatch.setattr(
        "code_forge.report_presentation.classify_report_records", boom, raising=False
    )
    prepare_report_views(safe, proj)
    prepare_legacy_report(doc, stdout=stdout, stderr="")


def test_prepare_functions_no_io(tmp_path, monkeypatch):
    # Refuse filesystem touches during prepare_*
    real_open = open

    def guarded_open(file, *a, **k):
        p = str(file)
        if p.startswith(str(tmp_path)) or p.startswith("/proc") or p.startswith("/dev"):
            return real_open(file, *a, **k)
        # allow importlib / pytest internals under site-packages
        if "site-packages" in p or "pytest" in p or p.endswith(".pyc"):
            return real_open(file, *a, **k)
        raise AssertionError(f"unexpected open: {file!r}")

    monkeypatch.setattr("builtins.open", guarded_open)
    doc = _minimal_sarif()
    stdout = _stdout_for(doc)
    safe = prepare_legacy_report(doc, stdout=stdout, stderr="")
    proj = classify_report_records(_state())
    prepare_report_views(safe, proj)


def test_prepare_does_not_mutate_projection_or_substrate():
    doc = _minimal_sarif([_sf()])
    stdout = _stdout_for(doc)
    safe = prepare_legacy_report(doc, stdout=stdout, stderr="s")
    proj = classify_report_records(_state([_sf()]))
    before_proj = copy.deepcopy(proj)
    before_doc = copy.deepcopy(safe.sarif_document)
    prepare_report_views(safe, proj)
    assert proj == before_proj
    assert safe.sarif_document == before_doc
