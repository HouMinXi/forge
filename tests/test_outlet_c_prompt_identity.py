# SPDX-License-Identifier: Apache-2.0
"""Outlet C off-path prompts stay byte-identical to their recorded base.

Oracle: tests/fixtures/outlet_c_prompt_digests_pre_lens.json. The base moved
from 609aa51 to 2238ea66 when the post-image annotation prefix became part of
the prompt text; the digests were recomputed there and the reason recorded. This module only reads it
and never imports the script's write path.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

from code_forge.cli import _make_subagent_spawn
from code_forge.llm_invoke import LLMResult, Usage

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "outlet_c_prompt_digests_pre_lens.json"
_BYTES_AT_IMPORT = FIXTURE.read_bytes()
_MTIME_AT_IMPORT = os.stat(FIXTURE).st_mtime_ns
ORACLE = json.loads(_BYTES_AT_IMPORT)

BASE = "2238ea6659e12832e40beb3f726559c560c66594"
PASSES = ("qodo", "expert", "adversarial")

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

LITERALS = {
    "C_full": {
        "qodo": "9b94797c9ef5633369ba2c902c8f247db8b18165f4f19beb579edeff0c2c270b",
        "expert": "999671748027386676ba1be47bdb6cfefb25a49e5589212b6f1c951416314349",
        "adversarial": "9f230c689a68a6451558935afbd3befdd17627bf7b2f81bd22638fa3fe974bcd",
    },
    "C_minimal": {
        "qodo": "64edb47fd1d8abc4cee2532337732226a227fedac76cf917ca474be2cc1a82d9",
        "expert": "a27585ae98529767757c268c8873caaa3cc061f7a63f5771d2c9da0548894197",
        "adversarial": "9018591eeec8b921d2279788ab4196caa7bf7497215a39c33e4f251cbc334c75",
    },
}


def _prompts(**kw) -> list[str]:
    out: list[str] = []

    def fake(prompt, **_):
        out.append(prompt)
        return LLMResult(content={"findings": []}, usage=Usage())

    with patch("code_forge.llm_invoke.llm_invoke", side_effect=fake):
        spawn = _make_subagent_spawn(backend=None, **kw)
        for name in PASSES:
            spawn(name, DIFF)
    assert len(out) == 3
    return out


def _digests(prompts: list[str]) -> dict[str, str]:
    return {n: hashlib.sha256(p.encode()).hexdigest() for n, p in zip(PASSES, prompts, strict=True)}


def test_fixture_is_bound_to_the_base_commit():
    assert ORACLE["source_tree"] == BASE
    assert ORACLE["outlet"] == "C"


def test_fixture_matches_the_hard_coded_digests():
    assert ORACLE["cases"] == LITERALS


def test_case_c_full_off_path_unchanged():
    got = _digests(
        _prompts(
            conv_digest=CONV,
            post_image=POST_IMAGE,
            contract_spec=CONTRACT,
            focus_spec="",
            manifest_spec=MANIFEST,
        )
    )
    assert got == LITERALS["C_full"]
    assert got == ORACLE["cases"]["C_full"]


def test_case_c_minimal_off_path_unchanged():
    got = _digests(_prompts(conv_digest="", post_image=""))
    assert got == LITERALS["C_minimal"]
    assert got == ORACLE["cases"]["C_minimal"]


def test_oracle_file_is_never_rewritten_by_this_module():
    # Rebuild once more so the assertion covers real use of the fixture.
    _prompts(conv_digest="", post_image="")
    assert FIXTURE.read_bytes() == _BYTES_AT_IMPORT
    assert os.stat(FIXTURE).st_mtime_ns == _MTIME_AT_IMPORT
