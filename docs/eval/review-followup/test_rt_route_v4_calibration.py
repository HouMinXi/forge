"""Verify saved scope judgments, not kernel acceptance or model performance."""

import json
import shutil
from pathlib import Path

import pytest

from code_forge.eval.review_followup import replay
from code_forge.eval.review_followup.fixtures import InputError, digest, pinned_files

FIXTURE = Path(__file__).resolve().parents[3] / "tests/fixtures/review_followup/rt-route-v4"
SCORING = {"scores.json", "scoring-evidence.json", "answer_keys", "adjudications", "evidence.json"}


def _seal(root):
    shutil.copytree(FIXTURE, root, dirs_exist_ok=True, ignore=lambda _dir, names: SCORING & set(names))
    assert not (root / "scores.json").exists()
    return replay.seal_runs(root, replay.validate(root))


def _stage_scoring(root):
    for name in sorted(SCORING):
        source = FIXTURE / name
        if source.is_dir():
            shutil.copytree(source, root / name)
        else:
            shutil.copyfile(source, root / name)


def _ruling_evidence(root):
    binding = json.loads((root / "scoring-evidence.json").read_bytes())
    evidence = pinned_files(root, list(binding.items()))["evidence"]
    return json.loads(evidence.raw)


def test_same_candidate_can_have_different_attribution_by_scope(tmp_path):
    sealed = _seal(tmp_path)
    _stage_scoring(tmp_path)
    evidence = _ruling_evidence(tmp_path)
    rows = {row["case"]: row for row in replay.score_sealed(tmp_path, sealed)}
    patch = rows["patch-scope"]
    series = rows["series-scope"]
    revisions = evidence["revisions"]
    assert patch["candidate"] == series["candidate"] == revisions["patch-4"]["revision"]
    assert {item.review.case: item.review.base for item in sealed} == {
        "patch-scope": revisions["patch-4-parent"]["revision"],
        "series-scope": revisions["series-base"]["revision"],
    }
    assert patch["lineage"] == series["lineage"]
    observations = [item.result.findings for item in sealed]
    assert observations[0] == observations[1]
    assert observations[0][0].claim == (
        "The rt-route Geneve options encoding does not carry both the outer options and inner "
        "Geneve NLA_F_NESTED flags together."
    )

    def carries_both(side):
        options = evidence["measurement"]["schemas"][side]["options"]
        return all(row["opts_nested"] and row["geneve_nested"] for row in options.values())

    assert not carries_both("patch-4")
    assert not carries_both("patch-4-parent")
    assert carries_both("series-base")
    assert patch["hits"] == series["hits"] == 1
    assert patch["usage"] is series["usage"] is None

    def disposition(row):
        judgment = row["findings"][0]["judgment"]
        return judgment["factual"], judgment["attribution"], judgment["blocking"]

    assert disposition(patch) == ("supported", "preexisting", "uncertain")
    assert disposition(series) == ("supported", "introduced", "uncertain")
    assert (patch["false_blockers"], patch["pending_findings"]) == (0, 1)
    assert (series["false_blockers"], series["pending_findings"]) == (0, 1)


def test_same_candidate_does_not_allow_other_scope_ruling(tmp_path):
    sealed = _seal(tmp_path)
    _stage_scoring(tmp_path)

    def read(name):
        return json.loads((tmp_path / name).read_bytes())

    case = "patch-scope"
    # Make the wrong ruling otherwise valid for the patch case, but keep its
    # actual sealed observation digest from the series scope.
    wrong = read("adjudications/series-scope/ruling.json")
    wrong["case"] = case
    path = f"adjudications/{case}/ruling.json"
    raw = json.dumps(wrong, sort_keys=True).encode()
    (tmp_path / path).write_bytes(raw)
    manifest = read("scores.json")
    manifest["cases"][case]["adjudication"]["sha256"] = digest(raw)
    (tmp_path / "scores.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(InputError) as caught:
        replay.score_sealed(tmp_path, sealed)
    assert (caught.value.code, caught.value.field) == ("invalid-schema", f"{case}/adjudication")


def test_saved_measurement_keeps_three_snapshots_distinct(tmp_path):
    _seal(tmp_path)
    _stage_scoring(tmp_path)
    report = _ruling_evidence(tmp_path)["measurement"]
    assert report["applied_archive_yaml_diffs"] == 2
    assert report["netlink_sockets_opened"] == 0
    expected = {"series-base": (True, True), "patch-4-parent": (False, True), "patch-4": (True, False)}
    assert report["schemas"].keys() == expected.keys()
    for side, flags in expected.items():
        options = report["schemas"][side]["options"]
        assert options.keys() == {"ip", "ip6"}
        for row in options.values():
            assert (row["opts_nested"], row["geneve_nested"]) == flags
    assert all(row["vxlan_nested_control"] for row in report["schemas"]["patch-4"]["options"].values())
