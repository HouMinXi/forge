# check6-actionable -- review evidence, 2026-08-15

Branch fix/check6-intent (worktree .worktrees/check6-intent), commit
52650e5 on origin/main @ 835115d:

- verify.py: check 6 (both the excerpt-derived and legacy branch) now
  names the files holding the largest uncovered line counts instead of
  a bare percentage; new helper _coverage_failure_detail; intent
  comment records why test lines count (charter item 6 decision,
  43.1-DECISIONS-20260815.md, signed off by user).
- tests/test_verify.py: test_low_coverage_fail asserts the message
  carries "largest uncovered: foo.py" and "bar.py".

## Bug-injection (closed loop)

Injected "" in place of _coverage_failure_detail(cov, all_diff) at the
excerpt-derived call site: test_low_coverage_fail FAILS (assertion on
"largest uncovered: foo.py" not met). Reverted: PASS. Full module
tests/test_verify.py 140 passed.

## Review rounds

Required_cycles in gate.yaml is 1, so each round below is one cycle of
three passes. Backend deepseek-direct (api.deepseek.com, no gateway) --
the gateway's semantic cache replays same-prompt rounds, so all rounds
run on the direct route (see a4-eslint-experiments-20260815.md).

- R1 (deepseek-direct): hung 24+ min in the falsify stage (server
  accepted the connection, sent nothing; timeout_s 1200-2400 parks the
  round for the full budget). Killed by hand. See
  hang-diagnosis-20260815.md. The three pass results were real but the
  round never produced a verdict.
- R1b (deepseek-nocache): PASS findings=1 confirmed=0 uncertain=0
  dismissed=1 fixed=0. 3 passes 53.3s, wall 182.8s. The dismissed
  finding is mutation-baseline-timeout (env noise, flaky guard).
  Advisory output (outside the findings count) carries three
  test-assertion notes: (1) _coverage_failure_detail has no direct
  unit tests for the empty/sort/top-5 branches; (2) the legacy-branch
  change (cycle_covered, verify.py:838-844) is not exercised by any
  test; (3) the assertions are broad substrings, not count/order
  checks. Disposition: hold until R2/R3 land, then decide.
- R2 (deepseek-nocache): PENDING findings=1 dismissed=1 (flaky
  mutation-baseline-timeout; env noise, disclosed per S1).
- R3 (deepseek-nocache): PASS findings=1 dismissed=1. Three-round
  verdict on the v1 diff: 2x PASS vs 1x flaky PENDING.
- v2 (amend with strengthened tests + both-branch detail): R1 FAIL
  findings=2 confirmed=1. The confirmed RUNTIME finding claimed the
  message suffix breaks downstream format parsers; ground truth
  disproves the premise (cli.py:1742 prints the reason verbatim for
  humans; no code parses the format, and the stable prefix is
  preserved). Disposition: contract comment added at both check-6
  sites ("prefix before ';' is stable, suffix is human guidance"),
  amended 58fded1. The test-assertion pass then asked for exact-count
  assertions; applied (measured output, not guessed: foo.py (4
  lines), bar.py (2 lines)), amended bd77a8c.
- v3 (bd77a8c): R1 PASS / R2 PENDING (uncertain=1: format-compat
  repeat, substance-free per rule (b)) / R3 PENDING (smoke unverified,
  flaky baseline; test-assertion found cov not a real subset of
  all_diff -- real, fixed in 4966358).
- v4 (4966358): R1 PASS / R2 PENDING (same flaky baseline; test-
  assertion caught that the subset fix left only ONE uncovered file,
  so multi-file count ordering was no longer exercised -- real,
  fixed in 33d477b; bug-injection re-verified).
- v5 (33d477b): running. Freeze line: further test-assertion advice
  that only asks for "more precise" assertions on already-measured
  outputs is an unbounded precision gradient, not a defect; record it
  and stop amending.

## Disposition

(awaiting rounds)
