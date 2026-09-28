"""Exercise excerpt collection and repair decisions on decoded model JSON."""

from code_forge.llm_invoke import (
    _excerpts_from,
    _loads_model_json,
    _needs_excerpt_repair,
)


def test_empty_root_excerpts_fall_back_to_nested_excerpts():
    parsed = _loads_model_json(
        '{"code_excerpts": [], "findings": [{"code_excerpts": ['
        '{"file": "a.py", "start_line": 1, "end_line": 1, '
        '"content": "x = 1", "rationale": "checked"}]}]}'
    )
    expected = [
        {
            "file": "a.py",
            "start_line": 1,
            "end_line": 1,
            "content": "x = 1",
            "rationale": "checked",
        }
    ]
    result = _excerpts_from(parsed)
    assert result == expected
    assert result is not parsed["findings"][0]["code_excerpts"]
    assert parsed == {"code_excerpts": [], "findings": [{"code_excerpts": expected}]}


def test_non_object_finding_does_not_hide_later_excerpts():
    assert _needs_excerpt_repair(_loads_model_json('{"findings": [null]}'), None) is True
    parsed = _loads_model_json(
        '{"findings": [null, {"code_excerpts": ['
        '{"file": "a.py", "start_line": 1, "end_line": 1, '
        '"content": "x = 1", "rationale": "checked"}]}]}'
    )
    assert _needs_excerpt_repair(parsed, None) is False


def test_empty_nested_excerpts_still_need_repair():
    parsed = _loads_model_json('{"findings": [{"code_excerpts": []}]}')
    assert _needs_excerpt_repair(parsed, None) is True
