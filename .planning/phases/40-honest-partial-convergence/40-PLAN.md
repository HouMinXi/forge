# Phase 40: Honest partial results + convergence (mechanical half)

## Goal

Make partial review results (per-pass timeouts, schema failures, large-diff
chunking) honest and visible: a round that completed 2 of 3 passes must never
silently present as a full PASS or a full FAIL with no detail. The founding
principle applies literally: "a green verdict is honest or declares what it did
not verify."

This phase covers the MECHANICAL half only. Convergence plateau and
prior-round memory are deferred to the post-Phase-44 semantic half (see
40-CONTEXT.md deferred section for the locked policy decision).

## Context

Phase 40 bundles three scope items from the dispatch schedule
(v2.8tail-v3-DISPATCH-SCHEDULE.md:495-503):

1. **F4 partial-SARIF / partial-verdict representation** -- when a pass fails
   mid-round, the receipt and summary must show which passes completed and
   which did not, not an opaque FAIL with no per-pass detail.
2. **P3 per-pass timeout/failure surfacing** -- the consumer pain point from
   a 2026-07-07 surflare dogfood: "adversarial 1800s timeout FAIL kills
   whole run (qodo+expert had good findings)". PM correction: findings
   survive (outlet_c.py:56-90 already accumulates); the gap is
   representation, not data loss.
3. **Large-diff summary/chunking** -- false-green trap #2: "large diff chokes
   backend, JSON parse error, no findings." No existing chunking code was
   found (grep confirmed 2026-07-15).

### Ground-truth verification (this planning session, 2026-07-15)

All code seams from 40-CONTEXT.md verified against live source at main @
cfade37:

| File:line | Claim | Status |
|---|---|---|
| outlet_c.py:56-90 | Findings survive pass timeout; INFRA marker appended | CONFIRMED |
| receipt.py:29-38 | `_split_by_pass` routes by "l1-<pass>-" prefix | CONFIRMED |
| receipt.py:58-134 | `write_receipts` writes per-pass receipts; no pass_status field | CONFIRMED (gap exists) |
| ledger.py:27-37 | TerminalState has no partial-shaped member | CONFIRMED |
| sarif.py:228-260 | `format_summary` shows aggregate counts, no per-pass detail | CONFIRMED (gap exists) |
| llm_invoke.py:77-79 | DEFAULT_TIMEOUT_S=1800, CLI_CAP=300, API_CAP=600 | CONFIRMED |
| machine.py:515-529 | Verdict computed from confirmed count; finding list intact | CONFIRMED |
| state.py:29-36 | Verdict enum: PASS/FAIL/ESCALATED/PENDING/DELEGATED/UNRELIABLE | CONFIRMED |

### Existing behavior trace (outlet_c.py timeout path)

When `spawn_fn(pass_name, diff)` raises (timeout, connection error, etc.):
1. Exception caught at line 63
2. StateFinding created: id="l1-<pass>-spawn-fail", source="INFRA",
   disposition=CONFIRMED
3. Appended to same `findings` list
4. `continue` to next pass name

Result: a 3-pass round where pass 2 timed out produces findings from passes
1 and 3 PLUS one INFRA marker from pass 2. All data survives. The receipt for
pass 2 (receipt.py `_split_by_pass`) correctly routes the INFRA finding to
that pass's receipt file (prefix match on "l1-<pass2>-spawn-fail").

The gap: the receipt has no explicit "this pass timed out" status field; the
INFRA finding is just one more finding. format_summary shows aggregate counts
only. A user reading the output sees "FAIL findings=5 confirmed=3" with no
indication that 1 of 3 passes never completed.

### Schema validation failure path

Similarly, when `validate_reviewer_json(raw)` raises ValueError (line 80):
StateFinding with id="l1-<pass>-schema-fail", source="INFRA",
disposition=CONFIRMED. Same routing to receipt. Same representation gap.

## Review History

### R0 Internal self-review (PBR 8-pass, 2026-07-15)

0B/1H/3M/0L after fixes:

HIGH (P1): factories.py missing from Task 1 -- `build_l1_provider` and
`build_sampling_l1_provider` both create closures returning 4-tuples that
become 5-tuples. Fix: added factories.py to files list + steps 5-6
detailing every site (stub lambda, empty-diff returns, inner closure
returns, machine.py unpacking at line 682, _run_l1_phase return at line
701, write_receipts call at line 834).

MEDIUM (Implementer): Task 3 chunking lacked diff parser specification.
Fix: added `diff --git` header parsing with `--- a/`/`+++ b/` fallback.

MEDIUM (Implementer): Task 2 step 4 underspecified -- format_summary
caller threading. Fix: expanded to explicit cli.py:111 analysis with
Option A (State field) vs Option B (separate variable), recommended B.

MEDIUM (Tester): FORGE_DIFF_CHUNK_THRESHOLD_KB malformed env var not
handled. Fix: added validation + warning + fallback to Task 3 step 3.

Self-review CLEAN after fixes. Proceeding to external aicc review.

### R0b Structural consistency fixes (2026-07-15, pre-R1)

3 structural issues found during cross-reference audit:
- derive_pass_outcomes placed in receipt.py but machine.py also needs
  it -> moved to state.py (alongside PassOutcome)
- Task 1 step 4 and step 5 conflicted (who calls derive_pass_outcomes?)
  -> resolved: derive at consumption site (Option B). receipt.py and
  sarif.py both call derive_pass_outcomes independently on findings.
