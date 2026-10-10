# SPDX-License-Identifier: Apache-2.0
"""Reconcile semantic groups with the verifier's mandatory diff obligations.

Planning is pure and atomic. It supplies original review inputs, never reviewer
excerpts or a claim that the resulting review has completed. Runtime admission
and genuine response/receipt verification remain separate gates.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .diff import get_changed_files, iter_diff_sections, parse_diff_hunks, split_diff_for_files
from .diff_grouping import Group, GroupingResult
from .reviewer_json import _requires_l1_excerpts


class GroupedCoverageError(ValueError):
    """The complete grouped plan cannot preserve the original obligations."""


@dataclass(frozen=True)
class PlannedReviewGroup:
    name: str
    role: str
    members: tuple[str, ...]
    passes: int
    diff_text: str
    provenance: str


def _obligations(diff_text: str) -> Counter:
    hunks, exempt = parse_diff_hunks(diff_text)
    if diff_text.strip() and not hunks and not exempt and _requires_l1_excerpts(diff_text):
        raise GroupedCoverageError("grouping: diff parse failed -- cannot reconcile mandatory hunks")
    # Match the verifier's parsed model, including its existing last-frame-wins
    # limitation. Exact original section bytes are checked separately below.
    return Counter(
        (path, hunk["start"], hunk["end"], tuple(hunk["added_lines"]))
        for path, entries in hunks.items()
        for hunk in entries
        if not hunk["is_deletion_only"]
    )


def validate_grouped_diff(diff_text: str) -> Counter:
    """Validate original input and return its existing mandatory obligations.

    This is the same pure parser check used during reconciliation, allowing
    callers to reject invalid input before semantic acquisition or fallback.
    It does not establish sliceability, semantic coverage, or provider fit.
    """
    if not isinstance(diff_text, str):
        raise GroupedCoverageError("grouping: diff must be text")
    return _obligations(diff_text)


def reconcile_grouped_coverage(
    diff_text: str, grouping: GroupingResult
) -> tuple[PlannedReviewGroup, ...]:
    """Return validated producing groups, without mutating the semantic result.

    Validate even nonproducing groups before promotion/fallback. File identity
    comes from the existing parser/slicer; no filesystem or basename matching
    may reassign an obligation. All errors precede provider construction.
    """
    original = validate_grouped_diff(diff_text)
    if not isinstance(grouping, GroupingResult) or not isinstance(grouping.groups, list):
        raise GroupedCoverageError("grouping: expected a GroupingResult with a group list")
    required = {key[0] for key in original}
    inventory = set(get_changed_files(diff_text))
    owners: set[str] = set()
    selected: list[tuple[str, str, tuple[str, ...], str]] = []
    for group in grouping.groups:
        if not isinstance(group, Group):
            raise GroupedCoverageError("grouping: invalid group type")
        if not all(isinstance(v, str) and v.strip() for v in (group.name, group.role)):
            raise GroupedCoverageError("grouping: group name and role must be nonempty strings")
        if type(group.passes) is not int or group.passes not in (0, 3):
            raise GroupedCoverageError("grouping: passes must be integer 0 or 3")
        if not isinstance(group.members, list) or not group.members:
            raise GroupedCoverageError("grouping: members must be a nonempty path list")
        for member in group.members:
            if not isinstance(member, str) or not member.strip():
                raise GroupedCoverageError("grouping: member must be a nonempty path string")
            if member not in inventory:
                raise GroupedCoverageError(f"grouping: unknown member path {member!r}")
            if member in owners:
                raise GroupedCoverageError(f"grouping: duplicate member ownership {member!r}")
            owners.add(member)
        members = tuple(group.members)
        if group.passes == 3 or required.intersection(members):
            selected.append(
                (group.name, group.role, members, "semantic" if group.passes else "promoted")
            )
    selected.extend(
        (f"non-semantic-fallback:{path}", "fallback", (path,), "non_semantic_fallback")
        for path in sorted(required - owners)
    )

    # Independently bind splitter output to raw original file frames. This is
    # not a second parser: iter_diff_sections supplies the existing identity.
    sections = tuple(iter_diff_sections(diff_text))
    plan: list[PlannedReviewGroup] = []
    dispatched: Counter = Counter()
    producing_owners: set[str] = set()
    for name, role, members, provenance in selected:
        member_set = set(members)
        sliced = split_diff_for_files(diff_text, list(members))
        expected = "".join(text for path, text in sections if path in member_set)
        if sliced != expected:
            raise GroupedCoverageError(f"grouping: altered diff slice for {name!r}")
        if not sliced and required.intersection(members):
            raise GroupedCoverageError(f"grouping: empty mandatory diff slice for {name!r}")
        actual = validate_grouped_diff(sliced)
        projected = Counter({key: count for key, count in original.items() if key[0] in member_set})
        if actual != projected:
            raise GroupedCoverageError(f"grouping: mandatory hunk mismatch for {name!r}")
        if producing_owners.intersection(members):
            raise GroupedCoverageError(f"grouping: duplicate producing ownership for {name!r}")
        producing_owners.update(members)
        dispatched.update(actual)
        plan.append(PlannedReviewGroup(name, role, members, 3, sliced, provenance))
    if required - producing_owners or dispatched != original:
        raise GroupedCoverageError("grouping: incomplete mandatory hunk coverage")
    return tuple(plan)
