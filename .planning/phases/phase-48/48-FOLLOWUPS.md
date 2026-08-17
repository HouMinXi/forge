# Phase 48 Follow-up Candidates

Recorded per forge review R1 disposition DEFER-1 (expert@3007) plus the
plan's own Contract non-goals. None of these are implemented this phase.

## 1. Configurable truncation-breaker threshold

The run-level `TruncationBreaker` threshold is hardcoded at 5, mirroring
the timeout breaker's default. A configurable threshold (e.g. a
`truncation_breaker_threshold` knob beside `retry.max_attempts` in
gate.yaml, threaded through cli.py -> build_l1_provider) would let
operators tune sensitivity per backend -- a genuinely high-output
backend that truncates once per run at its hard cap might warrant a
higher threshold than a clamped free route. Deferred: the fixed
threshold matches the existing timeout breaker's semantics and no
consumer has asked for per-backend tuning.

## 2. cross_repo.py run-orchestration site (plan Contract non-goal)

cross_repo.py:305-310 constructs its own TimeoutCircuitBreaker inside
`_thread_fn` and never passes a continuation_breaker, so cross-repo
runs get the fresh-per-call default (per-call recovery works; run-level
trip memory does not). Wiring needs a decision first: a breaker
constructed inside the per-primary-repo thread closure has
per-primary-repo lifetime, not per-run -- a semantic difference from
cli.py's per-run breaker. Decide per-run vs per-primary-repo lifetime,
then wire with a run_cross_repo integration test (threads + ExitStack +
per-repo cwds).

## 3. Probe follow-up (T0 drift check)

Neither T0 probe attempt forced the sn-deepseek-flash clamp
(unexpected_success output_tokens=11590 both times; the re-run is
[INFERRED, MED] an OmniRoute semantic-cache replay). A definitive A1
check needs a prompt variation (to defeat the cache) plus a prompt that
tightly constrains bulk output. If a future probe refutes A1 (the
gateway reports stop on clamped output), the no_json extension point
below becomes the recovery path for the default backend.

## 4. no_json extension point (plan Contract non-goal)

Undetected truncation (provider reports "stop" on cut JSON) still burns
max_attempts re-truncating retries and dies as no_json -- continuation
never fires because detection never fired. Extension: after no_json
retries exhaust, classify "{"-prefixed content as truncation-suspect
and attempt continuation.

## 5. Bonsai TTFT smoke

Exit criterion 12 was not run (BONSAI_API_KEY not set in the execution
environment). When a bonsai key is available, run one forge pass and
confirm stderr shows "backend bonsai: first token".

## 6. Review-agent artifacts in the worktree root -- CLOSED (moot)

The worktree was removed with --force on 2026-08-16 before the artifacts
could be archived, so `phase48-review.diff` and the
`.code-forge-r*-archived/` receipt directories are gone with it. The
phase-48 dir retains the authoritative records (PLAN.md, EXIT-CHECKLIST.md,
INJECTIONS.md, cp-artifacts/); the raw review receipts are unrecoverable.
