"""Frozen OVS calibration; no model, kernel, network, or production gate."""

import json
from pathlib import Path

import pytest

from code_forge.eval.review_followup import replay
from code_forge.eval.review_followup.fixtures import digest

FIXTURE = Path(__file__).resolve().parents[3] / "tests/fixtures/review_followup/ovs-v6"
CALIBRATION = json.loads((FIXTURE / "calibration.json").read_bytes())
FORGE = "git:34dbb221d07bdec511edff65a948f2d93f5f853f"


def _save(root, name, value):
    raw = (json.dumps(value, sort_keys=True) + "\n").encode()
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {"path": name, "sha256": digest(raw)}


def _seal(root, control):
    """Build a single-run corpus without a scoring tree."""
    case = control["id"]
    vport_blob = "16b21c42cac8c65ec4a4cb491b0e154a830ed6bb"
    # This is a single source blob identifier, not a candidate commit. Use
    # synthetic revision IDs in replay; retain real identities in the oracle.
    revisions = {"base": "synthetic:ovs-v6-base", "candidate": "synthetic:ovs-v6-overlay"}
    subject = {"candidate": revisions["candidate"], "case": case, "schema": 1}
    facts = _save(
        root,
        f"inputs/{case}/source.json",
        {
            "base_commit": CALIBRATION["base"],
            "vport_blob": vport_blob,
            "protocol": "genetlink-legacy",
            "classic_protocol": "netlink-raw",
        },
    )
    ref = _save(
        root,
        f"inputs/{case}/review.json",
        {
            **subject,
            "base": revisions["base"],
            "task": "first_review",
            "prior_findings": [],
            "groups": [{"id": "ovs", "artifacts": ["source"]}],
            "artifacts": [
                {
                    **facts,
                    "id": "source",
                    "role": "candidate",
                    "revision": revisions["candidate"],
                    "case": case,
                }
            ],
        },
    )
    _save(
        root,
        "manifest.json",
        {
            "schema": 1,
            "cases": [
                {
                    "id": case,
                    "lineage": CALIBRATION["lineage"],
                    "split": "calibration",
                    "input": ref,
                }
            ],
        },
    )
    findings = [{"id": "conclusion", "claim": CALIBRATION["conclusion"], "blocking": False}]
    if control["reason"] is not None:
        findings.append({"id": "reason", "claim": control["reason"], "blocking": False})
    saved = _save(
        root,
        f"saved_runs/{case}/observation.json",
        {
            **subject,
            "run": "handwritten",
            "input_sha256": ref["sha256"],
            "baseline": {
                "forge_commit": FORGE,
                "model": None,
                "prompt": None,
                "context_limit": None,
                "call_limit": None,
                "failure_policy": "saved-only",
            },
            "status": "completed",
            "findings": findings,
            "diagnostic": None,
            "usage": None,
        },
    )
    _save(root, "runs.json", {"schema": 1, "cases": {case: {"observation": saved}}})
    return subject, saved["sha256"], replay.seal_runs(root, replay.validate(root))


def _grade(root, subject, observed, sealed, reason_ruling):
    case = subject["case"]

    def ruling(finding, factual, matched_root=None):
        return {
            "finding": finding,
            "root": matched_root,
            "factual": factual,
            "attribution": "unknown",
            "blocking": "should_not_block",
        }

    rulings = [ruling("conclusion", "supported", "min-no-user-effect")]
    if reason_ruling is not None:
        rulings.append(ruling("reason", reason_ruling))
    answer_ref = _save(
        root, f"answer_keys/{case}/key.json", {**subject, "roots": ["min-no-user-effect"]}
    )
    ruling_ref = _save(
        root,
        f"adjudications/{case}/ruling.json",
        {
            **subject,
            "run": "handwritten",
            "observation_sha256": observed,
            "findings": rulings,
            "revisions": [],
        },
    )
    _save(
        root,
        "scores.json",
        {
            "schema": 1,
            "cases": {
                subject["case"]: {"answer": answer_ref, "adjudication": ruling_ref},
            },
        },
    )
    (row,) = replay.score_sealed(root, sealed)
    return row


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        (0, (1, "supported", True)),
        (1, (1, "unsupported", False)),
        (2, (1, "missing", False)),
        (3, (1, "uncertain", False)),
    ],
)
def test_correct_conclusion_does_not_validate_its_reason(tmp_path, index, expected):
    control = CALIBRATION["controls"][index]
    subject, observed, sealed = _seal(tmp_path, control)
    assert not (tmp_path / "scores.json").exists()
    row = _grade(tmp_path, subject, observed, sealed, control["ruling"])
    findings = {item["finding"]["id"]: item["judgment"] for item in row["findings"]}
    reason = findings.get("reason")
    verdict = reason["factual"] if reason else "missing"
    scoped_pass = row["hits"] == 1 and verdict == "supported"
    assert (row["hits"], verdict, scoped_pass) == expected
    assert row["false_blockers"] == 0 and row["usage"] is None


def test_independent_pinned_observations_keep_the_claim_narrow():
    ynl = json.loads((FIXTURE / "ynl-report.json").read_bytes())
    context = json.loads((FIXTURE / "context-report.json").read_bytes())
    assert f"git:{ynl['kernel_base']}" == CALIBRATION["base"]
    assert context["source_commit"] == ynl["kernel_base"]
    generated = ynl["generated"]
    for family in ("vport", "datapath"):
        assert generated[f"{family}_v6"] == generated[f"{family}_no_min"]
    assert generated["vport_no_min"] != generated["vport_u32_no_min"]
    assert generated["vport_v6"] != generated["vport_set_list_trimmed"]
    assert {(row["path"], row["lens"]) for row in context["slots"]} == {
        (path, lens) for path in ("ordinary", "grouped") for lens in ("qodo", "expert", "adversarial")
    }
    assert all(
        row["prompt_bytes"] - row["baseline_bytes"] == row["context_bytes"] == 3992
        and row["prompt_bytes"] <= context["prompt_byte_limit"]
        for row in context["slots"]
    )
    assert ynl["sent_model_requests"] == context["sent_model_requests"] == 0
    assert not ynl["used_kernel_runtime"]
