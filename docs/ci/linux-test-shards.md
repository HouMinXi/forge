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
9.1.1/9.2.0 subtests, and record the final session exit code after the Git-state guard.

The final gate reconstructs the plan from the original collection manifests. It
requires exact SHA and policy bindings, exact phase collections, one receipt per
phase, complete successful execution reports, and successful upstream job results.
Missing/failed/cancelled/skipped jobs, missing/duplicate receipts, omitted/extra
nodes, unexpected skips, xfails and failing subtests fail the gate. Ownership has
no skip allowance. Existing optional runtime skips may turn into passes but new
skip identities or reasons require explicit policy review.

## Policy provenance and maintenance

The `source_run` and `artifact_sha256` fields in `scripts/ci_test_policy.json`
identify the original skip-policy baseline, including existing skip identities/reasons,
from [run 41400661496](https://github.com/HouMinXi/forge/actions/runs/41400661496),
artifact 13122369995. Its ZIP SHA-256 is
`3d408a807c5a3ec73163061ab084c4eb300c0c6b805ec43b304b430e2d2bfcbe`.
The baseline had 78 runtime skips and three collection-skipped modules. The
collection skips are specifically the absent optional `code_review_graph` package.
The service-free module weights were refreshed separately from successful run
[41448274206](https://github.com/HouMinXi/forge/actions/runs/41448274206), exact
source `e9df2c0970ad93011ecf5a4081d9ca8075e35b52`. Its four shard artifacts are
13143241738, 13144551500, 13144311630 and 13144137235 (shards 0–3). The refresh
sums JUnit testcase durations by module, leaves unexecuted module weights and all
skip policy unchanged, and does not claim a measured wall-clock speedup.

Verified SHA-256 digests of those artifact ZIPs:

| Shard | Artifact | SHA-256 |
| --- | --- | --- |
| 0 | 13143241738 | `24bd2ec322eaf88663240db35c806523a2384b8bb0c708a63f9f4f3c2796c1b0` |
| 1 | 13144551500 | `a38e7f7d1d06c62afd4991e13cdee9daf11d901023cbd063bff048c39e498ab1` |
| 2 | 13144311630 | `dda806be2a37e82b673d50601c01d8e7120694de8324f4bf13a5e7baf59d0600` |
| 3 | 13144137235 | `265536ea9fcde66da5ad0e752f03d025448afcce4fda0a1020fa3fe425783e39` |

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

The publication path now requires a separately reviewed public identity manifest,
`scripts/ci_public_test_identities.json`. It lists complete public pytest nodeids,
including their parameter IDs, and known public repository test modules. This is
not generated from a live collection. Every runtime identity must match it
exactly before any receipt can be published. Unknown identities or reasons fail
with a fixed STOP message; no rejected value or hash of it is made public.

### The identity manifest is an ordered contract

`nodes` and `modules` must be sorted and duplicate-free; `validate_manifest`
rejects anything else, and a rejected manifest stops the `plan` job before
collection runs. The published ordinal `public-N` is the **position** of a node
in that sorted list, so inserting one entry renumbers every later ordinal. An
ordinal is only meaningful together with the manifest revision that produced it.

A published failure record carries one extra field per failed test, `kind`, which
is one of `assertion`, `timeout` or `error`. It is derived from the exception the
report names, never from the message body; a value outside that set suppresses
the whole published list rather than publishing part of it.

### Resolving a published ordinal locally

`scripts/reproduce_ci_failure.py` turns an ordinal back into the exact test and
runs it with a full traceback:

```
python3 scripts/reproduce_ci_failure.py --run <run-id> --id public-N
```

It reads the manifest at the run's revision from git rather than from the working
tree, refuses when the checkout is a different revision unless
`--skip-revision-check` is passed, and uploads nothing.

The private transport wrapper validates the raw collections or execution
outcomes against the original policy. Approved nodeids become module-prefixed
manifest indices; approved skip reasons become indices of already-public policy
constants. An execution receipt also records one fixed `failure_kind` label per
failed test, which the transport treats as optional so a receipt written before
the label existed still validates. The
public manifest and policy digests bind these mappings. These are digests of
reviewed public inputs, not deterministic hashes of private runtime values.

For a run phase, raw outcome checks alone are not a coverage proof. The projected
receipt is then checked against the independently downloaded public plan. The
final gate still reconstructs that plan from all four collection manifests and
checks all seven execution receipts with the original exact-node, lifecycle,
skip, xfail, subtest and upstream-job predicates. No test is silently dropped to
fit the manifest, and no job-status-only substitute is accepted.

Raw logs, JUnit and original receipts remain private to each runner. Every shell
step redirects diagnostics before setup and derives its private paths locally;
private paths are not exported through `GITHUB_ENV`. Transport output uses exact
relative filenames, with no directory or wildcard upload. Data publication also
requires its producing step to succeed. Failure summaries are separate fixed
records and cannot authorize uploading failed or stale data. Explicit old private
and public output names are cleared before reuse, and public writes are atomic.
An interrupted process cannot establish freshness by leaving an old receipt.

Private output permissions are scoped to their writers, not inherited by the
suite: the shell saves and restores its incoming umask around private setup,
and private log sinks use their own restrictive creation mask. The transport
creates private directories with mode 0700 and reserves fresh raw receipt/JUnit
targets with mode 0600 before launching pytest. The reviewed recorder and JUnit
writer truncate those files without replacing their modes. Empty reservations
are not valid evidence. Pytest and its fixture subprocesses retain the original
runner umask; changing a fixture's permissions is not a privacy mechanism.
These controls do not claim to defeat same-UID code deliberately replacing raw
files or changing their permissions.

This protects the workflow-controlled diagnostic payloads, not arbitrary hostile
code running as the runner user. GitHub and pinned checkout/setup/upload actions
still expose their normal platform and action metadata. Temporary private data
is not a durable private archive; removing public raw logs reduces forensic
detail and does not erase historical artifacts. Missing evidence or runner loss
never proves test success or cleanup.

Each job first uploads an immutable `archive-...-<run_attempt>` copy, then refreshes
its stable `linux-...` artifact. The gate downloads only stable names from the same
workflow run. This lets a failed-job-only rerun reuse successful jobs' artifacts
without deleting their historical attempt evidence. Both uploads are mandatory;
errors fail the job. A fresh workflow run never downloads artifacts from another
run. Archives and current artifacts retain the original 14-day lifetime.

Local pure controls: `python -m pytest tests/test_ci_test_shards.py`.
The full runtime qualification remains the actual GitHub Linux workflow; a local
collection proof or synthetic receipt control is not a substitute for it.

### Maintaining the public identity manifest

New modules or changed parameter IDs require explicit review of the complete
public identifiers before updating the manifest. Source literals, deterministic
public data and bounded public fallback labels can establish what may be
published. A source-derived possible label does not prove that pytest will
actually collect it. Dynamic host-derived values are not approved simply because
they appeared in a private candidate collection. The runtime never promotes
unknown data into the manifest, truncates it or hashes it as a privacy shortcut.

Review evidence records the source/configuration/dependency versions used to
establish public provenance. That evidence is a review and release check, not a
runtime claim that every implementation byte remains identical. An unchanged
approved public identifier can remain valid across implementation-only changes;
the CI source SHA and the original coverage validators retain their separate
roles. Manifest changes invalidate its public mapping digest and need the normal
source review and complete collection/coverage validation again.

The real complete manifest and qualified-runtime validation are release gates.
A missing manifest deliberately prevents collection/publication; that safe STOP
is not a completed CI migration. Synthetic manifest controls demonstrate the
mechanism, not that the current full repository suite has been admitted. Added
projection, parsing and validation costs must be measured separately from actual
CI wall time, runner setup and queueing; parallelism alone does not prove a speedup.

## Pytest 9.2 qualification

The dev and ordinary Linux/wheel execution pins are 9.2.0. Each Linux execution
job additionally provisions 9.1.1 in a separate qualification venv, identified by
`FORGE_QUALIFICATION_PYTHON`; it does not replace the main or system Python site.
This keeps the previous CPython 3.12 qualification cases executing instead of
silently becoming newly skipped after the upgrade. Runtime discovery must probe
venv entrypoints before resolving the physical interpreter.

Pytest 9.2 shortens automatically generated long parameter IDs by default.
Comparison with the previous 34,344-node collection found 244 changed identities.
The repository explicitly selects `parametrize_long_str_id_strategy = "legacy"`
to preserve existing node references and exact skip receipts; explicit IDs remain
unchanged. The collection command also names the repository config explicitly so
an external evidence path cannot change root/config discovery.

Qualification covers startup plugin failures (missing=4, import failure=3),
warning-as-error during plugin loading/cleanup, original lifecycle negative cases,
and bytecode cleanup. The stale-bytecode test supports timestamp and checked-hash
headers: the latter deliberately forges a matching hash around stale code rather
than claiming pytest 9.2 naturally has a same-second hash collision. Neither
failure evidence nor cleanup is inferred solely from an allowlist addition.

Upstream reference: [pytest 9.2 changelog](https://docs.pytest.org/en/stable/changelog.html#pytest-9-2-0-2026-10-27).


## Bounded failure diagnostics

The transport can also record the last entered code-owned boundary for each
plan, execution phase and aggregate operation. The separate
`diagnostics-linux-*-<attempt>` artifacts contain only a fixed versioned schema,
known operation/stage/state/error-code enums, the job status observed at
projection time (a later upload can still fail), and
`raw_evidence: NOT_PUBLISHED`. They do not match the aggregate's `linux-*`
download pattern, are not coverage receipts, and cannot authorize a pass.
The existing pytest argv, four-way partition, original validators, exact public
identity allowlist, skip/xfail/subtest rules and execution budgets are unchanged.

Boundaries distinguish input validation, collection, child execution, receipt
validation, coverage validation, publication and aggregate validation. They mark
the current outer operation, not an inferred cause inside a dependency.
`NONZERO_EXIT`, `INTERRUPTED`, `REJECTED` and `INTERNAL_ERROR` are fixed failure
classes; no exit number, exception message, PID, host path, unit name, environment
value, failed test parameter, runtime skip reason or runtime-value hash is public.
A child nonzero return is retained even if recording its failure also fails.

`COMPLETED` means the operation returned after all its existing checks; it is
not job success or native Linux qualification. `INCOMPLETE` means no terminal
record survived after the last boundary; it does not identify OOM, timeout or
cancellation. Missing records are `UNAVAILABLE/NOT_RECORDED`, never `NOT_RUN`.
Malformed records, mismatched operation/file names, unknown fields or values,
links, extra files and interrupted atomic-write remnants reject the diagnostic
publication rather than exposing partial input. Each invocation removes its old
checkpoint before starting. The fixed workflow generates a fresh 128-bit random
invocation label before the producer, publishes only that non-sensitive label
under a fixed job-output key, and passes the same label to the transport. The
publisher obtains the expected label from that specific producer's job output;
missing or mismatched labels yield UNAVAILABLE for every old record state.
The checkpoint's self-reported label is never its own authority. Labels can be
visible in GitHub's platform metadata, contain no runtime-derived private data,
are not secret keys or authentication, and are omitted from the fixed sidecar. The independent diagnostic step is fail-closed: a
diagnostic failure can make the job fail, but cannot change the original summary
outcome, bypass an original publication gate, or turn a failed workload green.

Raw subprocess logs and Python traceback frames (without captured locals) stay
in job-local 0700 directories and 0600 files. Diagnostic setup never changes the
workload umask. The diagnostic uploader names one exact reconstructed file; it
never uploads the private directory. Private tracebacks may be lost when the VM
is retired, and abrupt cancellation may prevent even the fixed sidecar upload.
This does not promise later access to full traceback or a complete crash report.
The fixed-code publisher trusts the reviewed helper and the job; it is not an
attestation against a malicious same-UID writer or replaced helper. Do not enable
GitHub runner/step global debug or publish raw logs as a substitute for this path.
