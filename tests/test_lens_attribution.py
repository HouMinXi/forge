# SPDX-License-Identifier: Apache-2.0
"""Phase 59.1 Task 6: a lens tag is attribution only.

It must not change a P3 rule type, the density decisions built from rule
types, a legacy rule hint, a fingerprint or a finding id.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from code_forge.disposition import Disposition
from code_forge.legacy import LegacyRunner
from code_forge.machine import _FixpointResult
from code_forge.reviewer_json import _json_to_state_findings
from code_forge.state import StateFinding

from .test_legacy import DIFF_FOO_LINE5, SHA_40, _fake_runner, _make_finding
from .test_machine_local import _make_sm_fp, _prime

# 35 identical characters then a short distinguishing suffix. Untagged, the
# suffix sits inside the 50-character rule-type window; behind a 10-character
# lens tag it falls outside it, so a tag that leaks into the rule type would
# collapse six distinct rules into one.
STEM = "y" * 35
FORMS = {
    "plain": "",
    "single": "[lens:L2] ",
    "double": "[lens:L2] [lens:L5] ",
}
BIG_DIFF = (
    "diff --git a/t.py b/t.py\n--- a/t.py\n+++ b/t.py\n@@ -0,0 +1,1000 @@\n"
    + "\n".join("+line %d" % i for i in range(1000))
)


def _p3(index: int, file: str, rule: str, tag: str) -> StateFinding:
    return StateFinding(
        id="fp-%d" % index,
        fingerprint="fp-%d" % index,
        source="L1",
        disposition=Disposition.CONFIRMED,
        file=file,
        line_range=[1, 1],
        description="[expert] %s%s%s" % (tag, STEM, rule),
        severity="P3",
    )


def _result(tmp_path, findings) -> _FixpointResult:
    sm = _make_sm_fp(tmp_path, git_diff=BIG_DIFF)
    sm._state.findings.extend(findings)
    _prime(sm, *[f.fingerprint for f in findings])
    return sm._fixpoint_reached()


@pytest.mark.parametrize("form", list(FORMS))
def test_distinct_per_file_threshold_ignores_the_tag(tmp_path, form):
    # 6 distinct rules in one file -> distinct_per_file 6 > 5
    findings = [_p3(i, "t.py", "R%d" % i, FORMS[form]) for i in range(6)]
    assert _result(tmp_path, findings) == _FixpointResult.CYCLE_RESTART


@pytest.mark.parametrize("form", list(FORMS))
def test_distinct_per_diff_threshold_ignores_the_tag(tmp_path, form):
    # 12 distinct rules over 3 files (4 each) -> only distinct_per_diff 12 > 10 fires
    files = ["a.py", "b.py", "c.py"]
    findings = [_p3(i, files[i % 3], "R%02d" % i, FORMS[form]) for i in range(12)]
    assert _result(tmp_path, findings) == _FixpointResult.CYCLE_RESTART


@pytest.mark.parametrize("form", list(FORMS))
def test_below_threshold_stays_clean_for_every_form(tmp_path, form):
    findings = [_p3(i, "t.py", "R%d" % i, FORMS[form]) for i in range(5)]
    assert _result(tmp_path, findings) == _FixpointResult.CLEAN


def test_tagged_and_untagged_copies_of_one_rule_are_one_rule_type(tmp_path):
    # Same rule restated with and without a tag must not count as two rules.
    findings = [_p3(i, "t.py", "R0", FORMS["plain" if i % 2 else "single"]) for i in range(8)]
    assert _result(tmp_path, findings) == _FixpointResult.CLEAN


def test_fingerprint_and_id_do_not_depend_on_the_tag():
    def convert(description: str) -> StateFinding:
        data = {"findings": [{"file": "a.py", "line": 7, "severity": "P3", "description": description}]}
        return _json_to_state_findings(data, "expert")[0]

    plain, tagged = convert("Unused value"), convert("[lens:L2] Unused value")
    assert plain.fingerprint == tagged.fingerprint
    assert plain.id == tagged.id
    assert tagged.description == "[expert] [lens:L2] Unused value"


def _legacy_id(description: str, tmp_path: Path) -> str:
    with patch("code_forge.legacy.git_blame") as blame:
        blame.return_value = {20: {"sha": SHA_40, "author": "A", "subject": "init"}}
        finding = _make_finding(file="foo.py", line=20, description=description)
        runner = LegacyRunner(l0_runner=_fake_runner([finding]))
        runner.source_files = [Path("foo.py")]
        runner.registry = {"tools": []}
        (tmp_path / "foo.py").write_text("\n".join("line %d" % i for i in range(1, 25)) + "\n")
        (out,) = runner.run(DIFF_FOO_LINE5, tmp_path)
    return out.id


@pytest.mark.parametrize("form", ["single", "double"])
def test_legacy_rule_hint_ignores_the_tag(tmp_path, form):
    body = "Unused local variable in the parser"
    plain = _legacy_id("[expert] " + body, tmp_path)
    tagged = _legacy_id("[expert] %s%s" % (FORMS[form], body), tmp_path)
    assert tagged == plain


def test_legacy_untagged_ids_match_the_pre_lens_rule_hint(tmp_path):
    description = "[expert] Unused local variable in the parser"
    expected_hint = description[:16].replace(" ", "_")
    assert _legacy_id(description, tmp_path).endswith(":" + expected_hint)
    bare = "some issue found here"
    assert _legacy_id(bare, tmp_path).endswith(":" + bare[:16].replace(" ", "_"))
