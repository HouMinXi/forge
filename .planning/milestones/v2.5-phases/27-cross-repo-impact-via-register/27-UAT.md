---
status: complete
phase: 27-cross-repo-impact-via-register
source: [27-01-SUMMARY.md, 27-02-SUMMARY.md]
started: 2026-06-24T00:15:00+08:00
updated: 2026-06-24T00:15:00+08:00
---

## Current Test

[testing complete]

## Tests

### 1. Cross-repo finding surfaces from two registered repos (SC-1)
expected: Given two repos registered in code-review-graph (A exports a symbol, B calls it), changing A's symbol produces a CROSS-REPO-IMPACT advisory finding naming B's call site with format "alias:relpath", the changed symbol in description, and line_range from B's graph.db.
result: pass

### 2. SKIP on absent registry (SC-2)
expected: When CRG_REGISTRY_PATH points at a nonexistent file or is unset, the runner returns empty findings with infra_errors populated. The review completes normally without crashing.
result: pass

### 3. Advisory finding never blocks verdict (SC-3)
expected: Cross-repo advisory findings are AdvisoryFinding type (frozen dataclass, no severity/fingerprint). They cannot participate in StateMachine convergence and never reset the cycle counter. A clean diff with only advisory findings stays PASS.
result: pass

### 4. Runner wired into primary thread only (D-11)
expected: CrossRepoImpactRunner() appears in the primary thread's advisory_runners list alongside TaintRunner, RuntimeRunner, GraphTriageRunner, DaemonStateRunner, LegacyRunner. Sibling threads keep advisory_runners=[].
result: pass

### 5. SKIP states distinguished from genuine no-callers (D-04)
expected: Four SKIP causes (primary db missing, no siblings, sibling db missing, corrupt db) each append to infra_errors and return []. Genuine no-callers (sibling present, no CALLS match) returns [] with EMPTY infra_errors -- the distinguisher.
result: pass

### 6. Subsystem proximity ranking and TOP_N cap (D-06/D-07)
expected: Findings are ranked by token-set Jaccard proximity over directory path segments (not prefix-based). Output capped at _TOP_N (10). Closer subsystems rank higher.
result: pass

## Summary

total: 6
passed: 6
issues: 0
pending: 0
skipped: 0

## Gaps

[none yet]
