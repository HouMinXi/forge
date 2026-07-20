# ADR-0004: Account-authenticated (OAuth/subscription) backends

**Date**: 2026-06-29
**Status**: accepted
**Deciders**: Minxi Hou
**Relates to**: [ADR-0002](0002-mcp-turnkey-delivery.md), [ADR-0003](0003-customer-supplied-backend.md) (extends the backend matrix to credentials that are not url+key)

## Context

ADR-0003 specifies a provider-agnostic backend the customer fills in with
`url + model + key + params`. But a class of tools the customer already pays
for does not expose a url+key at all: they authenticate the *user's account*
via OAuth / a subscription session. Examples on this workstation:

- **kimi**, **agy** (Antigravity), **GitHub Copilot** -- CLIs that log in with
  a device-code / OAuth flow and talk to the vendor under the user's seat.
- **`claude-me`** -- the user's own bashrc function (`.bashrc` L172, aliased
  L185). It runs `command claude --model opus --effort max` with an
  `env -u CLAUDE_CODE_USE_VERTEX -u ANTHROPIC_BASE_URL -u ANTHROPIC_API_KEY
  ...` prefix that clears every Vertex/key variable, forcing the real `claude`
  binary onto the user's Anthropic Pro account. This is *the model the user is
  driving right now* -- and it has no url+key to hand to forge either.

The question: how does forge run a review using the model under the user's
account, when there is no api_key_env to point at?

## Decision

forge supports account-authenticated models through **two existing paths,
with effectively zero new forge code**. The thing that holds the credential
sits in the middle; forge sees either a CLI it spawns or a plain HTTP backend.

### Path A -- `type: cli` (the authenticated CLI does the auth)

forge's cli outlet spawns `binary -p <prompt> --model <m> --output-format
json` and lets the binary authenticate itself. This already drives the user's
logged-in Claude with no url and no key:

- `claude-me` is exactly this case. Configure `type: cli, command: claude,
  model: opus`. forge spawns the user's authenticated `claude` binary; the
  Pro session does the review.
- Point forge at the **binary** (`claude`), not the bashrc function:
  `claude-me` is an alias to a shell function, so `shutil.which("claude-me")`
  cannot resolve it; `which claude` is a real path.
- One nuance: `claude-me` forces the Pro account by *clearing* Vertex/key env
  vars. forge's spawned `claude` inherits the forge/MCP process environment,
  so if that environment carries `ANTHROPIC_BASE_URL` / `CLAUDE_CODE_USE_VERTEX`,
  the child goes there instead of Pro. To replicate `claude-me` faithfully,
  start the forge/MCP process with the same cleared environment (a launcher
  that `unset`s those vars before exec). The optional `env` field (Open Item)
  makes this declarative.

This is the strongest and easiest case: the user's current session model
reviews, no proxy, no key.

### Path B -- local OAuth bridge -> localhost OpenAI/Anthropic endpoint

kimi / agy / Copilot CLIs do **not** speak Claude's `-p --output-format json`
contract, so forge cannot spawn them directly. The standard pattern (widely
implemented, e.g. CLIProxyAPI, llm-cli-proxy, claude-code-proxy, copilot
bridges): run a small local proxy that holds the OAuth/subscription session
and exposes an OpenAI- or Anthropic-compatible endpoint on
`http://localhost:PORT`. forge then consumes it as a plain backend:

```yaml
backends:
  my-subscription:
    type: api
    format: openai          # or anthropic, whatever the bridge speaks
    base_url: http://localhost:PORT/v1
    api_key_env: DUMMY_KEY  # bridge ignores it; set DUMMY_KEY=x
    model: <the bridge's model id>
```

This is the same code path as the OpenRouter example (ADR-0003): `format:
openai` + a custom `base_url`. **No forge change.** Any account-auth tool with
a community bridge is reachable this way.

### What forge will NOT do: per-CLI adapters

forge will not grow a kimi adapter, a copilot adapter, an agy adapter, each
chasing that tool's own flags and auth. Path B's bridge generalizes to all of
them; per-CLI adapters are unbounded maintenance for no capability the bridge
does not already provide.

### Already implemented (grounded 2026-06-29)

| Capability | Where |
|------------|-------|
| cli outlet spawns user-authenticated CLI | `_invoke_cli` llm_invoke.py:426 (`-p <prompt> --model <m> --output-format json`) |
| default = session-model cli backend (no url/key) | `DEFAULT_BACKEND = BackendConfig(name="session-default", ...)` backend.py:82 |
| cli auth probe (no network) | `_probe_cli`: `which("claude")` + `claude auth status --json` backend.py:496,508 |
| explicit cli backend bypasses probe | backend.py:420 (`name != "session-default"` -> ok=True) |
| localhost bridge as openai/anthropic backend | `format: openai\|anthropic` + `base_url` (ADR-0003 matrix, `VALID_API_FORMATS` backend.py:38) |

So Path A works today for the Claude session; Path B works today for anything
with a bridge. Neither needs new forge code.

## Open item (the only real gap)

**Declarative `env` on cli backends.** To replicate `claude-me` without an
external launcher script, add an optional `env` map on a cli backend:
`unset:` (vars to clear) and/or `set:` (vars to force) applied to the child's
environment before exec in `_invoke_cli`. Small, logic-bearing; handed to an
implementation sub-session. Until it lands, a launcher that `unset`s the
Vertex/key vars before starting the forge/MCP process is the zero-code
workaround.

## Alternatives Considered

### Alternative 1: Per-CLI adapters inside forge
- **Pros**: a user could name `kimi`/`copilot` directly as a backend type.
- **Cons**: every tool has different flags, auth, and output formats; forge
  would track all of them forever. The bridge already covers every case.
- **Why not**: unbounded maintenance for zero extra capability.

### Alternative 2: Require a bridge even for Claude
- **Pros**: one uniform path (everything is an api backend).
- **Cons**: the Claude session already works through the cli outlet with no
  proxy; forcing a bridge in front of it adds a moving part for nothing.
- **Why not**: Path A is strictly simpler for the most common case.

## Consequences

### Positive
- The model the user already pays for (Claude Pro via the session, or
  kimi/agy/Copilot via a bridge) reviews, with no api key to manage.
- forge stays small: one cli outlet + the existing api backend cover every
  account-auth tool; no per-vendor code.

### Negative
- Path B requires the user to run a local bridge process (community-maintained,
  not shipped by forge). Documented, not automated.
- Faithful `claude-me` replication needs a clean child environment until the
  `env` field lands.

### Risks
- A bridge that subtly diverges from the OpenAI/Anthropic schema could produce
  malformed responses; forge already fails closed on parse errors, so this
  surfaces as a backend failure, not a silent false-green.

## G1-G5 impact

Extends G1 ("real backend is the default review engine, not inline PASS") to
account-authenticated / subscription models: the cli outlet and the localhost
bridge both run real passes. G2 (no-/unreachable-backend FAIL FAST) is
unchanged. G3-G5 remain the substance work in
`project_forge_review_skill_retirement` memory.
