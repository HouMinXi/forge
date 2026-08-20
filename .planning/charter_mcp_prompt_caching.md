# Work Order: MCP review path prompt-caching fix

Status: OPEN — assigned to personal laptop (Z66) per user decision 2026-08-20.
Created: 2026-08-20 by architect session (office machine), transcribed from
kanban context of hermes-agent fix/aux-provider-extra-headers work.

## Why this exists

On 2026-08-20 the user disabled the forge MCP review path
(`forge_review` / `forge_gate_check`) across all hermes profiles after
measuring a single MCP-driven review round at 17+ minutes, versus:

| path                        | measured per round |
|-----------------------------|--------------------|
| MCP forge_review            | 17 min+            |
| code-forge CLI (CI mode)    | ~6-7 min           |
| code-forge skill inline     | ~4.5 min           |

Diagnosis at disable time: the backend calls made through the MCP path do
not exploit prompt caching, so every pass (qodo / expert / adversarial) and
every falsify call re-sends the full diff + file context and pays full
prompt processing each time.

Until this is fixed AND the architect explicitly announces the un-block in
a kanban comment, all hermes-side reviews must use the skill-inline path.
Do not re-enable MCP unilaterally.

## Suspected mechanism (verify, do not trust)

`src/code_forge/llm_invoke.py` builds each request payload as a fresh
single-user-message prompt:

- L1568 area: chat-completions style `messages: [{role: user, content: prompt}]`
- L1710 area: `url = backend.base_url + "/v1/messages"` (anthropic-style)
- L1895 area: another messages construction

No `cache_control` breakpoints (anthropic path), no `prompt_cache_key`
(openai path), and no evidence of deliberate prefix-stable prompt
construction. Three passes + N falsify calls on the same diff therefore
each pay full prefill on a multi-KB prompt.

Known backend behavior to account for:

- DeepSeek official API has automatic prefix context caching, but only
  when the prefix is byte-identical across calls.
- The office backends in play: `oc-deepseek` (zen gateway,
  deepseek-v4-flash-free) and `mimo-pro`. Whether the zen gateway forwards
  caching semantics is unverified — measure first.
- Hermes-side evidence from the same day: prompt caching through a relay
  works when the prefix is identical (cached_tokens=1792 on third
  identical request, zero on first two — write propagation delay exists).

## Scope

1. Instrument one MCP `forge_review` round end-to-end: per-call prompt
   tokens, cached_tokens (if returned), TTFT, wall time. Persist raw
   numbers to `.planning/evidence/`.
2. Identify why MCP path is ~2.5x slower than CLI path on the same diff
   and same backend. Two hypotheses to discriminate by experiment:
   a. MCP path constructs prompts differently (e.g. extra wrapping,
      different ordering) defeating prefix caching;
   b. MCP path routes to a different backend/config than CLI.
3. Implement the minimal fix: prefix-stable prompt construction and/or
   explicit cache hints where the backend supports them.
4. Re-measure. Acceptance: MCP round time at parity with CLI (within
   ~20%) on the same diff.

## Non-goals

- Changing review semantics, pass structure, or verdict logic.
- Backend provider changes (that is a hermes config concern, not forge).
- Re-enabling the MCP path on hermes profiles (architect's call, via
  kanban, after verification).

## Constraints (forge repo rules apply)

- English docs, ASCII-only code.
- Work through the GSD workflow (`/gsd-quick` or `/gsd-debug`), worktree +
  fix/ branch, no direct main edits.
- 3-cycle clean review before commit, per repo rules.
- Evidence files under `.planning/evidence/` are immutable once written
  (Fleet Law S2b).

## Verification commands

```bash
# baseline measurement on a known diff (any small hermes worktree diff works)
cd <worktree> && git add -A
time code-forge review            # CLI path baseline
# MCP path: invoke forge_review from a hermes session, capture wall time
# plus per-pass backend timings from the forge progress log.
```

## Unblock protocol

When acceptance is met on Z66: commit on a fix/ branch, then notify the
architect session. The architect re-enables the MCP path by kanban comment
to the reviewer and coder profiles. The SOUL.md entries in
`profiles/coder/SOUL.md` and `profiles/reviewer/SOUL.md` (section
"forge review 必选其一", dated 2026-08-20) must be updated at that time.
