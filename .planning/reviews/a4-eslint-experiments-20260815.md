# A4 cache-replay mechanism + eslint leading-slash repro -- evidence, 2026-08-15

Two experiments from the 43.1 charter backlog, both closed with data.

## A4 (charter item 4): cache-replay mechanism -- IDENTIFIED

Charter question: OmniRoute-side response caching, a forge-side memoization
keyed on (source_hash, axis), or something else? Answer: **OmniRoute-side
semantic cache on the request path, keyed on the full request payload, not a
forge-side memoization.**

### Evidence chain

1. Prior runs (llm-parse-retry-findings.md, 2026-08-15): byte-identical
   91/60/91-token replays at 0.2-0.3s across two different backends
   (gemini-omniroute, sn-deepseek-flash via OmniRoute), unaffected by
   clearing forge-side receipts/state.json, unaffected by a gate.yaml
   temperature change, absent on deepseek-direct (R7 and R8 differ).

2. OmniRoute source (Z66 checkout, `~/code/OmniRoute/src/lib/semanticCache.ts`):
   - "Caches LLM responses (temperature=0) to reduce cost and latency."
   - "Cache key = SHA-256(model + normalized messages + temperature + top_p)"
     plus an apiKeyId namespace prefix (per-key isolation).
   - Two-tier: in-memory LRU (default TTL 30 min, 50 entries / 2 MB) +
     SQLite. Memory hits increment cache_metrics but NOT the SQLite
     hit_count column.
   - Bypass header documented in source: `X-OmniRoute-No-Cache: true`.
   - `isCacheableForRead/Write` require an explicit numeric temperature 0.

3. Forge sends exactly that shape: llm_invoke.py defaults deepseek-class
   backends to temperature 0.0, so every forge pass through the OmniRoute
   gateway is a cacheable request.

4. Live gateway DB (X500, container `omniroute`, volume
   `systemd-omniroute/_data/storage.sqlite`):
   - `semantic_cache`: 5099 rows; 166 rows written 2026-08-15 with
     hit_count=0 (memory-layer hits do not write SQLite hit_count).
   - `cache_metrics`: hits=3433, misses=10481, tokens_saved=90,744,151.
   - A forge call log from the review window
     (`/app/data/call_logs/2026-08-15/2026-08-15T09-16-48.769Z_...json`,
     apiKeyName=fleet-clients, model=deepseek-v4-flash, duration 81178ms)
     embeds the production handler source, which imports
     generateSignature / getCachedResponse / setCachedResponse /
     isCacheableForRead / isCacheableForWrite from `@/lib/semanticCache`
     and saveIdempotency from `@/lib/idempotencyLayer`. The production
     image carries a newer handler than the Z66 checkout (no such
     consumer exists in the local tree, and /app/src in the container
     has no git), which is why a source grep alone could not find it.

5. Mechanism therefore explains both halves of charter item 4: the
   cross-run replays and the 2026-08-01 intra-run cycle-2-3 replays
   (same diff, same pass, same backend -> same key -> memory-layer hit).

### Consequences

- Consecutive-clean-rounds trust is real risk ONLY on gateway-routed
  backends. A converged verdict whose rounds all rode the gateway may be
  one real review plus replays.
- Working bypasses, cheapest first: (a) `X-OmniRoute-No-Cache: true`
  header on the backend request (documented OmniRoute knob; forge has no
  header hook today -- could be a backend-config field); (b)
  deepseek-direct or any non-gateway route (proven: R7 != R8); (c) vary
  the prompt (--focus does not enter the L1 prompt, so it does NOT vary
  the key -- measured in the earlier batch).
- The gate.yaml `temperature: 0.1` experiment failing to break the
  replay is now explained: if the config value did not reach
  llm_invoke's body builder, effective temperature stayed 0.0 and the
  key stayed identical. Worth a separate check of backend config
  temperature plumbing, but not part of A4.

## eslint leading-slash (defects #3): NOT REPRODUCIBLE on current main

The 2026-08-04 defect: L0 eslint received
`home/houminxi/code/OmniRoute/.claude/worktrees/repro-8779/...` -- an
absolute path minus the root slash -- and exited 2, silently killing the
whole L0 lint layer for that file.

### Reproductions attempted (2026-08-15, main @ 835115d)

1. Minimal-repo trace: fresh git repo, staged NEW .ts file, forged the
   exact chain (get_changed_files(diff) -> Path -> run_tool with
   OmniRoute's tools.yaml eslint entry). argv handed to subprocess was
   `['/home/houminxi/.npm-global/bin/eslint', '--format', 'json',
   'tests/unit/zzz-new.test.ts']` -- a correct relative path.

2. Real-path run: OmniRoute linked worktree
   `.claude/worktrees/eslint-repro-20260815`, staged new TS file, real
   StateMachine constructed with the real registry and the real eslint
   binary, `_run_l0_phase()` executed (no LLM, no stubs on the runner):
   - clean file -> no findings, no infra_errors (eslint ran fine);
   - known-positive syntax-error payload -> eslint finding produced,
     `finding.file` a correct absolute path. The check fires; the path
     is right.

3. Code archaeology: cli._paths, diff.get_changed_files,
   runner.run_tool, and the MCP forge_review cli_args are byte-identical
   between the 2026-08-04 HEAD (1fb3eea) and current main. No
   `lstrip("/")`, `relative_to("/")`, or root-relative conversion exists
   anywhere on the source_files -> runner chain, current or historical
   (the only lstrip sites are the factories coverage normalizers, which
   normalize both sides of a comparison and never reach the lint argv).
   cross_repo.derive_source_files does produce absolute paths, but WITH
   the root slash (`Path(repo_path / f).resolve()`), so it does not
   produce the observed shape either.

### Conclusion

Current main does not reproduce the defect on the 2026-08-04 scenario.
Most plausible residual explanation: the 2026-08-04 run rode an installed
wheel that was behind the main tree (the f97c661 path-normalization work
for L0 delta filtering, 2026-07-18, shows this subsystem was being fixed
in that era), or a code path no longer present. Action: mark defects #3
as not-reproducible-on-main; if it resurfaces, capture the installed
package version (`code-forge --version`) and whether the run was
cross-repo mode before filing again.
