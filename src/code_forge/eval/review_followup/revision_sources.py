"""Normalize saved source metadata; only independently pinned bytes can bind."""

from collections.abc import Iterable
from dataclasses import dataclass

from .fixtures import (
    MAX_BYTES,
    InputError,
    digest,
    enum,
    fields,
    identifier,
    parse,
    require,
    revision,
    sha,
)
from .models import version


@dataclass(frozen=True, slots=True)
class SourcePin:
    """Caller-verified association of one saved artifact with a frozen candidate.

    A pin is supplied by the owner of the verified fixture catalog, not
    extracted from a mail thread, provider record, or proposal.
    """

    provider: str
    source_id: str
    sha256: str
    candidate: str


@dataclass(frozen=True, slots=True)
class SavedRevisionSource:
    provider: str
    source_id: str
    sha256: str
    reported_revision: str | None
    reported_fixed: bool | None
    candidate: str | None


def normalize_revision_sources(
    sources: Iterable[bytes], pins: Iterable[SourcePin]
) -> tuple[SavedRevisionSource, ...]:
    """Join exact saved bytes to independent pins; retain unbound provenance."""
    by_key = {}
    for pin in pins:
        require(
            isinstance(pin, SourcePin)
            and enum(pin.provider, "github", "mail")
            and identifier(pin.source_id)
            and sha(pin.sha256)
            and revision(pin.candidate),
            "source_pin",
        )
        key = (pin.provider, pin.source_id)
        if by_key.setdefault(key, pin) != pin:
            raise InputError("source-pin-conflict", pin.source_id)
    normalized = {}
    total = 0
    for raw in sources:
        require(type(raw) is bytes, "saved_source")
        total += len(raw)
        if len(raw) > MAX_BYTES or total > 32 * MAX_BYTES:
            raise InputError("saved-source-too-large", "saved_sources")
        obj = parse(raw, "saved_source")
        require(type(obj) is dict, "saved_source")
        provider = obj.get("provider")
        reported_fixed = None
        if provider == "github":
            fields(obj, "schema provider review_id head", "saved_source")
            source_id, reported = obj["review_id"], obj["head"]
            require(revision(reported), "saved_source.head")
        elif provider == "mail":
            fields(obj, "schema provider message_key revision_label reports_fixed", "saved_source")
            source_id, reported = obj["message_key"], obj["revision_label"]
            require(reported is None or identifier(reported), "saved_source.revision_label")
            reported_fixed = obj["reports_fixed"]
            require(reported_fixed is None or type(reported_fixed) is bool, "saved_source.reports_fixed")
        else:
            raise InputError("invalid-schema", "saved_source.provider")
        version(obj, "saved_source")
        require(identifier(source_id), "saved_source.id")
        checksum = digest(raw)
        key = (provider, source_id)
        pin = by_key.get(key)
        if pin is not None:
            if pin.sha256 != checksum:
                raise InputError("source-pin-digest-mismatch", source_id)
            if provider == "github" and pin.candidate != reported:
                raise InputError("source-pin-candidate-mismatch", source_id)
        source = SavedRevisionSource(
            provider, source_id, checksum, reported, reported_fixed, pin.candidate if pin else None
        )
        if normalized.setdefault(key, source) != source:
            raise InputError("saved-source-conflict", source_id)
    if by_key.keys() - normalized.keys():
        raise InputError("source-pin-missing-artifact", min(by_key.keys() - normalized.keys())[1])
    return tuple(normalized[k] for k in sorted(normalized))
