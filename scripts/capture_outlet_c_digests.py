#!/usr/bin/env python3
"""Capture or check the off-path Outlet C prompt digests at the 609aa51 base.

Mirrors scripts/capture_l1_digests.py but drives cli._make_subagent_spawn with
llm_invoke mocked at its source module. Inputs reuse the capture script's fixed
strings so the two oracles share one input set.

--write PATH creates the fixture once and refuses to overwrite it; --check PATH
only compares. The fixture records the base commit as `source_tree`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import capture_l1_digests as cap  # noqa: E402  (fixed inputs only; main() not called)
from code_forge.cli import _make_subagent_spawn  # noqa: E402
from code_forge.llm_invoke import LLMResult, Usage  # noqa: E402

BASE = "609aa5133076d77b4e17c77b2e82063418e8518e"
PASSES = ("qodo", "expert", "adversarial")

CASES = {
    "C_full": dict(
        conv_digest=cap.CONV,
        post_image=cap.POST_IMAGE,
        contract_spec=cap.CONTRACT,
        focus_spec="",
        manifest_spec=cap.MANIFEST,
    ),
    "C_minimal": dict(conv_digest="", post_image=""),
}


def capture(kw):
    prompts = []

    def fake(prompt, **_):
        prompts.append(prompt)
        return LLMResult(content={"findings": []}, usage=Usage())

    with patch("code_forge.llm_invoke.llm_invoke", side_effect=fake):
        spawn = _make_subagent_spawn(backend=None, **kw)
        for p in PASSES:
            spawn(p, cap.DIFF)
    return {p: hashlib.sha256(s.encode()).hexdigest() for p, s in zip(PASSES, prompts, strict=True)}


def build():
    return {name: capture(kw) for name, kw in CASES.items()}


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def _write_refusal():
    """Refuse unless everything the prompts read is byte-for-byte the base.

    HEAD alone is not a guard: the admission commit sits on top of BASE and no
    task commits, so the working tree is what must equal BASE under src/ and
    scripts/. HEAD must descend from BASE and must not have moved code, scripts
    or tests since.
    """
    if _git("merge-base", "--is-ancestor", BASE, "HEAD").returncode != 0:
        return "HEAD does not descend from %s" % BASE
    if _git("diff", "--quiet", BASE, "HEAD", "--", "src/", "scripts/", "tests/").returncode != 0:
        return "HEAD changed src/, scripts/ or tests/ relative to %s" % BASE
    if _git("diff", "--quiet", BASE, "--", "src/", "scripts/capture_l1_digests.py").returncode != 0:
        return "src/ or scripts/capture_l1_digests.py differs from %s" % BASE
    st = _git("status", "--porcelain", "--", "src", "scripts/capture_l1_digests.py")
    if st.returncode != 0 or st.stdout.strip():
        return "untracked or modified files under src/: " + st.stdout.strip()[:200]
    return ""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--write", metavar="PATH", help="write once; refuses if PATH exists or the tree is not the base"
    )
    g.add_argument("--check", metavar="PATH", help="compare only, never writes")
    a = ap.parse_args(argv)
    if a.check:
        ref = json.loads(Path(a.check).read_text())
        ok = ref["source_tree"] == BASE and ref["cases"] == build()
        print("check", "ok" if ok else "MISMATCH")
        return 0 if ok else 1
    why = _write_refusal()
    if why:
        print("refusing:", why, file=sys.stderr)
        return 1
    dest = Path(a.write)
    body = (
        json.dumps({"source_tree": BASE, "outlet": "C", "cases": build()}, indent=2, sort_keys=True)
        + "\n"
    )
    try:
        # "x" is O_CREAT|O_EXCL: it fails if the path exists, even as a dangling
        # symlink, and leaves no exists()/write window.
        with open(dest, "x", encoding="utf-8") as fh:
            fh.write(body)
    except FileExistsError:
        print("refusing: oracle exists:", dest, file=sys.stderr)
        return 1
    print("wrote", dest, hashlib.sha256(dest.read_bytes()).hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main())
