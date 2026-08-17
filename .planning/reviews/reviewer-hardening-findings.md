# reviewer-hardening -- findings, dispositions, evidence

Fixes in this batch: JSON fence handling (reviewer_json.py) plus two
findings from the 2026-08-15 route-probe reviews (gemini3.1 / gemini3.6
/ lite, all reviewing the P2b code with forge's own L1 prompt).

## Finding 1 -- fence-wrapped JSON rejected (CONFIRMED + FIXED)

gemini3.6 and gemini3.1 both wrap their review JSON in ```json fences;
validate_reviewer_json does a bare json.loads, so every Gemini reply was
REJECTED at line 1. Reproduced 4/4 times across the two routes.

Fix (two layers):
- REVIEW_JSON_CONTRACT now ends with "Reply with the JSON object only --
  no markdown code fences, no surrounding text."
- validate_reviewer_json strips a complete fence envelope before parsing
  (_strip_fence). Only a full envelope (opening fence line + closing
  fence line, nothing outside) is stripped; anything else parses as
  before and fails closed.
- Tests: tests/test_reviewer_json.py (12 cases). Bug-injected by
  removing the strip call: 3 fence tests FAIL, restored: 12/12 PASS.

## Finding 2 -- _constant_offset skipped when overlap_lines is empty
## (DISMISSED -- unreachable scenario, two models both wrong)

gemini3.1 (P2) and gemini3.6 (P3) independently reported: the misnumber
check sits inside `if overlap_lines:`, so an excerpt whose claimed range
has zero overlap with the post-image reports "outside the diff" instead
of "misnumbered by +N".

Reachability analysis (experiment + code read):
- verify.py: hunk gate first: a claimed range that intersects no hunk is
  rejected as "outside every hunk" before STEP C runs.
- hunk ranges are [target_start, target_start + target_length - 1]
  (parse_diff_hunks), and post_image holds every added+context line's
  target_line_no (_extract_post_image_lines). Both come from the same
  hunk numbers, so any claimed line inside a hunk range lands in the
  post-image: overlap is non-empty on every path that reaches STEP C.
- The one exception is a deletion-only hunk (target_length 0, end ==
  start): overlap is empty there, but the post-image is empty too, so no
  delta can ever be matched -- "outside the diff post-image" is the
  correct diagnosis and misnumbering is not verifiable.
- Experiment: swept four diff shapes (pure-add, pure-del, mixed,
  two-hunks) over claimed ranges 1-8; zero-overlap claims that pass the
  hunk gate exist ONLY for pure-del, where file_lines == {}.

The models read the control-flow shape (guard around the offset check)
without tracking the invariant that makes the empty case unreachable.
No fix; the guard stays. This is the second time a finding pair from the
probe reviews failed reachability (the first being the machine.py
max_cycle pair, which did survive -- see below).

## Finding 3 -- max_cycle_this_diff redundant (CONFIRMED + FIXED)

lite (P3) and gemini3.1 (P3): _continuation_round_index tracks
max_cycle_this_diff alongside max_cycle_any_diff and returns the max of
both. Mathematically max_cycle_this_diff <= max_cycle_any_diff always
(this_diff updates only when diff_sha256 matches; any_diff updates
unconditionally), so the return reduces to max_cycle_any_diff and the
this_diff track is dead weight. Verified by reading the loop.

Fix: removed the this_diff track and the diff_sha256 comparison; the
return is now the global maximum with the collision rationale preserved
in the comment. Identity transform -- the max of a value and its
own subset-bound sibling. test_machine_ci.py + test_machine_local.py:
47/47 pass; no remaining references to max_cycle_this_diff.

## Review R1 (gemini-omniroute, 3 passes, 5 findings) -- dispositions

1. [qodo P1 + adversarial] _continuation_round_index "returns max without
   incrementing, overwrites the highest cycle receipt". DISMISSED:
   receipt.py write_receipts computes cycle = round_index + 1, so a run
   starting at max lands its receipts at max+1. Pinned by
   test_ci_run_does_not_overwrite_a_foreign_diff_receipt (foreign c2 ->
   this run writes c3). Both models read the return without tracking the
   cross-file caller.
2. [qodo P2 + expert] _strip_fence calls raw.strip() before any type
   check: int/list/None input now raises AttributeError instead of the
   ValueError the fail-closed contract promises (json.loads used to
   convert TypeError). CONFIRMED -- a real regression introduced by the
   fence fix. FIXED with an isinstance guard returning non-strings to
   the json.loads TypeError path. Tests: 3 parametrized non-string cases.
   Bug-injected (guard removed): 3 FAIL; restored: 16/16 PASS.
3. [adversarial] text.endswith("```") can amputate JSON whose last
   string ends with backticks. HARDENED: the closing fence must sit on
   its own line (char before ``` is newline); mid-line backticks are
   content. Note the old code failed closed either way (cutting into a
   JSON string breaks the parse), so this is precision, not safety.
4. [runtime advisory] trailing whitespace after the closing fence breaks
   endswith. DISMISSED: raw.strip() at entry already handles it;
   test_fence_with_surrounding_whitespace_accepted covers it.
5. [pre-existing-l0] path doubled for the new test file
   (".worktrees/reviewer-hardening/home/houminxi/.../test_reviewer_json.py"
   -- "No such file or directory"). Forge's own pre-existing-l0 axis
   mis-assembles worktree paths; not introduced here, recorded for a
   follow-up.

R1 verdict: PASS (CI), 5 findings -> 1 fixed regression + 1 hardening,
2 dismissed, 1 advisory dismissed, 1 pre-existing recorded.

## Review R2 (gemini-omniroute, 4 findings) -- dispositions

1. [expert CONFIRMED + adversarial] _continuation_round_index missing
   +1 (same claim as R1 items 1). DISMISSED as substance-free repeat:
   the R1 evidence stands -- receipt.py write_receipts computes
   cycle = round_index + 1, pinned by
   test_ci_run_does_not_overwrite_a_foreign_diff_receipt. The reviewer
   sees only the machine.py diff, not the cross-file caller.
2. [qodo P2 + adversarial] closing-fence check fails on CRLF line
   endings. DISMISSED by experiment: in "...}\r\n```" the byte at -4
   is the \n half of the terminator, so the old check text[-4:-3] !=
   "\n" already accepts CRLF envelopes. Verified with a direct
   validate_reviewer_json call on a CRLF-fenced reply (accepted) plus
   test_crlf_fenced_json_accepted. The models assumed \r sits next to
   the fence; it does not.
   The check was still broadened to `text[-4] not in "\r\n"` as an
   equivalent hardening (the \r branch covers a bare-\r layout that is
   unreachable anyway, since first_nl finds no \n there) and the CRLF
   test stays as a behavior pin.

R2 verdict: FAIL driven by 1 confirmed repeat + 1 misreported CRLF
pair; both dismissed on evidence. Code unchanged except the equivalent
\r\n hardening.

## Review R3 (gemini-omniroute, 4 findings) -- dispositions

1. continuation missing +1, now from all three passes (8 of 9 passes
   across R1-R3). DISMISSED as repeat; the cross-file +1 in
   write_receipts and the pinning test remain the evidence. The
   reviewer diff shows only machine.py, so this blind spot recurs by
   construction.
2. [adversarial, new] closing fence with leading indentation ("  ```")
   fails the char-offset check (text[-4] is a space). CONFIRMED --
   models do indent closing fences. FIXED by replacing the char-offset
   test with a line-based one: the closing fence must sit on its own
   line, only indentation may precede it (rfind("\n") then the tail
   must strip to empty). This subsumes the CRLF case too, so the
   earlier \r\n hardening is folded in. Tests: 2 indented cases added;
   bug-injected by restoring the char-offset check: 2 FAIL, restored:
   19/19 PASS.

R3 verdict: PASS (0 confirmed), 3 repeats dismissed + 1 new finding
fixed.

## Review R4 (gemini-omniroute, 2 findings) -- convergence

Both are the continuation repeat (qodo + adversarial), zero new
substance; fence code drew no findings. Four rounds, dispositions:
R1 fixed 2 (non-string regression + closing-line hardening), R2
dismissed 2 (repeat + CRLF misreport), R3 fixed 1 (indented closing
fence), R4 clean of new substance. The repeat recurs because the
reviewer sees only machine.py and never the write_receipts caller.

Convergence: 4 rounds, no unfixed findings, all dismissals evidenced
above.
