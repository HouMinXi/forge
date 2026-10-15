# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Receipt diagnostics must survive the real CI ledger serialization boundary."""

import copy
import json
from pathlib import Path

import pytest

from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.factories import build_l1_provider
from code_forge.falsify import StubFalsifier
from code_forge.llm_invoke import LLMResult, Usage
from code_forge.machine import StateMachine, _ReceiptGateFailure
from code_forge.source import compute_source_hash
from code_forge.state import Mode, StateFinding, Verdict

# Same real two-hunk witness fixture as test_machine_receipt_gate.py:
# the quoted first hunk covers 60%, but the second has no witness.
DIFF_10B = (
    "diff --git a/control.ts b/control.ts\n"
    "--- a/control.ts\n+++ b/control.ts\n"
    "@@ -1,6 +1,6 @@\n"
    "-const old1 = 0;\n"
    "-const old2 = 0;\n"
    "-const old3 = 0;\n"
    "-const old4 = 0;\n"
    "-const old5 = 0;\n"
    "-const old6 = 0;\n"
    "+const value1 = 1;\n"
    "+const value2 = 2;\n"
    "+const value3 = 3;\n"
    "+const value4 = 4;\n"
    "+const value5 = 5;\n"
    "+const value6 = 6;\n"
    "@@ -21,4 +21,4 @@\n"
    "-const old7 = 0;\n"
    "-const old8 = 0;\n"
    "-const old9 = 0;\n"
    "-const old10 = 0;\n"
    "+const value7 = 7;\n"
    "+const value8 = 8;\n"
    "+const value9 = 9;\n"
    "+const value10 = 10;\n"
)

CONTENT_10B = (
    "const value1 = 1;\nconst value2 = 2;\nconst value3 = 3;\n"
    "const value4 = 4;\nconst value5 = 5;\nconst value6 = 6;\n"
    "const value7 = 7;\nconst value8 = 8;\nconst value9 = 9;\n"
    "const value10 = 10;\n"
)


