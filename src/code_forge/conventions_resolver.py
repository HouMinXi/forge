# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Cross-repo conventions-seed resolver.

Discovers sibling repositories from 4 prioritized sources and extracts
naming conventions from each.

Exported symbols:
  resolve_sources        -- Stage 1: discover sibling repos (4 sources)
  extract_conventions    -- Stage 2: extract naming patterns from a repo
  get_cross_repo_digest  -- Orchestrate Stage 1+2
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from .conventions import (
    _MAX_ROSTER_FILES, _SKIP_DIRS, _ScanStatus, _extract_python_public_names,
    _is_convention_file, _open_scan_root,
    _path_fallback_supported,
    _read_regular_text, _source_file_size, _walk_source,
)

_MAX_TRACKED_ROSTER_BYTES = 4_000_000
_MAX_SOURCE_STATUS_BYTES = 4_000_000

# ---------------------------------------------------------------------------
# _PATH_RE: extracts absolute paths (/...) and relative paths (../) from text
#
# Character class [\w./+~@%-] includes +, ~, @, % for broader path coverage
# .
#
# Known limitations:
#   1. Paths with spaces or other special characters are not matched.
#   2. v1 Unix-only -- Windows backslash paths are not matched.
# ---------------------------------------------------------------------------
_PATH_RE = re.compile(
    r"(?:^|\s|`)((?:/[\w./+~@%-]+)|(?:\.\.(?:/[\w./+~@%-]+)+))(?:\s|`|$)",
    re.MULTILINE,
)


@dataclass
class ResolvedSource:
    """A resolved sibling repo with metadata about how it was discovered.

    Field order: fields without defaults before fields with defaults.
    Python dataclass raises TypeError at class definition otherwise.
    """

    repo_path: Path
    priority: int
    source_type: str
    target: str = "public names"
    recipe: str = "default"
    root_identity: Optional[tuple[int, int]] = None


@dataclass
class _ConsumerRoot:
    """An opened consumer directory retained for one digest assembly."""

    fd: int
    identity: tuple[int, int]


# ---------------------------------------------------------------------------
# Symlink guard helper
# ---------------------------------------------------------------------------


def _symlink_guard_passes(resolved_path: Path, cwd: Path) -> bool:
    """Return True if resolved_path is within cwd.parent (safe sibling).

    Uses Path.parents for path-component-safe containment check, NOT
    str.startswith which is vulnerable to prefix collision:
    e.g. cwd.parent == /tmp/repo would incorrectly allow /tmp/repo_evil
    with str.startswith.

    A path is safe if:
      - It IS cwd.parent (the parent directory itself), OR
      - cwd.parent is one of its .parents ancestors.
    """
    try:
        real_path = Path(os.path.realpath(str(resolved_path))).resolve()
        parent_root = cwd.resolve().parent
    except (OSError, ValueError, RuntimeError):
        return False
    return real_path == parent_root or parent_root in real_path.parents


def _directory_identity(path: Path) -> Optional[tuple[int, int]]:
    """Remember the directory admitted by source resolution, not just its name."""
    try:
        info = path.lstat()
    except OSError:
        return None
    return (info.st_dev, info.st_ino) if stat.S_ISDIR(info.st_mode) else None


def _source_root_is_current(source: ResolvedSource) -> bool:
    """Check the named root without following a replacement symlink."""
    try:
        root_fd = _open_scan_root(source.repo_path)
        if root_fd is None:
            if not _path_fallback_supported():
                return False
            info = os.lstat(source.repo_path)
            return stat.S_ISDIR(info.st_mode) and (
                source.root_identity is None
                or (info.st_dev, info.st_ino) == source.root_identity
            )
        try:
            info = os.fstat(root_fd)
            identity = (info.st_dev, info.st_ino)
            return source.root_identity is None or identity == source.root_identity
        finally:
            os.close(root_fd)
    except OSError:
        return False


def _resolve_repo_path(raw: Path, cwd: Path) -> Optional[Path]:
    """Resolve one repository path supplied by an external input."""
    try:
        return (raw if raw.is_absolute() else cwd / raw).resolve()
    except (OSError, ValueError, RuntimeError):
        return None


def _optional_is_file(path: Path) -> bool:
    """Treat an inaccessible optional metadata file as unavailable."""
    try:
        return path.is_file()
    except OSError:
        return False


def _optional_is_dir(path: Path) -> bool:
    """Treat an inaccessible optional metadata directory as unavailable."""
    try:
        return path.is_dir()
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Stage 1: Source resolver
# ---------------------------------------------------------------------------


def resolve_sources(cwd: Path) -> list[ResolvedSource]:
    """Discover sibling repos from 4 prioritized sources.

    Returns a deduplicated, priority-ordered list of ResolvedSource entries.
    Deduplication: same repo_path from multiple sources -> keep highest priority
    (lowest priority number). dict-keyed-first-wins: sources iterated in
    priority order, so first-seen wins for each repo_path key.

    Args:
        cwd: working directory of the repo under review.

    Returns:
        List of ResolvedSource, sorted by priority ascending.
    """
    # Source admission already resolves paths; do not resolve mutable entries again.
    seen: dict[Path, ResolvedSource] = {}

    def _add(source: ResolvedSource) -> None:
        """Insert source only if repo_path not already seen (first-wins)."""
        key = source.repo_path
        if key not in seen:
            seen[key] = source

    # ------------------------------------------------------------------
    # Source 1: Custom mapping (.code-forge/conventions.yaml) -- HIGHEST
    # ------------------------------------------------------------------
    for src in _resolve_source_custom(cwd):
        _add(src)

    # ------------------------------------------------------------------
    # Source 2: AGENTS.md (PREFERRED CURATED)
    # ------------------------------------------------------------------
    for src in _resolve_source_agents_md(cwd):
        _add(src)

    # ------------------------------------------------------------------
    # Source 3: Other agent-context files (FALLBACK)
    # ------------------------------------------------------------------
    for src in _resolve_source_agent_context(cwd):
        _add(src)

    # ------------------------------------------------------------------
    # Source 4: Dependency auto-discovery (SELF-FORMING, Mode A)
    # ------------------------------------------------------------------
    for src in _resolve_source_dependency(cwd):
        _add(src)

    return list(seen.values())


