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
from code_forge.ledger import LedgerRow, TerminalState, append_row, iter_rows


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
    assert args.file is None
    assert args.line is None
    assert args.axis_claim is None


def test_parser_ledger_mark_location_args():
    args = _parse([
        "ledger", "mark", "fp-1", "ESCAPED", "--new",
        "--file", "src/thing.py", "--line", "42",
        "--axis-claim", "SQL injection via string-concatenated query",
    ])
    assert args.file == "src/thing.py"
    assert args.line == 42
    assert args.axis_claim == "SQL injection via string-concatenated query"


def test_parser_ledger_list_args():
    args = _parse(["ledger", "list", "--json", "--fingerprint", "fp-2"])
    assert args.ledger_command == "list"
    assert args.as_json is True
    assert args.fingerprint == "fp-2"
    assert args.unadjudicated_only is False


def test_parser_ledger_adjudicate_args():
    args = _parse([
        "ledger", "adjudicate", "fp-adj-1", "FIXED",
        "--evidence", "fix-verified",
        "--base-sha", "a" * 40,
        "--head-sha", "b" * 40,
    ])
    assert args.subcommand == "ledger"
    assert args.ledger_command == "adjudicate"
    assert args.fingerprint == "fp-adj-1"
    assert args.terminal_state == "FIXED"
    assert args.evidence == "fix-verified"
    assert args.base_sha == "a" * 40
    assert args.head_sha == "b" * 40


def test_parser_ledger_list_unadjudicated_args():
    args = _parse(["ledger", "list", "--unadjudicated"])
    assert args.ledger_command == "list"
    assert args.unadjudicated_only is True


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
        "--file", "x.py", "--line", "1", "--axis-claim", "n/a",
        "--head-sha", "b" * 40,
    )
    assert result.returncode != 0
    assert "--base-sha and --head-sha must be provided together" in result.stderr


def test_mark_invalid_sha_format_returns_cli_error(tmp_path):
    _git_init(tmp_path)
    result = _run(
        tmp_path, "ledger", "mark", "fp-x", "ESCAPED", "--new",
        "--file", "x.py", "--line", "1", "--axis-claim", "n/a",
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
               "--evidence", "manual", "--new",
               "--file", "src/dup.py", "--line", "7",
               "--axis-claim", "duplicate of fp-cli-0")
    assert rc == 0, _run(tmp_path, "ledger", "list").stderr
    rows = list((tmp_path / ".code-forge" / "ledger.jsonl").open())
    assert len(rows) == 1
    data = json.loads(rows[0])
    assert data["fingerprint"] == "fp-cli-1"
    assert data["terminal_state"] == "DUPLICATE"
    assert data["evidence_class"] == "manual"
    assert data["file"] == "src/dup.py"
    assert data["line"] == 7
    assert data["axis_claim"] == "duplicate of fp-cli-0"


def test_mark_new_without_location_fails(tmp_path):
    """The pre-fix two-positional `--new` form (no --file/--line/
    --axis-claim) now fails: a --new row has no prior run to inherit a
    location or claim from, so omitting them would write a
    placeholder-only row -- exactly the defect this change closes."""
    _git_init(tmp_path)
    result = _run(tmp_path, "ledger", "mark", "exp1-escaped-0001",
                  "ESCAPED", "--new")
    assert result.returncode != 0
    assert "--file" in result.stderr
    assert "--line" in result.stderr
    assert "--axis-claim" in result.stderr
    assert "required with --new" in result.stderr
    assert not (tmp_path / ".code-forge" / "ledger.jsonl").exists()


def test_mark_new_partial_location_fails(tmp_path):
    """--new with only one of --file/--line/--axis-claim still fails,
    naming just the flag actually missing (not --file/--line, which
    were both provided)."""
    _git_init(tmp_path)
    result = _run(tmp_path, "ledger", "mark", "fp-partial", "ESCAPED",
                  "--new", "--file", "src/a.py", "--line", "3")
    assert result.returncode != 0
    assert "--axis-claim required with --new" in result.stderr
    assert "--file required" not in result.stderr
    assert "--line required" not in result.stderr


