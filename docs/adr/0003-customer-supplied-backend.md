# ADR-0003: Customer-supplied, provider-agnostic backend

**Date**: 2026-06-29
**Status**: accepted
**Deciders**: Minxi Hou
**Amends**: [ADR-0002](0002-mcp-turnkey-delivery.md) (specifies the provider matrix and config surface)

## Context

ADR-0002 said the customer "configures backend url + key." This ADR
specifies what that config surface must be: the out-of-the-box capability is
that the customer plugs in *their own* backend -- provider + endpoint URL +
model name + key + the parameters that matter -- and forge reviews with it.
The model is IDE-MCP-style model selection: the customer chooses the
provider and model, the tool just works with that choice.

"Out of the box" therefore means the configuration surface is **complete and
provider-agnostic**, not that the project ships a key. The product ships no
provider key and operates no gateway; the customer brings their own backend.
(An earlier draft of this ADR proposed a project-operated gateway holding a
bundled key -- withdrawn; see Alternatives.)

The provider set the customer must be able to choose from:
Vertex AI, any OpenAI-compatible endpoint (OpenRouter, SiliconFlow,
Together, self-hosted LiteLLM/one-api gateway), Anthropic, and a Claude
CLI/proxy.

## Decision

forge's customer-facing backend config is one provider-agnostic spec the
customer fills in (via gate.yaml, env, or first-run prompt):

- `format`: `openai` | `anthropic` | `vertex`, or `type: cli` for a Claude
  CLI/proxy.
- `base_url`: the customer's endpoint.
- `model`: the customer's model id (e.g. `anthropic/claude-sonnet-4.6` on
  OpenRouter).
- key: `api_key_env` (the env var name), or OAuth/ADC for Vertex.
- `max_tokens`, plus optional sampling params (see Open Items).

### Already implemented (grounded 2026-06-29)

The provider matrix and core fields already work; this ADR records the
decision and the remaining gaps, it does not start from zero:

| Capability | Where |
|------------|-------|
| openai / anthropic / vertex formats | backend.py `VALID_API_FORMATS` |
| api + cli backend types (cli = Claude proxy) | backend.py `VALID_BACKEND_TYPES` |
| OpenRouter / SiliconFlow / Together / self-hosted | `format: openai` + custom `base_url` |
| customer endpoint | `backend.base_url` (openai -> `/chat/completions`, anthropic -> `/v1/messages`) |
| customer key | `api_key_env` (Vertex: OAuth/ADC) |
| model selection | `backend.model` |
| output cap | `backend.max_tokens` |
| Vertex reachable through the gate | probe fix, forge main 113ca36 |
| router documentation | OpenRouter section, forge main 6263684 |

### Open items (the actual remaining work)

1. **Sampling-parameter passthrough.** Today only `model` and `max_tokens`
   are configurable; `temperature` is pinned to 0 in the openai path
   (deterministic, which is defensible for code review) and omitted in the
   anthropic/vertex paths (provider default). There is no `top_p`/`top_k`/
   `stop` passthrough. If customers must tune sampling, add an optional
   `params` dict on the backend config, merged into the request body,
   defaulting to current behaviour (temperature 0). DECISION NEEDED: expose
   params, or keep them pinned for review determinism. RESOLVED by ADR-0005 (typed fields + generic params passthrough).
2. **Self-service config UX.** Config is gate.yaml or env today. To match the
   "select your model like an IDE" expectation, add a guided
   `code-forge init` (prompt provider / url / model / key) and a per-IDE MCP
   env-block snippet. ADR-0002 already allows "env var or first-run prompt."

## Alternatives Considered

### Alternative 1: Project-operated gateway holding a bundled key (this ADR's withdrawn draft)
- **Pros**: True zero customer config -- the customer enters nothing.
- **Cons**: Either ships a raw provider key in the artifact (leaks to every
  install, unrotatable, quota-drainable) or requires operating a gateway
  (hosting, cost, abuse control, and the customer's diffs transit a
  project-run service -- a privacy surface).
- **Why not**: The customer brings their own backend. The project should
  ship no key and run no gateway. Bundling a raw key stays rejected
  regardless.

### Alternative 2: Single hardcoded provider
- **Pros**: Simplest possible config.
- **Cons**: Customers choose a provider by cost, data policy, and existing
  credits; one hardcoded provider serves none of those.
- **Why not**: Provider-agnostic choice is the requirement.

## Consequences

### Positive
- The capability is provider-agnostic and already mostly implemented; the
  customer picks Vertex / OpenAI-compatible / Anthropic / Claude-proxy +
  model + key.
- No key distribution, no gateway: no shared-cost, abuse, or privacy
  liability for the project.
- SiliconFlow can still be offered as a documented free *example* endpoint
  the customer points at with their own free key -- never the project's key.

### Negative
- There is no truly-zero-config default: the customer must supply a backend.
  This matches the IDE-MCP model-selection norm the requirement references.

### Risks
- The Open Items (param passthrough, config UX) are what stands between
  "works once configured" and "feels like a capability, not a config chore."
  Mitigation: `code-forge init` wizard + per-IDE doc; decide the param
  question explicitly.

## G1-G5 impact

G1 ("real backend is the default review engine, not inline PASS") is reached
for *any* customer-chosen backend precisely because the provider matrix is
complete: whatever the customer configures runs real passes. G2 (no-/
unreachable-backend FAIL FAST) is unchanged and still required. G3-G5 remain
the substance work in `project_forge_review_skill_retirement` memory.