- Validation Architecture table bug-inject descriptions stale (still
  said "remove pass_status write") -> updated to "corrupt INFRA
  finding ID pattern"

No external review involved. Proceeding to CP1b.

### R2 External (ds/mimo-pro/gm, 2026-07-15; lc timeout, 0 bytes)

| Model | B | H | M | L |
|-------|---|---|---|---|
| DeepSeek | 0 | 0 | 2 | 5+obs |
| Mimo-Pro | 0 | 3 | 2 | 1 |
| Gemini | 0 | 3 | 0 | 0 |
| LongCat | Did not participate (0 bytes, API issue) | | | |

**R2 consolidated dispositions:**

| # | Sev | Finding | Source | Disposition |
|---|-----|---------|--------|-------------|
| R2-1 | HIGH | Wrong INFRA ID: factories.py uses invoke-fail not spawn-fail | gm | FIXED: derive_pass_outcomes now checks both spawn-fail AND invoke-fail patterns |
| R2-2 | HIGH | Missing incomplete-coverage pattern | gm | FIXED: added incomplete-coverage -> TIMEOUT mapping |
| R2-3 | HIGH | Empty findings -> {} -> falsely 3/3 completed | mimo | FIXED: empty findings returns empty dict; callers must check |
| R2-4 | HIGH | _run_chunk return type vs merge contradiction | mimo | FIXED: clarified _run_chunk returns per-chunk findings; merge applies dedup + init-and-downgrade |
| R2-5 | HIGH | pass_outcomes not in save_state/load_state | gm | SUPERSEDED by lc-R3-4: cli.py:96 loads from disk, not resume-only. FIXED via Option B (derive at consumption site, State field deleted). |
| R2-6 | MEDIUM | receipt.py missing PassOutcome import | ds | FIXED: explicit import instruction added to Task 1 step 4 |
| R2-7 | MEDIUM | sarif.py import contradiction | ds | FIXED: Task 1 step 5 now explicitly imports from state.py |
| R2-8 | MEDIUM | _persist_round_snapshot method name wrong | gm | FIXED: corrected to _execute_round (line 786) |
| R2-9 | MEDIUM | Hardcoded pass names in 3+ modules | ds | FIXED: _PASS_NAMES constant defined in state.py |
| R2-10 | MEDIUM | Chunk regex not anchored | mimo | ACKNOWLEDGED: add `^` anchor in implementation |
| R2-11 | MEDIUM | pass_outcomes threaded via State AND parameter | mimo | SUPERSEDED by lc-R3-4: Option B deleted State field; derive at consumption site. No dual path. |
| R2-12 | LOW | No logger specified for env var warning | ds | DEFERRED: implementation detail |
| R2-13 | LOW | _run_chunk crash behavior unspecified | ds | DEFERRED: add try/except in implementation |
| R2-14 | LOW | Fallback diff parser re-creates trap #2 | ds | DEFERRED: parser failure should abort, not silently pass |

After fixes: 0B/0H/0M remaining. Proceeding to R3 confirmation round.

### R3 Confirmation (ds/mimo-pro/gm, 2026-07-15; lc API issue)

| Model | B | H | M | L |
|-------|---|---|---|---|
| DeepSeek | 0 | 0 | 4 | 3 |
| Mimo-Pro | 0 | 3 | 4 | 0 |
| Gemini | 0 | 2 | 2 | 1 |

**R3 consolidated dispositions:**

| # | Sev | Finding | Source | Disposition |
|---|-----|---------|--------|-------------|
| R3-1 | HIGH | Empty findings -> passes=0/3 (clean run false) | gm | FIXED: _count_pass_outcomes returns (3,3) for empty dict |
| R3-2 | HIGH | Merge of excerpts/usage/duration unspecified | gm, ds | FIXED: explicit merge rules added to Task 3 step 6 |
| R3-3 | HIGH | _PASS_NAMES not yet in state.py | mimo | ACKNOWLEDGED: plan is prescriptive (will be created in Task 1), not descriptive (already exists). Clear from context. |
| R3-4 | HIGH | test_sarif.py regex breaks with passes= suffix | mimo | FIXED: test_sarif.py added to Task 2 files + regex update step |
| R3-5 | HIGH | _run_l1_phase returns 2-tuple not 4-tuple | mimo | ACKNOWLEDGED: plan correctly says "after _run_l1_phase call (line 797)" where l1_findings is available. No confusion. |
| R3-6 | MEDIUM | Diff split consumes delimiters (re.split) | gm | FIXED: changed to re.findall to preserve headers |
| R3-7 | MEDIUM | Chunk-order last-write-wins violates worst-case | gm | FIXED: derive_pass_outcomes processes all INFRA findings; TIMEOUT is the worst outcome, which is what last-write produces for mixed TIMEOUT+SCHEMA_FAIL |
| R3-8 | MEDIUM | Factories.py source="INFRA" unverified | ds | DISPROVED: grep confirmed source="INFRA" on all patterns |
| R3-9 | MEDIUM | Fallback parser failure -> trap #2 | ds | FIXED: added "on parse failure, log warning and fall through to un-chunked path" |
| R3-10 | MEDIUM | save_state/load_state not updated | mimo | SUPERSEDED by lc-R3-4: canonical emit path goes through disk reload. FIXED via Option B (derive from persisted findings). |
| R3-11 | MEDIUM | env var reading location unspecified | mimo | DEFERRED: implementation detail |
| R3-12 | LOW | _PASS_NAMES underscore prefix | ds | DEFERRED: Python convention, not a bug |
| R3-13 | LOW | Non-positive threshold values | gm | FIXED: added threshold <= 0 handling (always-chunk is valid) |

