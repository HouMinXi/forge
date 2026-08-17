# Forge

## What This Is

A 5-step code review pipeline for AI coding assistants that enforces minimum 9
static review passes before any commit. Forge treats code review as a state machine
with cycle-counter logic, hook enforcement, and anti-hallucination gates. Trusted
models run the pipeline inline (Outlet B); untrusted/cheap models dispatch to the
code-forge CLI subprocess (Outlet A); session models spawn per-pass Agents (Outlet C).

## Core Value

No code ships without surviving three consecutive clean review cycles from three
independent perspectives. The cycle counter resets on any finding -- quality is
non-negotiable. A green verdict is a claim that must be honest; forge runs the
real artifact through the real lifecycle event (a logic simulation = UNVERIFIED,
never PASS) or declares the runtime surface it did not verify; never implies
"ready to ship" for what it only checked statically.

## Current Milestone: v2.9 ENV-GROUNDING

**Goal:** a forge verdict states the environment it was derived in. Today a
finding is asserted against an imagined runtime; v2.9 makes the basis
explicit (what survived falsification, over how many rounds), makes the
environment a declared artifact rather than an assumption, and -- where it
can be afforded -- replaces assertion with execution.

**Target features:**
- Phase 44: EVAL-ON-DUTY -- case generation re-extracts diffs from the LEDGER, so the eval corpus grows from real reviewed work instead of hand curation
- Phase 51: BASIS-DISCLOSE -- surface falsification_survived + convergence_rounds in the basis (the pipeline already computes both; no prompt change)
- Phase 52: ENV-MANIFEST -- manifest tiers (declared > observed > absent, with absent a first-class verdict state) + a version-sensitivity trigger that never trusts model self-report
- Phase 53a: EXEC-FALSIFY v1 -- native venv/build-tree subprocess execution with a time budget; a timeout degrades to an explicit exec-evidence-unavailable disclosure rather than silence
- Phase 53b: EXEC-FALSIFY v2 -- container + driver surface, opt-in, bought only on demonstrated need

**Also in scope (rolled forward from the v2.8 tail, 2026-08-17):**
- Router onboarding compat remainder: F3 trust path, F4 live probe, F2/F5 docs
- Phase 43.1 review-pipeline self-attestation gaps (charter ratified 2026-08-08; two items landed early on main, so scope is smaller than the charter text)

**Provenance:** the lane is not newly invented here. It comes from
v2.9-V3-GROUNDTRUTH-SCHEDULE.md AMENDMENT 1 rev 2, externally certified by
ds+lc adversarial review with a double 0/0/0/0 second round; the adjudication
ledger is dispatch/forge-env-r1-adjudication.txt.

**Shipped so far:** Phase 48 (LLM stream TTFT + truncation continuation),
merged 2026-08-16 as 59c1c51 -- first phase of this lane to land.

## Requirements

### Validated

- Real pre-commit test gate (R1) -- v2.1
- Mutation as pipeline step (R2) -- v2.1
- e2e coverage heuristic + components.yaml (R3) -- v2.1
- Gate-philosophy docs (R4) -- v2.1
- Anti-shirk receipt protocol + code-forge verify (Phase 4) -- v2.1
- Consecutive-clean convergence (3 rounds) -- v2.1
- State machine with 3-mode execution (LOCAL/CI/HOLD) -- v2.0
- SKILL.md dispatches to code-forge review (Outlet A) -- v2.2
- Outlet A: zero-config auto-detect + first-run auto-init -- v2.2
- Outlet A: fail-fast auth check (claude auth status) -- v2.2
- Outlet A: fresh subprocess per review pass -- v2.2
- Outlet A: subprocess orphan protection + cost transparency -- v2.2
- Outlet B: 3 pass skills merged, no Invoke hang -- v2.2
- Outlet B: severity unification to P0-P3 -- v2.2
- Outlet C: subagent dispatch (fresh Agent per pass) -- v2.2
- Both: worktree cwd validation -- v2.2
- Both: FORGE_LLM_MODEL + auth docs -- v2.2
- detect.py: multi-language, alias-free, regen-safe tools.yaml -- v2.2
- Reviewer Canary design spec (write only) -- v2.2
- --whole-file PATH: whole-file review without pending diff -- v2.2
- --model no-pin: session model runs, not forced pin -- v2.2
- D2: Reviewer-not-implementer enforced at outlet level (SHRK-02) -- Phase 15

- Custom backend wiring: gate.yaml backends block wired to cli.py _run() -- v2.3
- mimo / deepseek / kimi backend via api (openai/anthropic format) -- v2.3
- max_tokens fix for large review JSON truncation -- v2.3
- F1/F2/F3 cli.py cleanup + F3 false-green fix (INFRA tagging) -- v2.3
- D1: Outlet C receipt gap closed (deterministic receipt from subagent) -- v2.3
- D3: Diff-size adaptive tiering (2/3/4 cycles by line count) -- v2.3

### Active (v2.9)

