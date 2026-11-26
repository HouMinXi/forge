"""Hand-computed C6 ledger and exact-binding controls, using the C4 entry point."""

from dataclasses import replace

import pytest

from code_forge.eval.review_followup.fixtures import InputError
from code_forge.eval.review_followup.revision_adjudication import decide_revision
from code_forge.eval.review_followup.revision_codec import decode_record
from code_forge.eval.review_followup.revision_scoring import IndependentLabel, score_revision_batch
from code_forge.eval.review_followup.revision_validation import validate_proposal
from test_revision_adjudication import proposal, ruling, snapshot

FIXED = "verified_fixed"
NA = "no_longer_applicable"
PRESENT = "still_present"
INSUFFICIENT = "insufficient_evidence"
CONSTRAINT = {"id": "constraint-v1", "version": 1}


def label(record, conclusion, *, verify=None, correct=None):
    return IndependentLabel(
        (record.finding_id, record.candidate_sha),
        record.kind,
        record.record_id,
        record.sha256,
        conclusion,
        verify,
        correct,
    )


@pytest.fixture
def ledger(tmp_path):
    registered = [(f"finding-{i}", "synthetic:current") for i in range(10)]
    proposals, decisions, labels = [], {}, []
    for i, item in enumerate(registered[:-1]):
        wire_item = dict(zip(("finding_id", "candidate_sha"), item, strict=True))
        snap = snapshot(tmp_path / str(i), wire_item, [("src", "src.py", b"x\n")])
        suggested = FIXED if i in (0, 4, 5, 6) else PRESENT
        raw = proposal(str(i), wire_item, "missing" if i == 4 else "src", suggested, CONSTRAINT)
        proposals.append(raw)
        p = decode_record(raw)
        batch = [raw]
        if i != 7:
            v = validate_proposal(raw, snap, validation_id=f"v-{i}")
            batch.append(v.canonical)
            if i not in (4, 6):
                conclusion = FIXED if i == 0 else INSUFFICIENT if i == 5 else PRESENT
                r = ruling(f"r-{i}", wire_item, raw, v, conclusion)
                batch.append(r)
                if i == 8:
                    batch.append(ruling("conflicting-8", wire_item, raw, v, INSUFFICIENT))
                if i == 0:
                    labels += [label(p, FIXED), label(decode_record(r), FIXED, verify=30)]
                if i == 5:
                    labels.append(label(p, INSUFFICIENT, correct=12))
        decisions[item] = decide_revision(snap, batch)
    return registered, proposals, decisions, labels


def test_hand_computed_ledger(ledger):
    score = score_revision_batch(*ledger)
    assert score.coverage == (4, 10)
    assert score.closure_error_rate == (0, 1)
    assert [(r.proposal_id, r.outcome) for r in score.proposals] == [
        ("p-0", "confirmed_correct"),
        ("p-4", "host_rejected"),
        ("p-5", "human_insufficient"),
        ("p-6", "pending_human"),
    ]
    assert [r.state for r in score.items] == [
        "adjudicated",
        "adjudicated",
        "adjudicated",
        "adjudicated",
        "host_rejected",
        "adjudicated",
        "pending_human",
        "pending_validation",
        "conflict",
        "no_proposal",
    ]
    (close,) = score.closures
    assert (close.ruling_id, close.ruling_digest) == (
        "r-0",
        ledger[2][ledger[0][0]].effective.sha256,
    )
    assert close.review.verify_ms == 30 and close.review.correction_ms is None
    assert score.proposals[2].review.correction_ms == 12
    items, proposals, decisions, labels = ledger
    assert (
        score_revision_batch(
            items[::-1] + items,
            proposals[::-1] + proposals,
            dict(reversed(decisions.items())),
            labels[::-1] + labels,
        )
        == score
    )


