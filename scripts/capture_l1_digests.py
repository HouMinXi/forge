#!/usr/bin/env python3
"""Capture the L1 prompt digests that Phase 59-B2 must preserve.

Run this on main BEFORE any context-sources change lands. It builds the
L1 prompt through build_l1_provider with llm_invoke mocked, for the four
blast-radius cases the plan names, and writes sha256 of every pass's
prompt to tests/fixtures/l1_prompt_digests_pre_p59.json. B2's byte-
identity test reads that file as its oracle.

The inputs are fixed strings so the digests are reproducible on any
machine with the same source tree.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from code_forge.baseline import ResolvedReview  # noqa: E402
from code_forge.llm_invoke import LLMResult, Usage  # noqa: E402
from code_forge import factories  # noqa: E402

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
FOCUS = ""

# Exactly what cli.py:3677-3681 renders for two rows.
TWO_ROWS = (
    "| Entity | File | Downstream | Top Dependents |\n"
    "|--------|------|------------|----------------|\n"
    "| f | f.py | 3 | g, h |\n"
    "| g | g.py | 1 | h |"
)

CASES = {
    "A_two_rows": TWO_ROWS,
    "B_empty": "",
    # C (exception) renders identically to B today: the except at
    # cli.py:3682 leaves _graph_impact_context == "".
    "C_exception_as_empty": "",
}


def capture(graph_ctx: str) -> dict[str, str]:
    prompts: list[str] = []

    def fake_invoke(prompt, **kw):
        prompts.append(prompt)
        return LLMResult(content={"findings": []}, usage=Usage())

    resolved = ResolvedReview(
        source_files=[Path("f.py")], baseline_content=None,
        git_diff=DIFF, mode_hint="git", base_sha="a" * 40, head_sha="b" * 40,
    )
    # llm_invoke is imported inside build_l1_provider (factories.py:255),
    # so the seam is the source module, not factories.
    with patch("code_forge.llm_invoke.llm_invoke", side_effect=fake_invoke):
        provider = factories.build_l1_provider(
            "auto", resolved, backend=None,
            conventions_digest=CONV, post_image=POST_IMAGE,
            graph_impact_context=graph_ctx, contract_spec=CONTRACT,
            focus_spec=FOCUS, manifest_spec=MANIFEST,
        )
        provider()
    assert len(prompts) == 3, "expected three L1 passes, got %d" % len(prompts)
    return {
        name: hashlib.sha256(p.encode("utf-8")).hexdigest()
        for name, p in zip(("qodo", "expert", "adversarial"), prompts)
    }


def main() -> int:
    out = {"source_tree": _git_head(), "cases": {}}
    for name, ctx in CASES.items():
        out["cases"][name] = capture(ctx)
        print(name, out["cases"][name]["qodo"][:16], "...")
    dest = ROOT / "tests" / "fixtures" / "l1_prompt_digests_pre_p59.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2) + "\n")
    print("wrote", dest)
    return 0


def _git_head() -> str:
    import subprocess
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
    ).stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
