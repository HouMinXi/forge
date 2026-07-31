# Convergence record: FORGE_COMMIT_CLASS declared-class carve-out

## Verdict per lane, three rounds

kimi:    r1 = 0 MAJOR / 2 MINOR / 2 NIT  -> r2 = 0/0/0 -> r3 = 0/0/0
deepseek: r1 = 0 MAJOR / 3 MINOR / 2 NIT -> r2 = 0/0/2 NIT -> r3 = 0/0/0
gemini:  r1/r2 = transport failure (no output) -> r3 = 0/0/0 (manual relay)

Converged at 0/0/0 across all three lanes on round 3.

## Findings that escaped the first pass (all fixed, all tested)

1. presubmit-preservation for declared commits was claimed but untested
   (kimi + ds, one finding claimed twice). Test (o) + injection A
   (declared-exit moved before presubmit -> exactly that test red).
2. review-skip for declared commits was claimed but untested (ds).
   Test (p) + injection B (declared-exit moved after review -> exactly
   that test red).
3. AI-vocab gate untested for declared commits (kimi). Test (q).
4. docs/wip class values + chain variant untested (kimi). Tests (r),(s).
5. setup-vscode.md section insertion orphaned "versus the baseline"
   (kimi). Repositioned.
6. env-inheritance risk (ds): mitigated with docs warning +
   "one value, one commit" generator comment; mechanism rejected in
   design as over-coupled. Residual risk accepted: text gates and
   presubmit cannot be disabled by the variable.
7. echo omitted "chain" (ds NIT). Added.
8. "same vocabulary" imprecision in comment and docstring (ds NITs at
   r1/r2/r3). Both occurrences removed.

## Channel failures (environmental, disclosed per fleet law S1)

- r1/r2 gemini lane: aicc gm returned "Error parsing response:
  'choices'" three consecutive times. Root-caused with direct curl:
  upstream antigravity account (pro-high) emits HTTP 400, provider
  circuit breaker opens (~41s), every gemini combo call returns
  ALL_ACCOUNTS_INACTIVE, body has no choices field. See probes in this
  directory's session log.
- r3 substitute glm lane: HTTP 429 monthly quota exhausted.
- r3 gemini delivered by manual relay; meta-level delta verification.

## Test counts

- tests/test_hook_carveout.py: 8 -> 19 tests.
- hook-related files: 96 passed. Full suite: 3035 passed, 4 skipped
  (test_cli_integration excluded: live claude -p calls, pre-existing).
- Bug-injection sites verified: 4 (attestation guard, declared-exit
  condition, declared-exit before presubmit, declared-exit after
  review). Each injection turned exactly the intended test(s) red;
  implementation restored byte-identical (md5-checked) after each.
