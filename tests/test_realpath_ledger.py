# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""End-to-end REAL-PATH test: a real code-forge review on a real git repo
writes real ledger rows with real SHAs.

The acceptance gate from the dispatch requires >=1 ledger row with real
SHAs produced by a real review run. Mocked-only suites get gate-returned.
This test creates a real git repo, makes a real diff, then exercises
the state-machine convergence path that ends at _write_ledger_rows().
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.ledger import TerminalState, iter_rows
from code_forge.machine import StateMachine
from code_forge.state import Mode, StateFinding, Verdict


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True,
        capture_output=True, text=True,
    )


def _git_init_with_diff(path: Path):
    """Initialize a real git repo with one commit, then introduce a real
    second commit so HEAD~1..HEAD has a non-empty diff with real SHAs.
    """
    _git(path, "init", "--quiet", "--initial-branch=main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "test")
    (path / "x").write_text("init")
    _git(path, "add", "x")
    _git(path, "commit", "--quiet", "-m", "init")
    base_sha = _git(path, "rev-parse", "HEAD").stdout.strip()
    (path / "x").write_text("change")
    _git(path, "add", "x")
    _git(path, "commit", "--quiet", "-m", "second")
    head_sha = _git(path, "rev-parse", "HEAD").stdout.strip()
    return base_sha, head_sha


def test_real_review_run_writes_real_sha_ledger_row(tmp_path):
    """A real code-forge review on a real git repo with a real diff
    produces a ledger row whose SHAs match the git revs."""
    base, head = _git_init_with_diff(tmp_path)

    cf = tmp_path / ".code-forge"
    cf.mkdir(parents=True, exist_ok=True)
    (cf / "gate.yaml").write_text("test:\n  command: 'true'\n")

    # L0 mock returns a real finding; autofix promotes to FIXED; the
    # state machine's _finalize_local_terminal then writes the ledger.
    finding = StateFinding(
        id="fp-realpath",
        fingerprint="fp-realpath",
        source="L0",
        disposition=Disposition.CONFIRMED,
        file="x",
        line_range=[1, 1],
        description="synthetic realpath",
    )

    def mock_l0(registry, files):
        return ([finding], [])

    from code_forge.git import resolve_git_ref, git_diff
    base_sha = resolve_git_ref(base, tmp_path)
    head_sha = resolve_git_ref(head, tmp_path)
    diff = git_diff(base, head, [tmp_path / "x"], tmp_path)

    resolved = ResolvedReview(
        source_files=[tmp_path / "x"],
        baseline_content=None,
        git_diff=diff,
        mode_hint="git",
        base_sha=base_sha,
        head_sha=head_sha,
    )

    machine = StateMachine(
        mode=Mode.LOCAL,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=resolved,
        source_hash="realpath-src",
        baseline_spec_repr="git:" + base,
        cwd=tmp_path,
        registry={},
        l0_runner=mock_l0,
    )

    verdict = machine.run()
    assert verdict == Verdict.PASS

    rows = list(iter_rows(tmp_path))
    by_fp = {r.fingerprint: r for r in rows}
    assert "fp-realpath" in by_fp
    r = by_fp["fp-realpath"]
    # The SHAs come from git rev-parse, not synthetic placeholders.
    assert r.base_sha == base_sha == base
    assert r.head_sha == head_sha == head
    assert len(r.base_sha) == 40
    assert len(r.head_sha) == 40
    assert r.terminal_state == TerminalState.FIXED