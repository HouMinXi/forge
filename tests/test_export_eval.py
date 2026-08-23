# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <[EMAIL_REDACTED]>
"""Tests for the ``ledger export-eval`` extractor (Plan 44-02).

Fixture strategy (D-18 coupling): ledger rows are produced by the REAL
44-01 write paths -- a temp git repo plus a real StateMachine CI run that
emits UNADJUDICATED rows, upgraded to terminal states via the real
``ledger adjudicate`` / ``ledger mark --new`` CLI handlers.  No
hand-written ledger JSONL anywhere in this file.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

import pytest
import yaml

from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.cli import EXIT_CLI_ERROR, EXIT_PASS, _build_parser, _run_ledger
from code_forge.disposition import Disposition
from code_forge.eval.corpus import load_corpus
from code_forge.eval.export import ExportError, export_eval
from code_forge.eval.runner import _create_gate_yaml
from code_forge.falsify import StubFalsifier
from code_forge.ledger import iter_rows
from code_forge.machine import StateMachine
from code_forge.state import Mode, StateFinding


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )
    return res.stdout.strip()


def _make_repo(tmp_path: Path, hostile_gate: bool = False):
    """Create a real git repo with two commits; return (repo, base, head).

    The head commit modifies a.py.  With hostile_gate=True it also adds
    .code-forge/gate.yaml carrying a hostile test.command (D-17 fixture).
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")

    (repo / "a.py").write_text("x = 2\ny = x + 1\n", encoding="utf-8")
    _git(repo, "add", "a.py")
    if hostile_gate:
        gate_dir = repo / ".code-forge"
        gate_dir.mkdir(exist_ok=True)
        (gate_dir / "gate.yaml").write_text(
            "test:\n  command: touch /tmp/foreign_toolchain_ran\n",
            encoding="utf-8",
        )
        _git(repo, "add", ".code-forge/gate.yaml")
    _git(repo, "commit", "-q", "-m", "head")
    head = _git(repo, "rev-parse", "HEAD")
    return repo, base, head


def _shas(repo: Path):
    """Return (base, head) commit SHAs of a two-commit fixture repo."""
    return (
        _git(repo, "rev-list", "--max-parents=0", "HEAD"),
        _git(repo, "rev-parse", "HEAD"),
    )


def _make_finding(fp, file="a.py", line=1):
    return StateFinding(
        id=fp,
        fingerprint=fp,
        source="L0",
        disposition=Disposition.CONFIRMED,
        file=file,
        line_range=[line, line],
        description="synthetic %s" % fp,
        error=None,
    )


def _ci_run(repo: Path, base: str, head: str, findings) -> None:
    """Real 44-01 CI write path: StateMachine CI run appends rows."""
    (repo / ".code-forge").mkdir(parents=True, exist_ok=True)
    resolved = ResolvedReview(
        source_files=[Path("a.py")],
        baseline_content=None,
        git_diff=None,
        mode_hint="git",
        base_sha=base,
        head_sha=head,
    )

    def mock_l0(registry, files):
        return (findings, [])

    machine = StateMachine(
        mode=Mode.CI,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=resolved,
        source_hash="src-hash",
        baseline_spec_repr="empty",
        cwd=repo,
        registry={},
        l0_runner=mock_l0,
    )
    machine.run()


def _ledger_cli(cwd: Path, *argv: str) -> int:
    args = _build_parser().parse_args(["ledger", *argv])
    return _run_ledger(args, cwd)


def _adjudicate(repo: Path, fp: str, state: str, *extra: str) -> int:
    return _ledger_cli(repo, "adjudicate", fp, state, "--evidence", "t", *extra)


def _mark_new(repo: Path, fp: str, state: str, claim: str, *extra: str) -> int:
    return _ledger_cli(
        repo, "mark", fp, state, "--new",
        "--file", "a.py", "--line", "1", "--axis-claim", claim, *extra,
    )


# ---------------------------------------------------------------------------
# Task 1: classification + materialization + counters
# ---------------------------------------------------------------------------


def test_fixed_and_escaped_emit_hold_entries(tmp_path):
    """FIXED and ESCAPED rows export as expect-catch (HOLD) entries."""
    repo, base, head = _make_repo(tmp_path)
    _ci_run(repo, base, head, [_make_finding("fp-fixed", line=1)])
    assert _adjudicate(repo, "fp-fixed", "FIXED") == EXIT_PASS
    assert _mark_new(
        repo, "fp-esc", "ESCAPED", "logic error",
        "--base-sha", base, "--head-sha", head,
    ) == EXIT_PASS

    out = tmp_path / "out"
    summary = export_eval(repo, out)
    assert summary.emitted == 2

    entries = {e.name: e for e in load_corpus(out / "manifest.yaml")}
    assert set(entries) == {"lgr-fp-fixed", "lgr-fp-esc"}
    for e in entries.values():
        assert e.expected_verdict == "HOLD"
        assert e.expected_findings[0].file == "a.py"
        assert e.expected_findings[0].line_range is not None


