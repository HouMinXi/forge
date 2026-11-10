# Audit test determinism: what was rejected and why

Base: `1b22812dd`. Author: Minxi Hou. Date: 2026-11-08.

`tests/test_ordinary_audit_native.py` failed intermittently in two shapes: `assert 3 == 4`
against the recorded attempts, and a reopen that finds no `final` record. The first is an
attempt losing its record while the owner marks the session refused. The second has an extra
arm: the finalize ticket queues behind the same single audit worker, so a stalled drain can
leave the `final` write out even when nothing was refused.

## Rejected: relaxing the refusal

Three production-side cuts were built. Two had a deterministic proof of a real path into the
refusal; the third was stopped by an existing test. None of them moved the observed failure
rate.

| Cut | Deterministic proof | Rate |
|---|---|---|
| Capture notice sets a non-latching flag | notice followed by `finish` no longer raises | two 24-run series, unarchived |
| Owner-lock timeout sets a non-latching flag | held mutex, `adopt()` marks incomplete, `_refused` stays false | the same pair, unchanged |
| Partial adoption sets a non-latching flag | — | blocked by an existing test |

The third is decisive.
`tests/test_invocation_owner_stage.py::test_known_partial_adoption_preserves_prefix_without_complete_credit`
asserts `owner._refused` after a partial capture:

```python
assert record.refs == [ack.ref] and owner._refused and not record.usage_known
```

A session whose evidence is incomplete must not pass as trustworthy. Relaxing that is a
fail-open trade, not a flake fix.

An earlier attempt also introduced a real regression: replacing the replay snapshot's
`audit_complete` with a new flag let a run whose durable record says `audit_complete=false`
report `summary.audit_complete=True`, and `invocation_host.py:811` gates on that bit alone.

## Accepted: stop overlapping the passes in the test

The passes share one audit owner whose lock wait is `min(0.1, deadline - now)`
(`invocation_owner_stage.py:204`). Losing it calls `_fail`, which is session-wide by design.
Three passes dispatched together therefore race for a lock the test never needed to contend
for.

`FORGE_L1_SERIAL` runs them one at a time: opt-in (`1`/`true`/`yes`/`on`), CLI untouched, and a
serial run reports the sum of the pass durations instead of the parallel wall clock.

Evidence, all on this host with the runs serialized and nothing else running:

| Series | Base | Candidate |
|---|---|---|
| 30 pairs, order randomized within each pair | 6/30 | 0/30 |
| 30 more pairs, same protocol | 2/30 | 0/30 |
| Certification batch, candidate only | — | 0/299 |

Pooled: 8/60 vs 0/60, Fisher one-sided p = 0.003. These counters come from this session's own
run output; each batch loop printed one summary line and was not otherwise archived, so treat
them as a recorded count rather than a re-runnable artifact. Earlier ad-hoc tallies on other
trees (4/55, 8/34, 0/40, 0/75) measure the same thing on different bases and are not pooled
here. One run exited non-zero while printing `2 passed`;
its stderr was not retained and it did not recur, so it is recorded here rather than
counted either way.

## What this does not fix

- An attempt whose own deadline has already passed still refuses; serial removes the contention,
  not the expiry arm.
- The falsify loop is a second fan-out over the same recorder. With more than one candidate on an
  API backend it runs in parallel, and this switch does not reach it.
- Concurrent *L1 passes* no longer run against the real attempt recorder: the integration test
  that pools three passes uses a real host and owner, but a stub recorder. The owner's own
  concurrent operations are covered directly in `tests/test_invocation_owner.py`. Covering the
  pass fan-out against the real recorder needs a forced lock timeout rather than a race, and
  `tests/test_invocation_owner_stage.py` already drives that timeout for a single stage.
- Deleting the module fixture puts the flake back: on a loaded host the module then fails on the
  two assertions above, so nothing in the suite names the fixture as the cause. Deleting the
  conftest fixture is different: the module fixture names it as a dependency, so that is an
  immediate error. A guard that fails when the module fixture disappears would close this gap;
  none exists yet.

## Residual outside this change

Serialising the passes removes the pass-to-pass lock race. It does not remove the second arm, which
runs through the host's deadline policy rather than the lock, and that arm still fires on a loaded
host:

| Sample | Host load | Runs | Red |
|---|---|---|---|
| one review lens | 75-78 | 29 | 2 |
| this worktree | 74.9 | 20 | 0 |

Two batches disagree at the same load, so the rate is not pinned and this number is a record of
what was seen, not a measured rate.

