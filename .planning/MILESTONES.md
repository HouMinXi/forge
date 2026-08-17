# Forge -- Milestones

> **SHA validity warning (measured 2026-08-17).** main was filter-branch
> rewritten on 2026-07-10 (recorded in STATE.md as c0f2b3d -> 15fdbc6,
> tree-identical, metadata only). Every commit SHA recorded before that
> rewrite no longer resolves. Spot-checked 19 SHAs quoted across ROADMAP.md:
> 13 are DEAD (6fb427e, 965c247, 07d0381, 0a85662, 14b3985, 9f96fd5, 6abb6fb,
> 89a091f, e50b375, c0f2b3d, a18844a, 4b060bd, 4c5f46d) and 6 resolve
> (14328bb, 25b063e, ca0d860, 74adbf2, 8e18aa0, 933032d). The archive entries
> below quote only SHAs verified with `git cat-file -e` at the time of
> writing; pre-rewrite work is located by date and subject instead. Verify
> before quoting any SHA from a phase record older than 2026-07-16.

## v2.8 Onboarding + Throughput (Shipped: 2026-08-16)

**Phases:** 17 tracked units (37, 37.1, 38, 38.1, 38.1-5/6, 38.2, 38.3, 39,
40, 41, 42, 43, 45, 46, 47) plus four unplanned evidence-driven batches
**Timeline:** 2026-07-01 to 2026-08-16 (46 days)
**Git range:** e236e51..dec413e -- 257 commits, 179 files, +29,963/-2,052
**Codebase at close:** 30,817 LOC Python (src/), 155 test files

**Key accomplishments:**

- User-level config at ~/.config/code-forge/config.yaml with a $HOME walkup
  defuse, so a project without gate.yaml no longer inherits the home dir's
  (Phase 37, ADR-0009); backend passthrough and retryable truncation (37.1)
- `forge setup-mcp` one-command MCP onboarding (Phase 38); stale-process and
  workspace guards (38.1); PDEATHSIG orphan guard so a dead parent takes its
  MCP server with it (38.2)
- LEDGER append-only outcome record -- the substrate the whole v2.9
  ENV-GROUNDING lane consumes (Phase 43, 14328bb)
- L1 pass parallelization: three review passes concurrent via
  ThreadPoolExecutor/gather with a CLI serial guard and fixed-order fold,
  roughly 3x wall-clock (Phase 39)
- Honest partial results: PassOutcome enum, `passes=N/M` in the summary,
  pass_status in the receipt JSON, and file-based chunking for large diffs
  (Phase 40, 25b063e). The semantic half -- convergence plateau, prior-round
  memory, cross-file findings under chunking -- was deferred
- Review focus: design-intent header unified across all three prompt builders
  plus a `--focus` emphasis parameter carrying its own trust hash independent
  of backend trust (Phase 41, 74adbf2)
- Multi-language review: Go, C/C++, Java, JS/TS wired end-to-end. Landing this
  exposed that `_resolve_command` matched the whole command string rather than
  its first word, so every flagged tool (ruff, pylint SARIF modes) had silently
  never run -- main had been flake8-only at L0 (Phase 45)
- `forge doctor` registry-vs-executed tool audit, closing the false-green class
  that Phase 45 uncovered (Phase 46)
- LLMInvokeError diagnostics surfaced in str(exc) on both API and CLI paths
  (Phase 47, ca0d860)
- Windows MCP wave 1: guarded add_signal_handler, which had killed every MCP
  start, doctor self-check, and CLI review on Windows at lifespan setup
- Two consumer-pain batches from real downstream users (usability on-ramp,
  surflare) and a 2026-08-09 defect batch of 18 commits covering the
  verify/receipt chain, gate wiring, and the eval runner

**Key decisions:**

- Router onboarding compat F1 shipped by prevention, not tolerance: the stream
  flag is sent explicitly so a router that picks its own default never picks
  SSE. The shared-parse SSE tolerance stays deferred on a stated trigger
- Charter `review_pipeline_gaps` ratified 2026-08-08 as Phase 43.1; two of its
  items landed directly on main during the 08-09 batch, so its scope is
  smaller than the charter text describes

**Carried into v2.9:** Router onboarding compat remainder (F3 trust path, F4
live probe, F2/F5 docs), Phase 43.1, and eight pending todos.

---

## v2.7 Provider Capability (Shipped: 2026-07-01)

**Phases:** 3 (34, 35, 36) | **Plans:** 9
**Timeline:** 2026-06-30 to 2026-07-01 (2 days)
**Git range:** db17d7e..e236e51 -- 31 commits, 45 files, +1,266/-373

**Key accomplishments:**

- Provider-aware parameter passthrough and SSE streaming: backends declare
  per-provider reasoning/sampling params, so one gate.yaml entry per provider
  suffices (Phase 34, ADR-0004 + ADR-0005). Design is key-follows-field --
  the openai wire key is derived from whichever field is populated, while
  anthropic and vertex pin to max_tokens
- MCP sampling as a review backend: forge can use the calling client's model
  (Copilot, Claude Max subscription) instead of requiring a separate API key
  (Phase 35, ADR-0007)
- Usability hardening: 55 findings from a 5-round exhaustive audit resolved as
  7 fix patterns -- MCP-to-CLI flag alignment, error remediation hints, docs
  reconciled against code, silent failures surfaced, onboarding friction,
  edge-path crashes, packaging hygiene (Phase 36)

**Review evidence:** Phase 34 took 7 rounds of external plan review to reach
0/0/0/0 with real-API smoke on DeepSeek and Vertex. Phase 35 took 9 rounds to
converge; its code review then found 9 real bugs across 4 models x 3 rounds,
and the user's own finishing pass found 4 more, each fixed with bug-injection
proof.

---

## v2.6 Adoption (Shipped: 2026-06-29)

**Phases:** 4 (30, 31, 32, 33) | **Plans:** 9
**Timeline:** 2026-06-27 to 2026-06-29 (3 days)
**Git range:** 0c724fd..db17d7e -- 45 commits, 27 files, +4,834/-96

**Key accomplishments:**

- Switch-on and dogfood: forge gates real changes through its CN backend and
  blocked an injected new-failure change through its own pre-commit hook,
  end to end. `resolve-outlet` names a real backend, and a missing backend
  fails closed at exit 2 rather than passing silently (Phase 30)
- CN backend robustness across five providers: exponential backoff with
  jitter, Retry-After honored where the provider sends it (DeepSeek, Kimi),
  provider error codes classified retryable vs not, and 402/403 balance
  exhaustion fast-failing with an actionable message (Phase 31)
- Per-change intent contract: `--contract FILE` feeds stated invariants into
  the existing contract_spec slot so the reviewer checks code against the
  contract instead of against itself. Contract text states invariants and
  residual risks and never asserts "this is correct" -- framing a change as
  safe drops detection by 16-93pp (Phase 32)
- `code-forge-mcp` stdio server exposing review and gate-check to any MCP
  client, routing to the resolved trusted backend rather than a DELEGATED
  self-review by the calling model (Phase 33)

**Note on Zhipu/MiniMax error codes:** mapped from documentation that could
not be re-verified against live docs at the time (the scrape redirected to a
platform intro page). Recorded as UNCONFIRMED in the Phase 31 record.

---

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
