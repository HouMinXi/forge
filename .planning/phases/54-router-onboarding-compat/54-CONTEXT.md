# Phase 54: Router onboarding compat remainder - Context

**Gathered:** 2026-08-18
**Status:** Ready for planning

<domain>
## Phase Boundary

Close the remaining OmniRoute-class OpenAI-compatible-router onboarding
friction: F3 trust-path visibility + walk-up, F4 opt-in live backend probe
on doctor, F2 base_url /v1 doc, F5 user-level-inheritance discoverability.
F1 (SSE prevention at the request) already shipped in v2.8 (a47d888) and is
NOT in scope. The shared-parse SSE auto-detect tolerance stays deferred on
its stated trigger (a router that returns SSE despite being told not to).

Single plan (54-01), internal order: F2/F3/F5 (small) then F4 (the only
item with network behavior and real test design).

</domain>

<decisions>
## Implementation Decisions

### F4 live backend probe
- **D-01:** Probe depth is a real 1-token chat completion (minimal
  max_tokens), exercising URL assembly + response parsing end to end.
  Connectivity-only probing was rejected: it cannot catch the F1(SSE)/F2(URL)
  problem class, which is the actual router debug pain.
- **D-02:** UX is `code-forge doctor --live` -- a flag on the existing
  doctor backend check, NOT a new command (triage report forbids a
  from-scratch `backend test`).
- **D-03:** `--live` probes ALL configured backends, serially. Per-backend
  cost is ~1 token; router problems usually live on the non-default backend.
- **D-04:** Failure diagnostics = error class (SSE-mixed / JSON-malformed /
  timeout / connection-refused / credential-rejected) + first ~200 bytes of
  the response body + one suggested action line.
- **D-05:** Budget: 60s total timeout, ZERO retries. A probe is a
  deterministic snapshot of reality; retries mask instability. (Measured:
  <5s on VPN paths gives ~40% false failures; the 600s review cap would hang
  doctor.)
- **D-06:** A live-probe failure makes doctor exit 1 via the existing
  has_fail pipeline (doctor.py:509). `--live` is opt-in, so CI only pays
  this when it asks.

### F3 trust path visibility
- **D-07:** Mutating trust operations (bare `trust`, `--revoke`) print the
  resolved absolute gate.yaml path before acting. `--status` stays as-is.
- **D-08:** When cwd is not a git repo root, warn on stderr but proceed.
  A wrong-dir trust is recoverable via --revoke; do not hard-block scripted
  use.
- **D-09:** Add walk-up resolution to trust (currently
  `cwd / ".code-forge" / "gate.yaml"` directly, cli.py:1314), matching the
  review path's rule so subdirectory invocation works. Follows ADR-0009; no
  new policy invented. Combined with D-07 the printed path removes the
  ambiguity walk-up introduces.

### F2 base_url doc
- **D-10:** gate.schema.json base_url description only (~5 lines: forge
  concatenates base_url + "/chat/completions" verbatim; whether to include
  /v1 is the operator's responsibility). No README copy -- two wordings
  drift.

### F5 user-level inheritance discoverability
- **D-11:** Doc pointer to the existing ~/.config/code-forge/config.yaml
  inheritance (shipped in Phase 37.1, `_merge_user_into` cli.py:171, call
  sites :1249/:2786/:3879) PLUS a doctor output line surfacing it. The
  original author's real problem was discoverability, not a missing feature.

### Packaging
- **D-12:** One plan (54-01) covering F2+F3+F5+F4. All four items are
  small; splitting buys nothing.

### Claude's Discretion
- Probe prompt content, exact excerpt length (~200 bytes), and the error
  taxonomy strings are implementation details -- planner/implementer may
  choose within D-04's intent.
- Wave/task ordering inside 54-01 (F2/F3/F5 before F4) is a recommendation,
  not a constraint.

### Review protocol (user directive 2026-08-18, binding on the plan)
- **CP1 internal:** gsd-plan-checker + plan-review (PBR) adversarial passes,
  iterated to an internal 0B/0H/0M/0L exit.
- **CP1b external:** aicc panel deepseek + kimi-k2.7 + gemini, with
  per-model-focused prompts (read the aicc memory BEFORE dispatching:
  `~/.claude/projects/-home-houminxi/memory/reference_aicc_tool.md` and
  `reference_aicc_model_review_profiles.md`; kimi k2.7 vs k3 switching and
  the Kimi TPD `--upsert` requirement are documented there).
- **Fix loop:** external findings get ground-truth adjudicated and fixed;
  the FIXED plan re-runs CP1 internal to 0/0/0/0 before the next external
  round. Each external round's prompt must state what changed since the
  last round.
- **Final exit:** the LAST word belongs to the external panel -- the
  modified plan must go back out and come back unanimously 0B/0H/0M/0L.
  A fix round does not count as convergence.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Router friction evidence (the controlling document)
- `.planning/reports/router-friction-triage-20260725.md` -- PM-verified
  verdicts for F1-F5, the DO-NOT-rebuild section (F5 merge logic, F4
  from-scratch command), and the corrected priority table. Its file:line
  citations are from main @ 74adbf2 (2026-07-25) and are STALE -- re-grep,
  do not copy. This file's own warning: stale numbers can still resolve to
  real lines and read as correct.
- `.planning/ROADMAP.md` -- v2.9 section, "Phase 54" entry + the v2.8
  section's Router onboarding compat entry (full F1 history and the
  re-grep warning).
