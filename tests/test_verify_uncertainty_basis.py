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
    assert result.reason == f"{_INFRA_UNAVAILABLE_LABEL} c1p1 -- convergence not established"
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


# INFRA and UNTRUSTED receipts both carry authority infra-unavailable; the
# reason names both and claims neither a product defect nor trusted infra.
_INFRA_UNAVAILABLE_LABEL = (
    "unresolved finding without verified evidence (an infrastructure failure, "
    "or a product candidate in untrusted reviewer output)"
)
_MALFORMED_LABEL = "unresolved finding with a malformed basis"
_UNRECOGNIZED_LABEL = "unresolved finding with an unrecognized basis authority"


def _expected_label(basis):
    authority = basis.get("authority") if isinstance(basis, dict) else None
    if not isinstance(authority, str) or not authority.strip():
        return _MALFORMED_LABEL
    if authority.strip().lower() == "infra-unavailable":
        return _INFRA_UNAVAILABLE_LABEL
    return _UNRECOGNIZED_LABEL


_MISSING = object()
_UNVERIFIED_BASES = [
    None,
    False,
    0,
    "infra-unavailable",
    [],
    ["authority"],
    {},
    {"authority": None},
    {"authority": False},
    {"authority": 0},
    {"authority": []},
    {"authority": {}},
    {"authority": "infra-unavailable"},
    {"authority": " INFRA-UNAVAILABLE\t"},
    {"authority": ""},
    {"authority": " \t\n"},
    {"authority": "future-authority"},
]
_KNOWN_AUTHORITIES = [
    "deterministic-executed",
    "llm-trained",
    "llm-docs-latest",
    "llm-docs-pinned",
]


def _edit_finding(paths, *, basis=_MISSING, disposition="UNCERTAIN", incomplete=False):
    """Edit serialized receipts, retaining the original writer's other evidence."""
    original = None
    for index, path in enumerate(paths):
        receipt = json.loads(path.read_text())
        receipt["pass_status"] = "error" if incomplete and index == 2 else "completed"
        if index == 0:
            finding = receipt["findings"][0]
            if basis is _MISSING:
                finding.pop("basis", None)
            else:
                finding["basis"] = basis
            if disposition is _MISSING:
                finding.pop("disposition", None)
            else:
                finding["disposition"] = disposition
            original = json.dumps(finding, sort_keys=True, separators=(",", ":"))
        path.write_text(json.dumps(receipt))
    return original


def _assert_unresolved(result, snapshot, *, incomplete, hardened, label):
    assert not result.passed
    assert result.failure_kind is None
    assert result.checks_run == 8
    assert result.checks_passed == (6 if hardened else 7)
    assert result.unresolved_findings == ((1, 1, "hash", snapshot),)
    assert result.incomplete_passes == ((3,) if incomplete else ())
    assert result.completion_statuses == (
        (1, 1, "completed"),
        (1, 2, "completed"),
        (1, 3, "error" if incomplete else "completed"),
    )
    assert result.reason.endswith(f"{label} c1p1 -- convergence not established")
    assert "product finding" not in result.reason
    if incomplete:
        assert result.reason.startswith("pass did not complete: c1p3 status=error;")


@pytest.mark.parametrize("hardened", [False, True])
@pytest.mark.parametrize("incomplete", [False, True])
@pytest.mark.parametrize("basis", _UNVERIFIED_BASES)
def test_present_unverified_basis_is_unresolved(tmp_path, hardened, incomplete, basis):
    paths = _receipts(tmp_path)
    snapshot = _edit_finding(paths, basis=basis, incomplete=incomplete)
    before = [path.read_bytes() for path in paths]
    result = _verify(tmp_path, hardened=hardened, require_convergence=not incomplete)
    _assert_unresolved(
        result, snapshot, incomplete=incomplete, hardened=hardened, label=_expected_label(basis)
    )
    assert [path.read_bytes() for path in paths] == before


@pytest.mark.parametrize("hardened", [False, True])
@pytest.mark.parametrize("incomplete", [False, True])
@pytest.mark.parametrize(
    "disposition",
    [
        "CONFIRMED",
        "UNCERTAIN",
        "UNKNOWN",
        _MISSING,
        None,
        False,
        0,
        [],
        {},
        "dismissed",
        "fixed",
        "style",
        " DISMISSED ",
    ],
)
def test_every_open_disposition_uses_basis_validation(tmp_path, hardened, incomplete, disposition):
    paths = _receipts(tmp_path)
    snapshot = _edit_finding(
        paths, basis={"authority": []}, disposition=disposition, incomplete=incomplete
    )
    result = _verify(tmp_path, hardened=hardened, require_convergence=not incomplete)
    _assert_unresolved(
        result, snapshot, incomplete=incomplete, hardened=hardened, label=_MALFORMED_LABEL
    )


