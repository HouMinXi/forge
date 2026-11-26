"""Replay independent ruling-selection and human-adequacy controls."""

import hashlib
import json
from pathlib import Path

import pytest

from code_forge.eval.review_followup.replay import validate
from code_forge.eval.review_followup.revision_adjudication import decide_revision
from code_forge.eval.review_followup.revision_codec import decode_record
from code_forge.eval.review_followup.revision_validation import evidence_snapshot, validate_proposal

FIXTURES = Path(__file__).resolve().parents[3] / "tests/fixtures/review_followup/revision"
VECTORS = json.loads((FIXTURES / "decisions.json").read_bytes())


def wire(value):
    return json.dumps(value).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def record(kind, key, item, body):
    return wire({"schema": 1, "kind": kind, "id": key, "item": item, "body": body})


def snapshot(root, item, artifacts):
    """Pin the fixture's saved transcript bytes as candidate excerpts, not executable calls."""
    prefix = root / "inputs/case"
    prefix.mkdir(parents=True)
    rows = []
    for artifact_id, path, raw in artifacts:
        target = prefix / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        rows.append(
            {
                "id": artifact_id,
                "case": "case",
                "path": f"inputs/case/{path}",
                "role": "candidate",
                "revision": item["candidate_sha"],
                "sha256": sha(raw),
            }
        )
    data = wire(
        {
            "schema": 1,
            "case": "case",
            "task": "revision_followup",
            "base": "synthetic:prior",
            "candidate": item["candidate_sha"],
            "prior_findings": [item["finding_id"]],
            "groups": [{"id": "all", "artifacts": [x["id"] for x in rows]}],
            "artifacts": rows,
        }
    )
    (prefix / "review.json").write_bytes(data)
    (root / "manifest.json").write_bytes(
        wire(
            {
                "schema": 1,
                "cases": [
                    {
                        "id": "case",
                        "lineage": "saved-fixture",
                        "split": "calibration",
                        "input": {"path": "inputs/case/review.json", "sha256": sha(data)},
                    }
                ],
            }
        )
    )
    return evidence_snapshot(validate(root)[0], item["finding_id"])


def vector_snapshot(root, name):
    item = VECTORS["item"]
    return snapshot(
        root,
        item,
        [
            (key, spec["path"], VECTORS["observations"][VECTORS["snapshots"][name][key]].encode())
            for key, spec in VECTORS["artifacts"].items()
        ],
    )


def proposal(key, item, artifact_id, status, constraint, *, end=1):
    return record(
        "proposal",
        "p-" + key,
        item,
        {
            "claim": "Review the stated behavior under the pinned constraint.",
            "constraint_ref": constraint,
            "suggested_status": status,
            "evidence_refs": [
                {
                    "artifact_id": artifact_id,
                    "purpose": "current_basis",
                    "selector": {"kind": "source_bytes", "start": 0, "end": end},
                }
            ],
            "supersedes_proposal_id": None,
        },
    )


def ruling(key, item, p, v, status, supersedes=(), **changes):
    p = decode_record(p)
    fields = {
        "proposal_id": p.record_id,
        "proposal_digest": p.sha256,
        "validation_id": v.record_id,
        "validation_digest": v.sha256,
        "adopted_ref_indexes": [0],
        "reviewer_id": "fixture-human",
        "conclusion": status,
        "reason": "Pre-adjudicated fixture input, not an automated judgment.",
        "remaining_gaps": [],
        "supersedes_ruling_ids": list(supersedes),
    }
    return record("human_ruling", key, item, dict(fields, **changes))


def record_triplet(key, item, snap, evidence, status, supersedes=()):
    p = proposal(key, item, evidence, status, VECTORS["constraint"])
    v = validate_proposal(p, snap, validation_id="v-" + key)
    return p, v.canonical, ruling(key, item, p, v, status, supersedes)


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda case: case["id"])
def test_nine_independent_ruling_vectors(tmp_path, case):
    initial = vector_snapshot(tmp_path / "original", "original")
    active = vector_snapshot(tmp_path / "active", case["snapshot"])
    records = []
    for key in case["rulings"]:
        spec = VECTORS["rulings"][key]
        assert VECTORS["snapshots"]["original"][spec["adopts"]] == spec["original_bytes"]
        p, v, r = record_triplet(
            key, VECTORS["item"], initial, spec["adopts"], spec["conclusion"], spec["supersedes"]
        )
        records.extend((p, v, r))
        if key in case["fresh_validation_for"]:
            fresh = validate_proposal(p, active, validation_id="fresh-" + key)
            records.append(fresh.canonical)

    expected = decide_revision(active, records)
    assert (
        expected.state,
        expected.effective.record_id if expected.effective else None,
        expected.conclusion,
        expected.closed,
    ) == (case["expected_state"], case["expected_head"], case["expected_conclusion"], case["closed"])
    assert decide_revision(active, reversed(records)) == expected
    assert decide_revision(active, records + records[:3]) == expected
    if case["id"] == "invalid-middle-leaves-two-heads":
        assert expected.heads == ("A", "C")


