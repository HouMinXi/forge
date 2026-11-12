"""Private counts-only scope receipt for current-execution consumption.

The independent envelope and bounded storage do not extend diagnostic v1.
Expected identity must be independently held by the current host.
"""

from dataclasses import dataclass, fields
import json
from . import _advisory_diagnostics as storage

_NAMESPACE = "query-scope-receipts"
_ENVELOPE = "forge-cross-repo-query-scope-v1"
MAX_OCCURRENCES = 256  # Matches the existing capture entry budget; not an analysis limit.


@dataclass(frozen=True, slots=True)
class ScopeSnapshot:
    scope: str = "not_reached"
    registered: int | None = None
    attempted: int | None = None
    completed: int | None = None
    unavailable: int | None = None
    with_callers: int | None = None
    query_occurrences_accounted: bool = False


_FIELDS = frozenset(f.name for f in fields(ScopeSnapshot))
_CONTEXT = frozenset(
    {"execution_id", "repository_id", "source_hash", "diff_sha256", "observation_sequence"}
)


def _invalid():
    raise storage.DiagnosticError("invalid_schema")


def freeze(snapshot):
    """Copy only the supported private snapshot shape, never arbitrary metadata."""
    if type(snapshot) is dict:
        if snapshot.keys() != _FIELDS:
            _invalid()
        values = dict(snapshot)
    else:
        if type(snapshot) is not ScopeSnapshot:
            _invalid()
        values = {name: getattr(snapshot, name) for name in _FIELDS}
    result = ScopeSnapshot(**values)
    if type(result.scope) is not str or result.scope not in {
        "not_reached",
        "unavailable",
        "established",
    }:
        _invalid()
    if type(result.query_occurrences_accounted) is not bool:
        _invalid()
    counts = (
        result.registered,
        result.attempted,
        result.completed,
        result.unavailable,
        result.with_callers,
    )
    if result.scope != "established":
        if any(value is not None for value in counts) or result.query_occurrences_accounted:
            _invalid()
    else:
        if any(type(value) is not int or not 0 <= value <= MAX_OCCURRENCES for value in counts):
            _invalid()
        total, attempted, completed, unavailable, callers = counts
        if not callers <= completed <= attempted <= total or completed + unavailable > total:
            _invalid()
        if result.query_occurrences_accounted is not (completed + unavailable == total):
            _invalid()
    return result


def encode(expected, snapshot):
    storage._validate_context(expected)
    snapshot = freeze(snapshot)
    document = {
        "envelope": _ENVELOPE,
        "context": {name: getattr(expected, name) for name in _CONTEXT},
        "snapshot": {name: getattr(snapshot, name) for name in _FIELDS},
    }
    raw = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    if len(raw) > storage.MAX_BYTES:
        _invalid()
    return raw


def _bounded_integer(value: str) -> int:
    # This envelope admits occurrence counts through 256. Bound conversion
    # independently of Python's configurable integer-string limit; field
    # validation still enforces count <= 256 and context sequence <= 64.
    if len(value) > 3:
        _invalid()
    return int(value)


def decode(raw, expected):
    storage._validate_context(expected)
    if type(raw) is not bytes or len(raw) > storage.MAX_BYTES:
        _invalid()
    try:
        text = raw.decode("utf-8", errors="strict")
        storage._scan_depth(text)
        document = json.loads(
            text,
            object_pairs_hook=storage._pairs,
            parse_constant=storage._reject_number,
            parse_float=storage._reject_number,
            parse_int=_bounded_integer,
        )
    except (UnicodeError, ValueError, RecursionError):
        _invalid()
    if type(document) is not dict or document.keys() != {"envelope", "context", "snapshot"}:
        _invalid()
    if (
        document["envelope"] != _ENVELOPE
        or type(document["context"]) is not dict
        or document["context"].keys() != _CONTEXT
    ):
        _invalid()
    context = storage.ExpectedContext(**document["context"])
    storage._validate_context(context)
    if context != expected:
        raise storage.DiagnosticError("context_mismatch")
    if type(document["snapshot"]) is not dict:
        _invalid()
    return freeze(document["snapshot"])


def publish(binding, expected, snapshot):
    """Publish once under a separate namespace; no new sequence allocation."""
    return storage._publish_record(
        binding, lambda: expected, lambda: encode(expected, snapshot), _NAMESPACE
    )


def validate_publication(publication, expected):
    """Bound the scope-specific callback result before retaining any fields.

    Shape validation is not publication proof: the strict reader must still
    verify retained filesystem identities and bytes against independent context.
    """
    storage._validate_publication_namespace(publication, expected, _NAMESPACE)


def read(publication, expected):
    """Strictly consume the host-held locator and independently held identity."""
    try:
        validate_publication(publication, expected)
        return decode(storage._read_publication_bytes(publication), expected)
    except Exception as exc:  # noqa: BLE001 -- Expose bounded diagnostic failures, never raw storage errors.
        raise storage.DiagnosticError(storage._reason(exc, "read_failed")) from None
