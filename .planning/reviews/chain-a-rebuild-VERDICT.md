# Chain A rebuild -- acceptance review

Reviewer: main session (manual, adversarial). Forge review was NOT run --
the deepseek backend was stalling, and the sub-session handed the change
over unreviewed.

Subject: `.claude/worktrees/chain-a-rebuild`, 6 files staged on top of
`c72ff06`, +282/-4. Branch ref itself still points at `c72ff06`; nothing
is committed.

## Claims checked against ground truth

| Claim from the handover | Verdict |
| --- | --- |
| 6 files, +282/-4 | TRUE -- `git diff HEAD --stat` |
| 97 tests pass | TRUE -- `pytest tests/test_verify.py tests/test_receipt.py -q` |
| Fixes the earlier round's findings | TRUE for 4 of 5; the exempt-file one has no test |
| Review not run | TRUE, and it shows -- see F1..F3 |

Two facts the handover did not state:

1. This supersedes `e755156` (`fix/excerpt-fabrication-guard`). Same 6
   files, same `-4`. The 33-line difference is entirely comments and
   line-wrapping -- no functional code was dropped. `e755156` sits on the
   older base `bd9e268`; this sits on `c72ff06`.
2. `e755156` already carried F1 and F2. They are inherited, not new.

## Bug-injection results (inject at the fix, revert, re-run)

| Injection | Result |
| --- | --- |
| verify.py: disable the fabricated-lines refusal | test FAILS -- covered |
| verify.py: revert the mismatch message to start_line | test FAILS -- covered |
| receipt.py: disable the pre-flight block | test FAILS -- covered |
| verify.py: revert exempt guard to `.get(..., {})` | **97 still green -- NOT covered** |

## Findings

### F1 [HIGH] pre-flight false-warns on every exempt file

`receipt.py`, the `file_lines is None` branch. A binary, rename, or
mode-change file has no post-image, so `_extract_post_image_lines` never
produces a key for it and the pre-flight logs

    pre-flight: excerpt img.png:1-1 references file img.png not in diff
    post-image; verify may refuse this

Verify does not refuse it. `parse_diff_hunks` puts those files in
`exempt_files` (diff.py:197-203), `verify.py:412` lets them through, and
STEP C skips them outright. Measured, not inferred: a binary-only diff
fed through `write_receipts` emits exactly this warning while the same
excerpt passes `run_verify`.

The pre-flight cannot tell an exempt file from an invented filename --
both are simply absent from `post_image`. For the invented-filename half
the message is correct; for the exempt half it is a false alarm on every
review that touches an image, a rename, or a chmod.

