# Phase 2 Plan Draft: State Machine + Disposition Protocol

**Phase**: 2 of 7
**Status**: Draft for AI review (pre-split into sub-plans)
**Drafted**: 2026-05-17
**Depends on**: Phase 1 (Layer 0, done)

## Decisions Resolved by Review (2026-05-17)

Three BLOCKERs from R1 review (3/3 consensus) plus consensus on MEDIUM Q2:

| # | Decision | Source |
|---|----------|--------|
| **D1** | `.forge/` directory auto-created on first write (no `forge init` required). Permission 0755 (matches `.git/` pattern). `forge --help` does NOT create `.forge/`. | R1 Q7 unanimous |
| **D2** | `state.json` includes `schema_version: int` field from day 1. Version mismatch -> clear error + start fresh (no migration in v2.0). | R1 Q8 unanimous |
| **D3** | 02-02 (State Machine) accepts `mode: Mode` as constructor parameter. Mode resolution (TTY detection / env var / `--mode` flag) stays in 02-04 + 02-05. Removes dependency inversion. | R1 Q3 (Mimo BLOCKER) |
| **D4** | 02-01 defines `DISPOSITION_PROTOCOL_VERSION = 1` constant (cheap insurance, 1 line). Do NOT rename enum to `DispositionV1` (premature indirection). | R1 Q2 MEDIUM consensus |
| **D5** | 02-01 defines `MAX_FIX_ATTEMPTS_PER_FINGERPRINT` as protocol constant (DISPO-05 references it). 02-02 configures the value but the symbol lives in 02-01. | R1 hidden-dependency finding |
| **D6** | 02-04 title renamed: "HOLD Pause + Resume + Concurrent Lock" -> **"Mode Execution + Ordering + Lock"** (HOLD entry/exit is in 02-02, not 02-04). | R1 sub-plan boundary clarity |
| **D7** | `FEEDBACK_SCHEMA_VERSION = 1` constant defined in 02-01 (`src/forge/disposition.py`, alongside `DISPOSITION_PROTOCOL_VERSION`). Phase 7 misses.jsonl writer reads constant for record `schema_version` field. v2.0 ships only the constant; ledger writer/reader stays Phase 7. Rationale: 1-line addition now avoids v2.1 migration burden when Phase 7 ships. | Post-R1 challenge (opus4.6: "FEEDBACK schema can move to Phase 2") -- partial accept (constant only, not logic) |

## Phase Goal

Forge operates as a loop-until-fixpoint state machine with two modes (LOCAL, CI) and an abstract disposition protocol. State machine consumes Disposition (CONFIRMED/DISMISSED/UNCERTAIN/FIXED) without owning the falsification logic -- a stub engine at this phase returns configurable dispositions to drive tests. CLI surface, BASELINE handling, HOLD pause/resume, concurrent-run lock, and CI SARIF output are all in scope.

## Scope Surface (24 requirements)

Gate (2): GATE-01b, GATE-03
State Machine (11): STATE-01, STATE-02, STATE-03, STATE-04, STATE-05, STATE-06, STATE-07, STATE-08, STATE-09, STATE-10, STATE-11
Disposition Protocol (6): DISPO-01, DISPO-02, DISPO-03, DISPO-04, DISPO-05, DISPO-06
Baseline Model (3): BASELINE-01, BASELINE-02, BASELINE-03
CLI Surface (3): CLI-01, CLI-02, CLI-03
Layer 0 SARIF Output (1): LAYER0-07

Total: 26 reqs (some counts above overlap; 26 is the canonical count from ROADMAP.md Phase 2 reqs list)

## Proposed Sub-Plan Breakdown

This phase is much larger than Phase 1 (which had 9 reqs in 4 sub-plans). Proposed 6-sub-plan split:

