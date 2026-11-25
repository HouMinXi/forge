"""Offline #574 PR1 acceptance controls with hand-authored oracles."""

import json
import os
import shutil
from pathlib import Path

import pytest

from code_forge.eval.review_followup import replay as pipeline
from code_forge.eval.review_followup.__main__ import main
from code_forge.eval.review_followup.fixtures import InputError, digest, parse
from code_forge.eval.review_followup.report import build_report

FIXTURES = Path(__file__).parents[3] / "tests" / "fixtures" / "review_followup" / "synthetic"


@pytest.fixture
def tree(tmp_path):
    return shutil.copytree(FIXTURES, tmp_path / "input")


def modify(path, edit):
    document = json.loads(path.read_bytes())
    edit(document)
    data = json.dumps(document).encode()
    path.write_bytes(data)
    return digest(data)


def test_two_scorecards_match_hand_calculated_ledger(tree):
    report = build_report(pipeline.replay(tree))
    first = report["first_review"]
    assert {key: value for key, value in first.items() if key != "cases"} == {
        "attempted": 4,
        "completed": 2,
        "failed": 2,
        "scorable_runs": 2,
        "known_root_hits": 1,
        "false_blockers": 1,
        "pending_findings": 1,
        "usage_known": 12,
        "usage_unknown_runs": 2,
    }
    cases = {row["case"]: row for row in first["cases"]}
    assert cases["first"]["duplicates"] == 1
    assert cases["first"]["findings"][-1]["judgment"] is None
    assert cases["empty"]["hits"] == cases["empty"]["usage"] == 0
    assert cases["provider"]["status"] == "provider_failed"
    assert cases["invalid"]["status"] == "invalid_response"
    for name in ("provider", "invalid"):
        assert cases[name]["hits"] is cases[name]["usage"] is None
        assert cases[name]["findings"] == []
    revision = report["revision_followup"]
    assert revision["completed"] == 1
    assert revision["known_root_hits"] is None
    outcomes = {
        item["finding"]: (item["outcome"], item["open"]) for item in revision["cases"][0]["followups"]
    }
    assert outcomes == {
        "old-fixed": ("verified_fixed", False),
        "old-removed": ("no_longer_applicable", False),
        "old-unclear": ("insufficient_evidence", True),
        "old-open": (None, True),
    }


def test_scoring_files_not_needed_before_observation_is_sealed(tree):
    shutil.rmtree(tree / "answer_keys")
    shutil.rmtree(tree / "adjudications")
    (tree / "scores.json").unlink()
    views = pipeline.validate(tree)
    sealed = pipeline.seal_runs(tree, views)
    assert len(views) == len(sealed) == 5
    assert b"Candidate label" in views[0].evidence[0][-1]


@pytest.mark.parametrize("slot", ["input", "artifact"])
def test_an_attempt_to_read_an_answer_as_review_input_is_refused(tree, monkeypatch, slot):
    key = "answer_keys/first/key.json"
    decoy = {"path": key, "sha256": digest((tree / key).read_bytes())}
    if slot == "input":
        modify(tree / "manifest.json", lambda m: m["cases"][0].update(input=decoy))
    else:
        updated = modify(
            tree / "inputs/first/review.json",
            lambda view: view["artifacts"][0].update(decoy),
        )
        modify(tree / "manifest.json", lambda m: m["cases"][0]["input"].update(sha256=updated))
    original = Path.open

    def guarded(path, *args, **kwargs):
        assert "answer_keys" not in path.parts
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    with pytest.raises(InputError, match="invalid-schema: first/input"):
        pipeline.validate(tree)


@pytest.mark.parametrize("failure", ["missing-observation", "wrong-input", "invalid-failure-envelope"])
def test_failed_observation_cannot_open_answers_or_publish(tree, tmp_path, monkeypatch, failure):
    path = tree / "saved_runs/provider/observation.json"
    if failure == "missing-observation":
        path.unlink()
    else:
        changes = {"input_sha256": "0" * 64} if failure == "wrong-input" else {"diagnostic": None}
        updated = modify(path, lambda observation: observation.update(changes))
        modify(
            tree / "runs.json",
            lambda m: m["cases"]["provider"]["observation"].update(sha256=updated),
        )
    original = Path.open

    def guarded(path, *args, **kwargs):
        assert path.name != "scores.json"
        assert "answer_keys" not in path.parts
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    target = tmp_path / "report.json"
    assert main(["replay", str(tree), "--output", str(target)]) == 2
    assert not target.exists()


