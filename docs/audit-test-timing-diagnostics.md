# Audit test timing diagnostics

The detailed audit test probe (`FORGE_CI_AUDIT_TRACE=1`) retains bounded timing
metadata and includes it only in the audit retry test's failure report. Linux CI
already enables that test diagnostic mode. Successful tests publish no timing
report. This is test infrastructure, not a production audit policy change.

The timing report contains a 128-event tail, a pinned first abnormal window of
up to 128 events, a conservative truncation flag, a dropped-event lower-bound count, and explicit observer/snapshot failure
flags. Ticket ordinals are local to the probe, not durable audit identities.
Weak references do not keep tickets, targets, raw content, or exceptions alive.
Only fixed boundary/phase/state labels, ordinals, timestamps and deadline offsets
are recorded. The mock recorder boundary currently covers `begin_stage` only.

Observed boundaries distinguish submission, worker selection, target entry and
return, mock recorder entry and return, outcome publication, and waiter return.
`unknown_observed` is an observed state, not necessarily its first transition.
A published outcome is a supervisor result, not proof of native durable storage.
The monotonic timestamps are observations around Python method boundaries, not
system-call traces or exact kernel scheduling events. The worker selection event
is not an OS thread-creation event. A gap after target return can include
scheduling, lock acquisition and observer overhead; it does not identify which.

Observers add overhead and may perturb scheduling. Event insertion order is not
complete causal order. The truncation flag becomes true at capacity conservatively and stays true;
concurrent appends can undercount drops, so the count is only a lower bound.
Missing boundaries are inconclusive when the report may be truncated or either integrity flag is true. Bounded ordinal registration may
forget very old tickets; do not correlate ordinals across reports or infer that
an absent boundary never occurred. Default timeouts, worker behavior and
fail-closed handling of late acknowledgements are unchanged. These diagnostics
cannot retroactively determine causes when earlier runs lacked the observations.
