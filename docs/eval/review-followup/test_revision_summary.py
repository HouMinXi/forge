"""PR5 saved-source fixtures: positive closure, an honest gap, and no inherited tree."""

import json
import shutil
from dataclasses import replace

import pytest

from code_forge.eval.review_followup.fixtures import InputError, digest
from code_forge.eval.review_followup.revision_codec import decode_record
from code_forge.eval.review_followup.revision_sources import SourcePin, normalize_revision_sources
from code_forge.eval.review_followup.revision_summary import replay_revision_summary
from code_forge.eval.review_followup.revision_validation import evidence_snapshot, validate_proposal
from test_revision_adjudication import proposal, ruling, snapshot
from test_revision_scoring import CONSTRAINT, FIXED, INSUFFICIENT, NA, label
from test_revision_validation import FINDING as FIXTURE_FINDING
from test_revision_validation import FIXTURES as PR1_FIXTURES
from test_revision_validation import propose as propose_from_review
from test_revision_validation import ref, review_at, saved, session_ref

ITEM = ("lost-exit-status", "synthetic:revision-7")
OLD = (ITEM[0], "synthetic:revision-6")


def wire(value):
    return json.dumps(value, sort_keys=True).encode()


def source(provider, candidate=ITEM[1]):
    if provider == "github":
        obj = {"review_id": "pr-12-review-3", "head": candidate}
        source_id = obj["review_id"]
    else:
        obj = {"message_key": "mail-12-reply-3", "revision_label": "v7", "reports_fixed": True}
        source_id = obj["message_key"]
    raw = wire(dict(obj, provider=provider, schema=1))
    return raw, SourcePin(provider, source_id, digest(raw), candidate)


@pytest.fixture
def candidate(tmp_path):
    item = {"finding_id": ITEM[0], "candidate_sha": ITEM[1]}
    # Benign saved evidence frames, not live selftest or private message bytes.
    adequate = b"target assertion: failed creation status is preserved; passed\n"
    weak = b"suite exit 0; target assertion was not observed\n".ljust(len(adequate))
    snap = snapshot(tmp_path / "active", item, [("target", "target.txt", adequate)])
    replaced = snapshot(tmp_path / "replaced", item, [("target", "target.txt", weak)])
    p = proposal("fix", item, "target", FIXED, CONSTRAINT, end=len(adequate))
    v = validate_proposal(p, snap, validation_id="h-fix")
    r = ruling("r-fix", item, p, v, FIXED)
    return item, snap, replaced, (p, v.canonical, r)


def replay(provider, snap, records, labels=()):
    raw, pin = source(provider)
    return replay_revision_summary(
        registered_items=[ITEM],
        saved_sources=[raw],
        source_pins=[pin],
        snapshots={ITEM: snap},
        raw_records=records,
        independent_labels=labels,
    )


def test_equivalent_saved_github_and_mail_replay_share_decision_and_c6_counts(candidate):
    _, snap, _, records = candidate
    labels = [label(decode_record(records[0]), FIXED), label(decode_record(records[-1]), FIXED)]
    a = replay("github", snap, records, labels)
    b = replay("mail", snap, reversed(records), reversed(labels))
    pa, pb = (x.pop("sources_by_candidate")[ITEM[1]] for x in (a, b))
    assert pa[0]["provider"] == "github" and pb[0]["provider"] == "mail"
    assert pa[0]["candidate"] == pb[0]["candidate"] == ITEM[1]
    assert wire(a) == wire(b)
    assert a["score"]["coverage"] == (1, 1)
    assert a["score"]["closure_error_rate"] == (0, 1)
    row = a["items"][0]
    assert row["closed"] and row["conclusion"] == "verified_fixed"
    (chosen,) = row["effective"]["adopted_evidence"]
    assert chosen["purpose"] == "current_basis"
    assert chosen["binding"]["revision"] == ITEM[1]
    assert chosen["binding"]["selector"]["kind"] == "source_bytes"
    assert row["effective"]["validation_id"] == "h-fix"


def test_unbound_mail_claim_never_inherits_the_older_tree(candidate):
    _, snap, _, records = candidate
    old, old_pin = source("github", OLD[1])
    mail, _ = source("mail")
    args = dict(
        registered_items=[OLD, ITEM],
        saved_sources=[mail, old],
        source_pins=[old_pin],
        snapshots={ITEM: snap},
    )
    report = replay_revision_summary(raw_records=[], **args)
    assert report["unbound_sources"][0]["reported_revision"] == "v7"
    assert report["unbound_sources"][0]["candidate"] is None
    assert report["unbound_sources"][0]["reported_fixed"] is True
    assert set(report["sources_by_candidate"]) == {OLD[1]}
    assert not any(row["closed"] for row in report["items"])
    assert report["score"]["coverage"] == (0, 2)
    assert report["score"]["closures"] == ()
    with pytest.raises(InputError, match="unbound-revision-records"):
        replay_revision_summary(raw_records=records, **args)


