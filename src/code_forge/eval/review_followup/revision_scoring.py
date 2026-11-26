"""Score offline C4 decisions without changing which ruling takes effect."""

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .fixtures import InputError, enum, identifier, require, revision, sha
from .revision_adjudication import RevisionDecision
from .revision_import import import_records

type Item = tuple[str, str]

_CLOSE = ("verified_fixed", "no_longer_applicable")
_DETERMINATE = ("still_present", *_CLOSE)
_CONCLUSIONS = (*_DETERMINATE, "insufficient_evidence")
_PROPOSAL_STATES = ("pending_validation", "host_rejected", "pending_human")
_OPEN_STATES = (*_PROPOSAL_STATES, "graph_error", "conflict", "pending", "no_proposal")


@dataclass(frozen=True, slots=True)
class IndependentLabel:
    """One reconciled independent conclusion for an exact proposal or ruling."""

    item: Item
    record_kind: str
    record_id: str
    record_digest: str
    conclusion: str
    verify_ms: int | None = None
    correction_ms: int | None = None


@dataclass(frozen=True, slots=True)
class ProposalScore:
    item: Item
    proposal_id: str
    proposal_digest: str
    suggested_status: str
    host_state: str
    outcome: str
    review: IndependentLabel | None


@dataclass(frozen=True, slots=True)
class ItemScore:
    item: Item
    state: str
    conclusion: str | None = None
    ruling_id: str | None = None
    ruling_digest: str | None = None
    proposal_id: str | None = None
    proposal_digest: str | None = None


@dataclass(frozen=True, slots=True)
class ClosureScore:
    item: Item
    ruling_id: str
    ruling_digest: str
    outcome: str
    review: IndependentLabel | None


@dataclass(frozen=True, slots=True)
class RevisionScorecard:
    proposals: tuple[ProposalScore, ...]
    items: tuple[ItemScore, ...]
    closures: tuple[ClosureScore, ...]
    unused_labels: tuple[IndependentLabel, ...]

    @property
    def proposal_counts(self) -> dict[str, int]:
        return dict(Counter(row.outcome for row in self.proposals))

    @property
    def closure_counts(self) -> dict[str, int]:
        return dict(Counter(row.outcome for row in self.closures))

    @property
    def coverage(self) -> tuple[int, int]:
        return sum(row.conclusion in _DETERMINATE for row in self.items), len(self.items)

    @property
    def closure_type_errors(self) -> int:
        return sum(row.outcome == "wrong_closure_type" for row in self.closures)

    @property
    def closure_error_rate(self) -> tuple[int, int] | None:
        reviewed = [row for row in self.closures if row.review is not None]
        if not reviewed:
            return None
        return sum(row.outcome != "confirmed_correct" for row in reviewed), len(reviewed)


def _valid_item(item):
    return type(item) is tuple and len(item) == 2 and identifier(item[0]) and revision(item[1])


def _label_key(label):
    return label.item, label.record_kind, label.record_id, label.record_digest


def _index_labels(labels, items):
    index = {}
    for label in labels:
        require(
            isinstance(label, IndependentLabel)
            and _valid_item(label.item)
            and label.item in items
            and enum(label.record_kind, "proposal", "human_ruling")
            and identifier(label.record_id)
            and sha(label.record_digest)
            and enum(label.conclusion, *_CONCLUSIONS)
            and all(
                v is None or (type(v) is int and v >= 0) for v in (label.verify_ms, label.correction_ms)
            ),
            "independent_labels",
        )
        key = _label_key(label)
        previous = index.setdefault(key, label)
        if previous != label:
            raise InputError("label-conflict", label.record_id)
    return index