@pytest.mark.parametrize(
    ("links", "error"),
    [
        ({"A": ("missing",)}, "supersession-missing"),
        ({"A": ("A",)}, "supersession-self"),
        ({"A": ("B",), "B": ("A",)}, "supersession-cycle"),
    ],
)
def test_invalid_supersession_is_not_a_closure(tmp_path, links, error):
    snap = vector_snapshot(tmp_path, "original")
    records = [
        raw
        for key, edges in links.items()
        for raw in record_triplet(key, VECTORS["item"], snap, "X", "verified_fixed", edges)
    ]
    result = decide_revision(snap, records)
    assert result.state == "graph_error"
    assert not result.closed
    assert error in {code for _, _, code in result.diagnostics}


def test_cross_item_target_does_not_get_ignored(tmp_path):
    snap = vector_snapshot(tmp_path, "original")
    item = VECTORS["item"]
    p, v, r = record_triplet("A", item, snap, "X", "verified_fixed", ("B",))
    cross = ruling(
        "B", dict(item, candidate_sha="synthetic:other"), p, decode_record(v), "still_present"
    )
    result = decide_revision(snap, [p, v, r, cross])
    assert result.state == "graph_error"
    assert ("human_ruling", "A", "supersession-cross-item") in result.diagnostics
    assert not result.closed


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"adopted_ref_indexes": [1]}, "invalid-adopted-reference"),
        ({"adopted_ref_indexes": []}, "closure-without-adopted-current-basis"),
        ({"adopted_ref_indexes": [False]}, "invalid-ruling"),
        ({"validation_digest": "0" * 64}, "validation-binding-mismatch"),
        ({"proposal_digest": "0" * 64}, "proposal-binding-mismatch"),
    ],
)
def test_ruling_cannot_close_with_an_inadmissible_binding(tmp_path, change, error):
    snap = vector_snapshot(tmp_path, "original")
    item = VECTORS["item"]
    p, v, _ = record_triplet("A", item, snap, "X", "verified_fixed")
    bad = ruling("A", item, p, decode_record(v), "verified_fixed", **change)
    result = decide_revision(snap, [p, v, bad])
    assert result.state == "pending_human"
    assert not result.closed
    assert ("human_ruling", "A", error) in result.diagnostics


def test_validation_wire_types_cannot_be_substituted_even_with_fresh_ruling_digest(tmp_path):
    snap = vector_snapshot(tmp_path, "original")
    item = VECTORS["item"]
    p, v, _ = record_triplet("A", item, snap, "X", "verified_fixed")
    forged = json.loads(v)
    forged["body"]["contract_version"] = True
    raw = wire(forged)
    human = ruling("A", item, p, decode_record(raw), "verified_fixed")
    result = decide_revision(snap, [p, raw, human])
    assert result.state == "pending_validation"
    assert ("host_validation", "v-A", "inactive-or-invalid-validation") in result.diagnostics
    assert not result.closed


@pytest.mark.parametrize(
    "case",
    json.loads((FIXTURES / "evidence-adequacy.json").read_bytes())["cases"],
    ids=lambda case: case["id"],
)
def test_saved_adequacy_judgments_are_human_inputs_not_inferred(tmp_path, case):
    item = dict(VECTORS["item"], candidate_sha=case["candidate_sha"])
    obs = wire(case["observation"])
    snap = snapshot(tmp_path, item, [("saved", "observations/target.json", obs)])
    controls = json.loads((FIXTURES / "evidence-adequacy.json").read_bytes())
    p = proposal("A", item, "saved", "verified_fixed", controls["constraint"], end=len(obs))
    v = validate_proposal(p, snap, validation_id="v-A")
    assert json.loads(v.canonical)["body"]["result"] == case["expected_host"]
    assert decide_revision(snap, [p, v.canonical]).state == "pending_human"
    human = ruling("A", item, p, v, case["expected_human"])
    result = decide_revision(snap, [p, v.canonical, human])
    assert result.conclusion == case["expected_human"]
    assert result.closed is (case["expected_human"] == "verified_fixed")


def test_processing_states_are_not_human_insufficiency(tmp_path):
    snap = vector_snapshot(tmp_path, "original")
    item = VECTORS["item"]
    bad = proposal("bad", item, "absent", "verified_fixed", VECTORS["constraint"])
    rejection = validate_proposal(bad, snap, validation_id="rejected")
    waiting = proposal("waiting", item, "X", "verified_fixed", VECTORS["constraint"])
    assert decide_revision(snap, []).state == "no_proposal"
    assert decide_revision(snap, [bad, rejection.canonical]).state == "host_rejected"
    mixed = decide_revision(snap, [bad, rejection.canonical, waiting])
    assert mixed.state == "pending"
    assert mixed.conclusion is None
    assert mixed.proposal_states == (("p-bad", "host_rejected"), ("p-waiting", "pending_validation"))
    assert not mixed.closed


def test_not_applicable_remains_distinct_from_fixed(tmp_path):
    snap = vector_snapshot(tmp_path, "original")
    records = record_triplet("A", VECTORS["item"], snap, "X", "no_longer_applicable")
    result = decide_revision(snap, records)
    assert result.conclusion == "no_longer_applicable"
    assert result.closed
    assert result.effective.sha256 == decode_record(records[-1]).sha256


