# forge Phase 41 -- CP1b ROUND-4 plan review

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

## Round-3 disposition (non-convergence protocol -- READ before reviewing)

Round-3 (5 models: ds/kimi/mm/gm/lc) independently confirmed the core H1 fix
CORRECT (focus trust decoupled from backend trust via a raw gate.yaml read) and
surfaced 11 completeness/robustness findings. ALL 11 are now FIXED in the plan
below. Do NOT re-report them as open unless you find the FIX itself is wrong:

1. [ds] `_merge_focus_spec` missing `\n\n` separator -> FIXED in 3a-1 (mirrors
   `_merge_contract_spec`, cli.py:1889).
2. [ds] tests skipped `_evict_stale`/`snapshot_tempfile_paths` leak paths ->
   FIXED in REPLAN(e) (added a TTL-evict test + a snapshot test).
3. [kimi/mm] a 3c-2 test row described pre-H1 behavior -> FIXED (rewritten to
   the is_trusted_focus-gated scenario, kept distinct from the H1 row).
4. [kimi] cli.py:2182-2184 "never re-read gate.yaml raw" invariant conflicted
   with the H1 read -> FIXED (3a-3 instructs revising that comment to carve out
   the focus exception, anchored at the contract read cli.py:2195-2200).
5. [kimi] H1 bug-inject edited a non-dangerous field so is_trusted never
   flipped (hollow guard) -> FIXED (3a-3 now edits a DANGEROUS_FIELDS field,
   asserts "Untrusted repo backends ignored" first).
6. [kimi] extract+gate logic duplicated CLI vs sampling, no shared helper ->
   FIXED: new `_load_trusted_yaml_focus(gate_yaml_path, warn_fn)` helper in
   3a-3; CLI `_run` and `_dispatch_sampling` both call it.
7. [kimi] branch logic: whitespace-str warned "not a string"; falsy non-str
   silently dropped -> FIXED inside the helper (isinstance branch; `raw is not
   None` warns non-str). PM already EXECUTED 4 edge cases (whitespace silent,
   empty-list warns, trusted returns, untrusted warns) -- all pass; do not
   re-litigate the branch unless you find a NEW input class.
8. [kimi] 3a-4 attributed validation to argparse FILE type (--contract has no
   FileType) -> FIXED (guards live in `_load_focus_file`, cli.py:351/1666).
9. [gm] REPLAN(a) second tmpfile created outside the cleanup try leaks the
   first -> FIXED (both None-init, both created inside one try/except that
   unlinks both; contract_tmp is created pre-try at mcp_server.py:664-672).
10. [gm] is_trusted_focus pseudocode omitted `store = _load_trust_store()` ->
    FIXED (added after the short-circuit; real loader trust.py:131).
11. [lc] Task 1 factories.py:576 "unreachable" + dangling "Task 3g" -> FIXED
    (reachable post-2edb9d4 on the sampling path; Task 3g deleted).

Disproved earlier / do NOT resurrect: round-2 claimed H1 was "fully resolved"
via a backwards code reading (`_load_gate_backends` returns `([], {})` when
untrusted, cli.py:160, NOT `(cfgs, gd)`); that false-green is why H1 was fixed.

## REVIEW THIS HARDEST (the union fix may have introduced NEW defects)

The 11 fixes CHANGED plan text. Attack the changes themselves:

A. The keystone shared helper `_load_trusted_yaml_focus` (3a-3): does one helper
   correctly serve BOTH the CLI `_run` path AND `_dispatch_sampling`? In 3b(d)
   the plan shows the sampling call block -- verify the variables it uses are
   actually in scope in `_dispatch_sampling` (mcp_server.py:800): `workspace`
   and `staged` yes, `gate_yaml_path`/`warn` NO (it computes the path and uses
   an inline warn lambda mirroring mcp_server.py:847). Is that block correct?

B. REPLAN(a) dual-tmpfile restructure (#9 fix): both tmpfiles are now created
   inside a NEW try/except-unlink-both, and there is ALSO the existing DISPATCH
   try that follows. Trace every exit path: creation-failure, dispatch-raise,
   inline-return, timeout job-transfer, start_job-raise. Does any path
   double-unlink, leak, or unlink a path still owned by a running job?

C. The invariant-comment carve-out (#4): the plan adds a SECOND raw gate.yaml
   read inside `_run`. Confirm this does not actually bypass backend trust
   (focus is gated by is_trusted_focus; backends still go through
   _load_gate_backends). Is the carve-out reasoning sound or does it weaken the
   original invariant?

D. Cross-reference integrity: the plan references helpers/tasks across sections
   (3a-3 <-> 3b(d), Task 1 <-> RECONCILE). Any remaining internal contradiction
   or stale reference after the edits?

## Output format

First line: `SUMMARY: B=<n> H=<n> M=<n> L=<n>` (Blocker/High/Medium/Low).
Then per finding: severity, location (plan section + real file:line),
description, required fix. Verify each against real source before reporting.
If the plan is clean, say so plainly -- a clean 0/0/0/0 is a valid result.

--- PLAN FOLLOWS ---
