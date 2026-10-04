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

# Indented post-image. A quote that drops the four leading spaces is
# evidence quality, not a dead backend (issue #5).
DIFF_INDENT = (
    "diff --git a/control.ts b/control.ts\n"
    "--- a/control.ts\n+++ b/control.ts\n"
    "@@ -1 +1,3 @@\n"
    "-placeholder\n"
    "+    const context = 1;\n"
    "+    const value = 2;\n"
    "+    const end = 3;\n"
)
CONTENT_INDENT = "    const context = 1;\n    const value = 2;\n    const end = 3;\n"
CONTENT_INDENT_STRIPPED = "const context = 1;\nconst value = 2;\nconst end = 3;\n"

NO_PASS = object()


def _payload(name: str) -> dict:
    base = {
        "findings": [],
        "code_excerpts": [
            {
                "file": "control.ts",
                "start_line": 1,
                "end_line": 3,
                "content": CONTENT,
                "pass_name": "adversarial",
            }
        ],
    }
    if name == "valid":
        return base
    if name == "nonblank_tail":
        v = copy.deepcopy(base)
        v["code_excerpts"][0]["content"] = "\n".join(CONTENT.splitlines()[:2])
        return v
    if name == "minus_two":
        v = copy.deepcopy(base)
        v["code_excerpts"][0].update(
            content="\n".join(CONTENT_10.splitlines()[:8]),
            start_line=3,
            end_line=10,
        )
        return v
    if name == "wrong_literal":
        v = copy.deepcopy(base)
        v["code_excerpts"][0]["content"] = CONTENT.replace("value = 2;", "value = 20;")
        return v
    if name == "short_content":
        v = copy.deepcopy(base)
        v["code_excerpts"][0]["content"] = "const context = 1;\n"
        return v
    if name == "finding_short_content":
        v = copy.deepcopy(base)
        v["code_excerpts"][0]["content"] = "const context = 1;\n"
        v["findings"] = [
            {
                "file": "control.ts",
                "line": 2,
                "severity": "P1",
                "description": "DIAGNOSTIC_CANDIDATE_MUST_SURVIVE",
            }
        ]
        return v
    if name == "sparse_coverage":
        # One-hunk 10-line diff, quote only line 1: coverage 1/10 < 60%.
        # An open finding keeps the floor active; zero findings skip it.
        v = copy.deepcopy(base)
        v["code_excerpts"][0]["content"] = "const value1 = 1;\n"
        v["code_excerpts"][0]["start_line"] = 1
        v["code_excerpts"][0]["end_line"] = 1
        v["findings"] = [
            {
                "file": "control.ts",
                "line": 1,
                "severity": "P1",
                "description": "COVERAGE_FLOOR_MUST_HOLD",
            }
        ]
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
    if name == "indent_stripped":
        v = copy.deepcopy(base)
        v["code_excerpts"][0]["content"] = CONTENT_INDENT_STRIPPED
        v["code_excerpts"][0]["start_line"] = 1
        v["code_excerpts"][0]["end_line"] = 3
        return v
    if name == "indent_plus_token":
        v = copy.deepcopy(base)
        v["code_excerpts"][0]["content"] = CONTENT_INDENT_STRIPPED.replace("end = 3", "end = 30")
        v["code_excerpts"][0]["start_line"] = 1
        v["code_excerpts"][0]["end_line"] = 3
        return v
    raise KeyError(name)


