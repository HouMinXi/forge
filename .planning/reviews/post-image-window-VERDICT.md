# post-image windowing -- verdict

Branch `fix/post-image-window`, commit `b2c1dd6`, 2 files +274/-3.
Worktree `.claude/worktrees/postimage-window`.

## What was wrong

`_assemble_post_image` (cli.py) put the FULL content of every changed file
into every L1 review prompt. Two consequences:

- Its size tracked the size of the FILES, not the size of the CHANGE. A
  one-line edit to a large module cost as much as rewriting it.
- It ran once per pass, three passes per round, three rounds per review --
  so the whole-file content was paid nine times.

Measured on the chain-a diff (7 files, +392 lines): the post-image block was
87% of the input tokens, nearly all of it code the diff never touched.

## The fix

Keep only the neighbourhood of each hunk (default 40 lines of context),
merge overlapping windows, and mark each gap with the number of lines
dropped so a gap cannot be misread as the end of the file. A file whose
windows already cover everything is returned whole and keeps its plain
`## File: x` header; a narrowed one is labelled `x (around the changes)`.

## Why this cannot weaken verify

Two different things are called "post-image" in this codebase:

- `_extract_post_image_lines` (diff.py) -- verify rebuilds the post-image
  from the DIFF to check excerpt line numbers.
- `_assemble_post_image` (cli.py) -- the prompt block, built by reading
  files off DISK.

Only the second one is narrowed. Verify never reads it. Excerpt checking is
byte-for-byte unaffected.

## Measured savings

Re-measured on the committed code. `whole` = the old behaviour
(context_lines=10**6), `win40` = the new default, `win0` = hunk lines only.
Figures are est. tokens (chars/4) for one pass; the x9 row is a full review.

| case | whole | win40 | win0 | saved @40 |
|---|---|---|---|---|
| chain-a, 7 files +392 | 47,671 | 17,070 | 10,554 | 64% |
| (x9, full review) | 429,039 | 153,630 | 94,986 | |
| one file, diff.py | 4,563 | 2,237 | 1,467 | 50% |
| (x9) | 41,067 | 20,133 | 13,203 | |
| wide, 50 commits | 478,383 | 326,679 | 247,143 | 31% |
| (x9) | 4,305,447 | 2,940,111 | 2,224,287 | |

The saved% is lower than the 87% share because the annotated diff and the
JSON contract are fixed overhead that windowing does not touch, and win40
still carries 40 lines around every hunk.

## What it costs in wall clock, on the real route

Token counts are not the complaint. "A slightly bigger diff and the review
takes forever" is. Measured against sn-deepseek-flash through OmniRoute,
same diff, same max_tokens as gate.yaml, prompt assembled exactly as
`_make_subagent_spawn` assembles it (script:
`scratchpad/ab_window_latency.py`):

| post-image | est.tok | wall | reasoning tok | content chars | in / out |
|---|---|---|---|---|---|
| whole | 48,172 | 367.2s | 5754 | 5,121 | 51,239 / 7,191 |
| win40 | 17,571 | 91.6s | 5600 | 19,033 | 20,744 / 10,970 |

Four times faster on one pass, so roughly 37 minutes back on a nine-pass
review.

Two things in there are worth not glossing over:

- **The reasoning did not shrink.** 5754 vs 5600 is noise. The saved time
  is prefill -- reading 51k tokens of input -- not thinking. An earlier
  note of mine claimed reasoning grows with the prompt and that windowing
  would cut it; this measurement does not support that, and the claim is
  withdrawn. (A separate observation on oc-ds-flash-free did show
  reasoning scaling with prompt size. Different model, different prompt;
  neither result generalizes to the other.)
- **The narrowed run produced MORE output**, not less: 19,033 characters
  against 5,121. Whatever the whole-file version was doing with those
  extra 30k tokens, writing findings was not it.

n=1 per row and sampling is not deterministic, so the reasoning and
content figures are indicative only. The 4x wall-clock gap is far outside
that noise.

## Bug injection (8 sites, all caught)

Each injection was applied at the fix itself, run, reverted, and the file
md5-compared against its pre-injection copy.

| # | injected | caught by |
|---|---|---|
| K1 | drop the `kept >= len(lines)` whole-file short-circuit | `test_a_file_changed_throughout_comes_back_whole` |
| K2 | disable the overlap merge branch | `test_overlapping_windows_do_not_repeat_lines` |
| K3 | drop `max(1, ...)` on the low bound | `test_hunk_at_file_start_does_not_underflow` |
| K4 | drop `min(len(lines), ...)` on the high bound | `test_hunk_end_past_eof_is_clamped` |
| K5 | stop emitting the line-number prefix | `test_kept_lines_carry_their_numbers` |
| K6 | stop emitting the trailing gap marker | `test_hunk_at_file_end_does_not_overflow` |
| K7 | always label as narrowed | `test_whole_file_keeps_a_plain_header` |
| K8 | drop the `sorted(hunks, ...)` | `test_hunk_order_does_not_change_the_result` |

K2 is worth recording: the first attempt at it did NOT fail. The hunks I had
built were already overlapping after context was applied, so `max(hi)` alone
reproduced the merged output and the test stayed green. Comparing the actual
output rather than assuming showed it. Rebuilt with a genuinely overlapping
pair, disabling the merge emitted duplicate lines (15 -> 22, 31 -> 42), which
is the real defect the branch prevents.

K8 came out of that same look: the merge only compares against the previous
region, so it silently assumes hunks arrive ascending. git emits them that
way, so this held by luck of the caller. `sorted()` makes it hold by
construction.

## Real-path check

Built actual git repos and took real `git diff` output rather than
hand-writing diff text. That caught a false claim in my own first docstring:
it said exempt files (binary, rename, mode-change) "pass through whole". They
do not arrive at all -- `get_changed_files` lists only files carrying an
ADDED line, and a pure rename has none. Pinned as
`test_rename_only_never_reaches_the_post_image`, which asserts empty output
at both context_lines=5 and context_lines=10**6.

## Checks

- `python3 -m py_compile` on both files: OK
- `ruff check` on both files: clean (one F401 of my own, fixed before commit)
- non-ASCII gate on the diff and on the new file: no hits
- new file standalone: 20 passed
- full suite: **3186 passed, 9 skipped, exit 0, 603.45s**

That run did NOT carry `--ignore=tests/test_cli_integration.py`, so it is
37 tests WIDER than the gate's own command, not narrower. Reconciled
rather than assumed: 3158 collected with the ignore, plus 37 in that file,
is 3195, and 3186 + 9 skipped is 3195 exactly. Recorded because two
suite numbers that disagree are an observation, not a mechanism, and the
gap here turned out to favour the change.

## Not done

This branch was committed under `FORGE_COMMIT_CLASS=wip` to preserve the
work. It has NOT been through forge's own review, and must be before it
lands on main.
