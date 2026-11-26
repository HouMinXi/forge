"""Canonical identity of an offline revision record, not a ruling admission check."""

import json
from dataclasses import dataclass

from .fixtures import (
    MAX_BYTES,
    InputError,
    digest,
    fields,
    identifier,
    parse,
    require,
    revision,
)

_BODY_FIELDS = {
    "proposal": "claim constraint_ref suggested_status evidence_refs supersedes_proposal_id",
    "host_validation": ("proposal_id proposal_digest contract_version result reason reference_checks"),
    "human_ruling": (
        "proposal_id proposal_digest validation_id validation_digest adopted_ref_indexes "
        "reviewer_id conclusion reason remaining_gaps supersedes_ruling_ids"
    ),
}
_MAX_DEPTH = 32
_MAX_INTEGER = (1 << 53) - 1


@dataclass(frozen=True, slots=True)
class RevisionRecord:
    """Immutable canonical bytes and their routing metadata; not trusted evidence."""

    kind: str
    record_id: str
    finding_id: str
    candidate_sha: str
    canonical: bytes
    sha256: str


def _json_domain(value, depth, field):
    if not depth <= _MAX_DEPTH:
        raise InputError("record-too-deep", field)
    if type(value) is dict:
        for key, member in value.items():
            _json_domain(key, depth + 1, field)
            _json_domain(member, depth + 1, field)
    elif type(value) is list:
        for member in value:
            _json_domain(member, depth + 1, field)
    elif type(value) is str:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise InputError("invalid-unicode-scalar", field) from exc
    elif type(value) is int:
        require(-_MAX_INTEGER <= value <= _MAX_INTEGER, field)
    else:
        require(value is None or type(value) is bool, field)


def decode_record(raw: bytes) -> RevisionRecord:
    """Parse UTF-8, validate envelope shape, and derive a content identity."""
    if len(raw) > MAX_BYTES:
        raise InputError("file-too-large", "revision_record")
    record = parse(raw, "revision_record")
    fields(record, "schema kind id item body", "revision_record")
    require(type(record["schema"]) is int and record["schema"] == 1, "revision_record.schema")
    kind = record["kind"]
    require(type(kind) is str and kind in _BODY_FIELDS, "revision_record.kind")
    require(identifier(record["id"]), "revision_record.id")
    item = record["item"]
    fields(item, "finding_id candidate_sha", "revision_record.item")
    require(identifier(item["finding_id"]), "revision_record.item.finding_id")
    require(revision(item["candidate_sha"]), "revision_record.item.candidate_sha")
    fields(record["body"], _BODY_FIELDS[kind], "revision_record.body")
    _json_domain(record, 1, "revision_record")
    canonical = json.dumps(
        record, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")
    if len(canonical) > MAX_BYTES:
        raise InputError("canonical-record-too-large", "revision_record")
    return RevisionRecord(
        kind, record["id"], item["finding_id"], item["candidate_sha"], canonical, digest(canonical)
    )
