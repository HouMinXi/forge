# Forge -- Milestones

## v2.5 Releasable + Cross-Repo (Shipped: 2026-06-26)

**Phases:** 8 (24, 24.1, 25, 25.1, 26, 27, 28, 29) | **Plans:** 29
**Timeline:** 2026-06-15 to 2026-06-26 (11 days)
**Git range:** 93 commits, 91 files changed, +16,063/-573
**Codebase:** 20,925 LOC Python (src/), 2,112 tests

**Key accomplishments:**

- gate.yaml self-documenting with gate.schema.json; any user or LLM can fill fields from inline comments alone (Phase 24)
- A/B/C outlet alignment: same flow contract, tiered fixpoint reset, inline returns DELEGATED not PASS (Phase 24.1)
- Cross-repo merge review: two repos reviewed as one joint unit with both diffs in single context (Phase 25)
- Timeout circuit breaker (exit 6) and L1 truncation false-green guard (Phase 25.1)
- Opt-in contract context: sibling repo spec injected as read-only reference (Phase 26)
- Cross-repo impact advisory: changed symbol surfaces sibling call sites via code-review-graph register (Phase 27)
- Reviewer canary for inline outlet: planted defects detect rubber-stamp reviewers (Phase 28)
- Dead-code false-positive filter: if False/TYPE_CHECKING/version_info/#if 0 callers dropped from advisory findings (Phase 29)

**Key decisions:**

- Inline outlet returns Verdict.DELEGATED (exit 5), never Verdict.PASS
- Advisory axes never block; only FIXVAL and TRUST/SEC may block
- Dead-code filter built forge-side, not gated on upstream code-review-graph #576
- Canary never alters outlet or model selection (D-16)

**Eval results (mimo-pro, 2026-06-26):** Caught 3/3 blocking entries, 1 skipped (infra), 8 over-blocked (false positive)

---

## v2.3 Backend Wiring + Anti-Shirk (Shipped: 2026-06-09)

**Phases completed:** 6 phases, 19 plans, 16 tasks

**Key accomplishments:**

- One-liner:
- One-liner:
- One-liner:
- One-liner:
- One-liner:
- Task 1:
- Task 1 -- `src/code_forge/conventions_resolver.py` (new, 813 lines):
- 1. [Rule 1 - Bug] Integration tests relied on F3 defect for PASS verdict

---

## v2.2 Trusted Review Execution (Shipped: 2026-06-04)

**Phases completed:** 7 phases, 17 plans, 24 tasks

**Key accomplishments:**

- Python toolchain auto-detection with ruff/pylint/flake8 parsers, pyproject.toml-aware detection, and round-trip validated tools.yaml generation
- Pluggable review-backend abstraction with config schema, FORGE_BACKEND resolution, and backend-agnostic reachability probe (cli auth status + api key presence) with TTL caching
- Outlet selection with FORGE_OUTLET > gate.yaml > backend-reachability precedence, fail-closed on unreachable backend, inline-never-probes
- Wire detect, backend, and outlet_resolver modules into CLI with detect and resolve-outlet subcommands plus review auto-detect integration (D-20)
- Created files verified:
- Plan estimated final size under 1000 lines; actual is 1076 lines (7.6% over estimate).
- One-liner:
- One-liner:
- 1. [Rule 1 - Bug] test_falsify_real mocks returned raw dict instead of LLMResult
- Design spec for prompt-level canary injection that validates LLM reviewer attention by planting known defects in the diff and disqualifying on miss
- 1. [Rule 2 - Missing critical functionality] Language inference in idempotency path
- 1. [Rule 1 - Bug] Unused `import time` in test file
- Mechanism mismatch (corrected by main session):
- src/code_forge/outlet_resolver.py:

---

## v2.1 -- Dynamic Gate Backport (2026-05-30)