def _run(
    mode: Mode, payload_name: str, tmp_path: Path, writer_oserror: bool = False, diff: str | None = None
):
    if diff is None:
        diff = DIFF_10B if payload_name == "missing_hunk_witness" else DIFF
    cwd = tmp_path
    content = {
        DIFF: CONTENT,
        DIFF_10: CONTENT_10,
        DIFF_10B: CONTENT_10B,
        DIFF_INDENT: CONTENT_INDENT,
    }[diff]
    (cwd / "control.ts").write_text(content)
    state_dir = cwd / ".code-forge"
    state_dir.mkdir()
    (state_dir / "gate.yaml").write_text(
        'test:\n  command: ["true"]\nverify:\n  required_cycles: %d\n' % (3 if mode == Mode.LOCAL else 1)
    )
    if writer_oserror:
        (state_dir / "receipts").write_text("Deliberate fixture: not a directory.\n")
    resolved = ResolvedReview([Path("control.ts")], None, diff, "git")
    sha = compute_source_hash(git_diff=diff)
    payload = _payload(payload_name)
    calls = []

    def fake_transport(prompt, **kwargs):
        calls.append(prompt.rsplit("You are a ", 1)[-1])
        return LLMResult(copy.deepcopy(payload), Usage(), 0.0)

    from unittest.mock import patch

    with patch("code_forge.llm_invoke.llm_invoke", side_effect=fake_transport):
        provider = build_l1_provider("auto", resolved, backend=None, max_attempts=1)
    machine = StateMachine(
        mode=mode,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=resolved,
        source_hash=sha,
        baseline_spec_repr="receipt gate test",
        cwd=cwd,
        registry={},
        l0_runner=lambda *a: ([], []),
        l1_provider=provider,
        l2_runner=lambda *a, **kw: ([], []),
        max_total_rounds=3,
        clean_round_threshold=3,
    )
    with patch("code_forge.llm_invoke.llm_invoke", side_effect=fake_transport):
        try:
            returned = machine.run().value
        except Exception:  # noqa: BLE001 -- current bug escapes; gate must not
            returned = NO_PASS
    state_path = state_dir / "state.json"
    disk = json.loads(state_path.read_text()) if state_path.exists() else None
    return {
        "returned": returned,
        "memory_verdict": machine._state.verdict.value,
        "memory_converged": machine._state.converged,
        "clean_rounds": machine._state.consecutive_clean_rounds,
        "disk_verdict": disk.get("verdict") if disk else None,
        "disk_infra_errors": disk.get("infra_errors") if disk else None,
        "receipt_count": len(list((state_dir / "receipts").glob("receipt-*.json")))
        if (state_dir / "receipts").is_dir()
        else 0,
        "transport_calls": len(calls),
        "findings": [
            {"source": f.source, "description": f.description, "disposition": f.disposition.value}
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
    assert any("receipt" in d or "excerpt" in d for d in res["disk_infra_errors"] or [])


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
    hits = [f for f in res["findings"] if "DIAGNOSTIC_CANDIDATE_MUST_SURVIVE" in f["description"]]
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
    slips = [f for f in res["findings"] if "misnumbered" in f["description"]]
    assert slips, res["findings"]
    assert all(f["source"] == "UNTRUSTED" for f in slips), slips
    assert not any(
        f["source"] == "INFRA" and "misnumbered" in f["description"] for f in res["findings"]
    ), res["findings"]


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
@pytest.mark.parametrize("payload_name,diff", [("nonblank_tail", DIFF), ("minus_two", DIFF_10)])
def test_source_proven_metadata_is_audited_without_parsing_prose(mode, payload_name, diff, tmp_path):
    res = _run(mode, payload_name, tmp_path, diff=diff)
    assert res["returned"] == Verdict.PASS.value, res
    assert res["disk_verdict"] == Verdict.PASS.value
    audit = [f for f in res["findings"] if f["source"] == "UNTRUSTED"]
    assert audit, res["findings"]
    assert not res["disk_infra_errors"]
    for path in (tmp_path / ".code-forge/receipts").glob("receipt-*.json"):
        actual = json.loads(path.read_text())["code_excerpts"][0]
        expected = _payload(payload_name)["code_excerpts"][0]
        for key in ("start_line", "end_line", "content"):
            assert actual[key] == expected[key]


def test_typed_audit_preserves_existing_product_finding(tmp_path, monkeypatch):
    from code_forge.disposition import Disposition
    from code_forge.state import StateFinding
    import code_forge.verify as verify

    machine = object.__new__(StateMachine)
    machine.cwd = tmp_path
    machine._receipt_diff = lambda: DIFF
    product = StateFinding(
        id="product",
        fingerprint="actual-product-defect",
        file="control.ts",
        line_range=[2, 2],
        source="L1",
        disposition=Disposition.CONFIRMED,
        description="Actual product finding must survive audit classification",
    )
    excerpts = _payload("nonblank_tail")["code_excerpts"]

    def forbidden(_error):
        pytest.fail("machine parsed diagnostic text")

    monkeypatch.setattr(verify, "is_evidence_quality_fault", forbidden)
    findings, kept = machine._downgrade_one_line_slips([product], excerpts)
    assert kept == excerpts
    assert findings[0] is product
    assert product.disposition is Disposition.CONFIRMED
    assert len(findings) == 2
    assert findings[1].source == "UNTRUSTED"


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_indent_stripped_quote_does_not_fail_the_gate(mode, tmp_path):
    """A left-aligned quote of indented code is UNTRUSTED, not INFRA."""
    res = _run(mode, "indent_stripped", tmp_path, diff=DIFF_INDENT)
    assert res["returned"] == Verdict.PASS.value, res
    assert res["memory_verdict"] == Verdict.PASS.value
    assert res["disk_verdict"] == Verdict.PASS.value
    if mode == Mode.LOCAL:
        assert res["clean_rounds"] == 3
    slips = [f for f in res["findings"] if "indent-stripped" in f["description"]]
    assert slips, res["findings"]
    assert all(f["source"] == "UNTRUSTED" for f in slips), slips
    assert not any(
        f["source"] == "INFRA" and "indent-stripped" in f["description"] for f in res["findings"]
    ), res["findings"]


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_indent_stripped_plus_token_change_still_fails(mode, tmp_path):
    """A token change next to stripped indent is still a dead quote."""
    res = _run(mode, "indent_plus_token", tmp_path, diff=DIFF_INDENT)
    assert res["returned"] != Verdict.PASS.value, res
    assert not any("indent-stripped" in f["description"] for f in res["findings"]), res["findings"]


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_wrong_literal_receipts_not_completed(mode, tmp_path, monkeypatch):
    """Correct pass status before write: invalid evidence is not COMPLETED."""
    from code_forge import verify

    original = verify.validate_excerpts_against_diff
    captured = []

    def capture(diff_text, excerpts, *, cwd=None):
        errors = original(diff_text, excerpts, cwd=cwd)
        captured.append((diff_text, copy.deepcopy(excerpts), cwd, list(errors)))
        return errors

    monkeypatch.setattr(verify, "validate_excerpts_against_diff", capture)
    res = _run(mode, "wrong_literal", tmp_path)
    assert res["returned"] == Verdict.FAIL.value
    receipts_dir = tmp_path / ".code-forge" / "receipts"
    receipts = verify._load_receipts(receipts_dir)
    assert receipts
    writer_calls = [call for call in captured if call[1] == receipts[0]["code_excerpts"]]
    assert len(writer_calls) == len(receipts)
    assert writer_calls[0][3]
    for receipt in receipts:
        assert receipt["pass_status"] == "schema_fail"
        matching = [call for call in writer_calls if call[:3] == (DIFF, receipt["code_excerpts"], tmp_path)]
        assert matching
        assert receipt["excerpt_validation_errors"] == matching[0][3]
        assert receipt["diff_sha256"] == compute_source_hash(git_diff=DIFF)
        assert receipt["code_excerpts"][0]["content"] == _payload("wrong_literal")["code_excerpts"][0]["content"]


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_short_content_persists_bounded_non_pass(mode, tmp_path):
    res = _run(mode, "short_content", tmp_path)
    assert res["memory_verdict"] != Verdict.PASS.value
    # Disk must carry the bounded FAIL verdict, not a bare PENDING: a
    # circuit breaker that stops the run must persist the failure.
    assert res["disk_verdict"] == Verdict.FAIL.value


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_sparse_coverage_below_floor_fails(mode, tmp_path):
    """1/10 changed lines covered must not PASS (60% coverage floor).

    An open finding keeps the floor active. CI attests its single cycle
    and records the coverage failure. LOCAL holds on the finding before
    the terminal gate, so the failure never reaches disk there.
    """
    res = _run(mode, "sparse_coverage", tmp_path, diff=DIFF_10)
    _assert_non_pass(res)
    if mode == Mode.CI:
        assert any("coverage" in d or "receipt acceptance" in d for d in res["disk_infra_errors"] or [])


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_missing_hunk_witness_fails(mode, tmp_path):
    """An unwitnessed hunk must not PASS (per-hunk witness check)."""
    res = _run(mode, "missing_hunk_witness", tmp_path, diff=DIFF_10B)
    _assert_non_pass(res)
    assert any("witness" in d or "receipt acceptance" in d for d in res["disk_infra_errors"] or [])


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
    (state_dir / "gate.yaml").write_text('test:\n  command: ["true"]\nverify:\n  required_cycles: 3\n')
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
            reviewer_excerpts=[
                {
                    "file": "control.ts",
                    "start_line": 1,
                    "end_line": 3,
                    "content": CONTENT,
                    "pass_name": "adversarial",
                }
            ],
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

    with patch("code_forge.llm_invoke.llm_invoke", side_effect=fake_transport):
        provider = build_l1_provider("auto", resolved, backend=None, max_attempts=1)
    machine = StateMachine(
        mode=Mode.CI,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=resolved,
        source_hash=sha,
        baseline_spec_repr="stale-window test",
        cwd=cwd,
        registry={},
        l0_runner=lambda *a: ([], []),
        l1_provider=provider,
        l2_runner=lambda *a, **kw: ([], []),
        max_total_rounds=3,
        clean_round_threshold=3,
    )
    with patch("code_forge.llm_invoke.llm_invoke", side_effect=fake_transport):
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
    (state_dir / "gate.yaml").write_text('test:\n  command: ["true"]\nverify:\n  required_cycles: 1\n')
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
            reviewer_excerpts=[
                {
                    "file": "control.ts",
                    "start_line": 1,
                    "end_line": 3,
                    "content": CONTENT,
                    "pass_name": "adversarial",
                }
            ],
            manifest=None,
            exec_evidence=None,
        )
    payload = _payload("valid")
    calls = []

    def fake_transport(prompt, **kwargs):
        calls.append(1)
        return LLMResult(copy.deepcopy(payload), Usage(), 0.0)

    from unittest.mock import patch

    with patch("code_forge.llm_invoke.llm_invoke", side_effect=fake_transport):
        provider = build_l1_provider("auto", resolved, backend=None, max_attempts=1)
    machine = StateMachine(
        mode=Mode.CI,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=resolved,
        source_hash=sha,
        baseline_spec_repr="stale-valid test",
        cwd=cwd,
        registry={},
        l0_runner=lambda *a: ([], []),
        l1_provider=provider,
        l2_runner=lambda *a, **kw: ([], []),
        max_total_rounds=3,
        clean_round_threshold=3,
    )
    with patch("code_forge.llm_invoke.llm_invoke", side_effect=fake_transport):
        returned = machine.run().value
    disk = json.loads((state_dir / "state.json").read_text())
    assert returned == Verdict.PASS.value
    assert machine._state.verdict.value == Verdict.PASS.value
    assert disk["verdict"] == Verdict.PASS.value


ACQUIRED_CONTENT = 'value = "quoted\\path $(touch NEVER_EXECUTED)";\n'
ACQUIRED_DIFF = (
    "diff --git a/control.txt b/control.txt\n"
    "--- a/control.txt\n+++ b/control.txt\n"
    "@@ -1 +1 @@\n-value = 1;\n+" + ACQUIRED_CONTENT
)
PASS_NAMES = ("qodo", "expert", "adversarial")


def _acquired_case(
    tmp_path, monkeypatch, *, failed_pass=None, mode=Mode.LOCAL, invocation=1, findings=False
):
    """Real producer and writer with an offline, finite transport."""
    from collections import Counter

    from code_forge import llm_invoke, receipt
    from code_forge.machine import TimeoutBreaker

    (tmp_path / "control.txt").write_text(ACQUIRED_CONTENT)
    resolved = ResolvedReview([Path("control.txt")], None, ACQUIRED_DIFF, "git")
    counts = Counter()
    events = []
    payloads = {}
    guard_errors = []
    writer_arguments = []
    active = [False]
    dispatcher_code = llm_invoke.llm_invoke.__code__

    def transport(prompt, **kwargs):
        role = prompt.rsplit("You are a ", 1)[-1]
        name = "qodo" if "structural" in role else "expert" if "senior" in role else "adversarial"
        counts["transport"] += 1
        payload = {
            "findings": (
                [
                    {
                        "file": "control.txt",
                        "line": 1,
                        "severity": "P2",
                        "description": f"{name} current candidate",
                        "excerpt": ACQUIRED_CONTENT,
                    }
                ]
                if findings and name != failed_pass
                else []
            ),
            "code_excerpts": [
                {
                    "file": "control.txt",
                    "start_line": 1,
                    "end_line": 1,
                    "content": 123 if name == failed_pass else ACQUIRED_CONTENT,
                }
            ],
            "diagnostic_probe_tag": f"invocation-{invocation}-{name}",
            "pass_name": "untrusted-input-label",
        }
        payloads[name] = copy.deepcopy(payload)
        return LLMResult(payload, Usage(input_tokens=1, output_tokens=1), 0.0)

    monkeypatch.setattr(llm_invoke, "llm_invoke", transport)
    provider = build_l1_provider("auto", resolved, backend=None, max_attempts=1)

    def write(*args, **kwargs):
        counts["writer"] += 1
        events.append("writer")
        writer_arguments.append(kwargs)
        return real_writer(*args, **kwargs)

    real_writer = receipt.write_receipts
    monkeypatch.setattr(receipt, "write_receipts", write)
    machine = StateMachine(
        mode=mode,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=resolved,
        source_hash=compute_source_hash(git_diff=ACQUIRED_DIFF),
        baseline_spec_repr="acquired receipts",
        cwd=tmp_path,
        registry={},
        l0_runner=lambda *a: ([], []),
        l1_provider=provider,
        l2_runner=lambda *a, **kw: ([], []),
        e2e_runner=lambda *a, **kw: ([], []),
        advisory_runners=[],
        max_total_rounds=1 if failed_pass else 3,
        clean_round_threshold=3,
        post_round_hook=lambda index: events.append("hook"),
    )
    machine._state.env_manifest = {"tier": "declared", "offline_fixture": True}
    real_guard = machine._check_l1_can_still_converge
    real_save = machine._persist_state

    def guard(findings):
        events.append("guard")
        try:
            return real_guard(findings)
        except TimeoutBreaker as exc:
            guard_errors.append(exc)
            events.append("breaker")
            raise

    def save():
        events.append("save")
        return real_save()

    monkeypatch.setattr(machine, "_check_l1_can_still_converge", guard)
    monkeypatch.setattr(machine, "_persist_state", save)

    def counted(original, label):
        def call(*args, **kwargs):
            counts[label] += 1
            return original(*args, **kwargs)

        return call

    for method in (
        "_run_l2_phase",
        "_run_e2e_phase",
        "_run_coverage_phase",
        "_append_round_snapshot",
        "_run_exec_falsifier",
    ):
        monkeypatch.setattr(machine, method, counted(getattr(machine, method), method))

    def audit(event, args):
        forbidden = active[0] and event.startswith(
            (
                "socket.connect",
                "socket.getaddrinfo",
                "socket.sendto",
                "subprocess.Popen",
                "os.system",
                "os.posix_spawn",
                "os.fork",
                "os.exec",
            )
        )
        counts["forbidden_operations"] += int(forbidden)
        assert not forbidden, event

    def profile(frame, event, arg):
        counts["real_dispatcher"] += int(event == "call" and frame.f_code is dispatcher_code)

    import sys

    sys.addaudithook(audit)
    return {
        "machine": machine,
        "counts": counts,
        "events": events,
        "payloads": payloads,
        "guard_errors": guard_errors,
        "writer_arguments": writer_arguments,
        "active": active,
        "profile": profile,
        "audit": audit,
    }


def _run_acquired(case):
    import sys

    from code_forge.machine import TimeoutBreaker

    previous = sys.getprofile()
    sys.setprofile(case["profile"])
    case["active"][0] = True
    try:
        try:
            return case["machine"].run()
        except (TimeoutBreaker, OSError, ValueError) as exc:
            return exc
    finally:
        case["active"][0] = False
        sys.setprofile(previous)


def _assert_acquired_receipts(tmp_path, case, *, cycles, failed_pass=None):
    from code_forge.verify import _load_receipts

    receipts_dir = tmp_path / ".code-forge" / "receipts"
    receipts = _load_receipts(receipts_dir)
    assert len(receipts) == 3 * len(cycles)
    assert {r["cycle"] for r in receipts} == set(cycles)
    assert all(r["diff_sha256"] == case["machine"].source_hash for r in receipts)
    for obj in receipts:
        name = PASS_NAMES[obj["pass"] - 1]
        assert obj["pass_status"] == ("schema_fail" if name == failed_pass else "completed")
        assert obj["code_excerpts"] == (
            []
            if name == failed_pass
            else [
                {
                    "file": "control.txt",
                    "start_line": 1,
                    "end_line": 1,
                    "content": ACQUIRED_CONTENT,
                    "rationale": "reviewer-provided",
                }
            ]
        )
    assert not (tmp_path / "NEVER_EXECUTED").exists()
    assert case["counts"]["forbidden_operations"] == 0
    assert case["counts"]["real_dispatcher"] == 0


def test_healthy_acquired_receipts_keep_normal_order(tmp_path, monkeypatch):
    case = _acquired_case(tmp_path, monkeypatch)
    assert _run_acquired(case) == Verdict.PASS
    machine = case["machine"]
    _assert_acquired_receipts(tmp_path, case, cycles=(1, 2, 3))
    assert machine._written_cycles == [1, 2, 3]
    assert case["counts"]["writer"] == 3
    assert case["counts"]["transport"] == 9
    assert case["counts"]["_run_l2_phase"] == 3
    assert case["counts"]["_run_e2e_phase"] == 3
    assert case["events"].count("hook") == 3
    for index, event in enumerate(case["events"]):
        if event == "writer":
            assert case["events"][index + 1 : index + 3] == ["save", "hook"]
    assert not list((tmp_path / ".code-forge/receipts/attempted").glob("*.json"))


@pytest.mark.parametrize("failed_pass", PASS_NAMES)
def test_terminal_acquired_receipts_preserve_final_attempt(tmp_path, monkeypatch, failed_pass):
    from code_forge.machine import TimeoutBreaker

    for invocation in (1, 2, 3):
        state_path = tmp_path / ".code-forge/state.json"
        prior = json.loads(state_path.read_text()) if state_path.exists() else None
        with monkeypatch.context() as patcher:
            case = _acquired_case(tmp_path, patcher, failed_pass=failed_pass, invocation=invocation)
            case["machine"].exec_falsify = invocation == 3
            returned = _run_acquired(case)
        machine = case["machine"]
        assert machine._state.rounds_with_failed_pass == invocation
        _assert_acquired_receipts(
            tmp_path, case, cycles=range(1, invocation + 1), failed_pass=failed_pass
        )
        assert case["counts"]["writer"] == 1
        assert machine._written_cycles == [invocation]
        assert case["counts"]["transport"] == 3
        attempted_dir = tmp_path / ".code-forge/receipts/attempted"
        attempts = [json.loads(path.read_text()) for path in sorted(attempted_dir.glob("*.json"))]
        assert len(attempts) == invocation
        latest = attempts[-1]
        expected = case["payloads"][failed_pass] | {"pass_name": failed_pass}
        assert latest == {
            "attempted": True,
            "cycle": invocation,
            "pass_name": failed_pass,
            "payload": expected,
        }
        assert case["writer_arguments"][0]["attempted_excerpts"] == [expected]
        if invocation < 3:
            assert returned == Verdict.FAIL
        else:
            assert isinstance(returned, TimeoutBreaker)
            assert returned is case["guard_errors"][0]
            assert "3 consecutive rounds" in str(returned)
            for method in (
                "_run_l2_phase",
                "_run_e2e_phase",
                "_run_coverage_phase",
                "_append_round_snapshot",
                "_run_exec_falsifier",
            ):
                assert case["counts"][method] == 0
            assert "hook" not in case["events"]
            for field, value in prior.items():
                if field.startswith("cost_") or field == "round_history":
                    assert getattr(machine._state, field) == value
            assert len(machine._state.round_history) == 2
            assert case["events"] == ["save", "guard", "save", "breaker", "writer"]
    disk = json.loads((tmp_path / ".code-forge/state.json").read_text())
    assert disk["verdict"] == "FAIL"
    assert disk["round"] == 2
    assert disk["converged"] is False
    assert disk["rounds_with_failed_pass"] == 3


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
@pytest.mark.parametrize(
    "failure", [None, "writer", "preparation", "diagnostic_save", "guard_save", "programming"]
)
def test_terminal_acquired_error_boundaries(tmp_path, monkeypatch, caplog, mode, failure):
    from code_forge.machine import TimeoutBreaker

    case = _acquired_case(tmp_path, monkeypatch, failed_pass="qodo", mode=mode)
    machine = case["machine"]
    machine._state.rounds_with_failed_pass = 2
    machine.exec_falsify = True
    original_diff = machine._receipt_diff
    original_save = machine._persist_state
    receipts_dir = tmp_path / ".code-forge/receipts"
    if failure in ("writer", "diagnostic_save"):
        receipts_dir.parent.mkdir(exist_ok=True)
        receipts_dir.write_text("filesystem obstruction")

    def diff():
        if case["guard_errors"] and failure in ("preparation", "programming"):
            raise (ValueError if failure == "programming" else OSError)("preparation failed")
        return original_diff()

    def save():
        if failure == "guard_save" and machine._state.rounds_with_failed_pass == 3:
            raise OSError("original guard save failed")
        if failure == "diagnostic_save" and case["guard_errors"]:
            raise OSError("later diagnostic save failed")
        return original_save()

    monkeypatch.setattr(machine, "_receipt_diff", diff)
    monkeypatch.setattr(machine, "_persist_state", save)
    returned = _run_acquired(case)
    if failure == "guard_save":
        assert type(returned) is OSError
        assert str(returned) == "original guard save failed"
        assert not case["guard_errors"]
        assert case["counts"]["writer"] == 0
        return
    if failure == "programming":
        assert type(returned) is ValueError
        assert str(returned) == "preparation failed"
        assert case["counts"]["writer"] == 0
        return
    assert isinstance(returned, TimeoutBreaker)
    assert returned is case["guard_errors"][0]
    disk = json.loads((tmp_path / ".code-forge/state.json").read_text())
    assert disk["verdict"] == "FAIL"
    assert disk["rounds_with_failed_pass"] == 3
    assert disk["round"] == 0
    assert disk["converged"] is False
    assert machine._state.cost_passes == 0
    assert not machine._state.round_history
    assert "hook" not in case["events"]
    assert not any(
        case["counts"][method]
        for method in (
            "_run_l2_phase",
            "_run_e2e_phase",
            "_run_coverage_phase",
            "_append_round_snapshot",
            "_run_exec_falsifier",
        )
    )
    assert case["counts"]["forbidden_operations"] == case["counts"]["real_dispatcher"] == 0
    assert case["counts"]["writer"] == (0 if failure == "preparation" else 1)
    assert machine._written_cycles == ([] if failure else [1])
    if failure:
        assert any("receipt write failed:" in error for error in machine._state.infra_errors)
        if failure == "diagnostic_save":
            assert any("later diagnostic save failed" in error for error in machine._state.infra_errors)
            assert "later diagnostic save failed" in caplog.text
            assert any(record.name == "code_forge" for record in caplog.records)
            assert not any("receipt write failed:" in error for error in disk["infra_errors"])
        else:
            assert any("receipt write failed:" in error for error in disk["infra_errors"])
    else:
        _assert_acquired_receipts(tmp_path, case, cycles=(1,), failed_pass="qodo")
        attempts = list((receipts_dir / "attempted").glob("*.json"))
        assert len(attempts) == 1
        assert (
            json.loads(attempts[0].read_text())["payload"]["diagnostic_probe_tag"] == "invocation-1-qodo"
        )


def test_acquired_offline_audit_rejects_operations(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from code_forge.llm_invoke import llm_invoke

    dispatcher_frame = SimpleNamespace(f_code=llm_invoke.__code__)
    case = _acquired_case(tmp_path, monkeypatch)
    case["profile"](dispatcher_frame, "call", None)
    case["profile"](dispatcher_frame, "return", None)
    assert case["counts"]["real_dispatcher"] == 1
    case["active"][0] = True
    try:
        for event in ("socket.connect", "subprocess.Popen"):
            with pytest.raises(AssertionError, match=event):
                case["audit"](event, ())
    finally:
        case["active"][0] = False
    assert case["counts"]["forbidden_operations"] == 2


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_normal_acquired_preparation_error_still_propagates(tmp_path, monkeypatch, mode):
    case = _acquired_case(tmp_path, monkeypatch, mode=mode)

    def diff():
        raise OSError("normal preparation failed")

    monkeypatch.setattr(case["machine"], "_receipt_diff", diff)
    returned = _run_acquired(case)
    assert type(returned) is OSError
    assert str(returned) == "normal preparation failed"
    assert not case["guard_errors"]
    assert case["counts"]["writer"] == 0
    assert case["machine"]._written_cycles == []
    assert "hook" not in case["events"]


def _seed_acquired_state(case):
    machine = case["machine"]
    machine._state.source_hash = machine.source_hash
    machine._persist_state()
    return json.loads((machine.cwd / ".code-forge/state.json").read_text())


def _assert_terminal_round_unchanged(case, prior, returned):
    from code_forge.machine import TimeoutBreaker

    machine = case["machine"]
    disk = json.loads((machine.cwd / ".code-forge/state.json").read_text())
    assert isinstance(returned, TimeoutBreaker)
    assert returned is case["guard_errors"][0]
    assert disk["verdict"] == "FAIL"
    assert disk["rounds_with_failed_pass"] == 3
    assert disk["converged"] is False
    assert disk["round"] == 0
    for key, value in prior.items():
        if key.startswith("cost_") or key in ("findings", "round_history", "exec_evidence"):
            assert disk[key] == value
    assert case["counts"]["transport"] == 3
    assert case["counts"]["writer"] == 1
    assert machine._written_cycles == [1]
    assert not any(
        case["counts"][method]
        for method in (
            "_run_l2_phase",
            "_run_e2e_phase",
            "_run_coverage_phase",
            "_append_round_snapshot",
            "_run_exec_falsifier",
        )
    )
    assert "hook" not in case["events"]
    _assert_acquired_receipts(machine.cwd, case, cycles=(1,), failed_pass="qodo")


@pytest.mark.parametrize("status", ["pass_after", "fail_before"])
def test_terminal_acquired_omits_historical_execution(tmp_path, monkeypatch, status):
    from code_forge.exec_falsify import ExecEvidence, ExecStatus
    from code_forge.verify import _load_receipts

    case = _acquired_case(tmp_path, monkeypatch, failed_pass="qodo", findings=True)
    machine = case["machine"]
    historical = ExecEvidence(
        ExecStatus(status), ["old-command"], int(status == "fail_before"), 0.125
    ).to_dict()
    machine._state.exec_evidence = historical
    machine._state.rounds_with_failed_pass = 2
    machine.exec_falsify = True
    machine.exec_falsify_command = ["new-command"]
    prior = _seed_acquired_state(case)
    returned = _run_acquired(case)
    _assert_terminal_round_unchanged(case, prior, returned)
    assert machine._state.exec_evidence == historical
    receipts = _load_receipts(tmp_path / ".code-forge/receipts")
    assert sum(r["findings_count"] for r in receipts if r["pass"] != 1) == 2
    assert all("exec_evidence" not in r for r in receipts)
    assert all("exec_evidence" not in f["basis"] for r in receipts for f in r["findings"])
    assert case["writer_arguments"][0]["exec_evidence"] is None
    assert all(
        f["disposition"] == "CONFIRMED" for r in receipts if r["pass"] != 1 for f in r["findings"]
    )


@pytest.mark.parametrize(
    "current, history, promoted, expected",
    [
        ("DISMISSED", ["CONFIRMED"], False, "DISMISSED"),
        ("STYLE", ["DISMISSED"], False, "STYLE"),
        ("CONFIRMED", ["DISMISSED"], False, "CONFIRMED"),
        ("FIXED", ["DISMISSED"], False, "CONFIRMED"),
        (None, ["DISMISSED", None, None], False, "DISMISSED"),
        (None, ["STYLE", None], False, "STYLE"),
        (None, ["DISMISSED", "CONFIRMED", None], False, "CONFIRMED"),
        (None, ["FIXED", None], False, "CONFIRMED"),
        (None, ["UNCERTAIN"], True, "UNCERTAIN"),
        ("DISMISSED", ["UNCERTAIN"], True, "DISMISSED"),
        ("STYLE", ["UNCERTAIN"], True, "STYLE"),
        (None, [], False, "CONFIRMED"),
    ],
)
def test_terminal_acquired_preserves_sticky_dispositions(
    tmp_path, monkeypatch, current, history, promoted, expected
):
    from code_forge.disposition import Disposition
    from code_forge.reviewer_json import _location_fingerprint
    from code_forge.state import StateFinding
    from code_forge.verify import _load_receipts

    case = _acquired_case(tmp_path, monkeypatch, failed_pass="qodo", findings=True)
    machine = case["machine"]
    fp = _location_fingerprint("control.txt", 1, "expert")
    machine._state.rounds_with_failed_pass = 2
    machine._state.round_history = [{"dispositions": {fp: value} if value else {}} for value in history]
    if current:
        machine._state.findings = [
            StateFinding(
                id="prior-expert",
                fingerprint=fp,
                source="L1",
                disposition=Disposition(current),
                file="control.txt",
                line_range=[1, 1],
                description="prior persisted decision",
            )
        ]
    if promoted:
        machine._state.fix_attempts[fp] = machine.max_fix_attempts
    machine.exec_falsify = True
    prior = _seed_acquired_state(case)
    returned = _run_acquired(case)
    _assert_terminal_round_unchanged(case, prior, returned)
    receipts = _load_receipts(tmp_path / ".code-forge/receipts")
    expert = next(r for r in receipts if r["pass"] == 2)
    assert expert["findings_count"] == 1
    assert expert["findings"][0]["disposition"] == expected
    assert expert["findings"][0]["description"] == "[expert] expert current candidate"
    assert expert["findings"][0]["basis"]["falsification_survived"] == (expected != "DISMISSED")
    adversarial = next(r for r in receipts if r["pass"] == 3)
    assert adversarial["findings_count"] == 1
    assert adversarial["findings"][0]["disposition"] == "CONFIRMED"
    assert adversarial["findings"][0]["description"] == "[adversarial] adversarial current candidate"
    written_findings = case["writer_arguments"][0]["l1_findings"]
    assert next(f for f in written_findings if f.fingerprint == fp).disposition.value == expected
    assert machine._state.fix_attempts == ({fp: machine.max_fix_attempts} if promoted else {})


def test_healthy_acquired_publishes_current_execution(tmp_path, monkeypatch):
    from code_forge.exec_falsify import ExecEvidence, ExecFalsifier, ExecStatus
    from code_forge.verify import _load_receipts

    case = _acquired_case(tmp_path, monkeypatch)
    machine = case["machine"]
    machine._state.exec_evidence = ExecEvidence(
        ExecStatus.FAIL_BEFORE, ["old-command"], 1, 0.125
    ).to_dict()
    machine.exec_falsify = True
    machine.exec_falsify_command = ["new-command"]
    _seed_acquired_state(case)
    current = ExecEvidence(ExecStatus.PASS_AFTER, ["new-command"], 0, 0.25)
    calls = []

    def execute(falsifier, cwd):
        calls.append((falsifier._explicit_command, cwd))
        return current

    monkeypatch.setattr(ExecFalsifier, "run", execute)
    assert _run_acquired(case) == Verdict.PASS
    _assert_acquired_receipts(tmp_path, case, cycles=(1, 2, 3))
    assert calls == [(["new-command"], tmp_path)] * 3
    assert case["counts"]["_run_exec_falsifier"] == 3
    assert case["counts"]["writer"] == 3
    assert case["events"].count("hook") == 3
    assert machine._state.exec_evidence == current.to_dict()
    assert all(args["exec_evidence"] == current.to_dict() for args in case["writer_arguments"])
    receipts = _load_receipts(tmp_path / ".code-forge/receipts")
    assert all(r["exec_evidence"] == current.to_dict() for r in receipts)
    disk = json.loads((tmp_path / ".code-forge/state.json").read_text())
    assert disk["verdict"] == "PASS"
    assert disk["exec_evidence"] == current.to_dict()
