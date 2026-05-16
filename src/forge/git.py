# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Git subprocess wrapper with diff-spec validation.

This module is THE single owner of git diff subprocess calls in the
codebase. All other modules call these functions -- they do not call
git directly. Addresses Consensus #1 (git diff execution unowned).
"""

import re
import shutil
import subprocess

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
        text=True,
        check=False,
    )

    # Exit 128+: fatal git error
    if result.returncode >= 128:
        raise RuntimeError(
            result.stderr or "git diff fatal error"
        )

    # Exit 0 (no diff) or 1 (has diff) -- both normal
    return result.stdout
