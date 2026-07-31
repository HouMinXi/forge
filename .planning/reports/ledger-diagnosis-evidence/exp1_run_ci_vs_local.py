#!/usr/bin/env python3
"""EXPERIMENT 1: does mode=Mode.CI ever write ledger rows, compared to
mode=Mode.LOCAL, holding everything else identical?

Pattern copied from forge's own tests/test_realpath_ledger.py (read-only,
not modified) -- same StateMachine construction, same finding shape, same
real git repo mechanics. The only variable changed between runs is `mode`.

This exercises REAL production code (code_forge.machine.StateMachine,
code_forge.ledger.append_row / iter_rows) with a stub L0 finding and stub
autofixer so no network/model call is made (control-flow question only,
per the dispatch order's instruction not to spend real tokens proving
control flow).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.ledger import iter_rows
from code_forge.machine import StateMachine
from code_forge.state import Mode, StateFinding


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True,
        capture_output=True, text=True,
    )


def _git_init_with_diff(path: Path):
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


def run_once(mode: Mode, workdir: Path) -> dict:
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)

    base, head = _git_init_with_diff(workdir)

    cf = workdir / ".code-forge"
    cf.mkdir(parents=True, exist_ok=True)
    (cf / "gate.yaml").write_text("test:\n  command: 'true'\n")

    finding = StateFinding(
        id="fp-modecompare",
        fingerprint="fp-modecompare",
        source="L0",
        disposition=Disposition.CONFIRMED,
        file="x",
        line_range=[1, 1],
        description="synthetic mode-compare finding",
    )

    def mock_l0(registry, files):
        return ([finding], [])

    from code_forge.git import resolve_git_ref, git_diff
    base_sha = resolve_git_ref(base, workdir)
    head_sha = resolve_git_ref(head, workdir)
    diff = git_diff(base, head, [workdir / "x"], workdir)

    resolved = ResolvedReview(
        source_files=[workdir / "x"],
        baseline_content=None,
        git_diff=diff,
        mode_hint="git",
        base_sha=base_sha,
        head_sha=head_sha,
    )

    machine = StateMachine(
        mode=mode,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=resolved,
        source_hash="modecompare-src",
        baseline_spec_repr="git:" + base,
        cwd=workdir,
        registry={},
        l0_runner=mock_l0,
    )

    verdict = machine.run()

    ledger_path = workdir / ".code-forge" / "ledger.jsonl"
    rows = list(iter_rows(workdir))
    finding_after = None
    for f in machine._state.findings:
        if f.fingerprint == "fp-modecompare":
            finding_after = f
            break

    return {
        "mode": mode.value,
        "verdict": str(verdict),
        "ledger_file_exists": ledger_path.exists(),
        "ledger_row_count": len(rows),
        "ledger_rows": [r.fingerprint + ":" + r.terminal_state.value for r in rows],
        "finding_disposition_after_run": (
            str(finding_after.disposition) if finding_after else "NOT_FOUND"
        ),
        "workdir": str(workdir),
    }


def main():
    scratch_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/ledger_exp1_scratch")

    print("=" * 70)
    print("RUN A: mode=Mode.LOCAL")
    print("=" * 70)
    result_local = run_once(Mode.LOCAL, scratch_root / "local_run")
    for k, v in result_local.items():
        print("  %s: %r" % (k, v))

    print()
    print("=" * 70)
    print("RUN B: mode=Mode.CI (identical finding, identical autofixer/falsifier)")
    print("=" * 70)
    result_ci = run_once(Mode.CI, scratch_root / "ci_run")
    for k, v in result_ci.items():
        print("  %s: %r" % (k, v))

    print()
    print("=" * 70)
    print("COMPARISON")
    print("=" * 70)
    print("  LOCAL ledger_file_exists=%r rows=%d" % (
        result_local["ledger_file_exists"], result_local["ledger_row_count"]))
    print("  CI    ledger_file_exists=%r rows=%d" % (
        result_ci["ledger_file_exists"], result_ci["ledger_row_count"]))

    if result_local["ledger_row_count"] > 0 and result_ci["ledger_row_count"] == 0:
        print("  CONCLUSION: identical finding/disposition writes a ledger "
              "row under LOCAL and writes ZERO rows under CI.")
    else:
        print("  CONCLUSION: pattern did NOT match the expected "
              "LOCAL-writes/CI-silent split -- see raw values above.")


if __name__ == "__main__":
    main()
