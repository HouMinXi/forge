# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""W2-6 metadata is host-derived, digest-bound, additive and routing-neutral."""

import hashlib
import json
from pathlib import Path

import pytest

from code_forge.backend import BackendConfig
from code_forge.baseline import ResolvedReview
from code_forge.factories import build_l1_provider
from code_forge.llm_invoke import LLMResult, Usage
from code_forge.receipt import write_receipts
from code_forge.receipt_provenance import receipt_finding_provenance
from code_forge.report_projection import classify_report_records
from code_forge.state import Disposition, State, StateFinding, load_state, save_state
from tests.test_verify_uncertainty_basis import DIFF, _receipts, _verify

LEGACY_LABEL = "unresolved finding without verified evidence (a product candidate the host could not verify, or an infrastructure failure)"


def _finding(kind="schema-fail", source="INFRA", error=None):
    files = {
        "schema-fail": "<schema-validation>",
        "incomplete-coverage": "<coverage-guard>",
        "invoke-fail": "<llm-invoke>",
    }
    return StateFinding(
        f"l1-qodo-{kind}",
        f"{kind}-qodo",
        source,
        Disposition.CONFIRMED,
        files[kind],
        [0, 0],
        "reviewer text cannot establish provenance",
        error=error,
    )


@pytest.mark.parametrize(
    "kind,channel,subtype",
    [
        ("schema-fail", "gate", "RECEIPT_SCHEMA"),
        ("incomplete-coverage", "gate", "RECEIPT_COVERAGE"),
        ("invoke-fail", "execution", None),
    ],
)
def test_host_receipt_provenance_and_digest(tmp_path, kind, channel, subtype):
    f = _finding(kind)
    paths = write_receipts(tmp_path / "receipts", 0, [f], "hash", [], tmp_path)
    raw = paths[0].read_bytes()
    receipt = json.loads(raw)
    row = receipt["findings"][0]
    assert (row["source"], row["channel"]) == ("INFRA", channel)
    assert row.get("receipt_subtype") == subtype
    assert receipt["pass_status"] != "completed"
    row["channel"] = "product"
    assert hashlib.sha256(raw).digest() != hashlib.sha256(json.dumps(receipt).encode()).digest()


@pytest.mark.parametrize(
    "source,error", [("UNTRUSTED", None), ("L1", None), ("INFRA", "falsify() raised: failed")]
)
def test_product_cannot_spoof_host_diagnostic_geometry(source, error):
    f = _finding(source=source, error=error)
    assert receipt_finding_provenance(f) == {"source": source, "channel": "product"}
    f.receipt_subtype = "RECEIPT_SCHEMA"
    projection = classify_report_records(State(findings=[f]))
    records = projection.product + projection.unresolved_product_candidates
    assert len(records) == 1 and records[0].host_failure_kind is None


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"source": "INFRA"},
        {"channel": "gate"},
        {"source": "UNTRUSTED", "channel": "gate"},
        {"source": "INFRA", "channel": "future"},
        {"source": [], "channel": {}},
        {"source": "future", "channel": "execution"},
    ],
)
def test_unknown_or_legacy_provenance_keeps_old_fail_closed_behavior(tmp_path, metadata):
    paths = _receipts(tmp_path)
    receipt = json.loads(paths[0].read_text())
    finding = receipt["findings"][0]
    finding.pop("source")
    finding.pop("channel")
    finding.update(metadata)
    paths[0].write_text(json.dumps(receipt))
    result = _verify(tmp_path)
    assert not result.passed and result.failure_kind is None
    assert result.reason == f"{LEGACY_LABEL} c1p1 -- convergence not established"
    assert len(result.unresolved_findings) == 1
    assert _verify(tmp_path, require_convergence=False).passed


@pytest.mark.parametrize(
    "kind,subtype", [("schema-fail", "RECEIPT_SCHEMA"), ("incomplete-coverage", "RECEIPT_COVERAGE")]
)
def test_report_subtype_roundtrip_does_not_move_product_channel(tmp_path, kind, subtype):
    f = _finding(kind)
    f.receipt_subtype = subtype
    state = State(findings=[f])
    save_state(state, tmp_path / "state.json")
    loaded = load_state(tmp_path / "state.json")
    projection = classify_report_records(loaded)
    assert projection.product[0].host_failure_kind == subtype
    assert projection.product[0].channel == "product"  # no accounting/grouping policy change
    f.receipt_subtype = "RECEIPT_FUTURE"
    assert classify_report_records(state).product[0].host_failure_kind is None


