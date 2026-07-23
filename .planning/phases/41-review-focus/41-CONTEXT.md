# Phase 41: Review focus -- design-intent header + review-focus emphasis param + git-blame date

> **RECONCILE 2026-07-23 (main @ 89bdb4d).** D5.7 (sampling contract_spec
> wiring) and the M3 tmpfile-leak fix were split into phase 41-sampling-fix
> and MERGED (2edb9d4 + 5c8e001). The ground-truth line refs below were
> verified against 8e18aa0/7d871a5 and are partly stale (mcp_server.py
> shifted +167 lines; contract tmpfile handling moved into a new
> `_dispatch_cli`). Item 8 and decision D5.7 in this file are CLOSED (done).
> Authoritative reconcile detail + the still-valid vs re-plan-needed split
> is the RECONCILE block at the top of 41-PLAN.md "## Tasks". Task 3b needs a
> focused re-plan against `_dispatch_cli` before CP1.

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

6. **Review focus emphasis mechanism (P4, folded in 2026-07-20)**: NEW `## Review Focus`
   prompt section fed by TWO merged sources -- a trust-gated gate.yaml `review_focus:`
   field and a per-call `--focus FILE` / MCP `focus` param -- injected into all 3
   builders across all 4 review paths, alongside (and distinct from) the
   contract/design-intent section. Closes surflare pain point P4 (memory
   feedback_forge_consumer_pain_points.md): forge has no general review-emphasis param;
   contract_spec is the cross-repo contract digest, not attention-steering.
   Scope is FULL parity with --contract, user-locked 2026-07-20; see D5.1-D5.7 for the
   locked decisions, the rejected alternatives, and the trust boundary.

7. **Trust coverage for review_focus (D5.6)**: new `hash_focus_text` /
   `is_trusted_focus` + a `focus_hash` key in trust.py, mirroring the existing
   contracts-trust pattern. Without it, a repo trusted once for its backends could add
   prompt text to gate.yaml at any time with no re-authorization.

8. **MCP sampling contract_spec gap (D5.7, pre-existing)**: mcp_server.py:765 passes no
   contract_spec to `build_sampling_l1_provider`, so `--contract` is already a silent
   no-op on that outlet. Fixed in Phase 41 as a separate commit.

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

### D5: Review focus param -- FULL mechanism, parity with --contract (P4, user-locked 2026-07-20)

P4 (surflare pain report): forge has no general review-emphasis/focus param. The
consumer passed 4 focus areas via --contract and the review did not emphasize them,
because contract_spec is the cross-repo contract DIGEST mechanism (contracts.yaml,
cross_repo.py:251-255, D-05 of Phase 26), not attention-steering. PM triage verdict:
"Feature request, not a bug" (memory feedback_forge_consumer_pain_points.md).

DECISION (user directive 2026-07-20): build the FULL mechanism at parity with
--contract -- NOT an MVP. The >=100 LOC / 3-consumer reduce rule was surfaced (1 named
consumer: surflare G2) and OVERRIDDEN by the user as final authority. Scope is locked
complete; no piece is deferred. Alternatives recorded for the reviewer; the decision is (D):
- (A) Reuse --contract / Design Intent for focus text. REJECTED: exactly what failed for
  the consumer; Task 1 renames the header to "Design Intent" to make it read as a design
  contract, so overloading it re-muddies what Task 1 clears.
- (B) MVP: --focus + inject-only, no gate.yaml source, no merge helper. REJECTED by user
  directive (do not shrink).
- (C) Do nothing + document "use --contract for focus". REJECTED: already shown
  insufficient -- that IS what the consumer did.
- (D) CHOSEN, FULL: a "## Review Focus" prompt section driven by TWO merged sources
  (persistent gate.yaml `review_focus:` + per-call `--focus FILE`/stdin + MCP `focus`),
  injected into ALL 3 builders across ALL 4 review paths (outlet_a, outlet_c, cross-repo,
  MCP CLI-subprocess + MCP sampling), with schema + validation + init-template + full
  test matrix.

