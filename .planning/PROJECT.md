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

## Current Milestone: v2.6 Adoption

**Goal:** forge actually gates real changes on this machine through its real
(different-lab CN) backend, handles CN provider error diversity robustly,
offers per-change intent input, and provides an IDE-native MCP surface.

**Target features:**
- Phase 30: Switch-On + Dogfood -- CN backend trust, install-hooks, fail-closed, forge self-review
- Phase 31: CN Backend Robustness -- 429 retry (exponential backoff + jitter), Retry-After, provider-specific error codes, L1 pass concurrency control
- Phase 32: Per-Change Intent Contract -- `--contract` flag feeding existing contract_spec slot
- Phase 33: MCP Server -- `code-forge-mcp` stdio server for IDE-native review/gate-check

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

### Active (v2.6)

- ADOPT-01: resolve-outlet names a real backend, not "no backend" (Phase 30)
- ADOPT-02: one real review returns CN-API findings, not DELEGATED/PASS (Phase 30)
- ADOPT-03: pre-commit hook blocks a commit introducing a new test failure (Phase 30)
- ADOPT-04: no-backend = fail-closed error, never silent PASS (Phase 30)
- ADOPT-05: forge dogfoods itself -- injected failure blocked by own gate (Phase 30)
- ROBUST-01: 429 retry with exponential backoff + jitter in llm_invoke (Phase 31)
- ROBUST-02: Retry-After header honored when present (DeepSeek, Kimi) (Phase 31)
- ROBUST-03: provider-specific error codes mapped (Zhipu 1302/1305, MiniMax 1002/1041) (Phase 31)
- ROBUST-04: L1 pass concurrency control for rate-limited backends (Phase 31)
- ROBUST-05: 402/403 balance exhaustion = non-retryable fast-fail (Phase 31)
- CONTRACT-01: --contract flag injects per-change intent via existing contract_spec (Phase 32)
- MCP-01: code-forge-mcp stdio server callable from MCP client (Phase 33)
- MCP-02: MCP review tool routes to trusted backend, not caller self-review (Phase 33)

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
- l1_provider parallelization (3 sequential passes -- deferred)
- Reviewer Canary implementation (SPEC-01 spec ready, deferred to v2.4+)
- Windows IDE support (subprocess lifecycle, signal handler portability)
- kimi-cli / native third-party CLI support (YAGNI: all reachable via api)
- Diff-driven model routing (HARD NON-GOAL per D-26)

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

Shipped v2.5 (2026-06-26). 93 commits, 91 files, +16063/-573 since v2.4.
Full suite: 2104 tests (2103 passed + 1 pre-existing semgrep 1.166.0 env fail).
Codebase: 20925 LOC Python (src/).
v2.6 starts: forge engine complete, now on-ramping to production use.
Verified (this session): R1 baseline, deepseek backend trust, fail-closed.
Open: mimo-pro 429 under 3-pass burst, install-hooks, dogfood, contract, MCP.

## Evolution

This document evolves at phase transitions and milestone boundaries.

---
*Last updated: 2026-06-26 after v2.6 milestone start (Adoption)*
