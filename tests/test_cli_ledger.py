# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for the ``code-forge ledger {mark,list}`` CLI subcommand."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from code_forge.cli import _build_parser
from code_forge.ledger import LedgerRow, TerminalState, append_row


def _worktree_src():
    return str(Path(__file__).parent.parent / "src")


def _subprocess_env():
    """Ensure subprocess loads this worktree's code_forge, not main's.

    PYTHONPATH entries earlier in the list win; placing the worktree's
    src ahead of any inherited entry keeps the test isolation honest.
    """
    env = os.environ.copy()
    src = _worktree_src()
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _call(tmp_path, *args):
    return subprocess.call(
        [sys.executable, "-m", "code_forge", *args],
        cwd=str(tmp_path),
        env=_subprocess_env(),
    )


def _run(tmp_path, *args):
    return subprocess.run(
        [sys.executable, "-m", "code_forge", *args],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=_subprocess_env(),
    )


def _parse(argv):
    return _build_parser().parse_args(argv)


def test_parser_ledger_mark_args():
    args = _parse([
        "ledger", "mark", "fp-1", "FIXED",
        "--evidence", "manual-ruling",
        "--new",
    ])
    assert args.subcommand == "ledger"
    assert args.ledger_command == "mark"
    assert args.fingerprint == "fp-1"
    assert args.terminal_state == "FIXED"
    assert args.evidence == "manual-ruling"
    assert args.is_new is True


def test_parser_ledger_list_args():
    args = _parse(["ledger", "list", "--json", "--fingerprint", "fp-2"])
    assert args.ledger_command == "list"
    assert args.as_json is True
    assert args.fingerprint == "fp-2"


def test_parser_rejects_unknown_terminal_state():
    """No argparse-level rejection -- validation happens in the handler."""
    args = _parse(["ledger", "mark", "fp-1", "BOGUS", "--new"])
    assert args.terminal_state == "BOGUS"


def test_ledger_no_subcommand_returns_cli_error(tmp_path):
    """Bare `code-forge ledger` (no mark/list) exits non-zero with stderr hint."""
    result = _run(tmp_path, "ledger")
    assert result.returncode != 0
    assert "subcommand required" in result.stderr
    assert "mark" in result.stderr
    assert "list" in result.stderr


def test_ledger_unknown_subcommand_returns_cli_error(tmp_path):
    """`code-forge ledger foo` exits non-zero with stderr hint."""
    result = _run(tmp_path, "ledger", "foo")
    assert result.returncode != 0
    assert "invalid choice" in result.stderr


def test_mark_invalid_terminal_state_returns_cli_error(tmp_path):
    _git_init(tmp_path)
    rc = _call(tmp_path, "ledger", "mark", "fp-x", "BOGUS", "--new")
    assert rc != 0
    # stderr is captured by subprocess.call exit code only; rerun for text
    result = _run(tmp_path, "ledger", "mark", "fp-x", "BOGUS", "--new")
    assert "terminal_state must be one of" in result.stderr


def test_mark_head_sha_without_base_sha_returns_cli_error(tmp_path):
    """--head-sha without --base-sha (or vice versa) is rejected to avoid
    silent Phase 44 empty-diff extraction."""
    _git_init(tmp_path)
    result = _run(
        tmp_path, "ledger", "mark", "fp-x", "ESCAPED", "--new",
        "--head-sha", "b" * 40,
    )
    assert result.returncode != 0
    assert "--base-sha and --head-sha must be provided together" in result.stderr


def test_mark_invalid_sha_format_returns_cli_error(tmp_path):
    _git_init(tmp_path)
    result = _run(
        tmp_path, "ledger", "mark", "fp-x", "ESCAPED", "--new",
        "--base-sha", "not-a-sha", "--head-sha", "b" * 40,
    )
    assert result.returncode != 0
    assert "not a valid 40-hex" in result.stderr


def _git_init(path):
    """Initialize a git repo at path with one commit so SHA resolution works."""
    import subprocess as sp
    sp.run(["git", "init", "--quiet", "--initial-branch=main"],
           cwd=str(path), check=True, capture_output=True)
    sp.run(["git", "config", "user.email", "test@example.com"],
           cwd=str(path), check=True, capture_output=True)
    sp.run(["git", "config", "user.name", "test"], cwd=str(path),
           check=True, capture_output=True)
    (path / "x").write_text("init")
    sp.run(["git", "add", "x"], cwd=str(path), check=True, capture_output=True)
    sp.run(["git", "commit", "--quiet", "-m", "init"], cwd=str(path),
           check=True, capture_output=True)


