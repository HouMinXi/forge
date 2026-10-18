> Published planning snapshot, 2026-10-18. Source statements below retain their
> original dates and acceptance limits; historical "current" claims are not current-main
> verification. Private identity labels and unavailable evidence are explained in the
> [cloud start index](README.md). Publication grants no execution admission.

# Requirements: Forge

**Defined:** 2026-08-17; continuation reconciled 2026-10-04
**Milestone:** v3.0 MEASURED-REVIEW-QUALITY
**Core Value:** No code ships without surviving three consecutive clean review
cycles; a green verdict is honest or declares what it did not verify.
**Spine:** measured judgment improvement through bounded evidence, explicit environment and honest per-attempt failure/usage accounting. The prior environment guarantees remain in force.

## Founding Principle

The three-cycle pre-commit pipeline is the sole gate. Advisory axes surface
context but never block. A green verdict claims what it verified and declares
what it did not.

**v2.9 adds one clause, from the fix-delivery constitution:** forge never
writes the reviewed tree's code. A fix ships as a patch artifact plus its
evidence bundle, never as applied state; landing power stays with the caller.
`.code-forge/` is carved out as the one directory forge may write.

## Active requirements (v3.0 continuation)

The existing phase 59/60 sequence owns this scope. The parent accepted the frozen literature synthesis conditionally for bounded design/GSD planning on 2026-10-04; independently checked phase plans and fresh source binding remain required before implementation. All requirements below are pending; earlier repair deliveries do not satisfy them automatically. The accepted synthesis is `.planning/research/bounded-review-investigation/research/SUMMARY.md`, SHA256 1de1d0f955fdb6d0f3005a284b84ab2af82f82a7cca52730d8a3f915c45e20ec.

- `[ ]` **P59-CLOSE**: Reconcile the historical calibration decision against its original 18/20 closure gate and recover/verify the 59-B3 capped context arm/canary and 59-B4 matched report, or explicitly revise their acceptance through reviewed evidence. Source presence alone cannot close the phase; canonical artifact absence does not establish absence in every historical worktree.
- `[ ]` **INV-01**: Read bounded file/line ranges on demand from the review snapshot; return path, lines, snapshot identity and truncation/refusal status; reject escape, symlink/race and stale-source cases.
- `[ ]` **INV-02**: Query bounded callers/callees with source locations and graph snapshot provenance; missing/stale/incomplete graph is explicit and cannot mean no callers.
- `[ ]` **INV-03**: Execute at least one useful enabled bounded experiment under a qualified isolation/recipe policy, retaining inputs, stdout/stderr, exit/timeout/cancellation and environment identity; demonstrate denied escape and descendant cleanup. A disabled stub does not qualify. The initial qualified MVP uses a trusted fixed recipe with bounded untrusted-source data; arbitrary repository execution requires separately admitted isolation authority. A different initial profile requires an independently reviewed equivalent qualified boundary.
- `[ ]` **INV-04**: A typed tool loop distinguishes provider replies, tool requests/results and final findings; unsupported capabilities refuse explicitly and preserve the existing non-tool path.
- `[ ]` **INV-05**: Enforce declared finite read/query/output/call/time/process budgets at the Forge boundary; exhaustion or incomplete execution is visible and cannot become a clean result.
- `[ ]` **INV-06**: Preserve failed, rejected and recovered raw reply evidence per attempt, with source/review/round/group identity, declared retention/redaction policy and bounded artifacts; audit-write failure preserves the original failure instead of crashing or inventing success.
- `[ ]` **INV-07**: Record logical calls, observable transport attempts, retries, corrections, continuations, excerpt repairs and tool calls separately; correlate time/outcome/usage, avoid cumulative double counting, and distinguish missing usage from zero. Hidden CLI attempts remain explicitly unknown.
- `[ ]` **INV-08**: Keep deterministic receipt/source/clean-cycle gates authoritative; adversarial source/tool content cannot expand authority, edit the reviewed tree, or turn denied/incomplete investigation into verified evidence.
- `[ ]` **EVAL60-01**: Re-root the existing 150-entry corpus on recorded real checkouts with correct bug/fix direction, validated applicability and context availability before measuring context quality.
- `[ ]` **EVAL60-02**: Compare bounded investigation with the existing pipeline using a frozen matched bug/fix and failure corpus; report precision/recall, failures, time, observable calls/tokens and unknown cost. Include the authorized Ultra comparison only with matched configuration and disclosed material differences; a null result is valid and no superiority claim precedes measurement. Freeze scripts, denominators and acceptance before results; preserve accepted raw artifacts and disclose every result flip.

