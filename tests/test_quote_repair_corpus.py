"""Bounded replacement evidence for #265, NOT the lost five fuzzers.

The review-box /tmp/fz216{b,r,me} generators are not available here. This
stdlib-only corpus cannot reproduce their totals or establish #265 closure.
Inputs below come from #216 review examples; originals are known before
selected value-quote escapes are removed. Every subset is enumerated, with
at most eight content quotes per original and two reply layouts (720 cases).

Oracle: unchanged valid JSON is returned unchanged, even when removing
escapes changed its meaning. For invalid JSON, refusal is acceptable;
acceptance must equal the known original. Structural corruptions are a
separate cohort and must raise. No test requires ambiguous content to repair.

Provenance:
https://github.com/HouMinXi/forge/issues/265
https://github.com/HouMinXi/forge/pull/216#issuecomment-6262018713
https://github.com/HouMinXi/forge/pull/216#issuecomment-6262306363
https://github.com/HouMinXi/forge/pull/216#issuecomment-6266170525
https://github.com/HouMinXi/forge/pull/216#issuecomment-6266315871
"""

import json
from collections import Counter

import pytest

from code_forge.llm_invoke import ReplyKeys, _loads_model_json


# Source-code excerpts from the reviews above, not randomly generated prose.
_EXCERPTS = (
    'parts = line.split(" ")',
    'print(a, " ", b)',
    'indent = " " * 4',
    'd = {"k": " "} is used',
    'if c == " " or c == ":":',
    's.replace(" ", "_")',
    'x.split(", ")',
    '", ".join(xs)',
    'print("a", "b")',
    'cfg = {"a": "b", "c": "d"}',
    '"name": "forge",',
    'returns "None"',
)


def _originals():
    for index, excerpt in enumerate(_EXCERPTS):
        item = {"excerpt": excerpt, "line": 3}
        yield f"excerpt-{index}-object", item, ReplyKeys(top=frozenset(item))
        yield (
            f"excerpt-{index}-findings",
            {"findings": [item]},
            ReplyKeys(top=frozenset({"findings"}), items={"findings": frozenset(item)}),
        )


def _content_cases():
    for name, original, contract in _originals():
        raw = json.dumps(original)
        positions = [index for index in range(len(raw) - 1) if raw[index : index + 2] == '\\"']
        assert len(positions) <= 8
        for mask in range(1 << len(positions)):
            removed = {position for bit, position in enumerate(positions) if mask & (1 << bit)}
            mutated = "".join(char for index, char in enumerate(raw) if index not in removed)
            yield f"{name}-mask-{mask}", mutated, original, contract


def _structural_cases():
    for name, original, contract in _originals():
        raw = json.dumps(original)
        in_string = False
        escaped = False
        for index, char in enumerate(raw):
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char in ",:":
                # Only structural separators outside strings; content remains
                # correctly escaped. Never conflate these with content repairs.
                for operation, replacement in (("delete", ""), ("double", char + char)):
                    yield (
                        f"{name}-{operation}-{index}",
                        raw[:index] + replacement + raw[index + 1 :],
                        contract,
                    )


_CONTENT = tuple(_content_cases())
_STRUCTURAL = tuple(_structural_cases())


def _classify(raw, original, contract):
    try:
        strict = json.loads(raw)
    except json.JSONDecodeError:
        pass
    else:
        # Two partial-escape mutations become valid JSON with a new c key.
        # There is no repair and no recoverable evidence of the original.
        # ReplyKeys intentionally checks unknown keys only after a repair.
        assert _loads_model_json(raw, contract) == strict
        return "valid_unchanged" if strict == original else "valid_changed_meaning"
    try:
        actual = _loads_model_json(raw, contract)
    except json.JSONDecodeError:
        return "refused"
    assert actual == original, (raw, original, actual)
    return "repaired_exactly"


@pytest.mark.parametrize(
    ("raw", "original", "contract"),
    [case[1:] for case in _CONTENT],
    ids=[case[0] for case in _CONTENT],
)
def test_content_mutation_is_exact_or_refused(raw, original, contract):
    _classify(raw, original, contract)


@pytest.mark.parametrize(
    ("raw", "contract"),
    [case[1:] for case in _STRUCTURAL],
    ids=[case[0] for case in _STRUCTURAL],
)
def test_structural_separator_corruption_is_refused(raw, contract):
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)
    with pytest.raises(json.JSONDecodeError):
        _loads_model_json(raw, contract)


def test_corpus_size_and_valid_json_ambiguity_are_explicit():
    assert len(_CONTENT) == 720
    assert len(_STRUCTURAL) == 168
    counts = Counter(_classify(raw, original, contract) for _, raw, original, contract in _CONTENT)
    assert counts["valid_changed_meaning"] == 2
    assert counts["valid_unchanged"] == 24
    # Do not freeze the repair/refusal split: a future safe repair may reduce
    # refusals. The per-case exact-equality assertion remains the invariant.
    assert counts["repaired_exactly"] + counts["refused"] == 694


@pytest.mark.parametrize(
    "raw",
    [
        # #265's realistic example, completed as an object.
        '{"description": "returns "None"", "excerpt": ""name": "forge","}',
        # #216 comment 6266315871: nested earlier repair is conservative too.
        '{"o": {"d": "say "hi""}, "e": ""name": "forge","}',
    ],
)
def test_historical_same_container_ambiguity_still_fails_closed(raw):
    with pytest.raises(json.JSONDecodeError):
        _loads_model_json(raw)