@pytest.mark.parametrize("update_ruling", [False, True], ids=["stale-ruling", "fresh-ruling"])
def test_caller_cannot_reuse_ruling_after_saved_response_changes(tree, update_ruling):
    updated = modify(
        tree / "saved_runs/first/observation.json",
        lambda run: run["findings"][0].update(claim="Different response under the same ID"),
    )
    modify(tree / "runs.json", lambda m: m["cases"]["first"]["observation"].update(sha256=updated))
    if update_ruling:
        updated_ruling = modify(
            tree / "adjudications/first/ruling.json", lambda r: r.update(observation_sha256=updated)
        )
        modify(
            tree / "scores.json",
            lambda m: m["cases"]["first"]["adjudication"].update(sha256=updated_ruling),
        )
        assert pipeline.replay(tree)
    else:
        with pytest.raises(InputError, match="invalid-schema: first/adjudication"):
            pipeline.replay(tree)


def test_missing_artifact_wins_over_earlier_bad_digest(tree):
    first = tree / "inputs/first/candidate.txt"
    revision = tree / "inputs/revision/candidate.txt"
    before_first, before_revision = first.read_bytes(), revision.read_bytes()
    first.write_bytes(before_first + b"!")
    revision.unlink()
    with pytest.raises(InputError, match="missing-file: revision/artifacts/candidate-note"):
        pipeline.validate(tree)
    revision.write_bytes(before_revision)
    with pytest.raises(InputError, match="digest-mismatch: first/artifacts/candidate-note"):
        pipeline.validate(tree)
    first.write_bytes(before_first)
    assert len(pipeline.validate(tree)) == 5


@pytest.mark.parametrize("as_directory", [True, False], ids=["middle", "leaf"])
def test_symlink_is_refused_without_following(tree, tmp_path, as_directory):
    candidate = tree / "inputs/first/candidate.txt"
    victim = candidate.parent if as_directory else candidate
    outside = tmp_path / "outside"
    victim.rename(outside)
    victim.symlink_to(outside, target_is_directory=as_directory)
    with pytest.raises(InputError, match="symlink: first/"):
        pipeline.validate(tree)


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (b'{"a":{"b":1,"b":2}}', "duplicate-key"),
        (b'{"a":NaN}', "non-finite-number"),
        (b'{"a":1e1000}', "non-finite-number"),
        (b'{"a":\xff}', "invalid-json"),
    ],
)
def test_json_parsing_is_strict(raw, reason):
    with pytest.raises(InputError, match=reason):
        parse(raw, "fixture")


