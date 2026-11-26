"""Import a complete immutable record batch without making adjudication decisions."""

from collections.abc import Iterable

from .fixtures import MAX_BYTES, InputError
from .revision_codec import RevisionRecord, decode_record


def import_records(raw_records: Iterable[bytes]) -> tuple[RevisionRecord, ...]:
    """Normalize and deduplicate on (kind, ID); refuse replacements atomically."""
    records: dict[tuple[str, str], RevisionRecord] = {}
    total_bytes = 0
    canonical_bytes = 0
    for raw in raw_records:
        # Each blob consumes input budget, including repeated identical records.
        total_bytes += len(raw)
        if total_bytes > 32 * MAX_BYTES:
            raise InputError("batch-too-large", "revision_records")
        record = decode_record(raw)
        key = (record.kind, record.record_id)
        previous = records.get(key)
        if previous is not None:
            if previous.canonical != record.canonical:
                raise InputError("record-id-conflict", ":".join(key))
            continue
        canonical_bytes += len(record.canonical)
        if canonical_bytes > 32 * MAX_BYTES:
            raise InputError("canonical-batch-too-large", "revision_records")
        records[key] = record
    return tuple(records[key] for key in sorted(records))
