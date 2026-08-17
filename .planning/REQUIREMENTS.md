# Requirements: Forge

**Defined:** 2026-08-17
**Milestone:** v2.9 ENV-GROUNDING
**Core Value:** No code ships without surviving three consecutive clean review
cycles; a green verdict is honest or declares what it did not verify.
**Spine:** a verdict states the environment it was derived in. The basis
becomes explicit, the environment becomes a declared artifact rather than an
assumption, and where the budget allows, execution replaces assertion.

## Founding Principle

The three-cycle pre-commit pipeline is the sole gate. Advisory axes surface
context but never block. A green verdict claims what it verified and declares
what it did not.

**v2.9 adds one clause, from the fix-delivery constitution:** forge never
writes the reviewed tree's code. A fix ships as a patch artifact plus its
evidence bundle, never as applied state; landing power stays with the caller.
`.code-forge/` is carved out as the one directory forge may write.

## Provenance

The lane is not invented in this file. Phases 44/51/52/53a/53b and their
dependency order come from `v2.9-V3-GROUNDTRUTH-SCHEDULE.md` AMENDMENT 1
rev 2, externally certified by ds+lc adversarial review with a double
0/0/0/0 second round; the adjudication ledger is
`dispatch/forge-env-r1-adjudication.txt`. This file derives REQ-IDs from that
certified design rather than re-deciding it.

## Active Requirements (v2.9)

### Eval Substrate (Phase 44 -- EVAL-ON-DUTY)

- [ ] **EVAL-02**: Eval case generation re-extracts diffs from the LEDGER, so
  the corpus grows from real reviewed work instead of hand curation.
  Prereq Phase 43 (merged 14328bb)

### Basis Disclosure (Phase 51 -- BASIS-DISCLOSE)

- [ ] **BASIS-01**: The verdict basis states how many falsification rounds a
  finding survived (`falsification_survived`). The pipeline already computes
  it; this surfaces it
- [ ] **BASIS-02**: The verdict basis states how many convergence rounds ran
  (`convergence_rounds`). No prompt change is required for either

### Environment Manifest (Phase 52 -- ENV-MANIFEST)

- [ ] **ENV-01**: The environment a review assumed is recorded in manifest
  tiers, ordered declared > observed > absent
- [ ] **ENV-02**: "absent" is a first-class verdict-header state, printed as
  such. A review with no declared environment says so instead of reading as
  though one existed
- [ ] **ENV-03**: A version-sensitive finding is triggered by the claim's own
  attributes, or by a symbol being absent from the declared version set --
  never by the model reporting its own version sensitivity
- [ ] **ENV-04**: A finding capped by manifest absence carries a distinct
  SARIF level, so a consumer can tell it apart from a fully grounded one

### Execution Falsification (Phase 53a native, 53b container)

- [ ] **EXEC-01**: A finding can be checked by executing the reviewed diff in
  a declared native venv or build tree, synchronously and within a budget
  (Phase 53a, ~100-200 LOC)
- [ ] **EXEC-02**: A budget timeout degrades to an explicit
  exec-evidence-unavailable disclosure. It never degrades to a clean result
- [ ] **EXEC-03**: Evidence weight is asymmetric by design: reviewed-diff
  execution and fail-before are verdict inputs, while pass-after is
  receipt-level only. A passing fix must not confirm a finding, and a failing
  fix must not demote one
- [ ] **EXEC-04**: The execution surface extends to containers and a driver
  surface, opt-in and bought only on demonstrated need (Phase 53b; shares
  falsify_real.py with the Phase 50 charter and inherits its boundary)

### Router Onboarding Compat (rolled forward from the v2.8 tail)

- [ ] **ROUTER-02**: gate.schema.json documents base_url `/v1` semantics
  (docs only)
- [ ] **ROUTER-03**: `forge trust` prints the resolved gate.yaml path and
  warns when cwd is not a project, following the ADR-0009 $HOME policy