Dependency order: P59-CLOSE evidence recovery runs alongside INV-06/07 design; INV-06/07 observability and INV-05/08 authority controls precede enabling INV-04/01/02/03. A separately admitted final raw-rejection repair is useful but does not satisfy all-attempt INV-06/07. EVAL60-01 corpus preparation can run independently; EVAL60-02 needs qualified capabilities and that corpus. The phase plan chooses exact budgets and the initial supported adapter/probe under existing user authority, without a blanket new permission gate. Phase 50/53b remain deferred: explicitly adjudicate reuse if the selected execution policy requires their broader driver/container scope. Historical phase 52/53a environment/native execution contracts may inform reuse but do not qualify the new authority or isolation by themselves.

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| P59-CLOSE | Phase 59 | Pending |
| INV-01 | Phase 59 | Pending |
| INV-02 | Phase 59 | Pending |
| INV-03 | Phase 59 | Pending |
| INV-04 | Phase 59 | Pending |
| INV-05 | Phase 59 | Pending |
| INV-06 | Phase 59 | Pending |
| INV-07 | Phase 59 | Pending |
| INV-08 | Phase 59 | Pending |
| EVAL60-01 | Phase 60 | Pending |
| EVAL60-02 | Phase 60 | Pending |

Continuation coverage: 11/11 requirements mapped exactly once, 0 complete, no orphans. Historical phase 56/57/58 completions and v2.9 records below are retained, not re-run or re-certified here. The active phase success criteria in ROADMAP.md identify which of these requirements they establish.

## Historical v2.9 provenance

The lane is not invented in this file. Phases 44/51/52/53a/53b and their
dependency order come from `v2.9-V3-GROUNDTRUTH-SCHEDULE.md` AMENDMENT 1
rev 2, externally certified by ds+lc adversarial review with a double
0/0/0/0 second round; the adjudication ledger is
`dispatch/forge-env-r1-adjudication.txt`. This file derives REQ-IDs from that
certified design rather than re-deciding it.

## Historical requirements (v2.9)

### Eval Substrate (Phase 44 -- EVAL-ON-DUTY)

- `[x]` **EVAL-02**: Eval case generation re-extracts diffs from the LEDGER, so
  the corpus grows from real reviewed work instead of hand curation.
  SHIPPED 2026-08-23 as `code-forge ledger export-eval` (main @ 93ad344);
  adjudicated terminal rows become replayable corpus entries (manifest +
  materialized diffs, D-17 gate.yaml strip, D-09 PII guard).

### Basis Disclosure (Phase 51 -- BASIS-DISCLOSE)

- `[x]` **BASIS-01**: The verdict basis states how many falsification rounds a
  finding survived (`falsification_survived`). Shipped 2026-08-24 (95c06ce)
- `[x]` **BASIS-02**: The verdict basis states how many convergence rounds ran
  (`convergence_rounds`). Shipped 2026-08-24 (95c06ce)

### Environment Manifest (Phase 52 -- ENV-MANIFEST)

- `[x]` **ENV-01**: The environment a review assumed is recorded in manifest
  tiers, ordered declared > observed > absent (Complete: ab7db19)
- `[x]` **ENV-02**: "absent" is a first-class verdict-header state, printed as
  such. A review with no declared environment says so instead of reading as
  though one existed (Complete: ab7db19)
- `[x]` **ENV-03**: A version-sensitive finding is triggered by the claim's own
  attributes, or by a symbol being absent from the declared version set --
  never by the model reporting its own version sensitivity (Complete: ab7db19)
- `[x]` **ENV-04**: A finding capped by manifest absence carries a distinct
  SARIF level, so a consumer can tell it apart from a fully grounded one (Complete: ab7db19)

### Execution Falsification (Phase 53a native, 53b container)

- `[x]` **EXEC-01**: A finding can be checked by executing the reviewed diff in
  a declared native venv or build tree, synchronously and within a budget
  (Phase 53a, ~100-200 LOC) (Complete: eb97c2c)
- `[x]` **EXEC-02**: A budget timeout degrades to an explicit
  exec-evidence-unavailable disclosure. It never degrades to a clean result
  (Complete: eb97c2c)
- `[x]` **EXEC-03**: Evidence weight is asymmetric by design: reviewed-diff
  execution and fail-before are verdict inputs, while pass-after is
  receipt-level only. A passing fix must not confirm a finding, and a failing
  fix must not demote one (Complete: eb97c2c)
- `[ ]` **EXEC-04**: The execution surface extends to containers and a driver
  surface, opt-in and bought only on demonstrated need (Phase 53b; shares
  falsify_real.py with the Phase 50 charter and inherits its boundary)

### Router Onboarding Compat (rolled forward from the v2.8 tail)

- `[x]` **ROUTER-02**: gate.schema.json documents base_url `/v1` semantics
  (docs only) (Complete)