def _consume_projection(item, decision, proposals):
    """Check routing consistency, not eligibility or the supersession graph."""
    if decision is None:
        if proposals:
            raise InputError("projection-mismatch", item[0])
        return ItemScore(item, "no_proposal"), {}
    states = dict(decision.proposal_states)
    if (
        decision.item != item
        or decision.proposal_digests != tuple((key, p.sha256) for key, p in proposals.items())
        or states.keys() != proposals.keys()
        or len(states) != len(decision.proposal_states)
        or any(s not in _PROPOSAL_STATES for s in states.values())
    ):
        raise InputError("projection-mismatch", item[0])
    r = decision.effective
    if decision.state == "adjudicated" and r is not None and r.kind == "human_ruling":
        body = json.loads(r.canonical)["body"]
        p = proposals.get(body["proposal_id"])
        if (
            (r.finding_id, r.candidate_sha) == item
            and decision.heads == (r.record_id,)
            and enum(decision.conclusion, *_CONCLUSIONS)
            and body["conclusion"] == decision.conclusion
            and p is not None
            and p.sha256 == body["proposal_digest"]
            and states[p.record_id] == "pending_human"
        ):
            return ItemScore(
                item, decision.state, decision.conclusion, r.record_id, r.sha256, p.record_id, p.sha256
            ), states
    elif (
        r is None
        and decision.conclusion is None
        and decision.state in _OPEN_STATES
        and (decision.state != "no_proposal" or not proposals)
    ):
        return ItemScore(item, decision.state), states
    raise InputError("projection-mismatch", item[0])


def _proposal_outcome(host_state, suggested, review):
    if host_state != "pending_human":
        return host_state
    if review is None:
        return "pending_human"
    if review.conclusion == "insufficient_evidence":
        return "human_insufficient"
    return "confirmed_correct" if review.conclusion == suggested else "confirmed_wrong"


def _closure_outcome(conclusion, review):
    if review is None:
        return "unreviewed"
    if review.conclusion == conclusion:
        return "confirmed_correct"
    if review.conclusion in _CLOSE:
        return "wrong_closure_type"
    return "confirmed_wrong"


def score_revision_batch(
    registered_items: Iterable[Item],
    proposals: Iterable[bytes],
    projections: Mapping[Item, RevisionDecision],
    independent_labels: Iterable[IndependentLabel],
) -> RevisionScorecard:
    """Score one complete preregistered batch; projections come from C4.

    Missing projections are allowed only for items without a proposal. Independent
    labels bind to content, not just a display ID. Unused labels stay visible but
    do not enter any scored denominator. No source is read and no decision is made.
    """
    by_item = {}
    for item in registered_items:
        require(_valid_item(item), "registered_items")
        by_item.setdefault(item, {})
    require(
        all(
            _valid_item(k) and k in by_item and isinstance(v, RevisionDecision)
            for k, v in projections.items()
        ),
        "projections",
    )
    for record in import_records(proposals):
        item = (record.finding_id, record.candidate_sha)
        require(record.kind == "proposal" and item in by_item, "proposals")
        by_item[item][record.record_id] = record
    labels = _index_labels(independent_labels, by_item)
    proposal_rows, item_rows, closure_rows = [], [], []
    for item, rows in sorted(by_item.items()):
        result, states = _consume_projection(item, projections.get(item), rows)
        item_rows.append(result)
        for p in rows.values():
            suggested = json.loads(p.canonical)["body"]["suggested_status"]
            if not enum(suggested, *_CLOSE):
                continue
            host_state = states[p.record_id]
            review = None
            if host_state == "pending_human":
                review = labels.pop((item, "proposal", p.record_id, p.sha256), None)
            proposal_rows.append(
                ProposalScore(
                    item,
                    p.record_id,
                    p.sha256,
                    suggested,
                    host_state,
                    _proposal_outcome(host_state, suggested, review),
                    review,
                )
            )
        if result.conclusion in _CLOSE:
            key = (item, "human_ruling", result.ruling_id, result.ruling_digest)
            review = labels.pop(key, None)
            closure_rows.append(
                ClosureScore(
                    item,
                    result.ruling_id,
                    result.ruling_digest,
                    _closure_outcome(result.conclusion, review),
                    review,
                )
            )
    return RevisionScorecard(
        tuple(proposal_rows),
        tuple(item_rows),
        tuple(closure_rows),
        tuple(labels[k] for k in sorted(labels)),
    )
