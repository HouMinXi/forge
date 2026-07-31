# Evidence 07 -- Phase 43's own dispatch order and completion record

## What this is

A read of the actual planning artifacts for Phase 43, to answer
"defect or working as designed" from the plan's own stated intent
rather than from inference. No file modified.

## The dispatch named this exact failure mode as the #1 risk, in advance

`.planning/dispatch/draft_20260704_phase43_ledger_dispatch.txt:12-17`:
```
## Why this exists (scope-challenge pre-answered)

The v3 flywheel (ledger -> eval growth -> escape intake ->
synthesis -> registry) starts here. Named top process risk:
"components built, ledger never fed" -- hence the dogfood hard
gate below.
```

## The wiring instruction only ever named the LOCAL exit point

`.planning/dispatch/draft_20260704_phase43_ledger_dispatch.txt:63-72`:
```
T2 machine.py hook -- at _finalize_local_terminal (call site
   :544, def :966): batch-write rows for findings that reached a
   ledger-terminal outcome.
   - DESIGN POINT you must pin in your plan before coding ...
     Recommended: FIXED -> FIXED; DISMISSED -> DISPROVED with
     evidence_class from the dismissal reason; UNCERTAIN and
     still-open CONFIRMED -> NO row ...
```
Nothing in this dispatch mentions `_run_ci`, CI mode, or asks which
mode the project's actual invocations resolve to. The instruction is
scoped, explicitly and only, to the LOCAL fixpoint-exit function -- the
later hardcoded `mode=Mode.CI` in `mcp_server.py`'s sampling path
(evidence-01) and the CLI's TTY-based CI default (evidence-03) are
older, pre-existing mechanisms this dispatch never cross-checked
against.

## The dispatch's own acceptance bar, and how it was satisfied

`.planning/dispatch/draft_20260704_phase43_ledger_dispatch.txt:94-99`:
```
2. REAL-PATH (hard requirement): one real review run on a real
   diff produces >=1 ledger row with real SHAs. Mock-only test
   suites get gate-returned ...
3. Dogfood hard gate (split of duties): you deliver the working
   `ledger mark` command + docs; the MAIN session executes one
   real ruling at L4 verification and the delivery is not DONE
   until that row exists.
```
Item 2 was satisfied by `tests/test_realpath_ledger.py` (confirmed
passing in evidence-02) -- a real git repo, real SHAs, real production
`StateMachine`/`ledger` code, but a MANUALLY CONSTRUCTED
`mode=Mode.LOCAL` StateMachine, not an invocation through the project's
actual entry points. It proves the writer mechanism works; it does not
prove the writer is reachable the way forge is actually run.

Item 3 (the dogfood hard gate) requires a real manual ruling from the
main session to exist before the phase could be called done. G1 (zero
ledger rows anywhere on this machine, re-verified in evidence-01) means
that row, if it was ever produced, does not exist today -- either the
gate step was not actually completed/verified before closure, or it was
produced and then lost (plausibly to the worktree mechanism in
evidence-05, if the ruling was made inside the phase's own
`.worktrees/ledger` worktree, per this same dispatch's own Phase-0
instruction: `git worktree add .worktrees/ledger -b ledger`, line 112).
This diagnosis cannot tell which of these two happened from artifacts
alone -- see report, section 5.

## Phase 43 IS recorded as complete

`.planning/STATE.md:248`:
```
Completed: 37 user-config (6fb427e), 37.1 F5+F1 (965c247),
38 setup-mcp (07d0381), 38.1 stale-guard (0a85662),
Phase 43 LEDGER (14328bb), 38.2 PDEATHSIG (9f96fd5), ...
```
`.planning/STATE.md:239-240`:
```
only hard prereq (Phase 43 provenance) is merged (14328bb), so
pulling 51 forward to post-43 is PERMITTED
```
Confirmed: Phase 43 is not "in progress" or "blocked" -- it is closed,
merged at 14328bb, and treated project-wide as a satisfied prerequisite
for Phase 51 and (per ROADMAP.md) the entire v2.9 ENV-GROUNDING arc
(44/51/52/53a/53b).
