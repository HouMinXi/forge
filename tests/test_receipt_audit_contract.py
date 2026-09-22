"""Targeted receipt audit contract tests.

Verifies _downgrade_one_line_slips behavior and real receipt writing
for source-proven damaged excerpts.
"""

import copy
import hashlib
import json
from pathlib import Path

from code_forge.machine import StateMachine
from code_forge.receipt import write_receipts
from code_forge.state import Disposition, StateFinding
from code_forge.verify import parse_diff_files


FROZEN_DIFF = (
    "diff --git a/mod.py b/mod.py\n"
    "--- a/mod.py\n"
    "+++ b/mod.py\n"
    "@@ -1 +1,3 @@\n"
    "-placeholder\n"
    "+alpha = 1\n"
    "+beta = 2\n"
    "+)\n"
)

FROZEN_TWO_HUNK_DIFF = (
    "diff --git a/mod.py b/mod.py\n"
    "--- a/mod.py\n"
    "+++ b/mod.py\n"
    "@@ -1 +1,6 @@\n"
    "-placeholder\n"
    "+alpha = 1\n"
    "+beta = 2\n"
    "+)\n"
    "+gamma = 3\n"
    "+delta = 4\n"
    "+}\n"
)


def _make_machine(diff_text: str | None = None) -> StateMachine:
    machine = object.__new__(StateMachine)
    machine.cwd = Path(".")
    machine._receipt_diff = lambda: diff_text
    return machine


def test_downgrade_one_line_slips_single_nonblank_tail_omission():
    """Exercise _downgrade_one_line_slips with minimal real diff and tail omission."""
    machine = _make_machine(FROZEN_DIFF)

    existing_finding = StateFinding(
        id="l1-qodo-rule1",
        fingerprint="fp-product-rule1",
        source="L1",
        disposition=Disposition.CONFIRMED,
        file="mod.py",
        line_range=[1, 2],
        description="Existing confirmed finding",
    )
    input_findings = [existing_finding]
    input_findings_snapshot = copy.deepcopy(input_findings)

    # 3 lines claimed in range [1, 3], but content only carries 2 lines
    exc = {
        "file": "mod.py",
        "start_line": 1,
        "end_line": 3,
        "content": "alpha = 1\nbeta = 2",
    }
    input_excerpts = [exc]
    input_excerpts_snapshot = copy.deepcopy(input_excerpts)

    out_findings, out_excerpts = machine._downgrade_one_line_slips(
        input_findings, input_excerpts
    )

    # Input findings list and contents must remain unmodified
    assert input_findings == input_findings_snapshot
    assert input_excerpts == input_excerpts_snapshot

    # Return original excerpts object with contents unchanged
    assert out_excerpts is input_excerpts
    assert out_excerpts == [exc]

    # Existing confirmed product finding remains unchanged at index 0
    assert len(out_findings) == 2
    assert out_findings[0] == existing_finding
    assert out_findings[0].disposition is Disposition.CONFIRMED

    # Exactly one appended StateFinding with required fields
    audit_finding = out_findings[1]
    expected_diag = "excerpt mod.py:1-3 declares 3 lines but carries 2"
    expected_digest = hashlib.sha256(expected_diag.encode("utf-8")).hexdigest()[:12]
    expected_fp = f"receipt-{expected_digest}"

    assert audit_finding.id == "RECEIPT_UNTRUSTED"
    assert audit_finding.source == "UNTRUSTED"
    assert audit_finding.disposition is Disposition.UNCERTAIN
    assert audit_finding.file == "mod.py"
    assert audit_finding.line_range == [1, 3]
    assert audit_finding.description == expected_diag
    assert audit_finding.fingerprint == expected_fp


