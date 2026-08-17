# code-forge review (CLI) silent exit -- investigation, 2026-08-15

Bug report from the user (draft /tmp/draft_forge_cli_silent_exit_20260815.txt):
three L1 passes complete and print token lines, then the process exits 1
with no verdict line, no cost line, no traceback, no receipts, no
state.json. Reproduced twice with --mode local, deepseek-direct, a
~294-line diff, fresh state (state.json and receipts archived, lock
removed).

User's hypothesis: zero-findings path (coverage-guard branch in
factories.py) raises, an outer handler exits 1 without printing.

## Static pass so far

- factories.py fold section (318-520): the coverage-guard on the
  zero-findings path appends an INFRA finding (incomplete-coverage),
  never raises silently; normalization and set math are safe.
- cli.py: no os._exit; only entry-point sys.exit(main()). No broad
  except-returns-1 found on the review path yet.

## Plan

Reproduce with the progress event stream: run one local-mode review
with PYTHONPATH=src (fix/progress-visibility, 63fa06c+). The last
[forge] event before the silent exit names the stage that dies. This
is exactly the diagnostic the event stream was built for.

## Experiment 1 (2026-08-15, LOCAL + deepseek-nocache): NOT reproduced

`PYTHONPATH=src python3 -m code_forge.cli review --committed
--backend deepseek-nocache --mode local --no-color` completed the full
run: run start / round 0 / three passes / falsify 1-2 with
dispositions / run done verdict=PENDING findings=2 confirmed=0, exit
3 (BUSY, the normal PENDING mapping). No silent exit, receipts and
state written. The event stream covered the whole run.

Two differences from the report's repro remain untested: the report
used deepseek-direct (paid direct route) and hit the zero-findings
path (the report's own hypothesis). A repro on deepseek-direct is the
next experiment if the user wants it -- it costs a full direct-route
cycle and the route hung once today (hang-diagnosis-20260815.md).

Also recorded here: the review's own RUNTIME advisories on this run
were largely hallucinated ('from. Import progress' as a SyntaxError;
the module imports and runs fine -- this very log is the evidence)
plus theoretical blocking/staleness concerns already documented in
the progress module's constraint comment.

## Experiment 2 (LOCAL + deepseek-nocache, event stream): multi-round discovery

LOCAL mode is ITERATIVE: round 1 produced a CONFIRMED finding, the
convergence counter reset, and the run continued into rounds 2, 3, 4
(observed: round 4 start at t+547s, each round = three passes +
falsify). CI mode is one round per invocation; LOCAL keeps going
until clean-round convergence. This is the big difference from every
CI round run so far, and it makes LOCAL the natural home of the
reported bug: the silent exit happened on round 2+ of a LOCAL run
(the report used --mode local), a path the CI-only experiments never
touched.

The event stream tracked every round boundary and falsify call, so
whenever the silent exit reproduces, the last event names the exact
stage. Run continued past 600s and was moved to background;
final verdict recorded when it lands.

## Static findings that constrain the bug

- cli.py main() wraps the review dispatch in a broad except that
  prints "code-forge: unexpected error" + traceback and returns
  EXIT_FAIL (1). An escaping exception is therefore NEVER silent in
  the current source. A silent exit 1 must come from (a) an early
  `return Verdict.FAIL` inside _run() that skips the verdict/cost
  print points, (b) a different build than the source tree (the
  installed 2.7.0 wheel vs src/), or (c) stderr buffering loss on a
  non-tty (block-buffered stderr + abrupt exit).
- write_receipts does not clear the directory; it appends per-round
  files. Empty receipts on the reported run therefore means the
  process died before the first round's write -- which on a LOCAL
  multi-round run is inside round 1, after the three token lines and
  before the round-1 receipt write (falsify / mutation / e2e /
  coverage / merge territory).
- The user's repro used the installed CLI. Our experiments ran
  PYTHONPATH=src with the event stream; the installed 2.7.0 has
  neither the progress events nor the idle-timeout fix, which is why
  the user sees nothing after the token lines.

## Next step for the reporter

Rerun the exact repro with the source tree:
`PYTHONPATH=<forge>/src code-forge review --committed --backend
deepseek-direct --mode local`. If the silent exit reproduces, the
last `[forge] t+...` line names the stage that died; if it does not
reproduce, the installed build differs from src/ and the answer is
"upgrade the install".

## Experiment 2 result: HOLD abort, exit 3, no silent exit

The LOCAL run iterated six rounds, then entered the HOLD UX for an
UNCERTAIN finding. With no interactive stdin it aborted: "HOLD UX
aborted by user; state preserved at .../state.json", returning
PENDING -> EXIT_BUSY (3). State WAS written (contrary to the report's
empty state). Every exit path surveyed in the current source either
prints (exception handler in main, HOLD abort line, verdict line) or
writes state; a truly silent exit 1 is not reachable in this tree.
Conclusion stands: the reporter's installed 2.7.0 build differs from
src/, or the environment lost stderr output (non-tty block buffering
plus an abrupt kill). The PYTHONPATH=src rerun distinguishes the two.
