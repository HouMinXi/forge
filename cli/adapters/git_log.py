#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Git log source adapter -- scans reverts, fixup!, and squash! commits (D1)."""

import re
import subprocess
import sys
from typing import List, Tuple

from cli.adapters.base import (
    BaseAdapter,
    CanonicalFinding,
    MAX_CONTEXT_EXCERPT,
    MAX_RAW_SOURCE,
    TIMEOUT_GIT_FAST,
    TIMEOUT_GIT_SLOW,
)

_BRANCH_RE = re.compile(r'^[\w\-/\.~\^]+$')
_SHA_RE = re.compile(r'^[0-9a-f]{7,40}$')
MAX_MATCHES = 10000


class GitLogAdapter(BaseAdapter):
    """Git log revert/fixup/squash adapter.

    Scans git log for revert, fixup!, and squash! commits within
    a branch range and extracts diff context for each match.
    """

    def _do_fetch(self, source_ref: str) -> List[CanonicalFinding]:
        """Scan git log for reverts, fixups, and squashes.

        Args:
            source_ref: Branch name. Merge base is computed between
                source_ref and HEAD to determine the scan range.

        Returns:
            List of CanonicalFinding objects. Empty list on error.
        """
        # B2: validate branch name
        if not _BRANCH_RE.match(source_ref):
            print(
                f"forge: warning: invalid branch name "
                f"'{source_ref}'",
                file=sys.stderr,
            )
            return []

        merge_base = self._get_merge_base(source_ref)
        range_spec = f'{merge_base}..HEAD'

        findings: List[CanonicalFinding] = []
        seen_shas: set = set()

        for pattern in ['^Revert ', '^fixup! ', '^squash! ']:
            matches = self._search_log(pattern, range_spec)
            for sha, timestamp, subject in matches:
                if sha in seen_shas:
                    continue
                seen_shas.add(sha)

                commit_diff = self._get_diff(sha)

                findings.append(CanonicalFinding(
                    source='git_log',
                    source_tool='git',
                    source_id=sha,
                    timestamp=timestamp,
                    raw_source=subject[:MAX_RAW_SOURCE],
                    context={
                        'diff_hunk': (
                            commit_diff[:MAX_CONTEXT_EXCERPT]
                            if commit_diff else None
                        ),
                        'commit_sha': sha,
                        'branch': source_ref,
                    },
                ))

        return findings

    def _get_merge_base(self, source_ref: str) -> str:
        """Compute merge base between source_ref and HEAD.

        Args:
            source_ref: Branch name.

        Returns:
            Merge base commit SHA, or source_ref on failure.
        """
        try:
            result = subprocess.run(
                ['git', 'merge-base', source_ref, 'HEAD'],
                capture_output=True, text=True,
                timeout=TIMEOUT_GIT_FAST,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print(
                f"forge: warning: git merge-base failed: {exc}",
                file=sys.stderr,
            )
            return source_ref

        if result.returncode != 0:
            print(
                f"forge: warning: git merge-base {source_ref} HEAD "
                f"failed (rc={result.returncode}), "
                f"using '{source_ref}' as range start",
                file=sys.stderr,
            )
            return source_ref
        return result.stdout.strip()

    def _search_log(
        self,
        pattern: str,
        range_spec: str,
    ) -> List[Tuple[str, str, str]]:
        """Search git log for commits matching a grep pattern.

        Args:
            pattern: Git log --grep pattern (e.g., "^Revert ").
            range_spec: Git revision range (e.g., "abc123..HEAD").

        Returns:
            List of (sha, timestamp, subject) tuples. Capped at
            MAX_MATCHES to prevent unbounded iteration.
        """
        try:
            result = subprocess.run(
                ['git', 'log', f'--grep={pattern}',
                 '--format=%H|%aI|%s', range_spec],
                capture_output=True, text=True,
                timeout=TIMEOUT_GIT_SLOW,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print(
                f"forge: warning: git log search failed: {exc}",
                file=sys.stderr,
            )
            return []

        if result.returncode != 0:
            return []

        matches: List[Tuple[str, str, str]] = []
        for line in result.stdout.strip().split('\n'):
            if len(matches) >= MAX_MATCHES:
                print(
                    f"forge: warning: git log search hit "
                    f"{MAX_MATCHES} match limit, truncating",
                    file=sys.stderr,
                )
                break
            line = line.strip()
            if not line:
                continue
            parts = line.split('|', 2)
            if len(parts) == 3:
                matches.append((parts[0], parts[1], parts[2]))
        return matches

    def _get_diff(self, sha: str) -> str:
        """Get the diff for a specific commit.

        Args:
            sha: Commit SHA (validated against hex pattern).

        Returns:
            Diff output, or empty string on error.
        """
        # B2: validate SHA format
        if not _SHA_RE.match(sha):
            print(
                f"forge: warning: invalid commit SHA "
                f"'{sha}'",
                file=sys.stderr,
            )
            return ''

        try:
            result = subprocess.run(
                ['git', 'diff', f'{sha}~1..{sha}'],
                capture_output=True, text=True,
                timeout=TIMEOUT_GIT_SLOW,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print(
                f"forge: warning: git diff failed for "
                f"{sha}: {exc}",
                file=sys.stderr,
            )
            return ''

        if result.returncode != 0:
            return ''
        return result.stdout
