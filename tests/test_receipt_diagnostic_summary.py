"""Human clarification must not reclassify evidence or change machine output."""

import copy
import json

import pytest

from code_forge.disposition import Disposition
from code_forge.report_presentation import prepare_legacy_report, prepare_report_views
from code_forge.report_projection import classify_report_records
from code_forge.sarif import build_sarif_log, format_classification_line, format_summary
from code_forge.state import State, StateFinding, Verdict


def _receipt(index=0, **overrides):
    fields = dict(
        id="RECEIPT_INVALID",
        fingerprint=f"receipt-{index:012x}",
        source="INFRA",
        disposition=Disposition.CONFIRMED,
        file="<receipt-evidence>",
        line_range=[0, 0],
        description="private diagnostic details",
        receipt_subtype="RECEIPT_EVIDENCE",
    )
    fields.update(overrides)
    return StateFinding(**fields)


def _render(findings):
    state = State(verdict=Verdict.FAIL, findings=findings)
    state_before = copy.deepcopy(state)
    sarif = build_sarif_log(state, {}, "test")
    substrate = prepare_legacy_report(sarif, stdout=json.dumps(sarif), stderr=format_summary(state))
    prepared = prepare_report_views(substrate, classify_report_records(state))
    prepared_before = copy.deepcopy(prepared)
    line = format_classification_line(prepared.report_classification)
    assert prepared == prepared_before
    assert state == state_before
    assert build_sarif_log(state, {}, "test") == sarif
    return state, prepared, line


def _legacy_classification(count):
    return (
        f"code-forge: classification product={count} "
        f"[not adjudicated: confirmed={count} dismissed=0 fixed=0 style=0; "
        "with-observations=0] unresolved=0 [uncertain; with-observations=0] "
        "quarantine=0 [unverified]"
    )


def test_five_receipt_errors_are_explained_without_reducing_counts():
    findings = [_receipt(0, receipt_subtype="RECEIPT_INVALID_GATE")]
    findings.extend(_receipt(i) for i in range(1, 5))
    state, prepared, line = _render(findings)
    assert format_summary(state) == (
        "code-forge: FAIL findings=5 confirmed=5 uncertain=0 dismissed=0 fixed=0 infra=5"
    )
    assert prepared.report_classification.product_list_count == 5
    assert line == _legacy_classification(5) + (
        " host-receipt-diagnostics=5 [included in product/confirmed counts; "
        "not source-code findings; still block review]"
    )
    assert "private diagnostic details" not in line


def test_mixed_product_and_evidence_counts_remain_unchanged():
    product = _receipt(1, id="real-bug", fingerprint="real-bug", source="L0", file="main.py")
    state, prepared, line = _render([product, _receipt()])
    assert "findings=2 confirmed=2" in format_summary(state)
    assert prepared.report_classification.product_list_count == 2
    assert line.startswith(_legacy_classification(2))
    assert "host-receipt-diagnostics=1 " in line


@pytest.mark.parametrize(
    "subtype", ["RECEIPT_SCHEMA", "RECEIPT_COVERAGE", "RECEIPT_EVIDENCE", "RECEIPT_INVALID_GATE"]
)
def test_known_host_receipt_labels_are_explained(subtype):
    _, _, line = _render([_receipt(receipt_subtype=subtype)])
    assert "host-receipt-diagnostics=1 " in line


@pytest.mark.parametrize(
    "overrides",
    [
        {"source": "UNTRUSTED"},
        {"source": "L1"},
        {"id": "unknown-infra", "fingerprint": "unknown"},
    ],
)
def test_untrusted_or_unknown_records_keep_original_human_bytes(overrides):
    _, _, line = _render([_receipt(**overrides)])
    assert line == _legacy_classification(1)


def test_unavailable_classification_keeps_original_bytes():
    class Unavailable:
        availability = "unavailable"
        unavailable_reason = "no-report"

    assert format_classification_line(Unavailable()) == (
        "code-forge: classification unavailable (no-report)"
    )


def test_falsifier_observation_is_not_relabelled_as_receipt_failure():
    _, prepared, line = _render(
        [_receipt(disposition=Disposition.UNCERTAIN, error="falsify() backend timed out: no response")]
    )
    assert len(prepared.report_classification.unresolved_product_candidates) == 1
    assert "host-receipt-diagnostics" not in line


def test_cli_adds_only_human_clarification(monkeypatch, capsys, tmp_path):
    from code_forge import __version__, cli

    state = State(verdict=Verdict.FAIL, findings=[_receipt()])
    original = copy.deepcopy(state)
    log = build_sarif_log(state, {}, __version__)
    prepared = prepare_report_views(
        prepare_legacy_report(
            log,
            stdout=json.dumps(log, ensure_ascii=False, allow_nan=False),
            stderr=format_summary(state),
        ),
        classify_report_records(state),
    )
    monkeypatch.setattr(cli, "_load_state", lambda path: state)
    monkeypatch.setattr(cli, "_load_advisories", lambda path: [])
    cli._emit_ci_output(tmp_path / "state.json", {})
    output = capsys.readouterr()
    assert output.out == prepared.stdout + "\n"
    assert output.err.splitlines()[0] == format_summary(state)
    assert "host-receipt-diagnostics=1 " in output.err
    assert state == original
