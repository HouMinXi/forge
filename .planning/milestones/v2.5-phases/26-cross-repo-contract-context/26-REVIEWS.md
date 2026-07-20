# Phase 26: Cross-Repo Contract Context -- Cross-Model Review

**Date:** 2026-06-21
**Models:** kn (Kimi K2.6), gm (Gemini 3.1 Pro), mm (MiniMax M3), mimo-pro (MiMo v2.5 Pro), ds (DeepSeek V4 Pro)

## Convergence Summary

| Finding | kn | gm | mm | mimo | ds | Conv. | Action |
|---------|----|----|-----|------|-----|-------|--------|
| Symlink guard containment boundary wrong | BLOCKER | BLOCKER | HIGH | HIGH | LOW x2 | 5/5 | FIX |
| PermissionError not caught per-spec | -- | MEDIUM | MEDIUM | -- | -- | 2/5 | FIX |
| Plan 26-03 missing depends_on 26-02 | MEDIUM | -- | -- | -- | -- | 1/5 + checker | FIX |
| Missing backend= in cross-repo call | -- | -- | -- | HIGH | -- | 1/5 | FIX |
| SC-1 no end-to-end test | LOW | -- | MEDIUM | -- | -- | 2/5 | FIX |
| Trust deviation from D-13 intent | HIGH | -- | MEDIUM | -- | -- | 2/5 | DISCUSS |
| DoS: full-file read before size check | MEDIUM | -- | -- | -- | -- | 1/5 | FIX |
| bytes vs str in summarization | -- | HIGH | -- | -- | -- | 1/5 | FIX |
| Cache key 8 vs 12 hex chars | -- | -- | -- | HIGH | -- | 1/5 | FIX |
| Outlet C / test_prompt_section_order scope | LOW | -- | -- | -- | LOW | 2/5 | NOTE |
| Trust store key collision / hash naming | -- | -- | -- | MEDIUM | LOW | 2/5 | NOTE |
| SC-3 test missing capsys | -- | -- | -- | MEDIUM | -- | 1/5 | FIX |
| Cache orphan cleanup missing | -- | -- | -- | LOW | -- | 1/5 | NOTE |
| .gitignore for cache dir | -- | LOW | -- | -- | -- | 1/5 | FIX |
| D-06 violation (primary-only inject) | -- | BLOCKER | -- | -- | -- | 1/5 | DISCUSS |
| Trust CLI: no content validation at trust time | -- | -- | -- | -- | MEDIUM | 1/5 | SUBSUMED (DF-1 GAP1) |
| llm_invoke expected_keys mismatch for prose | -- | -- | -- | -- | MEDIUM | 1/5 | FIX |
| Trust revoke/status missing contracts | -- | -- | -- | -- | MEDIUM | 1/5 | SUBSUMED (DF-1 GAP2) |

## Findings to Fix (ordered by convergence then severity)

### CF-1 [5/5 CONVERGENT] Symlink guard containment boundary
**kn BLOCKER, gm BLOCKER, mm HIGH, mimo HIGH, ds LOW x2**

`_symlink_guard_passes(spec_abs, repo_path)` checks containment inside
`cwd.parent`, not `cwd`. With `cwd=repo_path`, the check becomes "is
spec_abs within repo_path.parent" -- inverts the intended boundary.
Legitimate nested specs (the primary use case) may be rejected while
specs outside the repo root may pass.

ds adds two supplementary angles: (1) importing private `_symlink_guard_passes`
across module boundaries is fragile (rename or add public wrapper); (2) threat
model T-26-05 says "outside repo root" but actual check is "outside repo parent"
-- description is misleading (correct for cross-repo sibling use case, but
documented wrong).

**Fix:** Write a dedicated `_is_within_repo(path, repo_root)` containment
check: `repo_root.resolve() in path.resolve().parents`. Do not reuse
`_symlink_guard_passes`. Correct T-26-05 description to say "outside repo
parent directory."

### CF-2 [2/4] PermissionError not caught per-spec
**gm MEDIUM, mm MEDIUM**

An unreadable spec raises OSError that propagates to the outer
try/except, aborting the entire digest (dropping all other valid specs).
D-07 says graceful per-spec degradation.

**Fix:** Wrap the per-spec read in `try/except OSError`. Warn + skip the
bad spec, continue the loop.

### CF-3 [2/4] SC-1 test has no end-to-end assertion
**kn LOW, mm MEDIUM**

SC-1 is proven by fragmented tests across Plans 02 and 03 (loader test +
prompt injection test + cross-repo test) but no single test exercises the
full chain: contracts.yaml -> trust -> load -> inject -> L1 prompt.

**Fix:** Add one end-to-end SC-1 test in Plan 03 that wires
load_contract_digest through build_l1_provider with a prompt-capturing
mock, asserting both diff and spec content appear in documented order.

### SF-1 [1/4] Missing backend= in cross-repo call
**mimo HIGH**

Plan 26-03 Task 1 calls `load_contract_digest(yaml, path)` without
`backend=backend`. Large specs get summarized with default backend, not
the user's configured L1 backend (D-04 violation).

**Fix:** Pass `backend=backend` in the cross-repo call.