REQ-IDs are defined in REQUIREMENTS.md; this list is the one-line reading.

- EVAL-02: eval cases are generated from LEDGER-recorded diffs, not hand-curated (Phase 44)
- BASIS-01/02: the verdict basis names how many falsification rounds a finding survived and how many convergence rounds ran (Phase 51)
- ENV-01/02: the environment a review assumed is recorded in tiers, and "absent" is a stated verdict state rather than a silent default (Phase 52)
- ENV-03: a version-sensitive finding is triggered by the claim's own attributes or by symbol absence from the declared version set, never by the model saying so (Phase 52)
- ENV-04: a finding capped by manifest absence carries a distinct SARIF level (Phase 52)
- EXEC-01/02: a finding can be checked by executing the reviewed diff in a declared native environment, and a timeout says so explicitly instead of degrading to a clean result (Phase 53a)
- EXEC-03: evidence weight is asymmetric -- fail-before is a verdict input, pass-after is receipt-level only (Phase 53a)
- EXEC-04: execution surface extends to containers on demonstrated need, opt-in (Phase 53b)
- ROUTER-02..05: Router onboarding compat remainder -- trust-path disclosure, live backend probe, and two docs gaps (rolled forward from v2.8)
- ATTEST-01..05: the review pipeline's self-attestation gaps close -- a runner cannot execute as a silent no-op, and a rejection rule is proven to reject (Phase 43.1)

### Validated (v2.8)

- Config: user-level ~/.config/code-forge/config.yaml with a $HOME walkup defuse (Phase 37, ADR-0009)
- Onboarding: `forge setup-mcp` one-command MCP install (Phase 38)
- Lifecycle: stale-process guard (38.1) + PDEATHSIG orphan guard (38.2)
- LEDGER: append-only outcome record -- the substrate v2.9 consumes (Phase 43)
- Throughput: L1 pass parallelization, ~3x wall-clock (Phase 39)
- Honesty: PassOutcome + passes=N/M + pass_status in receipts; large-diff chunking (Phase 40)
- Focus: design-intent header unified across 3 builders + --focus with independent trust hash (Phase 41)
- Coverage: multi-language review (Go, C/C++, Java, JS/TS) + doctor tool-audit closing the resolve false-green class (Phases 45, 46)
- Diagnostics: LLMInvokeError surfaced in str(exc) on API and CLI paths (Phase 47)
- Windows: MCP server starts (guarded add_signal_handler) -- wave 1 only

### Validated (v2.7)

- Provider-aware parameter passthrough + SSE streaming; one gate.yaml entry per provider (Phase 34, ADR-0004/0005)
- MCP sampling as a review backend -- the client's own model, no separate key (Phase 35, ADR-0007)
- Usability hardening: 55 audit findings resolved across 7 fix patterns (Phase 36)

### Validated (v2.6)

- ADOPT-01..05: forge gates real changes through a CN backend, fails closed with no backend, and blocked an injected failure through its own hook (Phase 30)
- ROBUST-01..05: 429 backoff with jitter, Retry-After honored, provider error codes classified, 402/403 fast-fail (Phase 31)
- CONTRACT-01: --contract injects per-change intent via the existing contract_spec slot (Phase 32)
- MCP-01/02: code-forge-mcp stdio server routing to the trusted backend, not caller self-review (Phase 33)

### Validated (v2.5)

- CONFIG-01: gate.yaml self-documents via inline field comments + gate.schema.json (Phase 24)
- CONFIG-02: corpus round-trip test validates schema agrees with loader (Phase 24)
- CROSS-01: >=2-repo logical change reviewed as single joint unit (Phase 25)
- CROSS-02: sibling repo spec injected as read-only reference (Phase 26)
- CROSS-03: cross-repo impact via register, advisory (Phase 27)
- SPEC-01: reviewer canary for inline outlet, opt-in (Phase 28)
- DEAD-01: dead-code false-positive filter for advisory axes (Phase 29)

### Out of Scope

- Modifying standalone pass skills (qodo-review, code-review-expert, adversarial-qe)
- M2 risk-graded review depth -- follows Path A
- M3 product repositioning
- Agentic review depth (anti-feature: incompatible with fixed-pipeline thesis)
- R5 test layering / threshold-triggered real-dependency regression
- kimi-cli / native third-party CLI support (YAGNI: all reachable via api)
- Diff-driven model routing (HARD NON-GOAL per D-26)
- forge writing the reviewed tree's code -- a fix ships as a patch artifact plus
  evidence, never as applied state; landing power stays with the caller
  (v2.9 fix-delivery constitution)

