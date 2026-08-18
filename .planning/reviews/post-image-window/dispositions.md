# fix/post-image-window review dispositions

Branch: fix/post-image-window-rebase. Cherry-pick b2c1dd6 -> d27d786, then
fix commit 9ac5b98. Backend: deepseek (sn-deepseek-flash, free OmniRoute).
Mode: --committed.

## Round 1 (receipts c1p*, 2026-08-18) -- 1 CONFIRMED finding, fixed

Finding (falsify CONFIRMED, 22.5s): _window_file_text produces a misleading
window when a hunk's start lies beyond the text in hand -- reachable via the
50KB read cap: lo = max(1, start-context) > hi = min(len(lines), end+context),
the window holds a bare omission marker and no code, under a header promising
"around the changes".

Ground-check before fixing: the finding's "returns an empty string" detail is
imprecise (the actual output is an omission-only marker, not empty), but the
substance is real -- reproduced with text=100 lines, hunk start=500:
output was "... [459 lines omitted]" with windowed=True and zero code lines.

Fix (9ac5b98): skip regions with lo > hi; if every region is out of range,
return the text unwindowed (whole file, plain header). Two tests added:
test_hunk_beyond_eof_falls_back_to_whole_text and
test_out_of_range_hunk_does_not_corrupt_an_in_range_one. Injection-proven:
reverting the guard+fallback turns exactly those two tests red; restore
turns all 22 green.

Round 1 also emitted three test-assertion advisories (dismissed by falsify):
the out-of-range edge case (now covered by the fix's tests), binary/NUL skip
coverage, and multi-file mixed windowing coverage. The first is addressed;
the other two are coverage suggestions, not defects.

Consecutive-clean streak: 0 (finding this round, fixed). Rounds 2+ on the
fixed diff (main..HEAD = d27d786 + 9ac5b98).

## Round 2 (receipts c2p*, 2026-08-18T01:56Z) -- CLEAN (streak 1)
Real run (325s, 71471 tokens -- distinct from round 1, not a replay). All
three passes zero findings on the fixed diff. The round-1 advisories did not
re-emit: the fix's tests closed the out-of-range coverage gap.

## Round 3 (receipts c3p*, 2026-08-18T02:00Z) -- receipts clean, 1 advisory adopted
Receipts all zero; one test-assertion advisory (dismissed by falsify) had a
fair style point: the "... [83 lines omitted]" assertion hardcoded 83, which
is 100-17 and brittle against _text()'s size. Adopted as 3cdfc94 (compute
the count). The diff changed, so the clean streak restarts: rounds 4+ run on
main..HEAD = d27d786 + 9ac5b98 + 3cdfc94.

## Round 4 (receipts c4p*, 2026-08-18T02:05Z) -- CLEAN (streak 1 on final diff)
Real run (169s, 17516 tokens). Zero findings, no advisories. First clean
round on the final diff (d27d786 + 9ac5b98 + 3cdfc94).

## SCOPE CORRECTION (2026-08-18T02:10Z) -- rounds 2-5 reviewed the wrong diff

`--committed` maps to `--baseline HEAD~1 --head HEAD` (cli.py:3645,
mcp_server.py:772) -- the LAST commit only. The MCP forge_review tool has no
baseline parameter. Actual coverage per round:

| round | HEAD | reviewed |
|---|---|---|
| 1 (CONFIRMED lo>hi) | d27d786 | full cherry-pick |
| 2,3 (clean) | 9ac5b98 | fix commit only |
| 4,5 (clean) | 3cdfc94 | test-style commit only |

The full 3-commit diff (main..HEAD) was never reviewed after the fix. The
clean streak recorded above is VOID; it measured per-commit tails, not the
delivery. Full-scope rounds restart via CLI:
`code-forge review --no-color --backend deepseek --baseline main --head HEAD`
(log lines confirm "diff: 2 files"). Round counter for convergence now
tracks full-scope rounds only.

Lesson recorded: the stderr line "reviewing <name> @ <sha> (diff: N files)"
is the scope tell. A shrinking N across rounds on a growing branch means the
review is following HEAD~1..HEAD, not the branch.

## Full-scope Round 1 (receipts c6p*, CLI --baseline main --head HEAD) -- 1 CONFIRMED + 2 UNCERTAIN

F1 CONFIRMED (cli.py:947, adversarial): a 50KB-capped read can end
mid-line; splitlines() then counts the partial tail as a line, hunk
windows shift, and a hunk near the cut yields fragments. Reproduced.
Fix 03e0da5: cut back to the last newline; append the truncation marker
AFTER windowing so it is not numbered as code. Injection-proven:
reverting both parts turns test_truncated_read_drops_the_partial_line
red; restore turns all 24 green. (The first version of that test only
asserted the marker's presence, which is identical pre- and post-fix --
caught by my own inject-red-check, rewritten to assert no partial lines
in the window, hunk placed at line 500 so the window spans the cut.)

