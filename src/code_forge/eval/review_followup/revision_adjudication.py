"""Replay human rulings against one active evidence snapshot. No state writes."""

import json
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass

from .fixtures import InputError, enum, identifier, sha
from .models import nonempty
from .revision_codec import RevisionRecord
from .revision_import import import_records
from .revision_validation import EvidenceSnapshot, validate_proposal

_CONCLUSIONS = frozenset(
    ("still_present", "verified_fixed", "no_longer_applicable", "insufficient_evidence")
)
_CLOSURES = frozenset(("verified_fixed", "no_longer_applicable"))


@dataclass(frozen=True, slots=True)
class RevisionDecision:
    """Read-only item status and the exact effective human record, if any."""

    state: str
    proposal_states: tuple[tuple[str, str], ...]
    diagnostics: tuple[tuple[str, str, str], ...]
    heads: tuple[str, ...] = ()
    effective: RevisionRecord | None = None
    conclusion: str | None = None
    item: tuple[str, str] | None = None
    proposal_digests: tuple[tuple[str, str], ...] = ()
    proposal_graph_rejected: tuple[str, ...] = ()
    adopts_current_basis: bool = False

    @property
    def closed(self) -> bool:
        return self.conclusion in _CLOSURES


def _item(record):
    return (record.finding_id, record.candidate_sha)


def _body(record):
    return json.loads(record.canonical)["body"]


def _body_bytes(body):
    # Compare wire types, not Python equality (e.g. True == 1).
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _ruling_shape(body):
    indexes = body["adopted_ref_indexes"]
    gaps = body["remaining_gaps"]
    return (
        identifier(body["proposal_id"])
        and sha(body["proposal_digest"])
        and identifier(body["validation_id"])
        and sha(body["validation_digest"])
        and type(indexes) is list
        and all(type(i) is int and i >= 0 for i in indexes)
        and len(indexes) == len(set(indexes))
        and identifier(body["reviewer_id"])
        and enum(body["conclusion"], *_CONCLUSIONS)
        and nonempty(body["reason"])
        and type(gaps) is list
        and all(map(nonempty, gaps))
    )


def _graph_errors(kind, edges, records, item):
    errors = []
    for key, targets in edges.items():
        for target in targets:
            if target == key:
                errors.append((kind, key, "supersession-self"))
            elif target not in records:
                errors.append((kind, key, "supersession-missing"))
            elif _item(records[target]) != item:
                errors.append((kind, key, "supersession-cross-item"))
    indegree = dict.fromkeys(edges, 0)
    for targets in edges.values():
        for target in targets:
            if target in indegree:
                indegree[target] += 1
    ready = deque(key for key, count in indegree.items() if count == 0)
    visited = 0
    while ready:
        visited += 1
        for target in edges[ready.popleft()]:
            if target in indegree:
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
    if kind == "proposal":
        # A proposal has at most one outgoing edge. Peeling zero-indegree
        # vertices leaves exactly the cycle members, not proposals feeding
        # into a cycle. An independent bad edge must not hide a cycle.
        errors.extend(
            (kind, key, "supersession-cycle")
            for key, count in indegree.items()
            if count and edges[key] != (key,)  # Self-links have their own diagnostic.
        )
    elif visited != len(indegree) and not errors:
        errors.append((kind, item[0], "supersession-cycle"))
    return errors


def _validation_states(validations, proposals, snapshot, diagnostics):
    """Retain only host records reproduced against the active snapshot."""
    passed = {}
    states = dict.fromkeys(proposals, "pending_validation")
    recomputed = {}
    for record in validations.values():
        body = _body(record)
        key = body["proposal_id"]
        if (
            not identifier(key)
            or key not in proposals
            or body["proposal_digest"] != proposals[key].sha256
        ):
            diagnostics.append(("host_validation", record.record_id, "proposal-binding-mismatch"))
            continue
        if key not in recomputed:
            try:
                check = validate_proposal(proposals[key].canonical, snapshot, validation_id="replay")
                recomputed[key] = _body_bytes(_body(check))
            except InputError:
                recomputed[key] = None
        if recomputed[key] != _body_bytes(body):
            diagnostics.append(("host_validation", record.record_id, "inactive-or-invalid-validation"))
        elif body["result"] == "passed":
            passed[record.record_id] = (record, key, body["reference_checks"])
            states[key] = "pending_human"
        elif states[key] == "pending_validation":
            states[key] = "host_rejected"
    return passed, states


def _adopts_current_basis(body, checks):
    return any(checks[i]["purpose"] == "current_basis" for i in body["adopted_ref_indexes"])


