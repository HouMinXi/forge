"""JSON encoding damage is a rejected evidence diagnostic, not a repair."""

import copy
import json

import pytest

from code_forge.llm_invoke import loads_reply_json
from code_forge.reviewer_json import REVIEW_JSON_CONTRACT, validate_reviewer_json
from code_forge.verify import ExcerptStatus, assess_excerpt_evidence


SOURCE = "def command = 'private\\\\windows\\\\task.ps1'"


def _assessment(content):
    raw = json.dumps(
        {
            "findings": [],
            "code_excerpts": [
                {"file": "pipeline.groovy", "start_line": 78, "end_line": 78, "content": content}
            ],
        }
    )
    decoded = loads_reply_json(raw)
    original = copy.deepcopy(decoded)
    validated = validate_reviewer_json(decoded)
    excerpt = validated["code_excerpts"][0]
    before = copy.deepcopy(excerpt)
    assessment = assess_excerpt_evidence(
        excerpt,
        {"pipeline.groovy": [{"start": 78, "end": 78}]},
        {"pipeline.groovy": {78: SOURCE}},
    )
    assert excerpt == before
    assert decoded == original
    assert loads_reply_json(raw) == original
    return assessment


def test_correct_json_encoding_preserves_literal_source():
    assert SOURCE.count("\\") == 4
    assert json.dumps(SOURCE).count("\\") == 8
    assessment = _assessment(SOURCE)
    assert assessment.status is ExcerptStatus.VALID
    assert assessment.diagnostic is None


def test_halved_backslashes_are_rejected_with_private_encoding_diagnostic():
    damaged = SOURCE.replace("\\\\", "\\")
    assert damaged.count("\\") == 2
    assessment = _assessment(damaged)
    assert assessment.status is ExcerptStatus.INVALID
    assert not assessment.proven_lines
    assert "possible JSON backslash-escaping damage in reviewer evidence" in assessment.diagnostic
    assert "not a source-code finding" in assessment.diagnostic
    assert "evidence remains rejected" in assessment.diagnostic
    assert "private" not in assessment.diagnostic
    assert "task.ps1" not in assessment.diagnostic
    assert SOURCE not in assessment.diagnostic
    assert damaged not in assessment.diagnostic


@pytest.mark.parametrize(
    "damaged",
    [
        SOURCE.replace("private", "changed").replace("\\\\", "\\"),
        SOURCE.replace("\\\\", "\\", 1),
        SOURCE.replace("\\\\", "\\\\\\"),
        SOURCE.replace("\\", "/"),
        SOURCE.replace("private", "changed"),
    ],
)
def test_other_mismatches_do_not_claim_halved_encoding(damaged):
    assessment = _assessment(damaged)
    assert assessment.status is ExcerptStatus.INVALID
    assert not assessment.proven_lines
    assert "excerpt content mismatch" in assessment.diagnostic
    assert "backslash-escaping" not in assessment.diagnostic


def test_review_contract_explains_source_and_json_layers():
    assert "including finding excerpts" in REVIEW_JSON_CONTRACT
    assert "after JSON decoding, they must match the source literally" in REVIEW_JSON_CONTRACT
    assert "two consecutive source backslashes require four in JSON" in REVIEW_JSON_CONTRACT
    assert "Do not interpret source-language string escapes" in REVIEW_JSON_CONTRACT
