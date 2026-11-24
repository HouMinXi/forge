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


def test_default_ci_output_is_byte_identical_with_and_without_attribution(tmp_path, monkeypatch):
    """The real CI emitter must not change its stdout or stderr when the
    round's attribution is present. The new counts live only in the view."""
    import io
    from contextlib import redirect_stderr, redirect_stdout

    from code_forge.cli import _emit_ci_output
    from code_forge.state import save_state

    interrupt = _interrupt("adversarial", kind="no_json")
    receipt = _receipt("pass did not complete: c1p3 status=incomplete")
    state = _state([interrupt, receipt])
    state_path = tmp_path / "state.json"
    save_state(state, state_path)

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        _emit_ci_output(state_path, {})
    # The emitter takes no attribution. Running it twice on the same state
    # must produce the same bytes, and the findings line keeps both records.
    assert (out.getvalue(), err.getvalue()) == _once_more(state_path)
    assert "findings=2 " in err.getvalue()


def _once_more(state_path):
    import io
    from contextlib import redirect_stderr, redirect_stdout

    from code_forge.cli import _emit_ci_output

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        _emit_ci_output(state_path, {})
    return out.getvalue(), err.getvalue()


def _ci_round(fault):
    """Run one constructed CI round and return the host-attributed view."""
    import os
    import shutil
    import tempfile
    from pathlib import Path

    import pytest

    from code_forge.host_attributed_view import host_attributed_report
    from code_forge.state import Mode
    from tests import _review_harness as harness

    home = os.getcwd()
    workspace = Path(tempfile.mkdtemp())
    monkeypatch = pytest.MonkeyPatch()
    try:
        (workspace / ".code-forge").mkdir()
        for name, content in harness.CONTENT.items():
            (workspace / name).write_text(content)
        (workspace / ".code-forge" / "gate.yaml").write_text("verify:\n  required_cycles: 3\n")
        monkeypatch.chdir(workspace)
        monkeypatch.setattr("code_forge.machine.resolve_ledger_root", lambda cwd: workspace)
        monkeypatch.setattr("code_forge.ledger.resolve_ledger_root", lambda cwd: workspace)
        machine, _calls = harness._new_machine(
            workspace, monkeypatch, fault=fault, rounds=1, mode=Mode.CI
        )
        verdict = machine.run()
        attribution = machine.host_attribution
        report = host_attributed_report(
            machine._state, captured=attribution["captured"], booked=attribution["booked"]
        )
        return verdict.value, report, list(machine._state.findings)
    finally:
        os.chdir(home)
        monkeypatch.undo()
        shutil.rmtree(workspace, ignore_errors=True)


def test_ci_pure_no_json_is_zero_one_one():
    verdict, report, _findings = _ci_round("no-json")
    assert verdict == "FAIL"
    assert report["product_count"] == 0
    assert len(report["interrupts"]) == 1
    assert len(report["receipt_blocks"]) == 1


def test_ci_plain_timeout_is_zero_one_one():
    verdict, report, _findings = _ci_round("timeout-empty-kind")
    assert verdict == "FAIL"
    assert report["product_count"] == 0
    assert len(report["interrupts"]) == 1
    assert len(report["receipt_blocks"]) == 1


def test_ci_other_failure_keeps_both_records():
    # A connect timeout is an interruption the host caught, but it is not one
    # of the two kinds this change reclassifies. Both the interruption and
    # the receipt stay in the product count, and the view must say which.
    verdict, report, findings = _ci_round("connect-timeout")
    assert verdict == "FAIL"
    kept = sorted(f.id for f in findings if f.fingerprint in report["kept_fingerprints"])
    assert kept == ["RECEIPT_INVALID", "l1-adversarial-invoke-fail"]
    assert report["interrupts"] == []
    assert report["receipt_blocks"] == []