def proposal_triplet(key, item, snap, supersedes, *, status="verified_fixed", ruling_edges=()):
    p = json.loads(proposal(key, item, "X", status, VECTORS["constraint"]))
    p["body"]["supersedes_proposal_id"] = supersedes
    p = wire(p)
    v = validate_proposal(p, snap, validation_id="v-" + key)
    return p, v.canonical, ruling(key, item, p, v, status, ruling_edges)


@pytest.mark.parametrize(
    ("links", "error"),
    [
        ({"A": "p-missing"}, "supersession-missing"),
        ({"A": "p-A"}, "supersession-self"),
        ({"A": "p-B", "B": "p-A"}, "supersession-cycle"),
    ],
)
def test_invalid_proposal_graph_cannot_close_an_item(tmp_path, links, error):
    snap = vector_snapshot(tmp_path, "original")
    records = [
        raw
        for key, target in links.items()
        for raw in proposal_triplet(key, VECTORS["item"], snap, target)
    ]
    result = decide_revision(snap, records)
    assert result.state == "graph_error"
    assert result.effective is None
    assert not result.closed
    assert error in {code for kind, _, code in result.diagnostics if kind == "proposal"}
    assert decide_revision(snap, reversed(records)) == result


def test_proposal_graph_reports_an_existing_other_item_target(tmp_path):
    snap = vector_snapshot(tmp_path, "original")
    item = VECTORS["item"]
    a = proposal_triplet("A", item, snap, "p-B")
    other = proposal(
        "B", dict(item, candidate_sha="synthetic:other"), "X", "still_present", VECTORS["constraint"]
    )
    result = decide_revision(snap, [*a, other])
    assert result.state == "graph_error"
    assert ("proposal", "p-A", "supersession-cross-item") in result.diagnostics
    assert not result.closed


def test_proposal_correction_retains_old_ruling_until_human_explicitly_supersedes_it(tmp_path):
    snap = vector_snapshot(tmp_path, "original")
    item = VECTORS["item"]
    first = proposal_triplet("A", item, snap, None)
    second = proposal_triplet("B", item, snap, "p-A", status="still_present", ruling_edges=("A",))
    # Import the new proposal and its passing host result, but not its human ruling.
    waiting = decide_revision(snap, [*second[:2], *first])
    assert waiting.effective.record_id == "A"
    assert waiting.closed
    assert dict(waiting.proposal_states)["p-B"] == "pending_human"
    decided = decide_revision(snap, [*second, *first])
    assert decided.effective.record_id == "B"
    assert decided.conclusion == "still_present"
    assert not decided.closed


def test_proposal_cannot_target_a_ruling_with_the_expected_id(tmp_path):
    snap = vector_snapshot(tmp_path, "original")
    item = VECTORS["item"]
    p, v, _ = proposal_triplet("A", item, snap, "shared")
    human = ruling("shared", item, p, decode_record(v), "verified_fixed")
    decision = decide_revision(snap, [p, v, human])
    assert decision.state == "graph_error"
    assert ("proposal", "p-A", "supersession-missing") in decision.diagnostics
    assert not decision.closed


def test_malformed_proposal_link_is_distinct_from_no_link(tmp_path):
    snap = vector_snapshot(tmp_path, "original")
    item = VECTORS["item"]
    first = proposal_triplet("A", item, snap, None)
    bad = json.loads(proposal("B", item, "X", "still_present", VECTORS["constraint"]))
    bad["body"]["supersedes_proposal_id"] = False
    decision = decide_revision(snap, [*first, wire(bad)])
    assert decision.state == "graph_error"
    assert ("proposal", "p-B", "invalid-supersession-reference") in decision.diagnostics
    assert not decision.closed


@pytest.mark.parametrize(
    ("links", "graph_error"),
    [
        (["B"], "supersession-self"),
        (["A"], None),
        (["missing"], "supersession-missing"),
        (["A", "A"], "invalid-supersession-reference"),
        ("A", "invalid-supersession-reference"),
    ],
)
def test_ruling_payload_failure_cannot_hide_graph_errors_or_grant_supersession(
    tmp_path, links, graph_error
):
    snap = vector_snapshot(tmp_path, "original")
    item = VECTORS["item"]
    original = record_triplet("A", item, snap, "X", "verified_fixed")
    p, v, _ = record_triplet("B", item, snap, "Y", "still_present")
    broken = ruling(
        "B", item, p, decode_record(v), "still_present", reason="", supersedes_ruling_ids=links
    )
    batch = [*original, p, v, broken]
    decision = decide_revision(snap, batch)
    assert ("human_ruling", "B", "invalid-ruling") in decision.diagnostics
    if graph_error:
        assert decision.state == "graph_error"
        assert ("human_ruling", "B", graph_error) in decision.diagnostics
        assert not decision.closed
    else:
        assert decision.effective.record_id == "A"
        assert decision.closed
    assert decide_revision(snap, reversed(batch)) == decision
