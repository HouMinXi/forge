# xdg-fix2 R6 review -- sn-deepseek-flash, 2 L1 findings (2026-08-15)

Backend: deepseek (OmniRoute sn-deepseek-flash). Job 449a94e9,
498s wall, 353338 tokens. passes=1/3: qodo + expert completed,
adversarial died on five 502s from OmniRoute (INFRA, retried 5x).

## Finding 1 -- expert/error: _continuation_round_index makes a
   re-reviewed diff's cycles non-consecutive so verify never passes
   (machine.py:530)

DISMISSED by experiment and code reading:

- verify check 1 computes last_n from the cycle set of ALL receipts on
  disk, then check 2 requires the sha of those last_n receipts to match
  the current diff. After a diff is re-reviewed for its full three
  cycles, the last N cycles belong entirely to that diff, so both
  checks pass. Experiment (receipts written by write_receipts: A=c1,
  B=c2,c3, A re-run=c4,c5,c6; run_verify for A) reached the hardened
  excerpt checks -- continuity and hash both passed; the failure was
  "unwitnessed hunk", an artifact of the experiment writing no
  reviewer excerpts, not of cycle layout.
- Mid-re-review (only one of three cycles done) does fail verify with
  "diff hash mismatch c2p1" -- expected: a diff with fewer than N
  clean cycles must not verify, that is the gate working.
- Interleaved writes by two concurrent runs are excluded by ForgeLock:
  cli._run wraps the whole review in `with ForgeLock(lock_path)`
  (cli.py:2909) and the MCP server uses the same lock path
  (mcp_server.py:944). _continuation_round_index runs inside it.

## Finding 2 -- expert/warning: generic truncation message reports
   "capacity (0 tokens)" when the backend has no usable cap
   (llm_invoke.py:1258)

CONFIRMED (narrow): max_tokens: 0 is reachable via explicit gate.yaml
config, and the generic length message then printed "output capacity
(0 tokens) insufficient" -- 0 is the config's absence marker, not a
capacity, and the advice to raise output_ceiling pointed at a knob
that was never set.

Fixed: the generic message splits on resolved_cap > 0. With no usable
cap the message says no output cap is configured and names max_tokens
/output_ceiling as the knobs to set. New test
test_openai_length_without_a_usable_cap_names_the_missing_knob
asserts the new wording and kind=="truncated". Bug-injection: the
split condition weakened to >= 0 makes the test fail. 250/250.

## Advisory runtime-0 -- concurrent CI runs can clobber receipts

DISMISSED: same ForgeLock argument as Finding 1. The whole review
runs under the lock, and _continuation_round_index is only reachable
inside it.

## INFRA (not code)

- incomplete-coverage-qodo: qodo returned 0 findings but its excerpts
  did not cover 10 changed files; the coverage guard correctly
  rejected the empty conclusion.
- invoke-fail-adversarial: five consecutive 502s from OmniRoute on
  the sn-deepseek-flash route.
Both warrant a rerun, not a code change.

## Rerun

R7 on backend=deepseek after the Finding-2 fix.
