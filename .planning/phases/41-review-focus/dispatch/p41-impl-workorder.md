# forge Phase 41 (review-focus) -- IMPLEMENTATION WORK ORDER

STATUS: draft for PM/user approval BEFORE dispatch. Executor: mimo-pro (via aicc)
or an equivalent implementer. You are the IMPLEMENTER. A SEPARATE reviewer reviews
your work afterward -- you never review your own change.

## 1. Task
Implement forge Phase 41 "review focus" exactly per the plan at
`.planning/phases/41-review-focus/41-PLAN.md`. That plan is CP1b-converged and is
the AUTHORITATIVE spec -- READ IT IN FULL and implement what it says. Do not
re-design, re-scope, or "improve" beyond it. Where this order and the plan appear
to differ, STOP and report -- do not guess.

The plan delivers three things: (a) rename the prompt "## Contract Reference"
header to "## Design Intent" in all 3 prompt builders; (b) add a trust-gated
review-focus emphasis mechanism (gate.yaml `review_focus:` + `--focus FILE` CLI
flag + MCP `focus` param, merged into a "## Review Focus" section on all 3
builders, distinct from design-intent, with its own trust hash); (c) add committer
date to git-blame attribution. The full Must-Haves list is in the plan header.

## 2. Scope fence (IN / OUT)
IN -- implement per plan, in wave order:
- Wave 1: Task 1 (header rename, 3 sites), Task 2 (blame date), Task 3a (focus plumbing)
- Wave 2: Task 3b (focus tmpfile wiring INSIDE _dispatch_cli)
- Wave 3: Task 3c (gate schema + template + tests), Task 4 (blame degradation)
- Wave 4: Task 5 (full suite, zero regressions)

OUT -- do NOT touch; these are ALREADY MERGED to main (PM-verified as ancestors of
HEAD this session), and rebuilding them is a scope violation:
- Sampling contract_spec wiring (Task 3b-5 / D5.7) -- merged as `2edb9d4`
  ("mcp/sampling: wire contract_spec through sampling dispatch path"). DONE.
- tmpfile-leak fix + CLI-dispatch centralization (M3 / 3b-3) -- merged as `5c8e001`
  ("mcp: centralize CLI dispatch to close tmpfile leak..."). DONE.
  Task 3b MIRRORS the existing contract_tmp lifecycle inside `_dispatch_cli`
  (mcp_server.py:647-697); it does NOT re-add the old scattered forge_review sites.
- Any other phase; the kimi proxy tooling (a different repo). Not your change.

## 3. Ground truth (PM-verified this session -- build on this, do not re-discover)
- Base: forge `main @ ca0d860` ("llm: surface CLI-path non-JSON diagnostic in
  str(exc)"). No implementation branch exists yet -- you create it.
- The plan is CP1b-closed (external review R1-R6 converged). Implement it; do not reopen it.
- Anchors still valid on main (RE-VERIFY each with grep immediately before editing;
  if an anchor moved, STOP and report -- never edit a guessed line):
  - Header rename: cli.py:780, factories.py:281, factories.py:576
  - Blame: git.py:358 (git_blame parser), legacy.py:~232-243 (attribution build)
  - Merge helper: cli._merge_contract_spec (cli.py:1828)
  - Trust anchors: hash_backends_block cli.py:99, is_trusted :125,
    hash_contracts_content :243
  - _dispatch_cli lifecycle: mcp_server.py:647-697 (the pattern Task 3b mirrors)

## 4. How to work (forge rules -- non-negotiable)
- Phase 0: create a worktree and work ONLY there.
  `git worktree add .worktrees/p41-impl -b phase-41-review-focus`
  then `ln -sf "$(git rev-parse --show-toplevel)/CLAUDE.md" .worktrees/p41-impl/CLAUDE.md`.
  Never edit main. Never merge. Never push.
- TDD where the plan specifies; every new/changed logic line executed by >=1 test.
- Per-site bug-injection where the plan requires it. Task 1: revert EACH of the 3
  header sites SEPARATELY -> its covering test FAILS -> restore -> PASSES. A
  single-site inject proves only one of three. Inject AT the changed site, not near it.
- Step 0 before each commit: `python3 -B -m py_compile` on changed files + `ruff check`
  (or the repo's configured linter); zero NEW warnings. Non-ASCII gate on every
  changed file, must be empty:
  `git diff HEAD --diff-filter=AM -U0 | grep '^+' | grep -P '[^\x00-\x7F]'`
- Atomic commits: one per task (or sub-task), in wave order. Commit messages: WHY not
  WHAT; NO plan-refs (`D-xx`, task numbers, `F8:`), NO P0-P3/severity labels, NO
  bullet inventories; end with `Signed-off-by: Minxi Hou <houminxi@gmail.com>`.
- Do NOT run the forge 3-cycle review yourself (impl != reviewer; a separate pass does it).

## 5. Output contract (return EXACTLY this -- real output, never narrated)
- Branch name + worktree path.
- One commit SHA per task, in order, each with its one-line subject.
- `git diff --stat master..HEAD`.
- The RAW output of each task's `verify:` command from the plan -- the actual grep
  results and the actual `pytest` output (paste the real terminal text; do NOT
  summarize as "all passed"). The PM re-runs all of it from scratch.
- A per-task status line: DONE or BLOCKED. For any BLOCKED, the exact reason plus
  file:line evidence.
Do NOT merge, do NOT push, do NOT declare the phase "complete." Your job ends at a
branch plus the artifacts above. The PM verifies and routes review.

## 6. Honest-failure pre-authorization (READ THIS -- it defines what counts as success)
A truthful "Task X is BLOCKED: the plan says <A> but the code at <file:line> actually
does <B>" is a SUCCESS and exactly what is wanted. You are graded on the REAL diff and
the REAL test output, which the PM re-runs independently. Therefore:
- Never invent or edit test output. Never claim a test passed that you did not run.
- Never skip a required bug-injection to save time -- an un-injected assertion is unproven.
- Never paper over a plan-vs-code mismatch by guessing -- STOP and report it.
- If you run low on budget mid-wave, commit what is real, mark the rest BLOCKED, return.
  A partial honest result beats a complete fabricated one.
The ONLY real failure here is a claim the PM's re-run contradicts. Honesty is free;
fabrication is the one unrecoverable error.