- `[x]` **ROUTER-03**: `forge trust` prints the resolved gate.yaml path and
  warns when cwd is not a project, following the ADR-0009 $HOME policy
  (Complete: cli.py:1396-1507)
- `[x]` **ROUTER-04**: doctor probes a backend live, not only its env vars.
  Shipped in Phase 54 (60336c9): `doctor --live` at cli.py:687 performs one
  real chat completion per api backend, 60s budget each, no retries.
  Verified at the T6 human checkpoint 2026-08-20 against 12 real backends,
  where the 8-class taxonomy fired live (4 ok / 3 http-error / 3
  credential-rejected / 1 unclassified) and caught two defects in the wild.
  Ledger note: this box stayed unchecked until 2026-08-30 even though the
  work shipped 08-21 -- the requirement was satisfied by Phase 54 rather
  than by a phase named after it (Complete)
- `[x]` **ROUTER-05**: Users are pointed at the existing
  ~/.config/code-forge/config.yaml inheritance, which already ships via
  `_merge_user_into` (docs only; docs/configuration.md) (Complete)

### Review Pipeline Self-Attestation (Phase 43.1)

Charter `charter_review_pipeline_gaps.md`, ratified 2026-08-08. Four of its
ten items closed during v2.8 (item 1 via 888333a, item 3 via 8f745dd, item 10
via 0309c55, item 4 root-caused to an OmniRoute-side semantic cache). What
remains:

- `[x]` **ATTEST-01**: The e2e_runner CLI path is wired the way l2_runner now
  is, so it cannot run as a silent no-op (charter item 2; Complete in cli.py)
- `[x]` **ATTEST-02**: A negative test proves the receipt-timestamp rejection
  actually rejects (charter item 5; Complete b8e8837)
- `[x]` **ATTEST-03**: Security reviews route to a backend that can serve them
  (charter item 7; Complete b8e8837, MCP fallback contract restored)
- `[x]` **ATTEST-04**: A reviewer that ignores the annotated post-image line
  numbers is detected (charter item 9; Complete b8e8837, contract clarified)
- `[x]` **ATTEST-05**: The check-6 coverage-floor calibration decision is signed
  off (charter item 6; Complete, signed off as working as intended)

## Validated Requirements (v2.9, shipped)

- `[x]` **STREAM-01**: A stream-mode pass emits a first-token progress event, so
  a long review shows life instead of silence (Phase 48, 59c1c51)
- `[x]` **STREAM-02**: `finish_reason=length` is read as truncation rather than
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
| Unbounded autonomous review replacing deterministic acceptance | Remains out of scope; bounded evidence tools are admitted in current phase59 |
| Diff-driven model routing | HARD NON-GOAL per D-26 |
| Retire the standalone pass trio | Gated on real-backend default + false-green traps closed |
| Windows wave 2 | Evidence-gated, not scheduled. lock.py's os.kill probe can kill a live holder on Windows |
| v3.x flywheel (ESCAPE/SYNTHESIS/REGISTRY/SCOUT) | Sketch only, not externally certified; numbering collides with shipped phases and renumbers at firm-up |

## Historical v2.9 traceability

| REQ-ID | Phase | Status |
|--------|-------|--------|
| STREAM-01 | 48 | Complete (59c1c51) |
| STREAM-02 | 48 | Complete (59c1c51) |
| EVAL-02 | 44 | Complete (93ad344) |
| BASIS-01 | 51 | Complete (95c06ce) |
| BASIS-02 | 51 | Complete (95c06ce) |
| ENV-01 | 52 | Complete (ab7db19) |
| ENV-02 | 52 | Complete (ab7db19) |
| ENV-03 | 52 | Complete (ab7db19) |
| ENV-04 | 52 | Complete (ab7db19) |
| EXEC-01 | 53a | Complete (eb97c2c) |
| EXEC-02 | 53a | Complete (eb97c2c) |
| EXEC-03 | 53a | Complete (eb97c2c) |
| EXEC-04 | 53b | Pending |
| ROUTER-02 | Router compat | Complete (docs) |
| ROUTER-03 | Router compat | Complete (cli.py) |
| ROUTER-04 | Router compat | Complete per recorded v2.9 closeout (Phase54 doctor --live); historical evidence, not re-run here |
| ROUTER-05 | Router compat | Complete (docs) |
| ATTEST-01 | 43.1 | Complete (cli.py) |
| ATTEST-02 | 43.1 | Complete (b8e8837) |
| ATTEST-03 | 43.1 | Complete (b8e8837) |
| ATTEST-04 | 43.1 | Complete (b8e8837) |
| ATTEST-05 | 43.1 | Complete (signed off) |

**Historical coverage note:** the former2-complete/20-pending total was stale and is not current progress. Historical milestone closeout lives in MILESTONES.md; current scope coverage is above.
- v2.9 requirements recorded:22
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
