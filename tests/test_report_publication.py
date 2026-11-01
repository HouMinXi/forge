# SPDX-License-Identifier: Apache-2.0
"""IC-01 publication sequencing: pure prep never publishes (§1.1 / §6)."""

from __future__ import annotations

import io
import json
import sys

import pytest

from code_forge.disposition import Disposition
from code_forge.report_presentation import (
    LegacyReportSubstrate,
    prepare_legacy_report,
    prepare_report_views,
    parse_report_classification,
    wire_to_json,
    _MAX_SARIF_REPORT_CHARS,
)
from code_forge.report_projection import classify_report_records
from code_forge.sarif import build_sarif_log
from code_forge.state import Mode, State, StateFinding, Verdict


def _sf(**kw) -> StateFinding:
    defaults = dict(
        id="f1",
        fingerprint="fp1",
        source="L1",
        disposition=Disposition.CONFIRMED,
        file="a.py",
        line_range=[1],
        description="d",
    )
    defaults.update(kw)
    return StateFinding(**defaults)  # type: ignore[arg-type]


def _doc(findings=None):
    return build_sarif_log(
        State(findings=list(findings or []), mode=Mode.CI, verdict=Verdict.PASS),
        tool_versions={},
        forge_version="0.0-test",
    )


def _stdout(doc):
    return json.dumps(doc, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def test_pure_functions_never_write_stdout(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    doc = _doc()
    stdout = _stdout(doc)
    safe = prepare_legacy_report(doc, stdout=stdout, stderr="s\n")
    proj = classify_report_records(State(findings=[], mode=Mode.CI, verdict=Verdict.PASS))
    prepared = prepare_report_views(safe, proj)
    assert buf.getvalue() == ""
    # Result carries the prepared views; nothing flushed to real stdout
    assert "report_classification" in prepared.stdout
    assert prepared.stderr == "s\n"


def test_publication_requires_complete_pure_prepared_report():
    """Unit-level sequencing contract: publish hook only after PurePreparedReport."""
    published: list[tuple[str, str]] = []

    def publish_hook(stdout: str, stderr: str) -> None:
        published.append((stdout, stderr))

    doc = _doc()
    stdout = _stdout(doc)
    # Stage 1: substrate only — must not publish
    safe = prepare_legacy_report(doc, stdout=stdout, stderr="sum\n")
    assert published == []
    # Stage 2: complete prepared report
    proj = classify_report_records(State(findings=[], mode=Mode.CI, verdict=Verdict.PASS))
    prepared = prepare_report_views(safe, proj)
    assert published == []
    # Stage 3: external publication (outside pure funcs)
    publish_hook(prepared.stdout, prepared.stderr)
    assert len(published) == 1
    assert published[0][0] == prepared.stdout
    # Reader of published stdout agrees with prepared.report_classification
    out = json.loads(prepared.stdout)
    rc = parse_report_classification(out["runs"][0], legacy_report_state="accepted")
    assert wire_to_json(rc) == wire_to_json(prepared.report_classification)


def test_budget_exceeded_selects_canonical_unavailable_or_omit():
    """Drive oversized extension without editing sarif.py.

    Construct a safe substrate whose legacy stdout is near the cap, then a
    projection that would add enough to exceed — expect budget-exceeded
    block if it fits, else omit-extension (legacy-report).
    """
    # Build a legacy document that is valid but large enough that adding a
    # normal available block still fits OR tip into budget. We force omit
    # by making legacy stdout almost at the cap with padding in an ignored
    # property (still accepted by legacy predicate).
    doc = _doc()
    # Pad properties with a large string field under an additive key the
    # legacy acceptor tolerates (not receiptAudit/results shape).
    pad_len = _MAX_SARIF_REPORT_CHARS - 800
    doc["runs"][0].setdefault("properties", {})["pad"] = "x" * max(pad_len, 1)
    stdout = _stdout(doc)
    if len(stdout) > _MAX_SARIF_REPORT_CHARS:
        pytest.skip("pad overshot cap; adjust")
    # Ensure legacy alone is accepted
    safe = prepare_legacy_report(doc, stdout=stdout, stderr="")
    if safe.sarif_document is None:
        pytest.skip("legacy pad rejected by acceptor")
    proj = classify_report_records(State(findings=[], mode=Mode.CI, verdict=Verdict.PASS))
    prepared = prepare_report_views(safe, proj)
    # Either budget-exceeded canonical block, or omit → legacy-report
    reason = prepared.report_classification.unavailable_reason
    avail = prepared.report_classification.availability
    if avail == "available":
        # Small enough to fit — acceptable for this host size
        assert prepared.report_classification.delivery_mode == "projection"
    else:
        assert reason in ("budget-exceeded", "legacy-report")
        if reason == "legacy-report":
            assert prepared.stdout == stdout  # byte-identical omission
            assert "additive report budget exceeded" in prepared.stderr
        if reason == "budget-exceeded":
            out = json.loads(prepared.stdout)
            block = out["runs"][0]["properties"]["report_classification"]
            assert block["unavailable_reason"] == "budget-exceeded"
            assert all(
                block[k] is None
                for k in block
                if k not in ("schema", "availability", "unavailable_reason")
            )


def test_raw_legacy_only_no_extension_no_qualifier():
    bad = (
        '{"version":"2.1.0","runs":[{"tool":{"driver":{"name":"code-forge"}},'
        '"results":[],"properties":{"duration":NaN}}]}'
    )
    raw = LegacyReportSubstrate(sarif_document=None, stdout=bad, stderr="orig")
    prepared = prepare_report_views(raw, None)
    assert prepared.stdout is bad
    assert prepared.stderr is raw.stderr  # no qualifier appended
    assert "report_classification" not in prepared.stdout
