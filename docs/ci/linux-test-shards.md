# Linux CI coverage and timing contract

The `Linux tests` workflow keeps its `linux-tests` final check name. That check
requires a collection plan, a serial process-contract job, and all four serial
service-free shards to succeed. No branch-protection or permission changes are
needed. Each job has a separate Ubuntu runner and checkout; this is not pytest
worker parallelism within one process environment.

## Coverage

The planner collects the original service-free selection with exactly the
existing marker expression, two cgroup-file exclusions and two `sem`-dependent
node deselections. It separately collects the original ownership, cgroup and
local-integration selections. Ownership must be a subset of the original full
selection. Its 568 existing nodes run once in their dedicated no-skip phase,
rather than again in the service-free phase.

All remaining modules, including collection-skipped modules, enter a deterministic
four-way partition. Historical module times affect only placement. New modules
receive a 1-second default weight and cannot disappear because a weight is absent.
The longest-weight-first algorithm breaks ties by module name and shard index.
Module execution remains serial; no individual parameter combinations are removed.
The original 20/15/90/5-minute execution budgets and Linux setup prerequisites remain.

Each pytest process is explicitly instrumented through command-line plugin options,
not inherited environment variables, so nested pytest processes do not overwrite
its receipt. Receipts distinguish setup/call/teardown, collection skips, and pytest
9.1.1 subtests, and record the final session exit code after the Git-state guard.

The final gate reconstructs the plan from the original collection manifests. It
requires exact SHA and policy bindings, exact phase collections, one receipt per
phase, complete successful execution reports, and successful upstream job results.
Missing/failed/cancelled/skipped jobs, missing/duplicate receipts, omitted/extra
nodes, unexpected skips, xfails and failing subtests fail the gate. Ownership has
no skip allowance. Existing optional runtime skips may turn into passes but new
skip identities or reasons require explicit policy review.

## Policy provenance and maintenance

`scripts/ci_test_policy.json` records weights and existing skip identities/reasons
from [run 41400661496](https://github.com/HouMinXi/forge/actions/runs/41400661496),
artifact 13122369995. Its ZIP SHA-256 is
`3d408a807c5a3ec73163061ab084c4eb300c0c6b805ec43b304b430e2d2bfcbe`.
The baseline had 78 runtime skips and three collection-skipped modules. The
collection skips are specifically the absent optional `code_review_graph` package.
Weights can be refreshed from a verified JUnit artifact without changing coverage.
Skip policy changes require reviewing the reason rather than silently regenerating
a baseline from a failing or reduced run.

The measured full-selection step took 31m42s, 39m34s and 35m56s in PRs 359, 360
and 361. The #361 case-time sum was 2,099 seconds; 568 duplicate ownership cases
accounted for 149 seconds of it. Four-way balancing is an estimate, not a promised
wall-clock result. Runner queueing, repeated setup/collection, slow modules and
resource variability still affect elapsed time. More concurrent runners can
increase billed runner-minutes even while shortening feedback time. Actual
elapsed and aggregate runner time must be compared on the published PR.

## Evidence and retries

Each job first uploads an immutable `archive-...-<run_attempt>` copy, then refreshes
its stable `linux-...` artifact. The gate downloads only stable names from the same
workflow run. This lets a failed-job-only rerun reuse successful jobs' artifacts
without deleting their historical attempt evidence. Both uploads are mandatory;
errors fail the job. A fresh workflow run never downloads artifacts from another
run. Archives and current artifacts retain the original 14-day lifetime.

Local pure controls: `python -m pytest tests/test_ci_test_shards.py`.
The full runtime qualification remains the actual GitHub Linux workflow; a local
collection proof or synthetic receipt control is not a substitute for it.
