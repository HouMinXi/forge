# Private CLI diagnostic completion handoff

This narrow opt-in addition gives the real CLI dispatch an in-memory diagnostic
result from its primary worker. It does not establish overall G04/G09 acceptance,
a trusted cross-restart history service, or completion of #33 or #441.

With `--diagnostic-ticket-dir`, `_cross_repo_verdict_or_none` owns a fresh private
completion slot and passes it to `run_cross_repo`. The worker still exports tickets
at its original presentation point. After capture close it seals bounded original
expected contexts and publication results, capture-cleanup status, worker outcome,
delivery state, and ticket-export outcomes before signalling its existing completed
event. CLI consumes only after the host returns or raises. It strictly rereads each
exported observation using that retained context and original locator, not the
exported ticket's self-described expectation, before emitting the existing ticket
notice. Missing or changed artifacts produce an unavailable notice without changing
the review verdict. No successful locator is created for an unknown publication.

No extra public command, callback, session interface, MCP schema, State field,
receipt credit, retry or filesystem write is introduced by the handoff. The private
optional host parameter is not a public API. With no ticket opt-in the old dispatch
and messages remain; direct host callers without a slot retain the existing notice
path. Plain diagnostic ticket files retain their caller-trusted historical contract.

Each slot belongs to one invocation in its creating process/thread. A completed
immutable result can be reread while its caller retains the object; it is not
serialized, retained globally or reopened across processes. No latest search,
source rebinding or same-UID authentication is claimed.

Attempt reservation occurs before publishing. KeyboardInterrupt/SystemExit during
publication preserve an explicit unknown attempt without a successful locator and
propagate the original exception. At most 64 attempts are retained, including
failed and interrupted ones. Earlier committed observations survive a later failure.
Setup with no capture is unavailable, not a successful empty observation. `not_run`
is never synthesized for cancellation. Failure in diagnostic notice delivery does
not replace an already propagating primary exception.

Worker completion, capture close, publication cleanup, strict reading and delivery
are separate facts. `worker_completed` does not prove the coordinator, receipt
collection or complete CLI finished successfully; capture cleanup is not temporary
cwd deletion. An uncatchable process death may leave no handoff. Strict filesystem
reading has no hard wall-clock bound. Existing locks, signal deferral, worker join,
public Verdict and cross-repository no-HOLD behavior are unchanged.
