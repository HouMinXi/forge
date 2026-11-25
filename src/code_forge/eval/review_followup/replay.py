"""Phase boundaries for saved-run evaluation; never starts a reviewer."""

from dataclasses import dataclass

from .fixtures import (
    InputError,
    enum,
    fields,
    parse,
    pinned_files,
    read_file,
    ref_shape,
    require,
    revision,
    sha,
)
from .models import (
    ReviewInput,
    RunObservation,
    adjudicate,
    answer_key,
    distinct_ids,
    input_shape,
    manifest_cases,
    observation,
    version,
)
from .scoring import score


@dataclass(frozen=True, slots=True)
class SealedObservation:
    review: ReviewInput
    # The observed digest is over the original bytes, not the manifest's claim.
    result: RunObservation


def _entries(root, name, cases, ref_names):
    doc = parse(read_file(root, name, name), name)
    fields(doc, "schema cases", name)
    version(doc, name)
    require(type(doc["cases"]) is dict and set(doc["cases"]) == set(cases), name)
    entries = []
    for case, refs in doc["cases"].items():
        fields(refs, " ".join(ref_names), f"{name}/{case}")
    for case in cases:
        for key in ref_names:
            field = f"{case}/{key}"
            ref = doc["cases"][case][key]
            ref_shape(ref, field)
            prefix = {
                "observation": "saved_runs",
                "answer": "answer_keys",
                "adjudication": "adjudications",
            }[key]
            require(ref["path"].startswith(f"{prefix}/{case}/"), field)
            entries.append((field, ref))
    return pinned_files(root, entries)


def validate(root):
    """Load only review inputs and their verified bytes."""
    cases = manifest_cases(parse(read_file(root, "manifest.json", "manifest"), "manifest"))
    entries = []
    for case in cases:
        field = f"{case['id']}/input"
        ref_shape(case["input"], field)
        require(case["input"]["path"].startswith(f"inputs/{case['id']}/"), field)
        entries.append((field, case["input"]))
    inputs = pinned_files(root, entries)

    pool = []
    decoded = []
    for case in cases:
        name = case["id"]
        field = f"{name}/input"
        verified_input = inputs[field]
        view = parse(verified_input.raw, field)
        prior = input_shape(view, case)
        artifacts = {}
        for item in view["artifacts"]:
            fields(item, "id path sha256 case role revision", field)
            ids = distinct_ids([item["id"]], field)
            art = ids[0]
            require(art not in artifacts and item["case"] == name, field)
            require(type(item["path"]) is str and sha(item["sha256"]), field)
            require(item["path"].startswith(f"inputs/{name}/"), field)
            require(enum(item["role"], "base", "candidate") and revision(item["revision"]), field)
            require(item["revision"] == view[item["role"]], field)
            artifacts[art] = item
            pool.append((f"{name}/artifacts/{art}", item))
        groups = []
        group_ids = set()
        used = set()
        for group in view["groups"]:
            fields(group, "id artifacts", field)
            group_id = distinct_ids([group["id"]], field)[0]
            members = distinct_ids(group["artifacts"], field)
            require(group_id not in group_ids and bool(members), field)
            require(all(member in artifacts for member in members), field)
            group_ids.add(group_id)
            used.update(members)
            groups.append((group_id, members))
        require(used == artifacts.keys(), field)
        decoded.append((case, view, verified_input.sha256, prior, tuple(groups), artifacts))

    verified = pinned_files(root, pool)
    return tuple(
        ReviewInput(
            case["id"],
            case["lineage"],
            case["split"],
            view["task"],
            view["base"],
            view["candidate"],
            groups,
            prior,
            tuple(
                (
                    key,
                    item["role"],
                    item["revision"],
                    verified[f"{case['id']}/artifacts/{key}"].sha256,
                    verified[f"{case['id']}/artifacts/{key}"].raw,
                )
                for key, item in artifacts.items()
            ),
            checksum,
        )
        for case, view, checksum, prior, groups, artifacts in decoded
    )


def seal_runs(root, reviews):
    saved = _entries(root, "runs.json", [entry.case for entry in reviews], ("observation",))
    sealed = []
    for review in reviews:
        field = f"{review.case}/observation"
        artifact = saved[field]
        sealed.append(
            SealedObservation(review, observation(parse(artifact.raw, field), review, artifact.sha256))
        )
    return tuple(sealed)


def score_sealed(root, sealed):
    """The only phase allowed to open scoring artifacts."""
    if type(sealed) is not tuple or any(type(item) is not SealedObservation for item in sealed):
        raise InputError("unsealed-observations", "scoring")
    artifacts = _entries(
        root, "scores.json", [item.review.case for item in sealed], ("answer", "adjudication")
    )
    rows = []
    for item in sealed:
        review = item.review
        answer_field = f"{review.case}/answer"
        answer_artifact = artifacts[answer_field]
        answer = answer_key(parse(answer_artifact.raw, answer_field), review)
        ruling_field = f"{review.case}/adjudication"
        ruling_artifact = artifacts[ruling_field]
        human = adjudicate(parse(ruling_artifact.raw, ruling_field), review, item.result, answer)
        row = score(review, item.result, answer, human)
        row["scoring_artifacts"] = {
            "answer": answer_artifact.reference(),
            "adjudication": ruling_artifact.reference(),
        }
        rows.append(row)
    return sorted(rows, key=lambda row: row["case"])


def replay(root):
    return score_sealed(root, seal_runs(root, validate(root)))
