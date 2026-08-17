# FORGE_LLM_TIMEOUT_S resolution contradiction, 2026-08-16

Found while building the startup banner for pain 4 (surflare report):
`effective_invoke_timeout_s` (llm_invoke.py:457) resolves
backend.timeout_s BEFORE the env var, so FORGE_LLM_TIMEOUT_S is
silently ignored whenever the backend config sets a timeout. Proven
live: opus-omniroute (user config, timeout_s=2400) + env=5400 -> the
banner shows 2400s and the env var never applies.

Contradicting evidence in the same codebase:
- llm_invoke.py:441 docstring: "honoring FORGE_LLM_TIMEOUT_S ...
  override takes effect"
- llm_invoke.py:80: "FORGE_LLM_TIMEOUT_S overrides per call"
- The RC=6 breaker message tells the user to "raise FORGE_LLM_TIMEOUT_S
  or switch backend" -- a lever that is dead on backends with
  timeout_s set.

Open question for the surflare session: R6 ran ~90 min on
FORGE_LLM_TIMEOUT_S=5400 with deepseek-direct, which only fits the
resolution chain if deepseek-direct's backend.timeout_s was unset
(env path, but then the API cap of 600s should have applied and
didn't). Ask whether R6 also edited the backend config timeout.

ANSWERED 2026-08-16 (surflare reply /tmp/draft_r6_mystery_reply_20260816.txt):
- R6 did NOT edit backend config; deepseek-direct with gate.yaml
  timeout_s=2400 was used.
- The "~90 min" premise was wrong: R6 actually ran ~38 min
  (03:46->04:24). Death cause was the 900s SILENCE detector, not the
  read deadline -- "went silent for 900s mid-response" x5 -> RC=6.
  The 2400s backend deadline never fired, so R6's data cannot
  distinguish whether env 5400 was overridden; 5400 was never
  actually tested.
- "exceeded total read deadline" evidence lives in R5 (01:17 log),
  not R6. R6's RC=6 advice ("raise FORGE_LLM_TIMEOUT_S") was
  misleading: the bottleneck was v4-flash CN-afternoon stream stalls
  under the 900s silence bound; switching to v4-pro (R7f) fixed it.
- No 600s API cap hit in R6 logs.
- Implication: the silence threshold (900s) is a separate hard bound
  NOT adjustable via FORGE_LLM_TIMEOUT_S; extending env coverage to
  the silence threshold would be a new requirement, out of pain 4's
  scope. The b134b39 banner's "ignored: backend timeout wins" remains
  a correct description of the R6 configuration even though the
  deadline chain was a bystander in R6's actual failure.

Proposed fix (own branch, own review): reorder the chain so an
explicit env override beats backend.timeout_s (caller-supplied
timeout_s stays on top), and decide whether env values are exempt
from the type-based cap (an explicit user override should not be
silently capped). The banner's "(ignored: backend timeout wins)" note
stays either way -- it is the diagnosis, the reorder is the cure.

Banner side (fix/silent-exit-visible) already ships the honest note
in `_banner_timeout_note` (cli.py), so the misleading "override"
claim is gone regardless of the reorder decision.
