# R03-08: native finalization before summary-return loss

## Scope and call map

Base `81dcc6e430412afeb6f5d44671794e1beede1770`. This is a bounded
R03-08 return-boundary extension, not completion of all T35 combinations.
No production behavior, default warning/strict policy or #33 code changes.

Writer process → real `HostAuditSession` → actual worker/supervisor →
`InvocationOwner` → native v2 `AttemptRecorder.finalize` → owner `_summary`.
A controlled OSError is raised only after the native finalization ACK, real
successful journal/directory fsyncs, the final marker and exactly one
`run_final` record have been observed. This prevents an earlier admission or
write failure from masquerading as the intended post-publication fault.

The host does not receive the owner's successful summary. Warning mode retains
an incomplete, publication-uncertain audit summary; strict mode raises the
existing `AuditPersistenceError`. Neither path rewrites the already committed
terminal record. A second host finalize returns the safe summary without a
second recorder finalize, and closed-host admission remains unavailable.
This is not a claim that ordinary warning mode must turn a successful product
review into failure.

After the writer exits and joins its worker, a fresh interpreter reopens the
native record independently. It retains the completed attempt and exact
synthetic raw bytes, with one terminal event. Reading and repeated host
finalization must leave the persisted bytes unchanged. Disk completion is
historical storage evidence, not a reconstructed caller ACK or current host
success. Ordinary native usage projection remains unchanged; no measured
usage is invented or replayed into accounting.

## Controls and evidence boundaries

Healthy and summary-loss cases run in warning and strict modes. An omitted
injected-fault control must fail specifically at the host-state oracle. This
is oracle sensitivity, not a production save-site omission test. Independent
expected raw is a fixed test constant, not echoed writer output. Process
operations are bounded and use only synthetic data; no external provider,
network request, credential or existing user checkout is involved.

Direct `InvocationOwner.finalize` has a different existing contract: repeating
it can call the recorder again, whose existing-terminal path validates rather
than appends another terminal. This experiment tests the public host's
no-resubmission boundary, not a new owner-wide sticky failure policy.

T35 start-only, raw published before journal, acquired journal committed before
caller return, and finish ACK loss with process termination remain separate
unqualified combinations. Existing native same-process EIO controls are not
new-process death evidence. A clean retained prefix may legitimately have
`state=active`, `audit_complete=True`, and an unknown unfinished attempt;
caller receipt uncertainty must not be inferred backwards from replay state.

No SIGKILL occurs in this test: it is controlled return loss followed by normal
writer termination and independent-process reading, not physical power loss,
kernel crash, installed CLI, external HTTP or whole-review acceptance.
Python's [fsync contract](https://docs.python.org/3/library/os.html#os.fsync)
and [subprocess timeout contract](https://docs.python.org/3/library/subprocess.html#subprocess.run)
explain the primitives used, not hardware durability qualification.

## Executed validation

Isolated Linux/btrfs clone, Python 3.13.12 / pytest 9.1.1: all five new cases
passed. Final seven-module regression: **213 passed, two warnings** (existing
`parametrize_long_str_id_strategy` setting and unset asyncio fixture-loop
scope). Modules: this test, T34 process recovery, finalize-native, host,
host-finalize-race, R03-summary-returns and CI-test-shards. Full Ruff check and
format check passed for 1049 Python files. Six review lenses found no remaining
blockers; independent reviewers performed source review, not separate runs.
The public identity manifest only adds one module/five nodes, removing none.