F2 UNCERTAIN (cli.py:994, expert): parse_diff_hunks without try/except
could raise on malformed diff. DISPROVED by experiment: empty / garbage /
hunk-header-only / truncated-hunk inputs all return empty maps, never
raise; empty map -> whole file, already the safe fallback.

F3 UNCERTAIN (cli.py:909, expert): windowing drops semantic context and
context_lines=40 is hardcoded. ACCEPTED RISK (design): the branch commit
already argues prompt-context-only, verify rebuilds from the diff, files
covered throughout stay whole. Not a defect.

Advisories (dismissed by falsify, spot-verified): context-zero gap-marker
coverage, context-0 boundary tests, overlapping-unsorted-hunk merge --
all coverage suggestions on already-tested logic paths.

Streak on full scope: 0. Rounds 2+ run on main..HEAD = 4 commits.

## Full-scope Round 2 (receipts c7p*, 2026-08-18T02:38Z) -- CLEAN (streak 1, full scope)
verdict=PASS, confirmed=0, receipts all zero. Three test-assertion advisories
(dismissed by falsify): binary-NUL skip coverage, empty-text, negative
context_lines. The latter two probe-verified correct-by-clamp and pinned in
7e8f4c9 (empty stays empty; negative context returns whole). Binary-skip
coverage skipped: needs a fixture file, low value; noted here as known gap.
Diff changed (test-only), streak restarts at round 3.

## Full-scope Round 3 (receipts c8p*, 2026-08-18T02:48Z) -- CLEAN (streak 2)
verdict=PASS, confirmed=0. c8p3 carries 1 UNCERTAIN: the nl=-1 (no newline
in the first 50KB) case. Experiment shows the post-F1-fix behavior is
already honest: truncated fragment + unnumbered "truncated at 50KB" marker,
no pretense of a whole file. This is the same (file, category) as round 1's
CONFIRMED F1, already fixed -- a substance-free re-raise, does not reset
the streak per CLAUDE.md. Three advisories are self-acknowledged "could be
stronger" coverage notes on pinned behavior; skipped.

## Full-scope Round 4 (receipts c9p*, 2026-08-18T02:52Z) -- cached replay, streak holds at 2
Identical token counts to round 3 (125405, 0.2s) = cached replay. Same
nl=-1 finding, same hash c8714f0f29b724b2, third substance-free emission.
Discriminating experiment run: giant-first-line file yields zero numbered
lines, plain header, unnumbered truncation marker, honest 50KB fragment --
the F1 fix is complete on this path. The re-raise happens because the tree
never said the shape was intended; pinned in the latest commit
(test_single_giant_line_reports_truncation_without_numbering). Diff changed
(test-only); streak on the new diff restarts at round 5.

## Full-scope Round 5 (receipts c10p*, 2026-08-18T03:04Z) -- 1 CONFIRMED, fixed
CONFIRMED (cli.py:909): negative context_lines inverted every region, the
lo>hi guard dropped them, and the empty-fallback returned the whole file --
three individually-correct parts composing into the opposite of narrowing.
Worse, my round-2 pin had named this "behaves as zero" while asserting the
whole-file shape, so the tree agreed with the bug. The reviewer's complaint
was about MY test, and it was right. Fix (latest commit): clamp context to
zero up front; test renamed to test_negative_context_clamps_to_zero and now
asserts the clamped window. Injection-proven (clamp removed -> red;
restored -> 27 green). Streak restarts on the new diff at round 6.

## Full-scope Round 6 (receipts c11p*, 2026-08-18T03:14Z) -- CLEAN (streak 1 on final diff)
verdict=PASS, confirmed=0, dismissed=2. c11p3's dismissed finding questions
the window-merge against a violated sort invariant -- but ascending sort is
the code's own stated precondition (comment at the merge site), so this is a
hypothetical, not a defect. Two advisories are "could assert more" notes on
already-pinned shapes; skipped.

## Full-scope Round 7 (receipts c12p*, 2026-08-18T03:19Z) -- CLEAN (streak 2)
Cached replay (2.4s, identical tokens). Same merge-invariant finding
(936d6af27d22bb05), DISMISSED a second time by falsify; it argues a
hypothetical violated-sort precondition, and the ascending sort is the
code's own stated invariant. Substance-free repeat; streak unaffected.

## Full-scope Round 8 (receipts c13p*, 2026-08-18T03:21Z) -- CLEAN (streak 3, CONVERGED)
Cached replay; same merge-invariant finding DISMISSED a third time.
confirmed=0 across rounds 6/7/8 on the final full-scope diff (main..HEAD,
7 commits). CONVERGED.

Final branch: fix/post-image-window-rebase, 7 commits:
d27d786 (cherry-pick) + 9ac5b98 (lo>hi fallback) + 3cdfc94 (computed
omission count) + 03e0da5 (50KB newline cut + marker after windowing) +
02d6dc9 (empty/negative edge pins) + 907daa2 (giant-line pin) + 2cb3749
(negative-context clamp).