def _ruling_refusal(body, proposals, validations):
    key = body["proposal_id"]
    if key not in proposals or proposals[key].sha256 != body["proposal_digest"]:
        return "proposal-binding-mismatch"
    active = validations.get(body["validation_id"])
    if active is None or active[0].sha256 != body["validation_digest"]:
        return "validation-binding-mismatch"
    if active[1] != key:
        return "validation-binding-mismatch"
    checks = active[2]
    indexes = body["adopted_ref_indexes"]
    if any(i >= len(checks) or checks[i]["binding"] is None for i in indexes):
        return "invalid-adopted-reference"
    if body["conclusion"] in _CLOSURES and not _adopts_current_basis(body, checks):
        return "closure-without-adopted-current-basis"
    return None


def decide_revision(snapshot: EvidenceSnapshot, raw_records: Iterable[bytes]) -> RevisionDecision:
    """Resolve one item from a complete offline batch; preserve unresolved statuses."""
    item = (snapshot.finding_id, snapshot.candidate_sha)
    return decide_revision_batch((snapshot,), raw_records)[item]


def decide_revision_batch(
    snapshots: Iterable[EvidenceSnapshot], raw_records: Iterable[bytes]
) -> dict[tuple[str, str], RevisionDecision]:
    """Index a complete offline batch once, preserving cross-item graph checks."""
    kinds = ("proposal", "host_validation", "human_ruling")
    by_item = {}
    for snapshot in snapshots:
        item = (snapshot.finding_id, snapshot.candidate_sha)
        if item in by_item:
            raise InputError("duplicate-snapshot", item[0])
        by_item[item] = (snapshot, {kind: {} for kind in kinds})
    all_graph_records = {"proposal": {}, "human_ruling": {}}
    for record in import_records(raw_records):
        if record.kind in all_graph_records:
            all_graph_records[record.kind][record.record_id] = record
        match = by_item.get(_item(record))
        if match is not None:
            match[1][record.kind][record.record_id] = record
    return {
        item: _decide_indexed_revision(snapshot, scoped, all_graph_records)
        for item, (snapshot, scoped) in sorted(by_item.items())
    }


def _decide_indexed_revision(snapshot, scoped, all_graph_records):
    item = (snapshot.finding_id, snapshot.candidate_sha)
    diagnostics = []
    proposals = scoped["proposal"]
    active, states = _validation_states(scoped["host_validation"], proposals, snapshot, diagnostics)
    eligible = {}
    edges = {}
    ruling_errors = []
    for key, record in scoped["human_ruling"].items():
        body = _body(record)
        targets = body["supersedes_ruling_ids"]
        valid_links = (
            type(targets) is list and all(map(identifier, targets)) and len(targets) == len(set(targets))
        )
        if valid_links:
            edges[key] = targets
        else:
            ruling_errors.append(("human_ruling", key, "invalid-supersession-reference"))
        if not valid_links or not _ruling_shape(body):
            diagnostics.append(("human_ruling", key, "invalid-ruling"))
            continue
        refusal = _ruling_refusal(body, proposals, active)
        if refusal is not None:
            diagnostics.append(("human_ruling", key, refusal))
            continue
        eligible[key] = (record, body)

    proposal_edges = {}
    proposal_errors = []
    for key, record in proposals.items():
        target = _body(record)["supersedes_proposal_id"]
        if target is None:
            proposal_edges[key] = ()
        elif identifier(target):
            proposal_edges[key] = (target,)
        else:
            proposal_errors.append(("proposal", key, "invalid-supersession-reference"))

    proposal_errors += _graph_errors("proposal", proposal_edges, all_graph_records["proposal"], item)
    errors = (
        proposal_errors
        + ruling_errors
        + _graph_errors("human_ruling", edges, all_graph_records["human_ruling"], item)
    )
    args = (tuple(sorted(states.items())), tuple(sorted(set(diagnostics + errors))))
    binding = {
        "item": item,
        "proposal_graph_rejected": tuple(sorted({key for _, key, _ in proposal_errors})),
        "proposal_digests": tuple(sorted((key, record.sha256) for key, record in proposals.items())),
    }
    if errors:
        return RevisionDecision("graph_error", *args, **binding)
    replaced = {target for key in eligible for target in edges[key] if target in eligible}
    heads = tuple(sorted(eligible.keys() - replaced))
    if len(heads) > 1:
        return RevisionDecision("conflict", *args, heads, **binding)
    if heads:
        record, body = eligible[heads[0]]
        checks = active[body["validation_id"]][2]
        return RevisionDecision(
            "adjudicated",
            *args,
            heads,
            record,
            body["conclusion"],
            adopts_current_basis=_adopts_current_basis(body, checks),
            **binding,
        )
    statuses = set(states.values())
    state = next(iter(statuses)) if len(statuses) == 1 else "pending" if statuses else "no_proposal"
    return RevisionDecision(state, *args, **binding)