def test_disproved_emits_pass_entry(tmp_path):
    """DISPROVED rows export as expect-no-catch (PASS) entries."""
    repo, base, head = _make_repo(tmp_path)
    _ci_run(repo, base, head, [_make_finding("fp-dis", line=1)])
    assert _adjudicate(repo, "fp-dis", "DISPROVED") == EXIT_PASS

    out = tmp_path / "out"
    summary = export_eval(repo, out)
    assert summary.emitted == 1
    entries = load_corpus(out / "manifest.yaml")
    assert entries[0].expected_verdict == "PASS"


def test_duplicate_rows_excluded(tmp_path):
    """DUPLICATE rows are excluded under their own counter (deepseek H-1)."""
    repo, base, head = _make_repo(tmp_path)
    assert _mark_new(
        repo, "fp-dup", "DUPLICATE", "logic error",
        "--base-sha", base, "--head-sha", head,
    ) == EXIT_PASS

    out = tmp_path / "out"
    summary = export_eval(repo, out)
    assert summary.duplicate_excluded == 1
    assert summary.emitted == 0
    assert load_corpus(out / "manifest.yaml") == []


def test_unadjudicated_skipped(tmp_path):
    """UNADJUDICATED rows are skipped and counted, never exported."""
    repo, base, head = _make_repo(tmp_path)
    _ci_run(repo, base, head, [_make_finding("fp-pend", line=1)])

    out = tmp_path / "out"
    summary = export_eval(repo, out)
    assert summary.unadjudicated_skipped == 1
    assert summary.emitted == 0
    assert load_corpus(out / "manifest.yaml") == []


def test_stale_sha_skipped_with_warning(tmp_path, capsys):
    """A row with unresolvable base/head is skipped: warn + count + no entry."""
    repo, base, head = _make_repo(tmp_path)
    _ci_run(repo, base, head, [_make_finding("fp-stale", line=1)])
    assert _adjudicate(
        repo, "fp-stale", "FIXED",
        "--base-sha", "f" * 40, "--head-sha", "e" * 40,
    ) == EXIT_PASS

    out = tmp_path / "out"
    summary = export_eval(repo, out)
    assert summary.stale_sha_skipped == 1
    assert summary.emitted == 0
    assert load_corpus(out / "manifest.yaml") == []
    assert "stale SHA skipped" in capsys.readouterr().err


def test_counters_mutually_exclusive_and_sum_to_total(tmp_path):
    """D-15: precedence unadjudicated > stale-sha > duplicate > empty-diff."""
    repo, base, head = _make_repo(tmp_path)

    # Row 1: UNADJUDICATED *and* stale-SHA -> counted once as unadjudicated.
    _ci_run(repo, "a" * 40, "b" * 40, [_make_finding("fp-both", line=1)])

    # Row 2: DUPLICATE *and* stale-SHA -> stale-sha wins over duplicate.
    assert _mark_new(
        repo, "fp-dupstale", "DUPLICATE", "logic error",
        "--base-sha", "f" * 40, "--head-sha", "e" * 40,
    ) == EXIT_PASS

    # Row 3: pure DUPLICATE with valid SHAs -> duplicate_excluded itself.
    assert _mark_new(
        repo, "fp-puredup", "DUPLICATE", "logic error",
        "--base-sha", base, "--head-sha", head,
    ) == EXIT_PASS

    # Row 4: ESCAPED with base == head -> empty-diff.
    assert _mark_new(repo, "fp-empty", "ESCAPED", "logic error") == EXIT_PASS

    # Rows 5+6: CI row adjudicated FIXED -> dedup-collapse 1 + emitted 1.
    _ci_run(repo, base, head, [_make_finding("fp-good", line=1)])
    assert _adjudicate(repo, "fp-good", "FIXED") == EXIT_PASS

    out = tmp_path / "out"
    summary = export_eval(repo, out)
    assert summary.unadjudicated_skipped == 1
    assert summary.stale_sha_skipped == 1
    assert summary.duplicate_excluded == 1
    assert summary.empty_diff_skipped == 1
    assert summary.dedup_collapsed == 1
    assert summary.emitted == 1
    assert summary.total_rows_read == len(list(iter_rows(repo))) == 6


def test_materialized_diff_applies_cleanly(tmp_path):
    """The emitted diff is non-empty and git-apply-clean at base content."""
    repo, base, head = _make_repo(tmp_path)
    _ci_run(repo, base, head, [_make_finding("fp-fix", line=1)])
    assert _adjudicate(repo, "fp-fix", "FIXED") == EXIT_PASS

    out = tmp_path / "out"
    export_eval(repo, out)
    diff_path = out / "diffs" / "lgr-fp-fix.diff"
    assert diff_path.is_file()
    diff_text = diff_path.read_text(encoding="utf-8")
    assert diff_text.strip()

    fresh = tmp_path / "fresh"
    fresh.mkdir()
    (fresh / "a.py").write_text("x = 1\n", encoding="utf-8")
    res = subprocess.run(
        ["git", "apply", str(diff_path)],
        cwd=str(fresh), capture_output=True, text=True, check=False,
    )
    assert res.returncode == 0, res.stderr
    assert (fresh / "a.py").read_text(encoding="utf-8") == "x = 2\ny = x + 1\n"


