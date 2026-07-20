---
status: complete
phase: 30-switch-on-dogfood
source: 30-01-PLAN.md, 30-02-PLAN.md (no SUMMARY.md -- wrap-up inline by main session)
started: "2026-06-27T01:30:00+08:00"
updated: "2026-06-27T02:45:00+08:00"
---

## Current Test

[testing complete]

## Tests

### 1. Planning-Leak Guard Blocks .planning/ Staging
expected: Running `git add -f .planning/STATE.md && git commit` in a forge repo with install-hooks deployed is rejected with "code-forge: BLOCKED: staged paths must never enter history" and the offending path listed.
result: pass
evidence: Manual dogfood proof 2026-06-27 in dedicated worktree (.worktrees/dogfood). Exit 1, stderr "code-forge: BLOCKED: staged paths must never enter history: .planning/STATE.md". Also verified by automated test_planning_leak_guard_blocks_staging (test_dogfood.py).

### 2. Planning-Leak Guard Blocks CLAUDE.md Staging
expected: Running `git add -f CLAUDE.md && git commit` is rejected with the same BLOCKED message listing CLAUDE.md.
result: pass
evidence: Automated test_planning_leak_guard_blocks_staging covers both .planning/ and CLAUDE.md paths. Manual CLAUDE.md staging verified in same test (exit 1, "CLAUDE.md" in stderr).

### 3. Gate-Check (R1) Blocks New Test Failure
expected: After establishing a test baseline, injecting `assert False` into a test file and committing is blocked by gate-check with "NEW test failures detected" and the failing test name listed.
result: pass
evidence: Main session manual proof 2026-06-27 on merged main code. gate_check.py:979 output "gate-check: NEW test failures detected ... test_dogfood_proof_fail", exit 1. Reverting the failure allowed the commit (exit 0). Also verified by automated test_injected_failure_blocks_commit (test_dogfood.py, 10-step cycle).

### 4. Attestation Gate Blocks Unreviewed Code Commits
expected: Committing code without a prior `code-forge review` receipt is rejected with "code-forge: receipt verification failed".
result: pass
evidence: Manual dogfood proof 2026-06-27. Staged test_sample_dogfood.py (code file), attempted commit without review, exit 1 "receipt verification failed". This is correct forge behavior -- every code commit requires attestation.

### 5. Non-Code Carve-Out Allows Doc Commits
expected: Committing only non-code files (.md, .txt, .yaml) skips attestation and gate-check with "code-forge: skipping verify (non-code commit)" and succeeds.
result: pass
evidence: Manual dogfood proof 2026-06-27. Staged docs/DOGFOOD_NOTE.txt, commit succeeded with "skipping verify (non-code commit)". Main session also verified with PROOF.md staging.

### 6. Forge Auto-Detection Enables Planning-Leak Guard
expected: Running `code-forge install-hooks` in a repo containing `src/code_forge/__init__.py` auto-enables the planning-leak guard in the generated hook (planning_leak_guard=True). Non-forge repos do NOT get the guard.
result: pass
evidence: Automated test_forge_detection_enables_planning_leak_guard and test_no_planning_leak_guard_for_non_forge_repos (test_dogfood.py). Both pass. 70 tests total green.

### 7. LLM Review Block Graceful Degradation
expected: The generated hook includes an LLM review block with `command -v` guard. When code-forge is not on PATH, review is skipped gracefully ("code-forge not found, skipping") without blocking the commit. Exit 2 (no backend) and exit 5 (DELEGATED) also produce warnings but do not block.
result: pass
evidence: Automated test_review_block_exit_2_degrades_gracefully and test_review_block_non_2_exit_blocks (test_install_hooks.py). Generated hook content verified to contain command -v guard, exit 2/5 graceful paths. 66 install_hooks tests pass.

## Summary

total: 7
passed: 7
issues: 0
pending: 0
skipped: 0
blocked: 0

## Gaps

[none]
