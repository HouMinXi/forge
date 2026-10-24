# SPDX-License-Identifier: Apache-2.0
"""Review lens block, its pass assignment and the tag helpers.

The lens text is a set of short, paraphrased review checks derived from
MIT-licensed skills; no upstream text is copied verbatim. Attribution and the
license texts are in THIRD_PARTY_NOTICES.md, which ships in the wheel.

The block is opt-in (gate.yaml `review_lenses: true`, separately trusted) and
is appended verbatim to the L1 review prompts. This module deliberately imports
nothing from the rest of code_forge so every outlet can share one source.
"""

from __future__ import annotations

import re

# One static block; the per-pass selection line names which lenses a pass applies.
LENS_BLOCK: str = (
    "\n"
    "## Review Lenses\n"
    "- Flag only defects that added or changed lines introduce or expose, not old issues in untouched code.\n"
    "- A sound change needs no findings; never pad or inflate severity. A serious defect matters even in a small diff.\n"
    '- Apply only the lenses on your last line; start such a description with its tag, e.g. "[lens:L2] ".\n'
    "L1 Hidden failure: a sentinel or flag value that now means two states; a fallback that hides an unrecoverable error; a guard or test stand-in unlike production; a multi-step update left half-applied on failure.\n"
    "L2 Simpler shape, P3 at most: name the exact branch, layer or wrapper to delete and show the replacement, not a preferred rewrite. Targets: one-off booleans, modes or ad-hoc conditionals in unrelated flows; thin or identity wrappers; feature logic in a shared module; a file pushed past 1000 lines.\n"
    "L3 Gate and env leaks: behavior reachable with its feature gate off; renamed or newly required env vars, secret sources, ports or setup scripts that break dev or deploy; not new alternatives or ordinary dependency adds.\n"
    "L4 Impact beyond the diff: report only a concrete break you can point to in this repo's code, not in how a library behaves, where grep misses: other-language readers of a stored or wire format, flag-gated paths, teardown or ordering timing, consumers 3 hops away.\n"
    "L5 Type and guard slop: casts, any, or ignore pragmas that dodge type checks; optional types or fallbacks that blur an invariant; defensive checks or try blocks for states the types or an earlier check on the same path already rule out.\n"
)

PASS_LENSES: dict[str, tuple[str, ...]] = {
    "qodo": ("L1",),
    "expert": ("L2", "L5"),
    "adversarial": ("L3", "L4"),
}

# (skill, repo, upstream path, source commit)
Source = tuple[str, str, str, str]

_CE_COMMIT = "dd1bf2b8d905fb9283ca16459bcc4ed172afa8fd"
_CURSOR_COMMIT = "9b93e81fb2921b9263d9463c15bafeabfe3903aa"
_CE_REPO = "EveryInc/compound-engineering-plugin"
_CURSOR_REPO = "cursor/plugins"

_CE_SKILL: Source = ("ce-code-review", _CE_REPO, "skills/ce-code-review/SKILL.md", _CE_COMMIT)
_CE_CORR: Source = (
    "ce-code-review",
    _CE_REPO,
    "skills/ce-code-review/references/personas/correctness-reviewer.md",
    _CE_COMMIT,
)
_TNQR: Source = (
    "thermo-nuclear-code-quality-review",
    _CURSOR_REPO,
    "cursor-team-kit/skills/thermo-nuclear-code-quality-review/SKILL.md",
    _CURSOR_COMMIT,
)
_TNR: Source = (
    "thermo-nuclear-review",
    _CURSOR_REPO,
    "thermos/skills/thermo-nuclear-review/SKILL.md",
    _CURSOR_COMMIT,
)
_BR: Source = ("blast-radius", _CURSOR_REPO, "pstack/skills/blast-radius/SKILL.md", _CURSOR_COMMIT)
_DS: Source = ("deslop", _CURSOR_REPO, "cursor-team-kit/skills/deslop/SKILL.md", _CURSOR_COMMIT)

# Gap-table rows behind each key: rules = TR-1, TR-7, CQ-11, CE-11, CE-17;
# L1 = CE-5/6/7/9, CQ-8; L2 = CQ-1/2/3/4/7, CE-17; L3 = TR-4/5; L4 = BR-3;
# L5 = DS-2/3, CQ-5, CE-18.
LENS_SOURCES: dict[str, tuple[Source, ...]] = {
    "rules": (_TNR, _TNQR, _CE_SKILL),
    "L1": (_CE_CORR, _TNQR),
    "L2": (_TNQR, _CE_SKILL),
    "L3": (_TNR,),
    "L4": (_BR,),
    "L5": (_DS, _TNQR, _CE_CORR),
}


def selection_line(pass_name: str) -> str:
    """Last line of a pass prompt: which lenses this pass applies."""
    return "\nLenses: " + ", ".join(PASS_LENSES[pass_name]) + ".\n"


LENS_RE = re.compile(r"^\[(qodo|expert|adversarial)\] \[lens:(L[1-5])\] ")


def lens_of(description: str) -> tuple[str, str] | None:
    """Parse a stored description; pure syntax, no pass/lens policy."""
    m = LENS_RE.match(description)
    return (m.group(1), m.group(2)) if m else None


def lens_mismatch(description: str) -> bool:
    """True when the tag names a lens not assigned to the tagged pass."""
    parsed = lens_of(description)
    return parsed is not None and parsed[1] not in PASS_LENSES[parsed[0]]


_LENS_TAG_RE = re.compile(r"^(\[[a-z]+\] )(?:\[lens:L[1-5]\] )+")


def strip_lens_tag(description: str) -> str:
    """Drop all leading lens tags but keep the pass prefix.

    Used by machine._rule_type and the legacy rule hint so a tag is attribution
    only and never changes a rule type or an id.
    """
    return _LENS_TAG_RE.sub(r"\1", description, count=1)