### 02-01-PLAN: Disposition Protocol + Stub Engine
**Reqs**: DISPO-01, DISPO-02, DISPO-03, DISPO-04, DISPO-05, DISPO-06; plus `FEEDBACK_SCHEMA_VERSION` constant (per D7; Phase 7 implements the misses.jsonl ledger itself)
**Scope**: Define Disposition enum (CONFIRMED/DISMISSED/UNCERTAIN/FIXED), abstract `falsify(finding) -> Disposition` interface, stub implementation returning configurable test dispositions, state.json schema with findings[]/dispositions/fix_attempts/round/mode/source_hash/verdict/converged, FIXED lifecycle (next-round re-verify), MAX_FIX_ATTEMPTS promote CONFIRMED->UNCERTAIN once, auto-fix-invalid revert handling. **Plus 1-line `FEEDBACK_SCHEMA_VERSION = 1` constant** added to disposition.py alongside DISPOSITION_PROTOCOL_VERSION; Phase 7 misses.jsonl writer reads this constant. No ledger logic in Phase 2.
**Success Criteria**:
1. Disposition enum + abstract interface defined; stub returns configurable disposition per test fixture
2. state.json schema written, loaded, validated against schema version
3. FIXED -> CONFIRMED revert on re-detection in next round works
4. MAX_FIX_ATTEMPTS promotion to UNCERTAIN fires exactly once per fingerprint
5. Auto-fix produces parse-error code -> git restore + fix_attempts increment + stays CONFIRMED
6. `FEEDBACK_SCHEMA_VERSION = 1` constant exported from disposition.py; test verifies it is an `int` with value `1` (Phase 7 contract). No ledger writer/reader in Phase 2.