After fixes: 0B/0H/0M remaining. Proceeding to R4 final confirmation.

### R1 External (ds/mimo/gm, 2026-07-15; kimi key-exhausted, mm timeout)

| Model | B | H | M | L |
|-------|---|---|---|---|
| DeepSeek | 0 | 3 | 6 | 4 |
| Mimo | 0 | 4 | 4 | 2 |
| Gemini | 1 | 2 | ? | ? (truncated) |
| Kimi | API error (keys exhausted) | | | |
| mm | 0 bytes (still running at cutoff) | | | |

**R1 consolidated dispositions:**

| # | Sev | Finding | Source | Disposition |
|---|-----|---------|--------|-------------|
| R1-1 | BLOCKER | 4-tuple->5-tuple contract change too invasive (15+ files) | gm | FIXED: redesigned Task 1 to derive pass_outcomes from INFRA findings. No contract change. |
| R1-2 | HIGH | PassOutcome in receipt.py risks circular import | ds | FIXED: moved to state.py |
| R1-3 | HIGH | pass_outcomes has no delivery path to format_summary | ds, mimo | FIXED: Option B (derive at consumption site from persisted findings). No State field needed. |
| R1-4 | HIGH | Dedup contradiction (concat vs dedup) | ds, mimo | FIXED: Task 3 now specifies fingerprint dedup |
| R1-5 | HIGH | cross_repo.py:326 hardcoded 4-tuple lambda | mimo | DISPROVED: with revised approach (no contract change), this lambda is unaffected |
| R1-6 | HIGH | mcp_server.py not in files list | mimo | DISPROVED: with revised approach, mcp_server.py needs no changes |
| R1-7 | HIGH | L1Provider type alias not updated | mimo | DISPROVED: with revised approach, alias is unchanged |
| R1-8 | HIGH | Chunking nested loop should be extracted | mimo | FIXED: Task 3 now extracts _run_chunk helper |
| R1-9 | HIGH | Cross-file findings silently lost | ds | ACKNOWLEDGED: added to T2 mitigation and Known Gaps |
| R1-10 | MEDIUM | File-based chunking creates too many API calls | gm | DEFERRED: bin-packing is an optimization, not a correctness fix. Add to Known Gaps. |
| R1-11 | MEDIUM | Single large file still chokes backend | gm | DEFERRED: hunk-based fallback for oversized single files. Add to Known Gaps. |
| R1-12 | MEDIUM | pass_outcomes merge direction | ds | SUPERSEDED by lc-R3-3: derive is single authority with severity ordering; Task 3 step 7 deleted. |
| R1-13 | MEDIUM | Empty/binary chunks waste LLM calls | ds | FIXED: Task 3 step 5 specifies skip |
| R1-14 | MEDIUM | Missing test survey in Task 0 | ds, mimo | FIXED: Task 0 step 2 added |
| R1-15 | MEDIUM | Receipt consumers unverified | ds | FIXED: Task 0 step 3 added |
| R1-16 | MEDIUM | Missing worktree step | mimo | FIXED: Task 0a added |
| R1-17 | MEDIUM | Ledger decision left implicit | gm | FIXED: Known Gaps item 2 now explicitly states the decision + rationale |
| R1-18 | LOW | Threshold default rationale undocumented | ds | DEFERRED: add comment in implementation |
| R1-19 | LOW | TerminalState skip rationale implicit | ds | FIXED: Known Gaps item 2 expanded |
| R1-20 | LOW | test_sarif.py assertions need update for passes= suffix | mimo | ACKNOWLEDGED: add to Task 2 |

Convergence: 0 BLOCKER / 0 HIGH / 0 MEDIUM (all resolved or deferred) / 0 LOW
after fixes. kimi and mm did not participate (key exhaustion / timeout).

### lc-R3 (dropped review, discovered by PM audit 2026-07-15)

lc R3 response (/tmp/p40-r3-lc.md, 7148 bytes) landed at 05:19 -- AFTER
R4 prompts were built at 04:58. It was never read, never dispositioned,
and appears nowhere in the R3 table or the final report. lc's R4 CLEAN
does NOT cover it: R4 was a separate session whose changelog never
contained these findings. This is an S1 disclosure violation; the fix
is recording, not deletion.

PM arbitrated all 5 findings against live code (see dispatch
/tmp/draft_phase40_plan_revision_dispatch_20260715.txt).