def test_mark_file_without_line_rejected(tmp_path):
    """--file and --line travel together even outside the --new path."""
    _git_init(tmp_path)
    append_row(tmp_path, LedgerRow(
        fingerprint="fp-existing-2",
        repo_root=str(tmp_path),
        base_sha="a" * 40, head_sha="b" * 40,
        file="x.py", line=1, axis_claim="review", pass_provenance="L1",
        terminal_state=TerminalState.FIXED,
        evidence_class="fix_applied", ts="2026-07-04T00:00:00Z",
    ))
    result = _run(tmp_path, "ledger", "mark", "fp-existing-2", "DUPLICATE",
                  "--file", "src/b.py")
    assert result.returncode != 0
    assert "--file and --line must be provided together" in result.stderr


def test_mark_line_zero_rejected(tmp_path):
    _git_init(tmp_path)
    result = _run(tmp_path, "ledger", "mark", "fp-zero", "ESCAPED", "--new",
                  "--file", "src/z.py", "--line", "0",
                  "--axis-claim", "off by one")
    assert result.returncode != 0
    assert "positive integer" in result.stderr


def test_mark_line_negative_rejected(tmp_path):
    _git_init(tmp_path)
    result = _run(tmp_path, "ledger", "mark", "fp-neg", "ESCAPED", "--new",
                  "--file", "src/z.py", "--line", "-1",
                  "--axis-claim", "off by one")
    assert result.returncode != 0
    assert "positive integer" in result.stderr


def test_mark_axis_claim_empty_rejected(tmp_path):
    _git_init(tmp_path)
    result = _run(tmp_path, "ledger", "mark", "fp-empty-claim", "ESCAPED",
                  "--new", "--file", "src/z.py", "--line", "1",
                  "--axis-claim", "   ")
    assert result.returncode != 0
    assert "--axis-claim must not be empty" in result.stderr


def test_mark_file_absolute_rejected(tmp_path):
    _git_init(tmp_path)
    result = _run(tmp_path, "ledger", "mark", "fp-abs", "ESCAPED", "--new",
                  "--file", "/etc/passwd", "--line", "1",
                  "--axis-claim", "should not matter")
    assert result.returncode != 0
    assert "must be relative" in result.stderr


def test_mark_file_escapes_repo_rejected(tmp_path):
    _git_init(tmp_path)
    result = _run(tmp_path, "ledger", "mark", "fp-escape", "ESCAPED",
                  "--new", "--file", "../../../etc/passwd", "--line", "1",
                  "--axis-claim", "should not matter")
    assert result.returncode != 0
    assert "escapes repo root" in result.stderr


def test_mark_new_writes_real_file_and_line(tmp_path):
    """The end-to-end shape of the defect this change closes: a real
    ESCAPED row carries a real file and line, not `:0`."""
    _git_init(tmp_path)
    rc = _call(tmp_path, "ledger", "mark", "exp-escaped-1", "ESCAPED",
               "--new", "--file", "src/code_forge/auth.py", "--line", "88",
               "--axis-claim",
               "auth bypass when the token header is present but empty")
    assert rc == 0
    result = _run(tmp_path, "ledger", "list")
    assert "src/code_forge/auth.py:88" in result.stdout
    result_json = _run(tmp_path, "ledger", "list", "--json")
    payload = json.loads(result_json.stdout)
    assert payload[0]["file"] == "src/code_forge/auth.py"
    assert payload[0]["line"] == 88
    assert payload[0]["axis_claim"] == (
        "auth bypass when the token header is present but empty"
    )


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


# ---------------------------------------------------------------------------
# Adjudicate and --unadjudicated tests (Plan 44-01 Task 3)
# ---------------------------------------------------------------------------