def test_changed_active_bytes_keep_old_ruling_open_and_traceable(candidate):
    _, _, replaced, records = candidate
    fresh = validate_proposal(records[0], replaced, validation_id="fresh")
    assert json.loads(fresh.canonical)["body"]["result"] == "passed"
    report = replay("mail", replaced, records, [label(decode_record(records[-1]), FIXED)])
    row = report["items"][0]
    assert row["effective"] is None and not row["closed"]
    assert row["state"] == "pending_validation"
    assert ("host_validation", "h-fix", "inactive-or-invalid-validation") in row["diagnostics"]
    assert ("human_ruling", "r-fix", "validation-binding-mismatch") in row["diagnostics"]
    assert report["score"]["coverage"] == (0, 1)
    assert report["score"]["closure_error_rate"] is None
    assert len(report["score"]["unused_labels"]) == 1


def test_honest_but_weak_observation_and_nonapplicability_remain_distinct(candidate):
    item, _, weak, _ = candidate
    p = proposal("weak", item, "target", FIXED, CONSTRAINT)
    v = validate_proposal(p, weak, validation_id="h-weak")
    r = ruling("r-weak", item, p, v, INSUFFICIENT, remaining_gaps=["target assertion not observed"])
    report = replay("mail", weak, [p, v.canonical, r])
    row = report["items"][0]
    assert row["conclusion"] == INSUFFICIENT and not row["closed"]
    assert row["effective"]["remaining_gaps"] == ["target assertion not observed"]
    assert report["score"]["coverage"] == (0, 1)
    later = ruling("r-na", item, p, v, NA, supersedes=("r-weak",))
    report = replay("mail", weak, [p, v.canonical, r, later])
    row = report["items"][0]
    # This is a supplied synthetic ruling to test projection, not a claim
    # that the same weak observation demonstrates removal of the premise.
    assert row["conclusion"] == NA and row["closed"]


def test_source_pins_bind_bytes_and_cannot_rewrite_a_github_head():
    gh, pin = source("github")
    with pytest.raises(InputError, match="source-pin-digest-mismatch"):
        normalize_revision_sources([gh + b"\n"], [pin])
    with pytest.raises(InputError, match="source-pin-candidate-mismatch"):
        normalize_revision_sources([gh], [replace(pin, candidate=OLD[1])])
    with pytest.raises(InputError, match="source-pin-missing-artifact"):
        normalize_revision_sources([], [pin])
    with pytest.raises(InputError, match="source-pin-conflict"):
        normalize_revision_sources([gh], [pin, replace(pin, candidate=OLD[1])])
    assert normalize_revision_sources([gh, gh], [pin, pin]) == normalize_revision_sources([gh], [pin])


def test_batch_keeps_cross_item_graph_errors_local(candidate, tmp_path):
    item, snap, _, records = candidate
    old_item = dict(item, candidate_sha=OLD[1])
    old = snapshot(tmp_path / "older", old_item, [("target", "target.txt", b"x")])
    obj = json.loads(proposal("old", old_item, "target", FIXED, CONSTRAINT))
    obj["body"]["supersedes_proposal_id"] = "p-fix"
    old_raw = wire(obj)
    gh, gh_pin = source("github")
    mail, mail_pin = source("mail", OLD[1])
    report = replay_revision_summary(
        registered_items=[ITEM, OLD],
        saved_sources=[mail, gh],
        source_pins=[mail_pin, gh_pin],
        snapshots={ITEM: snap, OLD: old},
        raw_records=[old_raw, *records],
    )
    first, second = report["items"]
    assert first["state"] == "graph_error" and not first["closed"]
    assert ("proposal", "p-old", "supersession-cross-item") in first["diagnostics"]
    assert second["closed"] and second["conclusion"] == FIXED
    assert report["score"]["coverage"] == (1, 2)
    assert report["score"]["proposal_counts"]["proposal_graph_rejected"] == 1


def test_provenance_does_not_multiply_scored_rows(candidate):
    _, snap, _, records = candidate
    gh, gh_pin = source("github")
    mail, mail_pin = source("mail")

    def report(sources, pins, records):
        return replay_revision_summary(
            registered_items=[ITEM],
            saved_sources=sources,
            source_pins=pins,
            snapshots={ITEM: snap},
            raw_records=records,
        )

    a = report([gh, mail], [gh_pin, mail_pin], records)
    b = report([mail, gh, mail], [mail_pin, gh_pin, mail_pin], records + records)
    assert wire(a) == wire(b)
    assert len(a["sources_by_candidate"][ITEM[1]]) == 2
    assert a["score"]["coverage"] == (1, 1)
    assert a["score"]["closure_counts"] == {"unreviewed": 1}
    assert a["score"]["closure_error_rate"] is None