- [ ] **ROUTER-04**: doctor probes a backend live, not only its env vars.
  This must justify itself on debug-loop value alone -- its original
  rationale (serving as F1's acceptance test) was spent when F1 shipped with
  its own real-path check
- [ ] **ROUTER-05**: Users are pointed at the existing
  ~/.config/code-forge/config.yaml inheritance, which already ships via
  `_merge_user_into` (docs only; the capability was verified present, not
  built)

### Review Pipeline Self-Attestation (Phase 43.1)

Charter `charter_review_pipeline_gaps.md`, ratified 2026-08-08. Four of its
ten items closed during v2.8 (item 1 via 888333a, item 3 via 8f745dd, item 10
via 0309c55, item 4 root-caused to an OmniRoute-side semantic cache). What
remains:

- [ ] **ATTEST-01**: The e2e_runner CLI path is wired the way l2_runner now
  is, so it cannot run as a silent no-op (charter item 2)
- [ ] **ATTEST-02**: A negative test proves the receipt-timestamp rejection
  actually rejects (charter item 5)
- [ ] **ATTEST-03**: Security reviews route to a backend that can serve them
  (charter item 7; todo #54 carries [BLOCKS DEPLOYMENT])
- [ ] **ATTEST-04**: A reviewer that ignores the annotated post-image line
  numbers is detected (charter item 9; todo #59)
- [ ] **ATTEST-05**: The check-6 coverage-floor calibration decision is signed
  off (charter item 6; recommendation on record is "working as intended")

## Validated Requirements (v2.9, shipped)

- [x] **STREAM-01**: A stream-mode pass emits a first-token progress event, so
  a long review shows life instead of silence (Phase 48, 59c1c51)
- [x] **STREAM-02**: `finish_reason=length` is read as truncation rather than
  normal completion, and recovered by bounded continuation with a run-level
  breaker (Phase 48, 59c1c51)

## Validated Requirements (v2.8 and prior)

- v2.8 Config/onboarding: user-level config + $HOME walkup defuse (Phase 37),
  `setup-mcp` (38), stale-process guard (38.1), PDEATHSIG orphan guard (38.2)
- v2.8 Substrate: LEDGER append-only outcome record (Phase 43)
- v2.8 Throughput: L1 pass parallelization, ~3x wall-clock (Phase 39)
- v2.8 Honesty: PassOutcome, passes=N/M, pass_status receipts, large-diff
  chunking (Phase 40)
- v2.8 Focus: unified design-intent header + `--focus` with an independent
  trust hash (Phase 41)
- v2.8 Coverage: multi-language review (Phase 45) + doctor tool-audit closing
  the resolve false-green class (Phase 46)
- v2.8 Diagnostics: LLMInvokeError in str(exc) on API and CLI paths (Phase 47)
- v2.7: provider-aware params + SSE (Phase 34), MCP sampling backend (35),
  55-finding usability hardening (36)
- v2.6: ADOPT-01..05, ROBUST-01..05, CONTRACT-01, MCP-01/02
- v2.5: CONFIG-01/02, CROSS-01/02/03, SPEC-01, DEAD-01
- v2.4: REVIEW-RUNTIME-01 (advisory), REVIEW-FIXVAL-01, REVIEW-TRUST-01 /
  SEC-01, REVIEW-LEGACY-01 / INTENT-01, REVIEW-SYSTEM-01, DAEMON-STATE,
  EVAL-01
- v2.1-v2.3: R1 commit gate, R2 mutation, R3 e2e heuristic, three outlets,
  gate.yaml backends block, diff-size adaptive tiering

## Out of Scope (v2.9)

| Feature | Reason |
|---------|--------|
| forge applying a fix to the reviewed tree | Fix-delivery constitution: delivery is a patch artifact plus evidence, never applied state |
| AutoFixer as a real fixer before Phase 53a | Stays a stub-for-delivery; the 53a execution organ runs code but never drafts |
| Kernel C execution falsification | Delegated to Beaker -- wrong budget for a synchronous review path |
| Agentic review depth | Anti-feature, incompatible with the fixed-pipeline thesis |
| Diff-driven model routing | HARD NON-GOAL per D-26 |
| Retire the standalone pass trio | Gated on real-backend default + false-green traps closed |
| Windows wave 2 | Evidence-gated, not scheduled. lock.py's os.kill probe can kill a live holder on Windows |
| v3.x flywheel (ESCAPE/SYNTHESIS/REGISTRY/SCOUT) | Sketch only, not externally certified; numbering collides with shipped phases and renumbers at firm-up |

## Traceability

| REQ-ID | Phase | Status |
|--------|-------|--------|
| STREAM-01 | 48 | Complete (59c1c51) |
| STREAM-02 | 48 | Complete (59c1c51) |
| EVAL-02 | 44 | Pending |
| BASIS-01 | 51 | Pending |
| BASIS-02 | 51 | Pending |
| ENV-01 | 52 | Pending |
| ENV-02 | 52 | Pending |
| ENV-03 | 52 | Pending |
| ENV-04 | 52 | Pending |
| EXEC-01 | 53a | Pending |
| EXEC-02 | 53a | Pending |
| EXEC-03 | 53a | Pending |
| EXEC-04 | 53b | Pending |
| ROUTER-02 | Router compat | Pending (docs) |
| ROUTER-03 | Router compat | Pending |
| ROUTER-04 | Router compat | Pending (must re-justify) |
| ROUTER-05 | Router compat | Pending (docs) |
| ATTEST-01 | 43.1 | Pending |
| ATTEST-02 | 43.1 | Pending |
| ATTEST-03 | 43.1 | Pending [BLOCKS DEPLOYMENT] |
| ATTEST-04 | 43.1 | Pending |
| ATTEST-05 | 43.1 | Pending (decision sign-off) |

**Coverage:**
- v2.9 requirements: 22 total (2 complete, 20 pending)
- Mapped to phases: 22
- Unmapped: 0

**Dependency order (from the certified schedule):**

    44 -> 51 -> 52 -> 53a -> 53b
                             +-- 53b also requires the Phase 50 charter

Phase 51's only hard prereq is Phase 43 (merged), so pulling it forward to
post-43 is permitted -- the post-44 slot is queue hygiene, not a dependency.
Router compat and 43.1 sit outside this chain and can run in any order.

---
*Requirements defined: 2026-08-17*
*Supersedes the v2.6 requirements set, which had been the live file since
2026-06-26 while v2.6, v2.7, and v2.8 all shipped. Its checkboxes had also
gone stale -- ROBUST-01..05 were marked pending though Phase 31 completed
2026-06-28.*
