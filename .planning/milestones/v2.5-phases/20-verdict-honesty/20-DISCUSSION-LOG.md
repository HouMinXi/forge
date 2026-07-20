# Phase 20: Verdict Honesty - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md -- this log preserves the alternatives considered.

**Date:** 2026-06-12
**Phase:** 20-Verdict Honesty
**Areas discussed:** Trust model, UNVERIFIED placement, Surface derivation,
Lifecycle question wiring, Advisory-HOLD tension / eval scoring, Smoke receipt
chain, Default state, Display policy, Two-outlet anti-drift, Per-surface
accounting, Eval matching mechanism

---

## Round 1 -- initial gray areas (all four selected)

### Trust model (SC1)

| Option | Description | Selected |
|--------|-------------|----------|
| Mechanical evidence gate | Smoke claim requires machine-verifiable receipt (transcript + exit code, Phase 4 anti-shirk model); no evidence = UNVERIFIED | x |
| Skill self-report contract | smoke-test SKILL.md records UNVERIFIED when it cannot run; forge trusts the record | |
| Hybrid: self-report + spot checks | Self-reported field plus lightweight evidence spot-validation | |

**User's choice:** Mechanical evidence gate (recommended option). -> D-01

### UNVERIFIED placement

| Option | Description | Selected |
|--------|-------------|----------|
| Smoke-axis status | Verdict stays PASS/FAIL/HOLD; smoke axis carries VERIFIED/UNVERIFIED; output gains NOT VERIFIED block; exit codes unchanged | x |
| New top-level verdict value | Fourth verdict value UNVERIFIED; consumers must adapt; friction with advisory-never-escalates | |

**User's choice:** Smoke-axis status (recommended). -> D-02

### Surface derivation (SC3)

| Option | Description | Selected |
|--------|-------------|----------|
| LLM enumeration (v1) | RUNTIME axis call enumerates unverified surfaces; eval 3-run majority tames stochasticity; zero new machinery | x |
| Mechanical catalog | Static diff-signal lookup table; deterministic but table quality decides everything | |
| Mechanical floor + LLM enrichment | Both; highest v1 cost; merge/dedup is a new problem | |

**User's choice:** LLM enumeration v1 (recommended). -> D-03

### Lifecycle question wiring (SC2)

| Option | Description | Selected |
|--------|-------------|----------|
| Dedicated RUNTIME axis call | Post-convergence axis LLM call via AxisRunner seam; no L1 prompt pollution; both outlets covered | x |
| Weave into existing L1 prompt | Zero extra calls but messier extraction, dilutes finding-focused prompts | |
| Both | L1 awareness + dedicated extraction; one extra call plus dual maintenance | |

**User's choice:** Dedicated RUNTIME axis call (recommended). -> D-04

| Option | Description | Selected |
|--------|-------------|----------|
| Fixed standard question | One fixed lifecycle/side-effect question with diff context slots; deterministic, auditable | x |
| LLM-generated per diff | Tailored but doubly stochastic, hard to audit or reproduce | |

**User's choice:** Fixed standard question (recommended). -> D-05

### Advisory-HOLD tension / eval scoring (SC5)

Context: E1-E6 corpus entries carry expected_verdict: HOLD, but RUNTIME is
advisory and structurally cannot produce HOLD (Phase 17 D-14).

| Option | Description | Selected |
|--------|-------------|----------|
| Content match + fix corpus | Add expected_advisory keywords; caught = advisory names the surface; correct E1-E6 expected_verdict to real pipeline behavior | x |
| Keep HOLD, other axes catch | Hope FIXVAL/TRUST/L2 HOLD these diffs; they cannot (runtime escapes), score becomes meaningless | |
| Eval-only HOLD escalation | RUNTIME escalates to HOLD in eval mode only; violates D-19 (eval runs the real pipeline) and the structural principle | |

**User's choice:** Content match + fix corpus (recommended). -> D-06

---

## Round 2 -- second-order gray areas (all four selected)

### Smoke receipt chain

| Option | Description | Selected |
|--------|-------------|----------|
| forge-owned wrapper | code-forge smoke-run executes the command, tees transcript + exit code, keys receipt by diff content-hash (P-05/P-07 pattern); executor cannot forge content | x |
| Executor-written + validation | Executor writes receipt JSON, forge heuristically validates; transcript remains forgeable | |

**User's choice:** forge-owned wrapper (recommended). -> D-07

### Default state with no smoke evidence

| Option | Description | Selected |
|--------|-------------|----------|
| Default UNVERIFIED | Fail-closed: no evidence = unverified; two states only | x |
| Mark only false claims | Only claimed-but-invalid runs flagged; silence on never-ran misses the most common case (most of E1-E6) | |

**User's choice:** Default UNVERIFIED (recommended). -> D-08

### NOT VERIFIED display policy

| Option | Description | Selected |
|--------|-------------|----------|
| Status line always + surfaces on demand | One smoke-status line every review; surface list expands only when non-empty; stderr + JSON dual output | x |
| Mention only when non-empty | Quietest, but silence-reads-as-verified returns -- the exact misread this phase kills | |
| Always fully expanded | Most honest but spams pure-docs diffs | |

**User's choice:** Status line always + surfaces on demand (recommended). -> D-09

### Two-outlet question text anti-drift

| Option | Description | Selected |
|--------|-------------|----------|
| Code constant + drift test | Canonical constant in src/code_forge; SKILL.md mirror copy; test asserts verbatim equality | x |
| install-skill generation-time injection | True single source but in-repo SKILL.md becomes a half-product with empty slots | |

**User's choice:** Code constant + drift test (recommended). -> D-10

---

## Round 3 -- third-order gray areas

### Per-surface accounting

| Option | Description | Selected |
|--------|-------------|----------|
| Per-surface accounting | smoke-run --surface declares what a run exercises; NOT-VERIFIED = enumerated minus declared; "1/3 surfaces verified" | x |
| Global boolean | Any valid receipt = VERIFIED; one marginal command washes the whole list green | |
| Boolean + appendix list | Global status with uncovered-list hint; status word still over-optimistic | |

**User's choice:** Per-surface accounting (recommended). -> D-11

### Eval expected_advisory matching

| Option | Description | Selected |
|--------|-------------|----------|
| Keyword substring | Case-insensitive substring per entry keyword list; any hit = caught; deterministic, zero extra calls | x |
| LLM judge | More semantic but the honesty ruler itself becomes stochastic | |
| Regex | More precise but heavy authoring burden, overfits wording | |

**User's choice:** Keyword substring (recommended). -> D-12

---

## Claude's Discretion

- Receipt JSON schema details (fields, timestamps, fingerprint format)
- Exact fixed lifecycle question wording (draft from E1-E6 catalog)
- Per-entry expected_advisory keyword lists
- Surface-name alignment mechanics (enumeration vs --surface declarations)
- Surfaces-list display cap / noise control
- Per-entry corrected expected_verdict values (run and observe, do not assume)
- SARIF inclusion of the advisory block

## Deferred Ideas

- Mechanical surface catalog (only if eval proves LLM enumeration misses)
- Re-execution verification (rejected v1: side effects, cost)
- LLM judge for eval matching (rejected v1: stochastic ruler)
- install-skill generation-time question injection (if mirrors multiply)
- PARTIAL smoke state (rejected: per-surface counts already convey it)
