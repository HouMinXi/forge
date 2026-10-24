#!/usr/bin/env python3
"""Check, and extend, the L1 prompt digests that byte-identity tests use as an oracle.

It builds the L1 prompt through build_l1_provider with llm_invoke mocked,
for the fixed blast-radius cases below, and compares sha256 of every pass's
prompt to tests/fixtures/l1_prompt_digests_pre_p59.json.

This script never overwrites the fixture:

* no flags: recompute the off-path digests (and the ``*_lenses`` digests when
  the fixture has them) and compare. Exit 1 when anything moved, is missing
  or is extra; a moved off-path digest needs a revision written by hand.
* ``--lenses --reason TEXT``: append the ``*_lenses`` cases (built with
  review_lenses=True) once, keeping source_tree and every existing case, and
  wrap the old revision as ``previous``. Refused when lens cases exist.

The inputs are fixed strings so the digests are reproducible on any
machine with the same source tree.
"""

from __future__ import annotations

import argparse
import copy
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


def capture(graph_ctx: str, review_lenses: bool = False) -> dict[str, str]:
    prompts: list[str] = []

    def fake_invoke(prompt, **kw):
        prompts.append(prompt)
        return LLMResult(content={"findings": []}, usage=Usage())

    resolved = ResolvedReview(
        source_files=[Path("f.py")],
        baseline_content=None,
        git_diff=DIFF,
        mode_hint="git",
        base_sha="a" * 40,
        head_sha="b" * 40,
    )
    # llm_invoke is imported inside build_l1_provider (factories.py:255),
    # so the seam is the source module, not factories.
    with patch("code_forge.llm_invoke.llm_invoke", side_effect=fake_invoke):
        provider = factories.build_l1_provider(
            "auto",
            resolved,
            backend=None,
            conventions_digest=CONV,
            post_image=POST_IMAGE,
            graph_impact_context=graph_ctx,
            contract_spec=CONTRACT,
            focus_spec=FOCUS,
            manifest_spec=MANIFEST,
            review_lenses=review_lenses,
        )
        provider()
    assert len(prompts) == 3, "expected three L1 passes, got %d" % len(prompts)
    return {
        name: hashlib.sha256(p.encode("utf-8")).hexdigest()
        for name, p in zip(("qodo", "expert", "adversarial"), prompts, strict=True)
    }


FIXTURE = ROOT / "tests" / "fixtures" / "l1_prompt_digests_pre_p59.json"


class DigestsMoved(RuntimeError):
    pass


def recompute(review_lenses: bool) -> dict[str, dict[str, str]]:
    suffix = "_lenses" if review_lenses else ""
    return {name + suffix: capture(ctx, review_lenses) for name, ctx in CASES.items()}


def _split(cases: dict) -> tuple[dict, dict]:
    off = {k: v for k, v in cases.items() if not k.endswith("_lenses")}
    on = {k: v for k, v in cases.items() if k.endswith("_lenses")}
    return off, on


def check(stored: dict, recomputed_off: dict, recomputed_lens: dict) -> None:
    off_stored, lens_stored = _split(stored["cases"])
    if recomputed_off != off_stored:
        raise DigestsMoved("off-path digests moved; write a revision by hand")
    if lens_stored and recomputed_lens != lens_stored:
        bad = sorted(
            k
            for k in set(lens_stored) | set(recomputed_lens)
            if lens_stored.get(k) != recomputed_lens.get(k)
        )
        raise DigestsMoved("lens digests differ from a fresh review_lenses=True build: %s" % bad)


def add_lenses(stored: dict, recomputed_off: dict, recomputed_lens: dict, reason: str) -> dict:
    off_stored, lens_stored = _split(stored["cases"])
    if recomputed_off != off_stored:
        raise DigestsMoved("off-path digests moved; write a revision by hand")
    if lens_stored:
        raise ValueError("lens cases already present: %s" % sorted(lens_stored))
    if not recomputed_lens or not all(k.endswith("_lenses") for k in recomputed_lens):
        raise ValueError("expected only *_lenses cases")
    out = copy.deepcopy(stored)
    out["cases"].update(recomputed_lens)
    out["revision"] = {
        "reason": reason,
        "added": sorted(recomputed_lens),
        "previous": stored.get("revision"),
    }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fixture", type=Path, default=FIXTURE)
    ap.add_argument("--lenses", action="store_true", help="append the *_lenses cases once")
    ap.add_argument("--reason", help="revision reason; required with --lenses")
    args = ap.parse_args(argv)
    if args.lenses and not args.reason:
        ap.error("--lenses requires --reason")

    stored = json.loads(args.fixture.read_text())
    recomputed_off = recompute(False)
    try:
        if args.lenses:
            new = add_lenses(stored, recomputed_off, recompute(True), args.reason)
            args.fixture.write_text(json.dumps(new, indent=2) + "\n")
            print("added", ", ".join(new["revision"]["added"]), "to", args.fixture)
            return 0
        lens_stored = _split(stored["cases"])[1]
        check(stored, recomputed_off, recompute(True) if lens_stored else {})
    except DigestsMoved as exc:
        print("error:", exc, file=sys.stderr)
        return 1
    except ValueError as exc:
        print("error:", exc, file=sys.stderr)
        return 2
    print("digests match", args.fixture)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
