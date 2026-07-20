# Requirements: Forge

**Defined:** 2026-06-26
**Milestone:** v2.6 Adoption
**Core Value:** No code ships without surviving three consecutive clean review
cycles; a green verdict is honest or declares what it did not verify.
**Spine:** forge goes from "built" to "on" -- gates real changes via CN backend,
handles provider error diversity, offers per-change intent, IDE-native MCP.

## Founding Principle

The three-cycle pre-commit pipeline is the sole gate. Advisory axes surface
context but never block. A green verdict claims what it verified and declares
what it did not.

## Active Requirements (v2.6)

### Switch-On + Dogfood (Phase 30)

- [ ] **ADOPT-01**: resolve-outlet names a real backend (not "no backend")
- [ ] **ADOPT-02**: One real review returns CN-API findings (not DELEGATED/PASS)
- [ ] **ADOPT-03**: Pre-commit hook in >=1 target repo blocks a commit that
  introduces a new test failure
- [ ] **ADOPT-04**: With no backend configured, `code-forge review` fails closed
  (errors out), never silent PASS
- [ ] **ADOPT-05**: Forge dogfoods itself: an injected new-failure change is
  blocked by forge's own gate end to end

### CN Backend Robustness (Phase 31)

- [ ] **ROBUST-01**: HTTP 429 triggers retry with exponential backoff + jitter
  in llm_invoke (not silent drop)
- [ ] **ROBUST-02**: Retry-After header honored when present (DeepSeek, Kimi
  return it; others do not)
- [ ] **ROBUST-03**: Provider-specific error codes mapped to retryable vs
  non-retryable (Zhipu 1302/1305/1308, MiniMax 1002/1039/1041/2045)
- [ ] **ROBUST-04**: L1 pass dispatch respects per-backend concurrency limit
  (serial or throttled for rate-limited providers like mimo-pro)
- [ ] **ROBUST-05**: HTTP 402/403 (balance exhaustion, forbidden) treated as
  non-retryable fast-fail with clear error message

### Per-Change Intent Contract (Phase 32)

- [ ] **CONTRACT-01**: `--contract FILE` flag on `code-forge review` feeds
  per-change intent through the existing contract_spec slot; a planted
  contract-violating change is caught that the no-contract run misses

### MCP Server (Phase 33)

- [x] **MCP-01**: `code-forge-mcp` stdio server starts and exposes review +
  gate-check tools callable from any MCP client
- [x] **MCP-02**: MCP review tool routes to the resolved trusted backend
  (proven: a finding returns via CN API, not a DELEGATED self-review)

## Validated Requirements (v2.5 and prior)

- CONFIG-01/02: gate.yaml self-documenting + schema corpus round-trip -- v2.5
- CROSS-01: cross-repo merge review (joint unit) -- v2.5
- CROSS-02: cross-repo contract context (read-only reference) -- v2.5
- CROSS-03: cross-repo impact via register (advisory) -- v2.5
- SPEC-01: reviewer canary for inline outlet -- v2.5
- DEAD-01: dead-code false-positive filter -- v2.5
- Real pre-commit test gate (R1) -- v2.1
- Mutation as pipeline step (R2) -- v2.1
- E2E coverage heuristic + components.yaml (R3) -- v2.1
- Three outlets: A (CLI), B (inline), C (subagent) -- v2.2/v2.3
- Custom backend wiring: gate.yaml backends block -- v2.3
- Diff-size adaptive tiering (2/3/4 cycles) -- v2.3
- REVIEW-RUNTIME-01: runtime lens (advisory) -- v2.4
- REVIEW-FIXVAL-01: fix-validation revert-RED/restore-GREEN -- v2.4
- REVIEW-TRUST-01 / SEC-01: danger-score + taint -- v2.4
- REVIEW-LEGACY-01/INTENT-01: advisory axes -- v2.4
- REVIEW-SYSTEM-01: graph-triage advisory -- v2.4
- DAEMON-STATE: cross-subsystem state-conflict advisory -- v2.4
- EVAL-01: eval corpus with 12 real entries -- v2.4

## Out of Scope (v2.6)

| Feature | Reason |
|---------|--------|
| Multi-branch capability | Deferred to v2.7+ |
| Building a new cross-repo graph engine | Reuse code-review-graph register |
| inspect-core FSL vendoring | Carry v2.4 NON-GOAL |
| Diff-driven model routing | HARD NON-GOAL (D-26) |
| Retire standalone pass skills (trio) | Gated on real-backend default + false-green traps closed (G1-G5) |
| Full CN provider SDK integration | API-level HTTP is sufficient; SDK adds dep weight |
| Windows/macOS MCP support | Linux-first; portability deferred |

## Traceability

| REQ-ID | Phase | Status |
|--------|-------|--------|
| ADOPT-01 | 30 | Verified (this session) |
| ADOPT-02 | 30 | Verified (this session) |
| ADOPT-03 | 30 | Pending |
| ADOPT-04 | 30 | Verified (this session) |
| ADOPT-05 | 30 | Pending |
| ROBUST-01 | 31 | Pending |
| ROBUST-02 | 31 | Pending |
| ROBUST-03 | 31 | Pending |
| ROBUST-04 | 31 | Pending |
| ROBUST-05 | 31 | Pending |
| CONTRACT-01 | 32 | Pending |
| MCP-01 | 33 | Complete |
| MCP-02 | 33 | Complete |

**Coverage:**
- v2.6 requirements: 13 total
- Mapped to phases: 13
- Already verified: 3 (ADOPT-01, ADOPT-02, ADOPT-04)
- Unmapped: 0

---
*Requirements defined: 2026-06-26*
*Last updated: 2026-06-26 after v2.6 milestone initialization*
