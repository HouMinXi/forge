# SPDX-License-Identifier: Apache-2.0
"""Phase 59.1 Task 4: lens bytes in the Outlet A and Outlet C prompts."""

from __future__ import annotations

import inspect
import os
from pathlib import Path
from unittest.mock import patch

from code_forge import cli, factories
from code_forge.baseline import ResolvedReview
from code_forge.cli import _estimate_l1_prompt_tokens, _make_subagent_spawn
from code_forge.diff import annotated_diff_prompt_block
from code_forge.lenses import LENS_BLOCK, PASS_LENSES, selection_line
from code_forge.llm_invoke import LLMResult, Usage
from code_forge.reviewer_json import REVIEW_JSON_CONTRACT

PASSES = ("qodo", "expert", "adversarial")
ROLES = {
    "qodo": "structural code reviewer: correctness and logic errors",
    "expert": "senior engineer: SOLID, architecture, security",
    "adversarial": "adversarial QE: assume bugs exist",
}
DIFF = (
    "diff --git a/f.py b/f.py\n"
    "--- a/f.py\n"
    "+++ b/f.py\n"
    "@@ -1,2 +1,3 @@\n"
    " def f(x):\n"
    "-    return x\n"
    "+    y = x + 1\n"
    "+    return y\n"
)
POST_IMAGE = "## f.py\n```python\ndef f(x):\n    y = x + 1\n    return y\n```\n"
CONV = "snake_case functions; no bare except"
CONTRACT = "f must return input plus one"
MANIFEST = "## Environment\npython 3.14"
TWO_ROWS = "| Entity | File |\n|--|--|\n| f | f.py |"


def _resolved(diff: str = DIFF) -> ResolvedReview:
    return ResolvedReview(
        source_files=[Path("f.py")],
        baseline_content=None,
        git_diff=diff,
        mode_hint="git",
        base_sha="a" * 40,
        head_sha="b" * 40,
    )


def _fake(out: list[str]):
    def fake(prompt, **kw):
        out.append(prompt)
        return LLMResult(content={"findings": []}, usage=Usage())

    return fake


def _a_prompts(**kw) -> list[str]:
    out: list[str] = []
    with patch("code_forge.llm_invoke.llm_invoke", side_effect=_fake(out)):
        factories.build_l1_provider(
            "auto",
            _resolved(),
            backend=None,
            conventions_digest=CONV,
            post_image=POST_IMAGE,
            graph_impact_context=TWO_ROWS,
            contract_spec=CONTRACT,
            focus_spec="focus on auth",
            manifest_spec=MANIFEST,
            **kw,
        )()
    assert len(out) == 3
    return out


def _c_prompts(**kw) -> list[str]:
    out: list[str] = []
    with patch("code_forge.llm_invoke.llm_invoke", side_effect=_fake(out)):
        spawn = _make_subagent_spawn(
            backend=None,
            conv_digest=CONV,
            post_image=POST_IMAGE,
            contract_spec=CONTRACT,
            focus_spec="focus on auth",
            manifest_spec=MANIFEST,
            **kw,
        )
        for name in PASSES:
            spawn(name, DIFF)
    assert len(out) == 3
    return out


def _strip_a(prompt: str, pass_name: str) -> str:
    assert prompt.count(LENS_BLOCK) == 1
    assert prompt.endswith(selection_line(pass_name))
    return prompt.replace(LENS_BLOCK, "", 1)[: -len(selection_line(pass_name))]


def test_review_lenses_defaults_to_off_everywhere():
    for fn in (factories.build_l1_provider, _make_subagent_spawn, cli._dispatch_subagent):
        param = inspect.signature(fn).parameters["review_lenses"]
        assert param.default is False, fn.__name__


def test_off_is_the_same_as_the_default():
    assert _a_prompts(review_lenses=False) == _a_prompts()
    assert _c_prompts(review_lenses=False) == _c_prompts()


def test_off_prompts_carry_no_lens_text():
    for prompt in _a_prompts() + _c_prompts():
        assert "Review Lenses" not in prompt
        assert "\nLenses: " not in prompt