### SF-2 [1/4] bytes vs str in summarization
**gm HIGH**

`_read_spec_content` may return bytes. Passing bytes to `_summarize_spec`
(which formats it into a prompt string) corrupts the LLM context.

**Fix:** Decode content to str before summarization.

### SF-3 [1/4] Cache key 8 vs 12 hex chars
**mimo HIGH**

Plan uses `hexdigest()[:8]` (32 bits) but conventions_resolver.py uses
`hexdigest()[:12]`. Inconsistency with established project pattern.

**Fix:** Use `hexdigest()[:12]` to match.

### SF-4 [1/4] DoS: full-file read before size check
**kn MEDIUM**

The loader reads the entire file into memory before the max_raw_size
gate. A trusted but multi-GB spec can exhaust memory.

**Fix:** `stat()` the file first; reject if size > hard upper bound
(e.g., 10x max_raw_size) before reading.

### SF-5 [1/4] SC-3 test missing capsys fixture
**mimo MEDIUM**

SC-3 test asserts "no warning to stderr" but function signature lacks
`capsys`, so stderr is not actually captured.

**Fix:** Add `capsys` parameter.

### SF-6 [1/4] .gitignore for cache directory
**gm LOW**

No plan modifies `.gitignore` to exclude `.code-forge/cache/`.

**Fix:** Add to scope or document as manual step.

### SF-7 [1/4] Plan 26-03 depends_on missing 26-02
**kn MEDIUM** (also caught by plan-checker in iteration 1)

Plan 03 passes `contract_spec=` to `build_l1_provider` which requires
Plan 02 to have added the kwarg. Safe in practice (GSD executes by plan
number within a wave) but dependency graph is incomplete.

**Fix:** Add `26-02` to Plan 03 depends_on.

### SF-8 [SUBSUMED by DF-1 GAP 1] Trust CLI: no content validation
**ds MEDIUM -- folded into DF-1 resolution**

Absorbed: contracts trust hash now covers resolved spec file contents,
which requires parsing contracts.yaml to discover spec paths. Content
validation happens as a side effect of the hash computation.

### SF-9 [1/5] llm_invoke expected_keys mismatch for prose summarization
**ds MEDIUM -- verified real (llm_invoke.py:163)**

`_summarize_spec` calls `llm_invoke(prompt, backend=backend,
expected_keys=None)`. When `expected_keys=None`, API backends use
`_REVIEW_ENVELOPE_KEYS` for JSON extraction, but summarization returns
free-form prose. JSON extractor may fail on API backends (CLI backends
unaffected). Failure is graceful (caught by outer try/except, returns "")
but large-spec summarization silently degrades.

**Fix:** Pass `expected_keys=frozenset({"summary"})` and request JSON
`{"summary": "..."}` output. frozenset() rejects all dicts on API
backends (obj.keys() & frozenset() is empty at llm_invoke.py:172).

### SF-10 [SUBSUMED by DF-1 GAP 2] Trust revoke/status missing contracts
**ds MEDIUM -- folded into DF-1 resolution**

Absorbed: DF-1 requires revoke/status to cover contracts alongside gate.
Independent hash storage creates an orphan if only gate is revocable.

## Findings to Discuss

### DF-1 [RESOLVED] Trust hash -- independent hashes, spec content coverage
**kn HIGH, mm MEDIUM -- adjudicated 2026-06-21**

Original D-13 said "same trust grant." Plan implemented separate
`contracts_hash` (researcher A4 rec). Two real gaps closed:
- GAP 1 (mm, security): hash covered manifest only, not resolved spec
  file contents. Post-trust spec edit bypassed re-approval. FIX: hash
  covers resolved spec paths + contents. (Subsumes ds SF-8.)
- GAP 2 (ds SF-10): independent storage requires revoke/status to also
  cover contracts, else revoking gate trust orphans contracts trust.
D-13 amended in 26-CONTEXT.md.

### DF-2 [RESOLVED] D-06 primary-only injection
**gm BLOCKER -- adjudicated 2026-06-21**

gm BLOCKER was literally correct (D-06 said "not only the primary").
But sibling L1 is a no-op lambda (cross_repo.py:313); injecting into
siblings is dead code. Resolution: keep primary-only injection, rewrite
D-06 to document the real constraint ("inject to threads running L1;
currently only primary runs L1"). Add one test asserting siblings receive
no contract_spec. D-06 amended in 26-CONTEXT.md.

## Findings Noted (no action required)

- Outlet C missing Blast Radius section / test_prompt_section_order scope
  (kn LOW, ds LOW): existing behavior, not introduced by Phase 26. ds adds
  that the test needs explicit outlet scoping.
- Trust store key collision (mimo MEDIUM, ds LOW): keys disambiguate by
  realpath, collision impossible. Naming moot after D-13 amendment.
- Cache orphan cleanup (mimo LOW): deferred to future enhancement,
  matches plan's stated scope

---

*5 models, 18 distinct findings (2 subsumed by DF-1), 5/5 convergent on
symlink guard boundary. DF-1 and DF-2 resolved; D-06 and D-13 amended in
26-CONTEXT.md. Stale references cleared from RESEARCH, PLAN 01/03.*