Three entries were removed from this list on 2026-08-17 because they had
shipped and the list had gone stale: l1_provider parallelization (built in
Phase 39), Reviewer Canary implementation (built in v2.5 Phase 28), and
Windows IDE support (wave 1 landed 2026-07-11 -- MCP server, doctor, and CLI
review all start on Windows). Windows wave 2 remains unbuilt and
evidence-gated: lock.py's os.kill probe can kill a live holder there, so never
run two forge processes against one workspace on Windows.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Path C is HARMFUL, not just weak | fake 5-star APPROVED worse than no review | v2.2: FAIL FAST, never degrade |
| Dual outlet, single backend | strong models need in-process; weak models need CLI enforcement | v2.2: same backend, two entry points |
| Do NOT touch standalone pass skills | daily kernel review entry + Outlet B source of truth | v2.2: hard constraint |
| Inline merge fixes hang bug | Invoke sub-skill blocks at "Launching skill: X" | v2.2: Outlet B eliminates Invoke |
| Outlet C = subagent per pass | fresh Agent context = anti-fabrication without CLI cold-start | v2.2: third outlet added |
| No model-pin by default (D-26) | session model runs; user configures FORGE_LLM_MODEL explicitly | v2.2: _resolve_model() returns "" |
| gate.yaml backends block (not backends.yaml) | load_backend_configs(data) already parses data.get("backends"); no new file/loader | v2.3: config location decided |
| kimi-cli native = YAGNI | all third-party models reachable via openai/anthropic api; cli backend = claude-code only | v2.3: api-only for third-party |
| Outlet C receipt gap = open design decision | cli.py returns Verdict.PASS for subagent; SKILL.md has no Python receipt call; D1 options a/b/c | v2.3: resolve in discuss phase |
| Conventions digest slot (D11) + cross-repo resolver (D12) | Give reviewer naming context without leaking implementer session; derive from AST + sibling repos, never from session | Phase 15: conventions.py + conventions_resolver.py, 4-source resolver, sha256 cache |
| _make_subagent_spawn factory (Outlet C) | llm_invoke per pass avoids 65K subagent truncation and hang failures; fresh context per pass satisfies SC1-SC3 | Phase 15: NotImplementedError stub replaced, spawn_fn closure, 9 independent llm_invoke calls per review |
| Diff-size tiering is relief, not defense (D-07) | reducing friction for small changes does not weaken large-change review | v2.3: tier_threshold pure function |
| F3 fail-closed via INFRA source tag | error-path findings tagged INFRA skip falsifier, stay CONFIRMED, block fixpoint | v2.3: 4 sites + 1 guard |
| tier_threshold(0)=3 safe default | empty/parse-error diff must not get weakest review; RESEARCH Finding #2 | v2.3: gatekeeper catch |
| Thesis evolves: admit graph-triage tier (v2.4) | system-level review is a real gap, but triage-FEEDS-pipeline preserves the deterministic-gate differentiator; agentic-AS-gate stays out | 2026-06-05: triage prioritizes/scopes/surfaces (advisory), the pre-commit pipeline stays sole gate |
| Advisory axes NEVER block | RUNTIME/LEGACY/INTENT/SYSTEM can surface but never gate; only FIXVAL and TRUST -- about the committed diff -- may block | v2.4: founding principle reaffirmed |
| RUNTIME-01 is ADVISORY, not a 4th R1/R2/R3 | It makes the verdict honest; it does not add a block | v2.4: design constraint |
| Eval corpus = real bugs only | No synthetic/generated bugs; corpus is named real buggy/fixed pairs (E1-E6 + gate.yaml RCE + BUG-P12-01 + ttl_class) | v2.4: eval integrity constraint |

## Constraints

- All documentation and skill files in English
- No non-ASCII in code
- Must work with Claude Code skill discovery (SKILL.md in ~/.claude/skills/<name>/)
- CLAUDE.md and .planning/ are local-only (gitignored); history leaked, purged 2026-06-04

## Context

Shipped v2.8 (2026-08-16). 257 commits, 179 files, +29,963/-2,052 across
46 days, closing the arc that took forge from "installed" to "used by people
who are not the author" -- user-level config, one-command MCP onboarding,
process lifecycle guards, parallel passes, multi-language coverage, and two
rounds of consumer-pain fixes reported by real downstream users.

Codebase at v2.9 start: 30,817 LOC Python (src/), 155 test files. Full suite
at main 59c1c51: 3,415 passed, 9 skipped, 778.89s.

v2.9 starts from a different problem than v2.8 did. v2.8 asked whether anyone
could run forge; v2.9 asks what a forge verdict actually means. A finding
today is asserted against an environment nobody declared -- the reviewer
imagines a runtime, and the verdict inherits that imagination silently. The
lane makes the basis explicit, the environment declared, and (in 53a) lets
execution replace assertion where the budget allows.

Open at start: six Phase 48 follow-ups (none blocking), the Router compat
remainder, Phase 43.1, and eight pending todos. `fix/rebase-msg` is one
commit ahead of main and needs inspecting before deletion.

## Evolution

This document evolves at phase transitions and milestone boundaries.

---
*Last updated: 2026-08-17 after v2.9 milestone start (ENV-GROUNDING). This
file had been stale since 2026-06-26 -- it still named v2.6 as current while
v2.6, v2.7, and v2.8 all shipped. The reconciliation that fixed it also
archived those three into MILESTONES.md.*