- `.planning/REQUIREMENTS.md` -- ROUTER-02..05 acceptance text.

### Policy
- `docs/adr/0009-user-level-configuration.md` -- $HOME walk-up policy that
  F3's resolution rule must follow (tracked in git, not .planning).

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets (line numbers re-grepped 2026-08-18 on main @ 4087b05)
- `src/code_forge/backend.py:896` `_probe_api` -- offline credential probe
  F4 extends; docstring states "No subprocess, no network call" (the live
  mode must stay opt-in to keep that guarantee for the default path).
- `src/code_forge/backend.py:777` `probe_backend` -- dispatcher that calls
  `_probe_api` for type=api backends (:817).
- `src/code_forge/doctor.py:118` `_check_backends` -- the doctor check row
  F4 hangs off; `:509` `return 1 if has_fail else 0` is the exit pipeline
  D-06 rides on.
- `src/code_forge/cli.py:1297` `_run_trust` -- F3's home; resolution at
  :1314, parser at :739 (--status/--revoke mutually exclusive).
- `src/code_forge/cli.py:171` `_merge_user_into` -- F5's existing machinery
  (call sites :1249 eval, :2786 review, :3879 resolve-outlet).
- `src/code_forge/llm_invoke.py:165-167` -- timeout conventions
  (DEFAULT 1800 / CLI cap 300 / API cap 600) that D-05 deliberately does
  NOT reuse; the probe gets its own 60s budget.

### Established Patterns
- Genre: evidence-driven consumer-pain batch (same shape as the shipped
  surflare-pain and usability-onramp batches) -- unplanned origin, grouped,
  evidence-gated.
- Forge review discipline: F3/F4 are logic-bearing (forge review + 3 clean
  cycles + bug-injection); F2 is schema text (docs marker, no review); F5's
  doctor line is code and rides with the code review.
- `feat/backend-custom-headers` (a3abdf3) is MERGED to main -- the 43.1
  briefing's "branch from the headers branch" warning is stale; branch from
  main.

### Integration Points
- doctor check registry: F4 adds a live tier to the backend check row.
- trust.py record_trust/revoke_trust: F3's print goes before these calls.

</code_context>

<specifics>
## Specific Ideas

- The F4 probe doubles as the regression witness for the F1 prevention
  mechanism: a router that misbehaves on `stream: false` should show up as
  a classified probe failure, not a hung review.
- House measurement rule applies to probe design: verify at production
  input size; a short-payload probe that hides client differences is a
  known trap (memory: feedback_client_fingerprint_wrong_hop).

</specifics>

<deferred>
## Deferred Ideas

- Shared-parse SSE auto-detect tolerance (the original F1 fix shape):
  deferred on trigger -- a router that returns SSE despite `stream: false`.
- `mcp-cli-gate-lookup-divergence` (todos/pending): CLI walks up, MCP only
  checks the exact root. Same gate.yaml-resolution theme as F3 but a
  distinct cross-entry-point defect; reviewed, not folded (scope guard).
- F1-style SSE-forcing-router support beyond the probe's diagnosis: out of
  scope until the deferred trigger fires.

### Reviewed Todos (not folded)
- `.planning/todos/pending/mcp-cli-gate-lookup-divergence-20260816.md` --
  considered for F3 folding; deferred as a separate MCP-vs-CLI resolution
  defect.

</deferred>

---

*Phase: 54-router-onboarding-compat*
*Context gathered: 2026-08-18*
