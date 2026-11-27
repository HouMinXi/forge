"""Offline controls for the review follow-up capture command."""

import hashlib
import json
import subprocess

import pytest

from code_forge.eval.review_followup.__main__ import main
from code_forge.eval.review_followup.revision_adjudication import decide_revision
from code_forge.eval.review_followup.revision_capture import capture_evidence_snapshot
from code_forge.eval.review_followup.revision_codec import decode_record
from code_forge.eval.review_followup.revision_validation import validate_proposal


def _git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args]).strip().decode()


def _commit(root):
    _git(root, "add", ".")
    _git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "fixture",
    )
    return f"git:{_git(root, 'rev-parse', 'HEAD')}"


def _fixture(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "old.ts").write_bytes(b"old\r\n")
    before = _commit(repo)
    (repo / "old.ts").unlink()
    (repo / "new.ts").write_bytes(b"new\r\n")
    (repo / "binary").write_bytes(b"\xff")
    (repo / "big").write_bytes(b"a" * (1024 * 1024 + 1))
    (repo / "link").symlink_to("new.ts")
    after = _commit(repo)
    (repo / "new.ts").write_bytes(b"dirty worktree, not evidence")
    return repo, {"schema": 1, "finding_id": "f-1", "base": before, "candidate": after}


def _item(key, role, path, end=5):
    return dict(id=key, role=role, path=path, start=0, end=end)


def _call(tmp_path, repo, obj, output="one.json"):
    request = tmp_path / "request.json"
    request.write_text(json.dumps(obj), encoding="utf-8")
    path = tmp_path / output
    return main(["capture-revision", str(repo), str(request), "--output", str(path)]), path


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def test_capture_uses_pinned_blobs_preserves_bytes_and_is_deterministic(tmp_path, monkeypatch):
    repo, obj = _fixture(tmp_path)
    obj["selections"] = [_item("historical", "base", "old.ts"), _item("current", "candidate", "new.ts")]
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "incorrect"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "incorrect-index"))
    code, path = _call(tmp_path, repo, obj)
    assert code == 0
    packet = json.loads(path.read_bytes())
    assert packet["request_sha256"] == _sha((tmp_path / "request.json").read_bytes())
    for row, role, exact in zip(
        packet["evidence"], ("base", "candidate"), (b"old\r\n", b"new\r\n"), strict=True
    ):
        assert row["revision"] == obj[role]
        assert row["error"] is None
        assert row["artifact"]["text"].encode() == exact
        assert row["artifact"]["artifact_sha256"] == row["artifact"]["blob_sha256"] == _sha(exact)
        assert (
            row["artifact"]["git_blob"]
            == hashlib.sha1(b"blob 5\0" + exact, usedforsecurity=False).hexdigest()
        )
        assert row["artifact"]["selector"] == {"kind": "source_bytes", "start": 0, "end": 5}
    assert not {"ruling", "status", "closure"} & packet.keys()
    code, repeat = _call(tmp_path, repo, obj, "repeat.json")
    assert code == 0 and repeat.read_bytes() == path.read_bytes()
    obj["finding_id"] = "f-2"
    assert _call(tmp_path, repo, obj)[0] == 1
    assert json.loads(path.read_bytes())["finding_id"] == "f-1"


def test_capture_reports_holes_instead_of_guessing(tmp_path):
    repo, obj = _fixture(tmp_path)
    obj["base"] = "git:" + "1" * 40
    obj["selections"] = [
        _item("missing-commit", "base", "old.ts"),
        _item("missing-path", "candidate", "old.ts"),
        _item("link", "candidate", "link"),
        _item("span", "candidate", "new.ts", 6),
        _item("binary", "candidate", "binary", 1),
        _item("big", "candidate", "big", 1),
        _item("good", "candidate", "new.ts"),
    ]
    code, path = _call(tmp_path, repo, obj)
    assert code == 0
    rows = json.loads(path.read_bytes())["evidence"]
    assert [row["error"] for row in rows] == [
        "revision-unavailable",
        "path-not-in-tree",
        "not-regular-blob",
        "span-out-of-range",
        "excerpt-not-utf8",
        "blob-too-large",
        None,
    ]
    assert all(row["artifact"] is None for row in rows[:-1])
    assert rows[-1]["artifact"]["text"] == "new\r\n"


def test_invalid_request_is_rejected_before_any_repository_lookup(tmp_path, capsys):
    _, obj = _fixture(tmp_path)
    obj["selections"] = [_item("escape", "base", "../outside")]
    code, path = _call(tmp_path, tmp_path / "missing", obj)
    assert code == 2 and not path.exists()
    assert "request.selections/0/path" in capsys.readouterr().err


