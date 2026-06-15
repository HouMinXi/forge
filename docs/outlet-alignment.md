# Outlet Alignment

forge exposes three review outlets: A (subprocess), B (inline/session), and C
(subagent). Each outlet delivers a review through a different L1 transport while
executing the same flow contract.

## 1. Three outlets, one flow contract

The flow contract defines what every outlet must do, regardless of transport:

- Run phases in the canonical order (L0 parse, L1 anti-hallucination, advisory axes)
- Apply the same reset rule (P0/P1 finding resets the cycle counter)
- Converge only after the configured number of consecutive clean cycles
- Require a backend-backed falsifier on the L1 anti-hallucination gate
- Map terminal verdicts to the canonical exit codes

## 2. A <-> C: implementation alignment (target)

Outlets A and C share the same implementation: both call `StateMachine` from
`machine.py` with the same injected legs -- real registry, backend-threaded
falsifier, and 5 advisory runners (Taint, Runtime, GraphTriage, DaemonState,
Legacy). Only the L1 transport differs: A uses `llm_invoke` via subprocess;
C uses `spawn_fn` via subagent.

> **Wave 0 note (current behavior):** As of this commit, Outlet C still runs
> with stub legs (registry={}, no advisory runners, falsifier without backend).
> D2 alignment lands in 24.1-02. This section describes the post-D2 target
> architecture, not current behavior.

## 3. {A, C} <-> B: semantic alignment only

Outlet B (inline) IS the session. It cannot run `machine.py` without nesting
a review session inside the session that is already reviewing -- a conceptual
contradiction. B is aligned by contract, not code: the operator (the session
or human running inline) must execute the same flow contract phases, apply the
same reset rule, converge on the same threshold, and use a backend-backed
falsifier. The verdict is declared externally; code-forge declares DELEGATED
to record that the CLI did not gate this run.

## 4. DELEGATED verdict

When `--outlet inline` is active, the CLI returns `Verdict.DELEGATED`:

- Exit code: **5** (`EXIT_DELEGATED`)
- Stderr: `code-forge: DELEGATED -- review delegated to session + external R1; exit 5`
- Meaning: the CLI did not run the StateMachine gate; review is delegated to
  the calling session plus the external R1 pre-commit test gate.

Exit 5 is distinct from 0 (PASS), 1 (FAIL), 2 (CLI_ERROR), 3 (BUSY), and
4 (ESCALATED). A caller checking for exit 0 correctly rejects DELEGATED as
not a confirmed gate result.