def test_empty_diff_skipped(tmp_path):
    """gemini B-2: base == head (mark --new defaults) -> empty-diff skip."""
    repo, base, head = _make_repo(tmp_path)
    assert _mark_new(repo, "fp-ed", "ESCAPED", "logic error") == EXIT_PASS

    out = tmp_path / "out"
    summary = export_eval(repo, out)
    assert summary.empty_diff_skipped == 1
    assert summary.emitted == 0
    assert load_corpus(out / "manifest.yaml") == []


# ---------------------------------------------------------------------------
# Task 2: manifest shape + PII guard + axis mapping + D-17 strip
# ---------------------------------------------------------------------------


def test_manifest_shape_and_extras_validate(tmp_path):
    """Emitted manifest loads through the real load_corpus loader."""
    repo, base, head = _make_repo(tmp_path)
    _ci_run(repo, base, head, [_make_finding("fp-v", line=1)])
    assert _adjudicate(repo, "fp-v", "FIXED") == EXIT_PASS

    out = tmp_path / "out"
    export_eval(repo, out)
    entries = load_corpus(out / "manifest.yaml")
    assert len(entries) == 1
    e = entries[0]
    assert e.name == "lgr-fp-v"
    assert e.diff_file == "diffs/lgr-fp-v.diff"
    assert e.expected_verdict == "HOLD"
    assert isinstance(e.axis_tags, list)


def test_pii_guard_no_absolute_paths(tmp_path):
    """D-09: no absolute path leaks; provenance is the repo basename only."""
    repo, base, head = _make_repo(tmp_path)
    _ci_run(repo, base, head, [_make_finding("fp-pii", line=1)])
    assert _adjudicate(repo, "fp-pii", "FIXED") == EXIT_PASS

    out = tmp_path / "out"
    export_eval(repo, out)
    manifest_text = (out / "manifest.yaml").read_text(encoding="utf-8")
    assert str(repo.resolve()) not in manifest_text
    assert str(tmp_path.resolve()) not in manifest_text
    data = yaml.safe_load(manifest_text)
    assert data["provenance"] == repo.resolve().name


def test_axis_mapping_and_fallback(tmp_path, capsys):
    """D-14: known claim maps to axis_tags; unknown -> UNKNOWN + warning."""
    repo, base, head = _make_repo(tmp_path)
    assert _mark_new(
        repo, "fp-ax1", "ESCAPED", "logic error",
        "--base-sha", base, "--head-sha", head,
    ) == EXIT_PASS
    assert _mark_new(
        repo, "fp-ax2", "ESCAPED", "quantum flux capacitor",
        "--base-sha", base, "--head-sha", head,
    ) == EXIT_PASS

    out = tmp_path / "out"
    export_eval(repo, out)
    entries = {e.name: e for e in load_corpus(out / "manifest.yaml")}
    assert entries["lgr-fp-ax1"].axis_tags == ["CORRECTNESS"]
    assert entries["lgr-fp-ax2"].axis_tags == ["UNKNOWN"]
    assert "unmapped axis_claim" in capsys.readouterr().err


def test_gate_yaml_stripped_and_replay_toolchain_free(tmp_path):
    """D-17: a foreign gate.yaml with hostile test.command never survives
    into the corpus diff, so replay's _create_gate_yaml starts clean."""
    marker = Path("/tmp/foreign_toolchain_ran")
    marker.unlink(missing_ok=True)
    repo, base, head = _make_repo(tmp_path, hostile_gate=True)
    _ci_run(repo, base, head, [_make_finding("fp-d17", line=1)])
    assert _adjudicate(repo, "fp-d17", "FIXED") == EXIT_PASS

    out = tmp_path / "out"
    summary = export_eval(repo, out)
    assert summary.emitted == 1
    diff_text = (out / "diffs" / "lgr-fp-d17.diff").read_text(encoding="utf-8")
    assert "gate.yaml" not in diff_text
    assert "a.py" in diff_text  # the rest of the diff survives

    # Replay the materialized diff into a fresh tree at base content.
    fresh = tmp_path / "replay"
    fresh.mkdir()
    (fresh / "a.py").write_text("x = 1\n", encoding="utf-8")
    res = subprocess.run(
        ["git", "apply", str(out / "diffs" / "lgr-fp-d17.diff")],
        cwd=str(fresh), capture_output=True, text=True, check=False,
    )
    assert res.returncode == 0, res.stderr
    assert not (fresh / ".code-forge" / "gate.yaml").exists()

    # The real replay merge path now writes ONLY the harness backend.
    gate_path = _create_gate_yaml(fresh, "harness-eval")
    merged = yaml.safe_load(gate_path.read_text(encoding="utf-8"))
    assert "test" not in merged
    assert list(merged["backends"]) == ["harness-eval"]
    assert not marker.exists()


# ---------------------------------------------------------------------------
# Task 3: CLI subcommand + output hygiene
# ---------------------------------------------------------------------------


def _fixed_ledger_repo(tmp_path: Path):
    repo, base, head = _make_repo(tmp_path)
    _ci_run(repo, base, head, [_make_finding("fp-cli", line=1)])
    assert _adjudicate(repo, "fp-cli", "FIXED") == EXIT_PASS
    return repo


