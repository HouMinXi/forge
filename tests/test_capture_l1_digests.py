# SPDX-License-Identifier: Apache-2.0
"""Phase 59.1 Task 4: capture_l1_digests.py merges, checks, and never overwrites."""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REAL = ROOT / "tests" / "fixtures" / "l1_prompt_digests_pre_p59.json"


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "capture_l1_digests_under_test", ROOT / "scripts" / "capture_l1_digests.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def script():
    return _load_script()


def _strip_lens(tmp: Path) -> dict:
    """A pre-lens copy of the real fixture: drop *_lenses cases and the newest revision."""
    data = json.loads(REAL.read_text())
    off = {k: v for k, v in data["cases"].items() if not k.endswith("_lenses")}
    rev = data["revision"]
    if "previous" in rev and "added" in rev:
        rev = rev["previous"]
    out = {**data, "cases": off, "revision": rev}
    tmp.write_text(json.dumps(out, indent=2) + "\n")
    return out


def test_check_passes_on_the_real_fixture(script, capsys):
    assert script.main(["--fixture", str(REAL)]) == 0


def test_check_never_writes(script, tmp_path):
    fx = tmp_path / "fx.json"
    shutil.copy(REAL, fx)
    before = fx.read_bytes()
    assert script.main(["--fixture", str(fx)]) == 0
    assert fx.read_bytes() == before


def test_moved_off_digest_exits_1_and_leaves_the_file(script, tmp_path, capsys):
    fx = tmp_path / "fx.json"
    data = json.loads(REAL.read_text())
    data["cases"]["A_two_rows"]["qodo"] = "0" * 64
    fx.write_text(json.dumps(data, indent=2) + "\n")
    before = fx.read_bytes()
    assert script.main(["--fixture", str(fx)]) == 1
    assert "off-path digests moved; write a revision by hand" in capsys.readouterr().err
    assert script.main(["--fixture", str(fx), "--lenses", "--reason", "x"]) == 1
    assert fx.read_bytes() == before


def test_lenses_appends_only_lens_cases_and_wraps_the_old_revision(script, tmp_path):
    fx = tmp_path / "fx.json"
    old = _strip_lens(fx)
    assert script.main(["--fixture", str(fx), "--lenses", "--reason", "add lens cases"]) == 0
    new = json.loads(fx.read_text())
    assert new["source_tree"] == old["source_tree"]
    for k, v in old["cases"].items():
        assert new["cases"][k] == v
    added = set(new["cases"]) - set(old["cases"])
    assert added == {k + "_lenses" for k in old["cases"]}
    assert new["revision"]["reason"] == "add lens cases"
    assert new["revision"]["added"] == sorted(added)
    assert new["revision"]["previous"] == old["revision"]


def test_lenses_is_refused_when_lens_cases_exist(script, tmp_path):
    fx = tmp_path / "fx.json"
    shutil.copy(REAL, fx)
    before = fx.read_bytes()
    assert script.main(["--fixture", str(fx), "--lenses", "--reason", "again"]) != 0
    assert fx.read_bytes() == before


def test_lenses_requires_a_reason(script, tmp_path):
    fx = tmp_path / "fx.json"
    _strip_lens(fx)
    with pytest.raises(SystemExit):
        script.main(["--fixture", str(fx), "--lenses"])


def _check_lens_case_failure(script, tmp_path, mutate):
    fx = tmp_path / "fx.json"
    data = json.loads(REAL.read_text())
    mutate(data["cases"])
    fx.write_text(json.dumps(data, indent=2) + "\n")
    return script.main(["--fixture", str(fx)])


def test_check_fails_on_a_corrupted_lens_digest(script, tmp_path):
    def mut(cases):
        cur = cases["A_two_rows_lenses"]["expert"]
        cases["A_two_rows_lenses"]["expert"] = ("0" if cur[0] != "0" else "1") + cur[1:]

    assert _check_lens_case_failure(script, tmp_path, mut) == 1


def test_check_fails_on_a_missing_lens_case(script, tmp_path):
    assert _check_lens_case_failure(script, tmp_path, lambda c: c.pop("B_empty_lenses")) == 1


def test_check_fails_on_an_extra_lens_case(script, tmp_path):
    def mut(cases):
        cases["Z_extra_lenses"] = copy.deepcopy(cases["A_two_rows_lenses"])

    assert _check_lens_case_failure(script, tmp_path, mut) == 1
