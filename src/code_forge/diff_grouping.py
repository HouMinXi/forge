# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi1990@gmail.com>
"""Split a diff into semantically coherent groups, deterministically.

Forge sends the entire diff into one prompt, three times. On a 14-file diff
that is 59,087 input tokens per pass and the backend truncates its response,
so a review round completes zero of three passes. This module cuts the diff
into groups small enough for each call to finish.

The grouping is deterministic: entity data comes from `sem diff` (already
wired up in graph_triage) and Python def-use edges come from `ast`. No LLM is
involved, so the same diff always yields the same groups.

Roles partition the diff; def-use edges do NOT group. An earlier iteration
grouped by edge connectivity and collapsed 9 of 14 files into one group,
because `cli -> machine -> rulepack -> state` is a genuine dependency chain.
Edges do two narrow jobs instead: attach each test to the subject it shares
symbols with, and report the contracts that end up split across groups.

Design record: .planning/charter_review_decomposition.md
"""

from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# sem's changeType vocabulary, read from the binary's serde variants rather
# than inferred from whichever branch was handy. A diff that only deletes
# emits "deleted"; an earlier draft looked for "removed" and would have scored
# every deletion as zero churn.
ADDED = "added"
DELETED = "deleted"
RENAMED = "renamed"
MOVED = "moved"
REORDERED = "reordered"
MODIFIED = "modified"
KNOWN_CHANGE_TYPES = frozenset(
    {ADDED, DELETED, RENAMED, MOVED, REORDERED, MODIFIED}
)

# Churn at or above this many entities makes a file the substance of the
# change rather than a registration point. Counted across every direction:
# a file that deletes twelve functions carries as much review risk as one
# that adds twelve.
_ENGINE_CHURN = 10
_INTEGRATION_CHURN = 2

# Assembled-prompt budget for the single-pass path. Over this, the review
# switches to grouped mode. See max_prompt_tokens_from_gate_config for the
# derivation.
_MAX_PROMPT_TOKENS = 32000


def _check_thresholds(engine_churn: int, integration_churn: int) -> None:
    for name, value, floor in (
        ("engine_churn", engine_churn, 1),
        ("integration_churn", integration_churn, 0),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("%s must be an int, got %r" % (name, value))
        if value < floor:
            raise ValueError("%s must be >= %d, got %d" % (name, floor, value))
    if integration_churn >= engine_churn:
        raise ValueError(
            "integration_churn (%d) must be below engine_churn (%d); "
            "overlapping ranges leave the boundary ambiguous"
            % (integration_churn, engine_churn)
        )

# Every reviewable group gets the full three passes. Measured across 30
# receipts in this repo: qodo found 12 distinct findings, expert 15,
# adversarial 15, union 40, and each pair overlapped by exactly 1. One pass
# recovers 30-37% of the union, so a discount here is a recall cut, not an
# economy.
#
# Only declarative content skips the LLM: config and docs are what the
# deterministic L0 checks already cover. Source in a language this module
# cannot parse still gets the full three passes -- no def-use edges is a
# limit on grouping precision, not a licence to skip review. A shell script
# that changes process lifecycle is logic-bearing by forge's own rules.
_PASSES_PER_GROUP = 3
_NO_LLM_ROLES = frozenset({"config", "docs"})

_PYTHON_SUFFIXES = (".py",)
_CONFIG_SUFFIXES = (".yaml", ".yml", ".json", ".toml", ".cfg", ".ini")
_DOC_SUFFIXES = (".md", ".rst", ".txt")


@dataclass(frozen=True)
class Group:
    """One review unit: the files, why they are together, and its budget."""

    name: str
    role: str
    members: list[str]
    passes: int


@dataclass
class GroupingResult:
    """Groups plus the contracts that splitting put out of sight."""

    groups: list[Group] = field(default_factory=list)
    roles: dict[str, tuple[str, str]] = field(default_factory=dict)
    cross_group_edges: list[dict] = field(default_factory=list)
    orphan_tests: list[str] = field(default_factory=list)


def churn_profile(entities: list[dict]) -> dict[str, int]:
    """Count one file's entity changes by direction.

    Raises on a changeType this module does not know, rather than scoring it
    as zero: a future sem release adding a variant must fail loudly instead of
    quietly making a changed file look untouched.
    """
    counts: dict[str, int] = defaultdict(int)
    unknown = set()
    for e in entities:
        ct = e.get("changeType", MODIFIED)
        if ct not in KNOWN_CHANGE_TYPES:
            unknown.add(ct)
        counts[ct] += 1
    if unknown:
        raise ValueError(
            "unknown sem changeType(s): %s" % sorted(unknown)
        )
    return {
        "total": sum(counts.values()),
        "added": counts[ADDED],
        "deleted": counts[DELETED],
        "renamed": counts[RENAMED],
        "moved": counts[MOVED],
        "reordered": counts[REORDERED],
        "modified": counts[MODIFIED],
    }


def thresholds_from_gate_config(config: dict) -> tuple[int, int]:
    """Map gate.yaml's optional `grouping:` section onto churn thresholds.

    Takes the already-parsed gate config; file loading lives with the caller.
    Absent section means defaults. Unknown keys are rejected outright -- a
    typo like `engin_churn` must fail loudly, not silently re-apply defaults.
    """
    section = config.get("grouping")
    if section is None:
        return _ENGINE_CHURN, _INTEGRATION_CHURN
    if not isinstance(section, dict):
        raise ValueError(
            "gate.yaml 'grouping' section must be a mapping, got %r" % (section,)
        )
    unknown = sorted(set(section) - {
        "engine_churn", "integration_churn", "max_prompt_tokens",
    })
    if unknown:
        raise ValueError(
            "gate.yaml 'grouping' has unknown keys: %s" % ", ".join(unknown)
        )
    engine = section.get("engine_churn", _ENGINE_CHURN)
    integration = section.get("integration_churn", _INTEGRATION_CHURN)
    try:
        _check_thresholds(engine, integration)
    except ValueError as e:
        raise ValueError("gate.yaml 'grouping': %s" % e) from e
    return engine, integration


def max_prompt_tokens_from_gate_config(config: dict) -> int:
    """The assembled-prompt size at which review switches to grouped mode.

    Default 32000 tokens: measured on the diff that motivated grouping, a
    21.5K call completed cleanly while a 59K call truncated, and 32K leaves
    the backend's 65536-token output budget roughly half free for thinking
    plus findings. Below the floor a config makes grouping trigger on
    diffs so small the split itself is the cost, so it is rejected.
    """
    section = config.get("grouping") or {}
    if not isinstance(section, dict):
        raise ValueError(
            "gate.yaml 'grouping' section must be a mapping, got %r" % (section,)
        )
    value = section.get("max_prompt_tokens", _MAX_PROMPT_TOKENS)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            "max_prompt_tokens must be an int, got %r" % (value,)
        )
    if value < 8192:
        raise ValueError(
            "max_prompt_tokens must be >= 8192, got %d" % value
        )
    return value


