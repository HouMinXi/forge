# FIXVAL execution proof

FIXVAL validates applicable LOCAL diffs after review convergence. It requires three
stable, complete fixed-code pytest inventories, then a real candidate-file test
call that fails after production reversal and passed in all three fixed runs.
Collection, setup, teardown, internal errors, signals and missing evidence do not
count as RED. XFAIL/XPASS-derived outcomes cannot supply the witness. A complete
reverted GREEN blocks a hollow test.

No executable changed tests, no production changes, non-git/no-diff review and a
nonempty explicit `FIXVAL_WAIVER` or `Fixval-Waiver:` reason remain distinct recorded
policy exceptions. They are resolved before requiring unused test configuration.
An applicable gate that cannot execute or validate is an ERROR and fails the
terminal review; it is never an accepted skip.

## Commands and deadlines

The initial qualified proof adapter supports literal `python`/`python3` followed
by `-m pytest` or `-B -m pytest`, on the capture module's qualified Python/pytest
pairs. The selected test command still has the ordered executable changed test
paths appended. A pinned reporter plugin is explicitly added and recorded.
Absolute Python paths, bare pytest, shell wrappers and other runner forms are
unavailable for applicable FIXVAL until their adapters are qualified. This does
not change the general commit gate's runner support.

An explicitly configured `test.timeout_seconds` applies to every fixed attempt,
reverted probe and optional overfit command. Values must be non-boolean integers
from 1 through 86400. Without this key, fixed attempts retain the 120-second
default and reverted/overfit commands retain 600 seconds. Mutation's own timeout
remains separate. Cleanup and evidence-validation reserves are additional to the
test deadline. A single recognized unavailable-runner retry may remove an
inherited virtual environment and restart the three fixed runs; assertions and
timeouts are not retried this way.

## Ownership and evidence

Test execution uses a Linux process owner with verified procfs/pidfd capabilities.
Unavailable ownership fails before native work. Owned descendants must be stopped
before source restoration. Cleanup/restoration uncertainty overrides any apparent
success and retains recovery material. Optional overfit judgments remain advisory;
unsafe cleanup/restoration is a safety failure.

The bounded owner drains and hashes full stdout/stderr streams but retains at most
1 MiB of diagnostic prefixes, explicitly marking truncation. Structured evidence
is never silently truncated. Each closed phase record is bounded to 8 MiB and
50000 test rows. Complete raw phase records are retained in a private external
on-runner evidence directory, at most 128 MiB/64 files/eight commands.

State has an additive versioned `fixval_stage` terminal projection, bounded to
32 KiB. It links one full ordinary candidate-call RED witness to its three GREEN
records, with command/config/source, runtime, process-incarnation, cleanup and
restoration binding. Its full witness identity is limited to 8 KiB. A new LOCAL
invocation invalidates earlier terminal proof before continuing review.

Review receipts keep their existing L1-cycle meaning. `verify` success alone does
not establish a later FIXVAL result. An external consumer must validate the
compact projection against independently admitted inputs and authenticate the
exact on-runner validator's execution provenance. It must not claim it replayed
raw inventories that were not retained remotely. Raw records are not automatically
uploaded or guaranteed available after an ephemeral runner disappears.

## Retrying a failed LOCAL invocation

Automatic retirement of earlier FIXVAL runtime errors is not implemented. A
same-source LOCAL retry can retain an earlier UNCERTAIN FIXVAL_ERROR and reach
HOLD before terminal validation. No saved record, repository setting or environment
variable authorizes treating unknown cleanup/restoration as safe. Such errors are
not silently dismissed or converted to disproved findings.

There is no review `--fresh` reset flag. The existing recovery contract permits an
explicit operator archive of state.json and all review receipts while holding the
same ForgeLock used by review (`.code-forge/code-forge.lock`), followed by an ordinary
fresh LOCAL review. Preserve the complete old state/receipts and all referenced
raw/recovery material; do not delete a live lock, discard findings, copy old PASS
credit into the new run or use CI mode as a substitute. Resolve unknown live-process
or source/index-restoration status first: archiving state does not establish safety
and is not automatic recovery. Fresh isolated candidates/invocations still require
all source, ownership, receipt and FIXVAL gates.

The inherited-review provenance check runs before new pending-stage persistence.
If that preflight refuses the saved evidence, the previous state remains available
for explicit recovery. After admission, terminal credit is invalidated and the new
invocation must obtain fresh FIXVAL proof.