def test_capture_requires_exact_repository_root_and_explicit_revision(tmp_path):
    repo, obj = _fixture(tmp_path)
    obj["selections"] = [_item("one", "base", "old.ts")]
    nested = repo / "nested"
    nested.mkdir()
    assert _call(tmp_path, nested, obj)[0] == 2
    obj["base"] = "HEAD"
    assert _call(tmp_path, repo, obj)[0] == 2


def test_capture_accepts_bare_repository(tmp_path):
    repo, obj = _fixture(tmp_path)
    bare = tmp_path / "bare.git"
    _git(tmp_path, "clone", "-q", "--bare", str(repo), str(bare))
    obj["selections"] = [_item("one", "base", "old.ts")]
    code, path = _call(tmp_path, bare, obj)
    assert code == 0
    assert json.loads(path.read_bytes())["evidence"][0]["artifact"]["text"] == "old\r\n"


def test_capture_ignores_replacements_and_preserves_offset_domains(tmp_path):
    repo, obj = _fixture(tmp_path)
    _git(repo, "replace", obj["candidate"][4:], obj["base"][4:])
    obj["selections"] = [{"id": "slice", "role": "candidate", "path": "new.ts", "start": 1, "end": 5}]
    code, output = _call(tmp_path, repo, obj)
    assert code == 0
    (row,) = json.loads(output.read_bytes())["evidence"]
    assert (row["start"], row["end"]) == (1, 5)
    assert row["artifact"]["text"] == "ew\r\n"
    assert row["artifact"]["artifact_sha256"] == _sha(b"ew\r\n")
    assert row["artifact"]["blob_sha256"] == _sha(b"new\r\n")
    assert row["artifact"]["selector"] == {"kind": "source_bytes", "start": 0, "end": 4}


@pytest.mark.parametrize("missing", ["base", "candidate"])
def test_capture_reports_missing_revision_with_no_selections(tmp_path, missing):
    repo, obj = _fixture(tmp_path)
    present = "candidate" if missing == "base" else "base"
    obj[missing] = "git:" + "1" * 40
    name, text = ("new.ts", "new\r\n") if present == "candidate" else ("old.ts", "old\r\n")
    obj["selections"] = [_item("only", present, name)]
    code, path = _call(tmp_path, repo, obj)
    assert code == 0
    packet = json.loads(path.read_bytes())
    assert packet["revision_errors"] == {missing: "revision-unavailable", present: None}
    (entry,) = packet["evidence"]
    assert entry["error"] is None
    assert entry["artifact"]["text"] == text


