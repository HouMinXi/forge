# Same-run dependency and file-view diagnostic

This is a manually dispatched, separately opted-in diagnostic. It is not an
admission decision, a service launcher, or a replacement for #376. A fresh
explicit approval for the exact reviewed source SHA and the two root stat reads
is required before a dispatch. Ordinary and fixed-metadata modes do not enable
this mode; selecting any two or more modes fails closed.

The dispatch wrapper verifies the event, job, repository, branch, first attempt,
and exact expected source SHA. It compares the working-tree bytes to the seven
bound tree entries: the workflow, dispatch wrapper, diagnostic worker, existing
snapshot probe, existing metadata helper, candidate derivation helper, and
fragment helper. The worker is passed as verified bytes; imported helpers are
checked against the source hashes. Context is a declared provider context, not
an independent attestation.

A single ordinary collector retains the verified user manager's identity and
private endpoint while sampling. It may invoke at most twice, without retry,
only the fixed sudo/timeout/stat command over the verified manager and collector
PIDs' mount namespace and root object. The command reads only device:inode
identities. The collector PID is the live collecting process, never
`/proc/self` of stat. Each stat has a two-second timeout and a bounded outer
capture; failures are recorded without raw stderr. Direct sudo reaping does not
independently establish that privileged descendants terminated.

Candidate identifiers are derived from that invocation's manager snapshot, not
from any prior VM list. The file helper hashes only candidate fragment paths
reported in that snapshot, under manager-reported unit roots, with strict
no-follow path handling, bounded file sizes and rechecks. An empty fragment is
reported only for the exact narrowly checked root-slice shape, and is not a
claim that no other configuration or effect exists. Drop-ins, precedence,
conditions, type-specific triggers/actions, and package provenance remain
unestablished. No file content, Exec or Environment values, cgroup or security
profile data are included in this diagnostic's receipt.

The collector uses a 25-second cooperative deadline; the wrapper has a
40-second worker capture. A blocked filesystem syscall may outlive the inner
cooperative limit; the outer worker timeout is not proof that all privileged
processes stopped. Unavailable, ambiguous, changing, or over-limit inputs
produce UNKNOWN. A stopped worker may not return its per-call receipt, so a
missing receipt does not establish that a root call was not attempted.

The observation is NONATOMIC. Matching endpoint samples and file hashes do not
prove no ABA or intermediate mount/file changes, that the manager parsed the
same bytes, or closure of any future transaction. Qualification remains
NOT_RUN. The underlying library's whole-reply allocation is not bounded by
these field limits.
