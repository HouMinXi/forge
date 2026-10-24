# SPDX-License-Identifier: Apache-2.0
"""Phase 59.1 Task 2: the lens text, its pass assignment and the tag helpers."""

from __future__ import annotations

import hashlib
import re

import pytest

from code_forge import lenses
from code_forge.lenses import (
    LENS_BLOCK,
    LENS_SOURCES,
    PASS_LENSES,
    lens_mismatch,
    lens_of,
    selection_line,
    strip_lens_tag,
)

ROLE_STRINGS = (
    "structural code reviewer: correctness and logic errors",
    "senior engineer: SOLID, architecture, security",
    "adversarial QE: assume bugs exist",
)


def test_block_is_pinned_by_sha256_and_size():
    raw = LENS_BLOCK.encode()
    assert (
        hashlib.sha256(raw).hexdigest()
        == "bef4b26e48d60ed38afc0beda7b1429b9aa333deea06a0d9335c6fcf1b3fffa1"
    )
    assert len(raw) == 1567
    assert len(raw) <= 1600


def test_block_shape():
    assert LENS_BLOCK.isascii()
    assert LENS_BLOCK.startswith("\n## Review Lenses\n")
    assert LENS_BLOCK.endswith("\n")
    lens_lines = [ln for ln in LENS_BLOCK.splitlines() if re.match(r"^L\d ", ln)]
    assert [ln[:2] for ln in lens_lines] == ["L1", "L2", "L3", "L4", "L5"]


def test_block_does_not_repeat_pass_roles_or_other_sections():
    for role in ROLE_STRINGS:
        assert role not in LENS_BLOCK
    for banned in ("unproven", "name the one fact", "Design Intent", "intent"):
        assert banned not in LENS_BLOCK, banned


def test_block_keeps_the_two_bounding_phrases():
    assert "in this repo's code, not in how a library behaves" in LENS_BLOCK
    assert "an earlier check on the same path" in LENS_BLOCK


def test_pass_assignment_is_literal():
    assert PASS_LENSES == {
        "qodo": ("L1",),
        "expert": ("L2", "L5"),
        "adversarial": ("L3", "L4"),
    }


def test_every_lens_is_assigned_to_exactly_one_pass():
    assigned = [x for lens in PASS_LENSES.values() for x in lens]
    assert sorted(assigned) == ["L1", "L2", "L3", "L4", "L5"]


def test_selection_line_texts_and_length():
    assert selection_line("qodo") == "\nLenses: L1.\n"
    assert selection_line("expert") == "\nLenses: L2, L5.\n"
    assert selection_line("adversarial") == "\nLenses: L3, L4.\n"
    assert max(len(selection_line(p)) for p in PASS_LENSES) <= 32


def test_selection_line_rejects_unknown_pass():
    with pytest.raises(KeyError):
        selection_line("nobody")


@pytest.mark.parametrize(
    "desc, expected",
    [
        ("[expert] [lens:L5] x", ("expert", "L5")),
        ("[qodo] [lens:L1] hidden failure", ("qodo", "L1")),
        ("[adversarial] [lens:L4] y", ("adversarial", "L4")),
    ],
)
def test_lens_of_parses_well_formed_tags(desc, expected):
    assert lens_of(desc) == expected


@pytest.mark.parametrize(
    "desc",
    [
        "x",
        "",
        "[expert] [lens:L9] x",
        "[expert] [lens:L0] x",
        "[expert]  [lens:L5] x",
        "[expert][lens:L5] x",
        "[expert] [Lens:L5] x",
        "[other] [lens:L5] x",
        "[lens:L5] x",
        " [expert] [lens:L5] x",
    ],
)
def test_lens_of_rejects_everything_else(desc):
    assert lens_of(desc) is None


def test_lens_mismatch_flags_only_a_wrong_pass_lens_pair():
    assert lens_mismatch("[qodo] [lens:L2] x") is True
    assert lens_mismatch("[expert] [lens:L1] x") is True
    assert lens_mismatch("[adversarial] [lens:L5] x") is True
    assert lens_mismatch("[expert] [lens:L5] x") is False
    assert lens_mismatch("[expert] [lens:L2] x") is False
    assert lens_mismatch("[qodo] [lens:L1] x") is False
    assert lens_mismatch("[qodo] untagged") is False
    assert lens_mismatch("no prefix at all") is False


def test_strip_removes_one_or_many_leading_tags_and_keeps_the_pass_prefix():
    assert strip_lens_tag("[expert] [lens:L2] X") == "[expert] X"
    assert strip_lens_tag("[expert] [lens:L2] [lens:L5] X") == "[expert] X"
    assert strip_lens_tag("[expert] [lens:L2] [lens:L5] [lens:L3] X") == "[expert] X"


def test_strip_is_idempotent():
    once = strip_lens_tag("[expert] [lens:L2] [lens:L5] X")
    assert strip_lens_tag(once) == once


@pytest.mark.parametrize(
    "desc",
    ["[expert] X", "P3: x (y)", "[lens:L2] X", "[expert] [lens:L9] X", "", "[expert] X [lens:L2] tail"],
)
def test_strip_leaves_everything_else_untouched(desc):
    assert strip_lens_tag(desc) == desc


def test_lens_sources_key_set():
    assert set(LENS_SOURCES) == {"rules", "L1", "L2", "L3", "L4", "L5"}


def test_lens_sources_per_key_skill_and_path_sets():
    def pairs(key):
        return {(s[0], s[2]) for s in LENS_SOURCES[key]}

    ce = ("ce-code-review", "skills/ce-code-review/SKILL.md")
    corr = ("ce-code-review", "skills/ce-code-review/references/personas/correctness-reviewer.md")
    tnqr = (
        "thermo-nuclear-code-quality-review",
        "cursor-team-kit/skills/thermo-nuclear-code-quality-review/SKILL.md",
    )
    tnr = ("thermo-nuclear-review", "thermos/skills/thermo-nuclear-review/SKILL.md")
    br = ("blast-radius", "pstack/skills/blast-radius/SKILL.md")
    ds = ("deslop", "cursor-team-kit/skills/deslop/SKILL.md")
    assert pairs("rules") == {tnr, tnqr, ce}
    assert pairs("L1") == {corr, tnqr}
    assert pairs("L2") == {tnqr, ce}
    assert pairs("L3") == {tnr}
    assert pairs("L4") == {br}
    assert pairs("L5") == {ds, tnqr, corr}


def test_lens_sources_are_pinned_to_full_commit_ids():
    for key, sources in LENS_SOURCES.items():
        for skill, repo, _path, commit in sources:
            assert re.fullmatch(r"[0-9a-f]{40}", commit), (key, skill)
            assert repo in {"EveryInc/compound-engineering-plugin", "cursor/plugins"}


def test_module_has_no_dependency_on_the_rest_of_code_forge():
    src = open(lenses.__file__, encoding="utf-8").read()
    assert not re.search(r"^\s*(from|import)\s+(\.|code_forge)", src, re.M)
