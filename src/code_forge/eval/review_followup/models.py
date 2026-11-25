"""Closed v1 data contracts for input, observation and human scoring."""

from dataclasses import dataclass

from .fixtures import enum, fields, identifier, require, revision, sha


@dataclass(frozen=True, slots=True)
class ReviewInput:
    case: str
    lineage: str
    split: str
    task: str
    base: str
    candidate: str
    groups: tuple
    prior_findings: tuple
    evidence: tuple
    sha256: str


@dataclass(frozen=True, slots=True)
class Finding:
    id: str
    claim: str
    blocking: bool


@dataclass(frozen=True, slots=True)
class RunObservation:
    run: str
    status: str
    findings: tuple
    diagnostic: str | None
    usage: int | None
    sha256: str
    baseline: tuple


@dataclass(frozen=True, slots=True)
class AnswerKey:
    roots: frozenset | None


@dataclass(frozen=True, slots=True)
class Judgment:
    finding: str
    root: str | None
    factual: str
    attribution: str
    blocking: str


@dataclass(frozen=True, slots=True)
class HumanAdjudication:
    findings: tuple
    revisions: tuple


def version(obj, field):
    require(type(obj["schema"]) is int and obj["schema"] == 1, field)


def distinct_ids(value, field):
    require(type(value) is list and all(map(identifier, value)), field)
    require(len(value) == len(set(value)), field)
    return tuple(value)


def nonempty(value):
    return type(value) is str and bool(value.strip())


def manifest_cases(value):
    fields(value, "schema cases", "manifest")
    version(value, "manifest/schema")
    cases = value["cases"]
    require(type(cases) is list and bool(cases), "manifest/cases")
    seen = set()
    splits = {}
    for i, case in enumerate(cases):
        field = f"manifest/cases/{i}"
        fields(case, "id lineage split input", field)
        require(identifier(case["id"]) and identifier(case["lineage"]), field)
        require(enum(case["split"], "calibration", "holdout"), field)
        require(case["id"] not in seen, field)
        require(splits.setdefault(case["lineage"], case["split"]) == case["split"], field)
        seen.add(case["id"])
    return cases


def input_shape(value, case):
    field = f"{case['id']}/input"
    fields(value, "schema case task base candidate groups prior_findings artifacts", field)
    version(value, field)
    require(value["case"] == case["id"], field)
    require(enum(value["task"], "first_review", "revision_followup"), field)
    require(revision(value["base"]) and revision(value["candidate"]), field)
    require(value["base"].split(":")[0] == value["candidate"].split(":")[0], field)
    prior = distinct_ids(value["prior_findings"], field)
    require(bool(prior) == (value["task"] == "revision_followup"), field)
    require(type(value["artifacts"]) is list and bool(value["artifacts"]), field)
    require(type(value["groups"]) is list and bool(value["groups"]), field)
    return prior


def observation(value, review, checksum):
    field = f"{review.case}/observation"
    fields(
        value, "schema case candidate input_sha256 run status findings diagnostic usage baseline", field
    )
    version(value, field)
    require(identifier(value["run"]), field)
    require(enum(value["status"], "completed", "provider_failed", "invalid_response"), field)
    require(type(value["findings"]) is list, field)
    require(value["usage"] is None or (type(value["usage"]) is int and value["usage"] >= 0), field)
    require(
        value["case"] == review.case
        and value["candidate"] == review.candidate
        and value["input_sha256"] == review.sha256,
        field,
    )
    baseline = value["baseline"]
    fields(baseline, "forge_commit model prompt context_limit call_limit failure_policy", field)
    require(revision(baseline["forge_commit"]) and baseline["forge_commit"].startswith("git:"), field)
    require(
        all(baseline[k] is None or nonempty(baseline[k]) for k in ("model", "prompt", "failure_policy")),
        field,
    )
    require(
        all(
            baseline[k] is None or (type(baseline[k]) is int and baseline[k] >= 0)
            for k in ("context_limit", "call_limit")
        ),
        field,
    )
    findings = []
    seen = set()
    for item in value["findings"]:
        fields(item, "id claim blocking", field)
        require(identifier(item["id"]) and nonempty(item["claim"]), field)
        require(type(item["blocking"]) is bool and item["id"] not in seen, field)
        seen.add(item["id"])
        findings.append(Finding(**item))
    if value["status"] == "completed":
        require(value["diagnostic"] is None, field)
    else:
        require(nonempty(value["diagnostic"]) and not findings, field)
    return RunObservation(
        value["run"],
        value["status"],
        tuple(findings),
        value["diagnostic"],
        value["usage"],
        checksum,
        tuple(sorted(baseline.items())),
    )


def answer_key(value, review):
    field = f"{review.case}/answer"
    fields(value, "schema case candidate roots", field)
    version(value, field)
    require(value["case"] == review.case and value["candidate"] == review.candidate, field)
    roots = value["roots"]
    return AnswerKey(None if roots is None else frozenset(distinct_ids(roots, field)))


def adjudicate(value, review, run, answer):
    field = f"{review.case}/adjudication"
    fields(value, "schema case candidate run observation_sha256 findings revisions", field)
    version(value, field)
    require(
        value["case"] == review.case
        and value["candidate"] == review.candidate
        and value["run"] == run.run
        and sha(value["observation_sha256"])
        and value["observation_sha256"] == run.sha256,
        field,
    )
    require(type(value["findings"]) is list and type(value["revisions"]) is list, field)
    ids = {finding.id for finding in run.findings}
    seen = set()
    judgments = []
    for row in value["findings"]:
        fields(row, "finding root factual attribution blocking", field)
        require(identifier(row["finding"]) and row["finding"] in ids - seen, field)
        require(
            row["root"] is None or (identifier(row["root"]) and row["root"] in (answer.roots or ())),
            field,
        )
        require(enum(row["factual"], "supported", "unsupported", "uncertain"), field)
        require(enum(row["attribution"], "introduced", "worsened", "preexisting", "unknown"), field)
        require(enum(row["blocking"], "should_block", "should_not_block", "uncertain"), field)
        require(row["root"] is None or row["factual"] == "supported", field)
        seen.add(row["finding"])
        judgments.append(Judgment(**row))
    revisions = []
    seen = set()
    for row in value["revisions"]:
        fields(row, "finding outcome evidence rationale", field)
        require(
            identifier(row["finding"]) and row["finding"] in set(review.prior_findings) - seen, field
        )
        require(
            enum(
                row["outcome"],
                "still_present",
                "verified_fixed",
                "no_longer_applicable",
                "insufficient_evidence",
            ),
            field,
        )
        require(nonempty(row["rationale"]), field)
        refs = distinct_ids(row["evidence"], field)
        roles = {artifact[0]: artifact[1] for artifact in review.evidence}
        require(all(ref in roles for ref in refs), field)
        if row["outcome"] != "insufficient_evidence":
            require(any(roles[ref] == "candidate" for ref in refs), field)
        seen.add(row["finding"])
        revisions.append((row["finding"], row["outcome"], refs, row["rationale"]))
    require(run.status == "completed" or not revisions, field)
    return HumanAdjudication(tuple(judgments), tuple(revisions))
