# xdg-fix2 R3 review record

- Date: 2026-08-14
- Job: 73665205-bd71-4f59-971c-b4d2b57aaddb (MCP forge_review, backend deepseek-direct)
- Worktree: /home/houminxi/code/forge/.worktrees/xdg-fix2 (branch fix/xdg-backend-fallback2)
- Wall: 1689.6s, 533212 tokens

## Confirmed findings and disposition

All seven confirmed against the real code before fixing. R4 (job d43a75a2)
re-reviewed the full P2b diff including these fixes.

| # | Area | Finding | Fix |
|---|------|---------|-----|
| A | machine.py | Cross-diff receipt clobbering: a changed diff restarting at cycle 0 overwrites the previous diff's receipt-cNpM.json (filenames carry no diff identity) | _continuation_round_index now continues from the highest cycle on disk for ANY diff; docstring rewritten |
| B | machine.py | Except clause too narrow (JSONDecodeError only) | widened to (ValueError, OSError, RecursionError) with comment matching verify.py _load_receipts |
| C | daemon_state.py | backend=None guard sat at the top of run(), killing heuristic/disabled/static-rule paths that never need an LLM | guard moved to just before Q1 llm_invoke; static findings + skip finding returned instead |
| D | llm_invoke.py | Docstring described a stale implicit fallthrough to DEFAULT_BACKEND | docstring now states None raises LLMInvokeError |
| E | receipt.py | str(None) laundering: non-string excerpt content was stringified, turning a missing excerpt into the literal string "None" that passes the schema check | non-string content left as-is so _validate_receipt_schema rejects it; list-of-lines still joined |
| F | test gap | Outside-post-image rejection path had no test | TestOutOfHunkExcerpts::test_excerpt_tail_outside_the_post_image_is_rejected added; bug-injection proved (disabled check -> FAIL, restored -> PASS) |
| G | pre-existing | Unused imports in tests/test_daemon_state.py (io, subprocess, Path, call, pytest) and tests/test_canary_gen.py:441 (io, function-local) | removed; ruff clean on all changed files |

## Additional finding from full-suite run (post-R3)

P1 (4582ce6) shipped `if True:` in cli.py _run_hold_loop, shorting the
hold-resume UI: LOCAL PENDING runs returned without ever entering HOLD.
Three pre-existing failures in tests/test_cli_hold_resume.py traced to it
(failures reproduce on clean main). Fix: cost reporting stays unconditional
(that was P1's intent -- PENDING spends tokens too), but the return is
gated on `verdict != Verdict.PENDING` again. test_none_state_fallback
updated for the new load_state call order (cost block now loads once per
cycle before the HOLD branch). test_changed_hash_restarts_at_zero renamed
and re-contracted for finding A's new behavior.

## Verification

- bug-injection E: injected str() branch -> 2 FAIL, restored -> 10 PASS (test_receipt.py)
- bug-injection cli.py: injected `if True:` -> 3 FAIL, restored -> 4 PASS
- full suite: 3280 passed, 9 skipped, 0 failed (see R4 record for the final run)
- Step 0: py_compile OK, ruff clean on all 20 changed files, non-ASCII 0 added lines