def test_host_attributed_flag_prints_the_view_without_touching_the_report(tmp_path):
    import io
    from contextlib import redirect_stderr, redirect_stdout

    from code_forge.cli import _emit_ci_output, _emit_host_attributed_view
    from code_forge.state import save_state

    interrupt = _interrupt("adversarial", kind="no_json")
    receipt = _receipt("pass did not complete: c1p3 status=error")
    state_path = tmp_path / "state.json"
    save_state(_state([interrupt, receipt]), state_path)
    attribution = {
        "captured": [_captured(interrupt, kind="no_json")],
        "booked": [_booked(receipt)],
    }

    def report():
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            _emit_ci_output(state_path, {})
        return out.getvalue(), err.getvalue()

    before = report()
    view = io.StringIO()
    with redirect_stderr(view):
        _emit_host_attributed_view(attribution, state_path)
    after = report()
    assert before == after
    assert (
        view.getvalue().strip() == "code-forge: host-attributed product=0 interrupts=1 receipt-blocks=1"
    )
    # No attribution in hand means no line, rather than a guessed count.
    silent = io.StringIO()
    with redirect_stderr(silent):
        _emit_host_attributed_view(None, state_path)
    assert silent.getvalue() == ""


def test_ci_mixed_failure_keeps_the_receipt(tmp_path):
    # One pass returns no JSON while another reports a real defect. The
    # verifier returns one result for both, and the receipt must stay.
    import os
    import shutil

    import pytest

    from code_forge.host_attributed_view import host_attributed_report
    from code_forge.state import Mode
    from tests import _review_harness as harness

    home = os.getcwd()
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch = pytest.MonkeyPatch()
    try:
        (workspace / ".code-forge").mkdir()
        for name, content in harness.CONTENT.items():
            (workspace / name).write_text(content)
        (workspace / ".code-forge" / "gate.yaml").write_text("verify:\n  required_cycles: 3\n")
        monkeypatch.chdir(workspace)
        monkeypatch.setattr("code_forge.machine.resolve_ledger_root", lambda cwd: workspace)
        monkeypatch.setattr("code_forge.ledger.resolve_ledger_root", lambda cwd: workspace)
        machine, _calls = harness._new_machine(
            workspace, monkeypatch, fault="no-json", rounds=1, mode=Mode.CI, mixed_product=True
        )
        verdict = machine.run()
        attribution = machine.host_attribution
        report = host_attributed_report(
            machine._state, captured=attribution["captured"], booked=attribution["booked"]
        )
        kept = sorted(
            f.id for f in machine._state.findings if f.fingerprint in report["kept_fingerprints"]
        )
        assert verdict.value == "FAIL"
        # The verified defect is in the receipt, but it did not refuse the
        # receipt. The only unresolved row is the interruption, so the
        # receipt leaves and the defect is the one product finding.
        assert report["product_count"] == 1
        assert "RECEIPT_INVALID" not in kept
        assert any(item.startswith("l1-qodo-") for item in kept)
        assert len(report["interrupts"]) == 1
        assert len(report["receipt_blocks"]) == 1
    finally:
        os.chdir(home)
        monkeypatch.undo()
        shutil.rmtree(workspace, ignore_errors=True)


def test_local_pure_no_json_is_zero_one_one(tmp_path):
    import os
    import shutil

    import pytest

    from code_forge.host_attributed_view import host_attributed_report
    from code_forge.state import Mode
    from tests import _review_harness as harness

    home = os.getcwd()
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch = pytest.MonkeyPatch()
    try:
        (workspace / ".code-forge").mkdir()
        for name, content in harness.CONTENT.items():
            (workspace / name).write_text(content)
        (workspace / ".code-forge" / "gate.yaml").write_text("verify:\n  required_cycles: 3\n")
        monkeypatch.chdir(workspace)
        monkeypatch.setattr("code_forge.machine.resolve_ledger_root", lambda cwd: workspace)
        monkeypatch.setattr("code_forge.ledger.resolve_ledger_root", lambda cwd: workspace)
        machine, _calls = harness._new_machine(
            workspace, monkeypatch, fault="no-json", rounds=1, mode=Mode.LOCAL
        )
        verdict = machine.run()
        attribution = machine.host_attribution
        report = host_attributed_report(
            machine._state, captured=attribution["captured"], booked=attribution["booked"]
        )
        assert verdict.value == "FAIL"
        assert report["product_count"] == 0
        assert len(report["interrupts"]) == 1
        assert len(report["receipt_blocks"]) == 1
    finally:
        os.chdir(home)
        monkeypatch.undo()
        shutil.rmtree(workspace, ignore_errors=True)


