# Host round observations

Modern `state.json` round-history rows record phase completion separately from
clean-credit decisions. An `unavailable` round whose `phase_status.l1` is not
`returned` must omit `fixpoint`: even an empty returned L0 phase cannot establish
a clean review. A null or placeholder value is not an absent observation.

This does not forbid `unavailable` with `fixpoint: CLEAN` after L1 returned.
Receipt rejection or the survivor limit can block credit despite a real CLEAN
observation. A genuine pre-L1 finding still records the existing `reset` action
and its RESET or CYCLE_RESTART observation. Credit counters, earned-window
reconstruction, and verdict rules are unchanged.

Older modern host histories that contain a fixpoint on an unavailable round
without returned L1 are rejected as corrupt when loaded or projected. They are
not silently relabeled or migrated into usable credit. Legacy rows without host
authority fields retain their existing interpretation.

## L2 infrastructure errors

A host round whose L2 phase returned an infrastructure error (the L2 runner
raised, or it reported a typed `MUTATION_ERROR`) records `"l2_infra_error":
true`. The error turns what would be a CLEAN fixpoint into INCOMPLETE. The
field is written only as `true`, only when `phase_status.l2` is `returned`,
and never beside `fixpoint: CLEAN`; any other value is rejected on load.

- A round that otherwise completed records `interrupted` with fixpoint
  INCOMPLETE: it earns nothing and keeps an earned window.
- A round that cannot prove its evidence (a failed attempt, receipts the
  gate refused, or the survivor limit) records `unavailable` with fixpoint
  INCOMPLETE, where it would record `unavailable` with CLEAN without the L2
  error. The inherited window then cannot be restored. An acquisition
  failure or a falsifier protocol stop in the same round still records
  `interrupted`.
- A pure excerpt rejection, which alone records `evidence_rejected` and keeps
  the window, records `unavailable` beside an L2 error, because
  `evidence_rejected` needs a CLEAN fixpoint.
- A product reset or a schema/coverage preserve beside the error records its
  usual action; the field is informational there.

Without the field, INCOMPLETE is accepted only on an `interrupted` round with a
falsifier protocol failure.

Compatibility: a version that predates the field cannot load a `state.json`
holding an `interrupted` or `unavailable` round whose INCOMPLETE comes from the
L2 error alone, even after the review later reached PASS. Archive the state to
go back. It loads rounds where the field sits beside a falsifier protocol
failure, a reset or a preserve, and ignores the field there.

A `state.json` written by an earlier version after an L2 infrastructure error
in a host-receipt review (an `interrupted` INCOMPLETE round without the field)
is also refused by this version. Archive `.code-forge/state.json` and
`.code-forge/receipts/` and start a fresh review.