| # | Sev | Finding | PM Verdict | Disposition |
|---|-----|---------|------------|-------------|
| lc-R3-1 | HIGH | Empty findings -> receipt/sarif contradiction | ALREADY RESOLVED (gm R3-1) | Docstring aligned to actual policy (empty -> all COMPLETED) |
| lc-R3-2 | HIGH | invoke-fail mislabeled TIMEOUT (ignores is_timeout) | CONFIRMED REAL | FIXED: derive_pass_outcomes consults is_timeout; ERROR for non-timeout; INCOMPLETE for coverage |
| lc-R3-4 | MEDIUM->ACCEPTANCE-BLOCKING | pass_outcomes not persisted through save/load_state; M1 unachievable via disk-reload path | CONFIRMED REAL | FIXED: Option B -- derive at consumption site; State field deleted |
| lc-R3-3 | MEDIUM | derive last-write vs Task 3 downgrade-only contradiction | CONFIRMED | FIXED: derive is single authority with severity ordering; Task 3 step 7 deleted |
| lc-R3-5 | LOW | _PASS_NAMES doesn't eliminate duplication (5 copies) | CONFIRMED | FIXED: receipt.py and outlet_c.py import _PASS_NAMES from state.py |

### R4 Final Confirmation (ds/mimo-pro/lc, 2026-07-15; gm retry)

| Model | B | H | M | L | Status |
|-------|---|---|---|---|--------|
| DeepSeek | 0 | 0 | 0 | 1 | 1 non-actionable LOW |
| Mimo-Pro | 0 | 0 | 0 | 0 | CLEAN |
| Gemini | 0 | 0 | 0 | 0 | CLEAN (retry with agy/gemini-3.1-pro-high) |
| LongCat | 0 | 0 | 0 | 0 | CLEAN (code-level verification) |

DeepSeek R4-1 LOW: threshold <=0 handling missing from plan text.
FIXED in plan (Task 3 step 3: "If <= 0, treat as always-chunk").

LongCat verified all code seams line-by-line against main @ cfade37:
outlet_c.py INFRA patterns, factories.py invoke-fail/incomplete-coverage,
receipt.py _split_by_pass routing, ledger.py TerminalState, sarif.py
format_summary, machine.py _execute_round/_run_l1_phase -- all CONFIRMED.

CONVERGED. Plan ready for execution.

## Must-Haves

- M1: A round where pass 2 of 3 timed out MUST show "2/3 passes completed"
  in both the receipt JSON and the CLI summary line.
- M2: Findings from completed passes MUST be surfaced (not suppressed) even
  when other passes failed.
