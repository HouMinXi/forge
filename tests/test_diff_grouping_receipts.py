# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi1990@gmail.com>
"""Grouped dispatch against the receipt verifier on a mixed diff (#34).

The verifier demands a witness for every non-deletion hunk. Grouped review
used to dispatch no passes for config and docs groups, so a mixed diff could
finish every dispatched pass and still fail check 5. These tests dispatch
groups the way the cli does, give each dispatched group's own hunks a
verbatim excerpt, and run the real verifier. The excerpts are test stimuli,
not review evidence.
"""

import difflib
import hashlib
import json

from code_forge.diff import parse_diff_hunks, split_diff_for_files
from code_forge.diff_grouping import group_diff, required_review_files, review_groups
from code_forge.verify import parse_diff_files, run_verify

_BEFORE = ["line %02d" % n for n in range(1, 13)]


def _versions(kind):
    after = list(_BEFORE)
    if kind == "added":
        after.insert(1, "added early")
        after.insert(11, "added late")
    elif kind == "deleted":
        after.remove("line 06")
    return after


# path -> kind of change. The json file only deletes a line: the control.
_FILES = {
    "src/app.py": "added",
    ".github/workflows/ci.yml": "added",
    "README.md": "added",
    "docs/guide.md": "added",
    "schema.json": "added",
    "trim.json": "deleted",
}


def _entity(path):
    return {"filePath": path, "changeType": "modified", "entityName": "f", "entityType": "function"}


def _mixed(files):
    parts = []
    post = {}
    for path, kind in sorted(files.items()):
        after = _versions(kind)
        post[path] = after
        body = difflib.unified_diff(_BEFORE, after, "a/" + path, "b/" + path, n=1, lineterm="")
        parts.append("\n".join(["diff --git a/%s b/%s" % (path, path), *body]) + "\n")
    return "".join(parts), post


def _excerpts(diff, post, members_by_group):
    """One verbatim excerpt per non-deletion hunk of each dispatched group's own diff."""
    out = []
    for members in members_by_group:
        hunks, _ = parse_diff_hunks(split_diff_for_files(diff, members))
        for path, rows in sorted(hunks.items()):
            for row in rows:
                if row["is_deletion_only"]:
                    continue
                text = "\n".join(post[path][row["start"] - 1 : row["end"]])
                out.append(
                    {"file": path, "start_line": row["start"], "end_line": row["end"], "content": text}
                )
    return out


def _verify(tmp_path, diff, excerpts):
    rd = tmp_path / ".code-forge" / "receipts"
    rd.mkdir(parents=True)
    sha = hashlib.sha256(diff.encode()).hexdigest()
    skills = ["qodo-review", "code-review-expert", "adversarial-qe"]
    for cycle in range(1, 4):
        for number in range(1, 4):
            receipt = {
                "cycle": cycle,
                "pass": number,
                "skill": skills[number - 1],
                "diff_sha256": sha,
                "timestamp": "2026-06-07T10:%02d:00Z" % (cycle * 3 + number),
                "findings_count": 0,
                "findings": [],
                "anchors": [],
                "code_excerpts": excerpts,
                "covered_line_ranges": [],
            }
            (rd / ("receipt-c%dp%d.json" % (cycle, number))).write_text(json.dumps(receipt))
    return run_verify(tmp_path, sha, parse_diff_files(diff), diff_text=diff)


def _dispatch(tmp_path, diff, files, *, required):
    result = group_diff([_entity(p) for p in files], tmp_path, required_files=required)
    return result, review_groups(result, required)[0]


def test_mixed_diff_dispatches_every_required_hunk_and_verifies(tmp_path):
    diff, post = _mixed(_FILES)
    required = required_review_files(diff)
    result, dispatched = _dispatch(tmp_path, diff, _FILES, required=required)
    assert {g.role: g.passes for g in result.groups} == {"integration": 3, "config": 3, "docs": 3}
    verdict = _verify(tmp_path, diff, _excerpts(diff, post, [g.members for g in dispatched]))
    assert verdict.passed, verdict.reason


def test_zero_pass_config_and_docs_leave_required_hunks_unwitnessed(tmp_path):
    """The reported failure: config and docs dispatched nothing, check 5 fails."""
    diff, post = _mixed(_FILES)
    result, dispatched = _dispatch(tmp_path, diff, _FILES, required=frozenset())
    assert {g.role: g.passes for g in result.groups} == {"integration": 3, "config": 0, "docs": 0}
    assert [g.role for g in dispatched] == ["integration"]
    verdict = _verify(tmp_path, diff, _excerpts(diff, post, [g.members for g in dispatched]))
    assert not verdict.passed
    assert "unwitnessed hunk .github/workflows/ci.yml" in verdict.reason, verdict.reason


def test_deletion_only_config_stays_unreviewed_and_still_verifies(tmp_path):
    files = {"src/app.py": "added", "trim.json": "deleted"}
    diff, post = _mixed(files)
    required = required_review_files(diff)
    assert required == {"src/app.py"}
    result, dispatched = _dispatch(tmp_path, diff, files, required=required)
    assert {g.role: g.passes for g in result.groups} == {"integration": 3, "config": 0}
    assert [g.role for g in dispatched] == ["integration"]
    verdict = _verify(tmp_path, diff, _excerpts(diff, post, [g.members for g in dispatched]))
    assert verdict.passed, verdict.reason


def test_file_scoped_fallback_witnesses_a_required_file_grouping_dropped(tmp_path):
    diff, post = _mixed(_FILES)
    required = required_review_files(diff)
    result, _ = _dispatch(tmp_path, diff, _FILES, required=frozenset())
    dispatched = review_groups(result, required)[0]
    assert {g.name for g in dispatched if g.role == "file"} == {
        "file:" + path
        for path in (".github/workflows/ci.yml", "README.md", "docs/guide.md", "schema.json")
    }
    verdict = _verify(tmp_path, diff, _excerpts(diff, post, [g.members for g in dispatched]))
    assert verdict.passed, verdict.reason