def test_unreviewed_and_no_closures_are_not_zero_error_rates(ledger):
    items, proposals, decisions, labels = ledger
    score = score_revision_batch(items, proposals, decisions, labels[:-2])
    assert len(score.closures) == 1
    assert score.closures[0].outcome == "unreviewed"
    assert score.closure_error_rate is None
    selected = items[1:]
    score = score_revision_batch(
        selected, proposals[1:], {k: v for k, v in decisions.items() if k in selected}, labels[2:]
    )
    assert score.closures == ()
    assert score.closure_error_rate is None


def test_wrong_closure_type_is_an_error_and_separately_counted(ledger):
    items, proposals, decisions, labels = ledger
    labels[1] = replace(labels[1], conclusion=NA)
    score = score_revision_batch(items, proposals, decisions, labels)
    assert score.closure_error_rate == (1, 1)
    assert score.closure_type_errors == 1
    assert score.closures[0].outcome == "wrong_closure_type"
    labels[1] = replace(labels[1], conclusion=PRESENT)
    assert score_revision_batch(items, proposals, decisions, labels).closure_type_errors == 0


def test_c4_correction_on_same_candidate_does_not_inherit_old_label(tmp_path):
    item = ("finding", "synthetic:current")
    wire_item = {"finding_id": item[0], "candidate_sha": item[1]}
    snap = snapshot(tmp_path, wire_item, [("src", "src.py", b"x")])
    p = proposal("a", wire_item, "src", FIXED, CONSTRAINT)
    v = validate_proposal(p, snap, validation_id="check")
    first = ruling("first", wire_item, p, v, FIXED)
    new = ruling("new", wire_item, p, v, FIXED, supersedes=("first",))
    decision = decide_revision(snap, [new, p, first, v.canonical])
    stale = label(decode_record(first), FIXED)
    wrong_digest = replace(label(decode_record(new), FIXED), record_digest="0" * 64)
    score = score_revision_batch([item], [p], {item: decision}, [stale, wrong_digest])
    assert score.coverage == (1, 1)
    assert score.closures[0].ruling_id == "new"
    assert score.closure_error_rate is None
    assert set(score.unused_labels) == {stale, wrong_digest}
    exact = label(decode_record(new), FIXED)
    assert score_revision_batch([item], [p], {item: decision}, [exact]).closure_error_rate == (0, 1)


def test_conflicting_label_or_proposal_never_silently_wins(ledger):
    items, proposals, decisions, labels = ledger
    with pytest.raises(InputError, match="label-conflict"):
        score_revision_batch(items, proposals, decisions, [*labels, replace(labels[0], conclusion=NA)])
    with pytest.raises(InputError, match="record-id-conflict"):
        score_revision_batch(
            items, proposals + [proposals[0].replace(b"constraint-v1", b"changed")], decisions, labels
        )


def test_incomplete_or_crosswired_projection_does_not_score_as_success(ledger):
    items, proposals, decisions, labels = ledger
    with pytest.raises(InputError, match="projection-mismatch"):
        score_revision_batch(items, proposals, {**decisions, items[1]: decisions[items[0]]}, labels)
    with pytest.raises(InputError, match="projection-mismatch"):
        score_revision_batch(items, proposals[1:], decisions, labels)


def test_open_proposal_cannot_reuse_host_state_from_replaced_bytes(ledger):
    items, proposals, decisions, labels = ledger
    proposals[6] = proposals[6].replace(b"constraint-v1", b"new-constraint")
    with pytest.raises(InputError, match="projection-mismatch: finding-6"):
        score_revision_batch(items, proposals, decisions, labels)


def test_counts_keep_host_rejection_separate_from_semantic_reviews(ledger):
    items, proposals, decisions, labels = ledger
    wrong = label(decode_record(proposals[6]), PRESENT)
    rejected_with_review = label(decode_record(proposals[4]), FIXED)
    score = score_revision_batch(items, proposals, decisions, [*labels, wrong, rejected_with_review])
    assert score.proposal_counts == {
        "confirmed_correct": 1,
        "host_rejected": 1,
        "human_insufficient": 1,
        "confirmed_wrong": 1,
    }
    assert score.closure_counts == {"confirmed_correct": 1}
    assert score.unused_labels == (rejected_with_review,)