`e755156` wrote the ambiguity down in a comment ("either the file is
exempt (binary/rename -- STEP B handles it) or the excerpt is
fabricated"). This rebuild dropped that comment and kept the message,
so the one signal that the case was known is gone.

Fix: take `exempt_files` from `parse_diff_hunks` and skip them, or split
the message so the two cases read differently.

### F2 [HIGH] the fabricated-lines algorithm exists twice, verbatim

`verify.py` and `receipt.py` each carry the same 16-line block. Only the
variable names (`exc["end_line"]` vs `end`) and the last line (`return`
vs `warning`) differ. The 100-element cap, the `max_line` bound, and the
two-phase in-range/beyond-range split all have to stay in lockstep by
hand; the first edit that touches one and not the other makes the
pre-flight warn about lines verify accepts, or stay silent on lines it
refuses.

Both files already import `_extract_post_image_lines` from `.diff`.
The block belongs next to it.

### F3 [HIGH] the exempt-file guard has no test

The `if file_lines is None: continue` line is the fix for the previous
round's first finding, and reverting it leaves all 97 tests green.

It is guarding something real. With the old `.get(exc["file"], {})`,
`file_lines` is `{}`, so `max_line` is 0, the whole excerpt range lands
beyond it, and verify refuses:

    excerpt logo.png:1-1 references lines 1 not in diff post-image

That is every review containing a binary, rename, or mode-change file
failing verify. A probe covering it fails on the old code and passes on
the new one; it should be in `tests/test_verify.py`.

`test_preflight_warns_when_file_absent_from_post_image` looks like it
covers this ground but does not -- it uses an invented filename, which is
the half that behaves correctly.

### F4 [MED] the A4 comment's last sentence cannot be true

`machine.py`: the comment correctly explains that the three
`cost_per_pass` entries are identical by construction (`// 3`, `/ 3.0`),
then closes with "Duration collapse (all passes same wall-clock time) is
the practical replay detector."

`duration_s` is `round(self._round_duration / 3.0, 3)`, computed inside
the loop without reference to `i`. All three durations are always equal,
replay or not, so within `cost_per_pass` that detector can never fire.
A comment written to prevent one misreading ends by inviting another.

### F5 [LOW] `exc` names two different things in one function

`receipt.py`: `except Exception as exc` (the exception) then `for exc in
assembled_excerpts` (an excerpt dict). Harmless -- Python deletes the
`except ... as` name at the end of the block -- but this is the same
class of problem as the `l` -> `x` rename the previous round already
took, and the shadow here is between two live concepts rather than one
short name.

### F6 [LOW] the private-import justification is inconsistent

`receipt.py` carries a four-line comment explaining why it imports
`_extract_post_image_lines`. `verify.py:23` imports the same function
with no comment at all. Either drop the comment or make the function
public; carrying the explanation in exactly one of two call sites is
the worst of both.

### F7 [LOW] the 100-cap only applies to half the loop

The beyond-`max_line` branch caps at 100 entries, but the in-range loop
appends without limit. An excerpt spanning a large sparse post-image can
still build a list of every missing line and inline it into the message.
Bounded by the diff already in memory, so this is message length, not
exhaustion.

## Mechanical pre-check

    py_compile   OK
    ruff         All checks passed
    non-ASCII    0 hits on the diff

## Verdict

The four A-steps do what they say and three of the four are held by tests
that fail when the fix is removed. F1 and F2 are the ones worth fixing
before this lands; F3 is a missing test for code already written. None of
them are reasons to redo the work.

---

# Fixes applied

All seven, in one pass. Diff is now +392/-5 across 7 files.

`diff.py` gained `describe_fabricated_lines(file_lines, start_line,
end_line, cap=100)`. The two copies in `verify.py` and `receipt.py` are
gone; both now call it. Merging them also collapsed the old two-phase
in-range/beyond-range split into one loop -- a line is fabricated when it
is absent from the post-image, and whether it falls in a gap between hunks
or past the end of the file was never a distinction worth two branches.

| Finding | What changed |
| --- | --- |
| F1 | pre-flight takes `exempt_files` from `parse_diff_hunks` and skips them; the remaining warning says "not in the diff", which is now the only case left |
| F2 | one implementation in `diff.py`, two callers |
| F3 | `test_exempt_file_excerpt_is_not_read_as_fabricated` |
| F4 | the A4 comment no longer claims duration can detect a replay |
| F5 | `except Exception as err`, `for excerpt in ...` |
| F6 | justification comment dropped; the new shared function is public |
| F7 | cap applies to the whole list, proven by `test_fabricated_line_list_is_capped` |

Also dropped a model name and review ID from a test docstring.

## Bug-injection, second round (7 sites, all on the fixed code)

| Injection | Result |
| --- | --- |
| `diff.py`: remove the cap | caught |
| `receipt.py`: stop skipping exempt files | caught |
| `verify.py`: revert exempt guard to `.get(..., {})` | caught |
| `verify.py`: disable the fabricated refusal | caught (3 tests) |
| `receipt.py`: disable the pre-flight block | caught (2 tests) |
| `verify.py`: revert the mismatch message | caught |
| `diff.py`: only treat beyond-max lines as fabricated | caught |

md5 of all three sources matches the pre-injection copy.

## Mechanical pre-check, after fixes

    py_compile   OK
    ruff         All checks passed on the 7 changed files
                 (10 errors remain in __init__.py, canary_gen.py, cli.py --
                  pre-existing, untouched here)
    non-ASCII    CLEAN

## Suite

    before fixes   3172 passed, 9 skipped, 4 warnings, exit 0, 687s
    after fixes    3175 passed, 9 skipped, 3 warnings, exit 0, 534s

The three added tests account for the delta exactly.

## Real-path smoke

Mocks only prove the code matches the diff I imagined, so: a real git
repo, one staged change set carrying all four shapes at once.

    post_image keys : ['a.py']
    exempt_files    : ['logo.png', 'new.py', 'run.sh']

                          binary, rename, mode-change

    pre-flight             no warnings
    verify                 passed, all 8 checks
    verify, same excerpt padded past the hunk
                           refused -- "excerpt a.py:1-4 references
                           lines 3, 4 not in diff post-image"

The last one is the case this exists for: the first two lines of that
excerpt are correct. Matching content on the lines the diff produced buys
nothing for the lines it did not.

## Landed

`fd98d12` on `chain-a-rebuild`, declared wip -- the review here is manual,
not a receipt set. Nine receipts still have to be earned before this
reaches main.

`e755156` (`fix/excerpt-fabrication-guard`) is superseded and can be
dropped. It is unmerged, so `git branch -d` will refuse it.
