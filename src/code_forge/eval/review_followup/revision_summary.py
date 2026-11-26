"""Replay saved records into a read-only revision summary. Never execute candidates."""

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import asdict

from .fixtures import InputError, identifier, require, revision
from .revision_adjudication import decide_revision_batch
from .revision_import import import_records
from .revision_scoring import IndependentLabel, Item, score_revision_batch
from .revision_sources import SourcePin, normalize_revision_sources
from .revision_validation import EvidenceSnapshot


def _body(record):
    return json.loads(record.canonical)["body"]


def _item(record):
    return (record.finding_id, record.candidate_sha)


def replay_revision_summary(
    *,
    registered_items: Iterable[Item],
    saved_sources: Iterable[bytes],
    source_pins: Iterable[SourcePin],
    snapshots: Mapping[Item, EvidenceSnapshot],
    raw_records: Iterable[bytes],
    independent_labels: Iterable[IndependentLabel] = (),
) -> dict:
    """Make one offline report for caller-verified inputs.

    The fixture owner supplies snapshots from PR1-verified material and source
    pins from independent provenance. An unbound observation supplies neither.
    A batch with records but no matching snapshot and bound source fails closed,
    rather than producing a successful report from only part of its inputs.
    """
    items = set()
    for item in registered_items:
        require(
            type(item) is tuple and len(item) == 2 and identifier(item[0]) and revision(item[1]),
            "registered_items",
        )
        items.add(item)
    for item, snapshot in snapshots.items():
        require(
            item in items
            and isinstance(snapshot, EvidenceSnapshot)
            and (snapshot.finding_id, snapshot.candidate_sha) == item,
            "summary.snapshot",
        )

    bound = defaultdict(list)
    unbound = []
    for source in normalize_revision_sources(saved_sources, source_pins):
        if source.candidate is None:
            unbound.append(asdict(source))
        else:
            bound[source.candidate].append(asdict(source))
    require(bound.keys() <= {item[1] for item in items}, "summary.source_candidate")

    records = import_records(raw_records)
    indexed = {(r.kind, r.record_id): r for r in records}
    routed_items = {_item(r) for r in records}
    for item in sorted(routed_items):
        if item not in items or item not in snapshots or item[1] not in bound:
            raise InputError("unbound-revision-records", ":".join(item))
    # Use the complete batch for C4's cross-item supersession checks. Pins and
    # reported revision strings never supply a proposal's evidence.
    canonical = tuple(r.canonical for r in records)
    decisions = decide_revision_batch((snapshots[item] for item in routed_items), canonical)
    score = score_revision_batch(
        items, (r.canonical for r in records if r.kind == "proposal"), decisions, independent_labels
    )

    proposal_states = {item: dict(decision.proposal_states) for item, decision in decisions.items()}
    host_rejections = defaultdict(list)
    for decision in decisions.values():
        for rejection in decision.host_rejections:
            host_rejections[rejection.proposal_id].append(
                {
                    "validation_id": rejection.validation_id,
                    "validation_sha256": rejection.validation_sha256,
                    "reason": rejection.reason,
                    "reference_errors": [
                        {"index": index, "error": error} for index, error in rejection.reference_errors
                    ],
                }
            )
    proposal_rows = defaultdict(list)
    for r in records:
        if r.kind != "proposal":
            continue
        item = _item(r)
        body = _body(r)
        proposal_rows[item].append(
            {
                "id": r.record_id,
                "sha256": r.sha256,
                "processing": proposal_states[item][r.record_id],
                "host_rejections": host_rejections[r.record_id],
                "claim": body["claim"],
                "constraint": body["constraint_ref"],
            }
        )
    rows = []
    for result in score.items:
        item = result.item
        decision = decisions.get(item)
        row = {
            "finding_id": item[0],
            "candidate": item[1],
            "state": result.state,
            "conclusion": result.conclusion,
            "adopts_current_basis": result.adopts_current_basis,
            "closed": decision.closed if decision else False,
            "proposals": proposal_rows[item],
            "diagnostics": decision.diagnostics if decision else (),
            "effective": None,
        }
        if decision and decision.effective:
            ruling = _body(decision.effective)
            proposal = _body(indexed["proposal", ruling["proposal_id"]])
            validation = _body(indexed["host_validation", ruling["validation_id"]])
            row["effective"] = {
                "ruling_id": result.ruling_id,
                "ruling_sha256": result.ruling_digest,
                "proposal_id": result.proposal_id,
                "proposal_sha256": result.proposal_digest,
                "validation_id": ruling["validation_id"],
                "validation_sha256": ruling["validation_digest"],
                "claim": proposal["claim"],
                "constraint": proposal["constraint_ref"],
                "adopted_evidence": [
                    validation["reference_checks"][i] for i in ruling["adopted_ref_indexes"]
                ],
                "reason": ruling["reason"],
                "remaining_gaps": ruling["remaining_gaps"],
            }
        rows.append(row)
    return {
        "schema": 1,
        "items": rows,
        "sources_by_candidate": dict(sorted(bound.items())),
        "unbound_sources": unbound,
        "score": {
            **asdict(score),
            "coverage": score.coverage,
            "closure_error_rate": score.closure_error_rate,
            "closure_type_errors": score.closure_type_errors,
            "proposal_counts": score.proposal_counts,
            "closure_counts": score.closure_counts,
        },
    }
