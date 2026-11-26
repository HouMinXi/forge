"""Build a deterministic two-arm context preview without running a reviewer."""

import json
from dataclasses import asdict

from .context_models import RELATIONSHIPS, context_input
from .fixtures import InputError, digest, parse, read_file, require
from .replay import validate

_METHOD = "compact-sorted-json-utf8-v1"


def _payload(required, optional):
    return {"required": required, "optional": optional}


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _order(candidate):
    return (
        RELATIONSHIPS.index(candidate.relationship),
        candidate.artifact or "",
        candidate.start if candidate.start is not None else -1,
        candidate.end if candidate.end is not None else -1,
        candidate.id,
    )


def _materialize(review, candidates):
    artifacts = {name: (role, rev, checksum, raw) for name, role, rev, checksum, raw in review.evidence}
    members = {group: set(ids) for group, ids in review.groups}
    for candidate in candidates:
        if candidate.artifact is None:
            continue
        at = f"{review.case}/context/{candidate.id}"
        require(candidate.artifact in members[candidate.group], f"{at}/artifact")
        role, revision, checksum, raw = artifacts[candidate.artifact]
        require(role == "candidate" and revision == candidate.revision, f"{at}/revision")
        require(candidate.end <= len(raw), f"{at}/span")
        try:
            raw[candidate.start : candidate.end].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InputError("invalid-span-encoding", f"{at}/span") from exc
        yield candidate.id, (checksum, raw)


def _pack(required, order, rendered, limit, field):
    optional = []
    decisions = {}
    selected = set()
    size = len(_encoded(_payload(required, [])))
    if size > limit:
        raise InputError("required-over-budget", field)
    for candidate in order:
        source = rendered.get(candidate.id)
        if source is None:
            decision = "missing"
        elif candidate.span in selected:
            decision = "duplicate"
        else:
            checksum, raw = source
            item = {
                "artifact": candidate.artifact,
                "sha256": checksum,
                "start": candidate.start,
                "end": candidate.end,
                "text": raw[candidate.start : candidate.end].decode("utf-8"),
            }
            cost = len(_encoded(item)) + bool(optional)  # Array separator when not first.
            if size + cost <= limit:
                optional.append(item)
                selected.add(candidate.span)
                size += cost
                decision = "selected"
            else:
                decision = "over_budget"
        decisions[candidate.id] = decision
    wire = _encoded(_payload(required, optional))
    return {"optional": optional, "bytes": size, "payload_sha256": digest(wire)}, decisions


def plan_context(review, value):
    """Compare ordered baseline selections with stable targeted selections."""
    groups, candidates, baseline = context_input(value, review)
    ordered = sorted(candidates.values(), key=_order)
    rendered = dict(_materialize(review, ordered))
    normalized_input = {**value, "candidates": [asdict(row) for row in ordered]}
    previews = []
    for group, members in review.groups:
        rule = groups[group]
        required = {
            "group": group,
            "members": list(members),
            "diff": rule["diff"],
            "contract": rule["contract"],
            "lenses": dict(rule["lenses"]),
        }
        limit = rule["limit_bytes"]
        baseline_ids = baseline[group]
        target = [row for row in ordered if row.group == group]
        at = f"{review.case}/context/groups/{group}"
        a, a_decisions = _pack(
            required, (candidates[item] for item in baseline_ids), rendered, limit, at
        )
        b, b_decisions = _pack(required, target, rendered, limit, at)
        b_spans = {row.span for row in target if b_decisions[row.id] == "selected"}
        previews.append(
            {
                "required": required,
                "limit_bytes": limit,
                "baseline_order": baseline_ids,
                "baseline": a,
                "targeted": b,
                "truncated": [],  # Whole spans only; an oversized span is never shortened.
                "candidates": [
                    {
                        **asdict(row),
                        "baseline": a_decisions.get(row.id, "not_in_baseline"),
                        "targeted": b_decisions[row.id],
                        "displaced": a_decisions.get(row.id) == "selected" and row.span not in b_spans,
                    }
                    for row in target
                ],
            }
        )
    return {
        "schema": 1,
        "measurement": _METHOD,
        "case": review.case,
        "candidate": review.candidate,
        "review_input_sha256": review.sha256,
        "context_input_sha256": digest(_encoded(normalized_input)),
        "groups": previews,
    }


def preview(root, case):
    """Use only PR1's review input tree and the named case's frozen context request."""
    review = next((review for review in validate(root) if review.case == case), None)
    if review is None:
        raise InputError("unknown-case", "case")
    path = f"inputs/{review.case}/context.json"
    value = parse(read_file(root, path, "context"), "context")
    return plan_context(review, value)
