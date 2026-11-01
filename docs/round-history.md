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
