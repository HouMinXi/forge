"""Strict evidence controls; these do not claim cross-repo orchestration is fixed."""
import pytest

from code_forge.cross_repo import build_cross_repo_context
from code_forge.verify import validate_excerpts_against_diff


PRIMARY = "diff --git a/main.py b/main.py\n--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
SIBLING = "diff --git a/sibling.py b/sibling.py\n--- a/sibling.py\n+++ b/sibling.py\n@@ -1 +1 @@\n-y = 1\n+y = 2\n"
EXCERPTS = [
    {"file": "main.py", "start_line": 1, "end_line": 1, "content": "x = 2"},
    {"file": "sibling.py", "start_line": 1, "end_line": 1, "content": "y = 2"},
]


def test_primary_only_rejects_sibling_but_joint_accepts():
    joint = build_cross_repo_context([
        {"label": "primary", "ref": "main..feature", "diff": PRIMARY},
        {"label": "sibling", "ref": "main..feature", "diff": SIBLING},
    ])
    assert validate_excerpts_against_diff(PRIMARY, EXCERPTS) == [
        "excerpt sibling.py:1 not in diff"]
    assert validate_excerpts_against_diff(joint, EXCERPTS) == []


@pytest.mark.parametrize("changes", [
    {"file": "third.py"},
    {"content": "x = 999"},
    {"start_line": 0},
    {"start_line": 2, "end_line": 2},
])
def test_invalid_evidence_stays_rejected(changes):
    excerpt = dict(EXCERPTS[0], **changes)
    assert validate_excerpts_against_diff(PRIMARY, [excerpt])
