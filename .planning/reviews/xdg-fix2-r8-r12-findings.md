# xdg-fix2 R8-R12 -- findings, fixes, and the infrastructure wall

## R8 (CLI, worktree pipeline, sn-deepseek-flash)

First run where the pipeline itself was the P2b code (PYTHONPATH to the
worktree src; the MCP server imports the installed package, which points
at the main tree). No SARIF crash -- the CI-PENDING sarif fix works on
its own code.

- F4 (qodo): machine.py `isinstance(cycle, int)` accepts bool. Fixed
  with an explicit bool exclusion; test
  test_bool_cycle_is_skipped_not_counted_as_one. Bug-injected: guard
  removed -> test fails. 15/15.
- adversarial pass: 502 x5 (INFRA).

## R9 (opus-omniroute) -- 400 x3, 0 tokens

Root-caused by probe: OmniRoute no longer resolves combo model
onmi-opus4.6 ("Unable to determine provider for model"; the provider
catalog has no anthropic credentials and no opus combo entry).

## R10 (sn-deepseek-flash)

adversarial completed this time; qodo/expert hit the coverage guard
(0 findings without excerpt coverage -- model output, not code).

- F5 (adversarial, note): _constant_offset convicted on the comparable
  subset; a line whose shifted position falls outside the post-image
  cannot vouch for the shift, so a partial match could call a
  fabricated tail "misnumbered". Fixed: require compared ==
  len(excerpt_line_map). Test
  test_partial_shift_match_is_not_called_misnumbered. Bug-injected:
  subset rule restored -> test fails. 140/140.
- F6 (warning): generic truncation message uses %s for a possibly
  non-numeric out_tok. DISMISSED: %s formats any value safely; the
  message reports the raw API value, which is the fact.
- F7 (warning): PENDING + state.json deleted mid-run prints no cost
  line. DISMISSED: that load_state-None case already escalates (M3);
  a cost line cannot report what the state no longer holds.
- test-assertion: disabled-config test did not assert infra_errors
  empty. Added; bug-injected (infra error appended on the disabled
  path -> test fails). 27/27.

## R11 (sn-deepseek-flash) -- 0/3 passes, all INFRA

qodo/adversarial: "truncated at 16384 output tokens... backend clamped
below it on its own" -- the new clamp diagnostic working as designed
against the route's hard 16384 output cap (the diff now exceeds what
the free route can output). expert: 502 x5.

## R12 (gemini-omniroute) -- 400 x3, 0 tokens

Same combo-expiry root cause as R9: onmi-gemini3.6 is not in the
provider catalog. Probes show the only live model on the route is
openai/deepseek-v4-flash (the sn-deepseek combo underneath), capped at
16384 output.

## Where this leaves the gate

Every L1 finding produced across R4-R11 is fixed, bug-injected, and
covered by tests (3294 passed). The three-consecutive-clean-cycle gate
is not attainable with the currently reachable backends: deepseek-direct
blackholes (R5: 2400s, 0 tokens), sn-deepseek-flash hard-caps output at
16384 (R11) with intermittent 502s, and the opus/gemini OmniRoute
combos are no longer registered (R9/R12, probe-verified). This is
backend infrastructure, not code.
