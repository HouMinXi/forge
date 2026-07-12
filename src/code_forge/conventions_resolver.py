# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Cross-repo conventions-seed resolver.

Discovers sibling repositories from 4 prioritized sources and extracts
naming conventions from each. Results are cached keyed on sibling commit hash.

Exported symbols:
  resolve_sources        -- Stage 1: discover sibling repos (4 sources)
  extract_conventions    -- Stage 2: extract naming patterns from a repo
  get_cross_repo_digest  -- Orchestrate Stage 1+2 with caching
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .conventions import _SKIP_DIRS, _extract_python_public_names

# ---------------------------------------------------------------------------
# _PATH_RE: extracts absolute paths (/...) and relative paths (../) from text
#
# Character class [\w./+~@%-] includes +, ~, @, % for broader path coverage
#.
#
# Known limitations:
#   1. Paths with spaces or other special characters are not matched.
#   2. v1 Unix-only -- Windows backslash paths are not matched.
# ---------------------------------------------------------------------------
_PATH_RE = re.compile(
    r'(?:^|\s|`)((?:/[\w./+~@%-]+)|(?:\.\.(?:/[\w./+~@%-]+)+))(?:\s|`|$)',
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
    except OSError:
        return False
    parent_root = cwd.parent.resolve()
    return real_path == parent_root or parent_root in real_path.parents


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
    # Accumulate sources in priority order; dedup via dict keyed on resolved str.
    seen: dict[str, ResolvedSource] = {}

    def _add(source: ResolvedSource) -> None:
        """Insert source only if repo_path not already seen (first-wins)."""
        key = str(source.repo_path.resolve())
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
    if not yaml_path.is_file():
        return []
    try:
        import yaml  # pyyaml is already a project dependency
        with open(str(yaml_path), encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not isinstance(data, dict):
            return []
        siblings = data.get("siblings", [])
        if not isinstance(siblings, list):
            return []
    except Exception:
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

        if not repo_str:
            continue

        # Resolve relative paths against cwd.
        raw = Path(repo_str)
        if not raw.is_absolute():
            resolved = (cwd / raw).resolve()
        else:
            resolved = raw.resolve()

        # Skip self-referential paths (e.g. repo: ".").
        if resolved == cwd.resolve():
            continue

        if not os.path.isdir(str(resolved)):
            continue

        # Symlink guard.
        if not _symlink_guard_passes(resolved, cwd):
            continue

        results.append(ResolvedSource(
            repo_path=resolved,
            priority=1,
            source_type="custom",
            target=target,
            recipe=recipe,
        ))

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
    if not agents_md.is_file():
        return []
    try:
        content = agents_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    repo_paths: list[Path] = []

    # Frontmatter detection: MUST use startswith("---\n").
    if content.startswith("---\n"):
        try:
            import yaml
            # Find the closing "---" line.
            rest = content[4:]  # skip opening "---\n"
            end_idx = rest.find("\n---")
            if end_idx >= 0:
                fm_text = rest[:end_idx]
                fm = yaml.safe_load(fm_text)
                if isinstance(fm, dict):
                    for key in ("related_repos", "siblings", "related"):
                        val = fm.get(key)
                        if isinstance(val, list):
                            for item in val:
                                if isinstance(item, str):
                                    repo_paths.append(Path(item))
                                elif isinstance(item, dict):
                                    r = item.get("repo", "")
                                    if r:
                                        repo_paths.append(Path(r))
        except Exception:
            pass  # Degrade gracefully -- fall through to regex

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

    return _resolve_paths_as_sources(unique_paths, cwd, priority=2,
                                     source_type="agents_md")


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
    if mdc_dir.is_dir():
        try:
            for mdc_file in sorted(mdc_dir.glob("*.mdc")):
                context_files.append(mdc_file)
        except OSError:
            pass

    # Dict keyed on resolved path str for dedup within Source 3.
    # Prevents the same path referenced from two .mdc files from being added twice.
    seen_in_src3: dict[str, ResolvedSource] = {}

    for ctx_file in context_files:
        if not ctx_file.is_file():
            continue
        try:
            content = ctx_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        repo_paths: list[Path] = []
        for match in _PATH_RE.finditer(content):
            repo_paths.append(Path(match.group(1)))

        for src in _resolve_paths_as_sources(repo_paths, cwd, priority=3,
                                              source_type="agent_context"):
            key = str(src.repo_path.resolve())
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
    if gitmodules.is_file():
        try:
            content = gitmodules.read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("path = "):
                    rel = stripped[len("path = "):].strip()
                    repo_paths.append(cwd / rel)
        except OSError:
            pass

    # package.json: "file:" prefix and "../" relative deps.
    pkg_json = cwd / "package.json"
    if pkg_json.is_file():
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
            for section in ("dependencies", "devDependencies"):
                deps = data.get(section, {})
                if isinstance(deps, dict):
                    for val in deps.values():
                        if isinstance(val, str):
                            if val.startswith("file:"):
                                rel = val[len("file:"):]
                                repo_paths.append((cwd / rel).resolve())
                            elif val.startswith("../"):
                                repo_paths.append((cwd / val).resolve())
        except Exception:
            pass

    # pyproject.toml: concrete regex for path= fields.
    # Limitation: Poetry/uv workspace refs require custom mapping (Source 1).
    pyproject = cwd / "pyproject.toml"
    if pyproject.is_file():
        try:
            content = pyproject.read_text(encoding="utf-8", errors="replace")
            _PYPROJECT_PATH_RE = re.compile(
                r'path\s*=\s*["\x27]([^"\x27]+)["\x27]'
            )
            for match in _PYPROJECT_PATH_RE.finditer(content):
                raw = match.group(1)
                raw_path = Path(raw)
                if not raw_path.is_absolute():
                    repo_paths.append((cwd / raw_path).resolve())
                else:
                    repo_paths.append(raw_path)
        except OSError:
            pass

    # go.mod: "replace" directives with local paths (=> ./ or => ../).
    go_mod = cwd / "go.mod"
    if go_mod.is_file():
        try:
            content = go_mod.read_text(encoding="utf-8", errors="replace")
            _GOMOD_RE = re.compile(r'=>\s+(\.\.?/\S+)')
            for match in _GOMOD_RE.finditer(content):
                rel = match.group(1)
                repo_paths.append((cwd / rel).resolve())
        except OSError:
            pass

    # Cargo.toml: dependencies with "path = " values.
    cargo_toml = cwd / "Cargo.toml"
    if cargo_toml.is_file():
        try:
            content = cargo_toml.read_text(encoding="utf-8", errors="replace")
            _CARGO_PATH_RE = re.compile(
                r'path\s*=\s*["\x27]([^"\x27]+)["\x27]'
            )
            for match in _CARGO_PATH_RE.finditer(content):
                raw = match.group(1)
                raw_path = Path(raw)
                if not raw_path.is_absolute():
                    repo_paths.append((cwd / raw_path).resolve())
                else:
                    repo_paths.append(raw_path)
        except OSError:
            pass

    return _resolve_paths_as_sources(repo_paths, cwd, priority=4,
                                     source_type="dependency")


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
        if not raw_path.is_absolute():
            resolved = (cwd / raw_path).resolve()
        else:
            resolved = raw_path.resolve()

        # Skip self-referential paths (e.g. pyproject.toml with path = ".").
        if resolved == cwd_resolved:
            continue

        if not os.path.isdir(str(resolved)):
            continue

        if not _symlink_guard_passes(resolved, cwd):
            continue

        results.append(ResolvedSource(
            repo_path=resolved,
            priority=priority,
            source_type=source_type,
        ))

    return results


# ---------------------------------------------------------------------------
# Stage 2: Convention / vocabulary extraction
# ---------------------------------------------------------------------------

def extract_conventions(source: ResolvedSource) -> str:
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

    Returns:
        Formatted conventions string, or "" if no names found.
    """
    if not os.path.isdir(str(source.repo_path)):
        return ""

    scan_root = source.repo_path.resolve()

    # Python: reuse shared helper -- no duplicated AST logic.
    py_funcs, py_classes = _extract_python_public_names(source.repo_path)
    py_names = py_funcs + py_classes

    # JS/TS extraction.
    js_names = _extract_js_ts_names(source.repo_path, scan_root)

    # Go extraction.
    go_names = _extract_go_names(source.repo_path, scan_root)

    # Rust extraction.
    rust_names = _extract_rust_names(source.repo_path, scan_root)

    if not py_names and not js_names and not go_names and not rust_names:
        return ""

    # Build output header.
    repo_name = source.repo_path.name
    if source.recipe != "default":
        header = (
            "## " + repo_name + " conventions (recipe: " + source.recipe + ")"
        )
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
    for dirpath, dirs, files in os.walk(str(root)):
        # Prune _SKIP_DIRS in-place.
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

        for fname in files:
            # Extension filter.
            matched = any(fname.endswith(ext) for ext in extensions)
            excluded = any(fname.endswith(ext) for ext in exclude_extensions)
            if not matched or excluded:
                continue

            fpath = Path(dirpath) / fname

            # Size guard.
            try:
                if fpath.stat().st_size > 100_000:
                    continue
            except OSError:
                continue

            # Symlink guard.
            try:
                real_path = Path(os.path.realpath(str(fpath))).resolve()
            except OSError:
                continue
            if real_path != scan_root and scan_root not in real_path.parents:
                continue

            result.append(fpath)

    return result


def _extract_js_ts_names(root: Path, scan_root: Path) -> list[str]:
    """Extract exported names from .js and .ts files (excluding .d.ts).

    Two regex passes:
      Pass 1: named exports (export function/class/const/let/var/type/interface)
      Pass 2: export default function/class

    Known limitation: Named re-exports (export { X } from '...') are not matched.

    Cap at 50 names.
    """
    _NAMED_EXPORT_RE = re.compile(
        r'export\s+(?:function|class|const|let|var|type|interface)\s+(\w+)'
    )
    _DEFAULT_EXPORT_RE = re.compile(
        r'export\s+default\s+(?:function|class)\s+(\w+)'
    )

    names: list[str] = []
    files = _iter_files(root, scan_root, (".js", ".ts"), (".d.ts",))

    for fpath in files:
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Pass 1: named exports.
        for match in _NAMED_EXPORT_RE.finditer(content):
            names.append(match.group(1))
        # Pass 2: export default.
        for match in _DEFAULT_EXPORT_RE.finditer(content):
            names.append(match.group(1))

    return list(dict.fromkeys(names))[:50]  # deduplicate, cap at 50


def _extract_go_names(root: Path, scan_root: Path) -> list[str]:
    """Extract exported names from .go files.

    Matches: func/type starting with a capital letter (exported in Go).
    Cap at 50 names.
    """
    _GO_EXPORT_RE = re.compile(r'^(?:func|type)\s+([A-Z]\w+)', re.MULTILINE)

    names: list[str] = []
    files = _iter_files(root, scan_root, (".go",))

    for fpath in files:
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in _GO_EXPORT_RE.finditer(content):
            names.append(match.group(1))

    return list(dict.fromkeys(names))[:50]


def _extract_rust_names(root: Path, scan_root: Path) -> list[str]:
    """Extract public names from .rs files.

    Matches: pub fn/struct/enum/trait/type declarations.
    Cap at 50 names.
    """
    _RUST_PUB_RE = re.compile(
        r'pub\s+(?:fn|struct|enum|trait|type)\s+(\w+)'
    )

    names: list[str] = []
    files = _iter_files(root, scan_root, (".rs",))

    for fpath in files:
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in _RUST_PUB_RE.finditer(content):
            names.append(match.group(1))

    return list(dict.fromkeys(names))[:50]


# ---------------------------------------------------------------------------
# Stage 1+2 orchestration with caching
# ---------------------------------------------------------------------------

def get_cross_repo_digest(cwd: Path) -> str:
    """Orchestrate Stage 1 + Stage 2 with commit-hash caching.

    Steps:
    1. resolve_sources(cwd) -- discover sibling repos.
    2. Orphaned cache cleanup: delete cache files for path_hashes
       no longer in the current source list.
    3. For each source:
       a. Compute path_hash = sha256(str(repo_path).encode()).hexdigest()[:12]
       b. Get commit hash via git subprocess.
       c. Check cache: {path_hash}_{commit_hash}.json
       d. Cache hit: read cached digest.
       e. Cache miss: call extract_conventions, write cache file, delete stale.
    4. Concatenate non-empty digests: filter out empty strings before
       joining to avoid extra "\\n\\n" noise.

    Cache dir: cwd/.code-forge/conventions-cache/
    Cache file: {path_hash}_{commit_hash}.json
    Cache format: {"digest": str, "repo": str, "commit": str}

    Args:
        cwd: working directory of the repo under review.

    Returns:
        Combined conventions digest, or "" if no sources or all empty.
    """
    sources = resolve_sources(cwd)
    cache_dir = cwd / ".code-forge" / "conventions-cache"

    # ------------------------------------------------------------------
    # Orphaned cache cleanup: delete cache files whose
    # path_hash prefix is not in the current source list.
    # ------------------------------------------------------------------
    current_path_hashes = {
        hashlib.sha256(str(s.repo_path).encode()).hexdigest()[:12]
        for s in sources
    }
    if cache_dir.is_dir():
        try:
            for cache_file in cache_dir.glob("*.json"):
                # Extract path_hash prefix (before first underscore).
                stem = cache_file.stem  # e.g. "abc123def456_commitsha"
                parts = stem.split("_", 1)
                if parts and parts[0] not in current_path_hashes:
                    try:
                        cache_file.unlink()
                    except OSError:
                        pass
        except OSError:
            pass

    digests: list[str] = []

    for source in sources:
        path_hash = hashlib.sha256(str(source.repo_path).encode()).hexdigest()[:12]

        commit_hash = _get_git_commit(source.repo_path)

        # Cache lookup.
        cache_file = cache_dir / (path_hash + "_" + commit_hash + ".json")
        digest = _read_cache(cache_file)

        if digest is None:
            # Cache miss: extract and write.
            digest = extract_conventions(source)
            _write_cache(cache_dir, cache_file, path_hash, digest, source,
                         commit_hash)

        if digest:
            digests.append(digest)

    # Filter empty digests to avoid extra "\n\n" noise.
    return "\n\n".join(d for d in digests if d)


def _get_git_commit(repo_path: Path) -> str:
    """Get HEAD commit hash for a repo via git subprocess.

    Uses capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5, check=False.
    Catches TimeoutExpired and FileNotFoundError -> "no-git" fallback.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=5,
            check=False,
        )
        return proc.stdout.strip() if proc.returncode == 0 else "no-git"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "no-git"


def _read_cache(cache_file: Path) -> Optional[str]:
    """Read digest from cache file. Returns None on miss or error."""
    if not cache_file.is_file():
        return None
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        return data.get("digest", None)
    except Exception:
        return None


def _write_cache(
    cache_dir: Path,
    cache_file: Path,
    path_hash: str,
    digest: str,
    source: ResolvedSource,
    commit_hash: str,
) -> None:
    """Write digest to cache file after evicting stale entries for same path_hash."""
    try:
        # Create cache dir on first write.
        cache_dir.mkdir(parents=True, exist_ok=True)

        # Evict stale cache files for same path_hash before writing new one.
        for stale in cache_dir.glob(path_hash + "_*.json"):
            try:
                stale.unlink()
            except OSError:
                pass

        # Write new cache file.
        payload = {
            "digest": digest,
            "repo": str(source.repo_path),
            "commit": commit_hash,
        }
        cache_file.write_text(
            json.dumps(payload, ensure_ascii=True),
            encoding="utf-8",
        )
    except OSError:
        pass  # Cache write failure is non-fatal.
