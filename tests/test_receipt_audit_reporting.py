"""Keep receipt metadata diagnostics visible without reporting product defects."""

import copy
import json

import pytest

from code_forge.machine import StateMachine
from code_forge.sarif import build_sarif_log, format_summary
from code_forge.state import (
    Disposition,
    Mode,
    State,
    StateFinding,
    Verdict,
    load_state,
    save_state,
)


def test_source_proven_tail_omission_has_separate_report_channel(tmp_path):
    (tmp_path / "mod.py").write_text("alpha = 1\nbeta = 2\ntail = 3\n", encoding="utf-8")
    diff = (
        "diff --git a/mod.py b/mod.py\n--- a/mod.py\n+++ b/mod.py\n"
        "@@ -1 +1,3 @@\n-placeholder\n+alpha = 1\n+beta = 2\n+tail = 3\n"
    )
    machine = object.__new__(StateMachine)
    machine.cwd = tmp_path
    machine._receipt_diff = lambda: diff
    excerpts = [{"file": "mod.py", "start_line": 1, "end_line": 3, "content": "alpha = 1\nbeta = 2"}]
    original = copy.deepcopy(excerpts)
    findings, kept = machine._downgrade_one_line_slips([], excerpts)
    assert len(findings) == 1
    assert findings[0].id == "RECEIPT_UNTRUSTED"
    assert findings[0].source == "UNTRUSTED"
    assert findings[0].disposition is Disposition.UNCERTAIN
    assert kept == original
    state = State(mode=Mode.CI, verdict=Verdict.PASS, findings=findings)
    state_path = tmp_path / "state.json"
    save_state(state, state_path)
    restored = load_state(state_path)
    assert restored is not None
    snapshot = copy.deepcopy(restored)
    report = build_sarif_log(restored, {}, "test")
    assert report["runs"][0]["results"] == []
    audit = report["runs"][0]["properties"]["receiptAudit"]
    assert audit == json.loads(state_path.read_text(encoding="utf-8"))["findings"]
    assert "declares 3 lines but carries 2" in audit[0]["description"]
    assert format_summary(restored) == (
        "code-forge: PASS findings=0 confirmed=0 uncertain=0 dismissed=0 fixed=0 receipt_audit=1"
    )
    machine._state = restored
    assert machine.active_findings == []
    assert machine.receipt_audit == restored.findings
    assert restored == snapshot
    assert kept == original


@pytest.mark.parametrize(
    ("identifier", "source", "disposition"),
    [
        ("product", "UNTRUSTED", Disposition.UNCERTAIN),
        ("RECEIPT_UNTRUSTED", "L1", Disposition.UNCERTAIN),
        ("RECEIPT_INVALID", "INFRA", Disposition.CONFIRMED),
        ("RECEIPT_UNTRUSTED", "UNTRUSTED", Disposition.CONFIRMED),
    ],
)
def test_receipt_audit_routing_does_not_hide_other_findings(identifier, source, disposition):
    audit = StateFinding(
        id="RECEIPT_UNTRUSTED",
        fingerprint="receipt-audit",
        source="UNTRUSTED",
        disposition=Disposition.UNCERTAIN,
        file="mod.py",
        line_range=[1, 3],
        description="excerpt metadata diagnostic",
    )
    candidate = StateFinding(
        id=identifier,
        fingerprint="keep-visible",
        source=source,
        disposition=disposition,
        file="mod.py",
        line_range=[1, 3],
        description="A candidate or invalid evidence must remain visible",
    )
    state = State(mode=Mode.CI, verdict=Verdict.FAIL, findings=[audit, candidate])
    report = build_sarif_log(state, {}, "test")["runs"][0]
    assert [r["ruleId"] for r in report["results"]] == ["keep-visible"]
    assert [r["fingerprint"] for r in report["properties"]["receiptAudit"]] == ["receipt-audit"]
    summary = format_summary(state)
    assert "findings=1 " in summary
    assert "receipt_audit=1" in summary
    if disposition is Disposition.CONFIRMED:
        assert "confirmed=1 uncertain=0" in summary
    else:
        assert "confirmed=0 uncertain=1" in summary
    assert state.findings == [audit, candidate]
    machine = object.__new__(StateMachine)
    machine._state = state
    assert machine.active_findings == [candidate]
    assert machine.receipt_audit == [audit]


def test_no_audit_keeps_existing_report_shape():
    state = State(mode=Mode.CI, verdict=Verdict.PASS)
    run = build_sarif_log(state, {}, "test")["runs"][0]
    assert "properties" not in run
    assert (
        format_summary(state)
        == "code-forge: PASS findings=0 confirmed=0 uncertain=0 dismissed=0 fixed=0"
    )


def test_sampling_report_exposes_audit_separately(tmp_path, monkeypatch):
    import asyncio
    import subprocess
    from unittest.mock import MagicMock

    import code_forge.factories as factories
    import code_forge.machine as machine_module
    import code_forge.mcp_server as server

    for command in (
        ["git", "init"],
        ["git", "config", "user.name", "Test"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "initial"],
    ):
        subprocess.run(command, cwd=tmp_path, capture_output=True, check=True)
    (tmp_path / "mod.py").write_text("answer = 42\n", encoding="utf-8")
    audit = StateFinding(
        id="RECEIPT_UNTRUSTED",
        fingerprint="receipt-audit",
        source="UNTRUSTED",
        disposition=Disposition.UNCERTAIN,
        file="mod.py",
        line_range=[1, 2],
        description="excerpt mod.py:1-2 declares 2 lines but carries 1",
    )
    machine = object.__new__(StateMachine)
    machine._state = State(mode=Mode.CI, verdict=Verdict.PASS, findings=[audit])
    monkeypatch.setattr(machine, "run", lambda: Verdict.PASS)
    monkeypatch.setattr(machine_module, "StateMachine", lambda **_kwargs: machine)
    monkeypatch.setattr(factories, "build_sampling_l1_provider", lambda **_kwargs: None)
    result = asyncio.run(
        server._dispatch_sampling(
            session=MagicMock(),
            committed=False,
            workspace=tmp_path,
            whole_files=["mod.py"],
        )
    )
    data = result.structuredContent
    assert data["findings_count"] == 0
    assert data["findings"] is None
    assert data["receipt_audit"][0]["description"] == audit.description
    assert data["receipt_audit"][0]["disposition"] == "UNCERTAIN"
    assert "receipt_audit=1" in data["output"]
    assert "metadata only; see state.json" in data["output"]
    assert json.loads(json.dumps(data))["receipt_audit"] == data["receipt_audit"]