The mechanism sits in `invocation_host._request`: each queue wait is capped at `control_timeout_s`,
The mechanism sits in `invocation_host._request`: each queue wait is capped at
`control_timeout_s`, and the caller's grant is itself one window (`cli.py:3664`,
`_ensure_open`'s `steps * control_timeout_s`). A control that arrives while the worker
is busy therefore expires before the worker reaches it, and the host is never opened,
which is the `host._owner` is `None` traceback. A replay inside a grant that is one
window changes nothing; the fix has to widen what callers grant, which is a change to
the deadline policy this branch deliberately leaves alone.
control that arrives while the worker is busy therefore expires before the worker reaches it, and
the host is never opened, which is the `host._owner` is `None` traceback. A replay inside a grant
that is one window changes nothing; the fix has to widen what callers grant, which is a change to
the deadline policy this branch deliberately leaves alone.

## How the refusal works today

`_refused` is a boolean on `InvocationOwner`: 19 assignment sites including the constructor
(7 in `invocation_owner.py`, 12 in `invocation_owner_stage.py`) and 8 reads. Once set,
`advance_source` refuses to advance it (`invocation_owner.py:615`), `_entered` rejects a dispatch
report for a record that was never dispatched (`:493`), and a finish whose outcome is
`completed` raises after the record has already settled (`:585-593`).

That intent is sound for a broken evidence chain. A lost 100 ms lock race is not one, which is why
this change removes the race rather than the refusal. A partial capture is a different question:
it is a fact about one attempt's payload, and it still refuses. Whether that is the right scope
is open, and this change does not settle it.

## Literature

| Source | What it establishes |
|---|---|
| OpenTelemetry, *Error handling in OpenTelemetry* — https://opentelemetry.io/docs/specs/otel/error-handling/ | "internal exceptions should only affect the execution context of the request that caused the exception". Prescribes per-request scoping. |
| `auditctl(8)` — https://man7.org/linux/man-pages/man8/auditctl.8.html | Audit failure policy is an explicit mode (`-f 0=silent 1=printk 2=panic`), chosen rather than incidental. |
| OpenTelemetry Python — https://github.com/open-telemetry/opentelemetry-python | 2,703 stars. Reference implementation: exporters swallow their own errors. |
| pgAudit — https://github.com/pgaudit/pgaudit | 1,722 stars. Statement auditing for PostgreSQL; its README documents its overhead instead of promising non-blocking capture. Cited as a mature statement-audit surface, not as a matching design. |
| Fail-open vs fail-closed — https://authzed.com/blog/fail-open | Frames the choice as one between availability and security. Read here as: on a path whose job is to record evidence, degrading to keep-going means unrecorded work passes as recorded. |
| Agent-observatory fail-open architecture — https://deepwiki.com/darshankparmar/agent-observatory/8.1-fail-open-architecture | Layered isolation: export failures never propagate into the caller. |

Three of those rows argue for scoping an observation failure to the request that caused it. This
change does not do that, and does not try to: the refusal is still session-wide and the default
parallel path still carries it. What the change removes is the pass-to-pass race inside the test,
on the view that a test should not manufacture the failure it then has to tolerate. The other
rows are about failure policy and precedent rather than scoping. The position this change takes
on the remaining question is its own, not the literature's: a partial capture still refuses the
session, and that refusal is not the same thing as a lost 100 ms race.

## Acceptance

This change claims that the passes no longer race each other for the audit owner. It does not
claim the module is deterministic on a loaded host: the deadline arm above survives it, and the
tests below pin the dispatch policy rather than the absence of every way the host can miss a
control.

- `tests/test_factories.py::TestDurationWallClock::test_api_backend_asked_for_serial_passes_keeps_its_duration`
  records the thread identity each pass ran on and asserts they all ran on the caller's. A pool
  never runs a submitted task on the submitting thread, so this separates the two dispatch
  branches without depending on how long a pass takes. It also asserts the summed duration.
- `test_serial_passes_never_pay_the_stagger` records the module clock, asserts three passes were
  dispatched, and asserts a serial run does not sleep on that clock, which pins the stagger site.
  That each pass is the one it should be is covered separately, in
  `test_factories.py`'s prompt checks, so this test counts dispatches rather than identities.
- Injections, each red on its own: the duration site back on `is_cli` (`got 0.000`), the switch
  ignored (`passes left the calling thread: [<worker thread ids>]` plus `serial passes slept
  before starting: [10.0, 20.0]`), the stagger site back on `is_cli` (`serial passes slept before
  starting: [10.0, 20.0]`).
- `test_serial_switch_parse_is_opt_in` pins the accepted spellings.
- `tests/test_invocation_owner_stage.py::test_known_partial_adoption_preserves_prefix_without_complete_credit`
  still asserts the refusal, unchanged.