def classify_file(
    fpath: str,
    by_file: dict[str, list[dict]],
    engine_churn: int = _ENGINE_CHURN,
    integration_churn: int = _INTEGRATION_CHURN,
) -> tuple[str, str]:
    """Return (role, direction) for one changed file.

    Role drives grouping; direction is carried alongside for reporting. Role
    keys on TOTAL churn, which is what makes a deletion-heavy diff classify
    correctly -- keying on added entities alone graded the two files carrying
    a 260-line deletion as ordinary source.
    """
    if fpath.startswith("tests/") or "/tests/" in fpath:
        return "test", "n/a"
    if fpath.endswith(_CONFIG_SUFFIXES):
        return "config", "n/a"
    if fpath.endswith(_DOC_SUFFIXES):
        return "docs", "n/a"
    if not fpath.endswith(_PYTHON_SUFFIXES):
        return "other", "n/a"

    _check_thresholds(engine_churn, integration_churn)
    prof = churn_profile(by_file[fpath])
    if prof["deleted"] > prof["added"]:
        direction = "deletion"
    elif prof["renamed"] + prof["moved"] >= max(prof["added"], 1):
        direction = "rename"
    elif prof["added"] > prof["modified"]:
        direction = "addition"
    else:
        direction = "edit"

    if prof["total"] >= engine_churn:
        role = "engine"
    elif prof["total"] <= integration_churn:
        role = "integration"
    else:
        role = "source"
    return role, direction


def extract_names(path: Path) -> tuple[set[str], set[str]]:
    """Module-level definitions and load-context usages for one Python file.

    Scope is the whole point. Collecting every assignment via ast.walk made
    cli.py report 363 "definitions" where only 51 are importable, and counting
    every Attribute.attr as a usage turned `self._state` and `obj.data` into
    cross-file references. Restricting definitions to tree.body and usages to
    Name(ctx=Load) plus ImportFrom cut a 14-file diff from 72 edges to 19 with
    no name filtering at all.

    Returns empty sets for anything unreadable or not Python, which is how a
    shell or markdown diff degrades to role-only grouping.
    """
    if path.suffix != ".py":
        return set(), set()
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set(), set()
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set(), set()

    defined: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defined.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)

    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used.add(node.id)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                used.add(alias.name)
    return defined, used


def build_edges(
    by_file: dict[str, list[dict]], repo_root: Path,
) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]]]:
    """Direct def-use edges between changed files.

    An edge A -> B means A uses a name B defines. Direct only: no transitive
    closure, which is the operator that collapsed everything into one group.
    """
    file_defined: dict[str, set[str]] = {}
    file_used: dict[str, set[str]] = {}
    for fpath in by_file:
        # sem emits repository-relative paths, but Path("/abs") on the right
        # of a join silently discards repo_root and sends the read outside the
        # tree. Strip the leading separator so a malformed or hostile entry
        # can only ever resolve inside the repo.
        rel = fpath.lstrip("/\\")
        file_defined[fpath], file_used[fpath] = extract_names(repo_root / rel)

    edges: dict[str, set[str]] = defaultdict(set)
    for user_file, used_names in file_used.items():
        for def_file, def_names in file_defined.items():
            if user_file != def_file and used_names & def_names:
                edges[user_file].add(def_file)
    return dict(edges), file_defined, file_used


