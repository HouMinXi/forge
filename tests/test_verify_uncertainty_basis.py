"""Receipt convergence cannot be attested by unresolved unverified products."""

import json
from pathlib import Path

import pytest

from code_forge.receipt import write_receipts
from code_forge.state import Disposition, StateFinding
from code_forge.verify import parse_diff_files, run_verify


DIFF = "diff --git a/mod.py b/mod.py\n--- a/mod.py\n+++ b/mod.py\n@@ -0,0 +1 @@\n+value = 1\n"


def _receipts(root, disposition=Disposition.UNCERTAIN, source="UNTRUSTED", audit=False):
    (root / "mod.py").write_text("value = 1\n", encoding="utf-8")
    candidate = StateFinding(
        id="RECEIPT_UNTRUSTED" if audit else "l1-qodo-product",
        fingerprint="product",
        source=source,
        disposition=disposition,
        file="mod.py",
        line_range=[1, 1],
        description="metadata" if audit else "Product candidate",
    )
    paths = write_receipts(
        root / ".code-forge/receipts",
        0,
        [candidate],
        "hash",
        [Path("mod.py")],
        root,
        diff_files=parse_diff_files(DIFF),
        diff_text=DIFF,
        reviewer_excerpts=[
            {
                "file": "mod.py",
                "start_line": 1,
                "end_line": 1,
                "content": "value = 1",
                "pass_name": "qodo",
            }
        ],
    )
    return paths


def _verify(root, **kwargs):
    return run_verify(
        root,
        "hash",
        parse_diff_files(DIFF),
        diff_text=DIFF,
        required_cycles=1,
        respect_floor=False,
        **kwargs,
    )


@pytest.mark.parametrize("hardened", [False, True])
@pytest.mark.parametrize("disposition", [Disposition.UNCERTAIN, Disposition.CONFIRMED])
def test_open_unverified_product_cannot_attest_convergence(tmp_path, hardened, disposition):
    _receipts(tmp_path, disposition)
    result = _verify(tmp_path, hardened=hardened)
    assert result.passed is False
    assert result.reason == "unresolved unverified product finding c1p1 -- convergence not established"
    assert result.checks_run == 8
    assert result.checks_passed == (6 if hardened else 7)


@pytest.mark.parametrize("hardened", [False, True])
def test_ci_single_cycle_can_attest_evidence_without_convergence(tmp_path, hardened):
    _receipts(tmp_path)
    assert _verify(tmp_path, hardened=hardened, require_convergence=False).passed is True


@pytest.mark.parametrize("hardened", [False, True])
@pytest.mark.parametrize("disposition", [Disposition.DISMISSED, Disposition.FIXED, Disposition.STYLE])
def test_closed_unverified_candidates_keep_existing_acceptance(tmp_path, hardened, disposition):
    _receipts(tmp_path, disposition)
    assert _verify(tmp_path, hardened=hardened).passed is True


@pytest.mark.parametrize("hardened", [False, True])
def test_metadata_audit_is_absent_from_product_acceptance(tmp_path, hardened):
    paths = _receipts(tmp_path, audit=True)
    assert all(json.loads(path.read_text())["findings_count"] == 0 for path in paths)
    assert _verify(tmp_path, hardened=hardened).passed is True


@pytest.mark.parametrize("hardened", [False, True])
def test_generic_l1_uncertainty_keeps_existing_acceptance(tmp_path, hardened):
    _receipts(tmp_path, source="L1")
    assert _verify(tmp_path, hardened=hardened).passed is True


@pytest.mark.parametrize("hardened", [False, True])
def test_historical_receipt_without_basis_keeps_existing_acceptance(tmp_path, hardened):
    paths = _receipts(tmp_path)
    receipt = json.loads(paths[0].read_text())
    del receipt["findings"][0]["basis"]
    paths[0].write_text(json.dumps(receipt))
    assert _verify(tmp_path, hardened=hardened).passed is True


@pytest.mark.parametrize("value", [None, 0, 1, "false", [], {}])
def test_evidence_only_option_cannot_be_a_falsy_nonboolean(tmp_path, value):
    _receipts(tmp_path)
    result = _verify(tmp_path, require_convergence=value)
    assert result.passed is False
    assert result.reason == "require_convergence must be a boolean"


def test_evidence_only_option_cannot_attest_multiple_cycles(tmp_path):
    paths = _receipts(tmp_path, disposition=Disposition.DISMISSED)
    for path in paths:
        receipt = json.loads(path.read_text())
        receipt["cycle"] = 2
        (path.parent / path.name.replace("c1", "c2")).write_text(json.dumps(receipt))
    result = run_verify(
        tmp_path,
        "hash",
        parse_diff_files(DIFF),
        diff_text=DIFF,
        required_cycles=2,
        respect_floor=False,
        require_convergence=False,
    )
    assert result.passed is False
    assert result.reason == "evidence-only attestation requires one cycle"
