# Phase 41: Sampling contract_spec + focus_spec wiring (D5.7)

## Goal

Fix the pre-existing bug where the MCP sampling outlet silently ignores
`--contract` / MCP `contract` parameter, and prepare the plumbing for
Phase 41b (review focus wiring).

## Ground-truth (verified 2026-07-20 against main @ 8e18aa0)

### The bug

`build_sampling_l1_provider` (factories.py:507) accepts `contract_spec: str = ""`
and injects it at factories.py:575-576. But its only caller `_dispatch_sampling`
(mcp_server.py:765) passes only `session/loop/resolved` — no `contract_kwarg`.
The parameter is unreachable in production.

### The gap chain

```
MCP client sends contract="..."
  → forge_review(mcp_server.py:888) has contract in scope (line 890)
    → _dispatch_sampling(mcp_server.py:914) — contract NOT passed
      → build_sampling_l1_provider(mcp_server.py:765) — no contract kwarg
        → prompt at factories.py:576 — never reached
```

### Additional gaps discovered during grounding

1. `_build_review_context` (mcp_server.py:648-678) loads only baseline/diff —
   no contracts.yaml. The CLI-subprocess path loads the digest inside its
   forked subprocess (cli.py:2063/2422); the sampling path has no equivalent.

2. Sampling fallback (mcp_server.py:822-823) constructs CLI args with only
   `--backend`/`--outlet`/`--committed` — contract and any future focus are lost.

3. `_merge_contract_spec` (cli.py:1861) with `backend=None` has no warning
   path for >4KB content — passes silently.

## Design decisions

### D1: Separate commit, not folded into focus
Pre-existing bug disclosed per CLAUDE.md rule. Lands before Phase 41b's
focus wiring so the parity test has a working baseline.

### D2: Sampling calls same merge helpers as CLI
Raw pass-through would skip `_merge_contract_spec` (digest merge,
`## Do NOT Flag` split, >4KB summarization, confirmation-bias directive),
producing different prompts by outlet. LOCKED: sampling calls the same
helpers with `backend=None`.

### D3: backend=None is deliberate
Sampling exists because the client has no API key. The `>4096 and
backend is not None` branch is not taken. A new `elif` branch warns
instead of passing silently.

### D4: Raw values in fallback tmpfiles
The fallback subprocess re-runs `_merge_contract_spec` inside its own
CLI path — writing merged values would cause double-merge.

## Scope boundary

Phase 41 threads `focus_spec` through `_dispatch_sampling` as a param
but does NOT wire it to the builder or add injection logic. That is
Phase 41b's scope. This phase only proves the contract wiring works.
