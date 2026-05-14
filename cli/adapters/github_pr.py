#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""GitHub PR source adapter -- fetches review and issue comments via gh api (D1)."""

import json
import re
import subprocess
import sys
from typing import List

from cli.adapters.base import (
    BaseAdapter,
    CanonicalFinding,
    MAX_RAW_SOURCE,
    TIMEOUT_NETWORK,
)

_REPO_RE = re.compile(r'^[\w\-\.]+/[\w\-\.]+$')


def _detect_source_tool(comment: dict) -> str:
    """Detect the source tool from a GitHub comment's user field.

    Checks user.type for Bot indicator, then matches login against
    known bot patterns (case-insensitive).

    Args:
        comment: GitHub API comment dict with 'user' field.

    Returns:
        Tool name ("human", "qodo", "coderabbit", "copilot",
        "github-actions", "unknown").
    """
    user = comment.get('user', {})
    user_type = user.get('type', '')
    login = user.get('login', '').lower()

    if user_type == 'Bot':
        if 'qodo' in login:
            return 'qodo'
        if 'coderabbit' in login:
            return 'coderabbit'
        if 'copilot' in login:
            return 'copilot'
        if 'github-actions' in login:
            return 'github-actions'
        return 'unknown'
    return 'human'


class GitHubPRAdapter(BaseAdapter):
    """GitHub PR comment adapter.

    Fetches both review comments (inline on diff) and issue comments
    (general PR discussion) via gh api with pagination.
    """

    def _do_fetch(self, source_ref: str) -> List[CanonicalFinding]:
        """Fetch PR comments via gh api.

        Args:
            source_ref: "owner/repo#N" format (e.g., "octocat/hello-world#1").

        Returns:
            List of CanonicalFinding objects. Empty list on error.
        """
        parts = source_ref.split('#')
        if len(parts) != 2:
            print(
                f"forge: error: invalid PR reference "
                f"'{source_ref}', expected 'owner/repo#N' format",
                file=sys.stderr,
            )
            return []

        repo = parts[0]
        pr_num = parts[1]

        # B1: validate repo format and pr_num
        if not _REPO_RE.match(repo):
            print(
                f"forge: warning: invalid repo format "
                f"'{repo}', expected 'owner/repo'",
                file=sys.stderr,
            )
            return []
        if not pr_num.isdigit():
            print(
                f"forge: warning: invalid PR number "
                f"'{pr_num}', expected integer",
                file=sys.stderr,
            )
            return []

        findings: List[CanonicalFinding] = []

        # Fetch review comments (inline on diff)
        review_comments = self._fetch_endpoint(
            f'repos/{repo}/pulls/{pr_num}/comments',
        )

        # Fetch issue comments (general PR comments)
        issue_comments = self._fetch_endpoint(
            f'repos/{repo}/issues/{pr_num}/comments',
        )

        pr_url = f"https://github.com/{repo}/pull/{pr_num}"

        for comment in review_comments:
            findings.append(self._to_canonical(comment, repo, pr_url))

        for comment in issue_comments:
            findings.append(self._to_canonical(comment, repo, pr_url))

        return findings

    def _fetch_endpoint(self, endpoint: str) -> List[dict]:
        """Fetch paginated results from a gh api endpoint.

        Args:
            endpoint: GitHub API endpoint path.

        Returns:
            List of comment dicts. Empty list on error.
        """
        try:
            result = subprocess.run(
                ['gh', 'api', '--paginate', endpoint, '--jq', '.[]'],
                capture_output=True, text=True, timeout=TIMEOUT_NETWORK,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print(
                f"forge: error: gh api call failed: {exc}",
                file=sys.stderr,
            )
            return []

        if result.returncode != 0:
            print(
                f"forge: error: gh api {endpoint} failed "
                f"(rc={result.returncode}): "
                f"{result.stderr.strip()}",
                file=sys.stderr,
            )
            return []

        comments: List[dict] = []
        if result.stdout.strip():
            for line in result.stdout.strip().split('\n'):
                line = line.strip()
                if not line:
                    continue
                try:
                    comments.append(json.loads(line))
                except json.JSONDecodeError:
                    print(
                        f"forge: warning: skipping malformed JSON "
                        f"line from {endpoint}",
                        file=sys.stderr,
                    )
        return comments

    def _to_canonical(
        self,
        comment: dict,
        repo: str,
        pr_url: str,
    ) -> CanonicalFinding:
        """Convert a GitHub API comment dict to CanonicalFinding.

        Args:
            comment: GitHub API comment dict.
            repo: "owner/repo" string.
            pr_url: Full PR URL for context.

        Returns:
            CanonicalFinding instance.
        """
        raw_body = comment.get('body', '')
        return CanonicalFinding(
            source='github_pr',
            source_tool=_detect_source_tool(comment),
            source_id=str(comment.get('id', '')),
            timestamp=comment.get('created_at', ''),
            raw_source=raw_body[:MAX_RAW_SOURCE],
            context={
                'diff_hunk': comment.get('diff_hunk', None),
                'pr_url': pr_url,
                'path': comment.get('path', None),
            },
        )