def test_mark_writes_new_row(tmp_path):
    """`code-forge ledger mark --new` writes a row visible to iter_rows."""
    _git_init(tmp_path)
    rc = _call(tmp_path, "ledger", "mark", "fp-cli-1", "DUPLICATE",
               "--evidence", "manual", "--new")
    assert rc == 0, _run(tmp_path, "ledger", "list").stderr
    rows = list((tmp_path / ".code-forge" / "ledger.jsonl").open())
    assert len(rows) == 1
    data = json.loads(rows[0])
    assert data["fingerprint"] == "fp-cli-1"
    assert data["terminal_state"] == "DUPLICATE"
    assert data["evidence_class"] == "manual"


def test_mark_new_with_fixed_or_disproved_refuses(tmp_path):
    """--new is reserved for DUPLICATE/ESCAPED. FIXED/DISPROVED must
    originate from a real review run, not a manual mark."""
    _git_init(tmp_path)
    for bad_state in ("FIXED", "DISPROVED"):
        result = _run(tmp_path, "ledger", "mark", "fp-x", bad_state,
                      "--new")
        assert result.returncode != 0
        assert "--new is reserved for DUPLICATE / ESCAPED" in result.stderr


def test_mark_refuses_unknown_fingerprint_without_new(tmp_path):
    """`code-forge ledger mark` without --new refuses an unseen fingerprint."""
    _git_init(tmp_path)
    rc = _call(tmp_path, "ledger", "mark", "fp-never-seen", "DISPROVED")
    assert rc != 0  # EXIT_CLI_ERROR
    assert not (tmp_path / ".code-forge" / "ledger.jsonl").exists()


def test_mark_after_existing_row_succeeds(tmp_path):
    """Without --new but with an existing fingerprint, mark succeeds."""
    _git_init(tmp_path)
    append_row(tmp_path, LedgerRow(
        fingerprint="fp-existing",
        repo_root=str(tmp_path),
        base_sha="a" * 40,
        head_sha="b" * 40,
        file="x.py", line=1,
        axis_claim="review", pass_provenance="L1",
        terminal_state=TerminalState.FIXED,
        evidence_class="fix_applied", ts="2026-07-04T00:00:00Z",
    ))
    rc = _call(tmp_path, "ledger", "mark", "fp-existing", "DUPLICATE",
               "--evidence", "human-ruling")
    assert rc == 0
    rows = list((tmp_path / ".code-forge" / "ledger.jsonl").open())
    assert len(rows) == 2
    second = json.loads(rows[1])
    assert second["terminal_state"] == "DUPLICATE"
    assert second["evidence_class"] == "human-ruling"


def test_list_default_tsv(tmp_path):
    append_row(tmp_path, LedgerRow(
        fingerprint="fp-list-1",
        repo_root=str(tmp_path),
        base_sha="a" * 40,
        head_sha="b" * 40,
        file="x.py", line=1,
        axis_claim="review", pass_provenance="L1",
        terminal_state=TerminalState.FIXED,
        evidence_class="fix_applied", ts="2026-07-04T00:00:00Z",
    ))
    result = _run(tmp_path, "ledger", "list")
    assert result.returncode == 0
    assert "fp-list-1" in result.stdout
    assert "FIXED" in result.stdout


def test_list_json(tmp_path):
    append_row(tmp_path, LedgerRow(
        fingerprint="fp-list-2",
        repo_root=str(tmp_path),
        base_sha="a" * 40,
        head_sha="b" * 40,
        file="x.py", line=1,
        axis_claim="review", pass_provenance="L1",
        terminal_state=TerminalState.DISPROVED,
        evidence_class="manual", ts="2026-07-04T00:00:00Z",
    ))
    result = _run(tmp_path, "ledger", "list", "--json")
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert len(payload) == 1
    assert payload[0]["fingerprint"] == "fp-list-2"
    assert payload[0]["terminal_state"] == "DISPROVED"


def test_list_filter_by_fingerprint(tmp_path):
    append_row(tmp_path, LedgerRow(
        fingerprint="fp-want",
        repo_root=str(tmp_path),
        base_sha="a" * 40,
        head_sha="b" * 40,
        file="x.py", line=1,
        axis_claim="review", pass_provenance="L1",
        terminal_state=TerminalState.FIXED,
        evidence_class="fix_applied", ts="2026-07-04T00:00:00Z",
    ))
    append_row(tmp_path, LedgerRow(
        fingerprint="fp-other",
        repo_root=str(tmp_path),
        base_sha="a" * 40,
        head_sha="b" * 40,
        file="y.py", line=2,
        axis_claim="review", pass_provenance="L1",
        terminal_state=TerminalState.DISPROVED,
        evidence_class="manual", ts="2026-07-04T00:00:00Z",
    ))
    result = _run(tmp_path, "ledger", "list", "--json",
                  "--fingerprint", "fp-want")
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert len(payload) == 1
    assert payload[0]["fingerprint"] == "fp-want"