"""A small JSON-to-frozen-source corpus; JSON validity is not evidence validity."""

import copy
import json

import pytest

from code_forge.llm_invoke import loads_reply_json
from code_forge.reviewer_json import validate_reviewer_json
from code_forge.verify import ExcerptStatus, assess_excerpt_evidence


CASES = [
    pytest.param(
        r"""command = "C:\\private\\task"; quote = '"' """,
        r"""command = "C:\private\task"; quote = '"' """,
        "\n",
        True,
        True,
        id="quotes-and-halved-backslashes",
    ),
    pytest.param(
        r"""payload = '{"path":"C:\\private","quote":"\""}' """,
        r"""payload = '{"path":"C:\private","quote":"\""}' """,
        "\n",
        True,
        False,
        id="nested-json-is-source-not-another-decoding-layer",
    ),
    pytest.param(
        'label = "左\u2028右"',
        'label = "左 右"',
        "\n",
        False,
        False,
        id="literal-unicode-line-separator",
    ),
    pytest.param(
        'label = "左\u2029右"',
        'label = "左 右"',
        "\n",
        True,
        False,
        id="json-escaped-unicode-paragraph-separator",
    ),
    pytest.param(
        'label = "left\fright"',
        'label = "left right"',
        "\n",
        True,
        False,
        id="formfeed-is-source-content",
    ),
    pytest.param(
        r'label = "left\nright"',
        'label = "left\nright"',
        "\n",
        True,
        False,
        id="literal-backslash-n-is-not-a-source-newline",
    ),
    pytest.param(
        'label = "quoted"',
        "label = 'quoted'",
        "\n",
        True,
        False,
        id="lf-physical-lines",
    ),
    pytest.param(
        'label = "quoted"',
        "label = 'quoted'",
        "\r\n",
        True,
        False,
        id="crlf-physical-lines",
    ),
]


@pytest.mark.parametrize("source,damaged,ending,ensure_ascii,escaping_hint", CASES)
def test_json_roundtrip_then_frozen_evidence(source, damaged, ending, ensure_ascii, escaping_hint):
    # The second source line prevents a shortened first quote from qualifying
    # as the existing truncated-tail recovery. CRLF is accepted as a line ending,
    # while embedded Unicode separators and formfeed remain source characters.
    tail = "assert_ready()"
    frozen = {"sample.txt": {1: source, 2: tail}}
    frozen_before = copy.deepcopy(frozen)
    for line, expected in ((source, ExcerptStatus.VALID), (damaged, ExcerptStatus.INVALID)):
        content = ending.join((line, tail)) + ending
        payload = {
            "findings": [],
            "code_excerpts": [
                {"file": "sample.txt", "start_line": 1, "end_line": 2, "content": content}
            ],
        }
        raw = json.dumps(payload, ensure_ascii=ensure_ascii)
        decoded = loads_reply_json(raw)
        assert decoded == payload
        assert decoded["code_excerpts"][0]["content"].encode("utf-8") == content.encode("utf-8")
        validated = validate_reviewer_json(decoded)
        excerpt = validated["code_excerpts"][0]
        assert decoded == payload
        assert excerpt["content"] == content
        before = copy.deepcopy(excerpt)
        assessment = assess_excerpt_evidence(excerpt, {"sample.txt": [{"start": 1, "end": 2}]}, frozen)
        assert assessment.status is expected
        assert excerpt == before
        assert loads_reply_json(raw) == payload
        assert frozen == frozen_before
        if expected is ExcerptStatus.VALID:
            assert assessment.diagnostic is None
            assert assessment.proven_lines == frozenset({1, 2})
        else:
            assert not assessment.proven_lines
            assert bool("possible JSON backslash-escaping damage" in assessment.diagnostic) is (
                escaping_hint
            )
            assert line not in assessment.diagnostic
            assert source not in assessment.diagnostic
