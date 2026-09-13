# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Receipt acceptance gate: terminal verdict must bind to current-run evidence.

Drives the REAL producer (build_l1_provider), REAL receipt writer and REAL
StateMachine with a fixture transport, then asserts the machine's own
verdict and the persisted state.json agree and stay non-PASS while the
current round's evidence is invalid. Wrong literal and receipt-write I/O
failure are the two measured cases; valid excerpts are the positive
control proving the gate is not a blanket blocker.
"""

import copy
import json
from pathlib import Path

import pytest

from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.factories import build_l1_provider
from code_forge.falsify import StubFalsifier
from code_forge.llm_invoke import LLMResult, Usage
from code_forge.machine import StateMachine
from code_forge.source import compute_source_hash
from code_forge.state import Mode, Verdict

DIFF = (
    "diff --git a/control.ts b/control.ts\n"
    "--- a/control.ts\n+++ b/control.ts\n"
    "@@ -1,2 +1,3 @@\n const context = 1;\n"
    "+const value = 2;\n const end = 3;\n"
)
CONTENT = "const context = 1;\nconst value = 2;\nconst end = 3;\n"

# Architect terminal-policy fixtures (terminal-policy-probe.py): one-hunk
# diff with 10 ADDED lines (coverage floor case) and a two-hunk diff
# 6+4 added lines (witness case). Quoting only line 1 of the first gives
# 1/10 coverage (<60%); quoting all six of the first hunk of the two-hunk
# diff gives exactly 60% coverage but leaves hunk 2 unwitnessed.
DIFF_10 = (
    "diff --git a/control.ts b/control.ts\n"
    "--- a/control.ts\n+++ b/control.ts\n"
    "@@ -0,0 +1,10 @@\n"
    "+const value1 = 1;\n"
    "+const value2 = 2;\n"
    "+const value3 = 3;\n"
    "+const value4 = 4;\n"
    "+const value5 = 5;\n"
    "+const value6 = 6;\n"
    "+const value7 = 7;\n"
    "+const value8 = 8;\n"
    "+const value9 = 9;\n"
    "+const value10 = 10;\n"
)
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
CONTENT_10 = (
    "const value1 = 1;\nconst value2 = 2;\nconst value3 = 3;\n"
    "const value4 = 4;\nconst value5 = 5;\nconst value6 = 6;\n"
    "const value7 = 7;\nconst value8 = 8;\nconst value9 = 9;\n"
    "const value10 = 10;\n"
)
CONTENT_10B = (
    "const value1 = 1;\nconst value2 = 2;\nconst value3 = 3;\n"
    "const value4 = 4;\nconst value5 = 5;\nconst value6 = 6;\n"
    "const value7 = 7;\nconst value8 = 8;\nconst value9 = 9;\n"
    "const value10 = 10;\n"
)

NO_PASS = object()


def _payload(name: str) -> dict:
    base = {"findings": [], "code_excerpts": [{
        "file": "control.ts", "start_line": 1, "end_line": 3,
        "content": CONTENT, "pass_name": "adversarial",
    }]}
    if name == "valid":
        return base
    if name == "wrong_literal":
        v = copy.deepcopy(base)
        v["code_excerpts"][0]["content"] = CONTENT.replace(
            "value = 2;", "value = 20;")
        return v
    if name == "short_content":
        v = copy.deepcopy(base)
        v["code_excerpts"][0]["content"] = "const context = 1;\n"
        return v
    if name == "finding_short_content":
        v = copy.deepcopy(base)
        v["code_excerpts"][0]["content"] = "const context = 1;\n"
        v["findings"] = [{
            "file": "control.ts", "line": 2, "severity": "P1",
            "description": "DIAGNOSTIC_CANDIDATE_MUST_SURVIVE",
        }]
        return v
    if name == "sparse_coverage":
        # One-hunk 10-line diff, quote only line 1: coverage 1/10 < 60%.
        v = copy.deepcopy(base)
        v["code_excerpts"][0]["content"] = "const value1 = 1;\n"
        v["code_excerpts"][0]["start_line"] = 1
        v["code_excerpts"][0]["end_line"] = 1
        return v
    if name == "missing_hunk_witness":
        # Two-hunk 6+4 diff, quote all of hunk 1 (coverage exactly 60%),
        # leave hunk 2 (lines 21-24) unwitnessed.
        v = copy.deepcopy(base)
        v["code_excerpts"][0]["content"] = (
            "const value1 = 1;\nconst value2 = 2;\nconst value3 = 3;\n"
            "const value4 = 4;\nconst value5 = 5;\nconst value6 = 6;\n"
        )
        v["code_excerpts"][0]["start_line"] = 1
        v["code_excerpts"][0]["end_line"] = 6
        return v
    if name == "plus_one_slip":
        # One attested excerpt plus a sibling whose body matches the
        # file one line down. Coordinate slip, not a fabricated quote.
        v = copy.deepcopy(base)
        v["code_excerpts"] = [
            {
                "file": "control.ts",
                "start_line": 1,
                "end_line": 10,
                "content": CONTENT_10,
                "pass_name": "adversarial",
            },
            {
                "file": "control.ts",
                "start_line": 2,
                "end_line": 11,
                "content": CONTENT_10,
                "pass_name": "adversarial",
            },
        ]
        return v
    raise KeyError(name)


def _run(mode: Mode, payload_name: str, tmp_path: Path,
         writer_oserror: bool = False, diff: str | None = None):
    if diff is None:
        diff = DIFF_10B if payload_name == "missing_hunk_witness" else DIFF
    cwd = tmp_path
    content = {
        DIFF: CONTENT,
        DIFF_10: CONTENT_10,
        DIFF_10B: CONTENT_10B,
    }[diff]
    (cwd / "control.ts").write_text(content)
    state_dir = cwd / ".code-forge"
    state_dir.mkdir()
    (state_dir / "gate.yaml").write_text(
        "test:\n  command: [\"true\"]\nverify:\n  required_cycles: %d\n"
        % (3 if mode == Mode.LOCAL else 1))
    if writer_oserror:
        (state_dir / "receipts").write_text(
            "Deliberate fixture: not a directory.\n")
    resolved = ResolvedReview([Path("control.ts")], None, diff, "git")
    sha = compute_source_hash(git_diff=diff)
    payload = _payload(payload_name)
    calls = []

    def fake_transport(prompt, **kwargs):
        calls.append(prompt.rsplit("You are a ", 1)[-1])
        return LLMResult(copy.deepcopy(payload), Usage(), 0.0)

    from unittest.mock import patch

    with patch("code_forge.llm_invoke.llm_invoke",
               side_effect=fake_transport):
        provider = build_l1_provider(
            "auto", resolved, backend=None, max_attempts=1)
    machine = StateMachine(
        mode=mode, falsifier=StubFalsifier(), autofixer=StubAutoFixer(),
        revert_fn=lambda f: None, resolved_review=resolved,
        source_hash=sha, baseline_spec_repr="receipt gate test",
        cwd=cwd, registry={}, l0_runner=lambda *a: ([], []),
        l1_provider=provider, l2_runner=lambda *a, **kw: ([], []),
        max_total_rounds=3, clean_round_threshold=3,
    )
    with patch("code_forge.llm_invoke.llm_invoke",
               side_effect=fake_transport):
        try:
            returned = machine.run().value
        except Exception:  # noqa: BLE001 -- current bug escapes; gate must not
            returned = NO_PASS
    state_path = state_dir / "state.json"
    disk = (json.loads(state_path.read_text())
            if state_path.exists() else None)
    return {
        "returned": returned,
        "memory_verdict": machine._state.verdict.value,
        "memory_converged": machine._state.converged,
        "clean_rounds": machine._state.consecutive_clean_rounds,
        "disk_verdict": disk.get("verdict") if disk else None,
        "disk_infra_errors": disk.get("infra_errors") if disk else None,
        "receipt_count": len(list((state_dir / "receipts").glob(
            "receipt-*.json"))) if (state_dir / "receipts").is_dir() else 0,
        "transport_calls": len(calls),
        "findings": [
            {"source": f.source, "description": f.description,
             "disposition": f.disposition.value}
            for f in machine._state.findings
        ],
    }


def _assert_non_pass(res: dict) -> None:
    assert res["returned"] != Verdict.PASS.value
    assert res["memory_verdict"] != Verdict.PASS.value
    assert res["memory_converged"] is False
    assert res["disk_verdict"] != Verdict.PASS.value


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_wrong_literal_never_passes(mode, tmp_path):
    res = _run(mode, "wrong_literal", tmp_path)
    _assert_non_pass(res)
    # The gate must flag invalid evidence, not just fail on a finding.
    assert any("receipt" in d or "excerpt" in d
               for d in res["disk_infra_errors"] or [])


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_wrong_literal_does_not_accumulate_clean_rounds(mode, tmp_path):
    res = _run(mode, "wrong_literal", tmp_path)
    assert res["clean_rounds"] == 0


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_receipt_io_failure_persists_non_pass(mode, tmp_path):
    res = _run(mode, "valid", tmp_path, writer_oserror=True)
    _assert_non_pass(res)
    assert res["disk_verdict"] is not None
    assert any("receipt" in d for d in res["disk_infra_errors"] or [])


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_valid_excerpts_still_pass(mode, tmp_path):
    res = _run(mode, "valid", tmp_path)
    assert res["returned"] == Verdict.PASS.value
    assert res["memory_verdict"] == Verdict.PASS.value
    assert res["disk_verdict"] == Verdict.PASS.value
    if mode == Mode.LOCAL:
        assert res["clean_rounds"] == 3
        assert res["receipt_count"] == 9
    else:
        assert res["receipt_count"] == 3


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_candidate_survives_invalid_excerpt_as_untrusted(mode, tmp_path):
    """A valid-shaped candidate must survive an invalid excerpt, untrusted."""
    res = _run(mode, "finding_short_content", tmp_path)
    _assert_non_pass(res)
    hits = [
        f for f in res["findings"]
        if "DIAGNOSTIC_CANDIDATE_MUST_SURVIVE" in f["description"]
    ]
    assert len(hits) >= 1, res["findings"]
    assert hits[0]["source"] == "UNTRUSTED"
    assert hits[0]["disposition"] == "UNCERTAIN"


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_one_line_coordinate_slip_does_not_fail_the_gate(mode, tmp_path):
    """A +/-1 numbering slip is evidence quality, not a dead backend.

    The detector still names the offset. The round must not persist
    RECEIPT_INVALID / INFRA CONFIRMED over it, and a fully covered
    hunk must still be allowed to PASS.
    """
    res = _run(mode, "plus_one_slip", tmp_path, diff=DIFF_10)
    assert res["returned"] == Verdict.PASS.value, res
    assert res["memory_verdict"] == Verdict.PASS.value
    assert res["disk_verdict"] == Verdict.PASS.value
    if mode == Mode.LOCAL:
        assert res["clean_rounds"] == 3
    slips = [
        f for f in res["findings"]
        if "misnumbered" in f["description"]
    ]
    assert slips, res["findings"]
    assert all(f["source"] == "UNTRUSTED" for f in slips), slips
    assert not any(
        f["source"] == "INFRA" and "misnumbered" in f["description"]
        for f in res["findings"]
    ), res["findings"]


def test_wrong_literal_receipts_not_completed(tmp_path):
    """Correct pass status before write: invalid evidence is not COMPLETED."""
    res = _run(Mode.CI, "wrong_literal", tmp_path)
    assert res["returned"] == Verdict.FAIL.value
    receipts_dir = tmp_path / ".code-forge" / "receipts"
    statuses = {
        r.get("pass_status")
        for r in (json.loads(p.read_text())
                  for p in receipts_dir.glob("receipt-*.json"))
    }
    assert statuses == {"schema_fail"}


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_short_content_persists_bounded_non_pass(mode, tmp_path):
    res = _run(mode, "short_content", tmp_path)
    assert res["memory_verdict"] != Verdict.PASS.value
    # Disk must carry the bounded FAIL verdict, not a bare PENDING: a
    # circuit breaker that stops the run must persist the failure.
    assert res["disk_verdict"] == Verdict.FAIL.value


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_sparse_coverage_below_floor_fails(mode, tmp_path):
    """1/10 changed lines covered must not PASS (60% coverage floor)."""
    res = _run(mode, "sparse_coverage", tmp_path, diff=DIFF_10)
    _assert_non_pass(res)
    assert any("coverage" in d or "receipt acceptance" in d
               for d in res["disk_infra_errors"] or [])


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_missing_hunk_witness_fails(mode, tmp_path):
    """An unwitnessed hunk must not PASS (per-hunk witness check)."""
    res = _run(mode, "missing_hunk_witness", tmp_path, diff=DIFF_10B)
    _assert_non_pass(res)
    assert any("witness" in d or "receipt acceptance" in d
               for d in res["disk_infra_errors"] or [])


def test_stale_window_cannot_vouch_for_bad_current_run(tmp_path):
    """Good old high cycles must not certify a bad current round.

    Seeds valid receipts for cycles 10/11/12 through the real writer
    (same diff hash), then runs a current round with a wrong literal.
    The acceptance gate validates THIS run's evidence, so the run must
    fail even though a disk-only verify would select the old cycle 12.
    """
    from code_forge.receipt import write_receipts

    cwd = tmp_path
    (cwd / "control.ts").write_text(CONTENT)
    state_dir = cwd / ".code-forge"
    state_dir.mkdir()
    (state_dir / "gate.yaml").write_text(
        "test:\n  command: [\"true\"]\nverify:\n  required_cycles: 3\n")
    resolved = ResolvedReview([Path("control.ts")], None, DIFF, "git")
    sha = compute_source_hash(git_diff=DIFF)
    diff_files = {"control.ts": [2]}
    # Real writer seeds three clean old cycles with valid excerpts.
    for cycle in (10, 11, 12):
        write_receipts(
            receipts_dir=state_dir / "receipts",
            round_index=cycle,
            l1_findings=[],
            diff_sha256=sha,
            source_files=[Path("control.ts")],
            cwd=cwd,
            diff_files=diff_files,
            diff_text=DIFF,
            reviewer_excerpts=[{
                "file": "control.ts", "start_line": 1, "end_line": 3,
                "content": CONTENT, "pass_name": "adversarial",
            }],
            manifest=None,
            exec_evidence=None,
        )
    # Current run produces a bad literal for cycle 1 (same diff hash).
    payload = _payload("wrong_literal")
    calls = []

    def fake_transport(prompt, **kwargs):
        calls.append(1)
        return LLMResult(copy.deepcopy(payload), Usage(), 0.0)

    from unittest.mock import patch

    with patch("code_forge.llm_invoke.llm_invoke",
               side_effect=fake_transport):
        provider = build_l1_provider(
            "auto", resolved, backend=None, max_attempts=1)
    machine = StateMachine(
        mode=Mode.CI, falsifier=StubFalsifier(), autofixer=StubAutoFixer(),
        revert_fn=lambda f: None, resolved_review=resolved,
        source_hash=sha, baseline_spec_repr="stale-window test",
        cwd=cwd, registry={}, l0_runner=lambda *a: ([], []),
        l1_provider=provider, l2_runner=lambda *a, **kw: ([], []),
        max_total_rounds=3, clean_round_threshold=3,
    )
    with patch("code_forge.llm_invoke.llm_invoke",
               side_effect=fake_transport):
        returned = machine.run().value
    disk = json.loads((state_dir / "state.json").read_text())
    assert returned == Verdict.FAIL.value
    assert machine._state.verdict.value == Verdict.FAIL.value
    assert disk["verdict"] == Verdict.FAIL.value
    assert machine._state.converged is False


def test_valid_current_run_passes_with_stale_high_cycles(tmp_path):
    """A reused receipts directory must not block a VALID current run.

    Cycles 10/11/12 exist on disk from an earlier run; the current run
    writes its own cycle above them (continuation round index) with
    valid evidence. The gate attests the current run's own receipts and
    must PASS -- rejecting borrowed OLD evidence is not the same as
    rejecting every reused directory.
    """
    from code_forge.receipt import write_receipts

    cwd = tmp_path
    (cwd / "control.ts").write_text(CONTENT)
    state_dir = cwd / ".code-forge"
    state_dir.mkdir()
    (state_dir / "gate.yaml").write_text(
        "test:\n  command: [\"true\"]\nverify:\n  required_cycles: 1\n")
    resolved = ResolvedReview([Path("control.ts")], None, DIFF, "git")
    sha = compute_source_hash(git_diff=DIFF)
    diff_files = {"control.ts": [2]}
    for cycle in (10, 11, 12):
        write_receipts(
            receipts_dir=state_dir / "receipts",
            round_index=cycle,
            l1_findings=[],
            diff_sha256=sha,
            source_files=[Path("control.ts")],
            cwd=cwd,
            diff_files=diff_files,
            diff_text=DIFF,
            reviewer_excerpts=[{
                "file": "control.ts", "start_line": 1, "end_line": 3,
                "content": CONTENT, "pass_name": "adversarial",
            }],
            manifest=None,
            exec_evidence=None,
        )
    payload = _payload("valid")
    calls = []

    def fake_transport(prompt, **kwargs):
        calls.append(1)
        return LLMResult(copy.deepcopy(payload), Usage(), 0.0)

    from unittest.mock import patch

    with patch("code_forge.llm_invoke.llm_invoke",
               side_effect=fake_transport):
        provider = build_l1_provider(
            "auto", resolved, backend=None, max_attempts=1)
    machine = StateMachine(
        mode=Mode.CI, falsifier=StubFalsifier(), autofixer=StubAutoFixer(),
        revert_fn=lambda f: None, resolved_review=resolved,
        source_hash=sha, baseline_spec_repr="stale-valid test",
        cwd=cwd, registry={}, l0_runner=lambda *a: ([], []),
        l1_provider=provider, l2_runner=lambda *a, **kw: ([], []),
        max_total_rounds=3, clean_round_threshold=3,
    )
    with patch("code_forge.llm_invoke.llm_invoke",
               side_effect=fake_transport):
        returned = machine.run().value
    disk = json.loads((state_dir / "state.json").read_text())
    assert returned == Verdict.PASS.value
    assert machine._state.verdict.value == Verdict.PASS.value
    assert disk["verdict"] == Verdict.PASS.value
