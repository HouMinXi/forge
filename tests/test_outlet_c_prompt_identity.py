# SPDX-License-Identifier: Apache-2.0
"""Phase 59.1: Outlet C off-path prompts stay byte-identical to the 609aa51 base.

Oracle: tests/fixtures/outlet_c_prompt_digests_pre_lens.json, written once on the
base by scripts/capture_outlet_c_digests.py --write. This module only reads it
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

BASE = "609aa5133076d77b4e17c77b2e82063418e8518e"
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
        "qodo": "b9475fffa9d167aa2863214eb2f21255d782b9b1aecadece8f00822d4e9f2493",
        "expert": "1776b05b1591a3fa45eee3fa7762d4bb99c8e60fff70fcccef3f4a89ea6ba999",
        "adversarial": "7a2b8ece4a31134e64546bf1758e591aae971ae912e6a7180ec9753d0cafe313",
    },
    "C_minimal": {
        "qodo": "fc8434ef780c4c81449e6b5378707864e730cc5cf95d17c37b0a89e807c301e5",
        "expert": "f779ba53c4248127e8da647fa0ff255c502da4e53ba5680b0348ab27e6d63000",
        "adversarial": "f4ed580192082f6998b0ad396dff7157d1dd55b4415ebe95dbaf7bba89318293",
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