def test_downgrade_two_damaged_excerpts_preserves_order_and_filters_controls():
    """Supply two distinct damaged excerpts, valid excerpt, and invalid-token excerpt."""
    machine = _make_machine(FROZEN_TWO_HUNK_DIFF)

    exc1_damaged = {
        "file": "mod.py",
        "start_line": 1,
        "end_line": 3,
        "content": "alpha = 1\nbeta = 2",
    }
    exc_valid = {
        "file": "mod.py",
        "start_line": 1,
        "end_line": 2,
        "content": "alpha = 1\nbeta = 2",
    }
    exc2_damaged = {
        "file": "mod.py",
        "start_line": 4,
        "end_line": 6,
        "content": "gamma = 3\ndelta = 4",
    }
    exc_invalid_token = {
        "file": "mod.py",
        "start_line": "invalid-token",
        "end_line": 3,
        "content": "alpha = 1",
    }

    input_excerpts = [exc1_damaged, exc_valid, exc2_damaged, exc_invalid_token]
    out_findings, out_excerpts = machine._downgrade_one_line_slips([], input_excerpts)

    # Must return original excerpts object
    assert out_excerpts is input_excerpts

    # Exactly two audit records appended in original encounter order
    assert len(out_findings) == 2

    # Verify first damaged excerpt audit record
    f1 = out_findings[0]
    expected_diag1 = "excerpt mod.py:1-3 declares 3 lines but carries 2"
    expected_fp1 = "receipt-" + hashlib.sha256(expected_diag1.encode("utf-8")).hexdigest()[:12]
    assert f1.id == "RECEIPT_UNTRUSTED"
    assert f1.source == "UNTRUSTED"
    assert f1.disposition is Disposition.UNCERTAIN
    assert f1.file == "mod.py"
    assert f1.line_range == [1, 3]
    assert f1.description == expected_diag1
    assert f1.fingerprint == expected_fp1

    # Verify second damaged excerpt audit record
    f2 = out_findings[1]
    expected_diag2 = "excerpt mod.py:4-6 declares 3 lines but carries 2"
    expected_fp2 = "receipt-" + hashlib.sha256(expected_diag2.encode("utf-8")).hexdigest()[:12]
    assert f2.id == "RECEIPT_UNTRUSTED"
    assert f2.source == "UNTRUSTED"
    assert f2.disposition is Disposition.UNCERTAIN
    assert f2.file == "mod.py"
    assert f2.line_range == [4, 6]
    assert f2.description == expected_diag2
    assert f2.fingerprint == expected_fp2


def test_empty_diff_and_empty_excerpts_return_originals():
    """Empty diff and empty excerpt list return original objects without audit entries."""
    existing_finding = StateFinding(
        id="l1-qodo-rule1",
        fingerprint="fp-product-rule1",
        source="L1",
        disposition=Disposition.CONFIRMED,
        file="mod.py",
        line_range=[1, 2],
        description="Existing confirmed finding",
    )
    initial_findings = [existing_finding]
    initial_excerpts = [
        {"file": "mod.py", "start_line": 1, "end_line": 3, "content": "alpha = 1"}
    ]

    # Case 1: empty diff returns original findings and excerpts
    machine_empty_diff = _make_machine("")
    f_res1, e_res1 = machine_empty_diff._downgrade_one_line_slips(
        initial_findings, initial_excerpts
    )
    assert f_res1 is initial_findings
    assert e_res1 is initial_excerpts

    # Case 2: empty excerpts returns original findings and excerpts
    machine_with_diff = _make_machine(FROZEN_DIFF)
    empty_excerpts = []
    f_res2, e_res2 = machine_with_diff._downgrade_one_line_slips(
        initial_findings, empty_excerpts
    )
    assert f_res2 is initial_findings
    assert e_res2 is empty_excerpts


def test_real_receipt_writer_coordinates_and_content_survive(tmp_path):
    """Run real receipt writer and read back receipt file to verify coordinates and content."""
    receipts_dir = tmp_path / ".code-forge" / "receipts"
    diff = FROZEN_DIFF
    diff_sha256 = hashlib.sha256(diff.encode("utf-8")).hexdigest()
    diff_files = parse_diff_files(diff)

    exc = {
        "file": "mod.py",
        "start_line": 1,
        "end_line": 3,
        "content": "alpha = 1\nbeta = 2",
        "pass_name": "qodo",
    }

    written_paths = write_receipts(
        receipts_dir=receipts_dir,
        round_index=0,
        l1_findings=[],
        diff_sha256=diff_sha256,
        source_files=[Path("mod.py")],
        cwd=tmp_path,
        diff_files=diff_files,
        diff_text=diff,
        reviewer_excerpts=[exc],
    )

    assert len(written_paths) == 3
    qodo_receipt_path = receipts_dir / "receipt-c1p1.json"
    assert qodo_receipt_path.exists()

    receipt_data = json.loads(qodo_receipt_path.read_text(encoding="utf-8"))
    code_excerpts = receipt_data.get("code_excerpts", [])
    assert len(code_excerpts) == 1

    actual_excerpt = code_excerpts[0]
    assert actual_excerpt["file"] == "mod.py"
    assert actual_excerpt["start_line"] == 1
    assert actual_excerpt["end_line"] == 3
    assert actual_excerpt["content"] == "alpha = 1\nbeta = 2"
    assert actual_excerpt["rationale"] == "reviewer-provided"
