# SPDX-License-Identifier: Apache-2.0
"""Phase 59-B2: wire context_sources into the L1 prompt without moving a byte.

Oracle: tests/fixtures/l1_prompt_digests_pre_p59.json, captured on main
24ed2e0 by scripts/capture_l1_digests.py before this change. Every case
here rebuilds the prompt the same way and compares sha256.

Cases:
  A  two blast-radius rows, no context_sources_text  -> digest unchanged
  B  no rows, no text                                -> digest unchanged
  C  exception-rendered-as-empty                     -> equals B
  D  two rows via gather()+render_blast_radius       -> equals A
  E  non-empty context_sources_text                  -> differs from A,
     and the section sits after Blast Radius, before Design Intent
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from code_forge import factories
from code_forge.baseline import ResolvedReview
from code_forge.context_sources import (
    FactRow, GatherResult, render_blast_radius, render_context_sources,
)
from code_forge.llm_invoke import LLMResult, Usage

ROOT = Path(__file__).resolve().parent.parent
ORACLE = json.loads(
    (ROOT / "tests" / "fixtures" / "l1_prompt_digests_pre_p59.json").read_text()
)["cases"]

# Same inputs as scripts/capture_l1_digests.py, verbatim.
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
TWO_ROWS = (
    "| Entity | File | Downstream | Top Dependents |\n"
    "|--------|------|------------|----------------|\n"
    "| f | f.py | 3 | g, h |\n"
    "| g | g.py | 1 | h |"
)
PASSES = ("qodo", "expert", "adversarial")


def _prompts(graph_ctx: str, context_sources_text: str = "") -> list[str]:
    out: list[str] = []

    def fake(prompt, **kw):
        out.append(prompt)
        return LLMResult(content={"findings": []}, usage=Usage())

    resolved = ResolvedReview(
        source_files=[Path("f.py")], baseline_content=None,
        git_diff=DIFF, mode_hint="git", base_sha="a" * 40, head_sha="b" * 40,
    )
    with patch("code_forge.llm_invoke.llm_invoke", side_effect=fake):
        factories.build_l1_provider(
            "auto", resolved, backend=None,
            conventions_digest=CONV, post_image=POST_IMAGE,
            graph_impact_context=graph_ctx, contract_spec=CONTRACT,
            focus_spec="", manifest_spec=MANIFEST,
            context_sources_text=context_sources_text,
        )()
    assert len(out) == 3
    return out


def _digests(prompts: list[str]) -> dict[str, str]:
    return {
        n: hashlib.sha256(p.encode()).hexdigest() for n, p in zip(PASSES, prompts)
    }


def test_case_a_two_rows_unchanged():
    assert _digests(_prompts(TWO_ROWS)) == ORACLE["A_two_rows"]


def test_case_b_empty_unchanged():
    assert _digests(_prompts("")) == ORACLE["B_empty"]


def test_case_c_exception_is_empty():
    # gather() with a raising source yields no rows; render gives "".
    from code_forge.context_sources import gather

    class _Boom:
        name = "boom"
        def snapshot_sha(self):
            return None
        def facts(self, changed_files, diff_text):
            raise RuntimeError("down")

    res = gather([_Boom()], ["f.py"], DIFF, head_sha=None)
    assert res.errors and res.rows == []
    assert _digests(_prompts(render_blast_radius(res.rows))) \
        == ORACLE["C_exception_as_empty"]


def test_case_d_rows_through_render_equal_a():
    rows = [
        FactRow("f", "f.py", "3", "g, h", "graph_triage"),
        FactRow("g", "g.py", "1", "h", "graph_triage"),
    ]
    assert render_blast_radius(rows) == TWO_ROWS
    assert _digests(_prompts(render_blast_radius(rows))) == ORACLE["A_two_rows"]


def test_case_e_context_text_changes_prompt_in_the_right_place():
    text = "| Source | Entity | File | Line | Note |\n|--|--|--|--|--|\n| mcp:kb | x | x.py | 7 | n |"
    prompts = _prompts(TWO_ROWS, context_sources_text=text)
    assert _digests(prompts) != ORACLE["A_two_rows"]
    for p in prompts:
        i_br = p.index("## Blast Radius Context")
        i_cs = p.index("## Context Sources")
        i_di = p.index("## Design Intent")
        assert i_br < i_cs < i_di
        assert text in p


def test_empty_context_text_adds_no_section():
    for p in _prompts(TWO_ROWS, context_sources_text=""):
        assert "## Context Sources" not in p


def test_sampling_variant_carries_the_section():
    """build_sampling_l1_provider is not covered by the digest oracle.
    Run its coroutine on a real loop in a thread so invoke_sampling is
    actually reached, and assert the section on the captured prompts."""
    import asyncio
    import threading
    from unittest.mock import MagicMock

    captured: list[str] = []

    async def fake_invoke_sampling(session, prompt, **kw):
        captured.append(prompt)
        return LLMResult(content={"findings": []}, usage=Usage())

    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    try:
        resolved = ResolvedReview(
            source_files=[Path("f.py")], baseline_content=None,
            git_diff=DIFF, mode_hint="git",
        )
        with patch("code_forge.llm_invoke.invoke_sampling", side_effect=fake_invoke_sampling):
            factories.build_sampling_l1_provider(
                MagicMock(), loop, resolved, context_sources_text="CTX-MARK",
            )()
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=5)
        loop.close()
    assert len(captured) == 3
    assert all("\n## Context Sources\nCTX-MARK\n" in p for p in captured)


def test_cli_wires_every_provider_construction_site():
    """The three build_*_l1_provider calls in cli._run must all pass
    context_sources_text, and the old inline blast-radius block must be
    gone (a second copy would silently win on the prompt)."""
    import ast
    import inspect

    from code_forge import cli

    src = inspect.getsource(cli._run)
    tree = ast.parse(src)
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id in ("build_l1_provider", "build_grouped_l1_provider")
    ]
    assert len(calls) == 3, [c.func.id for c in calls]
    for c in calls:
        kws = {k.arg for k in c.keywords}
        assert "context_sources_text" in kws, c.func.id
        assert "graph_impact_context" in kws, c.func.id
    # The old block parsed descriptions inline; that string must not be
    # in cli any more (it lives in context_sources._adapt_advisory).
    assert '" (impact: "' not in src
    assert "gather(" in src and "render_blast_radius(" in src
