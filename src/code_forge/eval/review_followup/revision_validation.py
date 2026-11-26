"""Offline provenance and selector checks; never decide evidence adequacy."""

import json
from dataclasses import dataclass

from .fixtures import InputError, digest, enum, fields, identifier, parse, require
from .models import ReviewInput, distinct_ids, nonempty, version
from .revision_codec import RevisionRecord, decode_record

_CONCLUSIONS = ("still_present", "verified_fixed", "no_longer_applicable", "insufficient_evidence")
_CLOSURE = frozenset(("verified_fixed", "no_longer_applicable"))
# One host binding carries more bytes than its proposal reference.
_MAX_EVIDENCE_REFS = 1024


@dataclass(frozen=True, slots=True)
class _Evidence:
    artifact_id: str
    role: str
    revision: str
    sha256: str
    length: int
    session_id: str | None = None
    call_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    finding_id: str
    candidate_sha: str
    entries: tuple[_Evidence, ...]


def evidence_snapshot(review: ReviewInput, finding_id: str, saved_observations=()) -> EvidenceSnapshot:
    """Adapt a validated PR1 input and separately pinned, case-owned saved observations.

    The caller must obtain the review with validate() and the observations from
    verified local artifacts, independent of the proposal. This does not reopen
    paths, trust proposal metadata, or infer calls from a PR1 review run.
    """
    require(review.task == "revision_followup" and finding_id in review.prior_findings, "snapshot.item")
    entries = {
        key: _Evidence(key, f"{role}_source", rev, checksum, len(raw))
        for key, role, rev, checksum, raw in review.evidence
    }
    for key, artifact in saved_observations:
        field = "snapshot.observation"
        require(identifier(key) and key not in entries, field)
        require(
            artifact.path.startswith(f"saved_observations/{review.case}/")
            and artifact.sha256 == digest(artifact.raw),
            field,
        )
        data = parse(artifact.raw, field)
        fields(data, "schema case input_sha256 candidate session call_ids", field)
        version(data, field)
        require(
            data["case"] == review.case
            and data["input_sha256"] == review.sha256
            and data["candidate"] == review.candidate
            and identifier(data["session"]),
            field,
        )
        entries[key] = _Evidence(
            key,
            "candidate_observation",
            review.candidate,
            artifact.sha256,
            len(artifact.raw),
            data["session"],
            distinct_ids(data["call_ids"], field),
        )
    return EvidenceSnapshot(finding_id, review.candidate, tuple(entries[key] for key in sorted(entries)))


def _selector_shape(selector, field):
    require(type(selector) is dict, field)
    kind = selector.get("kind")
    if kind == "source_bytes":
        fields(selector, "kind start end", field)
        require(
            type(selector["start"]) is int
            and type(selector["end"]) is int
            and 0 <= selector["start"] < selector["end"],
            field,
        )
    elif kind == "call":
        fields(selector, "kind session_id call_id", field)
        require(identifier(selector["session_id"]) and identifier(selector["call_id"]), field)
    else:
        fields(selector, "kind session_id", field)
        require(kind == "session" and identifier(selector["session_id"]), field)


def _reference_error(ref, entry, candidate):
    if entry is None:
        return "artifact-not-in-scope"
    purpose = ref["purpose"]
    if purpose == "current_basis" and (
        entry.revision != candidate or entry.role not in ("candidate_source", "candidate_observation")
    ):
        return "not-current-basis"
    if purpose == "historical_context" and (entry.revision == candidate or entry.role != "base_source"):
        return "not-historical-context"
    selector = ref["selector"]
    kind = selector["kind"]
    if kind == "source_bytes":
        if entry.role not in ("base_source", "candidate_source"):
            return "selector-role-mismatch"
        if selector["end"] > entry.length:
            return "source-span-out-of-range"
    else:
        if entry.role != "candidate_observation":
            return "selector-role-mismatch"
        if selector["session_id"] != entry.session_id:
            return "session-not-observed"
        if kind == "call" and selector["call_id"] not in entry.call_ids:
            return "call-not-observed"
    return None


def validate_proposal(raw: bytes, snapshot: EvidenceSnapshot, *, validation_id: str) -> RevisionRecord:
    """Produce an immutable host record with one ordered check per proposal reference."""
    proposal = decode_record(raw)
    require(proposal.kind == "proposal", "proposal.kind")
    require(
        (proposal.finding_id, proposal.candidate_sha) == (snapshot.finding_id, snapshot.candidate_sha),
        "proposal.item",
    )
    envelope = json.loads(proposal.canonical)
    body = envelope["body"]
    fields(body["constraint_ref"], "id version", "proposal.constraint_ref")
    require(
        nonempty(body["claim"])
        and identifier(body["constraint_ref"]["id"])
        and type(body["constraint_ref"]["version"]) is int
        and body["constraint_ref"]["version"] > 0
        and enum(body["suggested_status"], *_CONCLUSIONS)
        and (body["supersedes_proposal_id"] is None or identifier(body["supersedes_proposal_id"]))
        and type(body["evidence_refs"]) is list,
        "proposal.body",
    )
    if len(body["evidence_refs"]) > _MAX_EVIDENCE_REFS:
        raise InputError("too-many-evidence-refs", "proposal.evidence_refs")
    entries = {entry.artifact_id: entry for entry in snapshot.entries}
    current_basis = False
    checks = []
    for index, ref in enumerate(body["evidence_refs"]):
        field = f"proposal.evidence_refs/{index}"
        fields(ref, "artifact_id purpose selector", field)
        require(
            identifier(ref["artifact_id"])
            and enum(ref["purpose"], "historical_context", "current_basis"),
            field,
        )
        _selector_shape(ref["selector"], f"{field}/selector")
        entry = entries.get(ref["artifact_id"])
        error = _reference_error(ref, entry, snapshot.candidate_sha)
        binding = None
        if error is None:
            binding = {
                "artifact_id": entry.artifact_id,
                "artifact_sha256": entry.sha256,
                "revision": entry.revision,
                "role": entry.role,
                "selector": ref["selector"],
            }
            current_basis |= ref["purpose"] == "current_basis"
        checks.append({"index": index, "purpose": ref["purpose"], "error": error, "binding": binding})

    reason = None
    if body["suggested_status"] in _CLOSURE and not current_basis:
        reason = "closure-without-current-basis"
    output = {
        "schema": 1,
        "kind": "host_validation",
        "id": validation_id,
        "item": envelope["item"],
        "body": {
            "proposal_id": proposal.record_id,
            "proposal_digest": proposal.sha256,
            "contract_version": 1,
            "result": "rejected" if reason or any(check["error"] for check in checks) else "passed",
            "reason": reason,
            "reference_checks": checks,
        },
    }
    return decode_record(json.dumps(output, ensure_ascii=True, separators=(",", ":")).encode("ascii"))
