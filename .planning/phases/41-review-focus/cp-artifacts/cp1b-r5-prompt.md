# forge Phase 41 -- CP1b ROUND-5 plan review

You are reviewing an IMPLEMENTATION PLAN (not code yet). The plan is the
document that follows this prompt. It describes changes to a real Python
codebase at /home/houminxi/code/forge/src/code_forge/ -- you have that repo;
VERIFY every plan claim against the real source (cite file:line), do not trust
the plan's own line numbers.

Your job: find defects in the PLAN that would cause a wrong or leaky
implementation. Report only findings you verified against real code.

## What this feature is

Phase 41 adds a `review_focus` prompt mechanism at parity with `--contract`:
gate.yaml `review_focus:` (with its OWN trust hash, independent of backend
trust), a `--focus FILE` CLI flag, and a `focus` MCP param, all merging into a
"## Review Focus" prompt section on all 3 builders / 4 review paths. It also
renames "## Contract Reference" -> "## Design Intent" and adds a committer date
to git-blame.

## Round-4 disposition (non-convergence protocol -- READ before reviewing)

Rounds 1-3 confirmed the core H1 fix CORRECT (focus trust decoupled from backend
trust via a raw gate.yaml read) and are FULLY RESOLVED; do NOT resurrect them.
Round-4 (ds/kimi/gm/lc) then surfaced 11 findings -- SEVERAL of them defects the
plan author introduced in the round-3 union fix, several pre-existing staleness.
ALL 11 are now FIXED in the plan below. Do NOT re-report them as open unless you
find the FIX itself is wrong:

1. [kimi M3 + gm H1, CONVERGED] REPLAN(a) captured `contract_tmp = tmp.name`
   AFTER write/close, so a write/close failure left it None and leaked the
   already-created file -> FIXED: `.name` now captured immediately after
   `NamedTemporaryFile(...)`, BEFORE write/close (both contract and focus).
2. [kimi L5] `_dispatch_cli` docstring (mcp_server.py:654-663) was contract-only
   -> FIXED: REPLAN(a) instructs updating it for the dual contract+focus lifecycle.
3. [lc M1] 3b(d) explicit sampling block omitted the `raw_focus = focus_spec`
   save line (only prose had it) -> FIXED: save line added INSIDE the block,
   before the merge that reassigns `focus_spec`.
4. [kimi M1] all 5 mirrored `_dispatch_cli` tests + the pre-existing inline test
   exercised only job/raise branches, never asserting deletion on the INLINE
   result path -> FIXED: REPLAN(e) adds a 6th test driving the inline path
   (`_run_cli_budgeted` returns a str tuple), asserting `not os.path.exists()`.
5. [ds B1 + kimi M2, CONVERGED] the round-3 `_evict_stale` test recipe drove it
   via a small `max_lifetime_s`, but that param only caps `_wait_for_job`'s
   subprocess timeout, NOT `_evict_stale`'s TTL gate -- the test never reached
   the unlink code (or passed for the wrong reason: `_wait_for_job`'s finally
   unlinks first) -> FIXED: REPLAN(e)(i) rewritten to inject a `status="failed"`
   entry into `mcp_jobs._jobs` with a backdated `created_at` (or monkeypatch
   `_JOB_TTL_SECONDS`), then call `_evict_stale()`/`get_job()` directly.
6. [gm Low] 3a-1 said warn when merged focus "exceeds 8192 bytes", but the mirror
   `_merge_contract_spec` (cli.py:1861) uses a `len()` CHARACTER count -> FIXED:
   3a-1 reworded to "exceeds 8192 CHARACTERS (`len(merged) > 8192`)".
7. [kimi L2] 3a-3 told the implementer to also load the sampling contracts.yaml
   digest, but that load already exists (mcp_server.py:837-842) -> FIXED: 3a-3
   now marks it already-present, "do NOT re-add it".
8. [kimi L1 + gm M, CONVERGED] the 3b-1 wiring table cited the sampling call site
   as mcp_server.py:765, stale post-2edb9d4 -> FIXED: row now points to the
   `build_sampling_l1_provider(...)` call in `_dispatch_sampling`,
   mcp_server.py:853-857 (NOT :765).
9. [kimi M4] the 3a-2 rejection-message change silently breaks two verbatim
   substring assertions in test_trust_empty_backends.py:51 and :64 -> FIXED:
   3c-2 instructs updating both assertions.
10. [kimi L3] Task 3b header said "Fixes the pre-existing sampling contract_spec
    gap" while RECONCILE says D5.7 is ALREADY fixed via 2edb9d4 -> FIXED: header
    reworded to point at RECONCILE.
11. [kimi L4] Task 2c left test_legacy.py:262's docstring describing the pre-date
    attribution format -> FIXED: Task 2c instructs updating that docstring.

Author-executed, do NOT re-litigate: the two code-block fixes above (#1
tmpfile-name-timing and #3 raw_focus-save) were extracted verbatim into a
standalone script and EXECUTED by the plan author -- both `.name` values are
captured before write, and `raw_focus` is saved before the merge reassigns
`focus_spec`. Do not re-report the mechanics of #1/#3 unless you find a NEW
failure mode the execution did not cover.

Disproved earlier / do NOT resurrect: round-2 claimed H1 was "fully resolved"
via a backwards reading of `_load_gate_backends` (it returns `([], {})` when
untrusted, cli.py:160, NOT `(cfgs, gd)`); that false-green is why H1 was fixed.

## REVIEW THIS HARDEST (the round-4 fixes changed plan text -- attack the changes)

A. REPLAN(a) dual-tmpfile lifecycle (fixes #1, #2). The `.name` capture moved
   before write/close, and the docstring now claims a dual lifecycle. Trace
   EVERY exit path -- creation-failure, dispatch-raise, inline-return, timeout
   job-transfer, start_job-raise -- for double-unlink, leak, or unlink of a path
   still owned by a running job. Does the docstring (#2) accurately match the
   code the plan now specifies, or does it over-promise?

B. REPLAN(e) test recipes (fixes #4, #5). Is the rewritten `_evict_stale` recipe
   (#5) now actually FALSIFIABLE -- would it FAIL if `_evict_stale` never added
   the new tuple key? Trace it against the real mcp_jobs.py: does injecting a
   `status="failed"` entry with a backdated `created_at` actually drive
   `_evict_stale`'s `age <= _JOB_TTL_SECONDS` + terminal-status gate to the
   unlink? Does the inline-delete test (#4) actually reach the inline branch and
   assert deletion, not a job branch?

C. 3b(d) sampling block (fix #3). Is `raw_focus` saved before the merge line,
   and are the variables the block uses actually in scope in `_dispatch_sampling`
   (mcp_server.py:800)? `workspace`/`staged` yes; `gate_yaml_path`/`warn` are
   computed/inlined -- verify the block establishes them correctly.

D. Cross-reference integrity after fixes #6-#11. The edits changed line refs
   (#8 :765->:853-857), a redundant-load note (#7), test-assert targets (#9),
   two headers/docstrings (#10, #11), and a units word (#6). Grep the real
   source for each: is every cited file:line now correct, and did any edit
   introduce a NEW stale or contradictory reference elsewhere in the plan?

## Output format

First line: `SUMMARY: B=<n> H=<n> M=<n> L=<n>` (Blocker/High/Medium/Low).
Then per finding: severity, location (plan section + real file:line),
description, required fix. Verify each against real source before reporting.
If the plan is clean, say so plainly -- a clean 0/0/0/0 is a valid result, and
after four rounds of convergence it is the expected one if no NEW defect exists.

--- PLAN FOLLOWS ---