@pytest.mark.parametrize(
    ("missing_revision", "adopt_missing", "status", "reason", "ref_error"),
    [
        (True, False, "verified_fixed", "capture-revision-unavailable", None),
        (True, False, "no_longer_applicable", "capture-revision-unavailable", None),
        (True, False, "still_present", None, None),
        (False, False, "verified_fixed", None, None),
        (False, True, "verified_fixed", None, "source-capture-unavailable"),
    ],
)
def test_capture_proposal_checks_both_revisions_and_only_referenced_artifacts(
    tmp_path, missing_revision, adopt_missing, status, reason, ref_error
):
    repo, obj = _fixture(tmp_path)
    if missing_revision:
        obj["base"] = "git:" + "1" * 40
    obj["selections"] = [
        _item("current", "candidate", "new.ts"),
        _item("unavailable", "candidate", "missing.ts"),
    ]
    code, path = _call(tmp_path, repo, obj)
    assert code == 0
    # Validation must reacquire evidence rather than trust the saved report.
    path.write_text('{"revision_errors":{"base":null,"candidate":null}}', encoding="utf-8")
    proposal = tmp_path / "proposal.json"
    proposal.write_text(
        json.dumps(
            {
                "schema": 1,
                "id": "proposal",
                "kind": "proposal",
                "item": {"finding_id": obj["finding_id"], "candidate_sha": obj["candidate"]},
                "body": {
                    "claim": "Claim awaiting human review",
                    "constraint_ref": {"id": "behavior", "version": 1},
                    "suggested_status": status,
                    "supersedes_proposal_id": None,
                    "evidence_refs": [
                        {
                            "artifact_id": key,
                            "purpose": "current_basis",
                            "selector": {"kind": "source_bytes", "start": 0, "end": 5},
                        }
                        for key in (["current", "unavailable"] if adopt_missing else ["current"])
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "check.json"
    assert (
        main(
            [
                "validate-captured-proposal",
                str(repo),
                str(tmp_path / "request.json"),
                str(proposal),
                "--validation-id",
                "validation",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    report = json.loads(output.read_bytes())
    capture = report["capture"]
    assert capture["revision_errors"]["base"] == ("revision-unavailable" if missing_revision else None)
    assert capture["evidence"][0]["artifact"]["text"] == "new\r\n"
    assert capture["evidence"][1]["error"] == "path-not-in-tree"
    assert set(report) == {"schema", "capture", "host_validation"}
    body = report["host_validation"]["body"]
    assert body["result"] == ("rejected" if reason or ref_error else "passed")
    assert body["reason"] == reason
    assert body["reference_checks"][0]["error"] is None
    if adopt_missing:
        assert body["reference_checks"][1]["error"] == ref_error

    if missing_revision and status == "still_present":
        # A mechanical pass for a non-closing proposal cannot be reused to
        # close the finding when a reviewer arrives at a different conclusion.
        raw = proposal.read_bytes()
        vraw = json.dumps(report["host_validation"]).encode()
        v = decode_record(vraw)
        _, snapshot = capture_evidence_snapshot(repo, tmp_path / "request.json")
        ruling = {
            "schema": 1,
            "kind": "human_ruling",
            "id": "ruling",
            "item": json.loads(raw)["item"],
            "body": {
                "proposal_id": "proposal",
                "proposal_digest": decode_record(raw).sha256,
                "validation_id": v.record_id,
                "validation_digest": v.sha256,
                "adopted_ref_indexes": [0],
                "reviewer_id": "fixture-reviewer",
                "conclusion": "verified_fixed",
                "reason": "Synthetic negative control",
                "remaining_gaps": [],
                "supersedes_ruling_ids": [],
            },
        }
        decision = decide_revision(snapshot, [raw, vraw, json.dumps(ruling).encode()])
        assert decision.conclusion is None
        assert ("human_ruling", "ruling", "capture-revision-unavailable") in decision.diagnostics


@pytest.mark.parametrize("change", ["path", "offset", "unreferenced"])
def test_captured_ruling_remains_bound_to_original_git_location(tmp_path, change):
    repo, obj = _fixture(tmp_path)
    raw_file = b"same\nsame\n"
    (repo / "first.txt").write_bytes(raw_file)
    (repo / "second.txt").write_bytes(raw_file)
    obj["candidate"] = _commit(repo)
    obj["selections"] = [
        _item("current", "candidate", "first.txt"),
        _item("ignored", "candidate", "second.txt"),
    ]
    assert _call(tmp_path, repo, obj)[0] == 0
    request = tmp_path / "request.json"
    item = {"finding_id": obj["finding_id"], "candidate_sha": obj["candidate"]}
    proposal = json.dumps(
        {
            "schema": 1,
            "kind": "proposal",
            "id": "proposal",
            "item": item,
            "body": {
                "claim": "Synthetic claim",
                "constraint_ref": {"id": "behavior", "version": 1},
                "suggested_status": "verified_fixed",
                "supersedes_proposal_id": None,
                "evidence_refs": [
                    {
                        "artifact_id": "current",
                        "purpose": "current_basis",
                        "selector": {"kind": "source_bytes", "start": 0, "end": 5},
                    }
                ],
            },
        }
    ).encode()
    first_capture, before = capture_evidence_snapshot(repo, request)
    validation = validate_proposal(proposal, before, validation_id="validation")
    ruling = json.dumps(
        {
            "schema": 1,
            "kind": "human_ruling",
            "id": "ruling",
            "item": item,
            "body": {
                "proposal_id": "proposal",
                "proposal_digest": decode_record(proposal).sha256,
                "validation_id": "validation",
                "validation_digest": validation.sha256,
                "adopted_ref_indexes": [0],
                "reviewer_id": "fixture-reviewer",
                "conclusion": "verified_fixed",
                "reason": "Synthetic control",
                "remaining_gaps": [],
                "supersedes_ruling_ids": [],
            },
        }
    ).encode()
    records = [proposal, validation.canonical, ruling]
    assert decide_revision(before, records).conclusion == "verified_fixed"
    if change == "path":
        obj["selections"][0]["path"] = "second.txt"
    elif change == "offset":
        obj["selections"][0].update(start=5, end=10)
    else:
        obj["selections"][1]["path"] = "missing.txt"
    request.write_text(json.dumps(obj), encoding="utf-8")
    second_capture, after = capture_evidence_snapshot(repo, request)
    assert first_capture["request_sha256"] != second_capture["request_sha256"]
    assert first_capture["evidence"][0]["artifact"] == second_capture["evidence"][0]["artifact"]
    new = validate_proposal(proposal, after, validation_id="validation")
    decision = decide_revision(after, records)
    if change == "unreferenced":
        assert second_capture["evidence"][1]["error"] == "path-not-in-tree"
        assert new.canonical == validation.canonical
        assert decision.conclusion == "verified_fixed"
    else:
        assert new.sha256 != validation.sha256
        assert decision.conclusion is None
        assert (
            "host_validation",
            "validation",
            "inactive-or-invalid-validation",
        ) in decision.diagnostics
