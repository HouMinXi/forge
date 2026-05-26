# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""E2E coverage heuristic for forge (R3).

Layer 1 (heuristic, no config): diff touches >=2 source groups AND modifies
a function signature/return type -> non-blocking checklist finding.
Layer 2 (explicit, opt-in): .forge/components.yaml defines components, hubs,
data paths, and e2e artifact patterns. Co-occurrence trigger -> P2 finding.

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
import yaml

from .diff import get_changed_files
from .disposition import Disposition
from .errors import ComponentsConfigError
from .state import StateFinding

# ---------------------------------------------------------------------------
# Signature-detection patterns (Python + shell; C detection not implemented).
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

# Test directory first-segment names excluded from source grouping by default.
_TEST_DIRS: frozenset[str] = frozenset({"tests", "test", "spec"})

# Default e2e artifact patterns when e2e_patterns absent from components.yaml.
_DEFAULT_E2E_PATTERNS = ["tests/e2e/**", "test_*integration*"]


def detect_signature_changes(diff_text: str) -> set[str]:
    """Return set of file paths whose diff adds or modifies a function signature.

    Two detection arms combined with logical OR:
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
        exclude_test_dirs: when True, drop files whose first path segment is
            in {"tests", "test", "spec"}. Default True.

    Returns:
        {group_name: sorted(list_of_files)} with empty groups omitted.

    Top-level files (no "/" in path) group under their OWN filename -- NOT
    under "" -- to avoid collapsing all top-level files into a single
    pseudo-group that falsely triggers Layer 1.
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


# ---------------------------------------------------------------------------
# components.yaml loader and schema validation
# ---------------------------------------------------------------------------

def load_components_yaml(repo_root: Path) -> Optional[dict]:
    """Load and validate .forge/components.yaml.

    Args:
        repo_root: repository root path.

    Returns:
        Validated dict with e2e_patterns defaulted, or None when the file
        does not exist (Layer 2 is opt-in; absence is normal, not an error).

    Raises:
        ComponentsConfigError: when the file is present but fails schema
            validation. Every message starts "components.yaml: " and names
            the offending key.
    """
    config_path = repo_root / ".forge" / "components.yaml"
    if not config_path.exists():
        return None

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ComponentsConfigError(
            "components.yaml: YAML parse error: %s" % e
        ) from e

    if not isinstance(data, dict):
        raise ComponentsConfigError(
            "components.yaml: top-level value must be a mapping"
        )

    # (a) version check
    version = data.get("version")
    if version != 1:
        raise ComponentsConfigError(
            "components.yaml: version: expected 1, got %r" % version
        )

    # (b) components must be a dict; each value has a paths list.
    raw_components = data.get("components")
    if not isinstance(raw_components, dict):
        raise ComponentsConfigError(
            "components.yaml: 'components' must be a mapping"
        )
    for name, info in raw_components.items():
        if not isinstance(info, dict) or "paths" not in info:
            raise ComponentsConfigError(
                "components.yaml: component %r: missing 'paths' list" % name
            )
        if not isinstance(info["paths"], list):
            raise ComponentsConfigError(
                "components.yaml: component %r: 'paths' must be a list" % name
            )

    component_names = set(raw_components.keys())

    # (c) depends_on targets must exist; (d) no self-reference.
    for name, info in raw_components.items():
        for target in info.get("depends_on", []):
            if target == name:
                raise ComponentsConfigError(
                    "components.yaml: self-reference '%s' -> '%s'"
                    % (name, name)
                )
            if target not in component_names:
                raise ComponentsConfigError(
                    "components.yaml: depends_on '%s' (from '%s') is undefined"
                    % (target, name)
                )

    # (e) cycle detection via DFS.
    _detect_cycles(raw_components)

    # (f) e2e_absent_ok entries: each .component must exist.
    absent_ok_raw = data.get("e2e_absent_ok", [])
    if not isinstance(absent_ok_raw, list):
        raise ComponentsConfigError(
            "components.yaml: 'e2e_absent_ok' must be a list"
        )
    for entry in absent_ok_raw:
        if not isinstance(entry, dict):
            raise ComponentsConfigError(
                "components.yaml: each e2e_absent_ok entry must be a mapping"
            )
        comp = entry.get("component", "")
        if comp not in component_names:
            raise ComponentsConfigError(
                "components.yaml: e2e_absent_ok component %r is undefined"
                % comp
            )

    # (g) data_paths: each entry is a list of exactly 2 elements; each name
    #     must exist.
    data_paths_raw = data.get("data_paths", [])
    if not isinstance(data_paths_raw, list):
        raise ComponentsConfigError(
            "components.yaml: 'data_paths' must be a list"
        )
    for entry in data_paths_raw:
        if not isinstance(entry, list) or len(entry) != 2:
            raise ComponentsConfigError(
                "components.yaml: data_paths entry %r must be length 2, got %d"
                % (entry, len(entry) if isinstance(entry, list) else -1)
            )
        for comp in entry:
            if comp not in component_names:
                raise ComponentsConfigError(
                    "components.yaml: data_paths component %r is undefined"
                    % comp
                )

    # (h) default e2e_patterns when absent.
    if "e2e_patterns" not in data or not data["e2e_patterns"]:
        data["e2e_patterns"] = list(_DEFAULT_E2E_PATTERNS)

    return data


def _detect_cycles(raw_components: dict) -> None:
    """Raise ComponentsConfigError if depends_on forms a cycle.

    Uses DFS with three-color marking (white/gray/black).
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {name: WHITE for name in raw_components}
    path: list[str] = []

    def dfs(node: str) -> None:
        color[node] = GRAY
        path.append(node)
        for neighbor in raw_components[node].get("depends_on", []):
            if neighbor not in color:
                # Undefined references are caught by the caller before this
                # function runs; hitting this branch indicates a call-order bug.
                raise AssertionError(
                    "depends_on target %r not in component set; "
                    "validate before calling _detect_cycles" % neighbor
                )
            if color[neighbor] == GRAY:
                # cycle: reconstruct the cycle segment from path
                cycle_start = path.index(neighbor)
                cycle_nodes = path[cycle_start:] + [neighbor]
                raise ComponentsConfigError(
                    "components.yaml: cycle detected: %s"
                    % " -> ".join(cycle_nodes)
                )
            if color[neighbor] == WHITE:
                dfs(neighbor)
        path.pop()
        color[node] = BLACK

    for node in list(raw_components.keys()):
        if color[node] == WHITE:
            dfs(node)


