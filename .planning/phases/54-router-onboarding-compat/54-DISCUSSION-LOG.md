# Phase 54: Router onboarding compat remainder - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-08-18
**Phase:** 54-router-onboarding-compat
**Areas discussed:** F4 probe depth, F4 entry point, F4 backend scope, F3 print timing, F3 strictness, F2 doc placement, F5 discoverability, packaging, F4 diagnostic granularity, F4 timeout/retry budget, trust walk-up, doctor --live exit code

---

## F4 probe depth

| Option | Description | Selected |
|--------|-------------|----------|
| Real 1-token completion | Full URL+parse path; only depth that catches F1(SSE)/F2(URL) class | ✓ |
| Connectivity only (TCP/TLS/HTTP) | Zero tokens but cannot catch parse-layer failures | |
| Two-tier (shallow default, --deep) | Split "ok" semantics depending on tier | |

**User's choice:** Real 1-token completion
**Notes:** ROADMAP required F4 to justify itself on debug-loop value; connectivity-only adds too little over the existing offline credential check.

## F4 entry point

| Option | Description | Selected |
|--------|-------------|----------|
| doctor --live | Flag on existing doctor check; reuses table/exit/report plumbing | ✓ |
| doctor --live + --live-all split | Default-backend-only vs all; extra flag semantics | |

**User's choice:** doctor --live
**Notes:** Triage report forbids a from-scratch `backend test` command.

## F4 backend scope

| Option | Description | Selected |
|--------|-------------|----------|
| All configured backends | Serial; ~1 token each; router issues usually on non-default backend | ✓ |
| Default backend only | Cheapest, but probing the other backend needs config surgery | |

**User's choice:** All configured backends

## F3 print timing

| Option | Description | Selected |
|--------|-------------|----------|
| Before mutating ops | trust / --revoke print resolved absolute path before acting | ✓ |
| All subcommands | Including --status; extra noise on a read-only probe | |

**User's choice:** Before mutating ops

## F3 strictness (cwd not a git repo root)

| Option | Description | Selected |
|--------|-------------|----------|
| Warn but proceed | Recoverable via --revoke; don't block scripted use | ✓ |
| Hard refuse | Safest but contradicts --revoke's existence | |

**User's choice:** Warn but proceed (ADR-0009 governs; no new policy)

## F2 doc placement

| Option | Description | Selected |
|--------|-------------|----------|
| gate.schema.json only | ~5 lines in base_url description; closest manual at config time | ✓ |
| schema + README | Wider reach, two wordings drift | |

**User's choice:** gate.schema.json only

## F5 discoverability

| Option | Description | Selected |
|--------|-------------|----------|
| Doc pointer + doctor surface | The original problem was discoverability, not a missing feature | ✓ |
| Doc pointer only | Simplest; discoverability problem remains | |

**User's choice:** Doc pointer + doctor surface

## Packaging

| Option | Description | Selected |
|--------|-------------|----------|
| Single plan 54-01 | All four items; internal order F2/F3/F5 then F4 | ✓ |
| Two plans (small / F4) | Small items land without waiting on F4 design | |

**User's choice:** Single plan

## F4 diagnostic granularity

| Option | Description | Selected |
|--------|-------------|----------|
| Class + excerpt + suggestion | HTTP status, ~200-byte body excerpt, error class, one action line | ✓ |
| Status + class, no excerpt | Cleaner, but F2-class 400s need the body to diagnose | |

**User's choice:** Class + excerpt + suggestion

## F4 timeout/retry budget

| Option | Description | Selected |
|--------|-------------|----------|
| 60s / zero retries | Deterministic snapshot; retries mask instability | ✓ |
| 30s / one retry | Faster, but blurs flaky-network vs dead-backend | |

**User's choice:** 60s / zero retries
**Notes:** Measured VPN-path false-failure rate at <5s (~40%) and the 600s review API cap both cited as boundary evidence.

## trust walk-up

| Option | Description | Selected |
|--------|-------------|----------|
| Add walk-up | Matches review-path resolution; printed path removes ambiguity | ✓ |
| Print+warn only | Strictly triage scope; trust/review resolution stays split | |

**User's choice:** Add walk-up
**Notes:** Explicitly flagged as a behavior change beyond the triage text; user accepted.

## doctor --live exit code

| Option | Description | Selected |
|--------|-------------|----------|
| Failure = exit 1 | Rides existing has_fail pipeline (doctor.py:509); --live is opt-in | ✓ |
| Warn only, exit unchanged | Protects CI; but silently swallowed probe failure is the pattern forge exists to kill | |

**User's choice:** Failure = exit 1

---

## Claude's Discretion

- Probe prompt content, exact excerpt length, error taxonomy strings.
- Wave/task ordering inside 54-01.

## Deferred Ideas

- Shared-parse SSE auto-detect tolerance (deferred on trigger).
- `mcp-cli-gate-lookup-divergence` todo — reviewed, not folded (distinct MCP-vs-CLI resolution defect).
- SSE-forcing-router support beyond probe diagnosis.