def test_cli_runs_and_prints_summary(tmp_path, capsys):
    repo = _fixed_ledger_repo(tmp_path)
    out = tmp_path / "out"
    rc = _ledger_cli(repo, "export-eval", "--out", str(out))
    assert rc == EXIT_PASS
    captured = capsys.readouterr()
    assert "1 emitted" in captured.out
    assert "0 unadjudicated" in captured.out
    assert (out / "manifest.yaml").is_file()


def test_cli_unadjudicated_hint(tmp_path, capsys):
    repo, base, head = _make_repo(tmp_path)
    _ci_run(repo, base, head, [_make_finding("fp-hint", line=1)])
    rc = _ledger_cli(repo, "export-eval", "--out", str(tmp_path / "out"))
    assert rc == EXIT_PASS
    assert "ledger adjudicate" in capsys.readouterr().err


def test_cli_default_out_dir(tmp_path):
    repo = _fixed_ledger_repo(tmp_path)
    rc = _ledger_cli(repo, "export-eval")
    assert rc == EXIT_PASS
    assert (repo / ".code-forge" / "eval-export" / "manifest.yaml").is_file()


def test_cli_reexport_preserves_foreign_files(tmp_path):
    """D-22: re-export overwrites managed files, leaves foreign files."""
    repo = _fixed_ledger_repo(tmp_path)
    out = tmp_path / "out"
    assert _ledger_cli(repo, "export-eval", "--out", str(out)) == EXIT_PASS
    managed = out / "diffs" / "lgr-fp-cli.diff"
    assert managed.is_file()
    foreign = out / "notes.txt"
    foreign.write_text("keep me\n", encoding="utf-8")

    # Re-rule the fingerprint DUPLICATE with the row's real SHAs (a mark
    # without SHAs would default base==head and misroute to empty-diff):
    # the entry disappears on re-export via the duplicate counter.
    base, head = _shas(repo)
    assert _ledger_cli(
        repo, "mark", "fp-cli", "DUPLICATE",
        "--base-sha", base, "--head-sha", head,
    ) == EXIT_PASS
    assert _ledger_cli(repo, "export-eval", "--out", str(out)) == EXIT_PASS
    assert foreign.read_text(encoding="utf-8") == "keep me\n"
    assert not managed.exists()
    assert load_corpus(out / "manifest.yaml") == []


def test_cli_force_gate(tmp_path):
    repo = _fixed_ledger_repo(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "stray.txt").write_text("not ours\n", encoding="utf-8")
    assert _ledger_cli(repo, "export-eval", "--out", str(out)) == EXIT_CLI_ERROR
    assert _ledger_cli(
        repo, "export-eval", "--out", str(out), "--force"
    ) == EXIT_PASS
    assert (out / "stray.txt").read_text(encoding="utf-8") == "not ours\n"


def test_cli_repo_root_override(tmp_path):
    """D-09: --repo-root remaps row.repo_root for SHA resolution."""
    repo = _fixed_ledger_repo(tmp_path)
    # Move the ledger to a non-git dir so row.repo_root (the repo) is
    # unreachable from cwd; --repo-root supplies it explicitly.
    other = tmp_path / "elsewhere"
    (other / ".code-forge").mkdir(parents=True)
    ledger_src = repo / ".code-forge" / "ledger.jsonl"
    (other / ".code-forge" / "ledger.jsonl").write_text(
        ledger_src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    out = tmp_path / "out"
    rc = _ledger_cli(
        other, "export-eval", "--out", str(out), "--repo-root", str(repo)
    )
    assert rc == EXIT_PASS
    assert len(load_corpus(out / "manifest.yaml")) == 1


def test_cli_worktree_reads_main_repo_ledger(tmp_path):
    """B-3: export-eval invoked from a linked worktree reads the MAIN
    repo's ledger via resolve_ledger_root."""
    repo = _fixed_ledger_repo(tmp_path)
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "--detach", str(wt), "HEAD")
    out = tmp_path / "out"
    rc = _ledger_cli(wt, "export-eval", "--out", str(out))
    assert rc == EXIT_PASS
    assert len(load_corpus(out / "manifest.yaml")) == 1


