# forge Phase 41 -- CP1b ROUND-4 plan review (manual relay, no repo access)

You are reviewing an IMPLEMENTATION PLAN (the document that follows). It
describes changes to a Python codebase you do NOT have live access to. Review
the PLAN as a document: reason from its own claims, its cited `file:line`
references, and your engineering judgment about Python / asyncio / tempfile /
trust-hash patterns. Where a finding depends on actual code you cannot see, say
so explicitly ("unverified -- depends on <file> real behavior") rather than
asserting it as fact. A well-reasoned "this looks inconsistent, confirm X" beats
a confident guess. A clean 0/0/0/0 is a valid result if the plan holds.

## What this feature is

Phase 41 adds a `review_focus` prompt mechanism at parity with `--contract`:
gate.yaml `review_focus:` (with its OWN trust hash, independent of backend
trust), a `--focus FILE` CLI flag, and a `focus` MCP param, all merging into a
"## Review Focus" prompt section on 3 builders / 4 review paths. It also renames
"## Contract Reference" -> "## Design Intent" and adds a committer date to
git-blame.

## Round-3 disposition (READ before reviewing -- do NOT re-report these as open)

Round-3 (5 models) confirmed the core H1 fix CORRECT (focus trust decoupled from
backend trust via a raw gate.yaml read) and found 11 completeness findings, ALL
now FIXED in the plan below. Do NOT re-raise them unless you judge the FIX itself
wrong:

1. `_merge_focus_spec` missing `\n\n` separator -> FIXED 3a-1 (mirrors
   `_merge_contract_spec`).
2. tests skipped `_evict_stale`/`snapshot_tempfile_paths` leak paths -> FIXED
   REPLAN(e).
3. a 3c-2 test row described pre-H1 behavior -> FIXED (is_trusted_focus scenario).
4. "never re-read gate.yaml raw" invariant conflicted with the H1 read -> FIXED
   (3a-3 carves out the focus exception).
5. H1 bug-inject edited a non-dangerous field (hollow) -> FIXED (edits a
   DANGEROUS_FIELDS field, asserts backends dropped first).
6. extract+gate duplicated CLI vs sampling -> FIXED: shared helper
   `_load_trusted_yaml_focus`; both paths call it.
7. branch logic bug (whitespace-str mislabeled, falsy non-str silently dropped)
   -> FIXED inside the helper. (The plan author already executed 4 edge cases --
   whitespace silent, empty-list warns, trusted returns, untrusted warns -- all
   pass; do not re-litigate unless you find a NEW input class.)
8. 3a-4 mis-attributed validation to argparse -> FIXED (guards in
   `_load_focus_file`).
9. REPLAN(a) second tmpfile created outside the cleanup try leaks the first ->
   FIXED (both created inside one try/except-unlink-both).
10. is_trusted_focus pseudocode omitted the trust-store load -> FIXED.
11. Task 1 "unreachable factories.py:576" + dangling "Task 3g" -> FIXED.

Do NOT resurrect: round-2 claimed H1 "fully resolved" via a backwards reading of
`_load_gate_backends` (it returns `([], {})` when untrusted, NOT `(cfgs, gd)`);
that false-green is exactly why H1 was fixed.

## Review this hardest (the 11 fixes CHANGED plan text -- attack the changes)

A. The shared helper `_load_trusted_yaml_focus` (3a-3): does ONE helper correctly
   serve BOTH the CLI path and the sampling path? The plan (3b(d)) shows a
   sampling call block that computes `gate_yaml_path` and uses an inline warn
   lambda. Is the block's logic self-consistent, and does the plan establish that
   the variables it uses exist in `_dispatch_sampling`?

B. REPLAN(a) dual-tmpfile restructure: both tmpfiles created inside a NEW
   try/except-unlink-both, followed by the existing dispatch try. Trace every
   exit path (creation-failure, dispatch-raise, inline-return, timeout
   job-transfer, start_job-raise): any double-unlink, leak, or unlink of a path
   still owned by a running job? Does the pseudocode actually capture the tmpfile
   name BEFORE the write that could fail?

C. The invariant-comment carve-out: the plan adds a SECOND raw gate.yaml read in
   `_run`. Does the plan's reasoning that this doesn't bypass backend trust hold
   (focus gated by is_trusted_focus; backends still via _load_gate_backends)?

D. Cross-reference integrity: the plan references helpers/tasks/line-numbers
   across sections (3a-3 <-> 3b(d), Task 1 <-> RECONCILE, the 3b-1 wiring table).
   Any internal contradiction or stale line-number reference that would send an
   implementer to the wrong place?

E. Test falsifiability: for each new test the plan specifies, would it actually
   FAIL if the code it targets were broken (per the plan's own bug-injection
   claims)? A test that passes whether or not the bug exists is a hollow guard.

## Output format

First line: `SUMMARY: B=<n> H=<n> M=<n> L=<n>` (Blocker/High/Medium/Low).
Then per finding: severity, plan location (+ any cited file:line), description,
required fix. Mark anything you could not verify without the code as "unverified".

--- PLAN FOLLOWS ---
