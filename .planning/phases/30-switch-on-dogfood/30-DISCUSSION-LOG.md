# Phase 30: Switch-On + Dogfood - Discussion Log

**Date:** 2026-06-26
**Participants:** User + Claude (main session)

## Areas Discussed

### 1. Hook Target Repos

**Options presented:**
1. Only forge itself (minimal dogfood proof)
2. forge + 1 daily project (cross-repo proof)
3. forge + all daily projects (full rollout)

**User selected:** Other -- "scan ~/code/ for all common projects, update
forge config docs." Intent: full rollout to ALL daily projects, but survey
first to understand each repo's readiness.

**Decision:** D-30-01 -- survey ~/code/, update docs, roll out to all repos.

### 2. Dogfood Verification Strategy

**Q1: Dogfood method**
- Options: one-time manual proof / repeatable regression test / both
- **Selected:** Both -- manual bug-inject first, then regression test.
- Decision: D-30-02.

**Q2: Dogfood location**
- Options: worktree v26-adoption / dedicated dogfood worktree / you decide
- **Selected:** Dedicated dogfood worktree.
- Decision: D-30-03.

### 3. Hook Scope and Experience

**Q1: What the hook runs**
- Options: gate-check only / gate-check + review / gate-check required, review optional
- **Selected:** gate-check + full LLM review (every commit through complete pipeline).
- Decision: D-30-04.

**Q2: Coexistence with planning-leak guard**
- Options: merge into forge hook / keep two independent hooks / you decide
- **Selected:** Merge into forge hook. Additional requirement: hook must detect .git jurisdiction, non-git dirs silently skip.
- Decisions: D-30-05, D-30-06.

## Deferred Ideas

- Auto-detect test runner from repo structure
- Per-repo gate.yaml templates for common project types
- Resolve gate.yaml from git-common-dir (worktree auto-find)

---
*Discussion completed: 2026-06-26*