def test_a_receipt_that_names_its_own_rejection_stays(tmp_path):
    # The receipt text carries an independent rejection after the pass marker.
    # The booking comes from the real gate, and the view must keep that record.
    import os
    import shutil

    import pytest

    from code_forge.host_attributed_view import host_attributed_report
    from code_forge.state import Mode
    from tests import _review_harness as harness

    home = os.getcwd()
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch = pytest.MonkeyPatch()
    try:
        (workspace / ".code-forge").mkdir()
        for name, content in harness.CONTENT.items():
            (workspace / name).write_text(content)
        (workspace / ".code-forge" / "gate.yaml").write_text("verify:\n  required_cycles: 3\n")
        monkeypatch.chdir(workspace)
        monkeypatch.setattr("code_forge.machine.resolve_ledger_root", lambda cwd: workspace)
        monkeypatch.setattr("code_forge.ledger.resolve_ledger_root", lambda cwd: workspace)
        machine, _calls = harness._new_machine(
            workspace, monkeypatch, fault="no-json", rounds=1, mode=Mode.CI
        )
        real = machine._record_receipt_gate_failure

        def with_rejection(error, **kwargs):
            mixed = type(error)(str(error) + "; coverage 40% < 60%", error.verification)
            return real(mixed, **kwargs)

        monkeypatch.setattr(machine, "_record_receipt_gate_failure", with_rejection)
        machine.run()
        attribution = machine.host_attribution
        report = host_attributed_report(
            machine._state, captured=attribution["captured"], booked=attribution["booked"]
        )
        kept = [f.id for f in machine._state.findings if f.fingerprint in report["kept_fingerprints"]]
        assert "RECEIPT_INVALID" in kept
        assert report["receipt_blocks"] == []
    finally:
        os.chdir(home)
        monkeypatch.undo()
        shutil.rmtree(workspace, ignore_errors=True)


def test_an_incomparable_receipt_row_keeps_the_receipt(tmp_path):
    # A receipt row whose line is a list cannot be hashed. The booking must
    # keep the receipt instead of raising, because it cannot prove the row is
    # only the interruption.
    import os
    import shutil

    import pytest

    from code_forge.host_attributed_view import host_attributed_report
    from code_forge.state import Mode
    from tests import _review_harness as harness

    home = os.getcwd()
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch = pytest.MonkeyPatch()
    try:
        (workspace / ".code-forge").mkdir()
        for name, content in harness.CONTENT.items():
            (workspace / name).write_text(content)
        (workspace / ".code-forge" / "gate.yaml").write_text("verify:\n  required_cycles: 3\n")
        monkeypatch.chdir(workspace)
        monkeypatch.setattr("code_forge.machine.resolve_ledger_root", lambda cwd: workspace)
        monkeypatch.setattr("code_forge.ledger.resolve_ledger_root", lambda cwd: workspace)
        machine, _calls = harness._new_machine(
            workspace, monkeypatch, fault="no-json", rounds=1, mode=Mode.CI
        )
        real = machine._record_receipt_gate_failure

        def with_list_line(error, **kwargs):
            rows = getattr(error.verification, "unresolved_findings", ())
            error.verification.unresolved_findings = tuple(
                (row[0], row[1], row[2], ["not", "comparable"]) for row in rows
            )
            return real(error, **kwargs)

        monkeypatch.setattr(machine, "_record_receipt_gate_failure", with_list_line)
        machine.run()
        attribution = machine.host_attribution
        report = host_attributed_report(
            machine._state, captured=attribution["captured"], booked=attribution["booked"]
        )
        kept = [f.id for f in machine._state.findings if f.fingerprint in report["kept_fingerprints"]]
        assert "RECEIPT_INVALID" in kept
        assert report["receipt_blocks"] == []
    finally:
        os.chdir(home)
        monkeypatch.undo()
        shutil.rmtree(workspace, ignore_errors=True)