def test_adjudicate_inherits_source_metadata(tmp_path):
    """Test (a): Adjudicating an UNADJUDICATED row appends a terminal row inheriting metadata."""
    _git_init(tmp_path)
    base = "1" * 40
    head = "2" * 40
    append_row(tmp_path, LedgerRow(
        fingerprint="fp-adj-a",
        repo_root=str(tmp_path.resolve()),
        base_sha=base,
        head_sha=head,
        file="src/target.py",
        line=42,
        axis_claim="unvalidated input handling",
        pass_provenance="CI",
        terminal_state=TerminalState.UNADJUDICATED,
        evidence_class="test failure",
        ts="2026-08-20T00:00:00Z",
        version_sensitive=True,
    ))

    rc = _call(tmp_path, "ledger", "adjudicate", "fp-adj-a", "FIXED", "--evidence", "verified-by-human")
    assert rc == 0

    rows = list(iter_rows(tmp_path))
    assert len(rows) == 2
    latest = rows[-1]
    assert latest.fingerprint == "fp-adj-a"
    assert latest.terminal_state == TerminalState.FIXED
    assert latest.file == "src/target.py"
    assert latest.line == 42
    assert latest.axis_claim == "unvalidated input handling"
    assert latest.base_sha == base
    assert latest.head_sha == head
    assert latest.repo_root == str(tmp_path.resolve())
    assert latest.pass_provenance == "adjudicated"
    assert latest.evidence_class == "verified-by-human"
    assert latest.version_sensitive is True


def test_adjudicate_is_append_only(tmp_path):
    """Test (b): The source UNADJUDICATED row is still present after adjudication."""
    _git_init(tmp_path)
    append_row(tmp_path, LedgerRow(
        fingerprint="fp-adj-b",
        repo_root=str(tmp_path.resolve()),
        base_sha="a" * 40,
        head_sha="b" * 40,
        file="foo.py",
        line=10,
        axis_claim="claim",
        pass_provenance="CI",
        terminal_state=TerminalState.UNADJUDICATED,
        evidence_class="evidence",
        ts="2026-08-20T00:00:00Z",
    ))

    assert _call(tmp_path, "ledger", "adjudicate", "fp-adj-b", "DISPROVED") == 0

    rows = list(iter_rows(tmp_path))
    assert len(rows) == 2
    assert rows[0].terminal_state == TerminalState.UNADJUDICATED
    assert rows[1].terminal_state == TerminalState.DISPROVED


def test_adjudicate_error_cases(tmp_path):
    """Test (c): Errors on absent fingerprint, already terminal, invalid state, UNADJUDICATED."""
    _git_init(tmp_path)

    # 1. Absent fingerprint -> EXIT_CLI_ERROR
    r1 = _run(tmp_path, "ledger", "adjudicate", "fp-absent", "FIXED")
    assert r1.returncode != 0
    assert "not in ledger" in r1.stderr

    # 2. Already terminal -> EXIT_CLI_ERROR
    append_row(tmp_path, LedgerRow(
        fingerprint="fp-terminal",
        repo_root=str(tmp_path.resolve()),
        base_sha="a" * 40,
        head_sha="b" * 40,
        file="f.py",
        line=1,
        axis_claim="c",
        pass_provenance="CI",
        terminal_state=TerminalState.FIXED,
        evidence_class="e",
        ts="2026-08-20T00:00:00Z",
    ))
    r2 = _run(tmp_path, "ledger", "adjudicate", "fp-terminal", "DISPROVED")
    assert r2.returncode != 0
    assert "already terminal" in r2.stderr

    # 3. Invalid terminal state string -> EXIT_CLI_ERROR
    append_row(tmp_path, LedgerRow(
        fingerprint="fp-unadj",
        repo_root=str(tmp_path.resolve()),
        base_sha="a" * 40,
        head_sha="b" * 40,
        file="f.py",
        line=1,
        axis_claim="c",
        pass_provenance="CI",
        terminal_state=TerminalState.UNADJUDICATED,
        evidence_class="e",
        ts="2026-08-20T00:00:00Z",
    ))
    r3 = _run(tmp_path, "ledger", "adjudicate", "fp-unadj", "INVALID_STATE")
    assert r3.returncode != 0
    assert "terminal_state must be one of" in r3.stderr

    # 4. Adjudicating to UNADJUDICATED -> EXIT_CLI_ERROR
    r4 = _run(tmp_path, "ledger", "adjudicate", "fp-unadj", "UNADJUDICATED")
    assert r4.returncode != 0
    assert "terminal_state must be one of" in r4.stderr

    # 5. Asymmetric base/head SHA -> EXIT_CLI_ERROR
    r5 = _run(tmp_path, "ledger", "adjudicate", "fp-unadj", "FIXED", "--base-sha", "a" * 40)
    assert r5.returncode != 0
    assert "--base-sha and --head-sha must be provided together" in r5.stderr


