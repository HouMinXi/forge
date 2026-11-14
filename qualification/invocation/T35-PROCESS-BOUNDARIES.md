# T35: native publication before the owner return

## Exact route and scope

Base `ee34ff6afeda27b2b412cb5841139dee9e9d6684`. Tests exercise the existing
schema-v2 direct `InvocationOwner` route with synthetic bytes. They do not
change production/default/strict behavior, #33, or the parallel #493 document.
This is a native persistence/return-boundary slice, not an installed review
command, streaming stage/adopt route, whole round gate or all-T35 qualification.

Call map: writer → real owner/recorder and manifest → begin/entered/acquired/
finish → selected real publication method returns to its wrapper → readiness
pipe while the owner-facing call has not returned → controller SIGKILL and
reap → fresh reader → native read-only reopen and independent journal/raw checks.
The healthy control releases the same pause and lets the pending call return.
Both paths leave the run unfinalized intentionally.

| Cut point | Independently expected killed-writer record |
| --- | --- |
| `start` appended and fsynced | One start, no raw/finish; unknown attempt and dispatch; active valid prefix |
| `raw-*` `_stage` completed, before settlement journal | Exact orphan raw bytes, no trusted ref; dispatch unknown; incomplete/integrity_error |
| `settled_acquired` appended and fsynced | Exact bound ref/hash/length; unfinished attempt unknown; active valid prefix |
| `finish` appended and fsynced | One completed attempt and FINISH, raw retained; run still active, no final |

The actual `lease.acquired` route calls settlement and writes
`settled_acquired`, not `acquired`. The test asserts this exact event identity.
At the orphan-raw cut, `entered` exists only in owner memory; replay still
reports unknown dispatch until settlement journals it. Raw bytes and expected hash come from the reader's own fixed constant, not
writer-reported expected data. No usage observation is supplied; usage remains
unknown rather than fabricated zero. A valid prefix's `audit_complete=True`
is not evidence of owner return, run finalization or review success.

## Controls

Each cut has SIGKILL and healthy-release controls. Successful real fsyncs are
observed, including raw device/inode across temporary-name publication; the
journal is inspected before readiness. FINISH may revalidate/synchronize already
retained raw through the existing checked path; those observations do not
claim that FINISH wrote a second raw file. Controller requires the selected cut
and `returned=False`, kills only its own writer, verifies its SIGKILL exit and
reaps it before launching the reader. Each wait/child has a bounded timeout.
Writer and reader forbid network and descendant processes after imports.
The original user checkout is not modified.

Omitting the pause must fail at `cutpoint-oracle` without emitting readiness.
A genuine killed start prefix deliberately presented as finish must fail at
`finish-oracle`. These prove oracle sensitivity, not production omission-site
coverage. Healthy release proves that the same publication route can return.
Reader checks exact event cardinality, raw content/hash/length, terminal versus
unknown state, no final, and byte-for-byte absence of recovery writes.

## Limits

No caller ACK exists for the selected killed call; replay does not synthesize
one or authorize sending, creating a replacement owner, settling again or
crediting a round. The tests deliberately do not reinterpret all unknown
returns as incomplete storage: orphan raw and a complete journal prefix are
different states. Nor do they erase an already committed FINISH because its
caller did not receive the return.

This is controlled process death, not physical power loss or kernel crash:
SIGKILL does not invalidate kernel caches. There is no external HTTP/provider,
real billing, automatic recovery, or mutable reopened owner. Broader host and
streaming return-boundary combinations remain separate. #495 covers its
specific LOCAL round gate; #496 covers native finalization followed by normal
process exit and lost summary, not these SIGKILL cuts.

## Executed validation

Isolated Linux/btrfs, Python 3.13.12 / pytest 9.1.1. Nine new combinations
passed. Final five-module regression: **652 passed** in 229.30 seconds, with
the existing unknown `parametrize_long_str_id_strategy` configuration warning.
Modules: T35 process boundaries, R03-08 summary-return loss, T34 process
recovery, invocation owner, and finalize-native. Full Ruff check/format passed
for 1051 Python files. Six review lenses found no remaining blockers; separate
reviewers inspected source and did not claim independent test execution.
The manifest adds one module/nine nodes and removes no existing identity.
