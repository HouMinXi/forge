# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Conventions digest module.

Extracts naming conventions from the current repository to populate the
conventions-digest slot in criteria payloads. The digest is derived
independently of the implementer.

Exported symbols:
  _extract_python_public_names -- shared AST helper (also used by conventions_resolver.py)
  get_same_repo_digest         -- same-repo naming conventions digest
  get_digest                   -- full conventions digest
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Optional

_SKIP_DIRS = frozenset({
    "__pycache__", ".git", "venv", ".venv", ".tox",
    "build", "dist", "node_modules", ".eggs",
})


def _extract_python_public_names(
    dirpath: Path,
) -> tuple[list[str], list[str]]:
    """Extract public function and class names from Python source files.

    Shared private helper used by get_same_repo_digest (this file) and
    extract_conventions (conventions_resolver.py). Single source of
    truth for Python AST extraction -- no duplicated AST logic.

    Scans for .py files under dirpath/src (fallback to dirpath if no src/
    subdir). Uses os.walk so that _SKIP_DIRS can be pruned.

    Per-file guards:
    - Skips files larger than 100KB
    - Symlink traversal guard via Path.parents containment check, NOT
      str.startswith
    - Iterates only tree.body (top-level module children, NOT ast.walk which
      picks up nested functions)
    - Collects ast.FunctionDef AND ast.AsyncFunctionDef
    - Skips unparseable files silently (SyntaxError)

    Args:
        dirpath: root directory to scan.

    Returns:
        (public_functions, public_classes) -- each sorted, capped at 50.
    """
    scan_root = dirpath.resolve()

    # Prefer src/ subdir; fall back to dirpath itself.
    src_dir = dirpath / "src"
    walk_root = src_dir if src_dir.is_dir() else dirpath

    public_functions: set[str] = set()
    public_classes: set[str] = set()

    for root, dirs, files in os.walk(str(walk_root)):
        # Prune _SKIP_DIRS in-place so os.walk does not descend into them.
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

        for filename in files:
            if not filename.endswith(".py"):
                continue

            py_file = Path(root) / filename

            # Size guard: skip files larger than 100KB.
            try:
                if py_file.stat().st_size > 100_000:
                    continue
            except OSError:
                continue

            # Symlink traversal guard:
            # Use Path.parents for path-component-safe containment check
            # instead of str.startswith (which is a substring check that
            # matches prefix collisions like "/tmp/repo_evil" vs "/tmp/repo").
            try:
                real_path = Path(os.path.realpath(str(py_file))).resolve()
            except OSError:
                continue
            if real_path != scan_root and scan_root not in real_path.parents:
                continue

            # Parse file content.
            try:
                content = py_file.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(content)
            except SyntaxError:
                continue
            except OSError:
                continue

            # ast.walk would pick up nested functions -- we want module-level only.
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not node.name.startswith("_"):
                        public_functions.add(node.name)
                elif isinstance(node, ast.ClassDef):
                    if not node.name.startswith("_"):
                        public_classes.add(node.name)

    sorted_functions = sorted(public_functions)[:50]
    sorted_classes = sorted(public_classes)[:50]
    return (sorted_functions, sorted_classes)


def get_same_repo_digest(cwd: Path) -> str:
    """Build same-repo naming conventions digest.

    Extracts public Python function and class names from the current repo
    to give the reviewer naming convention context.

    Args:
        cwd: working directory of the repo under review.

    Returns:
        Formatted digest string, or "" if no Python sources found.
    """
    funcs, classes = _extract_python_public_names(cwd)
    if not funcs and not classes:
        return ""
    parts = []
    if funcs:
        parts.append("- public functions: " + ", ".join(funcs))
    if classes:
        parts.append("- public classes: " + ", ".join(classes))
    return "## Same-repo naming conventions\n" + "\n".join(parts) + "\n"


def get_digest(cwd: Path, backend: Optional[object] = None) -> str:
    """Build conventions digest for criteria payload.

    Args:
        cwd: working directory of the repo under review.
        backend: reserved for AI-summarization pass (unused).

    Returns:
        Conventions digest string, or "" if nothing extractable.
    """
    parts = []
    same = get_same_repo_digest(cwd)
    if same:
        parts.append(same)
    from .conventions_resolver import get_cross_repo_digest
    cross_repo = get_cross_repo_digest(cwd)
    if cross_repo:
        parts.append(cross_repo)
    return "\n\n".join(parts)