def test_adjudicate_echo_to_stderr(tmp_path):
    """Test (d): After writing, inherited file:line and base/head print to stderr (D-20a)."""
    _git_init(tmp_path)
    base = "3" * 40
    head = "4" * 40
    append_row(tmp_path, LedgerRow(
        fingerprint="fp-adj-echo",
        repo_root=str(tmp_path.resolve()),
        base_sha=base,
        head_sha=head,
        file="pkg/logic.py",
        line=89,
        axis_claim="memory leak in cache",
        pass_provenance="CI",
        terminal_state=TerminalState.UNADJUDICATED,
        evidence_class="leak detector",
        ts="2026-08-20T00:00:00Z",
    ))

    result = _run(tmp_path, "ledger", "adjudicate", "fp-adj-echo", "FIXED")
    assert result.returncode == 0
    assert "pkg/logic.py" in result.stderr
    assert "89" in result.stderr
    assert base in result.stderr
    assert head in result.stderr
    assert "adjudicated fp-adj-echo as FIXED" in result.stderr


def test_list_unadjudicated_filter_tsv(tmp_path):
    """Test (e): list --unadjudicated shows only fingerprints whose latest state is UNADJUDICATED."""
    _git_init(tmp_path)
    # fp-1: UNADJUDICATED -> should appear
    append_row(tmp_path, LedgerRow(
        fingerprint="fp-unadj-1",
        repo_root=str(tmp_path.resolve()),
        base_sha="a" * 40,
        head_sha="b" * 40,
        file="a.py",
        line=1,
        axis_claim="claim 1",
        pass_provenance="CI",
        terminal_state=TerminalState.UNADJUDICATED,
        evidence_class="e1",
        ts="2026-08-20T01:00:00Z",
    ))
    # fp-2: UNADJUDICATED then adjudicated to FIXED -> should NOT appear
    append_row(tmp_path, LedgerRow(
        fingerprint="fp-unadj-2",
        repo_root=str(tmp_path.resolve()),
        base_sha="a" * 40,
        head_sha="b" * 40,
        file="b.py",
        line=2,
        axis_claim="claim 2",
        pass_provenance="CI",
        terminal_state=TerminalState.UNADJUDICATED,
        evidence_class="e2",
        ts="2026-08-20T01:00:00Z",
    ))
    append_row(tmp_path, LedgerRow(
        fingerprint="fp-unadj-2",
        repo_root=str(tmp_path.resolve()),
        base_sha="a" * 40,
        head_sha="b" * 40,
        file="b.py",
        line=2,
        axis_claim="claim 2",
        pass_provenance="adjudicated",
        terminal_state=TerminalState.FIXED,
        evidence_class="manual",
        ts="2026-08-20T02:00:00Z",
    ))
    # fp-3: directly terminal -> should NOT appear
    append_row(tmp_path, LedgerRow(
        fingerprint="fp-term-3",
        repo_root=str(tmp_path.resolve()),
        base_sha="a" * 40,
        head_sha="b" * 40,
        file="c.py",
        line=3,
        axis_claim="claim 3",
        pass_provenance="L1",
        terminal_state=TerminalState.DISPROVED,
        evidence_class="e3",
        ts="2026-08-20T01:00:00Z",
    ))

    result = _run(tmp_path, "ledger", "list", "--unadjudicated")
    assert result.returncode == 0
    assert "fp-unadj-1" in result.stdout
    assert "fp-unadj-2" not in result.stdout
    assert "fp-term-3" not in result.stdout


def test_list_unadjudicated_filter_json(tmp_path):
    """Test (f): list --unadjudicated --json filters properly."""
    _git_init(tmp_path)
    append_row(tmp_path, LedgerRow(
        fingerprint="fp-json-unadj",
        repo_root=str(tmp_path.resolve()),
        base_sha="a" * 40,
        head_sha="b" * 40,
        file="a.py",
        line=1,
        axis_claim="claim 1",
        pass_provenance="CI",
        terminal_state=TerminalState.UNADJUDICATED,
        evidence_class="e1",
        ts="2026-08-20T01:00:00Z",
    ))
    append_row(tmp_path, LedgerRow(
        fingerprint="fp-json-fixed",
        repo_root=str(tmp_path.resolve()),
        base_sha="a" * 40,
        head_sha="b" * 40,
        file="b.py",
        line=2,
        axis_claim="claim 2",
        pass_provenance="L1",
        terminal_state=TerminalState.FIXED,
        evidence_class="e2",
        ts="2026-08-20T01:00:00Z",
    ))

    result = _run(tmp_path, "ledger", "list", "--unadjudicated", "--json")
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert len(payload) == 1
    assert payload[0]["fingerprint"] == "fp-json-unadj"
    assert payload[0]["terminal_state"] == "UNADJUDICATED"


