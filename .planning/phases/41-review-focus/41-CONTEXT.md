# Phase 41: Review focus -- design-intent header + review-focus emphasis param + git-blame date

## Goal

Improve review prompt quality: rename contract header for clarity, add date
to blame attribution, and update existing tests for the new behavior.

## Ground-truth (verified 2026-07-16 against main @ 7d871a5)

### Already implemented (no new code needed)

| Feature | Location | Evidence |
|---|---|---|
| --contract flag | cli.py:351 | argparse flag, resolved to contract_spec |
| Contract injection (3 sites, all live) | cli.py:780, factories.py:281, factories.py:576 | Same `## Contract Reference` header in ALL three live prompt builders: `_make_subagent_spawn` (outlet_c CLI), `build_l1_provider` (outlet_a + cross-repo), `build_sampling_l1_provider` (MCP sampling). Verified 2026-07-20 against main @ 8e18aa0. |
| git_blame() parser | git.py:358-456 | Porcelain parser, returns {line: {sha, author, subject}}. Does NOT parse committer-time/date. |
| LegacyRunner | legacy.py | AdvisoryRunner with blame attribution + SATD/commit intent classification |
| Blame attribution | legacy.py:230-245 | `git-blame: {author} {sha[:8]} {subject}` |
| Advisory pipeline | outlet_c.py:144-216 | advisory_runners parameter in run_outlet_c |
| format_summary advisory count | sarif.py:289 | IMPLEMENTED |

### Actual delta (what Phase 41 needs to build)

1. **Header rename (3 sites, not 1)**: "## Contract Reference" -> "## Design Intent"
   in ALL three live prompt builders -- cli.py:780 (_make_subagent_spawn, outlet_c CLI),
   factories.py:281 (build_l1_provider, outlet_a + cross-repo), factories.py:576
   (build_sampling_l1_provider, MCP sampling). Renaming only cli.py:780 (the original
   plan's scope) would leave the other two outlets emitting the old header --
   inconsistent prompt content by outlet. Discovered 2026-07-20 by grepping src.
   - PM rationale: "Design Intent" better communicates the purpose to the LLM
   - Trivial text change, but logic-bearing (prompt content affects review behavior)

2. **git.py parser change**: add committer-time parsing to git_blame() (git.py:358-456)
   - Current: parser ignores committer-time porcelain field entirely
   - Target: parse committer-time, convert unix timestamp to ISO date (YYYY-MM-DD),
     store as "date" key in blame_entry dict

3. **Blame format enhancement**: add date to blame attribution (legacy.py:230-245)
   - Current: `git-blame: {author} {sha[:8]} {subject}`
   - Target: `git-blame: {author} {sha[:8]} {date} {subject}`
   - Depends on git.py parser change (item 2)

4. **Update existing contract injection tests**: test_contract_wiring.py has
   14 occurrences of "Contract Reference" (assert lines, index calls,
   docstrings, assert messages). Task1 rename breaks all. Update to
   "Design Intent" via replace_all.

5. **Update existing blame format test**: test_legacy.py:261-279 already asserts
   blame format string. Update to include date field + add untracked-file
   degradation test.

6. **Review focus emphasis param (P4, folded in 2026-07-20)**: NEW `## Review Focus`
   prompt section, driven by a `--focus FILE` CLI flag and a `focus` MCP param,
   injected into all 3 builders alongside (and distinct from) the contract/design-intent
   section. Closes surflare pain point P4 (memory
   feedback_forge_consumer_pain_points.md): forge has no general review-emphasis param;
   contract_spec is the cross-repo contract digest, not attention-steering. MVP-scoped;
   see D5 for alternatives and the 1-consumer ceiling.

## Design decisions

### D1: Header rename is a prompt-only change
Changing "## Contract Reference" to "## Design Intent" at cli.py:780 affects
the LLM's interpretation of the contract content. No API surface change.

### D2: Date format in blame attribution
Use ISO date (YYYY-MM-DD) from git blame --porcelain committer-time field.
git_blame() does NOT currently parse committer-time (Task 2a adds this).
After Task 2a, the date will be available as a "date" key in blame_entry.
Format: `git-blame: {author} {sha[:8]} {date} {subject}`

### D3: Scope-fence = ADVISORY, never blocking (unchanged)
Existing design in legacy.py. No change needed.

### D4: No new eval gate required
Changes are prompt-text and format-string only. Known-answer tests sufficient.

### D5: Review focus param is a SEPARATE section, MVP-scoped (P4 fold-in 2026-07-20)

P4 (surflare pain report): forge has no general review-emphasis/focus param. The
consumer passed 4 focus areas via --contract and the review did not emphasize them,
because contract_spec is the cross-repo contract DIGEST mechanism (contracts.yaml,
cross_repo.py:251-255, D-05 of Phase 26), not attention-steering. PM triage verdict:
"Feature request, not a bug" (memory feedback_forge_consumer_pain_points.md).

Alternatives (per "list alternatives before implementing"):
- (A) Reuse --contract / Design Intent for focus text. REJECTED: this is exactly what
  failed for the consumer; and Task 1 renames the header to "Design Intent" precisely
  to make it read as a design contract, so overloading it re-muddies what Task 1 clears.
- (B) Full parallel to --contract: flag + gate.yaml source + merge/summarize helper +
  contracts.yaml-style config. REJECTED as over-built: contract's full plumbing
  (resolve_contract_specs, _merge_contract_spec, >4KB summarization) exists because a
  contract has a yaml digest source and can be large; a per-call focus string has
  neither. 1 named consumer does not justify that surface (the >=100 LOC / 3-consumer
  rule).
- (C) Do nothing + document "use --contract for focus". REJECTED: already shown
  insufficient -- that IS what the consumer did.
- (D) CHOSEN, MVP: a new "## Review Focus" section with imperative wording, driven by
  `--focus FILE` (reuse --contract's FILE/stdin read) and an MCP `focus` param, injected
  into all 3 live builders. NO gate.yaml source, NO merge/summarize helper -- focus is
  per-call only (that is the deliberate reduction vs contract's plumbing). The 3-builder
  injection is a correctness FLOOR, not gold-plating: wiring only 2 of 3 makes --focus
  silently no-op on the un-wired outlet -- the exact false-green class forge exists to
  prevent.

Ceiling: 1 named consumer (surflare G2) [FACT]. If no second consumer emerges, the
"emphasize + report coverage" prompt wording is the whole feature -- do not grow a
config surface for it. That imperative wording measurably steers the model more than a
passive append [ASSUMPTION]: unverified until a real-model focus run exists (a smoke,
not a unit test, closes this; unit tests prove the wiring, not the efficacy).