def _attach_tests(by_file, roles, file_defined, file_used):
    """Map each test to the subject it shares the most symbols with."""
    subjects = [
        f for f in by_file
        if roles[f][0] not in ("test", "config", "docs", "other")
    ]
    attached: dict[str, list[str]] = defaultdict(list)
    orphans: list[str] = []
    for f in sorted(by_file):
        if roles[f][0] != "test":
            continue
        best, best_n = None, 0
        for s in sorted(subjects):
            n = len(file_used.get(f, set()) & file_defined.get(s, set()))
            if n > best_n:
                best, best_n = s, n
        if best is None:
            orphans.append(f)
        else:
            attached[best].append(f)
    return attached, orphans


def build_groups(
    by_file: dict[str, list[dict]],
    file_defined: dict[str, set[str]],
    file_used: dict[str, set[str]],
    engine_churn: int = _ENGINE_CHURN,
    integration_churn: int = _INTEGRATION_CHURN,
) -> tuple[list[Group], list[str]]:
    """Partition the changed files into review groups.

    Returns (groups, orphan_tests). Def-use edges are deliberately absent from
    the signature: they do not partition anything, and taking them would imply
    otherwise. Sorted iteration throughout, so the output is byte-identical
    run to run.
    """
    roles = {
        f: classify_file(f, by_file, engine_churn, integration_churn)
        for f in by_file
    }
    attached, orphans = _attach_tests(by_file, roles, file_defined, file_used)

    groups: list[Group] = []
    placed: set[str] = set()

    def _emit(name, role, members):
        groups.append(Group(
            name=name, role=role, members=sorted(members),
            passes=0 if role in _NO_LLM_ROLES else _PASSES_PER_GROUP,
        ))
        placed.update(members)

    for f in sorted(by_file):
        if roles[f][0] == "engine":
            _emit("engine:%s" % Path(f).name, "engine",
                  [f] + attached.get(f, []))

    for f in sorted(by_file):
        if f in placed or roles[f][0] in ("test", "config", "docs", "other"):
            continue
        tests = [t for t in attached.get(f, []) if t not in placed]
        if tests:
            _emit("covered:%s" % Path(f).name, "covered", [f] + tests)

    rest = [
        f for f in sorted(by_file)
        if f not in placed and roles[f][0] in ("integration", "source")
    ]
    if rest:
        _emit("integration", "integration", rest)

    leftover_tests = [
        f for f in sorted(by_file)
        if f not in placed and roles[f][0] == "test"
    ]
    if leftover_tests:
        _emit("tests:unattached", "test", leftover_tests)

    for role in ("config", "docs", "other"):
        members = [
            f for f in sorted(by_file)
            if f not in placed and roles[f][0] == role
        ]
        if members:
            _emit(role, role, members)

    missing = set(by_file) - placed
    if missing:
        raise AssertionError("files left ungrouped: %s" % sorted(missing))
    return groups, orphans


def cross_group_edges(
    groups: list[Group],
    edges: dict[str, set[str]],
    file_defined: dict[str, set[str]],
    file_used: dict[str, set[str]],
) -> list[dict]:
    """Edges whose endpoints landed in different groups.

    Each is a contract no single review call sees both halves of -- the honest
    cost of splitting, and the input to a signature summary that costs a few
    hundred tokens instead of shipping a neighbouring group's full text.
    """
    owner = {m: g.name for g in groups for m in g.members}
    out = []
    for src in sorted(edges):
        for dst in sorted(edges[src]):
            if owner.get(src) == owner.get(dst):
                continue
            out.append({
                "from": src,
                "to": dst,
                "from_group": owner.get(src),
                "to_group": owner.get(dst),
                "symbols": sorted(
                    file_used.get(src, set()) & file_defined.get(dst, set())
                ),
            })
    return out


def group_diff(
    changes: list[dict],
    repo_root: Path,
    engine_churn: int = _ENGINE_CHURN,
    integration_churn: int = _INTEGRATION_CHURN,
) -> GroupingResult:
    """Group one diff's entity changes. The module's entry point.

    `changes` is what `sem diff --format json` puts under "changes", which
    graph_triage._run_sem already returns. Threshold overrides come from
    gate.yaml's optional `grouping:` section; absent that, the defaults
    calibrated on forge's own history apply.
    """
    _check_thresholds(engine_churn, integration_churn)
    by_file: dict[str, list[dict]] = defaultdict(list)
    for c in changes:
        by_file[c["filePath"]].append(c)
    if not by_file:
        return GroupingResult()

    edges, file_defined, file_used = build_edges(by_file, repo_root)
    groups, orphans = build_groups(
        by_file, file_defined, file_used, engine_churn, integration_churn
    )
    roles = {
        f: classify_file(f, by_file, engine_churn, integration_churn)
        for f in by_file
    }

    return GroupingResult(
        groups=groups,
        roles=roles,
        cross_group_edges=cross_group_edges(
            groups, edges, file_defined, file_used
        ),
        orphan_tests=orphans,
    )