@pytest.mark.parametrize("incomplete", [False, True])
@pytest.mark.parametrize("disposition", ["DISMISSED", "FIXED", "STYLE"])
@pytest.mark.parametrize("basis", _UNVERIFIED_BASES)
def test_exact_closed_dispositions_ignore_malformed_basis(tmp_path, incomplete, disposition, basis):
    paths = _receipts(tmp_path)
    _edit_finding(paths, basis=basis, disposition=disposition, incomplete=incomplete)
    result = _verify(tmp_path, require_convergence=not incomplete)
    _assert_accepted_basis(result, incomplete)


def _assert_accepted_basis(result, incomplete):
    from code_forge.verify import VerifyFailureKind

    assert result.passed is (not incomplete)
    assert result.unresolved_findings == ()
    assert result.failure_kind is (VerifyFailureKind.INCOMPLETE_PASS if incomplete else None)
    assert result.incomplete_passes == ((3,) if incomplete else ())


@pytest.mark.parametrize("incomplete", [False, True])
@pytest.mark.parametrize("authority", _KNOWN_AUTHORITIES)
@pytest.mark.parametrize("variant", ["literal", "upper", "whitespace"])
def test_known_authorities_accept_minimal_basis(tmp_path, incomplete, authority, variant):
    if variant == "upper":
        authority = authority.upper()
    elif variant == "whitespace":
        authority = f" \t{authority.upper()}\n"
    paths = _receipts(tmp_path)
    _edit_finding(paths, basis={"authority": authority}, incomplete=incomplete)
    _assert_accepted_basis(_verify(tmp_path, require_convergence=not incomplete), incomplete)


@pytest.mark.parametrize("incomplete", [False, True])
def test_absent_basis_preserves_legacy_acceptance_in_both_modes(tmp_path, incomplete):
    paths = _receipts(tmp_path)
    _edit_finding(paths, incomplete=incomplete)
    _assert_accepted_basis(_verify(tmp_path, require_convergence=not incomplete), incomplete)


@pytest.mark.parametrize("basis", _UNVERIFIED_BASES)
def test_healthy_evidence_only_does_not_classify_basis(tmp_path, basis):
    paths = _receipts(tmp_path)
    _edit_finding(paths, basis=basis)
    result = _verify(tmp_path, require_convergence=False)
    assert result.passed
    assert result.unresolved_findings == ()


def test_incomplete_evidence_collects_original_snapshots_across_roles(tmp_path):
    paths = _receipts(tmp_path)
    _edit_finding(paths, basis=None, incomplete=True)
    candidate = json.loads(paths[0].read_text())["findings"][0]
    expected = []
    for index, path in enumerate(paths):
        receipt = json.loads(path.read_text())
        finding = dict(
            candidate,
            disposition="unknown",
            basis={"authority": f" Unknown-{index} "},
            extra={"nested": [None, index, {"raw": " preserve me "}]},
        )
        receipt["findings"] = [finding]
        receipt["findings_count"] = 1
        path.write_text(json.dumps(receipt))
        expected.append(
            (1, index + 1, "hash", json.dumps(finding, sort_keys=True, separators=(",", ":")))
        )
    result = _verify(tmp_path, require_convergence=False)
    assert not result.passed and result.failure_kind is None
    assert result.unresolved_findings == tuple(expected)
    assert result.incomplete_passes == (3,)


def test_malformed_basis_outside_attested_window_is_ignored(tmp_path):
    paths = _receipts(tmp_path)
    _edit_finding(paths, basis=None)
    for path in paths:
        receipt = json.loads(path.read_text())
        receipt["cycle"] = 2
        for finding in receipt["findings"]:
            finding["basis"] = {"authority": "llm-trained"}
        (path.parent / path.name.replace("c1", "c2")).write_text(json.dumps(receipt))
    result = _verify(tmp_path, cycles=[2])
    assert result.passed and result.unresolved_findings == ()


@pytest.mark.parametrize("basis", [None, {"authority": []}, {"authority": "unknown"}])
def test_earned_cycle_capture_inherits_basis_validation(tmp_path, basis):
    from code_forge.verify import _capture_earned_cycle

    paths = _receipts(tmp_path)
    snapshot = _edit_finding(paths, basis=basis, disposition="unknown")
    proof, result = _capture_earned_cycle(
        tmp_path, "hash", parse_diff_files(DIFF), cycle=1, diff_text=DIFF
    )
    assert proof is None
    _assert_unresolved(result, snapshot, incomplete=False, hardened=True, label=_expected_label(basis))
