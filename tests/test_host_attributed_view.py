# SPDX-License-Identifier: Apache-2.0
"""The explicit host-attributed product view for #70.

The default v1 report stays on reporting_product_findings. This view is
opt-in and only drops a record when the current round both captured it and
can still point at exactly one persisted finding. A matching shape is not
enough, and a matching id or fingerprint is not enough once two records
share them.
"""

import hashlib

from code_forge.disposition import Disposition
from code_forge.host_attributed_view import host_attributed_report
from code_forge.state import Mode, State, StateFinding, Verdict


def _interrupt(pass_name, *, kind, is_timeout=False):
    failure = {"kind": kind} if kind else None
    return StateFinding(
        id="l1-%s-invoke-fail" % pass_name,
        fingerprint="invoke-fail-%s" % pass_name,
        source="INFRA",
        disposition=Disposition.CONFIRMED,
        file="<llm-invoke>",
        line_range=[0, 0],
        description="L1 invoke failed: constructed",
        is_timeout=is_timeout,
        provider_failure=failure,
    )


def _receipt(text):
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return StateFinding(
        id="RECEIPT_INVALID",
        fingerprint="receipt-%s" % digest,
        source="INFRA",
        disposition=Disposition.CONFIRMED,
        file="<receipt-evidence>",
        line_range=[0, 0],
        description=text,
    )


def _product(fingerprint="product-1"):
    return StateFinding(
        id="P1",
        fingerprint=fingerprint,
        source="L1",
        disposition=Disposition.CONFIRMED,
        file="control.ts",
        line_range=[2, 2],
        description="constructed product defect",
    )


def _state(findings, *, mode=Mode.CI):
    return State(findings=findings, mode=mode, verdict=Verdict.FAIL, round=3)


def _captured(finding, *, kind):
    """What the host records at the moment it catches this round's exception."""
    return {
        "id": finding.id,
        "fingerprint": finding.fingerprint,
        "kind": kind,
        "description": finding.description,
        "is_timeout": finding.is_timeout,
    }


def _booked(finding):
    """A receipt record this round actually appended, not one it skipped."""
    return {"fingerprint": finding.fingerprint, "description": finding.description}


def test_pure_no_json_reports_no_product_and_keeps_both_records():
    interrupt = _interrupt("adversarial", kind="no_json")
    receipt = _receipt("receipt acceptance: adversarial no_json")
    state = _state([interrupt, receipt])
    report = host_attributed_report(
        state,
        captured=[_captured(interrupt, kind="no_json")],
        booked=[_booked(receipt)],
    )
    assert report["product_count"] == 0
    assert report["interrupts"] == [interrupt.fingerprint]
    assert report["receipt_blocks"] == [receipt.fingerprint]
    assert [f.id for f in state.findings] == ["l1-adversarial-invoke-fail", "RECEIPT_INVALID"]
    assert report["summary"].startswith("code-forge: FAIL findings=0 ")
    assert report["results"] == []


def test_plain_timeout_reports_no_product():
    interrupt = _interrupt("adversarial", kind="", is_timeout=True)
    receipt = _receipt("receipt acceptance: adversarial timeout")
    report = host_attributed_report(
        _state([interrupt, receipt]),
        captured=[_captured(interrupt, kind="")],
        booked=[_booked(receipt)],
    )
    assert report["product_count"] == 0
    assert report["summary"].startswith("code-forge: FAIL findings=0 ")
    assert report["results"] == []


def test_shape_alone_does_not_drop_a_receipt():
    receipt = _receipt("receipt acceptance: looks real")
    report = host_attributed_report(_state([receipt]), captured=[], booked=[])
    assert report["product_count"] == 1
    assert [r["fingerprint"] for r in report["results"]] == [receipt.fingerprint]


def test_prior_round_same_identity_is_kept_when_this_round_captured_nothing():
    stale = _interrupt("adversarial", kind="no_json")
    report = host_attributed_report(_state([stale]), captured=[], booked=[])
    assert report["product_count"] == 1
    assert report["results"][0]["fingerprint"] == stale.fingerprint


def test_reload_cannot_tell_same_identity_records_apart_so_neither_drops():
    # Both rounds persisted the same id and fingerprint. After a reload the
    # report has no field naming which record this round captured, so it must
    # not guess by position and must not drop both.
    older = _interrupt("adversarial", kind="no_json")
    newer = _interrupt("adversarial", kind="no_json")
    report = host_attributed_report(
        _state([older, newer]),
        captured=[_captured(newer, kind="no_json")],
        booked=[],
    )
    assert report["product_count"] == 2
    assert [r["fingerprint"] for r in report["results"]] == [older.fingerprint, newer.fingerprint]


def test_identical_prior_receipt_text_is_kept_when_this_round_booked_nothing():
    # The gate saw the same error text this round but refused to append,
    # because a record with that fingerprint was already there. The booking
    # therefore names the prior record's description, and the view must not
    # treat that as a record this round added.
    stale = _receipt("receipt acceptance: adversarial no_json")
    report = host_attributed_report(
        _state([stale]),
        captured=[],
        booked=[{"fingerprint": stale.fingerprint, "description": "not appended this round"}],
    )
    assert report["product_count"] == 1
    assert report["results"][0]["fingerprint"] == stale.fingerprint