def test_a_on_places_block_after_contract_and_selection_line_last():
    for pass_name, prompt in zip(PASSES, _a_prompts(review_lenses=True), strict=True):
        assert (REVIEW_JSON_CONTRACT + LENS_BLOCK) in prompt
        role_sentence = "\nYou are a " + ROLES[pass_name] + ". Review this diff.\n"
        assert prompt.endswith(role_sentence + selection_line(pass_name))


def test_a_on_strips_back_to_the_off_prompt():
    on = _a_prompts(review_lenses=True)
    off = _a_prompts()
    for pass_name, a, b in zip(PASSES, on, off, strict=True):
        assert _strip_a(a, pass_name) == b


def test_a_on_keeps_block_and_diff_in_the_shared_prefix_and_tails_differ():
    prompts = _a_prompts(review_lenses=True)
    common = os.path.commonprefix(prompts)
    assert LENS_BLOCK in common
    assert annotated_diff_prompt_block(DIFF) in common
    for pass_name in PASSES:
        assert ROLES[pass_name] not in common
    tails = [p[len(common) :] for p in prompts]
    assert len(set(tails)) == 3


def test_a_selection_lines_follow_the_pass_assignment():
    prompts = _a_prompts(review_lenses=True)
    for pass_name, p in zip(PASSES, prompts, strict=True):
        assert p.endswith(selection_line(pass_name))
        for lens in PASS_LENSES[pass_name]:
            assert lens in selection_line(pass_name)


def test_c_on_places_block_after_role_and_contract_and_selection_line_last():
    for pass_name, prompt in zip(PASSES, _c_prompts(review_lenses=True), strict=True):
        head = "You are a " + ROLES[pass_name] + ". Review this diff.\n" + REVIEW_JSON_CONTRACT
        assert prompt.startswith(head + LENS_BLOCK)
        assert prompt.endswith(annotated_diff_prompt_block(DIFF) + selection_line(pass_name))


def test_c_on_strips_back_to_the_off_prompt():
    on = _c_prompts(review_lenses=True)
    off = _c_prompts()
    for pass_name, a, b in zip(PASSES, on, off, strict=True):
        assert _strip_a(a, pass_name) == b


def test_c_without_optional_blocks_still_strips_back():
    out: list[str] = []
    with patch("code_forge.llm_invoke.llm_invoke", side_effect=_fake(out)):
        for flag in (False, True):
            spawn = _make_subagent_spawn(None, "", "", review_lenses=flag)
            spawn("expert", DIFF)
    off, on = out
    assert _strip_a(on, "expert") == off


def _estimate(**kw) -> int:
    return _estimate_l1_prompt_tokens(DIFF, POST_IMAGE, CONV, TWO_ROWS, CONTRACT, MANIFEST, "", **kw)


def test_estimator_adds_the_lens_bytes_by_keyword():
    param = inspect.signature(_estimate_l1_prompt_tokens).parameters["lens_text"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default == ""
    lens_text = LENS_BLOCK + selection_line("expert")
    delta = _estimate(lens_text=lens_text) - _estimate()
    assert abs(delta - (1567 + 17) // 4) <= 1
    assert delta == (1567 + 17) // 4


def test_estimator_default_is_unchanged():
    assert _estimate() == _estimate(lens_text="")


def test_grouped_provider_forwards_the_flag():
    out: list[str] = []
    spec = {
        "name": "g1",
        "resolved": _resolved(),
        "post_image": POST_IMAGE,
        "conventions_digest": CONV,
        "split_context": "",
    }
    with patch("code_forge.llm_invoke.llm_invoke", side_effect=_fake(out)):
        factories.build_grouped_l1_provider("auto", [spec], backend=None, review_lenses=True)()
        on = list(out)
        out.clear()
        factories.build_grouped_l1_provider("auto", [spec], backend=None)()
        off = list(out)
    assert len(on) == 3 and len(off) == 3
    assert all(LENS_BLOCK in p for p in on)
    assert not any("Review Lenses" in p for p in off)
