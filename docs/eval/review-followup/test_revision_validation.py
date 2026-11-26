"""C3 acceptance: provenance is mechanical; adequacy is a human judgment."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from code_forge.eval.review_followup.fixtures import InputError, pinned_files
from code_forge.eval.review_followup.replay import validate
from code_forge.eval.review_followup.revision_codec import decode_record
from code_forge.eval.review_followup.revision_validation import evidence_snapshot, validate_proposal

FIXTURES = Path(__file__).resolve().parents[3] / "tests/fixtures/review_followup/synthetic"
FINDING = "old-fixed"


def encode(value):
    return json.dumps(value).encode()


def checksum(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def root(tmp_path):
    return Path(shutil.copytree(FIXTURES, tmp_path / "fixture"))


def review_at(root, edits=()):
    if edits:
        file = root / "inputs/revision/review.json"
        review = json.loads(file.read_bytes())
        for edit in edits:
            edit(review)
        raw = encode(review)
        file.write_bytes(raw)
        manifest = root / "manifest.json"
        doc = json.loads(manifest.read_bytes())
        for case in doc["cases"]:
            if case["id"] == "revision":
                case["input"]["sha256"] = checksum(raw)
        manifest.write_bytes(encode(doc))
    return next(review for review in validate(root) if review.case == "revision")


def ref(artifact_id="candidate-note", purpose="current_basis", selector=None):
    return {
        "artifact_id": artifact_id,
        "purpose": purpose,
        "selector": selector if selector is not None else {"kind": "source_bytes", "start": 0, "end": 4},
    }


def propose(review, refs, status="verified_fixed"):
    return encode(
        {
            "schema": 1,
            "kind": "proposal",
            "id": "proposal",
            "item": {"finding_id": FINDING, "candidate_sha": review.candidate},
            "body": {
                "claim": "Fix the original behavior under the admitted constraint.",
                "constraint_ref": {"id": "constraint", "version": 1},
                "suggested_status": status,
                "evidence_refs": refs,
                "supersedes_proposal_id": None,
            },
        }
    )


def check(raw, snap):
    record = validate_proposal(raw, snap, validation_id="validation")
    assert record.kind == "host_validation"
    assert decode_record(record.canonical) == record
    body = json.loads(record.canonical)["body"]
    assert body["proposal_digest"] == decode_record(raw).sha256
    return body


def test_same_artifact_id_path_and_bytes_do_not_establish_current_revision(root):
    current = review_at(root)

    def move_to_base(data):
        data["artifacts"][0].update(role="base", revision=data["base"])

    historical = review_at(root, (move_to_base,))
    assert current.evidence[0][0] == historical.evidence[0][0]
    assert current.evidence[0][3:] == historical.evidence[0][3:]
    assert current.candidate == historical.candidate
    raw = propose(current, [ref()])
    good = check(raw, evidence_snapshot(current, FINDING))
    assert good["result"] == "passed"
    assert good["reference_checks"][0]["binding"]["revision"] == current.candidate
    stale = check(raw, evidence_snapshot(historical, FINDING))
    assert stale["result"] == "rejected"
    assert stale["reference_checks"][0]["error"] == "not-current-basis"
    assert stale["reference_checks"][0]["binding"] is None

    history_ref = ref(purpose="historical_context")
    closure = check(propose(historical, [history_ref]), evidence_snapshot(historical, FINDING))
    assert closure["result"] == "rejected"
    assert closure["reason"] == "closure-without-current-basis"
    assert closure["reference_checks"][0]["binding"]["revision"] == historical.base
    opened = check(
        propose(historical, [history_ref], status="still_present"),
        evidence_snapshot(historical, FINDING),
    )
    assert opened["result"] == "passed"


def test_valid_historical_context_and_current_basis_are_both_retained_in_order(root):
    def add_history(data):
        data["artifacts"].append(
            dict(data["artifacts"][0], id="base-note", role="base", revision=data["base"])
        )
        data["groups"][0]["artifacts"].append("base-note")

    review = review_at(root, (add_history,))
    raw = propose(review, [ref("base-note", "historical_context"), ref()])
    body = check(raw, evidence_snapshot(review, FINDING))
    assert body["result"] == "passed"
    assert [c["binding"]["role"] for c in body["reference_checks"]] == [
        "base_source",
        "candidate_source",
    ]


def saved(root, review, *, ids=(), owner="revision", **changes):
    value = dict(
        schema=1,
        case=review.case,
        input_sha256=review.sha256,
        candidate=review.candidate,
        session="session-1",
        call_ids=list(ids),
    )
    value.update(changes)
    raw = encode(value)
    path = f"saved_observations/{owner}/session.json"
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_bytes(raw)
    return pinned_files(root, [("session", {"path": path, "sha256": checksum(raw)})])["session"]


def session_ref(kind="session", **fields):
    return ref("observation", selector={"kind": kind, "session_id": "session-1", **fields})


def test_session_without_target_is_admitted_but_absent_target_reference_is_rejected(root):
    review = review_at(root)
    artifact = saved(root, review, ids=("other",))
    snap = evidence_snapshot(review, FINDING, [("observation", artifact)])
    whole = check(propose(review, [session_ref()]), snap)
    assert whole["result"] == "passed"
    assert whole["reference_checks"][0]["binding"] == {
        "artifact_id": "observation",
        "artifact_sha256": artifact.sha256,
        "revision": review.candidate,
        "role": "candidate_observation",
        "selector": {"kind": "session", "session_id": "session-1"},
    }
    call_ref = session_ref("call", call_id="target")
    absent = check(propose(review, [call_ref]), snap)
    assert absent["result"] == "rejected"
    assert absent["reference_checks"][0]["error"] == "call-not-observed"

    present = saved(root, review, ids=("target",))
    admitted = evidence_snapshot(review, FINDING, [("observation", present)])
    body = check(propose(review, [call_ref]), admitted)
    assert body["result"] == "passed"
    assert body["reference_checks"][0]["binding"]["selector"] == call_ref["selector"]
    wrong_session = ref("observation", selector={"kind": "session", "session_id": "wrong"})
    assert check(propose(review, [wrong_session]), admitted)["reference_checks"][0]["error"] == (
        "session-not-observed"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"case": "another-case"},
        {"candidate": "synthetic:wrong"},
        {"input_sha256": "0" * 64},
        {"call_ids": ["duplicate", "duplicate"]},
    ],
)
def test_saved_observation_cannot_supply_its_own_provenance(root, changes):
    review = review_at(root)
    artifact = saved(root, review, **changes)
    with pytest.raises(InputError, match=r"^invalid-schema: snapshot.observation$"):
        evidence_snapshot(review, FINDING, [("observation", artifact)])


def test_source_byte_range_boundary_and_invalid_type(root):
    review = review_at(root)
    snap = evidence_snapshot(review, FINDING)
    length = len(review.evidence[0][-1])
    valid = ref(selector={"kind": "source_bytes", "start": length - 1, "end": length})
    assert check(propose(review, [valid]), snap)["result"] == "passed"
    outside = ref(selector={"kind": "source_bytes", "start": length - 1, "end": length + 1})
    body = check(propose(review, [outside]), snap)
    assert body["reference_checks"][0]["error"] == "source-span-out-of-range"
    malformed = ref(selector={"kind": "source_bytes", "start": False, "end": 1})
    with pytest.raises(InputError, match=r"^invalid-schema: proposal.evidence_refs/0/selector$"):
        check(propose(review, [malformed]), snap)


def test_wrong_item_missing_artifact_and_wrong_role_are_not_admitted(root):
    review = review_at(root)
    with pytest.raises(InputError, match=r"^invalid-schema: snapshot.item$"):
        evidence_snapshot(review, "not-preregistered")
    snap = evidence_snapshot(review, FINDING)
    good = propose(review, [ref()])
    wrong_item = json.loads(good)
    wrong_item["item"]["candidate_sha"] = "synthetic:wrong"
    with pytest.raises(InputError, match=r"^invalid-schema: proposal.item$"):
        check(encode(wrong_item), snap)
    body = check(
        propose(
            review,
            [ref(), ref("missing"), ref(selector={"kind": "session", "session_id": "session-1"})],
        ),
        snap,
    )
    assert body["result"] == "rejected"
    assert body["reason"] is None
    assert [c["error"] for c in body["reference_checks"]] == [
        None,
        "artifact-not-in-scope",
        "selector-role-mismatch",
    ]


def test_wrong_case_directory_is_refused_even_when_all_saved_fields_match(root):
    review = review_at(root)
    artifact = saved(root, review, owner="another-case")
    with pytest.raises(InputError, match=r"^invalid-schema: snapshot.observation$"):
        evidence_snapshot(review, FINDING, [("observation", artifact)])


def test_snapshot_uses_verified_bytes_without_reopening_evidence(root):
    review = review_at(root)
    admitted = saved(root, review, ids=("target",))
    before = evidence_snapshot(review, FINDING, [("observation", admitted)])
    updated = saved(root, review, ids=("another-target",))
    after = evidence_snapshot(review, FINDING, [("observation", updated)])
    assert admitted.path == updated.path
    assert admitted.sha256 != updated.sha256
    (root / admitted.path).unlink()

    raw = propose(review, [session_ref()])
    old = check(raw, before)
    new = check(raw, after)
    assert old["result"] == new["result"] == "passed"
    assert old["reference_checks"][0]["binding"]["artifact_sha256"] == admitted.sha256
    assert new["reference_checks"][0]["binding"]["artifact_sha256"] == updated.sha256
    assert old["proposal_digest"] == new["proposal_digest"]
