# Phase 46 Summary: doctor registry-vs-executed tool audit

**Provenance:** reconstructed 2026-07-11 by the PM session from the merged
commit and dispatch artifacts (worktree .worktrees/p46 already removed).
Sources: main commit a18844a, .planning/dispatch/dispatch_phase46_commit_clearance_20260711.txt,
phase46_exec_brief / phase46_plan_final under /tmp (dispatch copies persisted).

## Outcome

MERGED to main 2026-07-11 as **a18844a** "doctor: audit registry tools
through the real resolver". The worktree commit was f53bf84 on
feat/doctor-tool-audit; the SHA changed on merge (rebase), patch-id
47d8c647 verified identical, branch deleted, f53bf84 left dangling.

4 files, +311/-3:
- src/code_forge/doctor.py: _audit_tools + wiring (guarded chdir with
  OSError handling, ok=None SKIP rows without the word "SKIP" in
  payloads, docstring exit-semantics fix)
- src/code_forge/runner.py: whitespace-command guard in the resolver
- tests/test_doctor_tool_audit.py: 11 tests incl. integration capsys
- tests/test_doctor.py: smoke gains tools.yaml + positive assertion

## Review history (condensed)

Plan: 11 rounds (R1 internal -> R11 gm confirm), converged 0/0/0/0
across ds/lc/gm. Execution: PM L4 acceptance found 3 undisclosed gaps
(F1 missing integration assertion, F2 plan-ref docstrings, F3
whitespace-guard zero coverage) + 1 single-pass finding (P3 stale
doctor docstring); all fixed. Injection matrix I1-I4 bidirectional.
CP3 waived by user to a single pass (recorded). Full suite at commit:
2710 passed / 7 skipped.

## Windows ground truth (post-merge)

Tool-audit verified working on gpu-win 2026-07-11: ruff 0.15.21 PASS
row, ghost entry not_installed FAIL row, doctor exit codes correct
once the wave-1 signal guard (4b060bd) unblocked the self-check.

## Known gaps (from plan, still true)

The pipeline itself never chdirs (run_tool subprocess.run has no cwd=);
the audit chdir mirrors resolver semantics, not pipeline semantics.
Relative tool commands with forward slashes are not recognized as
paths on Windows (os.sep) -- portability ticket material, see
.planning/dispatch/draft_forge_wingpu_mcp_test_20260711.md T6.