def test_adjudicate_and_list_from_linked_worktree(tmp_path):
    """Test (g): adjudicate and list from linked worktree operate on main repo ledger (D-05, D-20b)."""
    main_repo = tmp_path / "main_repo"
    worktree = tmp_path / "worktree_branch"
    main_repo.mkdir()
    _git_init(main_repo)

    import subprocess as sp
    sp.run(["git", "worktree", "add", "-b", "wt-branch", str(worktree)],
           cwd=str(main_repo), check=True, capture_output=True)

    # Seed UNADJUDICATED row in main repo ledger
    append_row(main_repo, LedgerRow(
        fingerprint="fp-wt-read",
        repo_root=str(main_repo.resolve()),
        base_sha="a" * 40,
        head_sha="b" * 40,
        file="wt_file.py",
        line=15,
        axis_claim="boundary overflow",
        pass_provenance="CI",
        terminal_state=TerminalState.UNADJUDICATED,
        evidence_class="fuzzer report",
        ts="2026-08-20T00:00:00Z",
    ))

    # 1. list --unadjudicated from worktree finds the main repo row
    res_list = _run(worktree, "ledger", "list", "--unadjudicated")
    assert res_list.returncode == 0
    assert "fp-wt-read" in res_list.stdout

    # 2. adjudicate from worktree modifies the main repo ledger
    res_adj = _run(worktree, "ledger", "adjudicate", "fp-wt-read", "FIXED")
    assert res_adj.returncode == 0

    # Worktree local ledger was NOT created
    assert not (worktree / ".code-forge" / "ledger.jsonl").exists()

    # Main repo ledger has the new terminal row
    main_rows = list(iter_rows(main_repo))
    assert len(main_rows) == 2
    assert main_rows[-1].fingerprint == "fp-wt-read"
    assert main_rows[-1].terminal_state == TerminalState.FIXED
    assert main_rows[-1].repo_root == str(main_repo.resolve())


def test_mark_evidence_is_truncated(tmp_path):
    """Test (h): ledger mark --evidence >500 chars is truncated (D-07, D-21)."""
    _git_init(tmp_path)
    long_evidence = "x" * 600
    rc = _call(tmp_path, "ledger", "mark", "fp-trunc-mark", "DUPLICATE",
               "--new", "--file", "a.py", "--line", "1", "--axis-claim", "claim",
               "--evidence", long_evidence)
    assert rc == 0

    rows = list(iter_rows(tmp_path))
    assert len(rows) == 1
    assert len(rows[0].evidence_class) <= 500
    assert rows[0].evidence_class.endswith("... [truncated]")


def test_adjudicate_evidence_is_truncated(tmp_path):
    """Test adjudicate with --evidence >500 chars is truncated via append_row (D-07, D-21)."""
    _git_init(tmp_path)
    append_row(tmp_path, LedgerRow(
        fingerprint="fp-trunc-adj",
        repo_root=str(tmp_path.resolve()),
        base_sha="1" * 40,
        head_sha="2" * 40,
        file="a.py",
        line=1,
        axis_claim="claim",
        pass_provenance="CI",
        terminal_state=TerminalState.UNADJUDICATED,
        evidence_class="init",
        ts="2026-07-04T00:00:00Z",
    ))
    long_evidence = "y" * 600
    rc = _call(tmp_path, "ledger", "adjudicate", "fp-trunc-adj", "FIXED",
               "--evidence", long_evidence)
    assert rc == 0

    rows = list(iter_rows(tmp_path))
    assert len(rows) == 2
    assert len(rows[1].evidence_class) <= 500
    assert rows[1].evidence_class.endswith("... [truncated]")