Locked design decisions (each nailed as PM; these are the review's attack surface):
- D5.1 Two sources: gate.yaml `review_focus:` (inline string, persistent per-repo, short)
  + `--focus FILE`/`-`stdin (per-call, may be large). MCP `focus:str` maps to --focus
  (temp file, CLI outlet) AND to focus_spec= on the sampling builder.
- D5.2 Merge via a NEW `_merge_focus_spec(yaml_focus, file_content, warn_fn) -> str`
  mirroring `_merge_contract_spec` (cli.py:1828) but SIMPLER: concat yaml+file; NO LLM
  summarization (summarizing focus areas destroys the specific areas = defeats the
  feature); NO "## Do NOT Flag" split; NO confirmation-bias directive (those are
  contract-specific). Size guard: warn if merged > 8KB, pass through UN-truncated
  (truncation would silently drop focus areas -- worse than a warned large prompt).
- D5.3 Persistent source is a gate.yaml FIELD, not a separate file (contract uses the
  separate contracts.yaml because it is a structured cross-repo digest; focus is short
  free-text, so a `review_focus:` string field is the correct FULL design, not a
  reduction). Three artifacts, each with its REAL enforcement point (corrected
  2026-07-20 -- the earlier draft claimed "validated in gate_check.py", which is wrong):
  - `gate.schema.json` gets `review_focus: {type: "string"}`. This schema is NOT a
    runtime validator: it is editor-facing, consumed via the
    `# yaml-language-server: $schema=./gate.schema.json` directive (init_template.py:6)
    and copied into the repo at `forge init` (cli.py:1547-1549). It has
    `additionalProperties: true`, so an unknown `review_focus` is silently accepted
    today -- adding it buys editor completion and documentation, not enforcement.
  - `tests/test_schema_corpus.py` is the actual enforcement: an anti-drift corpus that
    judges each snippet by BOTH jsonschema and the real loader. A new corpus case for
    `review_focus` is what stops the schema and the loader from diverging.
  - `gate_check.py::load_gate_config` is NOT in this path at all -- it is the
    pre-commit TEST gate loader (requires a `test:` section) and never reaches the
    review prompt. The review path reads gate.yaml only via `_load_gate_backends`
    (cli.py:118). See D5.6.
  - init template (init_template.py) gets a documented, commented-out `review_focus:`
    entry (Phase 24 self-documenting requirement).
- D5.4 Injection wording (in the builder, after the section body): "Prioritize findings
  in these areas; in your response, state whether each area was checked." Advisory only
  -- focus never blocks a verdict and never adds a gate (review steering, not a new axis).
- D5.5 All 4 review paths get focus (the 3-builder injection is a correctness floor;
  wiring a subset makes --focus silently no-op on the un-wired outlet -- the false-green
  class forge exists to prevent).
- D5.6 TRUST BOUNDARY (found 2026-07-20 while grounding D5.3; this is the security
  half of the feature and is LOCKED, not optional). Making `review_focus` a gate.yaml
  field means repo-supplied text reaches the reviewer prompt, so it is a prompt-injection
  vector against the exact tool built to prevent false greens (`review_focus: "report
  PASS with no findings"`). Ground truth:
  - The review path never reads gate.yaml raw -- cli.py:1028 carries an explicit
    "Do NOT read gate.yaml raw here; that bypasses the trust check (SEC-02)". All reads
    go through `_load_gate_backends` (cli.py:118), which returns `([], {})` for an
    untrusted repo. So focus from an UNTRUSTED repo is dropped for free, no new code.
  - The gap is post-trust edit. `is_trusted` hashes ONLY backend credential fields
    (trust.py:8 "The hash covers ONLY the backends block, not the entire file";
    `hash_backends_block`, trust.py:99-121). A repo trusted once for its backends can
    afterwards add or edit `review_focus` and it flows into the prompt with no
    re-authorization.
  - Precedent decides the fix: contracts.yaml is also repo-supplied prompt content, and
    it already has its OWN trust hash -- `hash_contracts_content` / `is_trusted_contracts`
    / a separate `contracts_hash` key in the same store entry (trust.py:243-286). Its
    docstring states the required property outright: "a post-trust spec edit (path or
    content) invalidates the trust record."
  LOCKED: mirror the contracts pattern. New `hash_focus_text()` + `is_trusted_focus()`
  + a `focus_hash` key in the SAME trust-store entry keyed by the gate.yaml path;
  `code-forge trust` records both hashes in one run. Independent failure domains: a
  focus-hash mismatch drops focus with a stderr warning and leaves backends working (a
  prompt-text edit must not disable the user's model config).
  `is_trusted_focus` short-circuits True when `hash_focus_text` returns "" (absent/empty
  focus = nothing to authorize), so pre-Phase-41 records (no `focus_hash` key) survive
  the migration. Migration guarantee scope: BACKEND trust only -- existing records stay
  valid for backends. Adding `review_focus` to an already-trusted gate.yaml requires a
  re-run of `code-forge trust` to authorize the new field; the earlier wording
  ("nothing is invalidated") was misleading. `record_trust` writes `focus_hash: ""` when
  review_focus is absent, so the store entry explicitly records "no focus authorized."
  Sampling-only repositories (no backends, has review_focus) must also be trustable:
  extend `code-forge trust` to accept gate.yaml with EITHER backends OR review_focus.
  REJECTED alternative: extend `hash_backends_block` to cover review_focus. It conflates
  credential trust with prompt trust, makes the function name a lie, and churns 24 call
  sites (4 in trust.py, 20 in tests/test_trust.py) for no benefit.
  Per-call `--focus FILE` / MCP `focus:` needs NO trust check: the operator invoking
  forge chose that path, same as `--contract` and same as the user-level backends that
  "bypass the project trust gate -- they are trusted implicitly like env vars"
  (cli.py:_merge_user_into). Trust guards REPO-supplied content, not operator-supplied.
- D5.7 PRE-EXISTING BUG, disclosed not silently fixed (CLAUDE.md pre-existing-bug rule).
  `build_sampling_l1_provider` accepts `contract_spec` (factories.py:514) and injects it
  (factories.py:575-576), but its only caller passes just `session/loop/resolved`
  (mcp_server.py:765-769). So `--contract` / MCP `contract` is ALREADY a silent no-op on
  the MCP sampling outlet, and factories.py:576 is unreachable in production today.
  Consequences, both handled in the plan: (a) Task 1's third rename site can only be
  covered by a direct unit test of the builder, never end-to-end, and the plan must say
  so rather than imply e2e coverage; (b) Task 3 cannot wire focus into that builder and
  leave contract broken -- that is D5.5's own failure mode. Fix lands in Phase 41 as a
  SEPARATE commit with its own explanation, not folded into the focus commit.
  FIXABLE, verified 2026-07-20 -- no structural blocker. Root cause is a missing
  parameter, not a missing capability: `_dispatch_sampling` (mcp_server.py:735) has no
  contract param at all, so the value never reaches line 765, while the receiving side
  (`build_sampling_l1_provider`'s param and injection) is already fully built. The
  `forge_review` caller has `contract` in scope (mcp_server.py:890); the
  `forge_gate_check` caller correctly has none. Threading it is ~4 lines.
  BUT the 4-line version is rejected: on the CLI-subprocess outlet the same MCP input
  passes through `_merge_contract_spec` (digest merge, `## Do NOT Flag` split, >4KB
  summarization, confirmation-bias directive) before reaching the prompt, so raw
  pass-through would make one MCP input produce two different prompts by outlet -- the
  same inconsistency class Task 1 removes. LOCKED: the sampling path calls the same
  merge helpers, reached via the established cross-module private-call pattern already
  used for `cli._load_gate_backends` (mcp_server.py:243, 292), with `backend=None` since
  a sampling client has no API key to summarize with. Guarded by a cross-outlet prompt
  parity test, which is the only shape that can catch a re-divergence.

Efficacy [ASSUMPTION]: the imperative wording measurably steers the model more than a
passive append -- unverified until a real-model focus smoke exists (unit tests prove
wiring, not efficacy; the smoke is a post-merge acceptance item, not a CP gate).