# ---------------------------------------------------------------------------
# Layer 2 co-occurrence detection and e2e artifact matching
# ---------------------------------------------------------------------------

def sorted_pair_hash(a: str, b: str) -> str:
    """Commutative 16-char sha256 hash of a pair of component names.

    Uses the same scheme as the Layer 1 fingerprint so both layers produce
    comparable identifiers; must stay in sync if the scheme changes.
    """
    names = sorted([a, b])
    return hashlib.sha256("|".join(names).encode("utf-8")).hexdigest()[:16]


def find_e2e_artifacts(repo_root: Path, patterns: list[str]) -> set[str]:
    """Return repo-relative POSIX paths matching any e2e pattern.

    Uses pathlib.glob (not fnmatch) because patterns may contain **
    (recursive glob). Each path is converted via Path.relative_to(repo_root)
    .as_posix() before insertion -- never mix Path and str in the returned set.

    Args:
        repo_root: repository root path.
        patterns:  list of glob patterns (may include **).

    Returns:
        set[str] of repo-relative forward-slash paths.
    """
    artifacts: set[str] = set()
    for pattern in patterns:
        try:
            for p in repo_root.glob(pattern):
                if p.is_file():
                    artifacts.add(p.relative_to(repo_root).as_posix())
        except (OSError, ValueError):
            # glob errors (bad pattern, permission) are non-fatal; skip.
            pass
    return artifacts


def _artifact_satisfies_pair(
    artifacts: set[str],
    component_paths: list[str],
) -> bool:
    """Return True iff at least one artifact lies within the component's paths.

    Uses fnmatch for component path globs; pathlib.glob is not needed here
    because component paths do not require recursive ** expansion.

    Args:
        artifacts:       set[str] of repo-relative POSIX artifact paths.
        component_paths: list of glob patterns from the component's 'paths'.

    Returns:
        True on first match found; False if no artifact matches any pattern.
    """
    for artifact in artifacts:
        for pattern in component_paths:
            if fnmatch(artifact, pattern):
                return True
    return False