### 02-02-PLAN: State Machine Core (modes + bounds + verdict)
**Reqs**: STATE-01, STATE-02, STATE-03, STATE-04, STATE-05, GATE-01b, GATE-03; plus **DISPO-04/05/06 behavior tests** (moved from 02-01 per H4 from 02-01 review -- protocol contract lives in 02-01, transition behavior needs state machine to test)
**Scope**: Loop-until-fixpoint state machine bounded by MAX_TOTAL_ROUNDS; two mode branches (LOCAL loop, CI linear); non-convergence A/B/C/D diagnosis on ESCALATED; HOLD pause definition (GATE-01b); PASS conditional determinism (GATE-03). **Plus state-machine-driven tests** for DISPO-04 (FIXED lifecycle: remove vs revert), DISPO-05 (CONFIRMED -> UNCERTAIN promotion stickiness, L0 re-detect must not override), DISPO-06 (auto-fix parse-fail -> revert + fix_attempts++) consuming StubFalsifier + with_errors.json from 02-01.
**Success Criteria**:
1. LOCAL loop terminates on fixpoint (zero new CONFIRMED in a round)
2. CI linear path exits FAIL on first CONFIRMED, exits PASS otherwise
3. MAX_TOTAL_ROUNDS exhaustion -> ESCALATED (exit 4) with A/B/C/D diagnosis category; **one test fixture per A/B/C/D category** (4 fixtures) verifies each diagnosis path
4. PASS verdict reproducible given complete disposition ledger (defined as: state.json `findings[]` array where every entry has terminal disposition FIXED/DISMISSED, or in LOCAL mode every UNCERTAIN has human disposition recorded)
5. **In LOCAL mode**, HOLD only entered when UNCERTAIN remain AND no unfixed CONFIRMED. CI mode never enters HOLD (UNCERTAIN -> SARIF warning only).
6. **State.json failure modes**: corrupted state.json -> raise typed error, do not crash silently; missing state.json -> start fresh (not error). Test fixtures for each.
7. **DISPO-04 behavior** (from 02-01 H4): test_fixed_lifecycle.py covers (a) FIXED finding gone next round -> removed from active list; (b) FIXED finding persists -> revert to CONFIRMED + fix_attempts++; (c) new finding (fingerprint not in prior round's active findings) = independent entry per DISPO-04 "new" definition.
8. **DISPO-05 promotion stickiness** (from 02-01 H4): test_dispo05_promotion.py simulates >=2 rounds post-promotion -- CONFIRMED -> UNCERTAIN happens exactly once per fingerprint; subsequent L0 re-detection does NOT re-CONFIRM (FP-04 exception); human re-CONFIRM in HOLD -> ESCALATED frozen exit. Uses StubFalsifier `promotion_path.json` fixture moved from 02-01.
9. **DISPO-06 auto-fix revert paths** (from 02-01 H4): test_dispo06_revert.py covers (a) git-mode: parse-fail fix -> `git restore` invoked + fix_attempts++ + stays CONFIRMED; (b) non-git mode: parse-fail fix -> snapshot restore invoked (**requires 02-03 snapshot API**; non-git branch lands AFTER 02-03 ships, see sequencing note below)
10. **StubFalsifier "errors" catch-and-convert** (consumes 02-01 B4 with_errors.json): state machine calls falsify() that raises RuntimeError -> finding becomes UNCERTAIN with error field populated (Phase 4 SC-6 contract).

**Cross-sub-plan sequencing note for 02-02**: DISPO-06 SC9(a) (git-mode) lands when 02-02 ships; DISPO-06 SC9(b) (non-git mode snapshot restore) is gated on 02-03 BASELINE-02 snapshot API. 02-02 ships with git-mode test green; non-git test added as a follow-up commit inside 02-02 sub-plan after 02-03 lands. This is a soft dependency, not a blocker on 02-02 main scope.

### 02-03-PLAN: BASELINE & Source Model
**Reqs**: BASELINE-01, BASELINE-02, BASELINE-03, STATE-07
**Scope**: Unified `(source, baseline) -> ...` model. Source/baseline resolution per CLI-01: git mode (refs + WORKING/INDEX pseudo-refs) and non-git mode (baseline=empty or stored snapshot). source_hash computation per STATE-07. Snapshot persistence (BASELINE-02) and invalidation rules (BASELINE-03).
**Success Criteria**:
1. Git mode: `--baseline HEAD --head WORKING` produces correct diff including untracked files
2. Non-git mode first run: baseline=empty, all source content treated as new
3. Non-git mode subsequent run: stored snapshot at `.forge/snapshots/<source-hash>.json` loaded as baseline
4. Edits during HOLD -> source_hash changes -> stored state.json marked stale -> next forge invocation starts fresh (intended behavior, not a bug)
5. Snapshot partial invalidation: changed files re-reviewed, unchanged files use snapshot
6. Missing snapshot file -> fall back to baseline=empty silently (snapshot absence is normal, not an error)

### 02-04-PLAN: Mode Execution + Ordering + Lock
**Reqs**: STATE-06, STATE-08, STATE-09, STATE-11
**Scope**: Mode selection (`--mode` flag + TTY default), L0-before-L1 intra-round ordering, CI starts fresh (no state.json inherit from LOCAL), file lock with PID-based stale detection.
**Success Criteria**:
1. `--mode local|ci` flag works; default = LOCAL if TTY, CI otherwise
2. Within a round, L0 runs+fixes before L1 begins
3. CI invocation in directory with prior LOCAL state.json -> CI does not load prior LOCAL state.json (STATE-09 explicit test)
4. Concurrent forge invocation: alive PID -> exit 3 (BUSY); dead PID -> remove stale lock + proceed
5. **[integration test]** SIGKILL on forge -> stale lock cleanup works on next invocation (integration test required because unit test cannot simulate SIGKILL on the same Python process)

### 02-05-PLAN: Engine Swap + CLI Surface + Exit Codes
**Reqs**: STATE-10, CLI-01, CLI-02, CLI-03
**Scope**: `--falsification-engine auto|stub|real` flag with auto-detection. Full CLI specification per CLI-01 (mode, engine, sandbox, baseline, head, paths). Exit code map (0/1/2/3/4) per CLI-02. Env var overrides per CLI-03.
**Success Criteria**:
1. `auto` engine resolves to `real` when Phase 4 module importable, else `stub` (test by import shim)
2. CLI parses all flags and positional paths correctly; --help shows full surface
3. Invalid args -> exit 2 (CLI_ERROR) with usable error message
4. Env vars (FORGE_MODE, etc.) override defaults; explicit flag overrides env
5. Exit code map matches GATE-01a + GATE-01b semantics across all 5 codes

### 02-06-PLAN: SARIF Output (CI Mode)
**Reqs**: LAYER0-07
**Scope**: SARIF 2.1.0 emission to stdout in CI mode. Level mapping (CONFIRMED=error, UNCERTAIN=warning, DISMISSED=suppressed-external, FIXED=suppressed-inSource). One-line summary to stderr. `tool.driver.semanticVersion` populated.
**Success Criteria**:
1. SARIF output validates against SARIF 2.1.0 schema
2. All 4 disposition states map to correct SARIF level / suppression
3. stderr summary in deterministic format matching regex `^forge: (PASS|FAIL|ESCALATED) findings=(\d+) confirmed=(\d+) uncertain=(\d+) dismissed=(\d+) fixed=(\d+)$` -- integrator scripts can parse single-line summary reliably
4. semanticVersion includes forge version + each L0 tool version used

## Sub-Plan Sequencing

Recommended implementation order (each sub-plan independently committable):

1. **02-01 (Disposition Protocol)** AND **02-03 (BASELINE)** -- **parallelizable** (no inter-dependency); both are leaf modules
2. **02-02 (State Machine Core)** -- depends on 02-01 + 02-03 (consumes Disposition enum + source_hash)
3. **02-04 (Mode Execution + Ordering + Lock)** -- extends 02-02 with mode-specific behavior
4. **02-05 (CLI + Engine Swap)** -- integrates 02-01..02-04 into user-facing surface
5. **02-06 (SARIF Output)** -- output formatting; **parallelizable** with 02-05 (different concern)

**Parallel timeline (2 developers or 1 developer split focus):** 02-01 || 02-03 -> 02-02 -> 02-04 -> 02-05 || 02-06. Sequential single-developer timeline unchanged (6 sub-plans serial).

## Integration Points Between Sub-Plans

| Integration | Sub-plans | Concern |
|-------------|-----------|---------|
| Disposition enum used by state machine | 02-01 -> 02-02 | Enum must be stable before state machine implementation; protocol versioned per D4 |
| source_hash used by HOLD resume | 02-03 -> 02-04 | STATE-07 source_hash needs BASELINE-01 resolution |
| Mode flag affects state machine + CLI + state.json | 02-02 + 02-04 + 02-05 | Single source of truth for mode value (env var precedence rules); 02-02 receives mode as param per D3 |
| Engine swap affects all dispositions | 02-01 + 02-05 | stub vs real must produce identical Disposition shapes |
| CI mode + SARIF + exit codes | 02-04 + 02-05 + 02-06 | CI exit code derived from SARIF finding levels |
| **state.json schema as evolving cross-sub-plan contract** | 02-01 (base) + 02-02 (round/mode/verdict/converged) + 02-03 (source_hash/baseline_ref) + 02-04 (lock_pid?) | Single canonical schema file owned by 02-01; subsequent sub-plans add fields additively (no removes, no renames). `schema_version` per D2 increments only on breaking change. |
| **Phase 1 L0 runner API compatibility** | Phase 1 done -> 02-02 | Phase 1 produces Findings via existing runner. 02-02 state machine must consume Findings without modifying Phase 1 API. Audit Phase 1 `forge.runner.run_all_tools(diff)` signature in 02-02 planning before writing state machine consumer. |
| **Intra-round ordering vs FIXED lifecycle** | 02-04 STATE-08 + 02-01 DISPO-04 | Within a round: L0 fixes apply first; if L0 fix succeeds, finding goes FIXED. L1 then runs against post-L0-fix code. If L1 fixes a different line, it's a separate fingerprint (DISPO-04 "new = independent entry"). Test fixture required. |
| **CI fresh start vs state.json loader** | 02-04 STATE-09 + 02-01 DISPO-03 | DISPO-03 defines state.json schema + loader. STATE-09 says CI mode skips loader entirely. 02-01 stub-load should accept a `--no-load` or equivalent gate; 02-04 mode logic decides to call it. |
| **Baseline resolution vs CLI parsing** | 02-03 BASELINE-01 + 02-05 CLI-01 | CLI-01 parses `--baseline empty|<git-ref|pseudo>|<snapshot-path>` string; 02-03 BASELINE resolution consumes parsed value and dispatches to git/snapshot/empty handler. Contract: CLI emits typed `BaselineSpec` object, BASELINE resolves it. |

## Open Questions for AI Review

1. **Sub-plan granularity**: 6 sub-plans of varying size (02-01 + 02-02 are heavy, 02-06 is light). Should 02-06 merge into 02-05? Should 02-01 split further?

2. **Disposition enum stability**: 02-01 defines enum, all other sub-plans depend. If Phase 4 falsification engine in v2.1 finds we need 5th disposition state, all sub-plans need revisiting. Should 02-01 explicitly version the enum (Disposition v1) for forward compatibility?

3. **STATE-07 source_hash with non-git mode**: needs to hash concatenated file content (sorted by path). What about large repos with thousands of files? Hash performance acceptable for v2.0? Or need optimization (Merkle tree)?

4. **Concurrent lock edge cases**: PID reuse after OS PID wraparound (Linux: ~32k typical, can wrap). What if dead PID's slot was reassigned to vim? Mitigation: also store start-time in lock file, validate PID + start-time match. Worth adding?

5. **CI mode UNCERTAIN warning destination**: SARIF level=warning is correct for SARIF, but CI integrators may want a one-line summary count. Should stderr summary include per-disposition counts (CONFIRMED=3 / UNCERTAIN=2 / DISMISSED=5 / FIXED=0)?

6. **Stub disposition source**: 02-01 stub returns "configurable dispositions". Config format -- inline test fixture, YAML, JSON, env var? Likely Phase 2 planning detail, but architectural choice affects test ergonomics.

7. **`.forge/` directory creation**: forge needs `.forge/` for state.json, snapshots/, feedback/. Auto-create on first run? Or require explicit `forge init`? If auto, what permissions (0700 vs 0755)?

8. **Stale state.json from incompatible forge version**: If user upgrades forge, prior state.json might have incompatible schema. Migration vs reject? state.json schema version field?

## Risks Identified

| Risk | Severity | Mitigation |
|------|----------|------------|
| Phase 2 is large (6 sub-plans, ~3 months work for one person) | High | Sub-plans independently testable; can ship 02-01 + 02-02 as minimal MVP, defer 02-04..02-06 to Phase 2.5 if timeline slips |
| **Phase 1 L0 runner API mismatch** | **High** | Audit Phase 1 `forge/runner.py` signatures in 02-02 planning before writing state machine consumer; if API gaps found, raise as Phase 1 hotfix not Phase 2 redesign |
| **DISPO-05 precedence inversion implementation complexity** (CONFIRMED -> UNCERTAIN promotion sticks against L0 re-detection per FP-04 exception) | **High** | 02-01 explicitly tests promotion-stickiness with fixture: L0 detects X (CONFIRMED) -> fix fails N times -> promote to UNCERTAIN -> next round L0 still detects X -> finding stays UNCERTAIN (verify L0 re-detection does NOT override) |
| Stub engine (02-01) vs real engine (Phase 4) interface drift | Medium | Define Disposition enum + falsify() signature as protocol in 02-01; Phase 4 must conform; DISPOSITION_PROTOCOL_VERSION constant per D4 enforces awareness |
| Non-git mode (02-03) untested workflow | Medium | Phase 2 tests must include non-git fixtures; v1 kimi-next-key.sh pattern as canonical test case |
| **state.json schema drift across 4 sub-plans** | Medium | Single canonical schema file in 02-01; subsequent sub-plans add fields additively; schema_version per D2; schema validation test in every sub-plan |
| **Mode value three-way coupling (CLI/env/TTY default)** | Medium | 02-05 owns mode resolution; 02-02 receives resolved Mode object per D3; 02-04 reads from state.json (set by 02-05). Single test fixture exercises all 3 sources. |
| **Non-git mode auto-fix has no git restore** | Medium | DISPO-06 says auto-fix-invalid -> revert via git restore. Non-git mode needs alternative: snapshot original file before fix, restore from snapshot on parse failure. 02-01 + 02-03 coordination needed. |
| **Signal/interrupt cleanup (Ctrl+C mid-fix leaves partial tree changes)** | Medium | 02-04 trap SIGINT/SIGTERM, mark state.json with `interrupted: true`, attempt revert of in-flight fix, release lock. Test fixture for interrupt during DISPO-06 revert path. |
| **HOLD resume edge cases (corrupted state, permissions, deleted state)** | Medium | Per SC additions: corrupted state -> typed error; missing state -> start fresh; permission denied -> clear error message. Each case has test fixture. |
| **CLI flag interaction explosion** (5 flags x 3-4 values each = 20+ valid combos, more invalid) | Medium | 02-05 enumerate combo matrix in test fixtures; reject impossible combos in CLI parser (e.g., `--mode ci --falsification-engine stub` warns "stub in CI defeats determinism") |
| STATE-11 lock race conditions hard to test | Medium | Property-based tests + Hypothesis library; specifically test PID reuse, stale cleanup, concurrent acquisition |
| Test combinatorial explosion (modes x baselines x dispositions x dep audit triggers) | Low | Pairwise testing via Hypothesis; full matrix only for critical paths |
| Non-git snapshot disk usage | Low | Snapshot is JSON metadata (paths + hashes + dispositions), not file content copies; expected < 100KB per snapshot even for large dirs |
| SARIF schema compliance (02-06) | Low | Use SARIF validation library (e.g., python-jsonschema with SARIF schema) in tests |

## What This Plan Draft Does NOT Cover (intentional omissions)

- Sub-plan internal task breakdowns (will be in each 02-NN-PLAN.md after this draft is approved)
- Specific code structure / file layout (implementation detail for sub-plan time)
- Test harness specifics beyond "use existing pytest from Phase 1"
- Migration of Phase 1 state.json schema (Phase 1 is done; this is greenfield for Phase 2)

## Review Instructions

This is a draft of the Phase 2 PLAN BREAKDOWN, not the full sub-plans themselves. Reviewers should focus on:

1. **Sub-plan boundary correctness** -- are the 6 splits well-chosen? Any reqs orphaned or doubly-assigned?
2. **Sequencing soundness** -- is the implementation order valid? Any false dependencies?
3. **Integration points completeness** -- did we miss any cross-sub-plan handoffs?
4. **Open question priorities** -- which of the 8 open questions are BLOCKER vs MEDIUM vs LOW for Phase 2 planning to proceed?
5. **Risk completeness** -- what risks should we add?

Do NOT review:
- Phase 2 architectural decisions themselves (already settled in v2.0 docs after 5+ R rounds, see [[forge-v2-finalized]])
- Sub-plan internal details (will come in 02-01-PLAN through 02-06-PLAN after this draft is approved)
- Whether reqs are correct (settled in REQUIREMENTS.md)

## Next Steps After Review

1. Address review findings (revise sub-plan boundaries, close open questions)
2. Write each 02-NN-PLAN.md in proposed sequence order (02-01 first)
3. Each sub-plan independently goes through gsd-review before implementation
4. Implementation starts only after all 6 sub-plans approved
