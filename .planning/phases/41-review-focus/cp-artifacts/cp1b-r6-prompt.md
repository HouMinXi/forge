# forge Phase 41 -- CP1b ROUND-6 plan review

You are reviewing an IMPLEMENTATION PLAN (not code yet). The plan is the
document that follows this prompt. It describes changes to a real Python
codebase at /home/houminxi/code/forge/src/code_forge/ -- you have that repo;
VERIFY every plan claim against the real source (cite file:line), do not trust
the plan's own line numbers.

Report only findings you verified against real code. This is a convergence
round: a clean 0/0/0/0 is the expected result if no NEW defect exists, and it is
a valid, wanted outcome -- do not manufacture findings to look thorough.

## What this feature is

Phase 41 adds a `review_focus` prompt mechanism at parity with `--contract`:
gate.yaml `review_focus:` (with its OWN trust hash, independent of backend
trust), a `--focus FILE` CLI flag, and a `focus` MCP param, all merging into a
"## Review Focus" prompt section on all 3 builders / 4 review paths. It also
renames "## Contract Reference" -> "## Design Intent" and adds a committer date
to git-blame.

## Disposition through round-5 (non-convergence protocol -- READ FIRST)

This plan has iterated through 5 review rounds. Rounds 1-4 surfaced 33 findings,
ALL fixed and independently re-verified. Round-5 (gm repo-grounded + lc) found
exactly ONE, now FIXED:

- Task 3a-1's size-guard rationale mislabeled cli.py:1861 as a `len()` char-count
  guard. Real code is `len(effective_content.encode("utf-8")) > 4096` -- a UTF-8
  BYTE count. FIXED: 3a-1 now specifies `len(merged.encode("utf-8")) > 8192`
  (byte count, mirroring cli.py:1861's byte mechanism at a larger warn-only
  threshold; focus never summarizes, so 8192 > contract's 4096 is intentional),
  and the former internal contradiction with 41-PLAN.md:936 ("contract body
  <=4096 bytes") is resolved.

In round-5, lc independently re-verified these round-4 fixes CLEAN against real
source -- do NOT re-open them unless you find the fix itself is now wrong:
- REPLAN(a) dual-tmpfile lifecycle: `.name` captured before write; every exit
  path (creation-fail, dispatch-raise, inline-return, timeout transfer,
  start_job-raise) unlinks or transfers ownership; no leak / no double-unlink.
- REPLAN(e) tests: the `_evict_stale` recipe is falsifiable (inject a stale
  `status="failed"` entry into `mcp_jobs._jobs` with a backdated `created_at`,
  call `_evict_stale()` directly -- NOT via `max_lifetime_s`); the inline-path
  delete assertion is present.
- 3b(d): `raw_focus = focus_spec` saved before the merge; sampling-scope
  variables correct.
- Cross-refs: contracts.yaml digest already at mcp_server.py:837-842,
  `build_sampling_l1_provider` at mcp_server.py:853-857, trust-assert updates,
  test_legacy.py:262 date docstring -- all correct.

Do NOT resurrect the round-2 false-green: H1 was NOT "fully resolved" by a
backwards read of `_load_gate_backends` (it returns `([], {})` when untrusted,
cli.py:160, NOT `(cfgs, gd)`).

## Review this hardest

A. The round-5 fix itself (Task 3a-1 size guard). Against cli.py:1861, confirm
   the guard is now correctly a UTF-8 byte count, that
   `len(merged.encode("utf-8")) > 8192` is the right mirror of
   `len(effective_content.encode("utf-8")) > 4096`, and that NO other plan
   reference to :1861 or the focus size threshold still claims char-count or
   contradicts 41-PLAN.md:936.
B. Residual sweep. This is a convergence round: read the whole plan for any
   remaining stale file:line, internal contradiction, or claim that does not
   match real source. If you find nothing, say 0/0/0/0 plainly.

## Output format

First line: `SUMMARY: B=<n> H=<n> M=<n> L=<n>` (Blocker/High/Medium/Low).
Then per finding: severity, location (plan section + real file:line),
description, required fix. Verify each against real source before reporting.

--- PLAN FOLLOWS ---