@pytest.mark.parametrize("failure", ["terminal_witness", "writer_io"])
def test_receipt_gate_failure_reaches_ci_ledger(tmp_path, monkeypatch, capsys, failure):
    monkeypatch.delenv("CODE_FORGE_DISABLE_LEDGER", raising=False)
    (tmp_path / "control.ts").write_text(CONTENT_10B, encoding="utf-8")
    state_dir = tmp_path / ".code-forge"
    state_dir.mkdir()
    (state_dir / "gate.yaml").write_text(
        'test:\n  command: ["true"]\nverify:\n  required_cycles: 1\n',
        encoding="utf-8",
    )
    if failure == "writer_io":
        (state_dir / "receipts").write_text("Not a directory.\n", encoding="utf-8")
    resolved = ResolvedReview(
        [Path("control.ts")], None, DIFF_10B, "git",
        base_sha="a" * 40, head_sha="b" * 40,
    )
    payload = {
        "findings": [],
        "code_excerpts": [{
            "file": "control.ts", "start_line": 1, "end_line": 6,
            "content": "".join(CONTENT_10B.splitlines(keepends=True)[:6]),
            "pass_name": "adversarial",
        }],
    }
    transport_calls = []

    def transport(prompt, **kwargs):
        transport_calls.append(prompt)
        return LLMResult(copy.deepcopy(payload), Usage(), 0.0)

    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", transport)
    # Only environment routing is isolated; the actual ledger writer,
    # dataclass serialization and JSONL append/read stay on the production path.
    monkeypatch.setattr("code_forge.machine.resolve_ledger_root", lambda cwd: cwd)
    provider = build_l1_provider("auto", resolved, backend=None, max_attempts=1)
    machine = StateMachine(
        mode=Mode.CI, falsifier=StubFalsifier(), autofixer=StubAutoFixer(),
        revert_fn=lambda finding: None, resolved_review=resolved,
        source_hash=compute_source_hash(git_diff=DIFF_10B),
        baseline_spec_repr="receipt gate ledger test", cwd=tmp_path, registry={},
        l0_runner=lambda *args: ([], []), l1_provider=provider,
        l2_runner=lambda *args, **kwargs: ([], []),
    )
    machine._state.env_manifest = {"tier": "declared"}
    terminal_errors = []
    recorded_errors = []
    ledger_counts = []
    terminal_gate = machine._receipt_gate_terminal_errors
    recorder = machine._record_receipt_gate_failure
    ledger_writer = machine._write_ci_ledger_rows
    following = StateFinding(
        id="AFTER_RECEIPT", fingerprint="ordinary-finding-after-receipt",
        source="L0", disposition=Disposition.CONFIRMED,
        file="control.ts", line_range=[1, 1], description="ordinary finding after receipt",
    )

    def capture_terminal():
        errors = terminal_gate()
        terminal_errors.extend(errors)
        return errors

    def capture_record(error, **kwargs):
        recorded_errors.append(error)
        recorder(error, **kwargs)
        if following not in machine._state.findings:
            machine._state.findings.append(following)

    def capture_ledger():
        count = ledger_writer()
        ledger_counts.append(count)
        return count

    monkeypatch.setattr(machine, "_receipt_gate_terminal_errors", capture_terminal)
    monkeypatch.setattr(machine, "_record_receipt_gate_failure", capture_record)
    monkeypatch.setattr(machine, "_write_ci_ledger_rows", capture_ledger)
    assert machine.run() is Verdict.FAIL
    assert machine._state.verdict is Verdict.FAIL
    assert machine._state.converged is False
    assert len(transport_calls) == 3
    assert len(recorded_errors) == 1
    error = recorded_errors[0]
    expected = str(error)
    if failure == "terminal_witness":
        assert terminal_errors == [error]
        assert type(error) is _ReceiptGateFailure
        verification = error.verification
        assert verification.passed is False
        assert "hunk" in verification.reason
        assert expected == f"receipt acceptance: {verification.reason}"
        assert expected == "receipt acceptance: unwitnessed hunk control.ts:21-24"
        assert machine._written_cycles == [1]
        receipts = list((state_dir / "receipts").glob("receipt-*.json"))
        assert len(receipts) == 3
        assert all(json.loads(path.read_text())["pass_status"] == "completed" for path in receipts)
    else:
        assert terminal_errors == []
        assert type(error) is str
        assert expected.startswith("receipt write failed:")
        assert machine._written_cycles == []

    disk = json.loads((state_dir / "state.json").read_text())
    assert disk["verdict"] == "FAIL"
    assert disk["converged"] is False
    diagnostic = next(f for f in machine._state.findings if f.id == "RECEIPT_INVALID")
    assert diagnostic.description == expected
    assert next(f for f in disk["findings"] if f["id"] == "RECEIPT_INVALID")["description"] == expected
    assert f"receipt: {expected}" in disk["infra_errors"]

    # Assert the consumer before the type check: omission of str(error)
    # must fail on the missing persisted row, not just an implementation detail.
    assert ledger_counts == [2]
    rows = [json.loads(line) for line in (state_dir / "ledger.jsonl").read_text().splitlines()]
    assert [row["fingerprint"] for row in rows] == [diagnostic.fingerprint, following.fingerprint]
    assert [row["evidence_class"] for row in rows] == [expected, following.description]
    assert all(row["terminal_state"] == "UNADJUDICATED" for row in rows)
    assert all(row["base_sha"] == "a" * 40 and row["head_sha"] == "b" * 40 for row in rows)
    assert type(diagnostic.description) is str
    assert "ledger write failure" not in capsys.readouterr().err
    assert not any("ledger write failure" in item for item in machine._state.infra_errors)
    if failure == "terminal_witness":
        assert error.verification is verification
    # Normal CI dedup remains effective after the successful write.
    assert ledger_writer() == 0
    assert len((state_dir / "ledger.jsonl").read_text().splitlines()) == 2