def _resolve_source_custom(cwd: Path) -> list[ResolvedSource]:
    """Source 1: .code-forge/conventions.yaml (highest priority).

    Expected YAML format:
      siblings:
        - repo: "/path/to/sibling" or "../relative/sibling"
          target: "command struct names"   # optional
          recipe: "ovs_commands"           # optional

    Relative paths are resolved against cwd.
    Symlink guard applied.
    Degrades gracefully on missing/unparseable file.
    """
    yaml_path = cwd / ".code-forge" / "conventions.yaml"
    if not _optional_is_file(yaml_path):
        return []
    try:
        content = _read_regular_text(yaml_path, errors="strict")
    except UnicodeDecodeError:
        return []
    if content is None:
        return []
    try:
        data = yaml.safe_load(content)
    except (yaml.YAMLError, RecursionError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    siblings = data.get("siblings", [])
    if not isinstance(siblings, list):
        return []

    results: list[ResolvedSource] = []
    for entry in siblings:
        if isinstance(entry, str):
            repo_str = entry
            target = "public names"
            recipe = "default"
        elif isinstance(entry, dict):
            repo_str = entry.get("repo", "")
            target = entry.get("target", "public names")
            recipe = entry.get("recipe", "default")
        else:
            continue

        if not isinstance(repo_str, str) or not repo_str:
            continue
        if not isinstance(target, str):
            target = "public names"
        if not isinstance(recipe, str):
            recipe = "default"

        resolved = _resolve_repo_path(Path(repo_str), cwd)
        if resolved is None:
            continue

        # Skip self-referential paths (e.g. repo: ".").
        if resolved == cwd.resolve():
            continue

        if not os.path.isdir(str(resolved)):
            continue

        # Symlink guard.
        if not _symlink_guard_passes(resolved, cwd):
            continue

        identity = _directory_identity(resolved)
        if identity is None:
            continue

        results.append(
            ResolvedSource(
                repo_path=resolved,
                priority=1,
                source_type="custom",
                target=target,
                recipe=recipe,
                root_identity=identity,
            )
        )

    return results


def _resolve_source_agents_md(cwd: Path) -> list[ResolvedSource]:
    """Source 2: AGENTS.md (preferred curated source).

    Frontmatter detection: check content.startswith("---\\n") before
    attempting YAML frontmatter parse. If starts with "---\\n", find the
    closing "---" and yaml.safe_load the text between. Look for keys:
    "related_repos", "siblings", "related".

    If no YAML frontmatter, fall back to _PATH_RE regex extraction over the
    markdown body.

    Each matched path must exist as a directory and pass symlink guard.
    """
    agents_md = cwd / "AGENTS.md"
    if not _optional_is_file(agents_md):
        return []
    content = _read_regular_text(agents_md)
    if content is None:
        return []

    repo_paths: list[Path] = []

    # Frontmatter detection: MUST use startswith("---\n").
    if content.startswith("---\n"):
        # Find the closing "---" line.
        rest = content[4:]  # skip opening "---\n"
        end_idx = rest.find("\n---")
        if end_idx >= 0:
            fm_text = rest[:end_idx]
            try:
                fm = yaml.safe_load(fm_text)
            except (yaml.YAMLError, RecursionError, ValueError):
                fm = None
            if isinstance(fm, dict):
                for key in ("related_repos", "siblings", "related"):
                    val = fm.get(key)
                    if isinstance(val, list):
                        for item in val:
                            if isinstance(item, str):
                                repo_paths.append(Path(item))
                            elif isinstance(item, dict):
                                r = item.get("repo", "")
                                if isinstance(r, str) and r:
                                    repo_paths.append(Path(r))

    # Always also try regex extraction on the full content (captures paths
    # that appear in the markdown body regardless of frontmatter).
    for match in _PATH_RE.finditer(content):
        repo_paths.append(Path(match.group(1)))

    # Deduplicate raw paths before resolution to avoid redundant isdir checks
    # when the same path appears in both YAML frontmatter and markdown body.
    seen_raw: set[str] = set()
    unique_paths: list[Path] = []
    for p in repo_paths:
        key = str(p)
        if key not in seen_raw:
            seen_raw.add(key)
            unique_paths.append(p)

    return _resolve_paths_as_sources(unique_paths, cwd, priority=2, source_type="agents_md")


def _resolve_source_agent_context(cwd: Path) -> list[ResolvedSource]:
    """Source 3: Other agent-context files (fallback).

    Checks: CLAUDE.md, .cursorrules, .github/copilot-instructions.md,
    GEMINI.md, .windsurfrules, and .cursor/rules/*.mdc files.

    Each .mdc file is processed independently with _PATH_RE, then deduplicated
    via dict-keyed-first-wins mechanism.

    Symlink guard applied.
    """
    context_files = [
        cwd / "CLAUDE.md",
        cwd / ".cursorrules",
        cwd / ".github" / "copilot-instructions.md",
        cwd / "GEMINI.md",
        cwd / ".windsurfrules",
    ]

    # Also glob .cursor/rules/*.mdc (Cursor rule files).
    mdc_dir = cwd / ".cursor" / "rules"
    if _optional_is_dir(mdc_dir):
        try:
            for mdc_file in sorted(mdc_dir.glob("*.mdc")):
                context_files.append(mdc_file)
        except OSError:
            pass

    # Dict keyed on the admitted resolved path for dedup within Source 3.
    # Prevents the same path referenced from two .mdc files from being added twice.
    seen_in_src3: dict[Path, ResolvedSource] = {}

    for ctx_file in context_files:
        if not _optional_is_file(ctx_file):
            continue
        content = _read_regular_text(ctx_file)
        if content is None:
            continue

        repo_paths: list[Path] = []
        for match in _PATH_RE.finditer(content):
            repo_paths.append(Path(match.group(1)))

        for src in _resolve_paths_as_sources(repo_paths, cwd, priority=3, source_type="agent_context"):
            key = src.repo_path
            if key not in seen_in_src3:
                seen_in_src3[key] = src

    return list(seen_in_src3.values())


def _resolve_source_dependency(cwd: Path) -> list[ResolvedSource]:
    """Source 4: Dependency auto-discovery (SELF-FORMING, Mode A).

    Checks .gitmodules, package.json, pyproject.toml, go.mod, Cargo.toml
    for local dependency references.

    pyproject.toml: concrete regex r'path\\s*=\\s*[\"\\x27]([^\"\\x27]+)[\"\\x27]'
    Matches path= fields in [tool.setuptools] and [tool.poetry.dependencies].
    Known limitation: "v1 handles path= fields only; Poetry/uv workspace refs
    (packages = [{include = ...}]) require custom mapping (Source 1)."
    """
    repo_paths: list[Path] = []

    # .gitmodules: "path = " lines.
    gitmodules = cwd / ".gitmodules"
    if _optional_is_file(gitmodules):
        content = _read_regular_text(gitmodules)
        if content is None:
            content = ""
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("path = "):
                rel = stripped[len("path = ") :].strip()
                repo_paths.append(cwd / rel)

    # package.json: "file:" prefix and "../" relative deps.
    pkg_json = cwd / "package.json"
    if _optional_is_file(pkg_json):
        try:
            content = _read_regular_text(pkg_json, errors="strict")
            try:
                data = json.loads(content) if content is not None else {}
            except (RecursionError, ValueError):
                data = {}
            if not isinstance(data, dict):
                data = {}
            for section in ("dependencies", "devDependencies"):
                deps = data.get(section, {})
                if isinstance(deps, dict):
                    for val in deps.values():
                        if isinstance(val, str):
                            if val.startswith("file:"):
                                rel = val[len("file:") :]
                                repo_paths.append(Path(rel))
                            elif val.startswith("../"):
                                repo_paths.append(Path(val))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass

    # pyproject.toml: concrete regex for path= fields.
    # Limitation: Poetry/uv workspace refs require custom mapping (Source 1).
    pyproject = cwd / "pyproject.toml"
    if _optional_is_file(pyproject):
        try:
            content = _read_regular_text(pyproject)
            if content is None:
                content = ""
            _PYPROJECT_PATH_RE = re.compile(r'path\s*=\s*["\x27]([^"\x27]+)["\x27]')
            repo_paths.extend(Path(match.group(1)) for match in _PYPROJECT_PATH_RE.finditer(content))
        except OSError:
            pass

    # go.mod: "replace" directives with local paths (=> ./ or => ../).
    go_mod = cwd / "go.mod"
    if _optional_is_file(go_mod):
        try:
            content = _read_regular_text(go_mod)
            if content is None:
                content = ""
            _GOMOD_RE = re.compile(r"=>\s+(\.\.?/\S+)")
            for match in _GOMOD_RE.finditer(content):
                rel = match.group(1)
                repo_paths.append(Path(rel))
        except OSError:
            pass

    # Cargo.toml: dependencies with "path = " values.
    cargo_toml = cwd / "Cargo.toml"
    if _optional_is_file(cargo_toml):
        try:
            content = _read_regular_text(cargo_toml)
            if content is None:
                content = ""
            _CARGO_PATH_RE = re.compile(r'path\s*=\s*["\x27]([^"\x27]+)["\x27]')
            repo_paths.extend(Path(match.group(1)) for match in _CARGO_PATH_RE.finditer(content))
        except OSError:
            pass

    return _resolve_paths_as_sources(repo_paths, cwd, priority=4, source_type="dependency")


def _resolve_paths_as_sources(
    paths: list[Path],
    cwd: Path,
    priority: int,
    source_type: str,
) -> list[ResolvedSource]:
    """Resolve a list of raw paths into ResolvedSource entries.

    Each path is:
    1. Resolved relative to cwd if not absolute.
    2. Skipped if it resolves to cwd itself (self-referential dep).
    3. Checked with os.path.isdir.
    4. Checked with symlink guard.
    """
    cwd_resolved = cwd.resolve()
    results: list[ResolvedSource] = []
    for raw_path in paths:
        resolved = _resolve_repo_path(raw_path, cwd)
        if resolved is None:
            continue

        # Skip self-referential paths (e.g. pyproject.toml with path = ".").
        if resolved == cwd_resolved:
            continue

        if not os.path.isdir(str(resolved)):
            continue

        if not _symlink_guard_passes(resolved, cwd):
            continue

        identity = _directory_identity(resolved)
        if identity is None:
            continue

        results.append(
            ResolvedSource(
                repo_path=resolved,
                priority=priority,
                source_type=source_type,
                root_identity=identity,
            )
        )

    return results


# ---------------------------------------------------------------------------
# Stage 2: Convention / vocabulary extraction
# ---------------------------------------------------------------------------


def extract_conventions(source: ResolvedSource, *, scan_status: Optional[_ScanStatus] = None) -> str:
    """Extract naming conventions from a resolved sibling repo.

    Implements multi-language extraction (Python, JS/TS, Go, Rust).
    Python extraction REUSES the shared _extract_python_public_names helper
    from conventions.py -- no duplicated AST logic.

    Per-file guards (all languages):
    - Skips files >100KB
    - Symlink traversal guard via Path.parents containment check
    - _SKIP_DIRS pruning via os.walk

    JS/TS extraction uses two regex passes:
      Pass 1 (named exports): export function/class/const/let/var/type/interface
      Pass 2 (export default): export default function/class
    Known limitation: Named re-exports (export { X } from '...') are not matched.

    Args:
        source: ResolvedSource describing the sibling repo and extraction recipe.
        scan_status: optional completeness signal for source verification.

    Returns:
        Formatted conventions string, or "" if no names found.
    """
    scan_root = source.repo_path.absolute()
    try:
        root_fd = _open_scan_root(scan_root)
        if root_fd is None:
            if not _path_fallback_supported():
                if scan_status is not None:
                    scan_status.fail()
                return ""
            # The opened leaf can be contained, but the walk has no pinned
            # root; its digest is useful now and must be retried next time.
            if scan_status is not None:
                scan_status.fail()
    except OSError as exc:
        if scan_status is not None:
            scan_status.fail(exc)
        return ""
    try:
        info = os.fstat(root_fd) if root_fd is not None else os.lstat(scan_root)
        if not stat.S_ISDIR(info.st_mode):
            if scan_status is not None:
                scan_status.fail()
            return ""
        identity = (info.st_dev, info.st_ino)
        if source.root_identity is not None and identity != source.root_identity:
            if scan_status is not None:
                scan_status.fail()
            return ""

        # Every language walks and reads beneath the same opened directory.
        py_funcs, py_classes = _extract_python_public_names(
            scan_root, scan_status=scan_status, scan_root=scan_root, scan_root_fd=root_fd,
        )
        py_names = py_funcs + py_classes
        js_names = _extract_js_ts_names(
            scan_root, scan_root, scan_status=scan_status, scan_root_fd=root_fd,
        )
        go_names = _extract_go_names(scan_root, scan_root, scan_status=scan_status, scan_root_fd=root_fd)
        rust_names = _extract_rust_names(
            scan_root, scan_root, scan_status=scan_status, scan_root_fd=root_fd,
        )
    finally:
        if root_fd is not None:
            os.close(root_fd)

    if not _source_root_is_current(source) and scan_status is not None:
        scan_status.fail()

    if not py_names and not js_names and not go_names and not rust_names:
        return ""

    # Build output header.
    repo_name = source.repo_path.name
    if source.recipe != "default":
        header = "## " + repo_name + " conventions (recipe: " + source.recipe + ")"
    else:
        header = "## " + repo_name + " conventions"

    lines = [header, "- target: " + source.target]

    if py_names:
        lines.append("- python: " + ", ".join(py_names))
    if js_names:
        lines.append("- js/ts: " + ", ".join(js_names))
    if go_names:
        lines.append("- go: " + ", ".join(go_names))
    if rust_names:
        lines.append("- rust: " + ", ".join(rust_names))

    return "\n".join(lines) + "\n"


def _iter_files(
    root: Path,
    scan_root: Path,
    extensions: tuple[str, ...],
    exclude_extensions: tuple[str, ...] = (),
    *,
    scan_status: Optional[_ScanStatus] = None,
    scan_root_fd: Optional[int] = None,
) -> list[Path]:
    """Walk root, yielding files with given extensions.

    Applies _SKIP_DIRS pruning, 100KB size cap, and
    symlink guard with Path.parents containment check.

    Args:
        root: directory to walk.
        scan_root: resolved root for symlink containment check.
        extensions: file extensions to include (e.g. (".ts", ".js")).
        exclude_extensions: file extensions to exclude (e.g. (".d.ts",)).
    """
    result: list[Path] = []
    for dirpath, dirs, files in _walk_source(root, scan_root, scan_root_fd, scan_status):
        # Prune _SKIP_DIRS in-place.
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

        for fname in files:
            # Extension filter.
            matched = any(fname.endswith(ext) for ext in extensions)
            excluded = any(fname.endswith(ext) for ext in exclude_extensions)
            if not matched or excluded:
                continue

            fpath = Path(dirpath) / fname

            # Symlink guard.
            try:
                candidate_info = fpath.stat()
                real_path = Path(os.path.realpath(str(fpath))).resolve()
            except (OSError, ValueError, RuntimeError):
                if scan_status is not None:
                    scan_status.fail()
                continue
            if real_path != scan_root and scan_root not in real_path.parents:
                if scan_status is not None:
                    scan_status.fail()
                continue
            if scan_status is not None:
                scan_status.record_candidate(fpath, real_path, scan_root)

            # Only an admitted file may be skipped as intentionally oversized.
            try:
                size = _source_file_size(
                    real_path, scan_root, scan_root_fd, observed=candidate_info,
                )
                if scan_status is not None and scan_root_fd is not None:
                    scan_status.record_admitted_file(
                        real_path, scan_root, scan_root_fd, candidate_info,
                    )
                if size > 100_000:
                    if scan_status is not None:
                        scan_status.record_skip(fpath, scan_root, size)
                    continue
            except OSError:
                if scan_status is not None:
                    scan_status.fail()
                continue

            result.append(real_path)

    return result


def _extract_js_ts_names(
    root: Path, scan_root: Path, *, scan_status: Optional[_ScanStatus] = None,
    scan_root_fd: Optional[int] = None,
) -> list[str]:
    """Extract exported names from .js and .ts files (excluding .d.ts).

    Two regex passes:
      Pass 1: named exports (export function/class/const/let/var/type/interface)
      Pass 2: export default function/class

    Known limitation: Named re-exports (export { X } from '...') are not matched.

    Cap at 50 names.
    """
    _NAMED_EXPORT_RE = re.compile(r"export\s+(?:function|class|const|let|var|type|interface)\s+(\w+)")
    _DEFAULT_EXPORT_RE = re.compile(r"export\s+default\s+(?:function|class)\s+(\w+)")

    names: list[str] = []
    files = _iter_files(
        root, scan_root, (".js", ".ts"), (".d.ts",),
        scan_status=scan_status, scan_root_fd=scan_root_fd,
    )

    for fpath in files:
        content = _read_regular_text(
            fpath, max_bytes=100_000, scan_root=scan_root,
            scan_root_fd=scan_root_fd, scan_status=scan_status,
        )
        if content is None:
            continue
        # Pass 1: named exports.
        for match in _NAMED_EXPORT_RE.finditer(content):
            names.append(match.group(1))
        # Pass 2: export default.
        for match in _DEFAULT_EXPORT_RE.finditer(content):
            names.append(match.group(1))

    return list(dict.fromkeys(names))[:50]  # deduplicate, cap at 50


def _extract_go_names(
    root: Path, scan_root: Path, *, scan_status: Optional[_ScanStatus] = None,
    scan_root_fd: Optional[int] = None,
) -> list[str]:
    """Extract exported names from .go files.

    Matches: func/type starting with a capital letter (exported in Go).
    Cap at 50 names.
    """
    _GO_EXPORT_RE = re.compile(r"^(?:func|type)\s+([A-Z]\w+)", re.MULTILINE)

    names: list[str] = []
    files = _iter_files(root, scan_root, (".go",), scan_status=scan_status, scan_root_fd=scan_root_fd)

    for fpath in files:
        content = _read_regular_text(
            fpath, max_bytes=100_000, scan_root=scan_root,
            scan_root_fd=scan_root_fd, scan_status=scan_status,
        )
        if content is None:
            continue
        for match in _GO_EXPORT_RE.finditer(content):
            names.append(match.group(1))

    return list(dict.fromkeys(names))[:50]


def _extract_rust_names(
    root: Path, scan_root: Path, *, scan_status: Optional[_ScanStatus] = None,
    scan_root_fd: Optional[int] = None,
) -> list[str]:
    """Extract public names from .rs files.

    Matches: pub fn/struct/enum/trait/type declarations.
    Cap at 50 names.
    """
    _RUST_PUB_RE = re.compile(r"pub\s+(?:fn|struct|enum|trait|type)\s+(\w+)")

    names: list[str] = []
    files = _iter_files(root, scan_root, (".rs",), scan_status=scan_status, scan_root_fd=scan_root_fd)

    for fpath in files:
        content = _read_regular_text(
            fpath, max_bytes=100_000, scan_root=scan_root,
            scan_root_fd=scan_root_fd, scan_status=scan_status,
        )
        if content is None:
            continue
        for match in _RUST_PUB_RE.finditer(content):
            names.append(match.group(1))

    return list(dict.fromkeys(names))[:50]


# ---------------------------------------------------------------------------
# Stage 1+2 orchestration with caching
# ---------------------------------------------------------------------------


def _run_git_object_probe(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
    """Read local objects without allowing a promisor remote to fetch gaps."""
    return subprocess.run(
        ["git", "--no-lazy-fetch", "-C", str(repo_path), *args],
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
        capture_output=True, timeout=5, check=False,
    )


def _tracked_convention_files(repo_path: Path, commit_hash: str) -> set[Path]:
    """List selected files in a full Git object ID before caching."""
    if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", commit_hash):
        raise OSError("source has no full Git object ID")
    proc = _run_git_object_probe(
        repo_path, "ls-tree", "-r", "--name-only", "-z", commit_hash, "--", ".",
    )
    raw = proc.stdout
    if (
        proc.returncode != 0 or not isinstance(raw, bytes)
        or len(raw) > _MAX_TRACKED_ROSTER_BYTES
        or (raw and not raw.endswith(b"\0"))
    ):
        raise OSError("cannot prove committed source file roster")
    selected: set[Path] = set()
    for name in raw.split(b"\0"):
        if not name:
            continue
        path = Path(os.fsdecode(name))
        if path.is_absolute() or ".." in path.parts:
            raise OSError("invalid committed source path")
        if _is_convention_file(path.name) and not any(
            part in _SKIP_DIRS for part in path.parts[:-1]
        ):
            selected.add(path)
            if len(selected) > _MAX_ROSTER_FILES:
                raise OSError("committed source roster too large")
    return selected


def _git_index_generation(repo_path: Path) -> Optional[tuple[int, int, int, int, int]]:
    """Detect a checkout that returns to the same HEAD during a direct scan."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "--git-path", "index"],
            capture_output=True, timeout=5, check=False,
        )
        if proc.returncode != 0 or not isinstance(proc.stdout, bytes):
            return None
        path = Path(os.fsdecode(proc.stdout.strip()))
        if not path.is_absolute():
            path = repo_path / path
        info = path.stat()
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return (
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


def _scanned_reads_still_current(repo_path: Path, scan_status: _ScanStatus) -> bool:
    """Allow an unproven direct digest only while its read bytes remain."""
    root_fd: Optional[int] = None
    try:
        root_fd = _open_scan_root(repo_path)
        if root_fd is None:
            return False
        for path, hashes in scan_status.read_blobs.items():
            current = _ScanStatus()
            content = _read_regular_text(
                repo_path / path, max_bytes=100_000, scan_root=repo_path,
                scan_root_fd=root_fd, scan_status=current,
            )
            if (
                content is None or not current.complete
                or current.read_blobs.get(path) != hashes
            ):
                return False
        for logical in scan_status.skipped_files:
            resolved = scan_status.candidates[logical]
            if _source_file_size(repo_path / resolved, repo_path, root_fd) <= 100_000:
                return False
    except (OSError, KeyError):
        return False
    finally:
        if root_fd is not None:
            os.close(root_fd)
    return True


def _scan_matches_commit(
    repo_path: Path, commit_hash: str, scan_status: _ScanStatus,
) -> Optional[bool]:
    """Compare scanned source bytes and skip decisions with an immutable tree.

    None means Git could not prove the match, so the direct digest can be
    returned after direct source checks. False means the scan differs from
    the keyed commit, so its digest must be discarded.
    """
    try:
        proc = _run_git_object_probe(
            repo_path, "ls-tree", "-r", "-l", "-z", commit_hash, "--", ".",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    raw = proc.stdout
    if (
        proc.returncode != 0 or not isinstance(raw, bytes)
        or len(raw) > _MAX_TRACKED_ROSTER_BYTES
        or (raw and not raw.endswith(b"\0"))
    ):
        return None

    # Git paths are NUL-delimited; a tab inside a filename is unambiguous.
    tree: dict[Path, tuple[bytes, str, int]] = {}
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        try:
            header, name = entry.split(b"\t", 1)
            mode, kind, object_id, raw_size = header.split()
            path = Path(os.fsdecode(name))
            size = int(raw_size)
        except (ValueError, UnicodeError):
            return None
        if (
            kind != b"blob" or len(object_id) != len(commit_hash)
            or not re.fullmatch(rb"[0-9a-fA-F]+", object_id)
            or path.is_absolute() or ".." in path.parts or size < 0
        ):
            return None
        tree[path] = (mode, object_id.decode("ascii"), size)

    committed_dirs = {
        parent for path in tree for parent in path.parents if parent != Path(".")
    }

    def link_target(
        object_id: str, size: int,
    ) -> Optional[tuple[bool, tuple[str, ...]]]:
        if size > 4096:
            return None
        try:
            link = _run_git_object_probe(
                repo_path, "cat-file", "blob", object_id,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if (
            link.returncode != 0 or not isinstance(link.stdout, bytes)
            or len(link.stdout) != size or not link.stdout or b"\0" in link.stdout
        ):
            return None
        target = Path(os.fsdecode(link.stdout))
        if target.is_absolute():
            try:
                target = target.relative_to(repo_path)
            except ValueError:
                return None
            return True, target.parts
        return False, target.parts

    def resolved_link(path: Path) -> Optional[tuple[Path, bool]]:
        """Resolve every committed path component before interpreting `..`."""
        pending = list(path.parts)
        resolved: list[str] = []
        links_followed = 0
        saw_alias = False
        while pending:
            if len(pending) + len(resolved) > 1024:
                return None
            component = pending.pop(0)
            if component == "..":
                if not resolved:
                    return None
                resolved.pop()
                continue
            candidate = Path(*resolved, component)
            entry = tree.get(candidate)
            if entry is not None and entry[0] == b"120000":
                saw_alias = True
                if links_followed >= 40:
                    return None
                target = link_target(entry[1], entry[2])
                if target is None:
                    return None
                absolute, parts = target
                if absolute:
                    resolved.clear()
                pending[:0] = parts
                links_followed += 1
                continue
            if pending and candidate not in committed_dirs:
                return None
            if entry is not None and entry[0] not in (b"100644", b"100755"):
                return None
            resolved.append(component)
        final = Path(*resolved) if resolved else Path(".")
        if final != Path(".") and final not in tree and final not in committed_dirs:
            return None
        return final, saw_alias

    committed_alias = False
    src = tree.get(Path("src"))
    if src is not None and src[0] == b"120000":
        committed_alias = True
        resolved_src = resolved_link(Path("src"))
        if resolved_src is None:
            return None
        expected_python_root = resolved_src[0]
        if not any(
            expected_python_root in path.parents for path in tree
        ):
            return None
    elif any(Path("src") in path.parents for path in tree):
        expected_python_root = Path("src")
    else:
        expected_python_root = Path(".")
    if scan_status.python_walk_root != expected_python_root:
        # Git omits empty directories. An otherwise clean, empty local src/
        # changes Python's walk root without changing the committed sources.
        if expected_python_root == Path(".") and scan_status.python_walk_root == Path("src"):
            return None
        return False

    unproven_bytes = False
    for logical, resolved in scan_status.candidates.items():
        entry = tree.get(logical)
        if entry is None:
            return False
        mode, object_id, size = entry
        if mode == b"120000":
            resolved_alias = resolved_link(logical)
            if resolved_alias is None:
                return None
            target, has_alias = resolved_alias
            committed_alias |= has_alias
            if target != resolved:
                return None if has_alias else False
        elif mode in (b"100644", b"100755"):
            if logical != resolved:
                return False
        else:
            return None

        content = tree.get(resolved)
        if content is None or content[0] not in (b"100644", b"100755"):
            return None
        if logical in scan_status.skipped_files:
            if scan_status.skipped_files[logical] <= 100_000 or content[2] <= 100_000:
                return False
        else:
            observed = scan_status.read_blobs.get(resolved)
            if observed is None:
                return None
            hash_indices = (0, 2) if len(commit_hash) == 40 else (1, 3)
            if content[1] not in (observed[index] for index in hash_indices):
                unproven_bytes = True

    for path in scan_status.seen_files:
        if path in scan_status.candidates:
            continue
        if path.suffix != ".py" or scan_status.python_walk_root in path.parents:
            return None
    if unproven_bytes:
        if not _scanned_reads_still_current(repo_path, scan_status):
            return False
        scan_status.unproven_bytes = True
        return None
    scan_status.committed_alias = committed_alias
    return True


def _passive_git_listing(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
    """Read the index without callbacks or a missing-object fetch."""
    return subprocess.run(
        ["git", "--no-lazy-fetch", "-C", str(repo_path),
         "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}",
         "ls-files", *args],
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
        capture_output=True, timeout=5, check=False,
    )


def _git_index_has_ordinary_entries(repo_path: Path) -> bool:
    """Reject index flags that can hide working-tree edits from proof."""
    try:
        proc = _passive_git_listing(repo_path, "--cached", "-v", "-z", "--", ".")
    except (OSError, subprocess.TimeoutExpired):
        return False
    raw = proc.stdout
    if (
        proc.returncode != 0 or not isinstance(raw, bytes)
        or len(raw) > _MAX_SOURCE_STATUS_BYTES
        or (raw and not raw.endswith(b"\0"))
    ):
        return False
    # Git prints "h" for assume-unchanged and "S" for skip-worktree.
    # Conservatively bypass additional commit proof for unusual index state.
    return all(len(entry) > 2 and entry.startswith(b"H ") for entry in raw.split(b"\0")[:-1])


def _git_filter_config_absent(repo_path: Path) -> bool:
    """Fail closed when any effective clean/process filter may execute."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "config", "--includes", "--null",
             "--get-regexp", r"^filter\..*\.(clean|process)$"],
            capture_output=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return (
        proc.returncode == 1 and proc.stdout == b"" and proc.stderr == b""
    )


def _source_cache_eligible(repo_path: Path, commit_hash: str) -> bool:
    """Check whether HEAD and index permit an additional committed-byte proof.

    Direct extraction still includes untracked and ignored source files. These
    passive Git listings never ask Git to read file contents or run filters.
    Fail closed on filter configuration and unusual index flags.
    """
    if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", commit_hash):
        return False
    if not _git_filter_config_absent(repo_path):
        return False
    for listing in (
        ("--others", "--exclude-standard", "--directory"),
        ("--others", "--ignored", "--exclude-standard", "--directory"),
    ):
        try:
            proc = _passive_git_listing(repo_path, *listing, "-z", "--", ".")
        except (OSError, subprocess.TimeoutExpired):
            return False
        raw = proc.stdout
        if (
            proc.returncode != 0 or not isinstance(raw, bytes)
            or len(raw) > _MAX_SOURCE_STATUS_BYTES
            or (raw and not raw.endswith(b"\0"))
        ):
            return False
        for name in raw.split(b"\0")[:-1]:
            path = Path(os.fsdecode(name))
            if not name or path.is_absolute() or ".." in path.parts:
                return False
            if any(part in _SKIP_DIRS for part in path.parts[:-1]):
                continue
            if _is_convention_file(path.name):
                return False
            # --directory may collapse an ignored or untracked directory;
            # the scanner can descend into any directory it does not exclude.
            if name.endswith(b"/") and path.name not in _SKIP_DIRS:
                return False
    return (
        _git_filter_config_absent(repo_path)
        and _git_index_has_ordinary_entries(repo_path)
        and _get_git_commit(repo_path) == commit_hash
    )


def _scanned_aliases_still_current(source: ResolvedSource, scan_status: _ScanStatus) -> bool:
    """Reject a direct digest when a selected alias changed after its read."""
    scan_root = source.repo_path.absolute()
    for logical, resolved in scan_status.candidates.items():
        if logical == resolved:
            continue
        try:
            current = Path(os.path.realpath(scan_root / logical)).resolve()
            current = current.relative_to(scan_root)
        except (OSError, ValueError, RuntimeError):
            return False
        if current != resolved:
            return False
    return True


def _scan_snapshot_is_current(source: ResolvedSource, scan_status: _ScanStatus) -> bool:
    """Compare a final bound walk with the files observed during extraction."""
    scan_root = source.repo_path.absolute()
    root_fd: Optional[int] = None
    try:
        root_fd = _open_scan_root(scan_root)
        if root_fd is None:
            scan_status.fail()
            return False
        info = os.fstat(root_fd)
        if (
            not stat.S_ISDIR(info.st_mode)
            or source.root_identity != (info.st_dev, info.st_ino)
        ):
            scan_status.fail()
            return False
        for _dirpath, dirs, _files in _walk_source(
            scan_root, scan_root, root_fd, scan_status,
        ):
            dirs[:] = [name for name in dirs if name not in _SKIP_DIRS]
    except OSError as exc:
        scan_status.fail(exc)
    finally:
        if root_fd is not None:
            os.close(root_fd)
    return (
        scan_status.complete and _source_root_is_current(source)
        and _scanned_aliases_still_current(source, scan_status)
    )


def _repo_path_hash(repo_path: Path) -> Optional[str]:
    """Hash the filesystem's reversible encoding of a resolved path."""
    try:
        path_bytes = os.fsencode(repo_path)
    except (UnicodeEncodeError, ValueError):
        return None
    if b"\0" in path_bytes:
        return None
    return hashlib.sha256(path_bytes).hexdigest()[:12]


def _pin_consumer_root(cwd: Path) -> Optional[_ConsumerRoot]:
    """Pin every component of the consumer path before reading metadata."""
    fd = None
    try:
        fd = _open_scan_root(cwd.absolute())
        if fd is None:
            return None
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode):
            return None
        result = _ConsumerRoot(fd, (info.st_dev, info.st_ino))
        fd = None
        return result
    except (OSError, ValueError):
        return None
    finally:
        if fd is not None:
            os.close(fd)