def check_layer_2(
    diff_text: str,
    repo_root: Path,
    components: Optional[dict] = None,
) -> list[StateFinding]:
    """Layer 2 co-occurrence trigger.

    Args:
        diff_text:  unified diff text.
        repo_root:  repository root path (for glob-based artifact search).
        components: validated dict from load_components_yaml, or None.
                    When None, returns [] (Layer 2 is opt-in).

    Returns:
        list[StateFinding] with source="E2E_CHECK", disposition=UNCERTAIN,
        id="e2e-layer2", file="", line_range=[], fingerprint "e2e-l2:<hash>".
    """
    if components is None:
        return []

    changed = get_changed_files(diff_text)

    # Extract name->paths mapping before passing to group_source_files.
    # The full YAML dict has structural keys ("version", "data_paths",
    # "e2e_patterns") that group_source_files would silently iterate over.
    component_paths_map = {
        name: info["paths"]
        for name, info in components["components"].items()
    }

    # Touched components: keys from group_source_files that are real component
    # names. Filter out first-segment fallback groups that are not components.
    groups = group_source_files(changed, component_paths_map)
    touched_components: set[str] = {
        key for key in groups if key in component_paths_map
    }

    artifacts = find_e2e_artifacts(repo_root, components["e2e_patterns"])
    absent_ok: set[str] = {
        entry["component"]
        for entry in components.get("e2e_absent_ok", [])
    }

    # Compute hub set by reverse-scanning depends_on. A component is a hub
    # when other components list it in their depends_on.
    hubs: set[str] = set()
    for name, info in components["components"].items():
        for target in info.get("depends_on", []):
            hubs.add(target)

    findings: list[StateFinding] = []
    seen_fingerprints: set[str] = set()

    def _emit_p2(a: str, b: str, description: str) -> None:
        """Emit a P2 finding for the (a, b) pair if not already emitted."""
        fp = "e2e-l2:" + sorted_pair_hash(a, b)
        if fp in seen_fingerprints:
            return
        seen_fingerprints.add(fp)
        findings.append(
            StateFinding(
                id="e2e-layer2",
                fingerprint=fp,
                source="E2E_CHECK",
                disposition=Disposition.UNCERTAIN,
                file="",
                line_range=[],
                description=description,
            )
        )

    # HUB+DEPENDENT arm (one-level only; co-occurrence, not blast-radius).
    for h_name, h_info in components["components"].items():
        if h_name not in hubs:
            continue
        if h_name not in touched_components:
            # Hub not touched in this diff; skip (co-occurrence requires H).
            continue
        # Enumerate dependents (those that list H in their depends_on).
        for d_name, d_info in components["components"].items():
            if h_name not in d_info.get("depends_on", []):
                continue
            if d_name not in touched_components:
                # Dependent not touched -> no co-occurrence; Layer 1 handles
                # hub-only changes.
                continue
            # Escape hatch: e2e_absent_ok suppresses P2s for either endpoint.
            if d_name in absent_ok or h_name in absent_ok:
                continue
            # PER-PAIR: artifact must be within the dependent's paths.
            satisfied = _artifact_satisfies_pair(
                artifacts,
                components["components"][d_name]["paths"],
            )
            if satisfied:
                continue
            desc = (
                "cross-component change: hub '%s' + dependent '%s' both "
                "touched; no e2e artifact under '%s' paths matches e2e_patterns"
                % (h_name, d_name, d_name)
            )
            _emit_p2(h_name, d_name, desc)

    # PEER data_path arm (symmetric: both endpoints must be touched).
    for pair in components.get("data_paths", []):
        a, b = pair[0], pair[1]
        if a not in touched_components or b not in touched_components:
            continue
        # Escape hatch: e2e_absent_ok suppresses P2s for either endpoint.
        if a in absent_ok or b in absent_ok:
            continue
        # EITHER endpoint's component paths satisfies the pair.
        satisfied = _artifact_satisfies_pair(
            artifacts,
            components["components"][a]["paths"],
        ) or _artifact_satisfies_pair(
            artifacts,
            components["components"][b]["paths"],
        )
        if satisfied:
            continue
        desc = (
            "cross-component change: peer pair ('%s', '%s') both touched; "
            "no e2e artifact under either component's paths matches e2e_patterns"
            % (a, b)
        )
        _emit_p2(a, b, desc)

    return findings


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


# ---------------------------------------------------------------------------
# Orchestration: load config, run both layers, deduplicate findings
# ---------------------------------------------------------------------------

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

    Dedup: if Layer 2 fires, Layer 1 is suppressed entirely. Layer 2 is
        strictly stronger (enforceable, opt-in); Layer 1 adds no signal when
        Layer 2 already covers the same change. This is whole-diff
        simplification: even a partial L2 match drops the L1 finding.
    """
    infra_errors: list[str] = []
    config_error_findings: list[StateFinding] = []
    try:
        # Load components.yaml (Layer 2 config; None = opt-in not exercised).
        components_dict: Optional[dict] = None
        try:
            components_dict = load_components_yaml(repo_root)
        except ComponentsConfigError as cfg_err:
            # Surface the config error as a single UNCERTAIN finding so humans
            # see it. Layer 1 still runs (on default grouping = no config).
            config_error_findings.append(
                StateFinding(
                    id="e2e-layer2",
                    fingerprint="e2e-config-error",
                    source="E2E_CHECK",
                    disposition=Disposition.UNCERTAIN,
                    file="",
                    line_range=[],
                    description=str(cfg_err),
                )
            )
            components_dict = None

        # Extract name->paths mapping for Layer 1. group_source_files expects
        # {name: [patterns]}, not the full YAML dict whose top-level keys
        # ("version", "data_paths", "e2e_patterns") would be silently iterated.
        if components_dict is None:
            component_paths_map: Optional[dict] = None
        else:
            component_paths_map = {
                name: info["paths"]
                for name, info in components_dict["components"].items()
            }

        l1 = check_layer_1(diff_text, components=component_paths_map)
        l2 = check_layer_2(diff_text, repo_root, components=components_dict)

        # Dedup: Layer 2 is strictly stronger; drop Layer 1 when Layer 2 fires.
        kept_l1 = [] if l2 else l1

        return (kept_l1 + l2 + config_error_findings, infra_errors)

    except Exception as exc:  # noqa: BLE001
        infra_errors.append(str(exc))
        return ([], infra_errors)
