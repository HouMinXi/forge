# SPDX-License-Identifier: Apache-2.0
import pytest


@pytest.fixture(autouse=True)
def _skip_worktree_check(monkeypatch):
    """All tests bypass the linked-worktree enforcement gate."""
    monkeypatch.setenv("FORGE_SKIP_WORKTREE_CHECK", "1")
