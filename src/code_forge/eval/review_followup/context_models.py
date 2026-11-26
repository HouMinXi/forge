"""Closed schema for the offline comparison of optional context."""

from dataclasses import dataclass

from .fixtures import MAX_BYTES, enum, fields, identifier, require
from .models import distinct_ids, version

# Fixed tiers; ties are resolved by coordinates, not input order.
RELATIONSHIPS = (
    "failure_exit",
    "reader",
    "writer",
    "schema",
    "generator",
    "handler",
    "test_setup",
    "test_skip",
    "test_cleanup",
    "background",
)


@dataclass(frozen=True, slots=True)
class ContextCandidate:
    id: str
    group: str
    revision: str
    relationship: str
    artifact: str | None
    start: int | None
    end: int | None

    @property
    def span(self):
        return (self.artifact, self.start, self.end)


def _required_text(value, field):
    require(type(value) is str and bool(value.strip()), field)
    require(not any(0xD800 <= ord(char) <= 0xDFFF for char in value), field)


def context_input(value, review):
    field = f"{review.case}/context"
    fields(value, "schema case review_input_sha256 groups candidates", field)
    version(value, field)
    require(
        value["case"] == review.case
        and value["review_input_sha256"] == review.sha256
        and review.task == "first_review",
        field,
    )
    groups = value["groups"]
    ids = {group for group, _ in review.groups}
    require(type(groups) is dict and groups.keys() == ids and len(ids) <= 32, field)
    for key, group in groups.items():
        at = f"{field}/groups/{key}"
        fields(group, "limit_bytes diff contract lenses baseline", at)
        require(type(group["limit_bytes"]) is int and 0 <= group["limit_bytes"] <= MAX_BYTES, at)
        for name in ("diff", "contract"):
            _required_text(group[name], f"{at}/{name}")
        lenses = group["lenses"]
        fields(lenses, "qodo expert adversarial", f"{at}/lenses")
        for name, lens in lenses.items():
            _required_text(lens, f"{at}/lenses/{name}")

    rows = value["candidates"]
    require(type(rows) is list and len(rows) <= 256, field)
    candidates = {}
    for index, row in enumerate(rows):
        at = f"{field}/candidates/{index}"
        fields(row, "id group revision relationship artifact start end", at)
        require(identifier(row["id"]) and row["id"] not in candidates, at)
        require(identifier(row["group"]) and row["group"] in ids, at)
        require(row["revision"] == review.candidate, f"{at}/revision")
        require(enum(row["relationship"], *RELATIONSHIPS), f"{at}/relationship")
        if row["artifact"] is None:
            require(row["start"] is None and row["end"] is None, at)
        else:
            require(identifier(row["artifact"]), f"{at}/artifact")
            require(
                type(row["start"]) is int and type(row["end"]) is int and 0 <= row["start"] < row["end"],
                f"{at}/span",
            )
        candidates[row["id"]] = ContextCandidate(**row)

    baseline = {}
    for key, group in groups.items():
        at = f"{field}/groups/{key}/baseline"
        baseline[key] = distinct_ids(group["baseline"], at)
        require(
            all(item in candidates and candidates[item].group == key for item in baseline[key]),
            at,
        )
    return groups, candidates, baseline
