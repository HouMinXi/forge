"""Receipt loading and excerpt validation retain their failure contracts.

The loader rejects a receipt that omits a list it requires, before any
check runs. A file deleted outright is exempt from the literal excerpt
check, and dropping that exemption changes the verdict. A line range of
0, 0 is rejected as nonpositive.
"""

import json

from code_forge.verify import (
    ExcerptStatus,
    assess_excerpt_evidence,
)
from tests.test_verify_result_contract import _failure, _setup, _verify


def _drop(rd, *keys, cycle=2):
    for perspective in (1, 2, 3):
        path = rd / f"receipt-c{cycle}p{perspective}.json"
        data = json.loads(path.read_text())
        for key in keys:
            data.pop(key, None)
        path.write_text(json.dumps(data))


def test_loader_rejects_a_receipt_that_omits_a_required_list(tmp_path):
    rd = _setup(tmp_path)
    _drop(rd, "code_excerpts")
    result = _verify(tmp_path)
    _failure(
        result,
        "corrupt receipt: receipt-c2p1.json: code_excerpts must be a list of objects",
        1,
        0,
    )


def test_dropping_the_exempt_list_changes_the_verdict():
    from code_forge.verify import _diff_validation_context

    diff = "diff --git a/mod.py b/mod.py\n--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +0,0 @@\n-a\n-b\n"
    post_image, hunk_map, exempt = _diff_validation_context(diff)
    exc = {"file": "mod.py", "start_line": 1, "end_line": 1, "content": "a"}
    kept = assess_excerpt_evidence(exc, hunk_map, post_image, exempt)
    dropped = assess_excerpt_evidence(exc, hunk_map, post_image, None)
    assert exempt == ["mod.py"]
    assert kept.status is ExcerptStatus.VALID
    assert dropped.status is ExcerptStatus.INVALID


def test_nonpositive_end_line_is_invalid():
    exc = {"file": "mod.py", "start_line": 0, "end_line": 0, "content": "a"}
    result = assess_excerpt_evidence(exc, {}, {})
    assert result.status is ExcerptStatus.INVALID
    assert result.diagnostic == "excerpt mod.py:0-0 has nonpositive or unordered range"