def test_cli_reports_are_reproducible_and_never_overwrite(tree, tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    assert main(["validate", str(tree)]) == 0
    for target in (a, b):
        assert main(["replay", str(tree), "--output", str(target)]) == 0
    assert a.read_bytes() == b.read_bytes()
    saved = a.read_bytes()
    assert main(["replay", str(tree), "--output", str(a)]) == 1
    assert a.read_bytes() == saved
    assert not list(tmp_path.glob(".offline-replay-*"))


def test_interrupted_publish_does_not_leave_a_report(tree, tmp_path, monkeypatch):
    def interrupted(*_):
        raise KeyboardInterrupt

    monkeypatch.setattr("code_forge.eval.review_followup.report.os.link", interrupted)
    target = tmp_path / "report.json"
    with pytest.raises(KeyboardInterrupt):
        main(["replay", str(tree), "--output", str(target)])
    assert not target.exists()
    assert not list(tmp_path.glob(".offline-replay-*"))


def test_lineage_cannot_cross_splits(tree):
    def cross_split(manifest):
        manifest["cases"][1].update(lineage="first", split="holdout")

    modify(tree / "manifest.json", cross_split)
    with pytest.raises(InputError, match="invalid-schema: manifest/cases/1"):
        pipeline.validate(tree)


@pytest.mark.parametrize("fault", ["wrong-revision", "wrong-group", "bool-schema", "cross-case"])
def test_review_input_identity_rejects_inconsistent_metadata(tree, fault):
    def tamper(view):
        if fault == "wrong-revision":
            view["artifacts"][0]["revision"] = view["base"]
        elif fault == "wrong-group":
            view["groups"][0]["artifacts"] = ["does-not-exist"]
        elif fault == "bool-schema":
            view["schema"] = True
        else:
            view["artifacts"][0]["case"] = "revision"

    updated = modify(tree / "inputs/first/review.json", tamper)
    modify(tree / "manifest.json", lambda m: m["cases"][0]["input"].update(sha256=updated))
    with pytest.raises(InputError, match="invalid-schema: first/input"):
        pipeline.validate(tree)


def test_symlinked_answer_subtree_is_an_invalid_review_reference(tree):
    note = tree / "inputs/first/candidate.txt"
    note.unlink()
    note.symlink_to(tree / "answer_keys/first/key.json")
    with pytest.raises(InputError, match="symlink: first/artifacts/candidate-note"):
        pipeline.validate(tree)


def test_cleanup_failure_after_publication_keeps_success(tree, tmp_path, monkeypatch, capsys):
    unlink = os.unlink
    temp_files = []

    def fail_scratch_cleanup(path, *args, **kwargs):
        if Path(path).name.startswith(".offline-replay-"):
            temp_files.append(path)
            raise OSError("cannot remove scratch file")
        return unlink(path, *args, **kwargs)

    target = tmp_path / "report.json"
    try:
        with monkeypatch.context() as patch:
            patch.setattr("code_forge.eval.review_followup.report.os.unlink", fail_scratch_cleanup)
            code = main(["replay", str(tree), "--output", str(target)])
        assert len(temp_files) == 1
        assert json.loads(target.read_bytes()) == build_report(pipeline.replay(tree))
        assert code == 0
        assert "unable to publish" not in capsys.readouterr().err
    finally:
        for path in temp_files:
            unlink(path)


@pytest.mark.parametrize(
    ("kind", "relative_path"),
    [("answer", "answer_keys/first/key.json"), ("adjudication", "adjudications/first/ruling.json")],
)
def test_report_pins_exact_scoring_artifact_bytes(tree, kind, relative_path):
    def first_row():
        return next(row for row in pipeline.replay(tree) if row["case"] == "first")

    before = first_row()
    path = tree / relative_path
    original = path.read_bytes()
    revised = original + b"\n"
    path.write_bytes(revised)
    modify(
        tree / "scores.json", lambda index: index["cases"]["first"][kind].update(sha256=digest(revised))
    )
    after = first_row()
    assert before["scoring_artifacts"][kind] == {"path": relative_path, "sha256": digest(original)}
    assert after["scoring_artifacts"][kind] == {"path": relative_path, "sha256": digest(revised)}
    assert {k: v for k, v in before.items() if k != "scoring_artifacts"} == {
        k: v for k, v in after.items() if k != "scoring_artifacts"
    }


def test_bad_path_in_later_artifact_is_reported_before_any_artifact_io(tree, monkeypatch):
    first = tree / "inputs/first/candidate.txt"
    original = first.read_bytes()
    first.unlink()

    review = tree / "inputs/revision/review.json"
    good = review.read_bytes()
    updated = modify(
        review, lambda view: view["artifacts"][0].update(path="inputs/revision//candidate.txt")
    )
    modify(tree / "manifest.json", lambda index: index["cases"][1]["input"].update(sha256=updated))

    lstat = Path.lstat

    def guarded(path):
        assert path.name != "candidate.txt", "artifact lookup preceded batch path validation"
        return lstat(path)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "lstat", guarded)
        with pytest.raises(InputError, match="unsafe-path: revision/artifacts/candidate-note"):
            pipeline.validate(tree)
    review.write_bytes(good)
    modify(tree / "manifest.json", lambda index: index["cases"][1]["input"].update(sha256=digest(good)))
    with pytest.raises(InputError, match="missing-file: first/artifacts/candidate-note"):
        pipeline.validate(tree)
    first.write_bytes(original)
    assert len(pipeline.validate(tree)) == 5