def _consumer_root_alias(root: _ConsumerRoot) -> Optional[Path]:
    """Name a retained directory handle without reopening the mutable cwd."""
    for fd_directory in ("/proc/self/fd", "/dev/fd"):
        alias = Path(fd_directory) / str(root.fd)
        try:
            info = alias.stat()
        except (OSError, ValueError):
            continue
        if stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == root.identity:
            return alias
    return None


def get_cross_repo_digest(cwd: Path) -> str:
    """Assemble sibling conventions while retaining the consumer metadata root."""
    consumer_root = _pin_consumer_root(cwd)
    if consumer_root is None and not _path_fallback_supported():
        # A failed rooted open can mean an ancestor was replaced. Do not
        # discover sibling metadata through that unbound pathname.
        return ""
    try:
        source_cwd = _consumer_root_alias(consumer_root) if consumer_root is not None else cwd
        if source_cwd is None:
            return ""
        return _collect_cross_repo_digest(source_cwd)
    finally:
        if consumer_root is not None:
            os.close(consumer_root.fd)


def _collect_cross_repo_digest(source_cwd: Path) -> str:
    """Resolve and scan siblings without trusting or changing workspace cache files.

    A consumer-controlled directory can move after a successful binding check,
    so even descriptor-relative cache writes and cleanup can affect an external
    location. Existing cache entries can also contain forged digests. Read the
    source on every call and leave all existing cache entries untouched.
    """
    digests: list[str] = []
    for source in resolve_sources(source_cwd):
        if _repo_path_hash(source.repo_path) is None:
            continue
        commit_hash = _get_git_commit(source.repo_path)
        if not _source_root_is_current(source):
            continue

        commit_proof_eligible = _source_cache_eligible(source.repo_path, commit_hash)
        index_generation = (
            _git_index_generation(source.repo_path) if commit_proof_eligible else None
        )
        scan_status = _ScanStatus()
        digest = extract_conventions(source, scan_status=scan_status)
        snapshot_current = True
        if scan_status.complete and commit_proof_eligible:
            snapshot_current = _scan_snapshot_is_current(source, scan_status)
        if scan_status.complete and commit_proof_eligible:
            try:
                tracked_files = _tracked_convention_files(source.repo_path, commit_hash)
            except (OSError, subprocess.TimeoutExpired):
                scan_status.fail()
            else:
                if tracked_files != scan_status.seen_files:
                    scan_status.fail()
        if scan_status.complete and commit_proof_eligible:
            proof = _scan_matches_commit(source.repo_path, commit_hash, scan_status)
            if proof is not True:
                scan_status.fail()
                if proof is False:
                    snapshot_current = False

        if not _source_root_is_current(source) or _get_git_commit(source.repo_path) != commit_hash:
            continue
        if not snapshot_current:
            continue
        if scan_status.unproven_bytes and (
            index_generation is None
            or _git_index_generation(source.repo_path) != index_generation
        ):
            continue
        if not _scanned_aliases_still_current(source, scan_status):
            continue
        if digest:
            digests.append(digest)

    return "\n\n".join(digests)


def _get_git_commit(repo_path: Path) -> str:
    """Get HEAD commit hash for a repo via git subprocess.

    Uses capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5, check=False.
    Catches TimeoutExpired and FileNotFoundError -> "no-git" fallback.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
        return proc.stdout.strip() if proc.returncode == 0 else "no-git"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "no-git"
