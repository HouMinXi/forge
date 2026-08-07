# Commit plan, worktree .worktrees/anchor-infra

Written 2026-08-01. Nothing is committed: `check_git_commit_review.sh`
blocks logic-bearing commits until a forge review has run, and no usable
review backend exists right now (see BLOCKER below). Everything described
here is sitting in the worktree, tested and gated, waiting for that.

Branch: `defects/infra-anchor-poison`, based on main @ 1fb3eea.

## What is in the worktree

    src/code_forge/machine.py      early abort + TimeoutBreaker docstring
    tests/test_machine_local.py    2 new tests, 1 existing test adjusted
    src/code_forge/verify.py       check 8
    src/code_forge/receipt.py      anchor filter (INFRA excluded)
    tests/test_verify.py           helper + 4 tests (2 anchor, 2 check-8)
    src/code_forge/llm_invoke.py   retry log carries the exception

Full suite after all of it: 3075 passed, 9 skipped, 0 failed (462s).
Gates run and clean: non-ASCII on the diff, py_compile, ruff, banned
review vocabulary.

Note on an earlier number: a run reporting "2072 passed, 1 failed" was
`pytest -x`, which stopped at the failure with about a thousand tests
never reached. It is not comparable to the 3075 above.

## The three commits, in this order

**1. machine: early abort.** Only machine.py + test_machine_local.py.
Verified to stand alone: with the other four files stashed, 114 tests pass
on this commit's content by itself. Message drafted at /tmp/c1_msg.txt
(regenerate if gone; the content is in this session's transcript).

**2. verify check 8 + receipt anchor filter, together.** verify.py +
receipt.py + tests/test_verify.py in ONE commit, deliberately.

The ordering constraint that matters: the anchor filter REMOVES the only
signal that a pass never ran (the `<llm-invoke>` sentinel anchor), and
check 8 is what replaces it. Landing the filter first opens a window where
neither exists. They cannot be split anyway -- the check-8 tests use
`_run_with_failed_pass`, a helper introduced by the anchor work, so a
commit with one and not the other has tests that do not run. Same commit
is stronger than ordered commits here: no window at all.

**3. llm_invoke: retry log carries the cause.** Independent of the other
two. Arguably `# chore`, but it is inside a retry loop, so classify it
honestly rather than reaching for the cheap marker.

## Why the commits are blocked, precisely

The hook wants a review. Backends available:

- `deepseek` / `deepseek-direct`: measured unusable for reviews on
  2026-08-01. One round completed a single pass and timed out the other
  two; a standalone single call did not return in ten minutes. Not a
  timeout-tuning problem, so raising timeout_s does not help.
- `gemini-pro`: the model is fine (77-295s per call, correct content) but
  OmniRoute drops forge's concurrent passes. Its request queue gives a
  waiting job 15000ms and then 503s it, and forge fans out three passes at
  once with no flag to reduce that. Fix is on the OmniRoute side: raise
  `resilienceSettings.requestQueue.maxWaitMs` (Settings -> Resilience at
  http://192.168.100.10:20128) to something above 3x the single-call
  latency. 600000 is the suggested value. Detail in global memory
  `feedback_omniroute_queue_drops_concurrent`.
- `gemini-omniroute`: the Flash route with the empty-content bug on
  security-adjacent prompts. Do not use for this diff.

So the unblock is one setting change on X500, not a forge change.

## Which copy of the code runs, when forge reviews itself

Measured, because the two answers differ and guessing gets it wrong:

    pytest (the gate.yaml test command)  -> the WORKTREE's src
    the code-forge binary itself         -> the MAIN TREE's src

pyproject.toml sets `pythonpath = ["src"]`, resolved against pytest's
rootdir, so a pytest run started inside the worktree imports the worktree's
modules. Verified by importing code_forge.verify from a throwaway test and
printing `__file__`: it resolved inside .worktrees/anchor-infra and the
module contained check 8. A bare `python3 -c "import code_forge"` from the
same directory resolves to the main tree instead, because nothing puts
`src` on the path and the editable install points there.

Consequence, and it cuts both ways. The test gate DOES exercise the changed
code, so a green R1/R2 here means something. But the review binary runs the
main tree, so this diff's own changes to llm_invoke.py (retry log) and
machine.py (early abort) do NOT take effect during the review that judges
them -- a failing round in that review still prints the old bare retry line
and still burns all twelve rounds. Do not read the review run as a
real-path smoke test of those two changes. Setting PYTHONPATH to the
worktree would change that, but it also means reviewing with a
half-installed forge; not worth it.

## Do not "fix" this by marking the commits chore

All three touch control flow. The marker exists to classify honestly, and
a review that never ran must not be recorded as one that did -- which is
the same class of false signal these commits were written to remove.

## Bug-injection record (each guard proven by removing it)

    A  delete the call site in _execute_round   -> early-abort test red
    B  delete the counter reset                 -> recovery test red
    C  disable check 8's refusal                -> pass-never-ran test red
    D  drop check 8's tolerance for a missing
       pass_status                              -> 11 EXISTING tests red

D is the one worth remembering. The "natural" way to write the check is
`if status != "completed"`, and that rejects every receipt written without
the field -- eleven fixtures in this repo alone. pass_status is not
documented in SKILL.md, so hand-written receipts legitimately lack it. The
tolerance was not foresight: it came from counting the receipts on disk
first (204, all carrying the field, 189 completed / 12 error / 3 timeout)
and from the schema comment at the top of verify.py, which records the
same mistake being shipped once before.
