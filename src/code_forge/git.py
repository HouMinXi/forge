# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Git subprocess wrapper with diff-spec validation.

This module is THE single owner of git subprocess calls (diff, blame)
in the codebase. All other modules call these functions -- they do not
call git directly. Addresses Consensus #1 (git diff execution unowned).

02-03 additions: repo detection, ref validation, pseudo-ref resolution.
Existing Phase 1 API unchanged. New surface (B1 + H2 fixes):
  - is_git_repo(cwd)             -> bool                   (B1)
  - resolve_git_ref(ref, cwd)    -> str (resolved sha)     (B1)
  - is_pseudo_ref(name)          -> bool
  - working_tree_diff(...)       -> str                    (H2)
  - cached_diff(...)             -> str
  - git_diff(baseline, head, ...) -> str
"""

import re
import shutil
import subprocess
import warnings
from pathlib import Path

# Safe known flags that are allowed despite starting with --
_SAFE_FLAGS = frozenset({"--staged", "--cached"})

# Allowlist regex for diff-spec values.
# Permits: branch names (feature/foo), tags (v1.2.3), commit hashes
# (abc123), HEAD references (HEAD, HEAD~1, HEAD^), remote refs
# (origin/main), commit ranges (abc..def, HEAD~3..HEAD), and the @
# character (for refs like HEAD@).
#
# Round 7 R7-L5 (DeepSeek): ^ and - placement inside the character
# class is fragile. Both are now explicitly escaped (\^, \-) so
# reordering the class will not silently change semantics.
#
# Curly braces ({}) are NOT permitted -- users should use explicit
# ref names instead of @{u} / @{upstream} syntax.
_DIFF_SPEC_RE = re.compile(
    r"^[A-Za-z0-9_./~@\^\-]+(?:\.\.[A-Za-z0-9_./~@\^\-]+)?$"
)


def validate_diff_spec(diff_spec: str) -> str:
    """Validate diff_spec against flag injection.

    Returns sanitized spec unchanged.

    Raises:
        ValueError: on empty string, unsafe flags, or characters
            outside the allowlist.
    """
    if not diff_spec:
        raise ValueError("diff_spec must not be empty")

    # Allow safe known flags
    if diff_spec in _SAFE_FLAGS:
        return diff_spec

    # Reject other leading dashes (flag injection)
    if diff_spec.startswith("-"):
        raise ValueError(
            "Invalid diff_spec: '%s' looks like a flag" % diff_spec
        )

    # Allowlist check -- reject everything not matching
    if not _DIFF_SPEC_RE.match(diff_spec):
        raise ValueError(
            "Invalid diff_spec: '%s' contains disallowed characters"
            % diff_spec
        )

    return diff_spec


def run_git_diff(
    diff_spec: str = "HEAD",
    extra_args: list[str] | None = None,
) -> str:
    """Execute git diff and return raw diff text.

    Validates diff_spec before use. Default: git diff -U0 HEAD
    (working tree vs HEAD).

    Supports: HEAD, --staged, commit..commit, commit ranges.

    Note (Round 3 item 12): extra_args exists for future extensibility
    (e.g. --name-only). Currently unused by any caller in Phase 1.

    Git diff exit code semantics (CRITICAL):
        Exit 0: no differences (return stdout, typically empty)
        Exit 1: differences found (NORMAL -- return stdout with diff)
        Exit 128+: fatal git error (raise RuntimeError with stderr)

    This addresses Mimo F-03: git diff returns 1 when differences
    exist, NOT an error condition.

    Raises:
        RuntimeError: if git is not found or git returns exit 128+
        ValueError: if diff_spec is invalid (from validate_diff_spec)
    """
    diff_spec = validate_diff_spec(diff_spec)

    if shutil.which("git") is None:
        raise RuntimeError("git not found")

    cmd = ["git", "diff", "-U0", diff_spec] + (extra_args or [])

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        check=False,
    )

    if result.returncode not in (0, 1):
        raise RuntimeError(
            result.stderr or f"git diff failed (exit {result.returncode})"
        )

    return result.stdout


# --- 02-03 additions: pseudo-refs, repo detection, ref validation ---

WORKING = "WORKING"
INDEX = "INDEX"
PSEUDO_REFS = {WORKING, INDEX}


def is_pseudo_ref(name: str) -> bool:
    """Check whether name is a forge pseudo-ref (WORKING or INDEX)."""
    return name in PSEUDO_REFS


def is_git_repo(cwd: Path) -> bool:
    """B1 fix: check whether cwd is inside a git repo.

    Uses `git rev-parse --git-dir` (non-zero outside a repo).
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=cwd,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, OSError):
        return False


