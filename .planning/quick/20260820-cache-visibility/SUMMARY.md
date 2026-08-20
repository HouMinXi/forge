---
status: complete
completed: 2026-08-20
---

# Quick Task Summary: cache-token visibility

Branch: fix/cache-token-visibility @ 299353e (from origin/main 0ae7cd5)
Deliverable: 11 files, +338/-11, full suite 3466 passed / 0 failed.

- Usage.cached_input_tokens extracted through one shared null-guarded
  helper (_cached_tokens_from) at 6 construction sites, covering the
  three API dialects including DeepSeek's flat prompt_cache_hit_tokens.
- Visible in: per-pass progress line "(N cached)", state.json cost
  total_cached_tokens + per_pass cached, SARIF tokenCost cachedTokens,
  CLI cost summary "+N cached".
- Review: 3 rounds (trio -> fix-verify clean -> adversarial found
  DeepSeek dialect gap -> fixed + injection-proven). Record:
  .planning/reviews/cache-visibility-20260820.txt
- Bug-injection proven at 6 sites. Context: charter
  .planning/charter_mcp_prompt_caching.md + evidence
  .planning/evidence/mcp_prompt_caching_2026-08-20/.
- NOT merged to main (no auto-merge rule); worktree
  .worktrees/cache-visibility kept for user decision.
- Out of scope (cut with evidence): prefix-stable prompt reorder
  (~3% ceiling under concurrent passes, probe_mimo_concurrent_prefix).
