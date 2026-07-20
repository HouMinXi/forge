# Milestone v2.1: Dynamic Gate Backport

**Status:** SHIPPED 2026-05-30
**Phases:** 0-4
**Total Plans:** 15 (00-01, 01-01..04, 02-01..04, 03-01..05, 04-01)
**Git range:** c0b2fd0 (forge/gate: add test command config) .. 1f105ec (docs: document verify ceiling)
**Code:** 156 files changed, +16050 / -1939 LOC
**Codebase at close:** 7,777 LOC Python (src/)
**Tests at close:** 776 (all passing)
**Timeline:** 2026-05-25 to 2026-05-30 (6 days)
**Commits:** 82

## Overview

v2.1 closes forge's own blind spot: a marker-trusting commit gate that ran no tests,
plus mock-only coverage that missed integration bugs. Delivered four dynamic gates:
R1 (real pre-commit test gate), R2 (mutation as pipeline step), R3 (e2e coverage
heuristic + components.yaml), and R4 (gate-philosophy docs). Added anti-shirk
enforcement (Phase 4) as a self-contained milestone extension: LLM-driven review
passes, per-pass receipt protocol, and mechanical verification via `code-forge verify`.

## Phases

### Phase 0: Config Bootstrap (gate.yaml + baseline)

**Goal:** Bootstrap forge's own `.forge/gate.yaml` and test baseline to unblock Phase 1.
**Plans:** 1 (00-01)

- [x] 00-01: gate.yaml + .gitignore exception + test_baseline.json bootstrap

**Key deliverable:** `.forge/gate.yaml` with `test.env.PYTHONPATH=src` (mandatory: bare pytest
hits 44 import errors from src/ layout). `test_baseline.json` (521 tests, 0 failures) gitignored.

---

### Phase 1: R1 Commit Gate + R4 Docs

**Goal:** Real `.git/hooks/pre-commit` gate via `forge gate-check` + `install-hooks` + docs.
**Plans:** 4 (01-01..04)

- [x] 01-01: CLI subparser restructure + gate.yaml source_patterns
- [x] 01-02: gate-check core (exit-code translation, FAIL-OPEN guard, CI detection, baseline delta)
- [x] 01-03: install-hooks (worktree-safe hooks dir, absolute forge path, backup+chain)
- [x] 01-04: R4 docs (LIVE vs PLANNED) + bug-inject tests + host verification

**Key deliverables:** `forge gate-check` parses `gate.yaml`, translates exit codes (test 1/4/5 ->
BLOCK; 2-3 -> warn). `forge install-hooks` resolves hooks dir via `git rev-parse --git-path hooks`,
embeds absolute forge path. FAIL-OPEN guard: gate-check's OWN errors -> BLOCK, never warn path.
CI detection: FORGE_MODE=ci + platform vars (CI/GITHUB_ACTIONS/GITLAB_CI/JENKINS_URL/BUILD_URL).

---

### Phase 2: R2 Mutation Pipeline Step

**Goal:** Mutation as a state-machine step (not a commit gate); LOCAL sync / CI async.
**Plans:** 4 (02-01..04)

- [x] 02-01: state.py source field + mutation.py module + build_l2_runner factory
- [x] 02-02: machine.py l2_runner wiring + MUTANT autofix filter + consecutive_survivor_rounds + CI async + resolve_forge_path liveness
- [x] 02-03: full test suite (mutation unit + machine L2 integration + bug-inject teeth + liveness + factory + state)
- [x] 02-04: mutation dogfood checkpoint (mutmut not installed -> MUTATION_SKIPPED per D-05)

**Key deliverables:** `source="MUTANT"` StateFinding, `l2_runner` wired after L1 phase.
`consecutive_survivor_rounds` counter (3-round guard). CI async wrapper thread +
`mutation-result.json`. `resolve_forge_path` liveness check (--version + 1s timeout + fallback).
34 new tests including bug-inject teeth (toothless test -> MUTANT survives -> FAIL; clean -> PASS).

---

### Phase 3: R3 e2e Coverage

**Goal:** Integration/e2e heuristic + opt-in `.forge/components.yaml`.
**Plans:** 5 (03-01..05)

- [x] 03-01: e2e_runner base + no-op default + StateMachine wiring
- [x] 03-02: Layer 1 heuristic (diff spanning >= 2 dirs with changed signature -> advisory finding)
- [x] 03-03: Layer 2 components.yaml (co-occurrence trigger -> P2 finding when no e2e test)
- [x] 03-04: comprehensive test coverage (48 unit tests Groups A-G + 9 machine integration)
- [x] 03-05: Phase 3 closer: dogfood + host verification 4 cases + R4 docs (PLANNED -> LIVE)

