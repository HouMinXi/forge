# ADR-0010: Multi-method onboarding picker

**Date**: 2026-07-24
**Status**: draft (proposed) -- phase NOT yet opened; gated on Phase 41 (review-focus) CP1b close
**Deciders**: Minxi Hou (pending)
**Relates to**: [0002](0002-mcp-turnkey-delivery.md), [0003](0003-customer-supplied-backend.md), [0004](0004-account-authenticated-backends.md), [0005](0005-provider-aware-parameter-passthrough.md), [0007](0007-mcp-sampling-review-backend.md), [0009](0009-user-level-configuration.md)

> DRAFT seeded by a PM session 2026-07-24 to capture the design before the
> context was lost. It is NOT an accepted decision and NO implementation phase
> is open. Turn it into a GSD phase (design-first) only after Phase 41 closes.

## Context

A new forge user, on install, has no guided way to choose how forge runs
reviews. In practice they hand-edit `~/.config/code-forge/config.yaml` or run
`setup-mcp --backend <x>` needing to know the flags. If they copy a
`--backend mimo-pro` incantation, the generated MCP wrapper hard-fetches that
vendor's key on startup -- so a fresh install *appears* to "require mimo-pro."

Motivating incident (2026-07-24): a fresh install on fleet-suite looked like it
demanded a mimo-pro key. Root cause was NOT shipped forge -- forge's default is
keyless (`DEFAULT_BACKEND`, session-model cli, no url/key, backend.py). The
demand came from a user-generated `code-forge-mcp-pass` wrapper from a prior
`setup-mcp --backend mimo-pro`. But the incident exposed the real gap: onboarding
has no guided, keyless-first path, so users fall into key-required setups by
default instead of by choice.

**What is ALREADY decided (do not reinvent).** The backend/method substrate is
largely specced or built across the ADR series. The methods exist; the guided
UX to choose among them does not. This ADR is a UX/onboarding layer over decided
backends, not new backend types.

## Decision (proposed)

Add an interactive onboarding picker -- `code-forge login` (or an interactive
mode of `setup-mcp`) -- modeled on `claude /login`. It presents the
already-supported methods keyless-first and writes the correct config for the
chosen method, so a new user reaches a working review with zero hand-editing and
external keys become explicit opt-in.

### Method inventory (mapped to existing ADRs -- NOT new backend types)

| Method | Mechanism | ADR | Status today |
|--------|-----------|-----|--------------|
| Borrow model, CLI client | `type: cli` spawns the user's authenticated `claude` (session-model backend) | 0004 Path A | IMPLEMENTED, keyless |
| Borrow model, MCP GUI client | `outlet: sampling` (`ctx.session.create_message`) | 0007 | PROPOSED, not built; VS Code/Copilot only, NOT Claude Code |
| URL + key | `type: api`, `base_url` + `api_key_env/file` + `format: openai\|anthropic` | 0003 | IMPLEMENTED |
| Account-auth (OAuth/subscription) | cli Path A, or localhost OAuth bridge -> api backend Path B | 0004 | IMPLEMENTED (bridge is doc-not-automated by decision) |
| Cloud (Bedrock / Vertex) | api backend + provider params | 0005 | passthrough decided |
| Local model (ollama / llama-server) | api backend, localhost `base_url`, empty key | 0003 | IMPLEMENTED (special case of URL+key) |

### Client-aware keyless default (the non-trivial part)

"Borrow the model you already have" is TWO mechanisms with different client
support -- the picker's keyless option MUST branch on the detected client:

- **CLI clients** (Claude Code and peers): cli / session-model backend spawns the
  authenticated `claude` binary (ADR-0004 Path A). Keyless, works today.
- **MCP GUI clients that support sampling** (VS Code + Copilot, Visual Studio):
  sampling outlet (ADR-0007). Keyless, but PROPOSED-not-built and **unavailable
  on Claude Code** (github anthropics/claude-code#1785, no ETA).

A naive single "borrow the IDE model" option is wrong: on Claude Code it must be
the cli backend, not sampling.

### Picker UX sketch (keyless-first, like /login)

```
$ code-forge login
  How should forge run reviews?
  > 1. Borrow the model I already have   (no key -- uses your logged-in CLI/IDE)
    2. API endpoint + key                (deepseek / mimo-pro / glm / openai / custom / local)
    3. Cloud                             (Bedrock / Vertex)
```

- Option 1 detects the client: CLI -> write a cli/session-default gate.yaml
  (works now); sampling-capable GUI -> `outlet: sampling` (once 0007 ships).
- Option 2 -> preset submenu or free URL+key -> api backend in user config
  (ADR-0009 precedence).
- Default highlight on 1. External keys are never the default.

## Alternatives Considered

### Alternative 1: Do nothing / doc-only
- **Pros**: keyless default already exists; zero code.
- **Cons**: it is undiscovered; new users keep falling into key-required setups
  and the "requires vendor key" confusion recurs.
- **Why not**: rejected as the product-facing fix; kept as the interim state
  until the phase opens.

### Alternative 2: Keyless auto-detect, no picker
- **Pros**: zero prompts for the user.
- **Cons**: sampling is unsupported on Claude Code (0007 Phase 2 gates
  auto-detect on broader client support); silently choosing a backend hides cost
  and model choice.
- **Why not**: premature and opaque; explicit picker first.

### Alternative 3: Full picker over all 6 methods (incl. Bedrock/Vertex and
OAuth-bridge automation) in v1
- **Pros**: complete on day one.
- **Cons**: sampling needs 0007 built; OAuth-bridge automation is doc-not-
  automated by ADR-0004 decision; cloud flows are enterprise-niche.
- **Why not**: over-scope -- see scope challenge.

## Scope challenge (public artifact)

- **Smallest viable**: picker over the ALREADY-IMPLEMENTED methods -- keyless cli
  default (client-detected) + api URL+key with presets. Covers this user and the
  large majority of forge users.
- **Defer to backlog (demand-signal gated)**: sampling keyless path (needs 0007
  built, VS-Code-only), OAuth-bridge automation (0004 Path B is doc-by-decision),
  Bedrock/Vertex guided flow, the ADR-0004 declarative-`env` open item.
- **Demand signal that would justify expansion**: issues/requests asking for a
  guided Copilot/VS-Code keyless flow or a cloud-provider onboarding.
- **Do-nothing cost**: new users keep hand-editing config or copying
  key-demanding incantations; the "install demands a vendor key" class of
  confusion recurs.

## Consequences

### Positive
- New-user install has a guided, keyless-first path; external keys become
  explicit opt-in; kills the "install demands a vendor key" confusion at the root.
- Reuses the decided backend substrate -- the picker is UX, not new backends.

### Negative
- A picker is new interactive UX surface (prompts, client detection) to build and
  test; client-detection heuristics can misfire.

### Risks
- Sampling's Claude-Code gap means the "borrow model" option behaves differently
  per client -- this must be surfaced honestly in the picker, never hidden behind
  a single label that silently does nothing on Claude Code.

## Dependencies / open items

- ADR-0007 (sampling) is proposed-not-built; the GUI keyless path depends on it
  and is VS-Code/Copilot-only.
- ADR-0004 open item: declarative `env` (unset/set) on cli backends, for faithful
  `claude-me` clean-environment replication.
- Reconcile picker output with ADR-0009 user-config precedence during phase
  planning (line numbers in this draft are indicative; re-verify at plan time).

## Sequencing

This draft is a SEED, not an open phase. Open the phase only after Phase 41
(review-focus) closes CP1b. Then GSD design-first: this ADR -> phase plan ->
CP1/CP1b -> implement. Do not let it interrupt Phase 41.