**Status:** Shipped
**Phases:** 0-4 (config bootstrap, R1 commit gate + R4 docs, R2 mutation, R3 e2e, anti-shirk enforcement)
**Git range:** c0b2fd0..1f105ec
**Timeline:** 2026-05-25 to 2026-05-30 (6 days)
**Codebase:** 7,777 LOC Python (src/), 776 tests passing
**Files changed:** 156 files, +16050 / -1939
**Archive:** .planning/milestones/v2.1-dynamic-gate/MILESTONE-ARCHIVE.md

### What Shipped

- **R1 (commit gate):** real `.git/hooks/pre-commit` via `forge gate-check` + `forge install-hooks`.
  FAIL-OPEN guard (gate-check's own errors -> BLOCK). CI detection. Baseline delta (new failures only).

- **R2 (mutation pipeline):** `source="MUTANT"` StateFinding, `l2_runner` wired after L1,
  consecutive_survivor_rounds 3-round guard. CI async. mutmut soft dependency (MUTATION_SKIPPED if absent).

- **R3 (e2e coverage):** Layer 1 heuristic (diff spanning >=2 dirs + changed signature -> advisory).
  Layer 2 components.yaml co-occurrence (opt-in, P2 blocking). escape hatch via e2e_absent_ok.

- **R4 (gate-philosophy docs):** CLAUDE.md "What Forge Covers" + "What Forge Is Missing" updated;
  honest-assessment paragraph reflects dynamic gate reality.

- **Phase 4 (anti-shirk):** llm_invoke shim, RealFalsifier, consecutive-clean counter (3 rounds),
  per-pass receipt JSON (9 files/run), `code-forge verify` 7-check subcommand, pre-commit attestation.

### Key Decisions at Close

- consecutive_clean_rounds replaces machine.py:423-425 (early return made counter unreachable)
- compute_source_hash (Option A): single hash path for receipt writer, verify, and hook
- check #8 (progressive obligation) added then deleted: 100% false-positive rate for diligent reviewers
- mutmut as soft dependency (D-05): MUTATION_SKIPPED acceptable when not installed

### Deferred to Next Milestone

- R5 test layering (threshold-triggered real-dependency regression)
- verify anti-shirk ceiling: coverage claims can be fabricated for all-clean editor receipts
- LLM non-determinism / l1_provider parallelization

---

## v2.0.0a1 -- State Machine Rewrite (2026-05-18)

**Status:** Shipped
**Phase:** 02 (state-machine-rewrite), 6 sub-plans
**Git range:** 8df203a..d65d2f7
**Timeline:** 2026-05-12 to 2026-05-18 (7 days)
**Codebase:** 4,379 LOC Python (src/), 521 tests passing
**Files changed:** 90 files, +9327 / -213

### What Shipped

- **02-01 Disposition Protocol** -- 5-state disposition model (CONFIRMED / UNCERTAIN /
  DISMISSED / FIXED / PENDING) with stub engine and lifecycle rules

- **02-02 State Machine Core** -- 3-mode machine (LOCAL / CI / HOLD), cycle counter,
  verdict derivation, bounds enforcement

- **02-03 Baseline and Source Model** -- Git-ref and snapshot baselines, source hash
  computation, serialize/deserialize, resolve_baseline pipeline

- **02-04 Mode Execution, Lock, HOLD UX** -- ForgeLock (file lock with pid/timeout),
  resolve_mode, HOLD UI, mode ordering enforcement

- **02-05 CLI Swap, Exit Codes, HOLD Resume** -- Full CLI integration replacing Phase 1
  prototype, 10 exit codes, HOLD resume loop, falsifier/autofixer factories

- **02-06 SARIF 2.1.0 Output** -- CI-mode SARIF log to stdout, one-line summary to
  stderr, disposition-to-level mapping, suppression handling

### Notes

Phase executed without standard GSD planning artifacts (ROADMAP.md / REQUIREMENTS.md /
SUMMARY.md files). Plans archived in .planning/milestones/v2.0-phases/. Tag v2.0.0a1
pushed to origin.