def test_export_eval_force_gate_raises():
    """ExportError surfaces when the dir is foreign and non-empty."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        out = root / "out"
        out.mkdir()
        (out / "x.txt").write_text("foreign\n", encoding="utf-8")
        with pytest.raises(ExportError):
            export_eval(root, out)


# ---------------------------------------------------------------------------
# Forge R1 hardening: input-trust guards on ledger-carried values
# ---------------------------------------------------------------------------


def _append_raw_row(repo: Path, fp: str, base: str, head: str, claim,
                    repo_root: Optional[Path] = None):
    """Append one FIXED row via the real ledger write path, with field
    values a crafted/foreign ledger could carry (None claim, non-SHA)."""
    from datetime import datetime, timezone

    from code_forge.ledger import LedgerRow, TerminalState, append_row

    append_row(repo, LedgerRow(
        fingerprint=fp,
        repo_root=str((repo_root or repo).resolve()),
        base_sha=base,
        head_sha=head,
        file="a.py",
        line=1,
        axis_claim=claim,
        pass_provenance="manual",
        terminal_state=TerminalState.FIXED,
        evidence_class="t",
        ts=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    ))


def test_none_axis_claim_does_not_crash(tmp_path, capsys):
    """A row carrying axis_claim=None exports as UNKNOWN, no traceback."""
    repo, base, head = _make_repo(tmp_path)
    _append_raw_row(repo, "fp-none", base, head, None)

    out = tmp_path / "out"
    summary = export_eval(repo, out)
    assert summary.emitted == 1
    entries = load_corpus(out / "manifest.yaml")
    assert entries[0].axis_tags == ["UNKNOWN"]
    assert "unmapped axis_claim" in capsys.readouterr().err


def test_crafted_sha_never_reaches_git(tmp_path, monkeypatch):
    """A flag-shaped base_sha is rejected by format validation before any
    git subprocess runs: the row counts as stale and git sees nothing."""
    from code_forge.eval.export import _sha_format_ok

    # Format gate unit assertions: 7..40 hex only (git minimum
    # abbreviation is 7); flags, short prefixes, and non-hex rejected.
    assert _sha_format_ok("a" * 40)
    assert _sha_format_ok("0123abc")
    assert not _sha_format_ok("abc12")
    assert not _sha_format_ok("--upload-pack=/bin/sh")
    assert not _sha_format_ok("")
    assert not _sha_format_ok(None)

    repo, base, head = _make_repo(tmp_path)
    _append_raw_row(repo, "fp-evil", "--upload-pack=/bin/sh", head, "logic error")

    calls = []
    import code_forge.eval.export as export_mod

    real_run = subprocess.run

    def spy(*a, **k):
        calls.append(a[0] if a else k.get("args"))
        return real_run(*a, **k)

    monkeypatch.setattr(export_mod.subprocess, "run", spy)
    out = tmp_path / "out"
    summary = export_eval(repo, out)
    assert summary.stale_sha_skipped == 1
    assert summary.emitted == 0
    for argv in calls:
        assert not any("--upload-pack" in str(part) for part in argv)


def test_tampered_manifest_cannot_steal_foreign_file(tmp_path, capsys):
    """A managed-path entry with '..' is rejected; the file it points at
    outside the output dir survives re-export."""
    repo = _fixed_ledger_repo(tmp_path)
    out = tmp_path / "out"
    assert _ledger_cli(repo, "export-eval", "--out", str(out)) == EXIT_PASS

    victim = tmp_path / "victim.diff"
    victim.write_text("do not delete\n", encoding="utf-8")
    # Rewrite the manifest so the managed set points outside out/.
    (out / "manifest.yaml").write_text(
        yaml.dump({
            "provenance": "x",
            "entries": [{
                "name": "evil",
                "diff_file": "../victim.diff",
                "expected_verdict": "HOLD",
                "axis_tags": [],
            }],
        }),
        encoding="utf-8",
    )
    assert _ledger_cli(repo, "export-eval", "--out", str(out)) == EXIT_PASS
    assert victim.read_text(encoding="utf-8") == "do not delete\n"
    assert "ignoring unsafe managed path" in capsys.readouterr().err


def test_manifest_written_atomically(tmp_path, monkeypatch):
    """The manifest lands via tmp-file + Path.replace, not a direct write:
    a failed export must never orphan managed diffs without a manifest."""
    repo = _fixed_ledger_repo(tmp_path)
    out = tmp_path / "out"
    renames = []
    real_replace = Path.replace

    def spy_replace(self, target):
        renames.append((self.name, Path(target).name))
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", spy_replace)
    assert _ledger_cli(repo, "export-eval", "--out", str(out)) == EXIT_PASS
    assert ("manifest.yaml.tmp", "manifest.yaml") in renames
    assert not (out / "manifest.yaml.tmp").exists()


# ---------------------------------------------------------------------------
# Forge R2 hardening: strip-before-empty ordering and further trust guards
# ---------------------------------------------------------------------------


def test_gate_only_diff_counts_as_empty(tmp_path):
    """A diff whose ONLY change is a foreign gate.yaml strips to nothing:
    no vacuous HOLD entry, counted as empty-diff."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-q", "-m", "base")
    base, = (_git(repo, "rev-parse", "HEAD"),)
    gate_dir = repo / ".code-forge"
    gate_dir.mkdir()
    (gate_dir / "gate.yaml").write_text(
        "test:\n  command: touch /tmp/foreign_toolchain_ran\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".code-forge/gate.yaml")
    _git(repo, "commit", "-q", "-m", "gate only")
    head = _git(repo, "rev-parse", "HEAD")

    _append_raw_row(repo, "fp-gateonly", base, head, "logic error")
    out = tmp_path / "out"
    summary = export_eval(repo, out)
    assert summary.emitted == 0
    assert summary.empty_diff_skipped == 1
    assert not (out / "diffs").exists()


def test_malformed_yaml_manifest_reexport_graceful(tmp_path, capsys):
    """A manifest with invalid YAML degrades to an empty managed list:
    re-export succeeds and foreign files survive."""
    repo = _fixed_ledger_repo(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "manifest.yaml").write_text(
        "entries:\n  - [unclosed\n\tbad indent: {\n", encoding="utf-8",
    )
    foreign = out / "keep.txt"
    foreign.write_text("keep\n", encoding="utf-8")
    assert _ledger_cli(repo, "export-eval", "--out", str(out)) == EXIT_PASS
    assert foreign.read_text(encoding="utf-8") == "keep\n"
    assert load_corpus(out / "manifest.yaml")  # real manifest rewritten


def test_invalid_fingerprint_sanitized_not_crash(tmp_path, capsys):
    """A crafted fingerprint with path separators exports under a
    sanitized name instead of crashing the write."""
    repo, base, head = _make_repo(tmp_path)
    _append_raw_row(repo, "a/b/../x", base, head, "logic error")
    out = tmp_path / "out"
    summary = export_eval(repo, out)
    assert summary.emitted == 1
    entries = load_corpus(out / "manifest.yaml")
    # Unsafe chars are dashed and a short hash of the raw fingerprint is
    # pinned so colliding sanitizations cannot overwrite each other.
    assert entries[0].name.startswith("lgr-a-b----x-")
    assert len(entries[0].name.rsplit("-", 1)[1]) == 8
    assert ".." not in Path(entries[0].diff_file).parts
    target = out / entries[0].diff_file
    assert target.is_file()
    assert target.resolve().is_relative_to(out.resolve())
    assert "sanitized unsafe fingerprint" in capsys.readouterr().err


def test_git_diff_failure_counts_as_stale(tmp_path, monkeypatch, capsys):
    """git diff failing on resolvable SHAs is stale-sha, never empty-diff."""
    repo, base, head = _make_repo(tmp_path)
    _append_raw_row(repo, "fp-diffail", base, head, "logic error")

    import code_forge.eval.export as export_mod

    real_run = subprocess.run

    def fail_diff(*a, **k):
        argv = a[0] if a else k.get("args", [])
        if list(argv[:2]) == ["git", "diff"]:
            return subprocess.CompletedProcess(argv, 128, "", "bad object")
        return real_run(*a, **k)

    monkeypatch.setattr(export_mod.subprocess, "run", fail_diff)
    out = tmp_path / "out"
    summary = export_eval(repo, out)
    assert summary.stale_sha_skipped == 1
    assert summary.empty_diff_skipped == 0
    assert summary.emitted == 0
    assert "git diff failed" in capsys.readouterr().err


def test_cli_repo_root_must_be_git_repo(tmp_path, capsys):
    """--repo-root pointing at a non-git dir is a CLI error, not a
    misleading all-stale export."""
    repo = _fixed_ledger_repo(tmp_path)
    not_git = tmp_path / "plain"
    not_git.mkdir()
    rc = _ledger_cli(
        repo, "export-eval",
        "--out", str(tmp_path / "out"),
        "--repo-root", str(not_git),
    )
    assert rc == EXIT_CLI_ERROR
    assert "not a git repository" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Forge R3 hardening: CRLF diffs, missing paths, defensive process guards
# ---------------------------------------------------------------------------


def test_crlf_diff_still_strips_gate_yaml():
    """core.autocrlf / eol=crlf make git diff emit CRLF; the D-17 strip
    must still remove a foreign gate.yaml section or it silently no-ops."""
    from code_forge.eval.export import _strip_gate_yaml

    crlf_diff = (
        "diff --git a/a.py b/a.py\r\n"
        "--- a/a.py\r\n+++ b/a.py\r\n@@ -1 +1 @@\r\n-x = 1\r\n+x = 2\r\n"
        "diff --git a/.code-forge/gate.yaml b/.code-forge/gate.yaml\r\n"
        "new file mode 100644\r\n"
        "--- /dev/null\r\n+++ b/.code-forge/gate.yaml\r\n"
        "@@ -0,0 +1,2 @@\r\n+test:\r\n+  command: evil\r\n"
        "diff --git a/b.py b/b.py\r\n"
        "--- a/b.py\r\n+++ b/b.py\r\n@@ -1 +1 @@\r\n-y = 1\r\n+y = 2\r\n"
    )
    stripped = _strip_gate_yaml(crlf_diff)
    assert "gate.yaml" not in stripped
    assert "a.py" in stripped
    assert "b.py" in stripped


def test_row_with_missing_repo_root_counts_stale(tmp_path):
    """A ledger row whose repo_root moved away is a stale-skip, not a
    FileNotFoundError traceback."""
    repo, base, head = _make_repo(tmp_path)
    gone = tmp_path / "moved-away"
    _append_raw_row(repo, "fp-gone", base, head, "logic error",
                    repo_root=gone)
    summary = export_eval(repo, tmp_path / "out")
    assert summary.stale_sha_skipped == 1
    assert summary.emitted == 0


def test_out_path_is_regular_file(tmp_path):
    """--out pointing at an existing regular file is an ExportError,
    not a NotADirectoryError traceback."""
    repo = _fixed_ledger_repo(tmp_path)
    out = tmp_path / "afile"
    out.write_text("not a dir\n", encoding="utf-8")
    rc = _ledger_cli(repo, "export-eval", "--out", str(out), "--force")
    assert rc == EXIT_CLI_ERROR


def test_cli_repo_root_nonexistent_path(tmp_path, capsys):
    """--repo-root pointing at a path that does not exist is a CLI
    error, not an unhandled FileNotFoundError from the probe."""
    repo = _fixed_ledger_repo(tmp_path)
    rc = _ledger_cli(
        repo, "export-eval",
        "--out", str(tmp_path / "out"),
        "--repo-root", str(tmp_path / "no-such-dir"),
    )
    assert rc == EXIT_CLI_ERROR
    assert "not a git repository" in capsys.readouterr().err


def test_sha_trailing_newline_rejected():
    """fullmatch (not $) keeps a trailing newline from passing the
    SHA format gate."""
    from code_forge.eval.export import _sha_format_ok

    assert not _sha_format_ok("0123abc\n")
    assert not _sha_format_ok("0123abc\r\n")


def test_sha256_hashes_accepted():
    """SHA-256 object names (64 hex) pass the format gate."""
    from code_forge.eval.export import _sha_format_ok

    assert _sha_format_ok("a" * 64)
    assert not _sha_format_ok("a" * 65)


def test_crash_mid_swap_dir_is_recognized(tmp_path):
    """A dir with manifest.yaml.prev but no manifest.yaml (crash between
    rename and replace) is still our managed dir, not foreign."""
    repo = _fixed_ledger_repo(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "diffs").mkdir()
    (out / "diffs" / "lgr-stale.diff").write_text("stale\n")
    (out / "manifest.yaml.prev").write_text(
        "provenance: repo\nentries: []\n", encoding="utf-8",
    )
    # No --force: must succeed because .prev marks ownership.
    assert _ledger_cli(repo, "export-eval", "--out", str(out)) == EXIT_PASS
    assert not (out / "manifest.yaml.prev").exists()
    assert (out / "manifest.yaml").is_file()


def test_uppercase_fingerprints_cannot_collide(tmp_path):
    """'ABC' vs 'abc' would collide on case-insensitive filesystems;
    names are lowercased and hash-pinned so both survive."""
    repo, base, head = _make_repo(tmp_path)
    _append_raw_row(repo, "ABC", base, head, "logic error")
    _append_raw_row(repo, "abc", base, head, "logic error")
    out = tmp_path / "out"
    summary = export_eval(repo, out)
    assert summary.emitted == 2
    entries = load_corpus(out / "manifest.yaml")
    diff_files = [e.diff_file for e in entries]
    assert len(set(diff_files)) == 2
    for rel in diff_files:
        assert (out / rel).is_file()


def test_unreadable_manifest_reexport_graceful(tmp_path):
    """A manifest that disappears between the is_file check and the open
    degrades to an empty managed list, not an OSError traceback."""
    repo = _fixed_ledger_repo(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    manifest = out / "manifest.yaml"
    manifest.write_text("provenance: repo\nentries: []\n", encoding="utf-8")
    foreign = out / "keep.txt"
    foreign.write_text("keep\n", encoding="utf-8")
    manifest.unlink()
    # is_file() now false inside _managed_diff_files -> empty managed;
    # dir has no manifest so --force is required for the foreign file.
    assert _ledger_cli(repo, "export-eval", "--out", str(out)) == EXIT_CLI_ERROR
    assert foreign.read_text(encoding="utf-8") == "keep\n"


def test_manifest_never_points_at_missing_diffs(tmp_path):
    """Crash-ordering: old managed diffs are removed only AFTER the new
    manifest lands, so a manifest never references a deleted diff."""
    repo = _fixed_ledger_repo(tmp_path)
    out = tmp_path / "out"
    assert _ledger_cli(repo, "export-eval", "--out", str(out)) == EXIT_PASS

    events = []
    real_unlink = Path.unlink
    real_replace = Path.replace

    def spy_unlink(self, *a, **k):
        if self.name.endswith(".diff"):
            events.append(("unlink", self.name))
        return real_unlink(self, *a, **k)

    def spy_replace(self, target, *a, **k):
        if self.name == "manifest.yaml.tmp":
            events.append(("manifest-replace", ""))
        return real_replace(self, target, *a, **k)

    # Re-rule the fingerprint away from FIXED so its managed diff goes
    # stale on the second export.
    row = list(iter_rows(repo))[0]
    base, head = row.base_sha, row.head_sha
    assert _ledger_cli(
        repo, "mark", row.fingerprint, "DUPLICATE",
        "--base-sha", base, "--head-sha", head,
    ) == EXIT_PASS

    monkeypatch_t = pytest.MonkeyPatch()
    monkeypatch_t.setattr(Path, "unlink", spy_unlink)
    monkeypatch_t.setattr(Path, "replace", spy_replace)
    try:
        assert _ledger_cli(repo, "export-eval", "--out", str(out)) == EXIT_PASS
    finally:
        monkeypatch_t.undo()
    diff_unlinks = [i for i, e in enumerate(events) if e[0] == "unlink"]
    manifest_idx = next(
        i for i, e in enumerate(events) if e[0] == "manifest-replace"
    )
    assert diff_unlinks, "second export must clean up the stale diff"
    assert all(i > manifest_idx for i in diff_unlinks)


# ---------------------------------------------------------------------------
# Forge R4 hardening: process timeouts and sanitized-name collisions
# ---------------------------------------------------------------------------


def _timeout_for(argv_match):
    """Return a subprocess.run stand-in that raises TimeoutExpired only
    for the given argv prefix and delegates everything else."""

    real_run = subprocess.run

    def picky(*a, **k):
        argv = list(a[0]) if a else list(k.get("args", []))
        if argv[: len(argv_match)] == argv_match:
            raise subprocess.TimeoutExpired(argv, 1)
        return real_run(*a, **k)

    return picky


def test_cat_file_timeout_counts_stale(tmp_path, monkeypatch):
    """git cat-file hanging on a corrupted repo counts the row stale,
    never propagates TimeoutExpired out of the export."""
    import code_forge.eval.export as export_mod

    repo, base, head = _make_repo(tmp_path)
    _append_raw_row(repo, "fp-hang", base, head, "logic error")
    monkeypatch.setattr(
        export_mod.subprocess, "run",
        _timeout_for(["git", "cat-file"]),
    )
    summary = export_eval(repo, tmp_path / "out")
    assert summary.stale_sha_skipped == 1
    assert summary.emitted == 0


def test_git_diff_timeout_counts_stale(tmp_path, monkeypatch):
    """git diff hanging counts as stale, not as an empty diff."""
    import code_forge.eval.export as export_mod

    repo, base, head = _make_repo(tmp_path)
    _append_raw_row(repo, "fp-diffhang", base, head, "logic error")
    monkeypatch.setattr(
        export_mod.subprocess, "run",
        _timeout_for(["git", "diff"]),
    )
    summary = export_eval(repo, tmp_path / "out")
    assert summary.stale_sha_skipped == 1
    assert summary.empty_diff_skipped == 0
    assert summary.emitted == 0


def test_sanitized_fingerprints_stay_unique(tmp_path):
    """'a/b' and 'a-b' sanitize to the same stem; the pinned raw-hash
    suffix keeps both entries and both diff files distinct."""
    repo, base, head = _make_repo(tmp_path)
    _append_raw_row(repo, "a/b", base, head, "logic error")
    _append_raw_row(repo, "a-b", base, head, "logic error")
    out = tmp_path / "out"
    summary = export_eval(repo, out)
    assert summary.emitted == 2
    entries = load_corpus(out / "manifest.yaml")
    names = [e.name for e in entries]
    assert len(set(names)) == 2
    diff_files = [e.diff_file for e in entries]
    assert len(set(diff_files)) == 2
    for rel in diff_files:
        assert (out / rel).is_file()


def test_cli_repo_root_probe_timeout(tmp_path, monkeypatch, capsys):
    """A hanging --repo-root probe is a CLI error, not a traceback."""
    repo = _fixed_ledger_repo(tmp_path)
    monkeypatch.setattr(
        subprocess, "run",
        _timeout_for(["git", "rev-parse", "--git-dir"]),
    )
    rc = _ledger_cli(
        repo, "export-eval",
        "--out", str(tmp_path / "out"),
        "--repo-root", str(repo),
    )
    assert rc == EXIT_CLI_ERROR
    assert "not a git repository" in capsys.readouterr().err


def test_oversized_fingerprint_fits_filename_limit(tmp_path):
    """A 300-char fingerprint clamps to a name that keeps the diff path
    under the 255-byte ext4/APFS filename limit."""
    repo, base, head = _make_repo(tmp_path)
    _append_raw_row(repo, "f" * 300, base, head, "logic error")
    out = tmp_path / "out"
    summary = export_eval(repo, out)
    assert summary.emitted == 1
    entries = load_corpus(out / "manifest.yaml")
    # clamp bound: 4 ("lgr-") + 100 (stem) + 1 + 8 (hash) = 113 chars
    assert len(entries[0].name) <= 113
    assert len(Path(entries[0].diff_file).name.encode()) <= 255
    assert (out / entries[0].diff_file).is_file()


def test_export_oserror_becomes_cli_error(tmp_path, monkeypatch, capsys):
    """A filesystem failure mid-export (read-only dir, disk full) is a
    clean CLI error, not a raw traceback."""
    repo = _fixed_ledger_repo(tmp_path)
    out = tmp_path / "out"

    real_mkdir = Path.mkdir

    def deny_mkdir(self, *a, **k):
        if str(self).endswith("/out") or str(self).endswith("out/diffs"):
            raise PermissionError(13, "Permission denied", str(self))
        return real_mkdir(self, *a, **k)

    monkeypatch.setattr(Path, "mkdir", deny_mkdir)
    rc = _ledger_cli(repo, "export-eval", "--out", str(out))
    assert rc == EXIT_CLI_ERROR
    assert "export failed" in capsys.readouterr().err