@pytest.mark.parametrize("adopted_indexes", [[], [0]])
def test_still_present_without_adopted_current_evidence_is_not_coverage(candidate, adopted_indexes):
    item, snap, _, records = candidate
    p = records[0]
    v = validate_proposal(p, snap, validation_id="h-present")
    r = ruling("r-present", item, p, v, "still_present", adopted_ref_indexes=adopted_indexes)
    report = replay("github", snap, [p, v.canonical, r])
    row = report["items"][0]
    assert row["state"] == "adjudicated"
    assert not row["closed"] and row["conclusion"] == "still_present"
    assert report["score"]["coverage"] == (int(bool(adopted_indexes)), 1)
    assert report["score"]["closures"] == ()
    assert len(row["effective"]["adopted_evidence"]) == len(adopted_indexes)


def test_still_present_adopting_only_history_does_not_count_even_with_current_ref(tmp_path):
    root = tmp_path / "fixture"
    shutil.copytree(PR1_FIXTURES, root)

    def add_history(data):
        data["artifacts"].append(
            dict(data["artifacts"][0], id="base-note", role="base", revision=data["base"])
        )
        data["groups"][0]["artifacts"].append("base-note")

    review = review_at(root, (add_history,))
    snap = evidence_snapshot(review, FIXTURE_FINDING)
    item = (FIXTURE_FINDING, review.candidate)
    p = propose_from_review(review, [ref("base-note", "historical_context"), ref()], "still_present")
    v = validate_proposal(p, snap, validation_id="h")
    assert json.loads(v.canonical)["body"]["result"] == "passed"
    r = ruling(
        "r",
        {"finding_id": item[0], "candidate_sha": item[1]},
        p,
        v,
        "still_present",
        adopted_ref_indexes=[0],
    )
    raw, pin = source("github", item[1])
    report = replay_revision_summary(
        registered_items=[item],
        saved_sources=[raw],
        source_pins=[pin],
        snapshots={item: snap},
        raw_records=[p, v.canonical, r],
    )
    (row,) = report["items"]
    assert row["conclusion"] == "still_present" and not row["closed"]
    assert not row["adopts_current_basis"]
    assert report["score"]["coverage"] == (0, 1)


def test_summary_distinguishes_host_refusal_from_human_evidence_gap(tmp_path):
    root = tmp_path / "fixture"
    shutil.copytree(PR1_FIXTURES, root)
    review = review_at(root)
    item = (FIXTURE_FINDING, review.candidate)
    fixture_item = {"finding_id": item[0], "candidate_sha": item[1]}
    observation = saved(root, review, ids=("other",))
    active = evidence_snapshot(review, item[0], [("observation", observation)])
    proposal_session = propose_from_review(review, [session_ref()], FIXED)
    absent_obj = json.loads(propose_from_review(review, [session_ref("call", call_id="target")], FIXED))
    absent_obj["id"] = "call-proposal"
    proposal_call = wire(absent_obj)
    valid = validate_proposal(proposal_session, active, validation_id="h-session")
    rejected = validate_proposal(proposal_call, active, validation_id="h-call")
    human = ruling(
        "r-session",
        fixture_item,
        proposal_session,
        valid,
        INSUFFICIENT,
        remaining_gaps=["target call was not observed"],
    )
    altered = json.loads(rejected.canonical)
    altered["id"] = "h-unmatched"
    altered["body"]["reference_checks"][0]["error"] = "session-not-observed"
    raw, pin = source("github", item[1])
    args = dict(registered_items=[item], saved_sources=[raw], source_pins=[pin])
    records = [
        proposal_session,
        valid.canonical,
        human,
        proposal_call,
        rejected.canonical,
        wire(altered),
    ]
    report = replay_revision_summary(snapshots={item: active}, raw_records=records, **args)
    (row,) = report["items"]
    proposals = {p["id"]: p for p in row["proposals"]}
    assert not row["closed"] and row["conclusion"] == INSUFFICIENT
    assert row["effective"]["remaining_gaps"] == ["target call was not observed"]
    assert proposals["proposal"]["host_rejections"] == []
    assert proposals["call-proposal"]["processing"] == "host_rejected"
    assert proposals["call-proposal"]["host_rejections"] == [
        {
            "validation_id": "h-call",
            "validation_sha256": rejected.sha256,
            "reason": "closure-without-current-basis",
            "reference_errors": [{"index": 0, "error": "call-not-observed"}],
        }
    ]
    assert ("host_validation", "h-unmatched", "inactive-or-invalid-validation") in row["diagnostics"]
    assert report["score"]["coverage"] == (0, 1)
    assert report["score"]["closures"] == ()

    newer_observation = saved(root, review, ids=("target",))
    newer = evidence_snapshot(review, item[0], [("observation", newer_observation)])
    later = replay_revision_summary(snapshots={item: newer}, raw_records=reversed(records), **args)
    (row,) = later["items"]
    assert not row["closed"]
    assert all(not p["host_rejections"] for p in row["proposals"])
    assert ("host_validation", "h-call", "inactive-or-invalid-validation") in row["diagnostics"]