def test_unavailable_cause_recorded_at_real_producer_and_cleared(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = {"findings": "not an array", "constructed_unencodable": ("tuple",)}
    monkeypatch.setattr(
        "code_forge.llm_invoke._invoke_api", lambda *a, **k: LLMResult(payload, Usage(), 0.0)
    )
    provider = build_l1_provider(
        "real",
        ResolvedReview([Path("mod.py")], None, DIFF, "git"),
        backend=BackendConfig(name="offline", type="api", model="offline"),
        max_attempts=1,
        pass_stagger_s=0,
    )
    findings, _, _, _ = provider()
    assert provider.unavailable_rejected_causes == {
        name: "schema" for name in ("qodo", "expert", "adversarial")
    }
    assert all(f.receipt_subtype == "RECEIPT_SCHEMA" for f in findings)
    paths = write_receipts(
        tmp_path / "receipts",
        0,
        findings,
        "hash",
        [],
        tmp_path,
        unavailable_rejected_passes=provider.unavailable_rejected_passes,
        unavailable_rejected_causes=provider.unavailable_rejected_causes,
    )
    assert all(json.loads(path.read_text())["unavailable_cause"] == "schema" for path in paths)
    payload.clear()
    payload.update(
        findings=[],
        code_excerpts=[{"file": "mod.py", "start_line": 1, "end_line": 1, "content": "value = 1"}],
    )
    provider()
    assert provider.unavailable_rejected_causes == {}


@pytest.mark.parametrize("causes", [[], False, {"other": "schema"}, {"qodo": "future"}, {"qodo": []}])
def test_invalid_unavailable_cause_is_not_written(tmp_path, causes):
    with pytest.raises(ValueError):
        write_receipts(
            tmp_path / "receipts",
            0,
            [],
            "hash",
            [],
            tmp_path,
            unavailable_rejected_passes={"qodo"},
            unavailable_rejected_causes=causes,
        )
    assert not (tmp_path / "receipts").exists()


def test_provenance_fields_are_in_the_captured_earned_digest(tmp_path):
    from code_forge.verify import _capture_earned_cycle, parse_diff_files

    paths = _receipts(tmp_path, disposition=Disposition.FIXED)
    before, result = _capture_earned_cycle(
        tmp_path, "hash", parse_diff_files(DIFF), cycle=1, diff_text=DIFF
    )
    assert result.passed
    receipt = json.loads(paths[0].read_text())
    receipt["findings"][0]["channel"] = "future"
    paths[0].write_text(json.dumps(receipt))
    after, result = _capture_earned_cycle(
        tmp_path, "hash", parse_diff_files(DIFF), cycle=1, diff_text=DIFF
    )
    assert result.passed  # display-only metadata cannot change closed-finding credit
    assert before["receipt_sha256"]["1"] != after["receipt_sha256"]["1"]
    assert before["receipt_sha256"]["2"] == after["receipt_sha256"]["2"]


def test_untyped_attempted_payload_keeps_generic_subtype(tmp_path, monkeypatch):
    from tests.test_phase6_schema_coverage_credit import _guard_machine

    sm = _guard_machine(tmp_path, monkeypatch)
    sm._attempted_last_round = [{"pass_name": "qodo", "findings": "not an array"}]
    sm._excerpts_last_round = []
    sm._last_receipt_write_errors = []
    errors = sm._receipt_gate_round_errors()
    assert len(errors) == 1
    assert errors[0].receipt_subtype == "RECEIPT_INVALID_GATE"
    sm._record_receipt_gate_failure(errors[0])
    assert sm._state.findings[-1].receipt_subtype == "RECEIPT_INVALID_GATE"


@pytest.mark.parametrize("damage", ["schema", "io"])
def test_untyped_corrupt_receipt_keeps_generic_label(tmp_path, monkeypatch, damage):
    import code_forge.verify as verify
    from code_forge.errors import CorruptedReceiptError

    _receipts(tmp_path)
    if damage == "schema":
        (tmp_path / ".code-forge/receipts/receipt-c1p1.json").write_text("{")
    else:

        def unreadable(*args, **kwargs):
            raise CorruptedReceiptError("receipt-c1p1.json: cannot read receipt: denied")

        monkeypatch.setattr(verify, "_load_receipt_records", unreadable)
    result = _verify(tmp_path)
    _, captured = verify._capture_earned_cycle(
        tmp_path, "hash", verify.parse_diff_files(DIFF), cycle=1, diff_text=DIFF
    )
    assert not result.passed and not captured.passed
    assert result.receipt_subtype is None and captured.receipt_subtype is None


@pytest.mark.parametrize(
    "source,error", [("INFRA", None), ("UNTRUSTED", None), ("INFRA", "falsify() raised: unavailable")]
)
def test_report_preserves_source_without_changing_classification(source, error):
    from code_forge.report_presentation import _record_view_from_report_record

    f = _finding("invoke-fail", source=source, error=error)
    projection = classify_report_records(State(findings=[f]))
    record = (projection.product + projection.unresolved_product_candidates)[0]
    assert record.source == source
    assert _record_view_from_report_record(record).fields["source"] == source
    assert record.channel == "product"
    assert record.host_failure_kind is None


@pytest.mark.parametrize(
    "damage,subtype", [("literal", "RECEIPT_EVIDENCE"), ("missing", "RECEIPT_COVERAGE")]
)
def test_real_verifier_subtype_reaches_terminal_state_and_report(tmp_path, monkeypatch, damage, subtype):
    from code_forge.machine import _ReceiptGateFailure
    from tests.test_phase6_schema_coverage_credit import _guard_machine

    paths = _receipts(tmp_path, disposition=Disposition.FIXED)
    for path in paths:
        receipt = json.loads(path.read_text())
        if damage == "literal":
            for excerpt in receipt["code_excerpts"]:
                excerpt["content"] = "value = 200"
        else:
            receipt["code_excerpts"] = []
        path.write_text(json.dumps(receipt))
    result = _verify(tmp_path, hardened=True)
    assert not result.passed and result.receipt_subtype == subtype
    assert (result.checks_run, result.checks_passed) == (5, 4)
    machine = _guard_machine(tmp_path, monkeypatch)
    machine._state.findings = []
    machine._record_receipt_gate_failure(
        _ReceiptGateFailure(f"receipt acceptance: {result.reason}", result)
    )
    save_state(machine._state, tmp_path / "state.json")
    loaded = load_state(tmp_path / "state.json")
    assert loaded.findings[0].receipt_subtype == subtype
    assert loaded.findings[0].source == "INFRA"
    assert loaded.findings[0].disposition is Disposition.CONFIRMED
    assert loaded.consecutive_clean_rounds == 0 and not loaded.converged
    projection = classify_report_records(loaded)
    assert len(projection.product) == 1 and projection.product[0].host_failure_kind == subtype
    assert projection.product[0].channel == "product"  # additive, no regrouping
    assert projection.gate == () and projection.unresolved_product_candidates == ()


@pytest.mark.parametrize(
    "kind", ["ordinary-schema", "ordinary-evidence", "grouped-evidence", "grouped-mixed"]
)
def test_real_provider_unavailable_cause_reaches_round_and_receipts(tmp_path, monkeypatch, kind):
    from code_forge.factories import build_grouped_l1_provider
    from tests._outside_gate import outside_gate_root
    from tests.test_phase6_schema_coverage_credit import _guard_machine

    monkeypatch.chdir(tmp_path)
    grouped, mixed = kind.startswith("grouped"), kind.endswith("mixed")
    schema = kind == "ordinary-schema"
    names = ["mod.py", "peer.py"] if grouped else ["mod.py"]
    specs = []
    for name in names:
        (tmp_path / name).write_text("value = 1\n")
        specs.append(
            {
                "name": name,
                "resolved": ResolvedReview([Path(name)], None, DIFF.replace("mod.py", name), "git"),
            }
        )
    resolved = ResolvedReview(
        [Path(n) for n in names], None, "".join(s["resolved"].git_diff for s in specs), "git"
    )
    bad = [True]
    calls = []

    def transport(prompt, *args, **kwargs):
        name = "peer.py" if "diff --git a/peer.py" in prompt else "mod.py"
        calls.append((name, bad[0]))
        body = {
            "findings": [],
            "code_excerpts": [{"file": name, "start_line": 1, "end_line": 1, "content": "value = 1"}],
        }
        if bad[0]:
            body["constructed_unencodable"] = ("tuple",)
            if schema or (mixed and name == "mod.py"):
                body["findings"] = "not an array"
            else:
                body["code_excerpts"][0]["end_line"] = 3  # typed evidence rejection
        return LLMResult(body)

    monkeypatch.setattr("code_forge.llm_invoke._invoke_api", transport)
    options = dict(
        backend=BackendConfig(name="offline", type="api", model="offline"),
        max_attempts=1,
        pass_stagger_s=0,
    )
    provider = (
        build_grouped_l1_provider("real", specs, **options)
        if grouped
        else build_l1_provider("real", resolved, **options)
    )
    machine = _guard_machine(tmp_path, monkeypatch)
    machine.resolved_review, machine.l1_provider = resolved, provider
    machine.source_hash = "hash"
    machine._state.env_manifest = {}
    machine.test_config_root = outside_gate_root(tmp_path)
    machine.l2_runner = lambda *a, **k: ([], [])
    machine.e2e_runner = lambda *a: ([], [])
    monkeypatch.setattr("code_forge.machine.resolve_ledger_root", lambda root: root)
    monkeypatch.setattr("code_forge.ledger.resolve_ledger_root", lambda root: root)

    # Explicit offline command boundary; no Git/backend subprocess is permitted.
    def forbidden(*args, **kwargs):
        pytest.fail("offline provenance test attempted subprocess")

    monkeypatch.setattr("subprocess.Popen", forbidden)
    machine._execute_round(round_index=0)
    cause = "schema" if schema else "mixed" if mixed else "evidence"
    expected = {role: cause for role in ("qodo", "expert", "adversarial")}
    assert provider.unavailable_rejected_causes == expected
    assert machine._unavailable_rejected_causes_last_round == expected
    assert machine._unavailable_rejected_causes_last_round is not provider.unavailable_rejected_causes
    assert machine._unavailable_rejected_passes_last_round == set(expected)
    errors = machine._receipt_gate_round_errors()
    assert len(errors) == 1
    assert errors[0].receipt_subtype == ("RECEIPT_SCHEMA" if schema else "RECEIPT_INVALID_GATE")
    assert machine._unavailable_audit_is_schema_fail() is schema
    for path in (tmp_path / ".code-forge/receipts").glob("receipt-c1p*.json"):
        receipt = json.loads(path.read_text())
        assert receipt["unavailable_cause"] == cause
        assert receipt["pass_status"] != "completed"
    assert len(list((tmp_path / ".code-forge/receipts").glob("receipt-c1p*.json"))) == 3
    from code_forge.verify import _capture_earned_cycle, parse_diff_files

    proof, verification = _capture_earned_cycle(
        tmp_path, "hash", parse_diff_files(resolved.git_diff), cycle=1, diff_text=resolved.git_diff
    )
    assert proof is None and not verification.passed
    bad[0] = False
    machine._execute_round(round_index=1)
    assert provider.unavailable_rejected_causes == {}
    assert provider.unavailable_rejected_passes == set()
    assert machine._unavailable_rejected_causes_last_round == {}
    assert machine._unavailable_rejected_passes_last_round == set()
    assert machine._receipt_gate_round_errors() == []
    for number in (1, 2, 3):
        path = tmp_path / ".code-forge/receipts" / f"receipt-c2p{number}.json"
        receipt = json.loads(path.read_text())
        assert "unavailable_cause" not in receipt and receipt["pass_status"] == "completed"
    assert len(calls) == 6 * len(names)
    assert machine._state.consecutive_clean_rounds == 0  # phases alone never grant credit