- M3: The verdict MUST stay fail-closed: partial completion is never a PASS.
- M4: Large diffs MUST NOT silently produce zero findings (trap #2).
- M5: No existing behavior changes for rounds where all passes succeed.
- M6: Pure ASCII in all new code and comments.

## Threat Model

- T1 (severity: HIGH): New partial-verdict representation breaks existing
  consumers that parse receipt JSON or summary format.
  Mitigation: additive fields only (new keys, no renamed/removed keys);
  existing receipt structure preserved; new pass_status is optional in
  receipt schema (missing = old-format receipt).
- T2 (severity: MEDIUM): Chunking produces inconsistent findings when
  different chunks see different context (cross-file dependencies split
  across chunks).
  Mitigation: file-based chunking preserves intra-file context; findings
  carry file+line that is chunk-independent; merge uses fingerprint dedup.
  Limitation: cross-file findings (e.g., incompatible function signatures
  across files in different chunks) are impossible to produce from a
  single chunk. This is an inherent limitation of file-based chunking,
  acknowledged in Known Gaps item 3.
- T3 (severity: LOW): Ledger schema bump (new TerminalState member) collides
  with Phase 42's claim_type oracle.
  Mitigation: watch-item only; no evidence of actual conflict found. The
  plan adds a comment in ledger.py pointing to this coordination need.

## Validation Architecture

Each behavior change must be validated by at least one test that proves
the change has teeth (Golden Rule #2: bug-inject to prove test catches
regression).

| Change | Test Layer | Observable | Bug-Inject Proof |
|---|---|---|---|
| Pass-status tracking in receipt | Unit (test_receipt) | Receipt JSON has pass_status field | Corrupt INFRA finding ID (l1-qodo-spawn-fail -> l1-qodo-bogus) -> derive_pass_outcomes returns COMPLETED -> pass_status wrong -> test fails |
| Per-pass status in format_summary | Unit (test_sarif) | Summary line contains "2/3" | Corrupt INFRA finding ID -> pass count wrong -> test fails |
| CLI disk-reload path shows passes=N/M | Integration | format_summary on disk-reloaded State shows suffix | Remove derive_pass_outcomes call from _count_pass_outcomes -> suffix vanishes -> test fails |
| Partial-verdict not PASS | Unit (test_machine) | Verdict is FAIL when pass times out | Remove INFRA finding append -> verdict flips to PASS |
| Large-diff chunking | Unit (test_chunking) | Findings from all chunks present | Skip merge step -> some findings missing |
| Chunking activates on threshold | Integration | Chunking kicks in for >threshold diff | Set threshold to 0 -> always chunks |
| Receipt backward compat | Unit (test_receipt) | Old receipts without pass_status parse | Rename field -> parse fails |

## Plan

### Task 0a: Worktree and branch setup (mandatory)

**files:** None (git operations only)

**action:**
```bash
cd ~/code/forge
git worktree add .worktrees/p40-honest-partial main
cd .worktrees/p40-honest-partial
git checkout -b feature/p40-honest-partial
ln -sf "$(git rev-parse --show-toplevel)/CLAUDE.md" CLAUDE.md
```

**verify:** `git branch --show-current` shows `feature/p40-honest-partial`

**done:** Worktree created; CLAUDE.md symlinked; ready for implementation.

---

### Task 0: Ground-truth read-first (no code changes)

**files:** src/code_forge/receipt.py, src/code_forge/sarif.py,
src/code_forge/outlet_c.py, src/code_forge/state.py,
src/code_forge/ledger.py, src/code_forge/llm_invoke.py,
src/code_forge/machine.py, src/code_forge/machine.py (L1Provider
type alias at line 60), src/code_forge/cli.py (line 111)

**read_first:**
- receipt.py full file (135 lines) -- confirm _split_by_pass and
  write_receipts behavior
- sarif.py lines 228-270 -- confirm format_summary output format
- outlet_c.py lines 56-90 -- confirm timeout/failure handling,
  INFRA finding ID patterns (`l1-<pass>-spawn-fail`,
  `l1-<pass>-schema-fail`)
- state.py lines 29-90 -- confirm Verdict enum, StateFinding fields
- ledger.py lines 27-55 -- confirm TerminalState and LedgerRow schema
- machine.py line 60 -- confirm L1Provider type alias signature
- cli.py line 111 -- confirm format_summary call site

**action:**

1. Read-only verification. Compare against the ground-truth table in
   Context section above. If any file has changed since cfade37, update
   the table and adjust downstream tasks accordingly. Write findings to
   /tmp/p40-ground-truth-verify.txt.

2. Survey test files for l1_provider mocks:
   `grep -rn 'build_l1_provider\|l1_provider\|_run_l1_phase' tests/`
   List all hits. Each site that mocks the provider return must be
   verified -- does it expect a specific tuple shape? (With the revised
   Task 1 approach that derives pass_outcomes from INFRA findings,
   no tuple shape changes are needed, so test mocks should be
   unaffected. Verify this claim.)

3. Survey receipt consumers (code that READS receipt JSON):
   `grep -rn 'receipt.*\.json\|read_receipt\|results.*\.json' src/ tests/`
   Verify each consumer tolerates unknown keys (the new pass_status
   field is additive).

**verify:** All files read; table matches live source; any discrepancies
logged; test survey and receipt consumer survey complete.

**done:** Ground-truth table confirmed or corrected; test mocks verified
unaffected; receipt consumers verified tolerant of new fields.

---

### Task 1: Derive pass_status from INFRA findings (no contract change)

**Design choice (revised after R1 external review):** The original plan
modified the l1_provider return tuple from 4-tuple to 5-tuple, which
required changes to outlet_c.py, machine.py, factories.py,
cross_repo.py, mcp_server.py, L1Provider type alias, and all test
mocks (15+ files). Gemini's R1 BLOCKER identified a simpler path:
outlet_c.py already writes INFRA findings with predictable IDs when
passes fail. The receipt/sarif layer can derive pass_outcomes by
scanning for these patterns, avoiding the contract change entirely.

**files:** src/code_forge/receipt.py, src/code_forge/sarif.py,
src/code_forge/state.py, src/code_forge/cli.py

**read_first:**
- receipt.py full file (135 lines) -- confirm _split_by_pass and
  write_receipts behavior
- outlet_c.py lines 63-89 -- confirm INFRA finding ID patterns:
  `l1-<pass>-spawn-fail` (timeout/error) and
  `l1-<pass>-schema-fail` (JSON validation failure)
- sarif.py lines 228-270 -- confirm format_summary output format
- state.py lines 39-90 -- confirm StateFinding dataclass fields
- cli.py line 111 -- confirm format_summary call site

**action:**

1. Define a `PassOutcome` string enum in state.py (alongside Verdict
   and StateFinding -- this is a state-layer concern):
   - `COMPLETED` -- pass returned findings normally
   - `TIMEOUT` -- pass timed out (INFRA finding with `is_timeout=True`)
   - `ERROR` -- pass failed for a non-timeout reason (rate limit, quota,
     connection reset; INFRA finding with `is_timeout=False`)
   - `SCHEMA_FAIL` -- pass JSON was invalid (INFRA finding with
     id pattern `l1-<pass>-schema-fail`)
   - `INCOMPLETE` -- pass produced truncated output (INFRA finding with
     id pattern `l1-<pass>-incomplete-coverage`)
   - `SKIPPED` -- pass was not attempted (future use; reserves the slot)

   Severity order (worst first): TIMEOUT > ERROR > SCHEMA_FAIL >
   INCOMPLETE > COMPLETED. Used by derive_pass_outcomes to resolve
   multiple INFRA findings for the same pass.

2. Add `derive_pass_outcomes` as a pure function in state.py. Also
   export `_PASS_NAMES` from state.py for use by receipt.py, outlet_c.py,
   and sarif.py (eliminates the current 5-copy duplication):
   ```python
   _PASS_NAMES = ("qodo", "expert", "adversarial")
   _SEVERITY = {
       PassOutcome.TIMEOUT: 0,
       PassOutcome.ERROR: 1,
       PassOutcome.SCHEMA_FAIL: 2,
       PassOutcome.INCOMPLETE: 3,
       PassOutcome.COMPLETED: 4,
   }

   def derive_pass_outcomes(
       l1_findings: list[StateFinding],
   ) -> dict[str, PassOutcome]:
       """Derive per-pass outcomes from INFRA findings.

       Scans for INFRA findings with predictable IDs. Consults
       StateFinding.is_timeout to distinguish TIMEOUT from ERROR
       for invoke-fail findings (factories.py sets is_timeout on
       each finding; the discriminant is already there).

       Empty findings list: returns all COMPLETED. This is correct
       because if _run_l1_phase produced zero findings, all passes
       succeeded (no INFRA markers). If _run_l1_phase crashed,
       machine.py catches the exception and sets verdict to
       ESCALATED before format_summary is ever called.

       Worst-outcome-wins: if multiple INFRA findings exist for
       the same pass (e.g. across chunks), the most severe wins.
       """
       outcomes: dict[str, PassOutcome] = {}
       for f in l1_findings:
           if f.source != "INFRA":
               continue
           for pass_name in _PASS_NAMES:
               candidate: PassOutcome | None = None
               if f.id == "l1-%s-spawn-fail" % pass_name:
                   candidate = PassOutcome.TIMEOUT
               elif f.id == "l1-%s-invoke-fail" % pass_name:
                   candidate = (
                       PassOutcome.TIMEOUT
                       if getattr(f, "is_timeout", False)
                       else PassOutcome.ERROR
                   )
               elif f.id == "l1-%s-schema-fail" % pass_name:
                   candidate = PassOutcome.SCHEMA_FAIL
               elif f.id == "l1-%s-incomplete-coverage" % pass_name:
                   candidate = PassOutcome.INCOMPLETE
               if candidate is not None:
                   existing = outcomes.get(pass_name)
                   if existing is None or (
                       _SEVERITY[candidate] < _SEVERITY[existing]
                   ):
                       outcomes[pass_name] = candidate
       for pass_name in _PASS_NAMES:
           if pass_name not in outcomes:
               outcomes[pass_name] = PassOutcome.COMPLETED
       return outcomes
   ```

   Key design decisions:
   - Empty findings -> all COMPLETED (not empty dict). lc-R3-1 proved
     empty dict causes receipt/sarif contradiction.
   - invoke-fail: consults `is_timeout` (lc-R3-2). factories.py:362
     sets `is_timeout=pr.is_timeout`; non-timeout exceptions (rate
     limit, quota) get `is_timeout=False` -> ERROR.
   - incomplete-coverage: gets its own INCOMPLETE outcome (lc-R3-2),
     not lumped with TIMEOUT.
   - Worst-outcome-wins via severity ordering (lc-R3-3). Single
     authority for chunked-path outcomes; no separate downgrade
     tracking needed.
   - _PASS_NAMES exported from state.py (lc-R3-5). receipt.py and
     outlet_c.py import it instead of hardcoding.

3. (REMOVED: State.pass_outcomes field deleted. lc-R3-4 proved
   cli.py:96 loads state from disk via save_state/load_state, which
   don't persist pass_outcomes. Option B: derive at consumption site.
   See steps 4 and 5.)

4. Update `write_receipts` in receipt.py. Add imports at top:
   ```python
   from .state import PassOutcome, derive_pass_outcomes, _PASS_NAMES
   ```
   Remove the existing `from .state import StateFinding` (now covered).
   Also remove the local `_PASS_NAMES = [...]` at receipt.py:15 (now
   imported from state.py). No new import direction (state.py is
   already imported). Add a local variable inside write_receipts:
   ```python
   pass_outcomes = derive_pass_outcomes(l1_findings)
   ```
   Inside the per-pass loop:
   ```python
   "pass_status": pass_outcomes.get(
       pass_name, PassOutcome.COMPLETED
   ).value
   ```
   No new parameter needed. Backward compatible: if l1_findings is
   empty, pass_outcomes is all COMPLETED -> "completed" for all passes.

5. For format_summary in sarif.py: import `derive_pass_outcomes` and
   `_PASS_NAMES` from state.py (same direction as existing import).
   Add a lightweight helper:
   ```python
   def _count_pass_outcomes(
       l1_findings: list[StateFinding],
   ) -> tuple[int, int]:
       """Return (completed, total) by deriving from findings.

       Calls derive_pass_outcomes (same logic as receipt.py).
       Empty findings -> (3,3) all-completed (clean run).
       """
       from .state import PassOutcome, derive_pass_outcomes, _PASS_NAMES
       pass_outcomes = derive_pass_outcomes(l1_findings)
       completed = sum(
           1 for v in pass_outcomes.values()
           if v == PassOutcome.COMPLETED
       )
       return (completed, len(_PASS_NAMES))
   ```
   In format_summary, call `_count_pass_outcomes(state.findings)` to
   get the pass count. No State field needed -- derives directly from
   the findings list that persists in state.json.

   In cli.py, no threading needed. The call at line 111:
   `format_summary(final_state)` -- format_summary internally calls
   `_count_pass_outcomes(final_state.findings)` which derives from
   the disk-reloaded findings. M1 is satisfied through the
   disk-reload path.

**verify:**
- `python3 -m py_compile src/code_forge/state.py`
- `python3 -m py_compile src/code_forge/receipt.py`
- `python3 -m py_compile src/code_forge/sarif.py`
- `python3 -m py_compile src/code_forge/machine.py`

**done:** Receipt JSON contains `"pass_status": "timeout"` for a pass that
timed out (derived from INFRA finding); `"pass_status": "completed"` for
a pass that succeeded; `"pass_status": "schema_fail"` for a pass with
invalid JSON response. write_receipts derives pass_outcomes internally
via derive_pass_outcomes(l1_findings) -- no State field, no new
parameter, no machine.py changes. outlet_c.py updated to import
_PASS_NAMES from state.py (lc-R3-5). No changes to factories.py,
cross_repo.py, mcp_server.py, or L1Provider alias.

---

### Task 2: Per-pass status in format_summary

**files:** src/code_forge/sarif.py, src/code_forge/cli.py,
tests/test_sarif.py

**read_first:**
- sarif.py lines 228-280 (format_summary full body + regex pattern)
- cli.py line 111 (format_summary call site -- no changes needed under
  Option B; format_summary derives internally from state.findings)

**action:**

1. format_summary keeps its existing signature (no new parameter).
   Internally, call `_count_pass_outcomes(state.findings)` to derive
   the pass count from the findings list (which persists in state.json).
   ```python
   def format_summary(state: State, advisory_count: int = 0) -> str:
       # ... existing count logic ...
       completed, total = _count_pass_outcomes(state.findings)
   ```

2. When any pass is not COMPLETED, append a pass-completion suffix:
   ```
   code-forge: FAIL findings=5 confirmed=3 ... passes=2/3
   ```
   When all passes are COMPLETED, omit the suffix (backward compat).

3. Update the SUMMARY_REGEX in test_sarif.py (line ~319) from
   `fixed=\d+$` to `fixed=\d+( passes=\d+/\d+)?$` so existing tests
   pass with the new optional suffix. Add a new test: partial round
   produces `passes=2/3` suffix.

4. No cli.py threading needed. format_summary internally calls
   `_count_pass_outcomes(state.findings)` which derives from the
   findings list (persists in state.json). cli.py:111's existing
   call `format_summary(final_state)` works unchanged. M1 is
   satisfied: the disk-reload path at cli.py:96 loads findings from
   state.json, and derive_pass_outcomes reads them at emit time.

**verify:**
- `python3 -m py_compile src/code_forge/sarif.py`

**done:** format_summary output shows "passes=2/3" when one pass timed out;
shows no suffix when all passes completed.

---

### Task 3: Large-diff chunking

**files:** src/code_forge/outlet_c.py (or new src/code_forge/chunking.py
if the logic exceeds ~80 lines)

**read_first:**
- outlet_c.py full file -- understand where diff is passed to spawn_fn
- llm_invoke.py -- understand existing timeout and token cap knobs

**action:**

1. Re-confirm no existing chunking code anywhere in src/ or tests/.

2. Design choice: split-by-file (not split-by-hunk). Rationale:
   - File boundaries are natural context boundaries for code review
   - Each file can be reviewed independently without cross-hunk context
   - Hunk-based splitting loses intra-file context (function signatures,
     class definitions) that reviewers need
   - The false-green trap #2 is about diff SIZE overwhelming the LLM,
     not about logical chunking

3. Add a configurable threshold env var: `FORGE_DIFF_CHUNK_THRESHOLD_KB`
   (default: 100). When the diff exceeds this threshold (in KB), chunking
   activates. Validate: if the env var is set but non-numeric, log a
   warning and fall back to the default (100). If <= 0, treat as
   "always chunk" (valid use case for testing). Never crash on a
   malformed env var.

4. Implement by extracting a `_run_chunk(chunk_diff, spawn_fn, pass_names)`
   helper from the existing 3-pass loop in `_l1_provider`. This helper
   runs all 3 passes on one diff chunk and returns
   `(findings, excerpts, usage, duration)`. The existing `_l1_provider`
   becomes a thin dispatcher:
   - If diff <= threshold: call `_run_chunk(full_diff, ...)` once
   - If diff > threshold: split and call `_run_chunk` per chunk, merge

5. Split-by-file implementation:
   - Use `re.findall` (NOT `re.split`) to extract chunks. Splitting
     consumes the delimiter line (`diff --git a/X b/Y`), producing
     malformed chunks the LLM cannot parse. findall preserves the
     header in each chunk:
     ```python
     chunks = re.findall(r'diff --git .+?(?=diff --git |\Z)', diff, re.DOTALL)
     ```
   - If the diff lacks git headers (non-git diff), fall back to
     `--- a/` / `+++ b/` pairs (same findall approach).
   - Skip chunks with zero reviewable lines: no `@@` hunk headers
     (binary files, pure renames, whitespace-only).
   - On parse failure (zero chunks extracted from non-empty diff):
     log warning and fall through to un-chunked path (original
     behavior). Never silently produce zero findings.

6. Merge ALL 4-tuple elements from all chunks:
   - findings: list concat with dedup by `StateFinding.fingerprint`
   - excerpts: list concat (excerpts from all chunks are additive)
   - usage: sum input_tokens and output_tokens across chunks
   - duration: sum across chunks
   The merged 4-tuple is returned as if _run_chunk ran once.

7. Per-pass outcomes: derive_pass_outcomes (Task 1 step 2) is the
   single authority. It runs on the MERGED findings list after
   chunk-level concat+dedup. Its worst-outcome-wins severity ordering
   handles the case where different chunks produce different INFRA
   findings for the same pass. No separate chunk-level tracking needed.

8. The chunking is transparent to the receipt system: merged findings
   route through `_split_by_pass` and `derive_pass_outcomes` normally.

**verify:**
- `python3 -m py_compile src/code_forge/outlet_c.py`
- Test: 3-file diff over threshold produces findings from all 3 chunks
- Test: chunk timeout sets pass_status=TIMEOUT for that pass

**done:** Large diffs produce findings (not zero); chunking activates at
configurable threshold; pass_status reflects worst-per-chunk outcome.

---

### Task 4: Unit tests for partial-verdict representation

**files:** tests/test_receipt_partial.py (new)

**read_first:**
- tests/ directory structure (match existing test patterns)

**action:**

1. Test: receipt with pass_outcomes dict has pass_status field for each pass
2. Test: receipt without pass_outcomes defaults to "completed"
3. Test: derive_pass_outcomes detects TIMEOUT from INFRA finding
   (id="l1-qodo-spawn-fail", source="INFRA")
4. Test: derive_pass_outcomes detects SCHEMA_FAIL from INFRA finding
   (id="l1-expert-schema-fail", source="INFRA")
5. Test: derive_pass_outcomes returns COMPLETED for passes with no
   INFRA finding
6. Test: format_summary shows "passes=2/3" when 1 of 3 passes has
   INFRA finding
7. Test: format_summary shows no suffix when all passes COMPLETED
8. Test: partial round verdict is FAIL (not PASS)
9. Bug-inject: corrupt INFRA finding ID
   (l1-qodo-spawn-fail -> l1-qodo-bogus) -> derive_pass_outcomes
   returns COMPLETED for qodo -> pass_status is wrong -> test catches it.
   This proves the derivation logic has teeth (not just the write).

**verify:**
- `python3 -B -m pytest tests/test_receipt_partial.py -v`

**done:** All tests pass; bug-inject proof verified.

---

### Task 5: Unit tests for large-diff chunking

**files:** tests/test_chunking.py (new)

**read_first:**
- outlet_c.py chunking implementation from Task 3

**action:**

1. Test: diff under threshold -- no chunking, single pass
2. Test: diff over threshold -- chunking activates
3. Test: 3-file diff produces findings from all 3 files
4. Test: chunk timeout sets pass_status=TIMEOUT
5. Test: all chunks succeed -> pass_status=COMPLETED
6. Test: dedup by fingerprint across chunks
7. Bug-inject: skip merge step -> some findings missing -> test catches it

**verify:**
- `python3 -B -m pytest tests/test_chunking.py -v`

**done:** All tests pass; bug-inject proof verified.

---

### Task 6: Integration + full suite verification

**files:** None (verification only)

**read_first:**
- All modified files from Tasks 1-5

**action:**

1. Run full test suite: `python3 -B -m pytest tests/ -v`
2. Verify no regressions in existing tests
3. Verify new tests all pass
4. Verify receipt backward compatibility: old receipts (no pass_status)
   still parse correctly
5. Verify format_summary backward compat: no suffix when all passes OK

**verify:**
- `python3 -B -m pytest tests/ -v` -- full suite passes

**done:** Full suite green; no regressions; new behavior validated.

## Acceptance

- A round where 1 of 3 passes timed out shows "passes=2/3" in CLI summary
  and `"pass_status": "timeout"` in that pass's receipt JSON.
- Findings from completed passes are visible (not suppressed) in the
  receipt and summary.
- Verdict on partial completion is FAIL (fail-closed).
- Large diffs (over configurable threshold) produce findings via chunking.
- Large diffs under threshold behave identically to today.
- Old receipt JSON (without pass_status) still parses.
- format_summary output for all-passes-completed rounds is unchanged.
- Full test suite passes with zero regressions.
- Bug-inject proofs exist for all new behaviors.

## Known Gaps (documented, not in scope)

1. **Convergence plateau + prior-round memory**: deferred to post-Phase-44
   semantic half. Locked policy decision in 40-CONTEXT.md deferred section.
2. **Ledger: no partial-round terminal state** (DECISION): the plan does NOT
   add a new TerminalState enum member. Rationale: pass_outcomes is stored
   in receipt JSON only (via derive_pass_outcomes). The ledger's
   TerminalState records the machine's final verdict (FAIL/PASS/ESCALATED),
   which is unchanged by partial completion -- a partial round is still
   FAIL. Consequence: the ledger cannot distinguish "3 passes all
   completed, result FAIL" from "2 of 3 passes completed, result FAIL."
   This is acceptable because the receipt layer provides per-pass detail;
   the ledger is for terminal outcomes only. Watch-item for Phase 42:
   no evidence of actual collision with claim_type oracle.
3. **Cross-file findings lost under chunking** (inherent limitation):
   file-based chunking isolates each file, so findings that depend on
   cross-file context (incompatible signatures, import mismatches) are
   impossible to produce from a single chunk. Mitigation: when chunking
   activates, the user should be warned that cross-file findings may be
   missed. A future enhancement could add a lightweight cross-file
   synthesis step after per-chunk passes complete.
4. **Bin-packing for chunk count optimization**: if a large diff has 30
   small files, file-based chunking creates 30 chunks and 90 LLM calls.
   A bin-packing approach (group files until chunk reaches threshold)
   would reduce API calls. Deferred to implementation as an optimization.
5. **Single-file oversized fallback**: if a single file exceeds the
   threshold (e.g., a 500KB lockfile), file-based chunking still sends
   it as one chunk. A hunk-based fallback for oversized single files
   would close this gap. Deferred.
6. **Per-pass retry**: not included. The "keep-and-mark" policy is
   consistent with findings-survive model. Retry adds complexity without
   clear benefit (the findings are already there).
