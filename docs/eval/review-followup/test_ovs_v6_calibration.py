"""Frozen OVS calibration; no model, kernel, network, or production gate."""

import json
from pathlib import Path

import pytest

from code_forge.eval.review_followup import replay
from code_forge.eval.review_followup.fixtures import InputError, digest, pinned_files

FIXTURE = Path(__file__).resolve().parents[3] / "tests/fixtures/review_followup/ovs-v6"
CALIBRATION = json.loads((FIXTURE / "calibration.json").read_bytes())


def _save(root, name, value):
    return _save_bytes(root, name, (json.dumps(value, sort_keys=True) + "\n").encode())


def _save_bytes(root, name, raw):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {"path": name, "sha256": digest(raw)}


def _observation_bytes(case):
    return (FIXTURE / "observations" / f"{case}.json").read_bytes()


def _seal(root, case, observation_bytes):
    """Build a single-run corpus without a scoring tree."""
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
    saved = _save_bytes(root, f"saved_runs/{case}/observation.json", observation_bytes)
    _save(root, "runs.json", {"schema": 1, "cases": {case: {"observation": saved}}})
    return subject, replay.seal_runs(root, replay.validate(root))


def _grade(root, subject, sealed):
    case = subject["case"]
    # Load the recorded human judgment only after sealing. Its observation
    # digest is frozen, unlike the run manifest's digest of the current input.
    record = json.loads((FIXTURE / "adjudications.json").read_bytes())
    pinned_files(FIXTURE, [("adjudication-evidence", record["evidence"])])
    answer_ref = _save(
        root, f"answer_keys/{case}/key.json", {**subject, "roots": ["min-no-user-effect"]}
    )
    ruling_ref = _save(root, f"adjudications/{case}/ruling.json", record["rulings"][case])
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
    ("case", "expected"),
    [
        ("supported-reason", (1, "supported", True)),
        ("wrong-reason", (1, "unsupported", False)),
        ("missing-reason", (1, "missing", False)),
        ("uncertain-reason", (1, "uncertain", False)),
    ],
)
def test_correct_conclusion_does_not_validate_its_reason(tmp_path, case, expected):
    subject, sealed = _seal(tmp_path, case, _observation_bytes(case))
    assert not (tmp_path / "scores.json").exists()
    row = _grade(tmp_path, subject, sealed)
    findings = {item["finding"]["id"]: item["judgment"] for item in row["findings"]}
    reason = findings.get("reason")
    verdict = reason["factual"] if reason else "missing"
    scoped_pass = row["hits"] == 1 and verdict == "supported"
    assert (row["hits"], verdict, scoped_pass) == expected
    assert row["false_blockers"] == 0 and row["usage"] is None


def test_swapping_only_reason_invalidates_the_frozen_ruling(tmp_path):
    case = "supported-reason"
    original = _observation_bytes(case)
    wrong_reason = json.loads(_observation_bytes("wrong-reason"))["findings"][1]["claim"]
    old_reason = json.loads(original)["findings"][1]["claim"]
    old_bytes, wrong_bytes = (json.dumps(claim).encode() for claim in (old_reason, wrong_reason))
    assert original.count(old_bytes) == 1 and old_bytes != wrong_bytes
    tampered = original.replace(old_bytes, wrong_bytes)
    # The current run manifest matches the changed bytes, so sealing succeeds.
    subject, sealed = _seal(tmp_path, case, tampered)
    with pytest.raises(InputError) as error:
        _grade(tmp_path, subject, sealed)
    assert (error.value.code, error.value.field) == ("invalid-schema", f"{case}/adjudication")


def test_saved_reports_internal_consistency():
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
