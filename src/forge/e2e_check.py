# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""E2E coverage heuristic for forge (R3).

Layer 1 (heuristic, no config): diff touches >=2 source groups AND modifies
a function signature/return type -> non-blocking checklist finding.
Layer 2 (explicit, opt-in): implemented in plan 03-02; stub here.

No subprocess or git calls. diff_text is provided by caller via git module.
Uses unidiff directly (diff.py does not expose Hunk.section_header).
"""

from __future__ import annotations

import hashlib
import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Optional

import unidiff

from .diff import get_changed_files
from .disposition import Disposition
from .state import StateFinding

# ---------------------------------------------------------------------------
# Signature-detection patterns (Python + shell; C deferred per D-02a).
# Compiled once at module level to avoid per-call overhead.
# ---------------------------------------------------------------------------

# Python: matches "def foo(" or "async def foo(" lines (added lines).
_PY_DEF_RE = re.compile(
    r"^\s*(async\s+)?def\s+[A-Za-z_]\w*\s*\("
)

# Python: matches a return-type annotation "-> <type> :" at end of line.
_PY_RETURN_RE = re.compile(
    r"->\s*\S+.*:\s*$"
)

# Shell: matches a function definition line.
_SH_FUNC_RE = re.compile(
    r"^\s*(function\s+)?[A-Za-z_]\w*\s*\(\s*\)\s*\{?\s*$"
)

# Arm 2: matches a def/function pattern inside a section_header string.
# git emits section_header such as "def parse(self, ..." or "foo() {".
SECTION_HEADER_DEF_RE = re.compile(
    r"(?:(?:async\s+)?def\s+[A-Za-z_]\w*\s*\(|"
    r"(?:function\s+)?[A-Za-z_]\w*\s*\(\s*\)\s*\{?)"
)

# All added-line signature patterns as a flat list.
_SIG_PATTERNS = [_PY_DEF_RE, _PY_RETURN_RE, _SH_FUNC_RE]

# Test directory first-segment names excluded from source grouping (D-02b).
_TEST_DIRS: frozenset[str] = frozenset({"tests", "test", "spec"})


def detect_signature_changes(diff_text: str) -> set[str]:
    """Return set of file paths whose diff adds or modifies a function signature.

    Two arms (UNION per D-02a):
    - Arm 1 (added-lines regex): added line value matches any signature pattern.
    - Arm 2 (section_header): hunk.section_header matches SECTION_HEADER_DEF_RE.

    When section_header is empty (flat shell without a function wrapper), only
    Arm 1 contributes. That is the documented fallback, not an error.

    Returns empty set for empty diff, unparseable diff, or no signature found.
    """
    if not diff_text or not diff_text.strip():
        return set()

    try:
        patchset = unidiff.PatchSet(diff_text)
    except unidiff.errors.UnidiffParseError:
        return set()

    sig_files: set[str] = set()
    for patched_file in patchset:
        if patched_file.is_removed_file:
            continue
        filepath = patched_file.path
        for hunk in patched_file:
            # Arm 1: scan added lines for signature patterns.
            for line in hunk:
                if line.is_added:
                    val = line.value if hasattr(line, "value") else ""
                    for pat in _SIG_PATTERNS:
                        if pat.search(val):
                            sig_files.add(filepath)
                            break
            # Arm 2: check section_header for def-pattern.
            section_hdr = getattr(hunk, "section_header", "") or ""
            if section_hdr and SECTION_HEADER_DEF_RE.search(section_hdr):
                sig_files.add(filepath)

    return sig_files


def group_source_files(
    files: list[str],
    components: Optional[dict] = None,
    exclude_test_dirs: bool = True,
) -> dict[str, list[str]]:
    """Group file paths by source component.

    Args:
        files: list of file paths from the diff.
        components: optional dict of {component_name: [glob_patterns]}.
            When provided, files are assigned to the first matching component.
            Files matching no component fall back to first-segment grouping.
            (This is the shape plan 03-02 will load from components.yaml.)
        exclude_test_dirs: when True, drop files whose first path segment is
            in {"tests", "test", "spec"} (fixed builtin, not configurable per
            D-02b). Default True.

    Returns:
        {group_name: sorted(list_of_files)} with empty groups omitted.

    Top-level files (no "/" in path) group under their OWN filename -- NOT
    under "" -- to avoid collapsing all top-level files into a single
    pseudo-group that falsely triggers Layer 1. (LOCKED rule, plan 03-01.)
    """
    groups: dict[str, list[str]] = {}

    for fpath in files:
        # Determine first path segment for exclusion + default grouping.
        parts = fpath.split("/")
        first_seg = parts[0]

        if exclude_test_dirs and first_seg in _TEST_DIRS:
            continue

        if components is not None:
            # Assign to first matching component.
            assigned = None
            for comp_name, patterns in components.items():
                for pat in patterns:
                    if fnmatch(fpath, pat):
                        assigned = comp_name
                        break
                if assigned is not None:
                    break
            group_key = assigned if assigned is not None else first_seg
        else:
            # Default: first path segment, or own filename for top-level.
            group_key = first_seg if len(parts) > 1 else fpath

        groups.setdefault(group_key, []).append(fpath)

    # Sort file lists for deterministic output.
    return {k: sorted(v) for k, v in groups.items()}


def check_layer_2(
    diff_text: str,
    repo_root: Path,
    components: Optional[dict] = None,
) -> list[StateFinding]:
    """Layer 2 co-occurrence trigger (stub; implemented in plan 03-02).

    Returns [] until components.yaml loader is wired in plan 03-02.
    Signature is stable so plan 03-02 fills the body without touching callers.
    """
    return []


def check_layer_1(
    diff_text: str,
    components: Optional[dict] = None,
) -> list[StateFinding]:
    """Layer 1 heuristic: cross-component change with a signature modification.

    Fires only when:
    - detect_signature_changes finds at least one changed signature, AND
    - group_source_files yields >=2 distinct source groups.

    Returns at most ONE finding, disposition=DISMISSED (advisory, never blocks).
    Fingerprint is deterministic: sha256 of canonical groups+sig_files string,
    truncated to 16 hex chars, prefixed "e2e-l1:".
    """
    sig_files = detect_signature_changes(diff_text)
    if not sig_files:
        return []

    changed = get_changed_files(diff_text)
    groups = group_source_files(changed, components)

    if len(groups) < 2:
        return []

    # Defensive: sig_files should be a subset of changed; if somehow disjoint,
    # do not emit (would be a spurious finding with no anchor in the diff).
    if sig_files.isdisjoint(set(changed)):
        return []

    group_keys_str = "|".join(sorted(groups.keys()))
    sig_files_str = "|".join(sorted(sig_files))
    fp_input = (group_keys_str + "::" + sig_files_str).encode("utf-8")
    fingerprint = "e2e-l1:" + hashlib.sha256(fp_input).hexdigest()[:16]

    group_names = sorted(groups.keys())
    sig_names = sorted(sig_files)
    description = (
        "cross-component change spans groups {%s}; "
        "signature changed in {%s}; "
        "is there an e2e test for the joined path?"
        % (", ".join(group_names), ", ".join(sig_names))
    )

    finding = StateFinding(
        id="e2e-layer1",
        fingerprint=fingerprint,
        source="E2E_CHECK",
        disposition=Disposition.DISMISSED,
        file="",
        line_range=[],
        description=description,
    )
    return [finding]


def run_e2e_check(
    diff_text: str,
    repo_root: Path,
) -> tuple[list[StateFinding], list[str]]:
    """Orchestrate Layer 1 + Layer 2 e2e coverage checks.

    Args:
        diff_text: unified diff text (from caller via git module).
        repo_root: repository root path (used by Layer 2 for path resolution).

    Returns:
        (findings, infra_errors) where findings is a list of StateFinding
        with source="E2E_CHECK" and infra_errors is a list of error strings.
        On unexpected exception, returns ([], [str(e)]) so a malformed diff
        never crashes the review pipeline.

    Plan 03-02 will load components.yaml inside this function and pass the
    parsed dict to both layers, then add Layer 1 / Layer 2 dedup.
    """
    infra_errors: list[str] = []
    try:
        components = None  # plan 03-02 loads components.yaml here
        l1 = check_layer_1(diff_text, components=components)
        l2 = check_layer_2(diff_text, repo_root, components=components)
        return (l1 + l2, infra_errors)
    except Exception as exc:  # noqa: BLE001
        infra_errors.append(str(exc))
        return ([], infra_errors)