def resolve_git_ref(ref: str, cwd: Path) -> str:
    """B1 fix: validate that a git ref exists; return its resolved sha.

    Raises:
        BaselineResolutionError: ref does not exist.
    """
    from .errors import BaselineResolutionError

    result = subprocess.run(
        ["git", "rev-parse", "--verify", ref + "^{commit}"],
        cwd=cwd,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise BaselineResolutionError(
            "git ref %r does not resolve in %s: %s"
            % (ref, cwd, result.stderr.strip())
        )
    return result.stdout.strip()


def _is_likely_binary(path: Path) -> bool:
    """H2 fix: heuristic binary detection via null-byte in first 8KB.

    Matches git's own diff-detection behavior (loosely). Used to skip
    binary untracked files in working_tree_diff.
    """
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
        return b"\0" in chunk
    except OSError:
        return False


def git_diff(
    baseline_ref: str,
    head_ref: str,
    paths: list[Path],
    repo_root: Path,
) -> str:
    """Standard `git diff <baseline_ref> <head_ref> -- <paths>`.

    Exit code semantics (R3-1 fix; matches Phase 1 run_git_diff per
    Mimo F-03 in src/forge/git.py:80-86):
      0 = no differences (return empty stdout)
      1 = differences found (NORMAL -- return stdout with diff)
      2+ = real git error (raise BaselineResolutionError)
    """
    from .errors import BaselineResolutionError

    cmd = (
        ["git", "diff", baseline_ref, head_ref, "--"]
        + [str(p) for p in paths]
    )
    result = subprocess.run(
        cmd, cwd=repo_root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    if result.returncode not in (0, 1):
        raise BaselineResolutionError(
            "git diff %s..%s failed (exit %d): %s"
            % (
                baseline_ref,
                head_ref,
                result.returncode,
                result.stderr.strip(),
            )
        )
    return result.stdout


def cached_diff(
    baseline_ref: str,
    paths: list[Path],
    repo_root: Path,
) -> str:
    """`git diff --cached <baseline_ref> -- <paths>` (staged vs baseline).

    Exit code semantics (R3-1 fix): accept 0/1, raise on 2+.
    """
    from .errors import BaselineResolutionError

    cmd = (
        ["git", "diff", "--cached", baseline_ref, "--"]
        + [str(p) for p in paths]
    )
    result = subprocess.run(
        cmd, cwd=repo_root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    if result.returncode not in (0, 1):
        raise BaselineResolutionError(
            "git diff --cached %s failed (exit %d): %s"
            % (baseline_ref, result.returncode, result.stderr.strip())
        )
    return result.stdout


def working_tree_diff(
    baseline_ref: str,
    paths: list[Path],
    repo_root: Path,
) -> str:
    """Diff baseline..working_tree, including non-binary untracked files.

    Tracked diff: `git diff <baseline_ref> -- <paths>`.
    Untracked: enumerate via `git ls-files --others --exclude-standard`,
    synthesize as full-add via `git diff --no-index /dev/null <file>`.
    H2 fix: binary untracked files are SKIPPED with warnings.warn.

    Exit code handling (R2-1 + R3-1): all git diff calls accept exit 0/1,
    raise BaselineResolutionError on exit 2+. Follows Phase 1 run_git_diff
    convention (Mimo F-03).
    """
    from .errors import BaselineResolutionError

    # Tracked diff (R3-1: must NOT use check=True)
    tracked_cmd = (
        ["git", "diff", baseline_ref, "--"]
        + [str(p) for p in paths]
    )
    tracked_result = subprocess.run(
        tracked_cmd,
        cwd=repo_root,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        check=False,
    )
    if tracked_result.returncode not in (0, 1):
        raise BaselineResolutionError(
            "git diff %s (tracked, working_tree_diff) failed (exit %d): %s"
            % (
                baseline_ref,
                tracked_result.returncode,
                tracked_result.stderr.strip(),
            )
        )
    tracked = tracked_result.stdout

    # Untracked files (ls-files has no exit-1-normal semantics)
    ls_cmd = (
        ["git", "ls-files", "--others", "--exclude-standard", "--"]
        + [str(p) for p in paths]
    )
    untracked_paths = [
        line
        for line in subprocess.run(
            ls_cmd,
            cwd=repo_root,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            check=True,
        ).stdout.splitlines()
        if line.strip()
    ]

    untracked_diffs: list[str] = []
    skipped_binary: list[str] = []
    for rel_path in sorted(untracked_paths):
        full = repo_root / rel_path
        if _is_likely_binary(full):
            skipped_binary.append(rel_path)
            continue
        # R2-1: git diff --no-index exit codes:
        #   0 = files identical (impossible vs /dev/null with content)
        #   1 = files differ (THE expected case)
        #   2+ = real error
        cmd = ["git", "diff", "--no-index", "/dev/null", str(full)]
        result = subprocess.run(
            cmd,
            cwd=repo_root,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            check=False,
        )
        if result.returncode not in (0, 1):
            raise BaselineResolutionError(
                "git diff --no-index failed for untracked file %s "
                "(exit %d): %s"
                % (rel_path, result.returncode, result.stderr.strip())
            )
        untracked_diffs.append(result.stdout)

    if skipped_binary:
        warnings.warn(
            "forge: skipped %d binary untracked file(s) from "
            "working-tree diff: %s%s"
            % (
                len(skipped_binary),
                skipped_binary[:3],
                "..." if len(skipped_binary) > 3 else "",
            ),
            stacklevel=2,
        )

    return tracked + "\n".join(untracked_diffs)


# Hex characters for SHA validation (not isalnum -- rejects G-Z)
_HEX_CHARS = frozenset("0123456789abcdef")


def git_blame(file_path: str, repo_root: Path) -> dict[int, dict]:
    """Parse git blame --porcelain output for file_path.

    Returns {line_number: {"author": str, "sha": str, "subject": str}}.
    line_number is the final file line number (1-indexed).
    Returns {} if git blame fails (file absent, binary, untracked, etc.).

    Advisory axis: return {} instead of raising -- failures must degrade
    gracefully, not crash the review pipeline.
    """
    # Advisory axis: return {} instead of raising (unlike other git.py
    # functions that raise RuntimeError on missing git)
    if shutil.which("git") is None:
        return {}

    try:
        result = subprocess.run(
            ["git", "blame", "--porcelain", "--", file_path],
            cwd=repo_root,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            check=False,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError):
        return {}

    if result.returncode != 0:
        return {}

    if not result.stdout:
        return {}

    blame_map: dict[int, dict] = {}
    # Cache: sha -> {author, subject} -- populated on first occurrence
    sha_cache: dict[str, dict] = {}

    current_sha: str = ""
    current_final_line: int = 0
    # Track whether we saw "author" in the current block (for sha_cache)
    current_block_author: str = ""
    current_block_subject: str = ""
    current_block_has_author: bool = False

    for raw_line in result.stdout.splitlines():
        # 1. Tab prefix check FIRST: content line (blamed source code).
        #    Must be checked before SHA header -- a source line could
        #    contain a 40-hex string that would be falsely identified.
        if raw_line.startswith("\t"):
            entry = sha_cache.get(
                current_sha,
                {"sha": current_sha, "author": "unknown", "subject": ""},
            )
            blame_map[current_final_line] = {
                "sha": current_sha,
                "author": entry.get("author", "unknown"),
                "subject": entry.get("subject", ""),
            }
            continue

        # 2. Guard: skip empty lines
        parts = raw_line.split()
        if not parts:
            continue

        # 3. SHA header: 40 hex chars + orig-line + final-line [+ count]
        #    boundary/previous lines are non-hex, safely skipped
        if (
            len(parts) >= 3
            and len(parts[0]) == 40
            and all(c in _HEX_CHARS for c in parts[0].lower())
        ):
            current_sha = parts[0]
            current_final_line = int(parts[2])
            current_block_author = ""
            current_block_subject = ""
            current_block_has_author = False
            continue

        # Per-commit metadata (only update on FIRST occurrence of SHA)
        if raw_line.startswith("author ") and current_sha not in sha_cache:
            current_block_author = raw_line[7:]
            current_block_has_author = True
        elif (
            raw_line.startswith("summary ")
            and current_sha not in sha_cache
        ):
            current_block_subject = raw_line[8:]
        elif raw_line.startswith("filename "):
            # filename marks end of header block -- finalize sha_cache
            # entry IF author was seen (first occurrence of this SHA)
            if current_block_has_author and current_sha not in sha_cache:
                sha_cache[current_sha] = {
                    "sha": current_sha,
                    "author": current_block_author,
                    "subject": current_block_subject,
                }

    return blame_map