def test_verify_failure_with_an_independent_evidence_problem_keeps_the_unattributable():
    owned = _receipt("receipt acceptance: adversarial no_json")
    independent = _receipt("coverage 40% < 60% cycle 3")
    report = host_attributed_report(
        _state([owned, independent]),
        captured=[],
        booked=[_booked(owned)],
    )
    assert report["product_count"] == 1
    assert [r["fingerprint"] for r in report["results"]] == [independent.fingerprint]


def test_unattributable_verify_failure_keeps_its_classification():
    # The gate booked one record whose text names neither the interruption nor
    # a separable evidence problem. Nothing says which part belongs to the
    # interruption, so the record stays a product finding.
    mixed = _receipt("receipt acceptance: adversarial no_json; coverage 40% < 60%")
    report = host_attributed_report(
        _state([mixed]),
        captured=[],
        booked=[_booked(mixed)],
        attributable=False,
    )
    assert report["product_count"] == 1
    assert report["results"][0]["fingerprint"] == mixed.fingerprint


def test_real_defect_beside_an_interruption_counts_as_one():
    interrupt = _interrupt("adversarial", kind="no_json")
    receipt = _receipt("receipt acceptance: adversarial no_json")
    product = _product()
    for mode, verdict in ((Mode.CI, "FAIL"), (Mode.LOCAL, "ESCALATED")):
        state = _state([interrupt, receipt, product], mode=mode)
        state.verdict = Verdict.FAIL if mode is Mode.CI else Verdict.ESCALATED
        report = host_attributed_report(
            state,
            captured=[_captured(interrupt, kind="no_json")],
            booked=[_booked(receipt)],
        )
        assert report["product_count"] == 1
        assert [r["fingerprint"] for r in report["results"]] == [product.fingerprint]
        assert report["summary"].startswith("code-forge: %s findings=1 " % verdict)


def test_a_live_round_feeds_the_view_from_its_own_captures():
    # The capture is taken where the exception is handled, then read back
    # from the machine after the round. Nothing here rebuilds it by hand.
    import shutil
    import tempfile
    from pathlib import Path

    import pytest

    from tests import _review_harness as harness

    monkeypatch = pytest.MonkeyPatch()
    workspace = Path(tempfile.mkdtemp())
    try:
        monkeypatch.chdir(workspace)
        (workspace / ".code-forge").mkdir()
        monkeypatch.setattr("code_forge.machine.resolve_ledger_root", lambda cwd: workspace)
        monkeypatch.setattr("code_forge.ledger.resolve_ledger_root", lambda cwd: workspace)
        for name, content in harness.CONTENT.items():
            (workspace / name).write_text(content)
        (workspace / ".code-forge" / "gate.yaml").write_text("verify:\n  required_cycles: 3\n")
        machine, _calls = harness._new_machine(workspace, monkeypatch, fault="no-json", rounds=1)
        verdict = machine.run()
        attribution = machine.host_attribution
        assert attribution is not None
        assert verdict.value == "FAIL"
        captured = attribution["captured"]
        assert len(captured) == 1
        assert captured[0]["kind"] == "no_json"
        assert captured[0]["fingerprint"] == "invoke-fail-adversarial"
    finally:
        monkeypatch.undo()
        shutil.rmtree(workspace, ignore_errors=True)


def test_a_shared_fingerprint_does_not_drop_the_other_record():
    # The interruption and a real defect can share a fingerprint. The view
    # names the interruption by index, so the defect stays in the product count.
    interrupt = _interrupt("adversarial", kind="no_json")
    twin = _product(fingerprint=interrupt.fingerprint)
    report = host_attributed_report(
        _state([interrupt, twin]),
        captured=[_captured(interrupt, kind="no_json")],
        booked=[],
    )
    assert report["excluded_indices"] == []
    assert report["product_count"] == 2
    assert interrupt.fingerprint in report["kept_fingerprints"]


def test_default_report_is_unchanged():
    # The shared projection still counts both records. The new view is opt-in.
    from code_forge.state import reporting_product_findings

    interrupt = _interrupt("adversarial", kind="no_json")
    receipt = _receipt("receipt acceptance: adversarial no_json")
    assert len(reporting_product_findings([interrupt, receipt])) == 2


def test_summary_and_results_come_from_the_same_view():
    interrupt = _interrupt("adversarial", kind="no_json")
    receipt = _receipt("receipt acceptance: adversarial no_json")
    product = _product()
    report = host_attributed_report(
        _state([interrupt, receipt, product]),
        captured=[_captured(interrupt, kind="no_json")],
        booked=[_booked(receipt)],
    )
    assert "findings=%d " % report["product_count"] in report["summary"]
    assert len(report["results"]) == report["product_count"]