**Key deliverables:** Two-layer e2e check. Layer 1: diff heuristic (advisory, no config needed).
Layer 2: components.yaml co-occurrence (opt-in, P2 blocking). Dogfood confirmed escape hatch
when forge's own src/ and tests/ paths don't overlap (e2e_absent_ok pattern).

---

### Phase 4: Anti-Shirk Enforcement

**Goal:** Connect l1_provider + Falsifier; mechanical cycle counting; receipt protocol + verify.
**Plans:** 1 (04-01)

- [x] 04-01: llm_invoke shim + RealFalsifier + consecutive-clean counter + l1_provider wiring + receipt writer + verify subcommand + attestation + SKILL.md protocol

**Key deliverables:**
- `llm_invoke.py`: `claude -p` subprocess shim with ARG_MAX guard and JSON parsing
- `falsify_real.py`: RealFalsifier with 10-step anti-hallucination protocol
- `consecutive_clean_rounds` counter replacing single-fixpoint at machine.py:423-425
- `receipt.py`: per-pass receipt JSON (9 files per 3-cycle run)
- `verify.py`: 7-check validation CLI + `code-forge verify` subcommand
- Pre-commit attestation: hook includes `code-forge verify --quiet`
- SKILL.md: Receipt Protocol section + 7 verification checks documented

Post-merge: check #8 (progressive obligation) added and then deleted -- requiring Jaccard
distance >= 0.2 between all-clean cycles blocked diligent reviewers who cover the full diff
every pass (distance always 0.0); check removed as 100% false-positive. Verify reduced to 7 checks.

---

## Key Accomplishments

1. **Real pre-commit test gate (R1):** forge's own commits now gated by `forge gate-check` -- closes the "marker-trusting blind spot" that motivated v2.1.
2. **Mutation as pipeline step (R2):** `source="MUTANT"` findings surface test gaps without blocking commits; CI async path preserves build speed.
3. **e2e coverage heuristic (R3):** Layer 1 (heuristic) + Layer 2 (components.yaml) catch cross-component changes without e2e tests; escape hatch for components that legitimately have no integration coverage.
4. **Anti-shirk receipt protocol (Phase 4):** `code-forge verify` validates 9 receipt files (3 cycles x 3 passes) across 7 mechanical checks; pre-commit attestation closes the path from review to commit.
5. **Consecutive-clean convergence:** Machine now requires 3 clean rounds (not 1 fixpoint), closing the single-pass rubber-stamp path.
6. **Gate-philosophy docs (R4):** CLAUDE.md "What Forge Covers" and "What Forge Is Missing" sections reflect dynamic gate reality; honest-assessment paragraph updated after Phase 4.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| PYTHONPATH=src in gate.yaml | bare pytest hits 44 import errors from src/ layout | Correct; gate runs cleanly |
| FAIL-OPEN guard | gate-check's own errors must BLOCK, not silently allow | No known bypass incidents |
| consecutive_clean_rounds replaces early return | counter after line 423 was unreachable (dead code) | Fixed at source; 3-round convergence operational |
| compute_source_hash as single hash path (Option A) | shell sha256sum diverges from Python hash (different prefix + whitespace) | No hash mismatch between receipt writer, verify, and hook |
| check #8 deleted (progressive obligation) | diligent reviewers covering full diff always produce Jaccard distance 0.0 -- 100% false-positive rate | verify reduced to 7 checks; machine.py diff_files fix enables legitimate all-clean convergence |
| mutmut as soft dependency (D-05) | mutmut not always installed; MUTATION_SKIPPED is acceptable | Phase 2 shipped without blocking on env |

## Known Tech Debt / Deferred

- **R5 test layering** (threshold-triggered real-dependency regression): deferred per user direction. The ttl_class incident (real-API smoke caught what 639 mock tests + 9-pass review missed) is the motivation; implementation is post-v2.1.
- **verify anti-shirk ceiling**: `covered_line_ranges` claims are not verified against actual file reads -- coverage claims can be fabricated for all-clean editor receipts. Documented in CLAUDE.md honest-assessment.
- **LLM non-determinism**: temperature=0 or response normalization not yet implemented; non-deterministic output may prevent convergence in edge cases (M2).
- **l1_provider parallelization**: 3 sequential LLM calls per round (M2 performance opportunity).
- **components.yaml authoring burden**: large repos (e.g., code/kernel/networking with 106 subsystems) may find per-component mapping expensive.

---

*Archived: 2026-05-30 | Previous milestone: v2.0.0a1 (2026-05-18)*
*See .planning/MILESTONES.md for cross-milestone history*
